//! DWD's individual-site DX precipitation scans, fetched and decoded natively.

mod decode;
#[cfg(test)]
mod tests;

use crate::polar_radar::Palette;
use crate::radar_provider::{Error, Frame, Request};
use crate::{format_rfc3339_utc, polar_radar, radar_sites::RadarSite};
use base64::{engine::general_purpose::STANDARD, Engine};
use std::collections::VecDeque;
use std::sync::{Arc, Mutex, OnceLock, PoisonError};
use std::time::{Duration, Instant};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

const BASE: &str = "https://opendata.dwd.de/weather/radar/sites/dx";
const LIST_LIMIT: usize = 1024 * 1024;
const SCAN_LIMIT: usize = 512 * 1024;

struct Directory {
    site: String,
    checked: Instant,
    scans: Vec<(OffsetDateTime, String)>,
}

pub struct RadarClient {
    client: Result<reqwest::Client, String>,
    directories: Mutex<VecDeque<Directory>>,
    frames: Mutex<VecDeque<(String, Arc<Frame>)>>,
    requests: tokio::sync::Semaphore,
}

pub fn client() -> &'static RadarClient {
    static CLIENT: OnceLock<RadarClient> = OnceLock::new();
    CLIENT.get_or_init(RadarClient::default)
}

impl Default for RadarClient {
    fn default() -> Self {
        let _ = rustls::crypto::ring::default_provider().install_default();
        Self {
            client: reqwest::Client::builder()
                .user_agent("SHARPpy-Reimagined")
                .connect_timeout(Duration::from_secs(8))
                .timeout(Duration::from_secs(25))
                .build()
                .map_err(|error| error.to_string()),
            directories: Mutex::new(VecDeque::new()),
            frames: Mutex::new(VecDeque::new()),
            requests: tokio::sync::Semaphore::new(3),
        }
    }
}

impl RadarClient {
    pub fn clear(&self) {
        self.directories
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clear();
        self.frames
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clear();
    }

    pub async fn fetch(
        &self,
        request: Request,
        now: OffsetDateTime,
    ) -> Result<Option<Arc<Frame>>, Error> {
        let site = crate::radar_provider::resolve(&request)?;
        let Some(site) = site.filter(|site| site.country.as_deref() == Some("DE")) else {
            return Ok(None);
        };
        self.fetch_site(request, now, site).await
    }

    pub(crate) async fn fetch_site(
        &self,
        request: Request,
        now: OffsetDateTime,
        site: &'static RadarSite,
    ) -> Result<Option<Arc<Frame>>, Error> {
        if request
            .product
            .as_deref()
            .is_some_and(|product| product != "reflectivity")
        {
            return Err(Error::Invalid(
                "DWD DX provides precipitation-scan reflectivity only.".into(),
            ));
        }
        if request.tilt.as_deref().is_some_and(|tilt| tilt != "0") {
            return Err(Error::Invalid(
                "DWD DX uses a terrain-following precipitation scan, not selectable tilts.".into(),
            ));
        }
        let target = request
            .valid_time
            .as_deref()
            .map(|value| OffsetDateTime::parse(value, &Rfc3339))
            .transpose()
            .map_err(|_| Error::Invalid("A valid radar time is required.".into()))?
            .unwrap_or(now);
        if target < now - time::Duration::minutes(70) || target > now + time::Duration::minutes(1) {
            return Err(Error::Invalid(
                "Radar history is available only for the past 60 minutes.".into(),
            ));
        }
        let code = site.id[1..].to_ascii_lowercase();
        for (nominal, path) in self
            .scans(site, &code)
            .await?
            .into_iter()
            .rev()
            .filter(|(time, _)| *time <= target && *time >= target - time::Duration::minutes(10))
            .take(3)
        {
            let key = format!("{}:{path}", site.id);
            if let Some(frame) = self
                .frames
                .lock()
                .unwrap_or_else(PoisonError::into_inner)
                .iter()
                .find(|(cached, _)| cached == &key)
                .map(|(_, frame)| Arc::clone(frame))
            {
                if frame.actual_time.is_some_and(|actual| {
                    actual <= target && actual >= target - time::Duration::minutes(10)
                }) {
                    return Ok(Some(frame));
                }
                continue;
            }
            let url = format!("{BASE}/{code}/{path}");
            let bytes = match self.download(&url, SCAN_LIMIT).await {
                Err(Error::ScanMissing) => continue,
                result => result?,
            };
            let frame = tokio::task::spawn_blocking(move || {
                make_frame(site, &bytes, nominal, target, now, url)
            })
            .await
            .map_err(|error| Error::Fetch(error.to_string()))??;
            let frame = Arc::new(frame);
            let mut cache = self.frames.lock().unwrap_or_else(PoisonError::into_inner);
            if cache.len() >= 12 {
                cache.pop_front();
            }
            cache.push_back((key, Arc::clone(&frame)));
            return Ok(Some(frame));
        }
        Err(Error::ScanMissing)
    }

    async fn scans(
        &self,
        site: &RadarSite,
        code: &str,
    ) -> Result<Vec<(OffsetDateTime, String)>, Error> {
        if let Some(scans) = self
            .directories
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .iter()
            .find(|entry| {
                entry.site == site.id && entry.checked.elapsed() < Duration::from_secs(45)
            })
            .map(|entry| entry.scans.clone())
        {
            return Ok(scans);
        }
        let bytes = self
            .download(&format!("{BASE}/{code}/"), LIST_LIMIT)
            .await?;
        let scans = parse_listing(
            &bytes,
            site.wmo.as_deref().expect("DWD catalogue WMO"),
            code,
        );
        let mut cache = self
            .directories
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        cache.retain(|entry| entry.site != site.id);
        if cache.len() >= 32 {
            cache.pop_front();
        }
        cache.push_back(Directory {
            site: site.id.clone(),
            checked: Instant::now(),
            scans: scans.clone(),
        });
        Ok(scans)
    }

    async fn download(&self, url: &str, limit: usize) -> Result<Vec<u8>, Error> {
        let _permit = self
            .requests
            .acquire()
            .await
            .map_err(|error| Error::Fetch(error.to_string()))?;
        let response = self
            .client
            .as_ref()
            .map_err(|error| Error::Fetch(error.clone()))?
            .get(url)
            .send()
            .await
            .map_err(|error| Error::Fetch(error.to_string()))?;
        if response.status() == reqwest::StatusCode::NOT_FOUND {
            return Err(Error::ScanMissing);
        }
        let response = response
            .error_for_status()
            .map_err(|error| Error::Fetch(error.to_string()))?;
        crate::http::body(response, limit)
            .await
            .map_err(Error::Fetch)
    }
}

fn parse_listing(bytes: &[u8], wmo: &str, code: &str) -> Vec<(OffsetDateTime, String)> {
    let text = String::from_utf8_lossy(bytes);
    let prefix = format!("raa00-dx_{wmo}-");
    let suffix = format!("-{code}---bin");
    let mut scans = text
        .split("href=\"")
        .skip(1)
        .filter_map(|entry| {
            let file = entry.split_once('"')?.0;
            let stamp = file.strip_prefix(&prefix)?.strip_suffix(&suffix)?;
            if stamp.len() != 10 || !stamp.bytes().all(|byte| byte.is_ascii_digit()) {
                return None;
            }
            let date = format!(
                "20{}-{}-{}T{}:{}:00Z",
                &stamp[0..2],
                &stamp[2..4],
                &stamp[4..6],
                &stamp[6..8],
                &stamp[8..10]
            );
            Some((
                OffsetDateTime::parse(&date, &Rfc3339).ok()?,
                file.to_owned(),
            ))
        })
        .collect::<Vec<_>>();
    scans.sort_unstable();
    scans.dedup();
    scans
}

fn make_frame(
    site: &RadarSite,
    bytes: &[u8],
    nominal: OffsetDateTime,
    target: OffsetDateTime,
    now: OffsetDateTime,
    url: String,
) -> Result<Frame, Error> {
    let sweep = decode::decode(bytes, site).map_err(Error::Fetch)?;
    let actual = OffsetDateTime::parse(&sweep.valid_time, &Rfc3339)
        .map_err(|error| Error::Fetch(error.to_string()))?;
    if actual != nominal {
        return Err(Error::Fetch(
            "DWD scan header time does not match its filename.".into(),
        ));
    }
    if actual > target || actual < target - time::Duration::minutes(10) {
        return Err(Error::ScanMissing);
    }
    let mut palette: Palette = serde_json::from_str(include_str!(
        "../resources/australian_reflectivity.palette.json"
    ))
    .expect("bundled radar palette");
    // Hide weak returns visually but retain their exact values in the PNG lookup.
    palette.floor = Some(0.0);
    let (bounds, png) = polar_radar::render(&sweep, site, &palette).map_err(Error::Fetch)?;
    Ok(Frame {
        actual_time: Some(actual),
        product: "reflectivity".into(),
        site_id: site.id.clone(),
        elevation_angle: None,
        palette: Some(palette),
        legend_png_base64: None,
        label: "Precipitation-scan Reflectivity".into(),
        units: "dBZ".into(),
        bounds,
        retrieved_at: format_rfc3339_utc(now),
        valid_time: Some(format_rfc3339_utc(actual)),
        update_interval_s: 300.0,
        attribution: "Datenbasis: Deutscher Wetterdienst, Rasterdaten bildlich wiedergegeben"
            .into(),
        source_url: url,
        byte_length: png.len(),
        png_base64: STANDARD.encode(png),
    })
}

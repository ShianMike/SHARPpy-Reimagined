//! Australian radar volumes supplied by MRRL; all fetching and decoding is native.

mod decode;
mod render;
#[cfg(test)]
mod tests;

use crate::{format_rfc3339_utc, radar_sites::RadarSite};
use base64::{engine::general_purpose::STANDARD, Engine};
use geographiclib_rs::{Geodesic, InverseGeodesic};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::sync::{Arc, Mutex, OnceLock, PoisonError};
use std::time::{Duration, Instant};
use time::{format_description::well_known::Rfc3339, OffsetDateTime, PrimitiveDateTime};

const BASE: &str = "https://feeds.mrrl.net/polling";
const LIST_LIMIT: usize = 2 * 1024 * 1024;
const VOLUME_LIMIT: usize = 32 * 1024 * 1024;

#[derive(Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Request {
    pub product: Option<String>,
    pub site_id: Option<String>,
    pub latitude: Option<f64>,
    pub longitude: Option<f64>,
    pub valid_time: Option<String>,
    pub tilt: Option<String>,
}

#[derive(Debug)]
pub enum Error {
    Invalid(String),
    ScanMissing,
    Fetch(String),
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(message) | Self::Fetch(message) => f.write_str(message),
            Self::ScanMissing => {
                f.write_str("No Australian radar scan is available near the requested time.")
            }
        }
    }
}

#[derive(Clone, Serialize, Deserialize)]
pub struct Palette {
    pub stops: Vec<(f64, String)>,
    pub units: String,
    pub display: String,
    pub stepped: bool,
    pub floor: Option<f64>,
    pub ceiling: Option<f64>,
    pub ticks: Vec<f64>,
}

#[derive(Clone, Copy, Serialize)]
pub struct Bounds {
    pub west: f64,
    pub east: f64,
    pub south: f64,
    pub north: f64,
}

#[derive(Serialize)]
pub struct Frame {
    #[serde(skip)]
    actual_time: OffsetDateTime,
    pub product: String,
    pub site_id: String,
    pub elevation_angle: f64,
    pub palette: Palette,
    pub legend_png_base64: Option<String>,
    pub label: String,
    pub units: String,
    pub bounds: Bounds,
    pub retrieved_at: String,
    pub valid_time: String,
    pub update_interval_s: f64,
    pub attribution: String,
    pub source_url: String,
    pub byte_length: usize,
    pub png_base64: String,
}

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

    /// Return None when the existing US provider owns this site or nearest location.
    pub async fn fetch(
        &self,
        request: Request,
        now: OffsetDateTime,
    ) -> Result<Option<Arc<Frame>>, Error> {
        let site = if let Some(id) = request.site_id.as_deref() {
            crate::radar_sites::sites()
                .iter()
                .find(|site| site.id.eq_ignore_ascii_case(id))
        } else if let (Some(lat), Some(lon)) = (request.latitude, request.longitude) {
            if !lat.is_finite()
                || !lon.is_finite()
                || !(-90.0..=90.0).contains(&lat)
                || !(-180.0..=180.0).contains(&lon)
            {
                return Err(Error::Invalid("Radar coordinates must be in range.".into()));
            }
            let earth = Geodesic::wgs84();
            crate::radar_sites::sites()
                .iter()
                .map(|site| {
                    let distance: f64 = earth.inverse(lat, lon, site.lat, site.lon);
                    (site, distance)
                })
                .min_by(|a, b| a.1.total_cmp(&b.1))
                .filter(|(_, distance)| *distance <= 500_000.0)
                .map(|(site, _)| site)
        } else {
            None
        };
        let Some(site) = site.filter(|site| site.country.as_deref() == Some("AU")) else {
            return Ok(None);
        };
        if request
            .product
            .as_deref()
            .is_some_and(|product| !matches!(product, "reflectivity" | "velocity"))
        {
            return Err(Error::Invalid(
                "Australian radar supports Base Reflectivity and Velocity.".into(),
            ));
        }
        let product = request
            .product
            .as_deref()
            .unwrap_or("reflectivity")
            .to_owned();
        if request.tilt.as_deref().is_some_and(|tilt| tilt != "0") {
            return Err(Error::Invalid(
                "Australian radar uses its lowest available elevation.".into(),
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
        let scans = self.scans(site).await?;
        // A filename identifies a volume start. Its lowest complete cut can be
        // acquired minutes later; only the actual sweep time may satisfy history.
        for (_, path) in candidate_scans(&scans, target).take(3) {
            let cache_key = format!("{product}:{path}");
            if let Some(frame) = self
                .frames
                .lock()
                .unwrap_or_else(PoisonError::into_inner)
                .iter()
                .find(|(key, _)| key == &cache_key)
                .map(|(_, frame)| Arc::clone(frame))
            {
                let actual = frame.actual_time;
                if actual <= target && actual >= target - time::Duration::minutes(10) {
                    return Ok(Some(frame));
                }
                continue;
            }
            let bytes = self
                .download(&format!("{BASE}/{}/{path}", site.id), VOLUME_LIMIT)
                .await?;
            let source = path.clone();
            let kind = product.clone();
            let frame = tokio::task::spawn_blocking(move || {
                make_frame(site, &source, &bytes, &kind, target, now)
            })
            .await
            .map_err(|error| Error::Fetch(error.to_string()))?;
            let frame = match frame {
                Ok(frame) => Arc::new(frame),
                Err(Error::ScanMissing) => continue,
                Err(error) => return Err(error),
            };
            let mut cache = self.frames.lock().unwrap_or_else(PoisonError::into_inner);
            if cache.len() >= 12 {
                cache.pop_front();
            }
            cache.push_back((cache_key, Arc::clone(&frame)));
            return Ok(Some(frame));
        }
        Err(Error::ScanMissing)
    }

    async fn scans(&self, site: &RadarSite) -> Result<Vec<(OffsetDateTime, String)>, Error> {
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
            .download(&format!("{BASE}/{}/", site.id), LIST_LIMIT)
            .await?;
        let scans = parse_listing(&bytes, &site.id);
        let mut cache = self
            .directories
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        cache.retain(|entry| entry.site != site.id);
        if cache.len() >= 64 {
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

fn parse_listing(bytes: &[u8], site: &str) -> Vec<(OffsetDateTime, String)> {
    let format =
        time::format_description::parse_borrowed::<2>("[year][month][day]_[hour][minute][second]")
            .expect("static radar filename format");
    let text = String::from_utf8_lossy(bytes);
    let mut scans = Vec::new();
    for link in text
        .split("href=\"")
        .skip(1)
        .filter_map(|entry| entry.split_once('"').map(|(link, _)| link))
    {
        let file = link.rsplit('/').next().unwrap_or("");
        let Some(stamp) = file
            .strip_prefix(&format!("{site}_"))
            .and_then(|file| file.strip_suffix(".ar2v"))
        else {
            continue;
        };
        if let Ok(time) = PrimitiveDateTime::parse(stamp, &format) {
            scans.push((time.assume_utc(), file.to_owned()));
        }
    }
    scans.sort_unstable();
    scans.dedup();
    scans
}

fn candidate_scans(
    scans: &[(OffsetDateTime, String)],
    target: OffsetDateTime,
) -> impl Iterator<Item = &(OffsetDateTime, String)> {
    scans
        .iter()
        .rev()
        .filter(move |(time, _)| *time <= target && *time >= target - time::Duration::minutes(20))
}

fn make_frame(
    site: &RadarSite,
    path: &str,
    bytes: &[u8],
    product: &str,
    target: OffsetDateTime,
    now: OffsetDateTime,
) -> Result<Frame, Error> {
    let sweep = decode::decode(bytes, product).map_err(Error::Fetch)?;
    if !sweep.site_id.eq_ignore_ascii_case(&site.id) {
        return Err(Error::Fetch("Radar volume belongs to another site.".into()));
    }
    let actual = OffsetDateTime::parse(&sweep.valid_time, &Rfc3339)
        .map_err(|error| Error::Fetch(error.to_string()))?;
    if actual > target || actual < target - time::Duration::minutes(10) {
        return Err(Error::ScanMissing);
    }
    let velocity = product == "velocity";
    let palette: Palette = serde_json::from_str(if velocity {
        include_str!("../resources/australian_velocity.palette.json")
    } else {
        include_str!("../resources/australian_reflectivity.palette.json")
    })
    .expect("bundled radar palette");
    let (bounds, png) = render::render(&sweep, site, &palette).map_err(Error::Fetch)?;
    Ok(Frame {
        actual_time: actual,
        product: product.into(),
        site_id: site.id.clone(),
        elevation_angle: sweep.elevation_angle,
        palette,
        legend_png_base64: None,
        label: if velocity {
            "Velocity"
        } else {
            "Base Reflectivity"
        }
        .into(),
        units: if velocity { "mph" } else { "dBZ" }.into(),
        bounds,
        retrieved_at: format_rfc3339_utc(now),
        // Public weather DTOs use whole seconds; comparisons keep source precision.
        valid_time: format_rfc3339_utc(actual),
        update_interval_s: 60.0,
        attribution: "Australian Bureau of Meteorology radar via MRRL".into(),
        source_url: format!("{BASE}/{}/{path}", site.id),
        byte_length: png.len(),
        png_base64: STANDARD.encode(png),
    })
}

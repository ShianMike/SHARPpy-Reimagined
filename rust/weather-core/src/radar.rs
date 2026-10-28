//! Small NOAA Level III directory checks; radar image decoding stays in its provider.

use crate::format_rfc3339_utc;
use reqwest::{Client, StatusCode};
use std::collections::VecDeque;
use std::sync::{Mutex, PoisonError};
use std::time::{Duration, Instant};
use time::{OffsetDateTime, PrimitiveDateTime};
use tokio::sync::Semaphore;

const BASE: &str = "https://tgftp.nws.noaa.gov/SL.us008001/DF.of/DC.radar";
const TILTS: [&str; 7] = ["z", "0", "a", "1", "b", "2", "3"];
const BYTE_LIMIT: usize = 256 * 1024;
const CACHE_TTL: Duration = Duration::from_secs(45);
const CACHE_LIMIT: usize = 128;
static REQUESTS: Semaphore = Semaphore::const_new(3);

struct Directory {
    path: String,
    cached_at: Instant,
    scans: Vec<OffsetDateTime>,
}

#[derive(Debug, serde::Serialize)]
pub struct RadarTiltStatus {
    pub tilt: String,
    pub status: String,
}

#[derive(Debug, serde::Serialize)]
pub struct RadarTilts {
    pub site_id: String,
    pub product: String,
    pub checked_at: String,
    pub tilts: Vec<RadarTiltStatus>,
}

pub struct VelocityInventory {
    client: Result<Client, String>,
    directories: Mutex<VecDeque<Directory>>,
    #[cfg(test)]
    base: String,
}

impl VelocityInventory {
    pub fn new() -> Self {
        let _ = rustls::crypto::ring::default_provider().install_default();
        let timeout = std::env::var("SHARPMOD_REMOTE_TIMEOUT")
            .ok()
            .and_then(|value| value.parse::<f64>().ok())
            .filter(|value| value.is_finite() && *value > 0.0)
            .unwrap_or(8.0)
            .min(8.0);
        Self {
            client: Client::builder()
                .user_agent("sharpmod")
                .timeout(Duration::from_secs_f64(timeout))
                .build()
                .map_err(|error| format!("radar HTTP client could not be created: {error}")),
            directories: Mutex::new(VecDeque::new()),
            #[cfg(test)]
            base: BASE.into(),
        }
    }

    pub fn clear(&self) {
        self.directories
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clear();
    }

    /// Callers select a bundled site at their input boundary.
    pub async fn availability(
        &self,
        site: &crate::radar_sites::RadarSite,
        target: Option<OffsetDateTime>,
        now: OffsetDateTime,
    ) -> Result<RadarTilts, String> {
        let target = target.unwrap_or(now);
        if target < now - time::Duration::minutes(70) || target > now + time::Duration::minutes(1) {
            return Err("radar history is available only for the past 60 minutes".into());
        }
        self.client.as_ref().map_err(Clone::clone)?;
        let site = site.id.clone();
        let [z, zero, a, one, b, two, three] = TILTS.map(|cut| self.check(cut, &site, target, now));
        let (z, zero, a, one, b, two, three) = tokio::join!(z, zero, a, one, b, two, three);
        Ok(RadarTilts {
            site_id: site,
            product: "velocity".into(),
            checked_at: format_rfc3339_utc(now),
            tilts: vec![z, zero, a, one, b, two, three],
        })
    }

    async fn check(
        &self,
        cut: &str,
        site: &str,
        target: OffsetDateTime,
        now: OffsetDateTime,
    ) -> RadarTiltStatus {
        let directory = format!("DS.p99v{cut}/SI.{}/", site.to_ascii_lowercase());
        let status = match self.directory_scans(&directory).await {
            Ok(scans) => {
                if scans.into_iter().any(|date| {
                    (now - time::Duration::minutes(65)..=now).contains(&date)
                        && (target >= now - time::Duration::minutes(2)
                            || (date - target).abs() <= time::Duration::minutes(10))
                }) {
                    "available"
                } else {
                    "unavailable"
                }
            }
            Err(error) => {
                log::debug!("Radar directory {directory} could not be checked: {error}");
                "unknown"
            }
        };
        RadarTiltStatus {
            tilt: cut.into(),
            status: status.into(),
        }
    }

    async fn directory_scans(&self, path: &str) -> Result<Vec<OffsetDateTime>, String> {
        if let Some(scans) = self
            .directories
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .iter()
            .find(|entry| entry.path == path && entry.cached_at.elapsed() < CACHE_TTL)
            .map(|entry| entry.scans.clone())
        {
            return Ok(scans);
        }
        let (status, body) = self.fetch(path).await?;
        let scans = match status {
            StatusCode::NOT_FOUND => Vec::new(),
            StatusCode::FORBIDDEN => {
                let (parent, site) = path
                    .trim_end_matches('/')
                    .rsplit_once('/')
                    .expect("radar directory paths include a product and site");
                let (parent_status, body) = self.fetch(&format!("{parent}/")).await?;
                if !parent_status.is_success() || !site_absent(&body, site) {
                    return Err(
                        "radar product directory could not verify the site's absence".into(),
                    );
                }
                Vec::new()
            }
            status if status.is_success() => scans(&body)?,
            status => return Err(format!("radar directory returned HTTP {status}")),
        };
        let mut cache = self
            .directories
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        cache.retain(|entry| entry.path != path);
        if cache.len() >= CACHE_LIMIT {
            cache.pop_front();
        }
        cache.push_back(Directory {
            path: path.into(),
            cached_at: Instant::now(),
            scans: scans.clone(),
        });
        Ok(scans)
    }

    async fn fetch(&self, path: &str) -> Result<(StatusCode, Vec<u8>), String> {
        let client = self.client.as_ref().map_err(Clone::clone)?;
        let _permit = REQUESTS
            .acquire()
            .await
            .map_err(|error| error.to_string())?;
        #[cfg(not(test))]
        let base = BASE;
        #[cfg(test)]
        let base = self.base.as_str();
        let response = client
            .get(format!("{base}/{path}"))
            .send()
            .await
            .map_err(|error| error.to_string())?;
        let status = response.status();
        if !status.is_success() {
            return Ok((status, Vec::new()));
        }
        Ok((status, crate::http::body(response, BYTE_LIMIT).await?))
    }
}

impl Default for VelocityInventory {
    fn default() -> Self {
        Self::new()
    }
}

fn scans(payload: &[u8]) -> Result<Vec<OffsetDateTime>, String> {
    let text = String::from_utf8_lossy(payload);
    let format = time::format_description::parse_borrowed::<2>(
        "[day]-[month repr:short case_sensitive:false]-[year] [hour]:[minute]",
    )
    .expect("static NOAA modification-time format");
    let mut entries = Vec::new();
    for entry in text.split("href=\"").skip(1) {
        let Some((name, row)) = entry.split_once('"') else {
            continue;
        };
        if name.len() != 7
            || !name.starts_with("sn.")
            || !name.as_bytes()[3..].iter().all(u8::is_ascii_digit)
        {
            continue;
        }
        let Some(stamp) = row
            .lines()
            .next()
            .unwrap_or("")
            .as_bytes()
            .windows(17)
            .find(|stamp| {
                stamp[2] == b'-' && stamp[6] == b'-' && stamp[11] == b' ' && stamp[14] == b':'
            })
        else {
            continue;
        };
        if let Some(date) = std::str::from_utf8(stamp)
            .ok()
            .and_then(|stamp| PrimitiveDateTime::parse(stamp, &format).ok())
        {
            entries.push(date.assume_utc());
        }
    }
    if entries.is_empty() && !text.contains("Index of /") {
        return Err("radar scan directory was not a directory listing".into());
    }
    Ok(entries)
}

fn site_absent(payload: &[u8], site: &str) -> bool {
    let text = String::from_utf8_lossy(payload);
    let mut verified = false;
    for entry in text.split("href=\"").skip(1) {
        let Some((name, _)) = entry.split_once('"') else {
            continue;
        };
        if name.len() == 8
            && name.starts_with("SI.")
            && name.ends_with('/')
            && name.as_bytes()[3..7]
                .iter()
                .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit())
        {
            if name.trim_end_matches('/') == site {
                return false;
            }
            verified = true;
        }
    }
    verified
}

#[cfg(test)]
mod tests;

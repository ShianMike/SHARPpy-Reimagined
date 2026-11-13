//! Live NOAA MRMS CONUS radar mosaic overlay via NCEP GeoServer WMS.

use crate::format_rfc3339_utc;
use crate::polar_radar::Bounds;
use crate::radar_time::{self, TimeError};
use reqwest::Client;
use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex, OnceLock, PoisonError};
use std::time::{Duration, Instant};
use time::OffsetDateTime;

const WMS_URL: &str = "https://opengeo.ncep.noaa.gov/geoserver/conus/wms";
const WMS_VERSION: &str = "1.3.0";
const WMS_CRS: &str = "CRS:84";
const WMS_FORMAT: &str = "image/png";
const ATTRIBUTION: &str = "NOAA/NWS MRMS via NCEP GeoServer";
const COVERAGE_BOUNDS: Bounds = Bounds {
    west: -130.0,
    east: -60.0,
    south: 20.0,
    north: 55.0,
};
const DEFAULT_SIZE: (u32, u32) = (4096, 2048);
const HISTORY_SIZE: (u32, u32) = (8192, 4096);
const MAX_FRAME_BYTES: usize = 8 * 1024 * 1024;
const MAX_LEGEND_BYTES: usize = 128 * 1024;
const FRAME_CACHE_TTL: Duration = Duration::from_secs(100);
const FAILURE_CACHE_TTL: Duration = Duration::from_secs(60);
const CACHE_MAX_ENTRIES: usize = 16;
const LEGEND_CACHE_MAX_ENTRIES: usize = 32;

#[derive(Clone, Copy)]
pub struct MosaicProduct {
    pub key: &'static str,
    pub layer: &'static str,
    pub label: &'static str,
    pub style: &'static str,
    pub units: &'static str,
    pub update_interval_s: f64,
}

pub fn get_product(key: Option<&str>) -> Result<MosaicProduct, String> {
    let resolved = key
        .unwrap_or("composite-reflectivity")
        .trim()
        .to_ascii_lowercase();
    match resolved.as_str() {
        "" | "composite-reflectivity" => Ok(MosaicProduct {
            key: "composite-reflectivity",
            layer: "conus_cref_qcd",
            label: "Composite Reflectivity",
            style: "radar_reflectivity",
            units: "dBZ",
            update_interval_s: 120.0,
        }),
        "base-reflectivity" => Ok(MosaicProduct {
            key: "base-reflectivity",
            layer: "conus_bref_qcd",
            label: "Base Reflectivity",
            style: "radar_reflectivity",
            units: "dBZ",
            update_interval_s: 120.0,
        }),
        "echo-tops" => Ok(MosaicProduct {
            key: "echo-tops",
            layer: "conus_neet_v18",
            label: "Enhanced Echo Tops",
            style: "radar_echo_tops",
            units: "kft",
            update_interval_s: 120.0,
        }),
        _ => Err(format!("unknown radar product {resolved:?}")),
    }
}

#[derive(Clone)]
pub struct MosaicFrame {
    pub product: String,
    pub label: String,
    pub units: String,
    pub bounds: Bounds,
    pub retrieved_at: String,
    pub valid_time: Option<String>,
    pub update_interval_s: f64,
    pub attribution: String,
    pub source_url: String,
    pub png_bytes: Vec<u8>,
    pub legend_png_bytes: Option<Vec<u8>>,
}

impl MosaicFrame {
    pub fn to_json(&self) -> Result<String, String> {
        #[derive(serde::Serialize)]
        struct MosaicJson<'a> {
            product: &'a str,
            label: &'a str,
            units: &'a str,
            bounds: Bounds,
            retrieved_at: &'a str,
            valid_time: Option<&'a str>,
            update_interval_s: f64,
            attribution: &'a str,
            source_url: &'a str,
            png_base64: String,
            legend_png_base64: Option<String>,
        }
        let out = MosaicJson {
            product: &self.product,
            label: &self.label,
            units: &self.units,
            bounds: self.bounds,
            retrieved_at: &self.retrieved_at,
            valid_time: self.valid_time.as_deref(),
            update_interval_s: self.update_interval_s,
            attribution: &self.attribution,
            source_url: &self.source_url,
            png_base64: crate::base64_encode(&self.png_bytes),
            legend_png_base64: self.legend_png_bytes.as_deref().map(crate::base64_encode),
        };
        serde_json::to_string(&out).map_err(|error| error.to_string())
    }
}

#[derive(Debug)]
pub enum MosaicError {
    Invalid(String),
    ScanMissing(String),
    Fetch(String),
}

impl std::fmt::Display for MosaicError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(msg) | Self::ScanMissing(msg) | Self::Fetch(msg) => f.write_str(msg),
        }
    }
}

struct CachedFrame {
    key: String,
    expires_at: Instant,
    frame: Option<Arc<MosaicFrame>>,
}

pub struct MosaicClient {
    client: Result<Client, String>,
    frames: Mutex<VecDeque<CachedFrame>>,
    legends: Mutex<HashMap<String, Option<Vec<u8>>>>,
}

pub fn client() -> &'static MosaicClient {
    static CLIENT: OnceLock<MosaicClient> = OnceLock::new();
    CLIENT.get_or_init(MosaicClient::default)
}

impl Default for MosaicClient {
    fn default() -> Self {
        let _ = rustls::crypto::ring::default_provider().install_default();
        let timeout = std::env::var("SHARPMOD_REMOTE_TIMEOUT")
            .ok()
            .and_then(|value| value.parse::<f64>().ok())
            .filter(|value| value.is_finite() && *value > 0.0)
            .unwrap_or(15.0);
        Self {
            client: Client::builder()
                .user_agent("SHARPpy-Reimagined")
                .connect_timeout(Duration::from_secs(8))
                .timeout(Duration::from_secs_f64(timeout))
                .build()
                .map_err(|error| error.to_string()),
            frames: Mutex::new(VecDeque::new()),
            legends: Mutex::new(HashMap::new()),
        }
    }
}

impl MosaicClient {
    pub fn clear(&self) {
        self.frames
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clear();
        self.legends
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clear();
        radar_time::clear_cache();
    }

    pub async fn fetch(
        &self,
        product_key: Option<&str>,
        valid_time: Option<OffsetDateTime>,
        now: OffsetDateTime,
    ) -> Result<Arc<MosaicFrame>, MosaicError> {
        let product = get_product(product_key).map_err(MosaicError::Invalid)?;
        let client = self
            .client
            .as_ref()
            .map_err(|e| MosaicError::Fetch(e.clone()))?;
        let selected = radar_time::select_time(
            client,
            WMS_URL,
            product.layer,
            valid_time.unwrap_or(now),
            now,
        )
        .await
        .map_err(|error| match error {
            TimeError::Missing(msg) => {
                MosaicError::ScanMissing(format!("{} scan is unavailable: {msg}", product.label))
            }
            TimeError::Other(msg) => {
                MosaicError::Fetch(format!("{} scan is unavailable: {msg}", product.label))
            }
        })?;
        let (width, height) = if valid_time.is_some() {
            HISTORY_SIZE
        } else {
            DEFAULT_SIZE
        };
        let cache_key = format!(
            "{}:{}:{width}x{height}:{}",
            product.layer,
            product.style,
            radar_time::stamp(selected)
        );
        if let Some(cached) = self.cache_get(&cache_key) {
            return cached.ok_or_else(|| {
                MosaicError::Fetch(format!(
                    "{} was unavailable moments ago; not retrying yet",
                    product.label
                ))
            });
        }
        let mut url = reqwest::Url::parse(WMS_URL).expect("static WMS URL");
        let bbox = format!(
            "{:.6},{:.6},{:.6},{:.6}",
            COVERAGE_BOUNDS.west,
            COVERAGE_BOUNDS.south,
            COVERAGE_BOUNDS.east,
            COVERAGE_BOUNDS.north
        );
        {
            let mut pairs = url.query_pairs_mut();
            pairs.extend_pairs([
                ("SERVICE", "WMS"),
                ("VERSION", WMS_VERSION),
                ("REQUEST", "GetMap"),
                ("LAYERS", product.layer),
                ("STYLES", product.style),
                ("CRS", WMS_CRS),
                ("BBOX", &bbox),
                ("WIDTH", &width.to_string()),
                ("HEIGHT", &height.to_string()),
                ("FORMAT", WMS_FORMAT),
                ("TRANSPARENT", "TRUE"),
            ]);
            pairs.append_pair("TIME", &radar_time::stamp(selected));
        }
        let source_url = url.to_string();
        let response = match client
            .get(url)
            .send()
            .await
            .and_then(|r| r.error_for_status())
        {
            Ok(resp) => resp,
            Err(err) => {
                self.cache_put(&cache_key, None, FAILURE_CACHE_TTL);
                return Err(MosaicError::Fetch(format!(
                    "{} request failed: {err}",
                    product.label
                )));
            }
        };
        let payload = match crate::http::body(response, MAX_FRAME_BYTES).await {
            Ok(bytes) => bytes,
            Err(err) => {
                self.cache_put(&cache_key, None, FAILURE_CACHE_TTL);
                return Err(MosaicError::Fetch(format!(
                    "{} request failed: {err}",
                    product.label
                )));
            }
        };
        if !looks_like_image(&payload) {
            self.cache_put(&cache_key, None, FAILURE_CACHE_TTL);
            let detail = service_exception_text(&payload);
            let suffix = if detail.is_empty() {
                String::new()
            } else {
                format!(": {detail}")
            };
            return Err(MosaicError::Fetch(format!(
                "{} returned no image{suffix}",
                product.label
            )));
        }
        let legend_png_bytes = if valid_time.is_some() {
            self.legend_png(client, product.layer).await
        } else {
            None
        };
        let frame = Arc::new(MosaicFrame {
            product: product.key.to_owned(),
            label: product.label.to_owned(),
            units: product.units.to_owned(),
            bounds: COVERAGE_BOUNDS,
            retrieved_at: format_rfc3339_utc(now),
            valid_time: Some(format_rfc3339_utc(selected)),
            update_interval_s: product.update_interval_s,
            attribution: ATTRIBUTION.to_owned(),
            source_url,
            png_bytes: payload,
            legend_png_bytes,
        });
        self.cache_put(&cache_key, Some(Arc::clone(&frame)), FRAME_CACHE_TTL);
        Ok(frame)
    }

    async fn legend_png(&self, client: &Client, layer: &str) -> Option<Vec<u8>> {
        if let Some(cached) = self
            .legends
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .get(layer)
            .cloned()
        {
            return cached;
        }
        let mut url = reqwest::Url::parse(WMS_URL).expect("static WMS URL");
        url.query_pairs_mut().extend_pairs([
            ("service", "WMS"),
            ("version", WMS_VERSION),
            ("request", "GetLegendGraphic"),
            ("format", WMS_FORMAT),
            ("width", "500"),
            ("height", "30"),
            ("layer", layer),
        ]);
        let fetched = match client
            .get(url)
            .timeout(Duration::from_secs(10))
            .send()
            .await
            .and_then(|r| r.error_for_status())
        {
            Ok(resp) => crate::http::body(resp, MAX_LEGEND_BYTES)
                .await
                .ok()
                .filter(|bytes| bytes.starts_with(b"\x89PNG\r\n\x1a\n")),
            Err(_) => None,
        };
        let mut cache = self.legends.lock().unwrap_or_else(PoisonError::into_inner);
        if cache.len() >= LEGEND_CACHE_MAX_ENTRIES {
            if let Some(key) = cache.keys().next().cloned() {
                cache.remove(&key);
            }
        }
        cache.insert(layer.to_owned(), fetched.clone());
        fetched
    }

    fn cache_get(&self, key: &str) -> Option<Option<Arc<MosaicFrame>>> {
        let now = Instant::now();
        let mut cache = self.frames.lock().unwrap_or_else(PoisonError::into_inner);
        if let Some(pos) = cache.iter().position(|entry| entry.key == key) {
            if cache[pos].expires_at <= now {
                cache.remove(pos);
                return None;
            }
            return Some(cache[pos].frame.clone());
        }
        None
    }

    fn cache_put(&self, key: &str, frame: Option<Arc<MosaicFrame>>, ttl: Duration) {
        let now = Instant::now();
        let mut cache = self.frames.lock().unwrap_or_else(PoisonError::into_inner);
        cache.retain(|entry| entry.key != key && entry.expires_at > now);
        if cache.len() >= CACHE_MAX_ENTRIES {
            cache.pop_front();
        }
        cache.push_back(CachedFrame {
            key: key.to_owned(),
            expires_at: now + ttl,
            frame,
        });
    }
}

pub fn looks_like_image(payload: &[u8]) -> bool {
    payload.starts_with(b"\x89PNG\r\n\x1a\n")
        || payload.starts_with(b"GIF87a")
        || payload.starts_with(b"GIF89a")
        || payload.starts_with(b"\xff\xd8\xff")
}

pub fn service_exception_text(payload: &[u8]) -> String {
    let scan = &payload[..payload.len().min(4096)];
    let text = String::from_utf8_lossy(scan);
    let lower = text.to_ascii_lowercase();
    let mut offset = 0;
    while let Some(rel) = lower[offset..].find("<serviceexception") {
        let start = offset + rel;
        let after_tag = &lower[start + "<serviceexception".len()..];
        let valid_sep = after_tag
            .chars()
            .next()
            .is_some_and(|c| c == '>' || c.is_whitespace());
        if !valid_sep {
            offset = start + "<serviceexception".len();
            continue;
        }
        let Some(gt) = text[start..].find('>') else {
            break;
        };
        let body_start = start + gt + 1;
        if let Some(end_rel) = lower[body_start..].find("</serviceexception") {
            let inner = &text[body_start..body_start + end_rel];
            let collapsed = inner.split_whitespace().collect::<Vec<_>>().join(" ");
            return collapsed.chars().take(300).collect();
        }
        break;
    }
    String::new()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_service_exception_without_matching_wrapper() {
        let body = b"<ServiceExceptionReport><ServiceException code=\"Invalid\">  bad   layer </ServiceException></ServiceExceptionReport>";
        assert_eq!(service_exception_text(body), "bad layer");
        assert!(looks_like_image(b"\x89PNG\r\n\x1a\n1234"));
        assert!(!looks_like_image(body));
    }
}

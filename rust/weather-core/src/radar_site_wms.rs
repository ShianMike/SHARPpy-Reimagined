//! Single-site US NEXRAD WMS overlays (hydrometeor classification and rainfall accumulations).

use crate::format_rfc3339_utc;
use crate::polar_radar::Bounds;
use crate::radar_mosaic::{looks_like_image, service_exception_text};
use crate::radar_provider::{Error, Frame, Request};
use crate::radar_sites::RadarSite;
use crate::radar_time::{self, TimeError};
use base64::{engine::general_purpose::STANDARD, Engine};
use reqwest::Client;
use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex, OnceLock, PoisonError};
use std::time::{Duration, Instant};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

const BASE: &str = "https://opengeo.ncep.noaa.gov/geoserver";
const WMS_VERSION: &str = "1.3.0";
const WMS_CRS: &str = "CRS:84";
const WMS_FORMAT: &str = "image/png";
const ATTRIBUTION: &str = "NOAA/NWS NEXRAD via NCEP GeoServer";
const MAX_FRAME_BYTES: usize = 6 * 1024 * 1024 - 4096;
const MAX_LEGEND_BYTES: usize = 128 * 1024;
const FRAME_CACHE_TTL: Duration = Duration::from_secs(100);
const FAILURE_CACHE_TTL: Duration = Duration::from_secs(60);
const CACHE_MAX_ENTRIES: usize = 12;
const LEGEND_CACHE_MAX_ENTRIES: usize = 32;

#[derive(Clone, Copy)]
struct WmsProduct {
    key: &'static str,
    suffix: &'static str,
    label: &'static str,
    units: &'static str,
    update_interval_s: f64,
}

const PRODUCTS: [WmsProduct; 3] = [
    WmsProduct {
        key: "hydrometeor",
        suffix: "_bdhc",
        label: "Hydrometeor Classification",
        units: "",
        update_interval_s: 150.0,
    },
    WmsProduct {
        key: "storm-total",
        suffix: "_bdsa",
        label: "Storm Total Accumulation",
        units: "in",
        update_interval_s: 300.0,
    },
    WmsProduct {
        key: "one-hour",
        suffix: "_boha",
        label: "One-Hour Accumulation",
        units: "in",
        update_interval_s: 300.0,
    },
];

fn get_product(key: &str) -> Option<WmsProduct> {
    PRODUCTS.iter().copied().find(|p| p.key == key)
}

fn site_bounds(site: &RadarSite) -> Bounds {
    let span = site.half_span_deg;
    Bounds {
        west: (site.lon - span).max(-180.0),
        east: (site.lon + span).min(180.0),
        south: (site.lat - span).max(-90.0),
        north: (site.lat + span).min(90.0),
    }
}

fn frame_size(bounds: Bounds, pixels: usize) -> (usize, usize) {
    let lon_span = (bounds.east - bounds.west).max(1e-6);
    let lat_span = (bounds.north - bounds.south).max(1e-6);
    if lon_span >= lat_span {
        (
            pixels,
            256_usize.max((pixels as f64 * lat_span / lon_span).round() as usize),
        )
    } else {
        (
            256_usize.max((pixels as f64 * lon_span / lat_span).round() as usize),
            pixels,
        )
    }
}

struct CachedFrame {
    key: String,
    expires_at: Instant,
    frame: Option<Arc<Frame>>,
}

pub struct SiteWmsClient {
    client: Result<Client, String>,
    frames: Mutex<VecDeque<CachedFrame>>,
    legends: Mutex<HashMap<String, Option<Vec<u8>>>>,
}

pub fn client() -> &'static SiteWmsClient {
    static CLIENT: OnceLock<SiteWmsClient> = OnceLock::new();
    CLIENT.get_or_init(SiteWmsClient::default)
}

impl Default for SiteWmsClient {
    fn default() -> Self {
        let _ = rustls::crypto::ring::default_provider().install_default();
        let timeout = std::env::var("SHARPMOD_REMOTE_TIMEOUT")
            .ok()
            .and_then(|value| value.parse::<f64>().ok())
            .filter(|value| value.is_finite() && *value > 0.0)
            .unwrap_or(30.0);
        Self {
            client: Client::builder()
                .user_agent("sharpmod")
                .connect_timeout(Duration::from_secs(8))
                .timeout(Duration::from_secs_f64(timeout))
                .build()
                .map_err(|error| error.to_string()),
            frames: Mutex::new(VecDeque::new()),
            legends: Mutex::new(HashMap::new()),
        }
    }
}

impl SiteWmsClient {
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

    pub async fn fetch_site(
        &self,
        request: Request,
        now: OffsetDateTime,
        site: &'static RadarSite,
    ) -> Result<Option<Arc<Frame>>, Error> {
        let Some(product_key) = request.product.as_deref() else {
            return Ok(None);
        };
        let Some(product) = get_product(product_key) else {
            return Ok(None);
        };
        let needed = product.suffix.trim_start_matches('_');
        if !site.products.is_empty() && !site.products.iter().any(|p| p == needed) {
            return Err(Error::Invalid(format!(
                "{} does not publish {}",
                site.id, product.label
            )));
        }
        if request.tilt.as_deref().is_some_and(|tilt| tilt != "0") {
            return Err(Error::Invalid(format!(
                "{} does not have a selectable tilt",
                product.label
            )));
        }
        let requested = request
            .valid_time
            .as_deref()
            .map(|raw| OffsetDateTime::parse(raw, &Rfc3339))
            .transpose()
            .map_err(|_| Error::Invalid("A valid radar time is required.".into()))?;
        let client = self
            .client
            .as_ref()
            .map_err(|error| Error::Fetch(error.clone()))?;
        let site_lower = site.id.to_ascii_lowercase();
        let ows_url = format!("{BASE}/{site_lower}/ows");
        let layer = format!("{site_lower}{}", product.suffix);
        let selected = match requested {
            Some(when) => Some(
                radar_time::select_time(client, &ows_url, &layer, when, now)
                    .await
                    .map_err(|error| match error {
                        TimeError::Missing(_) => Error::ScanMissing,
                        TimeError::Other(msg) => {
                            Error::Fetch(format!("{} history is unavailable: {msg}", site.id))
                        }
                    })?,
            ),
            None => None,
        };
        let bounds = site_bounds(site);
        let pixels = if selected.is_some() { 4096 } else { 2048 };
        let (width, height) = frame_size(bounds, pixels);
        let cache_key = match selected {
            Some(t) => format!(
                "{}:{}:{width}x{height}:{}",
                product.key,
                site.id,
                radar_time::stamp(t)
            ),
            None => format!("{}:{}:{width}x{height}", product.key, site.id),
        };
        if let Some(cached) = self.cache_get(&cache_key) {
            return cached.map(Some).ok_or_else(|| {
                Error::Fetch(format!("{} {} was unavailable", site.id, product.label))
            });
        }
        let mut url = reqwest::Url::parse(&ows_url).map_err(|e| Error::Fetch(e.to_string()))?;
        let bbox = format!(
            "{},{},{},{}",
            bounds.west, bounds.south, bounds.east, bounds.north
        );
        {
            let mut pairs = url.query_pairs_mut();
            pairs.extend_pairs([
                ("service", "WMS"),
                ("version", WMS_VERSION),
                ("request", "GetMap"),
                ("layers", &layer),
                ("styles", ""),
                ("crs", WMS_CRS),
                ("bbox", &bbox),
                ("width", &width.to_string()),
                ("height", &height.to_string()),
                ("format", WMS_FORMAT),
                ("transparent", "TRUE"),
            ]);
            if let Some(t) = selected {
                pairs.append_pair("time", &radar_time::stamp(t));
            }
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
                return Err(Error::Fetch(format!("radar request failed: {err}")));
            }
        };
        let payload = match crate::http::body(response, MAX_FRAME_BYTES).await {
            Ok(bytes) => bytes,
            Err(err) => {
                self.cache_put(&cache_key, None, FAILURE_CACHE_TTL);
                return Err(Error::Fetch(format!("radar request failed: {err}")));
            }
        };
        if !looks_like_image(&payload) || !payload.starts_with(b"\x89PNG\r\n\x1a\n") {
            self.cache_put(&cache_key, None, FAILURE_CACHE_TTL);
            let detail = service_exception_text(&payload);
            let detail = if detail.is_empty() {
                "unrecognised response".to_owned()
            } else {
                detail
            };
            return Err(Error::Fetch(format!("{}: {detail}", site.id)));
        }
        let legend_png_base64 = if selected.is_some() {
            self.legend_png(client, &ows_url, &layer)
                .await
                .as_deref()
                .map(|bytes| STANDARD.encode(bytes))
        } else {
            None
        };
        let frame = Arc::new(Frame {
            actual_time: selected,
            product: product.key.to_owned(),
            site_id: site.id.clone(),
            elevation_angle: None,
            palette: None,
            legend_png_base64,
            label: product.label.to_owned(),
            units: product.units.to_owned(),
            bounds,
            retrieved_at: format_rfc3339_utc(now),
            valid_time: selected.map(format_rfc3339_utc),
            update_interval_s: product.update_interval_s,
            attribution: ATTRIBUTION.to_owned(),
            source_url,
            byte_length: payload.len(),
            png_base64: STANDARD.encode(payload),
        });
        self.cache_put(&cache_key, Some(Arc::clone(&frame)), FRAME_CACHE_TTL);
        Ok(Some(frame))
    }

    async fn legend_png(&self, client: &Client, ows_url: &str, layer: &str) -> Option<Vec<u8>> {
        if let Some(cached) = self
            .legends
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .get(layer)
            .cloned()
        {
            return cached;
        }
        let mut url = reqwest::Url::parse(ows_url).ok()?;
        url.query_pairs_mut().extend_pairs([
            ("service", "WMS"),
            ("version", WMS_VERSION),
            ("request", "GetLegendGraphic"),
            ("format", WMS_FORMAT),
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

    fn cache_get(&self, key: &str) -> Option<Option<Arc<Frame>>> {
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

    fn cache_put(&self, key: &str, frame: Option<Arc<Frame>>, ttl: Duration) {
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frame_size_preserves_square_and_clamped_dimensions() {
        let bounds = Bounds {
            west: -102.0,
            east: -92.0,
            south: 30.0,
            north: 40.0,
        };
        assert_eq!(frame_size(bounds, 2048), (2048, 2048));
        assert_eq!(frame_size(bounds, 4096), (4096, 4096));
        let narrow = Bounds {
            west: -180.0,
            east: -176.0,
            south: 45.0,
            north: 55.0,
        };
        assert_eq!(frame_size(narrow, 2048), (819, 2048));
    }
}

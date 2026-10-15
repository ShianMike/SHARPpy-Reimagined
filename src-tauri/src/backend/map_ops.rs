//! Map data operations, all served by the Python bridge today
//! (`sharpmod/bridge/fields.py`, `sharpmod/bridge/map_layers.py`).
//!
//! | Operation | Bridge method | Deadline |
//! |---|---|---|
//! | `get_map_catalog` | `map.catalog`, cached for the session | 30 s |
//! | `get_weather_field` | `fields.grid` | 120 s |
//! | `get_spc_outlook` | `outlooks.spc` | 60 s |
//! | `get_radar_frame` | `radar.mosaic` | 60 s |
//! | `get_place_labels` | `places.labels`, cached for the session | 30 s |
//! | `get_station_availability` | `stations.availability` | 35 s |
//!
//! Field and radar frames reach the webview as one binary SHRF frame: "SHRF",
//! u16 version 1, u16 0, u32 header length N, N bytes of JSON (padded with
//! spaces so the body starts on an 8-byte boundary), then the body.

use std::sync::{Arc, Mutex, PoisonError};
use std::time::Duration;

use base64::Engine as _;
use serde::Serialize;
use serde_json::json;

use crate::backend::router::Router;
use crate::backend::sounding_ops::{invalid, rejected};
use crate::dto::map::{
    AvailabilityReport, AvailabilityResult, FieldGridResult, MapCatalog, PlaceLabels, RadarResult,
    RadarSiteCatalog, RadarSiteCatalogPublic, RadarSiteResult, SpcOutlook,
};
use crate::error::{BackendError, ErrorContext, PYTHON_PROVIDER};

pub const OP_MAP_CATALOG: &str = "get_map_catalog";
pub const OP_FIELD: &str = "get_weather_field";
pub const OP_OUTLOOK: &str = "get_spc_outlook";
pub const OP_RADAR: &str = "get_radar_frame";
pub const OP_RADAR_SITE: &str = "get_radar_site_frame";
pub const OP_RADAR_SITES: &str = "get_radar_sites";
pub const OP_PLACES: &str = "get_place_labels";
pub const OP_AVAILABILITY: &str = "get_station_availability";

const CATALOG_TIMEOUT: Duration = Duration::from_secs(30);
const FIELD_TIMEOUT: Duration = Duration::from_secs(120);
const OUTLOOK_TIMEOUT: Duration = Duration::from_secs(60);
const RADAR_TIMEOUT: Duration = Duration::from_secs(60);
const PLACES_TIMEOUT: Duration = Duration::from_secs(30);
const AVAILABILITY_TIMEOUT: Duration = Duration::from_secs(35);
const PNG_SIGNATURE: &[u8] = b"\x89PNG\r\n\x1a\n";
const SHRF_FIXED: usize = 12;

/// One binary frame: the fixed header, the JSON header, then the body.
pub fn shrf_frame<H: Serialize>(header: &H, body: &[u8]) -> Vec<u8> {
    let mut json = serde_json::to_vec(header).unwrap_or_default();
    while !(SHRF_FIXED + json.len()).is_multiple_of(8) {
        json.push(b' ');
    }
    let length = u32::try_from(json.len()).unwrap_or(u32::MAX);
    let mut out = Vec::with_capacity(SHRF_FIXED + json.len() + body.len());
    out.extend_from_slice(b"SHRF");
    out.extend_from_slice(&1u16.to_le_bytes());
    out.extend_from_slice(&0u16.to_le_bytes());
    out.extend_from_slice(&length.to_le_bytes());
    out.extend_from_slice(&json);
    out.extend_from_slice(body);
    out
}

/// Product keys are short slugs; anything else never reaches Python.
pub(crate) fn key_ok(key: &str) -> bool {
    (1..=40).contains(&key.len()) && key.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
}

pub(crate) fn time_ok(time: &str) -> bool {
    (10..=40).contains(&time.len()) && time.is_ascii()
}

pub(crate) fn context(operation: &str, resource: &str) -> ErrorContext {
    ErrorContext::new(operation, Some(PYTHON_PROVIDER), Some(resource))
}

pub(crate) fn decode(text: &str) -> Result<Vec<u8>, String> {
    base64::engine::general_purpose::STANDARD
        .decode(text.as_bytes())
        .map_err(|e| format!("base64: {e}"))
}

fn cached<T>(slot: &Mutex<Option<Arc<T>>>) -> Option<Arc<T>> {
    slot.lock().unwrap_or_else(PoisonError::into_inner).clone()
}

impl Router {
    /// What the map can draw; static for the life of the bridge.
    pub async fn map_catalog(&self, model: &str) -> Result<Arc<MapCatalog>, BackendError> {
        if let Some(catalog) =
            cached(&self.map_catalog).filter(|catalog| catalog.field_model == model)
        {
            return Ok(catalog);
        }
        let context = context(OP_MAP_CATALOG, "map catalogue");
        if !key_ok(model) {
            return Err(invalid(&context, "model must be a key"));
        }
        let catalog: MapCatalog = self
            .call(
                &context,
                "map.catalog",
                json!({ "model": model }),
                CATALOG_TIMEOUT,
            )
            .await?;
        catalog
            .validate()
            .map_err(|reason| rejected(&context, "map.catalog", reason))?;
        let catalog = Arc::new(catalog);
        *self
            .map_catalog
            .lock()
            .unwrap_or_else(PoisonError::into_inner) = Some(Arc::clone(&catalog));
        Ok(catalog)
    }

    /// One selected model product/run/hour, as an SHRF frame of uint16 cells.
    pub async fn weather_field(
        &self,
        product: &str,
        run_time: &str,
        forecast_hour: u32,
        model: &str,
    ) -> Result<Vec<u8>, BackendError> {
        let resource = format!("{model} {product} {run_time} F{forecast_hour:03}");
        let context = context(OP_FIELD, &resource);
        if !key_ok(model) || !key_ok(product) || !time_ok(run_time) || forecast_hour > 84 {
            return Err(invalid(
                &context,
                "model and product must be keys, runTime an RFC 3339 time, forecastHour 0 to 84",
            ));
        }
        let params = json!({ "model": model, "product": product, "run_time": run_time, "forecast_hour": forecast_hour });
        let grid: FieldGridResult = self
            .call(&context, "fields.grid", params, FIELD_TIMEOUT)
            .await?;
        grid.validate()
            .map_err(|reason| rejected(&context, "fields.grid", reason))?;
        let body = decode(&grid.data_base64)
            .map_err(|reason| rejected(&context, "fields.grid", reason))?;
        let expected = u64::from(grid.width) * u64::from(grid.height) * 2;
        if u64::try_from(body.len()).ok() != Some(expected) {
            return Err(rejected(
                &context,
                "fields.grid",
                format!(
                    "{} bytes for a {}x{} grid",
                    body.len(),
                    grid.width,
                    grid.height
                ),
            ));
        }
        log::info!(
            "{OP_FIELD}: {resource} -> {}x{}, {} to {} {}",
            grid.width,
            grid.height,
            grid.value_min,
            grid.value_max,
            grid.units
        );
        Ok(shrf_frame(&grid.header(), &body))
    }

    /// The SPC outlook of one product covering a time; `outlook` is null when none does.
    pub async fn spc_outlook(
        &self,
        product: &str,
        valid_time: &str,
    ) -> Result<SpcOutlook, BackendError> {
        let context = context(OP_OUTLOOK, &format!("SPC {product} outlook"));
        if !key_ok(product) || !time_ok(valid_time) {
            return Err(invalid(
                &context,
                "product must be a key and validTime an RFC 3339 time",
            ));
        }
        let outlook: SpcOutlook = self
            .call(
                &context,
                "outlooks.spc",
                json!({ "product": product, "valid_time": valid_time }),
                OUTLOOK_TIMEOUT,
            )
            .await?;
        outlook
            .validate()
            .map_err(|reason| rejected(&context, "outlooks.spc", reason))?;
        Ok(outlook)
    }

    /// The newest radar mosaic frame, as an SHRF frame holding the service's PNG.
    pub async fn radar_frame(&self, product: Option<&str>) -> Result<Vec<u8>, BackendError> {
        let context = context(OP_RADAR, "MRMS radar mosaic");
        let params = match product {
            Some(key) if key_ok(key) => json!({ "product": key }),
            Some(_) => return Err(invalid(&context, "product must be a key")),
            None => json!({}),
        };
        let frame: RadarResult = self
            .call(&context, "radar.mosaic", params, RADAR_TIMEOUT)
            .await?;
        frame
            .validate()
            .map_err(|reason| rejected(&context, "radar.mosaic", reason))?;
        let body = decode(&frame.png_base64)
            .map_err(|reason| rejected(&context, "radar.mosaic", reason))?;
        if !body.starts_with(PNG_SIGNATURE)
            || u64::try_from(body.len()).ok() != Some(frame.byte_length)
        {
            return Err(rejected(
                &context,
                "radar.mosaic",
                "the frame is not the PNG that was described".to_owned(),
            ));
        }
        Ok(shrf_frame(&frame.header(), &body))
    }

    /// One antenna's own frame, pinned by site id or served for a map point.
    /// `site` is `Some(id)` to pin, else `latitude`/`longitude` serve the nearest.
    pub async fn radar_site_frame(
        &self,
        product: Option<&str>,
        site: Option<&str>,
        latitude: Option<f64>,
        longitude: Option<f64>,
    ) -> Result<Vec<u8>, BackendError> {
        let resource = format!(
            "single-site radar {}",
            site.unwrap_or(product.unwrap_or("default"))
        );
        let context = context(OP_RADAR_SITE, &resource);
        let params = match (site, latitude, longitude) {
            (Some(id), _, _) => {
                if !key_ok(id) {
                    return Err(invalid(&context, "site must be a radar identifier"));
                }
                match product {
                    Some(key) if key_ok(key) => json!({ "product": key, "site_id": id }),
                    Some(_) => return Err(invalid(&context, "product must be a key")),
                    None => json!({ "site_id": id }),
                }
            }
            (None, Some(lat), Some(lon)) => {
                if !lat.is_finite()
                    || !(-90.0..=90.0).contains(&lat)
                    || !lon.is_finite()
                    || !(-180.0..=180.0).contains(&lon)
                {
                    return Err(invalid(&context, "latitude and longitude must be in range"));
                }
                match product {
                    Some(key) if key_ok(key) => {
                        json!({ "product": key, "latitude": lat, "longitude": lon })
                    }
                    Some(_) => return Err(invalid(&context, "product must be a key")),
                    None => json!({ "latitude": lat, "longitude": lon }),
                }
            }
            _ => {
                return Err(invalid(
                    &context,
                    "site, or latitude and longitude, must be given",
                ));
            }
        };
        let frame: RadarSiteResult = self
            .call(&context, "radar.site", params, RADAR_TIMEOUT)
            .await?;
        frame
            .validate()
            .map_err(|reason| rejected(&context, "radar.site", reason))?;
        let body =
            decode(&frame.png_base64).map_err(|reason| rejected(&context, "radar.site", reason))?;
        if !body.starts_with(PNG_SIGNATURE)
            || u64::try_from(body.len()).ok() != Some(frame.byte_length)
        {
            return Err(rejected(
                &context,
                "radar.site",
                "the frame is not the PNG that was described".to_owned(),
            ));
        }
        Ok(shrf_frame(&frame.header(), &body))
    }

    /// The bundled WSR-88D catalogue backing the single-site picker.
    pub async fn radar_sites(&self) -> Result<Arc<RadarSiteCatalogPublic>, BackendError> {
        if let Some(sites) = cached(&self.radar_sites) {
            return Ok(sites);
        }
        let context = context(OP_RADAR_SITES, "radar site catalogue");
        let catalog: RadarSiteCatalog = self
            .call(&context, "radar.sites", json!({}), PLACES_TIMEOUT)
            .await?;
        catalog
            .validate()
            .map_err(|reason| rejected(&context, "radar.sites", reason))?;
        let catalog = Arc::new(catalog.into_public());
        *self
            .radar_sites
            .lock()
            .unwrap_or_else(PoisonError::into_inner) = Some(Arc::clone(&catalog));
        Ok(catalog)
    }

    /// Map place names; static for the life of the bridge.
    pub async fn place_labels(&self) -> Result<Arc<PlaceLabels>, BackendError> {
        if let Some(places) = cached(&self.places) {
            return Ok(places);
        }
        let context = context(OP_PLACES, "place names");
        let places: PlaceLabels = self
            .call(&context, "places.labels", json!({}), PLACES_TIMEOUT)
            .await?;
        places
            .validate()
            .map_err(|reason| rejected(&context, "places.labels", reason))?;
        let places = Arc::new(places);
        *self.places.lock().unwrap_or_else(PoisonError::into_inner) = Some(Arc::clone(&places));
        Ok(places)
    }

    /// The stations UWyo lists for one observation time.
    pub async fn station_availability(
        &self,
        observation_time: &str,
    ) -> Result<AvailabilityReport, BackendError> {
        let context = context(OP_AVAILABILITY, "UWyo station list");
        if !time_ok(observation_time) {
            return Err(invalid(
                &context,
                "observationTime must be an RFC 3339 time",
            ));
        }
        let result: AvailabilityResult = self
            .call(
                &context,
                "stations.availability",
                json!({ "observation_time": observation_time }),
                AVAILABILITY_TIMEOUT,
            )
            .await?;
        Ok(result.into_report())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_frame_body_starts_on_an_eight_byte_boundary() {
        for header in ["a", "ab", "abcdefgh", "abcdefghijk"] {
            let frame = shrf_frame(&header, &[1, 2, 3]);
            let length = u32::from_le_bytes([frame[8], frame[9], frame[10], frame[11]]) as usize;
            assert_eq!(&frame[..4], b"SHRF");
            assert!((SHRF_FIXED + length).is_multiple_of(8));
            assert_eq!(&frame[SHRF_FIXED + length..], &[1, 2, 3]);
            let json: String =
                serde_json::from_slice(&frame[SHRF_FIXED..SHRF_FIXED + length]).unwrap();
            assert_eq!(json, header);
        }
    }
}

//! Python map operations, with SHRF JSON headers and aligned binary bodies.
use std::sync::{Arc, Mutex, PoisonError};
use std::time::Duration;

pub use super::convert::shrf_frame;
use serde_json::json;

use crate::backend::router::Router;
use crate::backend::sounding_ops::{invalid, rejected};
use crate::dto::map::{
    AvailabilityReport, AvailabilityResult, FieldGridResult, MapCatalog, PlaceLabels, RadarResult,
    RadarSiteResult, SpcOutlook,
};
use crate::error::{BackendError, ErrorContext, PYTHON_PROVIDER};

pub const OP_MAP_CATALOG: &str = "get_map_catalog";
pub const OP_FIELD: &str = "get_weather_field";
pub const OP_OUTLOOK: &str = "get_spc_outlook";
pub const OP_RADAR: &str = "get_radar_frame";
pub const OP_RADAR_SITE: &str = "get_radar_site_frame";
pub const OP_PLACES: &str = "get_place_labels";
pub const OP_AVAILABILITY: &str = "get_station_availability";

const CATALOG_TIMEOUT: Duration = Duration::from_secs(30);
const FIELD_TIMEOUT: Duration = Duration::from_secs(120);
const OUTLOOK_TIMEOUT: Duration = Duration::from_secs(60);
const RADAR_TIMEOUT: Duration = Duration::from_secs(60);
const PLACES_TIMEOUT: Duration = Duration::from_secs(30);
const AVAILABILITY_TIMEOUT: Duration = Duration::from_secs(35);
const PNG_SIGNATURE: &[u8] = b"\x89PNG\r\n\x1a\n";

pub(crate) fn key_ok(key: &str) -> bool {
    (1..=40).contains(&key.len()) && key.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-')
}

pub(crate) fn time_ok(time: &str) -> bool {
    (10..=40).contains(&time.len()) && time.is_ascii()
}

pub(crate) fn context(operation: &str, resource: &str) -> ErrorContext {
    ErrorContext::new(operation, Some(PYTHON_PROVIDER), Some(resource))
}

fn cached<T>(slot: &Mutex<Option<Arc<T>>>) -> Option<Arc<T>> {
    slot.lock().unwrap_or_else(PoisonError::into_inner).clone()
}

impl Router {
    /// What the selected run can draw; untimed catalogues remain cached.
    pub async fn map_catalog(
        &self,
        model: &str,
        run_time: Option<&str>,
        forecast_hour: Option<u32>,
    ) -> Result<Arc<MapCatalog>, BackendError> {
        let context = context(OP_MAP_CATALOG, "map catalogue");
        if !key_ok(model)
            || run_time.is_some() != forecast_hour.is_some()
            || run_time.is_some_and(|time| !time_ok(time))
            || forecast_hour.is_some_and(|hour| hour > 384)
        {
            return Err(invalid(
                &context,
                "model must be a key; runTime and forecastHour must name one valid run and hour",
            ));
        }
        if let Some(catalog) = cached(&self.map_catalog)
            .filter(|catalog| run_time.is_none() && catalog.field_model == model)
        {
            return Ok(catalog);
        }
        let mut params = json!({ "model": model });
        if let Some(time) = run_time {
            params["run_time"] = json!(time);
            params["forecast_hour"] = json!(forecast_hour);
        }
        let timeout = if run_time.is_some() {
            FIELD_TIMEOUT
        } else {
            CATALOG_TIMEOUT
        };
        let catalog: MapCatalog = self.call(&context, "map.catalog", params, timeout).await?;
        catalog
            .validate()
            .map_err(|reason| rejected(&context, "map.catalog", reason))?;
        let catalog = Arc::new(catalog);
        if run_time.is_none() {
            let mut slot = self
                .map_catalog
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            *slot = Some(Arc::clone(&catalog));
        }
        Ok(catalog)
    }

    /// One selected model product/run/hour, as an SHRF frame of uint16 cells.
    pub async fn weather_field(
        &self,
        product: &str,
        run_time: &str,
        forecast_hour: u32,
        model: &str,
        request_id: Option<&str>,
    ) -> Result<Vec<u8>, BackendError> {
        let resource = format!("{model} {product} {run_time} F{forecast_hour:03}");
        let context = context(OP_FIELD, &resource);
        if !key_ok(model) || !key_ok(product) || !time_ok(run_time) || forecast_hour > 384 {
            return Err(invalid(
                &context,
                "model and product must be keys, runTime an RFC 3339 time, forecastHour 0 to 384",
            ));
        }
        let mut params = json!({ "model": model, "product": product, "run_time": run_time, "forecast_hour": forecast_hour });
        if let Some(id) = request_id {
            params["request_id"] = json!(id);
        }
        let grid: FieldGridResult = self
            .call(&context, "fields.grid", params, FIELD_TIMEOUT)
            .await?;
        grid.validate()
            .map_err(|reason| rejected(&context, "fields.grid", reason))?;
        let (frame, body_offset) = shrf_frame(&grid.header(), &grid.data_base64)
            .map_err(|reason| rejected(&context, "fields.grid", reason))?;
        let body = &frame[body_offset..];
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
        Ok(frame)
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

    /// A latest or genuinely time-addressed radar frame holding the service's PNG.
    pub async fn radar_frame(
        &self,
        product: Option<&str>,
        valid_time: Option<&str>,
    ) -> Result<Vec<u8>, BackendError> {
        let context = context(OP_RADAR, "MRMS radar mosaic");
        let mut params = match product {
            Some(key) if key_ok(key) => json!({ "product": key }),
            Some(_) => return Err(invalid(&context, "product must be a key")),
            None => json!({}),
        };
        if let Some(time) = valid_time {
            if !time_ok(time) {
                return Err(invalid(&context, "validTime must be an RFC 3339 time"));
            }
            params["valid_time"] = json!(time);
        }
        let frame: RadarResult = self
            .call(&context, "radar.mosaic", params, RADAR_TIMEOUT)
            .await?;
        frame
            .validate()
            .map_err(|reason| rejected(&context, "radar.mosaic", reason))?;
        if valid_time.is_some() && frame.valid_time.is_none() {
            return Err(rejected(
                &context,
                "radar.mosaic",
                "a requested scan has no actual valid_time".to_owned(),
            ));
        }
        let (bytes, body_offset) = shrf_frame(&frame.header(), &frame.png_base64)
            .map_err(|reason| rejected(&context, "radar.mosaic", reason))?;
        let body = &bytes[body_offset..];
        if !body.starts_with(PNG_SIGNATURE)
            || u64::try_from(body.len()).ok() != Some(frame.byte_length)
        {
            return Err(rejected(
                &context,
                "radar.mosaic",
                "the frame is not the PNG that was described".to_owned(),
            ));
        }
        Ok(bytes)
    }

    /// One antenna's own frame, pinned by site id or served for a map point.
    /// `site` is `Some(id)` to pin, else `latitude`/`longitude` serve the nearest.
    pub async fn radar_site_frame(
        &self,
        product: Option<&str>,
        site: Option<&str>,
        latitude: Option<f64>,
        longitude: Option<f64>,
        valid_time: Option<&str>,
        tilt: Option<&str>,
    ) -> Result<Vec<u8>, BackendError> {
        let id = site.or(product).unwrap_or("default");
        let context = context(OP_RADAR_SITE, &format!("single-site radar {id}"));
        let mut params = match (site, latitude, longitude) {
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
        if let Some(time) = valid_time {
            if !time_ok(time) {
                return Err(invalid(&context, "validTime must be an RFC 3339 time"));
            }
            params["valid_time"] = json!(time);
        }
        if let Some(cut) = tilt {
            if !matches!(cut, "x" | "y" | "z" | "0" | "a" | "1" | "b" | "2" | "3") {
                return Err(invalid(&context, "tilt must be a supported NEXRAD cut"));
            }
            params["tilt"] = json!(cut);
        }
        let native_request = serde_json::from_value(params.clone())
            .map_err(|reason| invalid(&context, &format!("Invalid radar request: {reason}")))?;
        match sharpmod_weather::australian_radar::client()
            .fetch(native_request, time::OffsetDateTime::now_utc())
            .await
        {
            Ok(Some(frame)) => {
                let frame: RadarSiteResult = serde_json::from_value(
                    serde_json::to_value(frame.as_ref())
                        .map_err(|reason| rejected(&context, "radar.site", reason.to_string()))?,
                )
                .map_err(|reason| rejected(&context, "radar.site", reason.to_string()))?;
                return shrf_frame(&frame.header(), &frame.png_base64)
                    .map(|(bytes, _)| bytes)
                    .map_err(|reason| rejected(&context, "radar.site", reason));
            }
            Ok(None) => {}
            Err(error) => {
                return Err(BackendError {
                    code: if matches!(error, sharpmod_weather::australian_radar::Error::Invalid(_))
                    {
                        crate::error::ErrorCode::InvalidArgument
                    } else {
                        crate::error::ErrorCode::OverlayFetchFailed
                    },
                    message: error.to_string(),
                    retryable: !matches!(
                        error,
                        sharpmod_weather::australian_radar::Error::Invalid(_)
                    ),
                    context: Some(Box::new(context)),
                    details: matches!(
                        error,
                        sharpmod_weather::australian_radar::Error::ScanMissing
                    )
                    .then(|| {
                        json!({"reason": "scanMissing"})
                            .as_object()
                            .expect("object")
                            .clone()
                    }),
                });
            }
        }
        // A cold Level II request downloads and decodes a complete volume.
        let timeout = if matches!(
            product.unwrap_or("reflectivity"),
            "reflectivity" | "correlation-coefficient"
        ) {
            Duration::from_secs(120)
        } else {
            RADAR_TIMEOUT
        };
        let frame: RadarSiteResult = self.call(&context, "radar.site", params, timeout).await?;
        frame
            .validate()
            .map_err(|reason| rejected(&context, "radar.site", reason))?;
        if valid_time.is_some() && frame.valid_time.is_none() {
            return Err(rejected(
                &context,
                "radar.site",
                "a requested scan has no actual valid_time".to_owned(),
            ));
        }
        let (bytes, body_offset) = shrf_frame(&frame.header(), &frame.png_base64)
            .map_err(|reason| rejected(&context, "radar.site", reason))?;
        let body = &bytes[body_offset..];
        if !body.starts_with(PNG_SIGNATURE)
            || u64::try_from(body.len()).ok() != Some(frame.byte_length)
        {
            return Err(rejected(
                &context,
                "radar.site",
                "the frame is not the PNG that was described".to_owned(),
            ));
        }
        Ok(bytes)
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

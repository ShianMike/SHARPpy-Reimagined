//! Python map operations, with SHRF JSON headers and aligned binary bodies.
use std::sync::{Arc, Mutex, PoisonError};
use std::time::Duration;

pub use super::convert::shrf_frame;
use serde_json::json;

use crate::backend::router::Router;
use crate::backend::sounding_ops::{invalid, rejected};
use crate::dto::map::{AvailabilityReport, FieldGridResult, MapCatalog, PlaceLabels, SpcOutlook};
use crate::dto::overlays::{Outlook, OutlookArea, Place};
use crate::dto::stations::{Station, StationAvailability as StationAvailabilityState};
use crate::error::{BackendError, ErrorCode, ErrorContext, PYTHON_PROVIDER};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

pub const OP_MAP_CATALOG: &str = "get_map_catalog";
pub const OP_FIELD: &str = "get_weather_field";
pub const OP_OUTLOOK: &str = "get_spc_outlook";
pub const OP_PLACES: &str = "get_place_labels";
pub const OP_AVAILABILITY: &str = "get_station_availability";

const CATALOG_TIMEOUT: Duration = Duration::from_secs(30);
const FIELD_TIMEOUT: Duration = Duration::from_secs(120);
const OUTLOOK_VERTEX_SCALE: f64 = 10_000.0;

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
        let when = OffsetDateTime::parse(valid_time, &Rfc3339)
            .map_err(|_| invalid(&context, "validTime must be an RFC 3339 time"))?;
        let product = if matches!(product, "cat" | "torn" | "wind" | "hail" | "prob") {
            product
        } else {
            "cat"
        };
        let layer = sharpmod_weather::spc_outlook::client()
            .fetch(when, OffsetDateTime::now_utc(), product)
            .await
            .map_err(|error| BackendError {
                code: ErrorCode::OverlayFetchFailed,
                message: format!("The SPC outlook could not be read: {error}."),
                retryable: true,
                context: Some(Box::new(context.clone())),
                details: None,
            })?;
        let round4 = |v: f64| (v * OUTLOOK_VERTEX_SCALE).round() / OUTLOOK_VERTEX_SCALE;
        let outlook = SpcOutlook {
            product: product.to_owned(),
            valid_time: Some(sharpmod_weather::format_rfc3339_utc(when)),
            outlook: layer.map(|layer| Outlook {
                title: layer.title.clone(),
                subtitle: layer.subtitle.clone(),
                valid_from: layer.valid_from.clone(),
                valid_to: layer.valid_to.clone(),
                issued: layer.issued.clone(),
                covers: layer.covers(when),
                source_url: layer.source_url.clone(),
                attribution: layer.attribution.clone(),
                areas: layer
                    .areas
                    .iter()
                    .map(|area| OutlookArea {
                        label: area.label.clone(),
                        description: area.description.clone(),
                        fill: Some(area.fill.clone()),
                        stroke: area.stroke.clone(),
                        rank: area.rank,
                        hatch: area.hatch,
                        hatch_level: area.hatch_level,
                        rings: area
                            .rings
                            .iter()
                            .map(|ring| {
                                ring.iter()
                                    .map(|&[lon, lat]| [round4(lon), round4(lat)])
                                    .collect()
                            })
                            .collect(),
                    })
                    .collect(),
            }),
        };
        outlook
            .validate()
            .map_err(|reason| rejected(&context, "outlooks.spc", reason))?;
        Ok(outlook)
    }

    /// Map place names; static for the life of the bridge.
    pub async fn place_labels(&self) -> Result<Arc<PlaceLabels>, BackendError> {
        if let Some(places) = cached(&self.places) {
            return Ok(places);
        }
        let context = context(OP_PLACES, "place names");
        let places = PlaceLabels {
            places: sharpmod_weather::place_names::place_labels()
                .iter()
                .map(|place| Place {
                    name: place.name.clone(),
                    lat: place.lat,
                    lon: place.lon,
                    tier: place.tier,
                })
                .collect(),
        };
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
        let when = OffsetDateTime::parse(observation_time, &Rfc3339)
            .map_err(|_| invalid(&context, "observationTime must be an RFC 3339 time"))?;
        let payload = sharpmod_weather::uwyo::client()
            .fetch(when, OffsetDateTime::now_utc())
            .await
            .map_err(|error| match error {
                sharpmod_weather::uwyo::AvailabilityError::Invalid(message) => {
                    invalid(&context, &message)
                }
                sharpmod_weather::uwyo::AvailabilityError::Unavailable(message) => BackendError {
                    code: ErrorCode::StationListUnavailable,
                    message: format!(
                        "The UWyo station list could not be loaded ({message}). The bundled list is still shown."
                    ),
                    retryable: true,
                    context: Some(Box::new(context)),
                    details: None,
                },
            })?;
        Ok(AvailabilityReport {
            observation_time: payload.observation_time,
            listed_ids: payload.listed_ids,
            extra_stations: payload
                .extra_stations
                .into_iter()
                .map(|record| Station {
                    id: record.id,
                    name: record.name,
                    latitude: record.lat,
                    longitude: record.lon,
                    source: record.src,
                    availability: StationAvailabilityState::Listed,
                })
                .collect(),
            dropped_count: payload.dropped_count,
        })
    }
}

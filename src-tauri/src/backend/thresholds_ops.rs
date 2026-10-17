//! Threshold operations, served by the Python bridge
//! (`sharpmod/bridge/thresholds.py`).
//!
//! | Operation | Bridge method | Deadline |
//! |---|---|---|
//! | `get_threshold_metrics` | `thresholds.metrics` | 60 s |
//! | `evaluate_threshold` | `thresholds.evaluate` | 300 s + 60 s per member |
//! | `export_threshold_csv` | `thresholds.export_csv` | 300 s + 60 s per member |
//!
//! The request names its members; each verdict keeps the reason a member could
//! not be used. Units for the form's conditions are filled from the registry in
//! the bridge, so no unit string crosses from the webview.

use std::time::Duration;

use serde_json::json;

use crate::backend::map_ops::context;
use crate::backend::router::Router;
use crate::backend::sounding_ops::{invalid, rejected};
use crate::dto::threshold_catalog::{ThresholdCsv, ThresholdMetrics};
use crate::dto::thresholds::{ThresholdDefinitionInput, ThresholdEvaluation};
use crate::error::BackendError;

pub const OP_THRESHOLD_METRICS: &str = "get_threshold_metrics";
pub const OP_EVALUATE_THRESHOLD: &str = "evaluate_threshold";
pub const OP_EXPORT_THRESHOLD_CSV: &str = "export_threshold_csv";

const METRICS_TIMEOUT: Duration = Duration::from_secs(60);
const EVALUATE_BASE: Duration = Duration::from_secs(300);
const EVALUATE_PER_MEMBER: Duration = Duration::from_secs(60);

fn validate_request(
    model: &str,
    latitude: f64,
    longitude: f64,
    forecast_hour: u32,
    definition: &ThresholdDefinitionInput,
) -> Result<(), String> {
    if model.is_empty() || model.len() > 40 {
        return Err("model must be 1 to 40 characters".into());
    }
    if !(-90.0..=90.0).contains(&latitude) {
        return Err("latitude must be within [-90, 90]".into());
    }
    if !(-180.0..=180.0).contains(&longitude) {
        return Err("longitude must be within [-180, 180]".into());
    }
    if forecast_hour > 1000 {
        return Err("forecastHour must be at most 1000".into());
    }
    definition.validate()
}

#[allow(clippy::too_many_arguments)] // Mirrors the fixed bridge request fields.
fn request_params(
    model: &str,
    latitude: f64,
    longitude: f64,
    forecast_hour: u32,
    run_time: Option<&str>,
    location: Option<&str>,
    members: &[String],
    definition: &ThresholdDefinitionInput,
) -> serde_json::Value {
    json!({
        "model": model,
        "latitude": latitude,
        "longitude": longitude,
        "forecast_hour": forecast_hour,
        "run_time": run_time,
        "location": location,
        "members": members,
        "definition": definition.bridge_value(),
    })
}

impl Router {
    /// Every registry diagnostic with its control bounds, for the form.
    pub async fn threshold_metrics(&self) -> Result<ThresholdMetrics, BackendError> {
        let context = context(OP_THRESHOLD_METRICS, "the threshold diagnostic catalog");
        let metrics: ThresholdMetrics = self
            .call(&context, "thresholds.metrics", json!({}), METRICS_TIMEOUT)
            .await?;
        metrics
            .validate()
            .map_err(|reason| rejected(&context, "thresholds.metrics", reason))?;
        Ok(metrics)
    }

    /// Where each requested member stands on one definition, with denominators.
    #[allow(clippy::too_many_arguments)]
    pub async fn evaluate_threshold(
        &self,
        model: &str,
        latitude: f64,
        longitude: f64,
        forecast_hour: u32,
        run_time: Option<&str>,
        location: Option<&str>,
        members: &[String],
        definition: ThresholdDefinitionInput,
    ) -> Result<ThresholdEvaluation, BackendError> {
        let context = context(
            OP_EVALUATE_THRESHOLD,
            &format!("the {model} ensemble at {latitude:.4}, {longitude:.4}"),
        );
        validate_request(model, latitude, longitude, forecast_hour, &definition)
            .map_err(|reason| invalid(&context, &reason))?;
        let params = request_params(
            model,
            latitude,
            longitude,
            forecast_hour,
            run_time,
            location,
            members,
            &definition,
        );
        let limit = EVALUATE_BASE + EVALUATE_PER_MEMBER * members.len() as u32;
        let result: ThresholdEvaluation = self
            .call(&context, "thresholds.evaluate", params, limit)
            .await?;
        result
            .validate()
            .map_err(|reason| rejected(&context, "thresholds.evaluate", reason))?;
        Ok(result)
    }

    /// The same verdicts as `export_threshold_csv` writes, as download text.
    #[allow(clippy::too_many_arguments)]
    pub async fn export_threshold_csv(
        &self,
        model: &str,
        latitude: f64,
        longitude: f64,
        forecast_hour: u32,
        run_time: Option<&str>,
        location: Option<&str>,
        members: &[String],
        definition: ThresholdDefinitionInput,
    ) -> Result<ThresholdCsv, BackendError> {
        let context = context(
            OP_EXPORT_THRESHOLD_CSV,
            &format!("the {model} ensemble at {latitude:.4}, {longitude:.4}"),
        );
        validate_request(model, latitude, longitude, forecast_hour, &definition)
            .map_err(|reason| invalid(&context, &reason))?;
        let params = request_params(
            model,
            latitude,
            longitude,
            forecast_hour,
            run_time,
            location,
            members,
            &definition,
        );
        let limit = EVALUATE_BASE + EVALUATE_PER_MEMBER * members.len() as u32;
        let exported: ThresholdCsv = self
            .call(&context, "thresholds.export_csv", params, limit)
            .await?;
        exported
            .validate()
            .map_err(|reason| rejected(&context, "thresholds.export_csv", reason))?;
        Ok(exported)
    }
}

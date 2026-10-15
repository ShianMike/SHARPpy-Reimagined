//! Ensemble summary operation, served by the Python bridge
//! (`sharpmod/bridge/ensemble.py`).
//!
//! | Operation | Bridge method | Deadline |
//! |---|---|---|
//! | `get_ensemble_summary` | `ensemble.summary` | 300 s + 60 s per member |
//!
//! The request names the members it wants and the result keeps the
//! acquisition ledger: what was asked for, what loaded, and why the rest did
//! not. One member's download failure is that member's row, not a failed
//! request.

use std::time::Duration;

use serde_json::json;

use crate::backend::map_ops::context;
use crate::backend::router::Router;
use crate::backend::sounding_ops::{invalid, rejected};
use crate::dto::ensemble::EnsembleSummary;
use crate::error::BackendError;

pub const OP_ENSEMBLE_SUMMARY: &str = "get_ensemble_summary";

const ENSEMBLE_BASE: Duration = Duration::from_secs(300);
const ENSEMBLE_PER_MEMBER: Duration = Duration::from_secs(60);

impl Router {
    /// The metric spreads of one ensemble run at one valid time.
    #[allow(clippy::too_many_arguments)] // Mirrors the published IPC request.
    pub async fn ensemble_summary(
        &self,
        model: &str,
        latitude: f64,
        longitude: f64,
        forecast_hour: u32,
        run_time: Option<&str>,
        location: Option<&str>,
        members: &[String],
    ) -> Result<EnsembleSummary, BackendError> {
        let context = context(
            OP_ENSEMBLE_SUMMARY,
            &format!("the {model} ensemble at {latitude:.4}, {longitude:.4}"),
        );
        if model.is_empty() || model.len() > 40 {
            return Err(invalid(&context, "model must be 1 to 40 characters"));
        }
        if !(-90.0..=90.0).contains(&latitude) {
            return Err(invalid(&context, "latitude must be within [-90, 90]"));
        }
        if !(-180.0..=180.0).contains(&longitude) {
            return Err(invalid(&context, "longitude must be within [-180, 180]"));
        }
        if forecast_hour > 1000 {
            return Err(invalid(&context, "forecastHour must be at most 1000"));
        }
        let params = json!({
            "model": model,
            "latitude": latitude,
            "longitude": longitude,
            "forecast_hour": forecast_hour,
            "run_time": run_time,
            "location": location,
            "members": members,
        });
        let limit = ENSEMBLE_BASE + ENSEMBLE_PER_MEMBER * members.len() as u32;
        let summary: EnsembleSummary = self
            .call(&context, "ensemble.summary", params, limit)
            .await?;
        summary
            .validate()
            .map_err(|reason| rejected(&context, "ensemble.summary", reason))?;
        Ok(summary)
    }
}

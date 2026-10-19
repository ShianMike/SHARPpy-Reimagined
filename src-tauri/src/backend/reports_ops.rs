//! Storm report and contour operations, served by the Python bridge
//! (`sharpmod/bridge/reports.py`, `sharpmod/bridge/contours.py`).
//!
//! | Operation | Bridge method | Deadline |
//! |---|---|---|
//! | `get_storm_reports` | `reports.storms` | 60 s |
//! | `get_report_options` | `reports.options`, cached for the session | 30 s |
//! | `get_field_contours` | `fields.contours` | 120 s |
//!
//! Reports cross as JSON point records and contours as JSON line segments,
//! not frames: each is numbers and short strings, and the frontend draws them.

use std::sync::{Arc, Mutex, PoisonError};
use std::time::Duration;

use serde_json::json;

use crate::backend::map_ops::{context, key_ok, time_ok};
use crate::backend::router::Router;
use crate::backend::sounding_ops::{invalid, rejected};
use crate::dto::contours::ContourLines;
use crate::dto::reports::{ReportOptions, StormReports};
use crate::error::BackendError;

pub const OP_REPORTS: &str = "get_storm_reports";
pub const OP_REPORT_OPTIONS: &str = "get_report_options";
pub const OP_CONTOURS: &str = "get_field_contours";

const REPORTS_TIMEOUT: Duration = Duration::from_secs(60);
const OPTIONS_TIMEOUT: Duration = Duration::from_secs(30);
const CONTOURS_TIMEOUT: Duration = Duration::from_secs(120);

fn filter_ok(filter: &str) -> bool {
    matches!(filter, "all" | "tornado" | "hail" | "wind" | "significant")
}

impl Router {
    /// Reports within a symmetric window of `valid_time`, after a hazard filter.
    pub async fn storm_reports(
        &self,
        valid_time: &str,
        window_s: Option<u32>,
        hazard_filter: Option<&str>,
    ) -> Result<StormReports, BackendError> {
        let context = context(OP_REPORTS, &format!("storm reports {valid_time}"));
        if !time_ok(valid_time) {
            return Err(invalid(&context, "validTime must be an RFC 3339 time"));
        }
        let mut params = json!({ "valid_time": valid_time });
        if let Some(seconds) = window_s {
            params["window_s"] = seconds.into();
        }
        match hazard_filter {
            Some(key) if filter_ok(key) => params["hazard_filter"] = key.into(),
            Some(_) => {
                return Err(invalid(
                    &context,
                    "hazardFilter must be \"all\", \"tornado\", \"hail\", \"wind\", or \"significant\"",
                ));
            }
            None => {}
        }
        let reports: StormReports = self
            .call(&context, "reports.storms", params, REPORTS_TIMEOUT)
            .await?;
        reports
            .validate()
            .map_err(|reason| rejected(&context, "reports.storms", reason))?;
        Ok(reports)
    }

    /// The provider's own window and filter labels; static for the bridge's life.
    pub async fn report_options(&self) -> Result<Arc<ReportOptions>, BackendError> {
        if let Some(options) = cached(&self.report_options) {
            return Ok(options);
        }
        let context = context(OP_REPORT_OPTIONS, "storm report options");
        let result: ReportOptions = self
            .call(&context, "reports.options", json!({}), OPTIONS_TIMEOUT)
            .await?;
        result
            .validate()
            .map_err(|reason| rejected(&context, "reports.options", reason))?;
        let options = Arc::new(result);
        *self
            .report_options
            .lock()
            .unwrap_or_else(PoisonError::into_inner) = Some(Arc::clone(&options));
        Ok(options)
    }
}

fn cached<T>(slot: &Mutex<Option<Arc<T>>>) -> Option<Arc<T>> {
    slot.lock().unwrap_or_else(PoisonError::into_inner).clone()
}

impl Router {
    /// The isolines of one product's contour field, as lon/lat segments.
    pub async fn field_contours(
        &self,
        product: &str,
        run_time: &str,
        forecast_hour: u32,
        model: &str,
        request_id: Option<&str>,
    ) -> Result<ContourLines, BackendError> {
        let context = context(
            OP_CONTOURS,
            &format!("{model} contour lines {product} {run_time} F{forecast_hour:03}"),
        );
        if !key_ok(model) || !key_ok(product) {
            return Err(invalid(&context, "model and product must be keys"));
        }
        if !time_ok(run_time) {
            return Err(invalid(&context, "runTime must be an RFC 3339 time"));
        }
        if forecast_hour > 84 {
            return Err(invalid(&context, "forecastHour must be 0 to 84"));
        }
        let mut params = json!({ "model": model, "product": product, "run_time": run_time, "forecast_hour": forecast_hour });
        if let Some(id) = request_id {
            params["request_id"] = json!(id);
        }
        let lines: ContourLines = self
            .call(&context, "fields.contours", params, CONTOURS_TIMEOUT)
            .await?;
        lines
            .validate()
            .map_err(|reason| rejected(&context, "fields.contours", reason))?;
        Ok(lines)
    }
}

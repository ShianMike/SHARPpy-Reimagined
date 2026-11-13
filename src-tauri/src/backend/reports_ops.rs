//! Storm report (native `sharpmod_weather::reports`) and contour operations
//! (`sharpmod/bridge/contours.py`).
//!
//! | Operation | Bridge method | Deadline |
//! |---|---|---|
//! | `get_storm_reports` | native (`sharpmod_weather::reports`) | — |
//! | `get_report_options` | native (`sharpmod_weather::reports`) | — |
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
use crate::dto::reports::{ReportFilter, ReportOptions, ReportPoint, ReportWindow, StormReports};
use crate::error::{BackendError, ErrorCode};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

pub const OP_REPORTS: &str = "get_storm_reports";
pub const OP_REPORT_OPTIONS: &str = "get_report_options";
pub const OP_CONTOURS: &str = "get_field_contours";

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
        let when = OffsetDateTime::parse(valid_time, &Rfc3339)
            .map_err(|_| invalid(&context, "validTime must be an RFC 3339 time"))?;
        let window_s = match window_s {
            Some(seconds @ (7200 | 21600 | 43200 | 86400)) => seconds,
            Some(_) => {
                return Err(invalid(
                    &context,
                    "window_s must be one of [7200, 21600, 43200, 86400]",
                ));
            }
            None => 21600,
        };
        if hazard_filter.is_some_and(|key| !filter_ok(key)) {
            return Err(invalid(
                &context,
                "hazardFilter must be \"all\", \"tornado\", \"hail\", \"wind\", or \"significant\"",
            ));
        }
        let snapshot = sharpmod_weather::reports::shared_client()
            .fetch_snapshot(when, window_s, hazard_filter, OffsetDateTime::now_utc())
            .await
            .map_err(|error| BackendError {
                code: ErrorCode::OverlayFetchFailed,
                message: format!("The storm reports could not be loaded: {error}."),
                retryable: true,
                context: Some(Box::new(context.clone())),
                details: None,
            })?;
        let reports = StormReports {
            valid_time: snapshot.valid_time,
            window_s: snapshot.window_s,
            window_label: snapshot.window_label,
            hazard_filter: snapshot.hazard_filter,
            total_count: snapshot.total_count,
            shown_count: snapshot.shown_count,
            subtitle: snapshot.subtitle,
            valid_from: snapshot.valid_from,
            valid_to: snapshot.valid_to,
            source_url: snapshot.source_url,
            attribution: snapshot.attribution,
            reports: snapshot
                .reports
                .into_iter()
                .map(|r| ReportPoint {
                    label: r.label,
                    description: r.description,
                    fill: r.fill,
                    stroke: r.stroke,
                    radius_px: r.radius_px,
                    symbol: r.symbol,
                    caption: r.caption,
                    significant: r.significant,
                    category: r.category,
                    valid: r.valid,
                    lon: r.lon,
                    lat: r.lat,
                })
                .collect(),
        };
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
        let payload = sharpmod_weather::reports::report_options();
        let result = ReportOptions {
            windows: payload
                .windows
                .into_iter()
                .map(|w| ReportWindow {
                    label: w.label,
                    seconds: w.seconds,
                })
                .collect(),
            filters: payload
                .filters
                .into_iter()
                .map(|f| ReportFilter {
                    key: f.key,
                    label: f.label,
                })
                .collect(),
        };
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
        if forecast_hour > 384 {
            return Err(invalid(&context, "forecastHour must be 0 to 384"));
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

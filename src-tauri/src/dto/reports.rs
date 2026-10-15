//! Storm report DTOs: typed point records and the provider's control options.
//! Read from the bridge in snake_case, written to the webview in camelCase,
//! like `overlays::Place` (one struct per direction pair).

use serde::{Deserialize, Serialize};

use crate::dto::checks::{check_lat_lon, check_text};

pub const MAX_REPORTS: usize = 500;

/// One local storm report: what it is, where, when, and how to mark it.
/// `radius_px` and `symbol` are the provider's own screen-marker spec.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct ReportPoint {
    pub label: String,
    pub description: String,
    pub fill: Option<String>,
    pub stroke: String,
    pub radius_px: f64,
    pub symbol: String,
    pub caption: String,
    pub significant: bool,
    pub category: String,
    pub valid: Option<String>,
    #[serde(rename(serialize = "longitude", deserialize = "lon"))]
    pub lon: f64,
    #[serde(rename(serialize = "latitude", deserialize = "lat"))]
    pub lat: f64,
}

/// `reports.storms` as the bridge sends it and the webview reads it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct StormReports {
    pub valid_time: String,
    pub window_s: u32,
    pub window_label: String,
    pub hazard_filter: String,
    pub total_count: u32,
    pub shown_count: u32,
    pub subtitle: String,
    pub valid_from: Option<String>,
    pub valid_to: Option<String>,
    pub source_url: String,
    pub attribution: String,
    pub reports: Vec<ReportPoint>,
}

/// `reports.options`: the provider's own window and filter labels.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct ReportOptions {
    pub windows: Vec<ReportWindow>,
    pub filters: Vec<ReportFilter>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct ReportWindow {
    pub label: String,
    pub seconds: u32,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct ReportFilter {
    pub key: String,
    pub label: String,
}

fn colour_ok(text: &str) -> bool {
    text.len() == 7 && text.starts_with('#') && text[1..].bytes().all(|b| b.is_ascii_hexdigit())
}

fn time_ok(value: &Option<String>) -> Result<(), String> {
    match value {
        None => Ok(()),
        Some(text) if (10..=40).contains(&text.chars().count()) && text.is_ascii() => Ok(()),
        Some(_) => Err("a report time must be an RFC 3339 time".into()),
    }
}

impl ReportPoint {
    fn validate(&self) -> Result<(), String> {
        check_text("label", &self.label, 1, 120)?;
        check_text("symbol", &self.symbol, 1, 16)?;
        check_lat_lon(Some(self.lat), Some(self.lon))?;
        time_ok(&self.valid)?;
        if !colour_ok(&self.stroke) || self.fill.as_deref().is_some_and(|c| !colour_ok(c)) {
            return Err("report colours must be #rrggbb".into());
        }
        if !(0.0..=20.0).contains(&self.radius_px) {
            return Err("report radius is out of range".into());
        }
        Ok(())
    }
}

impl StormReports {
    pub fn validate(&self) -> Result<(), String> {
        check_text("validTime", &self.valid_time, 10, 40)?;
        check_text("hazardFilter", &self.hazard_filter, 1, 24)?;
        time_ok(&self.valid_from)?;
        time_ok(&self.valid_to)?;
        if self.reports.len() > MAX_REPORTS {
            return Err("too many storm reports".into());
        }
        if self.shown_count as usize != self.reports.len() || self.total_count < self.shown_count {
            return Err("report counts disagree with the records".into());
        }
        self.reports.iter().try_for_each(ReportPoint::validate)
    }
}

impl ReportOptions {
    pub fn validate(&self) -> Result<(), String> {
        if self.windows.is_empty() || self.windows.len() > 8 || self.filters.len() > 8 {
            return Err("report options are out of range".into());
        }
        for window in &self.windows {
            check_text("window label", &window.label, 1, 24)?;
            if window.seconds == 0 {
                return Err("a report window must have a span".into());
            }
        }
        for filter in &self.filters {
            check_text("filter key", &filter.key, 1, 24)?;
            check_text("filter label", &filter.label, 1, 60)?;
        }
        Ok(())
    }
}

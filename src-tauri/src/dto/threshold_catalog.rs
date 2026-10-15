//! `get_threshold_metrics` and `export_threshold_csv` DTOs: the threshold
//! form's diagnostic catalog with its numeric-control bounds, and the same
//! verdicts `export_threshold_csv` writes as download text. Read from the
//! bridge in snake_case, written to the webview in camelCase, like
//! `dto::thresholds`. Every label, unit, and guidance sentence arrives from
//! sharpmod, so the form presents the registry's own numbers without naming
//! or computing any quantity.
//!
//! Rust is the trust boundary in front of the webview: every payload is
//! checked with `validate` before it is returned, and the TypeScript parser in
//! `lib/contracts/thresholds.ts` mirrors each rule here.

use serde::{Deserialize, Serialize};

use crate::dto::checks::{check_text, unique};
use crate::dto::thresholds::{MAX_CONDITIONS, MAX_METRIC, OPERATORS};

pub const MAX_CSV_CHARS: usize = 300_000;

/// One registry diagnostic with its numeric-control bounds, for the form.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct ThresholdMetric {
    pub key: String,
    pub label: String,
    pub units: String,
    pub display_label: String,
    pub decimals: u8,
    pub minimum: f64,
    pub maximum: f64,
    pub step: f64,
    pub suggested: f64,
    pub guidance: String,
}

impl ThresholdMetric {
    fn validate(&self) -> Result<(), String> {
        check_text("key", &self.key, 1, MAX_METRIC)?;
        check_text("label", &self.label, 1, 120)?;
        check_text("units", &self.units, 0, 40)?;
        check_text("displayLabel", &self.display_label, 1, 160)?;
        check_text("guidance", &self.guidance, 1, 300)?;
        if self.decimals > 6 {
            return Err("decimals must be at most 6".into());
        }
        if ![self.minimum, self.maximum, self.step, self.suggested]
            .iter()
            .all(|v| v.is_finite())
        {
            return Err("a control bound must be finite".into());
        }
        if self.minimum >= self.maximum {
            return Err("a control range must read minimum to maximum".into());
        }
        if self.step <= 0.0 {
            return Err("a control step must be positive".into());
        }
        if !(self.minimum..=self.maximum).contains(&self.suggested) {
            return Err("a suggested value must lie within the control range".into());
        }
        Ok(())
    }
}

/// The threshold form's catalog: every registry diagnostic with its control
/// bounds, the comparison rules in display order, and the diagnostics the
/// form starts with.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct ThresholdMetrics {
    pub metrics: Vec<ThresholdMetric>,
    pub operators: Vec<String>,
    pub default_metrics: Vec<String>,
}

impl ThresholdMetrics {
    pub fn validate(&self) -> Result<(), String> {
        if self.metrics.is_empty() || self.metrics.len() > 64 {
            return Err("metrics must hold 1 to 64 rows".into());
        }
        let keys: Vec<String> = self.metrics.iter().map(|m| m.key.clone()).collect();
        unique(&keys, "metrics")?;
        for metric in &self.metrics {
            metric.validate()?;
        }
        if self.operators != OPERATORS {
            return Err("operators must list the six comparison rules in order".into());
        }
        if self.default_metrics.is_empty() || self.default_metrics.len() > MAX_CONDITIONS {
            return Err("defaultMetrics must hold 1 to 3 diagnostics".into());
        }
        unique(&self.default_metrics, "defaultMetrics")?;
        for key in &self.default_metrics {
            if !keys.contains(key) {
                return Err("a default metric must come from the catalog".into());
            }
        }
        Ok(())
    }
}

/// The same verdicts `export_threshold_csv` writes, as download text.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct ThresholdCsv {
    pub file_name: String,
    pub csv: String,
}

impl ThresholdCsv {
    pub fn validate(&self) -> Result<(), String> {
        check_text("fileName", &self.file_name, 1, 80)?;
        if self.csv.is_empty() || self.csv.chars().count() > MAX_CSV_CHARS {
            return Err("csv must hold 1 to 300000 characters".into());
        }
        if !self.csv.contains('\n') {
            return Err("csv must carry its rows".into());
        }
        Ok(())
    }
}

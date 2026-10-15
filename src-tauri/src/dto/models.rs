//! `get_model_catalog` DTOs: the forecast models that can produce a point
//! sounding. Read from the bridge in snake_case, written to the webview in
//! camelCase.

use serde::{Deserialize, Serialize};

use crate::dto::checks::check_text;

pub const MAX_MODELS: usize = 64;
pub const MAX_OUTLINE_POINTS: usize = 4096;
pub const MAX_HOURS: usize = 1024;
pub const MAX_RUNS: usize = 64;
pub const MAX_MEMBERS: usize = 32;

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Bounds {
    pub west: f64,
    pub east: f64,
    pub south: f64,
    pub north: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct ForecastModel {
    pub key: String,
    pub label: String,
    pub domain: String,
    pub bounds: Bounds,
    /// The real grid perimeter as `[lon, lat]` points; empty when only bounds are known.
    pub outline: Vec<[f64; 2]>,
    pub cycles: Vec<u8>,
    pub forecast_hours: Vec<u32>,
    /// The last forecast hour of each cycle, 00Z to 23Z.
    pub max_forecast_hours: Vec<u32>,
    pub default_forecast_hour: u32,
    pub grid_spacing_km: Option<f64>,
    pub notes: String,
    /// The ensemble member labels this model accepts, control member first;
    /// empty for a deterministic model. The frontend names no member itself.
    pub members: Vec<String>,
    /// Newest first, RFC 3339 UTC.
    pub recent_runs: Vec<String>,
    pub suggested_run: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct ModelCatalog {
    pub models: Vec<ForecastModel>,
    pub generated_at: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum ModelStatusState {
    Available,
    Partial,
    Unavailable,
    Unknown,
    Configured,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct ModelStatus {
    pub model: String,
    pub provider: String,
    pub run_time: String,
    pub forecast_hour: u32,
    pub state: ModelStatusState,
    pub message: String,
    pub checked_at: String,
}

impl ModelStatus {
    pub fn validate(&self) -> Result<(), String> {
        check_text("model", &self.model, 1, 40)?;
        check_text("provider", &self.provider, 1, 120)?;
        check_text("runTime", &self.run_time, 1, 40)?;
        check_text("checkedAt", &self.checked_at, 1, 40)?;
        check_text("message", &self.message, 1, 500)?;
        if self.forecast_hour > 1000 {
            return Err("forecastHour is out of range".into());
        }
        Ok(())
    }
}

fn in_range(value: f64, limit: f64) -> bool {
    (-limit..=limit).contains(&value)
}

impl ForecastModel {
    fn validate(&self) -> Result<(), String> {
        check_text("key", &self.key, 1, 40)?;
        check_text("label", &self.label, 1, 120)?;
        check_text("domain", &self.domain, 0, 120)?;
        check_text("notes", &self.notes, 0, 2000)?;
        if self.members.len() > MAX_MEMBERS {
            return Err("a model lists at most 32 members".into());
        }
        for member in &self.members {
            check_text("member", member, 1, 16)?;
        }
        let unique: std::collections::BTreeSet<&str> =
            self.members.iter().map(String::as_str).collect();
        if unique.len() != self.members.len() {
            return Err("a model's members must be unique".into());
        }
        if self.outline.len() > MAX_OUTLINE_POINTS
            || self.forecast_hours.is_empty()
            || self.forecast_hours.len() > MAX_HOURS
            || self.max_forecast_hours.len() != 24
            || self
                .max_forecast_hours
                .iter()
                .any(|h| !self.forecast_hours.contains(h))
            || self.recent_runs.is_empty()
            || self.recent_runs.len() > MAX_RUNS
            || self.cycles.iter().any(|c| *c > 23)
        {
            return Err("outline, hours, runs, or cycles are out of range".into());
        }
        let b = self.bounds;
        let bounds_ok = in_range(b.south, 90.0)
            && in_range(b.north, 90.0)
            && in_range(b.west, 180.0)
            && in_range(b.east, 180.0);
        let outline_ok = self
            .outline
            .iter()
            .all(|[lon, lat]| in_range(*lon, 180.0) && in_range(*lat, 90.0));
        if bounds_ok && outline_ok {
            Ok(())
        } else {
            Err("bounds or outline are out of range".into())
        }
    }
}

impl ModelCatalog {
    pub fn validate(&self) -> Result<(), String> {
        if self.models.len() > MAX_MODELS {
            return Err("too many models".into());
        }
        for (i, model) in self.models.iter().enumerate() {
            model
                .validate()
                .map_err(|reason| format!("models[{i}] ({:?}): {reason}", model.key))?;
        }
        Ok(())
    }
}

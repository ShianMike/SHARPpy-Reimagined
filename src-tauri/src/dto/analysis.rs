//! `get_sounding_analysis` DTOs. Every quantity arrives labelled (key, label,
//! units, decimals) from sharpmod, so the webview renders tables without
//! naming or computing any of them.

use serde::{Deserialize, Serialize};

use crate::dto::checks::{check_opt_text, check_text};

pub const MAX_GROUPS: usize = 32;
pub const MAX_PARAMETERS: usize = 256;
pub const MAX_COLUMNS: usize = 16;
pub const MAX_ROWS: usize = 16;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TableColumn {
    pub key: String,
    pub label: String,
    pub units: String,
    pub decimals: u8,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TableRow {
    pub key: String,
    pub label: String,
    pub short: String,
    /// One value per column; `null` when sharpmod has no value.
    pub values: Vec<Option<f64>>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DataTable {
    pub title: String,
    pub columns: Vec<TableColumn>,
    pub rows: Vec<TableRow>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Notable {
    High,
    Low,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AnalysisParameter {
    pub key: String,
    pub label: String,
    pub units: String,
    pub decimals: u8,
    pub notable: Notable,
    pub value: Option<f64>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AnalysisGroup {
    pub name: String,
    pub parameters: Vec<AnalysisParameter>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct SoundingAnalysis {
    pub sounding_id: String,
    pub valid_time: Option<String>,
    /// The SPC possible hazard type the sounding image shows.
    pub watch_type: Option<String>,
    /// sharpmod's reformulated hazard classification.
    pub hazard: String,
    pub parcel_table: DataTable,
    pub motion_table: DataTable,
    pub groups: Vec<AnalysisGroup>,
}

pub const MAX_TREND_POINTS: usize = 400;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TrendPoint {
    /// RFC 3339 UTC valid time; `null` when the collection has no date for it.
    pub time: Option<String>,
    /// One value per column; `null` when sharpmod has no value.
    pub values: Vec<Option<f64>>,
    /// Why the point has no values, when it has none.
    pub note: Option<String>,
}

/// `get_sounding_trends`: the legacy Trends metrics at every valid time.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct SoundingTrends {
    pub sounding_id: String,
    pub title: String,
    pub columns: Vec<TableColumn>,
    pub points: Vec<TrendPoint>,
    /// More valid times exist than `points` holds.
    pub truncated: bool,
}

impl SoundingTrends {
    pub fn validate(&self) -> Result<(), String> {
        check_text("soundingId", &self.sounding_id, 1, 64)?;
        check_text("title", &self.title, 1, 80)?;
        if self.columns.len() > MAX_COLUMNS || self.points.len() > MAX_TREND_POINTS {
            return Err("the trend is too large".into());
        }
        for c in &self.columns {
            check_label_set(&c.key, &c.label, &c.units, c.decimals)?;
        }
        for point in &self.points {
            check_opt_text("time", point.time.as_ref(), 40)?;
            check_opt_text("note", point.note.as_ref(), 200)?;
            if point.values.len() != self.columns.len() {
                return Err("a trend point does not have one value per column".into());
            }
        }
        Ok(())
    }
}

fn check_label_set(key: &str, label: &str, units: &str, decimals: u8) -> Result<(), String> {
    check_text("key", key, 1, 40)?;
    check_text("label", label, 1, 80)?;
    check_text("units", units, 0, 20)?;
    if decimals > 6 {
        return Err(format!("{key}: decimals must be at most 6"));
    }
    Ok(())
}

impl DataTable {
    fn validate(&self) -> Result<(), String> {
        check_text("table title", &self.title, 1, 80)?;
        if self.columns.len() > MAX_COLUMNS || self.rows.len() > MAX_ROWS {
            return Err("the table is too large".into());
        }
        for c in &self.columns {
            check_label_set(&c.key, &c.label, &c.units, c.decimals)?;
        }
        for r in &self.rows {
            check_text("row key", &r.key, 1, 40)?;
            check_text("row label", &r.label, 1, 80)?;
            check_text("row short label", &r.short, 0, 12)?;
            if r.values.len() != self.columns.len() {
                return Err(format!(
                    "row {} has {} values for {} columns",
                    r.key,
                    r.values.len(),
                    self.columns.len()
                ));
            }
        }
        Ok(())
    }
}

impl SoundingAnalysis {
    pub fn validate(&self) -> Result<(), String> {
        check_text("soundingId", &self.sounding_id, 1, 64)?;
        check_text("hazard", &self.hazard, 1, 60)?;
        check_opt_text("watchType", self.watch_type.as_ref(), 60)?;
        check_opt_text("validTime", self.valid_time.as_ref(), 40)?;
        self.parcel_table.validate()?;
        self.motion_table.validate()?;
        if self.groups.len() > MAX_GROUPS {
            return Err("too many groups".into());
        }
        let count: usize = self.groups.iter().map(|g| g.parameters.len()).sum();
        if count > MAX_PARAMETERS {
            return Err("too many parameters".into());
        }
        for group in &self.groups {
            check_text("group name", &group.name, 1, 80)?;
            for p in &group.parameters {
                check_label_set(&p.key, &p.label, &p.units, p.decimals)?;
            }
        }
        Ok(())
    }
}

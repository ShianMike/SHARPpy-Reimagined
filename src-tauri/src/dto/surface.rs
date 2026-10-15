//! Surface station-plot DTOs: typed point records plus the display slots the
//! frontend draws the station models from. Read from the bridge in snake_case,
//! written to the webview in camelCase, like `overlays::Place` (one struct per
//! direction pair).

use serde::{Deserialize, Serialize};

use crate::dto::checks::{check_lat_lon, check_text};

pub const MAX_PLOTS: usize = 64;

/// The wind staff as the paint path's screen spec: the direction in screen
/// degrees and the pennant, barb, and half-barb counts along it. The counts
/// are the provider's own speed convention; laying them out is the frontend's.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct StaffSpec {
    pub angle_deg: f64,
    pub pennants: u32,
    pub barbs: u32,
    pub halves: u32,
}

/// One station model: where it is, what was measured, and the display slots
/// the plot draws. `upper_text` and `lower_text` are the plot's own reading
/// pair at its whole-degree precision; `label` and `description` are the hover
/// block in the provider's words.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct SurfacePlot {
    pub id: String,
    pub longitude: f64,
    pub latitude: f64,
    pub valid: Option<String>,
    pub offset_min: Option<f64>,
    pub stale: bool,
    pub availability: String,
    pub wind_state: String,
    pub staff: Option<StaffSpec>,
    pub upper_text: String,
    pub lower_text: String,
    pub label: String,
    pub description: String,
}

/// What the fetch checked and found, before any drawing.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct SurfacePlotCounts {
    pub matched: u32,
    pub stale: u32,
    pub no_observation: u32,
    pub failed: u32,
    pub nearby: u32,
    pub queried: u32,
    pub unqueried: u32,
}

/// `surface.plots` as the bridge sends it and the webview reads it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct SurfacePlots {
    pub valid_time: String,
    pub counts: SurfacePlotCounts,
    pub plots: Vec<SurfacePlot>,
}

fn time_ok(value: &Option<String>) -> Result<(), String> {
    match value {
        None => Ok(()),
        Some(text) if (10..=40).contains(&text.chars().count()) && text.is_ascii() => Ok(()),
        Some(_) => Err("a plot time must be an RFC 3339 time".into()),
    }
}

impl StaffSpec {
    fn validate(&self) -> Result<(), String> {
        if !(0.0..360.0).contains(&self.angle_deg) {
            return Err("a staff angle must be within [0, 360)".into());
        }
        if self.pennants > 5 || self.barbs > 4 || self.halves > 1 {
            return Err("a staff carries at most 5 pennants, 4 barbs, and 1 half".into());
        }
        Ok(())
    }
}

impl SurfacePlot {
    fn validate(&self) -> Result<(), String> {
        check_text("id", &self.id, 1, 16)?;
        check_lat_lon(Some(self.latitude), Some(self.longitude))?;
        time_ok(&self.valid)?;
        check_text("label", &self.label, 1, 120)?;
        check_text("description", &self.description, 1, 600)?;
        check_text("upperText", &self.upper_text, 1, 4)?;
        check_text("lowerText", &self.lower_text, 1, 4)?;
        if !matches!(
            self.availability.as_str(),
            "available" | "unavailable" | "unknown"
        ) {
            return Err("an availability must be available, unavailable, or unknown".into());
        }
        if !matches!(
            self.wind_state.as_str(),
            "available" | "calm" | "missing" | "direction-missing"
        ) {
            return Err(
                "a wind state must be available, calm, missing, or direction-missing".into(),
            );
        }
        match self.offset_min {
            Some(offset) if self.valid.is_some() && (-60.0..=60.0).contains(&offset) => {}
            Some(_) => return Err("a plot offset must belong to a plot with a time".into()),
            None if self.valid.is_none() => {}
            None => return Err("a plot with a time carries its offset".into()),
        }
        if self.staff.is_some() != (self.wind_state == "available") {
            return Err("a wind staff belongs to an available wind only".into());
        }
        match &self.staff {
            Some(staff) => staff.validate(),
            None => Ok(()),
        }
    }
}

impl SurfacePlots {
    pub fn validate(&self) -> Result<(), String> {
        check_text("validTime", &self.valid_time, 10, 40)?;
        if self.plots.len() > MAX_PLOTS {
            return Err("too many station plots".into());
        }
        let mut seen = std::collections::BTreeSet::new();
        for plot in &self.plots {
            if !seen.insert(plot.id.as_str()) {
                return Err("a station appears twice in the plots".into());
            }
        }
        if self.counts.unqueried + self.counts.queried != self.counts.nearby {
            return Err("the station counts do not add up".into());
        }
        let tallies = [
            ("matched", self.counts.matched),
            ("stale", self.counts.stale),
            ("noObservation", self.counts.no_observation),
            ("failed", self.counts.failed),
        ];
        let observed = [
            self.plots
                .iter()
                .filter(|plot| plot.availability == "available")
                .count() as u32,
            self.plots.iter().filter(|plot| plot.stale).count() as u32,
            self.plots
                .iter()
                .filter(|plot| plot.availability == "unavailable")
                .count() as u32,
            self.plots
                .iter()
                .filter(|plot| plot.availability == "unknown")
                .count() as u32,
        ];
        for ((name, count), seen) in tallies.into_iter().zip(observed) {
            if count != seen {
                return Err(format!("the {name} count disagrees with the records"));
            }
        }
        self.plots.iter().try_for_each(SurfacePlot::validate)
    }
}

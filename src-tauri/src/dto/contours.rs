//! Contour-line DTOs: the isolines of one product's contour field.
//! Read from the bridge in snake_case, written to the webview in camelCase.

use base64::Engine as _;
use serde::{Deserialize, Serialize};

use crate::dto::checks::{check_lat_lon, check_text};
use crate::dto::models::Bounds;

pub const MAX_SEGMENTS: usize = 100_000;
const MAX_PATH_POINTS: usize = 200_000;

/// Display winds: low 9 bits are from degrees, high 7 bits are speed / 5 kt.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct WindBarbGrid {
    pub width: u32,
    pub height: u32,
    pub bounds: Bounds,
    pub data_base64: String,
}

impl WindBarbGrid {
    pub fn validate(&self) -> Result<(), String> {
        let count = u64::from(self.width) * u64::from(self.height);
        if self.width == 0
            || self.height == 0
            || self.width > 4096
            || self.height > 4096
            || count > 8_000_000
        {
            return Err("wind grid dimensions are out of range".into());
        }
        check_lat_lon(Some(self.bounds.south), Some(self.bounds.west))?;
        check_lat_lon(Some(self.bounds.north), Some(self.bounds.east))?;
        if self.bounds.west >= self.bounds.east || self.bounds.south >= self.bounds.north {
            return Err("wind bounds are empty".into());
        }
        if self.data_base64.len() as u64 != (count * 2).div_ceil(3) * 4 {
            return Err("wind grid has the wrong encoded length".into());
        }
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(&self.data_base64)
            .map_err(|_| "wind grid is not base64")?;
        if bytes.len() as u64 != count * 2
            || bytes.as_chunks::<2>().0.iter().any(|pair| {
                let cell = u16::from_le_bytes(*pair);
                cell != u16::MAX && (cell & 511) > 359
            })
        {
            return Err("wind grid contains invalid cells".into());
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ContourLabel {
    pub position: [f64; 2],
    pub value: f64,
}

/// `fields.contours` as the bridge sends it and the webview reads it.
/// Each segment is two `[lon, lat]` endpoints in the field frame.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct ContourLines {
    pub product: String,
    pub run_time: String,
    pub forecast_hour: u32,
    pub unit: String,
    pub interval: f64,
    pub segments: Vec<[[f64; 2]; 2]>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub paths: Vec<Vec<[f64; 2]>>,
    pub wind: Option<WindBarbGrid>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub labels: Vec<ContourLabel>,
}

impl ContourLines {
    pub fn validate(&self) -> Result<(), String> {
        check_text("product", &self.product, 1, 40)?;
        check_text("runTime", &self.run_time, 10, 40)?;
        check_text("unit", &self.unit, 0, 40)?;
        if let Some(wind) = &self.wind {
            wind.validate()?;
        }
        if !self.interval.is_finite() || self.interval <= 0.0 {
            return Err("contour interval is out of range".into());
        }
        if self.segments.len() > MAX_SEGMENTS {
            return Err("too many contour segments".into());
        }
        let mut remaining = MAX_PATH_POINTS;
        for path in &self.paths {
            if path.len() < 2 || path.len() > remaining {
                return Err("contour paths exceed the point budget or are incomplete".into());
            }
            remaining -= path.len();
        }
        for [lon, lat] in self
            .segments
            .iter()
            .flatten()
            .chain(self.paths.iter().flatten())
        {
            if !(-180.0..=180.0).contains(lon) || !(-90.0..=90.0).contains(lat) {
                return Err("contour point is off the globe".into());
            }
        }
        if self.labels.len() > 256 {
            return Err("too many contour labels".into());
        }
        for label in &self.labels {
            let [lon, lat] = label.position;
            let multiple = label.value / self.interval;
            if !(-180.0..=180.0).contains(&lon)
                || !(-90.0..=90.0).contains(&lat)
                || !multiple.is_finite()
                || (multiple - multiple.round()).abs() > 1e-8
            {
                return Err("contour label is not a valid point and level".into());
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn optional_labels_preserve_legacy_frames_and_validate_pressure_levels() {
        let legacy = serde_json::json!({"product":"mslp","run_time":"2026-11-06T00:00:00Z","forecast_hour":0,"unit":"hPa","interval":4.0,"segments":[[[-100.0,35.0],[-99.0,36.0]]],"wind":null});
        let frame: ContourLines = serde_json::from_value(legacy.clone()).unwrap();
        frame.validate().unwrap();
        assert!(serde_json::to_value(frame).unwrap().get("labels").is_none());
        let mut labelled = legacy;
        labelled["labels"] = serde_json::json!([{"position":[-99.5,35.5],"value":1012.0}]);
        let mut frame: ContourLines = serde_json::from_value(labelled).unwrap();
        frame.validate().unwrap();
        frame.labels[0].value = 1013.0;
        assert!(frame.validate().is_err());
        frame.labels[0].value = 1012.0;
        frame.labels[0].position[1] = 91.0;
        assert!(frame.validate().is_err());
        frame.labels[0].position[1] = 35.5;
        frame.labels = vec![frame.labels[0].clone(); 257];
        assert!(frame.validate().is_err());
    }

    #[test]
    fn joined_pressure_paths_round_trip_and_enforce_the_geometry_budget() {
        let raw = serde_json::json!({"product":"mslp","run_time":"2026-11-06T00:00:00Z","forecast_hour":0,"unit":"hPa","interval":4.0,"segments":[],"paths":[[[-100.0,35.0],[-99.5,35.25],[-99.0,36.0]]],"wind":null});
        let mut frame: ContourLines = serde_json::from_value(raw.clone()).unwrap();
        frame.validate().unwrap();
        assert_eq!(serde_json::to_value(&frame).unwrap()["paths"], raw["paths"]);
        frame.paths[0][1][1] = 91.0;
        assert!(frame.validate().is_err());
        frame.paths[0].truncate(1);
        assert!(frame.validate().is_err());
        frame.paths[0] = vec![[-100.0, 35.0]; MAX_PATH_POINTS + 1];
        assert!(frame.validate().is_err());
    }
}

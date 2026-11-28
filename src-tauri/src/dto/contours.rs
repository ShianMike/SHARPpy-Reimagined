//! Contour-line DTOs: the isolines of one product's contour field.
//! Read from the bridge in snake_case, written to the webview in camelCase.

use base64::Engine as _;
use serde::{Deserialize, Serialize};

use crate::dto::checks::{check_lat_lon, check_text};
use crate::dto::models::Bounds;

pub const MAX_SEGMENTS: usize = 100_000;
const MAX_PATH_POINTS: usize = 200_000;
const MAX_CONTOUR_LABELS: usize = 256;
const MAX_PRESSURE_LABELS: usize = 2_048;
/// A frame never carries more named systems than a readable analysis draws.
pub const MAX_CENTERS: usize = 64;

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

/// One named synoptic system of the pressure field: `"H"` or `"L"`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct PressureCenter {
    pub lon: f64,
    pub lat: f64,
    pub kind: String,
    pub value: i32,
}

/// One smoothed 1000-500 mb thickness polyline paired with its dam level.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct ThicknessPath {
    pub value: f64,
    pub points: Vec<[f64; 2]>,
}

/// The 1000-500 mb thickness overlay of one frame, in decametres.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct ThicknessContours {
    pub unit: String,
    pub interval: f64,
    #[serde(default)]
    pub paths: Vec<ThicknessPath>,
    #[serde(default)]
    pub labels: Vec<ContourLabel>,
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
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub centers: Vec<PressureCenter>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub thickness: Option<ThicknessContours>,
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
        let label_limit = if self.unit == "hPa" {
            MAX_PRESSURE_LABELS
        } else {
            MAX_CONTOUR_LABELS
        };
        if self.labels.len() > label_limit {
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
        if self.centers.len() > MAX_CENTERS {
            return Err("too many pressure centers".into());
        }
        for center in &self.centers {
            if center.kind != "H" && center.kind != "L" {
                return Err("pressure center kind must be \"H\" or \"L\"".into());
            }
            if !(300..=1200).contains(&center.value) {
                return Err("pressure center value is out of range".into());
            }
            if !(-180.0..=180.0).contains(&center.lon) || !(-90.0..=90.0).contains(&center.lat) {
                return Err("pressure center is off the globe".into());
            }
        }
        if let Some(thickness) = &self.thickness {
            if !thickness.interval.is_finite() || thickness.interval <= 0.0 {
                return Err("thickness interval is out of range".into());
            }
            // The overlay shares the frame's geometry budgets: paths stay
            // complete and within the point budget, labels stay on level.
            let mut remaining = MAX_PATH_POINTS;
            for path in &thickness.paths {
                let multiple = path.value / thickness.interval;
                if !multiple.is_finite() || (multiple - multiple.round()).abs() > 1e-8 {
                    return Err("thickness path value is not a valid level".into());
                }
                if path.points.len() < 2 || path.points.len() > remaining {
                    return Err("thickness paths exceed the point budget or are incomplete".into());
                }
                for [lon, lat] in &path.points {
                    if !(-180.0..=180.0).contains(lon) || !(-90.0..=90.0).contains(lat) {
                        return Err("thickness point is off the globe".into());
                    }
                }
                remaining -= path.points.len();
            }
            if thickness.labels.len() > MAX_CONTOUR_LABELS {
                return Err("too many thickness labels".into());
            }
            for label in &thickness.labels {
                let [lon, lat] = label.position;
                let multiple = label.value / thickness.interval;
                if !(-180.0..=180.0).contains(&lon)
                    || !(-90.0..=90.0).contains(&lat)
                    || !multiple.is_finite()
                    || (multiple - multiple.round()).abs() > 1e-8
                {
                    return Err("thickness label is not a valid point and level".into());
                }
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
        frame.labels = vec![frame.labels[0].clone(); MAX_PRESSURE_LABELS];
        frame.validate().unwrap();
        frame.labels.push(frame.labels[0].clone());
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

    #[test]
    fn legacy_contract_frames_parse_and_keep_the_public_shape() {
        // The bridge fixture predates centers and thickness; both must default
        // to nothing without breaking the stored public contract.
        let frame: ContourLines = serde_json::from_str(include_str!(
            "../../contracts/bridge/map/contours.result.json"
        ))
        .unwrap();
        assert!(frame.centers.is_empty());
        assert!(frame.thickness.is_none());
        frame.validate().unwrap();
        let expected: serde_json::Value =
            serde_json::from_str(include_str!("../../contracts/public/map/contours.json")).unwrap();
        assert_eq!(serde_json::to_value(&frame).unwrap(), expected);
        let keys = expected.as_object().unwrap();
        assert!(!keys.contains_key("centers"));
        assert!(!keys.contains_key("thickness"));
    }

    #[test]
    fn centers_and_thickness_round_trip_and_validate_as_one_frame() {
        let raw = serde_json::json!({
            "product": "mslp", "run_time": "2026-11-06T00:00:00Z", "forecast_hour": 12,
            "unit": "hPa", "interval": 4.0, "segments": [], "wind": null,
            "centers": [
                {"lon": -93.625, "lat": 34.875, "kind": "H", "value": 1035},
                {"lon": -86.125, "lat": 39.875, "kind": "L", "value": 999}
            ],
            "thickness": {
                "unit": "dam", "interval": 6.0,
                "paths": [{"value": 546.0, "points": [[-100.0, 35.0], [-99.5, 35.25], [-99.0, 36.0]]}],
                "labels": [{"position": [-99.5, 35.25], "value": 546.0}]
            }
        });
        let mut frame: ContourLines = serde_json::from_value(raw).unwrap();
        frame.validate().unwrap();
        assert_eq!(frame.centers[0].kind, "H");
        assert_eq!(frame.centers[1].kind, "L");
        assert_eq!(frame.centers[1].value, 999);
        let thickness = frame.thickness.as_ref().unwrap();
        assert_eq!(thickness.unit, "dam");
        assert_eq!(thickness.interval, 6.0);
        assert_eq!(thickness.paths.len(), 1);
        assert_eq!(thickness.paths[0].value, 546.0);
        let serialized = serde_json::to_value(&frame).unwrap();
        assert_eq!(serialized["centers"][0]["kind"], "H");
        assert_eq!(serialized["centers"][1]["value"], 999);
        assert_eq!(serialized["thickness"]["unit"], "dam");
        assert_eq!(serialized["thickness"]["paths"][0]["value"], 546.0);

        frame.centers[0].kind = "X".to_string();
        assert!(frame.validate().is_err());
        frame.centers[0].kind = "H".to_string();
        frame.centers[0].value = 1201;
        assert!(frame.validate().is_err());
        frame.centers[0].value = 1035;
        frame.centers[0].lat = 90.5;
        assert!(frame.validate().is_err());
        frame.centers[0].lat = f64::NAN;
        assert!(frame.validate().is_err());
        frame.centers[0].lat = 34.875;
        frame.centers = vec![
            PressureCenter {
                lon: 0.0,
                lat: 0.0,
                kind: "H".to_string(),
                value: 1013
            };
            MAX_CENTERS + 1
        ];
        assert!(frame.validate().is_err());
        let intact = frame.centers[..2].to_vec();
        frame.centers = intact;

        frame.thickness.as_mut().unwrap().interval = 0.0;
        assert!(frame.validate().is_err());
        frame.thickness.as_mut().unwrap().interval = 6.0;
        frame.thickness.as_mut().unwrap().paths[0].value = 543.5;
        assert!(frame.validate().is_err());
        frame.thickness.as_mut().unwrap().paths[0].value = 546.0;
        frame.thickness.as_mut().unwrap().paths[0].points[0][0] = -181.0;
        assert!(frame.validate().is_err());
        frame.thickness.as_mut().unwrap().paths[0].points[0][0] = -100.0;
        frame.thickness.as_mut().unwrap().labels[0].value = 543.5;
        assert!(frame.validate().is_err());
        frame.thickness.as_mut().unwrap().labels[0].value = 546.0;
        frame.thickness.as_mut().unwrap().paths[0].points =
            vec![[-100.0, 35.0]; MAX_PATH_POINTS + 1];
        assert!(frame.validate().is_err());
        frame.thickness.as_mut().unwrap().paths = Vec::new();
        frame.thickness.as_mut().unwrap().labels = Vec::new();
        frame.validate().unwrap();

        // A thickness overlay may arrive without paths or labels at all.
        let minimal = serde_json::json!({
            "product": "mslp", "run_time": "2026-11-06T00:00:00Z", "forecast_hour": 0,
            "unit": "hPa", "interval": 4.0, "segments": [], "wind": null,
            "thickness": {"unit": "dam", "interval": 6.0}
        });
        let frame: ContourLines = serde_json::from_value(minimal).unwrap();
        let thickness = frame.thickness.as_ref().unwrap();
        assert!(thickness.paths.is_empty() && thickness.labels.is_empty());
        frame.validate().unwrap();
    }
}

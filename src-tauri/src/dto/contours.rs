//! Contour-line DTOs: the isolines of one product's contour field.
//! Read from the bridge in snake_case, written to the webview in camelCase.

use base64::Engine as _;
use serde::{Deserialize, Serialize};

use crate::dto::checks::{check_lat_lon, check_text};
use crate::dto::models::Bounds;

pub const MAX_SEGMENTS: usize = 100_000;

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
            || bytes.chunks_exact(2).any(|pair| {
                let cell = u16::from_le_bytes([pair[0], pair[1]]);
                cell != u16::MAX && (cell & 511) > 359
            })
        {
            return Err("wind grid contains invalid cells".into());
        }
        Ok(())
    }
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
    pub wind: Option<WindBarbGrid>,
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
        for segment in &self.segments {
            for [lon, lat] in segment {
                if !(-180.0..=180.0).contains(lon) || !(-90.0..=90.0).contains(lat) {
                    return Err("contour point is off the globe".into());
                }
            }
        }
        Ok(())
    }
}

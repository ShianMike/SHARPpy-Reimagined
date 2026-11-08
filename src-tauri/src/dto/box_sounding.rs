//! Geographic request and native aggregation metadata at the desktop boundary.
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BoxBounds {
    pub west: f64,
    pub south: f64,
    pub east: f64,
    pub north: f64,
}

impl BoxBounds {
    pub fn validate(&self) -> Result<(), String> {
        if ![self.west, self.south, self.east, self.north]
            .iter()
            .all(|v| v.is_finite())
            || self.west.abs() > 180.0
            || self.east.abs() > 180.0
            || self.south < -90.0
            || self.north > 90.0
            || self.south >= self.north
            || self.west == self.east
        {
            return Err("box must be a nonempty geographic rectangle".into());
        }
        let width = if self.east > self.west {
            self.east - self.west
        } else {
            360.0 + self.east - self.west
        };
        if !(0.0..=180.0).contains(&width) || width == 0.0 {
            return Err("box longitude span must be between zero and 180 degrees".into());
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all(serialize = "camelCase"), deny_unknown_fields)]
pub struct BoxCoverage {
    pub pressure_hpa: f64,
    pub valid_columns: u32,
    pub fraction: f64,
    pub retained: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all(serialize = "camelCase"), deny_unknown_fields)]
pub struct BoxTimings {
    pub source_ms: f64,
    pub geometry_ms: f64,
    pub decode_ms: f64,
    pub unpack_read_ms: f64,
    pub column_assembly_ms: f64,
    pub interpolation_ms: f64,
    pub spatial_ms: f64,
    pub aggregation_ms: f64,
    pub data_access_ms: f64,
    pub artifact_ms: f64,
    pub total_ms: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all(serialize = "camelCase"), deny_unknown_fields)]
pub struct BoxMetadata {
    pub bounds: BoxBounds,
    pub width_km: f64,
    pub height_km: f64,
    pub area_km2: f64,
    pub grid_resolution_km: f64,
    pub grid_type: String,
    pub selected_columns: u32,
    pub valid_columns: u32,
    pub surface_pressure_hpa: f64,
    pub method: String,
    pub location: String,
    pub source_cache_hit: bool,
    pub profile_cache_hit: bool,
    pub coverage: Vec<BoxCoverage>,
    pub timings: BoxTimings,
}

impl BoxMetadata {
    pub fn validate(&self) -> Result<(), String> {
        self.bounds.validate()?;
        if [
            self.width_km,
            self.height_km,
            self.area_km2,
            self.grid_resolution_km,
            self.surface_pressure_hpa,
        ]
        .iter()
        .any(|v| !v.is_finite() || *v <= 0.0)
            || self.selected_columns == 0
            || self.selected_columns > 65_536
            || self.valid_columns == 0
            || self.valid_columns > self.selected_columns
            || self.coverage.is_empty()
            || self.coverage.len() > 10_000
            || self.method.is_empty()
            || self.method.len() > 120
            || self.location.is_empty()
            || self.location.len() > 64
            || self.grid_type.is_empty()
            || self.grid_type.len() > 40
        {
            return Err("invalid native box metadata".into());
        }
        for level in &self.coverage {
            if !level.pressure_hpa.is_finite()
                || level.pressure_hpa <= 0.0
                || !level.fraction.is_finite()
                || !(0.0..=1.0).contains(&level.fraction)
                || level.valid_columns > self.valid_columns
            {
                return Err("invalid box level coverage".into());
            }
        }
        let t = &self.timings;
        if [
            t.source_ms,
            t.geometry_ms,
            t.decode_ms,
            t.unpack_read_ms,
            t.column_assembly_ms,
            t.interpolation_ms,
            t.spatial_ms,
            t.aggregation_ms,
            t.data_access_ms,
            t.artifact_ms,
            t.total_ms,
        ]
        .iter()
        .any(|value| !value.is_finite() || *value < 0.0)
        {
            return Err("invalid box timings".into());
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn geographic_boxes_wrap_and_reject_invalid_boundaries() {
        BoxBounds {
            west: 179.0,
            east: -179.0,
            south: -1.0,
            north: 1.0,
        }
        .validate()
        .unwrap();
        for value in [
            BoxBounds {
                west: 0.0,
                east: 0.0,
                south: 1.0,
                north: 2.0,
            },
            BoxBounds {
                west: 0.0,
                east: 1.0,
                south: 2.0,
                north: 1.0,
            },
            BoxBounds {
                west: f64::NAN,
                east: 1.0,
                south: 1.0,
                north: 2.0,
            },
        ] {
            assert!(value.validate().is_err());
        }
    }
}

//! GOES satellite frame DTOs: the frame's header and provenance.
//! Read from the bridge in snake_case, written to the webview in camelCase.

use serde::{Deserialize, Serialize};

use crate::dto::checks::{check_lat_lon, check_text};
use crate::dto::models::Bounds;

/// `satellite.goes` as the bridge sends it: the rendered frame and its provenance.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SatelliteResult {
    pub product: String,
    pub satellite: String,
    pub label: String,
    pub units: String,
    pub bounds: Bounds,
    pub requested_time: String,
    pub retrieved_at: Option<String>,
    /// The actual scan time, within the tolerance of the requested one.
    pub valid_time: String,
    pub update_interval_s: f64,
    pub coverage_fraction: f64,
    pub native_resolution_km: f64,
    pub attribution: String,
    pub source_url: String,
    pub byte_length: u64,
    pub png_base64: String,
}

/// The JSON header of a satellite frame; the body is the rendered PNG.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SatelliteFrameHeader {
    pub kind: &'static str,
    pub product: String,
    pub satellite: String,
    pub label: String,
    pub units: String,
    pub bounds: Bounds,
    pub requested_time: String,
    pub retrieved_at: Option<String>,
    pub valid_time: String,
    pub update_interval_s: f64,
    pub coverage_fraction: f64,
    pub native_resolution_km: f64,
    pub attribution: String,
    pub source_url: String,
}

fn bounds_ok(b: &Bounds) -> bool {
    check_lat_lon(Some(b.south), Some(b.west)).is_ok()
        && check_lat_lon(Some(b.north), Some(b.east)).is_ok()
        && b.west < b.east
        && b.south < b.north
}

impl SatelliteResult {
    pub fn validate(&self) -> Result<(), String> {
        check_text("product", &self.product, 1, 16)?;
        check_text("satellite", &self.satellite, 1, 16)?;
        check_text("label", &self.label, 1, 120)?;
        check_text("requestedTime", &self.requested_time, 10, 40)?;
        check_text("validTime", &self.valid_time, 10, 40)?;
        if !bounds_ok(&self.bounds) || !self.update_interval_s.is_finite() {
            return Err("bounds or the update interval are out of range".into());
        }
        if !(0.0..=1.0).contains(&self.coverage_fraction)
            || !self.native_resolution_km.is_finite()
            || self.native_resolution_km <= 0.0
        {
            return Err("coverage fraction or resolution is out of range".into());
        }
        Ok(())
    }

    pub fn header(&self) -> SatelliteFrameHeader {
        SatelliteFrameHeader {
            kind: "satellite",
            product: self.product.clone(),
            satellite: self.satellite.clone(),
            label: self.label.clone(),
            units: self.units.clone(),
            bounds: self.bounds,
            requested_time: self.requested_time.clone(),
            retrieved_at: self.retrieved_at.clone(),
            valid_time: self.valid_time.clone(),
            update_interval_s: self.update_interval_s,
            coverage_fraction: self.coverage_fraction,
            native_resolution_km: self.native_resolution_km,
            attribution: self.attribution.clone(),
            source_url: self.source_url.clone(),
        }
    }
}

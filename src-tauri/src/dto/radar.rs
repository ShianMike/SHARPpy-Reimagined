//! Single-site radar DTOs: one antenna's frame and the site catalogue.
//! Read from the bridge in snake_case, written to the webview in camelCase.
//! Re-exported from `map.rs` beside the other map DTOs.

use serde::{Deserialize, Serialize};

use crate::dto::checks::{check_lat_lon, check_text};
use crate::dto::models::Bounds;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RadarTiltStatus {
    pub tilt: String,
    pub status: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct RadarTilts {
    pub site_id: String,
    pub product: String,
    pub checked_at: String,
    pub tilts: Vec<RadarTiltStatus>,
}

impl RadarTilts {
    pub fn validate(&self) -> Result<(), String> {
        if self.site_id.len() != 4
            || !self.site_id.bytes().all(|c| c.is_ascii_alphanumeric())
            || !matches!(
                self.product.as_str(),
                "reflectivity" | "velocity" | "correlation-coefficient"
            )
        {
            return Err("invalid radar site or product".into());
        }
        crate::dto::weather_alerts::alert_time(&self.checked_at)?;
        let mut seen = std::collections::HashSet::new();
        for cut in &self.tilts {
            if !matches!(cut.tilt.as_str(), "z" | "0" | "a" | "1" | "b" | "2" | "3")
                || !matches!(cut.status.as_str(), "available" | "unavailable" | "unknown")
                || !seen.insert(&cut.tilt)
            {
                return Err("invalid or duplicate radar cut status".into());
            }
        }
        if seen.len() != 7 {
            return Err("radar availability must check all seven visible cuts".into());
        }
        Ok(())
    }
}

impl From<sharpmod_weather::radar::RadarTilts> for RadarTilts {
    fn from(result: sharpmod_weather::radar::RadarTilts) -> Self {
        Self {
            site_id: result.site_id,
            product: result.product,
            checked_at: result.checked_at,
            tilts: result
                .tilts
                .into_iter()
                .map(|cut| RadarTiltStatus {
                    tilt: cut.tilt,
                    status: cut.status,
                })
                .collect(),
        }
    }
}

/// `radar.site` as the bridge sends it: the mosaic's shape, plus the serving site.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RadarSiteResult {
    pub elevation_angle: Option<f64>,
    pub palette: Option<crate::dto::map::Palette>,
    pub legend_png_base64: Option<String>,
    pub product: String,
    pub site_id: String,
    pub label: String,
    pub units: String,
    pub bounds: Bounds,
    pub retrieved_at: Option<String>,
    pub valid_time: Option<String>,
    pub update_interval_s: f64,
    pub attribution: String,
    pub source_url: String,
    pub byte_length: u64,
    pub png_base64: String,
}

/// The JSON header of a single-site frame; the body is the service's PNG.
/// `siteId` names the antenna that served the frame.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RadarSiteFrameHeader {
    pub elevation_angle: Option<f64>,
    pub palette: Option<crate::dto::map::Palette>,
    pub legend_png_base64: Option<String>,
    pub kind: &'static str,
    pub product: String,
    pub site_id: String,
    pub label: String,
    pub units: String,
    pub bounds: Bounds,
    pub retrieved_at: Option<String>,
    pub valid_time: Option<String>,
    pub update_interval_s: f64,
    pub attribution: String,
    pub source_url: String,
}

/// `radar.sites` as the bridge sends it: the bundled WSR-88D catalogue.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RadarSiteCatalog {
    pub sites: Vec<RadarSiteEntry>,
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RadarSiteEntry {
    pub id: String,
    pub lat: f64,
    pub lon: f64,
}

/// One site for the webview: id and position only.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RadarSite {
    pub id: String,
    pub latitude: f64,
    pub longitude: f64,
}

/// The public site DTO: the catalogue sites that pass the record rule.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RadarSiteCatalogPublic {
    pub sites: Vec<RadarSite>,
}

impl RadarSiteResult {
    pub fn validate(&self) -> Result<(), String> {
        if self
            .elevation_angle
            .is_some_and(|angle| !angle.is_finite() || !(-1.0..=90.0).contains(&angle))
        {
            return Err("radar elevation is out of range".into());
        }
        if let Some(palette) = &self.palette {
            palette.validate()?;
        }
        crate::dto::checks::check_radar_legend(self.legend_png_base64.as_ref())?;
        check_text("product", &self.product, 1, 40)?;
        check_text("siteId", &self.site_id, 1, 16)?;
        check_text("label", &self.label, 1, 120)?;
        if !bounds_ok(&self.bounds) || !self.update_interval_s.is_finite() {
            return Err("bounds or the update interval are out of range".into());
        }
        Ok(())
    }

    pub fn header(&self) -> RadarSiteFrameHeader {
        RadarSiteFrameHeader {
            elevation_angle: self.elevation_angle,
            palette: self.palette.clone(),
            legend_png_base64: self.legend_png_base64.clone(),
            kind: "radar-site",
            product: self.product.clone(),
            site_id: self.site_id.clone(),
            label: self.label.clone(),
            units: self.units.clone(),
            bounds: self.bounds,
            retrieved_at: self.retrieved_at.clone(),
            valid_time: self.valid_time.clone(),
            update_interval_s: self.update_interval_s,
            attribution: self.attribution.clone(),
            source_url: self.source_url.clone(),
        }
    }
}

impl RadarSiteCatalog {
    /// A malformed entry is a bridge bug, reported as BRIDGE_PROTOCOL.
    pub fn validate(&self) -> Result<(), String> {
        if self.sites.len() > 1024 {
            return Err("too many radar sites".into());
        }
        for site in &self.sites {
            check_text("id", &site.id, 1, 16)?;
            check_lat_lon(Some(site.lat), Some(site.lon))?;
        }
        Ok(())
    }

    pub fn into_public(self) -> RadarSiteCatalogPublic {
        RadarSiteCatalogPublic {
            sites: self
                .sites
                .into_iter()
                .map(|site| RadarSite {
                    id: site.id,
                    latitude: site.lat,
                    longitude: site.lon,
                })
                .collect(),
        }
    }
}

fn bounds_ok(b: &Bounds) -> bool {
    check_lat_lon(Some(b.south), Some(b.west)).is_ok()
        && check_lat_lon(Some(b.north), Some(b.east)).is_ok()
        && b.west < b.east
        && b.south < b.north
}

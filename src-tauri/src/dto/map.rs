//! Map data DTOs: the map catalogue and the headers of field and radar frames.
//! Read from the bridge in snake_case, written to the webview in camelCase.
//! The overlay DTOs (SPC outlooks, place labels, station availability) are in
//! `overlays.rs` and re-exported here.

use serde::{Deserialize, Serialize};

use crate::dto::checks::{check_lat_lon, check_text};
use crate::dto::models::Bounds;

pub use crate::dto::overlays::{
    AvailabilityReport, AvailabilityResult, Outlook, OutlookArea, Place, PlaceLabels, SpcOutlook,
};
pub use crate::dto::radar::{
    RadarSite, RadarSiteCatalog, RadarSiteCatalogPublic, RadarSiteEntry, RadarSiteFrameHeader,
    RadarSiteResult,
};

pub const MAX_PRODUCTS: usize = 128;
pub const MAX_STOPS: usize = 256;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Palette {
    /// `[value, "#rrggbb"]` in the product's display units, ascending.
    pub stops: Vec<(f64, String)>,
    pub units: String,
    pub display: String,
    pub stepped: bool,
    pub floor: Option<f64>,
    pub ceiling: Option<f64>,
    pub ticks: Vec<f64>,
}

/// One product's contour-line spec, as the catalogue carries it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct ProductContour {
    pub interval: f64,
    pub label: String,
    pub colour: String,
    pub width: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct FieldProduct {
    pub key: String,
    pub label: String,
    pub short_label: String,
    pub category: String,
    pub description: String,
    pub caveat: String,
    pub palette: Palette,
    pub contour: Option<ProductContour>,
    pub wind: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RadarProduct {
    pub key: String,
    pub label: String,
    pub units: String,
    pub description: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OutlookProduct {
    pub key: String,
    pub label: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct MapCatalog {
    pub field_model: String,
    pub field_bounds: Bounds,
    /// The last forecast hour of each cycle, 00Z to 23Z.
    pub max_forecast_hours: Vec<u32>,
    pub categories: Vec<String>,
    pub default_product: String,
    pub products: Vec<FieldProduct>,
    pub radar_products: Vec<RadarProduct>,
    pub site_products: Vec<RadarProduct>,
    pub outlook_products: Vec<OutlookProduct>,
}

fn bounds_ok(b: &Bounds) -> bool {
    check_lat_lon(Some(b.south), Some(b.west)).is_ok()
        && check_lat_lon(Some(b.north), Some(b.east)).is_ok()
        && b.west < b.east
        && b.south < b.north
}

pub(crate) fn colour_ok(text: &str) -> bool {
    text.len() == 7 && text.starts_with('#') && text[1..].bytes().all(|b| b.is_ascii_hexdigit())
}

impl Palette {
    pub(crate) fn validate(&self) -> Result<(), String> {
        if !matches!(
            self.display.as_str(),
            "auto" | "fixed" | "temperature-difference"
        ) {
            return Err("unknown palette display quantity".into());
        }
        let ascending = self.stops.windows(2).all(|w| w[0].0 < w[1].0);
        let finite = self.stops.iter().all(|(v, _)| v.is_finite())
            && self.ticks.iter().all(|t| t.is_finite())
            && self.floor.is_none_or(f64::is_finite)
            && self.ceiling.is_none_or(f64::is_finite);
        if !(2..=MAX_STOPS).contains(&self.stops.len()) || self.ticks.len() > MAX_STOPS {
            return Err("a palette has 2 to 256 stops".into());
        }
        if !ascending || !finite || !self.stops.iter().all(|(_, c)| colour_ok(c)) {
            return Err("palette stops must ascend, be finite, and be #rrggbb".into());
        }
        check_text("units", &self.units, 0, 40)
    }
}

impl MapCatalog {
    pub fn validate(&self) -> Result<(), String> {
        check_text("fieldModel", &self.field_model, 1, 40)?;
        if !bounds_ok(&self.field_bounds) || self.max_forecast_hours.len() != 24 {
            return Err("field bounds or the 24 cycle limits are out of range".into());
        }
        if self.products.is_empty() || self.products.len() > MAX_PRODUCTS {
            return Err("expected 1 to 128 field products".into());
        }
        for (i, product) in self.products.iter().enumerate() {
            check_text("key", &product.key, 1, 40)?;
            check_text("label", &product.label, 1, 120)?;
            check_text("shortLabel", &product.short_label, 1, 40)?;
            check_text("category", &product.category, 1, 80)?;
            check_text("description", &product.description, 0, 2000)?;
            check_text("caveat", &product.caveat, 0, 2000)?;
            product
                .palette
                .validate()
                .map_err(|reason| format!("products[{i}]: {reason}"))?;
            if let Some(contour) = &product.contour {
                check_text("contour label", &contour.label, 1, 40)?;
                if !colour_ok(&contour.colour)
                    || !contour.interval.is_finite()
                    || contour.interval <= 0.0
                    || !contour.width.is_finite()
                    || !(0.0..=8.0).contains(&contour.width)
                {
                    return Err(format!("products[{i}]: contour spec is out of range"));
                }
            }
        }
        if !self.products.iter().any(|p| p.key == self.default_product) {
            return Err("the default product is not listed".into());
        }
        for item in &self.radar_products {
            check_text("radar key", &item.key, 1, 40)?;
            check_text("radar label", &item.label, 1, 120)?;
        }
        for item in &self.site_products {
            check_text("site radar key", &item.key, 1, 40)?;
            check_text("site radar label", &item.label, 1, 120)?;
        }
        for item in &self.outlook_products {
            check_text("outlook key", &item.key, 1, 16)?;
            check_text("outlook label", &item.label, 1, 120)?;
        }
        Ok(())
    }
}

/// `fields.grid` as the bridge sends it: the header and the base64 cells.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FieldGridResult {
    pub product: String,
    pub run_time: String,
    pub forecast_hour: u32,
    pub valid_time: String,
    pub width: u32,
    pub height: u32,
    pub bounds: Bounds,
    pub units: String,
    pub scale: f64,
    pub offset: f64,
    pub nodata: u16,
    pub value_min: f64,
    pub value_max: f64,
    pub source_url: String,
    pub attribution: String,
    pub data_base64: String,
}

/// The JSON header of a field frame; the body is `width * height` uint16 LE cells,
/// row 0 at the north edge, `value = cell * scale + offset`, `nodata` for none.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FieldFrameHeader {
    pub kind: &'static str,
    pub product: String,
    pub run_time: String,
    pub forecast_hour: u32,
    pub valid_time: String,
    pub width: u32,
    pub height: u32,
    pub bounds: Bounds,
    pub units: String,
    pub scale: f64,
    pub offset: f64,
    pub nodata: u16,
    pub value_min: f64,
    pub value_max: f64,
    pub source_url: String,
    pub attribution: String,
}

impl FieldGridResult {
    /// Checks the header; the body length is checked against it after decoding.
    pub fn validate(&self) -> Result<(), String> {
        check_text("product", &self.product, 1, 40)?;
        check_text("units", &self.units, 0, 40)?;
        let cells = u64::from(self.width) * u64::from(self.height);
        let finite = [self.scale, self.offset, self.value_min, self.value_max]
            .iter()
            .all(|v| v.is_finite());
        if cells == 0 || cells > 8_000_000 || !finite || self.scale <= 0.0 {
            return Err("grid size, scale, or value range is out of range".into());
        }
        if bounds_ok(&self.bounds) {
            Ok(())
        } else {
            Err("bounds are out of range".into())
        }
    }

    pub fn header(&self) -> FieldFrameHeader {
        FieldFrameHeader {
            kind: "field",
            product: self.product.clone(),
            run_time: self.run_time.clone(),
            forecast_hour: self.forecast_hour,
            valid_time: self.valid_time.clone(),
            width: self.width,
            height: self.height,
            bounds: self.bounds,
            units: self.units.clone(),
            scale: self.scale,
            offset: self.offset,
            nodata: self.nodata,
            value_min: self.value_min,
            value_max: self.value_max,
            source_url: self.source_url.clone(),
            attribution: self.attribution.clone(),
        }
    }
}

/// `radar.mosaic` as the bridge sends it.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RadarResult {
    pub legend_png_base64: Option<String>,
    pub product: String,
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

/// The JSON header of a radar frame; the body is the service's PNG.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RadarFrameHeader {
    pub legend_png_base64: Option<String>,
    pub kind: &'static str,
    pub product: String,
    pub label: String,
    pub units: String,
    pub bounds: Bounds,
    pub retrieved_at: Option<String>,
    pub valid_time: Option<String>,
    pub update_interval_s: f64,
    pub attribution: String,
    pub source_url: String,
}

impl RadarResult {
    pub fn validate(&self) -> Result<(), String> {
        crate::dto::checks::check_radar_legend(self.legend_png_base64.as_ref())?;
        check_text("product", &self.product, 1, 40)?;
        check_text("label", &self.label, 1, 120)?;
        if !bounds_ok(&self.bounds) || !self.update_interval_s.is_finite() {
            return Err("bounds or the update interval are out of range".into());
        }
        Ok(())
    }

    pub fn header(&self) -> RadarFrameHeader {
        RadarFrameHeader {
            legend_png_base64: self.legend_png_base64.clone(),
            kind: "radar",
            product: self.product.clone(),
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

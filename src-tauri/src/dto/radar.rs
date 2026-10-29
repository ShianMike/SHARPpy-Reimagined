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

/// `radar.sites` as the bridge sends it: the bundled radar catalogue.
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
    #[serde(default)]
    pub name: Option<String>,
    #[serde(default)]
    pub country: Option<String>,
    #[serde(default)]
    pub products: Option<Vec<String>>,
}

/// One site for the webview; legacy sites omit the optional metadata.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RadarSite {
    pub id: String,
    pub latitude: f64,
    pub longitude: f64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub country: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub products: Option<Vec<String>>,
}

impl From<&sharpmod_weather::radar_sites::RadarSite> for RadarSite {
    fn from(site: &sharpmod_weather::radar_sites::RadarSite) -> Self {
        Self {
            id: site.id.clone(),
            latitude: site.lat,
            longitude: site.lon,
            name: site.name.clone(),
            country: site.country.clone(),
            // The US catalogue carries WMS layer names, not public product IDs.
            products: (site.country.as_deref() == Some("AU")).then(|| site.products.clone()),
        }
    }
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
            if let Some(name) = &site.name {
                check_text("name", name, 1, 120)?;
            }
            if let Some(country) = &site.country {
                if country.len() != 2 || !country.bytes().all(|c| c.is_ascii_uppercase()) {
                    return Err("radar country must be a two-letter uppercase code".into());
                }
            }
            if let Some(products) = &site.products {
                if products.is_empty()
                    || products.iter().any(|product| {
                        !matches!(
                            product.as_str(),
                            "reflectivity" | "velocity" | "correlation-coefficient"
                        )
                    })
                {
                    return Err("radar products must name supported single-site products".into());
                }
                crate::dto::checks::unique(products, "radar products")?;
            }
        }
        Ok(())
    }

    pub fn into_public(self) -> RadarSiteCatalogPublic {
        RadarSiteCatalogPublic {
            sites: self
                .sites
                .into_iter()
                .map(|site| RadarSite {
                    products: site
                        .products
                        .filter(|_| site.country.as_deref() == Some("AU")),
                    id: site.id,
                    latitude: site.lat,
                    longitude: site.lon,
                    name: site.name,
                    country: site.country,
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

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn legacy_catalogue_keeps_its_public_contract() {
        let catalog: RadarSiteCatalog = serde_json::from_str(include_str!(
            "../../contracts/bridge/map/radar-sites.result.json"
        ))
        .unwrap();
        catalog.validate().unwrap();
        let expected: serde_json::Value =
            serde_json::from_str(include_str!("../../contracts/public/map/radar-sites.json"))
                .unwrap();
        assert_eq!(
            serde_json::to_value(catalog.into_public()).unwrap(),
            expected
        );
    }

    #[test]
    fn australian_metadata_survives_both_catalogue_paths() {
        let native = sharpmod_weather::radar_sites::sites()
            .iter()
            .find(|site| site.id == "ABRD")
            .unwrap();
        let expected = json!({
            "id": "ABRD", "latitude": -37.691, "longitude": 144.946,
            "name": "Broadmeadows", "country": "AU", "products": ["reflectivity", "velocity"]
        });
        assert_eq!(
            serde_json::to_value(RadarSite::from(native)).unwrap(),
            expected
        );
        let catalog: RadarSiteCatalog = serde_json::from_value(json!({"sites": [{
            "id": "ABRD", "lat": -37.691, "lon": 144.946,
            "name": "Broadmeadows", "country": "AU", "products": ["reflectivity", "velocity"]
        }]}))
        .unwrap();
        catalog.validate().unwrap();
        assert_eq!(
            serde_json::to_value(catalog.into_public()).unwrap(),
            json!({"sites": [expected]})
        );
        let us = sharpmod_weather::radar_sites::sites()
            .iter()
            .find(|site| site.id == "KTLX")
            .unwrap();
        let public_us = serde_json::to_value(RadarSite::from(us)).unwrap();
        assert_eq!(public_us.as_object().unwrap().len(), 3);
        assert!(public_us.get("products").is_none());
        let legacy: RadarSiteCatalog = serde_json::from_value(json!({"sites": [{
            "id": "KTLX", "lat": us.lat, "lon": us.lon,
            "products": ["reflectivity"]
        }]}))
        .unwrap();
        legacy.validate().unwrap();
        assert_eq!(
            serde_json::to_value(legacy.into_public()).unwrap(),
            json!({"sites": [public_us]})
        );
    }

    #[test]
    fn catalogue_rejects_invalid_metadata_at_the_bridge_boundary() {
        for metadata in [
            json!({"name": ""}),
            json!({"country": "Australia"}),
            json!({"country": "au"}),
            json!({"products": []}),
            json!({"products": ["sr_bref"]}),
            json!({"products": ["reflectivity", "reflectivity"]}),
        ] {
            let mut site = json!({"id": "ABRD", "lat": -37.691, "lon": 144.946});
            site.as_object_mut()
                .unwrap()
                .extend(metadata.as_object().unwrap().clone());
            let catalog: RadarSiteCatalog =
                serde_json::from_value(json!({"sites": [site]})).unwrap();
            assert!(catalog.validate().is_err(), "{metadata}");
        }
    }
}

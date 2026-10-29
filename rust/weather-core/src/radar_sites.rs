//! Bundled US WSR-88D and Australian MRRL radar sites.

use std::sync::OnceLock;

#[derive(Debug, serde::Deserialize, serde::Serialize)]
pub struct RadarSite {
    pub id: String,
    pub lat: f64,
    pub lon: f64,
    /// The US WMS image extent; polar-volume sources do not use this field.
    pub half_span_deg: f64,
    pub products: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub country: Option<String>,
}

pub fn sites() -> &'static [RadarSite] {
    #[derive(serde::Deserialize)]
    struct Catalog {
        sites: Vec<RadarSite>,
    }
    static SITES: OnceLock<Vec<RadarSite>> = OnceLock::new();
    SITES.get_or_init(|| {
        let mut catalog: Catalog =
            serde_json::from_str(include_str!("../resources/radar_sites.json"))
                .expect("bundled radar site catalogue");
        let australia: Catalog =
            serde_json::from_str(include_str!("../resources/australian_radar_sites.json"))
                .expect("bundled Australian radar site catalogue");
        catalog.sites.extend(australia.sites);
        catalog.sites
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn bundled_catalogue_has_unique_valid_sites() {
        let mut ids = std::collections::HashSet::new();
        assert!(sites().len() > 100);
        for site in sites() {
            assert!(
                site.id.len() == 4
                    && site
                        .id
                        .bytes()
                        .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit())
            );
            assert!(ids.insert(&site.id));
            assert!((-90.0..=90.0).contains(&site.lat));
            assert!((-180.0..=180.0).contains(&site.lon));
        }
        assert!(ids.contains(&"KTLX".to_string()));
    }

    #[test]
    fn us_catalogue_is_preserved() {
        let original: serde_json::Value =
            serde_json::from_str(include_str!("../resources/radar_sites.json")).unwrap();
        let us: Vec<_> = sites()
            .iter()
            .filter(|site| site.country.is_none())
            .map(|site| serde_json::to_value(site).unwrap())
            .collect();
        assert_eq!(serde_json::Value::Array(us), original["sites"]);
    }

    #[test]
    fn australian_sites_have_confirmed_products_and_no_missing_feed_aliases() {
        let australia: Vec<_> = sites()
            .iter()
            .filter(|site| site.country.as_deref() == Some("AU"))
            .collect();
        assert_eq!(australia.len(), 67);
        for site in &australia {
            assert!(site.name.as_ref().is_some_and(|name| !name.is_empty()));
            assert_eq!(site.products, ["reflectivity", "velocity"]);
            assert_eq!(site.half_span_deg, 0.0);
        }
        let broadmeadows = australia.iter().find(|site| site.id == "ABRD").unwrap();
        assert_eq!(broadmeadows.name.as_deref(), Some("Broadmeadows"));
        assert_eq!((broadmeadows.lat, broadmeadows.lon), (-37.691, 144.946));
        assert!(australia.iter().any(|site| site.id == "AWEP"));
        for alias in ["ADAM", "AFFW", "APER", "AWEI"] {
            assert!(!sites().iter().any(|site| site.id == alias));
        }
    }
}

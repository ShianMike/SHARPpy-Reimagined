//! Bundled radar sites and their provider-specific product capabilities.

use std::{collections::HashMap, sync::OnceLock};

#[derive(Clone, Debug, serde::Deserialize, serde::Serialize)]
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub wmo: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub provider: Option<String>,
}

#[derive(serde::Deserialize)]
struct SiteNames {
    nexrad: HashMap<String, String>,
    opera: HashMap<String, String>,
}

fn names() -> &'static SiteNames {
    static NAMES: OnceLock<SiteNames> = OnceLock::new();
    NAMES.get_or_init(|| {
        serde_json::from_str(include_str!("../resources/radar_site_names.json"))
            .expect("bundled radar site names")
    })
}

pub(crate) fn opera_site_name(code: &str) -> Option<&'static str> {
    names()
        .opera
        .get(&code.to_ascii_lowercase())
        .map(String::as_str)
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
        for site in &mut catalog.sites {
            site.name = names().nexrad.get(&site.id).cloned();
        }
        let australia: Catalog =
            serde_json::from_str(include_str!("../resources/australian_radar_sites.json"))
                .expect("bundled Australian radar site catalogue");
        catalog.sites.extend(australia.sites);
        let germany: Catalog =
            serde_json::from_str(include_str!("../resources/german_radar_sites.json"))
                .expect("bundled German radar site catalogue");
        catalog.sites.extend(germany.sites);
        let mrrl: Catalog =
            serde_json::from_str(include_str!("../resources/mrrl_radar_sites.json"))
                .expect("bundled MRRL radar site catalogue");
        catalog.sites.extend(mrrl.sites);
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
            .map(|site| {
                let mut record = serde_json::to_value(site).unwrap();
                assert!(record.as_object_mut().unwrap().remove("name").is_some());
                record
            })
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

    #[test]
    fn german_sites_describe_only_the_dx_product() {
        let germany: Vec<_> = sites()
            .iter()
            .filter(|site| site.country.as_deref() == Some("DE"))
            .collect();
        assert_eq!(germany.len(), 17);
        for site in germany {
            assert_eq!(site.products, ["reflectivity"]);
            assert!(site
                .wmo
                .as_ref()
                .is_some_and(|id| id.len() == 5 && id.bytes().all(|c| c.is_ascii_digit())));
        }
    }

    #[test]
    fn mrrl_international_sites_have_verified_provider_countries_and_no_opera_duplicates() {
        let mrrl: Vec<_> = sites()
            .iter()
            .filter(|site| site.provider.as_deref() == Some("MRRL"))
            .collect();
        assert_eq!(mrrl.len(), 11);
        for site in &mrrl {
            assert!(site.name.as_ref().is_some_and(|name| !name.is_empty()));
            assert!(matches!(
                site.country.as_deref(),
                Some("FR" | "GP" | "MQ" | "RE" | "NC")
            ));
            assert_eq!(site.products, ["reflectivity"]);
            assert_eq!(site.half_span_deg, 0.0);
        }
        let noumea = mrrl.iter().find(|site| site.id == "MF94").unwrap();
        assert_eq!(noumea.name.as_deref(), Some("Nouméa"));
        assert_eq!(noumea.country.as_deref(), Some("NC"));
        for duplicate in ["MF40", "MF41", "MF50", "MF51", "SAPP", "CSKR"] {
            assert!(!sites().iter().any(|site| site.id == duplicate));
        }
    }
}

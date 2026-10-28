//! Embed the same catalogue shipped with the scientific providers.

use std::sync::OnceLock;

#[derive(Debug, serde::Deserialize, serde::Serialize)]
pub struct RadarSite {
    pub id: String,
    pub lat: f64,
    pub lon: f64,
    pub half_span_deg: f64,
    pub products: Vec<String>,
}

pub fn sites() -> &'static [RadarSite] {
    #[derive(serde::Deserialize)]
    struct Catalog {
        sites: Vec<RadarSite>,
    }
    static SITES: OnceLock<Vec<RadarSite>> = OnceLock::new();
    SITES.get_or_init(|| {
        let catalog: Catalog = serde_json::from_str(include_str!("../resources/radar_sites.json"))
            .expect("bundled radar site catalogue");
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
            assert!(site.id.len() == 4 && site.id.bytes().all(|c| c.is_ascii_alphanumeric()));
            assert!(ids.insert(&site.id));
            assert!((-90.0..=90.0).contains(&site.lat));
            assert!((-180.0..=180.0).contains(&site.lon));
        }
        assert!(ids.contains(&"KTLX".to_string()));
    }
}

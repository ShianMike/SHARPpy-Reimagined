//! `get_station_catalog` DTOs and the station record rule.

use std::collections::HashSet;

use serde::{Deserialize, Serialize};

pub const MAX_ID_CHARS: usize = 16;
pub const MAX_NAME_CHARS: usize = 200;
pub const MAX_SOURCE_CHARS: usize = 16;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum StationAvailability {
    /// No availability data for this station (every station in Phase 1).
    Unknown,
    /// UWyo listed it for `availabilityBasis.observationTime` (Phase 2).
    Listed,
    /// UWyo did not list it for that time (Phase 2).
    NotListed,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Station {
    pub id: String,
    pub name: String,
    pub latitude: f64,
    pub longitude: f64,
    pub source: String,
    pub availability: StationAvailability,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum CatalogOrigin {
    Bundled,
    SeedFallback,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(
    tag = "kind",
    rename_all = "camelCase",
    rename_all_fields = "camelCase",
    deny_unknown_fields
)]
pub enum AvailabilityBasis {
    CatalogOnly,
    ListedAt { observation_time: String },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct StationCatalog {
    /// Sorted by id; ids unique.
    pub stations: Vec<Station>,
    pub origin: CatalogOrigin,
    pub availability_basis: AvailabilityBasis,
    /// Records rejected by the record rule.
    pub dropped_count: u32,
}

impl StationCatalog {
    /// Checks every station against the record rule and the unique-id rule.
    pub fn validate(&self) -> Result<(), String> {
        let mut seen = HashSet::with_capacity(self.stations.len());
        for (index, s) in self.stations.iter().enumerate() {
            check_record(&s.id, &s.name, s.latitude, s.longitude, &s.source)
                .map_err(|reason| format!("stations[{index}] ({:?}): {reason}", s.id))?;
            if !seen.insert(s.id.as_str()) {
                return Err(format!("stations[{index}]: duplicate id {:?}", s.id));
            }
        }
        Ok(())
    }
}

fn chars_within(value: &str, low: usize, high: usize) -> bool {
    let n = value.chars().count();
    (low..=high).contains(&n)
}

/// The record rule (DESIGN-contracts C4), shared by the bridge conversion and
/// the public DTO check: an id of 1 to 16 characters with no surrounding
/// whitespace, finite in-range coordinates, a name of at most 200 characters,
/// and a source of 1 to 16 characters.
pub fn check_record(
    id: &str,
    name: &str,
    lat: f64,
    lon: f64,
    src: &str,
) -> Result<(), &'static str> {
    if id.trim() != id || !chars_within(id, 1, MAX_ID_CHARS) {
        return Err("id must be 1 to 16 characters");
    }
    if !lat.is_finite() || !(-90.0..=90.0).contains(&lat) {
        return Err("latitude must be finite and within [-90, 90]");
    }
    if !lon.is_finite() || !(-180.0..=180.0).contains(&lon) {
        return Err("longitude must be finite and within [-180, 180]");
    }
    if !chars_within(name, 0, MAX_NAME_CHARS) {
        return Err("name must be at most 200 characters");
    }
    if !chars_within(src, 1, MAX_SOURCE_CHARS) {
        return Err("source must be 1 to 16 characters");
    }
    Ok(())
}

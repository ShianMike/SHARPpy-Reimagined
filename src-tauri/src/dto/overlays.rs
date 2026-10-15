//! Map overlay DTOs: SPC outlooks, place labels, and station availability.
//! Read from the bridge in snake_case, written to the webview in camelCase.

use serde::{Deserialize, Serialize};

use crate::dto::checks::{check_lat_lon, check_text};
use crate::dto::map::colour_ok;
use crate::dto::stations::{
    check_record, Station, StationAvailability as StationAvailabilityState, MAX_ID_CHARS,
};
use crate::python::protocol::BridgeStation;

pub const MAX_AREAS: usize = 256;
pub const MAX_RING_POINTS: usize = 100_000;
pub const MAX_PLACES: usize = 50_000;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct OutlookArea {
    pub label: String,
    pub description: String,
    pub fill: Option<String>,
    pub stroke: String,
    pub rank: i64,
    pub hatch: bool,
    pub hatch_level: u8,
    /// The exterior ring, then holes; `[lon, lat]` points, not repeated at the end.
    pub rings: Vec<Vec<[f64; 2]>>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct Outlook {
    pub title: String,
    pub subtitle: String,
    pub valid_from: Option<String>,
    pub valid_to: Option<String>,
    pub issued: Option<String>,
    /// False when the nearest outlook found does not cover the requested time.
    pub covers: bool,
    pub source_url: String,
    pub attribution: String,
    pub areas: Vec<OutlookArea>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct SpcOutlook {
    pub product: String,
    pub valid_time: Option<String>,
    /// None when SPC published nothing for the time (or it could not be found).
    pub outlook: Option<Outlook>,
}

impl SpcOutlook {
    pub fn validate(&self) -> Result<(), String> {
        let Some(outlook) = &self.outlook else {
            return Ok(());
        };
        check_text("title", &outlook.title, 0, 300)?;
        if outlook.areas.len() > MAX_AREAS {
            return Err("too many outlook areas".into());
        }
        for area in &outlook.areas {
            check_text("label", &area.label, 0, 120)?;
            let fill_ok = area.fill.as_deref().is_none_or(colour_ok);
            let points: usize = area.rings.iter().map(Vec::len).sum();
            let coords_ok = area
                .rings
                .iter()
                .flatten()
                .all(|[lon, lat]| check_lat_lon(Some(*lat), Some(*lon)).is_ok());
            if !fill_ok || !colour_ok(&area.stroke) || points > MAX_RING_POINTS || !coords_ok {
                return Err(format!("outlook area {:?} is malformed", area.label));
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Place {
    pub name: String,
    #[serde(rename(serialize = "latitude", deserialize = "lat"))]
    pub lat: f64,
    #[serde(rename(serialize = "longitude", deserialize = "lon"))]
    pub lon: f64,
    /// 1: a major city; 2: a town.
    pub tier: u8,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PlaceLabels {
    pub places: Vec<Place>,
}

impl PlaceLabels {
    pub fn validate(&self) -> Result<(), String> {
        if self.places.len() > MAX_PLACES {
            return Err("too many places".into());
        }
        for place in &self.places {
            check_text("name", &place.name, 1, 200)?;
            check_lat_lon(Some(place.lat), Some(place.lon))?;
            if !(1..=2).contains(&place.tier) {
                return Err("tier must be 1 or 2".into());
            }
        }
        Ok(())
    }
}

/// `stations.availability` as the bridge sends it.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AvailabilityResult {
    pub observation_time: String,
    pub listed_ids: Vec<String>,
    pub extra_stations: Vec<BridgeStation>,
    pub dropped_count: u32,
}

/// The public availability DTO; extra stations are listed by UWyo but not bundled.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AvailabilityReport {
    pub observation_time: String,
    pub listed_ids: Vec<String>,
    pub extra_stations: Vec<Station>,
    pub dropped_count: u32,
}

impl AvailabilityResult {
    /// Applies the record rule to the ids and the extra stations, as the catalogue does.
    pub fn into_report(self) -> AvailabilityReport {
        let mut dropped = self.dropped_count;
        let listed_ids: Vec<String> = self
            .listed_ids
            .into_iter()
            .filter(|id| id.trim() == id && (1..=MAX_ID_CHARS).contains(&id.chars().count()))
            .collect();
        let mut extra_stations = Vec::with_capacity(self.extra_stations.len());
        for record in self.extra_stations {
            if check_record(
                &record.id,
                &record.name,
                record.lat,
                record.lon,
                &record.src,
            )
            .is_err()
            {
                dropped = dropped.saturating_add(1);
                continue;
            }
            extra_stations.push(Station {
                id: record.id,
                name: record.name,
                latitude: record.lat,
                longitude: record.lon,
                source: record.src,
                availability: StationAvailabilityState::Listed,
            });
        }
        AvailabilityReport {
            observation_time: self.observation_time,
            listed_ids,
            extra_stations,
            dropped_count: dropped,
        }
    }
}

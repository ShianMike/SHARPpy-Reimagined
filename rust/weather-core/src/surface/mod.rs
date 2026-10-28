//! Nearby NWS observations, with explicit time matching and missing-value semantics.

mod fetch;
mod parse;

pub use fetch::{fetch, FetchError, JsonSource, Request, SurfaceClient};
pub use parse::{nearest_index, observations, stations};
use time::OffsetDateTime;

pub const MAX_BYTES: usize = 8 * 1024 * 1024;
pub const MAX_STATIONS: usize = 32;
pub const MAX_WORKERS: usize = 6;

#[derive(Clone, Debug, PartialEq)]
pub struct Station {
    pub id: String,
    pub name: String,
    pub lat: f64,
    pub lon: f64,
    pub elevation_m: Option<f64>,
    pub source_url: String,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Observation {
    pub observed_at: OffsetDateTime,
    pub temperature_c: Option<f64>,
    pub dewpoint_c: Option<f64>,
    pub direction_deg: Option<f64>,
    pub speed_kt: Option<f64>,
    pub gust_kt: Option<f64>,
    pub text: String,
    pub source_url: String,
}

#[derive(Debug)]
pub struct Dataset {
    pub requested: OffsetDateTime,
    pub lat: f64,
    pub lon: f64,
    pub catalog: Vec<Station>,
    pub matched: Vec<(usize, Observation)>,
    pub queried: Vec<usize>,
    pub unmatched: Vec<usize>,
    pub failed: Vec<usize>,
    pub retrieved_at: OffsetDateTime,
    pub source_url: String,
}

//! AWC's complete worldwide METAR cache, shared across callers in this process.

use super::parse::observation_time;
use super::plots::{observed_plot, PlotReference};
use super::{Observation, SurfaceClient, SurfacePlotCounts, SurfacePlotsSnapshot};
use crate::format_rfc3339_utc;
use csv::{ReaderBuilder, StringRecord, Trim};
use flate2::read::MultiGzDecoder;
use reqwest::header::LAST_MODIFIED;
use serde::Deserialize;
use std::collections::{hash_map::Entry, HashMap};
use std::future::Future;
use std::io::Read;
use std::sync::{Arc, OnceLock};
use std::time::Duration;
use time::{format_description::well_known::Rfc2822, OffsetDateTime};
use tokio::{sync::Mutex, time::Instant};

const CACHE_URL: &str = "https://aviationweather.gov/data/cache/metars.cache.csv.gz";
// https://aviationweather.gov/data/api/#cache lists a one-minute update cadence.
const REFRESH_INTERVAL: Duration = Duration::from_secs(60);
const MAX_SNAPSHOT_AGE: Duration = Duration::from_secs(15 * 60);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(12);
const MAX_GZIP_BYTES: usize = 2 * 1024 * 1024;
const MAX_CSV_BYTES: u64 = 8 * 1024 * 1024;
// Leave room in the 8 MiB bridge frame for the envelope and JSON re-encoding.
const MAX_BRIDGE_JSON_BYTES: usize = 7 * 1024 * 1024;
pub const MAX_LATEST_PLOTS: usize = 20_000;

#[derive(Debug)]
pub struct LatestSurfacePlots {
    pub snapshot: SurfacePlotsSnapshot,
    pub bridge_json: String,
    published_at: OffsetDateTime,
}

#[derive(Default)]
struct CacheState {
    good: Option<(Arc<LatestSurfacePlots>, Instant)>,
    refresh_after: Option<Instant>,
    error: Option<String>,
}

#[derive(Default)]
struct LatestCache(Mutex<CacheState>);

impl LatestCache {
    async fn get(
        &self,
        load: impl Future<Output = Result<LatestSurfacePlots, String>>,
    ) -> Result<Arc<LatestSurfacePlots>, String> {
        // Keep the lock through refresh so waiters share its result, including errors.
        let mut state = self.0.lock().await;
        let now = Instant::now();
        if state.refresh_after.is_none_or(|when| now >= when) {
            // Enforce the minimum interval even if the caller drops the refresh.
            state.refresh_after = Some(now + REFRESH_INTERVAL);
            let result = load.await;
            let finished = Instant::now();
            state.refresh_after = Some(finished + REFRESH_INTERVAL);
            match result.and_then(|value| {
                let age: Duration = (OffsetDateTime::now_utc() - value.published_at)
                    .try_into()
                    .unwrap_or_default();
                let remaining = MAX_SNAPSHOT_AGE.saturating_sub(age);
                if remaining.is_zero() {
                    return Err("AWC returned an expired METAR snapshot".into());
                }
                Ok((Arc::new(value), finished + remaining))
            }) {
                Ok(good) => {
                    state.good = Some(good);
                    state.error = None;
                }
                Err(error) => state.error = Some(error),
            }
        }
        state
            .good
            .take_if(|(_, expires)| Instant::now() >= *expires);
        state.good.as_ref().map_or_else(
            || {
                Err(state
                    .error
                    .clone()
                    .unwrap_or_else(|| "AWC METAR snapshot is unavailable".into()))
            },
            |(value, _)| Ok(Arc::clone(value)),
        )
    }
}

pub async fn fetch_latest_plots() -> Result<Arc<LatestSurfacePlots>, String> {
    static CACHE: OnceLock<LatestCache> = OnceLock::new();
    CACHE
        .get_or_init(LatestCache::default)
        .get(download())
        .await
}

async fn download() -> Result<LatestSurfacePlots, String> {
    let response = SurfaceClient::shared()
        .http()?
        .get(CACHE_URL)
        .timeout(REQUEST_TIMEOUT)
        .send()
        .await
        .and_then(reqwest::Response::error_for_status)
        .map_err(|error| format!("AWC METAR download failed: {error}"))?;
    let published_at = response
        .headers()
        .get(LAST_MODIFIED)
        .and_then(|header| header.to_str().ok())
        .and_then(|value| OffsetDateTime::parse(value, &Rfc2822).ok())
        .unwrap_or_else(OffsetDateTime::now_utc);
    let data = crate::http::body(response, MAX_GZIP_BYTES).await?;
    tokio::task::spawn_blocking(move || parse(&data, published_at))
        .await
        .map_err(|error| format!("AWC METAR decoding failed: {error}"))?
}

#[derive(Deserialize)]
struct Metar<'a> {
    raw_text: &'a str,
    station_id: &'a str,
    observation_time: &'a str,
    latitude: &'a str,
    longitude: &'a str,
    temp_c: &'a str,
    dewpoint_c: &'a str,
    wind_dir_degrees: &'a str,
    wind_speed_kt: &'a str,
    wind_gust_kt: &'a str,
}

impl Metar<'_> {
    fn observation(&self) -> Option<((f64, f64), Observation)> {
        let latitude = self.latitude.parse().ok()?;
        let longitude = self.longitude.parse().ok()?;
        if !(-90.0..=90.0).contains(&latitude) || !(-180.0..=180.0).contains(&longitude) {
            return None;
        }
        let reading = |text: &str| {
            text.parse()
                .ok()
                .filter(|value| (-999.0..=999.0).contains(value))
        };
        // The CSV can encode VRB as 0 degrees, which also occurs for northerly winds.
        let variable_wind = self
            .raw_text
            .split_ascii_whitespace()
            .take_while(|word| *word != "RMK")
            .any(|word| {
                word.strip_prefix("VRB")
                    .is_some_and(|code| code.as_bytes().first().is_some_and(u8::is_ascii_digit))
            });
        Some((
            (latitude, longitude),
            Observation {
                observed_at: observation_time(self.observation_time)?,
                temperature_c: reading(self.temp_c),
                dewpoint_c: reading(self.dewpoint_c),
                direction_deg: self
                    .wind_dir_degrees
                    .parse()
                    .ok()
                    .filter(|degrees| !variable_wind && (0.0..=360.0).contains(degrees)),
                // The existing display staff holds at most 295 knots.
                speed_kt: self
                    .wind_speed_kt
                    .parse()
                    .ok()
                    .filter(|kt| (0.0..=295.0).contains(kt)),
                gust_kt: self
                    .wind_gust_kt
                    .parse()
                    .ok()
                    .filter(|kt| (0.0..=999.0).contains(kt)),
                text: String::new(),
                source_url: String::new(),
            },
        ))
    }
}

fn parse(
    compressed_csv: &[u8],
    published_at: OffsetDateTime,
) -> Result<LatestSurfacePlots, String> {
    let gzip = MultiGzDecoder::new(compressed_csv).take(MAX_CSV_BYTES + 1);
    let mut reader = ReaderBuilder::new().trim(Trim::All).from_reader(gzip);
    let csv_error = |error: csv::Error| format!("AWC METAR cache returned invalid CSV: {error}");
    let header = reader.headers().map_err(csv_error)?.clone();
    let mut row = StringRecord::new();
    let mut reports = HashMap::new();
    while reader.read_record(&mut row).map_err(csv_error)? {
        let report: Metar<'_> = row.deserialize(Some(&header)).map_err(csv_error)?;
        if !(1..=16).contains(&report.station_id.len())
            || !report
                .station_id
                .bytes()
                .all(|byte| byte.is_ascii_graphic())
        {
            continue;
        }
        let Some((coordinates, observation)) = report.observation() else {
            continue;
        };
        match reports.entry(report.station_id.to_ascii_uppercase()) {
            Entry::Vacant(entry) => {
                entry.insert((coordinates, observation));
            }
            Entry::Occupied(mut entry) if entry.get().1.observed_at < observation.observed_at => {
                entry.insert((coordinates, observation));
            }
            Entry::Occupied(_) => {}
        }
        if reports.len() > MAX_LATEST_PLOTS {
            return Err(format!(
                "AWC METAR snapshot exceeds {MAX_LATEST_PLOTS} stations"
            ));
        }
    }
    if reader.into_inner().limit() == 0 {
        return Err("AWC METAR CSV exceeds its decompressed byte budget".into());
    }
    if reports.is_empty() {
        return Err("AWC returned no usable METAR station reports".into());
    }
    let mut plots: Vec<_> = reports
        .into_iter()
        .map(|(id, (coordinates, obs))| {
            observed_plot(
                id.clone(),
                id,
                coordinates,
                &obs,
                PlotReference::Snapshot(published_at),
            )
        })
        .collect();
    plots.sort_unstable_by(|a, b| a.id.cmp(&b.id));
    let stations = plots.len() as u32;
    let snapshot = SurfacePlotsSnapshot {
        valid_time: format_rfc3339_utc(published_at),
        counts: SurfacePlotCounts {
            matched: stations,
            stale: plots.iter().filter(|plot| plot.stale).count() as u32,
            no_observation: 0,
            failed: 0,
            nearby: stations,
            queried: stations,
            unqueried: 0,
        },
        plots,
    };
    let bridge_json = serde_json::to_string(&snapshot).map_err(|error| error.to_string())?;
    if bridge_json.len() > MAX_BRIDGE_JSON_BYTES {
        return Err("AWC METAR snapshot exceeds its bridge byte budget".into());
    }
    Ok(LatestSurfacePlots {
        snapshot,
        bridge_json,
        published_at,
    })
}

#[cfg(test)]
mod tests;

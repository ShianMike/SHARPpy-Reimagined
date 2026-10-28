use super::{
    nearest_index, observations, stations, Dataset, Observation, MAX_BYTES, MAX_STATIONS,
    MAX_WORKERS,
};
use reqwest::{Client, Url};
use serde_json::Value;
use std::{future::Future, time::Duration};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};
use tokio::task::JoinSet;

const BASE: &str = "https://api.weather.gov";

#[derive(Debug)]
pub enum FetchError {
    Provider(String),
    Cancelled,
}

pub struct Request {
    pub lat: f64,
    pub lon: f64,
    pub requested: OffsetDateTime,
    pub tolerance_seconds: f64,
    pub max_stations: i64,
    pub workers: i64,
    pub timeout: f64,
}

pub trait JsonSource: Clone + Send + Sync + 'static {
    fn get_json(
        &self,
        url: &str,
        timeout: Duration,
    ) -> impl Future<Output = Result<Value, String>> + Send;
}

#[derive(Clone)]
pub struct SurfaceClient {
    client: Result<Client, String>,
}

impl Default for SurfaceClient {
    fn default() -> Self {
        let _ = rustls::crypto::ring::default_provider().install_default();
        Self {
            client: Client::builder()
                .user_agent("SHARPpy-Reimagined (https://github.com/ShianMike/SHARPpy-Reimagined)")
                .build()
                .map_err(|error| error.to_string()),
        }
    }
}

impl JsonSource for SurfaceClient {
    async fn get_json(&self, url: &str, timeout: Duration) -> Result<Value, String> {
        let response = self
            .client
            .as_ref()
            .map_err(Clone::clone)?
            .get(url)
            .header(reqwest::header::ACCEPT, "application/geo+json")
            .timeout(timeout)
            .send()
            .await
            .map_err(|error| error.to_string())?
            .error_for_status()
            .map_err(|error| error.to_string())?;
        let raw = crate::http::body(response, MAX_BYTES).await?;
        let value: Value = serde_json::from_slice(&raw)
            .map_err(|error| format!("NWS API returned invalid JSON: {error}"))?;
        if !value.is_object() {
            return Err("NWS API response root is not an object".into());
        }
        Ok(value)
    }
}

fn check_cancel(cancel: &impl Fn() -> Result<bool, String>) -> Result<(), FetchError> {
    if cancel().map_err(FetchError::Provider)? {
        Err(FetchError::Cancelled)
    } else {
        Ok(())
    }
}

fn distance(lat: f64, lon: f64, station: &super::Station) -> f64 {
    let (a, b) = (lat.to_radians(), station.lat.to_radians());
    let value = ((b - a) / 2.).sin().powi(2)
        + a.cos() * b.cos() * ((station.lon - lon).to_radians() / 2.).sin().powi(2);
    2. * 6371.0088 * value.sqrt().min(1.).asin()
}

enum Outcome {
    Matched(Observation),
    Unmatched,
    Failed(String),
}

pub async fn fetch(
    source: impl JsonSource,
    request: Request,
    cancel: impl Fn() -> Result<bool, String>,
) -> Result<Dataset, FetchError> {
    let Request {
        lat,
        lon,
        requested,
        tolerance_seconds,
        max_stations,
        workers,
        timeout,
    } = request;
    // NWS accepts Python's microsecond timestamps, but rejects nanosecond precision.
    let requested = requested
        .replace_nanosecond(requested.microsecond() * 1000)
        .expect("microseconds fit within one second");
    if !lat.is_finite()
        || !lon.is_finite()
        || !(-90.0..=90.0).contains(&lat)
        || !(-180.0..=180.0).contains(&lon)
    {
        return Err(FetchError::Provider(
            "surface context location is out of range".into(),
        ));
    }
    let tolerance = time::Duration::checked_seconds_f64(tolerance_seconds)
        .filter(|duration| !duration.is_negative())
        .ok_or_else(|| FetchError::Provider("invalid surface-observation tolerance".into()))?;
    let timeout = Duration::try_from_secs_f64(timeout)
        .ok()
        .filter(|duration| !duration.is_zero())
        .ok_or_else(|| FetchError::Provider("invalid surface-observation timeout".into()))?;
    let start = requested
        .checked_sub(tolerance)
        .filter(|time| (1..=9999).contains(&time.year()));
    let end = requested
        .checked_add(tolerance)
        .filter(|time| (1..=9999).contains(&time.year()));
    let (Some(start), Some(end)) = (start, end) else {
        return Err(FetchError::Provider(
            "surface-observation window is out of range".into(),
        ));
    };
    check_cancel(&cancel)?;
    let point = source
        .get_json(&format!("{BASE}/points/{lat:.4},{lon:.4}"), timeout)
        .await
        .map_err(FetchError::Provider)?;
    let missing_link = || {
        FetchError::Provider(
            "NWS does not publish an observation-station collection for this point".into(),
        )
    };
    let link = point
        .get("properties")
        .and_then(|properties| properties.get("observationStations"))
        .and_then(Value::as_str)
        .ok_or_else(missing_link)?;
    let link = Url::parse(link).map_err(|_| missing_link())?;
    if link.scheme() != "https"
        || link.host_str() != Some("api.weather.gov")
        || !link.username().is_empty()
        || link.password().is_some()
        || link.port_or_known_default() != Some(443)
    {
        return Err(missing_link());
    }
    check_cancel(&cancel)?;
    let catalog = stations(
        &source
            .get_json(link.as_str(), timeout)
            .await
            .map_err(FetchError::Provider)?,
    );
    let mut nearby: Vec<_> = catalog
        .iter()
        .enumerate()
        .map(|(index, station)| (index, distance(lat, lon, station)))
        .collect();
    nearby.sort_by(|left, right| left.1.total_cmp(&right.1));
    nearby.truncate(max_stations.clamp(1, MAX_STATIONS as i64) as usize);
    let queried: Vec<_> = nearby.into_iter().map(|(index, _)| index).collect();
    let mut queue = queried.iter().enumerate();
    let mut tasks = JoinSet::new();
    let mut results: Vec<Option<Outcome>> = (0..queried.len()).map(|_| None).collect();
    let workers = workers.clamp(1, MAX_WORKERS as i64) as usize;
    let start = start
        .format(&Rfc3339)
        .map_err(|error| FetchError::Provider(error.to_string()))?;
    let end = end
        .format(&Rfc3339)
        .map_err(|error| FetchError::Provider(error.to_string()))?;
    loop {
        check_cancel(&cancel)?;
        while tasks.len() < workers {
            let Some((rank, &index)) = queue.next() else {
                break;
            };
            let mut url = Url::parse(BASE).expect("constant NWS API origin");
            url.path_segments_mut()
                .expect("NWS URL has a hierarchical path")
                .extend(["stations", &catalog[index].id, "observations"]);
            url.query_pairs_mut()
                .append_pair("start", &start)
                .append_pair("end", &end)
                .append_pair("limit", "50");
            let source = source.clone();
            tasks.spawn(async move {
                let outcome = match source.get_json(url.as_str(), timeout).await {
                    Ok(payload) => {
                        let mut rows = observations(&payload);
                        match nearest_index(
                            rows.iter().map(|row| row.observed_at),
                            requested,
                            tolerance,
                        ) {
                            Some(index) => Outcome::Matched(rows.swap_remove(index)),
                            None => Outcome::Unmatched,
                        }
                    }
                    Err(error) => Outcome::Failed(error),
                };
                (rank, outcome)
            });
        }
        let Some(result) = tasks.join_next().await else {
            break;
        };
        let (rank, outcome) = result.map_err(|error| {
            FetchError::Provider(format!("surface-observation worker failed: {error}"))
        })?;
        results[rank] = Some(outcome);
    }
    check_cancel(&cancel)?;
    let mut matched = Vec::new();
    let mut unmatched = Vec::new();
    let mut failed = Vec::new();
    let mut first_error = None;
    for (&index, result) in queried.iter().zip(results) {
        match result.expect("every queried station completed") {
            Outcome::Matched(observation) => matched.push((index, observation)),
            Outcome::Unmatched => unmatched.push(index),
            Outcome::Failed(error) => {
                first_error.get_or_insert_with(|| format!("{}: {error}", catalog[index].id));
                unmatched.push(index);
                failed.push(index);
            }
        }
    }
    if !queried.is_empty() && failed.len() == queried.len() {
        return Err(FetchError::Provider(format!(
            "nearby surface observations were unavailable: {}",
            first_error.expect("all stations failed")
        )));
    }
    Ok(Dataset {
        requested,
        lat,
        lon,
        catalog,
        matched,
        queried,
        unmatched,
        failed,
        retrieved_at: OffsetDateTime::now_utc(),
        source_url: link.to_string(),
    })
}

#[cfg(test)]
mod tests;

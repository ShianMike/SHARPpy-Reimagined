//! IEM Local Storm Report transport and row parsing, shared by map consumers.

use reqwest::Client;
use std::time::Duration;
use time::{OffsetDateTime, PrimitiveDateTime};

pub const MAX_BYTES: usize = 8 * 1024 * 1024;

#[derive(Debug, PartialEq)]
pub struct Report {
    pub code: String,
    pub valid: OffsetDateTime,
    pub lat: f64,
    pub lon: f64,
    pub magnitude: Option<f64>,
    pub city: String,
    pub county: String,
    pub state: String,
    pub wfo: String,
    pub source: String,
    pub remark: String,
}

pub fn parse(text: &str) -> Vec<Report> {
    let mut reader = csv::ReaderBuilder::new()
        .flexible(true)
        .from_reader(text.as_bytes());
    let Ok(headers) = reader.headers() else {
        return Vec::new();
    };
    let position = |name| {
        (0..headers.len())
            .rev()
            .find(|&index| headers.get(index) == Some(name))
    };
    let (Some(code), Some(valid), Some(lat), Some(lon)) = (
        position("TYPECODE"),
        position("VALID"),
        position("LAT"),
        position("LON"),
    ) else {
        return Vec::new();
    };
    let optional = [
        "MAG", "CITY", "COUNTY", "UGCNAME", "STATE", "WFO", "SOURCE", "REMARK",
    ]
    .map(position);
    let format = time::format_description::parse_borrowed::<2>("[year][month][day][hour][minute]")
        .expect("IEM UTC timestamp format");
    let mut output = Vec::new();
    let mut row = csv::StringRecord::new();
    while let Ok(true) = reader.read_record(&mut row) {
        let value = |position: usize| row.get(position).unwrap_or("").trim();
        let code = value(code).to_ascii_uppercase();
        if !matches!(code.as_str(), "T" | "G" | "D" | "H") {
            continue;
        }
        let finite = |value: &str| value.parse::<f64>().ok().filter(|value| value.is_finite());
        let (Some(lat), Some(lon), Ok(valid)) = (
            finite(value(lat)),
            finite(value(lon)),
            PrimitiveDateTime::parse(value(valid), &format),
        ) else {
            continue;
        };
        if !(-90.0..=90.0).contains(&lat) || !(-180.0..=180.0).contains(&lon) {
            continue;
        }
        let [mag, city, county, ugc, state, wfo, source, remark] =
            optional.map(|position| position.map(value).unwrap_or(""));
        output.push(Report {
            code,
            valid: valid.assume_utc(),
            lat,
            lon,
            magnitude: finite(mag),
            city: city.into(),
            county: if ugc.is_empty() { county } else { ugc }.into(),
            state: state.into(),
            wfo: wfo.into(),
            source: source.into(),
            remark: remark.into(),
        });
    }
    output
}

pub struct ReportsClient {
    client: Result<Client, String>,
}

impl Default for ReportsClient {
    fn default() -> Self {
        let _ = rustls::crypto::ring::default_provider().install_default();
        Self {
            client: Client::builder()
                .user_agent("sharpmod storm reports")
                .build()
                .map_err(|error| error.to_string()),
        }
    }
}

impl ReportsClient {
    pub async fn fetch(&self, url: &str, timeout: f64, limit: usize) -> Result<Vec<u8>, String> {
        let url = reqwest::Url::parse(url).map_err(|error| error.to_string())?;
        if url.scheme() != "https"
            || url.host_str() != Some("mesonet.agron.iastate.edu")
            || url.path() != "/cgi-bin/request/gis/lsr.py"
            || !url.username().is_empty()
            || url.password().is_some()
            || url.port_or_known_default() != Some(443)
        {
            return Err("unsupported IEM storm-report URL".into());
        }
        if !(1..=MAX_BYTES).contains(&limit) || !timeout.is_finite() || timeout <= 0.0 {
            return Err("invalid storm-report transfer limit or timeout".into());
        }
        let timeout = Duration::try_from_secs_f64(timeout).map_err(|error| error.to_string())?;
        let response = self
            .client
            .as_ref()
            .map_err(Clone::clone)?
            .get(url)
            .timeout(timeout)
            .send()
            .await
            .map_err(|error| error.to_string())?
            .error_for_status()
            .map_err(|error| error.to_string())?;
        crate::http::body(response, limit).await
    }
}

#[cfg(test)]
mod tests;

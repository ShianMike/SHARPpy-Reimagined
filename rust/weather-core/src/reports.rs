//! IEM Local Storm Report transport and row parsing, shared by map consumers.

use crate::format_rfc3339_utc;
use reqwest::Client;
use serde::Serialize;
use std::collections::VecDeque;
use std::sync::{Mutex, OnceLock, PoisonError};
use std::time::Duration;
use time::UtcOffset;
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
        if !(-90.0..=90.0).contains(&lat) || !(-180.0..=180.0).contains(&lon) || valid.year() < 1 {
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

pub const ATTRIBUTION: &str = "NWS Local Storm Reports via Iowa Environmental Mesonet";
const BASE_URL: &str = "https://mesonet.agron.iastate.edu/cgi-bin/request/gis/lsr.py";
const SIGNIFICANT_HAIL_IN: f64 = 2.00;
const SIGNIFICANT_WIND_MPH: f64 = 65.0 * 1.15078;
const LIVE_CACHE_TTL: Duration = Duration::from_secs(180);
const ARCHIVE_CACHE_TTL: Duration = Duration::from_secs(3600);
const FAILURE_CACHE_TTL: Duration = Duration::from_secs(60);
const CACHE_MAX_ENTRIES: usize = 32;

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ReportWindowOption {
    pub label: String,
    pub seconds: u32,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ReportFilterOption {
    pub key: String,
    pub label: String,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ReportOptionsPayload {
    pub windows: Vec<ReportWindowOption>,
    pub filters: Vec<ReportFilterOption>,
}

pub fn report_options() -> ReportOptionsPayload {
    ReportOptionsPayload {
        windows: vec![
            ReportWindowOption {
                label: "±1 hour".into(),
                seconds: 7200,
            },
            ReportWindowOption {
                label: "±3 hours".into(),
                seconds: 21600,
            },
            ReportWindowOption {
                label: "±6 hours".into(),
                seconds: 43200,
            },
            ReportWindowOption {
                label: "±12 hours".into(),
                seconds: 86400,
            },
        ],
        filters: vec![
            ReportFilterOption {
                key: "all".into(),
                label: "All convective hazards".into(),
            },
            ReportFilterOption {
                key: "tornado".into(),
                label: "Tornado".into(),
            },
            ReportFilterOption {
                key: "hail".into(),
                label: "Hail".into(),
            },
            ReportFilterOption {
                key: "wind".into(),
                label: "Wind + wind damage".into(),
            },
            ReportFilterOption {
                key: "significant".into(),
                label: "Significant hail/wind".into(),
            },
        ],
    }
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct StormReportPoint {
    pub label: String,
    pub description: String,
    pub fill: Option<String>,
    pub stroke: String,
    pub radius_px: f64,
    pub symbol: String,
    pub caption: String,
    pub significant: bool,
    pub category: String,
    pub valid: Option<String>,
    pub lon: f64,
    pub lat: f64,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct StormReportsSnapshot {
    pub valid_time: String,
    pub window_s: u32,
    pub window_label: String,
    pub hazard_filter: String,
    pub total_count: u32,
    pub shown_count: u32,
    pub subtitle: String,
    pub valid_from: Option<String>,
    pub valid_to: Option<String>,
    pub source_url: String,
    pub attribution: String,
    pub reports: Vec<StormReportPoint>,
}

fn month_abbr(month: time::Month) -> &'static str {
    match month {
        time::Month::January => "Jan",
        time::Month::February => "Feb",
        time::Month::March => "Mar",
        time::Month::April => "Apr",
        time::Month::May => "May",
        time::Month::June => "Jun",
        time::Month::July => "Jul",
        time::Month::August => "Aug",
        time::Month::September => "Sep",
        time::Month::October => "Oct",
        time::Month::November => "Nov",
        time::Month::December => "Dec",
    }
}

fn fmt_dd_mon_hhmm(dt: OffsetDateTime) -> String {
    let utc = dt.to_offset(UtcOffset::UTC);
    format!(
        "{:02} {} {:02}{:02}Z",
        utc.day(),
        month_abbr(utc.month()),
        utc.hour(),
        utc.minute()
    )
}

fn normalise_filter(value: Option<&str>) -> &'static str {
    let raw = value
        .unwrap_or("all")
        .trim()
        .to_ascii_lowercase()
        .replace('_', "-");
    match raw.as_str() {
        "t" | "tornado" => "tornado",
        "h" | "hail" => "hail",
        "g" | "d" | "wind" => "wind",
        "sig" | "significant-severe" | "significant" => "significant",
        _ => "all",
    }
}

fn filter_label(key: &str) -> &'static str {
    match key {
        "tornado" => "Tornado",
        "hail" => "Hail",
        "wind" => "Wind + wind damage",
        "significant" => "Significant hail/wind",
        _ => "All convective hazards",
    }
}

pub fn window_label(window_s: u32) -> String {
    let span_secs = i64::from(window_s).max(900);
    let half_minutes = ((span_secs as f64) / 120.0).round() as i64;
    if half_minutes % 60 == 0 {
        let hours = half_minutes / 60;
        let plural = if hours == 1 { "" } else { "s" };
        format!("±{hours} hour{plural}")
    } else {
        format!("±{half_minutes} min")
    }
}

fn report_is_significant(report: &Report) -> bool {
    match (report.code.as_str(), report.magnitude) {
        ("G" | "D", Some(mag)) => mag >= SIGNIFICANT_WIND_MPH,
        ("H", Some(mag)) => mag >= SIGNIFICANT_HAIL_IN,
        _ => false,
    }
}

fn magnitude_text(report: &Report) -> String {
    let Some(mag) = report.magnitude else {
        return String::new();
    };
    match report.code.as_str() {
        "H" => format!("{mag:.2} in"),
        "G" | "D" => format!("{mag:.0} mph"),
        _ => format!("{mag}"),
    }
}

fn build_report_point(report: &Report) -> (i32, StormReportPoint) {
    let significant = report_is_significant(report);
    let (hazard_label, base_fill, base_stroke, symbol, base_rank) = match report.code.as_str() {
        "T" => ("Tornado", "#E5342F", "#7F0000", "triangle", 5),
        "G" => ("Wind", "#3C7CFF", "#00337F", "circle", 2),
        "D" => ("Wind damage", "#3C7CFF", "#00337F", "square", 2),
        _ => ("Hail", "#26B14E", "#004D1A", "diamond", 1),
    };
    let mag_text = magnitude_text(report);
    let label = if significant {
        if mag_text.is_empty() {
            format!("Sig {hazard_label}")
        } else {
            format!("Sig {hazard_label} {mag_text}")
        }
    } else if mag_text.is_empty() {
        hazard_label.to_owned()
    } else {
        format!("{hazard_label} {mag_text}")
    };
    let caption = match report.code.as_str() {
        "T" => "TOR".to_owned(),
        "D" => "DMG".to_owned(),
        "H" => match report.magnitude {
            Some(mag) => format!("{mag}″"),
            None => hazard_label.to_owned(),
        },
        _ => {
            if mag_text.is_empty() {
                hazard_label.to_owned()
            } else {
                mag_text.clone()
            }
        }
    };
    let mut lines = Vec::new();
    let where_parts: Vec<&str> = [
        report.city.as_str(),
        report.county.as_str(),
        report.state.as_str(),
    ]
    .into_iter()
    .filter(|s| !s.is_empty())
    .collect();
    if !where_parts.is_empty() {
        lines.push(where_parts.join(", "));
    }
    lines.push(format!("Event time: {}", fmt_dd_mon_hhmm(report.valid)));
    if !mag_text.is_empty() {
        lines.push(format!("Magnitude: {mag_text}"));
    }
    if !report.source.is_empty() {
        lines.push(format!("Source: {} (report source)", report.source));
    }
    if !report.remark.is_empty() {
        lines.push(report.remark.clone());
    }
    if !report.wfo.is_empty() {
        lines.push(format!("Issuing office: NWS WFO {}", report.wfo));
    }
    lines.push(format!("Provider: {ATTRIBUTION}"));
    let rank = base_rank + if significant { 2 } else { 0 };
    let radius_px = if significant {
        3.5
    } else if report.code == "T" {
        3.25
    } else {
        2.75
    };
    (
        rank,
        StormReportPoint {
            label,
            description: lines.join("\n"),
            fill: Some(if significant { "#FFFFFF" } else { base_fill }.to_owned()),
            stroke: if significant { "#101010" } else { base_stroke }.to_owned(),
            radius_px,
            symbol: symbol.to_owned(),
            caption,
            significant,
            category: if significant {
                "significant".to_owned()
            } else {
                report.code.to_ascii_lowercase()
            },
            valid: Some(format_rfc3339_utc(report.valid)),
            lon: report.lon,
            lat: report.lat,
        },
    )
}

pub fn build_snapshot(
    parsed: &[Report],
    when: OffsetDateTime,
    window_s: u32,
    hazard_filter: Option<&str>,
    source_url: &str,
) -> StormReportsSnapshot {
    let filter_key = normalise_filter(hazard_filter);
    let total_count = parsed.len() as u32;
    let filtered: Vec<&Report> = parsed
        .iter()
        .filter(|report| match filter_key {
            "all" => true,
            "tornado" => report.code == "T",
            "hail" => report.code == "H",
            "wind" => matches!(report.code.as_str(), "G" | "D"),
            "significant" => report_is_significant(report),
            _ => true,
        })
        .collect();
    let win_label = window_label(window_s);
    if filtered.is_empty() {
        return StormReportsSnapshot {
            valid_time: format_rfc3339_utc(when),
            window_s,
            window_label: win_label,
            hazard_filter: filter_key.to_owned(),
            total_count,
            shown_count: 0,
            subtitle: String::new(),
            valid_from: None,
            valid_to: None,
            source_url: String::new(),
            attribution: String::new(),
            reports: Vec::new(),
        };
    }
    let half = time::Duration::seconds(i64::from(window_s).max(900) / 2);
    let valid_from = when - half;
    let valid_to = when + half;
    let mut ranked: Vec<(i32, StormReportPoint)> =
        filtered.into_iter().map(build_report_point).collect();
    ranked.sort_by_key(|(rank, _)| *rank);
    let reports: Vec<StormReportPoint> = ranked.into_iter().map(|(_, p)| p).collect();
    let shown_count = reports.len() as u32;
    let noun = if shown_count == 1 {
        "storm report"
    } else {
        "storm reports"
    };
    let count_label = if filter_key == "all" {
        noun.to_owned()
    } else {
        format!("{noun} ({})", filter_label(filter_key))
    };
    let subtitle = format!(
        "{shown_count} {count_label} · {win_label} · {}",
        fmt_dd_mon_hhmm(when)
    );
    StormReportsSnapshot {
        valid_time: format_rfc3339_utc(when),
        window_s,
        window_label: win_label,
        hazard_filter: filter_key.to_owned(),
        total_count,
        shown_count,
        subtitle,
        valid_from: Some(format_rfc3339_utc(valid_from)),
        valid_to: Some(format_rfc3339_utc(valid_to)),
        source_url: source_url.to_owned(),
        attribution: ATTRIBUTION.to_owned(),
        reports,
    }
}

pub fn build_url(window_s: u32, around: OffsetDateTime) -> String {
    let span = time::Duration::seconds(i64::from(window_s).max(900));
    let half = span / 2;
    let start_dt: OffsetDateTime = around - half;
    let end_dt: OffsetDateTime = around + half;
    let start = start_dt.to_offset(UtcOffset::UTC);
    let end = end_dt.to_offset(UtcOffset::UTC);
    let mut url = reqwest::Url::parse(BASE_URL).expect("static IEM URL");
    url.query_pairs_mut().extend_pairs([
        ("justcsv", "1".to_owned()),
        ("year1", start.year().to_string()),
        ("month1", u8::from(start.month()).to_string()),
        ("day1", start.day().to_string()),
        ("hour1", start.hour().to_string()),
        ("minute1", start.minute().to_string()),
        ("year2", end.year().to_string()),
        ("month2", u8::from(end.month()).to_string()),
        ("day2", end.day().to_string()),
        ("hour2", end.hour().to_string()),
        ("minute2", end.minute().to_string()),
    ]);
    url.to_string()
}

struct CachedFeed {
    url: String,
    expires_at: std::time::Instant,
    text: Option<String>,
}

fn cache_slot() -> &'static Mutex<VecDeque<CachedFeed>> {
    static CACHE: OnceLock<Mutex<VecDeque<CachedFeed>>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(VecDeque::new()))
}

pub fn clear_cache() {
    cache_slot()
        .lock()
        .unwrap_or_else(PoisonError::into_inner)
        .clear();
}

pub fn shared_client() -> &'static ReportsClient {
    static CLIENT: OnceLock<ReportsClient> = OnceLock::new();
    CLIENT.get_or_init(ReportsClient::default)
}

impl ReportsClient {
    pub async fn fetch_snapshot(
        &self,
        when: OffsetDateTime,
        window_s: u32,
        hazard_filter: Option<&str>,
        now: OffsetDateTime,
    ) -> Result<StormReportsSnapshot, String> {
        let url = build_url(window_s, when);
        let settled = (now - (when + time::Duration::seconds(i64::from(window_s).max(900) / 2)))
            >= time::Duration::hours(6);
        let cached_text = {
            let now_inst = std::time::Instant::now();
            let mut cache = cache_slot().lock().unwrap_or_else(PoisonError::into_inner);
            if let Some(pos) = cache.iter().position(|entry| entry.url == url) {
                if cache[pos].expires_at > now_inst {
                    match &cache[pos].text {
                        Some(cached) => Some(cached.clone()),
                        None => return Err("storm reports were unavailable for this window".into()),
                    }
                } else {
                    cache.remove(pos);
                    None
                }
            } else {
                None
            }
        };
        let csv_text = match cached_text {
            Some(cached) => cached,
            None => match self.fetch(&url, 20.0, MAX_BYTES).await {
                Ok(bytes) => {
                    let body = String::from_utf8_lossy(&bytes).into_owned();
                    let ttl = if settled {
                        ARCHIVE_CACHE_TTL
                    } else {
                        LIVE_CACHE_TTL
                    };
                    let mut cache = cache_slot().lock().unwrap_or_else(PoisonError::into_inner);
                    cache.retain(|e| e.url != url);
                    if cache.len() >= CACHE_MAX_ENTRIES {
                        cache.pop_front();
                    }
                    cache.push_back(CachedFeed {
                        url: url.clone(),
                        expires_at: std::time::Instant::now() + ttl,
                        text: Some(body.clone()),
                    });
                    body
                }
                Err(err) => {
                    let mut cache = cache_slot().lock().unwrap_or_else(PoisonError::into_inner);
                    cache.retain(|e| e.url != url);
                    if cache.len() >= CACHE_MAX_ENTRIES {
                        cache.pop_front();
                    }
                    cache.push_back(CachedFeed {
                        url,
                        expires_at: std::time::Instant::now() + FAILURE_CACHE_TTL,
                        text: None,
                    });
                    return Err(format!("storm reports were unavailable: {err}"));
                }
            },
        };
        let parsed = parse(&csv_text);
        Ok(build_snapshot(&parsed, when, window_s, hazard_filter, &url))
    }
}

#[cfg(test)]
mod tests;

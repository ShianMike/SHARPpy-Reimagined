//! Time-aware SPC convective outlook resolution, GeoJSON decoding, and caching.

use crate::format_rfc3339_utc;
use reqwest::{Client, StatusCode};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::sync::{Arc, Mutex, OnceLock, PoisonError};
use std::time::{Duration, Instant};
use time::{Date, Month, OffsetDateTime, PrimitiveDateTime, Time, UtcOffset};

const BASE: &str = "https://www.spc.noaa.gov/products/outlook";
const ARCHIVE_FIRST_YEAR: i32 = 2020;
const CONVECTIVE_DAY_START_HOUR: u8 = 12;
const MAX_CANDIDATES: usize = 12;
const MAX_OUTLOOK_BYTES: usize = 8 * 1024 * 1024;
const MAX_SHAPES_PER_LAYER: usize = 256;
const MAX_RINGS_PER_SHAPE: usize = 512;
const MAX_POINTS_PER_RING: usize = 100_000;
const MAX_POINTS_PER_LAYER: usize = 500_000;
const MIN_RING_POINTS: usize = 3;
const LIVE_CACHE_TTL: Duration = Duration::from_secs(300);
const ARCHIVE_CACHE_TTL: Duration = Duration::from_secs(24 * 3600);
const MISS_CACHE_TTL: Duration = Duration::from_secs(900);
const DAY_MISS_CACHE_TTL: Duration = Duration::from_secs(6 * 3600);
const CACHE_MAX_ENTRIES: usize = 512;
const SIGNIFICANT_RANK: i64 = 10_000;
pub const ATTRIBUTION: &str = "NOAA/NWS Storm Prediction Center";

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct OutlookRequest {
    pub url: String,
    pub day: u8,
    pub issued: Option<String>,
    pub label: String,
    pub live: bool,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct OutlookArea {
    pub rings: Vec<Vec<[f64; 2]>>,
    pub bounds: [f64; 4],
    pub stroke: String,
    pub fill: String,
    pub label: String,
    pub description: String,
    pub rank: i64,
    pub hatch: bool,
    pub hatch_level: u8,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct OutlookLayer {
    pub key: String,
    pub title: String,
    pub subtitle: String,
    pub short_name: String,
    pub valid_from: Option<String>,
    pub valid_to: Option<String>,
    pub issued: Option<String>,
    pub source_url: String,
    pub attribution: String,
    pub bounds: Option<[f64; 4]>,
    pub areas: Vec<OutlookArea>,
    #[serde(skip)]
    valid_from_dt: Option<OffsetDateTime>,
    #[serde(skip)]
    valid_to_dt: Option<OffsetDateTime>,
}

impl OutlookLayer {
    pub fn covers(&self, when: OffsetDateTime) -> bool {
        if self.valid_from_dt.is_none() && self.valid_to_dt.is_none() {
            return true;
        }
        if self.valid_from_dt.is_some_and(|start| when < start) {
            return false;
        }
        if self.valid_to_dt.is_some_and(|end| when >= end) {
            return false;
        }
        true
    }
}

struct ProductSpec {
    key: &'static str,
    label: &'static str,
    days: &'static [u8],
    short: &'static str,
}

fn product_spec(product: &str) -> ProductSpec {
    match product {
        "torn" => ProductSpec {
            key: "torn",
            label: "Tornado probability",
            days: &[1, 2],
            short: "TOR",
        },
        "wind" => ProductSpec {
            key: "wind",
            label: "Wind probability",
            days: &[1, 2],
            short: "WIND",
        },
        "hail" => ProductSpec {
            key: "hail",
            label: "Hail probability",
            days: &[1, 2],
            short: "HAIL",
        },
        "prob" => ProductSpec {
            key: "prob",
            label: "Total severe probability",
            days: &[3],
            short: "SVR",
        },
        _ => ProductSpec {
            key: "cat",
            label: "Categorical risk",
            days: &[1, 2, 3],
            short: "",
        },
    }
}

pub fn convective_day_start(when: OffsetDateTime) -> OffsetDateTime {
    let utc = when.to_offset(UtcOffset::UTC);
    let date = if utc.hour() < CONVECTIVE_DAY_START_HOUR {
        utc.date().previous_day().unwrap_or(utc.date())
    } else {
        utc.date()
    };
    OffsetDateTime::new_utc(
        date,
        Time::from_hms(CONVECTIVE_DAY_START_HOUR, 0, 0).expect("12Z"),
    )
}

fn issuances_for(day: u8, day_start: OffsetDateTime) -> Vec<OffsetDateTime> {
    let slots: &[(u8, u8)] = match day {
        1 => &[(20, 0), (16, 30), (13, 0), (12, 0), (1, 0)],
        2 => &[(17, 30), (6, 0), (7, 0)],
        3 => &[(19, 30), (7, 30), (8, 30)],
        _ => &[],
    };
    let base_date = day_start.date() - time::Duration::days(i64::from(day) - 1);
    slots
        .iter()
        .map(|&(hour, minute)| {
            let date = if day == 1 && hour == 1 {
                base_date.next_day().unwrap_or(base_date)
            } else {
                base_date
            };
            OffsetDateTime::new_utc(
                date,
                Time::from_hms(hour, minute, 0).expect("valid issuance clock"),
            )
        })
        .collect()
}

fn candidates_internal(
    valid_time: OffsetDateTime,
    now: OffsetDateTime,
    product: &str,
) -> Vec<(OutlookRequest, Option<OffsetDateTime>)> {
    let valid_utc = valid_time.to_offset(UtcOffset::UTC);
    let now_utc = now.to_offset(UtcOffset::UTC);
    let day_start = convective_day_start(valid_utc);
    if day_start.year() < ARCHIVE_FIRST_YEAR {
        return Vec::new();
    }
    let offset = (day_start - convective_day_start(now_utc)).whole_days();
    let spec = product_spec(product);
    let mut requests = Vec::new();
    for &day in spec.days {
        if offset == i64::from(day) - 1
            || (offset == i64::from(day) && now_utc.hour() < CONVECTIVE_DAY_START_HOUR)
        {
            requests.push((
                OutlookRequest {
                    url: format!("{BASE}/day{day}otlk_{}.nolyr.geojson", spec.key),
                    day,
                    issued: None,
                    label: format!("Day {day} (current)"),
                    live: true,
                },
                None,
            ));
        }
        let mut issuances: Vec<OffsetDateTime> = issuances_for(day, day_start)
            .into_iter()
            .filter(|issued| *issued <= now_utc && issued.year() >= ARCHIVE_FIRST_YEAR)
            .collect();
        issuances.sort_unstable_by(|a, b| b.cmp(a));
        let mut ordered: Vec<OffsetDateTime> = issuances
            .iter()
            .copied()
            .filter(|issued| *issued <= valid_utc)
            .collect();
        ordered.extend(issuances.into_iter().filter(|issued| *issued > valid_utc));
        for issued in ordered {
            let url = format!(
                "{BASE}/archive/{:04}/day{day}otlk_{:04}{:02}{:02}_{:02}{:02}_{}.nolyr.geojson",
                issued.year(),
                issued.year(),
                u8::from(issued.month()),
                issued.day(),
                issued.hour(),
                issued.minute(),
                spec.key
            );
            let label = format!(
                "Day {day} · {:02}{:02}Z issuance",
                issued.hour(),
                issued.minute()
            );
            requests.push((
                OutlookRequest {
                    url,
                    day,
                    issued: Some(format_rfc3339_utc(issued)),
                    label,
                    live: false,
                },
                Some(issued),
            ));
        }
    }
    requests.truncate(MAX_CANDIDATES);
    requests
}

pub fn candidates_for(
    valid_time: OffsetDateTime,
    now: OffsetDateTime,
    product: &str,
) -> Vec<OutlookRequest> {
    candidates_internal(valid_time, now, product)
        .into_iter()
        .map(|(req, _)| req)
        .collect()
}

fn cig_level(label: &str) -> u8 {
    let text = label.trim().to_ascii_uppercase();
    let Some(rest) = text.strip_prefix("CIG") else {
        return 0;
    };
    rest.parse::<u8>()
        .ok()
        .filter(|&level| (1..=3).contains(&level))
        .unwrap_or(0)
}

fn is_qualifier_label(label: &str) -> bool {
    label.trim().eq_ignore_ascii_case("SIGN") || cig_level(label) > 0
}

fn palette_colour(product: &str, label: &str) -> Option<(&'static str, &'static str)> {
    match product {
        "torn" => match label {
            "0.02" => Some(("#005500", "#66A366")),
            "0.05" => Some(("#70380f", "#9d4e15")),
            "0.10" => Some(("#DDAA00", "#FFE066")),
            "0.15" => Some(("#CC0000", "#E06666")),
            "0.30" => Some(("#CC00CC", "#EE99EE")),
            "0.45" | "0.60" => Some(("#a300cc", "#d633ff")),
            "SIGN" => Some(("#000000", "#888888")),
            _ => None,
        },
        "wind" | "hail" | "prob" => match label {
            "0.05" => Some(("#70380f", "#9d4e15")),
            "0.15" => Some(("#DDAA00", "#FFE066")),
            "0.30" => Some(("#CC0000", "#E06666")),
            "0.45" => Some(("#CC00CC", "#EE99EE")),
            "0.60" => Some(("#a300cc", "#d633ff")),
            "0.75" => Some(("#7d00b3", "#c04dff")),
            "0.90" => Some(("#5c0080", "#a366ff")),
            "SIGN" => Some(("#000000", "#888888")),
            _ => None,
        },
        _ => None,
    }
}

fn categorical_spec(dn: i64) -> Option<(&'static str, i64, &'static str, &'static str)> {
    match dn {
        2 => Some(("General Thunderstorms", 0, "#55BB55", "#C1E9C1")),
        3 => Some(("Marginal Risk", 1, "#005500", "#66A366")),
        4 => Some(("Slight Risk", 2, "#DDAA00", "#FFE066")),
        5 => Some(("Enhanced Risk", 3, "#FF6600", "#FFA366")),
        6 => Some(("Moderate Risk", 4, "#CC0000", "#E06666")),
        8 => Some(("High Risk", 5, "#CC00CC", "#EE99EE")),
        _ => None,
    }
}

fn format_g(value: f64) -> String {
    if (value - value.round()).abs() < 1e-9 {
        format!("{}", value.round() as i64)
    } else {
        let text = format!("{value:.6}");
        text.trim_end_matches('0').trim_end_matches('.').to_owned()
    }
}

fn feature_style(
    product: &str,
    dn: Option<i64>,
    label: &str,
) -> (String, i64, &'static str, &'static str, bool, u8) {
    let level = cig_level(label);
    if level > 0 || label.trim().eq_ignore_ascii_case("SIGN") {
        let (stroke, fill) = palette_colour(product, "SIGN").unwrap_or(("#000000", "#888888"));
        let name = if level > 0 {
            format!("Conditional Intensity Group {level}")
        } else {
            "Significant severe".to_owned()
        };
        return (
            name,
            SIGNIFICANT_RANK + i64::from(level),
            stroke,
            fill,
            true,
            level,
        );
    }
    if product == "cat" {
        if let Some((name, rank, stroke, fill)) = dn.and_then(categorical_spec) {
            return (name.to_owned(), rank, stroke, fill, false, 0);
        }
        let name = if label.is_empty() { "Unknown" } else { label };
        return (
            name.to_owned(),
            6 + dn.unwrap_or(0),
            "#FFFFFF",
            "#FFFFFF",
            false,
            0,
        );
    }
    let (stroke, fill) = palette_colour(product, label).unwrap_or(("#FFFFFF", "#FFFFFF"));
    let parsed = label.parse::<f64>().ok().filter(|v| v.is_finite());
    let rank = parsed
        .map(|v| (v * 1000.0).round() as i64)
        .unwrap_or_else(|| dn.unwrap_or(0));
    let name = parsed
        .map(|v| format!("{}% probability", format_g(v * 100.0)))
        .unwrap_or_else(|| label.to_owned());
    (name, rank, stroke, fill, false, 0)
}

fn display_label(product: &str, raw: &str) -> String {
    if product == "cat" || is_qualifier_label(raw) {
        return raw.to_owned();
    }
    match raw.parse::<f64>() {
        Ok(v) if v.is_finite() => format!("{}%", format_g(v * 100.0)),
        _ => raw.to_owned(),
    }
}

fn colour_or_fallback(value: Option<&Value>, fallback: &str) -> String {
    if let Some(text) = value.and_then(Value::as_str) {
        let trimmed = text.trim();
        if trimmed.len() == 7
            && trimmed.starts_with('#')
            && trimmed[1..].bytes().all(|b| b.is_ascii_hexdigit())
        {
            return trimmed.to_owned();
        }
    }
    fallback.to_owned()
}

fn parse_stamp(value: Option<&Value>) -> Option<OffsetDateTime> {
    let text = value.and_then(Value::as_str)?;
    if text.len() != 12 || !text.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let year: i32 = text[0..4].parse().ok()?;
    let month: u8 = text[4..6].parse().ok()?;
    let day: u8 = text[6..8].parse().ok()?;
    let hour: u8 = text[8..10].parse().ok()?;
    let minute: u8 = text[10..12].parse().ok()?;
    let month = Month::try_from(month).ok()?;
    let date = Date::from_calendar_date(year, month, day).ok()?;
    let time = Time::from_hms(hour, minute, 0).ok()?;
    Some(PrimitiveDateTime::new(date, time).assume_utc())
}

fn parse_ring(raw_ring: &Value) -> Option<Vec<[f64; 2]>> {
    let items = raw_ring.as_array()?;
    if items.len() > MAX_POINTS_PER_RING {
        return None;
    }
    let mut points: Vec<[f64; 2]> = Vec::new();
    for item in items {
        let pair = item.as_array()?;
        if pair.len() < 2 {
            continue;
        }
        let (Some(lon), Some(lat)) = (pair[0].as_f64(), pair[1].as_f64()) else {
            continue;
        };
        if !lon.is_finite()
            || !lat.is_finite()
            || !(-180.0..=180.0).contains(&lon)
            || !(-90.0..=90.0).contains(&lat)
        {
            continue;
        }
        if points.last() == Some(&[lon, lat]) {
            continue;
        }
        points.push([lon, lat]);
    }
    if points.len() > 1 && points.first() == points.last() {
        points.pop();
    }
    (points.len() >= MIN_RING_POINTS).then_some(points)
}

fn rings_from_geometry(geometry: Option<&Value>) -> Vec<Vec<Vec<[f64; 2]>>> {
    let Some(obj) = geometry.and_then(Value::as_object) else {
        return Vec::new();
    };
    let kind = obj.get("type").and_then(Value::as_str).unwrap_or("");
    let Some(coords) = obj.get("coordinates").and_then(Value::as_array) else {
        return Vec::new();
    };
    let raw_polygons: Vec<&Value> = match kind {
        "Polygon" => vec![obj.get("coordinates").expect("checked above")],
        "MultiPolygon" => coords.iter().collect(),
        _ => return Vec::new(),
    };
    let mut groups = Vec::new();
    for raw_polygon in raw_polygons {
        let Some(raw_rings) = raw_polygon.as_array() else {
            continue;
        };
        let mut rings = Vec::new();
        for raw_ring in raw_rings.iter().take(MAX_RINGS_PER_SHAPE) {
            if let Some(ring) = parse_ring(raw_ring) {
                rings.push(ring);
            }
        }
        if !rings.is_empty() {
            groups.push(rings);
        }
    }
    groups
}

fn bounds_of(rings: &[Vec<[f64; 2]>]) -> Option<[f64; 4]> {
    let mut min_lon = f64::INFINITY;
    let mut max_lon = f64::NEG_INFINITY;
    let mut min_lat = f64::INFINITY;
    let mut max_lat = f64::NEG_INFINITY;
    for ring in rings {
        for &[lon, lat] in ring {
            min_lon = min_lon.min(lon);
            max_lon = max_lon.max(lon);
            min_lat = min_lat.min(lat);
            max_lat = max_lat.max(lat);
        }
    }
    if min_lon > max_lon || min_lat > max_lat {
        return None;
    }
    Some([min_lon, max_lon, min_lat, max_lat])
}

fn short_month(month: Month) -> &'static str {
    match month {
        Month::January => "Jan",
        Month::February => "Feb",
        Month::March => "Mar",
        Month::April => "Apr",
        Month::May => "May",
        Month::June => "Jun",
        Month::July => "Jul",
        Month::August => "Aug",
        Month::September => "Sep",
        Month::October => "Oct",
        Month::November => "Nov",
        Month::December => "Dec",
    }
}

pub fn parse_outlook(
    payload: &[u8],
    source_url: &str,
    day: u8,
    label: &str,
    product: &str,
) -> Result<OutlookLayer, String> {
    let text = std::str::from_utf8(payload)
        .map_err(|error| format!("outlook payload is not UTF-8: {error}"))?;
    let document: Value = serde_json::from_str(text)
        .map_err(|error| format!("outlook payload is not valid JSON: {error}"))?;
    let Some(obj) = document.as_object() else {
        return Err("outlook payload is not a GeoJSON object".into());
    };
    let Some(features) = obj.get("features").and_then(Value::as_array) else {
        return Err("outlook payload has no feature list".into());
    };

    let mut areas: Vec<OutlookArea> = Vec::new();
    let mut total_points = 0;
    let mut truncated = false;
    let mut valid_from: Option<OffsetDateTime> = None;
    let mut valid_to: Option<OffsetDateTime> = None;
    let mut issued: Option<OffsetDateTime> = None;

    for feature in features.iter().take(MAX_SHAPES_PER_LAYER) {
        if truncated {
            break;
        }
        let Some(feature_obj) = feature.as_object() else {
            continue;
        };
        let props = feature_obj.get("properties").and_then(Value::as_object);
        if let Some(props) = props {
            valid_from = valid_from.or_else(|| parse_stamp(props.get("VALID")));
            valid_to = valid_to.or_else(|| parse_stamp(props.get("EXPIRE")));
            issued = issued.or_else(|| parse_stamp(props.get("ISSUE")));
        }
        let dn = props.and_then(|p| p.get("DN")).and_then(|v| {
            if v.is_boolean() {
                None
            } else {
                v.as_i64()
            }
        });
        let code = props
            .and_then(|p| p.get("LABEL"))
            .and_then(|v| match v {
                Value::Null => None,
                Value::String(s) => Some(s.trim().to_owned()),
                other => Some(other.to_string().trim().to_owned()),
            })
            .unwrap_or_default();
        if dn == Some(0) || (code.is_empty() && dn.is_none()) {
            continue;
        }
        let (default_name, rank, default_stroke, default_fill, hatch, hatch_level) =
            feature_style(product, dn, &code);
        let description = props
            .and_then(|p| p.get("LABEL2"))
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .unwrap_or(&default_name)
            .to_owned();
        let stroke = colour_or_fallback(props.and_then(|p| p.get("stroke")), default_stroke);
        let fill = colour_or_fallback(props.and_then(|p| p.get("fill")), default_fill);
        let display = display_label(product, &code);

        for rings in rings_from_geometry(feature_obj.get("geometry")) {
            let Some(bounds) = bounds_of(&rings) else {
                continue;
            };
            let points: usize = rings.iter().map(Vec::len).sum();
            if total_points + points > MAX_POINTS_PER_LAYER {
                truncated = true;
                break;
            }
            total_points += points;
            areas.push(OutlookArea {
                rings,
                bounds,
                stroke: stroke.clone(),
                fill: fill.clone(),
                label: display.clone(),
                description: description.clone(),
                rank,
                hatch,
                hatch_level,
            });
        }
    }

    areas.sort_by_key(|area| area.rank);
    let layer_bounds = if areas.is_empty() {
        None
    } else {
        let mut min_lon = f64::INFINITY;
        let mut max_lon = f64::NEG_INFINITY;
        let mut min_lat = f64::INFINITY;
        let mut max_lat = f64::NEG_INFINITY;
        for area in &areas {
            min_lon = min_lon.min(area.bounds[0]);
            max_lon = max_lon.max(area.bounds[1]);
            min_lat = min_lat.min(area.bounds[2]);
            max_lat = max_lat.max(area.bounds[3]);
        }
        Some([min_lon, max_lon, min_lat, max_lat])
    };

    let subtitle = match (valid_from, valid_to) {
        (Some(vf), Some(vt)) => {
            let prefix = if label.is_empty() {
                String::new()
            } else {
                format!("{label} · ")
            };
            if vf.date() == vt.date() {
                format!(
                    "{prefix}valid {:02} {} {:02}{:02}–{:02}{:02}Z",
                    vf.day(),
                    short_month(vf.month()),
                    vf.hour(),
                    vf.minute(),
                    vt.hour(),
                    vt.minute()
                )
            } else {
                format!(
                    "{prefix}valid {:02} {} {:02}{:02}Z → {:02} {} {:02}{:02}Z",
                    vf.day(),
                    short_month(vf.month()),
                    vf.hour(),
                    vf.minute(),
                    vt.day(),
                    short_month(vt.month()),
                    vt.hour(),
                    vt.minute()
                )
            }
        }
        _ => label.to_owned(),
    };
    let spec = product_spec(product);
    Ok(OutlookLayer {
        key: "spc_outlook".into(),
        title: format!(
            "SPC convective outlook — Day {day} {}",
            spec.label.to_ascii_lowercase()
        ),
        subtitle,
        short_name: spec.short.into(),
        valid_from: valid_from.map(format_rfc3339_utc),
        valid_to: valid_to.map(format_rfc3339_utc),
        issued: issued.map(format_rfc3339_utc),
        source_url: source_url.into(),
        attribution: ATTRIBUTION.into(),
        bounds: layer_bounds,
        areas,
        valid_from_dt: valid_from,
        valid_to_dt: valid_to,
    })
}

struct CacheEntry {
    expires_at: Instant,
    layer: Option<Arc<OutlookLayer>>,
}

pub struct OutlookClient {
    client: Result<Client, String>,
    layers: Mutex<HashMap<String, CacheEntry>>,
    day_misses: Mutex<HashMap<String, Instant>>,
}

pub fn client() -> &'static OutlookClient {
    static CLIENT: OnceLock<OutlookClient> = OnceLock::new();
    CLIENT.get_or_init(OutlookClient::default)
}

impl Default for OutlookClient {
    fn default() -> Self {
        let _ = rustls::crypto::ring::default_provider().install_default();
        let timeout = std::env::var("SHARPMOD_REMOTE_TIMEOUT")
            .ok()
            .and_then(|value| value.parse::<f64>().ok())
            .filter(|value| value.is_finite() && *value > 0.0)
            .unwrap_or(15.0);
        Self {
            client: Client::builder()
                .user_agent("SHARPpy-Reimagined")
                .connect_timeout(Duration::from_secs(8))
                .timeout(Duration::from_secs_f64(timeout))
                .build()
                .map_err(|error| error.to_string()),
            layers: Mutex::new(HashMap::new()),
            day_misses: Mutex::new(HashMap::new()),
        }
    }
}

impl OutlookClient {
    pub fn clear(&self) {
        self.layers
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clear();
        self.day_misses
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clear();
    }

    pub async fn fetch(
        &self,
        valid_time: OffsetDateTime,
        now: OffsetDateTime,
        product: &str,
    ) -> Result<Option<Arc<OutlookLayer>>, String> {
        let candidates = candidates_internal(valid_time, now, product);
        if candidates.is_empty() {
            return Ok(None);
        }
        let day_key = candidates
            .iter()
            .map(|(req, _)| req.url.as_str())
            .collect::<Vec<_>>()
            .join("|");
        if self.is_day_missed(&day_key) {
            return Ok(None);
        }
        let recent = candidates.iter().any(|(req, issued)| {
            req.live || issued.is_some_and(|stamp| stamp > now - time::Duration::days(1))
        });
        let miss_ttl = if recent {
            LIVE_CACHE_TTL
        } else {
            MISS_CACHE_TTL
        };
        let mut fallback: Option<Arc<OutlookLayer>> = None;
        let mut exhausted = true;
        for (request, _) in &candidates {
            if let Some(cached) = self.cache_get(&request.url) {
                let Some(layer) = cached else {
                    continue;
                };
                if layer.covers(valid_time) {
                    return Ok(Some(layer));
                }
                if fallback.is_none() {
                    fallback = Some(layer);
                }
                continue;
            }
            match self.download(&request.url).await {
                Ok(Some(bytes)) => {
                    let Ok(parsed) =
                        parse_outlook(&bytes, &request.url, request.day, &request.label, product)
                    else {
                        exhausted = false;
                        continue;
                    };
                    if parsed.valid_from_dt.is_none() || parsed.valid_to_dt.is_none() {
                        exhausted = false;
                        continue;
                    }
                    let layer = Arc::new(parsed);
                    let ttl = if request.live {
                        LIVE_CACHE_TTL
                    } else {
                        ARCHIVE_CACHE_TTL
                    };
                    self.cache_put(&request.url, Some(Arc::clone(&layer)), ttl);
                    if layer.covers(valid_time) {
                        return Ok(Some(layer));
                    }
                    if fallback.is_none() {
                        fallback = Some(layer);
                    }
                }
                Ok(None) => {
                    self.cache_put(&request.url, None, miss_ttl);
                }
                Err(_) => {
                    exhausted = false;
                }
            }
        }
        if fallback.is_some() {
            return Ok(fallback);
        }
        if exhausted {
            self.remember_day_miss(
                day_key,
                if recent {
                    LIVE_CACHE_TTL
                } else {
                    DAY_MISS_CACHE_TTL
                },
            );
        }
        Ok(None)
    }

    fn is_day_missed(&self, key: &str) -> bool {
        let now = Instant::now();
        let mut misses = self
            .day_misses
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        match misses.get(key) {
            Some(&expires_at) if expires_at >= now => true,
            Some(_) => {
                misses.remove(key);
                false
            }
            None => false,
        }
    }

    fn remember_day_miss(&self, key: String, ttl: Duration) {
        let now = Instant::now();
        let mut misses = self
            .day_misses
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        if misses.len() >= CACHE_MAX_ENTRIES {
            misses.retain(|_, expires| *expires >= now);
        }
        if misses.len() >= CACHE_MAX_ENTRIES {
            if let Some(oldest) = misses
                .iter()
                .min_by_key(|(_, expires)| *expires)
                .map(|(k, _)| k.clone())
            {
                misses.remove(&oldest);
            }
        }
        misses.insert(key, now + ttl);
    }

    fn cache_get(&self, url: &str) -> Option<Option<Arc<OutlookLayer>>> {
        let now = Instant::now();
        let mut cache = self.layers.lock().unwrap_or_else(PoisonError::into_inner);
        match cache.get(url) {
            Some(entry) if entry.expires_at >= now => Some(entry.layer.clone()),
            Some(_) => {
                cache.remove(url);
                None
            }
            None => None,
        }
    }

    fn cache_put(&self, url: &str, layer: Option<Arc<OutlookLayer>>, ttl: Duration) {
        let now = Instant::now();
        let mut cache = self.layers.lock().unwrap_or_else(PoisonError::into_inner);
        if cache.len() >= CACHE_MAX_ENTRIES {
            cache.retain(|_, entry| entry.expires_at >= now);
        }
        if cache.len() >= CACHE_MAX_ENTRIES {
            if let Some(oldest) = cache
                .iter()
                .min_by_key(|(_, entry)| entry.expires_at)
                .map(|(k, _)| k.clone())
            {
                cache.remove(&oldest);
            }
        }
        cache.insert(
            url.to_owned(),
            CacheEntry {
                expires_at: now + ttl,
                layer,
            },
        );
    }

    async fn download(&self, url: &str) -> Result<Option<Vec<u8>>, String> {
        let response = self
            .client
            .as_ref()
            .map_err(Clone::clone)?
            .get(url)
            .send()
            .await
            .map_err(|error| error.to_string())?;
        if response.status() == StatusCode::NOT_FOUND {
            return Ok(None);
        }
        let response = response
            .error_for_status()
            .map_err(|error| error.to_string())?;
        crate::http::body(response, MAX_OUTLOOK_BYTES)
            .await
            .map(Some)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use time::format_description::well_known::Rfc3339;

    fn instant(value: &str) -> OffsetDateTime {
        OffsetDateTime::parse(value, &Rfc3339).unwrap()
    }

    fn sample_payload(valid: &str, expire: &str, issue: &str) -> Vec<u8> {
        serde_json::json!({
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "MultiPolygon",
                        "coordinates": [[[[-97.0, 38.0], [-93.0, 38.0], [-93.0, 42.0], [-97.0, 42.0], [-97.0, 38.0]]]]
                    },
                    "properties": {
                        "DN": 8, "LABEL": "HIGH", "LABEL2": "High Risk",
                        "stroke": "#CC00CC", "fill": "#EE99EE",
                        "VALID": valid, "EXPIRE": expire, "ISSUE": issue
                    }
                },
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[-100.0, 35.0], [-90.0, 35.0], [-90.0, 45.0], [-100.0, 45.0], [-100.0, 35.0]],
                            [[-97.0, 38.0], [-93.0, 38.0], [-93.0, 42.0], [-97.0, 42.0], [-97.0, 38.0]]
                        ]
                    },
                    "properties": {
                        "DN": 3, "LABEL": "MRGL", "LABEL2": "Marginal Risk",
                        "stroke": "#005500", "fill": "#66A366",
                        "VALID": valid, "EXPIRE": expire, "ISSUE": issue
                    }
                }
            ]
        })
        .to_string()
        .into_bytes()
    }

    #[test]
    fn candidates_respect_convective_day_and_issuance_order() {
        let now = instant("2025-05-14T15:00:00Z");
        assert!(candidates_for(instant("1998-05-20T18:00:00Z"), now, "cat").is_empty());
        assert!(candidates_for(instant("2025-05-20T15:00:00Z"), now, "cat").is_empty());

        let got = candidates_for(instant("2023-03-31T18:00:00Z"), now, "cat");
        let day1: Vec<&OutlookRequest> = got.iter().filter(|c| c.day == 1).collect();
        assert_eq!(day1[0].issued.as_deref(), Some("2023-03-31T16:30:00Z"));
        assert!(day1
            .iter()
            .any(|c| c.url.contains("day1otlk_20230401_0100_cat")));

        let overnight = candidates_for(instant("2023-04-01T06:00:00Z"), now, "cat");
        assert!(overnight[0].url.contains("day1otlk_20230401_0100_cat"));
    }

    #[test]
    fn parse_orders_severity_preserves_holes_and_formats_window() {
        let payload = sample_payload("202503311630", "202504011200", "202503311619");
        let layer = parse_outlook(&payload, "u", 1, "Day 1 1630Z", "cat").unwrap();
        assert_eq!(layer.valid_from.as_deref(), Some("2025-03-31T16:30:00Z"));
        assert_eq!(layer.valid_to.as_deref(), Some("2025-04-01T12:00:00Z"));
        assert_eq!(layer.issued.as_deref(), Some("2025-03-31T16:19:00Z"));
        let labels: Vec<&str> = layer.areas.iter().map(|a| a.label.as_str()).collect();
        assert_eq!(labels, vec!["MRGL", "HIGH"]);
        assert_eq!(layer.areas[0].rings.len(), 2);
        assert_eq!(layer.areas[0].rings[0].len(), 4);
        assert!(layer.covers(instant("2025-03-31T18:00:00Z")));
        assert!(!layer.covers(instant("2025-04-01T12:00:00Z")));

        let same_day = parse_outlook(
            &sample_payload("202609230000", "202609231200", "202609230000"),
            "u",
            1,
            "Day 1 · 0000Z issuance",
            "cat",
        )
        .unwrap();
        assert!(same_day.subtitle.contains("valid 23 Sep 0000–1200Z"));
    }
}

//! UWyo station availability, catalogue filtering, and fixed-width sounding decoding.

use crate::format_rfc3339_utc;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashSet;
use std::sync::OnceLock;
use std::time::Duration;
use time::{Date, Month, OffsetDateTime, PrimitiveDateTime, Time, UtcOffset};

const STATION_LIST_URL: &str = "https://weather.uwyo.edu/wsgi/sounding_json";
const UWYO_STATIONS_JSON: &str = include_str!("../../../sharpmod/resources/uwyo_stations.json");
const MAX_LIST_BYTES: usize = 16 * 1024 * 1024;
const MAX_ID_CHARS: usize = 16;
const MAX_NAME_CHARS: usize = 200;
const MAX_SOURCE_CHARS: usize = 16;
const MISSING: f64 = -9999.0;
const MS_TO_KT: f64 = 1.943_844_492_440_604_6;
const HIGH_RES_LEVEL_THRESHOLD: usize = 400;
const THIN_MIN_DP_HPA: f64 = 2.0;
const MAX_ANALYSIS_LEVELS: usize = 300;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct StationRecord {
    pub id: String,
    pub name: String,
    pub lat: f64,
    pub lon: f64,
    pub src: String,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct AvailabilityPayload {
    pub observation_time: String,
    pub listed_ids: Vec<String>,
    pub extra_stations: Vec<StationRecord>,
    pub dropped_count: u32,
}

#[derive(Debug)]
pub enum AvailabilityError {
    Invalid(String),
    Unavailable(String),
}

impl std::fmt::Display for AvailabilityError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Invalid(msg) | Self::Unavailable(msg) => f.write_str(msg),
        }
    }
}

struct BundledCatalog {
    bundled_ids: HashSet<String>,
}

fn bundled_catalog() -> &'static BundledCatalog {
    static CATALOG: OnceLock<BundledCatalog> = OnceLock::new();
    CATALOG.get_or_init(|| {
        #[derive(Deserialize)]
        struct RawCatalog {
            stations: Vec<Value>,
        }
        let parsed: RawCatalog =
            serde_json::from_str(UWYO_STATIONS_JSON).expect("bundled uwyo_stations.json");
        let mut records = Vec::with_capacity(parsed.stations.len());
        for row in parsed.stations {
            let Some(obj) = row.as_object() else {
                continue;
            };
            let id = obj
                .get("id")
                .and_then(Value::as_str)
                .map(str::trim)
                .unwrap_or("")
                .to_owned();
            if id.is_empty() {
                continue;
            }
            let name = obj
                .get("name")
                .and_then(Value::as_str)
                .map(str::trim)
                .unwrap_or("")
                .to_owned();
            let lat = obj.get("lat").and_then(Value::as_f64).unwrap_or(f64::NAN);
            let lon = obj.get("lon").and_then(Value::as_f64).unwrap_or(f64::NAN);
            let src = obj
                .get("src")
                .and_then(Value::as_str)
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .unwrap_or("UNKNOWN")
                .to_owned();
            records.push(StationRecord {
                id,
                name,
                lat,
                lon,
                src,
            });
        }
        let (kept, _) = filter_records(records);
        let bundled_ids = kept.into_iter().map(|r| r.id).collect();
        BundledCatalog { bundled_ids }
    })
}

fn clean_name(raw: Option<&Value>) -> String {
    let text = match raw {
        Some(Value::String(s)) => s.as_str(),
        _ => return String::new(),
    };
    let collapsed = text.split_whitespace().collect::<Vec<_>>().join(" ");
    if collapsed.is_empty() {
        return String::new();
    }
    let low = collapsed.to_ascii_lowercase();
    if low.contains("dtype:") || low.contains("stationid") {
        return String::new();
    }
    collapsed
}

fn json_float(value: Option<&Value>) -> f64 {
    match value {
        Some(Value::Number(num)) => num.as_f64().unwrap_or(f64::NAN),
        Some(Value::String(s)) => s.trim().parse::<f64>().unwrap_or(f64::NAN),
        _ => f64::NAN,
    }
}

pub fn parse_station_list(raw: &[u8]) -> Result<Vec<StationRecord>, String> {
    let text = String::from_utf8_lossy(raw);
    let payload: Value = serde_json::from_str(&text)
        .map_err(|e| format!("UWyo station list was not valid JSON: {e}"))?;
    let rows = payload
        .as_object()
        .and_then(|obj| obj.get("stations"))
        .and_then(Value::as_array)
        .ok_or_else(|| {
            "UWyo station list response did not contain a 'stations' array".to_owned()
        })?;
    let mut out = Vec::new();
    let mut seen = HashSet::new();
    for row in rows {
        let Some(obj) = row.as_object() else {
            continue;
        };
        let raw_id = obj.get("stationid").or_else(|| obj.get("id"));
        let sid = match raw_id {
            Some(Value::String(s)) => s.trim().to_owned(),
            Some(Value::Number(n)) => n.to_string(),
            _ => String::new(),
        };
        if sid.is_empty() || !seen.insert(sid.clone()) {
            continue;
        }
        let lat = json_float(obj.get("lat"));
        let lon = json_float(obj.get("lon"));
        let name = clean_name(obj.get("name"));
        let src = match obj.get("src") {
            Some(Value::String(s)) if !s.trim().is_empty() => s.trim().to_owned(),
            _ => "UNKNOWN".to_owned(),
        };
        out.push(StationRecord {
            id: sid,
            name,
            lat,
            lon,
            src,
        });
    }
    if out.is_empty() {
        return Err("UWyo reported no stations for the requested time".into());
    }
    out.sort_by(|a, b| a.id.cmp(&b.id));
    Ok(out)
}

pub fn filter_records(records: Vec<StationRecord>) -> (Vec<StationRecord>, u32) {
    let mut kept = Vec::with_capacity(records.len());
    let mut seen = HashSet::new();
    let mut dropped: u32 = 0;
    for record in records {
        let sid = record.id.trim().to_owned();
        let sid_len = sid.chars().count();
        let name_len = record.name.chars().count();
        let src_len = record.src.chars().count();
        let valid = (1..=MAX_ID_CHARS).contains(&sid_len)
            && record.lat.is_finite()
            && (-90.0..=90.0).contains(&record.lat)
            && record.lon.is_finite()
            && (-180.0..=180.0).contains(&record.lon)
            && name_len <= MAX_NAME_CHARS
            && (1..=MAX_SOURCE_CHARS).contains(&src_len)
            && !seen.contains(&sid);
        if !valid {
            dropped = dropped.saturating_add(1);
            continue;
        }
        seen.insert(sid.clone());
        kept.push(StationRecord {
            id: sid,
            name: record.name,
            lat: record.lat,
            lon: record.lon,
            src: record.src,
        });
    }
    (kept, dropped)
}

pub fn split_availability(
    observation_time: OffsetDateTime,
    records: Vec<StationRecord>,
) -> AvailabilityPayload {
    let (stations, dropped_count) = filter_records(records);
    let bundled = &bundled_catalog().bundled_ids;
    let mut listed_ids = Vec::new();
    let mut extra_stations = Vec::new();
    for station in stations {
        if bundled.contains(&station.id) {
            listed_ids.push(station.id);
        } else {
            extra_stations.push(station);
        }
    }
    AvailabilityPayload {
        observation_time: format_rfc3339_utc(observation_time),
        listed_ids,
        extra_stations,
        dropped_count,
    }
}

pub struct AvailabilityClient {
    client: Result<Client, String>,
}

pub fn client() -> &'static AvailabilityClient {
    static CLIENT: OnceLock<AvailabilityClient> = OnceLock::new();
    CLIENT.get_or_init(AvailabilityClient::default)
}

impl Default for AvailabilityClient {
    fn default() -> Self {
        let _ = rustls::crypto::ring::default_provider().install_default();
        Self {
            client: Client::builder()
                .user_agent("SHARPpy-Reimagined")
                .connect_timeout(Duration::from_secs(8))
                .timeout(Duration::from_secs(30))
                .build()
                .map_err(|error| error.to_string()),
        }
    }
}

impl AvailabilityClient {
    pub async fn fetch(
        &self,
        observation_time: OffsetDateTime,
        now: OffsetDateTime,
    ) -> Result<AvailabilityPayload, AvailabilityError> {
        let utc = observation_time.to_offset(UtcOffset::UTC);
        if utc.minute() != 0 || utc.second() != 0 {
            return Err(AvailabilityError::Invalid(
                "observation_time must be on a whole hour".into(),
            ));
        }
        if utc > now + time::Duration::hours(1) {
            return Err(AvailabilityError::Invalid(
                "observation_time is in the future".into(),
            ));
        }
        let mut url = reqwest::Url::parse(STATION_LIST_URL).expect("static UWyo URL");
        let stamp = format!(
            "{:04}-{:02}-{:02} {:02}:00:00",
            utc.year(),
            u8::from(utc.month()),
            utc.day(),
            utc.hour()
        );
        url.query_pairs_mut().append_pair("datetime", &stamp);
        let response = self
            .client
            .as_ref()
            .map_err(|e| AvailabilityError::Unavailable(e.clone()))?
            .get(url)
            .send()
            .await
            .map_err(|e| {
                AvailabilityError::Unavailable(format!(
                    "UWyo station list could not be reached ({e})"
                ))
            })?
            .error_for_status()
            .map_err(|e| {
                AvailabilityError::Unavailable(format!(
                    "UWyo station list could not be reached ({e})"
                ))
            })?;
        let body = crate::http::body(response, MAX_LIST_BYTES)
            .await
            .map_err(AvailabilityError::Unavailable)?;
        let records = parse_station_list(&body).map_err(AvailabilityError::Unavailable)?;
        Ok(split_availability(utc, records))
    }
}

#[derive(Debug)]
pub enum DecodeError {
    Unavailable(String),
    Parse(String),
}

impl std::fmt::Display for DecodeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Unavailable(msg) | Self::Parse(msg) => f.write_str(msg),
        }
    }
}

#[derive(Debug)]
pub struct UwyoDecoded {
    pub pres: Vec<f64>,
    pub hght: Vec<f64>,
    pub tmpc: Vec<f64>,
    pub dwpc: Vec<f64>,
    pub wdir: Vec<f64>,
    pub wspd: Vec<f64>,
    pub valid_iso: Option<String>,
    pub loc: String,
    pub lat: f64,
    pub lon: f64,
}

pub fn decode_uwyo_text(text: &str) -> Result<UwyoDecoded, DecodeError> {
    if text.trim().is_empty() {
        return Err(DecodeError::Parse("UWyo response was empty".into()));
    }
    let lines: Vec<&str> = text.split('\n').collect();
    let Some(pre_idx) = lines.iter().position(|line| line.trim() == "<PRE>") else {
        if looks_unavailable(text) {
            return Err(DecodeError::Unavailable(
                "UWyo has no sounding for the requested station/time".into(),
            ));
        }
        return Err(DecodeError::Parse(
            "UWyo response did not contain a sounding data block".into(),
        ));
    };
    let header_end = (pre_idx + 5).min(lines.len());
    let header_block = lines[pre_idx + 1..header_end]
        .join("\n")
        .to_ascii_lowercase();
    let bgn = pre_idx + 5;
    let in_knots = header_block.contains("knot") || header_block.contains("sknt");
    let wspd_in_ms = !in_knots && (header_block.contains("m/s") || header_block.contains("sped"));

    let Some(rel_end) = lines.get(bgn..).and_then(|rest| {
        rest.iter()
            .position(|line| line.trim().starts_with("</PRE>"))
    }) else {
        return Err(DecodeError::Parse(
            "UWyo sounding data block was not terminated".into(),
        ));
    };
    let end = bgn + rel_end;
    if end <= bgn {
        return Err(DecodeError::Parse(
            "UWyo sounding data block was empty or malformed".into(),
        ));
    }

    let field_cols = [0_usize, 1, 2, 3, 6, 7];
    let mut cols: [Vec<f64>; 6] = Default::default();
    let mut n_levels = 0;
    for (offset, row) in lines[bgn..end].iter().enumerate() {
        if row.trim().is_empty() {
            continue;
        }
        let chars: Vec<char> = row.chars().collect();
        for (k, &col_idx) in field_cols.iter().enumerate() {
            let start = 7 * col_idx;
            let stop = 7 * (col_idx + 1);
            let val: String = if start < chars.len() {
                chars[start..stop.min(chars.len())].iter().collect()
            } else {
                String::new()
            };
            let trimmed = val.trim();
            if trimmed.is_empty() {
                cols[k].push(MISSING);
            } else {
                let num = trimmed.parse::<f64>().map_err(|error| {
                    DecodeError::Parse(format!(
                        "could not parse UWyo sounding level {offset}: {error}"
                    ))
                })?;
                cols[k].push(num);
            }
        }
        n_levels += 1;
    }
    if n_levels == 0 {
        return Err(DecodeError::Parse(
            "UWyo sounding contained no reported levels".into(),
        ));
    }

    if n_levels > HIGH_RES_LEVEL_THRESHOLD {
        let mut keep = pressure_spaced_indices(&cols[0], THIN_MIN_DP_HPA);
        if keep.len() > MAX_ANALYSIS_LEVELS {
            keep = subsample_indices(&keep, MAX_ANALYSIS_LEVELS);
        }
        if !keep.is_empty() && keep.len() < n_levels {
            for col in &mut cols {
                *col = keep.iter().map(|&idx| col[idx]).collect();
            }
        }
    }

    if wspd_in_ms {
        for v in &mut cols[5] {
            if *v != MISSING {
                *v *= MS_TO_KT;
            }
        }
    }

    let (valid_iso, loc, lat, lon) = parse_metadata(&lines);
    let [pres, hght, tmpc, dwpc, wdir, wspd] = cols;
    Ok(UwyoDecoded {
        pres,
        hght,
        tmpc,
        dwpc,
        wdir,
        wspd,
        valid_iso,
        loc,
        lat,
        lon,
    })
}

fn looks_unavailable(text: &str) -> bool {
    let low = text.to_ascii_lowercase();
    [
        "can't get",
        "cannot get",
        "no data",
        "sorry",
        "unable to",
        "not available",
        "invalid",
    ]
    .iter()
    .any(|marker| low.contains(marker))
}

fn pressure_spaced_indices(pres: &[f64], min_dp: f64) -> Vec<usize> {
    let mut keep = Vec::new();
    let mut last_p: Option<f64> = None;
    for (i, &p) in pres.iter().enumerate() {
        if !p.is_finite() {
            continue;
        }
        if last_p.is_none_or(|prev| (prev - p) >= min_dp) {
            keep.push(i);
            last_p = Some(p);
        }
    }
    let last_finite = pres.iter().rposition(|p| p.is_finite());
    if let Some(last_idx) = last_finite {
        if keep.last() != Some(&last_idx) && last_p.is_none_or(|prev| pres[last_idx] < prev) {
            keep.push(last_idx);
        }
    }
    keep
}

fn subsample_indices(indices: &[usize], max_count: usize) -> Vec<usize> {
    let n = indices.len();
    if n <= max_count || max_count < 2 {
        return indices.to_vec();
    }
    let mut chosen = Vec::with_capacity(max_count);
    for k in 0..max_count {
        let pos = (k as f64) * ((n - 1) as f64) / ((max_count - 1) as f64);
        chosen.push(pos.round() as usize);
    }
    chosen.sort_unstable();
    chosen.dedup();
    if chosen.first() != Some(&0) {
        chosen.insert(0, 0);
    }
    if chosen.last() != Some(&(n - 1)) {
        chosen.push(n - 1);
    }
    chosen.into_iter().map(|k| indices[k]).collect()
}

fn parse_month(text: &str) -> Option<Month> {
    match text.to_ascii_lowercase().as_str() {
        "jan" => Some(Month::January),
        "feb" => Some(Month::February),
        "mar" => Some(Month::March),
        "apr" => Some(Month::April),
        "may" => Some(Month::May),
        "jun" => Some(Month::June),
        "jul" => Some(Month::July),
        "aug" => Some(Month::August),
        "sep" => Some(Month::September),
        "oct" => Some(Month::October),
        "nov" => Some(Month::November),
        "dec" => Some(Month::December),
        _ => None,
    }
}

fn parse_h1_stamp(when: &str) -> Option<String> {
    let parts: Vec<&str> = when.split_whitespace().collect();
    if parts.len() != 5 || !parts[1].eq_ignore_ascii_case("utc") {
        return None;
    }
    let hour: u8 = parts[0].parse().ok()?;
    let day: u8 = parts[2].parse().ok()?;
    let month = parse_month(parts[3])?;
    let year: i32 = parts[4].parse().ok()?;
    let dt = PrimitiveDateTime::new(
        Date::from_calendar_date(year, month, day).ok()?,
        Time::from_hms(hour, 0, 0).ok()?,
    );
    Some(format!(
        "{:04}-{:02}-{:02}T{:02}:00:00",
        dt.year(),
        u8::from(dt.month()),
        dt.day(),
        dt.hour()
    ))
}

fn parse_h2_stamp(when: &str) -> Option<String> {
    let parts: Vec<&str> = when.split_whitespace().collect();
    if parts.len() != 4 {
        return None;
    }
    let hour_text = parts[0]
        .strip_suffix('Z')
        .or_else(|| parts[0].strip_suffix('z'))?;
    let hour: u8 = hour_text.parse().ok()?;
    let day: u8 = parts[1].parse().ok()?;
    let month = parse_month(parts[2])?;
    let year: i32 = parts[3].parse().ok()?;
    let dt = PrimitiveDateTime::new(
        Date::from_calendar_date(year, month, day).ok()?,
        Time::from_hms(hour, 0, 0).ok()?,
    );
    Some(format!(
        "{:04}-{:02}-{:02}T{:02}:00:00",
        dt.year(),
        u8::from(dt.month()),
        dt.day(),
        dt.hour()
    ))
}

fn parse_coord_after(haystack: &str, key: &str) -> Option<f64> {
    let idx = haystack.find(key)?;
    let rest = haystack[idx + key.len()..].trim_start();
    let token: String = rest
        .chars()
        .take_while(|c| c.is_ascii_digit() || *c == '.' || *c == '-' || *c == '+')
        .collect();
    token.parse::<f64>().ok()
}

fn parse_metadata(lines: &[&str]) -> (Option<String>, String, f64, f64) {
    let mut valid: Option<String> = None;
    let mut loc = String::new();
    let mut lat = f64::NAN;
    let mut lon = f64::NAN;
    for line in lines {
        let s = line.trim();
        let low = s.to_ascii_lowercase();
        if valid.is_none() && s.starts_with("<H1>") && s.contains(" UTC ") {
            if let Some(frag) = s[4..].split("</H1>").next() {
                if let Some((_, when)) = frag.split_once(" at ") {
                    valid = parse_h1_stamp(when.trim());
                }
            }
            continue;
        }
        if s.starts_with("<H2>") && s.ends_with("</H2>") {
            if loc.is_empty() {
                loc = s[4..]
                    .split("Observations")
                    .next()
                    .unwrap_or("")
                    .trim()
                    .to_owned();
            }
            if valid.is_none() {
                let inner = &s[4..s.len() - 5];
                let char_count = inner.chars().count();
                if char_count >= 15 {
                    let tail: String = inner.chars().skip(char_count - 15).collect();
                    valid = parse_h2_stamp(&tail);
                }
            }
            continue;
        }
        if loc.is_empty() && s.starts_with("<H3>") && s.ends_with("</H3>") {
            let name = s[4..s.len() - 5].trim();
            if !name.to_ascii_lowercase().contains("station information") {
                loc = name.to_owned();
            }
            continue;
        }
        if low.contains("latitude") && low.contains("longitude") && lat.is_nan() {
            if let (Some(parsed_lat), Some(parsed_lon)) = (
                parse_coord_after(&low, "latitude:"),
                parse_coord_after(&low, "longitude:"),
            ) {
                lat = parsed_lat;
                lon = parsed_lon;
            }
            continue;
        }
        if low.contains("station latitude") && lat.is_nan() {
            if let Some(val) = s
                .rsplit(':')
                .next()
                .and_then(|v| v.trim().parse::<f64>().ok())
            {
                lat = val;
            }
            continue;
        }
    }
    (valid, loc, lat, lon)
}

#[cfg(test)]
mod tests {
    use super::*;
    use time::format_description::well_known::Rfc3339;

    fn instant(value: &str) -> OffsetDateTime {
        OffsetDateTime::parse(value, &Rfc3339).unwrap()
    }

    #[test]
    fn parses_and_splits_uwyo_station_availability() {
        let payload = serde_json::json!({
            "stations": [
                {"stationid": "72357", "name": "OUN  Norman, OK", "lat": 35.18, "lon": -97.44, "src": "FM35"},
                {"stationid": "99901", "name": "Extra Site", "lat": 10.0, "lon": 20.0, "src": "BUFR"},
                {"stationid": "72451", "name": "Stationid\\n72451 Dtype: Str", "lat": 37.77, "lon": -99.97, "src": "FM35"},
                {"stationid": "BADLAT", "name": "Bad", "lat": 95.0, "lon": 0.0, "src": "FM35"}
            ]
        })
        .to_string();
        let parsed = parse_station_list(payload.as_bytes()).unwrap();
        assert_eq!(parsed.len(), 4);
        assert_eq!(parsed[0].name, "OUN Norman, OK");
        assert_eq!(parsed[1].name, "");
        let split = split_availability(instant("2026-10-04T12:00:00Z"), parsed);
        assert_eq!(split.listed_ids, vec!["72357", "72451"]);
        assert_eq!(split.extra_stations.len(), 1);
        assert_eq!(split.extra_stations[0].id, "99901");
        assert_eq!(split.dropped_count, 1);
    }

    #[test]
    fn decodes_uwyo_table_units_metadata_and_high_res_thinning() {
        let page = concat!(
            "<H1>Observations for Station 72558 at 00 UTC 16 Jun 2014</H1>\n",
            "<H3>OAX Omaha</H3>\n",
            "<I>Latitude: 41.32 Longitude: -96.37</I>\n",
            "<PRE>\n",
            "-----------------------------------------------------------------------------\n",
            "   PRES   HGHT   TEMP   DWPT   RELH   MIXR    DRCT   SPED   THTA   THTE   THTV\n",
            "    hPa     m      C      C      %    g/kg    deg    m/s     K      K      K\n",
            "-----------------------------------------------------------------------------\n",
            " 1000.0    110   24.0   20.0     78   14.8    160   10.0  297.2  340.5  299.8\n",
            "  500.0   5760  -12.0          40    2.1    270         290.1  300.0  290.5\n",
            "</PRE>\n"
        );
        let decoded = decode_uwyo_text(page).unwrap();
        assert_eq!(decoded.pres, vec![1000.0, 500.0]);
        assert_eq!(decoded.dwpc, vec![20.0, MISSING]);
        assert_eq!(decoded.wspd[1], MISSING);
        assert!((decoded.wspd[0] - 10.0 * MS_TO_KT).abs() < 1e-9);
        assert_eq!(decoded.valid_iso.as_deref(), Some("2014-06-16T00:00:00"));
        assert_eq!(decoded.loc, "OAX Omaha");
        assert_eq!(decoded.lat, 41.32);
        assert_eq!(decoded.lon, -96.37);

        assert!(matches!(
            decode_uwyo_text("<HTML><BODY>Sorry, unable to get data</BODY></HTML>"),
            Err(DecodeError::Unavailable(_))
        ));
        assert!(matches!(
            decode_uwyo_text("<HTML><BODY>Something random</BODY></HTML>"),
            Err(DecodeError::Parse(_))
        ));

        let mut big = String::from(
            "<PRE>\n-\nPRES HGHT TEMP DWPT RELH MIXR DRCT SKNT\n hPa m C C % g/kg deg knot\n-\n",
        );
        for i in 0..500 {
            let p = 1000.0 - (i as f64) * 1.5;
            big.push_str(&format!(
                "{p:7.1}{z:7.1}{t:7.1}{d:7.1}{r:7.1}{m:7.1}{dir:7.1}{spd:7.1}\n",
                z = 100.0 + (i as f64) * 10.0,
                t = 20.0,
                d = 10.0,
                r = 50.0,
                m = 5.0,
                dir = 180.0,
                spd = 15.0
            ));
        }
        big.push_str("</PRE>\n");
        let thinned = decode_uwyo_text(&big).unwrap();
        assert!(thinned.pres.len() <= MAX_ANALYSIS_LEVELS);
        assert_eq!(thinned.pres[0], 1000.0);
        assert!((thinned.pres.last().copied().unwrap() - (1000.0 - 499.0 * 1.5)).abs() < 0.2);
    }
}

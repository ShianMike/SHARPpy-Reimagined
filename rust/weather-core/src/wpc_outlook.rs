//! WPC Excessive Rainfall Outlook (ERO) Day 1-3 GeoJSON & discussion client.

use crate::format_rfc3339_utc;
use crate::outlook_discussion::{
    parse_wpc_ero_page, parse_wpc_qpferd, OutlookDiscussion, ParsedWpcDayDiscussion,
};
use crate::spc_outlook::{
    bounds_of, format_window_subtitle, rings_from_geometry, OutlookArea, OutlookLayer,
    MAX_POINTS_PER_LAYER, MAX_SHAPES_PER_LAYER,
};
use reqwest::{Client, StatusCode};
use serde_json::Value;
use std::collections::HashMap;
use std::sync::{Arc, Mutex, OnceLock, PoisonError};
use std::time::{Duration, Instant};
use time::{Date, Month, OffsetDateTime, PrimitiveDateTime, Time};

pub const ATTRIBUTION: &str = "NOAA/NWS Weather Prediction Center";
const MAPSERVER_BASE: &str =
    "https://mapservices.weather.noaa.gov/vector/rest/services/hazards/wpc_precip_hazards/MapServer";
const QPFERD_TXT_URL: &str = "https://tgftp.nws.noaa.gov/data/raw/fo/fous30.kwbc.qpf.erd.txt";
const QPFERD_HTML_URL: &str =
    "https://www.wpc.ncep.noaa.gov/discussions/hpcdiscussions.php?disc=qpferd";
const ERO_PREV_DAY1_URL: &str = "https://www.wpc.ncep.noaa.gov/qpf/ero.php?opt=prev&day=1";

const CACHE_TTL: Duration = Duration::from_secs(300);
const MAX_PAYLOAD_BYTES: usize = 8 * 1024 * 1024;
const PAST_HORIZON_HOURS: i64 = 36;
const FUTURE_HORIZON_HOURS: i64 = 96;

pub fn ero_page_url(day: u8, previous: bool) -> String {
    if previous {
        ERO_PREV_DAY1_URL.to_owned()
    } else {
        format!("https://www.wpc.ncep.noaa.gov/qpf/ero.php?opt=curr&day={day}")
    }
}

fn ero_geojson_url(day: u8) -> String {
    let layer_id = day.saturating_sub(1);
    format!("{MAPSERVER_BASE}/{layer_id}/query?where=1%3D1&outFields=*&f=geojson")
}

fn ero_category(
    dn: Option<i64>,
    outlook_text: &str,
) -> Option<(&'static str, &'static str, i64, &'static str, &'static str)> {
    let lower = outlook_text.to_ascii_lowercase();
    let tier = dn.or_else(|| {
        if lower.contains("high") {
            Some(4)
        } else if lower.contains("moderate") {
            Some(3)
        } else if lower.contains("slight") {
            Some(2)
        } else if lower.contains("marginal") {
            Some(1)
        } else {
            None
        }
    })?;
    match tier {
        1 => Some(("MRGL", "Marginal (At Least 5%)", 1, "#38A800", "#00734C")),
        2 => Some(("SLGT", "Slight (At Least 15%)", 2, "#FFFE00", "#E69800")),
        3 => Some(("MDT", "Moderate (At Least 40%)", 3, "#F50000", "#8A0000")),
        4 => Some(("HIGH", "High (At Least 70%)", 4, "#FF69C5", "#FF00FF")),
        _ => None,
    }
}

fn parse_sql_or_epoch_time(value: Option<&Value>) -> Option<OffsetDateTime> {
    let value = value?;
    if let Some(ms) = value.as_i64() {
        let secs = ms.div_euclid(1000);
        return OffsetDateTime::from_unix_timestamp(secs).ok();
    }
    let text = value.as_str()?.trim();
    if text.is_empty() {
        return None;
    }
    if let Ok(dt) = OffsetDateTime::parse(text, &time::format_description::well_known::Rfc3339) {
        return Some(dt);
    }
    // ArcGIS returns `YYYY-MM-DD HH:MM:SS` in UTC.
    let (date_part, time_part) = text.split_once(' ').or_else(|| text.split_once('T'))?;
    let mut d = date_part.split('-');
    let year: i32 = d.next()?.parse().ok()?;
    let month = Month::try_from(d.next()?.parse::<u8>().ok()?).ok()?;
    let day: u8 = d.next()?.parse().ok()?;
    let mut t = time_part.trim_end_matches('Z').split(':');
    let hour: u8 = t.next()?.parse().ok()?;
    let minute: u8 = t.next()?.parse().ok()?;
    let second: u8 = t.next().unwrap_or("0").parse().ok()?;
    let date = Date::from_calendar_date(year, month, day).ok()?;
    let time = Time::from_hms(hour, minute, second).ok()?;
    Some(PrimitiveDateTime::new(date, time).assume_utc())
}

pub fn parse_wpc_geojson(
    payload: &[u8],
    day: u8,
    source_url: &str,
) -> Result<Option<OutlookLayer>, String> {
    let text = std::str::from_utf8(payload)
        .map_err(|error| format!("WPC outlook payload is not UTF-8: {error}"))?;
    let document: Value = serde_json::from_str(text)
        .map_err(|error| format!("WPC outlook payload is not valid JSON: {error}"))?;
    let Some(obj) = document.as_object() else {
        return Err("WPC outlook payload is not a GeoJSON object".into());
    };
    let Some(features) = obj.get("features").and_then(Value::as_array) else {
        return Err("WPC outlook payload has no feature list".into());
    };
    if features.is_empty() {
        return Ok(None);
    }

    let mut areas: Vec<OutlookArea> = Vec::new();
    let mut total_points = 0usize;
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
            valid_from = valid_from.or_else(|| parse_sql_or_epoch_time(props.get("start_time")));
            valid_to = valid_to.or_else(|| parse_sql_or_epoch_time(props.get("end_time")));
            issued = issued.or_else(|| parse_sql_or_epoch_time(props.get("issue_time")));
        }
        let dn = props.and_then(|p| p.get("dn")).and_then(|v| {
            if v.is_boolean() {
                None
            } else {
                v.as_i64()
                    .or_else(|| v.as_str().and_then(|s| s.trim().parse::<i64>().ok()))
            }
        });
        let outlook_text = props
            .and_then(|p| p.get("outlook"))
            .and_then(Value::as_str)
            .map(str::trim)
            .unwrap_or("");
        let Some((code, default_desc, rank, fill, stroke)) = ero_category(dn, outlook_text) else {
            continue;
        };
        let description = if outlook_text.is_empty() {
            default_desc.to_owned()
        } else {
            outlook_text.to_owned()
        };

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
                stroke: stroke.to_owned(),
                fill: fill.to_owned(),
                label: code.to_owned(),
                description: description.clone(),
                rank,
                hatch: false,
                hatch_level: 0,
            });
        }
    }

    if !matches!((valid_from, valid_to), (Some(from), Some(to)) if from < to) {
        return Ok(None);
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

    Ok(Some(OutlookLayer::new(
        "wpc_ero".into(),
        format!("WPC Day {day} Excessive Rainfall Outlook"),
        format_window_subtitle(&format!("Day {day}"), valid_from, valid_to),
        "ERO".into(),
        valid_from.map(format_rfc3339_utc),
        valid_to.map(format_rfc3339_utc),
        issued.map(format_rfc3339_utc),
        source_url.to_owned(),
        ATTRIBUTION.into(),
        layer_bounds,
        areas,
        valid_from,
        valid_to,
    )))
}

fn empty_layer_from_discussion(day: u8, parsed: &ParsedWpcDayDiscussion) -> Option<OutlookLayer> {
    match (parsed.valid_from, parsed.valid_to) {
        (Some(vf), Some(vt)) if vf < vt => Some(OutlookLayer::new(
            "wpc_ero".into(),
            format!("WPC Day {day} Excessive Rainfall Outlook"),
            format_window_subtitle(&format!("Day {day}"), Some(vf), Some(vt)),
            "ERO".into(),
            Some(format_rfc3339_utc(vf)),
            Some(format_rfc3339_utc(vt)),
            parsed.issued.map(format_rfc3339_utc),
            parsed.discussion.source_url.clone(),
            ATTRIBUTION.into(),
            None,
            Vec::new(),
            Some(vf),
            Some(vt),
        )),
        _ => None,
    }
}

struct LayerEntry {
    expires_at: Instant,
    layer: Option<Arc<OutlookLayer>>,
}

struct DiscussionsEntry {
    expires_at: Instant,
    days: Arc<HashMap<u8, ParsedWpcDayDiscussion>>,
}

struct PrevEntry {
    expires_at: Instant,
    discussion: Option<ParsedWpcDayDiscussion>,
}

pub struct WpcOutlookClient {
    client: Result<Client, String>,
    layers: Mutex<HashMap<u8, LayerEntry>>,
    qpferd: Mutex<Option<DiscussionsEntry>>,
    prev_day1: Mutex<Option<PrevEntry>>,
}

pub fn client() -> &'static WpcOutlookClient {
    static CLIENT: OnceLock<WpcOutlookClient> = OnceLock::new();
    CLIENT.get_or_init(WpcOutlookClient::default)
}

impl Default for WpcOutlookClient {
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
            qpferd: Mutex::new(None),
            prev_day1: Mutex::new(None),
        }
    }
}

impl WpcOutlookClient {
    pub fn clear(&self) {
        self.layers
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clear();
        *self.qpferd.lock().unwrap_or_else(PoisonError::into_inner) = None;
        *self
            .prev_day1
            .lock()
            .unwrap_or_else(PoisonError::into_inner) = None;
    }

    async fn download_text(&self, url: &str) -> Result<Option<String>, String> {
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
        let bytes = crate::http::body(response, MAX_PAYLOAD_BYTES).await?;
        Ok(Some(String::from_utf8_lossy(&bytes).into_owned()))
    }

    async fn download_bytes(&self, url: &str) -> Result<Option<Vec<u8>>, String> {
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
        crate::http::body(response, MAX_PAYLOAD_BYTES)
            .await
            .map(Some)
    }

    async fn fetch_day_geojson(&self, day: u8) -> Result<Option<Arc<OutlookLayer>>, String> {
        let now = Instant::now();
        {
            let mut cache = self.layers.lock().unwrap_or_else(PoisonError::into_inner);
            if let Some(entry) = cache.get(&day) {
                if entry.expires_at >= now {
                    return Ok(entry.layer.clone());
                }
                cache.remove(&day);
            }
        }
        let url = ero_geojson_url(day);
        let page_url = ero_page_url(day, false);
        let Some(bytes) = self.download_bytes(&url).await? else {
            return Ok(None);
        };
        let layer = parse_wpc_geojson(&bytes, day, &page_url)?.map(Arc::new);
        let mut cache = self.layers.lock().unwrap_or_else(PoisonError::into_inner);
        cache.insert(
            day,
            LayerEntry {
                expires_at: Instant::now() + CACHE_TTL,
                layer: layer.clone(),
            },
        );
        Ok(layer)
    }

    async fn fetch_qpferd(&self) -> Option<Arc<HashMap<u8, ParsedWpcDayDiscussion>>> {
        let now = Instant::now();
        {
            let guard = self.qpferd.lock().unwrap_or_else(PoisonError::into_inner);
            if let Some(entry) = guard.as_ref() {
                if entry.expires_at >= now {
                    return Some(Arc::clone(&entry.days));
                }
            }
        }
        let mut parsed = match self.download_text(QPFERD_TXT_URL).await {
            Ok(Some(text)) => parse_wpc_qpferd(&text),
            _ => HashMap::new(),
        };
        if parsed.is_empty() {
            if let Ok(Some(html)) = self.download_text(QPFERD_HTML_URL).await {
                parsed = parse_wpc_qpferd(&html);
            }
        }
        if parsed.is_empty() {
            return None;
        }
        let days = Arc::new(parsed);
        *self.qpferd.lock().unwrap_or_else(PoisonError::into_inner) = Some(DiscussionsEntry {
            expires_at: Instant::now() + CACHE_TTL,
            days: Arc::clone(&days),
        });
        Some(days)
    }

    async fn fetch_prev_day1(&self) -> Option<ParsedWpcDayDiscussion> {
        let now = Instant::now();
        {
            let guard = self
                .prev_day1
                .lock()
                .unwrap_or_else(PoisonError::into_inner);
            if let Some(entry) = guard.as_ref() {
                if entry.expires_at >= now {
                    return entry.discussion.clone();
                }
            }
        }
        let parsed = match self.download_text(ERO_PREV_DAY1_URL).await {
            Ok(Some(html)) => parse_wpc_ero_page(&html, 1, ERO_PREV_DAY1_URL),
            _ => None,
        };
        *self
            .prev_day1
            .lock()
            .unwrap_or_else(PoisonError::into_inner) = Some(PrevEntry {
            expires_at: Instant::now() + CACHE_TTL,
            discussion: parsed.clone(),
        });
        parsed
    }

    async fn match_discussion_for_layer(
        &self,
        day: u8,
        layer: &OutlookLayer,
        qpferd: Option<&HashMap<u8, ParsedWpcDayDiscussion>>,
    ) -> Option<OutlookDiscussion> {
        let (Some(l_from), Some(l_to)) = (layer.valid_from_dt, layer.valid_to_dt) else {
            return None;
        };
        if let Some(map) = qpferd {
            for candidate_day in [day, 1, 2, 3] {
                if let Some(entry) = map.get(&candidate_day) {
                    if entry.valid_from == Some(l_from) && entry.valid_to == Some(l_to) {
                        return Some(entry.discussion.clone());
                    }
                }
            }
        }
        if day == 1 {
            if let Some(prev) = self.fetch_prev_day1().await {
                if (prev.valid_from == Some(l_from) && prev.valid_to == Some(l_to))
                    || prev.valid_to == Some(l_to)
                {
                    return Some(prev.discussion);
                }
            }
        }
        if let Some(map) = qpferd {
            for candidate_day in [day, 1, 2, 3] {
                if let Some(entry) = map.get(&candidate_day) {
                    if entry.valid_to == Some(l_to) {
                        return Some(entry.discussion.clone());
                    }
                }
            }
        }
        None
    }

    /// Fetch latest issuance + aligned discussion for Overview Day 1/2/3.
    pub async fn fetch_latest(
        &self,
        day: u8,
    ) -> Result<Option<(Arc<OutlookLayer>, Option<OutlookDiscussion>)>, String> {
        if !(1..=3).contains(&day) {
            return Ok(None);
        }
        let (layer_res, qpferd) = tokio::join!(self.fetch_day_geojson(day), self.fetch_qpferd());
        let layer = layer_res?;
        if let Some(mut layer) = layer {
            let discussion = self
                .match_discussion_for_layer(day, &layer, qpferd.as_deref())
                .await;
            if let Some(ref disc) = discussion {
                if disc.source_url != layer.source_url {
                    let mut updated = (*layer).clone();
                    updated.source_url = disc.source_url.clone();
                    layer = Arc::new(updated);
                }
            }
            return Ok(Some((layer, discussion)));
        }
        if let Some(entry) = qpferd.as_ref().and_then(|map| map.get(&day)) {
            if let Some(empty) = empty_layer_from_discussion(day, entry) {
                return Ok(Some((Arc::new(empty), Some(entry.discussion.clone()))));
            }
        }
        Ok(None)
    }

    /// Fetch the time-matched WPC ERO layer covering `valid_time` for the Map Viewer.
    pub async fn fetch(
        &self,
        valid_time: OffsetDateTime,
        now: OffsetDateTime,
    ) -> Result<Option<Arc<OutlookLayer>>, String> {
        if valid_time < now - time::Duration::hours(PAST_HORIZON_HOURS)
            || valid_time > now + time::Duration::hours(FUTURE_HORIZON_HOURS)
        {
            return Ok(None);
        }
        let (d1, d2, d3) = tokio::join!(
            self.fetch_day_geojson(1),
            self.fetch_day_geojson(2),
            self.fetch_day_geojson(3)
        );
        let layers = [d1?, d2?, d3?];
        for layer in layers.iter().flatten() {
            if layer.covers(valid_time) {
                return Ok(Some(Arc::clone(layer)));
            }
        }
        if let Some(qpferd) = self.fetch_qpferd().await {
            for day in [1u8, 2, 3] {
                if let Some(entry) = qpferd.get(&day) {
                    if let Some(empty) = empty_layer_from_discussion(day, entry) {
                        if empty.covers(valid_time) {
                            return Ok(Some(Arc::new(empty)));
                        }
                    }
                }
            }
        }
        Ok(layers.into_iter().flatten().next())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use time::format_description::well_known::Rfc3339;

    fn instant(value: &str) -> OffsetDateTime {
        OffsetDateTime::parse(value, &Rfc3339).unwrap()
    }

    #[test]
    fn parses_wpc_geojson_with_multiple_tiers_holes_and_empty_features() {
        let empty = br#"{"type":"FeatureCollection","features":[]}"#;
        assert!(parse_wpc_geojson(empty, 3, "https://example.com")
            .unwrap()
            .is_none());

        let payload = serde_json::json!({
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "MultiPolygon",
                        "coordinates": [[[[-78.0, 34.0], [-75.0, 34.0], [-75.0, 36.0], [-78.0, 36.0], [-78.0, 34.0]]]]
                    },
                    "properties": {
                        "dn": 2,
                        "outlook": "Slight (At Least 15%)",
                        "issue_time": "2026-11-25 00:50:00",
                        "start_time": "2026-11-25 12:00:00",
                        "end_time": "2026-11-26 12:00:00"
                    }
                },
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[-82.0, 30.0], [-73.0, 30.0], [-73.0, 38.0], [-82.0, 38.0], [-82.0, 30.0]],
                            [[-78.0, 34.0], [-75.0, 34.0], [-75.0, 36.0], [-78.0, 36.0], [-78.0, 34.0]]
                        ]
                    },
                    "properties": {
                        "dn": "1",
                        "outlook": "Marginal (At Least 5%)",
                        "issue_time": 1795567800000_i64,
                        "start_time": "2026-11-25 12:00:00",
                        "end_time": "2026-11-26 12:00:00"
                    }
                }
            ]
        })
        .to_string()
        .into_bytes();

        let layer = parse_wpc_geojson(
            &payload,
            1,
            "https://www.wpc.ncep.noaa.gov/qpf/ero.php?opt=curr&day=1",
        )
        .unwrap()
        .unwrap();
        assert_eq!(layer.title, "WPC Day 1 Excessive Rainfall Outlook");
        assert_eq!(layer.attribution, ATTRIBUTION);
        assert_eq!(layer.valid_from.as_deref(), Some("2026-11-25T12:00:00Z"));
        assert_eq!(layer.valid_to.as_deref(), Some("2026-11-26T12:00:00Z"));
        assert_eq!(layer.issued.as_deref(), Some("2026-11-25T00:50:00Z"));
        assert_eq!(layer.areas.len(), 2);
        assert_eq!(layer.areas[0].label, "MRGL");
        assert_eq!(layer.areas[0].fill, "#38A800");
        assert_eq!(layer.areas[0].stroke, "#00734C");
        assert_eq!(layer.areas[0].rings.len(), 2);
        assert_eq!(layer.areas[1].label, "SLGT");
        assert_eq!(layer.areas[1].fill, "#FFFE00");
        assert!(layer.covers(instant("2026-11-25T18:00:00Z")));
        assert!(!layer.covers(instant("2026-11-26T12:00:00Z")));
    }

    #[tokio::test]
    async fn out_of_horizon_fetch_short_circuits_without_network() {
        let client = WpcOutlookClient {
            client: Err("network disabled in test".into()),
            layers: Mutex::default(),
            qpferd: Mutex::default(),
            prev_day1: Mutex::default(),
        };
        let now = instant("2026-11-25T12:00:00Z");
        assert!(client
            .fetch(instant("1998-05-20T18:00:00Z"), now)
            .await
            .unwrap()
            .is_none());
        assert!(client
            .fetch(instant("2026-12-10T12:00:00Z"), now)
            .await
            .unwrap()
            .is_none());
    }

    #[tokio::test]
    async fn aligns_overnight_day1_layer_with_prev_discussion_or_qpferd_window() {
        let client = WpcOutlookClient {
            client: Err("network disabled in test".into()),
            layers: Mutex::default(),
            qpferd: Mutex::default(),
            prev_day1: Mutex::default(),
        };
        let layer = Arc::new(
            parse_wpc_geojson(
                br#"{"type":"FeatureCollection","features":[{"type":"Feature","geometry":{"type":"Polygon","coordinates":[[[-81.0,25.0],[-80.0,25.0],[-80.0,26.0],[-81.0,26.0],[-81.0,25.0]]]},"properties":{"dn":1,"outlook":"Marginal (At Least 5%)","issue_time":"2026-11-25 00:50:00","start_time":"2026-11-25 01:00:00","end_time":"2026-11-25 12:00:00"}}]}"#,
                1,
                "https://www.wpc.ncep.noaa.gov/qpf/ero.php?opt=curr&day=1",
            )
            .unwrap()
            .unwrap(),
        );
        client.layers.lock().unwrap().insert(
            1,
            LayerEntry {
                expires_at: Instant::now() + CACHE_TTL,
                layer: Some(layer),
            },
        );
        let qpferd = parse_wpc_qpferd(
            "236 AM EST Wed Nov 25 2026\nDay 1\nValid 12Z Wed Nov 25 2026 - 12Z Thu Nov 26 2026\n...THERE IS A SLIGHT RISK OF EXCESSIVE RAINFALL...\nFuture day 1 cycle.",
        );
        *client.qpferd.lock().unwrap() = Some(DiscussionsEntry {
            expires_at: Instant::now() + CACHE_TTL,
            days: Arc::new(qpferd),
        });
        let prev = parse_wpc_ero_page(
            "754 PM EST Tue Nov 24 2026\nDay 1\nValid 01Z Wed Nov 25 2026 - 12Z Wed Nov 25 2026\n...THERE IS A MARGINAL RISK OF EXCESSIVE RAINFALL...\nActive overnight flood threat.",
            1,
            ERO_PREV_DAY1_URL,
        );
        *client.prev_day1.lock().unwrap() = Some(PrevEntry {
            expires_at: Instant::now() + CACHE_TTL,
            discussion: prev,
        });

        let (got_layer, got_disc) = client.fetch_latest(1).await.unwrap().unwrap();
        assert_eq!(got_layer.source_url, ERO_PREV_DAY1_URL);
        assert_eq!(got_disc.unwrap().summary, "Active overnight flood threat.");
    }

    #[tokio::test]
    async fn empty_geojson_day3_builds_valid_quiet_layer_with_discussion() {
        let client = WpcOutlookClient {
            client: Err("network disabled in test".into()),
            layers: Mutex::default(),
            qpferd: Mutex::default(),
            prev_day1: Mutex::default(),
        };
        client.layers.lock().unwrap().insert(
            3,
            LayerEntry {
                expires_at: Instant::now() + CACHE_TTL,
                layer: None,
            },
        );
        let qpferd = parse_wpc_qpferd(
            "236 AM EST Wed Nov 25 2026\nDay 3\nValid 12Z Fri Nov 27 2026 - 12Z Sat Nov 28 2026\nThe probability of rainfall exceeding flash flood guidance is less than 5 percent.\n\nNo heavy rain expected.",
        );
        *client.qpferd.lock().unwrap() = Some(DiscussionsEntry {
            expires_at: Instant::now() + CACHE_TTL,
            days: Arc::new(qpferd),
        });

        let (got_layer, got_disc) = client.fetch_latest(3).await.unwrap().unwrap();
        assert!(got_layer.areas.is_empty());
        assert_eq!(
            got_layer.valid_from.as_deref(),
            Some("2026-11-27T12:00:00Z")
        );
        assert_eq!(got_disc.unwrap().summary, "No heavy rain expected.");
    }
}

//! WPC Winter Weather Outlook (WSSI Day 1-3) GeoJSON & QPFHSD discussion client.

use crate::format_rfc3339_utc;
use crate::outlook_discussion::{parse_wpc_qpfhsd, OutlookDiscussion};
use crate::spc_outlook::{
    bounds_of, format_window_subtitle, rings_from_geometry, OutlookArea, OutlookLayer,
    MAX_POINTS_PER_LAYER,
};
use reqwest::{Client, StatusCode};
use serde_json::Value;
use std::collections::{BTreeMap, HashMap};
use std::sync::{Arc, Mutex, OnceLock, PoisonError};
use std::time::{Duration, Instant};
use time::{Date, Month, OffsetDateTime, PrimitiveDateTime, Time};

pub const ATTRIBUTION: &str = "NOAA/NWS Weather Prediction Center";
pub const WSSI_PAGE_URL: &str = "https://www.wpc.ncep.noaa.gov/wwd/wssi/wssi.php";
const MAPSERVER_BASE: &str =
    "https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/wpc_wssi/MapServer";
const QPFHSD_TXT_URL: &str = "https://tgftp.nws.noaa.gov/data/raw/fo/fous11.kwbc.qpf.hsd.txt";
const QPFHSD_HTML_URL: &str =
    "https://www.wpc.ncep.noaa.gov/discussions/hpcdiscussions.php?disc=qpfhsd";

const CACHE_TTL: Duration = Duration::from_secs(300);
const MAX_PAYLOAD_BYTES: usize = 12 * 1024 * 1024;
/// Stay comfortably under src-tauri's MAX_AREAS = 256 DTO cap.
const MAX_WSSI_SHAPES: usize = 200;
const RESERVE_PER_TIER: usize = 8;
const PAST_HORIZON_HOURS: i64 = 36;
const FUTURE_HORIZON_HOURS: i64 = 96;

pub const WINTER_PRODUCTS: &[(&str, &str, [u8; 3])] = &[
    ("winter-overall", "Overall impact", [1, 2, 3]),
    ("winter-snow", "Snow amount", [6, 7, 8]),
    ("winter-snow-load", "Snow load", [11, 12, 13]),
    ("winter-ice", "Ice accumulation", [16, 17, 18]),
    ("winter-blowing-snow", "Blowing snow", [24, 25, 26]),
];

pub fn winter_product_spec(product: &str) -> Option<(&'static str, &'static str, [u8; 3])> {
    WINTER_PRODUCTS
        .iter()
        .copied()
        .find(|(key, _, _)| *key == product)
}

fn wssi_geojson_url(layer_id: u8) -> String {
    format!(
        "{MAPSERVER_BASE}/{layer_id}/query?where=1%3D1&outFields=impact,valid_time,issue_time,start_time,end_time,component&geometryPrecision=4&maxAllowableOffset=0.03&f=geojson"
    )
}

fn wssi_category(
    impact: &str,
) -> Option<(&'static str, &'static str, i64, &'static str, &'static str)> {
    match impact.trim().to_ascii_uppercase().as_str() {
        "WINTER WEATHER AREA" | "WWA" => {
            Some(("WWA", "Winter Weather Area", 1, "#D2DFE7", "#7B95A8"))
        }
        "MINOR" => Some(("MINOR", "Minor Impacts", 2, "#FAF5A3", "#C9B428")),
        "MODERATE" => Some(("MODERATE", "Moderate Impacts", 3, "#F7962F", "#B85A00")),
        "MAJOR" => Some(("MAJOR", "Major Impacts", 4, "#E61F26", "#8F0E14")),
        "EXTREME" => Some(("EXTREME", "Extreme Impacts", 5, "#7853A1", "#4A2B6E")),
        _ => None,
    }
}

/// Parse WSSI endpoint strings such as `"00Z 11/25/26"`.
pub(crate) fn parse_wssi_endpoint(raw: &str) -> Option<OffsetDateTime> {
    let mut tokens = raw.split_whitespace();
    let z_part = tokens.next()?.trim_end_matches(['Z', 'z']);
    let (hour, minute): (u8, u8) = match z_part.len() {
        1 | 2 => (z_part.parse().ok()?, 0),
        4 => (z_part[..2].parse().ok()?, z_part[2..].parse().ok()?),
        _ => return None,
    };
    let date_part = tokens.next()?;
    let mut parts = date_part.split('/');
    let month = Month::try_from(parts.next()?.parse::<u8>().ok()?).ok()?;
    let day: u8 = parts.next()?.parse().ok()?;
    let year_raw: i32 = parts.next()?.parse().ok()?;
    let year = if year_raw < 100 {
        2000 + year_raw
    } else {
        year_raw
    };
    let date = Date::from_calendar_date(year, month, day).ok()?;
    let time = Time::from_hms(hour, minute, 0).ok()?;
    Some(OffsetDateTime::new_utc(date, time))
}

/// Parse WSSI `issue_time` such as `"2026-11-25 0515Z"`.
pub(crate) fn parse_wssi_issue_time(raw: &str) -> Option<OffsetDateTime> {
    let trimmed = raw.trim();
    if let Ok(dt) = OffsetDateTime::parse(trimmed, &time::format_description::well_known::Rfc3339) {
        return Some(dt);
    }
    let (date_part, time_part) = trimmed
        .split_once(' ')
        .or_else(|| trimmed.split_once('T'))?;
    let mut d = date_part.split('-');
    let year: i32 = d.next()?.parse().ok()?;
    let month = Month::try_from(d.next()?.parse::<u8>().ok()?).ok()?;
    let day: u8 = d.next()?.parse().ok()?;
    let clean_t = time_part.trim_end_matches(['Z', 'z']);
    let (hour, minute, second) = if let Some((h, m)) = clean_t.split_once(':') {
        let m_only = m.split(':').next().unwrap_or(m);
        (h.parse().ok()?, m_only.parse().ok()?, 0)
    } else if clean_t.len() == 4 {
        (clean_t[..2].parse().ok()?, clean_t[2..].parse().ok()?, 0)
    } else {
        return None;
    };
    let date = Date::from_calendar_date(year, month, day).ok()?;
    let time = Time::from_hms(hour, minute, second).ok()?;
    Some(PrimitiveDateTime::new(date, time).assume_utc())
}

fn outer_ring_area(rings: &[Vec<[f64; 2]>]) -> f64 {
    let Some(ring) = rings.first() else {
        return 0.0;
    };
    if ring.len() < 3 {
        return 0.0;
    }
    let mut sum = 0.0;
    for i in 0..ring.len() {
        let [x1, y1] = ring[i];
        let [x2, y2] = ring[(i + 1) % ring.len()];
        sum += x1 * y2 - x2 * y1;
    }
    (sum * 0.5).abs()
}

pub fn parse_wssi_geojson(
    payload: &[u8],
    day: u8,
    product_label: &str,
) -> Result<Option<OutlookLayer>, String> {
    let text = std::str::from_utf8(payload)
        .map_err(|error| format!("WPC WSSI payload is not UTF-8: {error}"))?;
    let document: Value = serde_json::from_str(text)
        .map_err(|error| format!("WPC WSSI payload is not valid JSON: {error}"))?;
    let Some(obj) = document.as_object() else {
        return Err("WPC WSSI payload is not a GeoJSON object".into());
    };
    let Some(features) = obj.get("features").and_then(Value::as_array) else {
        return Err("WPC WSSI payload has no feature list".into());
    };
    if features.is_empty() {
        return Ok(None);
    }

    let mut valid_from: Option<OffsetDateTime> = None;
    let mut valid_to: Option<OffsetDateTime> = None;
    let mut issued: Option<OffsetDateTime> = None;
    let mut by_tier: BTreeMap<i64, Vec<(f64, OutlookArea)>> = BTreeMap::new();

    for feature in features {
        let Some(feature_obj) = feature.as_object() else {
            continue;
        };
        let props = feature_obj.get("properties").and_then(Value::as_object);
        if let Some(props) = props {
            if valid_from.is_none() {
                valid_from = props
                    .get("start_time")
                    .and_then(Value::as_str)
                    .and_then(parse_wssi_endpoint);
            }
            if valid_to.is_none() {
                valid_to = props
                    .get("end_time")
                    .and_then(Value::as_str)
                    .and_then(parse_wssi_endpoint);
            }
            if (valid_from.is_none() || valid_to.is_none()) && props.get("valid_time").is_some() {
                if let Some((left, right)) = props
                    .get("valid_time")
                    .and_then(Value::as_str)
                    .and_then(|s| s.split_once(" - "))
                {
                    valid_from = valid_from.or_else(|| parse_wssi_endpoint(left));
                    valid_to = valid_to.or_else(|| parse_wssi_endpoint(right));
                }
            }
            if issued.is_none() {
                issued = props
                    .get("issue_time")
                    .and_then(Value::as_str)
                    .and_then(parse_wssi_issue_time);
            }
        }
        let impact_raw = props
            .and_then(|p| p.get("impact"))
            .and_then(Value::as_str)
            .unwrap_or("");
        let Some((code, desc, rank, fill, stroke)) = wssi_category(impact_raw) else {
            continue;
        };
        for rings in rings_from_geometry(feature_obj.get("geometry")) {
            let Some(bounds) = bounds_of(&rings) else {
                continue;
            };
            let area_deg2 = outer_ring_area(&rings);
            by_tier.entry(rank).or_default().push((
                area_deg2,
                OutlookArea {
                    rings,
                    bounds,
                    stroke: stroke.to_owned(),
                    fill: fill.to_owned(),
                    label: code.to_owned(),
                    description: desc.to_owned(),
                    rank,
                    hatch: false,
                    hatch_level: 0,
                },
            ));
        }
    }

    if !matches!((valid_from, valid_to), (Some(from), Some(to)) if from < to) {
        return Ok(None);
    }

    // Reserve largest polygons per tier so every active impact level remains represented,
    // then fill remaining polygon budget highest-severity first.
    let mut prioritized: Vec<(i64, f64, OutlookArea)> = Vec::new();
    let mut overflow: Vec<(i64, f64, OutlookArea)> = Vec::new();
    for (rank, mut items) in by_tier {
        items.sort_by(|a, b| b.0.total_cmp(&a.0));
        for (idx, (score, area)) in items.into_iter().enumerate() {
            if idx < RESERVE_PER_TIER {
                prioritized.push((rank, score, area));
            } else {
                overflow.push((rank, score, area));
            }
        }
    }
    prioritized.sort_by(|a, b| b.0.cmp(&a.0).then_with(|| b.1.total_cmp(&a.1)));
    overflow.sort_by(|a, b| b.0.cmp(&a.0).then_with(|| b.1.total_cmp(&a.1)));

    let mut selected = Vec::with_capacity(MAX_WSSI_SHAPES);
    let mut total_points = 0usize;
    for (_, _, area) in prioritized.into_iter().chain(overflow) {
        if selected.len() >= MAX_WSSI_SHAPES {
            break;
        }
        let pts: usize = area.rings.iter().map(Vec::len).sum();
        if total_points + pts > MAX_POINTS_PER_LAYER {
            continue;
        }
        total_points += pts;
        selected.push(area);
    }
    selected.sort_by_key(|area| area.rank);

    let bounds = if selected.is_empty() {
        None
    } else {
        Some(selected.iter().fold(
            [
                f64::INFINITY,
                f64::NEG_INFINITY,
                f64::INFINITY,
                f64::NEG_INFINITY,
            ],
            |acc, a| {
                [
                    acc[0].min(a.bounds[0]),
                    acc[1].max(a.bounds[1]),
                    acc[2].min(a.bounds[2]),
                    acc[3].max(a.bounds[3]),
                ]
            },
        ))
    };

    Ok(Some(OutlookLayer::new(
        "wpc_winter".into(),
        format!("WPC Day {day} Winter Weather Outlook — {product_label}"),
        format_window_subtitle(
            &format!("Day {day} · {product_label}"),
            valid_from,
            valid_to,
        ),
        "WSSI".into(),
        valid_from.map(format_rfc3339_utc),
        valid_to.map(format_rfc3339_utc),
        issued.map(format_rfc3339_utc),
        WSSI_PAGE_URL.into(),
        ATTRIBUTION.into(),
        bounds,
        selected,
        valid_from,
        valid_to,
    )))
}

fn empty_winter_layer(
    day: u8,
    product_label: &str,
    valid_from: OffsetDateTime,
    valid_to: OffsetDateTime,
    issued: Option<String>,
) -> OutlookLayer {
    OutlookLayer::new(
        "wpc_winter".into(),
        format!("WPC Day {day} Winter Weather Outlook — {product_label}"),
        format_window_subtitle(
            &format!("Day {day} · {product_label}"),
            Some(valid_from),
            Some(valid_to),
        ),
        "WSSI".into(),
        Some(format_rfc3339_utc(valid_from)),
        Some(format_rfc3339_utc(valid_to)),
        issued,
        WSSI_PAGE_URL.into(),
        ATTRIBUTION.into(),
        None,
        Vec::new(),
        Some(valid_from),
        Some(valid_to),
    )
}

fn default_day_window(anchor: Option<OffsetDateTime>, day: u8) -> (OffsetDateTime, OffsetDateTime) {
    let base_date = anchor.unwrap_or_else(OffsetDateTime::now_utc).date();
    let start_date = base_date + time::Duration::days(i64::from(day.saturating_sub(1)));
    let start = PrimitiveDateTime::new(start_date, Time::MIDNIGHT).assume_utc();
    (start, start + time::Duration::hours(24))
}

struct LayerEntry {
    expires_at: Instant,
    layer: Option<Arc<OutlookLayer>>,
}

struct QpfhsdEntry {
    expires_at: Instant,
    raw: Arc<String>,
}

pub struct WpcWinterOutlookClient {
    client: Result<Client, String>,
    layers: Mutex<HashMap<(String, u8), LayerEntry>>,
    qpfhsd: Mutex<Option<QpfhsdEntry>>,
}

pub fn client() -> &'static WpcWinterOutlookClient {
    static CLIENT: OnceLock<WpcWinterOutlookClient> = OnceLock::new();
    CLIENT.get_or_init(WpcWinterOutlookClient::default)
}

impl Default for WpcWinterOutlookClient {
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
            qpfhsd: Mutex::new(None),
        }
    }
}

impl WpcWinterOutlookClient {
    pub fn clear(&self) {
        self.layers
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clear();
        *self.qpfhsd.lock().unwrap_or_else(PoisonError::into_inner) = None;
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

    async fn fetch_qpfhsd_raw(&self) -> Option<Arc<String>> {
        let now = Instant::now();
        {
            let guard = self.qpfhsd.lock().unwrap_or_else(PoisonError::into_inner);
            if let Some(entry) = guard.as_ref() {
                if entry.expires_at >= now {
                    return Some(Arc::clone(&entry.raw));
                }
            }
        }
        let mut raw = match self.download_text(QPFHSD_TXT_URL).await {
            Ok(Some(text)) if parse_wpc_qpfhsd(&text, None, WSSI_PAGE_URL).is_some() => Some(text),
            _ => None,
        };
        if raw.is_none() {
            if let Ok(Some(html)) = self.download_text(QPFHSD_HTML_URL).await {
                if parse_wpc_qpfhsd(&html, None, WSSI_PAGE_URL).is_some() {
                    raw = Some(html);
                }
            }
        }
        let raw = Arc::new(raw?);
        *self.qpfhsd.lock().unwrap_or_else(PoisonError::into_inner) = Some(QpfhsdEntry {
            expires_at: Instant::now() + CACHE_TTL,
            raw: Arc::clone(&raw),
        });
        Some(raw)
    }

    async fn fetch_day_geojson(
        &self,
        day: u8,
        product: &str,
    ) -> Result<Option<Arc<OutlookLayer>>, String> {
        let Some((_, label, layer_ids)) = winter_product_spec(product) else {
            return Err(format!("unknown WPC winter product: {product}"));
        };
        if !(1..=3).contains(&day) {
            return Ok(None);
        }
        let cache_key = (product.to_owned(), day);
        let now = Instant::now();
        {
            let mut cache = self.layers.lock().unwrap_or_else(PoisonError::into_inner);
            if let Some(entry) = cache.get(&cache_key) {
                if entry.expires_at >= now {
                    return Ok(entry.layer.clone());
                }
                cache.remove(&cache_key);
            }
        }
        let layer_id = layer_ids[(day - 1) as usize];
        let Some(bytes) = self.download_bytes(&wssi_geojson_url(layer_id)).await? else {
            return Ok(None);
        };
        let layer = parse_wssi_geojson(&bytes, day, label)?.map(Arc::new);
        let mut cache = self.layers.lock().unwrap_or_else(PoisonError::into_inner);
        cache.insert(
            cache_key,
            LayerEntry {
                expires_at: Instant::now() + CACHE_TTL,
                layer: layer.clone(),
            },
        );
        Ok(layer)
    }

    /// Fetch latest WSSI day layer + aligned QPFHSD discussion for Overview Day 1/2/3.
    pub async fn fetch_latest(
        &self,
        day: u8,
        product: &str,
    ) -> Result<Option<(Arc<OutlookLayer>, Option<OutlookDiscussion>)>, String> {
        let Some((_, label, _)) = winter_product_spec(product) else {
            return Err(format!("unknown WPC winter product: {product}"));
        };
        if !(1..=3).contains(&day) {
            return Ok(None);
        }
        let (layer_res, qpfhsd_raw) = tokio::join!(
            self.fetch_day_geojson(day, product),
            self.fetch_qpfhsd_raw()
        );
        let layer = layer_res?;
        let parsed_disc = qpfhsd_raw
            .as_deref()
            .and_then(|raw| parse_wpc_qpfhsd(raw, Some(day), WSSI_PAGE_URL));
        let (anchor, discussion) = match parsed_disc {
            Some((vf, _, disc)) => (vf, Some(disc)),
            None => (None, None),
        };
        if let Some(layer) = layer {
            return Ok(Some((layer, discussion)));
        }
        let (vf, vt) = default_day_window(anchor, day);
        let issued = discussion.as_ref().and_then(|d| d.issued.clone());
        Ok(Some((
            Arc::new(empty_winter_layer(day, label, vf, vt, issued)),
            discussion,
        )))
    }

    /// Fetch the time-matched WSSI layer covering `valid_time` for Map Weather Layers.
    pub async fn fetch(
        &self,
        valid_time: OffsetDateTime,
        now: OffsetDateTime,
        product: &str,
    ) -> Result<Option<Arc<OutlookLayer>>, String> {
        if winter_product_spec(product).is_none() {
            return Err(format!("unknown WPC winter product: {product}"));
        }
        if valid_time < now - time::Duration::hours(PAST_HORIZON_HOURS)
            || valid_time > now + time::Duration::hours(FUTURE_HORIZON_HOURS)
        {
            return Ok(None);
        }
        let (d1, d2, d3) = tokio::join!(
            self.fetch_day_geojson(1, product),
            self.fetch_day_geojson(2, product),
            self.fetch_day_geojson(3, product)
        );
        let layers = [d1?, d2?, d3?];
        for layer in layers.iter().flatten() {
            if layer.covers(valid_time) {
                return Ok(Some(Arc::clone(layer)));
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
    fn parses_wssi_timestamps_and_empty_geojson() {
        assert_eq!(
            parse_wssi_endpoint("00Z 11/25/26")
                .map(format_rfc3339_utc)
                .as_deref(),
            Some("2026-11-25T00:00:00Z")
        );
        assert_eq!(
            parse_wssi_issue_time("2026-11-25 0515Z")
                .map(format_rfc3339_utc)
                .as_deref(),
            Some("2026-11-25T05:15:00Z")
        );
        let empty = br#"{"type":"FeatureCollection","features":[]}"#;
        assert!(parse_wssi_geojson(empty, 1, "Overall impact")
            .unwrap()
            .is_none());
    }

    #[test]
    fn caps_fragmented_wssi_polygons_while_preserving_highest_tiers() {
        let mut features = Vec::new();
        let make_poly = |idx: usize| {
            let x = -110.0 + (idx % 20) as f64;
            let y = 35.0 + (idx / 20) as f64 * 0.5;
            vec![vec![
                [x, y],
                [x + 0.2, y],
                [x + 0.2, y + 0.2],
                [x, y + 0.2],
                [x, y],
            ]]
        };
        let wwa_polys: Vec<_> = (0..250).map(make_poly).collect();
        features.push(serde_json::json!({
            "type": "Feature",
            "properties": {
                "impact": "WINTER WEATHER AREA",
                "issue_time": "2026-11-25 0515Z",
                "start_time": "00Z 11/25/26",
                "end_time": "00Z 11/26/26"
            },
            "geometry": { "type": "MultiPolygon", "coordinates": wwa_polys }
        }));
        features.push(serde_json::json!({
            "type": "Feature",
            "properties": {
                "impact": "MAJOR",
                "issue_time": "2026-11-25 0515Z",
                "start_time": "00Z 11/25/26",
                "end_time": "00Z 11/26/26"
            },
            "geometry": { "type": "Polygon", "coordinates": make_poly(0) }
        }));
        let fc = serde_json::json!({ "type": "FeatureCollection", "features": features });
        let layer = parse_wssi_geojson(fc.to_string().as_bytes(), 1, "Overall impact")
            .unwrap()
            .unwrap();
        assert_eq!(layer.areas.len(), MAX_WSSI_SHAPES);
        assert!(layer.areas.iter().any(|a| a.label == "MAJOR"));
        assert!(layer.areas.iter().any(|a| a.label == "WWA"));
        assert_eq!(layer.areas.last().map(|a| a.label.as_str()), Some("MAJOR"));
        assert_eq!(layer.valid_from.as_deref(), Some("2026-11-25T00:00:00Z"));
        assert_eq!(layer.valid_to.as_deref(), Some("2026-11-26T00:00:00Z"));
    }

    #[test]
    fn out_of_horizon_returns_none_without_network() {
        let client = WpcWinterOutlookClient::default();
        let rt = tokio::runtime::Builder::new_current_thread()
            .build()
            .unwrap();
        let res = rt
            .block_on(client.fetch(
                instant("1998-05-20T18:00:00Z"),
                instant("2026-11-25T12:00:00Z"),
                "winter-overall",
            ))
            .unwrap();
        assert!(res.is_none());
    }
}

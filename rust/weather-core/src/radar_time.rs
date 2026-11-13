//! NOAA WMS GetCapabilities XML parsing and historical scan selection.

use std::collections::{HashMap, VecDeque};
use std::sync::{Mutex, OnceLock, PoisonError};
use std::time::{Duration, Instant};
use time::{format_description::well_known::Rfc3339, OffsetDateTime, UtcOffset};

const HISTORY: time::Duration = time::Duration::minutes(60);
const TOLERANCE: time::Duration = time::Duration::minutes(10);
const CAPABILITIES_TTL: Duration = Duration::from_secs(45);
const MAX_CAPABILITIES_BYTES: usize = 2 * 1024 * 1024;
const CACHE_LIMIT: usize = 24;

#[derive(Debug)]
pub enum TimeError {
    Missing(String),
    Other(String),
}

impl std::fmt::Display for TimeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Missing(msg) | Self::Other(msg) => f.write_str(msg),
        }
    }
}

struct CapEntry {
    endpoint: String,
    expires_at: Instant,
    layers: HashMap<String, Vec<OffsetDateTime>>,
}

fn cap_cache() -> &'static Mutex<VecDeque<CapEntry>> {
    static CACHE: OnceLock<Mutex<VecDeque<CapEntry>>> = OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(VecDeque::new()))
}

pub fn clear_cache() {
    cap_cache()
        .lock()
        .unwrap_or_else(PoisonError::into_inner)
        .clear();
}

pub fn stamp(value: OffsetDateTime) -> String {
    let utc = value.to_offset(UtcOffset::UTC);
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{:03}Z",
        utc.year(),
        u8::from(utc.month()),
        utc.day(),
        utc.hour(),
        utc.minute(),
        utc.second(),
        utc.millisecond()
    )
}

fn local_name(tag: &str) -> &str {
    tag.rsplit(':').next().unwrap_or(tag)
}

fn attr_value<'a>(attrs: &'a str, key: &str) -> Option<&'a str> {
    let mut rest = attrs;
    while let Some(eq) = rest.find('=') {
        let name = rest[..eq].split_whitespace().last().unwrap_or("");
        let after_eq = rest[eq + 1..].trim_start();
        let quote = after_eq.chars().next()?;
        if quote != '"' && quote != '\'' {
            rest = after_eq;
            continue;
        }
        let inner = &after_eq[1..];
        let end = inner.find(quote)?;
        let value = &inner[..end];
        if name.eq_ignore_ascii_case(key) {
            return Some(value);
        }
        rest = &inner[end + 1..];
    }
    None
}

fn parse_times(text: &str) -> Vec<OffsetDateTime> {
    let mut parsed = Vec::new();
    for token in text.split(',') {
        if token.contains('/') {
            continue;
        }
        let trimmed = token.trim();
        if trimmed.is_empty() {
            continue;
        }
        if let Ok(dt) = OffsetDateTime::parse(trimmed, &Rfc3339) {
            parsed.push(dt.to_offset(UtcOffset::UTC));
        }
    }
    parsed.sort_unstable();
    parsed.dedup();
    parsed
}

pub fn parse_capabilities(
    payload: &[u8],
) -> Result<HashMap<String, Vec<OffsetDateTime>>, TimeError> {
    if payload.len() > MAX_CAPABILITIES_BYTES {
        return Err(TimeError::Other(
            "radar capabilities exceed the safety limits".into(),
        ));
    }
    let text = std::str::from_utf8(payload)
        .map_err(|_| TimeError::Other("radar service returned invalid capabilities".into()))?;
    if text.to_ascii_uppercase().contains("<!DOCTYPE") {
        return Err(TimeError::Other(
            "radar capabilities exceed the safety limits".into(),
        ));
    }

    struct Frame {
        tag: String,
        times: Vec<OffsetDateTime>,
        is_time_dim: bool,
        is_name: bool,
        text: String,
    }

    let mut stack: Vec<Frame> = Vec::new();
    let mut result: HashMap<String, Vec<OffsetDateTime>> = HashMap::new();
    let mut saw_root = false;
    let mut rest = text;

    while let Some(lt) = rest.find('<') {
        let before = &rest[..lt];
        if let Some(top) = stack.last_mut() {
            if top.is_time_dim || top.is_name {
                top.text.push_str(before);
            }
        }
        let after_lt = &rest[lt + 1..];
        if let Some(after_comment) = after_lt.strip_prefix("!--") {
            let Some(end) = after_comment.find("-->") else {
                return Err(TimeError::Other(
                    "radar service returned invalid capabilities".into(),
                ));
            };
            rest = &after_comment[end + 3..];
            continue;
        }
        if let Some(after_pi) = after_lt.strip_prefix('?') {
            let Some(end) = after_pi.find("?>") else {
                return Err(TimeError::Other(
                    "radar service returned invalid capabilities".into(),
                ));
            };
            rest = &after_pi[end + 2..];
            continue;
        }
        let Some(gt) = after_lt.find('>') else {
            return Err(TimeError::Other(
                "radar service returned invalid capabilities".into(),
            ));
        };
        let inside = &after_lt[..gt];
        rest = &after_lt[gt + 1..];
        let trimmed = inside.trim();
        if trimmed.is_empty() {
            return Err(TimeError::Other(
                "radar service returned invalid capabilities".into(),
            ));
        }
        if let Some(closing) = trimmed.strip_prefix('/') {
            let close_name = local_name(closing.trim());
            let Some(popped) = stack.pop() else {
                return Err(TimeError::Other(
                    "radar service returned invalid capabilities".into(),
                ));
            };
            if popped.tag != close_name {
                return Err(TimeError::Other(
                    "radar service returned invalid capabilities".into(),
                ));
            }
            if popped.is_time_dim {
                if let Some(parent) = stack.last_mut() {
                    parent.times = parse_times(&popped.text);
                }
            } else if popped.is_name {
                let layer_name = popped.text.trim();
                if !layer_name.is_empty() {
                    if let Some(parent) = stack.last() {
                        let short = local_name(layer_name).to_owned();
                        result.insert(short, parent.times.clone());
                    }
                }
            }
            continue;
        }
        let (self_closing, body) = match trimmed.strip_suffix('/') {
            Some(b) => (true, b.trim()),
            None => (false, trimmed),
        };
        let mut split = body.splitn(2, char::is_whitespace);
        let raw_tag = split.next().unwrap_or("");
        let attrs = split.next().unwrap_or("");
        let tag = local_name(raw_tag).to_owned();
        if !saw_root {
            if !matches!(tag.as_str(), "WMS_Capabilities" | "WMT_MS_Capabilities") {
                return Err(TimeError::Other(
                    "radar service returned invalid capabilities".into(),
                ));
            }
            saw_root = true;
        }
        if self_closing {
            continue;
        }
        let inherited = stack.last().map(|f| f.times.clone()).unwrap_or_default();
        let is_time_dim = matches!(tag.as_str(), "Dimension" | "Extent")
            && attr_value(attrs, "name").is_some_and(|v| v.eq_ignore_ascii_case("time"));
        let is_name = tag == "Name";
        stack.push(Frame {
            tag,
            times: inherited,
            is_time_dim,
            is_name,
            text: String::new(),
        });
    }

    if !saw_root || !stack.is_empty() {
        return Err(TimeError::Other(
            "radar service returned invalid capabilities".into(),
        ));
    }
    Ok(result)
}

pub async fn select_time(
    client: &reqwest::Client,
    endpoint: &str,
    layer: &str,
    requested: OffsetDateTime,
    now: OffsetDateTime,
) -> Result<OffsetDateTime, TimeError> {
    let reference = now.to_offset(UtcOffset::UTC);
    let requested = requested.to_offset(UtcOffset::UTC);
    if requested < reference - HISTORY - TOLERANCE
        || requested > reference + time::Duration::minutes(1)
    {
        return Err(TimeError::Other(
            "radar history is available only for the past 60 minutes".into(),
        ));
    }
    let cached = {
        let now_inst = Instant::now();
        cap_cache()
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .iter()
            .find(|entry| entry.endpoint == endpoint && entry.expires_at > now_inst)
            .map(|entry| entry.layers.clone())
    };
    let layers = match cached {
        Some(found) => found,
        None => {
            let mut url =
                reqwest::Url::parse(endpoint).map_err(|e| TimeError::Other(e.to_string()))?;
            url.query_pairs_mut().extend_pairs([
                ("service", "WMS"),
                ("version", "1.3.0"),
                ("request", "GetCapabilities"),
            ]);
            let response = client
                .get(url)
                .send()
                .await
                .map_err(|e| TimeError::Other(e.to_string()))?
                .error_for_status()
                .map_err(|e| TimeError::Other(e.to_string()))?;
            let body = crate::http::body(response, MAX_CAPABILITIES_BYTES)
                .await
                .map_err(TimeError::Other)?;
            let parsed = parse_capabilities(&body)?;
            let mut cache = cap_cache().lock().unwrap_or_else(PoisonError::into_inner);
            cache.retain(|entry| entry.endpoint != endpoint);
            if cache.len() >= CACHE_LIMIT {
                cache.pop_front();
            }
            cache.push_back(CapEntry {
                endpoint: endpoint.to_owned(),
                expires_at: Instant::now() + CAPABILITIES_TTL,
                layers: parsed.clone(),
            });
            parsed
        }
    };
    let key = local_name(layer);
    let times = layers.get(key).map(Vec::as_slice).unwrap_or(&[]);
    let selected = times
        .iter()
        .copied()
        .filter(|&scan| scan >= reference - HISTORY && scan <= reference)
        .min_by_key(|&scan| ((scan - requested).abs(), scan))
        .ok_or_else(|| {
            TimeError::Missing(format!(
                "{layer} advertises no historical scans in the past 60 minutes"
            ))
        })?;
    let current = (reference - requested).abs() <= time::Duration::minutes(2);
    if !current && (selected - requested).abs() > TOLERANCE {
        return Err(TimeError::Missing(format!(
            "{layer} has no advertised scan within 10 minutes of the requested time"
        )));
    }
    Ok(selected)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn capabilities_inheritance_namespaces_and_safety_checks() {
        let xml = br#"<WMS_Capabilities xmlns="http://www.opengis.net/wms"><Capability><Layer><Dimension name="time" units="ISO8601" nearestValue="1">2026-10-17T11:39:00Z,2026-10-17T11:41:00Z,not-a-date,2026-10-17T10:00:00Z/2026-10-17T12:00:00Z/PT2M</Dimension><Layer><Name>conus:radar</Name></Layer></Layer></Capability></WMS_Capabilities>"#;
        let parsed = parse_capabilities(xml).unwrap();
        assert_eq!(parsed["radar"].len(), 2);
        assert!(parse_capabilities(b"<!DOCTYPE wms><wms/>").is_err());
        assert!(parse_capabilities(b"not XML").is_err());
    }
}

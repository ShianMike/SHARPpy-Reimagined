//! Independent, bounded checks of OVH liveness and two public weather samples.

use std::time::Duration;

use futures_util::{
    StreamExt,
    future::{Either, select},
    pin_mut,
};
use serde::{Deserialize, Serialize};
use worker::{
    AbortController, ByteStream, CacheMode, Date, DateInit, Delay, Env, Fetch, Method, Request,
    RequestInit, RequestRedirect, Response, Result, Url, js_sys, kv::KvStore,
};

use crate::policy::origin_url;

const SCHEMA_VERSION: u8 = 1;
const KV_BINDING: &str = "EDGE_STATE";
const CONFIG_KEY: &str = "config-v1";
const STATUS_KEY: &str = "status-v1";
const HOUR_MS: u64 = 60 * 60 * 1000;
const MAX_SNAPSHOT_AGE_MS: u64 = 15 * 60 * 1000;
const MAX_RADAR_AGE_MS: u64 = 15 * 60 * 1000;
const MAX_BODY_BYTES: usize = 4096;
const PROBE_TIMEOUT: Duration = Duration::from_secs(5);

const HRRR_BUCKET: &str = "noaa-hrrr-bdp-pds";
const MODEL_SOURCE: &str = "NOAA HRRR CONUS F00 (AWS S3)";
const RADAR_SOURCE: &str = "NOAA MRMS CONUS lowest-altitude reflectivity";
const MRMS_LATEST: &str = "https://mrms.ncep.noaa.gov/2D/ReflectivityAtLowestAltitude/MRMS_ReflectivityAtLowestAltitude.latest.grib2.gz";
const GITHUB_LATEST: &str = "https://github.com/ShianMike/SHARPpy-Reimagined/releases/latest";
const GITHUB_TAG: &str = "https://github.com/ShianMike/SHARPpy-Reimagined/releases/tag/v";

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct Config {
    schema_version: u8,
    route_public_reads: bool,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
enum OriginState {
    Up,
    Down,
    #[default]
    Unknown,
}

#[derive(Default, Deserialize, Serialize)]
struct Origin {
    state: OriginState,
    status: Option<u16>,
    version: Option<String>,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
enum FeedState {
    Available,
    Unavailable,
    #[default]
    Unknown,
}

#[derive(Serialize)]
struct Feed {
    state: FeedState,
    source: &'static str,
}

#[derive(Deserialize, Serialize)]
struct Release {
    version: String,
    url: String,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
struct Snapshot {
    schema_version: u8,
    checked_at_ms: u64,
    origin: Origin,
    models: FeedState,
    radar: FeedState,
    release: Option<Release>,
    last_release_attempt_ms: Option<u64>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct Status {
    schema_version: u8,
    checked_at_ms: Option<u64>,
    stale: bool,
    origin: Origin,
    models: Feed,
    radar: Feed,
    release: Option<Release>,
}

impl Status {
    fn from_snapshot(snapshot: Option<Snapshot>, now: u64) -> Self {
        let mut status = Self {
            schema_version: SCHEMA_VERSION,
            checked_at_ms: None,
            stale: true,
            origin: Origin::default(),
            models: Feed {
                state: FeedState::Unknown,
                source: MODEL_SOURCE,
            },
            radar: Feed {
                state: FeedState::Unknown,
                source: RADAR_SOURCE,
            },
            release: None,
        };
        if let Some(snapshot) = snapshot {
            status.release = snapshot.release;
            if let Some(age) = now.checked_sub(snapshot.checked_at_ms) {
                status.checked_at_ms = Some(snapshot.checked_at_ms);
                if age < MAX_SNAPSHOT_AGE_MS {
                    status.stale = false;
                    status.origin = snapshot.origin;
                    status.models.state = snapshot.models;
                    status.radar.state = snapshot.radar;
                }
            }
        }
        status
    }

    fn health_code(&self) -> u16 {
        if !self.stale && self.origin.state == OriginState::Up {
            200
        } else {
            503
        }
    }
}

/// Public routing configuration, disabled unless KV contains a valid opt-in.
pub async fn config(env: &Env) -> Result<Response> {
    let stored = read_optional(env, CONFIG_KEY).await;
    Response::from_json(&decode_config(stored.as_deref()))
}

/// Read the latest scheduled snapshot and apply its freshness limit.
pub async fn status(env: &Env) -> Result<Response> {
    Response::from_json(&current_status(env).await)
}

/// Return the status document with HTTP 503 unless OVH liveness is recent and up.
pub async fn health(env: &Env) -> Result<Response> {
    let status = current_status(env).await;
    Ok(Response::from_json(&status)?.with_status(status.health_code()))
}

/// Refresh the single snapshot. Called by the five-minute scheduled handler.
pub async fn refresh(env: &Env) -> Result<()> {
    let kv = env.kv(KV_BINDING)?;
    // A failed read must not overwrite the previous notice or reset its check interval.
    let stored = read_state(&kv, STATUS_KEY).await?;
    let (mut release, mut last_release_attempt_ms) = stored
        .as_deref()
        .and_then(decode_snapshot)
        .map_or((None, None), |snapshot| {
            (snapshot.release, snapshot.last_release_attempt_ms)
        });

    let checked_at_ms = Date::now().as_millis();
    let origin = probe_origin(env).await;
    let models = probe_model(checked_at_ms).await;
    let radar = probe_radar().await;

    let now = Date::now().as_millis();
    if release_due(last_release_attempt_ms, now) {
        last_release_attempt_ms = Some(now);
        release = probe_release().await.or(release);
    }

    let snapshot = Snapshot {
        schema_version: SCHEMA_VERSION,
        checked_at_ms,
        origin,
        models,
        radar,
        release,
        last_release_attempt_ms,
    };
    kv.put(STATUS_KEY, &snapshot)?.execute().await?;
    Ok(())
}

async fn read_state(kv: &KvStore, key: &str) -> Result<Option<Vec<u8>>> {
    match kv.get(key).stream().await? {
        Some(stream) => read_bounded(ByteStream::from(stream)).await,
        None => Ok(None),
    }
}

async fn read_optional(env: &Env, key: &str) -> Option<Vec<u8>> {
    let kv = env.kv(KV_BINDING).ok()?;
    read_state(&kv, key).await.ok().flatten()
}

async fn current_status(env: &Env) -> Status {
    let stored = read_optional(env, STATUS_KEY).await;
    Status::from_snapshot(
        stored.as_deref().and_then(decode_snapshot),
        Date::now().as_millis(),
    )
}

fn decode_config(stored: Option<&[u8]>) -> Config {
    stored
        .and_then(|bytes| serde_json::from_slice::<Config>(bytes).ok())
        .filter(|config| config.schema_version == SCHEMA_VERSION)
        .unwrap_or(Config {
            schema_version: SCHEMA_VERSION,
            route_public_reads: false,
        })
}

fn decode_snapshot(stored: &[u8]) -> Option<Snapshot> {
    let mut snapshot: Snapshot = serde_json::from_slice(stored).ok()?;
    if snapshot.schema_version != SCHEMA_VERSION {
        return None;
    }
    let origin = &mut snapshot.origin;
    if origin.state == OriginState::Up {
        if origin.status != Some(200) || !origin.version.as_deref().is_some_and(stable_version) {
            *origin = Origin::default();
        }
    } else {
        origin.version = None;
    }
    snapshot.release = snapshot
        .release
        .filter(|release| release_version(&release.url) == Some(release.version.as_str()));
    Some(snapshot)
}

fn release_due(last_attempt: Option<u64>, now: u64) -> bool {
    last_attempt
        .and_then(|attempt| now.checked_sub(attempt))
        .is_none_or(|age| age >= HOUR_MS)
}

fn stable_version(version: &str) -> bool {
    if version.len() > 32 {
        return false;
    }
    let mut parts = version.split('.');
    (0..3).all(|_| {
        parts.next().is_some_and(|part| {
            !part.is_empty()
                && (part == "0" || !part.starts_with('0'))
                && part.bytes().all(|byte| byte.is_ascii_digit())
        })
    }) && parts.next().is_none()
}

fn release_version(url: &str) -> Option<&str> {
    let version = url.strip_prefix(GITHUB_TAG)?;
    stable_version(version).then_some(version)
}

#[derive(Deserialize)]
struct OriginHealth {
    ok: bool,
    version: String,
}

fn parse_origin(status: Option<u16>, body: Option<&[u8]>) -> Origin {
    let version = if status == Some(200) {
        body.and_then(|body| serde_json::from_slice::<OriginHealth>(body).ok())
            .filter(|health| health.ok && stable_version(&health.version))
            .map(|health| health.version)
    } else {
        None
    };
    Origin {
        state: if version.is_some() {
            OriginState::Up
        } else {
            OriginState::Down
        },
        status,
        version,
    }
}

fn health_origin(override_origin: Option<&str>, default_origin: Option<&str>) -> Option<Url> {
    let base = override_origin
        .filter(|value| !value.trim().is_empty())
        .or(default_origin)?;
    let mut url = origin_url(base)?;
    url.set_path("/__health");
    Some(url)
}

async fn probe_origin(env: &Env) -> Origin {
    let override_origin = env
        .var("OVH_HEALTH_ORIGIN")
        .ok()
        .map(|origin| origin.to_string());
    let default_origin = env.var("OVH_ORIGIN").ok().map(|origin| origin.to_string());
    let Some(url) = health_origin(override_origin.as_deref(), default_origin.as_deref()) else {
        return Origin::default();
    };
    match probe(url.as_str(), Method::Get).await {
        Some(probe) => parse_origin(Some(probe.response.status_code()), probe.body.as_deref()),
        None => parse_origin(None, None),
    }
}

async fn probe_model(now: u64) -> FeedState {
    // HRRR publishes hourly. Sample F00 two UTC cycles back to allow publication time.
    let cycle: js_sys::Date = Date::new(DateInit::Millis(now.saturating_sub(2 * HOUR_MS))).into();
    let key = format!(
        "hrrr.{:04}{:02}{:02}/conus/hrrr.t{:02}z.wrfsfcf00.grib2",
        cycle.get_utc_full_year(),
        cycle.get_utc_month() + 1,
        cycle.get_utc_date(),
        cycle.get_utc_hours(),
    );
    let url =
        format!("https://{HRRR_BUCKET}.s3.amazonaws.com/?list-type=2&max-keys=1&prefix={key}");
    probe(&url, Method::Get)
        .await
        .and_then(|probe| probe.body)
        .map_or(FeedState::Unknown, |body| parse_model_listing(&body, &key))
}

fn xml_value<'a>(xml: &'a str, tag: &str) -> Option<&'a str> {
    let (_, rest) = xml.split_once(format!("<{tag}>").as_str())?;
    rest.split_once(format!("</{tag}>").as_str())
        .map(|(value, _)| value)
}

fn parse_model_listing(body: &[u8], expected_key: &str) -> FeedState {
    let Some(xml) = std::str::from_utf8(body).ok().map(str::trim) else {
        return FeedState::Unknown;
    };
    let root = if xml.starts_with("<?xml ") {
        xml.split_once("?>")
            .map_or("", |(_, root)| root.trim_start())
    } else {
        xml
    };
    // The only XML consumed here is S3's one-object ListObjectsV2 response.
    if !root.starts_with("<ListBucketResult ") && !root.starts_with("<ListBucketResult>") {
        return FeedState::Unknown;
    }
    let Some((_, result)) = root
        .strip_suffix("</ListBucketResult>")
        .and_then(|root| root.split_once('>'))
    else {
        return FeedState::Unknown;
    };
    if xml_value(result, "Name") != Some(HRRR_BUCKET)
        || xml_value(result, "Prefix") != Some(expected_key)
    {
        return FeedState::Unknown;
    }
    match (xml_value(result, "KeyCount"), xml_value(result, "Contents")) {
        (Some("0"), None) if xml_value(result, "IsTruncated") == Some("false") => {
            FeedState::Unavailable
        }
        (Some("1"), Some(object)) => {
            let key = xml_value(object, "Key");
            let size = xml_value(object, "Size").and_then(|size| size.parse::<u64>().ok());
            if key == Some(expected_key) && size.is_some_and(|size| size > 0) {
                FeedState::Available
            } else if key.and_then(|key| key.strip_prefix(expected_key)) == Some(".idx") {
                FeedState::Unavailable
            } else {
                FeedState::Unknown
            }
        }
        _ => FeedState::Unknown,
    }
}

async fn probe_radar() -> FeedState {
    let Some(probe) = probe(MRMS_LATEST, Method::Head).await else {
        return FeedState::Unknown;
    };
    let headers = probe.response.headers();
    let gzip = headers
        .get("Content-Type")
        .ok()
        .flatten()
        .is_some_and(|mime| matches!(mime.as_str(), "application/gzip" | "application/x-gzip"));
    let nonempty = headers
        .get("Content-Length")
        .ok()
        .flatten()
        .and_then(|length| length.parse::<u64>().ok())
        .is_some_and(|size| size > 0);
    let modified_ms = headers
        .get("Last-Modified")
        .ok()
        .flatten()
        .map(|value| js_sys::Date::parse(&value))
        .filter(|millis| millis.is_finite() && *millis >= 0.0)
        .map(|millis| millis as u64);
    radar_state(
        probe.response.status_code(),
        gzip && nonempty,
        modified_ms,
        Date::now().as_millis(),
    )
}

fn radar_state(
    status: u16,
    expected_object: bool,
    modified_ms: Option<u64>,
    now: u64,
) -> FeedState {
    match status {
        404 | 410 => FeedState::Unavailable,
        200 if expected_object => match modified_ms.and_then(|time| now.checked_sub(time)) {
            Some(age) if age < MAX_RADAR_AGE_MS => FeedState::Available,
            Some(_) => FeedState::Unavailable,
            None => FeedState::Unknown,
        },
        _ => FeedState::Unknown,
    }
}

async fn probe_release() -> Option<Release> {
    let probe = probe(GITHUB_LATEST, Method::Head).await?;
    if !matches!(probe.response.status_code(), 301 | 302 | 303 | 307 | 308) {
        return None;
    }
    let url = probe.response.headers().get("Location").ok()??;
    let version = release_version(&url)?.to_owned();
    Some(Release { version, url })
}

struct Probe {
    response: Response,
    body: Option<Vec<u8>>,
}

async fn probe(url: &str, method: Method) -> Option<Probe> {
    let read_body = method == Method::Get;
    let mut init = RequestInit::new();
    init.with_method(method)
        .with_cache(CacheMode::NoStore)
        .with_redirect(RequestRedirect::Manual);
    let request = Request::new_with_init(url, &init).ok()?;
    let controller = AbortController::default();
    let signal = controller.signal();
    let fetch = async {
        let mut response = Fetch::Request(request)
            .send_with_signal(&signal)
            .await
            .ok()?;
        let oversized = response
            .headers()
            .get("Content-Length")
            .ok()
            .flatten()
            .and_then(|length| length.parse::<u64>().ok())
            .is_some_and(|length| length > MAX_BODY_BYTES as u64);
        let body = if read_body && response.status_code() == 200 && !oversized {
            match response.stream() {
                Ok(stream) => read_bounded(stream).await.ok().flatten(),
                Err(_) => None,
            }
        } else {
            None
        };
        Some(Probe { response, body })
    };
    let timeout = Delay::from(PROBE_TIMEOUT);
    pin_mut!(fetch, timeout);
    let result = match select(fetch, timeout).await {
        Either::Left((result, _)) => result,
        Either::Right(_) => None,
    };
    controller.abort();
    result
}

async fn read_bounded(stream: ByteStream) -> Result<Option<Vec<u8>>> {
    pin_mut!(stream);
    let mut body = Vec::new();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk?;
        if chunk.len() > MAX_BODY_BYTES - body.len() {
            return Ok(None);
        }
        body.extend_from_slice(&chunk);
    }
    Ok(Some(body))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn remote_config_requires_an_explicit_boolean_in_schema_one() {
        let valid = br#"{"schemaVersion":1,"routePublicReads":true}"#;
        assert!(decode_config(Some(valid)).route_public_reads);
        for input in [
            None,
            Some(b"broken".as_slice()),
            Some(br#"{"routePublicReads":true}"#),
            Some(br#"{"schemaVersion":2,"routePublicReads":true}"#),
            Some(br#"{"schemaVersion":1,"routePublicReads":"true"}"#),
        ] {
            assert!(!decode_config(input).route_public_reads);
        }
        assert_eq!(decode_config(None).schema_version, SCHEMA_VERSION);
    }

    #[test]
    fn origin_requires_health_json_and_preserves_failed_http_status() {
        let up = parse_origin(
            Some(200),
            Some(br#"{"ok":true,"version":"3.9.0","nativePatch":null}"#),
        );
        assert_eq!(up.state, OriginState::Up);
        assert_eq!(up.version.as_deref(), Some("3.9.0"));
        let wrong_body = parse_origin(Some(200), Some(b"<html>login</html>"));
        assert_eq!(wrong_body.state, OriginState::Down);
        assert_eq!(wrong_body.status, Some(200));
        let disconnected = parse_origin(None, None);
        assert_eq!(disconnected.state, OriginState::Down);
        assert_eq!(disconnected.status, None);
    }

    #[test]
    fn health_origin_prefers_the_override_and_falls_back_when_unset_or_blank() {
        let default_origin = Some("https://app.example");
        let override_origin = Some("https://health.example/");
        assert_eq!(
            health_origin(override_origin, default_origin)
                .unwrap()
                .as_str(),
            "https://health.example/__health"
        );
        for disabled in [None, Some("  ")] {
            assert_eq!(
                health_origin(disabled, default_origin).unwrap().as_str(),
                "https://app.example/__health"
            );
        }
        assert!(health_origin(Some("https://health.example/path"), default_origin).is_none());
        assert!(health_origin(None, None).is_none());
    }

    #[test]
    fn model_listing_distinguishes_a_published_file_from_an_empty_or_invalid_result() {
        let key = "hrrr.20261125/conus/hrrr.t08z.wrfsfcf00.grib2";
        let listing = |count, truncated, contents: &str| {
            format!(
                "<?xml version=\"1.0\"?><ListBucketResult xmlns=\"http://s3.amazonaws.com/doc/2006-03-01/\"><Name>{HRRR_BUCKET}</Name><Prefix>{key}</Prefix><KeyCount>{count}</KeyCount><IsTruncated>{truncated}</IsTruncated>{contents}</ListBucketResult>"
            )
        };
        let file = format!("<Contents><Key>{key}</Key><Size>142704264</Size></Contents>");
        let idx = format!("<Contents><Key>{key}.idx</Key><Size>2048</Size></Contents>");
        assert_eq!(
            parse_model_listing(listing(1, true, &file).as_bytes(), key),
            FeedState::Available
        );
        for response in [listing(0, false, ""), listing(1, false, &idx)] {
            assert_eq!(
                parse_model_listing(response.as_bytes(), key),
                FeedState::Unavailable
            );
        }
        assert_eq!(
            parse_model_listing(b"<Error>denied</Error>", key),
            FeedState::Unknown
        );
        assert_eq!(
            parse_model_listing(listing(0, true, "").as_bytes(), key),
            FeedState::Unknown
        );
    }

    #[test]
    fn radar_requires_a_fresh_expected_object_and_treats_transport_errors_as_unknown() {
        let now = 2 * MAX_RADAR_AGE_MS;
        assert_eq!(
            radar_state(200, true, Some(now - 1), now),
            FeedState::Available
        );
        assert_eq!(
            radar_state(200, true, Some(now - MAX_RADAR_AGE_MS), now),
            FeedState::Unavailable
        );
        assert_eq!(radar_state(404, false, None, now), FeedState::Unavailable);
        for (code, object, time) in [
            (200, false, Some(now)),
            (200, true, Some(now + 1)),
            (200, true, None),
            (503, false, None),
        ] {
            assert_eq!(radar_state(code, object, time, now), FeedState::Unknown);
        }
    }

    #[test]
    fn release_notice_only_accepts_a_canonical_stable_tag_and_rechecks_hourly() {
        let valid = format!("{GITHUB_TAG}3.10.0");
        assert_eq!(release_version(&valid), Some("3.10.0"));
        for invalid in [
            format!("{valid}?other"),
            format!("{valid}-rc.1"),
            format!("{GITHUB_TAG}03.10.0"),
            valid.replace("github.com", "github.example"),
        ] {
            assert_eq!(release_version(&invalid), None);
        }
        assert!(release_due(None, HOUR_MS));
        assert!(!release_due(Some(HOUR_MS), 2 * HOUR_MS - 1));
        assert!(release_due(Some(HOUR_MS), 2 * HOUR_MS));
    }

    #[test]
    fn snapshot_expires_live_fields_and_health_without_losing_the_release_notice() {
        let now = 2 * MAX_SNAPSHOT_AGE_MS;
        let checked = now - MAX_SNAPSHOT_AGE_MS;
        let mut stored = serde_json::json!({
            "schemaVersion": 1,
            "checkedAtMs": checked,
            "origin": {"state":"up","status":200,"version":"3.9.0"},
            "models": "available",
            "radar": "available",
            "release": {"version":"3.9.0","url":format!("{GITHUB_TAG}3.9.0")},
            "lastReleaseAttemptMs": checked
        });
        let raw = serde_json::to_vec(&stored).unwrap();
        let fresh = Status::from_snapshot(decode_snapshot(&raw), now - 1);
        assert_eq!(fresh.health_code(), 200);
        let stale = Status::from_snapshot(decode_snapshot(&raw), now);
        assert_eq!(stale.health_code(), 503);
        assert_eq!(
            serde_json::to_value(stale).unwrap(),
            serde_json::json!({
                "schemaVersion": 1,
                "checkedAtMs": checked,
                "stale": true,
                "origin": {"state":"unknown","status":null,"version":null},
                "models": {"state":"unknown","source":MODEL_SOURCE},
                "radar": {"state":"unknown","source":RADAR_SOURCE},
                "release": {"version":"3.9.0","url":format!("{GITHUB_TAG}3.9.0")}
            })
        );
        let missing = Status::from_snapshot(None, now);
        assert_eq!(missing.checked_at_ms, None);
        assert_eq!(missing.health_code(), 503);
        let future = Status::from_snapshot(decode_snapshot(&raw), checked - 1);
        assert_eq!(future.checked_at_ms, None);
        assert_eq!(future.health_code(), 503);
        stored["origin"]["status"] = serde_json::Value::Null;
        let invalid = serde_json::to_vec(&stored).unwrap();
        assert_eq!(
            Status::from_snapshot(decode_snapshot(&invalid), now - 1).health_code(),
            503
        );
        stored["origin"] = serde_json::json!({"state":"down","status":null,"version":null});
        let disconnected = serde_json::to_vec(&stored).unwrap();
        let down = Status::from_snapshot(decode_snapshot(&disconnected), now - 1);
        assert!(!down.stale);
        assert_eq!(down.origin.state, OriginState::Down);
        assert_eq!(down.health_code(), 503);
    }
}

//! Personal-use Spotter Network positions. Only recent, sanitized samples are retained.

mod placefile;

use std::future::Future;
use std::sync::OnceLock;
use std::time::Duration;

use reqwest::header::{
    HeaderMap, HeaderName, HeaderValue, ETAG, IF_MODIFIED_SINCE, IF_NONE_MATCH, LAST_MODIFIED,
};
use reqwest::{Client, StatusCode};
use serde::Serialize;
use time::OffsetDateTime;
use tokio::{sync::Mutex, time::Instant};

use crate::format_rfc3339_utc;

// The live feeds carry hundreds of positions. Leave room for busy weather days
// while bounding both the transfer and the number of retained points.
const MAX_BYTES: usize = 1024 * 1024;
const MAX_POSITIONS: usize = 2048;
const MAX_AGE: time::Duration = time::Duration::minutes(10);
const CLOCK_SKEW: time::Duration = time::Duration::seconds(30);
const POLL_INTERVAL: Duration = Duration::from_secs(60);
const UNAVAILABLE: &str = "Spotter Network positions are temporarily unavailable.";

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SpotterPositions {
    pub checked_at: String,
    pub streamers_available: bool,
    pub positions: Vec<SpotterPosition>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SpotterPosition {
    pub latitude: f64,
    pub longitude: f64,
    pub updated_at: String,
    pub chase_cam: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub display_name: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub motion: Option<SpotterMotion>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "camelCase")]
pub enum SpotterMotion {
    Stationary,
    Heading { degrees: u16 },
}

#[derive(Clone)]
struct Position {
    latitude: f64,
    longitude: f64,
    updated_at: OffsetDateTime,
    chase_cam: bool,
    display_name: Option<String>,
    motion: Option<SpotterMotion>,
}

fn is_fresh(updated_at: OffsetDateTime, now: OffsetDateTime) -> bool {
    (-CLOCK_SKEW..=MAX_AGE).contains(&(now - updated_at))
}

fn sort_and_cap(positions: &mut Vec<Position>) {
    // Preserve feed order on exact matches so the general position stays first.
    positions.sort_by(|left, right| {
        right
            .updated_at
            .cmp(&left.updated_at)
            .then_with(|| {
                left.latitude
                    .partial_cmp(&right.latitude)
                    .expect("finite latitude")
            })
            .then_with(|| {
                left.longitude
                    .partial_cmp(&right.longitude)
                    .expect("finite longitude")
            })
    });
    positions.dedup_by(|duplicate, retained| {
        let same = duplicate.updated_at == retained.updated_at
            && duplicate.latitude == retained.latitude
            && duplicate.longitude == retained.longitude;
        if same {
            retained.chase_cam |= duplicate.chase_cam;
            if retained.display_name.is_none() {
                retained.display_name = duplicate.display_name.take();
            }
            retained.motion = retained.motion.or(duplicate.motion);
        }
        same
    });
    positions.truncate(MAX_POSITIONS);
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Source {
    General,
    Video,
}

type Validator = (HeaderName, HeaderValue);

#[derive(Default)]
struct Feed {
    positions: Vec<Position>,
    validator: Option<Validator>,
}

enum FeedUpdate {
    Replaced(Feed),
    NotModified(Option<Validator>),
}

impl Feed {
    fn apply(&mut self, update: FeedUpdate) {
        match update {
            FeedUpdate::Replaced(feed) => *self = feed,
            FeedUpdate::NotModified(validator) => {
                if validator.is_some() {
                    self.validator = validator;
                }
            }
        }
    }
}

#[derive(Default)]
struct Cache {
    next_poll: Option<Instant>,
    checked_at: Option<OffsetDateTime>,
    general: Feed,
    video: Feed,
    streamers_available: bool,
}

impl Cache {
    fn snapshot(&mut self, now: OffsetDateTime) -> Result<SpotterPositions, &'static str> {
        self.general
            .positions
            .retain(|position| is_fresh(position.updated_at, now));
        self.video
            .positions
            .retain(|position| is_fresh(position.updated_at, now));
        let checked_at = self
            .checked_at
            .filter(|checked_at| now - *checked_at <= MAX_AGE)
            .ok_or(UNAVAILABLE)?;
        let mut positions = self.general.positions.clone();
        if self.streamers_available {
            positions.extend_from_slice(&self.video.positions);
            sort_and_cap(&mut positions);
        }
        Ok(SpotterPositions {
            checked_at: format_rfc3339_utc(checked_at),
            streamers_available: self.streamers_available,
            positions: positions
                .into_iter()
                .map(|position| SpotterPosition {
                    latitude: position.latitude,
                    longitude: position.longitude,
                    updated_at: format_rfc3339_utc(position.updated_at),
                    chase_cam: position.chase_cam,
                    display_name: position.display_name,
                    motion: position.motion,
                })
                .collect(),
        })
    }
}

struct SpotterClient {
    http: Client,
    cache: Mutex<Cache>,
}

/// Check the official feeds on demand, sharing one cache and poll across callers.
pub async fn fetch_positions() -> Result<SpotterPositions, &'static str> {
    static CLIENT: OnceLock<Option<SpotterClient>> = OnceLock::new();
    let client = CLIENT
        .get_or_init(|| {
            let _ = rustls::crypto::ring::default_provider().install_default();
            let http = Client::builder()
                .https_only(true)
                .redirect(reqwest::redirect::Policy::none())
                .retry(reqwest::retry::never())
                .user_agent("SHARPpy-Reimagined (https://github.com/ShianMike/SHARPpy-Reimagined)")
                .connect_timeout(Duration::from_secs(2))
                .timeout(Duration::from_secs(4))
                .build()
                .ok()?;
            Some(SpotterClient {
                http,
                cache: Mutex::default(),
            })
        })
        .as_ref()
        .ok_or(UNAVAILABLE)?;
    read_positions(
        &client.cache,
        OffsetDateTime::now_utc,
        |source, validator| download(&client.http, source, validator),
    )
    .await
}

async fn read_positions<F: Future<Output = Option<FeedUpdate>>>(
    cache: &Mutex<Cache>,
    now: impl Fn() -> OffsetDateTime,
    fetch: impl Fn(Source, Option<Validator>) -> F,
) -> Result<SpotterPositions, &'static str> {
    // Holding the lock across the short requests makes concurrent calls share
    // the result. Set the cadence before I/O so errors and cancellation honor it.
    let mut cache = cache.lock().await;
    let started = Instant::now();
    if cache.next_poll.is_none_or(|due| started >= due) {
        cache.next_poll = Some(started + POLL_INTERVAL);
        if let Some(update) = fetch(Source::General, cache.general.validator.clone()).await {
            cache.general.apply(update);
            cache.checked_at = Some(now());
            cache.streamers_available = false;
            cache.next_poll = Some(Instant::now() + POLL_INTERVAL);
            if let Some(update) = fetch(Source::Video, cache.video.validator.clone()).await {
                cache.video.apply(update);
                cache.streamers_available = true;
            }
        }
    }
    cache.snapshot(now())
}

async fn download(client: &Client, source: Source, prior: Option<Validator>) -> Option<FeedUpdate> {
    let url = match source {
        Source::General => "https://www.spotternetwork.org/feeds/gr.txt",
        Source::Video => "https://www.spotternetwork.org/feeds/gr-stream-no.txt",
    };
    let mut request = client.get(url);
    let conditional = prior.is_some();
    if let Some((name, value)) = prior {
        request = request.header(name, value);
    }
    let response = request.send().await.ok()?;
    let validator = response_validator(response.headers());
    match response.status() {
        StatusCode::NOT_MODIFIED if conditional => Some(FeedUpdate::NotModified(validator)),
        StatusCode::OK => {
            let body = crate::http::body(response, MAX_BYTES).await.ok()?;
            let positions = parse_feed(&body, OffsetDateTime::now_utc(), source)?;
            Some(FeedUpdate::Replaced(Feed {
                positions,
                validator,
            }))
        }
        _ => None,
    }
}

fn parse_feed(body: &[u8], now: OffsetDateTime, source: Source) -> Option<Vec<Position>> {
    // The provider occasionally emits invalid UTF-8 in display text. Replacement
    // characters cannot pass the name or coordinate validators.
    placefile::parse(&String::from_utf8_lossy(body), now, source)
}

fn response_validator(headers: &HeaderMap) -> Option<Validator> {
    [(ETAG, IF_NONE_MATCH), (LAST_MODIFIED, IF_MODIFIED_SINCE)]
        .into_iter()
        .find_map(|(response_header, request_header)| {
            let value = headers.get(response_header)?;
            (!value.is_empty() && value.len() <= 256).then(|| (request_header, value.clone()))
        })
}

#[cfg(test)]
mod tests;

use super::*;
use serde_json::json;
use std::{
    collections::HashMap,
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc, Mutex,
    },
};

const COLLECTION: &str = "https://api.weather.gov/gridpoints/OUN/1,1/stations";

#[derive(Clone, Default)]
struct Fixture {
    payloads: Arc<HashMap<String, Result<Value, String>>>,
    hits: Arc<Mutex<Vec<String>>>,
    active: Arc<AtomicUsize>,
    peak: Arc<AtomicUsize>,
}

struct Active(Arc<AtomicUsize>);
impl Drop for Active {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::SeqCst);
    }
}

impl JsonSource for Fixture {
    async fn get_json(&self, url: &str, _: Duration) -> Result<Value, String> {
        self.hits.lock().unwrap().push(url.into());
        let active = self.active.fetch_add(1, Ordering::SeqCst) + 1;
        let _active = Active(self.active.clone());
        self.peak.fetch_max(active, Ordering::SeqCst);
        tokio::time::sleep(Duration::from_millis(2)).await;
        self.payloads
            .get(url.split('?').next().unwrap())
            .cloned()
            .unwrap_or_else(|| Err("unavailable".into()))
    }
}

fn request() -> Request {
    Request {
        lat: 35.,
        lon: -97.,
        requested: OffsetDateTime::parse("2026-09-13T01:45:00Z", &Rfc3339).unwrap(),
        tolerance_seconds: 3600.,
        max_stations: 24,
        workers: 6,
        timeout: 15.,
    }
}

fn fixture(count: usize) -> Fixture {
    let mut payloads = HashMap::new();
    payloads.insert(
        format!("{BASE}/points/35.0000,-97.0000"),
        Ok(json!({"properties":{"observationStations":COLLECTION}})),
    );
    payloads.insert(COLLECTION.into(), Ok(json!({"features":(0..count).rev().map(|i| json!({"geometry":{"coordinates":[-97.,35. + i as f64 * 0.01]},"properties":{"stationIdentifier":format!("K{i:03}")}})).collect::<Vec<_>>()})));
    for i in 0..count {
        payloads.insert(format!("{BASE}/stations/K{i:03}/observations"), Ok(json!({"features":[{"properties":{"timestamp":"2026-09-13T01:35:00Z","temperature":{"value":20,"unitCode":"degC"}}}]})));
    }
    Fixture {
        payloads: Arc::new(payloads),
        ..Default::default()
    }
}

#[tokio::test]
async fn bounds_queries_and_concurrency_and_retains_nearest_order() {
    let source = fixture(40);
    let result = fetch(
        source.clone(),
        Request {
            max_stations: 100,
            workers: 100,
            ..request()
        },
        || Ok(false),
    )
    .await
    .unwrap();
    assert_eq!(result.catalog.len(), 40);
    assert_eq!(result.queried.len(), MAX_STATIONS);
    assert_eq!(result.matched.len(), MAX_STATIONS);
    assert_eq!(source.peak.load(Ordering::SeqCst), MAX_WORKERS);
    assert_eq!(
        result
            .matched
            .iter()
            .map(|(index, _)| result.catalog[*index].id.as_str())
            .take(3)
            .collect::<Vec<_>>(),
        ["K000", "K001", "K002"]
    );
    let hits = source.hits.lock().unwrap();
    assert_eq!(hits.len(), MAX_STATIONS + 2);
    assert!(hits[2..]
        .iter()
        .all(|url| url.contains("start=2026-09-13T00%3A45%3A00Z")
            && url.contains("end=2026-09-13T02%3A45%3A00Z")
            && url.ends_with("limit=50")));
}

#[tokio::test]
async fn separates_missing_and_failed_stations_without_blanking_neighbours() {
    let mut source = fixture(3);
    let payloads = Arc::make_mut(&mut source.payloads);
    payloads.insert(
        format!("{BASE}/stations/K001/observations"),
        Ok(json!({"features":[]})),
    );
    payloads.insert(
        format!("{BASE}/stations/K002/observations"),
        Err("HTTP 503".into()),
    );
    let result = fetch(source, request(), || Ok(false)).await.unwrap();
    assert_eq!(result.matched.len(), 1);
    assert_eq!(
        result
            .unmatched
            .iter()
            .map(|index| result.catalog[*index].id.as_str())
            .collect::<Vec<_>>(),
        ["K001", "K002"]
    );
    assert_eq!(result.catalog[result.failed[0]].id, "K002");
}

#[tokio::test]
async fn all_failures_are_an_error_and_empty_catalogues_remain_valid() {
    let mut source = fixture(1);
    Arc::make_mut(&mut source.payloads).insert(
        format!("{BASE}/stations/K000/observations"),
        Err("HTTP 503".into()),
    );
    assert!(
        matches!(fetch(source, request(), || Ok(false)).await, Err(FetchError::Provider(message)) if message.contains("nearby surface observations were unavailable"))
    );
    let result = fetch(fixture(0), request(), || Ok(false)).await.unwrap();
    assert!(result.queried.is_empty() && result.matched.is_empty());
}

#[tokio::test]
async fn rejects_untrusted_links_and_cancellation_before_more_requests() {
    for link in [
        "http://api.weather.gov/stations",
        "https://api.weather.gov.evil.example/stations",
        "https://user@api.weather.gov/stations",
        "https://api.weather.gov:444/stations",
    ] {
        let mut source = fixture(1);
        Arc::make_mut(&mut source.payloads).insert(
            format!("{BASE}/points/35.0000,-97.0000"),
            Ok(json!({"properties":{"observationStations":link}})),
        );
        assert!(matches!(
            fetch(source.clone(), request(), || Ok(false)).await,
            Err(FetchError::Provider(_))
        ));
        assert_eq!(source.hits.lock().unwrap().len(), 1);
    }
    let source = fixture(12);
    assert!(matches!(
        fetch(source.clone(), request(), || Ok(true)).await,
        Err(FetchError::Cancelled)
    ));
    assert!(source.hits.lock().unwrap().is_empty());
    let hits = source.hits.clone();
    assert!(matches!(
        fetch(
            source.clone(),
            Request {
                workers: 2,
                ..request()
            },
            || Ok(hits.lock().unwrap().len() >= 4)
        )
        .await,
        Err(FetchError::Cancelled)
    ));
    assert_eq!(source.hits.lock().unwrap().len(), 4);
}

#[tokio::test]
async fn rejects_invalid_request_limits_before_network() {
    for request in [
        Request {
            lat: f64::NAN,
            ..request()
        },
        Request {
            lon: 181.,
            ..request()
        },
        Request {
            timeout: 0.,
            ..request()
        },
        Request {
            tolerance_seconds: f64::INFINITY,
            ..request()
        },
    ] {
        let source = fixture(1);
        assert!(matches!(
            fetch(source.clone(), request, || Ok(false)).await,
            Err(FetchError::Provider(_))
        ));
        assert!(source.hits.lock().unwrap().is_empty());
    }
}

#[tokio::test]
async fn truncates_nws_query_precision_to_microseconds() {
    let source = fixture(1);
    let request = Request {
        requested: request().requested.replace_nanosecond(123_456_789).unwrap(),
        ..request()
    };
    let result = fetch(source.clone(), request, || Ok(false)).await.unwrap();
    assert_eq!(result.requested.nanosecond(), 123_456_000);
    let hits = source.hits.lock().unwrap();
    assert!(hits[2].contains("00%3A45%3A00.123456Z"));
    assert!(hits[2].contains("02%3A45%3A00.123456Z"));
}

#[tokio::test]
#[ignore = "live NWS station collection and observation schemas; run explicitly"]
async fn fetches_live_nearby_observations() {
    let started = std::time::Instant::now();
    let result = fetch(
        SurfaceClient::default(),
        Request {
            lat: 35.22,
            lon: -97.44,
            requested: OffsetDateTime::now_utc(),
            max_stations: 3,
            workers: 3,
            ..request()
        },
        || Ok(false),
    )
    .await
    .unwrap();
    assert_eq!(result.queried.len(), 3);
    assert!(!result.matched.is_empty());
    assert!(result
        .matched
        .iter()
        .all(|(_, row)| (row.observed_at - result.requested).abs() <= time::Duration::hours(1)));
    println!(
        "Native NWS surface fetch: {:?}, {} matched, {} failed",
        started.elapsed(),
        result.matched.len(),
        result.failed.len()
    );
}

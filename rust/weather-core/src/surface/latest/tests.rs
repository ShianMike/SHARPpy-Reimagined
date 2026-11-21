use super::*;
use flate2::{write::GzEncoder, Compression};
use std::io::Write;
use std::sync::atomic::{AtomicUsize, Ordering};

const CSV: &str = r#"raw_text,station_id,observation_time,latitude,longitude,temp_c,dewpoint_c,wind_dir_degrees,wind_speed_kt,wind_gust_kt,sky_cover,sky_cover
METAR KAAA 200740Z 09010KT,KAAA,2026-11-20T07:40:00.000Z,30,-90,15,8,90,10,,,
"SPECI KAAA 200751Z 27020G28KT RMK TEST, TEST",kaaa,2026-11-20T08:51:02+01:00,35.25,-97.5,21.5,12,270,20,28,BKN,
METAR KAAA 200745Z 09010KT,KAAA,2026-11-20T07:45:00Z,32,-92,17,9,90,10,,,
METAR KAAA 200759Z 09010KT,KAAA,2026-11-20T07:59:00Z,91,-97,27,9,90,10,,,
METAR EGAA 200628Z VRB08KT,EGAA,2026-11-20T06:28:02Z,54.6,-6.2,,5,0,8,,,
METAR YBBB 200800Z 00000KT,YBBB,2026-11-20T08:00:08Z,-27.4,153.1,17,12,0,0,,,
METAR NZAA 200800Z,NZAA,2026-11-20T08:00:00Z,-37,181,5,3,,,,,
METAR CAAA 200800Z,CAAA,2026-11-20T08:00:00Z,NaN,-100,0,-5,,,,,
METAR LAAA,LAAA,invalid,41,12,12,9,,,,,
METAR ZOLD,ZOLD,2026-11-18T07:00:00Z,-90,180,-41,-45,0,12,,,
"#;

fn gzip(text: &str) -> Vec<u8> {
    let mut encoder = GzEncoder::new(Vec::new(), Compression::fast());
    encoder.write_all(text.as_bytes()).unwrap();
    encoder.finish().unwrap()
}

#[test]
fn keeps_the_latest_usable_report_per_station_and_its_actual_time() {
    let published_at = OffsetDateTime::parse("Fri, 20 Nov 2026 08:00:08 GMT", &Rfc2822).unwrap();
    let latest = parse(&gzip(CSV), published_at).unwrap();
    let snap = &latest.snapshot;
    assert_eq!(snap.valid_time, "2026-11-20T08:00:08Z");
    assert_eq!(
        snap.counts,
        SurfacePlotCounts {
            matched: 4,
            stale: 2,
            no_observation: 0,
            failed: 0,
            nearby: 4,
            queried: 4,
            unqueried: 0,
        }
    );
    let [egaa, kaaa, ybbb, zold] = snap.plots.as_slice() else {
        panic!("four distinct stations expected");
    };
    assert_eq!(egaa.id, "EGAA");
    assert_eq!(egaa.valid.as_deref(), Some("2026-11-20T06:28:02Z"));
    assert_eq!(egaa.offset_min, Some(-92.1));
    assert!(egaa.stale);
    assert_eq!(egaa.upper_text, "—");
    assert_eq!(egaa.wind_state, "direction-missing");
    assert_eq!(egaa.staff, None);

    assert_eq!(kaaa.id, "KAAA");
    assert_eq!((kaaa.latitude, kaaa.longitude), (35.25, -97.5));
    assert_eq!(kaaa.valid.as_deref(), Some("2026-11-20T07:51:02Z"));
    assert_eq!(kaaa.offset_min, Some(-9.1));
    assert_eq!(kaaa.temperature_c, Some(21.5));
    assert_eq!(kaaa.upper_text, "22");
    assert_eq!(kaaa.wind_state, "available");
    let staff = kaaa.staff.as_ref().unwrap();
    assert_eq!(staff.angle_deg, 180.0);
    assert_eq!(staff.barbs, 2);
    assert!(kaaa.description.contains("gusting 28 kt"));
    assert!(kaaa.description.contains("Aviation Weather Center METAR"));

    assert_eq!(ybbb.id, "YBBB");
    assert_eq!(ybbb.offset_min, Some(0.0));
    assert_eq!(ybbb.wind_state, "calm");
    assert_eq!(ybbb.staff, None);

    assert_eq!(zold.id, "ZOLD");
    assert_eq!(zold.valid.as_deref(), Some("2026-11-18T07:00:00Z"));
    assert_eq!(zold.offset_min, Some(-1440.0));
    assert!(zold.stale);
    assert_eq!(zold.wind_state, "available");
    assert_eq!(
        serde_json::from_str::<serde_json::Value>(&latest.bridge_json).unwrap(),
        serde_json::to_value(snap).unwrap()
    );
}

#[test]
fn rejects_a_truncated_or_empty_feed() {
    let mut truncated = gzip(CSV);
    truncated.truncate(truncated.len() - 8);
    assert!(parse(&truncated, OffsetDateTime::now_utc()).is_err());
    let header = CSV.lines().next().unwrap();
    assert!(parse(&gzip(header), OffsetDateTime::now_utc()).is_err());
}

async fn no_fetch() -> Result<LatestSurfacePlots, String> {
    panic!("a fetch during the cooldown");
}

#[tokio::test(start_paused = true)]
async fn cache_shares_the_download_and_enforces_refresh_failure_and_expiry_windows() {
    let cache = LatestCache::default();
    let attempts = AtomicUsize::new(0);
    let data = gzip(CSV);
    let ok = || async {
        attempts.fetch_add(1, Ordering::Relaxed);
        tokio::task::yield_now().await;
        parse(&data, OffsetDateTime::now_utc())
    };
    let fail = || async {
        attempts.fetch_add(1, Ordering::Relaxed);
        Err("provider unavailable".into())
    };
    let (first, concurrent) = tokio::join!(cache.get(ok()), cache.get(ok()));
    let first = first.unwrap();
    assert!(Arc::ptr_eq(&first, &concurrent.unwrap()));
    assert_eq!(attempts.load(Ordering::Relaxed), 1);

    let second = Duration::from_secs(1);
    tokio::time::advance(REFRESH_INTERVAL - second).await;
    assert!(Arc::ptr_eq(&first, &cache.get(no_fetch()).await.unwrap()));
    tokio::time::advance(second).await;
    assert!(Arc::ptr_eq(&first, &cache.get(fail()).await.unwrap()));
    assert_eq!(attempts.load(Ordering::Relaxed), 2);
    assert!(Arc::ptr_eq(&first, &cache.get(no_fetch()).await.unwrap()));

    tokio::time::advance(MAX_SNAPSHOT_AGE - REFRESH_INTERVAL - second).await;
    assert!(Arc::ptr_eq(&first, &cache.get(fail()).await.unwrap()));
    tokio::time::advance(second).await;
    assert_eq!(
        cache.get(no_fetch()).await.unwrap_err(),
        "provider unavailable"
    );
    assert_eq!(attempts.load(Ordering::Relaxed), 3);
    tokio::time::advance(REFRESH_INTERVAL - second).await;
    assert!(!Arc::ptr_eq(&first, &cache.get(ok()).await.unwrap()));
    assert_eq!(attempts.load(Ordering::Relaxed), 4);
}

#[tokio::test(start_paused = true)]
async fn cold_failures_back_off_and_old_provider_data_cannot_extend_the_cache() {
    let cache = LatestCache::default();
    let data = gzip(CSV);
    let old = OffsetDateTime::now_utc() - time::Duration::minutes(15);
    assert_eq!(
        cache.get(async { parse(&data, old) }).await.unwrap_err(),
        "AWC returned an expired METAR snapshot"
    );
    assert_eq!(
        cache.get(no_fetch()).await.unwrap_err(),
        "AWC returned an expired METAR snapshot"
    );
    tokio::time::advance(REFRESH_INTERVAL).await;
    assert!(cache
        .get(async { parse(&data, OffsetDateTime::now_utc()) })
        .await
        .is_ok());
}

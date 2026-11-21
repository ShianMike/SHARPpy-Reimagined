use std::cell::{Cell, RefCell};
use std::collections::VecDeque;
use std::future::ready;

use serde_json::json;
use time::format_description::well_known::Rfc3339;

use super::*;

const HEADERS: &str = "Title: Spotter Network\nRefresh: 1\nIconFile: 1, 8, 8, 4, 4, \"https://example.invalid/legend.png\"\nIcon: 0, 0, 0, 1, 1, \"Legend\"\n";

fn instant(value: &str) -> OffsetDateTime {
    OffsetDateTime::parse(value, &Rfc3339).unwrap()
}

fn record(coords: &str, timestamp: &str) -> String {
    popup_record(
        coords,
        &format!(
            r"Example Name, Somewhere\n{timestamp}\n555-0100\nhttps://example.invalid/profile"
        ),
    )
}

fn popup_record(coords: &str, popup: &str) -> String {
    format!("Object: {coords}\r\n  Icon: 0, 0, 0, 1, 1, \"{popup}\"\r\nEnd:\r\n")
}

#[test]
fn only_valid_fresh_utc_positions_are_serialized() {
    let now = instant("2026-11-20T12:00:00Z");
    let text = [
        HEADERS.to_owned(),
        record("44,-104", "2026-11-20 11:50:00 UTC"),
        record("41,-101", "2026-11-20 12:00:30 UTC"),
        record("40,-100", "2026-11-20 12:00:31 UTC"),
        record("39,-99", "2026-11-20 11:49:59 UTC"),
        record("NaN,-100", "2026-11-20 11:59:55 UTC"),
        record("42,-181", "2026-11-20 11:59:55 UTC"),
        record("90.1,-100", "2026-11-20 11:59:55 UTC"),
        record("42,-100,0", "2026-11-20 11:59:55 UTC"),
        record("42,-100", "2026-11-20 11:59:55 +00:00"),
        record("42,-100", "2026-11-20 11:59:55 UTC "),
        record("42,-100", r"missing\n2026-11-20 11:59:55 UTC"),
        record("42,-100", "2026-11-20 11:59:60 UTC"),
        record("42,-100", "2026-11-31 11:59:55 UTC"),
    ]
    .concat();
    let mut cache = Cache {
        checked_at: Some(now),
        general: Feed {
            positions: placefile::parse(&text, now, Source::General).unwrap(),
            ..Feed::default()
        },
        ..Cache::default()
    };
    assert_eq!(
        serde_json::to_value(cache.snapshot(now).unwrap()).unwrap(),
        json!({
            "checkedAt": "2026-11-20T12:00:00Z",
            "streamersAvailable": false,
            "positions": [
                {"latitude": 41.0, "longitude": -101.0, "updatedAt": "2026-11-20T12:00:30Z", "chaseCam": false},
                {"latitude": 44.0, "longitude": -104.0, "updatedAt": "2026-11-20T11:50:00Z", "chaseCam": false}
            ]
        })
    );
}

#[test]
fn malformed_display_text_does_not_reject_other_live_positions() {
    let now = instant("2026-11-20T12:00:00Z");
    let text = [
        HEADERS.to_owned(),
        popup_record(
            "44,-104",
            r"Jos? Example\n2026-11-20 12:00:00 UTC\nSTATIONARY",
        ),
        popup_record("43,-105", r"Valid Name\n2026-11-20 11:59:00 UTC"),
        popup_record("42?,-105", r"Bad Position\n2026-11-20 11:59:00 UTC"),
        popup_record("41,-105", r"Expired Name\n2026-11-20 11:49:00 UTC"),
    ]
    .concat();
    let body = text
        .bytes()
        .map(|byte| if byte == b'?' { 0xe9 } else { byte })
        .collect::<Vec<_>>();
    assert!(std::str::from_utf8(&body).is_err());
    let positions = parse_feed(&body, now, Source::General).unwrap();
    assert_eq!(positions.len(), 2);
    assert_eq!(positions[0].latitude, 44.0);
    assert_eq!(positions[0].display_name, None);
    assert_eq!(positions[0].motion, Some(SpotterMotion::Stationary));
    assert_eq!(positions[1].display_name.as_deref(), Some("Valid Name"));

    let video = parse_feed(&body, now, Source::Video).unwrap();
    assert_eq!(video.len(), 2);
    assert!(video.iter().all(|position| position.chase_cam));
}

#[test]
fn serializes_only_the_public_name_and_typed_motion() {
    let now = instant("2026-11-20T12:00:00Z");
    let text = [
        HEADERS.to_owned(),
        popup_record(
            "44,-104",
            r"  Example É. O’Name  \n2026-11-20 12:00:00 UTC\nSTATIONARY\nPhone: 555-0100\nEmail: example@example.invalid\nHam: TEST123\nNotes: https://example.invalid/profile",
        )
        .replace("\r\nEnd:", "\r\n  Text: 0, 0, 1, \"Ignored Label\"\r\nEnd:"),
        popup_record(
            "43,-105",
            r"Example Name\n2026-11-20 11:59:00 UTC\nHeading: NNW (322)",
        ),
    ]
    .concat();
    let mut cache = Cache {
        checked_at: Some(now),
        general: Feed {
            positions: placefile::parse(&text, now, Source::General).unwrap(),
            ..Feed::default()
        },
        ..Cache::default()
    };
    assert_eq!(
        serde_json::to_value(cache.snapshot(now).unwrap().positions).unwrap(),
        json!([
            {
                "latitude": 44.0, "longitude": -104.0, "updatedAt": "2026-11-20T12:00:00Z",
                "chaseCam": false, "displayName": "Example É. O’Name", "motion": {"kind": "stationary"}
            },
            {
                "latitude": 43.0, "longitude": -105.0, "updatedAt": "2026-11-20T11:59:00Z",
                "chaseCam": false, "displayName": "Example Name", "motion": {"kind": "heading", "degrees": 322}
            }
        ])
    );
}

#[test]
fn accepts_decorative_icons_and_rejects_ambiguous_popups() {
    let now = instant("2026-11-20T12:00:00Z");
    let decorated = concat!(
        "Object: 44,-104\r\n",
        "  Icon: 0, 0, 0, 1, 2\r\n",
        "  Text: 0, 0, 1, \"Ignored Label\"\r\n",
        "  Icon: 0, 0, 0, 1, 1, \"Example Name (N0XYZ)\\n2026-11-20 12:00:00 UTC\\nHeading: W (271)\"\r\n",
        "  Icon: 0, 0, 0, 1, 3, \"Decorative Text\"\r\n",
        "End:\r\n",
    );
    let ambiguous = concat!(
        "Object: 43,-105\r\n",
        "  Icon: 0, 0, 0, 1, 1, \"Example Name\\n2026-11-20 12:00:00 UTC\\nSTATIONARY\"\r\n",
        "  Icon: 0, 0, 0, 1, 1, \"Example Alias\\n2026-11-20 11:59:00 UTC\\nHeading: ESE (123)\"\r\n",
        "End:\r\n",
    );
    let text = format!("{HEADERS}{decorated}{ambiguous}");
    let positions = placefile::parse(&text, now, Source::General).unwrap();
    assert_eq!(positions.len(), 1);
    assert_eq!(
        positions[0].display_name.as_deref(),
        Some("Example Name (N0XYZ)")
    );
    assert_eq!(
        positions[0].motion,
        Some(SpotterMotion::Heading { degrees: 271 })
    );
}

#[test]
fn omits_unsafe_names_and_keeps_the_position() {
    let now = instant("2026-11-20T12:00:00Z");
    let long_contact = format!("{} @example.invalid", "A".repeat(81));
    for name in [
        "",
        "   ",
        "Example\u{0007}Name",
        "Example\tName",
        "Example\u{2028}Name",
        "Example\u{202e}Name",
        "Example\u{2067}Name",
        "Example\u{200f}Name",
        "Example\u{200b}Name",
        "Example, Somewhere",
        "Example (TEST)",
        "Example (12345)",
        "Example (A1)",
        "Example (EXAMPLE12)",
        "Example (Ｔ0XYZ)",
        "Example (N0XYZ/MORE)",
        "Example (N0XYZ/M/P)",
        "Example (example.invalid)",
        "Example (N0XYZ) (W1A)",
        "Example, Alias (N0XYZ)",
        "Example\u{202e}Name (N0XYZ)",
        "example.invalid (N0XYZ)",
        "Example (N0XYZ) https://example.invalid",
        "Example|Alias",
        r"Example\Alias",
        "Example \"Alias\"",
        "<Example>",
        "example.invalid",
        "www.example.invalid",
        "https://example.invalid",
        "example＠example.invalid",
        "example@example.invalid",
        "Phone: 555-0100",
        "555-0100",
        "TEST123",
        &long_contact,
    ] {
        let record = popup_record(
            "44,-104",
            &format!(r"{name}\n2026-11-20 12:00:00 UTC\nHeading: ESE (123)"),
        );
        let positions =
            placefile::parse(&format!("{HEADERS}{record}"), now, Source::General).unwrap();
        assert_eq!(positions.len(), 1, "{name:?}");
        assert_eq!(positions[0].display_name, None, "{name:?}");
        assert_eq!(
            positions[0].motion,
            Some(SpotterMotion::Heading { degrees: 123 })
        );
    }
}

#[test]
fn preserves_a_valid_callsign_after_the_primary_name() {
    let now = instant("2026-11-20T12:00:00Z");
    for (raw, expected) in [
        ("  Example Name (N0XYZ)  ", "Example Name (N0XYZ)"),
        ("Example Name (n0xy2)", "Example Name (n0xy2)"),
        ("Example Name  (N0XYZ/P)", "Example Name (N0XYZ/P)"),
    ] {
        let record = popup_record(
            "44,-104",
            &format!(r"{raw}\n2026-11-20 12:00:00 UTC\nSTATIONARY"),
        );
        let text = format!("{HEADERS}{record}");
        let general = placefile::parse(&text, now, Source::General).unwrap();
        assert_eq!(general[0].display_name.as_deref(), Some(expected));
        let video = placefile::parse(&text, now, Source::Video).unwrap();
        assert_eq!(video[0].display_name, None);
    }
}

#[test]
fn bounds_names_by_utf16_without_splitting_a_character() {
    let now = instant("2026-11-20T12:00:00Z");
    for expected in [format!("{}𐐀", "A".repeat(78)), "A".repeat(79)] {
        let overflow = if expected.ends_with('𐐀') {
            "B"
        } else {
            "𐐀"
        };
        let record = popup_record(
            "44,-104",
            &format!(r"  {expected}{overflow}  \n2026-11-20 12:00:00 UTC"),
        );
        let positions =
            placefile::parse(&format!("{HEADERS}{record}"), now, Source::General).unwrap();
        assert_eq!(
            positions[0].display_name.as_deref(),
            Some(expected.as_str())
        );
    }
    let prefix = format!("{}𐐀", "A".repeat(70));
    let record = popup_record(
        "44,-104",
        &format!(r"{prefix}𐐀 (N0XYZ)\n2026-11-20 12:00:00 UTC"),
    );
    let positions = placefile::parse(&format!("{HEADERS}{record}"), now, Source::General).unwrap();
    let expected = format!("{prefix} (N0XYZ)");
    assert_eq!(expected.encode_utf16().count(), 80);
    assert_eq!(
        positions[0].display_name.as_deref(),
        Some(expected.as_str())
    );
}

#[test]
fn parses_only_known_motion_in_the_motion_line() {
    let now = instant("2026-11-20T12:00:00Z");
    for (line, expected) in [
        ("STATIONARY", Some(SpotterMotion::Stationary)),
        (
            "Heading: N (0)",
            Some(SpotterMotion::Heading { degrees: 0 }),
        ),
        (
            "Heading: N (360)",
            Some(SpotterMotion::Heading { degrees: 0 }),
        ),
        (
            "Heading: NNW (359)",
            Some(SpotterMotion::Heading { degrees: 359 }),
        ),
        (
            "Heading: W (271)",
            Some(SpotterMotion::Heading { degrees: 271 }),
        ),
        (
            "Heading: ESE (123)",
            Some(SpotterMotion::Heading { degrees: 123 }),
        ),
        ("", None),
        ("stationary", None),
        ("STATIONARY outdoors", None),
        ("Heading: W", None),
        ("Heading: WEST (271)", None),
        ("Heading: W (+271)", None),
        ("Heading: W (-1)", None),
        ("Heading: W (271.0)", None),
        ("Heading: W (361)", None),
        ("Heading: W (271) extra", None),
        ("Notes: Heading: W (271)", None),
    ] {
        let record = popup_record(
            "44,-104",
            &format!(r"Example Name\n2026-11-20 12:00:00 UTC\n{line}\nNotes:\nSTATIONARY"),
        );
        let text = format!("{HEADERS}{record}");
        for source in [Source::General, Source::Video] {
            let positions = placefile::parse(&text, now, source).unwrap();
            assert_eq!(positions[0].motion, expected, "{line:?}");
            assert_eq!(
                positions[0].display_name.is_some(),
                source == Source::General
            );
        }
    }
}

#[test]
fn distinguishes_empty_feeds_from_invalid_responses() {
    let now = instant("2026-11-20T12:00:00Z");
    let stale = format!("{HEADERS}{}", record("44,-104", "2026-11-20 10:00:00 UTC"));
    for valid in [HEADERS, stale.as_str()] {
        assert!(placefile::parse(valid, now, Source::General)
            .unwrap()
            .is_empty());
    }
    let malformed = format!("{HEADERS}{}", record("44,-104", "unknown"));
    let truncated = format!("{HEADERS}Object: 44,-104\n");
    let unexpected = format!("{HEADERS}<html>Service unavailable</html>\n");
    for invalid in [
        "",
        "<html>Not found</html>",
        &malformed,
        &truncated,
        &unexpected,
    ] {
        assert!(placefile::parse(invalid, now, Source::General).is_none());
    }
}

#[test]
fn unions_both_feeds_and_marks_exact_duplicates() {
    let now = instant("2026-11-20T12:00:00Z");
    let general = [
        HEADERS.to_owned(),
        record("-0.0,-98.5", "2026-11-20 11:59:00 UTC"),
        popup_record(
            "0,-98.500",
            r"Example Name\n2026-11-20 11:59:00 UTC\nHeading: NNW (322)",
        ),
        record("0,-98.5", "2026-11-20 11:58:00 UTC"),
    ]
    .concat();
    let video = [
        HEADERS.to_owned(),
        popup_record(
            "0,-98.5",
            r"Streamer Alias\n2026-11-20 11:59:00 UTC\nSTATIONARY",
        ),
        popup_record(
            "33,-101",
            r"Streamer Alias\n2026-11-20 12:00:00 UTC\nHeading: W (271)",
        ),
    ]
    .concat();
    let mut cache = Cache {
        checked_at: Some(now),
        streamers_available: true,
        general: Feed {
            positions: placefile::parse(&general, now, Source::General).unwrap(),
            ..Feed::default()
        },
        video: Feed {
            positions: placefile::parse(&video, now, Source::Video).unwrap(),
            ..Feed::default()
        },
        ..Cache::default()
    };
    let snapshot = cache.snapshot(now).unwrap();
    let positions: Vec<_> = snapshot
        .positions
        .iter()
        .map(|p| (p.latitude, p.longitude, p.updated_at.as_str(), p.chase_cam))
        .collect();
    assert_eq!(
        positions,
        [
            (33.0, -101.0, "2026-11-20T12:00:00Z", true),
            (0.0, -98.5, "2026-11-20T11:59:00Z", true),
            (0.0, -98.5, "2026-11-20T11:58:00Z", false),
        ]
    );
    assert_eq!(snapshot.positions[0].display_name, None);
    assert_eq!(
        snapshot.positions[0].motion,
        Some(SpotterMotion::Heading { degrees: 271 })
    );
    assert_eq!(
        snapshot.positions[1].display_name.as_deref(),
        Some("Example Name")
    );
    assert_eq!(
        snapshot.positions[1].motion,
        Some(SpotterMotion::Heading { degrees: 322 })
    );
    assert_eq!(snapshot.positions[2].display_name, None);
    assert_eq!(snapshot.positions[2].motion, None);
}

#[test]
fn cap_keeps_the_newest_unique_positions() {
    let now = instant("2026-11-20T12:00:00Z");
    let mut text = HEADERS.to_owned();
    for index in 0..MAX_POSITIONS {
        let coords = format!("0,{}.{}", index / 100, index % 100 + 100);
        text.push_str(&record(&coords, "2026-11-20 11:59:00 UTC"));
    }
    text.push_str(&record("33,-101", "2026-11-20 12:00:00 UTC"));
    let positions = placefile::parse(&text, now, Source::General).unwrap();
    assert_eq!(positions.len(), MAX_POSITIONS);
    assert_eq!(positions[0].latitude, 33.0);
}

#[test]
fn conditional_requests_prefer_etag_then_last_modified() {
    let etag = HeaderValue::from_static("\"version-one\"");
    let modified = HeaderValue::from_static("Fri, 20 Nov 2026 11:59:00 GMT");
    let mut headers = HeaderMap::new();
    headers.insert(ETAG, etag.clone());
    headers.insert(LAST_MODIFIED, modified.clone());
    assert_eq!(response_validator(&headers), Some((IF_NONE_MATCH, etag)));
    headers.remove(ETAG);
    assert_eq!(
        response_validator(&headers),
        Some((IF_MODIFIED_SINCE, modified))
    );
}

#[tokio::test(start_paused = true)]
async fn failed_checks_preserve_primary_timestamp_and_expire_positions_on_read() {
    let initial = instant("2026-11-20T12:00:00Z");
    let clock_started = Instant::now();
    let now = || initial + clock_started.elapsed();
    let cache = Mutex::default();
    let position = Position {
        latitude: 43.0,
        longitude: -98.0,
        updated_at: initial - time::Duration::minutes(8),
        chase_cam: false,
        display_name: Some("Example Name".to_owned()),
        motion: Some(SpotterMotion::Stationary),
    };
    let general_etag = (IF_NONE_MATCH, HeaderValue::from_static("\"general\""));
    let video_etag = (IF_NONE_MATCH, HeaderValue::from_static("\"video\""));
    let responses = RefCell::new(VecDeque::from([
        (
            Source::General,
            None,
            Some(FeedUpdate::Replaced(Feed {
                positions: vec![position.clone()],
                validator: Some(general_etag.clone()),
            })),
        ),
        (
            Source::Video,
            None,
            Some(FeedUpdate::Replaced(Feed {
                positions: vec![Position {
                    chase_cam: true,
                    display_name: None,
                    motion: None,
                    ..position
                }],
                validator: Some(video_etag.clone()),
            })),
        ),
        (
            Source::General,
            Some(general_etag.clone()),
            Some(FeedUpdate::NotModified(None)),
        ),
        (Source::Video, Some(video_etag), None),
        (Source::General, Some(general_etag.clone()), None),
        (Source::General, Some(general_etag), None),
    ]));
    let fetch = |source, validator| {
        let (expected_source, expected_validator, update) =
            responses.borrow_mut().pop_front().unwrap();
        assert_eq!((source, validator), (expected_source, expected_validator));
        ready(update)
    };

    let first = read_positions(&cache, now, fetch).await.unwrap();
    assert_eq!(first.checked_at, "2026-11-20T12:00:00Z");
    assert!(first.streamers_available && first.positions[0].chase_cam);

    tokio::time::advance(POLL_INTERVAL).await;
    let partial = read_positions(&cache, now, fetch).await.unwrap();
    assert_eq!(partial.checked_at, "2026-11-20T12:01:00Z");
    assert!(!partial.streamers_available && !partial.positions[0].chase_cam);
    assert_eq!(
        partial.positions[0].display_name.as_deref(),
        Some("Example Name")
    );
    assert_eq!(partial.positions[0].motion, Some(SpotterMotion::Stationary));

    tokio::time::advance(POLL_INTERVAL).await;
    let fallback = read_positions(&cache, now, fetch).await.unwrap();
    assert_eq!(fallback.checked_at, partial.checked_at);
    assert_eq!(fallback.positions.len(), 1);

    tokio::time::advance(Duration::from_secs(1)).await;
    let expired = read_positions(&cache, now, fetch).await.unwrap();
    assert_eq!(expired.checked_at, partial.checked_at);
    assert!(expired.positions.is_empty());
    assert!(cache.lock().await.general.positions.is_empty());
    assert!(cache.lock().await.video.positions.is_empty());

    tokio::time::advance(Duration::from_secs(9 * 60)).await;
    assert_eq!(
        read_positions(&cache, now, fetch).await.unwrap_err(),
        UNAVAILABLE
    );
    assert_eq!(cache.lock().await.checked_at, Some(initial + POLL_INTERVAL));
    assert!(responses.borrow().is_empty());
}

#[tokio::test(start_paused = true)]
async fn concurrent_calls_share_poll_and_wait_for_the_result() {
    let cache = Mutex::default();
    let now = || instant("2026-11-20T12:00:00Z");
    let requests = Cell::new(0);
    let fetch = |_, _| async {
        requests.set(requests.get() + 1);
        tokio::time::sleep(Duration::from_secs(1)).await;
        Some(FeedUpdate::Replaced(Feed::default()))
    };
    let (a, b, c) = tokio::join!(
        read_positions(&cache, now, fetch),
        read_positions(&cache, now, fetch),
        read_positions(&cache, now, fetch)
    );
    for result in [a, b, c] {
        let snapshot = result.unwrap();
        assert_eq!(snapshot.checked_at, "2026-11-20T12:00:00Z");
        assert!(snapshot.streamers_available);
        assert!(snapshot.positions.is_empty());
    }
    assert_eq!(requests.get(), 2);

    // The secondary request started one second after the primary.
    tokio::time::advance(Duration::from_secs(58)).await;
    read_positions(&cache, now, fetch).await.unwrap();
    assert_eq!(requests.get(), 2);
    tokio::time::advance(Duration::from_secs(1)).await;
    read_positions(&cache, now, fetch).await.unwrap();
    assert_eq!(requests.get(), 4);
}

#[tokio::test(start_paused = true)]
async fn cold_failure_retries_only_after_the_cadence() {
    let cache = Mutex::default();
    let now = || instant("2026-11-20T12:00:00Z");
    let requests = Cell::new(0);
    let fetch = |source, _| {
        assert_eq!(source, Source::General);
        requests.set(requests.get() + 1);
        ready(None)
    };
    for _ in 0..2 {
        assert_eq!(
            read_positions(&cache, now, fetch).await.unwrap_err(),
            UNAVAILABLE
        );
    }
    assert_eq!(requests.get(), 1);
    tokio::time::advance(POLL_INTERVAL).await;
    assert_eq!(
        read_positions(&cache, now, fetch).await.unwrap_err(),
        UNAVAILABLE
    );
    assert_eq!(requests.get(), 2);
}

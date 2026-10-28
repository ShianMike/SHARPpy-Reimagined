use super::*;
use serde_json::json;
use time::format_description::well_known::Rfc3339;

fn now() -> OffsetDateTime {
    OffsetDateTime::parse("2026-10-04T21:00:00Z", &Rfc3339).unwrap()
}

fn ring() -> Value {
    json!([
        [-98.0, 34.0],
        [-96.0, 34.0],
        [-96.0, 36.0],
        [-98.0, 36.0],
        [-98.0, 34.0]
    ])
}

fn feature(id: &str, sig: &str) -> Value {
    json!({"properties": {
        "cap_id": id, "sig":sig,"phenom":"TO",
        "prod_type": if sig == "W" { "Tornado Warning" } else { "Tornado Watch" },
        "issuance":"2026-10-04T15:45:00-05:00", "expiration":"2026-10-04T22:00:00Z",
        "ends":" ", "onset":"2026-10-05T00:00:00Z", "wfo":"KOUN",
        "url":"https://api.weather.gov/alerts/example"
    }, "geometry":{"type":"Polygon", "coordinates":[ring()]}})
}

fn colours(layer: usize) -> parse::Colours {
    parse::colours(
        json!({"drawingInfo":{"renderer":{"uniqueValueGroups":[{"classes":[
            {"symbol":{"color":[255,0,0,255],"outline":{"color":[255,0,0,255]}},
             "values":[if layer==0 {json!(["TO","W"])} else {json!(["Tornado Warning"])}]},
            {"symbol":{"color":[255,255,0,255],"outline":{"color":[255,255,0,255]}},
             "values":[if layer==0 {json!(["TO","A"])} else {json!(["Tornado Watch"])}]}
        ]}]}}}),
        layer,
    )
    .unwrap()
}

fn snapshot(storms: Vec<Value>, zones: Vec<Value>) -> Result<(Snapshot, bool), String> {
    parse::snapshot(
        now(),
        now(),
        json!({"features": storms}),
        json!({"features": zones}),
        &colours(0),
        &colours(1),
    )
}

#[test]
fn prefers_precise_polygons_and_converts_times_without_using_future_watch_onset() {
    let mut zone = feature("warning", "W");
    zone["geometry"]["coordinates"][0][1] = json!([-95, 34]);
    let mut expired = feature("expired", "W");
    expired["properties"]["expiration"] = json!("2026-10-04T21:00:00Z");
    let (result, confirm) = snapshot(
        vec![feature("warning", "W")],
        vec![
            zone,
            feature("watch", "A"),
            expired,
            feature("advisory", "Y"),
        ],
    )
    .unwrap();
    assert!(!confirm);
    assert_eq!(result.fetched_at, "2026-10-04T21:00:00Z");
    assert_eq!(result.alerts.len(), 2);
    assert_eq!(
        serde_json::to_value(&result.alerts[0].polygons).unwrap(),
        json!([[ring()]])
    );
    assert_eq!(result.alerts[0].issued, "2026-10-04T20:45:00Z");
    assert_eq!(
        (result.alerts[0].kind, result.alerts[0].colour.as_str()),
        ("warning", "#ff0000")
    );
    assert_eq!(
        (result.alerts[1].kind, result.alerts[1].colour.as_str()),
        ("watch", "#ffff00")
    );
}

#[test]
fn retains_all_multipolygon_parts_and_zone_records() {
    let mut part = feature("watch", "A");
    part["geometry"] = json!({"type":"MultiPolygon", "coordinates":[[ring()],[ring()]]});
    let (result, _) = snapshot(vec![], vec![part, feature("watch", "A")]).unwrap();
    assert_eq!(result.alerts.len(), 1);
    assert_eq!(result.alerts[0].polygons.len(), 3);
}

#[test]
fn rejects_malformed_geometry_links_and_metadata_and_filters_inactive_alerts() {
    for (pointer, value) in [
        ("/geometry/coordinates/0/1", json!([181, 34])),
        ("/geometry/coordinates/0/4", json!([-97, 34])),
        ("/geometry/coordinates", json!([])),
        ("/geometry/type", json!("Point")),
        (
            "/properties/url",
            json!("https://api.weather.gov.evil.example/alerts"),
        ),
        (
            "/properties/url",
            json!("https://user:pass@api.weather.gov/alerts"),
        ),
        ("/properties/url", json!("http://api.weather.gov/alerts")),
        (
            "/properties/url",
            json!("https://api.weather.gov:444/alerts"),
        ),
        ("/properties/issuance", json!("2026-10-04T20:45:00")),
        ("/properties/wfo", json!("")),
    ] {
        let mut item = feature("warning", "W");
        *item.pointer_mut(pointer).unwrap() = value;
        assert!(snapshot(vec![item], vec![]).is_err(), "{pointer}");
    }
    for (key, value) in [
        ("ends", "2026-10-04T20:59:00Z"),
        ("issuance", "2026-10-04T21:01:00Z"),
    ] {
        let mut item = feature("warning", "W");
        item["properties"][key] = json!(value);
        let (result, confirm) = snapshot(vec![item], vec![]).unwrap();
        assert!(result.alerts.is_empty() && confirm);
    }
}

#[test]
fn rejects_incomplete_collections_and_excessive_geometry() {
    let incomplete = json!({"features":[],"exceededTransferLimit":true});
    assert!(parse::snapshot(
        now(),
        now(),
        incomplete,
        json!({"features":[]}),
        &colours(0),
        &colours(1)
    )
    .unwrap_err()
    .contains("incomplete"));
    let mut item = feature("warning", "W");
    item["geometry"]["coordinates"] = json!([vec![[-98, 34]; 250_001]]);
    assert!(snapshot(vec![item], vec![]).unwrap_err().contains("limits"));
    let too_many = json!({"features":vec![json!({"properties":{"sig":"Y"}});4001]});
    assert!(parse::snapshot(
        now(),
        now(),
        too_many,
        json!({"features":[]}),
        &colours(0),
        &colours(1)
    )
    .is_err());
}

#[test]
fn validates_colour_bytes_and_uses_outline_for_storms() {
    for rgb in [
        json!([256, 0, 0]),
        json!([-1, 0, 0]),
        json!([1.5, 0, 0]),
        json!([true, 0, 0]),
        json!([1, 2]),
    ] {
        let style = json!({"drawingInfo":{"renderer":{"uniqueValueGroups":[{"classes":[{"symbol":{"outline":{"color":rgb}},"values":[["TO","W"]]}]}]}}});
        assert!(parse::colours(style, 0).is_err());
    }
    assert!(parse::colours(json!({}), 0).is_err());
}

#[test]
fn cap_check_prevents_false_all_clear_and_rejects_pagination() {
    let active = json!({"features":[{"properties":{"event":"Tornado Watch","status":"Actual","messageType":"Alert","expires":"2026-10-04T22:00:00Z"}}]});
    assert!(parse::confirm_empty(active.clone(), now())
        .unwrap_err()
        .contains("temporarily incomplete"));
    for (key, value) in [
        ("status", "Test"),
        ("messageType", "Cancel"),
        ("expires", "2026-10-04T21:00:00Z"),
    ] {
        let mut payload = active.clone();
        payload["features"][0]["properties"][key] = json!(value);
        assert!(parse::confirm_empty(payload, now()).is_ok());
    }
    assert!(parse::confirm_empty(
        json!({"features":[{"properties":{"event":"Frost Advisory"}}]}),
        now()
    )
    .is_ok());
    assert!(parse::confirm_empty(
        json!({"features":[],"pagination":{"next":"https://api.weather.gov/next"}}),
        now()
    )
    .is_err());
    assert!(parse::confirm_empty(json!({}), now()).is_err());
    assert!(snapshot(vec![], vec![]).unwrap().1);
    assert!(snapshot(vec![feature("warning", "W")], vec![]).unwrap().1);
}

#[tokio::test]
async fn uncached_history_is_rejected_before_the_network() {
    let client = AlertsClient::default();
    for valid in [
        now() - time::Duration::minutes(1),
        now() + time::Duration::seconds(6),
    ] {
        assert_eq!(
            client.fetch(valid, now()).await.unwrap_err(),
            HISTORY_UNAVAILABLE
        );
    }
}

#[tokio::test]
#[ignore = "live NWS warning/watch schemas; run explicitly"]
async fn live_nws_snapshot_has_consistent_geometry_and_times() {
    let now = OffsetDateTime::now_utc();
    let started = std::time::Instant::now();
    let result = AlertsClient::default().fetch(now, now).await.unwrap();
    assert_eq!(result.valid_time, result.fetched_at);
    for alert in &result.alerts {
        assert!(!alert.polygons.is_empty());
        assert!(alert.issued <= result.fetched_at && result.fetched_at < alert.expires);
    }
    println!(
        "Native NWS snapshot: alerts={}, elapsed={:?}",
        result.alerts.len(),
        started.elapsed()
    );
}

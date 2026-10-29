use super::*;

#[test]
fn level_two_tilts_keep_the_bridge_contract() {
    for product in ["reflectivity", "correlation-coefficient"] {
        let launcher = FakeLauncher::with(vec![script(move |mut io| async move {
            let request = io.expect("radar.tilts").await.unwrap();
            assert_eq!(
                request["params"],
                serde_json::json!({"site_id": "KTLX", "product": product, "valid_time": "2026-10-17T08:00:00Z"})
            );
            let mut result = fixture("bridge/map/radar-tilts.result.json");
            result["product"] = serde_json::json!(product);
            io.reply(&request["id"], result).await;
            io.expect("shutdown").await;
            0
        })]);
        let (_app, webview) = window(launcher);
        for args in [
            serde_json::json!({"site": "../bad", "product": "velocity"}),
            serde_json::json!({"site": "KTLX", "product": "echo-tops"}),
            serde_json::json!({"site": "KTLX", "product": "velocity", "validTime": "bad"}),
        ] {
            let error: BackendError =
                parse(invoke_args(&webview, "get_radar_tilts", args).unwrap_err());
            assert_eq!(error.code, ErrorCode::InvalidArgument);
        }
        let mut expected = fixture("public/map/radar-tilts.json");
        expected["product"] = serde_json::json!(product);
        assert_eq!(invoke_args(&webview, "get_radar_tilts", serde_json::json!({"site": "KTLX", "product": product, "validTime": "2026-10-17T08:00:00Z"})).unwrap(), expected);
    }
    let mut invalid = fixture("bridge/map/radar-tilts.result.json");
    invalid["tilts"][0]["tilt"] = serde_json::json!("0");
    assert!(
        serde_json::from_value::<sharppy_reimagined_lib::dto::radar::RadarTilts>(invalid)
            .unwrap()
            .validate()
            .is_err()
    );
}

fn no_radar_bridge_launcher() -> Arc<FakeLauncher> {
    FakeLauncher::with(vec![script(|mut io| async move {
        while let Some(request) = io.next_request().await {
            match request["method"].as_str().unwrap() {
                "hello" => io.reply(&request["id"], common::hello_result()).await,
                "shutdown" => return 0,
                method => panic!("velocity inventory must not use Python: {method}"),
            }
        }
        0
    })])
}

#[test]
fn velocity_tilts_validate_site_and_history_before_fetching() {
    let (_app, webview) = window(no_radar_bridge_launcher());
    for (args, expected) in [
        (
            serde_json::json!({"site": "ZZZZ", "product": "velocity"}),
            ErrorCode::InvalidArgument,
        ),
        (
            serde_json::json!({"site": "KTLX", "product": "velocity", "validTime": "2000-01-01T00:00:00Z"}),
            ErrorCode::OverlayFetchFailed,
        ),
    ] {
        let error: BackendError =
            parse(invoke_args(&webview, "get_radar_tilts", args).unwrap_err());
        assert_eq!(error.code, expected);
        assert_eq!(
            error.context.unwrap().provider.as_deref(),
            Some("noaa-radar")
        );
    }
}

#[test]
fn australian_radar_uses_native_capabilities_and_rejects_unsupported_requests() {
    let (_app, webview) = window(no_radar_bridge_launcher());
    let catalogue = invoke_args(&webview, "get_radar_sites", serde_json::json!({})).unwrap();
    let broadmeadows = catalogue["sites"]
        .as_array()
        .unwrap()
        .iter()
        .find(|site| site["id"] == "ABRD")
        .unwrap();
    assert_eq!(broadmeadows["name"], "Broadmeadows");
    assert_eq!(broadmeadows["country"], "AU");
    assert_eq!(
        broadmeadows["products"],
        serde_json::json!(["reflectivity", "velocity"])
    );
    for (command, args) in [
        (
            "get_radar_site_frame",
            serde_json::json!({"site": "ABRD", "product": "correlation-coefficient"}),
        ),
        (
            "get_radar_site_frame",
            serde_json::json!({"site": "ABRD", "product": "reflectivity", "tilt": "3"}),
        ),
        (
            "get_radar_site_frame",
            serde_json::json!({"site": "ABRD", "validTime": "2000-01-01T00:00:00Z"}),
        ),
        (
            "get_radar_tilts",
            serde_json::json!({"site": "ABRD", "product": "velocity"}),
        ),
    ] {
        let error: BackendError = parse(invoke_args(&webview, command, args).unwrap_err());
        assert_eq!(error.code, ErrorCode::InvalidArgument);
    }
}

#[test]
#[ignore = "live NOAA directory fetch; run explicitly with --ignored --nocapture"]
fn velocity_tilts_fetch_live_without_python_inventory() {
    let (_app, webview) = window(no_radar_bridge_launcher());
    let args = serde_json::json!({"site": "ktlx", "product": "velocity"});
    let start = std::time::Instant::now();
    let result = invoke_args(&webview, "get_radar_tilts", args.clone()).unwrap();
    let fetched = start.elapsed();
    assert_eq!(result["siteId"], "KTLX");
    assert_eq!(result["product"], "velocity");
    let tilts = result["tilts"].as_array().unwrap();
    assert_eq!(tilts.len(), 7);
    assert!(
        tilts.iter().any(|cut| cut["status"] == "available"),
        "{result}"
    );
    let start = std::time::Instant::now();
    let cached = invoke_args(&webview, "get_radar_tilts", args).unwrap();
    assert_eq!(cached["tilts"], result["tilts"]);
    println!(
        "Native velocity inventory: fetched={fetched:?}, cached={:?}, {result}",
        start.elapsed()
    );
}

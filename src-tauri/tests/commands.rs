//! The three commands through IPC on the `tauri::test` mock runtime, with the
//! fake launcher behind the supervisor. No frontend build is needed
//! (`mock_context(noop_assets())`). The capability itself is checked by
//! tauri-build at compile time and by the real app (DESIGN-bridge B8).

mod common;
#[path = "commands/radar.rs"]
mod radar;

use std::sync::Arc;

use serde::de::DeserializeOwned;
use serde_json::Value;
use tauri::ipc::{CallbackFn, InvokeBody};
use tauri::test::{get_ipc_response, mock_builder, mock_context, noop_assets, INVOKE_KEY};
use tauri::webview::InvokeRequest;
use tauri::{WebviewWindow, WebviewWindowBuilder};

use common::{fixture, script, FakeLauncher, NotConfigured, RecordingSink};
use sharppy_reimagined_lib::backend::convert;
use sharppy_reimagined_lib::backend::router::Router;
use sharppy_reimagined_lib::dto::health::{
    AppInfo, BackendHealth, BridgeStatus, BuildProfile, ScientificBackendStatus,
};
use sharppy_reimagined_lib::dto::stations::StationCatalog;
use sharppy_reimagined_lib::error::{BackendError, ErrorCode};
use sharppy_reimagined_lib::python::launcher::ProcessLauncher;
use sharppy_reimagined_lib::python::supervisor::{Supervisor, SupervisorConfig};
use sharppy_reimagined_lib::state::AppState;

fn app_info() -> AppInfo {
    AppInfo {
        name: "SHARPpy Reimagined".into(),
        version: env!("CARGO_PKG_VERSION").into(),
        tauri_version: tauri::VERSION.into(),
        build_profile: BuildProfile::Debug,
        log_file: "C:\\logs\\sharppy-reimagined.log".into(),
    }
}

fn window(
    launcher: Arc<dyn ProcessLauncher>,
) -> (
    tauri::App<tauri::test::MockRuntime>,
    WebviewWindow<tauri::test::MockRuntime>,
) {
    let runtime = tauri::async_runtime::handle().inner().clone();
    let supervisor = Supervisor::new(
        SupervisorConfig::default(),
        launcher,
        Arc::new(RecordingSink::default()),
        runtime,
    );
    supervisor.start();
    let app = mock_builder()
        .manage(AppState::new(Router::new(supervisor, app_info(), true)))
        .invoke_handler(sharppy_reimagined_lib::invoke_handler())
        .build(mock_context(noop_assets()))
        .expect("mock app");
    let webview = WebviewWindowBuilder::new(&app, "main", Default::default())
        .build()
        .expect("mock window");
    (app, webview)
}

fn invoke(webview: &WebviewWindow<tauri::test::MockRuntime>, cmd: &str) -> Result<Value, Value> {
    invoke_args(webview, cmd, serde_json::json!({}))
}

fn invoke_args(
    webview: &WebviewWindow<tauri::test::MockRuntime>,
    cmd: &str,
    args: Value,
) -> Result<Value, Value> {
    let request = InvokeRequest {
        cmd: cmd.into(),
        callback: CallbackFn(0),
        error: CallbackFn(1),
        url: "http://tauri.localhost".parse().unwrap(),
        body: InvokeBody::Json(args),
        headers: Default::default(),
        invoke_key: INVOKE_KEY.to_string(),
    };
    get_ipc_response(webview, request)
        .map(|body| body.deserialize::<Value>().expect("JSON response"))
}

fn parse<T: DeserializeOwned>(value: Value) -> T {
    serde_json::from_value(value).expect("public DTO")
}

#[test]
fn global_forecast_cache_accepts_f384_and_rejects_later_hours() {
    let launcher = FakeLauncher::with(vec![script(|mut io| async move {
        for (model, count) in [("aigfs", 4), ("gfs", 209)] {
            let request = io.expect("fields.cached").await.unwrap();
            assert_eq!(request["params"]["model"], model);
            assert_eq!(request["params"]["hours"].as_array().unwrap().len(), count);
            io.reply(&request["id"], serde_json::json!([384])).await;
        }
        io.expect("shutdown").await;
        0
    })]);
    let (_app, webview) = window(launcher);
    let args = serde_json::json!({"model": "aigfs", "products": ["tmp-2m"],
        "runTime": "2026-10-26T00:00:00Z", "hours": [0, 144, 360, 384]});
    assert_eq!(
        invoke_args(&webview, "get_cached_forecast_hours", args.clone()).unwrap(),
        serde_json::json!([384])
    );
    let hours: Vec<u32> = (0..=120).chain((123..=384).step_by(3)).collect();
    let mut gfs = args.clone();
    gfs["model"] = serde_json::json!("gfs");
    gfs["hours"] = serde_json::json!(hours);
    assert_eq!(
        invoke_args(&webview, "get_cached_forecast_hours", gfs).unwrap(),
        serde_json::json!([384])
    );
    let mut invalid = args;
    invalid["hours"] = serde_json::json!([385]);
    let error: BackendError =
        parse(invoke_args(&webview, "get_cached_forecast_hours", invalid).unwrap_err());
    assert_eq!(error.code, ErrorCode::InvalidArgument);
}

#[test]
fn storage_commands_return_validated_usage_and_reject_arbitrary_paths() {
    let (_app, webview) = window(FakeLauncher::with(vec![]));
    assert_eq!(
        invoke(&webview, "get_storage_usage").unwrap(),
        fixture("public/storage/usage.json")
    );
    assert_eq!(
        invoke_args(&webview, "clear_cache", serde_json::json!({"cache": "all"})).unwrap(),
        fixture("public/storage/clear.json")
    );
    let error: BackendError = parse(
        invoke_args(
            &webview,
            "clear_cache",
            serde_json::json!({"cache": "../saved"}),
        )
        .unwrap_err(),
    );
    assert_eq!(error.code, ErrorCode::InvalidArgument);
}

#[test]
fn sounding_progress_command_tracks_the_model_request_and_validates_ids() {
    let (_app, webview) = window(FakeLauncher::with(vec![]));
    let args = serde_json::json!({"progressId": "download1"});
    assert_eq!(
        invoke_args(&webview, "get_sounding_progress", args).unwrap(),
        fixture("public/soundings/progress.json"),
    );
    invoke_args(
        &webview,
        "open_sounding",
        serde_json::json!({
            "progressId": "download1", "request": {
                "kind": "model", "model": "hrrr", "latitude": 36.68,
                "longitude": -95.66, "forecastHour": 18,
            },
        }),
    )
    .unwrap();
    for id in ["", "../saved", "é"] {
        let error: BackendError = parse(
            invoke_args(
                &webview,
                "get_sounding_progress",
                serde_json::json!({"progressId": id}),
            )
            .unwrap_err(),
        );
        assert_eq!(error.code, ErrorCode::InvalidArgument);
    }
}

#[test]
fn radar_commands_forward_optional_scan_times_and_preserve_the_actual_time() {
    let launcher = FakeLauncher::with(vec![script(|mut io| async move {
        for (method, params, file) in [
            ("radar.mosaic", serde_json::json!({}), "radar.result.json"),
            (
                "radar.mosaic",
                serde_json::json!({"product": "composite-reflectivity", "valid_time": "2026-10-17T11:40:00Z"}),
                "radar.result.json",
            ),
            (
                "radar.site",
                serde_json::json!({"site_id": "KTLX"}),
                "radar-site.result.json",
            ),
            (
                "radar.site",
                serde_json::json!({"product": "reflectivity", "site_id": "KTLX", "valid_time": "2026-10-17T11:40:00Z", "tilt": "1"}),
                "radar-site.result.json",
            ),
            (
                "radar.site",
                serde_json::json!({"latitude": 35.2, "longitude": -97.4, "valid_time": "2026-10-17T11:40:00Z"}),
                "radar-site.result.json",
            ),
        ] {
            let request = io.expect(method).await.unwrap();
            assert_eq!(request["params"], params);
            let mut result = fixture(&format!("bridge/map/{file}"));
            if params.get("valid_time").is_some() {
                result["valid_time"] = serde_json::json!("2026-10-17T11:38:49Z");
            }
            io.reply(&request["id"], result).await;
        }
        io.expect("shutdown").await;
        0
    })]);
    let (_app, webview) = window(launcher);
    let error: BackendError = parse(
        invoke_args(
            &webview,
            "get_radar_site_frame",
            serde_json::json!({"site": "KTLX", "tilt": "../0"}),
        )
        .unwrap_err(),
    );
    assert_eq!(error.code, ErrorCode::InvalidArgument);
    for (command, args) in [
        ("get_radar_frame", serde_json::json!({})),
        (
            "get_radar_frame",
            serde_json::json!({"product": "composite-reflectivity", "validTime": "2026-10-17T11:40:00Z"}),
        ),
        ("get_radar_site_frame", serde_json::json!({"site": "KTLX"})),
        (
            "get_radar_site_frame",
            serde_json::json!({"product": "reflectivity", "site": "KTLX", "validTime": "2026-10-17T11:40:00Z", "tilt": "1"}),
        ),
        (
            "get_radar_site_frame",
            serde_json::json!({"latitude": 35.2, "longitude": -97.4, "validTime": "2026-10-17T11:40:00Z"}),
        ),
    ] {
        let timed = args.get("validTime").is_some();
        let bytes: Vec<u8> = parse(invoke_args(&webview, command, args).unwrap());
        assert_eq!(&bytes[..8], b"SHRF\x01\x00\x00\x00");
        let length = u32::from_le_bytes(bytes[8..12].try_into().unwrap()) as usize;
        let header: Value = serde_json::from_slice(&bytes[12..12 + length]).unwrap();
        assert_eq!(
            header["validTime"],
            if timed {
                serde_json::json!("2026-10-17T11:38:49Z")
            } else if command == "get_radar_site_frame" {
                fixture("bridge/map/radar-site.result.json")["valid_time"].clone()
            } else {
                Value::Null
            }
        );
        assert!(bytes[12 + length..].starts_with(b"\x89PNG\r\n\x1a\n"));
    }
}

#[test]
fn every_raster_route_rejects_invalid_base64_padding_size_and_png_signatures() {
    use base64::Engine as _;

    for (command, method, file) in [
        ("get_weather_field", "fields.grid", "field.result.json"),
        ("get_radar_frame", "radar.mosaic", "radar.result.json"),
        (
            "get_radar_site_frame",
            "radar.site",
            "radar-site.result.json",
        ),
        (
            "get_satellite_frame",
            "satellite.goes",
            "satellite.result.json",
        ),
    ] {
        let cases = if method == "fields.grid" { 4 } else { 5 };
        let launcher = FakeLauncher::with(vec![script(move |mut io| async move {
            let encoded = if method == "fields.grid" {
                "data_base64"
            } else {
                "png_base64"
            };
            for fault in 0..cases {
                let request = io.expect(method).await.unwrap();
                let mut result = fixture(&format!("bridge/map/{file}"));
                match fault {
                    0 => result[encoded] = serde_json::json!("!"),
                    1 => result[encoded] = serde_json::json!("iVBORw0KGgo"),
                    2 => result[encoded] = serde_json::json!("iVBORw0KGgp="),
                    3 if method == "fields.grid" => {
                        result["width"] = serde_json::json!(result["width"].as_u64().unwrap() + 1);
                    }
                    3 => {
                        result["byte_length"] =
                            serde_json::json!(result["byte_length"].as_u64().unwrap() + 1);
                    }
                    _ => {
                        let engine = base64::engine::general_purpose::STANDARD;
                        let mut bytes = engine.decode(result[encoded].as_str().unwrap()).unwrap();
                        bytes[0] = 0;
                        result[encoded] = serde_json::json!(engine.encode(bytes));
                    }
                }
                io.reply(&request["id"], result).await;
            }
            let request = io.expect(method).await.unwrap();
            io.reply(&request["id"], fixture(&format!("bridge/map/{file}")))
                .await;
            io.expect("shutdown").await;
            0
        })]);
        let (_app, webview) = window(launcher);
        let args = serde_json::json!({
            "product": "refc", "runTime": "2026-10-04T20:00:00Z", "forecastHour": 1,
            "site": "KTLX", "validTime": "2026-10-04T21:00:00Z"
        });
        // Leave radar untimed: the mosaic fixture intentionally describes the latest scan.
        let args = if method.starts_with("radar.") {
            serde_json::json!({"site": "KTLX"})
        } else {
            args
        };
        for fault in 0..cases {
            let error: BackendError =
                parse(invoke_args(&webview, command, args.clone()).unwrap_err());
            assert_eq!(
                error.code,
                ErrorCode::BridgeProtocol,
                "{command}, fault {fault}"
            );
            assert_eq!(error.context.as_ref().unwrap().operation, command);
        }
        let bytes: Vec<u8> = parse(invoke_args(&webview, command, args).unwrap());
        let header_len = u32::from_le_bytes(bytes[8..12].try_into().unwrap()) as usize;
        let expected = fixture(&format!("bridge/map/{file}"));
        let encoded = if method == "fields.grid" {
            "data_base64"
        } else {
            "png_base64"
        };
        assert_eq!(
            &bytes[12 + header_len..],
            base64::engine::general_purpose::STANDARD
                .decode(expected[encoded].as_str().unwrap())
                .unwrap()
        );
    }
}

#[test]
fn ecmwf_catalogues_keep_run_hour_availability_and_validate_before_cache() {
    let launcher = FakeLauncher::with(vec![script(|mut io| async move {
        for hour in [None, Some(12), Some(360)] {
            let request = io.expect("map.catalog").await.unwrap();
            assert_eq!(request["params"]["model"], "ecmwf-ifs-9km");
            assert_eq!(request["params"]["forecast_hour"], serde_json::json!(hour));
            if hour.is_some() {
                assert_eq!(request["params"]["run_time"], "2026-10-26T12:00:00Z");
            }
            let mut result = fixture("bridge/map/catalog.models.json")["ecmwf-ifs-9km"].clone();
            if hour == Some(360) {
                result["products"]
                    .as_array_mut()
                    .unwrap()
                    .retain(|product| product["key"] != "snod");
            }
            io.reply(&request["id"], result).await;
        }
        io.expect("shutdown").await;
        0
    })]);
    let (_app, webview) = window(launcher);
    let initial = invoke_args(
        &webview,
        "get_map_catalog",
        serde_json::json!({"model": "ecmwf-ifs-9km"}),
    )
    .unwrap();
    assert_eq!(initial["products"].as_array().unwrap().len(), 20);
    let error: BackendError = parse(
        invoke_args(
            &webview,
            "get_map_catalog",
            serde_json::json!({
                "model": "ecmwf-ifs-9km", "forecastHour": 12
            }),
        )
        .unwrap_err(),
    );
    assert_eq!(error.code, ErrorCode::InvalidArgument);
    for (hour, count) in [(12, 20), (360, 19)] {
        let result = invoke_args(
            &webview,
            "get_map_catalog",
            serde_json::json!({
                "model": "ecmwf-ifs-9km", "runTime": "2026-10-26T12:00:00Z", "forecastHour": hour
            }),
        )
        .unwrap();
        assert_eq!(result["products"].as_array().unwrap().len(), count);
    }
}

#[test]
fn aigfs_accepts_f384_for_catalogues_fields_and_contours() {
    let launcher = FakeLauncher::with(vec![script(|mut io| async move {
        for (method, file) in [
            ("map.catalog", "catalog.models.json"),
            ("fields.grid", "field.result.json"),
            ("fields.contours", "contours.result.json"),
        ] {
            let request = io.expect(method).await.unwrap();
            assert_eq!(request["params"]["model"], "aigfs");
            assert_eq!(request["params"]["forecast_hour"], 384);
            let mut result = fixture(&format!("bridge/map/{file}"));
            if method == "map.catalog" {
                result = result["aigfs"].clone();
            } else {
                result["forecast_hour"] = serde_json::json!(384);
            }
            io.reply(&request["id"], result).await;
        }
        io.expect("shutdown").await;
        0
    })]);
    let (_app, webview) = window(launcher);
    let args = serde_json::json!({"model": "aigfs", "product": "hgt-wind-500",
        "runTime": "2026-10-27T00:00:00Z", "forecastHour": 384});
    let catalog = invoke_args(&webview, "get_map_catalog", args.clone()).unwrap();
    assert_eq!(catalog["products"].as_array().unwrap().len(), 9);
    let frame: Vec<u8> = parse(invoke_args(&webview, "get_weather_field", args.clone()).unwrap());
    assert!(!frame.is_empty());
    let contours = invoke_args(&webview, "get_field_contours", args).unwrap();
    assert_eq!(contours["forecastHour"], 384);
}

#[test]
fn timed_radar_rejects_an_untimed_provider_frame() {
    let launcher = FakeLauncher::with(vec![script(|mut io| async move {
        for (method, file) in [
            ("radar.mosaic", "radar.result.json"),
            ("radar.site", "radar-site.result.json"),
        ] {
            let request = io.expect(method).await.unwrap();
            assert_eq!(request["params"]["valid_time"], "2026-10-17T11:40:00Z");
            let mut result = fixture(&format!("bridge/map/{file}"));
            result["valid_time"] = Value::Null;
            io.reply(&request["id"], result).await;
        }
        io.expect("shutdown").await;
        0
    })]);
    let (_app, webview) = window(launcher);
    for command in ["get_radar_frame", "get_radar_site_frame"] {
        let error: BackendError = parse(
            invoke_args(
                &webview,
                command,
                serde_json::json!({"site": "KTLX", "validTime": "2026-10-17T11:40:00Z"}),
            )
            .unwrap_err(),
        );
        assert_eq!(error.code, ErrorCode::BridgeProtocol);
        let error: BackendError = parse(
            invoke_args(
                &webview,
                command,
                serde_json::json!({"site": "KTLX", "validTime": ""}),
            )
            .unwrap_err(),
        );
        assert_eq!(error.code, ErrorCode::InvalidArgument);
    }
}

#[test]
fn commands_return_public_dtos() {
    let (_app, webview) = window(FakeLauncher::with(vec![]));
    let catalog: StationCatalog = parse(invoke(&webview, "get_station_catalog").unwrap());
    let expected = convert::catalog(
        serde_json::from_value(fixture("bridge/stations.catalog.result.sample.json")).unwrap(),
    );
    assert_eq!(catalog, expected);
    assert_eq!(
        serde_json::to_value(&catalog).unwrap(),
        fixture("public/stations.catalog.sample.json")
    );

    let health: BackendHealth = parse(invoke(&webview, "get_backend_health").unwrap());
    assert_eq!(health.app, app_info());
    assert!(matches!(
        health.bridge,
        BridgeStatus::Up {
            restarts: 0,
            pid: 4242,
            ..
        }
    ));
    assert_eq!(
        health.python.unwrap().executable.as_deref(),
        Some("C:\\fake\\python.exe")
    );
    assert!(matches!(
        health.scientific_backend,
        Some(ScientificBackendStatus::Ok { .. })
    ));

    let status: BridgeStatus = parse(invoke(&webview, "restart_backend").unwrap());
    assert!(
        matches!(status, BridgeStatus::Up { restarts: 1, .. }),
        "{status:?}"
    );
    let again: StationCatalog = parse(invoke(&webview, "get_station_catalog").unwrap());
    assert_eq!(again, expected);
}

#[test]
fn errors_cross_ipc_as_backend_error_and_health_still_succeeds() {
    let (_app, webview) = window(Arc::new(NotConfigured));
    let error: BackendError = parse(invoke(&webview, "get_station_catalog").unwrap_err());
    assert_eq!(
        (error.code, error.retryable),
        (ErrorCode::PythonNotFound, false)
    );
    let context = error.context.unwrap();
    assert_eq!(context.operation, "get_station_catalog");
    assert_eq!(
        context.resource.as_deref(),
        Some("bundled UWyo station catalogue")
    );

    let health: BackendHealth = parse(invoke(&webview, "get_backend_health").unwrap());
    let BridgeStatus::Unavailable { error } = health.bridge else {
        panic!("{:?}", health.bridge)
    };
    assert_eq!(error.code, ErrorCode::PythonNotFound);
    assert!(health.python.is_none() && health.scientific_backend.is_none());
}

#[test]
fn unknown_commands_are_rejected() {
    let (_app, webview) = window(FakeLauncher::with(vec![]));
    assert!(invoke(&webview, "run_python").is_err());
    assert_eq!(
        invoke(&webview, "get_ensemble_summary").unwrap_err(),
        Value::String("Command get_ensemble_summary not found".into()),
    );
}

#[test]
fn weather_alerts_validate_requests_and_return_the_public_snapshot() {
    let launcher = FakeLauncher::with(vec![script(|mut io| async move {
        let request = io.expect("alerts.weather").await.unwrap();
        assert_eq!(
            request["params"],
            serde_json::json!({"valid_time": "2026-10-04T21:00:00Z"})
        );
        io.reply(&request["id"], fixture("bridge/map/alerts.result.json"))
            .await;
        io.expect("shutdown").await;
        0
    })]);
    let (_app, webview) = window(launcher);
    let error: BackendError = parse(
        invoke_args(
            &webview,
            "get_weather_alerts",
            serde_json::json!({"validTime": "not-an-instant"}),
        )
        .unwrap_err(),
    );
    assert_eq!(error.code, ErrorCode::InvalidArgument);
    assert_eq!(
        invoke_args(
            &webview,
            "get_weather_alerts",
            serde_json::json!({"validTime": "2026-10-04T21:00:00Z"})
        )
        .unwrap(),
        fixture("public/map/alerts.json")
    );
}

//! The three commands through IPC on the `tauri::test` mock runtime, with the
//! fake launcher behind the supervisor. No frontend build is needed
//! (`mock_context(noop_assets())`). The capability itself is checked by
//! tauri-build at compile time and by the real app (DESIGN-bridge B8).

mod common;

use std::sync::Arc;

use serde::de::DeserializeOwned;
use serde_json::Value;
use tauri::ipc::{CallbackFn, InvokeBody};
use tauri::test::{get_ipc_response, mock_builder, mock_context, noop_assets, INVOKE_KEY};
use tauri::webview::InvokeRequest;
use tauri::{WebviewWindow, WebviewWindowBuilder};

use common::{fixture, FakeLauncher, NotConfigured, RecordingSink};
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

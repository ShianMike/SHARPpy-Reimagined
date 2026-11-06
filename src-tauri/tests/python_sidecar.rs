//! The real Python sidecar against real repository data. Ignored by default
//! because it needs an interpreter with sharpmod installed: `SHARPPY_PYTHON`,
//! or `.gribenv` in debug builds. Run with `cargo test -- --include-ignored`.

mod common;

use std::collections::HashSet;
use std::process::Stdio;
use std::sync::Arc;
use std::time::Duration;

use base64::Engine as _;
use common::{fixture, RecordingSink};
use serde_json::json;
use sharppy_reimagined_lib::backend::router::Router;
use sharppy_reimagined_lib::dto::health::{
    AppInfo, BridgeStatus, BuildProfile, ScientificBackendStatus,
};
use sharppy_reimagined_lib::dto::stations::CatalogOrigin;
use sharppy_reimagined_lib::python::launcher::{ProcessLauncher, TokioLauncher};
use sharppy_reimagined_lib::python::supervisor::{Supervisor, SupervisorConfig};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

#[tokio::test]
#[ignore = "needs a Python interpreter with sharpmod (SHARPPY_PYTHON or .gribenv)"]
async fn bridge_starts_without_the_optional_native_map_extension() {
    let interpreter = sharppy_reimagined_lib::python::interpreter::discover_from_environment(
        &std::path::PathBuf::new(),
    )
    .unwrap();
    let mut child = tokio::process::Command::new(interpreter)
        .args([
            "-I",
            "-u",
            "-X",
            "utf8",
            "-c",
            "import sys; sys.modules['sharpmod_rs'] = None; from sharpmod.bridge.server import main; from sharpmod.providers.hrrr_products import available_products; assert available_products(); assert 'sharpmod.providers.hrrr_field' not in sys.modules; raise SystemExit(main())",
        ])
        .env("SHARPMOD_BACKEND", "auto")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .kill_on_drop(true)
        .spawn()
        .unwrap();
    let mut stdin = child.stdin.take().unwrap();
    let mut stdout = BufReader::new(child.stdout.take().unwrap());
    for (id, method) in [
        (1, "hello"),
        (2, "stations.catalog"),
        (3, "health"),
        (4, "storage.usage"),
        (5, "shutdown"),
    ] {
        let params = if method == "hello" {
            json!({"protocol_version": 1})
        } else {
            json!({})
        };
        let request = json!({"id": id, "method": method, "params": params});
        stdin
            .write_all(format!("{request}\n").as_bytes())
            .await
            .unwrap();
        stdin.flush().await.unwrap();
        let mut line = String::new();
        tokio::time::timeout(Duration::from_secs(60), stdout.read_line(&mut line))
            .await
            .unwrap()
            .unwrap();
        let response: serde_json::Value = serde_json::from_str(&line).expect("bridge response");
        assert_eq!(response["id"], id);
        let result = response.get("result").expect("successful bridge operation");
        match method {
            "hello" => assert_eq!(result["protocol_version"], 1),
            "stations.catalog" => assert_eq!(result["stations"].as_array().unwrap().len(), 933),
            "health" => {
                assert_eq!(result["scientific_backend"]["status"], "ok");
                assert_eq!(result["scientific_backend"]["active_backend"], "python");
                assert_eq!(result["scientific_backend"]["rust_installed"], false);
            }
            "storage.usage" => assert_eq!(result["caches"].as_array().unwrap().len(), 7),
            "shutdown" => assert_eq!(result["ok"], true),
            _ => unreachable!(),
        }
    }
    let status = tokio::time::timeout(Duration::from_secs(10), child.wait())
        .await
        .unwrap()
        .unwrap();
    assert!(status.success());
}

#[tokio::test]
#[ignore = "needs a Python interpreter with sharpmod (SHARPPY_PYTHON or .gribenv)"]
async fn session_files_are_removed_after_normal_and_forced_exit() {
    let sample = std::fs::read(
        sharppy_reimagined_lib::python::interpreter::repo_root()
            .join("examples/soundings/hrrr_point_36.68N_95.66W_f018.npz"),
    )
    .unwrap();
    for force in [false, true] {
        let working_dir =
            std::env::temp_dir().join(format!("sharppy-cache-test-{}-{force}", std::process::id()));
        std::fs::create_dir_all(&working_dir).unwrap();
        let saved = working_dir.join("keep.npz");
        std::fs::write(&saved, &sample).unwrap();
        let mut process = TokioLauncher::new(working_dir.clone(), working_dir.clone())
            .launch()
            .unwrap();
        let request = json!({"id": 1, "method": "soundings.open_file", "params": {
            "file_name": "sample.npz",
            "content_base64": base64::engine::general_purpose::STANDARD.encode(&sample)
        }});
        process
            .stdin
            .write_all(format!("{request}\n").as_bytes())
            .await
            .unwrap();
        process.stdin.flush().await.unwrap();
        let mut stdout = BufReader::new(process.stdout);
        let mut line = String::new();
        tokio::time::timeout(Duration::from_secs(60), stdout.read_line(&mut line))
            .await
            .unwrap()
            .unwrap();
        let response: serde_json::Value = serde_json::from_str(&line).unwrap();
        assert!(response.get("result").is_some(), "{response}");
        let cache = std::fs::read_dir(&working_dir)
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .find(|path| path.is_dir())
            .expect("a session-owned cache directory");
        let downloads = cache.join("model-grib");
        std::fs::create_dir(&downloads).unwrap();
        std::fs::write(downloads.join("model.grib2"), b"GRIB").unwrap();
        std::fs::write(downloads.join("model.grib2.provenance.json"), b"{}").unwrap();
        if force {
            process.control.start_kill();
        } else {
            process
                .stdin
                .write_all(b"{\"id\":2,\"method\":\"shutdown\",\"params\":{}}\n")
                .await
                .unwrap();
            process.stdin.flush().await.unwrap();
        }
        let code = tokio::time::timeout(Duration::from_secs(10), process.control.wait())
            .await
            .unwrap();
        if !force {
            assert_eq!(code, Some(0));
        }
        assert!(!cache.exists(), "session files survived (forced={force})");
        assert_eq!(std::fs::read(&saved).unwrap(), sample);
        std::fs::remove_file(saved).unwrap();
        std::fs::remove_dir(working_dir).unwrap();
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "needs a Python interpreter with sharpmod (SHARPPY_PYTHON or .gribenv)"]
async fn real_sidecar_serves_the_bundled_catalogue_and_shuts_down_cleanly() {
    let working_dir =
        std::env::temp_dir().join(format!("sharppy-sidecar-test-{}", std::process::id()));
    let sink = Arc::new(RecordingSink::default());
    let supervisor = Supervisor::new(
        SupervisorConfig::default(),
        Arc::new(TokioLauncher::new(working_dir.clone(), working_dir.clone())),
        sink.clone(),
        tokio::runtime::Handle::current(),
    );
    supervisor.start();
    let app = AppInfo {
        name: "SHARPpy Reimagined".into(),
        version: env!("CARGO_PKG_VERSION").into(),
        tauri_version: tauri::VERSION.into(),
        build_profile: BuildProfile::Debug,
        log_file: "unused".into(),
    };
    let router = Router::new(supervisor.clone(), app, true);

    let catalog = router
        .station_catalog()
        .await
        .expect("catalogue from the real bridge");
    assert_eq!(catalog.stations.len(), 933);
    assert_eq!(catalog.origin, CatalogOrigin::Bundled);
    assert_eq!(catalog.dropped_count, 0);
    let ids: HashSet<&str> = catalog.stations.iter().map(|s| s.id.as_str()).collect();
    assert_eq!(ids.len(), 933);
    assert!(catalog.stations.iter().all(|s| s.latitude.is_finite()
        && s.longitude.is_finite()
        && (-90.0..=90.0).contains(&s.latitude)
        && (-180.0..=180.0).contains(&s.longitude)));
    catalog.validate().unwrap();
    assert_eq!(
        serde_json::to_value(&*catalog).unwrap(),
        fixture("public/stations.catalog.full.json")
    );

    let health = router.health().await.unwrap();
    let BridgeStatus::Up {
        pid,
        protocol_version: 1,
        ..
    } = health.bridge
    else {
        panic!("bridge not up: {:?}", health.bridge);
    };
    assert!(
        matches!(
            health.scientific_backend,
            Some(ScientificBackendStatus::Ok { .. })
        ),
        "{:?}",
        health.scientific_backend
    );
    let python = health.python.expect("python facts while up");
    assert!(python.version.starts_with("3."), "{}", python.version);
    assert_eq!(python.sharpmod_version, env!("CARGO_PKG_VERSION"));

    assert_eq!(
        supervisor.finish_shutdown().await,
        Some(0),
        "the bridge exits 0 on shutdown"
    );
    assert_eq!(sink.names(), ["starting", "up"]);
    #[cfg(windows)]
    {
        let out = std::process::Command::new("tasklist")
            .args(["/FI", &format!("PID eq {pid}"), "/NH", "/FO", "CSV"])
            .output()
            .expect("tasklist");
        let listing = String::from_utf8_lossy(&out.stdout);
        assert!(
            !listing.contains(&format!("\"{pid}\"")),
            "interpreter {pid} survived: {listing}"
        );
    }
    let _ = std::fs::remove_dir(&working_dir);
}

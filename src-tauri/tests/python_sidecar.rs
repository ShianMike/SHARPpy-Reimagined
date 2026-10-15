//! The real Python sidecar against real repository data. Ignored by default
//! because it needs an interpreter with sharpmod installed: `SHARPPY_PYTHON`,
//! or `.gribenv` in debug builds. Run with `cargo test -- --include-ignored`.

mod common;

use std::collections::HashSet;
use std::sync::Arc;

use common::{fixture, RecordingSink};
use sharppy_reimagined_lib::backend::router::Router;
use sharppy_reimagined_lib::dto::health::{
    AppInfo, BridgeStatus, BuildProfile, ScientificBackendStatus,
};
use sharppy_reimagined_lib::dto::stations::CatalogOrigin;
use sharppy_reimagined_lib::python::launcher::TokioLauncher;
use sharppy_reimagined_lib::python::supervisor::{Supervisor, SupervisorConfig};

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

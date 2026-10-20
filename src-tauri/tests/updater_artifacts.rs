//! Run on the staged installer, using the installed updater's own signature verifier.
//! This mock runtime creates no windows and never installs anything.
use std::path::PathBuf;

use serde_json::Value;
use tauri_plugin_updater::UpdaterExt;

#[test]
#[ignore = "requires SHARPPY_UPDATER_ARTIFACT_DIR containing the final installer, signature and latest.json"]
fn staged_updater_verifies_bytes_and_binds_the_announced_version() {
    let directory = PathBuf::from(
        std::env::var_os("SHARPPY_UPDATER_ARTIFACT_DIR").expect("staged artifact directory"),
    );
    let manifest: Value =
        serde_json::from_slice(&std::fs::read(directory.join("latest.json")).unwrap()).unwrap();
    assert_eq!(manifest["version"], env!("CARGO_PKG_VERSION"));
    let platform = &manifest["platforms"]["windows-x86_64"];
    let prefix = format!(
        "https://github.com/ShianMike/SHARPpy-Reimagined/releases/download/v{}/",
        env!("CARGO_PKG_VERSION")
    );
    let url = platform["url"].as_str().unwrap();
    assert!(
        url.starts_with(&prefix),
        "unexpected updater destination: {url}"
    );
    let name = url.strip_prefix(&prefix).unwrap();
    assert_eq!(
        name,
        format!(
            "SHARPpy-Reimagined-v{}-windows-x64-setup.exe",
            env!("CARGO_PKG_VERSION")
        )
    );
    let bytes = std::fs::read(directory.join(name)).unwrap();
    let signature = std::fs::read_to_string(directory.join(format!("{name}.sig"))).unwrap();
    assert_eq!(platform["signature"], signature.trim());

    let mut context = tauri::test::mock_context(tauri::test::noop_assets());
    let config: Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
    context.config_mut().plugins = serde_json::from_value(config["plugins"].clone()).unwrap();
    let app = tauri::test::mock_builder()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .build(context)
        .unwrap();
    let updater = app
        .updater_builder()
        .target("windows-x86_64")
        .version_comparator(|_, _| true)
        .build()
        .unwrap();
    let update = updater.restore_update(manifest.clone()).unwrap().unwrap();
    update
        .verify(&bytes)
        .expect("final installer signature and signed version");

    let mut corrupt = bytes.clone();
    let last = corrupt.len() - 1;
    corrupt[last] ^= 1;
    assert!(
        update.verify(&corrupt).is_err(),
        "modified installer accepted"
    );
    let mut wrong_version = manifest;
    wrong_version["version"] = "99.0.0".into();
    assert!(
        updater
            .restore_update(wrong_version)
            .unwrap()
            .unwrap()
            .verify(&bytes)
            .is_err(),
        "version substitution accepted"
    );
    println!("Verified final installer bytes, signature sidecar, URL target, signed version, corruption and version substitution rejection.");
}

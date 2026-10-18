fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&[
            "get_backend_health",
            "get_station_catalog",
            "restart_backend",
            "cancel_request",
            "get_storage_usage",
            "clear_cache",
            "get_model_catalog",
            "get_model_status",
            "open_sounding",
            "get_sounding_progress",
            "inspect_wrf",
            "get_sounding_analysis",
            "get_sounding_trends",
            "get_threshold_metrics",
            "evaluate_threshold",
            "export_threshold_csv",
            "render_sounding",
            "render_sounding_view",
            "get_map_catalog",
            "get_weather_field",
            "get_cached_forecast_hours",
            "get_spc_outlook",
            "get_radar_frame",
            "get_radar_site_frame",
            "get_radar_sites",
            "get_radar_tilts",
            "get_satellite_frame",
            "get_storm_reports",
            "get_weather_alerts",
            "get_report_options",
            "get_field_contours",
            "get_surface_plots",
            "get_place_labels",
            "get_station_availability",
        ]),
    ))
    .expect("tauri-build failed");
    embed_test_manifest();
}

/// tauri-build embeds the Common Controls v6 manifest only into the app
/// binary. Tauri's default `common-controls-v6` feature imports comctl32 v6
/// entry points, so an integration-test binary that creates a (mock) window
/// fails to load with STATUS_ENTRYPOINT_NOT_FOUND without it
/// (tauri-apps/tauri#13419). Embed the same manifest into test binaries.
fn embed_test_manifest() {
    let windows_msvc = std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("windows")
        && std::env::var("CARGO_CFG_TARGET_ENV").as_deref() == Ok("msvc");
    if !windows_msvc {
        return;
    }
    let out_dir = std::path::PathBuf::from(std::env::var("OUT_DIR").expect("OUT_DIR"));
    let manifest = out_dir.join("test-common-controls.manifest");
    std::fs::write(&manifest, COMMON_CONTROLS_V6).expect("write the test manifest");
    println!("cargo:rustc-link-arg-tests=/MANIFEST:EMBED");
    println!(
        "cargo:rustc-link-arg-tests=/MANIFESTINPUT:{}",
        manifest.display()
    );
}

const COMMON_CONTROLS_V6: &str = r#"<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <dependency>
    <dependentAssembly>
      <assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls" version="6.0.0.0"
        processorArchitecture="*" publicKeyToken="6595b64144ccf1df" language="*" />
    </dependentAssembly>
  </dependency>
</assembly>
"#;

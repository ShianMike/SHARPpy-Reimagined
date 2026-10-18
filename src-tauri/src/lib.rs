//! SHARPpy Reimagined desktop application bridge.
//!
//! The crate supervises the Python sidecar, routes semantic operations, and
//! converts bridge payloads into the public DTOs the webview receives. It
//! contains no numerical code: every meteorological value comes from sharpmod.

pub mod backend;
pub mod commands;
mod diagnostics;
pub mod dto;
pub mod error;
pub mod events;
pub mod python;
pub mod state;

use std::sync::Arc;

use tauri::{AppHandle, Manager, RunEvent, Runtime};
use tauri_plugin_log::{RotationStrategy, Target, TargetKind, TimezoneStrategy};

use crate::backend::router::Router;
use crate::dto::health::{AppInfo, BuildProfile};
use crate::events::AppHandleSink;
use crate::python::launcher::TokioLauncher;
use crate::python::supervisor::{Supervisor, SupervisorConfig};
use crate::state::AppState;

/// Name of the developer log file (without extension) in the app log directory.
pub const LOG_FILE_NAME: &str = "sharppy-reimagined";

const PRODUCT_NAME: &str = "SHARPpy Reimagined";

fn log_plugin<R: Runtime>() -> tauri::plugin::TauriPlugin<R> {
    let mut targets = vec![Target::new(TargetKind::LogDir {
        file_name: Some(LOG_FILE_NAME.to_owned()),
    })];
    if cfg!(debug_assertions) {
        targets.push(Target::new(TargetKind::Stdout));
    }
    tauri_plugin_log::Builder::new()
        .clear_targets()
        .targets(targets)
        .level(log::LevelFilter::Info)
        .max_file_size(5_000_000)
        .rotation_strategy(RotationStrategy::KeepSome(3))
        .timezone_strategy(TimezoneStrategy::UseUtc)
        .build()
}

/// The command handler of the app: the commands of `build.rs`.
pub fn invoke_handler<R: Runtime>() -> impl Fn(tauri::ipc::Invoke<R>) -> bool + Send + Sync + 'static
{
    tauri::generate_handler![
        commands::backend::get_backend_health,
        commands::stations::get_station_catalog,
        commands::backend::restart_backend,
        commands::backend::get_storage_usage,
        commands::backend::clear_cache,
        commands::soundings::get_model_catalog,
        commands::soundings::get_model_status,
        commands::soundings::open_sounding,
        commands::soundings::get_sounding_progress,
        commands::soundings::inspect_wrf,
        commands::soundings::get_sounding_analysis,
        commands::soundings::get_sounding_trends,
        commands::soundings::get_threshold_metrics,
        commands::soundings::evaluate_threshold,
        commands::soundings::export_threshold_csv,
        commands::soundings::render_sounding,
        commands::soundings::render_sounding_view,
        commands::map::get_map_catalog,
        commands::map::get_weather_field,
        commands::map::get_spc_outlook,
        commands::map::get_radar_frame,
        commands::map::get_radar_site_frame,
        commands::map::get_radar_sites,
        commands::map::get_radar_tilts,
        commands::map::get_satellite_frame,
        commands::map::get_storm_reports,
        commands::map::get_weather_alerts,
        commands::map::get_report_options,
        commands::map::get_field_contours,
        commands::map::get_surface_plots,
        commands::map::get_place_labels,
        commands::map::get_station_availability,
    ]
}

fn app_info<R: Runtime>(app: &AppHandle<R>) -> Result<AppInfo, tauri::Error> {
    let log_file = app
        .path()
        .app_log_dir()?
        .join(format!("{LOG_FILE_NAME}.log"));
    Ok(AppInfo {
        name: app
            .config()
            .product_name
            .clone()
            .unwrap_or_else(|| PRODUCT_NAME.to_owned()),
        version: app.package_info().version.to_string(),
        tauri_version: tauri::VERSION.to_owned(),
        build_profile: BuildProfile::current(),
        log_file: log_file.display().to_string(),
    })
}

fn build_state<R: Runtime>(app: &AppHandle<R>) -> Result<AppState, tauri::Error> {
    let working_dir = app.path().app_local_data_dir()?;
    let runtime = tauri::async_runtime::handle().inner().clone();
    let supervisor = Supervisor::new(
        SupervisorConfig::default(),
        Arc::new(TokioLauncher::new(working_dir, app.path().resource_dir()?)),
        Arc::new(AppHandleSink::new(app.clone())),
        runtime,
    );
    Ok(AppState::new(Router::new(
        supervisor,
        app_info(app)?,
        cfg!(debug_assertions),
    )))
}

fn supervisor<R: Runtime>(app: &AppHandle<R>) -> Option<Supervisor> {
    app.try_state::<AppState>()
        .map(|state| state.router.supervisor().clone())
}

/// Builds and runs the application. The window loads while the Python
/// sidecar starts in the background; nothing waits for Python.
pub fn run() {
    let args: Vec<_> = std::env::args_os().collect();
    let diagnostic = (args.len() == 4 && args[1] == "--desktop-runtime-check").then(|| {
        (
            std::path::PathBuf::from(&args[2]),
            std::path::PathBuf::from(&args[3]),
        )
    });
    let app = tauri::Builder::default()
        .plugin(log_plugin())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(move |app| {
            let state = build_state(app.handle())?;
            let supervisor = state.router.supervisor().clone();
            let router = Arc::clone(&state.router);
            app.manage(state);
            log::info!("{PRODUCT_NAME} {} starting", app.package_info().version);
            supervisor.start();
            if let Some((output, sample)) = diagnostic {
                diagnostics::start(app.handle().clone(), router, output, sample);
            }
            Ok(())
        })
        .invoke_handler(invoke_handler())
        .build(tauri::generate_context!())
        .expect("error while building SHARPpy Reimagined");
    app.run(|app, event| match event {
        RunEvent::ExitRequested { .. } => {
            if let Some(supervisor) = supervisor(app) {
                supervisor.begin_shutdown();
            }
        }
        RunEvent::Exit => {
            if let Some(supervisor) = supervisor(app) {
                tauri::async_runtime::block_on(async move {
                    supervisor.finish_shutdown().await;
                });
            }
        }
        _ => {}
    });
}

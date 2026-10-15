//! Sounding and forecast-model commands.

use crate::dto::analysis::{SoundingAnalysis, SoundingTrends};
use crate::dto::ensemble::EnsembleSummary;
use crate::dto::models::{ModelCatalog, ModelStatus};
use crate::dto::soundings::{ImageMode, OpenSoundingRequest, RenderParcel, SoundingSummary};
use crate::dto::threshold_catalog::{ThresholdCsv, ThresholdMetrics};
use crate::dto::thresholds::{ThresholdDefinitionInput, ThresholdEvaluation};
use crate::dto::wrf::WrfFile;
use crate::error::BackendError;
use crate::state::AppState;

/// The forecast models that can produce a point sounding, with recent runs.
#[tauri::command]
pub async fn get_model_catalog(
    state: tauri::State<'_, AppState>,
) -> Result<ModelCatalog, BackendError> {
    let catalog = state.router.model_catalog().await?;
    Ok(ModelCatalog::clone(&catalog))
}

#[tauri::command]
pub async fn get_model_status(
    state: tauri::State<'_, AppState>,
    model: String,
    run_time: String,
    forecast_hour: u32,
) -> Result<ModelStatus, BackendError> {
    state
        .router
        .model_status(&model, &run_time, forecast_hour)
        .await
}

#[tauri::command]
pub async fn inspect_wrf(
    state: tauri::State<'_, AppState>,
    file_path: String,
) -> Result<WrfFile, BackendError> {
    state.router.inspect_wrf(&file_path).await
}

/// Opens an observed, model, or file sounding; returns its summary.
#[tauri::command]
pub async fn open_sounding(
    state: tauri::State<'_, AppState>,
    request: OpenSoundingRequest,
) -> Result<SoundingSummary, BackendError> {
    state.router.open_sounding(request).await
}

/// The parameters of an open sounding.
#[tauri::command]
pub async fn get_sounding_analysis(
    state: tauri::State<'_, AppState>,
    sounding_id: String,
) -> Result<SoundingAnalysis, BackendError> {
    state.router.sounding_analysis(&sounding_id).await
}

/// The Trends metrics at every valid time of an open sounding's file.
#[tauri::command]
pub async fn get_sounding_trends(
    state: tauri::State<'_, AppState>,
    sounding_id: String,
) -> Result<SoundingTrends, BackendError> {
    state.router.sounding_trends(&sounding_id).await
}

/// The metric spreads of one ensemble run at one valid time, with the
/// member acquisition ledger. `members` names the requested members (2 to 32).
#[tauri::command]
#[allow(clippy::too_many_arguments)] // Published IPC fields stay separate.
pub async fn get_ensemble_summary(
    state: tauri::State<'_, AppState>,
    model: String,
    latitude: f64,
    longitude: f64,
    forecast_hour: u32,
    run_time: Option<String>,
    location: Option<String>,
    members: Vec<String>,
) -> Result<EnsembleSummary, BackendError> {
    state
        .router
        .ensemble_summary(
            &model,
            latitude,
            longitude,
            forecast_hour,
            run_time.as_deref(),
            location.as_deref(),
            &members,
        )
        .await
}

/// The sounding image of an open sounding, as raw PNG bytes (an ArrayBuffer in JS).
/// `ascent` is the renderer's parcel choice (SFC, ML, FCST, MU, EFF; MU by default).
#[tauri::command]
pub async fn render_sounding(
    state: tauri::State<'_, AppState>,
    sounding_id: String,
    ascent: Option<RenderParcel>,
    image_mode: Option<ImageMode>,
) -> Result<tauri::ipc::Response, BackendError> {
    let bytes = state
        .router
        .render_sounding(
            &sounding_id,
            ascent.unwrap_or_default(),
            image_mode.unwrap_or_default(),
        )
        .await?;
    Ok(tauri::ipc::Response::new(bytes))
}

/// Render a reversible view of a sounding; validation and calculations stay in Python.
#[tauri::command]
pub async fn render_sounding_view(
    state: tauri::State<'_, AppState>,
    sounding_id: String,
    ascent: RenderParcel,
    image_mode: ImageMode,
    view: serde_json::Value,
) -> Result<serde_json::Value, BackendError> {
    state
        .router
        .render_sounding_view(&sounding_id, ascent, image_mode, view)
        .await
}

/// Every registry diagnostic with its control bounds, for the threshold form.
#[tauri::command]
pub async fn get_threshold_metrics(
    state: tauri::State<'_, AppState>,
) -> Result<ThresholdMetrics, BackendError> {
    state.router.threshold_metrics().await
}

/// Where each requested member stands on one definition, with denominators.
#[tauri::command]
#[allow(clippy::too_many_arguments)] // Published IPC fields stay separate.
pub async fn evaluate_threshold(
    state: tauri::State<'_, AppState>,
    model: String,
    latitude: f64,
    longitude: f64,
    forecast_hour: u32,
    run_time: Option<String>,
    location: Option<String>,
    members: Vec<String>,
    definition: ThresholdDefinitionInput,
) -> Result<ThresholdEvaluation, BackendError> {
    state
        .router
        .evaluate_threshold(
            &model,
            latitude,
            longitude,
            forecast_hour,
            run_time.as_deref(),
            location.as_deref(),
            &members,
            definition,
        )
        .await
}

/// The same verdicts as `export_threshold_csv` writes, as download text.
#[tauri::command]
#[allow(clippy::too_many_arguments)] // Published IPC fields stay separate.
pub async fn export_threshold_csv(
    state: tauri::State<'_, AppState>,
    model: String,
    latitude: f64,
    longitude: f64,
    forecast_hour: u32,
    run_time: Option<String>,
    location: Option<String>,
    members: Vec<String>,
    definition: ThresholdDefinitionInput,
) -> Result<ThresholdCsv, BackendError> {
    state
        .router
        .export_threshold_csv(
            &model,
            latitude,
            longitude,
            forecast_hour,
            run_time.as_deref(),
            location.as_deref(),
            &members,
            definition,
        )
        .await
}

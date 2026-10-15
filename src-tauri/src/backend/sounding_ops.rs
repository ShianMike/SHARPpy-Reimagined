//! Sounding and forecast-model operations. All are served by the Python
//! bridge today (`sharpmod/bridge/soundings.py`); the frontend only sees the
//! operation names, so any of them can move to Rust later.
//!
//! | Operation | Bridge method | Deadline |
//! |---|---|---|
//! | `get_model_catalog` | `models.catalog`, cached 10 min | 30 s |
//! | `open_sounding` | `soundings.open_observed` / `open_model` / `open_era5` / `open_file` | 90 / 300 / 300 / 60 s |
//! | `get_sounding_analysis` | `soundings.analysis` | 60 s |
//! | `render_sounding` | `soundings.render` (the Qt render worker) | 200 s |

use std::sync::{Arc, PoisonError};
use std::time::{Duration, Instant};

use base64::Engine as _;
use serde::de::DeserializeOwned;
use serde_json::{json, Value};

use crate::backend::router::Router;
use crate::dto::analysis::{SoundingAnalysis, SoundingTrends};
use crate::dto::checks::check_text;
use crate::dto::models::{ModelCatalog, ModelStatus};
use crate::dto::soundings::{
    ImageMode, OpenSoundingRequest, RenderParcel, RenderResult, SoundingSummary,
};
use crate::dto::wrf::WrfFile;
use crate::error::{BackendError, BridgeFailure, ErrorCode, ErrorContext, PYTHON_PROVIDER};

pub const OP_MODELS: &str = "get_model_catalog";
pub const OP_OPEN: &str = "open_sounding";
pub const OP_ANALYSIS: &str = "get_sounding_analysis";
pub const OP_TRENDS: &str = "get_sounding_trends";
pub const OP_RENDER: &str = "render_sounding";

const MODELS_TIMEOUT: Duration = Duration::from_secs(30);
const MODELS_TTL: Duration = Duration::from_secs(600);
const OPEN_OBSERVED_TIMEOUT: Duration = Duration::from_secs(90);
const OPEN_MODEL_TIMEOUT: Duration = Duration::from_secs(300);
const OPEN_FILE_TIMEOUT: Duration = Duration::from_secs(60);
const ANALYSIS_TIMEOUT: Duration = Duration::from_secs(60);
const TRENDS_TIMEOUT: Duration = Duration::from_secs(180);
const RENDER_TIMEOUT: Duration = Duration::from_secs(200);
const PNG_SIGNATURE: &[u8] = b"\x89PNG\r\n\x1a\n";

pub(crate) fn invalid(context: &ErrorContext, reason: &str) -> BackendError {
    BackendError {
        code: ErrorCode::InvalidArgument,
        message: format!("The request was not valid: {reason}."),
        retryable: false,
        context: Some(Box::new(context.clone())),
        details: None,
    }
}

pub(crate) fn rejected(context: &ErrorContext, method: &str, reason: String) -> BackendError {
    log::error!("bridge {method}: result rejected: {reason}");
    BridgeFailure::Protocol {
        reason: format!("{method} result rejected: {reason}"),
        details: None,
    }
    .into_backend_error(context)
}

/// Sounding ids are the bridge's 16 hex digits; anything else never reaches Python.
fn sounding_id_ok(id: &str) -> bool {
    (1..=64).contains(&id.len()) && id.bytes().all(|b| b.is_ascii_alphanumeric())
}

const BAD_ID: &str = "soundingId must be 1 to 64 letters or digits";

impl Router {
    pub(crate) async fn call<T: DeserializeOwned>(
        &self,
        context: &ErrorContext,
        method: &str,
        params: Value,
        limit: Duration,
    ) -> Result<T, BackendError> {
        self.supervisor()
            .request_typed::<T>(method, params, limit)
            .await
            .map_err(|failure| {
                let error = failure.into_backend_error(context);
                log::warn!(
                    "{} failed: {} ({})",
                    context.operation,
                    error.message,
                    error.code.as_str()
                );
                error
            })
    }

    /// The forecast models that can produce a point sounding, with recent runs.
    pub async fn model_catalog(&self) -> Result<Arc<ModelCatalog>, BackendError> {
        {
            let cached = self.models.lock().unwrap_or_else(PoisonError::into_inner);
            if let Some((at, catalog)) = cached.as_ref() {
                if at.elapsed() < MODELS_TTL {
                    return Ok(Arc::clone(catalog));
                }
            }
        }
        let context = ErrorContext::new(
            OP_MODELS,
            Some(PYTHON_PROVIDER),
            Some("forecast model catalogue"),
        );
        let catalog: ModelCatalog = self
            .call(&context, "models.catalog", json!({}), MODELS_TIMEOUT)
            .await?;
        catalog
            .validate()
            .map_err(|reason| rejected(&context, "models.catalog", reason))?;
        let catalog = Arc::new(catalog);
        *self.models.lock().unwrap_or_else(PoisonError::into_inner) =
            Some((Instant::now(), Arc::clone(&catalog)));
        Ok(catalog)
    }

    pub async fn model_status(
        &self,
        model: &str,
        run_time: &str,
        forecast_hour: u32,
    ) -> Result<ModelStatus, BackendError> {
        let context = ErrorContext::new("get_model_status", Some(PYTHON_PROVIDER), Some(model));
        check_text("model", model, 1, 40)
            .and_then(|()| check_text("runTime", run_time, 1, 40))
            .map_err(|reason| invalid(&context, &reason))?;
        if forecast_hour > 1000 {
            return Err(invalid(&context, "forecastHour is out of range"));
        }
        let status: ModelStatus = self
            .call(
                &context,
                "models.status",
                json!({"model": model, "run_time": run_time, "forecast_hour": forecast_hour}),
                Duration::from_secs(30),
            )
            .await?;
        status
            .validate()
            .map_err(|reason| rejected(&context, "models.status", reason))?;
        if status.model != model
            || status.run_time != run_time
            || status.forecast_hour != forecast_hour
        {
            return Err(rejected(
                &context,
                "models.status",
                "response does not match the requested cycle".into(),
            ));
        }
        Ok(status)
    }

    /// Fetches, extracts, or decodes one sounding and keeps it open in the bridge.
    pub async fn inspect_wrf(&self, file_path: &str) -> Result<WrfFile, BackendError> {
        let context = ErrorContext::new("inspect_wrf", Some(PYTHON_PROVIDER), Some(file_path));
        check_text("filePath", file_path, 1, 4096).map_err(|reason| invalid(&context, &reason))?;
        if !std::path::Path::new(file_path).is_absolute() {
            return Err(invalid(&context, "filePath must be absolute"));
        }
        let result: WrfFile = self
            .call(
                &context,
                "soundings.inspect_wrf",
                json!({ "file_path": file_path }),
                OPEN_FILE_TIMEOUT,
            )
            .await?;
        result
            .validate()
            .map_err(|reason| rejected(&context, "soundings.inspect_wrf", reason))?;
        Ok(result)
    }

    pub async fn open_sounding(
        &self,
        request: OpenSoundingRequest,
    ) -> Result<SoundingSummary, BackendError> {
        let resource = request.resource();
        let context = ErrorContext::new(OP_OPEN, Some(PYTHON_PROVIDER), Some(&resource));
        request
            .validate()
            .map_err(|reason| invalid(&context, &reason))?;
        let limit = match request {
            OpenSoundingRequest::Observed { .. } => OPEN_OBSERVED_TIMEOUT,
            OpenSoundingRequest::Model { .. } | OpenSoundingRequest::Era5 { .. } => {
                OPEN_MODEL_TIMEOUT
            }
            OpenSoundingRequest::File { .. } => OPEN_FILE_TIMEOUT,
            OpenSoundingRequest::Wrf { .. } => OPEN_MODEL_TIMEOUT,
        };
        let (method, params) = request.bridge_call();
        let summary: SoundingSummary = self.call(&context, method, params, limit).await?;
        summary
            .validate()
            .map_err(|reason| rejected(&context, method, reason))?;
        log::info!(
            "{OP_OPEN}: {} ({:?}) as {}",
            summary.title,
            summary.kind,
            summary.sounding_id
        );
        Ok(summary)
    }

    /// Parcels, storm motion, hazard type, and every parameter, from sharpmod.
    pub async fn sounding_analysis(
        &self,
        sounding_id: &str,
    ) -> Result<SoundingAnalysis, BackendError> {
        let context = ErrorContext::new(
            OP_ANALYSIS,
            Some(PYTHON_PROVIDER),
            Some("sounding parameters"),
        );
        if !sounding_id_ok(sounding_id) {
            return Err(invalid(&context, BAD_ID));
        }
        let analysis: SoundingAnalysis = self
            .call(
                &context,
                "soundings.analysis",
                json!({ "sounding_id": sounding_id }),
                ANALYSIS_TIMEOUT,
            )
            .await?;
        analysis
            .validate()
            .map_err(|reason| rejected(&context, "soundings.analysis", reason))?;
        Ok(analysis)
    }

    /// The legacy Trends metrics at every valid time of the sounding's file.
    pub async fn sounding_trends(&self, sounding_id: &str) -> Result<SoundingTrends, BackendError> {
        let context = ErrorContext::new(OP_TRENDS, Some(PYTHON_PROVIDER), Some("sounding trends"));
        if !sounding_id_ok(sounding_id) {
            return Err(invalid(&context, BAD_ID));
        }
        let trends: SoundingTrends = self
            .call(
                &context,
                "soundings.trends",
                json!({ "sounding_id": sounding_id }),
                TRENDS_TIMEOUT,
            )
            .await?;
        trends
            .validate()
            .map_err(|reason| rejected(&context, "soundings.trends", reason))?;
        Ok(trends)
    }

    /// The edited PNG and normalized hit targets from sharpmod's native viewer.
    pub async fn render_sounding_view(
        &self,
        sounding_id: &str,
        parcel: RenderParcel,
        image_mode: ImageMode,
        view: Value,
    ) -> Result<Value, BackendError> {
        let context = ErrorContext::new(
            "render_sounding_view",
            Some(PYTHON_PROVIDER),
            Some("sounding image"),
        );
        if !sounding_id_ok(sounding_id) || !view.is_object() {
            return Err(invalid(
                &context,
                "A sounding id and view object are required",
            ));
        }
        let result: Value = self
            .call(
                &context,
                "soundings.view",
                json!({
                    "sounding_id": sounding_id, "parcel": parcel.as_str(),
                    "image_mode": image_mode.as_str(), "view": view,
                }),
                RENDER_TIMEOUT,
            )
            .await?;
        let image = result.get("image").and_then(Value::as_str).unwrap_or("");
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(image)
            .map_err(|e| rejected(&context, "soundings.view", e.to_string()))?;
        if !bytes.starts_with(PNG_SIGNATURE) {
            return Err(rejected(
                &context,
                "soundings.view",
                "Expected a PNG".to_owned(),
            ));
        }
        Ok(result)
    }

    /// The sounding image as PNG bytes, drawn by sharpmod's renderer.
    pub async fn render_sounding(
        &self,
        sounding_id: &str,
        parcel: RenderParcel,
        image_mode: ImageMode,
    ) -> Result<Vec<u8>, BackendError> {
        let context = ErrorContext::new(OP_RENDER, Some(PYTHON_PROVIDER), Some("sounding image"));
        if !sounding_id_ok(sounding_id) {
            return Err(invalid(&context, BAD_ID));
        }
        let result: RenderResult = self
            .call(
                &context,
                "soundings.render",
                json!({
                    "sounding_id": sounding_id,
                    "parcel": parcel.as_str(),
                    "image_mode": image_mode.as_str(),
                }),
                RENDER_TIMEOUT,
            )
            .await?;
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(result.png_base64.as_bytes())
            .map_err(|e| rejected(&context, "soundings.render", format!("png_base64: {e}")))?;
        if !bytes.starts_with(PNG_SIGNATURE)
            || u64::try_from(bytes.len()).ok() != Some(result.byte_length)
            || result.sounding_id != sounding_id
        {
            return Err(rejected(
                &context,
                "soundings.render",
                "the image is not the PNG that was described".to_owned(),
            ));
        }
        log::info!(
            "{OP_RENDER}: {sounding_id} {} {} -> {}x{}, {} bytes",
            result.parcel.as_str(),
            result.image_mode.as_str(),
            result.width,
            result.height,
            bytes.len()
        );
        Ok(bytes)
    }
}

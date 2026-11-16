//! Image encoding uses the shared Rust core directly, without Python IPC.
use crate::error::{BackendError, ErrorCode, ErrorContext};
use sharpmod_weather::animation::{encode_png, WebpFrame};

#[tauri::command]
pub async fn encode_webp_frame(png: String) -> Result<WebpFrame, BackendError> {
    tauri::async_runtime::spawn_blocking(move || encode_png(&png))
        .await
        .map_err(|_| BackendError::internal("encode_webp_frame"))?
        .map_err(|message| BackendError {
            code: ErrorCode::RenderFailed,
            message,
            retryable: false,
            context: Some(Box::new(ErrorContext::new(
                "encode_webp_frame",
                Some("Rust"),
                Some("animation frame"),
            ))),
            details: None,
        })
}

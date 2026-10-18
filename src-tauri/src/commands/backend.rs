//! Backend status commands.

use crate::dto::health::{BackendHealth, BridgeStatus};
use crate::dto::storage::{CacheClearResult, StorageUsage};
use crate::error::BackendError;
use crate::state::AppState;

#[tauri::command]
pub async fn cancel_request(
    state: tauri::State<'_, AppState>,
    request_id: String,
) -> Result<(), BackendError> {
    let context = crate::error::ErrorContext::new("cancel_request", Some("Python"), None);
    if !crate::backend::sounding_ops::sounding_id_ok(&request_id) {
        return Err(crate::backend::sounding_ops::invalid(
            &context,
            "requestId must be 1 to 64 letters or digits",
        ));
    }
    let _: serde_json::Value = state
        .router
        .call(
            &context,
            "cancel",
            serde_json::json!({"request_id": request_id}),
            std::time::Duration::from_secs(5),
        )
        .await?;
    Ok(())
}

/// The health report; succeeds in every bridge state.
#[tauri::command]
pub async fn get_backend_health(
    state: tauri::State<'_, AppState>,
) -> Result<BackendHealth, BackendError> {
    state.router.health().await
}

/// Kills any bridge child, clears the crash history and the catalogue cache,
/// and starts a new child. Returns the status once `hello` succeeded or failed.
#[tauri::command]
pub async fn restart_backend(
    state: tauri::State<'_, AppState>,
) -> Result<BridgeStatus, BackendError> {
    state.router.restart_backend().await
}

#[tauri::command]
pub async fn get_storage_usage(
    state: tauri::State<'_, AppState>,
) -> Result<StorageUsage, BackendError> {
    state.router.storage_usage().await
}

#[tauri::command]
pub async fn clear_cache(
    state: tauri::State<'_, AppState>,
    cache: String,
) -> Result<CacheClearResult, BackendError> {
    state.router.clear_cache(&cache).await
}

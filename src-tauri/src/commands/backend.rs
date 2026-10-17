//! Backend status commands.

use crate::dto::health::{BackendHealth, BridgeStatus};
use crate::dto::storage::{CacheClearResult, StorageUsage};
use crate::error::BackendError;
use crate::state::AppState;

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

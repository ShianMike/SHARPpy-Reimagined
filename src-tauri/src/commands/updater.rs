//! Keep the signed updater's Windows exit on the supervisor's cleanup path.

use crate::state::AppState;
use serde::Serialize;
use tauri::{Manager, ResourceId, Runtime, Webview};
use tauri_plugin_updater::UpdaterExt;

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateMetadata {
    rid: ResourceId,
    current_version: String,
    version: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    date: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    body: Option<String>,
    raw_json: serde_json::Value,
}

#[tauri::command]
pub async fn check_for_updates<R: Runtime>(
    webview: Webview<R>,
    state: tauri::State<'_, AppState>,
    timeout: Option<u64>,
) -> Result<Option<UpdateMetadata>, String> {
    let supervisor = state.router.supervisor().clone();
    let app = webview.app_handle().clone();
    let mut builder = webview.updater_builder().on_before_exit(move || {
        supervisor.shutdown_before_exit();
        app.cleanup_before_exit();
    });
    if let Some(timeout) = timeout {
        if !(1..=120_000).contains(&timeout) {
            return Err("update timeout must be between 1 and 120000 milliseconds".into());
        }
        builder = builder.timeout(std::time::Duration::from_millis(timeout));
    }
    let update = builder
        .build()
        .map_err(|error| error.to_string())?
        .check()
        .await
        .map_err(|error| error.to_string())?;
    update
        .map(|update| {
            let metadata = UpdateMetadata {
                current_version: update.current_version.clone(),
                version: update.version.clone(),
                date: update
                    .date
                    .map(|date| date.format(&time::format_description::well_known::Rfc3339))
                    .transpose()
                    .map_err(|error| error.to_string())?,
                body: update.body.clone(),
                raw_json: update.raw_json.clone(),
                rid: webview.resources_table().add(update),
            };
            Ok(metadata)
        })
        .transpose()
}

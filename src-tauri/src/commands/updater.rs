//! Signed Windows updates and the release page for manual desktop updates.

use std::{process::Stdio, time::Duration};

use crate::state::AppState;
use serde::{Deserialize, Serialize};
#[cfg(windows)]
use tauri::{Manager, ResourceId};
use tauri::{Runtime, Webview};
#[cfg(windows)]
use tauri_plugin_updater::UpdaterExt;

const RELEASES_URL: &str = "https://github.com/ShianMike/SHARPpy-Reimagined/releases";

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub enum UpdateAction {
    GetPlatform,
    OpenReleases,
}

#[derive(Serialize)]
#[serde(untagged)]
pub enum UpdateResponse {
    Platform(&'static str),
    #[cfg(windows)]
    Checked(Option<UpdateMetadata>),
    Opened,
}

#[cfg(windows)]
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
    action: Option<UpdateAction>,
) -> Result<UpdateResponse, String> {
    // These actions use the existing updater IPC permission and need no update feed.
    match action {
        Some(UpdateAction::GetPlatform) => {
            return Ok(UpdateResponse::Platform(std::env::consts::OS));
        }
        Some(UpdateAction::OpenReleases) => {
            let opener = match std::env::consts::OS {
                "linux" => "xdg-open",
                "macos" => "/usr/bin/open",
                _ => return Err("This action is available in the Linux and macOS apps.".into()),
            };
            let mut command = tokio::process::Command::new(opener);
            command
                .arg(RELEASES_URL)
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null());
            // Some Linux browser launchers stay alive until the browser closes.
            if let Ok(result) = tokio::time::timeout(Duration::from_secs(2), command.status()).await
            {
                let status =
                    result.map_err(|error| format!("Could not open GitHub Releases ({error})."))?;
                if !status.success() {
                    return Err(format!("Could not open GitHub Releases ({status})."));
                }
            }
            return Ok(UpdateResponse::Opened);
        }
        None => {}
    }

    #[cfg(windows)]
    {
        check_windows_updates(webview, state, timeout)
            .await
            .map(UpdateResponse::Checked)
    }
    #[cfg(not(windows))]
    {
        let _ = (webview, state, timeout);
        Err(
            "In-app updates are available on Windows. Download updates from GitHub Releases."
                .into(),
        )
    }
}

#[cfg(windows)]
async fn check_windows_updates<R: Runtime>(
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
        builder = builder.timeout(Duration::from_millis(timeout));
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

//! Packaged-app smoke check: real backend discovery, import, and Qt rendering.
use std::path::PathBuf;
use std::sync::Arc;

use base64::Engine as _;
use serde_json::{json, Value};
use tauri::{AppHandle, Manager};

use crate::backend::router::Router;
use crate::dto::soundings::{ImageMode, OpenSoundingRequest, RenderParcel};
use crate::error::BackendError;

async fn check(router: &Router, sample: PathBuf) -> Result<Value, BackendError> {
    let catalog = router.station_catalog().await?;
    let health = router.health().await?;
    let data = std::fs::read(&sample).map_err(|error| {
        crate::backend::sounding_ops::invalid(
            &crate::error::ErrorContext::new("desktop_runtime_check", None, None),
            &format!("sample could not be read: {error}"),
        )
    })?;
    let summary = router
        .open_sounding(
            OpenSoundingRequest::File {
                file_name: sample
                    .file_name()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .into_owned(),
                content_base64: base64::engine::general_purpose::STANDARD.encode(data),
            },
            None,
            None,
        )
        .await?;
    let image = router
        .render_sounding(&summary.sounding_id, RenderParcel::Mu, ImageMode::Hd)
        .await?;
    Ok(
        json!({ "ok": true, "health": health, "station_count": catalog.stations.len(),
        "sounding": summary, "image_bytes": image.len() }),
    )
}

pub fn start(app: AppHandle, router: Arc<Router>, output: PathBuf, sample: PathBuf) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.hide();
    }
    tauri::async_runtime::spawn(async move {
        let result = check(&router, sample).await;
        let (report, code) = match result {
            Ok(report) => (report, 0),
            Err(error) => (json!({ "ok": false, "error": error }), 1),
        };
        let written = serde_json::to_vec_pretty(&report)
            .map_err(std::io::Error::other)
            .and_then(|bytes| std::fs::write(output, bytes));
        if let Err(error) = written {
            log::error!("desktop runtime report: {error}");
            app.exit(1);
        } else {
            app.exit(code);
        }
    });
}

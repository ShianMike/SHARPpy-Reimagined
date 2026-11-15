//! Packaged-app smoke check: real backend discovery, import, and Qt rendering.
use std::path::PathBuf;
use std::sync::Arc;

use base64::Engine as _;
use serde_json::{json, Value};
use tauri::{AppHandle, Manager};

use crate::backend::router::Router;
use crate::dto::soundings::{ImageMode, OpenSoundingRequest, RenderParcel, SoundingColorStyle};
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
        .render_sounding(
            &summary.sounding_id,
            RenderParcel::Mu,
            ImageMode::Hd,
            SoundingColorStyle::Standard,
        )
        .await?;
    let mut views = serde_json::Map::new();
    for (name, mode, quality) in [
        ("severe_hd", "severe", ImageMode::Hd),
        ("winter_hd", "winter", ImageMode::Hd),
        ("winter_uhd", "winter", ImageMode::Uhd),
    ] {
        let view = router
            .render_sounding_view(
                &summary.sounding_id,
                RenderParcel::Mu,
                quality,
                json!({"mode": mode, "chart": "streamwiseness", "edits": []}),
                SoundingColorStyle::Standard,
            )
            .await?;
        // Exercise the exact interactive view route and dynamic native imports
        // used by the desktop; the legacy Severe render alone misses Winter.
        let png = view_png(&view).map_err(|message| {
            crate::backend::sounding_ops::invalid(
                &crate::error::ErrorContext::new("desktop_runtime_check", None, None),
                &format!("{name}: {message}"),
            )
        })?;
        views.insert(name.into(), png);
    }
    Ok(
        json!({ "ok": true, "health": health, "station_count": catalog.stations.len(),
        "sounding": summary, "image_bytes": image.len(), "views": views }),
    )
}

fn view_png(view: &Value) -> Result<Value, String> {
    let encoded = view["image"].as_str().ok_or("missing PNG image")?;
    let bytes = base64::engine::general_purpose::STANDARD
        .decode(encoded)
        .map_err(|error| error.to_string())?;
    if bytes.len() < 24 || &bytes[..16] != b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" {
        return Err("missing PNG dimensions".into());
    }
    let width = u32::from_be_bytes(bytes[16..20].try_into().unwrap());
    let height = u32::from_be_bytes(bytes[20..24].try_into().unwrap());
    if width == 0 || height == 0 {
        return Err("empty PNG dimensions".into());
    }
    Ok(json!({"image_bytes": bytes.len(), "width": width, "height": height}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn frozen_view_report_requires_png_dimensions() {
        assert!(view_png(&json!({})).is_err());
        assert!(view_png(&json!({"image": "not base64"})).is_err());
        let encode = |bytes: &[u8]| json!({"image": base64::engine::general_purpose::STANDARD.encode(bytes)});
        assert!(view_png(&encode(b"\x89PNG\r\n\x1a\n")).is_err());
        let mut png = *b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR\0\0\x0c\xbc\0\0\x08\x96";
        assert_eq!(
            view_png(&encode(&png)).unwrap(),
            json!({"image_bytes": 24, "width": 3260, "height": 2198})
        );
        png[16..20].fill(0);
        assert!(view_png(&encode(&png)).is_err());
    }
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

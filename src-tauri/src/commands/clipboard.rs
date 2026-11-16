//! Copy a forecast animation without converting it to a still image.

#[cfg(any(windows, test))]
mod cache;
#[cfg(any(windows, test))]
mod gif;
#[cfg(windows)]
mod windows;

use tauri::ipc::Request;
use tauri::{AppHandle, Runtime};

#[cfg(any(windows, test))]
fn gif_payload(body: &tauri::ipc::InvokeBody) -> Result<Vec<u8>, String> {
    match body {
        tauri::ipc::InvokeBody::Raw(bytes) => {
            gif::validate(bytes)?;
            Ok(bytes.clone())
        }
        tauri::ipc::InvokeBody::Json(_) => Err("GIF clipboard requires raw binary data.".into()),
    }
}

/// The Windows clipboard contains both GIF bytes and a file-paste path. Whether
/// animation survives a paste depends on the receiving application's formats.
#[tauri::command]
pub async fn copy_forecast_gif<R: Runtime>(
    window: tauri::WebviewWindow<R>,
    request: Request<'_>,
) -> Result<(), String> {
    #[cfg(windows)]
    {
        use tauri::Manager;
        let bytes = gif_payload(request.body())?;
        let directory = window
            .app_handle()
            .path()
            .app_local_data_dir()
            .map_err(|error| format!("Cannot locate the GIF clipboard cache: {error}"))?
            .join("forecast-clipboard");
        let hwnd = window
            .hwnd()
            .map_err(|error| format!("Cannot access the application window: {error}"))?
            .0 as usize;
        tauri::async_runtime::spawn_blocking(move || windows::copy(&directory, hwnd, &bytes))
            .await
            .map_err(|error| format!("GIF clipboard task failed: {error}"))?
    }
    #[cfg(not(windows))]
    {
        let _ = (window, request);
        Err("Native GIF copying is currently available only on Windows.".to_owned())
    }
}

/// Recover old owned files, retaining any paths still on the Windows clipboard.
pub fn cleanup_on_startup<R: Runtime>(app: &AppHandle<R>) {
    #[cfg(windows)]
    {
        use tauri::Manager;
        if let Ok(directory) = app.path().app_local_data_dir() {
            tauri::async_runtime::spawn_blocking(move || {
                if let Err(error) = windows::cleanup(&directory.join("forecast-clipboard")) {
                    log::debug!("GIF clipboard cleanup deferred: {error}");
                }
            });
        }
    }
    #[cfg(not(windows))]
    let _ = app;
}

#[cfg(test)]
mod tests {
    use super::*;
    use tauri::ipc::InvokeBody;

    const GIF: &[u8] = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff\x2c\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02\x44\x01\x00\x3b";

    #[test]
    fn accepts_only_valid_binary_gif_payloads() {
        assert_eq!(gif_payload(&InvokeBody::Raw(GIF.to_vec())).unwrap(), GIF);
        assert!(gif_payload(&InvokeBody::Json(
            serde_json::json!({ "bytes": [71, 73, 70] })
        ))
        .is_err());
        assert!(gif_payload(&InvokeBody::Raw(b"not a gif".to_vec())).is_err());
        assert!(gif_payload(&InvokeBody::Raw(vec![0; 16 * 1024 * 1024 + 1])).is_err());
    }
}

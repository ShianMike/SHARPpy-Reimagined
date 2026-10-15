//! Events from Rust to the webview, and the [`StatusSink`] seam that keeps the
//! supervisor independent of the Tauri runtime.

use tauri::{AppHandle, Emitter, Runtime};

use crate::dto::health::BridgeStatus;

/// Emitted to the main window on every supervisor transition; payload `BridgeStatus`.
/// `frontend/src/lib/backend/events.ts` mirrors the name.
pub const BRIDGE_STATUS_CHANGED: &str = "bridge-status-changed";

/// The window that receives backend events.
pub const MAIN_WINDOW: &str = "main";

/// Receives every bridge status transition.
pub trait StatusSink: Send + Sync {
    fn publish(&self, status: &BridgeStatus);
}

/// Publishes transitions as `bridge-status-changed` events to the main window.
pub struct AppHandleSink<R: Runtime> {
    app: AppHandle<R>,
}

impl<R: Runtime> AppHandleSink<R> {
    pub fn new(app: AppHandle<R>) -> Self {
        Self { app }
    }
}

impl<R: Runtime> StatusSink for AppHandleSink<R> {
    fn publish(&self, status: &BridgeStatus) {
        if let Err(err) = self.app.emit_to(MAIN_WINDOW, BRIDGE_STATUS_CHANGED, status) {
            log::warn!("could not emit {BRIDGE_STATUS_CHANGED}: {err}");
        }
    }
}

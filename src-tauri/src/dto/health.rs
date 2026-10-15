//! `get_backend_health` DTOs and the `bridge-status-changed` payload.

use serde::{Deserialize, Serialize};

use crate::error::BackendError;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct BackendHealth {
    pub app: AppInfo,
    pub bridge: BridgeStatus,
    /// Present only while the bridge is `up`.
    pub python: Option<PythonInfo>,
    /// Present only while the bridge is `up`.
    pub scientific_backend: Option<ScientificBackendStatus>,
    pub checked_at: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum BuildProfile {
    Debug,
    Release,
}

impl BuildProfile {
    pub fn current() -> Self {
        if cfg!(debug_assertions) {
            BuildProfile::Debug
        } else {
            BuildProfile::Release
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct AppInfo {
    pub name: String,
    pub version: String,
    pub tauri_version: String,
    pub build_profile: BuildProfile,
    /// Absolute path of the developer log.
    pub log_file: String,
}

/// The supervisor state as the webview sees it.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    tag = "status",
    rename_all = "camelCase",
    rename_all_fields = "camelCase",
    deny_unknown_fields
)]
pub enum BridgeStatus {
    Starting {
        since: String,
    },
    Up {
        protocol_version: u32,
        /// The interpreter's own id from `hello`, not a venv redirector's.
        pid: u32,
        since: String,
        /// Times the bridge reached `up` in this session, minus one.
        restarts: u32,
    },
    Restarting {
        attempt: u32,
        retry_in_ms: u64,
        last_error: BackendError,
    },
    Unavailable {
        error: BackendError,
    },
}

impl BridgeStatus {
    /// The state name used in logs and `details.state`.
    pub fn name(&self) -> &'static str {
        match self {
            BridgeStatus::Starting { .. } => "starting",
            BridgeStatus::Up { .. } => "up",
            BridgeStatus::Restarting { .. } => "restarting",
            BridgeStatus::Unavailable { .. } => "unavailable",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PythonInfo {
    pub version: String,
    /// Debug builds only; `null` in release builds.
    pub executable: Option<String>,
    pub sharpmod_version: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum RequestedBackend {
    Auto,
    Python,
    Rust,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ActiveBackend {
    Python,
    Rust,
}

/// The two codes the scientific backend reports inside a health result.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ScientificBackendCode {
    BackendUnavailable,
    BackendConfigInvalid,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    tag = "status",
    rename_all = "camelCase",
    rename_all_fields = "camelCase",
    deny_unknown_fields
)]
pub enum ScientificBackendStatus {
    Ok {
        requested_backend: RequestedBackend,
        active_backend: ActiveBackend,
        rust_installed: bool,
        rust_version: Option<String>,
        fallback_reason: Option<String>,
    },
    Unavailable {
        code: ScientificBackendCode,
        message: String,
    },
    /// The bridge did not answer the health query.
    Unknown { error: BackendError },
}

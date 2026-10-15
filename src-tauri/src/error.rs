//! The one error shape that crosses every boundary, and the bridge's internal
//! failure type with its single mapping onto it.

use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

use crate::python::protocol::BridgeErrorCode;

/// Closed set of public error codes (21). The TypeScript client mirrors it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum ErrorCode {
    PythonNotFound,
    BridgeSpawnFailed,
    BridgeTimeout,
    BridgeCrashed,
    BridgeUnavailable,
    BridgeProtocol,
    MethodNotFound,
    InvalidArgument,
    CatalogUnavailable,
    StationListUnavailable,
    BackendUnavailable,
    BackendConfigInvalid,
    Internal,
    SoundingUnavailable,
    SoundingFetchFailed,
    SoundingInvalid,
    SoundingNotFound,
    RenderFailed,
    FieldUnavailable,
    FieldFetchFailed,
    OverlayFetchFailed,
}

impl ErrorCode {
    pub const ALL: [ErrorCode; 21] = [
        ErrorCode::PythonNotFound,
        ErrorCode::BridgeSpawnFailed,
        ErrorCode::BridgeTimeout,
        ErrorCode::BridgeCrashed,
        ErrorCode::BridgeUnavailable,
        ErrorCode::BridgeProtocol,
        ErrorCode::MethodNotFound,
        ErrorCode::InvalidArgument,
        ErrorCode::CatalogUnavailable,
        ErrorCode::StationListUnavailable,
        ErrorCode::BackendUnavailable,
        ErrorCode::BackendConfigInvalid,
        ErrorCode::Internal,
        ErrorCode::SoundingUnavailable,
        ErrorCode::SoundingFetchFailed,
        ErrorCode::SoundingInvalid,
        ErrorCode::SoundingNotFound,
        ErrorCode::RenderFailed,
        ErrorCode::FieldUnavailable,
        ErrorCode::FieldFetchFailed,
        ErrorCode::OverlayFetchFailed,
    ];

    /// The wire name, for example `"BRIDGE_TIMEOUT"`.
    pub fn as_str(self) -> &'static str {
        match self {
            ErrorCode::PythonNotFound => "PYTHON_NOT_FOUND",
            ErrorCode::BridgeSpawnFailed => "BRIDGE_SPAWN_FAILED",
            ErrorCode::BridgeTimeout => "BRIDGE_TIMEOUT",
            ErrorCode::BridgeCrashed => "BRIDGE_CRASHED",
            ErrorCode::BridgeUnavailable => "BRIDGE_UNAVAILABLE",
            ErrorCode::BridgeProtocol => "BRIDGE_PROTOCOL",
            ErrorCode::MethodNotFound => "METHOD_NOT_FOUND",
            ErrorCode::InvalidArgument => "INVALID_ARGUMENT",
            ErrorCode::CatalogUnavailable => "CATALOG_UNAVAILABLE",
            ErrorCode::StationListUnavailable => "STATION_LIST_UNAVAILABLE",
            ErrorCode::BackendUnavailable => "BACKEND_UNAVAILABLE",
            ErrorCode::BackendConfigInvalid => "BACKEND_CONFIG_INVALID",
            ErrorCode::Internal => "INTERNAL",
            ErrorCode::SoundingUnavailable => "SOUNDING_UNAVAILABLE",
            ErrorCode::SoundingFetchFailed => "SOUNDING_FETCH_FAILED",
            ErrorCode::SoundingInvalid => "SOUNDING_INVALID",
            ErrorCode::SoundingNotFound => "SOUNDING_NOT_FOUND",
            ErrorCode::RenderFailed => "RENDER_FAILED",
            ErrorCode::FieldUnavailable => "FIELD_UNAVAILABLE",
            ErrorCode::FieldFetchFailed => "FIELD_FETCH_FAILED",
            ErrorCode::OverlayFetchFailed => "OVERLAY_FETCH_FAILED",
        }
    }
}

/// What data, operation, and provider an error concerns (REQUEST §27).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ErrorContext {
    pub operation: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub provider: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub resource: Option<String>,
}

impl ErrorContext {
    pub fn new(operation: &str, provider: Option<&str>, resource: Option<&str>) -> Self {
        Self {
            operation: operation.to_owned(),
            provider: provider.map(str::to_owned),
            resource: resource.map(str::to_owned),
        }
    }

    /// The context of the supervisor's own start-up failures.
    pub fn start_backend() -> Self {
        Self::new("start_backend", Some(PYTHON_PROVIDER), None)
    }
}

/// Provider name of every operation the Python bridge serves.
pub const PYTHON_PROVIDER: &str = "python-bridge";

/// The public error DTO. `message` is one user-facing sentence, never a
/// traceback; `details` is developer-only and is logged, never rendered.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct BackendError {
    pub code: ErrorCode,
    pub message: String,
    pub retryable: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub context: Option<ErrorContext>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub details: Option<Map<String, Value>>,
}

impl BackendError {
    /// An unexpected failure in `operation`.
    pub fn internal(operation: &str) -> Self {
        BridgeFailure::Internal.into_backend_error(&ErrorContext::new(operation, None, None))
    }

    /// The same error, reported for another operation.
    pub fn with_context(mut self, context: &ErrorContext) -> Self {
        self.context = Some(context.clone());
        self
    }
}

/// Where a request was when its deadline passed (DESIGN-bridge B6).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RequestPhase {
    WaitingForBridge,
    AwaitingReply,
}

impl RequestPhase {
    pub fn as_str(self) -> &'static str {
        match self {
            RequestPhase::WaitingForBridge => "waitingForBridge",
            RequestPhase::AwaitingReply => "awaitingReply",
        }
    }
}

/// Failures inside the bridge. [`BridgeFailure::into_backend_error`] is the only
/// place they become the public [`BackendError`].
#[derive(Debug, thiserror::Error)]
pub enum BridgeFailure {
    #[error("the Python backend is not configured: {message}")]
    NotConfigured { message: String },
    #[error("the Python backend could not start: {reason}")]
    Spawn {
        reason: String,
        details: Option<Map<String, Value>>,
    },
    #[error("bridge request timed out after {after:?} ({phase:?})")]
    Timeout {
        phase: RequestPhase,
        state: Option<&'static str>,
        after: Duration,
    },
    #[error("the Python backend stopped (restart requested: {restart_requested})")]
    Crashed {
        restart_requested: bool,
        details: Option<Map<String, Value>>,
    },
    #[error("automatic restarts are paused after repeated crashes (last {last:?})")]
    Unavailable { last: ErrorCode },
    #[error("bridge protocol violation: {reason}")]
    Protocol {
        reason: String,
        details: Option<Map<String, Value>>,
    },
    #[error("Python reported {code:?}: {message}")]
    Python {
        code: BridgeErrorCode,
        message: String,
        retryable: bool,
        details: Option<Map<String, Value>>,
    },
    /// An error the supervisor already reported (the `unavailable` state's).
    #[error("{}", .0.message)]
    Reported(Box<BackendError>),
    #[error("internal error")]
    Internal,
}

fn details(pairs: &[(&str, Value)]) -> Option<Map<String, Value>> {
    Some(
        pairs
            .iter()
            .map(|(k, v)| ((*k).to_owned(), v.clone()))
            .collect(),
    )
}

fn merge(
    base: Option<Map<String, Value>>,
    extra: Option<Map<String, Value>>,
) -> Option<Map<String, Value>> {
    match (base, extra) {
        (Some(mut a), Some(b)) => {
            a.extend(b);
            Some(a)
        }
        (a, b) => a.or(b),
    }
}

impl BridgeFailure {
    /// The single mapping from a bridge failure to the public error DTO.
    pub fn into_backend_error(self, context: &ErrorContext) -> BackendError {
        let resource = context.resource.as_deref().unwrap_or("requested data");
        let (code, message, retryable, extra) = match self {
            BridgeFailure::NotConfigured { message } => {
                (ErrorCode::PythonNotFound, message, false, None)
            }
            BridgeFailure::Spawn { reason, details } => (
                ErrorCode::BridgeSpawnFailed,
                format!("The Python backend could not start: {reason}."),
                false,
                details,
            ),
            BridgeFailure::Timeout {
                phase,
                state,
                after,
            } => {
                let ms = Value::from(u64::try_from(after.as_millis()).unwrap_or(u64::MAX));
                let (message, extra) = match phase {
                    RequestPhase::WaitingForBridge => (
                        format!("The Python backend is still starting, so the {resource} could not be loaded in time."),
                        details(&[
                            ("phase", Value::from(phase.as_str())),
                            ("state", Value::from(state.unwrap_or("starting"))),
                            ("timeoutMs", ms),
                        ]),
                    ),
                    RequestPhase::AwaitingReply => (
                        format!("The Python backend did not answer in time while loading the {resource}."),
                        details(&[("phase", Value::from(phase.as_str())), ("timeoutMs", ms)]),
                    ),
                };
                (ErrorCode::BridgeTimeout, message, true, extra)
            }
            BridgeFailure::Crashed {
                restart_requested,
                details: d,
            } => {
                let (message, extra) = if restart_requested {
                    (
                        "The Python backend was restarted while this request was running.",
                        merge(details(&[("reason", Value::from("restartRequested"))]), d),
                    )
                } else {
                    (
                        "The Python backend stopped unexpectedly and is restarting.",
                        d,
                    )
                };
                (ErrorCode::BridgeCrashed, message.to_owned(), true, extra)
            }
            BridgeFailure::Unavailable { last } => (
                ErrorCode::BridgeUnavailable,
                "The Python backend stopped repeatedly, so automatic restarts are paused."
                    .to_owned(),
                false,
                details(&[("lastErrorCode", Value::from(last.as_str()))]),
            ),
            BridgeFailure::Protocol { reason, details: d } => (
                ErrorCode::BridgeProtocol,
                PROTOCOL_MESSAGE.to_owned(),
                false,
                merge(details(&[("reason", Value::from(reason))]), d),
            ),
            BridgeFailure::Python {
                code,
                message,
                retryable,
                details: d,
            } => {
                let code = code.to_error_code();
                let message = match code {
                    ErrorCode::MethodNotFound => format!(
                        "The installed sharpmod does not support {}. Update sharpmod or the app so their versions match.",
                        context.operation
                    ),
                    ErrorCode::BridgeProtocol => PROTOCOL_MESSAGE.to_owned(),
                    ErrorCode::Internal => internal_message(&context.operation),
                    _ => message,
                };
                (code, message, retryable, d)
            }
            BridgeFailure::Reported(error) => return (*error).with_context(context),
            BridgeFailure::Internal => (
                ErrorCode::Internal,
                internal_message(&context.operation),
                false,
                None,
            ),
        };
        BackendError {
            code,
            message,
            retryable,
            context: Some(context.clone()),
            details: extra,
        }
    }
}

const PROTOCOL_MESSAGE: &str =
    "The Python backend sent a response the app could not read. The app and sharpmod versions may not match.";

fn internal_message(operation: &str) -> String {
    format!("Something went wrong in {operation}. Details are in the log.")
}

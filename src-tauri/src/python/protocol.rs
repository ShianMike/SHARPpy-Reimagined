//! Bridge protocol version 1: one UTF-8 JSON object per `\n`-terminated line.
//!
//! Decoding has two stages, which is what separates a corrupt stream from a bad
//! payload: [`decode_envelope`] checks the envelope by key presence, and only
//! then is the `result` or `error` value decoded into the method's payload type
//! with [`decode_payload`].

use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use tokio::io::{AsyncBufRead, AsyncBufReadExt};

use crate::dto::health::{ActiveBackend, RequestedBackend, ScientificBackendCode};
use crate::error::ErrorCode;

pub const PROTOCOL_VERSION: u32 = 1;
/// Frame cap in bytes, excluding the terminator, in both directions.
pub const MAX_FRAME_BYTES: usize = 8 * 1024 * 1024;
pub const MAX_REQUEST_ID: u64 = (1 << 53) - 1;

/// The codes a Python error frame may carry (DESIGN-contracts C6, plus soundings).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum BridgeErrorCode {
    MethodNotFound,
    InvalidArgument,
    BridgeProtocol,
    CatalogUnavailable,
    StationListUnavailable,
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

impl BridgeErrorCode {
    pub const ALL: [BridgeErrorCode; 14] = [
        BridgeErrorCode::MethodNotFound,
        BridgeErrorCode::InvalidArgument,
        BridgeErrorCode::BridgeProtocol,
        BridgeErrorCode::CatalogUnavailable,
        BridgeErrorCode::StationListUnavailable,
        BridgeErrorCode::Internal,
        BridgeErrorCode::SoundingUnavailable,
        BridgeErrorCode::SoundingFetchFailed,
        BridgeErrorCode::SoundingInvalid,
        BridgeErrorCode::SoundingNotFound,
        BridgeErrorCode::RenderFailed,
        BridgeErrorCode::FieldUnavailable,
        BridgeErrorCode::FieldFetchFailed,
        BridgeErrorCode::OverlayFetchFailed,
    ];

    pub fn to_error_code(self) -> ErrorCode {
        match self {
            BridgeErrorCode::MethodNotFound => ErrorCode::MethodNotFound,
            BridgeErrorCode::InvalidArgument => ErrorCode::InvalidArgument,
            BridgeErrorCode::BridgeProtocol => ErrorCode::BridgeProtocol,
            BridgeErrorCode::CatalogUnavailable => ErrorCode::CatalogUnavailable,
            BridgeErrorCode::StationListUnavailable => ErrorCode::StationListUnavailable,
            BridgeErrorCode::Internal => ErrorCode::Internal,
            BridgeErrorCode::SoundingUnavailable => ErrorCode::SoundingUnavailable,
            BridgeErrorCode::SoundingFetchFailed => ErrorCode::SoundingFetchFailed,
            BridgeErrorCode::SoundingInvalid => ErrorCode::SoundingInvalid,
            BridgeErrorCode::SoundingNotFound => ErrorCode::SoundingNotFound,
            BridgeErrorCode::RenderFailed => ErrorCode::RenderFailed,
            BridgeErrorCode::FieldUnavailable => ErrorCode::FieldUnavailable,
            BridgeErrorCode::FieldFetchFailed => ErrorCode::FieldFetchFailed,
            BridgeErrorCode::OverlayFetchFailed => ErrorCode::OverlayFetchFailed,
        }
    }
}

/// `hello` result: version facts, also the liveness ping.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HelloResult {
    pub protocol_version: u32,
    pub sharpmod_version: String,
    pub python_version: String,
    pub python_executable: String,
    /// `os.getpid()` of the interpreter itself.
    pub pid: u32,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HealthResult {
    pub scientific_backend: BridgeScientificBackend,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "status", rename_all = "lowercase", deny_unknown_fields)]
pub enum BridgeScientificBackend {
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
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BridgeStation {
    pub id: String,
    pub name: String,
    pub lat: f64,
    pub lon: f64,
    pub src: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BridgeOrigin {
    Bundled,
    SeedFallback,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CatalogResult {
    pub stations: Vec<BridgeStation>,
    pub origin: BridgeOrigin,
    pub dropped_count: u32,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ShutdownResult {
    pub ok: bool,
}

/// The value of an error frame's `error` key.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ErrorPayload {
    pub code: BridgeErrorCode,
    pub message: String,
    pub retryable: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub details: Option<Map<String, Value>>,
}

/// A response envelope that passed the first decoding stage.
#[derive(Debug, Clone, PartialEq)]
pub enum Envelope {
    Result {
        id: u64,
        result: Value,
    },
    /// `id` is `None` when Python could not parse a request frame.
    Error {
        id: Option<u64>,
        error: Value,
    },
}

/// A line that is not a valid envelope: the stream itself is corrupt.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CorruptFrame(pub String);

fn strip_terminator(mut line: &[u8]) -> &[u8] {
    if let Some(rest) = line.strip_suffix(b"\n") {
        line = rest;
    }
    line.strip_suffix(b"\r").unwrap_or(line)
}

fn frame_id(value: &Value) -> Option<u64> {
    value
        .as_u64()
        .filter(|id| (1..=MAX_REQUEST_ID).contains(id))
}

/// First decoding stage: a JSON object that is exactly `{id, result}` or `{id, error}`.
pub fn decode_envelope(line: &[u8]) -> Result<Envelope, CorruptFrame> {
    let line = strip_terminator(line);
    if line.len() > MAX_FRAME_BYTES {
        return Err(CorruptFrame(format!(
            "frame of {} bytes exceeds the cap",
            line.len()
        )));
    }
    let mut map: Map<String, Value> = serde_json::from_slice(line)
        .map_err(|e| CorruptFrame(format!("not a JSON object: {e}")))?;
    if map.len() != 2 || !map.contains_key("id") {
        return Err(CorruptFrame(format!(
            "envelope keys are {:?}",
            map.keys().collect::<Vec<_>>()
        )));
    }
    let id = map.remove("id").unwrap_or(Value::Null);
    if let Some(result) = map.remove("result") {
        let id = frame_id(&id).ok_or_else(|| CorruptFrame(format!("result frame with id {id}")))?;
        return Ok(Envelope::Result { id, result });
    }
    if let Some(error) = map.remove("error") {
        if id.is_null() {
            return Ok(Envelope::Error { id: None, error });
        }
        let id = frame_id(&id).ok_or_else(|| CorruptFrame(format!("error frame with id {id}")))?;
        return Ok(Envelope::Error {
            id: Some(id),
            error,
        });
    }
    Err(CorruptFrame(
        "envelope has neither result nor error".to_owned(),
    ))
}

/// Second decoding stage: the payload of one method.
pub fn decode_payload<T: DeserializeOwned>(value: Value) -> Result<T, String> {
    serde_json::from_value(value).map_err(|e| e.to_string())
}

/// Serializes one request frame, including its `\n` terminator.
pub fn encode_request(id: u64, method: &str, params: Value) -> Vec<u8> {
    let frame = serde_json::json!({ "id": id, "method": method, "params": params });
    let mut bytes = serde_json::to_vec(&frame).unwrap_or_default();
    bytes.push(b'\n');
    bytes
}

/// One read from the child's stdout.
#[derive(Debug, PartialEq, Eq)]
pub enum LineRead {
    Line(Vec<u8>),
    /// More than the cap arrived without a newline; the stream is corrupt.
    Oversized,
    /// End of stream (a trailing partial line is discarded).
    Eof,
}

/// Reads one `\n`-terminated line, never buffering more than the cap plus `\r\n`.
pub async fn read_line<R: AsyncBufRead + Unpin>(reader: &mut R) -> std::io::Result<LineRead> {
    let mut line = Vec::new();
    loop {
        let available = reader.fill_buf().await?;
        if available.is_empty() {
            return Ok(LineRead::Eof);
        }
        if let Some(end) = available.iter().position(|b| *b == b'\n') {
            line.extend_from_slice(&available[..=end]);
            reader.consume(end + 1);
            return Ok(LineRead::Line(line));
        }
        let taken = available.len();
        line.extend_from_slice(available);
        reader.consume(taken);
        if line.len() > MAX_FRAME_BYTES + 1 {
            return Ok(LineRead::Oversized);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn envelope_classes() {
        let ok = decode_envelope(b"{\"id\":7,\"result\":{}}\r\n").unwrap();
        assert_eq!(
            ok,
            Envelope::Result {
                id: 7,
                result: serde_json::json!({})
            }
        );
        let null = decode_envelope(br#"{"id":null,"error":{"code":"BRIDGE_PROTOCOL"}}"#).unwrap();
        assert!(matches!(null, Envelope::Error { id: None, .. }));
        for bad in [
            &b"not json"[..],
            br#"{"id":1,"result":NaN}"#,
            br#"{"id":1,"result":{},"error":{}}"#,
            br#"{"id":0,"result":{}}"#,
            br#"{"id":9007199254740992,"result":{}}"#,
            br#"{"id":null,"result":{}}"#,
            br#"{"id":"1","result":{}}"#,
            br#"{"id":1}"#,
            br#"{"id":1,"value":{}}"#,
            br#"[1]"#,
            b"\xff\xfe",
        ] {
            assert!(
                decode_envelope(bad).is_err(),
                "{:?}",
                String::from_utf8_lossy(bad)
            );
        }
    }

    #[test]
    fn oversized_frame_is_corrupt() {
        let mut frame = br#"{"id":1,"result":""#.to_vec();
        frame.resize(MAX_FRAME_BYTES + 1, b'x');
        assert!(decode_envelope(&frame).is_err());
    }

    #[test]
    fn requests_are_compact_lines() {
        let bytes = encode_request(3, "hello", serde_json::json!({"protocol_version": 1}));
        assert_eq!(
            bytes,
            b"{\"id\":3,\"method\":\"hello\",\"params\":{\"protocol_version\":1}}\n"
        );
    }

    #[tokio::test]
    async fn line_reader_splits_and_caps() {
        let data = b"{\"a\":1}\r\n{\"b\":2}\npartial".to_vec();
        let mut reader = tokio::io::BufReader::with_capacity(4, &data[..]);
        assert_eq!(
            read_line(&mut reader).await.unwrap(),
            LineRead::Line(b"{\"a\":1}\r\n".to_vec())
        );
        assert_eq!(
            read_line(&mut reader).await.unwrap(),
            LineRead::Line(b"{\"b\":2}\n".to_vec())
        );
        assert_eq!(read_line(&mut reader).await.unwrap(), LineRead::Eof);
        let big = vec![b'x'; MAX_FRAME_BYTES + 8];
        let mut reader = tokio::io::BufReader::new(&big[..]);
        assert_eq!(read_line(&mut reader).await.unwrap(), LineRead::Oversized);
    }
}

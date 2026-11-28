//! Native bridge wire-protocol request rules (milestone M42).
//!
//! Rust ports of the pure half of two Python bridge modules:
//!
//! * ``sharpmod.bridge.protocol`` -- ``decode_request``'s frame stripping,
//!   8 MiB cap, strict UTF-8 JSON parsing, and id / envelope / method /
//!   params validation ([`bridge_decode_request_frame`]), ``check_params``'
//!   per-method parameter contract ([`bridge_check_request_params`]), and
//!   ``encode_frame``'s frame-size cap ([`bridge_encode_frame_check`]);
//! * ``sharpmod.bridge.params`` -- ``time``'s RFC 3339 validation and
//!   supported range ([`bridge_validate_rfc3339_time`]) and ``iso``'s UTC
//!   formatting ([`bridge_format_iso_utc`]).
//!
//! Messages match the Python sources verbatim.  Protocol failures raise the
//! Python ``ProtocolError`` wrapping a ``BridgeError`` of the original code
//! (falling back to ``ValueError`` when the classes cannot be imported);
//! the RFC 3339 validator raises ``BridgeError`` directly, exactly as
//! ``params.time`` does.  The current time is supplied by the caller because
//! Python's ``params.now()`` is fixture-pinned.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyInt, PyList, PyString};
use serde_json::{Map, Value};
use time::{Date, Month, OffsetDateTime, PlainDateTime, Time, UtcOffset};

/// The wire protocol version ``hello`` negotiates (Python
/// ``PROTOCOL_VERSION``).
const PROTOCOL_VERSION: i64 = 1;

/// Frames are at most 8 MiB excluding the newline terminator (Python
/// ``MAX_FRAME_BYTES``).
const MAX_FRAME_BYTES: usize = 8 * 1024 * 1024;

/// Request ids are positive and exact in ``f64`` (Python ``MAX_REQUEST_ID``,
/// ``2**53 - 1``).
const MAX_REQUEST_ID: i64 = 9_007_199_254_740_991;

/// The request envelope keys (Python ``_ENVELOPE_KEYS``).
const ENVELOPE_KEYS: [&str; 3] = ["id", "method", "params"];

/// Methods whose handler validates its own parameters (Python
/// ``HANDLER_VALIDATED``).
const HANDLER_VALIDATED: [&str; 38] = [
    "soundings.snapshot",
    "soundings.retain",
    "soundings.restore",
    "animation.webp_frame",
    "soundings.progress",
    "storage.clear",
    "models.catalog",
    "models.status",
    "soundings.open_observed",
    "soundings.open_model",
    "soundings.open_era5",
    "soundings.open_file",
    "soundings.inspect_wrf",
    "soundings.open_wrf",
    "soundings.analysis",
    "soundings.render",
    "soundings.view",
    "map.catalog",
    "fields.grid",
    "fields.cached",
    "outlooks.spc",
    "outlooks.wpc",
    "outlooks.build_forecast",
    "alerts.weather",
    "radar.mosaic",
    "radar.site",
    "radar.sites",
    "radar.tilts",
    "satellite.goes",
    "reports.storms",
    "reports.options",
    "fields.contours",
    "surface.plots",
    "thresholds.metrics",
    "thresholds.evaluate",
    "thresholds.export_csv",
    "places.labels",
    "stations.availability",
];

/// 1900-01-01T00:00:00Z in microseconds, the earliest supported time.
const MIN_SUPPORTED_US: i64 = -2_208_988_800_000_000;

/// Thirty days in microseconds, the slack the supported range allows past
/// now.
const THIRTY_DAYS_US: i64 = 30 * 24 * 60 * 60 * 1_000_000;

// ---------------------------------------------------------------------------
// Python formatting and error helpers
// ---------------------------------------------------------------------------

/// Python's ``repr`` for the strings that appear in bridge messages: single
/// quotes by default, double quotes when the text already contains one, and
/// backslash/quote escapes otherwise.
fn py_str_repr(text: &str) -> String {
    if !text.contains('\'') {
        format!("'{text}'")
    } else if !text.contains('"') {
        format!("\"{text}\"")
    } else {
        format!("'{}'", text.replace('\\', "\\\\").replace('\'', "\\'"))
    }
}

/// Python's ``repr`` for one list of strings, as ``f"{list(...)}"`` renders
/// it in the envelope message.
fn py_list_repr(items: &[String]) -> String {
    let rendered: Vec<String> = items.iter().map(|item| py_str_repr(item)).collect();
    format!("[{}]", rendered.join(", "))
}

/// Raise the bridge's ``ProtocolError`` wrapping a ``BridgeError`` of
/// ``code`` with ``message``, answered under ``request_id`` (``None`` when
/// the frame never yielded a valid id).  Falls back to ``ValueError`` when
/// either class cannot be imported.
fn protocol_error(py: Python<'_>, code: &str, message: &str, request_id: Option<i64>) -> PyErr {
    match PyModule::import(py, "sharpmod.bridge.errors")
        .and_then(|module| module.getattr("BridgeError"))
        .and_then(|class| class.call1((code, message, false)))
        .and_then(|error| {
            PyModule::import(py, "sharpmod.bridge.protocol")
                .and_then(|module| module.getattr("ProtocolError"))
                .and_then(|class| class.call1((error, request_id)))
        }) {
        Ok(error) => PyErr::from_value(error),
        Err(_) => PyValueError::new_err(message.to_string()),
    }
}

/// Raise the bridge's ``INVALID_ARGUMENT`` ``BridgeError`` with the
/// ``The request was not valid: ...`` message, falling back to
/// ``ValueError`` when the class cannot be imported.
fn invalid_argument(py: Python<'_>, what: &str) -> PyErr {
    let message = format!("The request was not valid: {what}.");
    match PyModule::import(py, "sharpmod.bridge.errors")
        .and_then(|module| module.getattr("BridgeError"))
        .and_then(|class| class.call1(("INVALID_ARGUMENT", &message, false)))
    {
        Ok(error) => PyErr::from_value(error),
        Err(_) => PyValueError::new_err(message),
    }
}

// ---------------------------------------------------------------------------
// JSON value conversion
// ---------------------------------------------------------------------------

/// Convert one JSON value to the Python object Python's ``json.loads``
/// would have produced: ``Null`` becomes ``None``, numbers keep their
/// integer shape, and objects become dicts.
fn json_to_py(py: Python<'_>, value: &Value) -> PyResult<Py<PyAny>> {
    match value {
        Value::Null => Ok(py.None()),
        Value::Bool(b) => Ok(b.into_pyobject(py)?.to_owned().into_any().unbind()),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(i.into_pyobject(py)?.into_any().unbind())
            } else if let Some(u) = n.as_u64() {
                Ok(u.into_pyobject(py)?.into_any().unbind())
            } else if let Some(f) = n.as_f64() {
                Ok(f.into_pyobject(py)?.into_any().unbind())
            } else {
                Ok(py.None())
            }
        }
        Value::String(s) => Ok(s.into_pyobject(py)?.into_any().unbind()),
        Value::Array(items) => {
            let list = PyList::empty(py);
            for item in items {
                list.append(json_to_py(py, item)?)?;
            }
            Ok(list.into_any().unbind())
        }
        Value::Object(map) => {
            let dict = PyDict::new(py);
            for (k, v) in map {
                dict.set_item(k, json_to_py(py, v)?)?;
            }
            Ok(dict.into_any().unbind())
        }
    }
}

/// Convert one JSON object into the ``params`` dict of a decoded request.
fn json_map_to_py<'py>(py: Python<'py>, map: Map<String, Value>) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    for (k, v) in map {
        dict.set_item(k, json_to_py(py, &v)?)?;
    }
    Ok(dict)
}

// ---------------------------------------------------------------------------
// Request frame decoding (protocol.decode_request)
// ---------------------------------------------------------------------------

/// One decoded request envelope: the id, the method, and the params map
/// before it becomes a Python dict.  `Debug` is derived for the test
/// assertions on the reject path.
#[derive(Debug)]
pub(crate) struct DecodedRequest {
    pub(crate) request_id: i64,
    pub(crate) method: String,
    pub(crate) params: Map<String, Value>,
}

/// Why a request frame cannot be served: the ``BridgeError`` code, its
/// verbatim message, and the id to answer under (``None`` before a valid id
/// is known, so the reply carries ``"id": null``).  `Debug` is derived for
/// the test assertions on the success path.
#[derive(Debug)]
pub(crate) struct ProtocolReject {
    pub(crate) code: &'static str,
    pub(crate) message: String,
    pub(crate) request_id: Option<i64>,
}

impl ProtocolReject {
    /// A ``BRIDGE_PROTOCOL`` failure (Python ``_protocol``).
    fn protocol(message: &str, request_id: Option<i64>) -> Self {
        ProtocolReject {
            code: "BRIDGE_PROTOCOL",
            message: message.to_string(),
            request_id,
        }
    }

    /// An ``INVALID_ARGUMENT`` failure naming the field (Python
    /// ``_invalid``).
    fn invalid(what: &str, request_id: i64) -> Self {
        ProtocolReject {
            code: "INVALID_ARGUMENT",
            message: format!("The request was not valid: {what}."),
            request_id: Some(request_id),
        }
    }
}

/// The pure half of ``decode_request``: strip at most one trailing ``\n``
/// and then one trailing ``\r``, enforce the 8 MiB cap, parse one strict
/// UTF-8 JSON document (``NaN`` and ``Infinity`` tokens are not JSON), and
/// validate the id, envelope, method, and params exactly like the Python
/// reader.
pub(crate) fn decode_request_frame_core(line: &[u8]) -> Result<DecodedRequest, ProtocolReject> {
    let mut frame = line;
    if frame.ends_with(b"\n") {
        frame = &frame[..frame.len() - 1];
    }
    if frame.ends_with(b"\r") {
        frame = &frame[..frame.len() - 1];
    }
    if frame.len() > MAX_FRAME_BYTES {
        return Err(ProtocolReject::protocol(
            "Request frame exceeds the 8 MiB limit.",
            None,
        ));
    }
    let parsed: Value = match serde_json::from_slice(frame) {
        Ok(parsed) => parsed,
        Err(_) => {
            return Err(ProtocolReject::protocol(
                "Request frame is not valid JSON.",
                None,
            ))
        }
    };
    let Value::Object(object) = parsed else {
        return Err(ProtocolReject::protocol(
            "Request frame is not a JSON object.",
            None,
        ));
    };
    // An id must be an exact JSON integer (``1.0`` parses as a float and
    // fails Python's ``type(value) is int``), within the positive 2^53 - 1
    // band.
    let request_id = match object.get("id") {
        Some(Value::Number(number)) if !number.is_f64() => number
            .as_i64()
            .filter(|id| (1..=MAX_REQUEST_ID).contains(id)),
        _ => None,
    };
    let Some(request_id) = request_id else {
        return Err(ProtocolReject::protocol(
            "Request frame has no valid id.",
            None,
        ));
    };
    let mut keys: Vec<String> = object.keys().cloned().collect();
    keys.sort();
    let mut missing: Vec<String> = ENVELOPE_KEYS
        .iter()
        .map(|key| (*key).to_string())
        .filter(|key| !keys.contains(key))
        .collect();
    missing.sort();
    let mut extra: Vec<String> = keys
        .iter()
        .filter(|key| !ENVELOPE_KEYS.contains(&key.as_str()))
        .cloned()
        .collect();
    extra.sort();
    if !missing.is_empty() || !extra.is_empty() {
        return Err(ProtocolReject {
            code: "BRIDGE_PROTOCOL",
            message: format!(
                "Request envelope keys are wrong (missing {}, unexpected {}).",
                py_list_repr(&missing),
                py_list_repr(&extra)
            ),
            request_id: Some(request_id),
        });
    }
    let method = match object.get("method") {
        Some(Value::String(method)) if !method.is_empty() => method.clone(),
        _ => {
            return Err(ProtocolReject::protocol(
                "Request method must be a non-empty string.",
                Some(request_id),
            ))
        }
    };
    let params = match object.get("params") {
        Some(Value::Object(params)) => params.clone(),
        _ => {
            return Err(ProtocolReject::invalid(
                "params must be an object",
                request_id,
            ))
        }
    };
    Ok(DecodedRequest {
        request_id,
        method,
        params,
    })
}

/// Decode one request line (with or without its terminator).
///
/// Accepts a trailing ``\r\n``.  The frame must be at most 8 MiB after the
/// terminator is stripped, one strict UTF-8 JSON object with exactly the
/// ``{"id", "method", "params"}`` envelope, a positive integer id at most
/// ``2**53 - 1`` (an integral float like ``1.0`` is not an id), a non-empty
/// string method, and an object ``params``.  Every failure raises
/// ``ProtocolError`` with the original code and message, answered under the
/// request id once one is known; success returns ``(id, method, params)``
/// with ``params`` as a fresh dict.
#[pyfunction]
fn bridge_decode_request_frame<'py>(
    py: Python<'py>,
    line: &[u8],
) -> PyResult<(i64, String, Bound<'py, PyDict>)> {
    let decoded = decode_request_frame_core(line)
        .map_err(|reject| protocol_error(py, reject.code, &reject.message, reject.request_id))?;
    let params = json_map_to_py(py, decoded.params)?;
    Ok((decoded.request_id, decoded.method, params))
}

// ---------------------------------------------------------------------------
// Per-method parameter validation (protocol.check_params)
// ---------------------------------------------------------------------------

/// The ``hello`` ``protocol_version`` value as Python's ``check_params``
/// sees it: an exact non-bool ``int`` rendered for the message, or not one.
pub(crate) enum HelloVersion {
    /// ``protocol_version`` equal to the version this bridge speaks.
    One,
    /// Any other exact integer; carries its decimal rendering for the
    /// message.
    Other(String),
    /// Missing, boolean, or not an integer at all.
    NotInt,
}

/// The pure half of ``check_params``: handler-validated methods accept any
/// parameters, ``hello`` takes exactly an integer ``protocol_version`` of
/// the version this bridge speaks, and every other method takes none.
pub(crate) fn check_request_params_core(
    method: &str,
    param_keys: &[String],
    version: &HelloVersion,
) -> Result<(), String> {
    if HANDLER_VALIDATED.contains(&method) {
        return Ok(());
    }
    if method == "hello" {
        if param_keys.len() != 1
            || param_keys[0] != "protocol_version"
            || matches!(version, HelloVersion::NotInt)
        {
            return Err("The request was not valid: hello takes exactly an integer \
protocol_version."
                .to_string());
        }
        if let HelloVersion::Other(rendered) = version {
            return Err(format!(
                "The request was not valid: protocol_version {rendered} is not supported \
(this bridge speaks {PROTOCOL_VERSION})."
            ));
        }
        return Ok(());
    }
    if !param_keys.is_empty() {
        return Err(format!(
            "The request was not valid: {method} takes no parameters."
        ));
    }
    Ok(())
}

/// Read ``params["protocol_version"]`` the way ``type(version) is int``
/// does: booleans are not integers, any other ``int`` counts with its
/// decimal rendering (Python renders arbitrarily large ints in full), and
/// everything else is not an integer.
fn hello_version(params: &Bound<'_, PyDict>) -> HelloVersion {
    let Ok(Some(value)) = params.get_item("protocol_version") else {
        return HelloVersion::NotInt;
    };
    if value.is_instance_of::<PyBool>() || !value.is_instance_of::<PyInt>() {
        return HelloVersion::NotInt;
    }
    match value.extract::<i64>() {
        Ok(PROTOCOL_VERSION) => HelloVersion::One,
        Ok(other) => HelloVersion::Other(other.to_string()),
        Err(_) => match value.str().and_then(|text| text.extract::<String>()) {
            Ok(rendered) => HelloVersion::Other(rendered),
            Err(_) => HelloVersion::NotInt,
        },
    }
}

/// Validate one request's ``params`` for its method.
///
/// Mirrors ``check_params``: the methods in ``HANDLER_VALIDATED`` check
/// their own parameters, ``hello`` takes exactly an integer
/// ``protocol_version`` of the version this bridge speaks, and every other
/// method takes no parameters.  Failures raise ``ProtocolError`` wrapping an
/// ``INVALID_ARGUMENT`` ``BridgeError``, answered under ``request_id``.
#[pyfunction]
fn bridge_check_request_params(
    py: Python<'_>,
    request_id: i64,
    method: &str,
    params: &Bound<'_, PyDict>,
) -> PyResult<()> {
    let mut keys = Vec::with_capacity(params.len());
    for key in params.keys() {
        // A non-string key can never be "protocol_version"; any marker
        // that differs keeps Python's set comparison honest.
        let key = key.extract::<String>().unwrap_or_default();
        keys.push(key);
    }
    let version = hello_version(params);
    check_request_params_core(method, &keys, &version)
        .map_err(|message| protocol_error(py, "INVALID_ARGUMENT", &message, Some(request_id)))
}

// ---------------------------------------------------------------------------
// Frame encoding cap (protocol.encode_frame)
// ---------------------------------------------------------------------------

/// The pure half of ``encode_frame``'s size contract: a frame over the cap
/// fails with Python's message; a passing frame gains its ``\n``
/// terminator.
pub(crate) fn encode_frame_check_core(data: &[u8]) -> Result<Vec<u8>, String> {
    if data.len() > MAX_FRAME_BYTES {
        return Err(format!(
            "frame of {} bytes exceeds {}",
            data.len(),
            MAX_FRAME_BYTES
        ));
    }
    let mut framed = Vec::with_capacity(data.len() + 1);
    framed.extend_from_slice(data);
    framed.push(b'\n');
    Ok(framed)
}

/// Append one frame's newline terminator after checking the wire cap.
///
/// ``data`` is one serialized frame without its terminator.  It must be at
/// most 8 MiB; anything longer raises ``ValueError`` with
/// ``encode_frame``'s message, and a passing frame is returned with its
/// terminating ``\n``.
#[pyfunction]
fn bridge_encode_frame_check<'py>(py: Python<'py>, data: &[u8]) -> PyResult<Bound<'py, PyBytes>> {
    let framed = encode_frame_check_core(data).map_err(PyValueError::new_err)?;
    Ok(PyBytes::new(py, &framed))
}

// ---------------------------------------------------------------------------
// RFC 3339 time validation (params.time)
// ---------------------------------------------------------------------------

/// One ASCII decimal digit at ``at``, or nothing.
fn digit_at(bytes: &[u8], at: usize) -> Option<u32> {
    let byte = *bytes.get(at)?;
    if byte.is_ascii_digit() {
        Some(u32::from(byte - b'0'))
    } else {
        None
    }
}

/// Two ASCII decimal digits at ``at`` (``bytes[at]`` then ``bytes[at+1]``).
fn two_digits_at(bytes: &[u8], at: usize) -> Option<u32> {
    Some(digit_at(bytes, at)? * 10 + digit_at(bytes, at + 1)?)
}

/// Parse the RFC 3339 subset ``params.time`` accepts into UTC microseconds.
///
/// The forms are ``YYYY-MM-DD`` with a ``T`` or space separator, ``HH:MM``
/// optionally followed by ``:SS`` and fractional seconds truncated to
/// microsecond resolution (as Python's ``fromisoformat`` does), and an
/// offset of ``Z``/``z``, ``±HH:MM``, or none (a naive time is UTC).
/// Years are ``0001..=9999`` and every component is range-checked,
/// including leap days.  Anything else is nothing.
pub(crate) fn parse_rfc3339_us(text: &str) -> Option<i64> {
    let bytes = text.as_bytes();
    let year = (digit_at(bytes, 0)? * 1000
        + digit_at(bytes, 1)? * 100
        + digit_at(bytes, 2)? * 10
        + digit_at(bytes, 3)?) as i32;
    if bytes.get(4) != Some(&b'-') || year < 1 {
        return None;
    }
    let month = two_digits_at(bytes, 5)?;
    if bytes.get(7) != Some(&b'-') {
        return None;
    }
    let day = two_digits_at(bytes, 8)?;
    match bytes.get(10) {
        Some(&b'T') | Some(&b' ') => {}
        _ => return None,
    }
    let hour = two_digits_at(bytes, 11)?;
    if bytes.get(13) != Some(&b':') {
        return None;
    }
    let minute = two_digits_at(bytes, 14)?;
    let (second, micros, position) = match bytes.get(16) {
        Some(&b':') => {
            let second = two_digits_at(bytes, 17)?;
            if bytes.get(19) != Some(&b'.') {
                (second, 0, 19)
            } else {
                let mut count = 0usize;
                let mut fraction = 0u32;
                while let Some(&byte) = bytes.get(20 + count) {
                    if !byte.is_ascii_digit() {
                        break;
                    }
                    if count < 6 {
                        fraction = fraction * 10 + u32::from(byte - b'0');
                    }
                    count += 1;
                }
                if count == 0 {
                    return None;
                }
                let micros = if count > 6 {
                    fraction
                } else {
                    fraction * 10_u32.pow(6 - count as u32)
                };
                (second, micros, 20 + count)
            }
        }
        _ => (0, 0, 16),
    };
    let offset_seconds = match bytes.get(position) {
        None => 0,
        Some(&b'Z') | Some(&b'z') => {
            if bytes.len() != position + 1 {
                return None;
            }
            0
        }
        Some(&sign @ (b'+' | b'-')) => {
            if bytes.len() != position + 6 {
                return None;
            }
            let offset_hour = two_digits_at(bytes, position + 1)?;
            if bytes.get(position + 3) != Some(&b':') {
                return None;
            }
            let offset_minute = two_digits_at(bytes, position + 4)?;
            if offset_hour > 23 || offset_minute > 59 {
                return None;
            }
            let magnitude = i64::from(offset_hour) * 3_600 + i64::from(offset_minute) * 60;
            if sign == b'-' {
                -magnitude
            } else {
                magnitude
            }
        }
        Some(_) => return None,
    };
    let month = Month::try_from(u8::try_from(month).ok()?).ok()?;
    let date = Date::from_calendar_date(year, month, u8::try_from(day).ok()?).ok()?;
    let clock = Time::from_hms_micro(
        u8::try_from(hour).ok()?,
        u8::try_from(minute).ok()?,
        u8::try_from(second).ok()?,
        micros,
    )
    .ok()?;
    let naive = PlainDateTime::new(date, clock);
    let instant = if offset_seconds == 0 {
        naive.assume_utc()
    } else {
        let offset = UtcOffset::from_hms(
            (offset_seconds / 3_600) as i8,
            ((offset_seconds % 3_600) / 60) as i8,
            0,
        )
        .ok()?;
        naive.assume_offset(offset)
    };
    // Years stay within 0001..=9999, so seconds times a million cannot
    // leave i64 microseconds.
    Some(instant.unix_timestamp() * 1_000_000 + i64::from(instant.nanosecond() / 1_000))
}

/// The pure half of ``params.time``'s range check: a parsed time is
/// supported from 1900-01-01T00:00:00Z up to thirty days past ``now``.
pub(crate) fn time_in_supported_range_core(parsed_us: i64, now_epoch_us: i64) -> bool {
    let latest_us = now_epoch_us.saturating_add(THIRTY_DAYS_US);
    (MIN_SUPPORTED_US..=latest_us).contains(&parsed_us)
}

/// Validate one request's RFC 3339 time field.
///
/// Mirrors ``params.time``: the value must be a string of at most 40
/// characters (Python ``len``, so code points) naming a supported RFC 3339
/// time, and the parsed instant must lie between 1900-01-01T00:00:00Z and
/// ``now_epoch_us`` plus thirty days.  The current time is supplied by the
/// caller because Python's ``now()`` is fixture-pinned.  Failures raise
/// ``BridgeError`` with the original ``INVALID_ARGUMENT`` message; success
/// returns the UTC microseconds.
#[pyfunction]
fn bridge_validate_rfc3339_time(
    value: &Bound<'_, PyAny>,
    key: &str,
    now_epoch_us: i64,
) -> PyResult<i64> {
    let py = value.py();
    let not_a_time = format!("{key} must be an RFC 3339 time");
    let text = match value.cast::<PyString>() {
        Ok(text) => match text.to_str() {
            Ok(raw) => raw,
            Err(_) => return Err(invalid_argument(py, &not_a_time)),
        },
        Err(_) => return Err(invalid_argument(py, &not_a_time)),
    };
    if text.chars().count() > 40 {
        return Err(invalid_argument(py, &not_a_time));
    }
    let Some(micros) = parse_rfc3339_us(text) else {
        return Err(invalid_argument(py, &not_a_time));
    };
    if !time_in_supported_range_core(micros, now_epoch_us) {
        return Err(invalid_argument(
            py,
            &format!("{key} is outside the supported range"),
        ));
    }
    Ok(micros)
}

// ---------------------------------------------------------------------------
// UTC formatting (params.iso)
// ---------------------------------------------------------------------------

/// The pure half of ``params.iso``: whole UTC seconds formatted as
/// ``%Y-%m-%dT%H:%M:%SZ`` for any year Python's ``datetime`` can hold.
pub(crate) fn format_iso_utc_core(epoch_seconds: i64) -> Option<String> {
    let stamp = OffsetDateTime::from_unix_timestamp(epoch_seconds).ok()?;
    let year = stamp.year();
    if !(1..=9_999).contains(&year) {
        return None;
    }
    Some(format!(
        "{year:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        u8::from(stamp.month()),
        stamp.day(),
        stamp.hour(),
        stamp.minute(),
        stamp.second()
    ))
}

/// Format UTC seconds as RFC 3339 with whole seconds.
///
/// The bridge's ``iso`` shape, ``2026-06-26T00:00:00Z``.  Seconds outside
/// the ``datetime`` year range raise ``ValueError`` (Python callers never
/// hold one).
#[pyfunction]
fn bridge_format_iso_utc(epoch_seconds: i64) -> PyResult<String> {
    format_iso_utc_core(epoch_seconds)
        .ok_or_else(|| PyValueError::new_err("epoch seconds are outside the supported range"))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(bridge_decode_request_frame, module)?)?;
    module.add_function(wrap_pyfunction!(bridge_check_request_params, module)?)?;
    module.add_function(wrap_pyfunction!(bridge_encode_frame_check, module)?)?;
    module.add_function(wrap_pyfunction!(bridge_validate_rfc3339_time, module)?)?;
    module.add_function(wrap_pyfunction!(bridge_format_iso_utc, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn owned(strings: &[&str]) -> Vec<String> {
        strings.iter().map(|string| (*string).to_string()).collect()
    }

    // -----------------------------------------------------------------
    // Frame decoding
    // -----------------------------------------------------------------

    #[test]
    fn frames_decode_after_stripping_one_terminator_pair() {
        let plain = decode_request_frame_core(
            b"{\"id\":1,\"method\":\"hello\",\"params\":{\"protocol_version\":1}}\n",
        )
        .unwrap();
        assert_eq!(plain.request_id, 1);
        assert_eq!(plain.method, "hello");
        assert_eq!(
            plain.params.get("protocol_version").and_then(Value::as_i64),
            Some(1)
        );
        let crlf = decode_request_frame_core(
            b"{\"id\":1,\"method\":\"hello\",\"params\":{\"protocol_version\":1}}\r\n",
        )
        .unwrap();
        assert_eq!(crlf.request_id, 1);
        let bare = decode_request_frame_core(
            b"{\"id\":1,\"method\":\"hello\",\"params\":{\"protocol_version\":1}}",
        )
        .unwrap();
        assert_eq!(bare.method, "hello");
    }

    #[test]
    fn nested_params_survive_decoding() {
        let decoded = decode_request_frame_core(
            b"{\"id\":2,\"method\":\"soundings.snapshot\",\"params\":{\"list\":[1,2.5,\"x\",null,true],\"nested\":{\"deep\":-3}}}",
        )
        .unwrap();
        let list = decoded.params.get("list").expect("list present");
        assert_eq!(list[0], Value::from(1));
        assert_eq!(list[1], Value::from(2.5));
        assert_eq!(list[2], Value::from("x"));
        assert_eq!(list[3], Value::Null);
        assert_eq!(list[4], Value::from(true));
        assert_eq!(
            decoded.params.get("nested").expect("nested")["deep"],
            Value::from(-3)
        );
    }

    #[test]
    fn oversized_frames_are_rejected_before_parsing() {
        let over = vec![b'x'; MAX_FRAME_BYTES + 1];
        let reject = decode_request_frame_core(&over).unwrap_err();
        assert_eq!(reject.code, "BRIDGE_PROTOCOL");
        assert_eq!(reject.message, "Request frame exceeds the 8 MiB limit.");
        assert_eq!(reject.request_id, None);
    }

    #[test]
    fn unparsable_frames_are_rejected_without_an_id() {
        for line in [
            &b"not json\n"[..],
            b"{\"id\": 1, \"method\": \"hello\", \"params\": {\"protocol_version\": NaN}}\n",
            b"\xff\xfe\n",
        ] {
            let reject = decode_request_frame_core(line).unwrap_err();
            assert_eq!(reject.code, "BRIDGE_PROTOCOL");
            assert_eq!(reject.message, "Request frame is not valid JSON.");
            assert_eq!(reject.request_id, None);
        }
        // A valid JSON document that is not an object.
        let reject = decode_request_frame_core(b"[1, 2]\n").unwrap_err();
        assert_eq!(reject.message, "Request frame is not a JSON object.");
        assert_eq!(reject.request_id, None);
    }

    #[test]
    fn ids_must_be_exact_positive_integers_in_range() {
        let largest = format!("{{\"id\":{MAX_REQUEST_ID},\"method\":\"m\",\"params\":{{}}}}");
        let decoded = decode_request_frame_core(largest.as_bytes()).unwrap();
        assert_eq!(decoded.request_id, MAX_REQUEST_ID);
        for line in [
            &b"{\"id\":0,\"method\":\"hello\",\"params\":{}}\n"[..],
            b"{\"id\":-1,\"method\":\"hello\",\"params\":{}}\n",
            b"{\"id\":true,\"method\":\"hello\",\"params\":{}}\n",
            b"{\"id\":\"1\",\"method\":\"hello\",\"params\":{}}\n",
            b"{\"id\":1.5,\"method\":\"hello\",\"params\":{}}\n",
            b"{\"id\":1.0,\"method\":\"hello\",\"params\":{}}\n",
            b"{\"method\":\"hello\",\"params\":{}}\n",
            b"{\"id\":18446744073709551616,\"method\":\"hello\",\"params\":{}}\n",
        ] {
            let reject = decode_request_frame_core(line).unwrap_err();
            assert_eq!(reject.code, "BRIDGE_PROTOCOL");
            assert_eq!(reject.message, "Request frame has no valid id.");
            assert_eq!(reject.request_id, None, "{line:?}");
        }
        // MAX_REQUEST_ID + 1 still parses as an integer but is out of band.
        let past = format!(
            "{{\"id\":{},\"method\":\"m\",\"params\":{{}}}}\n",
            MAX_REQUEST_ID + 1
        );
        let reject = decode_request_frame_core(past.as_bytes()).unwrap_err();
        assert_eq!(reject.message, "Request frame has no valid id.");
    }

    #[test]
    fn envelope_key_mismatches_render_python_list_reprs() {
        let reject = decode_request_frame_core(b"{\"id\":4,\"method\":\"hello\"}\n").unwrap_err();
        assert_eq!(reject.code, "BRIDGE_PROTOCOL");
        assert_eq!(
            reject.message,
            "Request envelope keys are wrong (missing ['params'], unexpected [])."
        );
        assert_eq!(reject.request_id, Some(4));
        let reject = decode_request_frame_core(
            b"{\"id\":4,\"method\":\"hello\",\"params\":{},\"extra\":1}\n",
        )
        .unwrap_err();
        assert_eq!(
            reject.message,
            "Request envelope keys are wrong (missing [], unexpected ['extra'])."
        );
        let reject = decode_request_frame_core(b"{\"id\":4,\"extra\":2}\n").unwrap_err();
        assert_eq!(
            reject.message,
            "Request envelope keys are wrong (missing ['method', 'params'], unexpected ['extra'])."
        );
        assert_eq!(reject.request_id, Some(4));
    }

    #[test]
    fn methods_must_be_non_empty_strings() {
        for line in [
            &b"{\"id\":4,\"method\":7,\"params\":{}}\n"[..],
            b"{\"id\":4,\"method\":\"\",\"params\":{}}\n",
            b"{\"id\":4,\"method\":null,\"params\":{}}\n",
        ] {
            let reject = decode_request_frame_core(line).unwrap_err();
            assert_eq!(reject.code, "BRIDGE_PROTOCOL");
            assert_eq!(reject.message, "Request method must be a non-empty string.");
            assert_eq!(reject.request_id, Some(4), "{line:?}");
        }
    }

    #[test]
    fn params_must_be_objects() {
        for line in [
            &b"{\"id\":4,\"method\":\"health\",\"params\":[]}\n"[..],
            b"{\"id\":4,\"method\":\"health\",\"params\":\"x\"}\n",
            b"{\"id\":4,\"method\":\"health\",\"params\":null}\n",
        ] {
            let reject = decode_request_frame_core(line).unwrap_err();
            assert_eq!(reject.code, "INVALID_ARGUMENT");
            assert_eq!(
                reject.message,
                "The request was not valid: params must be an object."
            );
            assert_eq!(reject.request_id, Some(4), "{line:?}");
        }
    }

    // -----------------------------------------------------------------
    // Per-method parameter validation
    // -----------------------------------------------------------------

    #[test]
    fn handler_validated_methods_check_their_own_parameters() {
        assert_eq!(HANDLER_VALIDATED.len(), 38);
        for method in HANDLER_VALIDATED {
            assert!(
                check_request_params_core(method, &owned(&["anything"]), &HelloVersion::NotInt)
                    .is_ok(),
                "{method} validates its own parameters"
            );
        }
    }

    #[test]
    fn hello_takes_exactly_the_protocol_version() {
        let keys = owned(&["protocol_version"]);
        assert_eq!(
            check_request_params_core("hello", &keys, &HelloVersion::One),
            Ok(())
        );
        assert_eq!(
            check_request_params_core("hello", &keys, &HelloVersion::Other("2".to_string()))
                .unwrap_err(),
            "The request was not valid: protocol_version 2 is not supported \
(this bridge speaks 1)."
        );
        for keys in [
            owned(&[]),
            owned(&["protocol_version", "x"]),
            owned(&["other"]),
        ] {
            assert_eq!(
                check_request_params_core("hello", &keys, &HelloVersion::One).unwrap_err(),
                "The request was not valid: hello takes exactly an integer protocol_version.",
                "{keys:?}"
            );
        }
        // A present key with a non-integer value is the same failure.
        assert_eq!(
            check_request_params_core("hello", &keys, &HelloVersion::NotInt).unwrap_err(),
            "The request was not valid: hello takes exactly an integer protocol_version."
        );
    }

    #[test]
    fn other_methods_take_no_parameters() {
        let empty: Vec<String> = Vec::new();
        assert_eq!(
            check_request_params_core("stations.search", &empty, &HelloVersion::NotInt),
            Ok(())
        );
        // Unknown methods only fail dispatch, not the params check.
        assert_eq!(
            check_request_params_core("ensemble.summary", &empty, &HelloVersion::NotInt),
            Ok(())
        );
        assert_eq!(
            check_request_params_core("stations.search", &owned(&["q"]), &HelloVersion::NotInt)
                .unwrap_err(),
            "The request was not valid: stations.search takes no parameters."
        );
    }

    // -----------------------------------------------------------------
    // Frame encoding
    // -----------------------------------------------------------------

    #[test]
    fn frames_gain_a_terminator_under_the_cap() {
        assert_eq!(encode_frame_check_core(b"").unwrap(), b"\n".to_vec());
        assert_eq!(
            encode_frame_check_core(b"data").unwrap(),
            b"data\n".to_vec()
        );
        let at_cap = encode_frame_check_core(&vec![b'x'; MAX_FRAME_BYTES]).unwrap();
        assert_eq!(at_cap.len(), MAX_FRAME_BYTES + 1);
        assert_eq!(at_cap.last(), Some(&b'\n'));
        let over = encode_frame_check_core(&vec![b'x'; MAX_FRAME_BYTES + 1]).unwrap_err();
        assert_eq!(
            over,
            format!(
                "frame of {} bytes exceeds {}",
                MAX_FRAME_BYTES + 1,
                MAX_FRAME_BYTES
            )
        );
    }

    // -----------------------------------------------------------------
    // RFC 3339 parsing and range
    // -----------------------------------------------------------------

    #[test]
    fn rfc3339_forms_parse_to_utc_microseconds() {
        assert_eq!(
            parse_rfc3339_us("2026-06-26T00:00:00Z"),
            Some(1_782_432_000_000_000)
        );
        assert_eq!(
            parse_rfc3339_us("2026-06-26 00:00:00Z"),
            Some(1_782_432_000_000_000)
        );
        assert_eq!(
            parse_rfc3339_us("2026-06-26T00:00:00"),
            Some(1_782_432_000_000_000)
        );
        assert_eq!(
            parse_rfc3339_us("2026-06-26T00:00:00z"),
            Some(1_782_432_000_000_000)
        );
        assert_eq!(
            parse_rfc3339_us("2026-06-26T00:00Z"),
            Some(1_782_432_000_000_000)
        );
        assert_eq!(
            parse_rfc3339_us("2026-06-26T00:00:00+02:00"),
            Some(1_782_424_800_000_000)
        );
        assert_eq!(
            parse_rfc3339_us("2026-06-26T00:00:00-05:00"),
            Some(1_782_450_000_000_000)
        );
        assert_eq!(
            parse_rfc3339_us("2026-06-26T00:00:00.123Z"),
            Some(1_782_432_000_123_000)
        );
        assert_eq!(
            parse_rfc3339_us("2026-06-26T00:00:00.5Z"),
            Some(1_782_432_000_500_000)
        );
        // Extra fractional digits truncate at microseconds.
        assert_eq!(
            parse_rfc3339_us("2026-06-26T00:00:00.123456789012Z"),
            Some(1_782_432_000_123_456)
        );
        assert_eq!(
            parse_rfc3339_us("2024-02-29T00:00:00Z"),
            Some(1_709_164_800_000_000)
        );
        assert_eq!(
            parse_rfc3339_us("2018-09-01T05:00:00Z"),
            Some(1_535_778_000_000_000)
        );
    }

    #[test]
    fn non_rfc3339_forms_are_rejected() {
        for text in [
            "2026-13-01T00:00:00Z",
            "2026-00-01T00:00:00Z",
            "2026-06-00T00:00:00Z",
            "2026-06-32T00:00:00Z",
            "2026-02-30T00:00:00Z",
            "2026-02-29T00:00:00Z",
            "2026-06-26T24:00:00Z",
            "2026-06-26T00:60:00Z",
            "2026-06-26T00:00:60Z",
            "2026-06-26t00:00:00Z",
            "2026-06-26X00:00:00Z",
            "2026-06-26T00:00:00+0200",
            "2026-06-26T00:00:00+02",
            "2026-06-26T00:00:00+99:00",
            "2026-06-26T00:00:00+02:99",
            "2026-06-26T00:00:00 Z",
            "2026-06-26T00:00:00Z ",
            " 2026-06-26T00:00:00Z",
            "2026-06-26T00:00:00ZZ",
            "2026-06-26T00:00.",
            "2026-06-26T00:00:00.",
            "2026-06-26T00:00.5Z",
            "2026-06-26",
            "2026-6-26T00:00:00Z",
            "0000-06-26T00:00:00Z",
            "20260-06-26T00:00:00Z",
            "",
        ] {
            assert!(parse_rfc3339_us(text).is_none(), "{text} should not parse");
        }
    }

    #[test]
    fn times_must_be_within_the_supported_range() {
        let now = 1_782_432_000_000_000_i64;
        assert!(time_in_supported_range_core(MIN_SUPPORTED_US, now));
        assert!(!time_in_supported_range_core(MIN_SUPPORTED_US - 1, now));
        assert!(time_in_supported_range_core(now, now));
        assert!(time_in_supported_range_core(now + THIRTY_DAYS_US, now));
        assert!(!time_in_supported_range_core(now + THIRTY_DAYS_US + 1, now));
        // The window moves with the caller's now, never with the wall clock.
        let later = 2_500_000_000_000_000_i64;
        assert!(time_in_supported_range_core(later, later));
        assert!(!time_in_supported_range_core(later, now));
    }

    // -----------------------------------------------------------------
    // UTC formatting
    // -----------------------------------------------------------------

    #[test]
    fn whole_seconds_format_as_rfc3339_utc() {
        assert_eq!(format_iso_utc_core(0).unwrap(), "1970-01-01T00:00:00Z");
        assert_eq!(
            format_iso_utc_core(1_782_432_000).unwrap(),
            "2026-06-26T00:00:00Z"
        );
        assert_eq!(
            format_iso_utc_core(1_535_778_000).unwrap(),
            "2018-09-01T05:00:00Z"
        );
        assert_eq!(
            format_iso_utc_core(-86_400).unwrap(),
            "1969-12-31T00:00:00Z"
        );
        assert_eq!(
            format_iso_utc_core(253_402_300_799).unwrap(),
            "9999-12-31T23:59:59Z"
        );
        assert_eq!(format_iso_utc_core(253_402_300_800), None);
        assert_eq!(format_iso_utc_core(i64::MAX), None);
        assert_eq!(format_iso_utc_core(i64::MIN), None);
    }

    // -----------------------------------------------------------------
    // Python rendering helpers
    // -----------------------------------------------------------------

    #[test]
    fn list_repr_matches_python_rendering() {
        assert_eq!(py_list_repr(&[]), "[]");
        assert_eq!(py_list_repr(&owned(&["a"])), "['a']");
        assert_eq!(py_list_repr(&owned(&["b", "a"])), "['b', 'a']");
        assert_eq!(py_str_repr("plain"), "'plain'");
        assert_eq!(py_str_repr("it's"), "\"it's\"");
        assert_eq!(py_str_repr("bo'th\""), "'bo\\'th\"'");
    }
}

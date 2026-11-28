//! Native SharpTab clamp, bridge-parameter, and progress rules
//! (milestone M32).
//!
//! Rust ports of the pure validation logic behind four Python modules:
//!
//! * ``sharpmod.sharptab.profile`` -- the physical-range half of
//!   ``_clamp_to_range`` for scalars ([`profile_clamp_scalar_in_range`]) and
//!   the SFC-500 m mean-wind vector form
//!   ([`profile_clamp_vector_in_range`]);
//! * ``sharpmod.bridge.sounding_progress`` -- the ``_id`` check
//!   ([`progress_validate_id`]), the ``DownloadProgress.stage`` phase mapping
//!   ([`progress_stage_phase`]), and the ``DownloadProgress.snapshot``
//!   window arithmetic ([`progress_window_snapshot`]);
//! * ``sharpmod.bridge.params`` -- ``keys`` ([`bridge_validate_param_keys`]),
//!   ``text`` ([`bridge_validate_text`]), ``number``
//!   ([`bridge_validate_number`]), ``integer`` ([`bridge_validate_integer`]),
//!   and ``choice`` ([`bridge_validate_choice`]);
//! * ``sharpmod.backends._common`` -- ``prepare_sfc_index``
//!   ([`backend_validate_sfc_index`]).
//!
//! Error messages match the Python sources verbatim.  The missing/masked
//! sentinel (``MISSING``) crosses the boundary as ``None``: the pure cores
//! stay NumPy-free and the caller maps ``None`` to ``numpy.ma.masked``.
//! Bridge validators raise the Python ``BridgeError`` class with code
//! ``INVALID_ARGUMENT`` (falling back to ``ValueError`` if the class cannot
//! be imported); the backend surface-index validator keeps its plain
//! ``ValueError`` contract.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyString};

// ---------------------------------------------------------------------------
// Bridge error helper (sharpmod.bridge.params.invalid)
// ---------------------------------------------------------------------------

/// Raise the bridge's ``INVALID_ARGUMENT`` ``BridgeError`` with the
/// ``The request was not valid: ...`` message, falling back to
/// ``ValueError`` when the class cannot be imported.
fn invalid(py: Python<'_>, what: &str) -> PyErr {
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
// Physical-range clamps (sharpmod.sharptab.profile._clamp_to_range)
// ---------------------------------------------------------------------------

/// The pure half of ``_clamp_to_range``'s scalar path: a present value stays
/// unchanged only when it is finite and inside its documented bounds;
/// everything else is missing.  ``None`` models the ``MISSING`` sentinel.
pub(crate) fn clamp_scalar_in_range_core(
    value: Option<f64>,
    min_val: f64,
    max_val: f64,
) -> Option<f64> {
    let value = value?;
    if !value.is_finite() {
        return None;
    }
    // Deliberately ordered comparisons (no range negation): NaN bounds
    // disable the clamp, exactly like Python's two-sided check.
    if value < min_val || value > max_val {
        return None;
    }
    Some(value)
}

/// Clamp one derived scalar to its documented physical range.
///
/// ``None`` in is missing, and missing stays missing; a non-finite value
/// (NaN or infinity) or a value outside ``min_val..=max_val`` becomes
/// ``None``; an in-range value is returned unchanged.  The bounds match the
/// ``PARAM_REGISTRY`` ``phys_min``/``phys_max`` of the parameter the caller
/// is validating.
#[pyfunction]
fn profile_clamp_scalar_in_range(value: Option<f64>, min_val: f64, max_val: f64) -> Option<f64> {
    clamp_scalar_in_range_core(value, min_val, max_val)
}

/// The pure half of ``_clamp_to_range``'s vector path: exactly two
/// components are range-checked on their ``hypot`` magnitude, and an
/// in-range vector is returned verbatim -- both components -- so the
/// renderer keeps the full mean wind.
pub(crate) fn clamp_vector_in_range_core(
    values: &[f64],
    min_val: f64,
    max_val: f64,
) -> Option<Vec<f64>> {
    // np.hypot(*value) needs exactly two components; any other length is a
    // TypeError, which the Python clamp turns into MISSING.
    if values.len() != 2 {
        return None;
    }
    let magnitude = values[0].hypot(values[1]);
    if !magnitude.is_finite() {
        return None;
    }
    if magnitude < min_val || magnitude > max_val {
        return None;
    }
    Some(values.to_vec())
}

/// Clamp one derived vector (the ``(u, v)`` SFC-500 m mean wind) to its
/// documented magnitude range.
///
/// Exactly two components are required; the magnitude ``hypot(u, v)`` must
/// be finite and within ``min_val..=max_val``.  A passing vector is
/// returned verbatim, missing data is ``None``, and masked components cross
/// the boundary as NaN, which fails the finite check.
#[pyfunction]
fn profile_clamp_vector_in_range(values: Vec<f64>, min_val: f64, max_val: f64) -> Option<Vec<f64>> {
    clamp_vector_in_range_core(&values, min_val, max_val)
}

// ---------------------------------------------------------------------------
// Download progress rules (sharpmod.bridge.sounding_progress)
// ---------------------------------------------------------------------------

/// The pure half of ``_id``: progress ids are 1 to 64 ASCII letters or
/// digits; anything else is invalid (no case folding, no whitespace).
pub(crate) fn validate_progress_id_core(value: &str) -> Result<String, String> {
    let length = value.chars().count();
    if (1..=64).contains(&length)
        && value.is_ascii()
        && value.chars().all(|c| c.is_ascii_alphanumeric())
    {
        Ok(value.to_string())
    } else {
        Err("progress_id must be 1 to 64 letters or digits".to_string())
    }
}

/// Validate one download progress id.
///
/// The value must be a non-empty string of at most 64 ASCII letters or
/// digits (exactly ``_id``'s checks); failure raises the bridge's
/// ``INVALID_ARGUMENT`` error and success returns the id unchanged.
#[pyfunction]
fn progress_validate_id(value: &Bound<'_, PyAny>) -> PyResult<String> {
    let text = value
        .cast::<PyString>()
        .map_err(|_| invalid(value.py(), "progress_id must be 1 to 64 letters or digits"))?;
    let raw = text
        .to_str()
        .map_err(|_| invalid(value.py(), "progress_id must be 1 to 64 letters or digits"))?;
    validate_progress_id_core(raw).map_err(|message| invalid(value.py(), &message))
}

/// The pure half of ``DownloadProgress.stage``: map one stage word to its
/// user-facing phase, comparing exactly like the Python tuple membership
/// (case-sensitive, no whitespace handling).
pub(crate) fn stage_phase_core(stage: &str) -> &'static str {
    match stage {
        "locating" | "queued" | "validating" => "waiting",
        "downloading" | "retrieving" => "downloading",
        "decoding" | "opening" | "extracting" | "cached" => "decoding",
        "calculating" => "calculating",
        _ => "processing",
    }
}

/// Map one progress stage word to its download phase.
///
/// ``locating``/``queued``/``validating`` wait, ``downloading``/``retrieving``
/// download, ``decoding``/``opening``/``extracting``/``cached`` decode,
/// ``calculating`` calculates, and any other word reports ``processing``
/// (matching the Python cascade exactly).  ``_planned_bytes`` is accepted and
/// ignored, mirroring the Python method signature.
#[pyfunction]
#[pyo3(signature = (stage, _planned_bytes = 0))]
fn progress_stage_phase(stage: &str, _planned_bytes: i64) -> String {
    stage_phase_core(stage).to_string()
}

/// The download-progress snapshot row returned by
/// [`progress_window_snapshot`]:
/// ``(stage, downloaded_bytes, bytes_per_second)`` (Python builds the dict
/// with the keys ``stage``, ``downloaded_bytes``, and ``bytes_per_second``).
pub(crate) type ProgressSnapshotRow = (String, i64, f64);

/// The pure half of ``DownloadProgress.snapshot``: measure the transfer
/// speed over the trailing sample window.
///
/// ``samples`` are the retained ``(timestamp, bytes)`` pairs (oldest
/// first, ``time.monotonic`` seconds on the caller's clock), ``now`` is the
/// current monotonic time, and the baseline is advanced exactly like
/// ``_sample``'s prune loop -- dropping leading samples while the *next*
/// sample is older than three seconds -- so the speed always spans at most
/// the trailing window.  The newest sample contributes nothing to the
/// arithmetic (Python's coalescing append never changes the baseline), so
/// the caller's own sample bookkeeping cannot skew the result.  Speed is
/// ``(downloaded - baseline_bytes) / max(now - baseline_time, 0.1)`` and is
/// reported only while the phase is ``downloading``.
pub(crate) fn window_snapshot_core(
    phase: &str,
    downloaded: i64,
    samples: &[(f64, i64)],
    now: f64,
) -> ProgressSnapshotRow {
    let (start_time, start_bytes) = match samples.first() {
        None => (now, downloaded),
        Some(_) => {
            let mut index = 0;
            while index + 1 < samples.len() && samples[index + 1].0 < now - 3.0 {
                index += 1;
            }
            samples[index]
        }
    };
    let window = now - start_time;
    // Python's max(window, 0.1): 0.1 wins only when strictly larger.
    let denominator = if 0.1 > window { 0.1 } else { window };
    let speed = (downloaded - start_bytes) as f64 / denominator;
    let bytes_per_second = if phase == "downloading" { speed } else { 0.0 };
    (phase.to_string(), downloaded, bytes_per_second)
}

/// Measure one download's progress over its trailing speed window.
///
/// Inputs mirror ``DownloadProgress.snapshot``'s state: the current phase,
/// the total downloaded bytes, the retained ``(monotonic time, bytes)``
/// samples, and the current monotonic time.  The result is
/// ``(stage, downloaded_bytes, bytes_per_second)``: the stage word verbatim,
/// the byte total, and the windowed speed when the phase is
/// ``downloading`` or ``0.0`` otherwise.
#[pyfunction]
fn progress_window_snapshot(
    phase: &str,
    downloaded_bytes: i64,
    samples: Vec<(f64, i64)>,
    now: f64,
) -> ProgressSnapshotRow {
    window_snapshot_core(phase, downloaded_bytes, &samples, now)
}

// ---------------------------------------------------------------------------
// Python value formatting for bridge messages
// ---------------------------------------------------------------------------

/// Python's ``repr`` for the strings that appear in bridge messages:
/// single quotes by default, double quotes when the text already contains
/// one, and backslash/quote escapes otherwise.
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
/// it in the bridge messages.
fn py_list_repr(items: &[String]) -> String {
    let rendered: Vec<String> = items.iter().map(|item| py_str_repr(item)).collect();
    format!("[{}]", rendered.join(", "))
}

/// Python's ``f"{value:g}"`` (C ``%g``, six significant digits): plain
/// decimal notation from ``1e-4`` up to ``1e6``, scientific notation with a
/// two-digit exponent outside that band, and trailing zeros stripped.
fn format_g(value: f64) -> String {
    if value == 0.0 {
        return if value.is_sign_negative() {
            "-0".to_string()
        } else {
            "0".to_string()
        };
    }
    let negative = value < 0.0;
    let magnitude = value.abs();
    let scientific = format!("{magnitude:.5e}");
    let (mantissa, exponent_text) = match scientific.split_once('e') {
        Some(parts) => parts,
        None => return scientific,
    };
    let exponent: i32 = match exponent_text.parse() {
        Ok(exponent) => exponent,
        Err(_) => return scientific,
    };
    let body = if !(-4..6).contains(&exponent) {
        let trimmed = mantissa.trim_end_matches('0').trim_end_matches('.');
        let sign = if exponent < 0 { '-' } else { '+' };
        format!("{trimmed}e{sign}{:02}", exponent.abs())
    } else {
        let decimals = (5 - exponent).max(0) as usize;
        let plain = format!("{magnitude:.decimals$}");
        if plain.contains('.') {
            plain
                .trim_end_matches('0')
                .trim_end_matches('.')
                .to_string()
        } else {
            // An integer rendering keeps its trailing zeros: 123450 is
            // already the shortest %g form.
            plain
        }
    };
    if negative {
        format!("-{body}")
    } else {
        body
    }
}

// ---------------------------------------------------------------------------
// Request parameter validation (sharpmod.bridge.params)
// ---------------------------------------------------------------------------

/// The pure half of ``params.keys``: the missing required keys (in request
/// order) and the unexpected extras (sorted) that make a request invalid.
pub(crate) fn param_keys_core(
    present: &[String],
    required: &[String],
    optional: &[String],
) -> Result<(), String> {
    let allowed: std::collections::HashSet<&str> = required
        .iter()
        .chain(optional.iter())
        .map(String::as_str)
        .collect();
    let missing: Vec<String> = required
        .iter()
        .filter(|key| !present.contains(key))
        .cloned()
        .collect();
    let mut extra: Vec<String> = present
        .iter()
        .filter(|key| !allowed.contains(key.as_str()))
        .cloned()
        .collect();
    extra.sort();
    if missing.is_empty() && extra.is_empty() {
        return Ok(());
    }
    Err(format!(
        "expected {} (optional {}), missing {}, unexpected {}",
        py_list_repr(required),
        py_list_repr(optional),
        py_list_repr(&missing),
        py_list_repr(&extra),
    ))
}

/// Validate the keys of one bridge request's ``params`` object.
///
/// Every key in ``required`` must be present, and anything outside
/// ``required`` and ``optional`` is rejected; the message lists the expected
/// and optional keys and names the missing and unexpected ones with
/// Python's original wording and list rendering.
#[pyfunction]
#[pyo3(signature = (params, required, optional = None))]
fn bridge_validate_param_keys(
    params: &Bound<'_, PyDict>,
    required: Vec<String>,
    optional: Option<Vec<String>>,
) -> PyResult<()> {
    let optional = optional.unwrap_or_default();
    let mut present = Vec::with_capacity(params.len());
    for key in params.keys() {
        let key = key
            .extract::<String>()
            .map_err(|_| invalid(params.py(), "object keys must be text"))?;
        present.push(key);
    }
    param_keys_core(&present, &required, &optional)
        .map_err(|message| invalid(params.py(), &message))
}

/// Validate one text field: a string whose stripped length stays within
/// ``low..=high`` characters; the stripped text is returned.
#[pyfunction]
fn bridge_validate_text(
    value: &Bound<'_, PyAny>,
    field_name: &str,
    low: i64,
    high: i64,
) -> PyResult<String> {
    let message = format!("{field_name} must be text of {low} to {high} characters");
    let text = value
        .cast::<PyString>()
        .map_err(|_| invalid(value.py(), &message))?;
    let raw = text.to_str().map_err(|_| invalid(value.py(), &message))?;
    let stripped = raw.trim();
    let length = stripped.chars().count() as i64;
    if !(low..=high).contains(&length) {
        return Err(invalid(value.py(), &message));
    }
    Ok(stripped.to_string())
}

/// Validate one numeric field: a non-boolean number within ``low..=high``.
#[pyfunction]
fn bridge_validate_number(
    value: &Bound<'_, PyAny>,
    field_name: &str,
    low: f64,
    high: f64,
) -> PyResult<f64> {
    let message = format!("{field_name} must be a number");
    if value.is_instance_of::<PyBool>()
        || !(value.is_instance_of::<PyInt>() || value.is_instance_of::<PyFloat>())
    {
        return Err(invalid(value.py(), &message));
    }
    let result = value.extract::<f64>()?;
    if !result.is_finite() || result < low || result > high {
        return Err(invalid(
            value.py(),
            &format!(
                "{field_name} must be within [{}, {}]",
                format_g(low),
                format_g(high)
            ),
        ));
    }
    Ok(result)
}

/// Validate one integer field: a non-boolean integer within
/// ``low..=high``.
#[pyfunction]
fn bridge_validate_integer(
    value: &Bound<'_, PyAny>,
    field_name: &str,
    low: i64,
    high: i64,
) -> PyResult<i64> {
    let message = format!("{field_name} must be an integer within [{low}, {high}]");
    if value.is_instance_of::<PyBool>() || !value.is_instance_of::<PyInt>() {
        return Err(invalid(value.py(), &message));
    }
    let number = value
        .extract::<i64>()
        .map_err(|_| invalid(value.py(), &message))?;
    if !(low..=high).contains(&number) {
        return Err(invalid(value.py(), &message));
    }
    Ok(number)
}

/// Validate one choice field: a string drawn from the allowed values, in
/// their documented order.
#[pyfunction]
fn bridge_validate_choice(
    value: &Bound<'_, PyAny>,
    field_name: &str,
    allowed: Vec<String>,
) -> PyResult<String> {
    let text = value.cast::<PyString>().map_err(|_| {
        invalid(
            value.py(),
            &format!("{field_name} must be one of {}", allowed.join(", ")),
        )
    })?;
    let raw = match text.to_str() {
        Ok(raw) => raw,
        Err(_) => {
            return Err(invalid(
                value.py(),
                &format!("{field_name} must be one of {}", allowed.join(", ")),
            ))
        }
    };
    if !allowed.iter().any(|choice| choice == raw) {
        return Err(invalid(
            value.py(),
            &format!("{field_name} must be one of {}", allowed.join(", ")),
        ));
    }
    Ok(raw.to_string())
}

// ---------------------------------------------------------------------------
// Surface index validation (sharpmod.backends._common.prepare_sfc_index)
// ---------------------------------------------------------------------------

/// Result of ``int(idx)`` plus the exactness test in ``prepare_sfc_index``.
pub(crate) enum SfcCoercion {
    /// An exact integer usable as the profile index.
    Exact(i64),
    /// An exact integer whose magnitude left ``i64`` but not ``float``
    /// range; Python reports it as out-of-profile, not unconvertible.
    PastI64,
    /// ``int``/``float`` failed or the value was not an exact integer.
    NotAnIndex,
}

/// Python accepts one underscore between two ASCII decimal digits when the
/// ``int`` constructor parses a string; any other underscore invalidates it.
fn python_underscores_valid(text: &str) -> bool {
    let bytes = text.as_bytes();
    !bytes.iter().enumerate().any(|(index, byte)| {
        *byte == b'_'
            && (index == 0
                || index + 1 == bytes.len()
                || !bytes[index - 1].is_ascii_digit()
                || !bytes[index + 1].is_ascii_digit())
    })
}

/// The pure half of ``prepare_sfc_index``'s string path: an int-like string
/// works when it also survives the ``float(sfc) == float(index)`` test,
/// which needs a magnitude within float range.
fn sfc_index_from_string(raw: &str) -> SfcCoercion {
    let trimmed = raw.trim();
    let (negative, magnitude) = match trimmed.as_bytes().first() {
        Some(b'+') => (false, &trimmed[1..]),
        Some(b'-') => (true, &trimmed[1..]),
        Some(_) => (false, trimmed),
        None => return SfcCoercion::NotAnIndex,
    };
    if magnitude.is_empty() || !python_underscores_valid(magnitude) {
        return SfcCoercion::NotAnIndex;
    }
    let mut digits = String::with_capacity(magnitude.len());
    for byte in magnitude.bytes() {
        if byte == b'_' {
            continue;
        }
        if !byte.is_ascii_digit() {
            return SfcCoercion::NotAnIndex;
        }
        digits.push(byte as char);
    }
    if let Ok(index) = digits.parse::<i64>() {
        return SfcCoercion::Exact(if negative { -index } else { index });
    }
    // Beyond i64: float(value) == float(index) holds while the magnitude
    // parses as a finite float, and fails past float range.
    match digits.parse::<f64>() {
        Ok(number) if number.is_finite() => SfcCoercion::PastI64,
        _ => SfcCoercion::NotAnIndex,
    }
}

/// Recognize ``numpy`` boolean scalars (``np.bool_``/``np.bool``), which
/// behave like booleans in ``prepare_sfc_index``'s first check: integer
/// coercible, but explicitly rejected.
fn is_numpy_bool(value: &Bound<'_, PyAny>) -> bool {
    let type_object = value.get_type();
    let module = type_object
        .getattr("__module__")
        .and_then(|module| module.extract::<String>())
        .unwrap_or_default();
    if !module.starts_with("numpy") {
        return false;
    }
    match type_object.name() {
        Ok(name) => matches!(name.to_str().ok(), Some("bool") | Some("bool_")),
        Err(_) => false,
    }
}

/// Coerce one surface index the way ``prepare_sfc_index`` does: booleans
/// (Python and NumPy) are rejected first, then ``int(idx)`` accepts exact
/// integers -- truncating whole floats, parsing whole int strings, and
/// honouring ``__index__`` objects like NumPy integers -- as long as the
/// ``float(idx) == float(index)`` exactness test holds.
pub(crate) fn sfc_coercion_core(value: &Bound<'_, PyAny>) -> PyResult<SfcCoercion> {
    if is_numpy_bool(value) || value.is_instance_of::<PyBool>() {
        return Ok(SfcCoercion::NotAnIndex);
    }
    if value.is_instance_of::<PyFloat>() {
        // int(float) truncates; nan/inf raise inside prepare_sfc_index's
        // caught int() call, and a fractional value fails the exactness test.
        let number = value.extract::<f64>()?;
        if !number.is_finite() || number.fract() != 0.0 {
            return Ok(SfcCoercion::NotAnIndex);
        }
        let truncated = number.trunc();
        if truncated.abs() >= 9_223_372_036_854_775_808.0 {
            return Ok(SfcCoercion::PastI64);
        }
        return Ok(SfcCoercion::Exact(truncated as i64));
    }
    if let Ok(text) = value.cast::<PyString>() {
        let raw = match text.to_str() {
            Ok(raw) => raw,
            Err(_) => return Ok(SfcCoercion::NotAnIndex),
        };
        return Ok(sfc_index_from_string(raw));
    }
    if let Ok(index) = value.extract::<i64>() {
        // Python ints and other __index__ objects (NumPy integers).
        return Ok(SfcCoercion::Exact(index));
    }
    if value.is_instance_of::<PyInt>() {
        // A big integer beyond i64: exact while its float conversion fits,
        // unconvertible past float range.
        return match value.extract::<f64>() {
            Ok(_) => Ok(SfcCoercion::PastI64),
            Err(_) => Ok(SfcCoercion::NotAnIndex),
        };
    }
    Ok(SfcCoercion::NotAnIndex)
}

/// Validate and return one profile's surface index
/// (``prepare_sfc_index``).
///
/// ``idx`` coerces like Python's ``int``: booleans and NumPy booleans are
/// rejected outright, exact integers pass, whole floats (``2.0``) and whole
/// int strings (``"2"``) are accepted, and anything else -- fractional,
/// non-finite, or non-numeric -- reports ``sfc must be an integer profile
/// index``.  An accepted index must lie inside the profile: ``0 <= idx <
/// len`` for a non-empty ``len``, and exactly ``0`` when ``len`` is
/// zero; violators report ``sfc is outside the profile``.  Both failures
/// are plain ``ValueError``s, exactly as in Python.
#[pyfunction]
fn backend_validate_sfc_index(idx: &Bound<'_, PyAny>, len: i64) -> PyResult<i64> {
    let index = match sfc_coercion_core(idx)? {
        SfcCoercion::Exact(index) => index,
        // An exact magnitude beyond i64 is nonzero and nowhere near the
        // profile: the range check rejects it either way.
        SfcCoercion::PastI64 => {
            return Err(PyValueError::new_err("sfc is outside the profile"));
        }
        SfcCoercion::NotAnIndex => {
            return Err(PyValueError::new_err(
                "sfc must be an integer profile index",
            ));
        }
    };
    if len != 0 {
        if !(0..len).contains(&index) {
            return Err(PyValueError::new_err("sfc is outside the profile"));
        }
    } else if index != 0 {
        return Err(PyValueError::new_err("sfc is outside the profile"));
    }
    Ok(index)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(profile_clamp_scalar_in_range, module)?)?;
    module.add_function(wrap_pyfunction!(profile_clamp_vector_in_range, module)?)?;
    module.add_function(wrap_pyfunction!(progress_validate_id, module)?)?;
    module.add_function(wrap_pyfunction!(progress_stage_phase, module)?)?;
    module.add_function(wrap_pyfunction!(progress_window_snapshot, module)?)?;
    module.add_function(wrap_pyfunction!(bridge_validate_param_keys, module)?)?;
    module.add_function(wrap_pyfunction!(bridge_validate_text, module)?)?;
    module.add_function(wrap_pyfunction!(bridge_validate_number, module)?)?;
    module.add_function(wrap_pyfunction!(bridge_validate_integer, module)?)?;
    module.add_function(wrap_pyfunction!(bridge_validate_choice, module)?)?;
    module.add_function(wrap_pyfunction!(backend_validate_sfc_index, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scalar_clamp_keeps_finite_in_range_values() {
        assert_eq!(clamp_scalar_in_range_core(Some(3.0), 0.0, 100.0), Some(3.0));
        assert_eq!(clamp_scalar_in_range_core(Some(0.0), 0.0, 100.0), Some(0.0));
        assert_eq!(
            clamp_scalar_in_range_core(Some(100.0), 0.0, 100.0),
            Some(100.0)
        );
        assert_eq!(
            clamp_scalar_in_range_core(Some(-90.5), -90.5, 90.5),
            Some(-90.5)
        );
    }

    #[test]
    fn scalar_clamp_masks_missing_nonfinite_and_out_of_range() {
        assert_eq!(clamp_scalar_in_range_core(None, 0.0, 1.0), None);
        assert_eq!(clamp_scalar_in_range_core(Some(f64::NAN), 0.0, 1.0), None);
        assert_eq!(
            clamp_scalar_in_range_core(Some(f64::INFINITY), 0.0, 1.0),
            None
        );
        assert_eq!(
            clamp_scalar_in_range_core(Some(f64::NEG_INFINITY), 0.0, 1.0),
            None
        );
        assert_eq!(clamp_scalar_in_range_core(Some(-1.0), 0.0, 1.0), None);
        assert_eq!(clamp_scalar_in_range_core(Some(1.5), 0.0, 1.0), None);
        // NaN bounds disable the corresponding comparison, exactly like Python's.
        assert_eq!(
            clamp_scalar_in_range_core(Some(0.5), f64::NAN, 1.0),
            Some(0.5)
        );
        assert_eq!(clamp_scalar_in_range_core(Some(7.7), f64::NAN, 1.0), None);
        assert_eq!(
            clamp_scalar_in_range_core(Some(7.7), f64::NAN, f64::NAN),
            Some(7.7)
        );
    }

    #[test]
    fn vector_clamp_checks_the_hypot_magnitude() {
        let kept = clamp_vector_in_range_core(&[3.0, 4.0], 0.0, 5.0);
        assert_eq!(
            kept,
            Some(vec![3.0, 4.0]),
            "the magnitude boundary is inclusive"
        );
        assert_eq!(clamp_vector_in_range_core(&[3.0, 4.0], 0.0, 4.999), None);
        assert_eq!(clamp_vector_in_range_core(&[3.0, 4.0], 5.0001, 99.0), None);
        assert_eq!(
            clamp_vector_in_range_core(&[0.0, 0.0], 0.0, 1.0),
            Some(vec![0.0, 0.0])
        );
    }

    #[test]
    fn vector_clamp_requires_exactly_two_finite_components() {
        assert_eq!(clamp_vector_in_range_core(&[], 0.0, 1.0), None);
        assert_eq!(clamp_vector_in_range_core(&[1.0], 0.0, 1.0), None);
        assert_eq!(clamp_vector_in_range_core(&[1.0, 2.0, 2.0], 0.0, 9.0), None);
        assert_eq!(clamp_vector_in_range_core(&[f64::NAN, 1.0], 0.0, 9.0), None);
        assert_eq!(
            clamp_vector_in_range_core(&[f64::INFINITY, 1.0], 0.0, 9.0),
            None
        );
    }

    #[test]
    fn progress_ids_accept_ascii_letters_and_digits_only() {
        assert_eq!(validate_progress_id_core("A1").unwrap(), "A1");
        let long = "a".repeat(64);
        assert!(validate_progress_id_core(&long).is_ok());
        assert!(validate_progress_id_core(&"a".repeat(65)).is_err());
        assert!(validate_progress_id_core("").is_err());
        assert!(validate_progress_id_core("a b").is_err());
        assert!(validate_progress_id_core("a-b").is_err());
        assert!(validate_progress_id_core("a_b").is_err());
        assert!(validate_progress_id_core("àb").is_err(), "non-ASCII");
        assert_eq!(
            validate_progress_id_core("").unwrap_err(),
            "progress_id must be 1 to 64 letters or digits"
        );
    }

    #[test]
    fn stage_words_map_to_phases_verbatim() {
        assert_eq!(stage_phase_core("locating"), "waiting");
        assert_eq!(stage_phase_core("queued"), "waiting");
        assert_eq!(stage_phase_core("validating"), "waiting");
        assert_eq!(stage_phase_core("downloading"), "downloading");
        assert_eq!(stage_phase_core("retrieving"), "downloading");
        assert_eq!(stage_phase_core("decoding"), "decoding");
        assert_eq!(stage_phase_core("opening"), "decoding");
        assert_eq!(stage_phase_core("extracting"), "decoding");
        assert_eq!(stage_phase_core("cached"), "decoding");
        assert_eq!(stage_phase_core("calculating"), "calculating");
        assert_eq!(stage_phase_core("finished"), "processing");
        assert_eq!(
            stage_phase_core("cached "),
            "processing",
            "membership is exact, not stripped"
        );
        assert_eq!(
            stage_phase_core("Cached"),
            "processing",
            "membership is case-sensitive"
        );
    }

    #[test]
    fn snapshot_measures_speed_over_the_trailing_window() {
        let samples = [(10.0, 0), (10.5, 100), (12.0, 500)];
        let (stage, bytes, speed) = window_snapshot_core("downloading", 600, &samples, 12.5);
        assert_eq!(stage, "downloading");
        assert_eq!(bytes, 600);
        assert_eq!(speed, 600.0 / (12.5 - 10.0));
    }

    #[test]
    fn snapshot_prunes_the_window_like_sample() {
        // now - 3 lands between the samples at t=10 and t=12, so the
        // baseline advances exactly like the popleft loop.
        let samples = [(10.0, 0), (12.0, 100)];
        let (_, _, speed) = window_snapshot_core("downloading", 600, &samples, 20.0);
        assert_eq!(speed, (600 - 100) as f64 / (20.0 - 12.0));
        // The prune keeps at least one sample: the baseline never advances
        // past the last one.
        let stale = [(1.0, 0)];
        let (_, _, speed) = window_snapshot_core("downloading", 50, &stale, 100.0);
        assert_eq!(speed, 50.0 / 99.0);
    }

    #[test]
    fn snapshot_clamps_the_window_floor_and_zeroes_other_phases() {
        let samples = [(5.0, 0)];
        let (_, _, quick) = window_snapshot_core("downloading", 10, &samples, 5.05);
        assert_eq!(quick, 100.0, "a burst within 0.1 s divides by the floor");
        let (_, _, waiting) = window_snapshot_core("waiting", 10, &samples, 5.05);
        assert_eq!(waiting, 0.0);
        let (_, bytes, empty) = window_snapshot_core("waiting", 10, &[], 4.0);
        assert_eq!(empty, 0.0);
        assert_eq!(bytes, 10);
    }

    #[test]
    fn python_format_g_matches_the_bridge_messages() {
        assert_eq!(format_g(-90.0), "-90");
        assert_eq!(format_g(90.0), "90");
        assert_eq!(format_g(180.0), "180");
        assert_eq!(format_g(1e9), "1e+09");
        assert_eq!(format_g(-1e9), "-1e+09");
        assert_eq!(format_g(0.5), "0.5");
        assert_eq!(format_g(0.0001), "0.0001");
        assert_eq!(format_g(0.00001), "1e-05");
        assert_eq!(format_g(2500.0), "2500");
        assert_eq!(format_g(999_999.0), "999999");
        assert_eq!(format_g(1e6), "1e+06");
        assert_eq!(format_g(1_234_567.0), "1.23457e+06");
        assert_eq!(format_g(0.0), "0");
        assert_eq!(format_g(1e-7), "1e-07");
        assert_eq!(format_g(123_450.0), "123450", "integer forms keep zeros");
        assert_eq!(format_g(250_000.0), "250000");
        assert_eq!(format_g(100_000.0), "100000");
        assert_eq!(format_g(304_500.0), "304500");
    }

    #[test]
    fn list_repr_matches_python_rendering() {
        assert_eq!(py_list_repr(&[]), "[]");
        assert_eq!(
            py_list_repr(&["model".to_string(), "forecast_hour".to_string()]),
            "['model', 'forecast_hour']"
        );
        assert_eq!(py_str_repr("plain"), "'plain'");
        assert_eq!(py_str_repr("it's"), "\"it's\"");
        assert_eq!(py_str_repr("bo'th\""), "'bo\\'th\"'");
    }

    #[test]
    fn param_keys_report_missing_and_unexpected_in_python_order() {
        let result = param_keys_core(
            &["model".to_string(), "extra".to_string()],
            &["model".to_string(), "forecast_hour".to_string()],
            &["run_time".to_string()],
        );
        assert_eq!(
            result.unwrap_err(),
            "expected ['model', 'forecast_hour'] (optional ['run_time']), \
             missing ['forecast_hour'], unexpected ['extra']"
        );
        // Extras are sorted; missing keys keep required order.
        let result = param_keys_core(
            &["zz".to_string(), "aa".to_string()],
            &["k1".to_string(), "k2".to_string()],
            &[],
        );
        assert_eq!(
            result.unwrap_err(),
            "expected ['k1', 'k2'] (optional []), missing ['k1', 'k2'], unexpected ['aa', 'zz']"
        );
        assert!(param_keys_core(
            &["a".to_string(), "b".to_string()],
            &["a".to_string(), "b".to_string()],
            &["c".to_string()]
        )
        .is_ok());
        // Optional keys need not be present and unknown keys are extras.
        assert!(
            param_keys_core(&["a".to_string()], &["a".to_string()], &["b".to_string()]).is_ok()
        );
        assert!(
            param_keys_core(&["a".to_string(), "b".to_string()], &["a".to_string()], &[]).is_err()
        );
    }

    #[test]
    fn sfc_index_strings_follow_python_int_semantics() {
        assert!(matches!(sfc_index_from_string("2"), SfcCoercion::Exact(2)));
        assert!(matches!(
            sfc_index_from_string("-7"),
            SfcCoercion::Exact(-7)
        ));
        assert!(matches!(
            sfc_index_from_string(" 3 "),
            SfcCoercion::Exact(3)
        ));
        assert!(matches!(
            sfc_index_from_string("1_0"),
            SfcCoercion::Exact(10)
        ));
        assert!(matches!(
            sfc_index_from_string("2.0"),
            SfcCoercion::NotAnIndex
        ));
        assert!(matches!(
            sfc_index_from_string("2.5"),
            SfcCoercion::NotAnIndex
        ));
        assert!(matches!(sfc_index_from_string(""), SfcCoercion::NotAnIndex));
        assert!(matches!(
            sfc_index_from_string("nan"),
            SfcCoercion::NotAnIndex
        ));
        assert!(matches!(
            sfc_index_from_string("9223372036854775808"),
            SfcCoercion::PastI64
        ));
        // A magnitude past float range fails float(index) in Python too.
        let huge = "9".repeat(400);
        assert!(matches!(
            sfc_index_from_string(&huge),
            SfcCoercion::NotAnIndex
        ));
        // Between i64 and float range the exactness test still holds.
        let large = "9".repeat(25);
        assert!(matches!(
            sfc_index_from_string(&large),
            SfcCoercion::PastI64
        ));
    }
}

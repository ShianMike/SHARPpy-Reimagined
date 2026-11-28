//! Native export-composition layout and validation rules (milestone M31).
//!
//! Rust ports of the pure bookkeeping half of four render/export Python
//! modules:
//!
//! * ``sharpmod.rendering.export`` -- `compose_widget_pixmap`'s dimension
//!   coercion and caption/scale geometry ([`export_compose_layout`]),
//!   ``_composition_colours``'s static theme triplets
//!   ([`export_composition_theme_hex`]), and the PNG signature/size
//!   validation behind ``save_pixmap_png_atomic`` extended with a trailing
//!   IEND chunk check ([`export_valid_png_file`]);
//! * ``sharpmod.render_patches.render_patch_registry`` -- the spec loop of
//!   ``apply_patch_registry`` ([`patch_registry_validate_specs`]) and
//!   ``validate_sharppy_version`` ([`patch_registry_validate_version`]);
//! * ``sharpmod.tools.box_extract`` -- ``_sequence_hours``
//!   ([`box_extract_sequence_hours`]);
//! * ``sharpmod.tools.observed_sounding`` -- ``_safe_token``
//!   ([`observed_safe_token`]).
//!
//! Error messages match the Python sources verbatim so existing tests keep
//! passing against either implementation.  Where a Python function consults
//! a live object (widget sizes, the QtWidgets palette, installed package
//! metadata, published model hours), the caller supplies that state as plain
//! arguments; the rule itself stays pure.

use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;

use pyo3::exceptions::{PyOverflowError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyFloat, PyInt, PyString};

/// The single SHARPpy build the vendored widget patches target
/// (Python ``SUPPORTED_SHARPPY_VERSIONS``).
const SUPPORTED_SHARPPY_VERSIONS: [&str; 1] = ["1.4.0a5"];

/// Eight-byte PNG stream signature (``save_pixmap_png_atomic``'s header).
const PNG_SIGNATURE: [u8; 8] = *b"\x89PNG\r\n\x1a\n";

/// Complete trailing IEND chunk: zero length, the type tag, and its CRC.
const IEND_CHUNK: [u8; 12] = *b"\x00\x00\x00\x00IEND\xaeB`\x82";

// ---------------------------------------------------------------------------
// Export canvas composition (sharpmod.rendering.export)
// ---------------------------------------------------------------------------

/// Result of the Python ``int`` coercion applied to an export dimension.
enum CoercedDimension {
    /// A usable pixel count.
    Value(i64),
    /// A finite integral magnitude beyond ``i64``; Python keeps it as an
    /// exact integer, so it flows into the 1..=16384 range check and fails
    /// there.
    BeyondRange,
    /// ``int(x)`` raised ``TypeError`` or ``ValueError``, which
    /// ``compose_widget_pixmap`` reports as a pixels error.
    Invalid,
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

/// Port of the string form of Python's ``int`` constructor (base 10 only,
/// matching ``int(x)`` without a base argument).  Non-ASCII decimal digits
/// are rejected, the one deliberate divergence shared with the rest of the
/// crate's Python number ports.
fn python_int_from_string(raw: &str) -> CoercedDimension {
    let trimmed = raw.trim();
    let (negative, magnitude) = match trimmed.as_bytes().first() {
        Some(b'+') => (false, &trimmed[1..]),
        Some(b'-') => (true, &trimmed[1..]),
        Some(_) => (false, trimmed),
        None => return CoercedDimension::Invalid,
    };
    if magnitude.is_empty() || !python_underscores_valid(magnitude) {
        return CoercedDimension::Invalid;
    }
    let mut value: i64 = 0;
    let mut overflowed = false;
    for byte in magnitude.bytes() {
        if byte == b'_' {
            continue;
        }
        if !byte.is_ascii_digit() {
            return CoercedDimension::Invalid;
        }
        if !overflowed {
            match value
                .checked_mul(10)
                .and_then(|scaled| scaled.checked_add(i64::from(byte - b'0')))
            {
                Some(total) => value = total,
                None => overflowed = true,
            }
        }
    }
    if overflowed {
        CoercedDimension::BeyondRange
    } else if negative {
        CoercedDimension::Value(-value)
    } else {
        CoercedDimension::Value(value)
    }
}

/// Coerce one export dimension the way ``int(width)`` does inside
/// ``compose_widget_pixmap``: ``bool`` counts as 0/1 (it is an ``int``
/// subclass), ``__index__`` objects convert, floats truncate toward zero,
/// and strings parse.  A NaN float is a plain ``ValueError``; float infinity
/// raises ``OverflowError``, which the Python ``except`` clause does not
/// catch, so it is raised unchanged.
fn coerce_dimension(value: &Bound<'_, PyAny>) -> PyResult<CoercedDimension> {
    if value.is_instance_of::<PyInt>() {
        return match value.extract::<i64>() {
            Ok(number) => Ok(CoercedDimension::Value(number)),
            // An exact integer too large for i64 still reaches the range
            // check in Python, and no such magnitude fits in 16384 pixels.
            Err(_) => Ok(CoercedDimension::BeyondRange),
        };
    }
    if value.is_instance_of::<PyFloat>() {
        let number = value.extract::<f64>()?;
        if number.is_nan() {
            return Ok(CoercedDimension::Invalid);
        }
        if number.is_infinite() {
            return Err(PyOverflowError::new_err(
                "cannot convert float infinity to integer",
            ));
        }
        let truncated = number.trunc();
        if truncated.abs() >= 9_223_372_036_854_775_808.0 {
            return Ok(CoercedDimension::BeyondRange);
        }
        return Ok(CoercedDimension::Value(truncated as i64));
    }
    if let Ok(text) = value.cast::<PyString>() {
        let raw = match text.to_str() {
            Ok(raw) => raw,
            Err(_) => return Ok(CoercedDimension::Invalid),
        };
        return Ok(python_int_from_string(raw));
    }
    if let Ok(number) = value.extract::<i64>() {
        // Any other object honouring __index__ converts like Python's int().
        return Ok(CoercedDimension::Value(number));
    }
    Ok(CoercedDimension::Invalid)
}

/// Python's two-argument ``min``: ``b`` is kept only when ``b < a``.
fn python_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// The composition geometry returned by [`export_compose_layout`], mirroring
/// everything ``compose_widget_pixmap`` derives before any colour work:
/// `(width, height, content_height, caption_height, scale, rendered_width,
/// rendered_height, offset_x, offset_y, caption_text)`.
pub(crate) type ComposeLayoutRow = (i64, i64, i64, i64, f64, i64, i64, i64, i64, String);

/// The pure half of ``compose_widget_pixmap``: validate the coerced pixel
/// dimensions and derive the caption band, uniform scale, and centring
/// offsets, all with Python's rounding and clamping semantics.
pub(crate) fn compose_layout_core(
    width: i64,
    height: i64,
    logical_width: i64,
    logical_height: i64,
    caption: &str,
) -> Result<ComposeLayoutRow, String> {
    if !(1..=16_384).contains(&width) || !(1..=16_384).contains(&height) {
        return Err("export dimensions must be between 1 and 16384 pixels".to_string());
    }
    let logical_width = logical_width.max(1);
    let logical_height = logical_height.max(1);
    let caption_text = caption.trim();
    let caption_height = if caption_text.is_empty() {
        0
    } else {
        let raw = ((height as f64) * 0.065).round_ties_even() as i64;
        raw.clamp(40, 72)
    };
    let content_height = (height - caption_height).max(1);
    let scale = python_min(
        width as f64 / logical_width as f64,
        content_height as f64 / logical_height as f64,
    );
    let rendered_width = (((logical_width as f64) * scale).round_ties_even() as i64).max(1);
    let rendered_height = (((logical_height as f64) * scale).round_ties_even() as i64).max(1);
    let offset_x = (width - rendered_width).div_euclid(2);
    let offset_y = (content_height - rendered_height).div_euclid(2);
    Ok((
        width,
        height,
        content_height,
        caption_height,
        scale,
        rendered_width,
        rendered_height,
        offset_x,
        offset_y,
        caption_text.to_string(),
    ))
}

/// Coerce a raw Python object the way the caption argument of
/// ``compose_widget_pixmap`` is coerced: ``str(caption or "")``.
fn python_str_or_empty(value: Option<&Bound<'_, PyAny>>) -> PyResult<String> {
    match value {
        None => Ok(String::new()),
        Some(object) => {
            if !object.is_truthy()? {
                return Ok(String::new());
            }
            Ok(object.str()?.to_str()?.to_owned())
        }
    }
}

/// Compose the export canvas geometry for one widget: validated dimensions,
/// caption band, scale, and centring offsets.
///
/// ``width``/``height`` reprise the Python ``int()`` coercion (floats
/// truncate toward zero, booleans count as 0/1, strings parse with Python's
/// underscore rule, NaN fails with the pixels message, and infinities raise
/// ``OverflowError`` exactly as Python does).  The widget's live size is
/// supplied by the caller as ``logical_width``/``logical_height`` -- Python
/// applies ``max(1, int(widget.width()))`` there.  ``caption`` follows
/// ``str(caption or "").strip()``.  Theme colours are resolved separately by
/// [`export_composition_theme_hex`].
#[pyfunction]
#[pyo3(signature = (width, height, logical_width, logical_height, caption = None))]
fn export_compose_layout(
    width: &Bound<'_, PyAny>,
    height: &Bound<'_, PyAny>,
    logical_width: i64,
    logical_height: i64,
    caption: Option<&Bound<'_, PyAny>>,
) -> PyResult<ComposeLayoutRow> {
    // A magnitude beyond i64 cannot satisfy the range check either, so it
    // delegates there and produces the same Python message.
    let to_pixels = |value: &Bound<'_, PyAny>| -> PyResult<i64> {
        match coerce_dimension(value)? {
            CoercedDimension::Value(number) => Ok(number),
            CoercedDimension::BeyondRange => Ok(i64::MAX),
            CoercedDimension::Invalid => Err(PyValueError::new_err(
                "export dimensions must be integer pixels",
            )),
        }
    };
    let width = to_pixels(width)?;
    let height = to_pixels(height)?;
    let caption_text = python_str_or_empty(caption)?;
    compose_layout_core(width, height, logical_width, logical_height, &caption_text)
        .map_err(PyValueError::new_err)
}

/// The pure half of ``_composition_colours``: one theme word selects the
/// ``(background, caption_background, foreground)`` triplet.  Any theme
/// without static anchors -- including "current", which in Python reads the
/// live ``QApplication`` palette -- resolves to the dark fallback colours
/// used whenever no palette is available.
pub(crate) fn composition_theme_hex_core(theme: &str) -> [String; 3] {
    let requested = theme.trim().to_lowercase();
    let triplet = match requested.as_str() {
        "light" => ["#f4f1eb", "#ffffff", "#1a1815"],
        _ => ["#0d0d0c", "#1f1e1b", "#f1efeb"],
    };
    triplet.map(|colour| colour.to_string())
}

/// Resolve a composition theme's ``(background, caption_background,
/// foreground)`` colours as hex strings.
///
/// The theme follows ``str(theme or "current").strip().lower()``: "light"
/// selects the light anchors, and every other value -- "dark", "current",
/// or an unknown word -- selects the offline fallback triplet, which is
/// exactly what Python uses without a valid ``QApplication`` palette.
#[pyfunction]
#[pyo3(signature = (theme = None))]
fn export_composition_theme_hex(
    theme: Option<&Bound<'_, PyAny>>,
) -> PyResult<(String, String, String)> {
    let requested = python_str_or_empty(theme)?;
    let [background, caption_background, foreground] = composition_theme_hex_core(&requested);
    Ok((background, caption_background, foreground))
}

/// The pure half of [`export_valid_png_file`]: the size bound from
/// ``save_pixmap_png_atomic`` (strictly greater than 8 bytes), the signature
/// header, and the complete trailing IEND chunk.
pub(crate) fn valid_png_parts_core(
    size: u64,
    header: &[u8; PNG_SIGNATURE.len()],
    trailer: &[u8; IEND_CHUNK.len()],
) -> bool {
    if size <= PNG_SIGNATURE.len() as u64 {
        return false;
    }
    if size < (PNG_SIGNATURE.len() + IEND_CHUNK.len()) as u64 {
        return false;
    }
    header == &PNG_SIGNATURE && trailer == &IEND_CHUNK
}

/// Resolve a path argument the way ``os.fspath(outfile)`` does: strings and
/// bytes pass through, and path-like objects provide their ``__fspath__``
/// value.  Anything else is simply not a PNG file path.
fn fspath(value: &Bound<'_, PyAny>) -> Option<String> {
    if let Ok(text) = value.cast::<PyString>() {
        return text.to_str().ok().map(|raw| raw.to_owned());
    }
    if let Ok(bytes) = value.cast::<PyBytes>() {
        return String::from_utf8(bytes.as_bytes().to_vec()).ok();
    }
    let resolved = PyModule::import(value.py(), "os")
        .and_then(|module| module.getattr("fspath"))
        .and_then(|fspath| fspath.call1((value,)))
        .ok()?;
    if let Ok(text) = resolved.cast::<PyString>() {
        text.to_str().ok().map(|raw| raw.to_owned())
    } else if let Ok(bytes) = resolved.cast::<PyBytes>() {
        String::from_utf8(bytes.as_bytes().to_vec()).ok()
    } else {
        None
    }
}

/// Whether ``path`` names a complete PNG file.
///
/// The rule carries ``save_pixmap_png_atomic``'s post-write contract -- a
/// size above 8 bytes and the eight signature bytes -- over the whole file:
/// the last twelve bytes must be the complete zero-length ``IEND`` chunk with
/// its CRC, so truncated, padded, or non-PNG exports are rejected.  The path
/// follows ``os.fspath`` and must name an existing regular file; unreadable
/// paths and directories are simply not valid PNG files (``False``), never
/// an exception.
#[pyfunction]
fn export_valid_png_file(path: &Bound<'_, PyAny>) -> bool {
    let Some(path) = fspath(path) else {
        return false;
    };
    let Ok(mut file) = File::open(Path::new(&path)) else {
        return false;
    };
    let Ok(metadata) = file.metadata() else {
        return false;
    };
    if !metadata.is_file() {
        return false;
    }
    let size = metadata.len();
    // At most 8 bytes fails ``os.path.getsize <= 8``; without room for the
    // signature plus the complete IEND chunk no PNG file can exist either.
    let minimum = (PNG_SIGNATURE.len() + IEND_CHUNK.len()) as u64;
    if size <= PNG_SIGNATURE.len() as u64 || size < minimum {
        return false;
    }
    let mut header = [0; PNG_SIGNATURE.len()];
    if file.read_exact(&mut header).is_err() {
        return false;
    }
    let mut trailer = [0; IEND_CHUNK.len()];
    if file
        .seek(SeekFrom::End(-(IEND_CHUNK.len() as i64)))
        .is_err()
        || file.read_exact(&mut trailer).is_err()
    {
        return false;
    }
    valid_png_parts_core(size, &header, &trailer)
}

// ---------------------------------------------------------------------------
// Render patch registry rules
// (sharpmod.render_patches.render_patch_registry)
// ---------------------------------------------------------------------------

/// One patch spec's contribution to the pure validation loop.
pub(crate) struct PatchSpecInput {
    name: String,
    installer_callable: bool,
}

/// The pure half of ``apply_patch_registry``'s spec loop: names cannot be
/// empty, duplicates are rejected, and every installer must be callable --
/// checked in that order, one spec at a time.
pub(crate) fn patch_registry_validate_specs_core(
    specs: &[PatchSpecInput],
) -> Result<Vec<String>, String> {
    let mut seen: Vec<&str> = Vec::with_capacity(specs.len());
    for spec in specs {
        let name = spec.name.trim();
        if name.is_empty() {
            return Err("render patch names cannot be empty".to_string());
        }
        if seen.iter().any(|existing| existing == &name) {
            return Err(format!("duplicate render patch name: {name}"));
        }
        if !spec.installer_callable {
            return Err(format!("render patch installer is not callable: {name}"));
        }
        seen.push(name);
    }
    Ok(seen.iter().map(|name| (*name).to_string()).collect())
}

/// Read the name one patch spec contributes to validation, the way the
/// Python loop does: ``str(getattr(spec, "name", "") or "")``.  A missing
/// attribute or a falsy value is the empty name.
fn spec_name(spec: &Bound<'_, PyAny>) -> PyResult<String> {
    let raw = match spec.getattr("name") {
        Ok(value) => {
            if value.is_truthy()? {
                value.str()?.to_str()?.to_owned()
            } else {
                String::new()
            }
        }
        Err(_) => String::new(),
    };
    Ok(raw)
}

/// Read whether the spec's installer attribute exists and is callable
/// (Python ``callable(getattr(spec, "installer", None))``).
fn spec_installer_callable(spec: &Bound<'_, PyAny>) -> bool {
    match spec.getattr("installer") {
        Ok(installer) => installer.is_callable(),
        Err(_) => false,
    }
}

/// Raise ``RenderPatchError`` or ``UnsupportedSHARPpyVersion`` from the
/// registry, falling back to ``RuntimeError`` when the class cannot be
/// imported (the Python classes subclass ``RuntimeError``).
fn patch_registry_error(py: Python<'_>, class_name: &str, message: String) -> PyErr {
    match PyModule::import(py, "sharpmod.render_patches.render_patch_registry")
        .and_then(|module| module.getattr(class_name))
        .and_then(|class| class.call1((message.clone(),)))
    {
        Ok(error) => PyErr::from_value(error),
        Err(_) => PyRuntimeError::new_err(message),
    }
}

/// Validate one ordered collection of patch specs.
///
/// Each item must expose a non-empty, unique ``name`` (read as
/// ``str(getattr(spec, "name", "") or "").strip()``) and a callable
/// ``installer``; the checks run per spec in order, so the first empty
/// name, duplicate, or non-callable installer raises the Python message
/// verbatim as a ``RenderPatchError``.  On success the normalized stripped
/// names are returned in order.
#[pyfunction]
fn patch_registry_validate_specs<'py>(
    py: Python<'py>,
    specs: Vec<Bound<'py, PyAny>>,
) -> PyResult<Vec<String>> {
    let mut inputs = Vec::with_capacity(specs.len());
    for spec in &specs {
        inputs.push(PatchSpecInput {
            name: spec_name(spec)?,
            installer_callable: spec_installer_callable(spec),
        });
    }
    patch_registry_validate_specs_core(&inputs)
        .map_err(|message| patch_registry_error(py, "RenderPatchError", message))
}

/// The pure half of ``validate_sharppy_version``: the trimmed version must
/// be one of the supported builds; an empty candidate is reported as
/// ``<unknown>`` in the failure message.
pub(crate) fn validate_sharppy_version_core(version: &str) -> Result<String, String> {
    let detected = version.trim();
    if SUPPORTED_SHARPPY_VERSIONS.contains(&detected) {
        return Ok(detected.to_string());
    }
    let supported = SUPPORTED_SHARPPY_VERSIONS.join(", ");
    let shown = if detected.is_empty() {
        "<unknown>"
    } else {
        detected
    };
    Err(format!(
        "SHARppy {shown} is not supported by the render patch registry; expected {supported}"
    ))
}

/// Validate one detected SHARPpy version against the patch registry's
/// supported set, returning the trimmed version.
///
/// The caller supplies the version string -- package detection stays with
/// Python's ``detected_sharppy_version`` -- and a missing or empty value is
/// the registry's ``<unknown>`` case.  Failures raise the registry's
/// ``UnsupportedSHARPpyVersion`` class with the original message.
#[pyfunction]
#[pyo3(signature = (version = None))]
fn patch_registry_validate_version(py: Python<'_>, version: Option<&str>) -> PyResult<String> {
    let raw = version.unwrap_or("");
    validate_sharppy_version_core(raw)
        .map_err(|message| patch_registry_error(py, "UnsupportedSHARPpyVersion", message))
}

// ---------------------------------------------------------------------------
// Box-sequence hour selection (sharpmod.tools.box_extract)
// ---------------------------------------------------------------------------

/// The pure half of ``box_sounding.normalize_hours``: sorted, unique,
/// validated forecast hours.
pub(crate) fn normalize_sequence_hours_core(hours: &[i64]) -> Result<Vec<i64>, String> {
    let mut values = hours.to_vec();
    values.sort_unstable();
    values.dedup();
    if values.is_empty() {
        return Err("a sequence needs at least one forecast hour".to_string());
    }
    if values.iter().any(|hour| hour.is_negative()) {
        return Err("forecast hours cannot be negative".to_string());
    }
    if values.len() > 12 {
        return Err(format!(
            "a box sequence is limited to 12 forecast hours ({} requested); each hour is its own download",
            values.len()
        ));
    }
    Ok(values)
}

/// The pure half of ``_sequence_hours``: resolve the forecast hours to
/// sample from the model's own published cadence.
///
/// Inputs mirror the CLI state: ``model_label`` (``plan.model_label``),
/// ``run_hour`` (``run_dt.hour``), ``published`` (the model's published
/// hours), ``fxx`` (``--fxx``), ``hours`` (``--hours``), and ``hour_step``
/// (``--hour-step``).  The count clamps to at least one, the step to at
/// least one hour, the start must be a published hour, and the remaining
/// sequence stays on published hours at the requested step.  Every failure
/// is the Python message verbatim (the CLI adds its own ``ERROR: `` prefix).
pub(crate) fn box_extract_sequence_hours_core(
    model_label: &str,
    run_hour: i64,
    published: &[i64],
    fxx: i64,
    hours: i64,
    hour_step: i64,
) -> Result<Vec<i64>, String> {
    let start = fxx;
    let count = hours.max(1);
    if !published.contains(&start) {
        return Err(format!(
            "{model_label} does not publish F{start:03} for its {run_hour:02}Z cycle"
        ));
    }
    if count == 1 {
        return Ok(vec![start]);
    }
    let step = hour_step.max(1);
    let mut later = published.to_vec();
    later.sort_unstable();
    let chosen: Vec<i64> = later
        .into_iter()
        .filter(|hour| hour >= &start && (hour - start) % step == 0)
        .take(count as usize)
        .collect();
    if chosen.len() < 2 {
        return Err(format!(
            "{model_label} publishes no further forecast hour at or after F{start:03} with a {step}-hour step"
        ));
    }
    normalize_sequence_hours_core(&chosen)
}

/// Resolve the forecast hours a box sequence samples, or raise the reason
/// with the exact CLI wording.
#[allow(clippy::too_many_arguments)]
#[pyfunction]
fn box_extract_sequence_hours(
    model_label: &str,
    run_hour: i64,
    published: Vec<i64>,
    fxx: i64,
    hours: i64,
    hour_step: i64,
) -> PyResult<Vec<i64>> {
    box_extract_sequence_hours_core(model_label, run_hour, &published, fxx, hours, hour_step)
        .map_err(PyValueError::new_err)
}

// ---------------------------------------------------------------------------
// Observed-station filename tokens
// (sharpmod.tools.observed_sounding)
// ---------------------------------------------------------------------------

/// The pure half of ``_safe_token``: keep letters and digits, replace every
/// other character with ``_``, strip the boundary underscores, and never
/// return an empty token.
pub(crate) fn safe_token_core(value: &str) -> String {
    let token: String = value
        .chars()
        .map(|character| {
            if character.is_alphanumeric() {
                character
            } else {
                '_'
            }
        })
        .collect();
    let stripped = token.trim_matches('_');
    if stripped.is_empty() {
        "station".to_string()
    } else {
        stripped.to_string()
    }
}

/// Filename-safe token for a provider or station label: alphanumeric
/// characters are kept, anything else becomes ``_``, outer underscores are
/// stripped, and an empty result defaults to ``station``.
#[pyfunction]
fn observed_safe_token(value: &str) -> String {
    safe_token_core(value)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(export_compose_layout, module)?)?;
    module.add_function(wrap_pyfunction!(export_composition_theme_hex, module)?)?;
    module.add_function(wrap_pyfunction!(export_valid_png_file, module)?)?;
    module.add_function(wrap_pyfunction!(patch_registry_validate_specs, module)?)?;
    module.add_function(wrap_pyfunction!(patch_registry_validate_version, module)?)?;
    module.add_function(wrap_pyfunction!(box_extract_sequence_hours, module)?)?;
    module.add_function(wrap_pyfunction!(observed_safe_token, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn string_int_coercion_matches_python() {
        assert!(matches!(
            python_int_from_string("800"),
            CoercedDimension::Value(800)
        ));
        assert!(matches!(
            python_int_from_string(" -12 "),
            CoercedDimension::Value(-12)
        ));
        assert!(matches!(
            python_int_from_string("1_000"),
            CoercedDimension::Value(1_000)
        ));
        assert!(matches!(
            python_int_from_string("+7"),
            CoercedDimension::Value(7)
        ));
        assert!(matches!(
            python_int_from_string("800.5"),
            CoercedDimension::Invalid
        ));
        assert!(matches!(
            python_int_from_string("0x10"),
            CoercedDimension::Invalid
        ));
        assert!(matches!(
            python_int_from_string(""),
            CoercedDimension::Invalid
        ));
        assert!(matches!(
            python_int_from_string("_800"),
            CoercedDimension::Invalid
        ));
        assert!(matches!(
            python_int_from_string("800_"),
            CoercedDimension::Invalid
        ));
        assert!(matches!(
            python_int_from_string("8__0"),
            CoercedDimension::Invalid
        ));
        assert!(matches!(
            python_int_from_string("99999999999999999999999"),
            CoercedDimension::BeyondRange
        ));
    }

    #[test]
    fn compose_layout_rejects_out_of_range_dimensions() {
        assert_eq!(
            compose_layout_core(0, 800, 400, 300, "").unwrap_err(),
            "export dimensions must be between 1 and 16384 pixels"
        );
        assert_eq!(
            compose_layout_core(800, 16_385, 400, 300, "").unwrap_err(),
            "export dimensions must be between 1 and 16384 pixels"
        );
        assert_eq!(
            compose_layout_core(-3, 800, 400, 300, "").unwrap_err(),
            "export dimensions must be between 1 and 16384 pixels"
        );
    }

    #[test]
    fn compose_layout_derives_identity_geometry_without_caption() {
        let layout = compose_layout_core(800, 600, 800, 600, "  ").unwrap();
        assert_eq!(layout.3, 0, "a whitespace-only caption is falsy");
        assert_eq!(layout.9, "");
        assert_eq!(layout.2, 600);
        assert_eq!(layout.4, 1.0);
        assert_eq!(layout.5, 800);
        assert_eq!(layout.6, 600);
        assert_eq!(layout.7, 0);
        assert_eq!(layout.8, 0);
    }

    #[test]
    fn compose_layout_scales_down_and_centers() {
        // 808x606 canvas, 404x300 widget, caption band of 40 pixels.
        let layout = compose_layout_core(808, 606, 404, 300, "Survey").unwrap();
        let caption_height = ((606.0f64) * 0.065).round_ties_even() as i64;
        let caption_height = caption_height.clamp(40, 72);
        assert_eq!(layout.3, caption_height);
        let content_height = 606 - caption_height;
        assert_eq!(layout.2, content_height);
        let scale = python_min(808.0 / 404.0, content_height as f64 / 300.0);
        assert_eq!(layout.4, scale);
        let rendered_width = (((404.0f64) * layout.4).round_ties_even() as i64).max(1);
        let rendered_height = (((300.0f64) * layout.4).round_ties_even() as i64).max(1);
        assert_eq!(layout.5, rendered_width);
        assert_eq!(layout.6, rendered_height);
        assert_eq!(layout.7, (808 - layout.5).div_euclid(2));
        assert_eq!(layout.8, (layout.2 - layout.6).div_euclid(2));
        assert_eq!(layout.9, "Survey");
    }

    #[test]
    fn compose_layout_caption_band_clamps_and_rounds_like_python() {
        // 700 * 0.065 rounds through the exact 45.5 tie to even (46).
        let tall = compose_layout_core(400, 700, 100, 100, "note").unwrap();
        assert_eq!(tall.3, 46);
        // 900 * 0.065 lands on the exact 58.5 tie, which rounds down to 58.
        let taller = compose_layout_core(400, 900, 100, 100, "note").unwrap();
        assert_eq!(taller.3, 58);
        // Small exports clamp up to 40, tall ones clamp down to 72.
        let small = compose_layout_core(400, 200, 100, 100, "n").unwrap();
        assert_eq!(small.3, 40);
        let long = compose_layout_core(400, 4000, 100, 100, "n").unwrap();
        assert_eq!(long.3, 72);
    }

    #[test]
    fn compose_layout_content_height_never_collapses() {
        let layout = compose_layout_core(1, 1, 1, 1, "caption on a 1x1 export").unwrap();
        assert_eq!(layout.3, 40);
        assert_eq!(layout.2, 1);
        assert_eq!(layout.5, 1);
        assert_eq!(layout.6, 1);
        assert!(layout.7 >= 0);
        assert!(layout.8 >= 0);
    }

    #[test]
    fn theme_hex_maps_light_dark_and_current() {
        let light = composition_theme_hex_core("Light");
        assert_eq!(light[0], "#f4f1eb");
        assert_eq!(light[1], "#ffffff");
        assert_eq!(light[2], "#1a1815");
        let dark = composition_theme_hex_core("dark");
        assert_eq!(dark[0], "#0d0d0c");
        assert_eq!(dark[1], "#1f1e1b");
        assert_eq!(dark[2], "#f1efeb");
        let current = composition_theme_hex_core("current");
        assert_eq!(current, dark, "no live palette: the fallback anchors");
        assert_eq!(composition_theme_hex_core("  "), dark);
        assert_eq!(composition_theme_hex_core("sunset"), dark);
    }

    #[test]
    fn png_parts_need_signature_size_and_iend() {
        let size = 40u64;
        assert!(valid_png_parts_core(size, &PNG_SIGNATURE, &IEND_CHUNK));
        assert!(!valid_png_parts_core(size, b"NOT_PNG!", &IEND_CHUNK));
        assert!(!valid_png_parts_core(
            size,
            &PNG_SIGNATURE,
            b"0000IEND\xaeB`\x82"
        ));
        let broken_crc: [u8; 12] = [0, 0, 0, 0, b'I', b'E', b'N', b'D', 0xAE, 0x42, 0x60, 0x83];
        assert!(!valid_png_parts_core(size, &PNG_SIGNATURE, &broken_crc));
        assert!(!valid_png_parts_core(8, &PNG_SIGNATURE, &IEND_CHUNK));
        assert!(!valid_png_parts_core(19, &PNG_SIGNATURE, &IEND_CHUNK));
        assert!(valid_png_parts_core(20, &PNG_SIGNATURE, &IEND_CHUNK));
        assert!(!valid_png_parts_core(9, &PNG_SIGNATURE, &IEND_CHUNK));
    }

    #[test]
    fn patch_specs_accept_ordered_unique_names() {
        let ok = patch_registry_validate_specs_core(&[
            PatchSpecInput {
                name: "zero_arg".to_string(),
                installer_callable: true,
            },
            PatchSpecInput {
                name: "aspect".to_string(),
                installer_callable: true,
            },
        ])
        .unwrap();
        assert_eq!(ok, vec!["zero_arg".to_string(), "aspect".to_string()]);
    }

    #[test]
    fn patch_specs_reject_with_python_messages_in_order() {
        let empty = patch_registry_validate_specs_core(&[PatchSpecInput {
            name: "   ".to_string(),
            installer_callable: true,
        }])
        .unwrap_err();
        assert_eq!(empty, "render patch names cannot be empty");
        let duplicate = patch_registry_validate_specs_core(&[
            PatchSpecInput {
                name: "a".to_string(),
                installer_callable: true,
            },
            PatchSpecInput {
                name: " a ".to_string(),
                installer_callable: true,
            },
        ])
        .unwrap_err();
        assert_eq!(duplicate, "duplicate render patch name: a");
        let uncallable = patch_registry_validate_specs_core(&[PatchSpecInput {
            name: "b".to_string(),
            installer_callable: false,
        }])
        .unwrap_err();
        assert_eq!(uncallable, "render patch installer is not callable: b");
        // Checks are per spec: the second spec's empty name wins over its
        // missing installer, but the first spec stays valid.
        let order = patch_registry_validate_specs_core(&[
            PatchSpecInput {
                name: "dup".to_string(),
                installer_callable: true,
            },
            PatchSpecInput {
                name: "  ".to_string(),
                installer_callable: false,
            },
        ])
        .unwrap_err();
        assert_eq!(order, "render patch names cannot be empty");
    }

    #[test]
    fn patch_versions_match_the_supported_tuple() {
        assert_eq!(validate_sharppy_version_core("1.4.0a5").unwrap(), "1.4.0a5");
        assert_eq!(
            validate_sharppy_version_core(" 1.4.0a5 ").unwrap(),
            "1.4.0a5"
        );
        assert_eq!(
            validate_sharppy_version_core("").unwrap_err(),
            "SHARppy <unknown> is not supported by the render patch registry; expected 1.4.0a5"
        );
        assert_eq!(
            validate_sharppy_version_core("1.4.0a4").unwrap_err(),
            "SHARppy 1.4.0a4 is not supported by the render patch registry; expected 1.4.0a5"
        );
    }

    #[test]
    fn sequence_hours_from_published_cadence() {
        let published = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];
        let hours = box_extract_sequence_hours_core("HRRR", 12, &published, 0, 3, 2).unwrap();
        assert_eq!(hours, vec![0, 2, 4]);
        let single = box_extract_sequence_hours_core("HRRR", 12, &published, 7, 1, 1).unwrap();
        assert_eq!(single, vec![7]);
        let stepped = box_extract_sequence_hours_core("NAM", 0, &published, 1, 4, 3).unwrap();
        assert_eq!(stepped, vec![1, 4, 7, 10]);
        // Unsorted inputs are sorted before selection, like sorted(published).
        let shuffled = box_extract_sequence_hours_core("HRRR", 12, &[12, 0, 6], 0, 3, 6).unwrap();
        assert_eq!(shuffled, vec![0, 6, 12]);
    }

    #[test]
    fn sequence_hours_keep_published_duplicates_until_normalize() {
        let duplicated = [0, 0, 6, 6, 12, 12];
        let hours = box_extract_sequence_hours_core("HRRR", 0, &duplicated, 0, 3, 1).unwrap();
        // The first three published slots at or after F000 are 0, 0, and 6 --
        // duplicates survive the slice like Python's later[:count], and only
        // normalize_hours deduplicates them.
        assert_eq!(hours, vec![0, 6]);
    }

    #[test]
    fn sequence_hours_validate_start_and_step_with_exact_messages() {
        let published = [0, 6, 12];
        assert_eq!(
            box_extract_sequence_hours_core("HRRR", 12, &published, 3, 2, 1).unwrap_err(),
            "HRRR does not publish F003 for its 12Z cycle"
        );
        assert_eq!(
            box_extract_sequence_hours_core("HRRR", 0, &[0], 0, 2, 1).unwrap_err(),
            "HRRR publishes no further forecast hour at or after F000 with a 1-hour step"
        );
        assert_eq!(
            box_extract_sequence_hours_core("HRRR", 12, &[], 0, 2, 1).unwrap_err(),
            "HRRR does not publish F000 for its 12Z cycle"
        );
        // A negative start hour is reported with Python's sign-aware padding.
        assert_eq!(
            box_extract_sequence_hours_core("HRRR", 12, &published, -1, 2, 1).unwrap_err(),
            "HRRR does not publish F-01 for its 12Z cycle"
        );
        // Zero and negative steps clamp to one hour.
        let clamped = box_extract_sequence_hours_core("HRRR", 0, &published, 0, 9, 0).unwrap();
        assert_eq!(clamped, vec![0, 6, 12]);
        let negative = box_extract_sequence_hours_core("HRRR", 0, &published, 6, 0, -3).unwrap();
        assert_eq!(negative, vec![6]);
    }

    #[test]
    fn sequence_hours_limit_the_sequence_length() {
        let published: Vec<i64> = (0..=48).collect();
        let over = box_extract_sequence_hours_core("GFS", 0, &published, 0, 13, 1).unwrap_err();
        assert_eq!(
            over,
            "a box sequence is limited to 12 forecast hours (13 requested); each hour is its own download"
        );
        let exact = box_extract_sequence_hours_core("GFS", 0, &published, 0, 12, 1).unwrap();
        assert_eq!(exact, vec![0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]);
    }

    #[test]
    fn safe_tokens_keep_alphanumerics_only() {
        assert_eq!(safe_token_core("UWyo"), "UWyo");
        assert_eq!(safe_token_core("IEM RAOB"), "IEM_RAOB");
        assert_eq!(safe_token_core("--station--"), "station");
        assert_eq!(safe_token_core("  "), "station");
        assert_eq!(safe_token_core(""), "station");
        assert_eq!(safe_token_core("K-SEA/2024"), "K_SEA_2024");
        assert_eq!(
            safe_token_core("ä ö"),
            "ä_ö",
            "unicode letters count as alphanumerics"
        );
        assert_eq!(safe_token_core("_leading"), "leading");
        assert_eq!(safe_token_core("trailing_2_"), "trailing_2");
    }
}

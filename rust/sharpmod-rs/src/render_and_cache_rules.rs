//! Native ports of the pure rule helpers behind three Python modules:
//!
//! * ``sharpmod.rendering.density``: PNG mode aliases, the quality/scale
//!   environment clamps, and the density-pixmap copy/scale geometry;
//! * ``sharpmod.models.model_hour_cache``: key normalisation and the
//!   download-directory prefix layout;
//! * ``sharpmod.tools.ifs_extract``: request range validation and the
//!   00/06/12/18 UTC cycle flooring.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

fn value_error(message: String) -> PyErr {
    PyValueError::new_err(message)
}

/// Quote a string the way Python's ``repr`` does for the quoting cases an
/// undeclared image or environment value can produce.
fn py_repr_str(text: &str) -> String {
    if !text.contains('\'') {
        format!("'{text}'")
    } else if !text.contains('"') {
        format!("\"{text}\"")
    } else {
        format!("'{}'", text.replace('\\', "\\\\").replace('\'', "\\'"))
    }
}

// ---------------------------------------------------------------------------
// Python number parsing for environment overrides
// ---------------------------------------------------------------------------

/// Python accepts one underscore between two ASCII decimal digits when the
/// ``float``/``int`` constructors parse strings; any other underscore
/// invalidates the whole value.
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

/// Port of the string form of Python's ``float`` constructor.  Rust's f64
/// parser already matches CPython's decimal, exponent, sign, ``inf``, and
/// ``nan`` spellings case-insensitively; only digit-grouping underscores and
/// the surrounding-whitespace trim need help.  Non-ASCII decimal digit
/// codepoints are the one deliberate divergence.
fn parse_python_float(raw: &str) -> Option<f64> {
    let trimmed = raw.trim();
    if python_underscores_valid(trimmed) {
        trimmed.replace('_', "").parse::<f64>().ok()
    } else {
        None
    }
}

/// Outcomes of the Python ``int`` constructor on a string: a value, a
/// syntactically valid magnitude beyond ``i64`` (Python integers are
/// unbounded, so only the surviving sign matters), or an invalid form.
enum ParsedInt {
    Value(i64),
    OverflowHigh,
    OverflowLow,
    Invalid,
}

/// Port of the string form of Python's ``int`` constructor in base 10:
/// optional sign, ASCII digits with single underscore grouping, and nothing
/// else (no decimal point, exponent, or ``0x`` prefix).
fn parse_python_int(raw: &str) -> ParsedInt {
    let trimmed = raw.trim();
    let (negative, magnitude) = match trimmed.as_bytes().first() {
        Some(b'+') => (false, &trimmed[1..]),
        Some(b'-') => (true, &trimmed[1..]),
        Some(_) => (false, trimmed),
        None => return ParsedInt::Invalid,
    };
    if magnitude.is_empty() || !python_underscores_valid(magnitude) {
        return ParsedInt::Invalid;
    }
    let mut value: i64 = 0;
    let mut overflowed = false;
    for byte in magnitude.bytes() {
        if byte == b'_' {
            continue;
        }
        if !byte.is_ascii_digit() {
            return ParsedInt::Invalid;
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
        return if negative {
            ParsedInt::OverflowLow
        } else {
            ParsedInt::OverflowHigh
        };
    }
    ParsedInt::Value(if negative { -value } else { value })
}

/// Python's two-argument ``min``/``max`` builtins: ``min(a, b)`` keeps ``b``
/// only when ``b < a``, so a NaN operand falls through to the other side.
/// Porting that comparison order literally keeps NaN and infinity
/// environment values clamping exactly like
/// ``max(lower, min(upper, value))`` in Python.
fn python_clamp<T: PartialOrd<T>>(value: T, lower: T, upper: T) -> T {
    let within_upper = if value < upper { value } else { upper };
    if within_upper > lower {
        within_upper
    } else {
        lower
    }
}

/// Port of ``density._bounded_float_env`` once the raw environment value is
/// in hand: parse like Python ``float()``, fall back to the default, and
/// clamp the way Python's ordered comparisons do.
pub(crate) fn bounded_float_core(raw: Option<&str>, default: f64, lower: f64, upper: f64) -> f64 {
    let value = raw.and_then(parse_python_float).unwrap_or(default);
    python_clamp(value, lower, upper)
}

/// Port of ``density._bounded_int_env``: base-10 integer parse, default
/// fallback, and the same ordered clamp.  Magnitudes beyond ``i64`` (legal
/// Python integers) clamp toward the bound their sign points at.
pub(crate) fn bounded_int_core(raw: Option<&str>, default: i64, lower: i64, upper: i64) -> i64 {
    let value = match raw.map(parse_python_int) {
        Some(ParsedInt::Value(value)) => value,
        Some(ParsedInt::OverflowHigh) => i64::MAX,
        Some(ParsedInt::OverflowLow) => i64::MIN,
        Some(ParsedInt::Invalid) | None => default,
    };
    python_clamp(value, lower, upper)
}

#[pyfunction]
#[pyo3(signature = (raw, default, lower, upper))]
fn render_bounded_float(raw: Option<&str>, default: f64, lower: f64, upper: f64) -> f64 {
    bounded_float_core(raw, default, lower, upper)
}

#[pyfunction]
#[pyo3(signature = (raw, default, lower, upper))]
fn render_bounded_int(raw: Option<&str>, default: i64, lower: i64, upper: i64) -> i64 {
    bounded_int_core(raw, default, lower, upper)
}

// ---------------------------------------------------------------------------
// PNG image modes
// ---------------------------------------------------------------------------

/// Port of ``density._normalise_png_image_mode``: ``None`` and empty mode
/// default to ``"hd"``, the input trims, lowercases, and folds underscores
/// to dashes, the eleven aliases fold to the three canonical modes, and an
/// unknown mode raises the exact Python error.
pub(crate) fn normalise_png_mode_core(mode: Option<&str>) -> Result<String, String> {
    let raw = match mode {
        Some(value) if !value.is_empty() => value,
        _ => "hd",
    };
    let folded = raw.trim().to_lowercase().replace('_', "-");
    let canonical = match folded.as_str() {
        "hires" | "hi-res" | "high-resolution" | "high-definition" => "hd",
        "ultra" | "ultrahd" | "ultra-hd" | "ultra-high-definition" => "uhd",
        "compact" | "original" | "original-size" => "lossless",
        _ => folded.as_str(),
    };
    if matches!(canonical, "hd" | "uhd" | "lossless") {
        Ok(canonical.to_string())
    } else {
        Err(format!(
            "unknown PNG image mode {}; expected hd, uhd, lossless",
            py_repr_str(raw)
        ))
    }
}

#[pyfunction]
#[pyo3(signature = (mode = None))]
fn render_normalise_png_mode(mode: Option<&str>) -> PyResult<String> {
    normalise_png_mode_core(mode).map_err(value_error)
}

// ---------------------------------------------------------------------------
// Density pixmap geometry
// ---------------------------------------------------------------------------

/// Port of ``DensityPixmap._map_copy_axis``: logical coordinates map to
/// physical coordinates with Python's ties-to-even rounding, and a negative
/// length is Qt's "through the edge" sentinel.
pub(crate) fn map_copy_axis_core(
    start: i64,
    length: i64,
    dpr: f64,
    physical_extent: i64,
) -> (i64, i64) {
    let physical_start = (start as f64 * dpr).round_ties_even() as i64;
    if length < 0 {
        // Resolve the sentinel explicitly: copy from the mapped start to
        // the physical edge of the axis.
        return (physical_start, (physical_extent - physical_start).max(0));
    }
    let physical_end = ((start + length) as f64 * dpr).round_ties_even() as i64;
    (physical_start, (physical_end - physical_start).max(0))
}

/// Port of the integer-size scaling in ``DensityPixmap.__init__``: scale by
/// the density with ties-to-even rounding, never below zero.
pub(crate) fn scale_dimensions_core(width: i64, height: i64, dpr: f64) -> (i64, i64) {
    (
        ((width as f64 * dpr).round_ties_even() as i64).max(0),
        ((height as f64 * dpr).round_ties_even() as i64).max(0),
    )
}

/// Port of ``DensityPixmap.width``/``height``: a stored DPR above 1 rounds
/// the physical extent back down to logical pixels; anything else returns the
/// raw extent.
pub(crate) fn logical_extent_core(raw: i64, dpr: f64) -> i64 {
    if dpr > 1.0 {
        (raw as f64 / dpr).round_ties_even() as i64
    } else {
        raw
    }
}

#[pyfunction]
fn render_map_copy_axis(start: i64, length: i64, dpr: f64, physical_extent: i64) -> (i64, i64) {
    map_copy_axis_core(start, length, dpr, physical_extent)
}

#[pyfunction]
fn render_scale_dimensions(width: i64, height: i64, dpr: f64) -> (i64, i64) {
    scale_dimensions_core(width, height, dpr)
}

#[pyfunction]
fn render_logical_extent(raw: i64, dpr: f64) -> i64 {
    logical_extent_core(raw, dpr)
}

// ---------------------------------------------------------------------------
// Model hour cache keys and directories
// ---------------------------------------------------------------------------

/// Port of ``ModelHourKey.create``'s string handling: the model folds to
/// trimmed lowercase, and members/spatial regions trim with blanks becoming
/// ``None`` so they cannot masquerade as new cache identities.
pub(crate) fn canonical_key_parts_core(
    model: &str,
    member: Option<&str>,
    spatial: Option<&str>,
) -> (String, Option<String>, Option<String>) {
    (
        model.trim().to_lowercase(),
        member
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty()),
        spatial
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty()),
    )
}

/// Map non-alphanumeric characters to underscores, like both loops in
/// ``ModelHourCache._directory_prefix``.
fn alnum_underscore(text: &str) -> String {
    text.chars()
        .map(|character| {
            if character.is_alphanumeric() {
                character
            } else {
                '_'
            }
        })
        .collect()
}

/// Port of ``ModelHourCache._directory_prefix``: the mapped model, the
/// ``%Y%m%d%H`` run stamp, the zero-padded forecast hour (the sign counts in
/// the pad width, exactly like ``f"{fxx:03d}"``), and the optional ``p..._``
/// spatial region suffix keep the tempdir prefix stable.
pub(crate) fn directory_prefix_core(
    model: &str,
    run_stamp: &str,
    fxx: i64,
    spatial: Option<&str>,
) -> String {
    let model_part = alnum_underscore(model);
    let spatial_part = match spatial.filter(|value| !value.is_empty()) {
        Some(value) => format!("p{}_", alnum_underscore(value)),
        None => String::new(),
    };
    format!("sharpmod_hour_{model_part}_{run_stamp}_f{fxx:03}_{spatial_part}")
}

#[pyfunction]
#[pyo3(signature = (model, member = None, spatial = None))]
fn hour_cache_canonical_key_parts(
    model: &str,
    member: Option<&str>,
    spatial: Option<&str>,
) -> (String, Option<String>, Option<String>) {
    canonical_key_parts_core(model, member, spatial)
}

#[pyfunction]
#[pyo3(signature = (model, run_stamp, fxx, spatial = None))]
fn hour_cache_directory_prefix(
    model: &str,
    run_stamp: &str,
    fxx: i64,
    spatial: Option<&str>,
) -> String {
    directory_prefix_core(model, run_stamp, fxx, spatial)
}

// ---------------------------------------------------------------------------
// IFS request validation and cycle flooring
// ---------------------------------------------------------------------------

/// Format one float the way Python's ``%.4f`` does, including the lowercase
/// spelling of NaN that Rust prints differently.
fn python_format_4f(value: f64) -> String {
    if value.is_nan() {
        "nan".to_string()
    } else {
        format!("{value:.4}")
    }
}

/// Port of ``ifs_extract._validate_request``: latitudes live in [-90, 90] and
/// longitudes in [-180, 360] (IFS publishes 0..360 grids).  The closed ranges
/// reject NaN along with everything out of bounds, and the messages match the
/// Python percent-formatting byte for byte.
pub(crate) fn validate_request_core(lat: f64, lon: f64) -> Result<(), String> {
    if !(-90.0..=90.0).contains(&lat) {
        return Err(format!(
            "latitude {} is out of range; permitted range is [-90.0, 90.0] degrees",
            python_format_4f(lat)
        ));
    }
    if !(-180.0..=360.0).contains(&lon) {
        return Err(format!(
            "longitude {} is out of range; permitted range is \
             [-180.0, 360.0] degrees (IFS global coverage)",
            python_format_4f(lon)
        ));
    }
    Ok(())
}

/// Port of ``ifs_extract._nearest_cycle`` for the hour component: floor to
/// the 00/06/12/18 UTC cycle grid.  The cycle list never offers more than 18,
/// and Python's guard keeps hour 0 for anything below the first cycle.
pub(crate) fn nearest_cycle_hour_core(hour: i64) -> i64 {
    if hour < 0 {
        0
    } else {
        (hour / 6 * 6).min(18)
    }
}

#[pyfunction]
fn ifs_validate_request(lat: f64, lon: f64) -> PyResult<()> {
    validate_request_core(lat, lon).map_err(value_error)
}

#[pyfunction]
fn ifs_nearest_cycle_hour(hour: i64) -> i64 {
    nearest_cycle_hour_core(hour)
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(render_normalise_png_mode, module)?)?;
    module.add_function(wrap_pyfunction!(render_bounded_float, module)?)?;
    module.add_function(wrap_pyfunction!(render_bounded_int, module)?)?;
    module.add_function(wrap_pyfunction!(render_map_copy_axis, module)?)?;
    module.add_function(wrap_pyfunction!(render_scale_dimensions, module)?)?;
    module.add_function(wrap_pyfunction!(render_logical_extent, module)?)?;
    module.add_function(wrap_pyfunction!(hour_cache_canonical_key_parts, module)?)?;
    module.add_function(wrap_pyfunction!(hour_cache_directory_prefix, module)?)?;
    module.add_function(wrap_pyfunction!(ifs_validate_request, module)?)?;
    module.add_function(wrap_pyfunction!(ifs_nearest_cycle_hour, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn png_mode_normalises_default_and_aliases() {
        // None and empty default to hd, exactly like ``mode or PNG_IMAGE_HD``.
        assert_eq!(normalise_png_mode_core(None), Ok("hd".to_string()));
        assert_eq!(normalise_png_mode_core(Some("")), Ok("hd".to_string()));
        // Trim, lower, and fold underscores to dashes before the alias table.
        assert_eq!(normalise_png_mode_core(Some(" HD ")), Ok("hd".to_string()));
        assert_eq!(
            normalise_png_mode_core(Some("HI_RES")),
            Ok("hd".to_string())
        );
        assert_eq!(
            normalise_png_mode_core(Some("high-definition")),
            Ok("hd".to_string())
        );
        // The uhd and lossless aliases fold the same way.
        assert_eq!(
            normalise_png_mode_core(Some("Ultra")),
            Ok("uhd".to_string())
        );
        assert_eq!(
            normalise_png_mode_core(Some("ULTRA-HD")),
            Ok("uhd".to_string())
        );
        assert_eq!(
            normalise_png_mode_core(Some("ultra-high-definition")),
            Ok("uhd".to_string())
        );
        assert_eq!(
            normalise_png_mode_core(Some("Compact")),
            Ok("lossless".to_string())
        );
        assert_eq!(
            normalise_png_mode_core(Some("ORIGINAL_SIZE")),
            Ok("lossless".to_string())
        );
    }

    #[test]
    fn png_mode_rejects_unknown_with_python_repr() {
        // The repr quotes the original, un-normalised text.
        assert_eq!(
            normalise_png_mode_core(Some("hD_")),
            Err("unknown PNG image mode 'hD_'; expected hd, uhd, lossless".to_string())
        );
        // A mode containing a single quote reprs with double quotes.
        assert_eq!(
            normalise_png_mode_core(Some("hi'res")),
            Err("unknown PNG image mode \"hi'res\"; expected hd, uhd, lossless".to_string())
        );
        // Whitespace-only input is not the hd default: it folds to the empty
        // mode and fails validation with the original text repr'd.
        assert_eq!(
            normalise_png_mode_core(Some("   ")),
            Err("unknown PNG image mode '   '; expected hd, uhd, lossless".to_string())
        );
    }

    #[test]
    fn bounded_float_parses_python_number_forms() {
        // Whitespace, signs, fractional/exponent forms, and digit grouping
        // all parse the way Python's float() accepts them.
        assert_eq!(bounded_float_core(Some(" 2.5 "), 2.0, 1.0, 4.0), 2.5);
        assert_eq!(bounded_float_core(Some("+2.5"), 2.0, 1.0, 4.0), 2.5);
        assert_eq!(bounded_float_core(Some("1_000.5"), 2.0, 1.0, 4.0), 4.0);
        assert_eq!(bounded_float_core(Some("1e-1"), 2.0, 1.0, 4.0), 1.0);
        assert_eq!(bounded_float_core(Some(".5"), 2.0, 1.0, 4.0), 1.0);
        // A missing value behaves like Python's str(default); broken forms
        // fall back to the default (NaN-excepted forms included).
        assert_eq!(bounded_float_core(None, 2.5, 1.0, 4.0), 2.5);
        assert_eq!(bounded_float_core(Some("1__0"), 2.5, 1.0, 4.0), 2.5);
        assert_eq!(bounded_float_core(Some("nonsense"), 2.5, 1.0, 4.0), 2.5);
        assert_eq!(bounded_float_core(Some("0x10"), 2.5, 1.0, 4.0), 2.5);
    }

    #[test]
    fn bounded_float_clamps_and_handles_non_finite() {
        // Values clamp to [lower, upper] like Python's min/max pair.
        assert_eq!(bounded_float_core(Some("-100.0"), 2.0, 1.0, 4.0), 1.0);
        assert_eq!(bounded_float_core(Some("1e10"), 2.0, 1.0, 4.0), 4.0);
        assert_eq!(bounded_float_core(Some("1e999"), 2.0, 1.0, 4.0), 4.0);
        // NaN parses but the ordered clamp keeps the surviving upper bound.
        assert_eq!(bounded_float_core(Some("nan"), 2.0, 1.0, 4.0), 4.0);
        // Infinities clamp to their bounds; "infinity" parses case-blind.
        assert_eq!(bounded_float_core(Some("-inf"), 2.0, 1.0, 4.0), 1.0);
        assert_eq!(bounded_float_core(Some("INFINITY"), 2.0, 1.0, 4.0), 4.0);
        // The default itself clamps when it sits outside [lower, upper].
        assert_eq!(bounded_float_core(None, 10.0, 1.0, 4.0), 4.0);
    }

    #[test]
    fn bounded_int_parses_python_int_forms() {
        assert_eq!(bounded_int_core(None, 50, 0, 100), 50);
        assert_eq!(bounded_int_core(Some(" 7 "), 50, 0, 100), 7);
        assert_eq!(bounded_int_core(Some("+7"), 50, 0, 100), 7);
        assert_eq!(bounded_int_core(Some("-3"), 50, 0, 100), 0);
        // Python int() accepts underscore grouping and leading zeros.
        assert_eq!(bounded_int_core(Some("1_0"), 50, 0, 100), 10);
        assert_eq!(bounded_int_core(Some("007"), 50, 0, 100), 7);
        // Python int() rejects these, so the default applies.
        assert_eq!(bounded_int_core(Some("2.5"), 50, 0, 100), 50);
        assert_eq!(bounded_int_core(Some("0x10"), 50, 0, 100), 50);
        assert_eq!(bounded_int_core(Some("_7"), 50, 0, 100), 50);
        assert_eq!(bounded_int_core(Some("1e1"), 50, 0, 100), 50);
        assert_eq!(bounded_int_core(Some(""), 50, 0, 100), 50);
    }

    #[test]
    fn bounded_int_clamps_and_saturates_overflow() {
        assert_eq!(bounded_int_core(Some("200"), 50, 0, 100), 100);
        assert_eq!(bounded_int_core(Some("-200"), 50, 0, 100), 0);
        // Python integers are unbounded: a 30-digit environment value still
        // clamps toward the bound its sign points at.
        let huge = "9".repeat(30);
        assert_eq!(bounded_int_core(Some(&huge), 50, 0, 100), 100);
        let tiny = format!("-{huge}");
        assert_eq!(bounded_int_core(Some(&tiny), 50, 0, 100), 0);
        // i64-sized values clamp through the normal bounds unchanged.
        assert_eq!(
            bounded_int_core(Some("9223372036854775807"), 50, 0, 100),
            100
        );
    }

    #[test]
    fn map_copy_axis_matches_pixmap_rounding() {
        // The density suite's 11x9 pixmap at scale 2.8: the copy rectangle
        // (5, 4, 6, 5) maps to physical (14, 11) through (31, 25).
        assert_eq!(map_copy_axis_core(5, 6, 2.8, 31), (14, 17));
        assert_eq!(map_copy_axis_core(0, 11, 2.8, 31), (0, 31));
        // Qt's -1 length copies through the edge from the physical start.
        assert_eq!(map_copy_axis_core(5, -1, 2.8, 31), (14, 17));
        assert_eq!(map_copy_axis_core(10, -1, 2.0, 36), (20, 16));
        // The clamped length never drops below zero, even past the edge.
        assert_eq!(map_copy_axis_core(30, -1, 2.8, 44), (84, 0));
    }

    #[test]
    fn scale_dimensions_round_ties_even_like_python_round() {
        // The density suite's logical 13x7 at 2x and 2.8x.
        assert_eq!(scale_dimensions_core(13, 7, 2.0), (26, 14));
        assert_eq!(scale_dimensions_core(13, 7, 2.8), (36, 20));
        // Python round() breaks .5 ties to the neighbouring even value.
        assert_eq!(scale_dimensions_core(3, 5, 1.5), (4, 8));
        assert_eq!(scale_dimensions_core(11, 7, 0.5), (6, 4));
        // Negative physical extents clamp to zero.
        assert_eq!(scale_dimensions_core(-5, -4, 2.0), (0, 0));
    }

    #[test]
    fn logical_extent_rounds_back_down_when_dense() {
        assert_eq!(logical_extent_core(36, 2.8), 13);
        assert_eq!(logical_extent_core(26, 2.0), 13);
        // Ties round to the neighbouring even value like Python round().
        assert_eq!(logical_extent_core(9, 2.0), 4);
        assert_eq!(logical_extent_core(11, 2.0), 6);
        // A unit or lower DPR leaves the physical extent alone.
        assert_eq!(logical_extent_core(100, 1.0), 100);
        assert_eq!(logical_extent_core(100, 0.5), 100);
    }

    #[test]
    fn hour_cache_key_parts_normalise_like_create() {
        // "HRRR" folds to lowercase and a blank member is not a member.
        let (model, member, spatial) = canonical_key_parts_core("HRRR", Some("  "), None);
        assert_eq!(model, "hrrr");
        assert_eq!(member, None);
        assert_eq!(spatial, None);
        // Members and spatial regions trim and survive verbatim otherwise.
        let (model, member, spatial) =
            canonical_key_parts_core(" GFS ", Some(" mem12 "), Some("35.0000,-97.0000"));
        assert_eq!(model, "gfs");
        assert_eq!(member, Some("mem12".to_string()));
        assert_eq!(spatial, Some("35.0000,-97.0000".to_string()));
    }

    #[test]
    fn hour_cache_directory_prefix_matches_reference_layout() {
        // f"{1:03d}" is "001", and the empty region still leaves the tail.
        assert_eq!(
            directory_prefix_core("hrrr", "2026070512", 1, None),
            "sharpmod_hour_hrrr_2026070512_f001_"
        );
        assert_eq!(
            directory_prefix_core("hrrr", "2026070512", 18, None),
            "sharpmod_hour_hrrr_2026070512_f018_"
        );
        // Wide forecast hours keep all digits; negative hours fold the sign
        // into the pad width exactly like "f{-1:03d}" producing "-01".
        assert_eq!(
            directory_prefix_core("hrrr", "2026070512", 1234, None),
            "sharpmod_hour_hrrr_2026070512_f1234_"
        );
        assert_eq!(
            directory_prefix_core("hrrr", "2026070512", -1, None),
            "sharpmod_hour_hrrr_2026070512_f-01_"
        );
        // Non-alphanumeric model characters become underscores and a spatial
        // region takes the "p..._" suffix.
        assert_eq!(
            directory_prefix_core("gfs-0.25", "2026070512", 18, Some("35.0000,-97.0000")),
            "sharpmod_hour_gfs_0_25_2026070512_f018_p35_0000__97_0000_"
        );
        // A blank spatial region is skipped like a falsy key.spatial.
        assert_eq!(
            directory_prefix_core("hrrr", "2026070512", 18, Some("")),
            "sharpmod_hour_hrrr_2026070512_f018_"
        );
    }

    #[test]
    fn ifs_validate_request_matches_message_layout() {
        // The closed ranges keep their boundary values.
        assert_eq!(validate_request_core(90.0, 360.0), Ok(()));
        assert_eq!(validate_request_core(-90.0, -180.0), Ok(()));
        assert_eq!(validate_request_core(0.0, 0.0), Ok(()));
        assert_eq!(
            validate_request_core(90.0001, 0.0).unwrap_err(),
            "latitude 90.0001 is out of range; permitted range is [-90.0, 90.0] degrees"
        );
        assert_eq!(
            validate_request_core(0.0, -180.5).unwrap_err(),
            "longitude -180.5000 is out of range; permitted range is \
             [-180.0, 360.0] degrees (IFS global coverage)"
        );
        // NaN coordinates fail with Python's %-formatting spelling.
        assert_eq!(
            validate_request_core(f64::NAN, 0.0).unwrap_err(),
            "latitude nan is out of range; permitted range is [-90.0, 90.0] degrees"
        );
        assert_eq!(
            validate_request_core(0.0, f64::NAN).unwrap_err(),
            "longitude nan is out of range; permitted range is \
             [-180.0, 360.0] degrees (IFS global coverage)"
        );
    }

    #[test]
    fn ifs_nearest_cycle_floors_to_cycle_hours() {
        assert_eq!(nearest_cycle_hour_core(0), 0);
        assert_eq!(nearest_cycle_hour_core(5), 0);
        assert_eq!(nearest_cycle_hour_core(6), 6);
        assert_eq!(nearest_cycle_hour_core(11), 6);
        assert_eq!(nearest_cycle_hour_core(12), 12);
        assert_eq!(nearest_cycle_hour_core(17), 12);
        assert_eq!(nearest_cycle_hour_core(18), 18);
        assert_eq!(nearest_cycle_hour_core(23), 18);
        // The cycle list never offers more than 18, whatever arrives.
        assert_eq!(nearest_cycle_hour_core(24), 18);
        assert_eq!(nearest_cycle_hour_core(1000), 18);
        // Python's guard keeps hour 0 for a negative hour value.
        assert_eq!(nearest_cycle_hour_core(-1), 0);
    }
}

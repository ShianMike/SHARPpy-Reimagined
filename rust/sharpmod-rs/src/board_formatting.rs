//! Value-formatting helpers for the bottom-panel widgets (milestone M30).
//!
//! Rust mirrors of the pure-formatting rules used by the index board, the
//! SHIP inset, and the user-configurable Custom_Panel:
//!
//! * `sharpmod/viz/index_board.py` -- `i0`, `f1`, `f2`, `dirspd`, `uv_dirspd`,
//!   the wind-unit-aware `_dirspd`/`_uv_dirspd`/`_temp`/`_pwat`/`_wind_speed`
//!   methods, the `_barb_path` barb decomposition, `_cinh_legacy`, and
//!   `_cape3_color`.
//! * `sharpmod/viz/ship.py` -- `plotSHIP.scale_fraction` and `plotSHIP._fmt`.
//! * `sharpmod/viz/custom_panel.py` -- `CustomPanel._resolve_label_unit`'s
//!   unit normalisation and `CustomPanel._fmt_value`.
//!
//! Because the Python sources format with `"%.*f"` / `f"{x:.Nf}"` and
//! `str(int(round(x)))`, this module carries a bit-exact CPython `%.Nf`
//! implementation ([`py_fixed`]) built on exact mantissa arithmetic with
//! half-even ties, matching the correctly-rounded `dtoa` output CPython
//! produces on every platform (including pieces Rust's native `{:.*}`
//! differs on, such as `0.5` at precision 0).  Missing/masked/non-finite
//! values become the `"--"` sentinel exactly like the Python helpers: the
//! board always coerces through `_f` before formatting, and the NumPy masked
//! constant coerces to NaN through `float()`, so every caller observes the
//! same `"--"` while the boundary stays crash-free.

use pyo3::prelude::*;
use pyo3::types::{PyList, PyTuple};

/// Missing-value sentinel shared by every board/inset formatter
/// (`colors.MISSING_STR`).
const MISS: &str = "--";

/// `index_board.KT_TO_MS`: knots to metres per second.
const KT_TO_MS: f64 = 0.514444;
/// `index_board.IN_TO_CM`: inches to centimetres.
const IN_TO_CM: f64 = 2.54;

// ---------------------------------------------------------------------------
// Exact CPython `%.Nf` / `str(int(round(...)))` formatting
// ---------------------------------------------------------------------------

/// Outcome of scaling `|x|` by `10^precision` and rounding to an integer
/// with half-even ties.  `Exact` carries the rounded scale; `Native` marks
/// magnitudes whose scaled integer exceeds `u128` (only reachable far
/// outside the board's physical range), where the scaled value is provably
/// integral -- `|x| >= 2^63` forces a binary exponent with
/// `e + precision >= 11 > 0`, so no rounding ever occurs -- and Rust's exact
/// digit generation matches CPython byte for byte.
enum ScaledRound {
    Exact(u128),
    Native,
}

/// `|x| * 10^p` rounded half-even to an integer via exact mantissa scaling.
///
/// For `x = m * 2^e` (taken straight from the IEEE bits, `m <= 2^53`) the
/// scaled value is `m * 5^p * 2^(e+p)`, so every rounding decision --
/// including exact decimal ties -- happens on the true binary expansion
/// with integer arithmetic, exactly what CPython's `dtoa`-backed `"%.*f"`
/// does.
fn scaled_rounded_abs(y: f64, precision: usize) -> ScaledRound {
    if y == 0.0 || y.is_nan() {
        return ScaledRound::Exact(0);
    }
    let bits = y.to_bits();
    let raw_exponent = ((bits >> 52) & 0x7ff) as i32;
    // |x| = mantissa * 2^exponent; subnormals carry exponent -1074 and no
    // implicit bit, matching the raw bit layout.
    let (mantissa, exponent) = if raw_exponent == 0 {
        (bits & ((1u64 << 52) - 1), -1074)
    } else {
        (
            (bits & ((1u64 << 52) - 1)) | (1u64 << 52),
            raw_exponent - 1075,
        )
    };
    if mantissa == 0 {
        return ScaledRound::Exact(0);
    }
    // 5^21 is the largest 5-power keeping a 53-bit mantissa inside u128
    // after the shift; every call site uses precision <= 2.
    if precision > 21 {
        return ScaledRound::Native;
    }
    let scaled_mantissa = u128::from(mantissa) * 5u128.pow(precision as u32);
    let shift = exponent + precision as i32;
    if shift >= 0 {
        return match scaled_mantissa.checked_shl(shift as u32) {
            Some(value) => ScaledRound::Exact(value),
            None => ScaledRound::Native,
        };
    }
    let cut = (-shift) as u32;
    if cut > 127 {
        // scaled < 2^116 / 2^127: far below one half, rounds to zero.
        return ScaledRound::Exact(0);
    }
    let divisor = 1u128 << cut;
    let quotient = scaled_mantissa / divisor;
    let remainder = scaled_mantissa % divisor;
    // Round half to even: twice the remainder against the divisor decides
    // less/tie/greater without any float re-rounding.
    let rounded = match (remainder * 2).cmp(&divisor) {
        std::cmp::Ordering::Less => quotient,
        std::cmp::Ordering::Equal if quotient.is_multiple_of(2) => quotient,
        std::cmp::Ordering::Equal | std::cmp::Ordering::Greater => quotient + 1,
    };
    ScaledRound::Exact(rounded)
}

/// Render the `|x| * 10^p`-scaled integer digits with `p` decimal places.
fn render_fixed_digits(scaled: u128, precision: usize) -> String {
    if precision == 0 {
        return scaled.to_string();
    }
    let scale = 10u128.pow(precision as u32);
    let whole = scaled / scale;
    let fraction = scaled % scale;
    format!("{whole}.{fraction:0width$}", width = precision)
}

/// Bit-exact CPython `"%.*f"` / `f"{x:.{p}f}"` fixed-point formatting.
///
/// Correctly rounded from the exact binary expansion with round-half-even
/// ties: `0.5` -> `"0"`, `2.5` -> `"2"`, `0.125` -> `"0.12"`, `0.375` ->
/// `"0.38"`, matching CPython's `dtoa` on every platform.  A negative input
/// always keeps its sign, including values that round to zero
/// (`"%.1f" % -0.04` -> `"-0.0"`); non-finite values render as `nan`,
/// `inf`, and `-inf` like CPython.
pub(crate) fn py_fixed(x: f64, precision: usize) -> String {
    if x.is_nan() {
        return "nan".to_string();
    }
    if x.is_infinite() {
        return if x < 0.0 { "-inf" } else { "inf" }.to_string();
    }
    let body = match scaled_rounded_abs(x.abs(), precision) {
        ScaledRound::Exact(scaled) => render_fixed_digits(scaled, precision),
        // Integral at these magnitudes, so Rust's exact fixed-precision
        // digits equal CPython's; the sign is appended once below.
        ScaledRound::Native => format!("{:.*}", precision, x.abs()),
    };
    if x.is_sign_negative() {
        format!("-{body}")
    } else {
        body
    }
}

/// `str(int(round(x)))` with Python's round-half-even `round`.
///
/// Unlike [`py_fixed`] at precision 0 this never prints a negative zero
/// (`int(round(-0.4))` is `0`).  Non-finite input never reaches this helper
/// from the board paths (Python raises there); it formats as the `"--"`
/// sentinel instead of panicking.
pub(crate) fn py_round_int_str(x: f64) -> String {
    if !x.is_finite() {
        return MISS.to_string();
    }
    let scaled = scaled_rounded_abs(x.abs(), 0);
    match scaled {
        ScaledRound::Exact(rounded) => {
            if rounded == 0 {
                "0".to_string()
            } else if x.is_sign_negative() {
                format!("-{rounded}")
            } else {
                rounded.to_string()
            }
        }
        ScaledRound::Native => {
            if x.is_sign_negative() {
                format!("-{:.0}", x.abs())
            } else {
                format!("{x:.0}")
            }
        }
    }
}

/// `round(x)` truncated to `i64`, saturating at the integer bounds where
/// Python's big-int `round` keeps full digits (unreachable for physical
/// quantities the board formats).
fn py_round_i64(x: f64) -> i64 {
    x.round_ties_even() as i64
}

/// `"%03d/%02d" % (int(round(d)) % 360, int(round(s)))`.
fn dirspd_text(direction: f64, speed: f64) -> String {
    let dir = py_round_i64(direction).rem_euclid(360);
    let spd = py_round_i64(speed);
    format!("{dir:03}/{spd:02}")
}

/// `"%03d/%02d" % (int(round((270 - degrees(atan2(w, u))) % 360)) % 360, int(round(hypot(u, w) * factor)))`.
///
/// The `factor` converts the magnitude to the wind display unit (applied
/// after the `hypot`, mirroring `IndexBoard._wind_speed(math.hypot(...))`).
fn uv_dirspd_text(u: f64, w: f64, factor: f64) -> String {
    let angle = (270.0 - w.atan2(u).to_degrees()).rem_euclid(360.0);
    dirspd_text(angle, u.hypot(w) * factor)
}

// ---------------------------------------------------------------------------
// Pure cores, mirroring the Python helpers one to one
// ---------------------------------------------------------------------------

/// `index_board.i0`: rounded integer, `"--"` when missing.
pub(crate) fn format_i0_core(x: Option<f64>) -> String {
    match x {
        Some(value) => py_round_int_str(value),
        None => MISS.to_string(),
    }
}

/// `index_board.f1`: one decimal place.
pub(crate) fn format_f1_core(x: Option<f64>) -> String {
    match x {
        Some(value) => py_fixed(value, 1),
        None => MISS.to_string(),
    }
}

/// `index_board.f2`: two decimal places.
pub(crate) fn format_f2_core(x: Option<f64>) -> String {
    match x {
        Some(value) => py_fixed(value, 2),
        None => MISS.to_string(),
    }
}

/// `index_board._temp`: Fahrenheit by default, Celsius conversion optional.
fn format_temp_core(x: Option<f64>, celsius: bool) -> String {
    let Some(value) = x else {
        return MISS.to_string();
    };
    if celsius {
        format!("{}\u{b0}C", py_round_int_str((value - 32.0) * 5.0 / 9.0))
    } else {
        format!("{} \u{b0}F", py_round_int_str(value))
    }
}

/// `index_board._pwat`: inches (with a no-break space) by default, cm
/// optional.
fn format_pwat_core(x: Option<f64>, centimetres: bool) -> String {
    let Some(value) = x else {
        return MISS.to_string();
    };
    if centimetres {
        format!("{} cm", py_fixed(value * IN_TO_CM, 1))
    } else {
        format!("{}\u{a0}in", py_fixed(value, 2))
    }
}

/// Wind-unit conversion factor of `IndexBoard._wind_speed`
/// (`x * KT_TO_MS` only when the preference is `"m/s"`).
fn wind_speed_factor(wind_units: &str) -> f64 {
    if wind_units == "m/s" {
        KT_TO_MS
    } else {
        1.0
    }
}

/// `IndexBoard._barb_path` barb decomposition:
/// `(rounded 5-knot speed, flag count, full-barb count, half-barb count)`.
///
/// A non-positive rounded speed selects the Python calm-circle branch (all
/// counts zero) while the staff branch decomposes `50s -> flags`,
/// `10s -> full barbs`, then a possible lone half barb.
fn barb_components_core(wspd: f64) -> (i64, i64, i64, i64) {
    let rounded = py_round_i64(wspd / 5.0) * 5;
    let mut speed = rounded;
    if speed <= 0 {
        return (rounded, 0, 0, 0);
    }
    let flags = speed / 50;
    speed -= flags * 50;
    let fulls = speed / 10;
    speed -= fulls * 10;
    let halves = speed / 5;
    (rounded, flags, fulls, halves)
}

/// `IndexBoard._cinh_legacy` colour tier: 0 = neutral (missing), 1 = green
/// (`>= -50`), 2 = orange (`>= -100`), 3 = red.
///
/// A NaN falls through every threshold to red, exactly like the Python
/// comparisons.
pub(crate) fn cinh_legacy_tier_core(cin: Option<f64>) -> u8 {
    match cin {
        None => 0,
        Some(value) => {
            if value >= -50.0 {
                1
            } else if value >= -100.0 {
                2
            } else {
                3
            }
        }
    }
}

/// `IndexBoard._cape3_color` colour tier: 0 = neutral/foreground, 1 = green
/// (> 25), 2 = yellow (> 50), 3 = orange (> 75), 4 = red (> 100),
/// 5 = magenta (> 125).
pub(crate) fn cape3_tier_core(value: Option<f64>) -> u8 {
    let Some(v) = value else {
        return 0;
    };
    if v > 125.0 {
        5
    } else if v > 100.0 {
        4
    } else if v > 75.0 {
        3
    } else if v > 50.0 {
        2
    } else if v > 25.0 {
        1
    } else {
        0
    }
}

/// `plotSHIP.scale_fraction`: clamped `[0, 1]` axis fraction.
///
/// A non-positive span collapses to `0.0`; a NaN value propagates (the
/// threshold comparisons are false for NaN in Python too).
pub(crate) fn ship_scale_fraction_core(value: f64, scale_min: f64, scale_max: f64) -> f64 {
    let span = scale_max - scale_min;
    if span <= 0.0 {
        return 0.0;
    }
    let fraction = (value - scale_min) / span;
    fraction.clamp(0.0, 1.0)
}

/// `plotSHIP._fmt` once the value is known float-or-missing: integral values
/// without a decimal part, one decimal otherwise, `"--"` when missing.
pub(crate) fn ship_format_value_core(value: Option<f64>) -> String {
    let Some(v) = value else {
        return MISS.to_string();
    };
    if !v.is_finite() {
        // int(inf)/int(nan) raise in Python; `_extract_ship` guarantees this
        // never happens on the widget path.  Formatting as missing keeps the
        // helper crash-free.
        return MISS.to_string();
    }
    if v == v.trunc() {
        return py_round_int_str(v);
    }
    py_fixed(v, 1)
}

/// `CustomPanel._resolve_label_unit`'s unit normalisation: `None`, blank,
/// `unitless`, and `none` spellings (case-insensitive) become empty; any
/// other string is kept unchanged, whitespace included.
fn normalise_unit_core(unit: Option<&str>) -> String {
    match unit {
        Some(text) => {
            let folded = text.trim().to_lowercase();
            if folded.is_empty() || folded == "unitless" || folded == "none" {
                String::new()
            } else {
                text.to_string()
            }
        }
        None => String::new(),
    }
}

/// `CustomPanel._fmt_value` once the vector handling has produced the float
/// to format: two decimals for `vgp`, none for `|f| >= 1000`, integers
/// without a decimal point, one decimal otherwise, `"--"` when missing or
/// non-finite.
fn custom_format_number_core(value: Option<f64>, param: Option<&str>) -> String {
    let Some(f) = value else {
        return MISS.to_string();
    };
    if !f.is_finite() {
        return MISS.to_string();
    }
    if param == Some("vgp") {
        return py_fixed(f, 2);
    }
    if f.abs() >= 1000.0 {
        return py_fixed(f, 0);
    }
    if f == f.trunc() {
        return py_round_int_str(f);
    }
    py_fixed(f, 1)
}

// ---------------------------------------------------------------------------
// PyO3 boundary: `_f`-style coercion (missing/masked/NaN -> missing)
// ---------------------------------------------------------------------------

/// Class-name probe for the NumPy masked constant without importing numpy,
/// matching the `viz_colors` helper.
fn masked_constant(value: &Bound<'_, PyAny>) -> bool {
    value
        .getattr("__class__")
        .and_then(|class| class.getattr("__name__"))
        .and_then(|name| name.extract::<String>())
        .is_ok_and(|name| name == "MaskedConstant")
}

/// Builtins `float`, imported lazily like the `viz_colors` boundary helper.
fn builtin_float(py: Python<'_>) -> Option<Bound<'_, PyAny>> {
    py.import("builtins").ok()?.getattr("float").ok()
}

/// `float(value)`; `None` on any conversion failure.
fn py_float(py: Python<'_>, value: &Bound<'_, PyAny>) -> Option<f64> {
    builtin_float(py)?
        .call1((value,))
        .ok()?
        .extract::<f64>()
        .ok()
}

/// `index_board._f(v)` at the boundary: `None`/masked/non-finite/
/// non-coercible become missing; a leading tuple/list element unwraps one
/// nesting level like the Python recursion into `v[0]`.
fn f_value(py: Python<'_>, value: &Bound<'_, PyAny>) -> Option<f64> {
    if value.is_none() {
        return None;
    }
    let kind = pair_len(value);
    if kind.is_some() {
        if kind == Some(0) {
            return None;
        }
        let first = value.get_item(0).ok()?;
        return f_value(py, &first);
    }
    if masked_constant(value) {
        return None;
    }
    let number = py_float(py, value)?;
    number.is_finite().then_some(number)
}

/// Length of a tuple/list, `None` for any other type.
fn pair_len(value: &Bound<'_, PyAny>) -> Option<usize> {
    if let Ok(tuple) = value.cast::<PyTuple>() {
        Some(tuple.len())
    } else if let Ok(list) = value.cast::<PyList>() {
        Some(list.len())
    } else {
        None
    }
}

/// First two elements of a tuple/list pair, mirroring the
/// `isinstance(v, (tuple, list)) and len(v) >= 2` gate of `dirspd` /
/// `uv_dirspd`; `None` for anything shorter or not a tuple/list.
fn pair_elements<'py>(value: &Bound<'py, PyAny>) -> Option<(Bound<'py, PyAny>, Bound<'py, PyAny>)> {
    if pair_len(value)? < 2 {
        return None;
    }
    let first = value.get_item(0).ok()?;
    let second = value.get_item(1).ok()?;
    Some((first, second))
}

/// `str(value)` for the `_fmt_value` raw fallbacks.
fn py_str(value: &Bound<'_, PyAny>) -> PyResult<String> {
    Ok(value.str()?.to_string())
}

// ---------------------------------------------------------------------------
// PyFunctions
// ---------------------------------------------------------------------------

/// Format a value as a rounded integer, `"--"` when missing
/// (`index_board.i0`).
#[pyfunction]
fn board_format_i0(py: Python<'_>, x: &Bound<'_, PyAny>) -> String {
    format_i0_core(f_value(py, x))
}

/// Format a value with one decimal place (`index_board.f1`).
#[pyfunction]
fn board_format_f1(py: Python<'_>, x: &Bound<'_, PyAny>) -> String {
    format_f1_core(f_value(py, x))
}

/// Format a value with two decimal places (`index_board.f2`).
#[pyfunction]
fn board_format_f2(py: Python<'_>, x: &Bound<'_, PyAny>) -> String {
    format_f2_core(f_value(py, x))
}

/// Format a `(direction, speed knots)` vector as `"ddd/ss"`, converting the
/// speed to m/s when `wind_units` is `"m/s"`
/// (`index_board.dirspd` / `IndexBoard._dirspd`).
#[pyfunction]
#[pyo3(signature = (v, wind_units = "knots"))]
fn board_format_dirspd(py: Python<'_>, v: &Bound<'_, PyAny>, wind_units: &str) -> String {
    let Some((first, second)) = pair_elements(v) else {
        return MISS.to_string();
    };
    let direction = f_value(py, &first);
    let speed = f_value(py, &second).map(|knots| knots * wind_speed_factor(wind_units));
    match (direction, speed) {
        (Some(d), Some(s)) => dirspd_text(d, s),
        _ => MISS.to_string(),
    }
}

/// Format a `(u, v)` wind vector as `"ddd/ss"` (direction the wind blows
/// from plus speed), converting the magnitude to m/s when `wind_units` is
/// `"m/s"` (`index_board.uv_dirspd` / `IndexBoard._uv_dirspd`).
#[pyfunction]
#[pyo3(signature = (v, wind_units = "knots"))]
fn board_format_uv_dirspd(py: Python<'_>, v: &Bound<'_, PyAny>, wind_units: &str) -> String {
    let Some((first, second)) = pair_elements(v) else {
        return MISS.to_string();
    };
    let (Some(u), Some(w)) = (f_value(py, &first), f_value(py, &second)) else {
        return MISS.to_string();
    };
    uv_dirspd_text(u, w, wind_speed_factor(wind_units))
}

/// Format a temperature: `"NN F"` Fahrenheit by default, `"NNC"` when
/// `temp_units` is `"Celsius"` (`IndexBoard._temp`).
#[pyfunction]
#[pyo3(signature = (v, temp_units = "Fahrenheit"))]
fn board_format_temp(py: Python<'_>, v: &Bound<'_, PyAny>, temp_units: &str) -> String {
    format_temp_core(f_value(py, v), temp_units == "Celsius")
}

/// Format precipitable water: `"N.NNin"` (no-break space) by default,
/// `"N.N cm"` when `pw_units` is `"cm"` (`IndexBoard._pwat`).
#[pyfunction]
#[pyo3(signature = (v, pw_units = "in"))]
fn board_format_pwat(py: Python<'_>, v: &Bound<'_, PyAny>, pw_units: &str) -> String {
    format_pwat_core(f_value(py, v), pw_units == "cm")
}

/// Decompose a wind barb into `(rounded 5-knot speed, flags, full barbs,
/// half barbs)` (`IndexBoard._barb_path`).
///
/// `None` when either input is non-finite or not `float()`-coercible; a
/// speed of zero or below selects the calm-circle branch (all counts zero).
#[pyfunction]
fn board_barb_components(
    py: Python<'_>,
    wdir: &Bound<'_, PyAny>,
    wspd: &Bound<'_, PyAny>,
) -> Option<(i64, i64, i64, i64)> {
    // The Python try wraps both conversions and any failure short-circuits.
    let direction = py_float(py, wdir)?;
    let speed = py_float(py, wspd)?;
    if direction.is_finite() && speed.is_finite() {
        Some(barb_components_core(speed))
    } else {
        None
    }
}

/// Legacy parcel-CINH colour tier (`IndexBoard._cinh_legacy`):
/// 0 = neutral/foreground (missing), 1 = green (`>= -50`),
/// 2 = orange (`>= -100`), 3 = red.
#[pyfunction]
fn board_cinh_legacy_tier(cin: Option<f64>) -> u8 {
    cinh_legacy_tier_core(cin)
}

/// 3CAPE colour tier (`IndexBoard._cape3_color`): 0 = neutral/foreground,
/// 1 = green (> 25), 2 = yellow (> 50), 3 = orange (> 75), 4 = red (> 100),
/// 5 = magenta (> 125).
#[pyfunction]
fn board_cape3_tier(v: Option<f64>) -> u8 {
    cape3_tier_core(v)
}

/// Fraction of the SHIP display scale for `value`, clamped to `[0, 1]`
/// (`plotSHIP.scale_fraction`), with the documented `0.0`..`6.0` bounds as
/// defaults.
#[pyfunction]
#[pyo3(signature = (value, scale_min = 0.0, scale_max = 6.0))]
fn ship_scale_fraction(value: f64, scale_min: f64, scale_max: f64) -> f64 {
    ship_scale_fraction_core(value, scale_min, scale_max)
}

/// Compact SHIP read-out text (`plotSHIP._fmt`): integral values without
/// decimals, one decimal otherwise, `"--"` when missing.
#[pyfunction]
fn ship_format_value(py: Python<'_>, value: &Bound<'_, PyAny>) -> String {
    ship_format_value_core(py_float(py, value))
}

/// Normalise a Custom_Panel unit label (`CustomPanel._resolve_label_unit`):
/// blank, `unitless`, and `none` spellings (case-insensitive) become empty.
#[pyfunction]
fn custom_panel_normalise_unit(unit: Option<&str>) -> String {
    normalise_unit_core(unit)
}

/// Compact Custom_Panel value string (`CustomPanel._fmt_value`).
///
/// A 2-element tuple/list renders as the magnitude of its `(u, v)` pair; a
/// `vgp` parameter keeps two decimals, values at or above 1000 drop them,
/// integral values print as integers, and everything else keeps one decimal.
/// Non-numeric input falls back to `str(value)` like the Python helper.
#[pyfunction]
#[pyo3(signature = (value, param = None))]
fn custom_panel_format_number(
    py: Python<'_>,
    value: &Bound<'_, PyAny>,
    param: Option<&str>,
) -> PyResult<String> {
    // The Python helper tests tuple/list of length exactly two first; any
    // other sequence follows it as a plain scalar.
    if pair_len(value) == Some(2) {
        let first = value.get_item(0)?;
        let second = value.get_item(1)?;
        match (py_float(py, &first), py_float(py, &second)) {
            (Some(u), Some(w)) => return Ok(custom_format_number_core(Some(u.hypot(w)), param)),
            _ => return py_str(value),
        }
    }
    match py_float(py, value) {
        Some(number) => Ok(custom_format_number_core(Some(number), param)),
        None => py_str(value),
    }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(board_format_i0, module)?)?;
    module.add_function(wrap_pyfunction!(board_format_f1, module)?)?;
    module.add_function(wrap_pyfunction!(board_format_f2, module)?)?;
    module.add_function(wrap_pyfunction!(board_format_dirspd, module)?)?;
    module.add_function(wrap_pyfunction!(board_format_uv_dirspd, module)?)?;
    module.add_function(wrap_pyfunction!(board_format_temp, module)?)?;
    module.add_function(wrap_pyfunction!(board_format_pwat, module)?)?;
    module.add_function(wrap_pyfunction!(board_barb_components, module)?)?;
    module.add_function(wrap_pyfunction!(board_cinh_legacy_tier, module)?)?;
    module.add_function(wrap_pyfunction!(board_cape3_tier, module)?)?;
    module.add_function(wrap_pyfunction!(ship_scale_fraction, module)?)?;
    module.add_function(wrap_pyfunction!(ship_format_value, module)?)?;
    module.add_function(wrap_pyfunction!(custom_panel_normalise_unit, module)?)?;
    module.add_function(wrap_pyfunction!(custom_panel_format_number, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn py_fixed_matches_cpython_probe_values() {
        // Golden values produced by CPython 3.11 "%.Nf" on this codebase's
        // reference interpreter.
        assert_eq!(py_fixed(0.25, 1), "0.2");
        assert_eq!(py_fixed(0.125, 2), "0.12");
        assert_eq!(py_fixed(0.375, 2), "0.38");
        assert_eq!(py_fixed(0.5, 0), "0");
        assert_eq!(py_fixed(-0.5, 0), "-0");
        assert_eq!(py_fixed(2.5, 0), "2");
        assert_eq!(py_fixed(3.5, 0), "4");
        assert_eq!(py_fixed(1000.5, 0), "1000");
        assert_eq!(py_fixed(0.75, 1), "0.8");
        assert_eq!(py_fixed(6.15, 1), "6.2");
        assert_eq!(py_fixed(0.35, 1), "0.3");
        assert_eq!(py_fixed(1.125, 2), "1.12");
        assert_eq!(py_fixed(2.25, 1), "2.2");
        assert_eq!(py_fixed(0.05, 1), "0.1");
        assert_eq!(py_fixed(0.15, 1), "0.1");
        assert_eq!(py_fixed(-0.75, 1), "-0.8");
        assert_eq!(py_fixed(5.175, 2), "5.17");
        assert_eq!(py_fixed(1234.56, 1), "1234.6");
        assert_eq!(py_fixed(1234.56, 2), "1234.56");
        assert_eq!(py_fixed(3.175, 1), "3.2");
        assert_eq!(py_fixed(0.0, 2), "0.00");
        assert_eq!(py_fixed(-0.0, 1), "-0.0");
        assert_eq!(py_fixed(2.675, 2), "2.67");
        assert_eq!(py_fixed(0.005, 2), "0.01");
        assert_eq!(py_fixed(-0.125, 2), "-0.12");
        assert_eq!(py_fixed(123456.0, 2), "123456.00");
        assert_eq!(py_fixed(0.04, 1), "0.0");
        assert_eq!(py_fixed(-0.04, 1), "-0.0");
        assert_eq!(py_fixed(12.25, 1), "12.2");
        assert_eq!(py_fixed(16.125, 2), "16.12");
    }

    #[test]
    fn py_fixed_renders_exact_big_integers_and_specials() {
        // 1e20/1e21/1e22 are exactly representable scales of ten; CPython
        // and the mantissa path both print every digit.
        assert_eq!(py_fixed(1e20, 0), "100000000000000000000");
        assert_eq!(py_fixed(1e21, 0), "1000000000000000000000");
        assert_eq!(py_fixed(1e22, 0), "10000000000000000000000");
        assert_eq!(py_fixed(1e22, 2), "10000000000000000000000.00");
        assert_eq!(py_fixed(f64::NAN, 1), "nan");
        assert_eq!(py_fixed(f64::INFINITY, 1), "inf");
        assert_eq!(py_fixed(f64::NEG_INFINITY, 1), "-inf");
        // Subnormal magnitudes round to zero like CPython's "%.2f".
        assert_eq!(py_fixed(5e-324, 2), "0.00");
        assert_eq!(py_fixed(f64::MIN_POSITIVE, 2), "0.00");
    }

    #[test]
    fn py_round_int_str_matches_str_int_round() {
        assert_eq!(py_round_int_str(0.5), "0");
        assert_eq!(py_round_int_str(-0.5), "0");
        assert_eq!(py_round_int_str(2.5), "2");
        assert_eq!(py_round_int_str(-2.5), "-2");
        assert_eq!(py_round_int_str(3.5), "4");
        assert_eq!(py_round_int_str(-3.5), "-4");
        assert_eq!(py_round_int_str(4.5), "4");
        assert_eq!(py_round_int_str(2.675), "3");
        assert_eq!(py_round_int_str(0.15), "0");
        assert_eq!(py_round_int_str(-0.4), "0");
        assert_eq!(py_round_int_str(-0.0), "0");
        assert_eq!(py_round_int_str(0.0), "0");
        assert_eq!(py_round_int_str(5.0), "5");
        assert_eq!(py_round_int_str(1234.0), "1234");
        assert_eq!(py_round_int_str(1e20), "100000000000000000000");
        // Non-finite input never reaches this helper from the board; it
        // formats as the missing sentinel instead of panicking.
        assert_eq!(py_round_int_str(f64::NAN), MISS);
        assert_eq!(py_round_int_str(f64::INFINITY), MISS);
    }

    #[test]
    fn board_integer_and_decimal_formats() {
        assert_eq!(format_i0_core(None), "--");
        assert_eq!(format_i0_core(Some(1234.56)), "1235");
        assert_eq!(format_i0_core(Some(0.5)), "0");
        assert_eq!(format_i0_core(Some(-0.4)), "0");
        assert_eq!(format_f1_core(None), "--");
        assert_eq!(format_f1_core(Some(12.25)), "12.2");
        assert_eq!(format_f1_core(Some(-0.04)), "-0.0");
        assert_eq!(format_f2_core(None), "--");
        assert_eq!(format_f2_core(Some(1.125)), "1.12");
        assert_eq!(format_f2_core(Some(2.675)), "2.67");
        assert_eq!(format_f2_core(Some(5.175)), "5.17");
    }

    #[test]
    fn board_dirspd_formats_direction_and_speed() {
        // Round to integer, floor-mod the direction, zero-pad both fields.
        assert_eq!(dirspd_text(359.7, 5.4), "000/05");
        assert_eq!(dirspd_text(359.4, 14.0), "359/14");
        assert_eq!(dirspd_text(-359.7, 0.0), "000/00");
        assert_eq!(dirspd_text(270.4, -2.4), "270/-2");
    }

    #[test]
    fn board_temp_and_pwat_match_the_reference() {
        assert_eq!(format_temp_core(None, false), "--");
        assert_eq!(format_temp_core(Some(72.0), false), "72 \u{b0}F");
        assert_eq!(format_temp_core(Some(98.6), false), "99 \u{b0}F");
        assert_eq!(format_temp_core(Some(-40.0), false), "-40 \u{b0}F");
        assert_eq!(format_temp_core(Some(100.0), true), "38\u{b0}C");
        assert_eq!(format_temp_core(Some(98.6), true), "37\u{b0}C");
        assert_eq!(format_temp_core(Some(-40.0), true), "-40\u{b0}C");
        assert_eq!(format_temp_core(Some(4500.4), true), "2482\u{b0}C");

        assert_eq!(format_pwat_core(None, false), "--");
        assert_eq!(format_pwat_core(Some(1.25), false), "1.25\u{a0}in");
        assert_eq!(format_pwat_core(Some(0.756), false), "0.76\u{a0}in");
        assert_eq!(format_pwat_core(Some(0.005), false), "0.01\u{a0}in");
        assert_eq!(format_pwat_core(Some(1.25), true), "3.2 cm");
        assert_eq!(format_pwat_core(Some(0.75), true), "1.9 cm");
        assert_eq!(format_pwat_core(Some(3.0), true), "7.6 cm");
    }

    #[test]
    fn board_uv_dirspd_matches_python_math() {
        // (u, v) = (3, 4): hypot 5, direction (270 - atan2(4, 3)) % 360 = 217.
        assert_eq!(uv_dirspd_text(3.0, 4.0, 1.0), "217/05");
        // Zero vector: atan2(0, 0) = 0 -> direction 270, speed 0.
        assert_eq!(uv_dirspd_text(0.0, 0.0, 1.0), "270/00");
        // knots -> m/s conversion applies to the magnitude only.
        let kt_ms = uv_dirspd_text(3.0, 4.0, KT_TO_MS);
        let expected = format!("217/{:02}", py_round_i64(5.0 * KT_TO_MS));
        assert_eq!(kt_ms, expected);
        // 5 kt is 2.57222 m/s, which rounds to 3.
        assert_eq!(py_round_i64(5.0 * KT_TO_MS), 3);
        assert_eq!(kt_ms, "217/03");
    }

    #[test]
    fn board_wind_speed_factor_only_converts_ms() {
        assert_eq!(wind_speed_factor("knots"), 1.0);
        assert_eq!(wind_speed_factor("m/s"), KT_TO_MS);
        assert_eq!(wind_speed_factor("mph"), 1.0);
    }

    #[test]
    fn board_barb_components_decompose_like_the_python_loops() {
        assert_eq!(barb_components_core(47.0), (45, 0, 4, 1));
        assert_eq!(barb_components_core(137.0), (135, 2, 3, 1));
        assert_eq!(barb_components_core(93.0), (95, 1, 4, 1));
        assert_eq!(barb_components_core(55.0), (55, 1, 0, 1));
        assert_eq!(barb_components_core(5.0), (5, 0, 0, 1));
        assert_eq!(barb_components_core(3.2), (5, 0, 0, 1));
        assert_eq!(barb_components_core(2.4), (0, 0, 0, 0));
        assert_eq!(barb_components_core(0.0), (0, 0, 0, 0));
        assert_eq!(barb_components_core(-12.0), (-10, 0, 0, 0));
    }

    #[test]
    fn board_cinh_legacy_tier_walks_the_legacy_breakpoints() {
        assert_eq!(cinh_legacy_tier_core(None), 0);
        assert_eq!(cinh_legacy_tier_core(Some(0.0)), 1);
        assert_eq!(cinh_legacy_tier_core(Some(-49.9)), 1);
        assert_eq!(cinh_legacy_tier_core(Some(-50.0)), 1);
        assert_eq!(cinh_legacy_tier_core(Some(-50.1)), 2);
        assert_eq!(cinh_legacy_tier_core(Some(-100.0)), 2);
        assert_eq!(cinh_legacy_tier_core(Some(-100.1)), 3);
        assert_eq!(cinh_legacy_tier_core(Some(f64::NAN)), 3);
    }

    #[test]
    fn board_cape3_tier_uses_strict_thresholds() {
        assert_eq!(cape3_tier_core(None), 0);
        assert_eq!(cape3_tier_core(Some(0.0)), 0);
        assert_eq!(cape3_tier_core(Some(25.0)), 0);
        assert_eq!(cape3_tier_core(Some(25.1)), 1);
        assert_eq!(cape3_tier_core(Some(50.1)), 2);
        assert_eq!(cape3_tier_core(Some(76.0)), 3);
        assert_eq!(cape3_tier_core(Some(101.0)), 4);
        assert_eq!(cape3_tier_core(Some(125.0)), 4);
        assert_eq!(cape3_tier_core(Some(125.1)), 5);
        assert_eq!(cape3_tier_core(Some(f64::NAN)), 0);
    }

    #[test]
    fn ship_scale_fraction_clamps_to_the_axis() {
        assert_eq!(ship_scale_fraction_core(0.0, 0.0, 6.0), 0.0);
        assert_eq!(ship_scale_fraction_core(3.0, 0.0, 6.0), 0.5);
        assert_eq!(ship_scale_fraction_core(6.0, 0.0, 6.0), 1.0);
        assert_eq!(ship_scale_fraction_core(9.0, 0.0, 6.0), 1.0);
        assert_eq!(ship_scale_fraction_core(-1.0, 0.0, 6.0), 0.0);
        // Degenerate span collapses to the left end.
        assert_eq!(ship_scale_fraction_core(4.0, 6.0, 6.0), 0.0);
        assert_eq!(ship_scale_fraction_core(4.0, 6.0, 5.0), 0.0);
        // NaN propagates like the Python comparisons.
        assert!(ship_scale_fraction_core(f64::NAN, 0.0, 6.0).is_nan());
    }

    #[test]
    #[allow(clippy::approx_constant)]
    fn ship_format_value_matches_plotship_fmt() {
        assert_eq!(ship_format_value_core(None), "--");
        assert_eq!(ship_format_value_core(Some(3.0)), "3");
        assert_eq!(ship_format_value_core(Some(3.14)), "3.1");
        assert_eq!(ship_format_value_core(Some(2.5)), "2.5");
        assert_eq!(ship_format_value_core(Some(2.675)), "2.7");
        assert_eq!(ship_format_value_core(Some(-0.5)), "-0.5");
        assert_eq!(ship_format_value_core(Some(6.0)), "6");
        assert_eq!(ship_format_value_core(Some(1000.0)), "1000");
        assert_eq!(ship_format_value_core(Some(1234.56)), "1234.6");
        assert_eq!(ship_format_value_core(Some(0.05)), "0.1");
        assert_eq!(ship_format_value_core(Some(f64::NAN)), "--");
        assert_eq!(ship_format_value_core(Some(f64::INFINITY)), "--");
    }

    #[test]
    fn custom_panel_unit_normalisation() {
        assert_eq!(normalise_unit_core(None), "");
        assert_eq!(normalise_unit_core(Some("")), "");
        assert_eq!(normalise_unit_core(Some("   ")), "");
        assert_eq!(normalise_unit_core(Some("unitless")), "");
        assert_eq!(normalise_unit_core(Some(" Unitless ")), "");
        assert_eq!(normalise_unit_core(Some("NONE")), "");
        assert_eq!(normalise_unit_core(Some("none")), "");
        assert_eq!(normalise_unit_core(Some("m/s")), "m/s");
        // Non-matching strings are returned unchanged, whitespace included.
        assert_eq!(normalise_unit_core(Some(" g/kg ")), " g/kg ");
        assert_eq!(normalise_unit_core(Some("J/kg")), "J/kg");
    }

    #[test]
    fn custom_panel_number_format_matches_fmt_value() {
        assert_eq!(custom_format_number_core(None, None), "--");
        assert_eq!(custom_format_number_core(Some(5.0), None), "5");
        assert_eq!(custom_format_number_core(Some(125.5), None), "125.5");
        assert_eq!(custom_format_number_core(Some(2.675), None), "2.7");
        assert_eq!(custom_format_number_core(Some(999.9), None), "999.9");
        assert_eq!(custom_format_number_core(Some(-0.5), None), "-0.5");
        assert_eq!(custom_format_number_core(Some(1234.56), None), "1235");
        assert_eq!(custom_format_number_core(Some(12345.678), None), "12346");
        assert_eq!(
            custom_format_number_core(Some(1e20), None),
            "100000000000000000000"
        );
        // vgp keeps two decimals.
        assert_eq!(custom_format_number_core(Some(1.2345), Some("vgp")), "1.23");
        assert_eq!(custom_format_number_core(Some(0.005), Some("vgp")), "0.01");
        assert_eq!(custom_format_number_core(Some(12.5), Some("vgp")), "12.50");
        assert_eq!(custom_format_number_core(Some(2.5), Some("vgp")), "2.50");
        // Any other parameter name takes the generic path.
        assert_eq!(custom_format_number_core(Some(1.2345), Some("dcp")), "1.2");
        // Non-finite renders as missing.
        assert_eq!(custom_format_number_core(Some(f64::NAN), None), "--");
        assert_eq!(
            custom_format_number_core(Some(f64::NEG_INFINITY), Some("vgp")),
            "--"
        );
    }

    #[test]
    fn scaled_rounding_handles_extreme_magnitudes_without_panicking() {
        // |x| far beyond u128 falls back to Rust's exact digit generation:
        // a huge all-digit string with a correct sign.
        let huge = py_fixed(1e300, 0);
        assert!(huge.len() >= 300);
        assert!(huge.chars().all(|c| c.is_ascii_digit()));
        let negative_huge = py_fixed(-1e300, 2);
        assert!(negative_huge.starts_with('-'));
        // The sign is appended exactly once (no double negative).
        assert!(!negative_huge.starts_with("--"));
        assert!(negative_huge[1..]
            .chars()
            .all(|c| c.is_ascii_digit() || c == '.'));
    }
}

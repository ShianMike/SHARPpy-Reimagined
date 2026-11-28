//! Rust mirror of the documented foreground colour scheme in
//! `sharpmod/viz/colors.py` and the colour-scale helpers reused from it in
//! `sharpmod/viz/box_field.py`.
//!
//! Tier colours are recomputed from the current value on every call, the
//! theme resolver reproduces the WCAG-anchored integer walk toward black for
//! light canvases, and the box-field helpers carry the ramp, normalise,
//! scale, and cell-sampling geometry.  Python `float()` coercion, the
//! `"--"`/blank missing-value sentinels, and the `MaskedConstant` probe live
//! at the PyO3 boundary; the scheme's rules are pure Rust so they stay
//! testable without a Python interpreter.

use std::collections::HashMap;

use pyo3::exceptions::{PyKeyError, PyValueError};
use pyo3::prelude::*;

pub(crate) type Rgb = (u8, u8, u8);
pub(crate) type SemanticPalette = HashMap<String, String>;
pub(crate) type RampStops = Vec<(f64, String)>;

const BLACK: &str = "#000000";
const WHITE: &str = "#ffffff";
const FG_COLOR: &str = WHITE;
const ALERT_L1_COLOR: &str = "#c8911f";
const ALERT_L2_COLOR: &str = "#e0a800";
const MISSING_STR: &str = "--";
const GRADIENT_YELLOW: &str = "#FFFF00";
const GRADIENT_RED: &str = "#FF0000";
const GRADIENT_PINK: &str = "#FF00FF";
const GRADIENT_CYAN: &str = "#00FFFF";

/// Alert-tier palette indexed 0..6 (`colors.ALERT_TIERS`).
const ALERT_TIERS: [&str; 7] = [
    "#775000", "#c8911f", "#e0a800", "#ffff00", "#ff0000", "#e700df", "#ff00ff",
];

/// Dispatch keys of `colors._TIER_DISPATCH` in Python's `sorted()` order, as
/// rendered by the unknown-parameter error message.
const TIER_KEYS: [&str; 23] = [
    "cape",
    "cinh",
    "dcp",
    "ecape",
    "ehi",
    "hgz_cape",
    "lapse_rate",
    "lcl",
    "li",
    "lrghail",
    "lscp",
    "mcs",
    "mcs_index",
    "modified_sherbe",
    "ncape",
    "nstp",
    "peskov",
    "scp",
    "ship",
    "stp_cin",
    "stp_effective",
    "stp_fixed",
    "sweat",
];

/// Semantic renderer roles of `colors._SEMANTIC_COLORS`, in module order.
const SEMANTIC_COLORS: [(&str, &str); 28] = [
    ("neutral", WHITE),
    ("header", WHITE),
    ("rule", "#8a8a8a"),
    ("cyan", "#00b0b0"),
    ("magenta", "#ff40ff"),
    ("red", "#ff4040"),
    ("yellow", "#e0c000"),
    ("green", "#00ff00"),
    ("orange", "#ffa500"),
    ("blue", "#3399ff"),
    ("amber_l1", ALERT_L1_COLOR),
    ("amber_l2", ALERT_L2_COLOR),
    ("profile", "#44ddaa"),
    ("cyclonic", "#ff3333"),
    ("anticyclonic", "#4488ff"),
    ("border", "#3399cc"),
    ("grid", "#33506a"),
    ("marker_gray", "#b8bcc2"),
    ("marker_orange", "#ff8800"),
    ("marker_yellow", "#ffcc00"),
    ("corfidi", "#00bfff"),
    ("mpl", "#00d7ff"),
    ("hodo_0500", "#ff00ff"),
    ("tornado_ef1", "#006600"),
    ("tornado_ef2", "#ffcc33"),
    ("tornado_ef3", "#ff0000"),
    ("tornado_ef4", "#ff00ff"),
    ("conditional_grid", "#0080ff"),
];

const CAPE_RULES: [(f64, usize); 5] =
    [(4000.0, 6), (3000.0, 5), (2000.0, 4), (1000.0, 3), (0.0, 2)];
const CINH_RULES: [(f64, usize); 4] = [(-25.0, 6), (-50.0, 5), (-75.0, 4), (-125.0, 3)];
const LCL_RULES: [(f64, usize); 5] = [
    (750.0, 6),
    (1000.0, 5),
    (1250.0, 4),
    (1500.0, 3),
    (2000.0, 3),
];
const LI_RULES: [(f64, usize); 4] = [(-13.0, 6), (-10.0, 5), (-7.0, 4), (-4.0, 3)];

/// CPython's two-argument `min`/`max` keep the first operand when the second
/// is NaN, which is how a NaN span reaches the clamp in `FieldScale.normalize`.
fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// `repr()` of a Python `str`: single quotes unless the text contains `'` but
/// no `"`, with the usual control-character escapes.  Used verbatim in the
/// `!r` fields of the module's exception messages.
fn py_repr_string(value: &str) -> String {
    let quote = if value.contains('\'') && !value.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(value.len() + 2);
    out.push(quote);
    for ch in value.chars() {
        let code = ch as u32;
        match ch {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            _ if code == quote as u32 => {
                out.push('\\');
                out.push(ch);
            }
            _ if code < 0x20 || code == 0x7f => out.push_str(&format!("\\x{code:02x}")),
            _ => out.push(ch),
        }
    }
    out.push(quote);
    out
}

/// Parse a CSS-style hex colour without pulling Qt in: strip whitespace and
/// an optional leading `#`, expand the 3-hex shorthand, then require exactly
/// six hex digits (`colors._rgb`).
fn parse_rgb_core(color: &str) -> Option<Rgb> {
    let mut value = color.trim();
    if let Some(stripped) = value.strip_prefix('#') {
        value = stripped;
    }
    let expanded: String = if value.chars().count() == 3 {
        value.chars().flat_map(|ch| [ch, ch]).collect()
    } else {
        value.to_string()
    };
    if expanded.chars().count() != 6 || !expanded.chars().all(|c| c.is_ascii_hexdigit()) {
        return None;
    }
    Some((
        u8::from_str_radix(&expanded[0..2], 16).ok()?,
        u8::from_str_radix(&expanded[2..4], 16).ok()?,
        u8::from_str_radix(&expanded[4..6], 16).ok()?,
    ))
}

/// `colors._hex`: normalized lowercase `#rrggbb`.
fn hex_core((r, g, b): Rgb) -> String {
    format!("#{r:02x}{g:02x}{b:02x}")
}

fn linear_channel(channel: u8) -> f64 {
    let value = channel as f64 / 255.0;
    if value <= 0.04045 {
        value / 12.92
    } else {
        ((value + 0.055) / 1.055).powf(2.4)
    }
}

fn relative_luminance_core(rgb: Rgb) -> f64 {
    let (r, g, b) = rgb;
    0.2126 * linear_channel(r) + 0.7152 * linear_channel(g) + 0.0722 * linear_channel(b)
}

/// WCAG contrast ratio between two channels tuples (`colors.contrast_ratio`).
fn contrast_ratio_core(foreground: Rgb, background: Rgb) -> f64 {
    let first = relative_luminance_core(foreground);
    let second = relative_luminance_core(background);
    let lighter = first.max(second);
    let darker = first.min(second);
    (lighter + 0.05) / (darker + 0.05)
}

/// `round(channel * (1.0 - fraction))` on one walk step: Python's `round` is
/// banker's rounding, so ties resolve to the even neighbour.
fn fade_to_black(channel: u8, fade: f64) -> u8 {
    (f64::from(channel) * fade).round_ties_even() as u8
}

/// Linear ramp step (`box_field.ramp_color` body).
fn ramp_blend(start: u8, end: u8, weight: f64) -> u8 {
    (f64::from(start) + (f64::from(end) - f64::from(start)) * weight).round_ties_even() as u8
}

/// `colors._resolve_theme_color_cached` plus the `max(1.0, minimum)` clamp
/// that `resolve_theme_color` applies before caching.  `None` means a
/// malformed colour, where the caller falls back to the raw foreground
/// string exactly like the Python `except (TypeError, ValueError)` arm.
fn resolve_theme_color_core(
    color: &str,
    bg_color: &str,
    fg_color: &str,
    minimum: f64,
) -> Option<String> {
    let source_rgb = parse_rgb_core(color)?;
    let source = hex_core(source_rgb);
    let background_rgb = parse_rgb_core(bg_color)?;
    let foreground_rgb = parse_rgb_core(fg_color)?;
    let foreground = hex_core(foreground_rgb);
    // NaN clamps to 1.0 like Python's max(1.0, minimum).
    let threshold = if minimum > 1.0 { minimum } else { 1.0 };

    if relative_luminance_core(background_rgb) < 0.5 {
        return Some(source);
    }
    if contrast_ratio_core(source_rgb, background_rgb) >= threshold {
        return Some(source);
    }
    if source == WHITE && contrast_ratio_core(foreground_rgb, background_rgb) >= threshold {
        return Some(foreground);
    }
    for step in 1..=255u16 {
        let fraction = f64::from(step) / 255.0;
        let fade = 1.0 - fraction;
        let candidate_rgb = (
            fade_to_black(source_rgb.0, fade),
            fade_to_black(source_rgb.1, fade),
            fade_to_black(source_rgb.2, fade),
        );
        if contrast_ratio_core(candidate_rgb, background_rgb) >= threshold {
            return Some(hex_core(candidate_rgb));
        }
    }
    Some(BLACK.to_string())
}

/// `colors.resolve_theme_color`: never fails; malformed input returns the
/// configured foreground string unchanged.
fn resolve_theme_color_fallback(
    color: &str,
    bg_color: &str,
    fg_color: &str,
    minimum: f64,
) -> String {
    resolve_theme_color_core(color, bg_color, fg_color, minimum)
        .unwrap_or_else(|| fg_color.to_string())
}

/// String half of `colors.is_missing`: blank text or the `"--"` sentinel.
fn is_missing_text(text: &str) -> bool {
    let stripped = text.trim();
    stripped == MISSING_STR || stripped.is_empty()
}

fn common_gradient_core(
    value: Option<f64>,
    yellow: f64,
    red: f64,
    pink: f64,
    higher: bool,
) -> &'static str {
    let Some(v) = value else {
        return FG_COLOR;
    };
    if v == 0.0 {
        return FG_COLOR;
    }
    if higher {
        if v >= pink {
            return GRADIENT_PINK;
        }
        if v >= red {
            return GRADIENT_RED;
        }
        if v >= yellow {
            return GRADIENT_YELLOW;
        }
    } else {
        if v <= pink {
            return GRADIENT_PINK;
        }
        if v <= red {
            return GRADIENT_RED;
        }
        if v <= yellow {
            return GRADIENT_YELLOW;
        }
    }
    FG_COLOR
}

/// `colors._low_severe_composite_color`: the readable L1 brown for severe
/// composites in `[0, 1)`.
fn low_severe_composite_core(value: Option<f64>) -> Option<&'static str> {
    match value {
        Some(v) if (0.0..1.0).contains(&v) => Some(ALERT_L1_COLOR),
        _ => None,
    }
}

fn cape_color_core(value: Option<f64>, has_positive_cape: bool) -> &'static str {
    let Some(v) = value else {
        return FG_COLOR;
    };
    if !has_positive_cape || v <= 0.0 {
        return FG_COLOR;
    }
    for &(threshold, tier) in &CAPE_RULES {
        if v >= threshold {
            return ALERT_TIERS[tier];
        }
    }
    FG_COLOR
}

fn cinh_color_core(value: Option<f64>, cape: Option<f64>) -> &'static str {
    let (Some(v), Some(cape_v)) = (value, cape) else {
        return FG_COLOR;
    };
    if cape_v <= 0.0 {
        return FG_COLOR;
    }
    for &(threshold, tier) in &CINH_RULES {
        if v >= threshold {
            return ALERT_TIERS[tier];
        }
    }
    ALERT_TIERS[2]
}

fn lcl_color_core(value: Option<f64>, cape: Option<f64>) -> &'static str {
    let (Some(v), Some(cape_v)) = (value, cape) else {
        return FG_COLOR;
    };
    if v == 0.0 || cape_v <= 0.0 {
        return FG_COLOR;
    }
    for &(threshold, tier) in &LCL_RULES {
        if v < threshold {
            return ALERT_TIERS[tier];
        }
    }
    ALERT_TIERS[2]
}

fn li_color_core(value: Option<f64>, cape: Option<f64>) -> &'static str {
    let (Some(v), Some(cape_v)) = (value, cape) else {
        return FG_COLOR;
    };
    if v == 0.0 || cape_v <= 0.0 {
        return FG_COLOR;
    }
    for &(threshold, tier) in &LI_RULES {
        if v < threshold {
            return ALERT_TIERS[tier];
        }
    }
    ALERT_TIERS[2]
}

fn lapse_rate_color_core(value: Option<f64>) -> &'static str {
    let Some(v) = value else {
        return FG_COLOR;
    };
    if v == 0.0 {
        return FG_COLOR;
    }
    const RULES: [(f64, &str); 4] = [
        (6.0, "#00FF00"),
        (7.0, "#FFFF00"),
        (8.0, "#FFA500"),
        (9.0, "#FF0000"),
    ];
    for &(threshold, hexcolor) in &RULES {
        if v <= threshold {
            return hexcolor;
        }
    }
    "#FF00FF"
}

fn sweat_color_core(value: Option<f64>) -> &'static str {
    let Some(v) = value else {
        return FG_COLOR;
    };
    if v == 0.0 {
        return FG_COLOR;
    }
    const RULES: [(f64, &str); 4] = [
        (250.0, "#3399FF"),
        (350.0, "#FFFFFF"),
        (500.0, "#FFFF00"),
        (650.0, "#FF0000"),
    ];
    for &(threshold, hexcolor) in &RULES {
        if v < threshold {
            return hexcolor;
        }
    }
    "#FF00FF"
}

fn stp_fixed_color_core(value: Option<f64>) -> &'static str {
    if let Some(low) = low_severe_composite_core(value) {
        return low;
    }
    common_gradient_core(value, 1.0, 2.0, 5.0, true)
}

fn stp_effective_color_core(value: Option<f64>) -> &'static str {
    if let Some(low) = low_severe_composite_core(value) {
        return low;
    }
    common_gradient_core(value, 0.5, 2.0, 5.0, true)
}

fn ship_color_core(value: Option<f64>) -> &'static str {
    if let Some(low) = low_severe_composite_core(value) {
        return low;
    }
    common_gradient_core(value, 1.0, 2.0, 3.0, true)
}

fn dcp_color_core(value: Option<f64>) -> &'static str {
    if let Some(low) = low_severe_composite_core(value) {
        return low;
    }
    common_gradient_core(value, 1.0, 4.0, 6.0, true)
}

fn scp_color_core(value: Option<f64>) -> &'static str {
    let Some(v) = value else {
        return FG_COLOR;
    };
    if v < 0.0 {
        return GRADIENT_CYAN;
    }
    if let Some(low) = low_severe_composite_core(value) {
        return low;
    }
    common_gradient_core(value, 0.5, 2.0, 5.0, true)
}

/// `colors.tier_color` over a pre-normalised key: `None` means the parameter
/// is not one of the documented dispatch keys.
fn tier_color_core(
    key: &str,
    value: Option<f64>,
    cape: Option<f64>,
    has_positive_cape: bool,
) -> Option<&'static str> {
    Some(match key {
        "cape" => cape_color_core(value, has_positive_cape),
        "cinh" => cinh_color_core(value, cape),
        "lcl" => lcl_color_core(value, cape),
        "li" => li_color_core(value, cape),
        "lapse_rate" => lapse_rate_color_core(value),
        "stp_fixed" => stp_fixed_color_core(value),
        "stp_effective" | "stp_cin" => stp_effective_color_core(value),
        "scp" => scp_color_core(value),
        "ship" => ship_color_core(value),
        "lrghail" => common_gradient_core(value, 4.0, 7.0, 10.0, true),
        "sweat" => sweat_color_core(value),
        "dcp" => dcp_color_core(value),
        "ehi" => common_gradient_core(value, 1.0, 2.0, 3.0, true),
        "lscp" => common_gradient_core(value, -1.0, -4.0, -8.0, false),
        "nstp" => common_gradient_core(value, 1.0, 2.0, 4.0, true),
        "modified_sherbe" => common_gradient_core(value, 1.0, 2.0, 3.0, true),
        "mcs" | "mcs_index" => common_gradient_core(value, 1.0, 2.0, 3.0, true),
        "peskov" => common_gradient_core(value, 1.0, 4.0, 7.0, true),
        "hgz_cape" => common_gradient_core(value, 1000.0, 2500.0, 4000.0, true),
        "ncape" => common_gradient_core(value, 0.1, 0.2, 0.3, true),
        "ecape" => common_gradient_core(value, 1000.0, 2500.0, 4000.0, true),
        _ => return None,
    })
}

/// `colors.semantic_palette`: every documented role resolved against the
/// current canvas theme with the default 4.5:1 minimum.
fn semantic_palette_core(bg_color: &str, fg_color: &str) -> SemanticPalette {
    SEMANTIC_COLORS
        .iter()
        .map(|&(role, value)| {
            (
                role.to_string(),
                resolve_theme_color_fallback(value, bg_color, fg_color, 4.5),
            )
        })
        .collect()
}

/// `box_field.ramp_color` body.  Stops arrive pre-parsed; an unparseable stop
/// reads as `(0, 0, 0)` like an invalid `QColor`.  Empty stops have no first
/// entry to interpolate, where the Python reference raises; this mirror
/// returns black rather than crashing the paint path.
fn ramp_rgb_core(position: f64, stops: &[(f64, Rgb)]) -> Rgb {
    let Some(&(previous_stop, previous_color)) = stops.first() else {
        return (0, 0, 0);
    };
    let mut previous_stop = previous_stop;
    let mut previous_color = previous_color;
    for &(stop, color) in stops {
        if position <= stop {
            if stop <= previous_stop {
                return color;
            }
            let weight = (position - previous_stop) / (stop - previous_stop);
            return (
                ramp_blend(previous_color.0, color.0, weight),
                ramp_blend(previous_color.1, color.1, weight),
                ramp_blend(previous_color.2, color.2, weight),
            );
        }
        previous_stop = stop;
        previous_color = color;
    }
    stops[stops.len() - 1].1
}

/// `FieldScale.normalize` body: value already known finite and coercible.
fn field_normalize_core(number: f64, minimum: f64, maximum: f64) -> f64 {
    let span = maximum - minimum;
    if span <= 0.0 {
        return 0.5;
    }
    py_max(0.0, py_min(1.0, (number - minimum) / span))
}

/// `box_field.scale_for` geometry: `(low, high, use_diverging)`.
fn scale_for_core(
    minimum: f64,
    maximum: f64,
    p10: f64,
    p90: f64,
    robust: bool,
) -> (f64, f64, bool) {
    let (mut low, mut high) = (minimum, maximum);
    if robust && p90 > p10 {
        low = p10;
        high = p90;
    }
    if low < 0.0 && 0.0 < high {
        let limit = py_max(low.abs(), high.abs());
        return (-limit, limit, true);
    }
    (low, high, false)
}

/// `box_field._cell_polygon` edge sampling: `4 * max(1, segments)` geographic
/// vertices, starting from the top-left corner and walking clockwise.
fn cell_vertices_core(
    lon: f64,
    lat: f64,
    half_lon: f64,
    half_lat: f64,
    segments: i64,
) -> Vec<(f64, f64)> {
    let segments = segments.max(1);
    let corners = [
        (lon - half_lon, lat + half_lat),
        (lon + half_lon, lat + half_lat),
        (lon + half_lon, lat - half_lat),
        (lon - half_lon, lat - half_lat),
    ];
    let mut points = Vec::new();
    for (start, end) in corners.iter().zip(corners.iter().cycle().skip(1)) {
        for step in 0..segments {
            let fraction = step as f64 / segments as f64;
            points.push((
                start.0 + (end.0 - start.0) * fraction,
                start.1 + (end.1 - start.1) * fraction,
            ));
        }
    }
    points
}

fn invalid_color_error(color: &str) -> PyErr {
    PyValueError::new_err(format!(
        "unsupported color {}; expected #rrggbb",
        py_repr_string(color)
    ))
}

fn builtin_float(py: Python<'_>) -> Option<Bound<'_, PyAny>> {
    py.import("builtins").ok()?.getattr("float").ok()
}

/// Plain `float(value)`: never propagates Python errors.
fn py_float(py: Python<'_>, value: &Bound<'_, PyAny>) -> Option<f64> {
    builtin_float(py)?
        .call1((value,))
        .ok()?
        .extract::<f64>()
        .ok()
}

/// `colors.is_missing`: `None`, blank/`"--"` strings, the NumPy masked
/// constant, and anything whose `float()` is missing or non-finite.
fn py_is_missing(py: Python<'_>, value: &Bound<'_, PyAny>) -> bool {
    if value.is_none() {
        return true;
    }
    if let Ok(text) = value.extract::<String>() {
        return is_missing_text(&text);
    }
    if masked_constant(value) {
        return true;
    }
    match py_float(py, value) {
        Some(number) => !number.is_finite(),
        None => true,
    }
}

/// `colors._as_float`: a finite float, or `None` for every missing sentinel
/// and non-coercible value, in the same check order as the Python helper.
fn py_as_float(py: Python<'_>, value: &Bound<'_, PyAny>) -> Option<f64> {
    if value.is_none() {
        return None;
    }
    if let Ok(text) = value.extract::<String>() {
        if is_missing_text(&text) {
            return None;
        }
    }
    if masked_constant(value) {
        return None;
    }
    let number = py_float(py, value)?;
    number.is_finite().then_some(number)
}

/// `getattr(value, "__class__", None).__name__ == "MaskedConstant"`, done
/// without importing numpy.
fn masked_constant(value: &Bound<'_, PyAny>) -> bool {
    value
        .getattr("__class__")
        .and_then(|class| class.getattr("__name__"))
        .and_then(|name| name.extract::<String>())
        .is_ok_and(|name| name == "MaskedConstant")
}

/// Parse a CSS-style hex colour into its RGB channels.
#[pyfunction]
fn color_parse_rgb(color: &str) -> PyResult<Rgb> {
    parse_rgb_core(color).ok_or_else(|| invalid_color_error(color))
}

/// WCAG contrast ratio between two `#rrggbb` colours.
#[pyfunction]
fn color_contrast_ratio(foreground: &str, background: &str) -> PyResult<f64> {
    let foreground_rgb =
        parse_rgb_core(foreground).ok_or_else(|| invalid_color_error(foreground))?;
    let background_rgb =
        parse_rgb_core(background).ok_or_else(|| invalid_color_error(background))?;
    Ok(contrast_ratio_core(foreground_rgb, background_rgb))
}

/// Keep `color` readable on the given canvas, darkening it only when a light
/// theme needs more contrast.
#[pyfunction]
#[pyo3(signature = (color, bg_color, fg_color, minimum = 4.5))]
fn color_resolve_theme(color: &str, bg_color: &str, fg_color: &str, minimum: f64) -> String {
    resolve_theme_color_fallback(color, bg_color, fg_color, minimum)
}

/// Every documented semantic renderer role resolved for the current theme.
#[pyfunction]
fn color_semantic_palette(bg_color: &str, fg_color: &str) -> SemanticPalette {
    semantic_palette_core(bg_color, fg_color)
}

/// Whether `value` should be treated as missing/undefined.
#[pyfunction]
fn color_is_missing(py: Python<'_>, value: &Bound<'_, PyAny>) -> bool {
    py_is_missing(py, value)
}

/// The white/yellow/red/pink gradient shared by composite and index values.
#[pyfunction]
#[pyo3(signature = (value, yellow, red, pink, higher = true))]
fn color_common_gradient(
    py: Python<'_>,
    value: &Bound<'_, PyAny>,
    yellow: f64,
    red: f64,
    pink: f64,
    higher: bool,
) -> String {
    common_gradient_core(py_as_float(py, value), yellow, red, pink, higher).to_string()
}

/// Recompute the tier colour for `param` from the current `value`.
#[pyfunction]
#[pyo3(signature = (param, value, cape = None, has_positive_cape = true, bg_color = None, fg_color = None))]
fn color_tier_for_param(
    py: Python<'_>,
    param: &str,
    value: &Bound<'_, PyAny>,
    cape: Option<&Bound<'_, PyAny>>,
    has_positive_cape: bool,
    bg_color: Option<&str>,
    fg_color: Option<&str>,
) -> PyResult<String> {
    let key = param.trim().to_lowercase();
    let value_number = py_as_float(py, value);
    let cape_number = cape.and_then(|cape_value| py_as_float(py, cape_value));
    let Some(resolved) = tier_color_core(&key, value_number, cape_number, has_positive_cape) else {
        let keys = TIER_KEYS
            .iter()
            .map(|key| format!("'{key}'"))
            .collect::<Vec<_>>()
            .join(", ");
        return Err(PyKeyError::new_err(format!(
            "unknown tier parameter {}; expected one of [{keys}]",
            py_repr_string(param)
        )));
    };
    match bg_color {
        Some(bg) => Ok(resolve_theme_color_fallback(
            resolved,
            bg,
            fg_color.unwrap_or(FG_COLOR),
            4.5,
        )),
        None => Ok(resolved.to_string()),
    }
}

/// The ramp colour at `fraction` in `[0, 1]` as `(r, g, b)`.
#[pyfunction]
fn box_field_ramp_rgb(
    py: Python<'_>,
    fraction: &Bound<'_, PyAny>,
    stops: RampStops,
) -> PyResult<Rgb> {
    let parsed: Vec<(f64, Rgb)> = stops
        .iter()
        .map(|(position, color)| (*position, parse_rgb_core(color).unwrap_or((0, 0, 0))))
        .collect();
    let position = py_float(py, fraction)
        .filter(|position| position.is_finite())
        .unwrap_or(0.0);
    let position = py_max(0.0, py_min(1.0, position));
    Ok(ramp_rgb_core(position, &parsed))
}

/// `value`'s position in `[0, 1]`, or `None` when it is absent.
#[pyfunction]
fn box_field_normalize(
    py: Python<'_>,
    value: &Bound<'_, PyAny>,
    minimum: f64,
    maximum: f64,
) -> Option<f64> {
    let number = py_float(py, value)?;
    if !number.is_finite() {
        return None;
    }
    Some(field_normalize_core(number, minimum, maximum))
}

/// `(low, high, use_diverging)` colour range for a field's statistics.
#[pyfunction]
#[pyo3(signature = (minimum, maximum, p10, p90, robust = true))]
fn box_field_scale_range(
    minimum: f64,
    maximum: f64,
    p10: f64,
    p90: f64,
    robust: bool,
) -> (f64, f64, bool) {
    scale_for_core(minimum, maximum, p10, p90, robust)
}

/// Geographic vertices of one sampled cell, top-left clockwise.
#[pyfunction]
#[pyo3(signature = (lon, lat, half_lon, half_lat, segments = 4))]
fn box_field_cell_vertices(
    lon: f64,
    lat: f64,
    half_lon: f64,
    half_lat: f64,
    segments: i64,
) -> Vec<(f64, f64)> {
    cell_vertices_core(lon, lat, half_lon, half_lat, segments)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(color_parse_rgb, module)?)?;
    module.add_function(wrap_pyfunction!(color_contrast_ratio, module)?)?;
    module.add_function(wrap_pyfunction!(color_resolve_theme, module)?)?;
    module.add_function(wrap_pyfunction!(color_semantic_palette, module)?)?;
    module.add_function(wrap_pyfunction!(color_is_missing, module)?)?;
    module.add_function(wrap_pyfunction!(color_common_gradient, module)?)?;
    module.add_function(wrap_pyfunction!(color_tier_for_param, module)?)?;
    module.add_function(wrap_pyfunction!(box_field_ramp_rgb, module)?)?;
    module.add_function(wrap_pyfunction!(box_field_normalize, module)?)?;
    module.add_function(wrap_pyfunction!(box_field_scale_range, module)?)?;
    module.add_function(wrap_pyfunction!(box_field_cell_vertices, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sequential() -> Vec<(f64, Rgb)> {
        [
            (0.00, "#2B3A8F"),
            (0.20, "#1E88C7"),
            (0.40, "#3FB878"),
            (0.60, "#E8D44D"),
            (0.80, "#E8873A"),
            (1.00, "#C8353A"),
        ]
        .iter()
        .map(|&(position, color)| (position, parse_rgb_core(color).unwrap()))
        .collect()
    }

    #[test]
    fn parse_rgb_accepts_supported_forms() {
        assert_eq!(parse_rgb_core("#ff00ff"), Some((255, 0, 255)));
        assert_eq!(parse_rgb_core("ff00ff"), Some((255, 0, 255)));
        assert_eq!(parse_rgb_core(" #F0e1d2 "), Some((240, 225, 210)));
        assert_eq!(parse_rgb_core("abc"), Some((170, 187, 204)));
        assert_eq!(parse_rgb_core("#abc"), Some((170, 187, 204)));
    }

    #[test]
    fn parse_rgb_rejects_malformed_and_quotes_like_python() {
        assert_eq!(parse_rgb_core(""), None);
        assert_eq!(parse_rgb_core("#"), None);
        assert_eq!(parse_rgb_core("#12345"), None);
        assert_eq!(parse_rgb_core("1234567"), None);
        assert_eq!(parse_rgb_core("12"), None);
        assert_eq!(parse_rgb_core("#ff00fg"), None);
        assert_eq!(parse_rgb_core("##ff00ff"), None);
        assert_eq!(py_repr_string("oops"), "'oops'");
        assert_eq!(py_repr_string(""), "''");
        assert_eq!(py_repr_string("isn't"), "\"isn't\"");
        assert_eq!(py_repr_string("say \"hi\""), "'say \"hi\"'");
        assert_eq!(py_repr_string("tab\tend"), "'tab\\tend'");
    }

    #[test]
    fn contrast_ratio_matches_wcag() {
        let white = parse_rgb_core("#ffffff").unwrap();
        let black = parse_rgb_core("#000000").unwrap();
        let black_on_white = contrast_ratio_core(black, white);
        assert!((black_on_white - 21.0).abs() < 1e-9, "{black_on_white}");
        assert_eq!(contrast_ratio_core(white, white), 1.0);
        // #767676 on white is the well-known 4.54:1 minimum body-text gray.
        let gray = parse_rgb_core("#767676").unwrap();
        let ratio = contrast_ratio_core(gray, white);
        assert!(ratio >= 4.5, "{ratio}");
        assert!((ratio - 4.54).abs() < 0.01, "{ratio}");
    }

    #[test]
    fn resolve_theme_dark_background_keeps_source() {
        assert_eq!(
            resolve_theme_color_core("#ff00ff", "#000000", "#000000", 4.5),
            Some("#ff00ff".to_string())
        );
        assert_eq!(
            resolve_theme_color_core("#ff4040", "#123456", "#abcdef", 4.5),
            Some("#ff4040".to_string())
        );
    }

    #[test]
    fn resolve_theme_light_background_darkens_minimally() {
        // White on white with a dark configured foreground becomes the foreground.
        assert_eq!(
            resolve_theme_color_core("#ffffff", "#ffffff", "#000000", 4.5),
            Some("#000000".to_string())
        );
        // A light gray is darkened exactly enough to clear 4.5:1.
        assert_eq!(
            resolve_theme_color_core("#eeeeee", "#ffffff", "#000000", 4.5),
            Some("#767676".to_string())
        );
        // Already-compliant colours are unchanged in normalized form.
        assert_eq!(
            resolve_theme_color_core("#800000", "#ffffff", "#000000", 4.5),
            Some("#800000".to_string())
        );
        // A sub-1.0 threshold (and NaN) clamps to 1.0 like Python's max(1.0, x).
        assert_eq!(
            resolve_theme_color_core("#eeeeee", "#ffffff", "#000000", 0.5),
            Some("#eeeeee".to_string())
        );
        assert_eq!(
            resolve_theme_color_core("#eeeeee", "#ffffff", "#000000", f64::NAN),
            Some("#eeeeee".to_string())
        );
        // An unreachable threshold falls back to black, via the walk or the tail.
        assert_eq!(
            resolve_theme_color_core("#eeeeee", "#ffffff", "#000000", 21.0),
            Some("#000000".to_string())
        );
        assert_eq!(
            resolve_theme_color_core("#eeeeee", "#ffffff", "#000000", 25.0),
            Some("#000000".to_string())
        );
    }

    #[test]
    fn resolve_theme_malformed_input_falls_back_to_foreground() {
        assert_eq!(
            resolve_theme_color_fallback("not_hex", "#ffffff", "#123456", 4.5),
            "#123456"
        );
        assert_eq!(
            resolve_theme_color_fallback("#ffffff", "nope", "#123456", 4.5),
            "#123456"
        );
        // The fallback returns the raw foreground string, whitespace included.
        assert_eq!(
            resolve_theme_color_fallback("#ffffff", "#ffffff", "  raw  ", 4.5),
            "  raw  "
        );
    }

    #[test]
    fn semantic_palette_dark_background_is_byte_exact() {
        assert_eq!(SEMANTIC_COLORS.len(), 28);
        let palette = semantic_palette_core("#000000", "#ffffff");
        assert_eq!(palette.len(), 28);
        assert_eq!(palette.get("tornado_ef4"), Some(&"#ff00ff".to_string()));
        assert_eq!(
            palette.get("conditional_grid"),
            Some(&"#0080ff".to_string())
        );
        assert_eq!(palette.get("amber_l1"), Some(&"#c8911f".to_string()));
        assert_eq!(palette.get("amber_l2"), Some(&"#e0a800".to_string()));
        assert_eq!(palette.get("rule"), Some(&"#8a8a8a".to_string()));
        assert!(!palette.contains_key("missing_role"));
    }

    #[test]
    fn semantic_palette_light_background_meets_contrast() {
        let white = parse_rgb_core("#ffffff").unwrap();
        let palette = semantic_palette_core("#ffffff", "#000000");
        assert_eq!(palette.len(), 28);
        // White roles swap to the configured dark foreground.
        assert_eq!(palette.get("neutral"), Some(&"#000000".to_string()));
        // Everything else clears 4.5:1 against the light canvas.
        for (role, resolved) in &palette {
            let rgb = parse_rgb_core(resolved)
                .unwrap_or_else(|| panic!("unparseable {role}: {resolved}"));
            let ratio = contrast_ratio_core(rgb, white);
            assert!(ratio >= 4.5, "{role} -> {resolved} = {ratio}");
        }
    }

    #[test]
    fn tier_cape_walks_the_documented_bands() {
        assert_eq!(
            tier_color_core("cape", Some(500.0), None, true),
            Some(ALERT_TIERS[2])
        );
        assert_eq!(
            tier_color_core("cape", Some(1500.0), None, true),
            Some(ALERT_TIERS[3])
        );
        assert_eq!(
            tier_color_core("cape", Some(2500.0), None, true),
            Some(ALERT_TIERS[4])
        );
        assert_eq!(
            tier_color_core("cape", Some(3500.0), None, true),
            Some(ALERT_TIERS[5])
        );
        assert_eq!(
            tier_color_core("cape", Some(4500.0), None, true),
            Some(ALERT_TIERS[6])
        );
        assert_eq!(
            tier_color_core("cape", Some(5000.0), None, false),
            Some(FG_COLOR)
        );
        assert_eq!(
            tier_color_core("cape", Some(-10.0), None, true),
            Some(FG_COLOR)
        );
        assert_eq!(
            tier_color_core("cape", Some(0.0), None, true),
            Some(FG_COLOR)
        );
        assert_eq!(tier_color_core("cape", None, None, true), Some(FG_COLOR));
    }

    #[test]
    fn tier_cape_gated_parameters_require_positive_cape() {
        assert_eq!(
            tier_color_core("cinh", Some(0.0), Some(1000.0), true),
            Some(ALERT_TIERS[6])
        );
        assert_eq!(
            tier_color_core("cinh", Some(-200.0), Some(1000.0), true),
            Some(ALERT_TIERS[2])
        );
        assert_eq!(
            tier_color_core("cinh", Some(-200.0), Some(0.0), true),
            Some(FG_COLOR)
        );
        assert_eq!(
            tier_color_core("cinh", Some(-200.0), None, true),
            Some(FG_COLOR)
        );
        assert_eq!(
            tier_color_core("cinh", None, Some(1000.0), true),
            Some(FG_COLOR)
        );
        assert_eq!(
            tier_color_core("lcl", Some(800.0), Some(1000.0), true),
            Some(ALERT_TIERS[5])
        );
        assert_eq!(
            tier_color_core("lcl", Some(700.0), Some(1000.0), true),
            Some(ALERT_TIERS[6])
        );
        assert_eq!(
            tier_color_core("lcl", Some(2500.0), Some(1000.0), true),
            Some(ALERT_TIERS[2])
        );
        assert_eq!(
            tier_color_core("lcl", Some(0.0), Some(1000.0), true),
            Some(FG_COLOR)
        );
        assert_eq!(
            tier_color_core("li", Some(-13.5), Some(1000.0), true),
            Some(ALERT_TIERS[6])
        );
        assert_eq!(
            tier_color_core("li", Some(-13.0), Some(1000.0), true),
            Some(ALERT_TIERS[5])
        );
        assert_eq!(
            tier_color_core("li", Some(-5.0), Some(1000.0), true),
            Some(ALERT_TIERS[3])
        );
        assert_eq!(
            tier_color_core("li", Some(-2.0), Some(1000.0), true),
            Some(ALERT_TIERS[2])
        );
    }

    #[test]
    fn tier_fixed_scales_match_the_reference() {
        assert_eq!(
            tier_color_core("lapse_rate", Some(5.0), None, true),
            Some("#00FF00")
        );
        assert_eq!(
            tier_color_core("lapse_rate", Some(6.5), None, true),
            Some("#FFFF00")
        );
        assert_eq!(
            tier_color_core("lapse_rate", Some(7.0), None, true),
            Some("#FFFF00")
        );
        assert_eq!(
            tier_color_core("lapse_rate", Some(8.5), None, true),
            Some("#FF0000")
        );
        assert_eq!(
            tier_color_core("lapse_rate", Some(9.1), None, true),
            Some("#FF00FF")
        );
        assert_eq!(
            tier_color_core("lapse_rate", Some(0.0), None, true),
            Some("#ffffff")
        );
        assert_eq!(
            tier_color_core("lapse_rate", None, None, true),
            Some("#ffffff")
        );
        assert_eq!(
            tier_color_core("sweat", Some(100.0), None, true),
            Some("#3399FF")
        );
        assert_eq!(
            tier_color_core("sweat", Some(250.0), None, true),
            Some("#FFFFFF")
        );
        assert_eq!(
            tier_color_core("sweat", Some(400.0), None, true),
            Some("#FFFF00")
        );
        assert_eq!(
            tier_color_core("sweat", Some(650.0), None, true),
            Some("#FF00FF")
        );
        assert_eq!(
            tier_color_core("sweat", Some(0.0), None, true),
            Some("#ffffff")
        );
    }

    #[test]
    fn tier_composites_use_the_modernized_brown_band() {
        for key in [
            "scp",
            "stp_fixed",
            "stp_effective",
            "stp_cin",
            "ship",
            "dcp",
        ] {
            for value in [0.0, 0.1, 0.5, 0.999] {
                assert_eq!(
                    tier_color_core(key, Some(value), None, true),
                    Some(ALERT_L1_COLOR),
                    "{key} {value}"
                );
            }
            assert_ne!(
                tier_color_core(key, Some(1.0), None, true),
                Some(ALERT_L1_COLOR)
            );
        }
        assert_eq!(
            tier_color_core("scp", Some(-0.5), None, true),
            Some(GRADIENT_CYAN)
        );
        assert_eq!(
            tier_color_core("scp", Some(1.0), None, true),
            Some(GRADIENT_YELLOW)
        );
        assert_eq!(
            tier_color_core("scp", Some(2.0), None, true),
            Some(GRADIENT_RED)
        );
        assert_eq!(
            tier_color_core("scp", Some(5.0), None, true),
            Some(GRADIENT_PINK)
        );
        assert_eq!(
            tier_color_core("stp_fixed", Some(2.0), None, true),
            Some(GRADIENT_RED)
        );
        assert_eq!(
            tier_color_core("stp_effective", Some(1.0), None, true),
            Some(GRADIENT_YELLOW)
        );
    }

    #[test]
    fn tier_plain_gradients_dispatch_exactly() {
        assert_eq!(
            tier_color_core("lrghail", Some(7.0), None, true),
            Some(GRADIENT_RED)
        );
        assert_eq!(
            tier_color_core("ehi", Some(1.0), None, true),
            Some(GRADIENT_YELLOW)
        );
        assert_eq!(
            tier_color_core("ehi", Some(2.0), None, true),
            Some(GRADIENT_RED)
        );
        assert_eq!(
            tier_color_core("ehi", Some(3.0), None, true),
            Some(GRADIENT_PINK)
        );
        assert_eq!(
            tier_color_core("lscp", Some(-2.0), None, true),
            Some(GRADIENT_YELLOW)
        );
        assert_eq!(
            tier_color_core("lscp", Some(-9.0), None, true),
            Some(GRADIENT_PINK)
        );
        assert_eq!(
            tier_color_core("nstp", Some(2.5), None, true),
            Some(GRADIENT_RED)
        );
        assert_eq!(
            tier_color_core("modified_sherbe", Some(0.0), None, true),
            Some(FG_COLOR)
        );
        assert_eq!(
            tier_color_core("mcs", Some(1.0), None, true),
            Some(GRADIENT_YELLOW)
        );
        assert_eq!(
            tier_color_core("mcs_index", Some(2.0), None, true),
            Some(GRADIENT_RED)
        );
        assert_eq!(
            tier_color_core("peskov", Some(1.0), None, true),
            Some(GRADIENT_YELLOW)
        );
        assert_eq!(
            tier_color_core("hgz_cape", Some(2500.0), None, true),
            Some(GRADIENT_RED)
        );
        assert_eq!(
            tier_color_core("ncape", Some(0.25), None, true),
            Some(GRADIENT_RED)
        );
        assert_eq!(
            tier_color_core("ecape", Some(5000.0), None, true),
            Some(GRADIENT_PINK)
        );
        assert_eq!(
            tier_color_core("dcp", Some(5.0), None, true),
            Some(GRADIENT_RED)
        );
        assert_eq!(
            tier_color_core("ship", Some(3.0), None, true),
            Some(GRADIENT_PINK)
        );
        // Every documented key dispatches with a missing value, and unknown
        // parameters (including case differences) are rejected.
        assert_eq!(TIER_KEYS.len(), 23);
        for key in TIER_KEYS {
            assert_eq!(
                tier_color_core(key, None, None, true),
                Some(FG_COLOR),
                "{key}"
            );
        }
        assert_eq!(tier_color_core("CAPE", Some(1500.0), None, true), None);
        assert_eq!(tier_color_core("not_a_param", Some(1.0), None, true), None);
    }

    #[test]
    fn common_gradient_and_missing_text_match_python() {
        assert_eq!(common_gradient_core(None, 1.0, 2.0, 3.0, true), "#ffffff");
        assert_eq!(
            common_gradient_core(Some(f64::NAN), 1.0, 2.0, 3.0, true),
            "#ffffff"
        );
        assert_eq!(
            common_gradient_core(Some(0.0), 1.0, 2.0, 3.0, true),
            "#ffffff"
        );
        assert_eq!(
            common_gradient_core(Some(3.0), 1.0, 2.0, 3.0, true),
            "#FF00FF"
        );
        assert_eq!(
            common_gradient_core(Some(2.5), 1.0, 2.0, 3.0, true),
            "#FF0000"
        );
        assert_eq!(
            common_gradient_core(Some(1.5), 1.0, 2.0, 3.0, true),
            "#FFFF00"
        );
        assert_eq!(
            common_gradient_core(Some(0.5), 1.0, 2.0, 3.0, true),
            "#ffffff"
        );
        assert_eq!(
            common_gradient_core(Some(-8.0), -1.0, -4.0, -8.0, false),
            "#FF00FF"
        );
        assert_eq!(
            common_gradient_core(Some(-5.0), -1.0, -4.0, -8.0, false),
            "#FF0000"
        );
        assert_eq!(
            common_gradient_core(Some(-1.5), -1.0, -4.0, -8.0, false),
            "#FFFF00"
        );
        assert_eq!(
            common_gradient_core(Some(0.0), -1.0, -4.0, -8.0, false),
            "#ffffff"
        );
        assert_eq!(
            common_gradient_core(Some(0.5), -1.0, -4.0, -8.0, false),
            "#ffffff"
        );
        assert!(is_missing_text("--"));
        assert!(is_missing_text("  --  "));
        assert!(is_missing_text("   "));
        assert!(is_missing_text(""));
        assert!(!is_missing_text("3.5"));
        assert!(!is_missing_text("-"));
    }

    #[test]
    fn box_ramp_interpolates_with_python_rounding() {
        let stops = sequential();
        assert_eq!(ramp_rgb_core(0.0, &stops), (43, 58, 143));
        assert_eq!(ramp_rgb_core(1.0, &stops), (200, 53, 58));
        assert_eq!(ramp_rgb_core(-4.0, &stops), (43, 58, 143));
        assert_eq!(ramp_rgb_core(9.0, &stops), (200, 53, 58));
        // 0.1 is midway between the first two stops; the red channel lands on
        // the exact 36.5 tie and banker's rounding picks the even neighbour.
        assert_eq!(ramp_rgb_core(0.1, &stops), (36, 97, 171));
        // A 127.5 channel tie also rounds to even (128).
        let halves = vec![(0.0, (0, 0, 0)), (1.0, (255, 0, 0))];
        assert_eq!(ramp_rgb_core(0.5, &halves), (128, 0, 0));
        // Equal stop positions return that stop's colour instead of dividing
        // by zero.
        let equal_positions = vec![(0.0, (1, 2, 3)), (0.0, (4, 5, 6)), (0.5, (7, 8, 9))];
        assert_eq!(ramp_rgb_core(0.0, &equal_positions), (1, 2, 3));
        assert_eq!(ramp_rgb_core(0.3, &[]), (0, 0, 0));
    }

    #[test]
    fn box_normalize_matches_field_scale_semantics() {
        assert_eq!(field_normalize_core(0.0, 0.0, 100.0), 0.0);
        assert_eq!(field_normalize_core(50.0, 0.0, 100.0), 0.5);
        assert_eq!(field_normalize_core(100.0, 0.0, 100.0), 1.0);
        assert_eq!(field_normalize_core(-10.0, 0.0, 100.0), 0.0);
        assert_eq!(field_normalize_core(1000.0, 0.0, 100.0), 1.0);
        // A uniform field lands mid-ramp instead of implying a spread.
        assert_eq!(field_normalize_core(5.0, 5.0, 5.0), 0.5);
        assert_eq!(field_normalize_core(-99.0, 5.0, 5.0), 0.5);
        // A NaN span collapses toward 1.0 like Python's max(0.0, min(1.0, nan)).
        assert_eq!(field_normalize_core(50.0, f64::NAN, 100.0), 1.0);
        assert_eq!(field_normalize_core(50.0, 0.0, f64::NAN), 1.0);
    }

    #[test]
    fn box_scale_range_robust_band_and_diverging() {
        assert_eq!(
            scale_for_core(5.0, 100.0, 15.0, 80.0, true),
            (15.0, 80.0, false)
        );
        assert_eq!(
            scale_for_core(5.0, 100.0, 15.0, 80.0, false),
            (5.0, 100.0, false)
        );
        // A degenerate percentile band falls back to the full range.
        assert_eq!(
            scale_for_core(5.0, 100.0, 80.0, 80.0, true),
            (5.0, 100.0, false)
        );
        // Straddling zero makes a symmetric diverging scale.
        assert_eq!(
            scale_for_core(-40.0, 10.0, -30.0, 8.0, true),
            (-30.0, 30.0, true)
        );
        assert_eq!(
            scale_for_core(-40.0, 10.0, -30.0, 8.0, false),
            (-40.0, 40.0, true)
        );
        assert_eq!(scale_for_core(0.0, 10.0, 2.0, 8.0, true), (2.0, 8.0, false));
    }

    #[test]
    fn box_cell_vertices_sample_edges_from_top_left() {
        let points = cell_vertices_core(0.0, 0.0, 1.0, 1.0, 4);
        assert_eq!(points.len(), 16);
        assert_eq!(points[0], (-1.0, 1.0));
        assert_eq!(points[3], (0.5, 1.0));
        assert_eq!(points[4], (1.0, 1.0));
        assert_eq!(points[8], (1.0, -1.0));
        assert_eq!(points[12], (-1.0, -1.0));
        assert_eq!(points[15], (-1.0, 0.5));
        let two = cell_vertices_core(0.0, 0.0, 1.0, 1.0, 2);
        assert_eq!(
            two,
            vec![
                (-1.0, 1.0),
                (0.0, 1.0),
                (1.0, 1.0),
                (1.0, 0.0),
                (1.0, -1.0),
                (0.0, -1.0),
                (-1.0, -1.0),
                (-1.0, 0.0),
            ]
        );
        // Degenerate segment counts clamp to one, like max(1, int(segments)).
        assert_eq!(cell_vertices_core(0.0, 0.0, 1.0, 1.0, 0).len(), 4);
        assert_eq!(cell_vertices_core(0.0, 0.0, 1.0, 1.0, -7).len(), 4);
    }
}

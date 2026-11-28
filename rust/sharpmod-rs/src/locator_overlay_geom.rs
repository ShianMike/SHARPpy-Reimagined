//! Overlay-paint geometry for the hodograph sounding locator (module M28).
//!
//! Rust mirror of the Python-side paint contracts in:
//!
//! * ``sharpmod.viz.hodo_overlay_drawing`` -- the world-copy range for raster
//!   blits, the opacity clamp, and the ring/marker pixel projections used by
//!   ``_draw_overlay_rasters`` and ``_draw_overlay_layers``;
//! * ``sharpmod.viz.hodo_locator`` -- ``overlay_timing_text`` /
//!   ``_format_utc`` and the coordinate-label filter in
//!   ``location_name_from_widget``.
//!
//! Parity notes: two-argument ``max``/``min`` keep CPython's NaN semantics;
//! every ``bounds`` is `(west, south, east, north)` and every paint rectangle
//! is `(left, top, width, height)`; ``QRectF::contains`` counts the edges.
//! The coordinate-label pattern is ``hodo_locator._COORDINATE_LABEL_RE``
//! re-implemented by hand (the crate carries no regex dependency), preserving
//! its leftmost-first alternation, greedy backtracking, case-insensitive
//! verbose-mode classes, and word boundaries.

use pyo3::exceptions::PyZeroDivisionError;
use pyo3::prelude::*;
use pyo3::types::PyDict;

/// Locator view bounds: `(west, south, east, north)`.
pub(crate) type Bounds = (f64, f64, f64, f64);
/// Paint rectangle: `(left, top, width, height)`.
pub(crate) type Rect = (f64, f64, f64, f64);
/// One projected polyline of `(x, y)` or `(lon, lat)` pairs.
pub(crate) type Line = Vec<(f64, f64)>;

/// Fixed indicator inset from the map edge, in pixels, from
/// ``hodo_locator._marker_position``.
const MARKER_EDGE_MARGIN: f64 = 3.0;

/// Why a projection failed: the Python seam raises ``ZeroDivisionError``
/// when a geographic denominator is exactly zero.
#[derive(Debug, PartialEq)]
pub(crate) struct ZeroDivision;

/// Mirrors CPython's two-argument ``max``/``min``: the second operand wins
/// only when the comparison is true, so a NaN second operand is ignored and a
/// NaN first operand survives.
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

/// Wrap ``lon`` onto the copy of the world closest to ``center``.
fn longitude_near_core(lon: f64, center: f64) -> f64 {
    center + ((lon - center + 180.0).rem_euclid(360.0)) - 180.0
}

/// ``_map_point``: plate carree projection onto ``(x, y)`` pixels.
fn map_point_core(rect: Rect, bounds: Bounds, lat: f64, lon: f64) -> (f64, f64) {
    let (left, top, width, height) = rect;
    let (west, south, east, north) = bounds;
    (
        left + (lon - west) / (east - west) * width,
        top + (north - lat) / (north - south) * height,
    )
}

/// ``QRectF::contains``: the edges belong to the rectangle.
fn rect_contains(rect: Rect, x: f64, y: f64) -> bool {
    let (left, top, width, height) = rect;
    x >= left && x <= left + width && y >= top && y <= top + height
}

/// Port of the world-copy range behind ``_draw_overlay_rasters``.
///
/// Given the raster's own east/west edges and the view's west/east edges,
/// returns ``(usable, first_copy, last_copy)``: the copies of the world the
/// blit must paint.  Non-finite edges are refused exactly like the pieces of
/// the Python ``try`` block that catch ``math.floor``'s ``ValueError`` and
/// skip the raster; an empty range (``first_copy > last_copy``) means no
/// copy reaches the view, and Python's ``range`` handles that naturally too.
pub(crate) fn raster_copy_range_core(
    raster_west: f64,
    raster_east: f64,
    west: f64,
    east: f64,
) -> (bool, i64, i64) {
    if !(raster_west.is_finite() && raster_east.is_finite() && west.is_finite() && east.is_finite())
    {
        return (false, 0, 0);
    }
    let first_copy = ((west - raster_east) / 360.0).floor() as i64 + 1;
    let last_copy = ((east - raster_west) / 360.0).ceil() as i64 - 1;
    (true, first_copy, last_copy)
}

/// Port of the opacity helper: ``min(1.0, max(0.0, float(opacity)))`` with a
/// fallback for values that cannot convert.  NaN compares as false against
/// both bounds, so it clamps to 0.0 exactly as CPython's max does.
pub(crate) fn clamp_opacity_core(coerced: Option<f64>, default: f64) -> f64 {
    match coerced {
        Some(value) => py_min(1.0, py_max(0.0, value)),
        None => default,
    }
}

/// Port of the ring projection loop in ``_draw_overlay_layers``: project each
/// ring of geographic ``(lon, lat)`` vertices onto pixel coordinates, keeping
/// the painter's rule that rings with fewer than three points draw nothing.
/// A degenerate view raises like the Python seam's float division.
pub(crate) fn project_shape_rings_core(
    rings: &[Line],
    bounds: Bounds,
    map_rect: Rect,
) -> Result<Vec<Line>, ZeroDivision> {
    let (west, south, east, north) = bounds;
    // Python only divides inside rings it actually projects, so an empty or
    // all-short ring list never raises.
    if rings.iter().any(|ring| ring.len() >= 3) && (east - west == 0.0 || north - south == 0.0) {
        return Err(ZeroDivision);
    }
    let mut projected: Vec<Line> = Vec::new();
    for ring in rings {
        if ring.len() < 3 {
            continue;
        }
        projected.push(
            ring.iter()
                .map(|&(lon, lat)| map_point_core(map_rect, bounds, lat, lon))
                .collect(),
        );
    }
    Ok(projected)
}

/// Port of ``hodo_locator._marker_position`` for overlay markers: map a point
/// through the wrap-aware projection, clamp to a visible edge indicator, and
/// report whether it landed inside before clamping.
pub(crate) fn project_marker_point_core(
    lat: f64,
    lon: f64,
    bounds: Bounds,
    map_rect: Rect,
) -> Result<(f64, f64, bool), ZeroDivision> {
    let (west, south, east, north) = bounds;
    if east - west == 0.0 || north - south == 0.0 {
        return Err(ZeroDivision);
    }
    let center = (west + east) / 2.0;
    let lon = longitude_near_core(lon, center);
    let (x, y) = map_point_core(map_rect, bounds, lat, lon);
    let inside = rect_contains(map_rect, x, y);
    let (left, top, width, height) = map_rect;
    let x = py_min(
        left + width - MARKER_EDGE_MARGIN,
        py_max(left + MARKER_EDGE_MARGIN, x),
    );
    let y = py_min(
        top + height - MARKER_EDGE_MARGIN,
        py_max(top + MARKER_EDGE_MARGIN, y),
    );
    Ok((x, y, inside))
}

/// Port of ``overlay_timing_text``'s word assembly: name the provider
/// timestamp's meaning instead of guessing validity.  An empty stamp is no
/// timing text at all.
pub(crate) fn overlay_timing_text_core(stamp: &str, basis: &str) -> String {
    if stamp.is_empty() {
        return String::new();
    }
    if basis == "retrieved" {
        return format!("retrieved {stamp}");
    }
    format!("actual {basis} {stamp}")
}

// ---------------------------------------------------------------------------
// Coordinate-label matching: hodo_locator._COORDINATE_LABEL_RE
// ---------------------------------------------------------------------------
//
//     (?ix)
//     (?:\b\d{1,2}(?:\.\d+)?\s*[NS]\s*[,/ ]+\s*\d{1,3}(?:\.\d+)?\s*[EW]\b)
//     |(?:[-+]?\d{1,2}(?:\.\d+)?\s*,\s*[-+]?\d{1,3}(?:\.\d+)?)
//
// Hand-rolled with Python re semantics: leftmost scanning position wins,
// alternatives are tried in order, and every quantifier backtracks greedily.
// ``\w`` and ``\s`` follow Python's str patterns closely enough for labels
// (\w is alphanumeric plus underscore, \s is whitespace).

/// One indexed character: byte offset plus the character.
type Indexed = (usize, char);

/// Python's ``\w`` for str patterns: alphanumerics and the underscore.
fn is_word_char(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

fn is_north_south(c: char) -> bool {
    matches!(c, 'n' | 's' | 'N' | 'S')
}

fn is_east_west(c: char) -> bool {
    matches!(c, 'e' | 'w' | 'E' | 'W')
}

/// ``\b`` before consuming the (digit) character at ``at``: the previous
/// character must not be a word character.
fn leading_word_boundary(chars: &[Indexed], at: usize) -> bool {
    at < chars.len() && (at == 0 || !is_word_char(chars[at - 1].1))
}

/// ``\b`` just past the character ending at ``at``: the next character must
/// not be a word character (or the text is over).
fn trailing_word_boundary(chars: &[Indexed], at: usize) -> bool {
    at >= chars.len() || !is_word_char(chars[at].1)
}

/// Consume one character matching ``matcher``, returning the next index.
fn at_char(chars: &[Indexed], at: usize, matcher: fn(char) -> bool) -> Option<usize> {
    if at < chars.len() && matcher(chars[at].1) {
        Some(at + 1)
    } else {
        None
    }
}

/// Possible ends for ``\d{min..=max}``, greedy: longest consumption first.
fn digit_ends(chars: &[Indexed], at: usize, min: usize, max: usize) -> Vec<usize> {
    let run = (at..chars.len())
        .take_while(|&i| chars[i].1.is_ascii_digit())
        .count()
        .min(max);
    if run < min {
        return Vec::new();
    }
    (min..=run).rev().map(|count| at + count).collect()
}

/// Possible ends for ``(?:\.\d+)?``, greedy: with the fraction first.
fn fraction_ends(chars: &[Indexed], at: usize) -> Vec<usize> {
    let mut ends = vec![at];
    if at < chars.len() && chars[at].1 == '.' {
        let digits = (at + 1..chars.len())
            .take_while(|&i| chars[i].1.is_ascii_digit())
            .count();
        if digits > 0 {
            ends.insert(0, at + 1 + digits);
        }
    }
    ends
}

/// Possible ends for ``\s*``, greedy: longest whitespace run first.
fn space_ends(chars: &[Indexed], at: usize) -> Vec<usize> {
    let spaces = (at..chars.len())
        .take_while(|&i| chars[i].1.is_whitespace())
        .count();
    (at..=at + spaces).rev().collect()
}

/// Possible ends for ``[,/ ]+``, greedy and non-empty.
fn separator_ends(chars: &[Indexed], at: usize) -> Vec<usize> {
    let run = (at..chars.len())
        .take_while(|&i| matches!(chars[i].1, ',' | '/' | ' '))
        .count();
    if run == 0 {
        return Vec::new();
    }
    (at + 1..=at + run).rev().collect()
}

/// Possible ends for ``[-+]?``, greedy: with the sign first.
fn sign_ends(chars: &[Indexed], at: usize) -> Vec<usize> {
    let mut ends = vec![at];
    if at < chars.len() && matches!(chars[at].1, '+' | '-') {
        ends.insert(0, at + 1);
    }
    ends
}

/// The bearing form: ``\b\d{1,2}(?:\.\d+)?\s*[NS]\s*[,/ ]+\s*
/// \d{1,3}(?:\.\d+)?\s*[EW]\b``.  Returns the match's end index.
fn match_bearing_form(chars: &[Indexed], start: usize) -> Option<usize> {
    if !leading_word_boundary(chars, start) {
        return None;
    }
    for lat_end in digit_ends(chars, start, 1, 2) {
        for frac_end in fraction_ends(chars, lat_end) {
            for pre_north_south in space_ends(chars, frac_end) {
                let Some(after_north_south) = at_char(chars, pre_north_south, is_north_south)
                else {
                    continue;
                };
                for pre_separator in space_ends(chars, after_north_south) {
                    for separator_end in separator_ends(chars, pre_separator) {
                        for post_separator in space_ends(chars, separator_end) {
                            for lon_end in digit_ends(chars, post_separator, 1, 3) {
                                for frac_end in fraction_ends(chars, lon_end) {
                                    for pre_east_west in space_ends(chars, frac_end) {
                                        let Some(end) = at_char(chars, pre_east_west, is_east_west)
                                        else {
                                            continue;
                                        };
                                        if trailing_word_boundary(chars, end) {
                                            return Some(end);
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    None
}

/// The comma form: ``[-+]?\d{1,2}(?:\.\d+)?\s*,\s*[-+]?\d{1,3}(?:\.\d+)?``.
/// Nothing follows the optional fraction, so the first completion is the
/// greedy one Python returns.
fn match_comma_form(chars: &[Indexed], start: usize) -> Option<usize> {
    for signed in sign_ends(chars, start) {
        for lat_end in digit_ends(chars, signed, 1, 2) {
            for frac_end in fraction_ends(chars, lat_end) {
                for pre_comma in space_ends(chars, frac_end) {
                    let Some(after_comma) = at_char(chars, pre_comma, |c| c == ',') else {
                        continue;
                    };
                    for post_comma in space_ends(chars, after_comma) {
                        for signed_lon in sign_ends(chars, post_comma) {
                            for lon_end in digit_ends(chars, signed_lon, 1, 3) {
                                if let Some(&end) = fraction_ends(chars, lon_end).first() {
                                    return Some(end);
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    None
}

/// ``re.search`` over the combined pattern: the leftmost position wins, and
/// at a shared position the bearing form outranks the comma form.  Returns
/// the byte range of the first match.
fn find_coordinate_label(text: &str) -> Option<(usize, usize)> {
    let chars: Vec<Indexed> = text.char_indices().collect();
    for start in 0..chars.len() {
        let end = match_bearing_form(&chars, start).or_else(|| match_comma_form(&chars, start));
        if let Some(end) = end {
            let (offset, last) = chars[end - 1];
            return Some((chars[start].0, offset + last.len_utf8()));
        }
    }
    None
}

/// Port of the legacy-label cut in ``location_name_from_widget``:
/// ``_COORDINATE_LABEL_RE.split(label, maxsplit=1)[0].strip(" ,-")`` -- the
/// text before the first coordinate label, stripped of spaces, commas, and
/// hyphens at both ends.  With no coordinate in the label, the whole
/// (stripped) label is returned, exactly as the Python split does.
pub(crate) fn strip_coordinate_label_core(text: &str) -> String {
    let prefix = match find_coordinate_label(text) {
        Some((start, _)) => &text[..start],
        None => text,
    };
    prefix.trim_matches([' ', ',', '-']).to_string()
}

fn builtin_float(py: Python<'_>) -> Option<Bound<'_, PyAny>> {
    py.import("builtins").ok()?.getattr("float").ok()
}

/// ``float(value)`` through the builtin, mirroring the try/except in
/// ``_draw_overlay_rasters``: anything that cannot convert falls back.
fn coerce_f64(value: &Bound<'_, PyAny>) -> Option<f64> {
    let float_fn = builtin_float(value.py())?;
    let converted = float_fn.call1((value,)).ok()?;
    converted.extract::<f64>().ok()
}

/// Port of ``_format_utc``'s decision core: only datetimes render, naive
/// values are treated as UTC, and the stamp is ``"%Y-%m-%d %H:%MZ"``.
fn format_utc_stamp(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    let datetime_module = PyModule::import(py, "datetime")?;
    let datetime_class = datetime_module.getattr("datetime")?;
    if !value.is_instance(&datetime_class)? {
        return Ok(None);
    }
    let timezone_utc = datetime_module.getattr("timezone")?.getattr("utc")?;
    let resolved = if value.getattr("tzinfo")?.is_none() {
        let kwargs = PyDict::new(py);
        kwargs.set_item("tzinfo", timezone_utc)?;
        value.call_method("replace", (), Some(&kwargs))?
    } else {
        value.call_method1("astimezone", (timezone_utc,))?
    };
    let stamp = resolved.call_method1("strftime", ("%Y-%m-%d %H:%MZ",))?;
    Ok(Some(stamp.extract::<String>()?))
}

/// Return the world-copy range a raster blit must paint over the view.
///
/// ``raster_west``/``raster_east`` are the raster's own edges and
/// ``west``/``east`` the view's: the returns are ``(usable, first_copy,
/// last_copy)`` with the same `math.floor`/`math.ceil` arithmetic as
/// ``hodo_overlay_drawing``.  An unusable result skips the raster, and
/// ``first_copy > last_copy`` means no copy reaches the view.
#[pyfunction]
fn hodo_overlay_raster_copy_range(
    raster_west: f64,
    raster_east: f64,
    west: f64,
    east: f64,
) -> (bool, i64, i64) {
    raster_copy_range_core(raster_west, raster_east, west, east)
}

/// Clamp an overlay opacity to ``[0, 1]``, falling back to ``default`` when
/// the value cannot convert to a float (the Python equivalent catches
/// ``TypeError``/``ValueError`` from ``float`` and uses 1.0).
#[pyfunction]
#[pyo3(signature = (value, default = 1.0))]
fn hodo_overlay_clamp_opacity(value: &Bound<'_, PyAny>, default: f64) -> f64 {
    clamp_opacity_core(coerce_f64(value), default)
}

/// Project geographic shape rings onto the locator map's pixels.
///
/// ``rings`` are the shape's rings of ``(lon, lat)`` vertices; ``bounds`` is
/// `(west, south, east, north)` and ``map_rect`` is `(left, top, width,
/// height)`.  Rings with fewer than three points are dropped, matching the
/// painter, and a degenerate view raises ``ZeroDivisionError`` just like the
/// Python float division it mirrors.
#[pyfunction]
fn hodo_overlay_project_shape_rings(
    rings: Vec<Line>,
    bounds: Bounds,
    map_rect: Rect,
) -> PyResult<Vec<Line>> {
    project_shape_rings_core(&rings, bounds, map_rect)
        .map_err(|_| PyZeroDivisionError::new_err("float division by zero"))
}

/// Project an overlay marker point onto the locator map, clamping an
/// off-view point to a visible edge indicator.
///
/// Returns ``(x, y, inside)`` exactly like ``hodo_locator._marker_position``:
/// the marker position plus whether the point landed inside the rectangle
/// before clamping.
#[pyfunction]
fn hodo_overlay_project_marker_point(
    lat: f64,
    lon: f64,
    bounds: Bounds,
    map_rect: Rect,
) -> PyResult<(f64, f64, bool)> {
    project_marker_point_core(lat, lon, bounds, map_rect)
        .map_err(|_| PyZeroDivisionError::new_err("float division by zero"))
}

/// Name the provider timestamp's meaning instead of guessing validity, as
/// ``hodo_locator.overlay_timing_text`` does for an overlay context.
///
/// ``context`` needs ``actual_time``/``time_basis`` keys like the Python
/// mapping; a non-datetime time, or no time at all, renders as ``""``.
#[pyfunction]
fn hodo_overlay_timing_text(context: &Bound<'_, PyAny>) -> PyResult<String> {
    let py = context.py();
    let actual = context.call_method1("get", ("actual_time",))?;
    let Some(stamp) = format_utc_stamp(py, &actual)? else {
        return Ok(String::new());
    };
    let basis_value = context.call_method1("get", ("time_basis",))?;
    let basis = if basis_value.is_truthy()? {
        basis_value.str()?.to_str()?.to_string()
    } else {
        "valid".to_string()
    };
    Ok(overlay_timing_text_core(&stamp, &basis))
}

/// Cut a legacy coordinate-only label down to its naming prefix.
///
/// The coordinate may be a bearing pair (``45.76N 91.60W``) or a signed
/// comma pair (``35.22, -97.44``); the text before the first match is
/// returned stripped of spaces, commas, and hyphens at both ends, matching
/// the fallback in ``hodo_locator.location_name_from_widget``.
#[pyfunction]
fn hodo_overlay_strip_coordinate_label(text: &str) -> String {
    strip_coordinate_label_core(text)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(hodo_overlay_raster_copy_range, module)?)?;
    module.add_function(wrap_pyfunction!(hodo_overlay_clamp_opacity, module)?)?;
    module.add_function(wrap_pyfunction!(hodo_overlay_project_shape_rings, module)?)?;
    module.add_function(wrap_pyfunction!(hodo_overlay_project_marker_point, module)?)?;
    module.add_function(wrap_pyfunction!(hodo_overlay_timing_text, module)?)?;
    module.add_function(wrap_pyfunction!(
        hodo_overlay_strip_coordinate_label,
        module
    )?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn copy_range_paints_one_copy_for_a_local_view() {
        // The CONUS raster against the Oklahoma view: copy 0 only.
        assert_eq!(
            raster_copy_range_core(-134.5, -60.5, -100.0, -90.0),
            (true, 0, 0),
        );
        // Just touching the east edge still means no copy reaches.
        assert_eq!(
            raster_copy_range_core(-134.5, -60.5, -60.5, -55.0),
            (true, 1, 0),
        );
        assert_eq!(
            raster_copy_range_core(-134.5, -60.5, -60.4, -55.0),
            (true, 1, 0),
        );
        // An unwrapped wide view needs more than one copy.
        assert_eq!(
            raster_copy_range_core(170.0, 190.0, 100.0, 600.0),
            (true, 0, 1),
        );
        assert_eq!(
            raster_copy_range_core(170.0, 190.0, 175.0, 185.0),
            (true, 0, 0),
        );
        assert_eq!(
            raster_copy_range_core(170.0, 190.0, 530.0, 550.0),
            (true, 1, 1),
        );
    }

    #[test]
    fn copy_range_refuses_non_finite_edges() {
        assert_eq!(
            raster_copy_range_core(f64::NAN, -60.5, -100.0, -90.0),
            (false, 0, 0),
        );
        assert_eq!(
            raster_copy_range_core(-134.5, f64::INFINITY, -100.0, -90.0),
            (false, 0, 0),
        );
        assert_eq!(
            raster_copy_range_core(-134.5, -60.5, f64::NAN, -90.0),
            (false, 0, 0),
        );
        assert_eq!(
            raster_copy_range_core(-134.5, -60.5, -100.0, f64::NEG_INFINITY),
            (false, 0, 0),
        );
    }

    #[test]
    fn opacity_clamps_like_the_python_bounds_check() {
        assert_eq!(clamp_opacity_core(Some(0.75), 1.0), 0.75);
        assert_eq!(clamp_opacity_core(Some(1.5), 1.0), 1.0);
        assert_eq!(clamp_opacity_core(Some(-0.2), 1.0), 0.0);
        assert_eq!(clamp_opacity_core(Some(0.0), 1.0), 0.0);
        assert_eq!(clamp_opacity_core(Some(1.0), 1.0), 1.0);
        // The infinities clamp through the comparisons; NaN is muted by
        // max()'s keep-first-on-false-comparison.
        assert_eq!(clamp_opacity_core(Some(f64::INFINITY), 1.0), 1.0);
        assert_eq!(clamp_opacity_core(Some(f64::NEG_INFINITY), 1.0), 0.0);
        assert_eq!(clamp_opacity_core(Some(f64::NAN), 1.0), 0.0);
        // Unconvertible values fall back, like the except in the paint loop.
        assert_eq!(clamp_opacity_core(None, 1.0), 1.0);
        assert_eq!(clamp_opacity_core(None, 0.4), 0.4);
    }

    #[test]
    fn rings_project_through_the_plate_carree_seam() {
        let bounds = (0.0, 30.0, 10.0, 40.0);
        let map_rect = (10.0, 20.0, 240.0, 150.0);
        let rings: Vec<Line> = vec![
            vec![(0.0, 30.0), (10.0, 30.0), (0.0, 40.0)], // triangle
            vec![(1.0, 31.0), (2.0, 32.0)],               // too short
        ];
        let projected = project_shape_rings_core(&rings, bounds, map_rect).unwrap();
        assert_eq!(
            projected,
            vec![vec![(10.0, 170.0), (250.0, 170.0), (10.0, 20.0)]],
        );
        // A single projected pair to pin the orientation: lon runs with x,
        // lat runs against y.
        let map_rect_flag = (0.0, 0.0, 100.0, 100.0);
        let flagged = project_shape_rings_core(
            &[vec![(0.0, 30.0), (10.0, 40.0), (0.0, 40.0)]],
            bounds,
            map_rect_flag,
        )
        .unwrap();
        assert_eq!(
            flagged[0][0],
            (0.0, 100.0),
            "lat 30 maps to the bottom of the square view",
        );
    }

    #[test]
    fn rings_keep_pythons_division_errors() {
        let rect = (10.0, 20.0, 240.0, 150.0);
        let projectable: Vec<Line> = vec![vec![(0.0, 30.0), (10.0, 30.0), (0.0, 40.0)]];
        let degenerate_lon = (10.0, 30.0, 10.0, 40.0);
        let degenerate_lat = (0.0, 30.0, 10.0, 30.0);
        assert!(project_shape_rings_core(&projectable, degenerate_lon, rect).is_err());
        assert!(project_shape_rings_core(&projectable, degenerate_lat, rect).is_err());
        // No ring of three points means no projection, so no division, and
        // Python returns the empty path set without raising.
        let short: Vec<Line> = vec![vec![(0.0, 30.0), (10.0, 40.0)]];
        assert_eq!(
            project_shape_rings_core(&short, degenerate_lon, rect),
            Ok(Vec::new()),
        );
        // Non-finite vertices project to non-finite pixels, as in Python.
        let nan_ring: Vec<Line> = vec![vec![(f64::NAN, 30.0), (10.0, 30.0), (0.0, f64::NAN)]];
        let projected = project_shape_rings_core(&nan_ring, (0.0, 30.0, 10.0, 40.0), rect).unwrap();
        assert_eq!(projected.len(), 1);
        assert!(projected[0][0].0.is_nan());
        assert!(projected[0][2].1.is_nan());
    }

    #[test]
    fn marker_points_project_wrap_clamp_and_report_inside() {
        let bounds = (-100.0, 35.0, -95.0, 40.0);
        let rect = (10.0, 20.0, 240.0, 150.0);
        let (x, y, inside) = project_marker_point_core(37.5, -97.5, bounds, rect).unwrap();
        assert_eq!(x, 10.0 + 240.0 / 2.0);
        assert_eq!(y, 20.0 + 150.0 / 2.0);
        assert!(inside);
        let (_, _, inside) = project_marker_point_core(40.0, -95.0, bounds, rect).unwrap();
        assert!(inside, "QRectF::contains counts the edge");
        let (x, y, inside) = project_marker_point_core(60.0, -160.0, bounds, rect).unwrap();
        assert!(!inside);
        assert_eq!(x, rect.0 + MARKER_EDGE_MARGIN);
        assert_eq!(y, rect.1 + MARKER_EDGE_MARGIN);
        // A point a world away lands on the view's own copy of it.
        let antimeridian = (165.0, 35.0, 175.0, 40.0);
        let (x, _, _) = project_marker_point_core(37.5, -190.0, antimeridian, rect).unwrap();
        let (wrapped, _, _) = project_marker_point_core(37.5, 170.0, antimeridian, rect).unwrap();
        assert_eq!(x, wrapped);
        assert!(
            project_marker_point_core(37.5, -97.5, (-100.0, 35.0, -100.0, 40.0), rect).is_err()
        );
        assert!(project_marker_point_core(37.5, -97.5, (-100.0, 35.0, -95.0, 35.0), rect).is_err());
        // NaN coordinates clamp to the top-left indicator, as Python's
        // comparisons are all false and never move the clamp.
        let (x, y, inside) = project_marker_point_core(f64::NAN, f64::NAN, bounds, rect).unwrap();
        assert!(!inside);
        assert_eq!(x, rect.0 + MARKER_EDGE_MARGIN);
        assert_eq!(y, rect.1 + MARKER_EDGE_MARGIN);
    }

    #[test]
    fn timing_text_names_the_basis_of_the_stamp() {
        let stamp = "2026-09-04 21:00Z";
        assert_eq!(overlay_timing_text_core("", "retrieved"), "");
        assert_eq!(
            overlay_timing_text_core(stamp, "retrieved"),
            format!("retrieved {stamp}")
        );
        assert_eq!(
            overlay_timing_text_core(stamp, "valid"),
            format!("actual valid {stamp}")
        );
        assert_eq!(
            overlay_timing_text_core(stamp, "issued"),
            format!("actual issued {stamp}")
        );
        assert_eq!(
            overlay_timing_text_core(stamp, "valid from"),
            format!("actual valid from {stamp}")
        );
    }

    #[test]
    fn coordinate_labels_cut_the_prefix_before_the_first_match() {
        // The regression pair from hodo_locator: a legacy model label keeps
        // only the model name, never the raw coordinates.
        assert_eq!(strip_coordinate_label_core("HRRR 45.76N 91.60W"), "HRRR");
        // Case-insensitive bearings, slashed or comma'd, plain labels kept.
        assert_eq!(strip_coordinate_label_core("HRRR 45.76n 91.60w"), "HRRR");
        assert_eq!(strip_coordinate_label_core("X 45N/91W"), "X");
        assert_eq!(
            strip_coordinate_label_core("Sample -97.44, 35.22 OK"),
            "Sample"
        );
        assert_eq!(
            strip_coordinate_label_core("Norman, Oklahoma"),
            "Norman, Oklahoma"
        );
        // A bare coordinate reduces to the empty prefix, stripped.
        assert_eq!(strip_coordinate_label_core("45.76, -91.60"), "");
        // End chars are stripped like Python's .strip(" ,-").
        assert_eq!(strip_coordinate_label_core("  Model X - "), "Model X");
        assert_eq!(strip_coordinate_label_core("-, ,"), "");
    }

    #[test]
    fn coordinate_labels_respect_python_boundaries_and_backtracking() {
        // A word character before the number suppresses \b: no match.  When a
        // dot precedes the decimal digits (e.g. ``xA45.76N``), Python's \b
        // matches between `.` and `76`, cutting at ``xA45.``.
        assert_eq!(strip_coordinate_label_core("xA45N 91.60W"), "xA45N 91.60W");
        assert_eq!(strip_coordinate_label_core("xA45.76N 91.60W"), "xA45.");
        // Underscore is a word character, matching Python's \w.
        assert_eq!(strip_coordinate_label_core("x_45N 91.60W"), "x_45N 91.60W");
        assert_eq!(strip_coordinate_label_core("x_45.76N 91.60W"), "x_45.");
        // \d{1,2} cannot cover three digits: 129N never matches.
        assert_eq!(
            strip_coordinate_label_core("HRRR 129N 91.60W"),
            "HRRR 129N 91.60W"
        );
        // A word character after the E/W quenches the trailing \b.
        assert_eq!(
            strip_coordinate_label_core("45.76N 91.6West"),
            "45.76N 91.6West"
        );
        // More separator combinations behave in the same way.
        assert_eq!(strip_coordinate_label_core("A 45.76N , 91.60W"), "A");
        assert_eq!(strip_coordinate_label_core("B 45.76 N 91.60 W"), "B");
        assert_eq!(strip_coordinate_label_core("C 45.76N/91.60W"), "C");
    }

    #[test]
    fn coordinate_label_search_reports_the_leftmost_python_range() {
        assert_eq!(find_coordinate_label("HRRR 45.76N 91.60W"), Some((5, 18)));
        assert_eq!(find_coordinate_label("45.76N 91.60W"), Some((0, 13)));
        assert_eq!(find_coordinate_label("OK 35.22, -97.44"), Some((3, 16)));
        assert_eq!(find_coordinate_label("nothing here"), None);
        // The two forms over plain pairs; the separator class also accepts a
        // comma and a space together.
        assert_eq!(find_coordinate_label("35.22N 97.44E"), Some((0, 13)));
        assert_eq!(find_coordinate_label("35.22, -97.44"), Some((0, 13)));
        assert_eq!(find_coordinate_label("35.22N, 97.44E"), Some((0, 14)));
        // A signed longitude cannot be the bearing form's lon half, and the
        // comma form needs its comma straight after the first number, so
        // the whole label survives -- exactly what Python does.
        assert_eq!(find_coordinate_label("35.22N, -97.44"), None);
    }
}

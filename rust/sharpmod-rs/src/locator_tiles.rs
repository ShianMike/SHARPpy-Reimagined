//! Tile lookup, point reading, raster crops, and marker projection for the
//! hodograph sounding locator (module M27).
//!
//! Rust mirror of the Python-side paint contracts in:
//!
//! * ``sharpmod.viz.hodo_locator`` -- ``point_from_widget``,
//!   ``requested_point_from_widget``, ``box_from_widget``, ``zoom_bounds``,
//!   ``raster_source_rect``, ``raster_draw_rects``, the bundled county tile
//!   decoder, ``county_lines_for_bounds``, the worldwide line filter, and
//!   ``_marker_position`` / ``_inset_rect``/``_map_point``;
//! * ``scripts.build_conus_county_tiles`` -- the on-disk tile encoding that
//!   the decoder reads.
//!
//! Orders and NaN behaviour are pinned to the Python source: every
//! ``bounds`` is ``(west, south, east, north)``, raster extents are spelled
//! ``(west, east, south, north)`` exactly as ``raster.bounds`` stores them,
//! and paint rectangles are ``(left, top, width, height)`` following the
//! ``QRectF`` seam.  Two-argument ``max``/``min`` keep CPython's
//! NaN-first-operand semantics through the ``py_max``/``py_min`` helpers.
//!
//! Known divergences on payloads no production caller can produce are noted
//! next to the code: JSON tokens Python accepts and ``serde_json`` rejects
//! (``NaN``/``Infinity``/huge integers), and ``int()`` coercions whose exact
//! results would need a bigint.

use pyo3::exceptions::{PyOverflowError, PyValueError, PyZeroDivisionError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyString};

/// Locator view bounds: `(west, south, east, north)`.
pub(crate) type Bounds = (f64, f64, f64, f64);
/// Paint rectangle: `(left, top, width, height)`.
pub(crate) type Rect = (f64, f64, f64, f64);
/// One decoded polyline of geographic `(lon, lat)` pairs.
pub(crate) type Line = Vec<(f64, f64)>;

/// Inset half-height in degrees of latitude (``LOCATOR_HALF_LAT_DEGREES``).
const LOCAL_HALF_LAT_DEGREES: f64 = 0.98;
/// Longitude widening before the cosine-of-latitude correction.
const LON_ASPECT: f64 = 1.35;
/// Room left around an averaged area (``LOCATOR_BOX_MARGIN``).
const BOX_MARGIN: f64 = 1.18;
/// Ceiling on the box-driven zoom-out in degrees of latitude.
const MAX_HALF_LAT_DEGREES: f64 = 25.0;
/// ``cos(latitude)`` floor protecting the longitude aspect at the poles.
const MIN_LATITUDE_COSINE: f64 = 0.35;
/// National-scale county queries are refused before decoding any tile.
const MAX_QUERY_TILES: i64 = 64;
/// Tile linework lives on a ``10 ** 5`` integer degree grid.
const TILE_COORDINATE_PRECISION: i32 = 5;
/// Decoder guards shared with ``_county_tile_lines_cached``.
const MAX_TILE_LINES: usize = 20_000;
const MAX_LINE_VALUES: usize = 200_000;
/// Points may sit this many degrees outside their one-degree tile.
const TILE_POINT_MARGIN_WEST: f64 = 1.0e-4;
const TILE_POINT_MARGIN_EAST: f64 = 1.0001;
/// Fixed marker inset from the locator's edge, in pixels.
const MARKER_EDGE_MARGIN: f64 = 3.0;
/// A request closer than this to the sampled point is one marker.
const REQUESTED_POINT_TOLERANCE_DEGREES: f64 = 1.0e-4;
/// Floor of any finite float beyond this magnitude leaves exact ``i64``
/// territory; such spans are refused rather than saturated.
const I64_SATURATION_LIMIT: f64 = 9.223_372_036_854_776e18;

/// ``int()`` on a float: NaN refuses, the infinities overflow, the rest
/// truncates toward zero.
#[derive(Debug)]
pub(crate) enum IntegerCoercionError {
    NotAnInteger,
    Infinite,
}

/// Mirrors CPython's two-argument ``max``/``min``: the second operand wins only
/// when the comparison is true, so a NaN second operand is ignored and a NaN
/// first operand survives.
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

/// Why a projection failed: the Python seam raises ``ZeroDivisionError`` when
/// the geographic denominators are exactly zero.
#[derive(Debug, PartialEq)]
pub(crate) struct ZeroDivision;

/// Wrap ``lon`` onto the copy of the world closest to ``center``.
fn longitude_near_core(lon: f64, center: f64) -> f64 {
    center + ((lon - center + 180.0).rem_euclid(360.0)) - 180.0
}

/// A useable sounding point: finite, on the planet.
pub(crate) fn point_valid_core(lat: f64, lon: f64) -> bool {
    lat.is_finite()
        && lon.is_finite()
        && (-90.0..=90.0).contains(&lat)
        && (-180.0..=180.0).contains(&lon)
}

/// Port of ``requested_point_from_widget``'s decision core: report whether a
/// requested point survives its own validation and is distinct at map
/// precision from the selected point.  A missing or invalid selected point
/// never suppresses the request.
pub(crate) fn requested_distinct_core(
    lat: Option<f64>,
    lon: Option<f64>,
    req_lat: Option<f64>,
    req_lon: Option<f64>,
) -> bool {
    let (Some(req_lat), Some(req_lon)) = (req_lat, req_lon) else {
        return false;
    };
    if !point_valid_core(req_lat, req_lon) {
        return false;
    }
    match (lat, lon) {
        (Some(selected_lat), Some(selected_lon))
            if point_valid_core(selected_lat, selected_lon) =>
        {
            // Python measures the wrapped *difference*:
            // ((lon - selected_lon + 180.0) % 360.0) - 180.0.
            let delta_lon = longitude_near_core(req_lon, selected_lon) - selected_lon;
            !((req_lat - selected_lat).abs() < REQUESTED_POINT_TOLERANCE_DEGREES
                && delta_lon.abs() < REQUESTED_POINT_TOLERANCE_DEGREES)
        }
        _ => true,
    }
}

/// Port of ``box_from_widget``'s numeric tail.  The box is stored in the
/// region's own ``(south, west, north, east)` order and returned as
/// `(west, south, east, north)`; an east edge at or west of the west edge is
/// an antimeridian crossing unwrapped eastward.
pub(crate) fn normalise_box_core(south: f64, west: f64, north: f64, east: f64) -> Option<Bounds> {
    for value in [south, west, north, east] {
        if !value.is_finite() {
            return None;
        }
    }
    if !(-90.0..=90.0).contains(&south) || !(-90.0..=90.0).contains(&north) {
        return None;
    }
    if north <= south {
        return None;
    }
    let east = if east <= west { east + 360.0 } else { east };
    if east - west > 360.0 {
        return None;
    }
    Some((west, south, east, north))
}

/// Port of ``zoom_bounds``: a local, near-square geographic extent centred on
/// the sounding, widened with fixed aspect until an averaged box fits, then
/// capped at the box-driven zoom ceiling.
pub(crate) fn zoom_bounds_core(lat: f64, lon: f64, box_area: Option<Bounds>) -> Bounds {
    let mut half_lat = LOCAL_HALF_LAT_DEGREES;
    let cos_lat = py_max(MIN_LATITUDE_COSINE, lat.to_radians().cos());
    if let Some((west, south, east, north)) = box_area {
        let reach_lat = py_max((north - lat).abs(), (lat - south).abs());
        let reach_lon = py_max((east - lon).abs(), (lon - west).abs());
        half_lat = py_max(
            py_max(half_lat, reach_lat * BOX_MARGIN),
            reach_lon * BOX_MARGIN * cos_lat / LON_ASPECT,
        );
        half_lat = py_min(half_lat, MAX_HALF_LAT_DEGREES);
    }
    let half_lon = half_lat * LON_ASPECT / cos_lat;
    (
        lon - half_lon,
        lat - half_lat,
        lon + half_lon,
        lat + half_lat,
    )
}

/// Port of ``raster_source_rect``: the unclipped image rectangle covering the
/// view bounds.  ``None`` means the field does not reach this sounding.  The
/// raster extent is passed in ``raster.bounds`` order -- ``(west, east,
/// south, north)`` -- and there is deliberately no finiteness check, matching
/// the Python comparison-only control flow.
pub(crate) fn raster_source_rect_core(
    raster_west: f64,
    raster_east: f64,
    raster_south: f64,
    raster_north: f64,
    view: Bounds,
    width: f64,
    height: f64,
) -> Option<Rect> {
    let (west, south, east, north) = view;
    let span_lon = raster_east - raster_west;
    let span_lat = raster_north - raster_south;
    if span_lon <= 0.0 || span_lat <= 0.0 {
        return None;
    }
    if east <= raster_west || west >= raster_east || north <= raster_south || south >= raster_north
    {
        return None;
    }
    let left = (west - raster_west) / span_lon * width;
    let right = (east - raster_west) / span_lon * width;
    // Row 0 is the north edge, so latitude runs the other way.
    let top = (raster_north - north) / span_lat * height;
    let bottom = (raster_north - south) / span_lat * height;
    Some((
        left,
        top,
        py_max(1.0, right - left),
        py_max(1.0, bottom - top),
    ))
}

/// Port of ``raster_draw_rects``: source and target rectangles clipped
/// together so the surviving pixels are drawn on the ground they describe.
/// Off-domain parts of the inset are left unpainted.
#[allow(clippy::too_many_arguments)] // one painter seam carries the whole crop contract
pub(crate) fn raster_draw_rects_core(
    raster_west: f64,
    raster_east: f64,
    raster_south: f64,
    raster_north: f64,
    map_rect: Rect,
    view: Bounds,
    width: f64,
    height: f64,
) -> Option<(Rect, Rect)> {
    let (x, y, w, h) = raster_source_rect_core(
        raster_west,
        raster_east,
        raster_south,
        raster_north,
        view,
        width,
        height,
    )?;
    if w <= 0.0 || h <= 0.0 {
        return None;
    }
    let (target_x, target_y, target_w, target_h) = map_rect;
    let left = py_max(0.0, x);
    let top = py_max(0.0, y);
    let right = py_min(width, x + w);
    let bottom = py_min(height, y + h);
    if right <= left || bottom <= top {
        return None;
    }
    // The same fraction removed from the source comes off the destination.
    let target_left = target_x + (left - x) / w * target_w;
    let target_top = target_y + (top - y) / h * target_h;
    let target_width = (right - left) / w * target_w;
    let target_height = (bottom - top) / h * target_h;
    if target_width <= 0.0 || target_height <= 0.0 {
        return None;
    }
    Some((
        (left, top, right - left, bottom - top),
        (target_left, target_top, target_width, target_height),
    ))
}

/// Payload-level failures of the tile decoder, mirroring the exceptions the
/// Python caller sees: non-ASCII payloads and structural JSON errors raise
/// ``ValueError`` subclasses.
#[derive(Debug)]
pub(crate) enum TileDecodeError {
    NonAscii,
    MalformedJson,
}

/// Port of the bundled county tile decoder (``_county_tile_lines_cached``):
/// an ASCII JSON payload of delta-encoded ``1e-5``-degree integer paths.
///
/// Per-line guards match Python exactly: arrays only, at least four values,
/// even length, bounded length, and integers throughout -- with ``bool``
/// accepted because ``isinstance(True, int)`` is true in Python.  Lines keep
/// only points inside their one-degree tile's tolerance margins.  Payloads
/// Python accepts through its arbitrary-precision ``int`` (integers beyond
/// ``i64``) or its non-standard JSON extensions (``NaN``/``Infinity``) are
/// rejected instead; the builder emits neither.
pub(crate) fn decode_county_tile_core(
    payload: &[u8],
    tile_lon: i64,
    tile_lat: i64,
) -> Result<Vec<Line>, TileDecodeError> {
    if !payload.is_ascii() {
        return Err(TileDecodeError::NonAscii);
    }
    let raw: serde_json::Value =
        serde_json::from_slice(payload).map_err(|_| TileDecodeError::MalformedJson)?;
    let serde_json::Value::Array(raw_lines) = raw else {
        return Ok(Vec::new());
    };
    if raw_lines.len() > MAX_TILE_LINES {
        return Ok(Vec::new());
    }
    // float(10 ** 5): powi multiplies out exactly, like Python's literal.
    let scale = 10.0f64.powi(TILE_COORDINATE_PRECISION);
    let lon_min = tile_lon as f64 - TILE_POINT_MARGIN_WEST;
    let lon_max = tile_lon as f64 + TILE_POINT_MARGIN_EAST;
    let lat_min = tile_lat as f64 - TILE_POINT_MARGIN_WEST;
    let lat_max = tile_lat as f64 + TILE_POINT_MARGIN_EAST;
    let mut lines: Vec<Line> = Vec::new();
    for raw_line in &raw_lines {
        let serde_json::Value::Array(values) = raw_line else {
            continue;
        };
        if values.len() < 4 || values.len() % 2 != 0 || values.len() > MAX_LINE_VALUES {
            continue;
        }
        let Some(deltas) = values
            .iter()
            .map(|value| match value {
                // bool is an int subclass in Python, so True decodes as 1.
                serde_json::Value::Bool(flag) => Some(i64::from(*flag)),
                serde_json::Value::Number(number) => number.as_i64(),
                _ => None,
            })
            .collect::<Option<Vec<i64>>>()
        else {
            continue;
        };
        let mut lon = deltas[0] as i128;
        let mut lat = deltas[1] as i128;
        let mut points: Line = Vec::with_capacity(values.len() / 2);
        points.push((lon as f64 / scale, lat as f64 / scale));
        let mut index = 2;
        while index < deltas.len() {
            lon += deltas[index] as i128;
            lat += deltas[index + 1] as i128;
            index += 2;
            points.push((lon as f64 / scale, lat as f64 / scale));
        }
        if points.iter().all(|&(point_lon, point_lat)| {
            lon_min <= point_lon
                && point_lon <= lon_max
                && lat_min <= point_lat
                && point_lat <= lat_max
        }) {
            lines.push(points);
        }
    }
    Ok(lines)
}

/// Port of the tile enumeration in ``county_lines_for_bounds``: the one-degree
/// cells covering the view, lon-major, refused when the view is degenerate or
/// covers more tiles than a local query may decode.
pub(crate) fn county_tile_coords_core(
    west: f64,
    south: f64,
    east: f64,
    north: f64,
) -> Vec<(i64, i64)> {
    if !west.is_finite() || !east.is_finite() || !south.is_finite() || !north.is_finite() {
        return Vec::new();
    }
    if west >= east || south >= north {
        return Vec::new();
    }
    // At and beyond 2^63 every representable span is thousands of tiles wide,
    // so Python refuses it through the tile-count cap; refuse it up front
    // rather than saturating the floor casts.
    if west.abs() >= I64_SATURATION_LIMIT
        || east.abs() >= I64_SATURATION_LIMIT
        || south.abs() >= I64_SATURATION_LIMIT
        || north.abs() >= I64_SATURATION_LIMIT
    {
        return Vec::new();
    }
    let tile_west = west.floor() as i64;
    let tile_east = east.floor() as i64;
    let tile_south = south.floor() as i64;
    let tile_north = north.floor() as i64;
    let span_lon = i128::from(tile_east) - i128::from(tile_west) + 1;
    let span_lat = i128::from(tile_north) - i128::from(tile_south) + 1;
    let Some(tile_count) = span_lon.checked_mul(span_lat) else {
        return Vec::new();
    };
    // `tile_count <= 0` stays as a defensive mirror; monotone floors keep
    // both spans at least one once west < east and south < north.
    if tile_count <= 0 || tile_count > i128::from(MAX_QUERY_TILES) {
        return Vec::new();
    }
    let mut coords = Vec::new();
    for tile_lon in tile_west..=tile_east {
        for tile_lat in tile_south..=tile_north {
            coords.push((tile_lon, tile_lat));
        }
    }
    coords
}

/// Port of the per-line viewport filter behind ``global_lines_for_bounds``:
/// wrap each longitude onto the copy of the world nearest the view centre,
/// then keep the lines whose bounding box overlaps the view.  Python raises
/// ``ValueError`` from ``min()`` on an empty polyline; validated global
/// layers never supply one, so the equivalent input is skipped here.
pub(crate) fn filter_global_lines_core(
    lines: &[Line],
    west: f64,
    south: f64,
    east: f64,
    north: f64,
) -> Vec<Line> {
    let center = (west + east) / 2.0;
    let mut matches: Vec<Line> = Vec::new();
    for line in lines {
        if line.is_empty() {
            continue;
        }
        let adjusted: Line = line
            .iter()
            .map(|&(lon, lat)| (longitude_near_core(lon, center), lat))
            .collect();
        let (mut line_west, mut line_south) = adjusted[0];
        let (mut line_east, mut line_north) = adjusted[0];
        for &(point_lon, point_lat) in &adjusted[1..] {
            line_west = py_min(line_west, point_lon);
            line_east = py_max(line_east, point_lon);
            line_south = py_min(line_south, point_lat);
            line_north = py_max(line_north, point_lat);
        }
        if line_east >= west && line_west <= east && line_north >= south && line_south <= north {
            matches.push(adjusted);
        }
    }
    matches
}

fn python_int(value: f64) -> Result<i64, IntegerCoercionError> {
    if value.is_nan() {
        return Err(IntegerCoercionError::NotAnInteger);
    }
    if value.is_infinite() {
        return Err(IntegerCoercionError::Infinite);
    }
    // Python's int() keeps arbitrary precision past i64; beyond here the cast
    // saturates, and every such frame sits far outside any painted bitmap.
    Ok(value.trunc() as i64)
}

/// Port of ``_inset_rect``'s numeric tail: the locator's pixel rectangle,
/// `None` when the hodograph has no room for it.  ``frame_left`` and
/// ``frame_top`` are the raw widget offsets -- the ``int()`` coercion the
/// Python applies to ``tlx``/``tly`` happens here.
pub(crate) fn inset_rect_core(
    bitmap_width: f64,
    bitmap_height: f64,
    frame_left_raw: f64,
    frame_top_raw: f64,
) -> Result<Option<Rect>, IntegerCoercionError> {
    let frame_left = python_int(frame_left_raw)? + 1;
    let frame_top = python_int(frame_top_raw)? + 1;
    let available_width = py_max(0.0, bitmap_width - frame_left as f64 - 8.0);
    let available_height = py_max(0.0, bitmap_height - frame_top as f64 - 8.0);
    let scaled_width = python_int(bitmap_width * 0.29)? as f64;
    let width = py_min(py_min(py_max(150.0, scaled_width), 250.0), available_width);
    let scaled_height = python_int(width * 0.64)? as f64;
    let height = py_min(py_max(96.0, scaled_height), available_height);
    if width < 110.0 || height < 72.0 {
        return Ok(None);
    }
    Ok(Some((frame_left as f64, frame_top as f64, width, height)))
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

/// Port of ``_marker_position``: map a point, clamping an off-view point to a
/// visible edge indicator, and report whether it landed inside before
/// clamping.
pub(crate) fn marker_position_core(
    lat: f64,
    lon: f64,
    bounds: Bounds,
    rect: Rect,
) -> Result<(f64, f64, bool), ZeroDivision> {
    let (west, south, east, north) = bounds;
    if east - west == 0.0 || north - south == 0.0 {
        return Err(ZeroDivision);
    }
    let center = (west + east) / 2.0;
    let lon = longitude_near_core(lon, center);
    let (x, y) = map_point_core(rect, bounds, lat, lon);
    let inside = rect_contains(rect, x, y);
    let (left, top, width, height) = rect;
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

fn builtin_float(py: Python<'_>) -> Option<Bound<'_, PyAny>> {
    py.import("builtins").ok()?.getattr("float").ok()
}

/// ``float()`` each element of an iterable and require exactly four results,
/// mirroring the generator unpacking in ``box_from_widget`` and
/// ``county_lines_for_bounds``: anything that cannot convert means "not
/// usable", not an exception.
fn coerce_four_floats(value: &Bound<'_, PyAny>) -> Option<[f64; 4]> {
    let float_fn = builtin_float(value.py())?;
    let iter = value.try_iter().ok()?;
    let mut items: Vec<f64> = Vec::with_capacity(4);
    for entry in iter {
        if items.len() == 4 {
            // A fifth element means the four-name unpack raises ValueError.
            return None;
        }
        let entry = entry.ok()?;
        let converted = float_fn.call1((entry,)).ok()?;
        items.push(converted.extract::<f64>().ok()?);
    }
    if items.len() != 4 {
        return None;
    }
    Some([items[0], items[1], items[2], items[3]])
}

/// Report whether the active sounding point is useable.  ``None`` marks a
/// missing latitude or longitude and reads as invalid, matching the
/// ``_as_float`` seam in ``point_from_widget``.
#[pyfunction]
fn hodo_locator_point_valid(lat: Option<f64>, lon: Option<f64>) -> bool {
    match (lat, lon) {
        (Some(lat), Some(lon)) => point_valid_core(lat, lon),
        _ => false,
    }
}

/// Report whether a requested point deserves its own marker: it must be a
/// useable point *and* distinct at map precision from the selected one.
#[pyfunction]
fn hodo_locator_requested_distinct(
    lat: Option<f64>,
    lon: Option<f64>,
    req_lat: Option<f64>,
    req_lon: Option<f64>,
) -> bool {
    requested_distinct_core(lat, lon, req_lat, req_lon)
}

/// Normalise an averaged sounding's stored box -- in the region's own
/// ``(lat0, lon0, lat1, lon1)`` order -- into `(west, south, east, north)`:
/// ``None`` when the box is unusable or spans more than one world.
#[pyfunction]
fn hodo_locator_normalise_box(box_value: &Bound<'_, PyAny>) -> Option<Bounds> {
    if box_value.is_none()
        || box_value.is_instance_of::<PyString>()
        || box_value.is_instance_of::<PyBytes>()
    {
        return None;
    }
    let [south, west, north, east] = coerce_four_floats(box_value)?;
    normalise_box_core(south, west, north, east)
}

/// Return a local, near-square geographic extent centred on the sounding.
///
/// With ``box_area`` given as `(west, south, east, north)` the extent widens
/// until the whole rectangle fits with margin, capped at 25 degrees of
/// half-height.  Returns `(west, south, east, north)`.
#[pyfunction]
#[pyo3(signature = (lat, lon, box_area = None))]
fn hodo_locator_zoom_bounds(lat: f64, lon: f64, box_area: Option<Bounds>) -> Bounds {
    zoom_bounds_core(lat, lon, box_area)
}

/// Return the unclipped `(x, y, w, h)` of the raster covering ``bounds``, or
/// ``None`` when the field does not reach the sounding.
///
/// The raster extent is passed in ``raster.bounds`` order as four floats --
/// ``(west, east, south, north)`` -- and the view bounds as one
/// `(west, south, east, north)` tuple.
#[pyfunction]
fn hodo_locator_raster_source_rect(
    raster_west: f64,
    raster_east: f64,
    raster_south: f64,
    raster_north: f64,
    bounds: Bounds,
    raster_width: f64,
    raster_height: f64,
) -> Option<Rect> {
    raster_source_rect_core(
        raster_west,
        raster_east,
        raster_south,
        raster_north,
        bounds,
        raster_width,
        raster_height,
    )
}

/// Return the clipped `(source, target)` crop of the raster at
/// ``(map_rect, bounds)``, or an empty sequence when Python would return
/// ``None``.
///
/// Both rectangles are clipped in step so the surviving pixels describe the
/// ground they are painted on; the uncovered part of the inset stays
/// unpainted.  ``map_rect`` is `(left, top, width, height)``.
#[pyfunction]
#[allow(clippy::too_many_arguments)] // one painter seam carries the whole crop contract
fn hodo_locator_raster_draw_rects(
    raster_west: f64,
    raster_east: f64,
    raster_south: f64,
    raster_north: f64,
    map_rect: Rect,
    bounds: Bounds,
    raster_width: f64,
    raster_height: f64,
) -> Vec<(Rect, Rect)> {
    match raster_draw_rects_core(
        raster_west,
        raster_east,
        raster_south,
        raster_north,
        map_rect,
        bounds,
        raster_width,
        raster_height,
    ) {
        Some((source, target)) => vec![(source, target)],
        None => Vec::new(),
    }
}

/// Decode one bundled county-outline tile payload into its polylines of
/// `(lon, lat)` degree pairs.
///
/// The payload is the ASCII JSON of integer deltas on the ``1e-5``-degree
/// grid emitted by ``scripts.build_conus_county_tiles``.  Structurally
/// unusable tiles decode to an empty result; non-ASCII or malformed payloads
/// raise ``ValueError`` like the Python decoder's codec and JSON errors.
#[pyfunction]
fn hodo_locator_decode_county_tile(
    payload: &[u8],
    tile_lon: i64,
    tile_lat: i64,
) -> PyResult<Vec<Line>> {
    decode_county_tile_core(payload, tile_lon, tile_lat).map_err(|error| {
        PyValueError::new_err(match error {
            TileDecodeError::NonAscii => "county tile payload is not ASCII",
            TileDecodeError::MalformedJson => "county tile payload is not valid JSON",
        })
    })
}

/// Return the one-degree county tile coordinates covering ``bounds``, in
/// lon-major order, or an empty sequence when the bounds are degenerate or
/// the query exceeds the local tile cap.
#[pyfunction]
fn hodo_locator_county_tile_coords(bounds: &Bound<'_, PyAny>) -> Vec<(i64, i64)> {
    match coerce_four_floats(bounds) {
        Some([west, south, east, north]) => county_tile_coords_core(west, south, east, north),
        None => Vec::new(),
    }
}

/// Filter worldwide boundary polylines to those intersecting the view.
///
/// Each longitude is wrapped onto the copy of the world nearest the view
/// centre and kept when the wrapped line's bounding box overlaps
/// `(west, south, east, north)`; the returned polylines carry the wrapped
/// coordinates.
#[pyfunction]
fn hodo_locator_filter_global_lines(
    polylines: Vec<Line>,
    west: f64,
    south: f64,
    east: f64,
    north: f64,
) -> Vec<Line> {
    filter_global_lines_core(&polylines, west, south, east, north)
}

/// Return the locator inset's pixel rectangle `(left, top, width, height)`,
/// or ``None`` when the hodograph has no room for it.
///
/// ``frame_left`` and ``frame_top`` are the raw widget offsets (``tlx`` /
/// ``tly``); their ``int()`` coercion, including its ``ValueError`` on NaN
/// and ``OverflowError`` on the infinities, is mirrored here.  The explicit
/// ``_sharpmod_locator_rect`` branch of ``_inset_rect`` stays with the
/// caller, which can attempt the ``QRectF`` conversion itself.
#[pyfunction]
fn hodo_locator_inset_rect(
    bitmap_width: f64,
    bitmap_height: f64,
    frame_left: f64,
    frame_top: f64,
) -> PyResult<Option<Rect>> {
    inset_rect_core(bitmap_width, bitmap_height, frame_left, frame_top).map_err(|error| match error
    {
        IntegerCoercionError::NotAnInteger => {
            PyValueError::new_err("cannot convert float NaN to integer")
        }
        IntegerCoercionError::Infinite => {
            PyOverflowError::new_err("cannot convert float infinity to integer")
        }
    })
}

/// Project a sounding point onto the locator map, clamping an off-view point
/// to a visible edge indicator.
///
/// Returns ``(x, y, inside)``: the marker position plus whether the point
/// landed inside the rectangle before clamping, exactly like
/// ``hodo_locator._marker_position``.  ``bounds`` is
/// `(west, south, east, north)`; ``map_rect`` is `(left, top, width,
/// height)`.
#[pyfunction]
fn hodo_locator_marker_position(
    lat: f64,
    lon: f64,
    bounds: Bounds,
    map_rect: Rect,
) -> PyResult<(f64, f64, bool)> {
    marker_position_core(lat, lon, bounds, map_rect)
        .map_err(|_| PyZeroDivisionError::new_err("float division by zero"))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(hodo_locator_point_valid, module)?)?;
    module.add_function(wrap_pyfunction!(hodo_locator_requested_distinct, module)?)?;
    module.add_function(wrap_pyfunction!(hodo_locator_normalise_box, module)?)?;
    module.add_function(wrap_pyfunction!(hodo_locator_zoom_bounds, module)?)?;
    module.add_function(wrap_pyfunction!(hodo_locator_raster_source_rect, module)?)?;
    module.add_function(wrap_pyfunction!(hodo_locator_raster_draw_rects, module)?)?;
    module.add_function(wrap_pyfunction!(hodo_locator_decode_county_tile, module)?)?;
    module.add_function(wrap_pyfunction!(hodo_locator_county_tile_coords, module)?)?;
    module.add_function(wrap_pyfunction!(hodo_locator_filter_global_lines, module)?)?;
    module.add_function(wrap_pyfunction!(hodo_locator_inset_rect, module)?)?;
    module.add_function(wrap_pyfunction!(hodo_locator_marker_position, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const FIELD_BOUNDS: (f64, f64, f64, f64) = (-134.5, -60.5, 20.5, 53.0);
    const LAT: f64 = 40.39;
    const LON: f64 = -80.16;

    #[test]
    fn point_valid_accepts_only_planetary_coordinates() {
        assert!(point_valid_core(39.0319, -88.6713));
        assert!(point_valid_core(90.0, 180.0));
        assert!(point_valid_core(-90.0, -180.0));
        assert!(!point_valid_core(90.0001, 0.0));
        assert!(!point_valid_core(0.0, -180.0001));
        assert!(!point_valid_core(f64::NAN, 0.0));
        assert!(!point_valid_core(0.0, f64::NAN));
        assert!(!point_valid_core(f64::INFINITY, 0.0));
    }

    #[test]
    fn requested_points_separate_the_request_from_the_sample() {
        // The regression pair from test_viz_hodo_locator.py: a request
        // distinct from the grid point keeps its own marker.
        assert!(requested_distinct_core(
            Some(35.25),
            Some(-97.47),
            Some(35.22),
            Some(-97.44),
        ));
        // An effectively identical request does not create a second marker.
        assert!(!requested_distinct_core(
            Some(35.25),
            Some(-97.47),
            Some(35.25001),
            Some(-97.47001),
        ));
        // The tolerance is strict: exactly 1e-4 degrees apart is distinct.
        assert!(requested_distinct_core(
            Some(35.25),
            Some(-97.47),
            Some(35.2501),
            Some(-97.47),
        ));
        // Missing or invalid requests never draw a second marker.
        assert!(!requested_distinct_core(
            Some(35.25),
            Some(-97.47),
            None,
            Some(-97.44),
        ));
        assert!(!requested_distinct_core(
            Some(35.25),
            Some(-97.47),
            Some(91.0),
            Some(-97.44),
        ));
        // An invalid selected point cannot suppress a valid request.
        assert!(requested_distinct_core(
            Some(f64::NAN),
            Some(-97.47),
            Some(35.22),
            Some(-97.44),
        ));
        assert!(requested_distinct_core(
            None,
            None,
            Some(35.22),
            Some(-97.44)
        ));
    }

    #[test]
    fn closeness_wraps_across_the_antimeridian() {
        // lon 179.99997 is within 1e-4 of lon -179.99997 once wrapped.
        assert!(!requested_distinct_core(
            Some(0.0),
            Some(-179.99997),
            Some(0.0),
            Some(179.99997),
        ));
        // Two degrees away through the same crossing stays distinct.
        assert!(requested_distinct_core(
            Some(0.0),
            Some(-179.99997),
            Some(0.0),
            Some(178.0),
        ));
    }

    #[test]
    fn normalise_box_converts_region_order_and_unwraps_the_antimeridian() {
        // Stored (lat0, lon0, lat1, lon1) -> returned (west, south, east,
        // north), as in test_viz_locator_box.py.
        assert_eq!(
            normalise_box_core(36.0, -96.5, 37.4, -94.8),
            Some((-96.5, 36.0, -94.8, 37.4)),
        );
        // A wrapped east edge becomes the real 20-degree span.
        let bounds = normalise_box_core(50.0, 170.0, 56.0, -170.0).unwrap();
        assert_eq!(bounds, (170.0, 50.0, 190.0, 56.0));
        assert!((bounds.2 - bounds.0 - 20.0).abs() < 1.0e-9);
        // Degenerate edges unwrap to exactly one world.
        assert_eq!(
            normalise_box_core(0.0, 5.0, 10.0, 5.0),
            Some((5.0, 0.0, 365.0, 10.0)),
        );
    }

    #[test]
    fn normalise_box_refuses_unusable_bounds() {
        for (south, west, north, east) in [
            (36.0, -96.5, 37.4, f64::NAN),
            (f64::NAN, -96.5, 37.4, -94.8),
            (f64::INFINITY, -96.5, 37.4, -94.8),
            (37.4, -96.5, 36.0, -94.8),   // north below south
            (200.0, -96.5, 250.0, -94.8), // off the planet
            (-91.0, -96.5, 37.4, -94.8),
            (36.0, -96.5, 91.0, -94.8),
        ] {
            assert_eq!(normalise_box_core(south, west, north, east), None);
        }
        // More than one world wide is refused; an unwrapped east edge always
        // lands inside one world of the west edge, so only the plain
        // already-east case can overflow.
        assert_eq!(normalise_box_core(1.0, -10.0, 2.0, 350.6), None);
        assert_eq!(normalise_box_core(1.0, 10.0, 2.0, 370.1), None);
    }

    #[test]
    fn zoom_bounds_stay_local_and_near_square_on_the_ground() {
        for lat in [0.0, 25.0, 41.79, 58.0, 72.0, -45.0] {
            let (west, south, east, north) = zoom_bounds_core(lat, 0.0, None);
            assert!(west < 0.0 && east > 0.0, "lat {lat}");
            assert!(south < lat && north > lat, "lat {lat}");
            assert!(east - west < 8.0, "lat {lat}");
            assert!(north - south < 2.0, "lat {lat}");
            let tall_km = (north - south) * 111.32;
            let cos_lat = py_max(MIN_LATITUDE_COSINE, lat.to_radians().cos());
            let wide_km = (east - west) * 111.32 * cos_lat;
            assert!(200.0 < tall_km && tall_km < 240.0, "lat {lat}");
            assert!(tall_km < wide_km && wide_km < 340.0, "lat {lat}");
            assert!((wide_km / tall_km - LON_ASPECT).abs() < 1.0e-6);
        }
    }

    #[test]
    fn zoom_bounds_widen_until_the_box_fits_but_not_past_the_cap() {
        // A tiny box inside the plain extent changes nothing.
        let plain = zoom_bounds_core(35.22, -97.44, None);
        let tiny = zoom_bounds_core(35.22, -97.44, Some((-97.5, 35.0, -97.4, 35.4)));
        assert_eq!(tiny, plain);
        // A wide box widens the extent with margin until it fits.
        let box_area = (-99.5, 32.0, -95.0, 38.0);
        let (west, south, east, north) = zoom_bounds_core(35.22, -97.44, Some(box_area));
        assert!(west <= box_area.0 && east >= box_area.2);
        assert!(south <= box_area.1 && north >= box_area.3);
        assert!(north - south > 1.96, "the extent actually widened");
        // A pathological box saturates at the 25-degree zoom ceiling.
        let (_, south, _, north) = zoom_bounds_core(35.0, -97.0, Some((-120.0, 10.0, -60.0, 50.0)));
        assert_eq!(south, 35.0 - MAX_HALF_LAT_DEGREES);
        assert_eq!(north, 35.0 + MAX_HALF_LAT_DEGREES);
    }

    #[test]
    fn zoom_bounds_nan_semantics_match_cpython_max_min() {
        // A NaN latitude falls back to the cosine clamp, so the longitude
        // span stays the 35-degree one while the latitude edges go NaN.
        let (west, south, east, north) = zoom_bounds_core(f64::NAN, 10.0, None);
        assert_eq!(west, 10.0 - 0.98 * LON_ASPECT / 0.35);
        assert_eq!(east, 10.0 + 0.98 * LON_ASPECT / 0.35);
        assert!(south.is_nan() && north.is_nan());
        // NaN box members are ignored by max(), so half_lat stays 0.98.
        let bounds = zoom_bounds_core(35.0, -97.0, Some((f64::NAN, 34.9, -96.9, 35.1)));
        assert!((bounds.3 - bounds.1 - 1.96).abs() < 1.0e-9);
    }

    #[test]
    fn raster_source_rect_places_the_crop_on_the_sounding() {
        let bounds = zoom_bounds_core(LAT, LON, None);
        let (west, south, east, north) = bounds;
        let width = 3072.0;
        let height = 1536.0;
        let (x, y, w, h) = raster_source_rect_core(
            FIELD_BOUNDS.0,
            FIELD_BOUNDS.1,
            FIELD_BOUNDS.2,
            FIELD_BOUNDS.3,
            bounds,
            width,
            height,
        )
        .unwrap();
        // Mirror the back-projection identity from test_locator_model_field.
        let (lon0, lon1, lat0, lat1) = FIELD_BOUNDS;
        let back_lon = lon0 + (x + w / 2.0) / width * (lon1 - lon0);
        let back_lat = lat1 - (y + h / 2.0) / height * (lat1 - lat0);
        assert!((back_lon - (west + east) / 2.0).abs() < 0.02);
        assert!((back_lat - (south + north) / 2.0).abs() < 0.02);
        // An interior sounding stays inside the image.
        assert!(x >= 0.0 && y >= 0.0);
        assert!(x + w <= width + 1.0 && y + h <= height + 1.0);
    }

    #[test]
    fn raster_source_rect_is_left_unclamped_at_the_domain_edge() {
        let bounds = zoom_bounds_core(40.0, -133.8, None);
        let (x, _y, w, _h) = raster_source_rect_core(
            FIELD_BOUNDS.0,
            FIELD_BOUNDS.1,
            FIELD_BOUNDS.2,
            FIELD_BOUNDS.3,
            bounds,
            3072.0,
            1536.0,
        )
        .unwrap();
        assert!(x < 0.0);
        assert!(x + w > 0.0);
    }

    #[test]
    fn raster_source_rect_refuses_distant_and_degenerate_fields() {
        let london = zoom_bounds_core(51.5, -0.12, None);
        assert!(raster_source_rect_core(
            FIELD_BOUNDS.0,
            FIELD_BOUNDS.1,
            FIELD_BOUNDS.2,
            FIELD_BOUNDS.3,
            london,
            3072.0,
            1536.0,
        )
        .is_none());
        // A degenerate extent is refused through the overlap rule: the view
        // sits entirely east of the 1e-12-degree field.
        assert!(raster_source_rect_core(
            -100.0,
            -100.0 + 1.0e-12,
            40.0,
            40.0 + 1.0e-12,
            zoom_bounds_core(LAT, LON, None),
            8.0,
            4.0,
        )
        .is_none());
        // NaN operands never trip the <= comparisons, so the crop follows
        // the muted arithmetic of CPython, including max(1.0, nan) == 1.0.
        let crop = raster_source_rect_core(
            f64::NAN,
            FIELD_BOUNDS.1,
            FIELD_BOUNDS.2,
            FIELD_BOUNDS.3,
            zoom_bounds_core(LAT, LON, None),
            3072.0,
            1536.0,
        )
        .unwrap();
        assert!(crop.0.is_nan());
        assert_eq!(crop.2, 1.0);
    }

    /// Worst corner disagreement, in degrees, matching ``_placement_error``
    /// from the Python test suite.
    fn placement_error(bounds: Bounds, source: Rect, target: Rect, rect: Rect) -> f64 {
        let (lon0, lon1, lat0, lat1) = FIELD_BOUNDS;
        let (west, _south, east, north) = bounds;
        let (sx, sy, sw, sh) = source;
        let (tx, ty, tw, th) = target;
        let width = 3072.0;
        let height = 1536.0;
        let mut worst = 0.0f64;
        for (px, py, ix, iy) in [(sx, sy, tx, ty), (sx + sw, sy + sh, tx + tw, ty + th)] {
            let source_lon = lon0 + px / width * (lon1 - lon0);
            let source_lat = lat1 - py / height * (lat1 - lat0);
            let target_lon = west + (ix - rect.0) / rect.2 * (east - west);
            let target_lat = north - (iy - rect.1) / rect.3 * (north - bounds.1);
            worst = worst.max((source_lon - target_lon).abs());
            worst = worst.max((source_lat - target_lat).abs());
        }
        worst
    }

    #[test]
    fn draw_rects_keep_an_edge_sounding_on_its_own_ground() {
        let rect = (4.0, 6.0, 240.0, 150.0);
        for (lat, lon) in [
            (40.39, -80.16),
            (40.0, -133.8),
            (40.0, -60.8),
            (52.8, -100.0),
            (20.8, -100.0),
            (52.8, -134.0),
            (20.8, -60.8),
        ] {
            let bounds = zoom_bounds_core(lat, lon, None);
            let Some((source, target)) = raster_draw_rects_core(
                FIELD_BOUNDS.0,
                FIELD_BOUNDS.1,
                FIELD_BOUNDS.2,
                FIELD_BOUNDS.3,
                rect,
                bounds,
                3072.0,
                1536.0,
            ) else {
                panic!("no rectangles for ({lat}, {lon})");
            };
            // Source and target must agree to a rounding of the same ground.
            assert!(
                placement_error(bounds, source, target, rect) < 1.0e-6,
                "lat {lat} lon {lon}"
            );
            // The source stays inside the image, the target inside the inset.
            assert!(source.0 >= -1.0e-6 && source.1 >= -1.0e-6);
            assert!(source.0 + source.2 <= 3072.0 + 1.0e-6);
            assert!(source.1 + source.3 <= 1536.0 + 1.0e-6);
            assert!(target.0 >= rect.0 - 1.0e-6 && target.1 >= rect.1 - 1.0e-6);
            assert!(target.0 + target.2 <= rect.0 + rect.2 + 1.0e-6);
            assert!(target.1 + target.3 <= rect.1 + rect.3 + 1.0e-6);
        }
    }

    #[test]
    fn draw_rects_fill_the_inset_and_leave_gaps_truthfully() {
        // An interior sounding covers the whole inset.
        let rect = (0.0, 0.0, 240.0, 150.0);
        let (tx, ty, tw, th) = raster_draw_rects_core(
            FIELD_BOUNDS.0,
            FIELD_BOUNDS.1,
            FIELD_BOUNDS.2,
            FIELD_BOUNDS.3,
            rect,
            zoom_bounds_core(LAT, LON, None),
            3072.0,
            1536.0,
        )
        .unwrap()
        .1;
        assert!((tx - 0.0).abs() < 1.0e-6 && (ty - 0.0).abs() < 1.0e-6);
        assert!((tw - 240.0).abs() < 1.0e-6 && (th - 150.0).abs() < 1.0e-6);
        // A north-west corner sounding leaves most of the inset unpainted.
        let (_, target) = raster_draw_rects_core(
            FIELD_BOUNDS.0,
            FIELD_BOUNDS.1,
            FIELD_BOUNDS.2,
            FIELD_BOUNDS.3,
            rect,
            zoom_bounds_core(52.8, -134.0, None),
            3072.0,
            1536.0,
        )
        .unwrap();
        let covered = (target.2 * target.3) / (rect.2 * rect.3);
        assert!(0.2 < covered && covered < 0.6, "covered {covered}");
        // A sounding outside the field draws nothing at all.
        assert!(raster_draw_rects_core(
            FIELD_BOUNDS.0,
            FIELD_BOUNDS.1,
            FIELD_BOUNDS.2,
            FIELD_BOUNDS.3,
            rect,
            zoom_bounds_core(51.5, -0.12, None),
            3072.0,
            1536.0,
        )
        .is_none());
    }

    fn tile(values: &[i64]) -> String {
        format!(
            "[{}]",
            values
                .iter()
                .map(|i| i.to_string())
                .collect::<Vec<_>>()
                .join(",")
        )
    }

    #[test]
    fn county_tiles_decode_delta_paths_like_the_reference() {
        // The builder's encoding: absolute first pair, then deltas, all on
        // the 1e-5 degree grid.
        let payload =
            format!("[{}]", tile(&[-9_600_000, 3_650_000, 50_000, 0, 50_000, 0])).into_bytes();
        let lines = decode_county_tile_core(&payload, -96, 36).unwrap();
        assert_eq!(
            lines,
            vec![vec![(-96.0, 36.5), (-95.5, 36.5), (-95.0, 36.5)]]
        );
    }

    #[test]
    fn county_tiles_enforce_their_one_degree_containment() {
        // Lines carry an absolute point plus a zero delta, so each case is a
        // degenerate two-point path at the margin under test.  Exactly on an
        // inclusive margin: kept; one 1e-5-degree unit beyond it: dropped.
        let cases = [
            (&[-10, 0, 0, 0][..], true),      // lon == -1e-4, the west margin
            (&[-11, 0, 0, 0][..], false),     // one unit past the west margin
            (&[100_010, 0, 0, 0][..], true),  // lon == 1.0001, the east margin
            (&[100_011, 0, 0, 0][..], false), // one unit past the east margin
            (&[0, -10, 0, 0][..], true),      // lat == -1e-4, the south margin
            (&[0, -11, 0, 0][..], false),
            (&[0, 100_010, 0, 0][..], true), // lat == 1.0001, the north margin
            (&[0, 100_011, 0, 0][..], false),
        ];
        for (line, expect_kept) in cases {
            let payload = format!("[{}]", tile(line)).into_bytes();
            let lines = decode_county_tile_core(&payload, 0, 0).unwrap();
            assert_eq!(lines.is_empty(), !expect_kept, "line {line:?}");
        }
    }

    #[test]
    fn county_tiles_skip_lines_the_reference_would_skip() {
        // A tile whose only line is malformed decodes to nothing.
        for payload in [
            b"[[-9600000]]".to_vec(),
            b"[[1,2,3]]".to_vec(),
            b"[[1,2,3,4,5]]".to_vec(),
            b"[[1,2,1.5,2.5]]".to_vec(),
            br#"[["a","b",1,2]]"#.to_vec(),
        ] {
            assert!(
                decode_county_tile_core(&payload, 0, 0).unwrap().is_empty(),
                "payload {payload:?}"
            );
        }
        // A well-formed line beside the malformed one survives.
        let payload = format!("[[1,2,3],{}]", tile(&[-9_600_000, 3_600_000, 0, 0])).into_bytes();
        let lines = decode_county_tile_core(&payload, -96, 36).unwrap();
        assert_eq!(lines, vec![vec![(-96.0, 36.0), (-96.0, 36.0)]]);
    }

    #[test]
    fn county_tiles_accept_bools_null_and_bounded_payloads() {
        // bool is an int in Python, so true/false decode as 1/0.
        let payload = b"[[-9600000, true, 100000, false]]".to_vec();
        let lines = decode_county_tile_core(&payload, -96, 0).unwrap();
        assert_eq!(lines, vec![vec![(-96.0, 1.0e-5), (-95.0, 1.0e-5)]]);
        // null never counts as an int.
        let with_null = b"[[null, 1, 0, 1]]".to_vec();
        assert!(decode_county_tile_core(&with_null, 0, 0)
            .unwrap()
            .is_empty());
        // Non-list tops decode to nothing without raising, exactly like the
        // Python decoder's guards.
        assert!(decode_county_tile_core(b"null", 0, 0).unwrap().is_empty());
        assert!(decode_county_tile_core(b"42", 0, 0).unwrap().is_empty());
        assert!(decode_county_tile_core(br#"{"tile": 1}"#, 0, 0)
            .unwrap()
            .is_empty());
        // Beyond the per-tile line cap the whole tile is refused.
        let many = format!(
            "[{}]",
            (0..=MAX_TILE_LINES)
                .map(|_| "0")
                .collect::<Vec<_>>()
                .join(",")
        );
        assert!(decode_county_tile_core(many.as_bytes(), 0, 0)
            .unwrap()
            .is_empty());
    }

    #[test]
    fn county_tiles_raise_value_error_on_unusable_payloads() {
        assert!(matches!(
            decode_county_tile_core(&[0x80, b'1'], 0, 0),
            Err(TileDecodeError::NonAscii)
        ));
        assert!(matches!(
            decode_county_tile_core(b"{", 0, 0),
            Err(TileDecodeError::MalformedJson)
        ));
        // Python's json accepts the non-standard NaN token and then skips
        // the line; serde_json refuses the payload, as documented.
        assert!(matches!(
            decode_county_tile_core(b"[NaN]", 0, 0),
            Err(TileDecodeError::MalformedJson)
        ));
    }

    #[test]
    fn tile_coords_enumerate_lon_major_and_respect_the_locality_cap() {
        assert_eq!(county_tile_coords_core(0.0, 0.0, 0.9, 0.9), vec![(0, 0)]);
        assert_eq!(
            county_tile_coords_core(0.0, 0.0, 1.0, 1.0),
            vec![(0, 0), (0, 1), (1, 0), (1, 1)],
        );
        assert_eq!(
            county_tile_coords_core(-1.5, -1.0, -0.5, 0.0),
            vec![(-2, -1), (-2, 0), (-1, -1), (-1, 0)],
        );
        assert_eq!(
            county_tile_coords_core(0.5, 0.5, 2.5, 2.5),
            vec![
                (0, 0),
                (0, 1),
                (0, 2),
                (1, 0),
                (1, 1),
                (1, 2),
                (2, 0),
                (2, 1),
                (2, 2),
            ],
        );
        // The national query is refused before decoding anything.
        assert!(county_tile_coords_core(-125.0, 24.0, -66.0, 50.0).is_empty());
        // Degenerate, non-finite and absurd spans are refused as well.
        assert!(county_tile_coords_core(1.0, 1.0, 1.0, 2.0).is_empty());
        assert!(county_tile_coords_core(f64::NAN, 0.0, 1.0, 1.0).is_empty());
        assert!(county_tile_coords_core(1.0e30, -1.0e30, 1.0, 2.0).is_empty());
    }

    #[test]
    fn global_lines_wrap_near_the_view_centre_before_filtering() {
        let lines: Vec<Line> = vec![
            vec![(178.0, 57.65), (178.5, 57.65)],   // unshifted, in view
            vec![(-178.0, 57.65), (-177.5, 57.65)], // wraps onto the same copy
            vec![(10.0, 57.0), (12.0, 58.0)],       // far away
            vec![(175.5, 57.0), (175.8, 58.0)],     // west of the view
        ];
        // A view whose centre sits at 180.5 keeps the unwrapped and wrapped
        // copies of the same antimeridian coastline.
        let (west, south, east, north) = (178.0, 57.0, 183.0, 59.0);
        let matched = filter_global_lines_core(&lines, west, south, east, north);
        assert_eq!(matched.len(), 2);
        assert_eq!(matched[0], vec![(178.0, 57.65), (178.5, 57.65)]);
        assert_eq!(matched[1], vec![(182.0, 57.65), (182.5, 57.65)]);

        // The viewport test is inclusive on every edge.
        let touching: Vec<Line> = vec![vec![(west, north), (west, north)]];
        assert_eq!(
            filter_global_lines_core(&touching, west, south, east, north).len(),
            1
        );
        let beside: Vec<Line> = vec![vec![(west - 0.0001, north), (west - 0.0001, north)]];
        assert!(filter_global_lines_core(&beside, west, south, east, north).is_empty());
    }

    #[test]
    fn inset_rect_matches_the_widget_geometry_seam() {
        // A hodograph-sized bitmap reserves the 29% corner inset.
        let (left, top, width, height) = inset_rect_core(640.0, 480.0, 0.0, 0.0).unwrap().unwrap();
        assert_eq!((left, top), (1.0, 1.0));
        assert_eq!(width, 185.0);
        assert_eq!(height, 118.0);
        // The raw widget offsets are coerced like int(tlx): truncation only.
        let (left, top, _, _) = inset_rect_core(640.0, 480.0, 3.9, 4.9).unwrap().unwrap();
        assert_eq!((left, top), (4.0, 5.0));
        // The inset caps at 250 px wide.
        let rect = inset_rect_core(2000.0, 2000.0, 0.0, 0.0).unwrap().unwrap();
        assert_eq!(rect.2, 250.0);
        // Room the inset rejects: width under 110 despite the bitmap.
        assert!(inset_rect_core(118.0, 480.0, 0.0, 0.0).unwrap().is_none());
        // A bitmap too short for the inset: the available 71 px is under
        // the 72 px floor.
        assert!(inset_rect_core(640.0, 80.0, 0.0, 0.0).unwrap().is_none());
        // A frame pushed against the bitmap edge leaves no room.
        assert!(inset_rect_core(640.0, 480.0, 632.0, 0.0).unwrap().is_none());
    }

    #[test]
    fn inset_rect_mirrors_pythons_int_failures() {
        assert!(matches!(
            inset_rect_core(640.0, 480.0, f64::NAN, 0.0),
            Err(IntegerCoercionError::NotAnInteger)
        ));
        assert!(matches!(
            inset_rect_core(640.0, 480.0, 0.0, f64::INFINITY),
            Err(IntegerCoercionError::Infinite)
        ));
        assert!(matches!(
            inset_rect_core(f64::NAN, 480.0, 0.0, 0.0),
            Err(IntegerCoercionError::NotAnInteger)
        ));
        assert!(matches!(
            inset_rect_core(f64::INFINITY, 480.0, 0.0, 0.0),
            Err(IntegerCoercionError::Infinite)
        ));
    }

    #[test]
    fn marker_position_projects_wraps_clamps_and_reports_inside() {
        let bounds = (-100.0, 35.0, -95.0, 40.0);
        let rect = (10.0, 20.0, 240.0, 150.0);
        // The centre of the view maps to the centre of the rect.
        let (x, y, inside) = marker_position_core(37.5, -97.5, bounds, rect).unwrap();
        assert_eq!(x, 10.0 + 240.0 / 2.0);
        assert_eq!(y, 20.0 + 150.0 / 2.0);
        assert!(inside);
        // Edges count as inside (QRectF::contains is inclusive).
        let (_, _, inside) = marker_position_core(40.0, -95.0, bounds, rect).unwrap();
        assert!(inside);
        // An off-view point clamps to a 3 px edge indicator and reports
        // that it was outside.
        let (x, y, inside) = marker_position_core(60.0, -160.0, bounds, rect).unwrap();
        assert!(!inside);
        assert_eq!(x, rect.0 + MARKER_EDGE_MARGIN);
        assert_eq!(y, rect.1 + MARKER_EDGE_MARGIN);
        // A point a world away lands on the view's own copy of it.
        let antimeridian = (165.0, 35.0, 175.0, 40.0);
        let (x, _, _) = marker_position_core(37.5, -190.0, antimeridian, rect).unwrap();
        let (wrapped, _, _) = marker_position_core(37.5, 170.0, antimeridian, rect).unwrap();
        assert_eq!(x, wrapped);
        assert_eq!(x, 10.0 + 240.0 / 2.0);
    }

    #[test]
    fn marker_position_refuses_degenerate_views_and_mutes_nan() {
        let rect = (10.0, 20.0, 240.0, 150.0);
        let bounds = (-100.0, 35.0, -95.0, 40.0);
        assert!(marker_position_core(37.5, -97.5, (-100.0, 35.0, -100.0, 40.0), rect).is_err());
        assert!(marker_position_core(37.5, -97.5, (-100.0, 35.0, -95.0, 35.0), rect).is_err());
        // NaN results mirror Python's clamping order: max(left+3, nan) is
        // left+3 because the NaN comparison is never true.
        let (x, y, inside) = marker_position_core(f64::NAN, f64::NAN, bounds, rect).unwrap();
        assert!(!inside);
        assert_eq!(x, rect.0 + MARKER_EDGE_MARGIN);
        assert_eq!(y, rect.1 + MARKER_EDGE_MARGIN);
    }
}

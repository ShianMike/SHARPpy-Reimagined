//! Overlay geometry for the Qt-free map overlay model.
//!
//! Rust mirror of the geometry helpers in `sharpmod.maps.map_overlays`:
//! bounded decoding of `GeoJSON` polygon coordinates, prepared bounds in the
//! basemap's `(min_lon, max_lon, min_lat, max_lat)` order, odd-even point
//! containment across every ring so holes subtract, image-signature
//! detection, coarse data-age wording, and the raster/view intersection
//! rule.  Decoding is bounded and total: malformed features yield empty
//! results rather than raising.

use pyo3::prelude::*;
use pyo3::types::{PyAny, PyByteArray, PyBytes, PyDict, PyList, PyTuple};

const MAX_RINGS_PER_SHAPE: usize = 512;
const MAX_POINTS_PER_RING: usize = 100_000;
const MIN_RING_POINTS: usize = 3;

const IMAGE_MAGIC: [&[u8]; 4] = [b"\x89PNG\r\n\x1a\n", b"GIF87a", b"GIF89a", b"\xff\xd8\xff"];

type RawPoint = (Option<f64>, Option<f64>);
type Point = (f64, f64);
type Ring = Vec<Point>;
type RingGroup = Vec<Ring>;

/// CPython's two-argument `max` keeps the first operand when the second is
/// NaN, and keeps NaN when it comes first; these helpers reproduce that
/// ordering exactly.
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

fn is_sequence(value: &Bound<'_, PyAny>) -> bool {
    value.is_instance_of::<PyList>() || value.is_instance_of::<PyTuple>()
}

fn sequence_len(value: &Bound<'_, PyAny>) -> Option<usize> {
    if let Ok(list) = value.cast::<PyList>() {
        Some(list.len())
    } else if let Ok(tuple) = value.cast::<PyTuple>() {
        Some(tuple.len())
    } else {
        None
    }
}

fn finite_coord(value: &Bound<'_, PyAny>) -> Option<f64> {
    value
        .extract::<f64>()
        .ok()
        .filter(|number| number.is_finite())
}

/// Apply the ring rules to decoded candidate coordinates: drop non-numeric,
/// non-finite and out-of-range vertices plus consecutive duplicates, remove a
/// single closing vertex, and require three distinct points.
fn normalise_ring(raw_points: &[RawPoint]) -> Option<Ring> {
    if raw_points.len() > MAX_POINTS_PER_RING {
        return None;
    }
    let mut points: Ring = Vec::with_capacity(raw_points.len());
    for &(lon, lat) in raw_points {
        let (Some(lon), Some(lat)) = (lon, lat) else {
            continue;
        };
        if !(-180.0..=180.0).contains(&lon) || !(-90.0..=90.0).contains(&lat) {
            continue;
        }
        if let Some(&(last_lon, last_lat)) = points.last() {
            if last_lon == lon && last_lat == lat {
                continue;
            }
        }
        points.push((lon, lat));
    }
    if points.len() > 1 && points[0] == points[points.len() - 1] {
        points.pop();
    }
    if points.len() < MIN_RING_POINTS {
        return None;
    }
    Some(points)
}

fn decode_ring(raw_ring: &Bound<'_, PyAny>) -> Option<Ring> {
    if !is_sequence(raw_ring) {
        return None;
    }
    let length = sequence_len(raw_ring)?;
    if length > MAX_POINTS_PER_RING {
        return None;
    }
    let mut raw_points: Vec<RawPoint> = Vec::with_capacity(length);
    for entry in raw_ring.try_iter().ok()? {
        let raw_point = entry.ok()?;
        if !is_sequence(&raw_point) {
            raw_points.push((None, None));
            continue;
        }
        let lon = raw_point
            .get_item(0)
            .ok()
            .and_then(|item| finite_coord(&item));
        let lat = raw_point
            .get_item(1)
            .ok()
            .and_then(|item| finite_coord(&item));
        raw_points.push((lon, lat));
    }
    normalise_ring(&raw_points)
}

/// Split a `GeoJSON` geometry into validated per-polygon ring groups
/// (`(exterior, *holes)` per group).  Anything unrecognised or unusable
/// yields an empty result.
fn ring_groups(geometry: &Bound<'_, PyAny>) -> PyResult<Vec<RingGroup>> {
    let Ok(dict) = geometry.cast::<PyDict>() else {
        return Ok(Vec::new());
    };
    let coordinates = match dict.get_item("coordinates")? {
        Some(value) => value,
        None => return Ok(Vec::new()),
    };
    if !is_sequence(&coordinates) {
        return Ok(Vec::new());
    }
    let kind = dict
        .get_item("type")?
        .and_then(|value| value.extract::<String>().ok());
    let polygons: Vec<Bound<'_, PyAny>> = match kind.as_deref() {
        Some("Polygon") => vec![coordinates],
        Some("MultiPolygon") => {
            let mut polygons = Vec::new();
            for polygon in coordinates.try_iter()? {
                polygons.push(polygon?);
            }
            polygons
        }
        _ => return Ok(Vec::new()),
    };
    let mut groups: Vec<RingGroup> = Vec::new();
    for polygon in &polygons {
        if !is_sequence(polygon) {
            continue;
        }
        let mut rings: RingGroup = Vec::new();
        for entry in polygon.try_iter()?.take(MAX_RINGS_PER_SHAPE) {
            if let Some(points) = decode_ring(&entry?) {
                rings.push(points);
            }
        }
        if !rings.is_empty() {
            groups.push(rings);
        }
    }
    Ok(groups)
}

fn point_in_bounds(bounds: (f64, f64, f64, f64), lon: f64, lat: f64) -> bool {
    let (min_lon, max_lon, min_lat, max_lat) = bounds;
    min_lon <= lon && lon <= max_lon && min_lat <= lat && lat <= max_lat
}

/// Odd-even ray casting across every ring, which is what makes holes work
/// without special-casing them.
fn rings_contain_point(rings: &[Ring], lon: f64, lat: f64) -> bool {
    let mut crossings = 0usize;
    for ring in rings {
        let count = ring.len();
        for index in 0..count {
            let (lon1, lat1) = ring[index];
            let (lon2, lat2) = ring[(index + 1) % count];
            if (lat1 > lat) == (lat2 > lat) {
                continue;
            }
            let lon_at = lon1 + (lat - lat1) * (lon2 - lon1) / (lat2 - lat1);
            if lon < lon_at {
                crossings += 1;
            }
        }
    }
    crossings % 2 == 1
}

fn rings_bounds(rings: &[Ring]) -> Option<(f64, f64, f64, f64)> {
    let mut min_lon = f64::INFINITY;
    let mut max_lon = f64::NEG_INFINITY;
    let mut min_lat = f64::INFINITY;
    let mut max_lat = f64::NEG_INFINITY;
    for ring in rings {
        for &(lon, lat) in ring {
            min_lon = min_lon.min(lon);
            max_lon = max_lon.max(lon);
            min_lat = min_lat.min(lat);
            max_lat = max_lat.max(lat);
        }
    }
    if min_lon > max_lon || min_lat > max_lat {
        return None;
    }
    Some((min_lon, max_lon, min_lat, max_lat))
}

fn collect_rings(rings: &Bound<'_, PyAny>) -> PyResult<Vec<Ring>> {
    let mut collected: Vec<Ring> = Vec::new();
    for ring in rings.try_iter()? {
        let mut points: Ring = Vec::new();
        for point in ring?.try_iter()? {
            points.push(point?.extract::<Point>()?);
        }
        collected.push(points);
    }
    Ok(collected)
}

fn image_signature_bytes_match(payload: &[u8]) -> bool {
    !payload.is_empty() && IMAGE_MAGIC.iter().any(|magic| payload.starts_with(magic))
}

/// Report whether `view` coerces to exactly four floats, mirroring Python's
/// generator-unpacking failure behaviour (wrong length or a non-numeric
/// element means "not a view").
fn view_values(view: &Bound<'_, PyAny>) -> Option<[f64; 4]> {
    let mut values = [0.0f64; 4];
    let mut count = 0usize;
    for entry in view.try_iter().ok()? {
        if count >= 4 {
            return None;
        }
        values[count] = entry.ok()?.extract::<f64>().ok()?;
        count += 1;
    }
    if count == 4 {
        Some(values)
    } else {
        None
    }
}

fn raster_view_intersects(bounds: (f64, f64, f64, f64), view: [f64; 4]) -> bool {
    let (min_lon, max_lon, min_lat, max_lat) = bounds;
    let [lon0, lon1, lat0, lat1] = view;
    if py_max(lon0, lon1) < min_lon || py_min(lon0, lon1) > max_lon {
        return false;
    }
    if py_max(lat0, lat1) < min_lat || py_min(lat0, lat1) > max_lat {
        return false;
    }
    true
}

/// Split a `GeoJSON` geometry into per-polygon ring groups.
///
/// Returns a nested tuple of groups, each a tuple of rings, each ring a tuple
/// of `(lon, lat)` pairs.  Unusable geometry returns an empty tuple.
#[pyfunction]
fn overlay_rings_from_geometry<'py>(
    py: Python<'py>,
    geometry: &Bound<'_, PyAny>,
) -> PyResult<Bound<'py, PyTuple>> {
    let groups = ring_groups(geometry)?;
    let mut polygons: Vec<Bound<'py, PyTuple>> = Vec::with_capacity(groups.len());
    for group in &groups {
        let mut rings: Vec<Bound<'py, PyTuple>> = Vec::with_capacity(group.len());
        for points in group {
            rings.push(PyTuple::new(py, points.iter().copied())?);
        }
        polygons.push(PyTuple::new(py, rings)?);
    }
    PyTuple::new(py, polygons)
}

/// Return `(min_lon, max_lon, min_lat, max_lat)` over every ring, or `None`
/// when there is nothing to measure.
#[pyfunction]
fn overlay_bounds_of(rings: &Bound<'_, PyAny>) -> PyResult<Option<(f64, f64, f64, f64)>> {
    let collected = collect_rings(rings)?;
    Ok(rings_bounds(&collected))
}

/// Report whether `(lon, lat)` falls inside the rings.
///
/// A bounding-box early out rejects far-away points, then odd-even ray
/// casting across every ring decides, so holes subtract.
#[pyfunction]
fn overlay_shape_contains(
    rings: &Bound<'_, PyAny>,
    bounds: (f64, f64, f64, f64),
    lon: f64,
    lat: f64,
) -> PyResult<bool> {
    if !point_in_bounds(bounds, lon, lat) {
        return Ok(false);
    }
    let rings = collect_rings(rings)?;
    Ok(rings_contain_point(&rings, lon, lat))
}

/// Report whether `payload` begins with a supported image signature.
///
/// Accepts `bytes` and `bytearray` payloads; a `WMS` service error at HTTP
/// 200 is XML and must never reach the paint path as a broken image.
#[pyfunction]
fn overlay_looks_like_image(payload: &Bound<'_, PyAny>) -> bool {
    let bytes: Vec<u8> = if let Ok(bytes) = payload.cast::<PyBytes>() {
        bytes.as_bytes().to_vec()
    } else if let Ok(array) = payload.cast::<PyByteArray>() {
        array.to_vec()
    } else {
        return false;
    };
    image_signature_bytes_match(&bytes)
}

/// Render a data age for display, or `""` when it is unknown.
///
/// Coarse on purpose: a live frame's usefulness turns on minutes versus tens
/// of minutes.  A negative age reads as "just now"; a NaN reads as unknown.
#[pyfunction]
fn overlay_format_age(seconds: Option<f64>) -> String {
    let Some(seconds) = seconds else {
        return String::new();
    };
    if seconds < 0.0 {
        return "just now".to_string();
    }
    if seconds.is_nan() {
        return String::new();
    }
    if seconds < 90.0 {
        return format!("{} s old", seconds as i64);
    }
    let minutes = seconds / 60.0;
    if minutes < 90.0 {
        return format!("{} min old", minutes.round_ties_even() as i64);
    }
    format!("{:.1} h old", minutes / 60.0)
}

/// Report whether the raster box overlaps a `(lon0, lon1, lat0, lat1)` view.
///
/// A view that is not four coercible floats reports no intersection, which
/// lets the paint path skip decoding entirely.
#[pyfunction]
fn overlay_raster_intersects(bounds: (f64, f64, f64, f64), view: &Bound<'_, PyAny>) -> bool {
    match view_values(view) {
        Some(values) => raster_view_intersects(bounds, values),
        None => false,
    }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(overlay_rings_from_geometry, module)?)?;
    module.add_function(wrap_pyfunction!(overlay_bounds_of, module)?)?;
    module.add_function(wrap_pyfunction!(overlay_shape_contains, module)?)?;
    module.add_function(wrap_pyfunction!(overlay_looks_like_image, module)?)?;
    module.add_function(wrap_pyfunction!(overlay_format_age, module)?)?;
    module.add_function(wrap_pyfunction!(overlay_raster_intersects, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pair(lon: f64, lat: f64) -> RawPoint {
        (Some(lon), Some(lat))
    }

    #[test]
    fn ring_decoding_drops_closing_and_duplicate_points() {
        let square = [
            pair(0.0, 0.0),
            pair(10.0, 0.0),
            pair(10.0, 10.0),
            pair(0.0, 10.0),
            pair(0.0, 0.0),
        ];
        let ring = normalise_ring(&square).unwrap();
        assert_eq!(
            ring,
            vec![(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
        );
        assert_ne!(ring[0], ring[ring.len() - 1]);

        let duplicates = [
            pair(0.0, 0.0),
            pair(0.0, 0.0),
            pair(5.0, 0.0),
            pair(5.0, 5.0),
            pair(5.0, 5.0),
        ];
        assert_eq!(
            normalise_ring(&duplicates).unwrap(),
            vec![(0.0, 0.0), (5.0, 0.0), (5.0, 5.0)]
        );
    }

    #[test]
    fn ring_rejects_out_of_range_nonfinite_and_short_points() {
        let raw = [
            pair(0.0, 0.0),
            pair(999.0, 0.0),
            pair(0.0, 91.0),
            (Some(f64::NAN), Some(1.0)),
            (Some(f64::INFINITY), Some(1.0)),
            (None, None),
            (Some(5.0), None),
            pair(5.0, 5.0),
            pair(10.0, 10.0),
        ];
        assert_eq!(
            normalise_ring(&raw).unwrap(),
            vec![(0.0, 0.0), (5.0, 5.0), (10.0, 10.0)]
        );
    }

    #[test]
    fn ring_needs_three_distinct_vertices() {
        assert!(normalise_ring(&[pair(0.0, 0.0), pair(1.0, 1.0)]).is_none());
        assert!(normalise_ring(&[]).is_none());
        let huge = vec![pair(0.0, 0.0); MAX_POINTS_PER_RING + 1];
        assert!(normalise_ring(&huge).is_none());
        let closed_triangle = [
            pair(0.0, 0.0),
            pair(1.0, 0.0),
            pair(0.0, 1.0),
            pair(0.0, 0.0),
        ];
        assert_eq!(normalise_ring(&closed_triangle).unwrap().len(), 3);
    }

    #[test]
    fn bounds_order_matches_the_basemap_convention() {
        let square: Ring = vec![(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)];
        let hole: Ring = vec![(3.0, 3.0), (7.0, 3.0), (7.0, 7.0), (3.0, 7.0)];
        assert_eq!(
            rings_bounds(&[square.clone(), hole.clone()]),
            Some((0.0, 10.0, 0.0, 10.0))
        );
        assert_eq!(rings_bounds(&[square]), Some((0.0, 10.0, 0.0, 10.0)));
        assert_eq!(rings_bounds(&[hole]), Some((3.0, 7.0, 3.0, 7.0)));
        assert_eq!(rings_bounds(&[]), None);
        assert_eq!(rings_bounds(&[Vec::new()]), None);
    }

    #[test]
    fn shape_contains_respects_holes_and_the_bounding_box() {
        let square: Ring = vec![(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)];
        let hole: Ring = vec![(3.0, 3.0), (7.0, 3.0), (7.0, 7.0), (3.0, 7.0)];
        let rings = [square, hole];
        let bounds = (0.0, 10.0, 0.0, 10.0);
        for (lon, lat, inside) in [
            (1.0, 5.0, true),
            (5.0, 1.0, true),
            (9.0, 9.0, true),
            (5.0, 5.0, false),
            (4.0, 5.0, false),
            (11.0, 5.0, false),
            (5.0, 11.0, false),
        ] {
            assert_eq!(rings_contain_point(&rings, lon, lat), inside);
        }
        assert!(point_in_bounds(bounds, 1.0, 1.0));
        assert!(!point_in_bounds(bounds, 1000.0, 1000.0));
        assert!(!point_in_bounds(bounds, -1.0, 5.0));
        assert!(!point_in_bounds(bounds, 5.0, 11.0));
    }

    #[test]
    fn image_signatures_are_recognised() {
        assert!(image_signature_bytes_match(b"\x89PNG\r\n\x1a\n\x00\x00"));
        assert!(image_signature_bytes_match(b"GIF87a\x00\x00"));
        assert!(image_signature_bytes_match(b"GIF89a\x00\x00"));
        assert!(image_signature_bytes_match(b"\xff\xd8\xff\xe0"));
        assert!(!image_signature_bytes_match(
            b"<?xml version=\"1.0\"?><ServiceException/>"
        ));
        assert!(!image_signature_bytes_match(b""));
        assert!(!image_signature_bytes_match(b"not an image at all"));
        assert!(!image_signature_bytes_match(b"\x89PN"));
    }

    #[test]
    fn age_wording_matches_the_reference_table() {
        assert_eq!(overlay_format_age(None), "");
        assert_eq!(overlay_format_age(Some(-5.0)), "just now");
        assert_eq!(overlay_format_age(Some(f64::NEG_INFINITY)), "just now");
        assert_eq!(overlay_format_age(Some(0.0)), "0 s old");
        assert_eq!(overlay_format_age(Some(45.0)), "45 s old");
        assert_eq!(overlay_format_age(Some(89.9)), "89 s old");
        assert_eq!(overlay_format_age(Some(200.0)), "3 min old");
        assert_eq!(overlay_format_age(Some(3600.0)), "60 min old");
        assert_eq!(overlay_format_age(Some(20000.0)), "5.6 h old");
        assert_eq!(overlay_format_age(Some(5400.0)), "1.5 h old");
        assert_eq!(overlay_format_age(Some(f64::NAN)), "");
        assert_eq!(overlay_format_age(Some(f64::INFINITY)), "inf h old");
    }

    #[test]
    fn minutes_round_ties_to_even_like_the_reference() {
        assert_eq!(overlay_format_age(Some(5310.0)), "88 min old");
        assert_eq!(overlay_format_age(Some(5370.0)), "90 min old");
    }

    #[test]
    fn raster_intersection_matches_the_raster_rule() {
        let conus = (-130.0, -60.0, 20.0, 55.0);
        assert!(raster_view_intersects(conus, [-100.0, -90.0, 35.0, 45.0]));
        assert!(raster_view_intersects(conus, [-180.0, 180.0, -90.0, 90.0]));
        assert!(raster_view_intersects(conus, [-90.0, -100.0, 45.0, 35.0]));
        assert!(!raster_view_intersects(conus, [0.0, 20.0, 40.0, 60.0]));
        assert!(!raster_view_intersects(conus, [-130.0, -60.0, 60.0, 70.0]));
    }

    #[test]
    fn python_pair_max_min_handles_nan_like_python() {
        assert!(py_max(f64::NAN, 1.0).is_nan());
        assert_eq!(py_max(1.0, f64::NAN), 1.0);
        assert!(py_min(f64::NAN, 1.0).is_nan());
        assert_eq!(py_min(1.0, f64::NAN), 1.0);
        // NaN coordinate pairs behave like their Python equivalents inside
        // the intersection rule.
        let conus = (-130.0, -60.0, 20.0, 55.0);
        assert!(raster_view_intersects(conus, [f64::NAN, -90.0, 35.0, 45.0]));
        assert!(raster_view_intersects(
            conus,
            [-100.0, f64::NAN, 35.0, 45.0]
        ));
    }
}

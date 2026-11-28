//! Geometry and overlay-selection contracts for the sounding locator map.
//!
//! Ports of the Qt-free presentation logic shared by the three locator views
//! (hodograph inset, expanded inspection window, and standalone PNG export):
//!
//! * bounds handling from ``sharpmod.maps.locator_presentation`` -- canonical
//!   bounds in this module are always ``(west, south, east, north)``, while
//!   picker map widgets expose ``(south, west, north, east)`` box tuples, so
//!   the explicit conversion keeps a four-float tuple from being accepted in
//!   the wrong order without anyone noticing;
//! * the overlay selection strings from ``sharpmod.maps.locator_overlay``;
//! * the haversine and value-formatting contracts from
//!   ``sharpmod.maps.map_field_inspection``.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyString};
use std::collections::HashSet;

/// Canonical locator bounds: ``(west, south, east, north)``.
type Bounds = (f64, f64, f64, f64);

/// One chosen overlay: a family, and optionally which product within it.
type Selection = (String, Option<String>);

// Kept aligned with locator_presentation.py so the expanded view and the
// export cannot quietly choose different meanings for Local and Regional
// than the hodograph inset does.
const LOCAL_HALF_LAT_DEGREES: f64 = 0.98;
const LON_ASPECT: f64 = 1.35;
const AREA_MARGIN: f64 = 1.18;
const MAX_HALF_LAT_DEGREES: f64 = 40.0;
const MIN_SPAN_DEGREES: f64 = 0.02;
// Corrupt session metadata must not drive the bounds helpers into a
// non-terminating wrap loop, so unwrapped longitudes stay bounded.
const MAX_LONGITUDE_MAGNITUDE: f64 = 1_000_000.0;
/// ``cos(latitude)`` floor protecting the longitude aspect at the poles.
const MIN_LATITUDE_COSINE: f64 = 0.35;

/// Every locator overlay family, in the order a control should offer them.
const FAMILIES: [&str; 5] = ["risk", "reports", "hrrr", "radar-site", "radar-mosaic"];
/// Families that may be shown at the same time as each other.
const COMBINABLE: [&str; 3] = ["risk", "reports", "hrrr"];
/// Families that displace every other selection.
const EXCLUSIVE: [&str; 2] = ["radar-site", "radar-mosaic"];
/// Storm reports exist to be read against the outlook that anticipated them.
const REQUIRED_BY_REPORTS: &str = "risk";

/// Plate-distance readout placeholder: an em dash.
const NO_VALUE_TEXT: &str = "\u{2014}";

/// Great-circle radius shared with ``map_field_inspection.haversine_km``.
const EARTH_RADIUS_KM: f64 = 6371.0088;

// ---------------------------------------------------------------------------
// Bounds normalisation (locator_presentation.py)
// ---------------------------------------------------------------------------

/// Port of ``locator_presentation.py::normalise_bounds`` for already-finite
/// inputs.  An east edge at or west of the west edge is treated as an
/// antimeridian crossing and unwrapped eastward; longitudes remain unwrapped
/// afterwards, matching the shared picker map projection.
pub(crate) fn normalise_bounds_core(
    west: f64,
    south: f64,
    east: f64,
    north: f64,
) -> Option<Bounds> {
    // Adding 360 to a very large finite float can leave it unchanged, which
    // would make the wrap non-terminating on corrupt metadata.  This bound
    // still permits thousands of shifted world copies, far beyond supported
    // map navigation or a custom longitude spin box.
    if west.abs() > MAX_LONGITUDE_MAGNITUDE || east.abs() > MAX_LONGITUDE_MAGNITUDE {
        return None;
    }
    if !(-90.0 <= south && south < north && north <= 90.0) {
        return None;
    }
    let east = if east <= west {
        let wrapped = (east - west).rem_euclid(360.0);
        west + if wrapped != 0.0 { wrapped } else { 360.0 }
    } else {
        east
    };
    if east - west > 360.0 {
        return None;
    }
    if east - west < MIN_SPAN_DEGREES || north - south < MIN_SPAN_DEGREES {
        return None;
    }
    Some((west, south, east, north))
}

/// Port of ``locator_presentation.py::area_to_map_box``: convert already
/// normalised ``(west, south, east, north)`` bounds to a picker map box tuple
/// in ``(south, west, north, east)`` order, wrapping the east edge back into
/// ``[-180, 180)`` when it lies beyond the antimeridian.
pub(crate) fn area_to_map_box_core(bounds: Bounds) -> (f64, f64, f64, f64) {
    let (west, south, east, north) = bounds;
    let east = if east > 180.0 {
        ((east + 180.0).rem_euclid(360.0)) - 180.0
    } else {
        east
    };
    (south, west, north, east)
}

fn builtin_float(py: Python<'_>) -> Option<Bound<'_, PyAny>> {
    py.import("builtins").ok()?.getattr("float").ok()
}

/// ``float(item)`` followed by the finite check, mirroring the module's
/// private ``_finite`` helper: anything that cannot convert to a finite float
/// means "not usable", not an exception.
fn finite_float(float_fn: &Bound<'_, PyAny>, item: &Bound<'_, PyAny>) -> Option<f64> {
    let converted = float_fn.call1((item,)).ok()?;
    let number = converted.extract::<f64>().ok()?;
    number.is_finite().then_some(number)
}

/// Extract exactly four finite ``(west, south, east, north)`` floats from an
/// arbitrary Python value, mirroring the generator unpacking in
/// ``normalise_bounds``: strings and bytes are rejected outright, and anything
/// that is not an iterable of exactly four finite numbers yields ``None``.
fn bounds_from_py(value: &Bound<'_, PyAny>) -> Option<Bounds> {
    let py = value.py();
    if value.is_instance_of::<PyString>() || value.is_instance_of::<PyBytes>() {
        return None;
    }
    let iter = value.try_iter().ok()?;
    let float_fn = builtin_float(py)?;
    let mut items: Vec<Option<f64>> = Vec::with_capacity(4);
    for item in iter {
        let item = item.ok()?;
        if items.len() == 4 {
            // A fifth element means the four-name unpack raises ValueError.
            return None;
        }
        items.push(finite_float(&float_fn, &item));
    }
    if items.len() != 4 {
        // Not enough values to unpack.
        return None;
    }
    let (Some(west), Some(south), Some(east), Some(north)) =
        (items[0], items[1], items[2], items[3])
    else {
        return None;
    };
    normalise_bounds_core(west, south, east, north)
}

#[pyfunction]
fn locator_normalise_bounds(value: &Bound<'_, PyAny>) -> Option<Bounds> {
    bounds_from_py(value)
}

#[pyfunction]
fn locator_area_to_map_box(value: &Bound<'_, PyAny>) -> Option<(f64, f64, f64, f64)> {
    bounds_from_py(value).map(area_to_map_box_core)
}

// ---------------------------------------------------------------------------
// Centred / fitted extents (locator_presentation.py)
// ---------------------------------------------------------------------------

/// Signed longitude delta from ``center`` to ``lon``, in degrees.
fn longitude_near(lon: f64, center: f64) -> f64 {
    center + ((lon - center + 180.0).rem_euclid(360.0)) - 180.0
}

/// Shift an area so its center longitude sits near ``lon``.
fn shift_area_near(area: Bounds, lon: f64) -> Bounds {
    let (west, south, east, north) = area;
    let center = (west + east) / 2.0;
    let shift = longitude_near(center, lon) - center;
    (west + shift, south, east + shift, north)
}

/// Clamp a latitude band into range, keeping at least the minimum span.
fn clamp_latitudes(south: f64, north: f64) -> (f64, f64) {
    let span = 180.0_f64.min(MIN_SPAN_DEGREES.max(north - south));
    let center = (south + north) / 2.0;
    let center = (90.0 - span / 2.0).min((-90.0 + span / 2.0).max(center));
    (center - span / 2.0, center + span / 2.0)
}

/// Port of ``locator_presentation.py::centred_bounds``: frame a point at the
/// established ground aspect, optionally fitting a represented area.  A large
/// represented area outranks the ordinary local/regional zoom cap; after a
/// pole clamp, longitude must independently retain the whole area.
pub(crate) fn centred_bounds_core(
    lat: f64,
    lon: f64,
    half_lat: f64,
    include: Option<Bounds>,
) -> Bounds {
    let mut half_lat = MAX_HALF_LAT_DEGREES.min(LOCAL_HALF_LAT_DEGREES.max(half_lat));
    let cos_lat = MIN_LATITUDE_COSINE.max(lat.to_radians().cos());
    let required_half_lon = match include.map(|area| shift_area_near(area, lon)) {
        Some((west, south, east, north)) => {
            let reach_lat = (north - lat).abs().max((lat - south).abs());
            let reach_lon = (east - lon).abs().max((lon - west).abs());
            let required_half_lon = reach_lon * AREA_MARGIN;
            half_lat = half_lat
                .max(reach_lat * AREA_MARGIN)
                .max(required_half_lon * cos_lat / LON_ASPECT);
            required_half_lon
        }
        None => {
            half_lat = half_lat.min(MAX_HALF_LAT_DEGREES);
            0.0
        }
    };
    half_lat = half_lat.min(90.0);
    let half_lon = (half_lat * LON_ASPECT / cos_lat).max(required_half_lon);
    let (south, north) = clamp_latitudes(lat - half_lat, lat + half_lat);
    (lon - half_lon, south, lon + half_lon, north)
}

/// Port of ``locator_presentation.py::fit_bounds``: fit ``area`` and ``point``
/// with margin, centring their geographic union.  Fit is a promise about
/// coverage, not a request for a fixed zoom ceiling: the latitude span can
/// saturate at the poles while longitude still needs more than its nominal
/// aspect to keep the entire selected area visible.
pub(crate) fn fit_bounds_core(
    lat: f64,
    lon: f64,
    area: Option<Bounds>,
    minimum_half_lat: f64,
) -> Bounds {
    let Some((west, south, east, north)) = area.map(|bounds| shift_area_near(bounds, lon)) else {
        return centred_bounds_core(lat, lon, minimum_half_lat, None);
    };
    let west = west.min(lon);
    let east = east.max(lon);
    let south = south.min(lat);
    let north = north.max(lat);
    let center_lon = (west + east) / 2.0;
    let center_lat = (south + north) / 2.0;
    let mut half_lat = minimum_half_lat.max((north - south) * AREA_MARGIN / 2.0);
    let cos_lat = MIN_LATITUDE_COSINE.max(center_lat.to_radians().cos());
    let required_half_lon = (east - west) * AREA_MARGIN / 2.0;
    half_lat = half_lat.max(required_half_lon * cos_lat / LON_ASPECT);
    half_lat = half_lat.min(90.0);
    let half_lon = (half_lat * LON_ASPECT / cos_lat).max(required_half_lon);
    let (south, north) = clamp_latitudes(center_lat - half_lat, center_lat + half_lat);
    (center_lon - half_lon, south, center_lon + half_lon, north)
}

#[pyfunction]
#[pyo3(signature = (lat, lon, half_lat, include = None))]
fn locator_centred_bounds(lat: f64, lon: f64, half_lat: f64, include: Option<Bounds>) -> Bounds {
    centred_bounds_core(lat, lon, half_lat, include)
}

#[pyfunction]
#[pyo3(signature = (lat, lon, area = None, minimum_half_lat = 0.98))]
fn locator_fit_bounds(lat: f64, lon: f64, area: Option<Bounds>, minimum_half_lat: f64) -> Bounds {
    fit_bounds_core(lat, lon, area, minimum_half_lat)
}

// ---------------------------------------------------------------------------
// Overlay selection strings (locator_overlay.py)
// ---------------------------------------------------------------------------

/// Position of ``family`` within ``FAMILIES``, or the full error message an
/// unknown family produces (mirroring ``Selection.__post_init__``).
fn family_rank(family: &str) -> Result<usize, String> {
    FAMILIES
        .iter()
        .position(|&known| known == family)
        .ok_or_else(|| {
            format!(
                "unknown locator overlay family '{family}'; expected one of: {}",
                FAMILIES.join(", ")
            )
        })
}

/// The family ``family`` still needs before it can be shown, if any.
fn prerequisite_for(family: &str) -> Option<&'static str> {
    (family == "reports").then_some(REQUIRED_BY_REPORTS)
}

/// Port of ``locator_overlay.py::enforce_exclusivity``: drop whatever cannot
/// be shown alongside the selections given.  First mention wins per family;
/// an exclusive radar selection displaces everything else (and a prerequisite
/// that has just been displaced cannot satisfy anything either); a family
/// whose prerequisite is not also selected is dropped.  The result is ordered
/// by ``FAMILIES`` because the inset draws selections in the order received.
pub(crate) fn enforce_overlay_exclusivity_core(
    selections: Vec<Selection>,
) -> Result<Vec<Selection>, String> {
    for (family, _) in &selections {
        family_rank(family)?;
    }
    let mut ordered: Vec<Selection> = Vec::with_capacity(selections.len());
    let mut seen: HashSet<String> = HashSet::with_capacity(selections.len());
    for selection in selections {
        // One slot per family; the first mention wins.
        if !seen.insert(selection.0.clone()) {
            continue;
        }
        ordered.push(selection);
    }
    if let Some(radar) = ordered
        .iter()
        .find(|(family, _)| EXCLUSIVE.contains(&family.as_str()))
    {
        return Ok(vec![radar.clone()]);
    }
    let present: HashSet<&str> = ordered
        .iter()
        .filter(|(family, _)| COMBINABLE.contains(&family.as_str()))
        .map(|(family, _)| family.as_str())
        .collect();
    let mut satisfied: Vec<Selection> = ordered
        .iter()
        .filter(|(family, _)| match prerequisite_for(family) {
            Some(required) => present.contains(required),
            None => true,
        })
        .cloned()
        .collect();
    satisfied.sort_by_key(|(family, _)| {
        family_rank(family).expect("selection families are validated above")
    });
    Ok(satisfied)
}

/// Port of ``locator_overlay.py::parse``: parse a selection string such as
/// ``"risk:torn,hrrr:refc"``.  ``None``, an empty string and ``"none"`` all
/// mean "draw a bare locator", which is the default: an inset that reaches
/// for the network before being asked is a surprise, not a feature.
pub(crate) fn parse_overlay_spec_core(text: Option<&str>) -> Result<Vec<Selection>, String> {
    let Some(text) = text else {
        return Ok(Vec::new());
    };
    let cleaned = text.trim();
    if cleaned.is_empty() || cleaned.to_lowercase() == "none" {
        return Ok(Vec::new());
    }
    let mut selections: Vec<Selection> = Vec::new();
    for part in cleaned.split(',') {
        let token = part.trim();
        if token.is_empty() {
            continue;
        }
        let (family, product) = match token.split_once(':') {
            Some((family, product)) => (family.trim().to_lowercase(), Some(product.trim())),
            None => (token.to_lowercase(), None),
        };
        let product = match product {
            Some(product) if !product.is_empty() => Some(product.to_string()),
            _ => None,
        };
        selections.push((family, product));
    }
    enforce_overlay_exclusivity_core(selections)
}

#[pyfunction]
#[pyo3(signature = (text = None))]
fn locator_parse_overlay_spec(text: Option<&str>) -> PyResult<Vec<Selection>> {
    parse_overlay_spec_core(text).map_err(PyValueError::new_err)
}

#[pyfunction]
fn locator_enforce_overlay_exclusivity(selections: Vec<Selection>) -> PyResult<Vec<Selection>> {
    enforce_overlay_exclusivity_core(selections).map_err(PyValueError::new_err)
}

// ---------------------------------------------------------------------------
// Inspection contracts (map_field_inspection.py)
// ---------------------------------------------------------------------------

/// Port of ``map_field_inspection.py::haversine_km``: great-circle distance
/// between two points, in kilometres.
pub(crate) fn haversine_km_core(lat0: f64, lon0: f64, lat1: f64, lon1: f64) -> f64 {
    let phi0 = lat0.to_radians();
    let phi1 = lat1.to_radians();
    let delta_phi = (lat1 - lat0).to_radians();
    let delta_lambda = (lon1 - lon0).to_radians();
    let half_phi = (delta_phi / 2.0).sin();
    let half_lambda = (delta_lambda / 2.0).sin();
    let half = half_phi * half_phi + phi0.cos() * phi1.cos() * half_lambda * half_lambda;
    2.0 * EARTH_RADIUS_KM * half.max(0.0).sqrt().min(1.0).asin()
}

#[pyfunction]
fn inspection_haversine_km(lat0: f64, lon0: f64, lat1: f64, lon1: f64) -> f64 {
    haversine_km_core(lat0, lon0, lat1, lon1)
}

/// Port of ``map_field_inspection.py::format_field_value`` for a value that
/// has already been converted: format at a precision suited to magnitude.
pub(crate) fn format_field_value_core(number: Option<f64>) -> String {
    let Some(number) = number else {
        return NO_VALUE_TEXT.to_string();
    };
    if !number.is_finite() {
        return NO_VALUE_TEXT.to_string();
    }
    let magnitude = number.abs();
    if magnitude >= 100.0 {
        format!("{number:.0}")
    } else if magnitude >= 1.0 {
        format!("{number:.1}")
    } else {
        format!("{number:.2}")
    }
}

#[pyfunction]
fn inspection_format_field_value(value: &Bound<'_, PyAny>) -> String {
    let number = builtin_float(value.py()).and_then(|float_fn| finite_float(&float_fn, value));
    format_field_value_core(number)
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(locator_normalise_bounds, module)?)?;
    module.add_function(wrap_pyfunction!(locator_area_to_map_box, module)?)?;
    module.add_function(wrap_pyfunction!(locator_centred_bounds, module)?)?;
    module.add_function(wrap_pyfunction!(locator_fit_bounds, module)?)?;
    module.add_function(wrap_pyfunction!(locator_parse_overlay_spec, module)?)?;
    module.add_function(wrap_pyfunction!(
        locator_enforce_overlay_exclusivity,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(inspection_haversine_km, module)?)?;
    module.add_function(wrap_pyfunction!(inspection_format_field_value, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sel(family: &str, product: Option<&str>) -> Selection {
        (family.to_string(), product.map(String::from))
    }

    #[test]
    fn normalise_bounds_accepts_valid_bounds() {
        assert_eq!(
            normalise_bounds_core(-10.0, 30.0, 10.0, 40.0),
            Some((-10.0, 30.0, 10.0, 40.0))
        );
        // Latitude edges are inclusive.
        assert_eq!(
            normalise_bounds_core(-10.0, -90.0, 10.0, 90.0),
            Some((-10.0, -90.0, 10.0, 90.0))
        );
    }

    #[test]
    fn normalise_bounds_rejects_invalid_or_contrived_bounds() {
        // south must be strictly below north; both stay within [-90, 90].
        assert_eq!(normalise_bounds_core(0.0, 40.0, 10.0, 40.0), None);
        assert_eq!(normalise_bounds_core(0.0, -90.5, 10.0, 40.0), None);
        assert_eq!(normalise_bounds_core(0.0, 30.0, 10.0, 90.5), None);
        // Degenerate spans are rejected.
        assert_eq!(normalise_bounds_core(-0.005, -0.005, 0.005, 0.005), None);
        assert_eq!(normalise_bounds_core(-1.0, 0.0, 1.0, 0.01), None);
        assert_eq!(normalise_bounds_core(-0.005, 0.0, 0.005, 1.0), None);
        // Corrupt metadata with huge unwrapped longitudes is refused.
        assert_eq!(normalise_bounds_core(-1_500_000.0, 0.0, 10.0, 10.0), None);
        assert_eq!(normalise_bounds_core(0.0, 0.0, 1_500_000.0, 10.0), None);
        // Spans beyond one world are refused even when already unwrapped.
        assert_eq!(normalise_bounds_core(0.0, 0.0, 361.0, 10.0), None);
    }

    #[test]
    fn normalise_bounds_unwraps_antimeridian_crossings() {
        assert_eq!(
            normalise_bounds_core(170.0, -10.0, -170.0, 10.0),
            Some((170.0, -10.0, 190.0, 10.0))
        );
        // Coincident edges unwrap to a full world span of 360 degrees.
        assert_eq!(
            normalise_bounds_core(5.0, -10.0, 5.0, 20.0),
            Some((5.0, -10.0, 365.0, 20.0))
        );
        // A wrapped span keeps its measured width, not a full world.
        assert_eq!(
            normalise_bounds_core(350.0, 0.0, 10.0, 10.0),
            Some((350.0, 0.0, 370.0, 10.0))
        );
    }

    #[test]
    fn area_to_map_box_uses_south_west_north_east_order() {
        assert_eq!(
            area_to_map_box_core((-10.0, 30.0, 10.0, 40.0)),
            (30.0, -10.0, 40.0, 10.0)
        );
        // An east edge beyond +180 wraps back into [-180, 180).
        assert_eq!(
            area_to_map_box_core((170.0, -10.0, 190.0, 10.0)),
            (-10.0, 170.0, 10.0, -170.0)
        );
        // Exactly +180 is left untouched for the picker to handle.
        assert_eq!(
            area_to_map_box_core((170.0, -10.0, 180.0, 10.0)),
            (-10.0, 170.0, 10.0, 180.0)
        );
    }

    #[test]
    fn centred_bounds_fits_local_aspect() {
        let bounds = centred_bounds_core(35.0, -97.0, LOCAL_HALF_LAT_DEGREES, None);
        // Centre stays on the point; the ground aspect is 1.35 : cos(lat).
        assert!((((bounds.0 + bounds.2) / 2.0) - -97.0).abs() < 1e-9);
        assert!((bounds.3 - bounds.1 - 1.96).abs() < 1e-9);
        assert!((bounds.1 - 34.02).abs() < 1e-9);
        assert!((bounds.3 - 35.98).abs() < 1e-9);
        let cos_lat = 0.35_f64.max(35.0_f64.to_radians().cos());
        assert!(((bounds.2 - bounds.0) / 2.0 - (0.98 * LON_ASPECT / cos_lat)).abs() < 1e-9);
    }

    #[test]
    fn centred_bounds_shifts_and_covers_included_area() {
        // Area written in 0..360 longitudes, point near the antimeridian
        // crossing: the area must be shifted into the point's frame rather
        // than being read literally.
        let bounds = centred_bounds_core(0.0, 0.0, 0.98, Some((350.0, -1.0, 355.0, 1.0)));
        assert!((bounds.0 - -11.8).abs() < 1e-9);
        assert!((bounds.1 - -8.740_740_74).abs() < 1e-6);
        assert!((bounds.2 - 11.8).abs() < 1e-9);
        assert!((bounds.3 - 8.740_740_74).abs() < 1e-6);
    }

    #[test]
    fn fit_bounds_without_area_matches_centred_bounds() {
        assert_eq!(
            fit_bounds_core(35.0, -97.0, None, LOCAL_HALF_LAT_DEGREES),
            centred_bounds_core(35.0, -97.0, LOCAL_HALF_LAT_DEGREES, None)
        );
    }

    #[test]
    fn fit_bounds_covers_area_and_point_with_margin() {
        let bounds = fit_bounds_core(0.0, 0.0, Some((-1.0, -1.0, 1.0, 1.0)), 0.98);
        // half_lat = max(0.98, 2 * 1.18 / 2) = 1.18; half_lon = 1.18 * 1.35.
        assert!((bounds.0 - -1.593).abs() < 1e-9);
        assert!((bounds.1 - -1.18).abs() < 1e-9);
        assert!((bounds.2 - 1.593).abs() < 1e-9);
        assert!((bounds.3 - 1.18).abs() < 1e-9);
    }

    #[test]
    fn fit_bounds_extends_toward_a_far_area() {
        // The point sits west of the drawn area: the union must be centred.
        let bounds = fit_bounds_core(0.0, 0.0, Some((350.0, -1.0, 355.0, 1.0)), 0.98);
        assert!((bounds.0 - -10.9).abs() < 1e-9);
        assert!((bounds.2 - 0.9).abs() < 1e-9);
        assert!((bounds.3 - 4.370_37).abs() < 1e-6);
        assert!((bounds.1 - -4.370_37).abs() < 1e-6);
    }

    #[test]
    fn overlay_spec_none_empty_and_case_insensitive_none() {
        assert_eq!(parse_overlay_spec_core(None), Ok(Vec::new()));
        assert_eq!(parse_overlay_spec_core(Some("")), Ok(Vec::new()));
        assert_eq!(parse_overlay_spec_core(Some("   ")), Ok(Vec::new()));
        assert_eq!(parse_overlay_spec_core(Some("none")), Ok(Vec::new()));
        assert_eq!(parse_overlay_spec_core(Some("  NONE ")), Ok(Vec::new()));
    }

    #[test]
    fn overlay_spec_parses_and_parts_and_case() {
        assert_eq!(
            parse_overlay_spec_core(Some("risk:torn,hrrr:refc")),
            Ok(vec![sel("risk", Some("torn")), sel("hrrr", Some("refc"))])
        );
        // Family is case-insensitive; the product keeps its case, and
        // everything after the first colon stays part of the product.
        assert_eq!(
            parse_overlay_spec_core(Some("RISK:TORN")),
            Ok(vec![sel("risk", Some("TORN"))])
        );
        assert_eq!(
            parse_overlay_spec_core(Some("hrrr:refc:surface")),
            Ok(vec![sel("hrrr", Some("refc:surface"))])
        );
        // Empty products and empty parts degrade gracefully.
        assert_eq!(
            parse_overlay_spec_core(Some("hrrr:, risk")),
            Ok(vec![sel("risk", None), sel("hrrr", None)])
        );
    }

    #[test]
    fn overlay_spec_reports_requires_risk_and_radar_exists_exclusively() {
        assert_eq!(
            parse_overlay_spec_core(Some("reports")),
            Ok(Vec::new()),
            "reports without risk cannot be read against anything"
        );
        assert_eq!(
            parse_overlay_spec_core(Some("risk,reports")),
            Ok(vec![sel("risk", None), sel("reports", None)])
        );
        assert_eq!(
            parse_overlay_spec_core(Some("risk,radar-mosaic")),
            Ok(vec![sel("radar-mosaic", None)]),
            "naming radar is the more specific request and displaces the rest"
        );
        assert_eq!(
            parse_overlay_spec_core(Some("radar-mosaic,radar-site")),
            Ok(vec![sel("radar-mosaic", None)]),
            "the first exclusive family mentioned wins"
        );
    }

    #[test]
    fn overlay_spec_deduplicates_and_orders_by_family() {
        assert_eq!(
            parse_overlay_spec_core(Some("hrrr:refc,risk:torn")),
            Ok(vec![sel("risk", Some("torn")), sel("hrrr", Some("refc"))]),
            "results are ordered by FAMILIES, not by the order typed"
        );
        assert_eq!(
            parse_overlay_spec_core(Some("risk,hrrr:refc,risk:torn")),
            Ok(vec![sel("risk", None), sel("hrrr", Some("refc"))]),
            "one slot per family; the first mention wins"
        );
    }

    #[test]
    fn overlay_spec_rejects_unknown_families() {
        let err = parse_overlay_spec_core(Some("bogus")).unwrap_err();
        assert_eq!(
            err,
            "unknown locator overlay family 'bogus'; expected one of: risk, reports, hrrr, radar-site, radar-mosaic"
        );
        // A trailing comma attached to "none" is a family name, not "none".
        assert!(parse_overlay_spec_core(Some("none,")).is_err());
        // enforce_exclusivity validates families the same way.
        assert!(enforce_overlay_exclusivity_core(vec![sel("bogus", None)]).is_err());
    }

    #[test]
    fn enforce_exclusivity_keeps_first_radar_and_drops_displaced_prerequisites() {
        assert_eq!(
            enforce_overlay_exclusivity_core(vec![
                sel("reports", None),
                sel("radar-site", Some("KTLX")),
                sel("risk", None),
            ]),
            Ok(vec![sel("radar-site", Some("KTLX"))])
        );
        // The known prerequisite rule: reports requires risk on screen.
        assert_eq!(
            enforce_overlay_exclusivity_core(
                vec![sel("reports", None), sel("hrrr", Some("refc")),]
            ),
            Ok(vec![sel("hrrr", Some("refc"))])
        );
        assert_eq!(
            enforce_overlay_exclusivity_core(vec![
                sel("reports", None),
                sel("risk", Some("torn")),
                sel("hrrr", Some("refc")),
            ]),
            Ok(vec![
                sel("risk", Some("torn")),
                sel("reports", None),
                sel("hrrr", Some("refc")),
            ])
        );
    }

    #[test]
    fn haversine_matches_reference_distances() {
        assert_eq!(haversine_km_core(35.0, -97.0, 35.0, -97.0), 0.0);
        // One degree of arc at the shared Earth radius.
        let d = haversine_km_core(0.0, 0.0, 0.0, 1.0);
        assert!((d - 111.195_08).abs() < 1.0e-3);
        // A two-degree hop across the antimeridian.
        let d = haversine_km_core(0.0, 179.0, 0.0, -179.0);
        assert!((d - 222.390_17).abs() < 1.0e-2);
        // Antipodal points clamp to half the circumference: pi * R.
        let d = haversine_km_core(90.0, 0.0, -90.0, 0.0);
        assert!((d - std::f64::consts::PI * EARTH_RADIUS_KM).abs() < 1.0e-9);
    }

    #[test]
    fn haversine_clamps_nan_to_zero_like_python_max() {
        // Python's max(0.0, nan) returns 0.0, so a NaN coordinate reads as
        // zero distance in the reference implementation; mirror that.
        assert_eq!(haversine_km_core(f64::NAN, 0.0, 0.0, 0.0), 0.0);
    }

    #[test]
    fn format_field_value_precision_follows_magnitude() {
        assert_eq!(format_field_value_core(None), "\u{2014}");
        assert_eq!(format_field_value_core(Some(f64::NAN)), "\u{2014}");
        assert_eq!(format_field_value_core(Some(f64::INFINITY)), "\u{2014}");
        assert_eq!(format_field_value_core(Some(123.456)), "123");
        assert_eq!(format_field_value_core(Some(-123.656)), "-124");
        assert_eq!(format_field_value_core(Some(12.34)), "12.3");
        assert_eq!(format_field_value_core(Some(-0.5)), "-0.50");
        assert_eq!(format_field_value_core(Some(0.567)), "0.57");
        assert_eq!(format_field_value_core(Some(99.99)), "100.0");
    }
}

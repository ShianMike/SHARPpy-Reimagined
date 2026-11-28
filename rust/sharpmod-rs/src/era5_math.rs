//! Native ports of the pure math contracts in ``sharpmod.tools.era5_extract``.
//!
//! The functions mirror the module-level helpers of the ERA5 point-sounding
//! extractor: great-circle distance against a request point, nearest
//! grid-point selection on regular (separable) and curvilinear grids, the
//! Magnus dewpoint conversions, the bottom-most relative-vorticity lookup
//! that feeds the surface merge, and the metre-scale east-west/north-south
//! spacings used by the wind-gradient fallback.

use std::borrow::Cow;

use numpy::{PyArray1, PyReadonlyArray1, PyReadonlyArray2};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

const EARTH_RADIUS_KM: f64 = 6371.0088;
const EARTH_RADIUS_M: f64 = 6_371_008.8;
const EARTH_ROTATION_RATE: f64 = 7.2921159e-5;
const MAGNUS_A: f64 = 17.625;
const MAGNUS_B: f64 = 243.04;
/// Saturation vapor pressure at 0 degrees C, in hPa.
const SATURATION_VAPOR_PRESSURE_HPA: f64 = 6.112;

/// Borrow contiguous NumPy data directly, and copy only when the array is
/// non-contiguous (so sliced views stay correct).
fn slice_or_vec<'a>(arr: &'a PyReadonlyArray1<'_, f64>) -> Cow<'a, [f64]> {
    match arr.as_slice() {
        Ok(slice) => Cow::Borrowed(slice),
        Err(_) => Cow::Owned(arr.as_array().iter().copied().collect()),
    }
}

/// Index of the maximum, matching ``np.argmax`` semantics: the first NaN wins
/// over any value, and the first occurrence wins on ties.
fn arg_max(values: &[f64]) -> usize {
    let mut best_index = 0;
    let mut best_value = values[0];
    for (index, &value) in values.iter().enumerate().skip(1) {
        if best_value.is_nan() {
            break;
        }
        if value.is_nan() || value > best_value {
            best_index = index;
            best_value = value;
        }
    }
    best_index
}

/// Index of the minimum, matching ``np.argmin`` semantics: the first NaN wins
/// over any value, and the first occurrence wins on ties.
fn arg_min(values: &[f64]) -> usize {
    let mut best_index = 0;
    let mut best_value = values[0];
    for (index, &value) in values.iter().enumerate().skip(1) {
        if best_value.is_nan() {
            break;
        }
        if value.is_nan() || value < best_value {
            best_index = index;
            best_value = value;
        }
    }
    best_index
}

// ---------------------------------------------------------------------------
// Great-circle distance
// ---------------------------------------------------------------------------

/// Port of ``era5_extract.great_circle_distance_km``: haversine distance in
/// kilometres from a scalar request point to each ``(lat, lon)`` pair.  The
/// formula handles longitude wrap-around naturally, so mixed 0..360 /
/// -180..180 conventions are safe.
pub(crate) fn great_circle_distance_km_core(
    lat1: f64,
    lon1: f64,
    lat2_values: &[f64],
    lon2_values: &[f64],
) -> Vec<f64> {
    let cos_lat1 = lat1.to_radians().cos();
    lat2_values
        .iter()
        .zip(lon2_values.iter())
        .map(|(&la, &lo)| {
            let lat2r = la.to_radians();
            let dlat = (la - lat1).to_radians();
            let dlon = (lo - lon1).to_radians();
            let s_lat = (dlat / 2.0).sin();
            let s_lon = (dlon / 2.0).sin();
            let a = s_lat * s_lat + cos_lat1 * lat2r.cos() * s_lon * s_lon;
            if a.is_nan() {
                // ``np.clip`` keeps NaN, and NumPy's downstream reductions
                // propagate it here in the reference implementation.
                f64::NAN
            } else {
                2.0 * EARTH_RADIUS_KM * a.clamp(0.0, 1.0).sqrt().asin()
            }
        })
        .collect()
}

#[pyfunction]
fn era5_great_circle_distance_km<'py>(
    py: Python<'py>,
    lat1: f64,
    lon1: f64,
    lat2: PyReadonlyArray1<'py, f64>,
    lon2: PyReadonlyArray1<'py, f64>,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let lat2_values = slice_or_vec(&lat2);
    let lon2_values = slice_or_vec(&lon2);
    if lat2_values.len() != lon2_values.len() {
        return Err(PyValueError::new_err(format!(
            "lat2 and lon2 must have the same length ({} != {})",
            lat2_values.len(),
            lon2_values.len()
        )));
    }
    Ok(PyArray1::from_vec(
        py,
        great_circle_distance_km_core(lat1, lon1, &lat2_values, &lon2_values),
    ))
}

// ---------------------------------------------------------------------------
// Nearest grid-point selection
// ---------------------------------------------------------------------------

/// Port of the separable branch of
/// ``era5_extract.select_nearest_grid_point`` for 1-D latitude/longitude
/// coordinate vectors: the longitude maximizing ``cos(delta_lon)`` is optimal
/// for every latitude because ``cos(latitude)`` is non-negative over the
/// geographic range, which makes the spherical dot product separable without
/// allocating the full great-circle mesh.
pub(crate) fn select_nearest_grid_point_1d_core(
    lats: &[f64],
    lons: &[f64],
    lat0: f64,
    lon0: f64,
) -> Option<((usize, usize), f64, f64)> {
    if lats.is_empty() || lons.is_empty() {
        return None;
    }
    let lat0r = lat0.to_radians();
    let sin_lat0 = lat0r.sin();
    let cos_lat0 = lat0r.cos();
    let lon_scores: Vec<f64> = lons
        .iter()
        .map(|&lon| (lon - lon0).to_radians().cos())
        .collect();
    let ilon = arg_max(&lon_scores);
    let lon_score = lon_scores[ilon];
    let lat_scores: Vec<f64> = lats
        .iter()
        .map(|&lat| {
            let latr = lat.to_radians();
            sin_lat0 * latr.sin() + cos_lat0 * latr.cos() * lon_score
        })
        .collect();
    let ilat = arg_max(&lat_scores);
    Some(((ilat, ilon), lats[ilat], lons[ilon]))
}

#[pyfunction]
fn era5_select_nearest_grid_point_1d(
    lats: PyReadonlyArray1<'_, f64>,
    lons: PyReadonlyArray1<'_, f64>,
    lat0: f64,
    lon0: f64,
) -> PyResult<((usize, usize), f64, f64)> {
    let lats_values = slice_or_vec(&lats);
    let lons_values = slice_or_vec(&lons);
    select_nearest_grid_point_1d_core(&lats_values, &lons_values, lat0, lon0)
        .ok_or_else(|| PyValueError::new_err("lats and lons must be non-empty arrays"))
}

/// Port of the 2-D branch of ``era5_extract.select_nearest_grid_point`` for
/// curvilinear grids: pure haversine distances over a row-major grid with the
/// first minimum winning, then unravelled into ``(iy, ix)``.
pub(crate) fn select_nearest_grid_point_2d_core(
    lats: &[f64],
    lons: &[f64],
    shape: (usize, usize),
    lat0: f64,
    lon0: f64,
) -> Option<((usize, usize), f64, f64)> {
    let (rows, cols) = shape;
    if rows == 0 || cols == 0 {
        return None;
    }
    debug_assert_eq!(lats.len(), rows * cols);
    debug_assert_eq!(lons.len(), rows * cols);
    let cos_lat0 = lat0.to_radians().cos();
    let mut distances: Vec<f64> = Vec::with_capacity(rows * cols);
    for row in 0..rows {
        for col in 0..cols {
            let index = row * cols + col;
            let la = lats[index];
            let lo = lons[index];
            let lat2r = la.to_radians();
            let dlat = (la - lat0).to_radians();
            let dlon = (lo - lon0).to_radians();
            let s_lat = (dlat / 2.0).sin();
            let s_lon = (dlon / 2.0).sin();
            let a = s_lat * s_lat + cos_lat0 * lat2r.cos() * s_lon * s_lon;
            distances.push(2.0 * EARTH_RADIUS_KM * a.clamp(0.0, 1.0).sqrt().asin());
        }
    }
    let flat = arg_min(&distances);
    let iy = flat / cols;
    let ix = flat % cols;
    let index = iy * cols + ix;
    Some(((iy, ix), lats[index], lons[index]))
}

#[pyfunction]
fn era5_select_nearest_grid_point_2d(
    lats: PyReadonlyArray2<'_, f64>,
    lons: PyReadonlyArray2<'_, f64>,
    lat0: f64,
    lon0: f64,
) -> PyResult<((usize, usize), f64, f64)> {
    // ``as_array`` handles non-contiguous sliced views cleanly.
    let lats_view = lats.as_array();
    let lons_view = lons.as_array();
    let shape = lats_view.dim();
    if lons_view.dim() != shape {
        return Err(PyValueError::new_err(format!(
            "lats and lons must have the same shape ({shape:?} != {:?})",
            lons_view.dim()
        )));
    }
    let lats_values: Vec<f64> = lats_view.iter().copied().collect();
    let lons_values: Vec<f64> = lons_view.iter().copied().collect();
    select_nearest_grid_point_2d_core(&lats_values, &lons_values, shape, lat0, lon0)
        .ok_or_else(|| PyValueError::new_err("lats and lons must be non-empty arrays"))
}

// ---------------------------------------------------------------------------
// Dewpoint conversions
// ---------------------------------------------------------------------------

/// Port of ``era5_extract.dewpoint_from_rh``: dewpoint (deg C) from
/// temperature (deg C) and relative humidity (%).  Uses the Magnus-Tetens
/// approximation; NaNs propagate as NaN so the caller can mark the level
/// missing.
pub(crate) fn dewpoint_from_rh_core(tmpc: &[f64], rh: &[f64]) -> Vec<f64> {
    tmpc.iter()
        .zip(rh.iter())
        .map(|(&t, &r)| {
            // ``np.clip`` semantics: NaN stays NaN, finite values clamp.
            let rh_clamped = r.clamp(1e-3, 100.0);
            let gamma = (rh_clamped / 100.0).ln() + (MAGNUS_A * t) / (MAGNUS_B + t);
            (MAGNUS_B * gamma) / (MAGNUS_A - gamma)
        })
        .collect()
}

#[pyfunction]
fn era5_dewpoint_from_rh<'py>(
    py: Python<'py>,
    tmpc: PyReadonlyArray1<'py, f64>,
    rh: PyReadonlyArray1<'py, f64>,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let tmpc_values = slice_or_vec(&tmpc);
    let rh_values = slice_or_vec(&rh);
    if tmpc_values.len() != rh_values.len() {
        return Err(PyValueError::new_err(format!(
            "tmpc and rh must have the same length ({} != {})",
            tmpc_values.len(),
            rh_values.len()
        )));
    }
    Ok(PyArray1::from_vec(
        py,
        dewpoint_from_rh_core(&tmpc_values, &rh_values),
    ))
}

/// Port of ``era5_extract.dewpoint_from_specific_humidity``: dewpoint (deg C)
/// from specific humidity (kg/kg) and pressure (hPa).
pub(crate) fn dewpoint_from_specific_humidity_core(q: &[f64], pres_hpa: &[f64]) -> Vec<f64> {
    q.iter()
        .zip(pres_hpa.iter())
        .map(|(&qv, &p)| {
            // Vapor pressure (hPa) from specific humidity.
            let e = (qv * p) / (0.622 + 0.378 * qv);
            // ``np.clip(e, 1e-6, None)``: NaN stays NaN, else the lower bound.
            let e = if e.is_nan() { e } else { e.max(1e-6) };
            let ln = (e / SATURATION_VAPOR_PRESSURE_HPA).ln();
            (MAGNUS_B * ln) / (MAGNUS_A - ln)
        })
        .collect()
}

#[pyfunction]
fn era5_dewpoint_from_specific_humidity<'py>(
    py: Python<'py>,
    q: PyReadonlyArray1<'py, f64>,
    pres_hpa: PyReadonlyArray1<'py, f64>,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let q_values = slice_or_vec(&q);
    let pres_values = slice_or_vec(&pres_hpa);
    if q_values.len() != pres_values.len() {
        return Err(PyValueError::new_err(format!(
            "q and pres_hpa must have the same length ({} != {})",
            q_values.len(),
            pres_values.len()
        )));
    }
    Ok(PyArray1::from_vec(
        py,
        dewpoint_from_specific_humidity_core(&q_values, &pres_values),
    ))
}

// ---------------------------------------------------------------------------
// Surface relative vorticity
// ---------------------------------------------------------------------------

/// Ascending comparison with NaN sorting last, matching ``np.argsort``.
fn f64_cmp_nan_last(a: f64, b: f64) -> std::cmp::Ordering {
    match (a.is_nan(), b.is_nan()) {
        (true, true) => std::cmp::Ordering::Equal,
        (true, false) => std::cmp::Ordering::Greater,
        (false, true) => std::cmp::Ordering::Less,
        (false, false) => a.partial_cmp(&b).unwrap_or(std::cmp::Ordering::Equal),
    }
}

/// Port of ``era5_extract._surface_relative_vorticity_from_column``: the
/// bottom-most relative-vorticity value from a level column.  With
/// ``absolute`` set the input is absolute vorticity and the Coriolis term is
/// removed before the lookup; with ``surface_pressure`` given, levels below
/// the verified ground cannot be used.  A pressure level exactly coincident
/// with the ground is still a valid near-surface sample.
pub(crate) fn surface_relative_vorticity_core(
    values: &[f64],
    levels: &[f64],
    latitude: Option<f64>,
    absolute: bool,
    surface_pressure: Option<f64>,
    missing: f64,
) -> Option<f64> {
    if values.is_empty() || values.len() != levels.len() {
        return None;
    }
    let column: Vec<f64> = if absolute {
        let latitude = latitude?;
        let coriolis = 2.0 * EARTH_ROTATION_RATE * latitude.to_radians().sin();
        values.iter().map(|&value| value - coriolis).collect()
    } else {
        values.to_vec()
    };
    // Bottom-up order: descending pressure level, matching np.argsort(-lev).
    // NaN levels sort last and ties keep their original order.
    let mut order: Vec<usize> = (0..levels.len()).collect();
    order.sort_by(|&a, &b| f64_cmp_nan_last(-levels[a], -levels[b]));
    let mut ordered: Vec<f64> = Vec::with_capacity(order.len());
    for &index in &order {
        if let Some(sp) = surface_pressure {
            // NumPy semantics: a NaN level compares false and is dropped.
            if levels[index].is_nan() || levels[index] > sp {
                continue;
            }
        }
        let value = column[index];
        ordered.push(if value.is_finite() { value } else { missing });
    }
    ordered
        .iter()
        .copied()
        .find(|value| value.is_finite() && *value != missing)
}

#[pyfunction]
#[pyo3(signature = (values, levels, latitude = None, absolute = false, surface_pressure = None, missing = -9999.0))]
fn era5_surface_relative_vorticity(
    values: PyReadonlyArray1<'_, f64>,
    levels: PyReadonlyArray1<'_, f64>,
    latitude: Option<f64>,
    absolute: bool,
    surface_pressure: Option<f64>,
    missing: f64,
) -> PyResult<Option<f64>> {
    let values_col = slice_or_vec(&values);
    let levels_col = slice_or_vec(&levels);
    Ok(surface_relative_vorticity_core(
        &values_col,
        &levels_col,
        latitude,
        absolute,
        surface_pressure,
        missing,
    ))
}

// ---------------------------------------------------------------------------
// Neighbour spacing
// ---------------------------------------------------------------------------

/// Signed longitude delta from ``lon1`` to ``lon2`` in degrees.
fn wrapped_lon_delta(lon1: f64, lon2: f64) -> f64 {
    ((lon2 - lon1 + 180.0).rem_euclid(360.0)) - 180.0
}

/// Port of ``era5_extract._east_west_distance_m``: approximate signed
/// east-west distance between two longitudes at a latitude, in metres.
#[pyfunction]
fn era5_east_west_distance_m(lat: f64, lon1: f64, lon2: f64) -> f64 {
    EARTH_RADIUS_M * lat.to_radians().cos() * wrapped_lon_delta(lon1, lon2).to_radians()
}

/// Port of ``era5_extract._north_south_distance_m``: approximate signed
/// north-south distance between two latitudes, in metres.
#[pyfunction]
fn era5_north_south_distance_m(lat1: f64, lat2: f64) -> f64 {
    EARTH_RADIUS_M * (lat2 - lat1).to_radians()
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(era5_great_circle_distance_km, module)?)?;
    module.add_function(wrap_pyfunction!(era5_select_nearest_grid_point_1d, module)?)?;
    module.add_function(wrap_pyfunction!(era5_select_nearest_grid_point_2d, module)?)?;
    module.add_function(wrap_pyfunction!(era5_dewpoint_from_rh, module)?)?;
    module.add_function(wrap_pyfunction!(
        era5_dewpoint_from_specific_humidity,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(era5_surface_relative_vorticity, module)?)?;
    module.add_function(wrap_pyfunction!(era5_east_west_distance_m, module)?)?;
    module.add_function(wrap_pyfunction!(era5_north_south_distance_m, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn great_circle_distance_matches_reference() {
        let d = great_circle_distance_km_core(0.0, 0.0, &[0.0], &[1.0]);
        assert_eq!(d.len(), 1);
        assert!((d[0] - 111.195_08).abs() < 1.0e-3);
        // A two-degree hop across the antimeridian.
        let d = great_circle_distance_km_core(0.0, 179.0, &[0.0], &[-179.0]);
        assert!((d[0] - 222.390_17).abs() < 1.0e-2);
        // Antipodal points clamp to half the circumference: pi * R.
        let d = great_circle_distance_km_core(0.0, 0.0, &[0.0], &[180.0]);
        assert!((d[0] - std::f64::consts::PI * EARTH_RADIUS_KM).abs() < 1.0e-9);
        // Zero distance at the identical point.
        assert_eq!(
            great_circle_distance_km_core(35.0, -97.0, &[35.0], &[-97.0])[0],
            0.0
        );
    }

    #[test]
    fn great_circle_distance_propagates_nan() {
        let d = great_circle_distance_km_core(0.0, 0.0, &[f64::NAN, 1.0], &[1.0, f64::NAN]);
        assert!(d[0].is_nan());
        assert!(d[1].is_nan());
    }

    #[test]
    fn select_1d_picks_nearest_axes_independently() {
        let lats = [-10.0, 0.0, 10.0];
        let lons = [-10.0, 0.0, 10.0];
        assert_eq!(
            select_nearest_grid_point_1d_core(&lats, &lons, 1.0, 1.0),
            Some(((1, 1), 0.0, 0.0))
        );
        assert_eq!(
            select_nearest_grid_point_1d_core(&lats, &lons, -8.0, 9.0),
            Some(((0, 2), -10.0, 10.0))
        );
    }

    #[test]
    fn select_1d_first_maximum_wins_on_ties() {
        // Identical coordinates tie on both axes; the first index must win,
        // exactly like np.argmax.
        let lats = [10.0, 10.0];
        let lons = [-5.0, -5.0];
        assert_eq!(
            select_nearest_grid_point_1d_core(&lats, &lons, 0.0, 0.0),
            Some(((0, 0), 10.0, -5.0))
        );
    }

    #[test]
    fn select_1d_first_nan_wins_like_np_argmax() {
        let lats = [f64::NAN, 0.0];
        let lons = [0.0, 0.0];
        assert_eq!(
            select_nearest_grid_point_1d_core(&lats, &lons, 0.0, 0.0)
                .unwrap()
                .0,
            (0, 0)
        );
    }

    #[test]
    fn select_1d_rejects_empty_axes() {
        assert_eq!(
            select_nearest_grid_point_1d_core(&[], &[0.0], 0.0, 0.0),
            None
        );
        assert_eq!(
            select_nearest_grid_point_1d_core(&[0.0], &[], 0.0, 0.0),
            None
        );
    }

    #[test]
    fn select_2d_picks_row_major_nearest_point() {
        let lats = [0.0, 0.0, 10.0, 10.0];
        let lons = [0.0, 10.0, 0.0, 10.0];
        assert_eq!(
            select_nearest_grid_point_2d_core(&lats, &lons, (2, 2), 1.0, 1.0),
            Some(((0, 0), 0.0, 0.0))
        );
        assert_eq!(
            select_nearest_grid_point_2d_core(&lats, &lons, (2, 2), 9.0, 9.0),
            Some(((1, 1), 10.0, 10.0))
        );
    }

    #[test]
    fn select_2d_first_minimum_wins_on_row_major_ties() {
        // Identical cells tie exactly; row-major order makes the first cell
        // the winner, exactly like np.argmin + unravel_index.
        let lats = [5.0, 5.0, 5.0, 5.0];
        let lons = [5.0, 5.0, 5.0, 5.0];
        assert_eq!(
            select_nearest_grid_point_2d_core(&lats, &lons, (2, 2), 5.0, 5.0),
            Some(((0, 0), 5.0, 5.0))
        );
    }

    #[test]
    fn select_2d_agrees_with_separable_search_on_a_product_grid() {
        let lats_axis = [-10.0, 0.0, 10.0];
        let lons_axis = [-10.0, 0.0, 10.0];
        let mut lats2d = Vec::with_capacity(9);
        let mut lons2d = Vec::with_capacity(9);
        for &lat in &lats_axis {
            for &lon in &lons_axis {
                lats2d.push(lat);
                lons2d.push(lon);
            }
        }
        let from_1d = select_nearest_grid_point_1d_core(&lats_axis, &lons_axis, 1.2, -0.7);
        let from_2d = select_nearest_grid_point_2d_core(&lats2d, &lons2d, (3, 3), 1.2, -0.7);
        assert_eq!(from_1d, from_2d);
        assert_eq!(from_2d, Some(((1, 1), 0.0, 0.0)));
    }

    #[test]
    fn dewpoint_from_rh_matches_magnus_reference() {
        let d = dewpoint_from_rh_core(&[20.0], &[50.0]);
        assert!((d[0] - 9.261_2).abs() < 1.0e-2);
        // 100% relative humidity round-trips the temperature.
        let d = dewpoint_from_rh_core(&[20.0], &[100.0]);
        assert!((d[0] - 20.007).abs() < 1.0e-2);
    }

    #[test]
    fn dewpoint_from_rh_clamps_and_propagates_nan() {
        // Out-of-range relative humidity is clipped to [1e-3, 100] first.
        let clamped_low = dewpoint_from_rh_core(&[20.0], &[f64::NEG_INFINITY]);
        let at_floor = dewpoint_from_rh_core(&[20.0], &[1e-3]);
        assert_eq!(clamped_low[0], at_floor[0]);
        let clamped_high = dewpoint_from_rh_core(&[20.0], &[250.0]);
        let at_ceiling = dewpoint_from_rh_core(&[20.0], &[100.0]);
        assert_eq!(clamped_high[0], at_ceiling[0]);
        // NaN humidity stays NaN so the caller can mark the level missing.
        let d = dewpoint_from_rh_core(&[20.0], &[f64::NAN]);
        assert!(d[0].is_nan());
    }

    #[test]
    fn dewpoint_from_specific_humidity_matches_reference() {
        // 7.6 g/kg at 1000 hPa is roughly 12.16 hPa of vapor pressure,
        // which is a dewpoint near 9.9 deg C.
        let d = dewpoint_from_specific_humidity_core(&[0.007_6], &[1000.0]);
        assert!((d[0] - 9.869_5).abs() < 2.0e-2);
        // Dry or non-physical humidity floors at the 1e-6 hPa vapor
        // pressure lower bound instead of going through the logarithm
        // with a negative value.
        let d = dewpoint_from_specific_humidity_core(&[0.0], &[1000.0]);
        let floored = dewpoint_from_specific_humidity_core(&[-0.5], &[1000.0]);
        assert!((d[0] - floored[0]).abs() < 1.0e-12);
        // NaN inputs keep NaN, mirroring np.clip(e, 1e-6, None).
        let d = dewpoint_from_specific_humidity_core(&[f64::NAN], &[1000.0]);
        assert!(d[0].is_nan());
    }

    #[test]
    fn surface_vorticity_returns_bottom_most_level() {
        // Highest pressure first, regardless of the order stored.
        assert_eq!(
            surface_relative_vorticity_core(
                &[1e-5, 2e-5, 3e-5],
                &[1000.0, 850.0, 700.0],
                None,
                false,
                None,
                -9999.0,
            ),
            Some(1e-5)
        );
        assert_eq!(
            surface_relative_vorticity_core(
                &[3e-5, 1e-5, 2e-5],
                &[700.0, 1000.0, 850.0],
                None,
                false,
                None,
                -9999.0,
            ),
            Some(1e-5)
        );
    }

    #[test]
    fn surface_vorticity_respects_surface_pressure() {
        // Levels below the verified ground are dropped; a level exactly at
        // the ground is still a valid near-surface sample.
        assert_eq!(
            surface_relative_vorticity_core(
                &[1e-5, 2e-5, 3e-5],
                &[1000.0, 850.0, 700.0],
                None,
                false,
                Some(800.0),
                -9999.0,
            ),
            Some(3e-5)
        );
        assert_eq!(
            surface_relative_vorticity_core(
                &[1e-5, 2e-5, 3e-5],
                &[1000.0, 850.0, 700.0],
                None,
                false,
                Some(900.0),
                -9999.0,
            ),
            Some(2e-5)
        );
        assert_eq!(
            surface_relative_vorticity_core(
                &[1e-5, 2e-5],
                &[1000.0, 850.0],
                None,
                false,
                Some(1000.0),
                -9999.0,
            ),
            Some(1e-5)
        );
    }

    #[test]
    fn surface_vorticity_marks_non_finite_and_allows_sentinel_equality() {
        // A non-finite value becomes the sentinel and is skipped.
        assert_eq!(
            surface_relative_vorticity_core(
                &[f64::NAN, 2e-5, 3e-5],
                &[1000.0, 850.0, 700.0],
                None,
                false,
                None,
                -9999.0,
            ),
            Some(2e-5)
        );
        // A genuine value equal to the sentinel is treated as missing too.
        assert_eq!(
            surface_relative_vorticity_core(
                &[-9999.0, 2e-5],
                &[1000.0, 850.0],
                None,
                false,
                None,
                -9999.0,
            ),
            Some(2e-5)
        );
        // Nothing usable in the column.
        assert_eq!(
            surface_relative_vorticity_core(
                &[f64::NAN, f64::INFINITY],
                &[1000.0, 850.0],
                None,
                false,
                None,
                -9999.0,
            ),
            None
        );
    }

    #[test]
    fn surface_vorticity_removes_coriolis_for_absolute_input() {
        // The input is absolute vorticity: the bottom-most value has the
        // planetary term 2 * Omega * sin(lat) removed first.
        let d = surface_relative_vorticity_core(
            &[1e-5, 5e-5],
            &[1000.0, 850.0],
            Some(30.0),
            true,
            None,
            -9999.0,
        );
        let coriolis = 2.0 * EARTH_ROTATION_RATE * 30.0_f64.to_radians().sin();
        assert!((d.unwrap() - (1e-5 - coriolis)).abs() < 1.0e-15);
        // At the equator the Coriolis parameter is zero.
        let d = surface_relative_vorticity_core(&[1e-5], &[1000.0], Some(0.0), true, None, -9999.0);
        assert_eq!(d, Some(1e-5));
        // Absolute input without a latitude cannot be converted.
        assert_eq!(
            surface_relative_vorticity_core(&[1e-5], &[1000.0], None, true, None, -9999.0),
            None
        );
    }

    #[test]
    fn surface_vorticity_rejects_misaligned_columns() {
        assert_eq!(
            surface_relative_vorticity_core(&[1e-5], &[1000.0, 850.0], None, false, None, -9999.0),
            None
        );
        assert_eq!(
            surface_relative_vorticity_core(&[], &[], None, false, None, -9999.0),
            None
        );
    }

    #[test]
    fn spacing_helpers_match_reference_distances() {
        // One degree of latitude at the mean Earth radius.
        let ns = era5_north_south_distance_m(0.0, 1.0);
        assert!((ns - 111_195.08).abs() < 0.5);
        // One degree of longitude at the equator; halved at 60 degrees.
        let ew = era5_east_west_distance_m(0.0, 0.0, 1.0);
        assert!((ew - 111_195.08).abs() < 0.5);
        let ew = era5_east_west_distance_m(60.0, 0.0, 1.0);
        assert!((ew - 55_597.54).abs() < 0.5);
        // Longitude wrap-around keeps a two-degree hop across the
        // antimeridian short, and carries the sign of the direction.
        let ew = era5_east_west_distance_m(0.0, 179.0, -179.0);
        assert!((ew - 222_390.16).abs() < 1.0);
        let ew = era5_east_west_distance_m(0.0, -179.0, 179.0);
        assert!((ew + 222_390.16).abs() < 1.0);
        // The north-south distance carries its sign as well.
        let ns = era5_north_south_distance_m(1.0, 0.0);
        assert!((ns + 111_195.08).abs() < 0.5);
    }

    #[test]
    fn arg_extremes_match_numpy_tie_and_nan_rules() {
        // First occurrence wins on ties.
        assert_eq!(arg_max(&[1.0, 2.0, 2.0, 1.5]), 1);
        assert_eq!(arg_min(&[2.0, 1.0, 1.0, 1.5]), 1);
        // The first NaN displaces everything, in either direction.
        assert_eq!(arg_max(&[1.0, f64::NAN, 2.0]), 1);
        assert_eq!(arg_min(&[5.0, f64::NAN, 0.0]), 1);
        assert_eq!(arg_max(&[f64::NAN, 1.0]), 0);
    }
}

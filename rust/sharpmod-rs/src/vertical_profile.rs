//! Native vertical-profile helpers for SharpTab (milestone M19).
//!
//! Rust ports of three Python helpers so profile/timeline readers can resolve
//! levels without re-entering Python between calls:
//!
//! * [`isotherm_pres_and_hght`] mirrors
//!   ``sharpmod.sharptab.interp._isotherm_pres_and_hght``.
//! * [`prepare_vertical_interpolator`] and [`NativeVerticalInterpolator`]
//!   mirror ``sharpmod.sharptab.vertical_interpolation.prepare_vertical_interpolator``.
//! * [`native_six_km_cape_eligible`] mirrors
//!   ``sharpmod.sharptab.params._native_six_km_cape_eligible``.
//!
//! Behaviour matches the Python sources: missing data propagates as ``None``
//! or ``false`` rather than raising; only shape errors raise ``ValueError``.

use numpy::PyReadonlyArray1;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::borrow::Cow;

fn slice_or_vec<'a>(arr: &'a PyReadonlyArray1<'_, f64>) -> Cow<'a, [f64]> {
    match arr.as_slice() {
        Ok(slice) => Cow::Borrowed(slice),
        Err(_) => Cow::Owned(arr.as_array().iter().copied().collect()),
    }
}

/// Resolve `(pressure hPa, height m MSL)` of the first `temp_c` isotherm crossing.
///
/// Levels whose temperature, log-pressure, or height entry is non-finite are
/// dropped before the scan, matching the Python masked-array filtering. Order
/// of operations matches the Python source exactly: exact reported-level hits
/// win, then the "never crossed" guard, then the first bracketing pair with
/// linear interpolation of both `log10(pressure)` and height against
/// temperature.
pub(crate) fn compute_isotherm_pres_and_hght(
    tmpc: &[f64],
    logp: &[f64],
    hght: &[f64],
    temp_c: f64,
) -> Result<Option<(f64, f64)>, String> {
    if tmpc.len() != logp.len() || tmpc.len() != hght.len() {
        return Err("tmpc, logp, and hght columns must have matching lengths".to_string());
    }

    let mut temps: Vec<f64> = Vec::with_capacity(tmpc.len());
    let mut logs: Vec<f64> = Vec::with_capacity(tmpc.len());
    let mut heights: Vec<f64> = Vec::with_capacity(tmpc.len());
    for ((&t, &l), &h) in tmpc.iter().zip(logp.iter()).zip(hght.iter()) {
        if t.is_finite() && l.is_finite() && h.is_finite() {
            temps.push(t);
            logs.push(l);
            heights.push(h);
        }
    }
    if temps.len() < 2 {
        return Ok(None);
    }

    let diff: Vec<f64> = temps.iter().map(|&value| value - temp_c).collect();

    // Exact hit on a reported level: return it directly (first occurrence).
    for (i, &delta) in diff.iter().enumerate() {
        if delta == 0.0 {
            return Ok(Some((10.0_f64.powf(logs[i]), heights[i])));
        }
    }

    // Isotherm never crossed: all temperatures strictly above or strictly
    // below (a non-finite target makes every delta NaN, so it lands here too).
    if !temp_c.is_finite()
        || diff.iter().all(|&delta| delta > 0.0)
        || diff.iter().all(|&delta| delta < 0.0)
    {
        return Ok(None);
    }

    // First index whose bracketing pair straddles the isotherm.
    let Some(i) = diff
        .windows(2)
        .position(|window| window[0] * window[1] < 0.0)
    else {
        return Ok(None);
    };

    // np.interp against [t_next, t_curr]: with exactly two points the result
    // is the straight line through them regardless of the x ordering, so one
    // linear formula covers both the normal and the inverted crossing.
    let t_curr = temps[i];
    let t_next = temps[i + 1];
    let weight = (temp_c - t_next) / (t_curr - t_next);
    let logp_i = logs[i + 1] + weight * (logs[i] - logs[i + 1]);
    let hght_i = heights[i + 1] + weight * (heights[i] - heights[i + 1]);
    Ok(Some((10.0_f64.powf(logp_i), hght_i)))
}

/// Column interpolator prepared once and reused for repeated scalar reads.
#[pyclass(frozen)]
pub struct NativeVerticalInterpolator {
    x: Vec<f64>,
    y: Vec<f64>,
    max_gap_m: f64,
}

impl NativeVerticalInterpolator {
    /// Interpolate to `target` with no extrapolation and a maximum-gap rule.
    fn evaluate(&self, target: f64) -> Option<f64> {
        if !target.is_finite() || self.x.is_empty() {
            return None;
        }
        // First neighbour within np.isclose(target) semantics (rtol 1e-5,
        // atol 0.01) short-circuits as an exact match.
        let tolerance = 0.01 + 1e-5 * target.abs();
        let first_close = self.x.partition_point(|&xi| xi < target - tolerance);
        if first_close < self.x.len() && (self.x[first_close] - target).abs() <= tolerance {
            return Some(self.y[first_close]);
        }
        let upper = self.x.partition_point(|&xi| xi <= target);
        if upper == 0 || upper >= self.x.len() {
            return None;
        }
        let lower = upper - 1;
        if self.x[upper] - self.x[lower] > self.max_gap_m {
            return None;
        }
        let weight = (target - self.x[lower]) / (self.x[upper] - self.x[lower]);
        Some(self.y[lower] + weight * (self.y[upper] - self.y[lower]))
    }
}

#[pymethods]
impl NativeVerticalInterpolator {
    fn __call__(&self, target: f64) -> Option<f64> {
        self.evaluate(target)
    }
}

/// Build a reusable interpolator over finite, sorted, deduplicated heights.
pub(crate) fn compute_prepare_interpolator(
    heights: &[f64],
    values: &[f64],
    max_gap_m: f64,
) -> NativeVerticalInterpolator {
    let size = heights.len().min(values.len());
    let mut pairs: Vec<(f64, f64)> = heights[..size]
        .iter()
        .zip(&values[..size])
        .filter(|(&h, v): &(&f64, &f64)| h.is_finite() && v.is_finite())
        .map(|(&h, &v)| (h, v))
        .collect();
    // Stable ascending order so duplicate heights keep the first value seen.
    pairs.sort_by(|a, b| a.0.total_cmp(&b.0));
    pairs.dedup_by(|a, b| a.0 == b.0);
    let x = pairs.iter().map(|pair| pair.0).collect();
    let y = pairs.iter().map(|pair| pair.1).collect();
    NativeVerticalInterpolator { x, y, max_gap_m }
}

/// Resolve a Python-style index (negatives allowed); `None` when out of bounds.
fn resolve_index(len: usize, index: isize) -> Option<usize> {
    let n = len as isize;
    let resolved = if index < 0 {
        index.saturating_add(n)
    } else {
        index
    };
    if (0..n).contains(&resolved) {
        Some(resolved as usize)
    } else {
        None
    }
}

/// Whether reported-level spacing between the surface and 6 km AGL is dense
/// enough for the cached native 6CAPE integral.
pub(crate) fn compute_native_six_km_cape_eligible(
    hght: &[f64],
    sfc: isize,
    max_gap_m: f64,
) -> bool {
    let n = hght.len();
    if n < 2 {
        return false;
    }
    let Some(idx) = resolve_index(n, sfc) else {
        return false;
    };
    let sfc_h = hght[idx];
    if !sfc_h.is_finite() {
        return false;
    }
    let mut levels: Vec<f64> = Vec::with_capacity(n + 2);
    levels.push(0.0);
    for &h in hght {
        let agl = h - sfc_h;
        if agl.is_finite() && (0.0..=6000.0).contains(&agl) {
            levels.push(agl);
        }
    }
    levels.push(6000.0);
    levels.sort_by(|a, b| a.total_cmp(b));
    levels.dedup_by(|a, b| (*a - *b) == 0.0);
    levels.len() >= 2
        && levels
            .windows(2)
            .all(|window| (window[1] - window[0]) <= max_gap_m)
}

/// Pressure (hPa) and height (m MSL) of the first `temp_c` isotherm crossing.
///
/// Returns `None` when the isotherm is never crossed or the profile has
/// fewer than two usable levels.
#[pyfunction]
fn isotherm_pres_and_hght(
    tmpc: PyReadonlyArray1<'_, f64>,
    logp: PyReadonlyArray1<'_, f64>,
    hght: PyReadonlyArray1<'_, f64>,
    temp_c: f64,
) -> PyResult<Option<(f64, f64)>> {
    let tmpc_s = slice_or_vec(&tmpc);
    let logp_s = slice_or_vec(&logp);
    let hght_s = slice_or_vec(&hght);
    compute_isotherm_pres_and_hght(&tmpc_s, &logp_s, &hght_s, temp_c).map_err(PyValueError::new_err)
}

/// Prepare a reusable vertical interpolator over cleaned height columns.
#[pyfunction]
fn prepare_vertical_interpolator(
    heights: PyReadonlyArray1<'_, f64>,
    values: PyReadonlyArray1<'_, f64>,
    max_gap_m: f64,
) -> NativeVerticalInterpolator {
    let heights_s = slice_or_vec(&heights);
    let values_s = slice_or_vec(&values);
    compute_prepare_interpolator(&heights_s, &values_s, max_gap_m)
}

/// Whether the 0-6 km AGL reported levels are dense enough for cached 6CAPE.
#[pyfunction]
fn native_six_km_cape_eligible(
    hght: PyReadonlyArray1<'_, f64>,
    sfc: isize,
    max_gap_m: f64,
) -> bool {
    let hght_s = slice_or_vec(&hght);
    compute_native_six_km_cape_eligible(&hght_s, sfc, max_gap_m)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(isotherm_pres_and_hght, module)?)?;
    module.add_function(wrap_pyfunction!(prepare_vertical_interpolator, module)?)?;
    module.add_class::<NativeVerticalInterpolator>()?;
    module.add_function(wrap_pyfunction!(native_six_km_cape_eligible, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn close(a: f64, b: f64) -> bool {
        (a - b).abs() <= 1e-9
    }

    #[test]
    fn isotherm_exact_level_returns_reported_values() {
        let out = compute_isotherm_pres_and_hght(
            &[20.0, 10.0, 0.0],
            &[3.0, 2.0, 1.0],
            &[0.0, 100.0, 200.0],
            10.0,
        )
        .unwrap();
        assert_eq!(out, Some((100.0, 100.0)));
    }

    #[test]
    fn isotherm_exact_level_uses_first_occurrence() {
        let out = compute_isotherm_pres_and_hght(
            &[10.0, 10.0, 5.0],
            &[2.0, 1.9, 1.8],
            &[0.0, 50.0, 100.0],
            10.0,
        )
        .unwrap();
        assert_eq!(out, Some((100.0, 0.0)));
    }

    #[test]
    fn isotherm_bracketed_crossing_interpolates_linearly() {
        let (pres, hgt) =
            compute_isotherm_pres_and_hght(&[20.0, 10.0], &[3.0, 2.0], &[0.0, 100.0], 15.0)
                .unwrap()
                .unwrap();
        assert!(close(pres, 316.22776601683796));
        assert!(close(hgt, 50.0));
    }

    #[test]
    fn isotherm_inversion_crossing_matches_np_interp_line() {
        // temperature rises through the isotherm: np.interp still evaluates
        // the two-point line, and so does the Rust port.
        let (pres, hgt) =
            compute_isotherm_pres_and_hght(&[20.0, 30.0], &[3.0, 2.0], &[0.0, 100.0], 25.0)
                .unwrap()
                .unwrap();
        assert!(close(pres, 316.22776601683796));
        assert!(close(hgt, 50.0));
    }

    #[test]
    fn isotherm_drops_nonfinite_rows_before_scanning() {
        let (pres, hgt) = compute_isotherm_pres_and_hght(
            &[20.0, f64::NAN, 10.0],
            &[2.9, 2.5, 2.0],
            &[10.0, 30.0, 110.0],
            15.0,
        )
        .unwrap()
        .unwrap();
        assert!(close(pres, 281.8382931264454));
        assert!(close(hgt, 60.0));
    }

    #[test]
    fn isotherm_returns_none_when_unreachable() {
        // Never crossed: every temperature above the isotherm.
        assert_eq!(
            compute_isotherm_pres_and_hght(&[30.0, 20.0], &[3.0, 2.0], &[0.0, 100.0], 10.0)
                .unwrap(),
            None
        );
        // Never crossed: every temperature below the isotherm.
        assert_eq!(
            compute_isotherm_pres_and_hght(&[5.0, -5.0], &[3.0, 2.0], &[0.0, 100.0], 10.0).unwrap(),
            None
        );
        // Non-finite target is never crossed.
        assert_eq!(
            compute_isotherm_pres_and_hght(&[20.0, 10.0], &[3.0, 2.0], &[0.0, 100.0], f64::NAN)
                .unwrap(),
            None
        );
        // Fewer than two usable levels.
        assert_eq!(
            compute_isotherm_pres_and_hght(&[20.0, f64::NAN], &[3.0, 2.5], &[0.0, 50.0], 10.0)
                .unwrap(),
            None
        );
    }

    #[test]
    fn isotherm_rejects_mismatched_lengths() {
        let err = compute_isotherm_pres_and_hght(&[1.0, 2.0], &[1.0], &[1.0], 0.0).unwrap_err();
        assert_eq!(
            err,
            "tmpc, logp, and hght columns must have matching lengths"
        );
    }

    #[test]
    fn interpolator_sorts_deduplicates_and_interpolates() {
        let interp = compute_prepare_interpolator(
            &[1000.0, 500.0, 1000.0, f64::NAN],
            &[1.0, 2.0, 3.0, 9.0],
            600.0,
        );
        // duplicate height keeps the first value seen, NaN rows drop out.
        assert_eq!(interp.evaluate(500.0), Some(2.0));
        assert_eq!(interp.evaluate(1000.0), Some(1.0));
        assert_eq!(interp.evaluate(750.0), Some(1.5));
        assert_eq!(interp.evaluate(400.0), None);
        assert_eq!(interp.evaluate(1250.0), None);
        assert_eq!(interp.evaluate(f64::NAN), None);
    }

    #[test]
    fn interpolator_neighbour_tolerance_matches_np_isclose() {
        let interp = compute_prepare_interpolator(&[1000.0, 2000.0], &[5.0, 7.0], 100.0);
        // tolerance = 0.01 + 1e-5 * |1000.01| ~= 0.02: the first point matches.
        assert_eq!(interp.evaluate(1000.01), Some(5.0));
        // 0.1 away is beyond tolerance: falls through to interpolation.
        assert_eq!(interp.evaluate(1000.10), None);
    }

    #[test]
    fn interpolator_enforces_maximum_gap() {
        let strict = compute_prepare_interpolator(&[500.0, 1000.0], &[2.0, 1.0], 499.0);
        assert_eq!(strict.evaluate(750.0), None);
        // A gap exactly at the limit is allowed: the check is strict >.
        let at_limit = compute_prepare_interpolator(&[500.0, 1000.0], &[2.0, 1.0], 500.0);
        assert_eq!(at_limit.evaluate(750.0), Some(1.5));
    }

    #[test]
    fn interpolator_trims_to_shorter_column_and_handles_empty() {
        let trimmed = compute_prepare_interpolator(&[0.0, 10.0], &[3.0], 10.0);
        assert_eq!(trimmed.evaluate(0.0), Some(3.0));
        assert_eq!(trimmed.evaluate(10.0), None);
        let empty = compute_prepare_interpolator(&[f64::NAN, f64::NAN], &[1.0, 2.0], 10.0);
        assert_eq!(empty.evaluate(0.0), None);
    }

    #[test]
    fn six_km_eligible_accepts_dense_column() {
        let heights: Vec<f64> = (0..=13).map(|k| 500.0 * k as f64).collect();
        // sfc = 1 -> AGL levels 0..6000 in 500 m steps.
        assert!(compute_native_six_km_cape_eligible(&heights, 1, 500.0));
        // sfc = 0 with the levels folded to 0..6500: the >6000 tail is out.
        assert!(compute_native_six_km_cape_eligible(&heights, 0, 500.0));
    }

    #[test]
    fn six_km_rejects_interior_and_final_gaps() {
        let sparse = vec![0.0, 1500.0, 3000.0, 4500.0, 6000.0];
        assert!(!compute_native_six_km_cape_eligible(&sparse, 0, 500.0));
        // Column stops at 5000 m: the interpolated 6-km boundary leaves a
        // 1000 m final gap that must be caught.
        let mut short_column: Vec<f64> = (0..=10).map(|k| 500.0 * k as f64).collect();
        short_column.push(f64::NAN);
        assert!(!compute_native_six_km_cape_eligible(
            &short_column,
            0,
            500.0
        ));
        // A surface-only column has one 6000 m gap.
        let surface_only = vec![0.0, f64::NAN];
        assert!(!compute_native_six_km_cape_eligible(
            &surface_only,
            0,
            500.0
        ));
    }

    #[test]
    fn six_km_resolves_surface_index_like_python() {
        let column: Vec<f64> = (0..12).map(|k| 500.0 * k as f64 + 100.0).collect();
        // -1 selects the final level; with the surface at the top the layer
        // collapses to [0, 6000] and the gap check fails.
        assert!(!compute_native_six_km_cape_eligible(&column, -1, 500.0));
        // Out-of-bounds indices (positive and negative) are rejected.
        assert!(!compute_native_six_km_cape_eligible(&column, 12, 500.0));
        assert!(!compute_native_six_km_cape_eligible(&column, -13, 500.0));
        // Non-finite surface height rejects the whole check.
        let masked_surface = vec![f64::NAN, 100.0, 200.0, 3000.0];
        assert!(!compute_native_six_km_cape_eligible(
            &masked_surface,
            0,
            500.0
        ));
        // Fewer than two levels cannot define a layer.
        assert!(!compute_native_six_km_cape_eligible(&[100.0], 0, 500.0));
    }
}

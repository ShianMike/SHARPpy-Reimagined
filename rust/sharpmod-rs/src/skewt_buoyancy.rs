//! Skew-T buoyancy / hail-growth-zone geometry and hover-inset readouts
//! (milestone M29).
//!
//! Rust mirrors of the numeric cores behind four `sharpmod.viz` overlays:
//!
//! * [`skew_hgz_band_pixels`] mirrors the pixel-band resolution of
//!   `sharpmod.viz.skew.HGZOverlay.band_pixels` (Requirements 19.9-19.11):
//!   the hgz-CAPE/isotherm gates, the `pres_to_pix` transform wrapped in the
//!   Python `except (TypeError, ValueError)`, the NaN guard, and the clamp
//!   of the -10 C..-30 C band to the plot rectangle.
//! * [`skew_interp_env_on_parcel`] mirrors the `np.interp(p_p, e_p[argsort],
//!   e_tv[argsort], left=nan, right=nan)` interpolation step of
//!   `sharpmod.viz.skew.draw_cape_fill`, including NumPy's
//!   duplicate-sample and exact-endpoint semantics.
//! * [`skew_cape_fill_polygons`] mirrors the CAPE/CIN polygon bodies of
//!   `draw_cape_fill`: per-segment quads coloured by `(d0 + d1) > 0` and
//!   crossings split in two triangles at the LFC/EL envelope intersection.
//! * [`inset_thetae_readout`], [`inset_srwinds_readout`],
//!   [`inset_slinky_nearest_height`], and [`inset_readout_box_rect`] mirror
//!   the hover-readout helpers of `sharpmod.viz.inset_readout`
//!   (`thetae_readout_lines`, `srwinds_readout_lines`,
//!   `slinky_readout_lines`, and the `_draw_readout_box` rect layout).
//!
//! Missing data propagates, never crashes: every gate the Python sources
//! express results in `None`/empty output rather than an exception, and
//! array inputs follow the `np.ma.filled(..., np.nan)` fill convention of
//! `sharpmod.sharptab.interp` (non-finite entries are dropped like masked
//! ones).  Only shape errors raise `ValueError`, mirroring NumPy's
//! broadcasting errors.

use crate::board_formatting::py_fixed;
use numpy::PyReadonlyArray1;
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use std::borrow::Cow;

// ---------------------------------------------------------------------------
// Small Python-semantics helpers
// ---------------------------------------------------------------------------

/// `min`/`max` of two floats with CPython's NaN behaviour (the first
/// operand survives when the comparison is false).
fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

/// Borrowed view of a `PyReadonlyArray1`, falling back to a copied vector
/// for non-contiguous inputs (the `vertical_profile` convention).
fn slice_or_vec<'a>(arr: &'a PyReadonlyArray1<'_, f64>) -> Cow<'a, [f64]> {
    match arr.as_slice() {
        Ok(slice) => Cow::Borrowed(slice),
        Err(_) => Cow::Owned(arr.as_array().iter().copied().collect()),
    }
}

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

/// `is_missing`-style gate: `None`, the NumPy masked constant, and anything
/// whose `float()` is missing or non-finite count as unavailable (the
/// `viz_colors` boundary convention for
/// `sharpmod.sharptab.constants.is_missing`).
fn missing_value(py: Python<'_>, value: &Bound<'_, PyAny>) -> bool {
    if value.is_none() {
        return true;
    }
    if masked_constant(value) {
        return true;
    }
    match py_float(py, value) {
        Some(number) => !number.is_finite(),
        None => true,
    }
}

// ---------------------------------------------------------------------------
// np.interp mirror
// ---------------------------------------------------------------------------

/// `np.interp(x, xp, fp, left, right)` over ascending sample points.
///
/// Mirrors NumPy's exact semantics: `left`/`right` apply strictly outside
/// the sample range (querying exactly `xp[0]`/`xp[-1]` returns `fp[0]` /
/// `fp[-1]`, even with explicit `left`/`right`), a NaN query yields NaN, and
/// duplicated sample values interpolate from the last duplicate.  Interior
/// evaluation follows NumPy's operation order (`slope` first) so results
/// are bit-identical.
fn np_interp(x: f64, xp: &[f64], fp: &[f64], left: f64, right: f64) -> f64 {
    if xp.is_empty() || x.is_nan() {
        return f64::NAN;
    }
    if x < xp[0] {
        return left;
    }
    if x > xp[xp.len() - 1] {
        return right;
    }
    // j = np.searchsorted(xp, x, side="right"): every sample <= x.
    let j = xp.partition_point(|&sample| sample <= x);
    if j == xp.len() {
        return fp[fp.len() - 1];
    }
    let slope = (fp[j] - fp[j - 1]) / (xp[j] - xp[j - 1]);
    fp[j - 1] + slope * (x - xp[j - 1])
}

/// Sort `(pressure, value)` pairs ascending with a stable order
/// (`np.argsort(..., kind="stable")`); duplicate pressures keep the first
/// value seen.
fn sort_by_pressure(points: &mut [(f64, f64)]) {
    points.sort_by(|a, b| a.0.total_cmp(&b.0));
}

// ---------------------------------------------------------------------------
// Pure cores
// ---------------------------------------------------------------------------

/// `HGZOverlay.band_pixels` core: map the warm/cold isotherm pixels onto
/// the clamped `(top, bottom)` band for the plot rectangle.
///
/// `None` (draw nothing) when either pixel is NaN -- the Python `y == y`
/// guard -- or when the clamped band is empty or degenerate
/// (`bottom <= top`).
pub(crate) fn hgz_band_pixels_core(
    y_warm: f64,
    y_cold: f64,
    rect_top: f64,
    rect_bottom: f64,
) -> Option<(f64, f64)> {
    if y_warm.is_nan() || y_cold.is_nan() {
        return None;
    }
    let y_top = py_min(y_warm, y_cold);
    let y_bottom = py_max(y_warm, y_cold);
    // Requirement 19.10: clamp the band to the plot rectangle's extent.
    let top = py_max(y_top, rect_top);
    let bottom = py_min(y_bottom, rect_bottom);
    if bottom <= top {
        return None;
    }
    Some((top, bottom))
}

/// `draw_cape_fill` environment interpolation:
/// `np.interp(parcel_p, env_p[argsort], env_tv[argsort], nan, nan)`.
///
/// `Err` mirrors the shape errors NumPy raises for mismatched sample
/// arrays; parcel pressures outside the sounding become NaN so their
/// segments are skipped rather than clamped.
pub(crate) fn env_on_parcel_core(
    parcel_p: &[f64],
    env_p: &[f64],
    env_tv: &[f64],
) -> Result<Vec<f64>, String> {
    if env_p.len() != env_tv.len() {
        return Err("fp and xp are not of the same length.".to_string());
    }
    if env_p.is_empty() {
        return Err("array of sample points is empty".to_string());
    }
    let mut samples: Vec<(f64, f64)> = env_p
        .iter()
        .zip(env_tv.iter())
        .map(|(&pressure, &temperature)| (pressure, temperature))
        .collect();
    sort_by_pressure(&mut samples);
    let xp: Vec<f64> = samples.iter().map(|pair| pair.0).collect();
    let fp: Vec<f64> = samples.iter().map(|pair| pair.1).collect();
    Ok(parcel_p
        .iter()
        .map(|&pressure| np_interp(pressure, &xp, &fp, f64::NAN, f64::NAN))
        .collect())
}

/// One CAPE/CIN polygon: `(positive buoyancy, vertices in plot order)`.
/// Triangle pairs mark a parcel/environment crossing split
/// (`d0 * d1 < 0`).
pub(crate) type BuoyancyPolygon = (bool, Vec<(f64, f64)>);

/// `draw_cape_fill` polygon core: split the parcel-versus-environment area
/// per level-segment.
///
/// Segments whose interpolated environment is non-finite are skipped, as
/// are zero-height slices (`d0 == 0 && d1 == 0`); a single sign (or a lone
/// zero endpoint) renders one quad coloured by `(d0 + d1) > 0`, and a sign
/// change inside the segment splits into two triangles at the crossing
/// fraction `f = d0 / (d0 - d1)`.  Fewer than two valid levels fills
/// nothing.
pub(crate) fn cape_fill_polygons_core(
    parcel_tv: &[f64],
    env_on_parcel: &[f64],
    x_par: &[f64],
    x_env: &[f64],
    y: &[f64],
) -> Vec<BuoyancyPolygon> {
    let valid: Vec<bool> = env_on_parcel
        .iter()
        .map(|value| value.is_finite())
        .collect();
    if env_on_parcel
        .iter()
        .filter(|value| value.is_finite())
        .count()
        < 2
    {
        return Vec::new();
    }
    let mut polygons: Vec<BuoyancyPolygon> = Vec::new();
    for k in 0..env_on_parcel.len().saturating_sub(1) {
        if !valid[k] || !valid[k + 1] {
            continue;
        }
        let d0 = parcel_tv[k] - env_on_parcel[k];
        let d1 = parcel_tv[k + 1] - env_on_parcel[k + 1];
        if d0 == 0.0 && d1 == 0.0 {
            continue;
        }
        if d0 * d1 >= 0.0 {
            // Whole segment has one sign; a lone zero endpoint inherits its
            // neighbour's colour via (d0 + d1).
            polygons.push((
                (d0 + d1) > 0.0,
                vec![
                    (x_par[k], y[k]),
                    (x_par[k + 1], y[k + 1]),
                    (x_env[k + 1], y[k + 1]),
                    (x_env[k], y[k]),
                ],
            ));
        } else {
            // Sign change: split at the crossing fraction.
            let f = d0 / (d0 - d1);
            let ym = y[k] + f * (y[k + 1] - y[k]);
            let xm = x_par[k] + f * (x_par[k + 1] - x_par[k]);
            polygons.push((d0 > 0.0, vec![(x_par[k], y[k]), (xm, ym), (x_env[k], y[k])]));
            polygons.push((
                d1 > 0.0,
                vec![(xm, ym), (x_par[k + 1], y[k + 1]), (x_env[k + 1], y[k + 1])],
            ));
        }
    }
    polygons
}

/// `thetae_readout_lines` core.  `pres`/`thetae` arrive pre-filled with NaN
/// for masked entries (the `interp` fill convention); pairs where either
/// entry is non-finite are dropped exactly like the NumPy mask union.
#[allow(clippy::too_many_arguments)]
pub(crate) fn thetae_readout_core(
    x: f64,
    y: f64,
    tlx: f64,
    brx: f64,
    tpad: f64,
    bry: f64,
    pmax: f64,
    pmin: f64,
    pres: &[f64],
    thetae: &[f64],
) -> Result<Option<Vec<String>>, String> {
    if tlx <= x && x <= brx && tpad <= y && y <= bry {
        let span = bry - tpad;
        if span <= 0.0 {
            return Ok(None);
        }
        let p = pmax - ((bry - y) / span) * (pmax - pmin);
        if p.is_finite() && p > 0.0 {
            if pres.len() != thetae.len() {
                return Err("operands could not be broadcast together".to_string());
            }
            let mut levels: Vec<(f64, f64)> = pres
                .iter()
                .zip(thetae.iter())
                .filter(|(&pressure, &thte)| pressure.is_finite() && thte.is_finite())
                .map(|(&pressure, &thte)| (pressure, thte))
                .collect();
            if levels.len() < 2 {
                return Ok(None);
            }
            sort_by_pressure(&mut levels);
            let xp: Vec<f64> = levels.iter().map(|pair| pair.0).collect();
            let fp: Vec<f64> = levels.iter().map(|pair| pair.1).collect();
            // Default np.interp clamping to the profile ends.
            let thte_val = np_interp(p, &xp, &fp, fp[0], fp[fp.len() - 1]);
            return Ok(Some(vec![
                format!("{} hPa", py_fixed(p, 0)),
                format!("\u{3b8}e {} K", py_fixed(thte_val, 0)),
            ]));
        }
    }
    Ok(None)
}

/// `srwinds_readout_lines` core.  `sfc_h` is `None` when the surface-height
/// read failed (the Python `try/except` around `prof.hght[prof.sfc]`);
/// `sru`/`srv`/`hght` arrive NaN-filled, and triples with a non-finite
/// member are dropped like the mask union.
#[allow(clippy::too_many_arguments)]
pub(crate) fn srwinds_readout_core(
    x: f64,
    y: f64,
    tlx: f64,
    brx: f64,
    tpad: f64,
    bry: f64,
    hmax: f64,
    hmin: f64,
    sfc_h: Option<f64>,
    sru: &[f64],
    srv: &[f64],
    hght: &[f64],
) -> Result<Option<Vec<String>>, String> {
    let Some(sfc) = sfc_h else {
        return Ok(None);
    };
    if tlx <= x && x <= brx && tpad <= y && y <= bry {
        let span = bry - tpad;
        if span > 0.0 {
            let h = ((bry - 2.0 - y) / span) * (hmax - hmin) - hmin;
            if h.is_finite() {
                let h = py_max(h, 0.0);
                if sru.len() != srv.len() || sru.len() != hght.len() {
                    return Err("operands could not be broadcast together".to_string());
                }
                let mut levels: Vec<(f64, f64)> = sru
                    .iter()
                    .zip(srv.iter())
                    .zip(hght.iter())
                    .filter(|((&u, &v), &height)| {
                        u.is_finite() && v.is_finite() && height.is_finite()
                    })
                    .map(|((&u, &v), &height)| (height - sfc, u.hypot(v)))
                    .collect();
                if levels.len() < 2 {
                    return Ok(None);
                }
                levels.sort_by(|a, b| a.0.total_cmp(&b.0));
                let xp: Vec<f64> = levels.iter().map(|pair| pair.0).collect();
                let fp: Vec<f64> = levels.iter().map(|pair| pair.1).collect();
                let spd_val = np_interp(h * 1000.0, &xp, &fp, fp[0], fp[fp.len() - 1]);
                return Ok(Some(vec![
                    format!("{} km", py_fixed(h, 1)),
                    format!("{} kt", py_fixed(spd_val, 0)),
                ]));
            }
        }
    }
    Ok(None)
}

/// `slinky_readout_lines` selection core over pre-mapped candidates
/// `(pixel_x, pixel_y, trajectory z or missing)`.
///
/// The strict comparison keeps the first ring on ties, exactly like the
/// Python loop, and a winning height that is missing or non-finite rejects
/// the whole readout (the `np.isfinite(best_z)` gate).
pub(crate) fn slinky_nearest_height_core(
    candidates: &[(f64, f64, Option<f64>)],
    x: f64,
    y: f64,
    hit_radius: f64,
) -> Option<f64> {
    let mut best_d2 = hit_radius * hit_radius;
    let mut best_z: Option<f64> = None;
    for &(px, py, z) in candidates {
        let d2 = (px - x) * (px - x) + (py - y) * (py - y);
        if d2 < best_d2 {
            best_d2 = d2;
            best_z = z;
        }
    }
    match best_z {
        Some(z) if z.is_finite() => Some(z),
        _ => None,
    }
}

/// `_draw_readout_box` rect layout:
/// `(bx, by, box_w, box_h)` for a label box preferring up-and-to-the-right
/// of the cursor, flipped/clamped to stay inside the widget.
pub(crate) fn readout_box_rect_core(
    cx: f64,
    cy: f64,
    text_w: f64,
    line_h: f64,
    line_count: usize,
    widget_w: f64,
    widget_h: f64,
) -> (f64, f64, f64, f64) {
    let pad = 4.0;
    let box_w = text_w + 2.0 * pad;
    let box_h = line_h * line_count as f64 + 2.0 * pad;
    let mut bx = cx + 12.0;
    let mut by = cy - box_h - 6.0;
    if bx + box_w > widget_w {
        bx = cx - box_w - 12.0;
    }
    bx = py_max(0.0, py_min(bx, widget_w - box_w));
    if by < 0.0 {
        by = cy + 12.0;
    }
    by = py_max(0.0, py_min(by, widget_h - box_h));
    (bx, by, box_w, box_h)
}

// ---------------------------------------------------------------------------
// PyFunctions
// ---------------------------------------------------------------------------

/// `float(pres_to_pix(float(pressure)))` with the Python exception gate:
/// `Ok(None)` for a `TypeError`/`ValueError`, other errors propagated.
fn pixel_or_none(
    py: Python<'_>,
    pressure: &Bound<'_, PyAny>,
    pres_to_pix: &Bound<'_, PyAny>,
) -> PyResult<Option<f64>> {
    let outcome: PyResult<f64> = (|| {
        let float_fn = builtin_float(py)
            .ok_or_else(|| PyTypeError::new_err("builtins float is unavailable"))?;
        let pressure = float_fn.call1((pressure,))?;
        let pixel = pres_to_pix.call1((pressure,))?;
        float_fn.call1((pixel,))?.extract::<f64>()
    })();
    match outcome {
        Ok(value) => Ok(Some(value)),
        Err(err) => {
            if err.is_instance_of::<PyTypeError>(py) || err.is_instance_of::<PyValueError>(py) {
                Ok(None)
            } else {
                Err(err)
            }
        }
    }
}

/// Hail-growth-zone band pixels for the plot rectangle
/// (`HGZOverlay.band_pixels`).
///
/// `hgz_cape`, `p_warm`, and `p_cold` accept raw profile values (`None`,
/// the masked constant, NaN, and non-coercible input all read as missing
/// and draw nothing); `pres_to_pix` maps a pressure (hPa) to the vertical
/// pixel.  A `TypeError`/`ValueError` from the transform or the conversions
/// is swallowed exactly like the Python `except` clause; anything else
/// propagates.  Returns the clamped `(top, bottom)` band or `None`.
#[pyfunction]
#[pyo3(signature = (hgz_cape, p_warm, p_cold, pres_to_pix, rect_top, rect_bottom))]
fn skew_hgz_band_pixels(
    py: Python<'_>,
    hgz_cape: &Bound<'_, PyAny>,
    p_warm: &Bound<'_, PyAny>,
    p_cold: &Bound<'_, PyAny>,
    pres_to_pix: &Bound<'_, PyAny>,
    rect_top: f64,
    rect_bottom: f64,
) -> PyResult<Option<(f64, f64)>> {
    // 19.11: missing hgz_cape, or unresolved -10/-30 C isotherms -> none.
    if missing_value(py, hgz_cape) || missing_value(py, p_warm) || missing_value(py, p_cold) {
        return Ok(None);
    }
    // y_warm/y_cold = float(pres_to_pix(float(p))) behind the except gate.
    let y_warm = pixel_or_none(py, p_warm, pres_to_pix)?;
    let y_cold = pixel_or_none(py, p_cold, pres_to_pix)?;
    match (y_warm, y_cold) {
        (Some(y_warm), Some(y_cold)) => {
            Ok(hgz_band_pixels_core(y_warm, y_cold, rect_top, rect_bottom))
        }
        _ => Ok(None),
    }
}

/// Interpolate the environment temperature onto the parcel pressure grid
/// (`draw_cape_fill`'s `np.interp(..., left=nan, right=nan)` step).
///
/// The environment entries are sorted by pressure (stable, like
/// `np.argsort`); parcel levels outside the sounding interpolate to NaN so
/// the caller skips their segments.  Mismatched environment lengths or an
/// empty environment raise `ValueError` like NumPy.
#[pyfunction]
fn skew_interp_env_on_parcel(
    parcel_p: PyReadonlyArray1<'_, f64>,
    env_p: PyReadonlyArray1<'_, f64>,
    env_tv: PyReadonlyArray1<'_, f64>,
) -> PyResult<Vec<f64>> {
    let parcel_p = slice_or_vec(&parcel_p);
    let env_p = slice_or_vec(&env_p);
    let env_tv = slice_or_vec(&env_tv);
    env_on_parcel_core(&parcel_p, &env_p, &env_tv).map_err(PyValueError::new_err)
}

/// CAPE/CIN shading polygons between the parcel and environment traces
/// (`draw_cape_fill`).
///
/// Inputs are the aligned level arrays: parcel virtual temperature, the
/// interpolated environment virtual temperature (from
/// [`skew_interp_env_on_parcel`], NaN outside the sounding), and the pixel
/// coordinates `x_par`/`x_env`/`y` produced by the widget transforms.  The
/// return value carries `(is_positive_buoyancy, [(x, y), ...])` per filled
/// polygon in segment order -- one quad for a whole-segment sign, two
/// triangles for a parcel/environment crossing -- and an empty list when
/// nothing fills.
#[pyfunction]
fn skew_cape_fill_polygons(
    parcel_tv: PyReadonlyArray1<'_, f64>,
    env_on_parcel: PyReadonlyArray1<'_, f64>,
    x_par: PyReadonlyArray1<'_, f64>,
    x_env: PyReadonlyArray1<'_, f64>,
    y: PyReadonlyArray1<'_, f64>,
) -> PyResult<Vec<BuoyancyPolygon>> {
    let parcel_tv = slice_or_vec(&parcel_tv);
    let env_on_parcel = slice_or_vec(&env_on_parcel);
    let x_par = slice_or_vec(&x_par);
    let x_env = slice_or_vec(&x_env);
    let y = slice_or_vec(&y);
    if parcel_tv.len() != env_on_parcel.len()
        || parcel_tv.len() != x_par.len()
        || parcel_tv.len() != x_env.len()
        || parcel_tv.len() != y.len()
    {
        return Err(PyValueError::new_err(
            "cape fill columns must have matching lengths",
        ));
    }
    Ok(cape_fill_polygons_core(
        &parcel_tv,
        &env_on_parcel,
        &x_par,
        &x_env,
        &y,
    ))
}

/// Theta-E inset hover readout
/// (`inset_readout.thetae_readout_lines`) for the vendored `plotThetae`
/// widget geometry.
///
/// Returns `["NNN hPa", "e NNN K"]` (the cursor pressure and the interpolated
/// theta-e in K) or `None` when the cursor sits outside the frame, the
/// pressure axis is degenerate, the cursor pressure is non-positive, or
/// fewer than two usable levels remain.  `pres`/`thetae` follow the
/// NaN-fill convention for masked entries.
#[allow(clippy::too_many_arguments)]
#[pyfunction]
#[pyo3(signature = (x, y, tlx, brx, tpad, bry, pmax, pmin, pres, thetae))]
fn inset_thetae_readout(
    x: f64,
    y: f64,
    tlx: f64,
    brx: f64,
    tpad: f64,
    bry: f64,
    pmax: f64,
    pmin: f64,
    pres: PyReadonlyArray1<'_, f64>,
    thetae: PyReadonlyArray1<'_, f64>,
) -> PyResult<Option<Vec<String>>> {
    let pres = slice_or_vec(&pres);
    let thetae = slice_or_vec(&thetae);
    thetae_readout_core(x, y, tlx, brx, tpad, bry, pmax, pmin, &pres, &thetae)
        .map_err(PyValueError::new_err)
}

/// Storm-relative-wind inset hover readout
/// (`inset_readout.srwinds_readout_lines`) for the vendored `plotWinds`
/// widget geometry.
///
/// `sfc_h` carries the surface height (m MSL) of the profile and is `None`
/// when the read failed.  Returns `["N.N km", "NN kt"]` (height AGL and
/// storm-relative speed) under the same missing-data rules as the Python
/// helper; `sru`/`srv`/`hght` follow the NaN-fill convention.
#[allow(clippy::too_many_arguments)]
#[pyfunction]
#[pyo3(signature = (x, y, tlx, brx, tpad, bry, hmax, hmin, sfc_h, sru, srv, hght))]
fn inset_srwinds_readout(
    x: f64,
    y: f64,
    tlx: f64,
    brx: f64,
    tpad: f64,
    bry: f64,
    hmax: f64,
    hmin: f64,
    sfc_h: Option<f64>,
    sru: PyReadonlyArray1<'_, f64>,
    srv: PyReadonlyArray1<'_, f64>,
    hght: PyReadonlyArray1<'_, f64>,
) -> PyResult<Option<Vec<String>>> {
    let sru = slice_or_vec(&sru);
    let srv = slice_or_vec(&srv);
    let hght = slice_or_vec(&hght);
    srwinds_readout_core(
        x, y, tlx, brx, tpad, bry, hmax, hmin, sfc_h, &sru, &srv, &hght,
    )
    .map_err(PyValueError::new_err)
}

/// Storm Slinky nearest-ring readout
/// (`inset_readout.slinky_readout_lines`).
///
/// `traj` is the widget's `slinky_traj` (an iterable of `(x, y, z)` rows;
/// `None` or the masked constant draws nothing); `xy_to_pix` maps a
/// trajectory point to widget pixels.  Points whose coordinates are not
/// finite are skipped, exactly like the QC gate, and the strict distance
/// comparison keeps the first ring on ties.  Returns `["N.N km"]` with the
/// nearest ring's height (m) inside the hit radius (default 16.0 pixels,
/// `inset_readout._SLINKY_HIT_RADIUS`), or `None`.
#[pyfunction]
#[pyo3(signature = (x, y, traj, xy_to_pix, hit_radius = 16.0))]
fn inset_slinky_nearest_height(
    py: Python<'_>,
    x: f64,
    y: f64,
    traj: &Bound<'_, PyAny>,
    xy_to_pix: &Bound<'_, PyAny>,
    hit_radius: f64,
) -> PyResult<Option<Vec<String>>> {
    if traj.is_none() || masked_constant(traj) {
        return Ok(None);
    }
    let Ok(iter) = traj.try_iter() else {
        // The paint handler catches every exception raised by the Python
        // helper, so a non-iterable trajectory reads as "no readout" too.
        return Ok(None);
    };
    let mut candidates: Vec<(f64, f64, Option<f64>)> = Vec::new();
    for point in iter {
        // tx, ty, tz = pt[0], pt[1], pt[2]: an unpacking failure aborts the
        // readout exactly like an exception does through the paint handler.
        let Ok(point) = point else {
            return Ok(None);
        };
        let (Ok(tx_item), Ok(ty_item), Ok(tz_item)) =
            (point.get_item(0), point.get_item(1), point.get_item(2))
        else {
            return Ok(None);
        };
        // QC gate: non-finite coordinates never participate.
        let (Some(tx), Some(ty)) = (py_float(py, &tx_item), py_float(py, &ty_item)) else {
            continue;
        };
        if !tx.is_finite() || !ty.is_finite() {
            continue;
        }
        let Ok(pixel) = xy_to_pix.call1((tx, ty)) else {
            return Ok(None);
        };
        let (Ok(pixel_x), Ok(pixel_y)) = (pixel.get_item(0), pixel.get_item(1)) else {
            return Ok(None);
        };
        let (Some(pix_x), Some(pix_y)) = (py_float(py, &pixel_x), py_float(py, &pixel_y)) else {
            return Ok(None);
        };
        candidates.push((pix_x, pix_y, py_float(py, &tz_item)));
    }
    match slinky_nearest_height_core(&candidates, x, y, hit_radius) {
        Some(z) => Ok(Some(vec![format!("{} km", py_fixed(z / 1000.0, 1))])),
        None => Ok(None),
    }
}

/// Hover readout box rect layout (`inset_readout._draw_readout_box`):
/// `(bx, by, box_w, box_h)` for `line_count` lines at the measured
/// `text_w`/`line_h` metrics, kept inside the `widget_w` x `widget_h`
/// frame.
#[pyfunction]
#[pyo3(signature = (cx, cy, text_w, line_h, line_count, widget_w, widget_h))]
fn inset_readout_box_rect(
    cx: f64,
    cy: f64,
    text_w: f64,
    line_h: f64,
    line_count: usize,
    widget_w: f64,
    widget_h: f64,
) -> (f64, f64, f64, f64) {
    readout_box_rect_core(cx, cy, text_w, line_h, line_count, widget_w, widget_h)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(skew_hgz_band_pixels, module)?)?;
    module.add_function(wrap_pyfunction!(skew_interp_env_on_parcel, module)?)?;
    module.add_function(wrap_pyfunction!(skew_cape_fill_polygons, module)?)?;
    module.add_function(wrap_pyfunction!(inset_thetae_readout, module)?)?;
    module.add_function(wrap_pyfunction!(inset_srwinds_readout, module)?)?;
    module.add_function(wrap_pyfunction!(inset_slinky_nearest_height, module)?)?;
    module.add_function(wrap_pyfunction!(inset_readout_box_rect, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hgz_band_clamps_and_guards() {
        // Straightforward interior band.
        assert_eq!(
            hgz_band_pixels_core(100.0, 200.0, 50.0, 430.0),
            Some((100.0, 200.0))
        );
        // The cold isotherm can map above the warm one: order is irrelevant.
        assert_eq!(
            hgz_band_pixels_core(200.0, 100.0, 50.0, 430.0),
            Some((100.0, 200.0))
        );
        // Band partially outside the plot area clamps to the rectangle.
        assert_eq!(
            hgz_band_pixels_core(10.0, 200.0, 50.0, 430.0),
            Some((50.0, 200.0))
        );
        assert_eq!(
            hgz_band_pixels_core(10.0, 480.0, 50.0, 430.0),
            Some((50.0, 430.0))
        );
        // Failing the y == y NaN guard draws nothing.
        assert_eq!(hgz_band_pixels_core(f64::NAN, 100.0, 50.0, 430.0), None);
        assert_eq!(hgz_band_pixels_core(100.0, f64::NAN, 50.0, 430.0), None);
        // Band fully outside the plot area (or degenerate) is rejected.
        assert_eq!(hgz_band_pixels_core(10.0, 20.0, 50.0, 430.0), None);
        assert_eq!(hgz_band_pixels_core(500.0, 600.0, 50.0, 430.0), None);
        assert_eq!(hgz_band_pixels_core(100.0, 100.0, 50.0, 430.0), None);
        // Touching the clamp boundary exactly is still degenerate.
        assert_eq!(hgz_band_pixels_core(0.0, 50.0, 50.0, 430.0), None);
    }

    #[test]
    fn np_interp_mirrors_numpy_semantics() {
        let xp = [0.0, 1.0, 1.0, 2.0];
        let fp = [0.0, 10.0, 20.0, 30.0];
        // Interior linear interpolation.
        assert_eq!(np_interp(0.5, &xp, &fp, 0.0, 99.0), 5.0);
        // A duplicate sample interpolates from its last duplicate.
        assert_eq!(np_interp(1.0, &xp, &fp, 0.0, 99.0), 20.0);
        assert_eq!(np_interp(1.5, &xp, &fp, 0.0, 99.0), 25.0);
        // Exact endpoint hits return the sample value, never the fill.
        assert_eq!(np_interp(0.0, &xp, &fp, f64::NAN, f64::NAN), 0.0);
        assert_eq!(np_interp(2.0, &xp, &fp, f64::NAN, f64::NAN), 30.0);
        // Strictly outside: the explicit left/right values.
        assert!(np_interp(-0.5, &xp, &fp, f64::NAN, f64::NAN).is_nan());
        assert!(np_interp(2.5, &xp, &fp, f64::NAN, f64::NAN).is_nan());
        // Default clamping keeps the profile-end values.
        assert_eq!(np_interp(-1.0, &xp, &fp, fp[0], 30.0), 0.0);
        assert_eq!(np_interp(9.0, &xp, &fp, 0.0, fp[3]), 30.0);
        // A single-sample array resolves to that value from both sides,
        // matching the fp[0]/fp[-1] end clamps the readout cores pass.
        assert_eq!(np_interp(0.0, &[1.0], &[7.5], 7.5, 7.5), 7.5);
        assert_eq!(np_interp(2.0, &[1.0], &[7.5], 7.5, 7.5), 7.5);
        assert_eq!(np_interp(1.0, &[1.0], &[7.5], 7.5, 7.5), 7.5);
        // NaN queries yield NaN.
        assert!(np_interp(f64::NAN, &xp, &fp, 0.0, 30.0).is_nan());
        // An all-duplicate sample resolves to the last duplicate's value.
        assert_eq!(
            np_interp(1.0, &[1.0, 1.0, 1.0], &[7.0, 8.0, 9.0], 0.0, 0.0),
            9.0
        );
    }

    #[test]
    fn env_on_parcel_interpolates_sorted_samples() {
        // Unsorted environment input still interpolates on the sorted axis;
        // parcel levels beyond the sounding read NaN (never clamp).
        let out = env_on_parcel_core(&[800.0, 200.0], &[500.0, 1000.0], &[10.0, 20.0]).unwrap();
        assert_eq!(out.len(), 2);
        assert_eq!(out[0], 16.0);
        assert!(out[1].is_nan());
        // Exact endpoint level returns the environment value.
        let exact = env_on_parcel_core(&[500.0], &[500.0, 1000.0], &[10.0, 20.0]).unwrap();
        assert_eq!(exact[0], 10.0);
        // Duplicated environment pressures resolve to the last duplicate.
        let dup =
            env_on_parcel_core(&[1.0], &[0.0, 1.0, 1.0, 2.0], &[0.0, 10.0, 20.0, 30.0]).unwrap();
        assert_eq!(dup[0], 20.0);
        // Empty parcel grid is a valid empty result.
        let empty = env_on_parcel_core(&[], &[500.0, 1000.0], &[10.0, 20.0]).unwrap();
        assert!(empty.is_empty());
        // NaN parcel queries stay NaN.
        let nan_query = env_on_parcel_core(&[f64::NAN], &[500.0, 1000.0], &[10.0, 20.0]).unwrap();
        assert!(nan_query[0].is_nan());
        // Shape errors mirror numpy.
        assert_eq!(
            env_on_parcel_core(&[1.0], &[1.0, 2.0], &[1.0]).unwrap_err(),
            "fp and xp are not of the same length."
        );
        assert!(env_on_parcel_core(&[1.0], &[], &[]).is_err());
    }

    #[test]
    fn cape_fill_same_sign_segments_make_quads() {
        let polygons = cape_fill_polygons_core(
            &[3.0, 4.0],
            &[2.0, 2.0],
            &[0.0, 10.0],
            &[2.0, 12.0],
            &[100.0, 50.0],
        );
        assert_eq!(polygons.len(), 1);
        assert!(polygons[0].0);
        assert_eq!(
            polygons[0].1,
            vec![(0.0, 100.0), (10.0, 50.0), (12.0, 50.0), (2.0, 100.0)]
        );
        // Negative buoyancy flips the sign flag with identical geometry.
        let cold = cape_fill_polygons_core(
            &[2.0, 2.0],
            &[3.0, 4.0],
            &[0.0, 10.0],
            &[2.0, 12.0],
            &[100.0, 50.0],
        );
        assert_eq!(cold.len(), 1);
        assert!(!cold[0].0);
    }

    #[test]
    fn cape_fill_splits_crossings_in_two_triangles() {
        // dt = p_tv - env: [+2, -1] -> crossing at f = 2/3.
        let polygons = cape_fill_polygons_core(
            &[4.0, 3.0],
            &[2.0, 4.0],
            &[0.0, 10.0],
            &[2.0, 12.0],
            &[100.0, 50.0],
        );
        assert_eq!(polygons.len(), 2);
        let f = 2.0 / 3.0;
        let xm = 0.0 + f * 10.0;
        let ym = 100.0 + f * (50.0 - 100.0);
        assert!(polygons[0].0);
        assert_eq!(polygons[0].1, vec![(0.0, 100.0), (xm, ym), (2.0, 100.0)]);
        assert!(!polygons[1].0);
        assert_eq!(polygons[1].1, vec![(xm, ym), (10.0, 50.0), (12.0, 50.0)]);
        assert_eq!(xm, 6.666_666_666_666_666);
        assert_eq!(ym, 66.66666666666667);
    }

    #[test]
    fn cape_fill_skips_invalid_and_zero_slices() {
        // Segments joined to an out-of-sounding level are skipped entirely;
        // zero-endpoint slices inherent their neighbour's sign.
        let skipped = cape_fill_polygons_core(
            &[3.0, 4.0, 5.0],
            &[2.0, 4.0, f64::NAN],
            &[0.0, 10.0, 20.0],
            &[2.0, 12.0, 22.0],
            &[100.0, 50.0, 10.0],
        );
        assert_eq!(skipped.len(), 1);
        assert!(skipped[0].0);
        assert_eq!(skipped[0].1[0], (0.0, 100.0));
        // Zero/zero endpoints are a zero-height slice: nothing drawn.
        let flat = cape_fill_polygons_core(
            &[2.0, 2.0],
            &[2.0, 2.0],
            &[0.0, 10.0],
            &[2.0, 12.0],
            &[100.0, 50.0],
        );
        assert!(flat.is_empty());
        // A single zero endpoint keeps the neighbour's colour.
        let zero_positive = cape_fill_polygons_core(
            &[2.0, 7.0],
            &[2.0, 4.0],
            &[0.0, 10.0],
            &[2.0, 12.0],
            &[100.0, 50.0],
        );
        assert_eq!(zero_positive.len(), 1);
        assert!(zero_positive[0].0);
        let zero_negative = cape_fill_polygons_core(
            &[2.0, 1.0],
            &[2.0, 4.0],
            &[0.0, 10.0],
            &[2.0, 12.0],
            &[100.0, 50.0],
        );
        assert_eq!(zero_negative.len(), 1);
        assert!(!zero_negative[0].0);
        // Fewer than two valid environment levels fill nothing.
        let starved = cape_fill_polygons_core(
            &[3.0, 4.0],
            &[f64::NAN, 2.0],
            &[0.0, 10.0],
            &[2.0, 12.0],
            &[100.0, 50.0],
        );
        assert!(starved.is_empty());
    }

    /// In-frame geometry helper with the standard test axis:
    /// tlx 10, brx 300, tpad 0, bry 200, pmax 1000, pmin 100.
    fn thetae_lines(
        x: f64,
        y: f64,
        pres: &[f64],
        thte: &[f64],
    ) -> Result<Option<Vec<String>>, String> {
        thetae_readout_core(x, y, 10.0, 300.0, 0.0, 200.0, 1000.0, 100.0, pres, thte)
    }

    #[test]
    fn thetae_readout_matches_the_reference_lines() {
        // y=100 reads p = 550 hPa; theta-e interpolates to 321 K.
        let lines = thetae_lines(100.0, 100.0, &[1000.0, 500.0], &[330.0, 320.0])
            .unwrap()
            .unwrap();
        assert_eq!(lines, vec!["550 hPa", "\u{3b8}e 321 K"]);
        // Pressure below the profile range clamps to the end theta-e.
        let clamped = thetae_lines(100.0, 0.0, &[1000.0, 500.0], &[330.0, 320.0])
            .unwrap()
            .unwrap();
        assert_eq!(clamped, vec!["100 hPa", "\u{3b8}e 320 K"]);
    }

    #[test]
    fn thetae_readout_gates_match_python() {
        let pres = [1000.0, 500.0];
        let thte = [330.0, 320.0];
        // Outside the frame: right / below / left / above.
        assert_eq!(thetae_lines(301.0, 100.0, &pres, &thte), Ok(None));
        assert_eq!(thetae_lines(100.0, 201.0, &pres, &thte), Ok(None));
        assert_eq!(thetae_lines(9.0, 100.0, &pres, &thte), Ok(None));
        assert_eq!(thetae_lines(100.0, -1.0, &pres, &thte), Ok(None));
        // Degenerate pressure span.
        assert_eq!(
            thetae_readout_core(
                100.0, 100.0, 10.0, 300.0, 100.0, 99.0, 1000.0, 100.0, &pres, &thte
            ),
            Ok(None)
        );
        // Non-positive cursor pressure: pmax=0 -> p = -fraction*100 <= 0.
        assert_eq!(
            thetae_readout_core(100.0, 100.0, 10.0, 300.0, 0.0, 200.0, 0.0, -100.0, &pres, &thte),
            Ok(None)
        );
        // Non-finite axis bounds reject the readout.
        assert_eq!(
            thetae_readout_core(
                100.0,
                100.0,
                10.0,
                300.0,
                0.0,
                200.0,
                f64::NAN,
                100.0,
                &pres,
                &thte
            ),
            Ok(None)
        );
        // Fewer than two usable levels.
        assert_eq!(
            thetae_lines(100.0, 100.0, &[1000.0, f64::NAN], &[330.0, 320.0]),
            Ok(None)
        );
        // Non-finite pairs are dropped; the valid remainder interpolates.
        let mixed = thetae_lines(
            100.0,
            100.0,
            &[f64::NAN, 1000.0, 500.0],
            &[330.0, 330.0, 320.0],
        )
        .unwrap()
        .unwrap();
        assert_eq!(mixed, vec!["550 hPa", "\u{3b8}e 321 K"]);
        // Shape mismatch errors like the numpy mask union.
        assert!(thetae_lines(100.0, 100.0, &pres, &[330.0]).is_err());
    }

    /// In-frame geometry helper at y=100: h = 0.49 * (hmax - hmin) - hmin.
    fn srwinds_lines(
        sfc_h: Option<f64>,
        sru: &[f64],
        srv: &[f64],
        hght: &[f64],
    ) -> Result<Option<Vec<String>>, String> {
        srwinds_readout_core(
            100.0, 100.0, 10.0, 300.0, 0.0, 200.0, 6.0, 0.0, sfc_h, sru, srv, hght,
        )
    }

    #[test]
    fn srwinds_readout_matches_the_reference_lines() {
        // y=100 reads h = 2.94 km; both bracketing levels blow 5 kt.
        let lines = srwinds_lines(Some(100.0), &[3.0, 4.0], &[4.0, 3.0], &[1000.0, 4000.0])
            .unwrap()
            .unwrap();
        assert_eq!(lines, vec!["2.9 km", "5 kt"]);
        // A below-zero height clamps to 0 km; the speed clamps to the
        // lowest kept level.
        let clamp = srwinds_readout_core(
            200.0,
            200.0,
            10.0,
            300.0,
            0.0,
            200.0,
            6.0,
            0.0,
            Some(100.0),
            &[3.0, 4.0],
            &[4.0, 3.0],
            &[1000.0, 4000.0],
        )
        .unwrap()
        .unwrap();
        assert_eq!(clamp, vec!["0.0 km", "5 kt"]);
    }

    #[test]
    fn srwinds_readout_gates_match_python() {
        let sru = [3.0, 4.0];
        let srv = [4.0, 3.0];
        let hght = [1000.0, 4000.0];
        // Surface-height read failed -> no readout.
        assert_eq!(srwinds_lines(None, &sru, &srv, &hght), Ok(None));
        // Outside the frame.
        assert_eq!(
            srwinds_readout_core(
                400.0,
                100.0,
                10.0,
                300.0,
                0.0,
                200.0,
                6.0,
                0.0,
                Some(100.0),
                &sru,
                &srv,
                &hght,
            ),
            Ok(None)
        );
        // Degenerate span.
        assert_eq!(
            srwinds_readout_core(
                100.0,
                100.0,
                10.0,
                300.0,
                100.0,
                99.0,
                6.0,
                0.0,
                Some(100.0),
                &sru,
                &srv,
                &hght,
            ),
            Ok(None)
        );
        // Non-finite height scale.
        assert_eq!(
            srwinds_readout_core(
                100.0,
                100.0,
                10.0,
                300.0,
                0.0,
                200.0,
                f64::NAN,
                0.0,
                Some(100.0),
                &sru,
                &srv,
                &hght,
            ),
            Ok(None)
        );
        // Fewer than two usable AGL levels.
        assert_eq!(
            srwinds_lines(
                Some(100.0),
                &[3.0, f64::NAN],
                &[4.0, 3.0],
                &[1000.0, f64::NAN]
            ),
            Ok(None)
        );
        // Non-finite triples drop; the remainder still interpolates.
        let mixed = srwinds_lines(
            Some(100.0),
            &[3.0, f64::NAN, 4.0],
            &[4.0, 9.0, 3.0],
            &[1000.0, 2000.0, 4000.0],
        )
        .unwrap()
        .unwrap();
        assert_eq!(mixed, vec!["2.9 km", "5 kt"]);
        // Shape mismatch errors like numpy broadcasting.
        assert!(srwinds_lines(Some(100.0), &sru, &[4.0], &hght).is_err());
    }

    #[test]
    fn slinky_nearest_height_selects_within_radius() {
        // The nearest mapped ring wins; the strict comparison keeps the
        // first of equally-distant rings (0. pixel distance here).
        let candidates = [(10.0, 10.0, Some(3500.0)), (100.0, 100.0, Some(1000.0))];
        assert_eq!(
            slinky_nearest_height_core(&candidates, 10.0, 10.0, 16.0),
            Some(3500.0)
        );
        let tied = [(10.0, 10.0, Some(1000.0)), (10.0, 10.0, Some(2000.0))];
        assert_eq!(
            slinky_nearest_height_core(&tied, 18.0, 10.0, 16.0),
            Some(1000.0)
        );
        // Everything outside the hit radius.
        assert_eq!(
            slinky_nearest_height_core(&candidates, 500.0, 500.0, 16.0),
            None
        );
        // A winning ring with a non-finite height rejects the readout,
        // exactly like the np.isfinite(best_z) gate.
        let bad = [(10.0, 10.0, Some(f64::NAN)), (200.0, 200.0, Some(1500.0))];
        assert_eq!(slinky_nearest_height_core(&bad, 10.0, 10.0, 16.0), None);
        let missing = [(10.0, 10.0, None)];
        assert_eq!(slinky_nearest_height_core(&missing, 10.0, 10.0, 16.0), None);
        // A custom hit radius rescales the search.
        assert_eq!(
            slinky_nearest_height_core(&candidates, 10.0, 10.0, 1.0),
            Some(3500.0)
        );
        assert_eq!(
            slinky_nearest_height_core(&candidates, 10.0, 40.0, 30.0),
            None
        );
        // Height formatting: the winning ring's metres become km with one
        // decimal, exactly like "%.1f km" % (z / 1000.0).
        assert_eq!(format!("{} km", py_fixed(3500.0 / 1000.0, 1)), "3.5 km");
        assert_eq!(format!("{} km", py_fixed(3504.0 / 1000.0, 1)), "3.5 km");
        assert_eq!(format!("{} km", py_fixed(1250.0 / 1000.0, 1)), "1.2 km");
    }

    #[test]
    fn readout_box_rect_prefers_up_right_and_clamps_inside() {
        // (bx, by, box_w, box_h); pad 4, offsets 12/6.
        assert_eq!(
            readout_box_rect_core(100.0, 100.0, 60.0, 12.0, 2, 300.0, 200.0),
            (112.0, 62.0, 68.0, 32.0)
        );
        // Not enough room to the right: flip left of the cursor, clamped
        // inside a widget narrower than the box.
        assert_eq!(
            readout_box_rect_core(100.0, 100.0, 60.0, 12.0, 2, 50.0, 200.0),
            (0.0, 62.0, 68.0, 32.0)
        );
        // Cursor near the top: flip below the cursor.
        assert_eq!(
            readout_box_rect_core(100.0, 10.0, 60.0, 12.0, 2, 300.0, 200.0),
            (112.0, 22.0, 68.0, 32.0)
        );
        // Tight widget height clamps the vertical position.
        assert_eq!(
            readout_box_rect_core(100.0, 190.0, 60.0, 12.0, 2, 300.0, 40.0),
            (112.0, 8.0, 68.0, 32.0)
        );
        // A cursor at the very top flips the box below itself.
        assert_eq!(
            readout_box_rect_core(0.0, 0.0, 40.0, 14.0, 2, 200.0, 100.0),
            (12.0, 12.0, 48.0, 36.0)
        );
        // Three lines grow the box by one line height.
        assert_eq!(
            readout_box_rect_core(100.0, 100.0, 60.0, 12.0, 3, 300.0, 200.0),
            (112.0, 50.0, 68.0, 44.0)
        );
    }
}

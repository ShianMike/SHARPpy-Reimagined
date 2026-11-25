use super::slice_or_vec;
use crate::interpolation::interpolate_1d;
use crate::wind::{components_to_wind, wind_to_components};
use numpy::PyReadonlyArray1;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple};

const MISSING_LIMIT: f64 = -9000.0;

pub(crate) fn compute_transect_column(
    pres: &[f64],
    raw: &[f64],
    wspd: Option<&[f64]>,
    log_ladder: &[f64],
    is_wdir: bool,
) -> Result<Vec<Option<f64>>, String> {
    let blank = || vec![None; log_ladder.len()];
    if pres.len() != raw.len() {
        return Ok(blank());
    }
    if is_wdir {
        let Some(speed) = wspd else {
            return Ok(blank());
        };
        if speed.len() != pres.len() {
            return Ok(blank());
        }
        let mut coords = Vec::new();
        let mut dirs = Vec::new();
        let mut spds = Vec::new();
        for ((&p, &d), &s) in pres.iter().zip(raw.iter()).zip(speed.iter()) {
            if p.is_finite()
                && d.is_finite()
                && s.is_finite()
                && p > 0.0
                && d > MISSING_LIMIT
                && s > MISSING_LIMIT
            {
                coords.push(p.log10());
                dirs.push(d);
                spds.push(s);
            }
        }
        if coords.len() < 2 {
            return Ok(blank());
        }
        let (u, v) = wind_to_components(&dirs, &spds, None)?;
        let interp_u = interpolate_1d(log_ladder, &coords, &u, None, false)?;
        let interp_v = interpolate_1d(log_ladder, &coords, &v, None, false)?;
        let (interpolated, _) = components_to_wind(&interp_u, &interp_v, None)?;
        Ok(interpolated
            .into_iter()
            .map(|val| val.is_finite().then_some(val))
            .collect())
    } else {
        let mut coords = Vec::new();
        let mut vals = Vec::new();
        for (&p, &val) in pres.iter().zip(raw.iter()) {
            if p.is_finite() && val.is_finite() && p > 0.0 && val > MISSING_LIMIT {
                coords.push(p.log10());
                vals.push(val);
            }
        }
        if coords.len() < 2 {
            return Ok(blank());
        }
        let interpolated = interpolate_1d(log_ladder, &coords, &vals, None, false)?;
        Ok(interpolated
            .into_iter()
            .map(|val| val.is_finite().then_some(val))
            .collect())
    }
}

#[pyfunction]
#[pyo3(signature = (pres, field_values, wspd, log_ladder, is_wdir = false))]
pub(super) fn box_transect_column(
    pres: PyReadonlyArray1<'_, f64>,
    field_values: PyReadonlyArray1<'_, f64>,
    wspd: Option<PyReadonlyArray1<'_, f64>>,
    log_ladder: PyReadonlyArray1<'_, f64>,
    is_wdir: bool,
) -> PyResult<Vec<Option<f64>>> {
    let pres_s = slice_or_vec(&pres);
    let field_s = slice_or_vec(&field_values);
    let ladder_s = slice_or_vec(&log_ladder);
    let wspd_buf = wspd.as_ref().map(slice_or_vec);
    compute_transect_column(&pres_s, &field_s, wspd_buf.as_deref(), &ladder_s, is_wdir)
        .map_err(pyo3::exceptions::PyValueError::new_err)
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
#[pyo3(signature = (pres, tmpc, dwpc, wspd, wdir, omeg, log_ladder))]
pub(super) fn box_transect_columns<'py>(
    py: Python<'py>,
    pres: PyReadonlyArray1<'_, f64>,
    tmpc: PyReadonlyArray1<'_, f64>,
    dwpc: PyReadonlyArray1<'_, f64>,
    wspd: PyReadonlyArray1<'_, f64>,
    wdir: PyReadonlyArray1<'_, f64>,
    omeg: Option<PyReadonlyArray1<'_, f64>>,
    log_ladder: PyReadonlyArray1<'_, f64>,
) -> PyResult<Bound<'py, PyDict>> {
    let pres_s = slice_or_vec(&pres);
    let tmpc_s = slice_or_vec(&tmpc);
    let dwpc_s = slice_or_vec(&dwpc);
    let wspd_s = slice_or_vec(&wspd);
    let wdir_s = slice_or_vec(&wdir);
    let omeg_buf = omeg.as_ref().map(slice_or_vec);
    let ladder_s = slice_or_vec(&log_ladder);

    let out = PyDict::new(py);
    let insert = |name: &str, col: Vec<Option<f64>>| -> PyResult<()> {
        let tup = PyTuple::new(py, col)?;
        out.set_item(name, tup)
    };

    let err = pyo3::exceptions::PyValueError::new_err;
    insert(
        "tmpc",
        compute_transect_column(&pres_s, &tmpc_s, None, &ladder_s, false).map_err(err)?,
    )?;
    insert(
        "dwpc",
        compute_transect_column(&pres_s, &dwpc_s, None, &ladder_s, false).map_err(err)?,
    )?;
    insert(
        "wspd",
        compute_transect_column(&pres_s, &wspd_s, None, &ladder_s, false).map_err(err)?,
    )?;
    insert(
        "wdir",
        compute_transect_column(&pres_s, &wdir_s, Some(&wspd_s), &ladder_s, true).map_err(err)?,
    )?;
    let omeg_col = match omeg_buf.as_deref() {
        Some(omega) => {
            compute_transect_column(&pres_s, omega, None, &ladder_s, false).map_err(err)?
        }
        None => vec![None; ladder_s.len()],
    };
    insert("omeg", omeg_col)?;
    Ok(out)
}

use numpy::{PyArray1, PyReadonlyArray1};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::borrow::Cow;

fn slice_or_vec<'a>(arr: &'a PyReadonlyArray1<'_, f64>) -> Cow<'a, [f64]> {
    match arr.as_slice() {
        Ok(slice) => Cow::Borrowed(slice),
        Err(_) => Cow::Owned(arr.as_array().iter().copied().collect()),
    }
}

pub(crate) fn compute_sanitize_keep_mask(pres: &[f64], hght: &[f64]) -> Option<Vec<bool>> {
    if pres.is_empty() || pres.len() != hght.len() {
        return None;
    }
    let keep: Vec<bool> = pres
        .iter()
        .zip(hght.iter())
        .map(|(&p, &h)| p.is_finite() && h.is_finite())
        .collect();
    keep.iter().any(|&flag| flag).then_some(keep)
}

pub(crate) fn compute_clean_column(
    values: &[f64],
    keep_mask: &[bool],
    missing: f64,
) -> Result<Vec<f64>, String> {
    if values.len() != keep_mask.len() {
        return Err("column and keep_mask lengths differ".to_string());
    }
    Ok(values
        .iter()
        .zip(keep_mask.iter())
        .filter(|(_, &keep)| keep)
        .map(|(&val, _)| if val.is_finite() { val } else { missing })
        .collect())
}

pub(crate) fn compute_thin_indices(count: usize, max_levels: usize) -> Vec<usize> {
    if count == 0 || max_levels == 0 {
        return Vec::new();
    }
    if count <= max_levels {
        return (0..count).collect();
    }
    if max_levels == 1 {
        return vec![0];
    }
    let step = ((count - 1) as f64) / ((max_levels - 1) as f64);
    let mut indices = Vec::with_capacity(max_levels);
    for i in 0..max_levels {
        let pos = if i + 1 == max_levels {
            (count - 1) as f64
        } else {
            (i as f64) * step
        };
        let idx = (pos.round_ties_even() as isize).clamp(0, (count - 1) as isize) as usize;
        if indices.last().copied() != Some(idx) {
            indices.push(idx);
        }
    }
    indices
}

#[pyfunction]
fn sanitize_profile_keep_mask(
    pres: PyReadonlyArray1<'_, f64>,
    hght: PyReadonlyArray1<'_, f64>,
) -> Option<Vec<bool>> {
    let pres_s = slice_or_vec(&pres);
    let hght_s = slice_or_vec(&hght);
    compute_sanitize_keep_mask(&pres_s, &hght_s)
}

#[pyfunction]
fn clean_column<'py>(
    py: Python<'py>,
    values: PyReadonlyArray1<'_, f64>,
    keep_mask: Vec<bool>,
    missing: f64,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let val_s = slice_or_vec(&values);
    let cleaned =
        compute_clean_column(&val_s, &keep_mask, missing).map_err(PyValueError::new_err)?;
    Ok(PyArray1::from_vec(py, cleaned))
}

#[pyfunction]
fn thin_profile_indices(count: usize, max_levels: usize) -> Vec<usize> {
    compute_thin_indices(count, max_levels)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(sanitize_profile_keep_mask, module)?)?;
    module.add_function(wrap_pyfunction!(clean_column, module)?)?;
    module.add_function(wrap_pyfunction!(thin_profile_indices, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sanitize_and_thin_indices_match_numpy() {
        let keep = compute_sanitize_keep_mask(
            &[1000.0, f64::NAN, 850.0, 700.0],
            &[100.0, 500.0, f64::INFINITY, 3000.0],
        )
        .unwrap();
        assert_eq!(keep, vec![true, false, false, true]);
        let cleaned = compute_clean_column(&[25.0, 10.0, 5.0, f64::NAN], &keep, -9999.0).unwrap();
        assert_eq!(cleaned, vec![25.0, -9999.0]);

        let idx = compute_thin_indices(10, 5);
        assert_eq!(idx, vec![0, 2, 4, 7, 9]);
        let all_nan = compute_sanitize_keep_mask(&[f64::NAN], &[100.0]);
        assert!(all_nan.is_none());
    }
}

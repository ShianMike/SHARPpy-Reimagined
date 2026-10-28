//! The compatibility boundary owns NumPy shape/dtype conversion and buffer lifetime.

use numpy::{IntoPyArray, PyArray1, PyReadonlyArrayDyn, PyUntypedArrayMethods};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[pyfunction(signature = (u, v, indices, inside, lambert=None))]
fn pack_wind_grid<'py>(
    py: Python<'py>,
    u: PyReadonlyArrayDyn<'py, f32>,
    v: PyReadonlyArrayDyn<'py, f32>,
    indices: PyReadonlyArrayDyn<'py, i64>,
    inside: PyReadonlyArrayDyn<'py, bool>,
    lambert: Option<(usize, f64, f64, f64, f64)>,
) -> PyResult<Bound<'py, PyArray1<u16>>> {
    if u.shape() != v.shape() || indices.shape() != inside.shape() {
        return Err(PyValueError::new_err(
            "wind components or mapping do not match the model grid",
        ));
    }
    let (u, v, indices) = (u.as_slice()?, v.as_slice()?, indices.as_slice()?);
    let mut sampled_u = Vec::with_capacity(indices.len());
    let mut sampled_v = Vec::with_capacity(indices.len());
    let mut native_indices = Vec::with_capacity(indices.len());
    for &index in indices {
        let index = usize::try_from(index)
            .ok()
            .filter(|&index| index < u.len())
            .ok_or_else(|| PyValueError::new_err("wind mapping is outside the model grid"))?;
        native_indices.push(index);
        sampled_u.push(u[index]);
        sampled_v.push(v[index]);
    }
    let inside = inside.as_slice()?.to_vec();
    let grid = lambert.map(
        |(width, x0, y0, spacing, rho0)| crate::wind_grid::LambertGrid {
            width,
            x0,
            y0,
            spacing,
            rho0,
        },
    );
    let cells = py
        .detach(move || {
            crate::wind_grid::pack(&sampled_u, &sampled_v, &native_indices, &inside, grid)
        })
        .map_err(PyValueError::new_err)?;
    Ok(cells.into_pyarray(py))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(pack_wind_grid, module)?)?;
    Ok(())
}

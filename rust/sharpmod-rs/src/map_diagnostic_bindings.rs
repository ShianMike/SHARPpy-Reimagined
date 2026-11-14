//! Convert and broadcast field buffers once before native diagnostic evaluation.

use crate::{
    map_diagnostics::{evaluate, precipitation_type, Diagnostic},
    numeric_grid::{self, NumericGrid},
};
use pyo3::{exceptions::PyValueError, prelude::*};
use rayon::prelude::*;

#[pyfunction]
fn derive_map_diagnostic(
    py: Python<'_>,
    diagnostic: &str,
    values: Vec<NumericGrid<'_>>,
) -> PyResult<Py<PyAny>> {
    let kind = Diagnostic::parse(diagnostic).map_err(PyValueError::new_err)?;
    if values.len() != kind.arity() {
        return Err(PyValueError::new_err(
            "incorrect map diagnostic input count",
        ));
    }
    let grids = numeric_grid::broadcast(values)?;
    let precipitation = matches!(kind, Diagnostic::PrecipitationType);
    let single = if precipitation {
        grids.buffers[0].single()
    } else {
        grids.buffers.iter().all(|buffer| buffer.single())
    };
    let result = py.detach(|| {
        (0..grids.cells)
            .into_par_iter()
            .with_min_len(4096)
            .map(|cell| {
                if precipitation {
                    let (rate, flag) = (grids.buffers[0].value(cell), grids.buffers[1].value(cell));
                    return if single {
                        f64::from(precipitation_type(rate as f32, flag))
                    } else {
                        precipitation_type(rate, flag)
                    };
                }
                if single {
                    let mut v = [0.0f32; 6];
                    for (slot, buffer) in v.iter_mut().zip(&grids.buffers) {
                        *slot = buffer.value(cell) as f32;
                    }
                    f64::from(evaluate(kind, &v))
                } else {
                    let mut v = [0.0f64; 6];
                    for (slot, buffer) in v.iter_mut().zip(&grids.buffers) {
                        *slot = buffer.value(cell);
                    }
                    evaluate(kind, &v)
                }
            })
            .collect()
    });
    Ok(numeric_grid::output(py, &grids.shape, result, single))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(derive_map_diagnostic, module)?)?;
    Ok(())
}

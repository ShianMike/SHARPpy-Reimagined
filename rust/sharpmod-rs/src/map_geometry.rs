//! Native map geometry bindings; no Python work runs in the extraction loop.

use numpy::PyReadonlyArrayDyn;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[pyfunction(signature = (field, interval, max_segments=100_000, bounds=None))]
fn contour_segments(
    py: Python<'_>,
    field: PyReadonlyArrayDyn<'_, f64>,
    interval: f64,
    max_segments: usize,
    bounds: Option<(f64, f64, f64, f64)>,
) -> PyResult<Py<PyAny>> {
    let field = field.as_array();
    if field.ndim() != 2 {
        return Ok(Vec::<crate::contours::Segment>::new()
            .into_pyobject(py)?
            .into_any()
            .unbind());
    }
    let (height, width) = (field.shape()[0], field.shape()[1]);
    let values: Vec<f64> = field.iter().copied().collect();
    let segments = py.detach(move || {
        let mut segments = crate::contours::extract(&values, height, width, interval, max_segments)
            .map_err(PyValueError::new_err)?;
        if let Some(bounds) = bounds {
            crate::contours::project(&mut segments, height, width, bounds)
                .map_err(PyValueError::new_err)?;
        }
        Ok::<_, PyErr>(segments)
    })?;
    if bounds.is_some() {
        let segments: Vec<[[f64; 2]; 2]> = segments
            .into_iter()
            .map(|(a, b)| [[a.0, a.1], [b.0, b.1]])
            .collect();
        Ok(segments.into_pyobject(py)?.into_any().unbind())
    } else {
        Ok(segments.into_pyobject(py)?.into_any().unbind())
    }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(contour_segments, module)?)?;
    Ok(())
}

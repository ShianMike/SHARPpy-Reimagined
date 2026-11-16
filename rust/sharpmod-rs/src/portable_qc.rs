//! Physical checks for already structurally validated portable soundings.

use crate::quality_control::profile_qc;
use numpy::PyReadonlyArray1;
use pyo3::{
    exceptions::{PyKeyError, PyValueError},
    prelude::*,
    types::{PyDict, PyTuple},
};

#[pyfunction]
fn portable_profile_issues<'py>(
    py: Python<'py>,
    columns: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyTuple>> {
    let numpy = PyModule::import(py, "numpy")?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("dtype", numpy.getattr("float64")?)?;
    let mut values: [Vec<f64>; 6] = std::array::from_fn(|_| Vec::new());
    for (i, name) in ["pres", "hght", "tmpc", "dwpc", "wdir", "wspd"]
        .into_iter()
        .enumerate()
    {
        let column = columns
            .get_item(name)?
            .ok_or_else(|| PyKeyError::new_err(name))?;
        // Like the existing np.asarray contract, use a masked array's data,
        // not its mask. Snapshot strides/read-only arrays before releasing Python.
        let array = numpy.call_method("asarray", (column,), Some(&kwargs))?;
        let array = array.extract::<PyReadonlyArray1<'_, f64>>()?;
        values[i] = array.as_array().iter().copied().collect();
    }
    let issues = py
        .detach(move || profile_qc(values.each_ref().map(Vec::as_slice), Some(-9999.0), false))
        .map_err(PyValueError::new_err)?
        .issues;
    PyTuple::new(py, issues)
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(portable_profile_issues, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests;

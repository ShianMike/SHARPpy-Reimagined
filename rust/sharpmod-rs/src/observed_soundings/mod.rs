//! Provider selection, validated portable output and bridge registration for observations.
mod fetch;
mod open;
mod write;

use pyo3::{prelude::*, types::PyDict};

pub(super) const FIELDS: [&str; 6] = ["pres", "hght", "tmpc", "dwpc", "wdir", "wspd"];
const MISSING: f64 = -9999.;

pub(super) fn observed_error(py: Python<'_>, kind: &str, message: impl Into<String>) -> PyErr {
    PyModule::import(py, "sharpmod.providers.observations")
        .and_then(|module| module.getattr(kind))
        .and_then(|kind| kind.call1((message.into(),)))
        .map(PyErr::from_value)
        .unwrap_or_else(|error| error)
}

pub(super) fn columns<'py>(
    py: Python<'py>,
    profile: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyDict>> {
    let numpy = PyModule::import(py, "numpy")?;
    let masked = numpy.getattr("ma")?;
    let dtype = PyDict::new(py);
    dtype.set_item("dtype", numpy.getattr("float64")?)?;
    let columns = PyDict::new(py);
    for name in FIELDS {
        let data = masked
            .getattr("asarray")?
            .call((profile.getattr(name)?,), Some(&dtype))?
            .call_method1("filled", (MISSING,))?;
        columns.set_item(name, numpy.call_method1("asarray", (data,))?)?;
    }
    Ok(columns)
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(fetch::fetch_observed, module)?)?;
    module.add_function(wrap_pyfunction!(write::write_observed_npz, module)?)?;
    module.add_function(wrap_pyfunction!(open::open_observed_sounding, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests;

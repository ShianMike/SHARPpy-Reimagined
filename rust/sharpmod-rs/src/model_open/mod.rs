//! Open transactions own cache outputs until summary registration succeeds.
use pyo3::{prelude::*, types::PyDict};
mod forecast;
mod local;

struct Output {
    cache: Py<PyAny>,
    id: String,
    committed: bool,
}
impl Output {
    fn new(py: Python<'_>) -> PyResult<Self> {
        let cache =
            PyModule::import(py, "sharpmod.bridge.sounding_cache")?.call_method0("cache")?;
        Ok(Self {
            id: cache.call_method0("new_id")?.extract()?,
            cache: cache.unbind(),
            committed: false,
        })
    }
    fn path<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        self.cache
            .bind(py)
            .call_method1("data_path", (&self.id, ".npz"))
    }
}
impl Drop for Output {
    fn drop(&mut self) {
        if !self.committed {
            Python::attach(|py| {
                let _ = self
                    .cache
                    .bind(py)
                    .call_method1("_remove_files", (&self.id,));
            });
        }
    }
}
fn error(py: Python<'_>, code: &str, message: String, retryable: bool) -> PyResult<PyErr> {
    Ok(PyErr::from_value(
        PyModule::import(py, "sharpmod.bridge.errors")?
            .getattr("BridgeError")?
            .call1((code, message, retryable))?,
    ))
}
fn first_line(py: Python<'_>, error: &PyErr) -> String {
    error
        .value(py)
        .to_string()
        .trim()
        .lines()
        .next()
        .unwrap_or("Unknown extraction error")
        .chars()
        .take(300)
        .collect()
}
fn point(lat: f64, lon: f64) -> String {
    format!(
        "{:.2}{} {:.2}{}",
        lat.abs(),
        if lat >= 0. { "N" } else { "S" },
        lon.abs(),
        if lon >= 0. { "E" } else { "W" }
    )
}
pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(forecast::open_model_sounding, module)?)?;
    module.add_function(wrap_pyfunction!(local::open_era5_sounding, module)?)?;
    module.add_function(wrap_pyfunction!(local::open_wrf_sounding, module)?)
}
#[cfg(test)]
mod tests;

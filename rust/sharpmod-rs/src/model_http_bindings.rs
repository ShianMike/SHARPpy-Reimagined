//! Preserve field-provider exceptions at the native network boundary.

use pyo3::{prelude::*, types::PyBytes};
use sharpmod_weather::model_http::{Error, ModelClient};

type InventoryTuple = (usize, u64, u64, &'static str, String, String);

#[pyfunction]
fn parse_ecmwf_inventory(py: Python<'_>, text: &str) -> PyResult<Vec<InventoryTuple>> {
    let rows = match py.detach(|| crate::ecmwf_inventory::parse_inventory(text)) {
        Ok(rows) => rows,
        Err(()) => {
            let kind =
                PyModule::import(py, "sharpmod.providers.hrrr_field")?.getattr("HrrrFieldError")?;
            return Err(PyErr::from_value(
                kind.call1(("ECMWF inventory contains an invalid record",))?,
            ));
        }
    };
    Ok(rows
        .into_iter()
        .map(|row| {
            (
                row.index,
                row.offset,
                row.length,
                row.variable,
                row.level,
                row.forecast,
            )
        })
        .collect())
}

#[pyfunction(signature=(url,timeout,limit,byte_range=None,should_cancel=None))]
fn fetch_model_bytes(
    py: Python<'_>,
    url: String,
    timeout: f64,
    limit: usize,
    byte_range: Option<(u64, u64)>,
    should_cancel: Option<Py<PyAny>>,
) -> PyResult<Py<PyBytes>> {
    let runtime = crate::weather::runtime()?;
    let result = py.detach(move || {
        let cancel = || match &should_cancel {
            None => Ok(false),
            Some(callback) => Python::attach(|py| callback.bind(py).call0()?.is_truthy())
                .map_err(|error: PyErr| error.to_string()),
        };
        runtime.block_on(ModelClient::shared().fetch(&url, timeout, limit, byte_range, cancel))
    });
    match result {
        Ok(bytes) => Ok(PyBytes::new(py, &bytes).unbind()),
        Err(Error::Cancelled) => {
            let error = PyModule::import(py, "sharpmod.models.model_transport")?
                .getattr("DownloadCancelled")?;
            Err(PyErr::from_value(
                error.call1(("field download cancelled",))?,
            ))
        }
        Err(error) => {
            let (kind, message) = match error {
                Error::Offline(message) => (
                    "HrrrFieldOffline",
                    format!("HRRR connection failed: {message}"),
                ),
                Error::Unavailable(status) => (
                    "HrrrFieldUnavailable",
                    format!("HRRR object is not published yet ({status})"),
                ),
                Error::Http(status) => (
                    "HrrrFieldError",
                    format!("HRRR request failed: HTTP {status}"),
                ),
                Error::Budget => (
                    "HrrrFieldError",
                    "HRRR response exceeded the byte budget".into(),
                ),
                Error::Invalid(message) => ("HrrrFieldError", message),
                Error::Cancelled => unreachable!("cancellation handled above"),
            };
            let error = PyModule::import(py, "sharpmod.providers.hrrr_field")?.getattr(kind)?;
            Err(PyErr::from_value(error.call1((message,))?))
        }
    }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(parse_ecmwf_inventory, module)?)?;
    module.add_function(wrap_pyfunction!(fetch_model_bytes, module)?)?;
    Ok(())
}

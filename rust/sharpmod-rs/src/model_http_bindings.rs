//! Preserve field-provider exceptions at the native network boundary.

use pyo3::{prelude::*, types::PyBytes};
use sharpmod_weather::{
    map_fields::{self, RangeJob},
    model_http::{Error, ModelClient},
};

type InventoryTuple = (usize, u64, u64, &'static str, String, String);
type WgribInventoryTuple = (i64, u64, u64, String, String, String, usize);
type MapRangeTuple = (u64, u64, Vec<usize>);

#[pyfunction]
fn parse_wgrib_inventory(py: Python<'_>, text: &str) -> Vec<WgribInventoryTuple> {
    py.detach(|| map_fields::parse_wgrib_inventory(text))
        .into_iter()
        .map(|row| {
            (
                row.index,
                row.offset,
                row.length,
                row.variable,
                row.level,
                row.forecast,
                row.field_index,
            )
        })
        .collect()
}

#[pyfunction(signature=(rows,slack=65536))]
fn merge_map_ranges(
    py: Python<'_>,
    rows: Vec<(u64, u64)>,
    slack: i64,
) -> PyResult<Vec<MapRangeTuple>> {
    py.detach(|| map_fields::merge_map_ranges(&rows, slack))
        .map(|groups| {
            groups
                .into_iter()
                .map(|group| (group.start, group.end, group.members))
                .collect()
        })
        .map_err(|error| field_error(py, error))
}

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
        let cancel = || cancelled(should_cancel.as_ref());
        runtime.block_on(ModelClient::shared().fetch(&url, timeout, limit, byte_range, cancel))
    });
    result
        .map(|bytes| PyBytes::new(py, &bytes).unbind())
        .map_err(|error| field_error(py, error))
}

#[pyfunction(signature=(jobs,timeout,should_cancel=None,workers=4))]
fn fetch_model_ranges(
    py: Python<'_>,
    jobs: Vec<(String, usize, u64, u64)>,
    timeout: f64,
    should_cancel: Option<Py<PyAny>>,
    workers: usize,
) -> PyResult<Vec<Py<PyBytes>>> {
    let jobs = jobs
        .into_iter()
        .map(|(url, limit, start, end)| RangeJob {
            url,
            limit,
            start,
            end,
        })
        .collect();
    let runtime = crate::weather::runtime()?;
    let result = py.detach(move || {
        runtime.block_on(map_fields::fetch_model_ranges(
            ModelClient::shared(),
            jobs,
            timeout,
            workers,
            || cancelled(should_cancel.as_ref()),
        ))
    });
    result
        .map(|payloads| {
            payloads
                .into_iter()
                .map(|bytes| PyBytes::new(py, &bytes).unbind())
                .collect()
        })
        .map_err(|error| field_error(py, error))
}

fn cancelled(callback: Option<&Py<PyAny>>) -> Result<bool, String> {
    match callback {
        None => Ok(false),
        Some(callback) => Python::attach(|py| callback.bind(py).call0()?.is_truthy())
            .map_err(|error: PyErr| error.to_string()),
    }
}

fn field_error(py: Python<'_>, error: Error) -> PyErr {
    let (kind, message) = match error {
        Error::Cancelled => ("DownloadCancelled", "field download cancelled".into()),
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
    };
    let module = if kind == "DownloadCancelled" {
        "sharpmod.models.model_transport"
    } else {
        "sharpmod.providers.hrrr_field"
    };
    let construct = || -> PyResult<PyErr> {
        let error = PyModule::import(py, module)?.getattr(kind)?;
        Ok(PyErr::from_value(error.call1((message,))?))
    };
    construct().unwrap_or_else(|error| error)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(parse_ecmwf_inventory, module)?)?;
    module.add_function(wrap_pyfunction!(parse_wgrib_inventory, module)?)?;
    module.add_function(wrap_pyfunction!(merge_map_ranges, module)?)?;
    module.add_function(wrap_pyfunction!(fetch_model_bytes, module)?)?;
    module.add_function(wrap_pyfunction!(fetch_model_ranges, module)?)?;
    Ok(())
}

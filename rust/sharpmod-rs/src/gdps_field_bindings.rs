//! GDPS map transfers through the shared native HTTP client and GRIB runtime.

use crate::{
    gdps_fields::{
        self, Frame, Record, HEIGHT, MAX_FIELD_BYTES, MAX_INVENTORY_BYTES, SPACING, WIDTH,
    },
    grib::gdps_grid,
    model_http_bindings::{cancelled, field_error},
};
use numpy::{ndarray::Array2, IntoPyArray};
use pyo3::{prelude::*, types::PyDict};
use sharpmod_weather::{
    map_fields::{fetch_model_fields, FieldJob},
    model_http::{Error, ModelClient},
};
use std::path::Path;
use time::OffsetDateTime;

fn records(product: &Bound<'_, PyAny>) -> PyResult<Vec<(String, Record)>> {
    product
        .getattr("all_fields")?
        .try_iter()?
        .map(|field| {
            let name: String = field?.getattr("name")?.extract()?;
            let record =
                gdps_fields::record(&name).map_err(|error| field_error(product.py(), error))?;
            Ok((name, record))
        })
        .collect()
}

#[pyfunction]
fn gdps_map_grid(py: Python<'_>) -> PyResult<Py<PyAny>> {
    let kwargs = PyDict::new(py);
    kwargs.set_item("shape", (HEIGHT, WIDTH))?;
    kwargs.set_item("spacing", SPACING)?;
    Ok(PyModule::import(py, "types")?
        .getattr("SimpleNamespace")?
        .call((), Some(&kwargs))?
        .unbind())
}

#[pyfunction]
fn gdps_map_directory(py: Python<'_>, run: OffsetDateTime, hour: u16) -> PyResult<String> {
    Ok(Frame::new(run, hour)
        .map_err(|error| field_error(py, error))?
        .directory())
}

#[pyfunction]
fn available_gdps_map_products(
    py: Python<'_>,
    products: Vec<Py<PyAny>>,
    run: OffsetDateTime,
    hour: u16,
    timeout: f64,
) -> PyResult<Vec<Py<PyAny>>> {
    let frame = Frame::new(run, hour).map_err(|error| field_error(py, error))?;
    let requested = products
        .iter()
        .map(|product| {
            Ok(records(product.bind(py))?
                .into_iter()
                .map(|(_, record)| record)
                .collect::<Vec<_>>())
        })
        .collect::<PyResult<Vec<_>>>()?;
    let cancellation = PyModule::import(py, "sharpmod.bridge.cancellation")?;
    let event = cancellation.call_method0("event")?;
    let callback = if event.is_none() {
        None
    } else {
        Some(event.getattr("is_set")?.unbind())
    };
    let runtime = crate::weather::runtime()?;
    let url = frame.directory();
    let available = py.detach(|| {
        let listing = runtime.block_on(ModelClient::shared().fetch(
            &url,
            timeout,
            MAX_INVENTORY_BYTES,
            None,
            || cancelled(callback.as_ref()),
        ))?;
        let text = std::str::from_utf8(&listing)
            .map_err(|_| Error::Invalid("Invalid GDPS file listing".into()))?;
        let inventory = frame.inventory(text);
        Ok::<_, Error>(
            requested
                .iter()
                .map(|records| frame.has_records(&inventory, records))
                .collect::<Vec<_>>(),
        )
    });
    match available {
        Ok(available) => Ok(products
            .into_iter()
            .zip(available)
            .filter_map(|(product, present)| present.then_some(product))
            .collect()),
        Err(Error::Cancelled) => Err(PyErr::from_value(
            cancellation.getattr("Cancelled")?.call0()?,
        )),
        Err(error) => Err(field_error(py, error)),
    }
}

#[pyfunction(signature = (product, run, hour, timeout, should_cancel=None, workers=4))]
fn download_gdps_map_fields<'py>(
    py: Python<'py>,
    product: &Bound<'py, PyAny>,
    run: OffsetDateTime,
    hour: u16,
    timeout: f64,
    should_cancel: Option<Py<PyAny>>,
    workers: usize,
) -> PyResult<Bound<'py, PyDict>> {
    let frame = Frame::new(run, hour).map_err(|error| field_error(py, error))?;
    let records = records(product)?;
    let library = PyModule::import(py, "sharpmod.backends.rust_backend")?
        .getattr("_eccodes_library_path")?
        .call0()
        .and_then(|path| path.extract::<String>())
        .map_err(|error| {
            field_error(
                py,
                Error::Invalid(format!("GDPS needs the GRIB runtime: {error}")),
            )
        })?;
    let jobs = records
        .iter()
        .map(|(_, record)| FieldJob {
            url: frame.file_url(*record),
            limit: MAX_FIELD_BYTES,
            range: None,
        })
        .collect();
    let runtime = crate::weather::runtime()?;
    let stop = || cancelled(should_cancel.as_ref());
    let payloads = py
        .detach(|| {
            runtime.block_on(fetch_model_fields(
                ModelClient::shared(),
                jobs,
                timeout,
                workers,
                stop,
            ))
        })
        .map_err(|error| field_error(py, error))?;
    // Other map providers use ecCodes from Python. Share their lock only
    // while decoding, so waiting on Datamart never blocks another decoder.
    let lock = PyModule::import(py, "sharpmod.backends.grib")?.getattr("_ECCODES_LOCK")?;
    lock.call_method0("acquire")?;
    let values = py.detach(|| {
        records
            .into_iter()
            .zip(payloads)
            .map(|((name, record), payload)| {
                if stop().map_err(Error::Invalid)? {
                    return Err(Error::Cancelled);
                }
                let values = gdps_grid::decode(Path::new(&library), &payload, &frame, record)
                    .map_err(|error| Error::Invalid(error.to_string()))?;
                Ok((name, values))
            })
            .collect::<Result<Vec<_>, _>>()
    });
    lock.call_method0("release")?;
    let values = values.map_err(|error| field_error(py, error))?;
    let decoded = PyDict::new(py);
    for (name, values) in values {
        let grid =
            Array2::from_shape_vec((HEIGHT, WIDTH), values).expect("validated GDPS dimensions");
        decoded.set_item(name, grid.into_pyarray(py))?;
    }
    Ok(decoded)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(gdps_map_grid, module)?)?;
    module.add_function(wrap_pyfunction!(gdps_map_directory, module)?)?;
    module.add_function(wrap_pyfunction!(available_gdps_map_products, module)?)?;
    module.add_function(wrap_pyfunction!(download_gdps_map_fields, module)?)?;
    Ok(())
}

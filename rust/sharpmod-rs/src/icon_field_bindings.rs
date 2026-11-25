//! DWD ICON maps through the shared map-provider and GRIB interfaces.

use crate::{
    grib::icon_grid,
    icon_fields::{self, File, Frame, Plan, Run},
    icon_mesh,
    model_http_bindings::{cancelled, field_error},
};
use numpy::{ndarray::Array2, IntoPyArray};
use pyo3::{prelude::*, types::PyDict};
use rayon::prelude::*;
use sharpmod_weather::{
    map_fields::{fetch_model_fields, FieldJob},
    model_http::{Error, ModelClient},
};
use std::{path::Path, sync::Arc};
use time::OffsetDateTime;
use tokio::{runtime::Runtime, sync::Mutex};

const MAX_FIELD_BYTES: usize = 16 * 1024 * 1024;
const SHAPE: (usize, usize) = (icon_mesh::HEIGHT, icon_mesh::WIDTH);

fn plan(product: &Bound<'_, PyAny>, frame: Frame) -> PyResult<Option<Vec<(String, Plan)>>> {
    let mut plans = Vec::new();
    for field in product.getattr("all_fields")?.try_iter()? {
        let name: String = field?.getattr("name")?.extract()?;
        let plan = match frame.plan(&name) {
            Ok(plan) => plan,
            Err(Error::Unavailable(_)) => return Ok(None),
            Err(error) => return Err(field_error(product.py(), error)),
        };
        plans.push((name, plan));
    }
    Ok(Some(plans))
}

#[pyfunction]
fn icon_map_grid(py: Python<'_>) -> PyResult<Py<PyAny>> {
    let kwargs = PyDict::new(py);
    kwargs.set_item("shape", SHAPE)?;
    kwargs.set_item("spacing", icon_mesh::SPACING)?;
    Ok(PyModule::import(py, "types")?
        .getattr("SimpleNamespace")?
        .call((), Some(&kwargs))?
        .unbind())
}

#[pyfunction(signature = (run, hour, field="tmp2m"))]
fn icon_map_url(py: Python<'_>, run: OffsetDateTime, hour: u16, field: &str) -> PyResult<String> {
    let frame = Frame::new(run, hour).map_err(|error| field_error(py, error))?;
    let plan = frame.plan(field).map_err(|error| field_error(py, error))?;
    Ok(plan.files[0].url(frame.run))
}

#[pyfunction]
fn probe_icon_map(py: Python<'_>, run: OffsetDateTime, hour: u16, timeout: f64) -> PyResult<()> {
    let url = icon_map_url(py, run, hour, "tmp2m")?;
    let runtime = crate::weather::runtime()?;
    py.detach(|| runtime.block_on(ModelClient::shared().identity(&url, timeout, || Ok(false))))
        .map(|_| ())
        .map_err(|error| field_error(py, error))
}

#[pyfunction]
fn available_icon_map_products(
    py: Python<'_>,
    products: Vec<Py<PyAny>>,
    run: OffsetDateTime,
    hour: u16,
    timeout: f64,
) -> PyResult<Vec<Py<PyAny>>> {
    let frame = Frame::new(run, hour).map_err(|error| field_error(py, error))?;
    let planned = products
        .iter()
        .map(|product| plan(product.bind(py), frame))
        .collect::<PyResult<Vec<_>>>()?;

    let cancellation = PyModule::import(py, "sharpmod.bridge.cancellation")?;
    let event = cancellation.call_method0("event")?;
    let callback = if event.is_none() {
        None
    } else {
        Some(event.getattr("is_set")?.unbind())
    };
    let runtime = crate::weather::runtime()?;
    let files = py
        .detach(|| {
            runtime.block_on(icon_fields::inventory(frame.run, timeout, || {
                cancelled(callback.as_ref())
            }))
        })
        .map_err(|error| {
            if error == Error::Cancelled {
                cancellation
                    .getattr("Cancelled")
                    .and_then(|kind| kind.call0())
                    .map_or_else(|error| error, PyErr::from_value)
            } else {
                field_error(py, error)
            }
        })?;
    if !icon_fields::coordinates()
        .into_iter()
        .all(|file| files.contains(file))
    {
        return Ok(Vec::new());
    }
    Ok(products
        .into_iter()
        .zip(planned)
        .filter_map(|(product, plan)| {
            plan?
                .iter()
                .flat_map(|(_, plan)| &plan.files)
                .all(|&file| files.contains(file))
                .then_some(product)
        })
        .collect())
}

struct Transfer<'a, 'py> {
    py: Python<'py>,
    runtime: &'a Runtime,
    library: &'a Path,
    lock: Bound<'py, PyAny>,
    run: Run,
    timeout: f64,
    should_cancel: Option<Py<PyAny>>,
    workers: usize,
}

impl Transfer<'_, '_> {
    fn fetch(&self, files: &[File]) -> PyResult<Vec<Vec<u8>>> {
        let jobs = files
            .iter()
            .map(|file| FieldJob {
                url: file.url(self.run),
                limit: MAX_FIELD_BYTES,
                range: None,
            })
            .collect();
        let runtime = self.runtime;
        let timeout = self.timeout;
        let workers = self.workers;
        let callback = self.should_cancel.as_ref();
        self.py
            .detach(|| {
                runtime.block_on(fetch_model_fields(
                    ModelClient::shared(),
                    jobs,
                    timeout,
                    workers,
                    || cancelled(callback),
                ))
            })
            .map_err(|error| field_error(self.py, error))
    }

    fn decode(&self, file: File, payload: &[u8]) -> PyResult<Vec<f32>> {
        if cancelled(self.should_cancel.as_ref())
            .map_err(|message| field_error(self.py, Error::Invalid(message)))?
        {
            return Err(field_error(self.py, Error::Cancelled));
        }
        // The Python decoders and the native ecCodes context share this process.
        let library = self.library;
        let run = self.run;
        self.lock.call_method0("acquire")?;
        let decoded = self
            .py
            .detach(|| icon_grid::decode(library, payload, run, file));
        self.lock.call_method0("release")?;
        decoded.map_err(|error| field_error(self.py, Error::Invalid(error.to_string())))
    }

    fn grid_indices(&self) -> PyResult<Arc<[u32]>> {
        // All fields are validated against the same DWD grid UUID before using these indices.
        static INDICES: Mutex<Option<Arc<[u32]>>> = Mutex::const_new(None);
        let runtime = self.runtime;
        let mut cached = self.py.detach(|| runtime.block_on(INDICES.lock()));
        if let Some(indices) = cached.as_ref() {
            return Ok(Arc::clone(indices));
        }
        let files = icon_fields::coordinates();
        let payloads = self.fetch(&files)?;
        let latitude = self.decode(files[0], &payloads[0])?;
        let longitude = self.decode(files[1], &payloads[1])?;
        let indices = self
            .py
            .detach(|| icon_mesh::nearest_indices(&latitude, &longitude))
            .map_err(|message| field_error(self.py, Error::Invalid(message)))?
            .into();
        *cached = Some(Arc::clone(&indices));
        Ok(indices)
    }
}

#[pyfunction(signature = (product, run, hour, timeout, should_cancel=None, workers=4))]
fn download_icon_map_fields<'py>(
    py: Python<'py>,
    product: &Bound<'py, PyAny>,
    run: OffsetDateTime,
    hour: u16,
    timeout: f64,
    should_cancel: Option<Py<PyAny>>,
    workers: usize,
) -> PyResult<Bound<'py, PyDict>> {
    let frame = Frame::new(run, hour).map_err(|error| field_error(py, error))?;
    let planned = plan(product, frame)?.ok_or_else(|| field_error(py, Error::Unavailable(404)))?;
    let library: String = PyModule::import(py, "sharpmod.backends.rust_backend")?
        .getattr("_eccodes_library_path")?
        .call0()
        .and_then(|path| path.extract())
        .map_err(|error| {
            field_error(
                py,
                Error::Invalid(format!("ICON Global maps need the GRIB runtime: {error}")),
            )
        })?;
    let transfer = Transfer {
        py,
        runtime: crate::weather::runtime()?,
        library: Path::new(&library),
        lock: PyModule::import(py, "sharpmod.backends.grib")?.getattr("_ECCODES_LOCK")?,
        run: frame.run,
        timeout,
        should_cancel,
        workers,
    };
    let sources = planned
        .iter()
        .flat_map(|(_, plan)| plan.files.iter().copied())
        .collect::<Vec<_>>();
    let payloads = transfer.fetch(&sources)?;
    let indices = transfer.grid_indices()?;
    let mut records = sources.into_iter().zip(payloads);
    let mapped = PyDict::new(py);
    for (name, plan) in planned {
        let mut ingredients = records.by_ref().take(plan.files.len());
        let (file, payload) = ingredients.next().expect("each ICON field needs a record");
        let mut values = transfer.decode(file, &payload)?;
        for (file, payload) in ingredients {
            let next = transfer.decode(file, &payload)?;
            py.detach(|| {
                values
                    .par_iter_mut()
                    .zip(&next)
                    .for_each(|(first, &second)| {
                        *first = plan.reduction.combine(*first, second);
                    })
            });
        }
        let regular: Vec<f32> = py.detach(|| {
            indices
                .par_iter()
                .map(|&index| values[index as usize])
                .collect()
        });
        let grid =
            Array2::from_shape_vec(SHAPE, regular).expect("validated ICON mapping dimensions");
        mapped.set_item(name, grid.into_pyarray(py))?;
    }
    Ok(mapped)
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(icon_map_grid, module)?)?;
    module.add_function(wrap_pyfunction!(icon_map_url, module)?)?;
    module.add_function(wrap_pyfunction!(probe_icon_map, module)?)?;
    module.add_function(wrap_pyfunction!(available_icon_map_products, module)?)?;
    module.add_function(wrap_pyfunction!(download_icon_map_fields, module)?)?;
    Ok(())
}

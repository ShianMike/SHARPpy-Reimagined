use super::*;
use rayon::prelude::*;
use std::sync::{
    atomic::{AtomicU64, Ordering},
    Mutex,
};
struct Loader {
    get: Option<Py<PyAny>>,
    cancel: Option<Py<PyAny>>,
    progress: Option<Py<PyAny>>,
    bytes: Option<Py<PyAny>>,
    downloaded: AtomicU64,
    report: Mutex<()>,
}
impl Loader {
    fn load(&self, url: &str) -> PyResult<Vec<u8>> {
        if crate::model_http_bindings::cancelled(self.cancel.as_ref())
            .map_err(pyo3::exceptions::PyRuntimeError::new_err)?
        {
            return Python::attach(|py| {
                Err(crate::model_http_bindings::field_error(
                    py,
                    sharpmod_weather::model_http::Error::Cancelled,
                ))
            });
        }
        let value = if let Some(get) = &self.get {
            Python::attach(|py| get.bind(py).call1((url,))?.extract::<Vec<u8>>())?
        } else {
            let result = crate::weather::runtime()?.block_on(
                sharpmod_weather::model_http::ModelClient::shared().fetch(
                    url,
                    30.,
                    64 * 1024 * 1024,
                    None,
                    || crate::model_http_bindings::cancelled(self.cancel.as_ref()),
                ),
            );
            result
                .map_err(|e| Python::attach(|py| crate::model_http_bindings::field_error(py, e)))?
        };
        let _guard = self.report.lock().unwrap();
        let total = self
            .downloaded
            .fetch_add(value.len() as u64, Ordering::Relaxed)
            + value.len() as u64;
        Python::attach(|py| -> PyResult<()> {
            if let Some(callback) = &self.bytes {
                callback.bind(py).call1((value.len(),))?;
            }
            if let Some(callback) = &self.progress {
                callback.bind(py).call1((total, 0))?;
            }
            Ok(())
        })?;
        Ok(value)
    }
}
#[pyfunction]
fn fetch_hrrr_zarr_native<'py>(
    py: Python<'py>,
    run: &Bound<'py, PyAny>,
    fxx: i64,
    lat: f64,
    lon: f64,
    options: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyAny>> {
    if fxx != 0 {
        return Err(error(
            py,
            "the public HRRR Zarr archive currently exposes analyses, not lead times",
        ));
    }
    let api = PyModule::import(py, "sharpmod.providers.hrrr_zarr")?;
    let root = api.call_method1("_root_url", (run,))?.extract::<String>()?;
    let cache = api.call_method1("_cache_path", (options.get_item("cache_dir")?,))?;
    let cached = api.call_method1("_load_cached_point", (&cache, run, lat, lon, &root))?;
    if !cached.is_none() {
        return Ok(cached);
    }
    let callable = |key| -> PyResult<Option<Py<PyAny>>> {
        Ok(options
            .get_item(key)?
            .filter(|v| !v.is_none())
            .map(Bound::unbind))
    };
    let loader = Loader {
        get: callable("get_bytes")?,
        cancel: callable("cancelled")?,
        progress: callable("progress")?,
        bytes: callable("on_bytes")?,
        downloaded: AtomicU64::new(0),
        report: Mutex::new(()),
    };
    let bytes = py.detach(|| loader.load(&format!("{root}/.zmetadata")))?;
    let consolidated = PyModule::import(py, "json")?
        .call_method1("loads", (pyo3::types::PyBytes::new(py, &bytes),))?;
    let metadata = consolidated.get_item("metadata")?;
    let metadata = metadata.cast::<PyDict>()?;
    let plan = hrrr_pressure_plan(py, metadata)?;
    let surface_plan = hrrr_surface_plan(py, metadata)?;
    let (iy, ix, selected_lat, selected_lon): (usize, usize, f64, f64) =
        api.call_method1("hrrr_grid_index", (lat, lon))?.extract()?;
    let levels: Vec<f64> = plan.getattr("levels")?.extract()?;
    let fields: Vec<String> = plan.getattr("fields")?.extract()?;
    let mut jobs = Vec::new();
    let mut add =
        |kind: bool, index: usize, field: String, array: Bound<'_, PyAny>| -> PyResult<()> {
            let path: String = array.get_item(0)?.extract()?;
            let meta = array.get_item(1)?;
            let (chunks, _, _) = geometry(meta.cast::<PyDict>()?, iy, ix)?;
            jobs.push((
                kind,
                index,
                field,
                format!("{root}/{path}/{}.{}", iy / chunks[0], ix / chunks[1]),
                meta.unbind(),
            ));
            Ok(())
        };
    let arrays = plan.getattr("arrays")?;
    for (index, &level) in levels.iter().enumerate() {
        for field in &fields {
            add(
                false,
                index,
                field.clone(),
                arrays.get_item((level, field))?,
            )?;
        }
    }
    for entry in surface_plan
        .getattr("arrays")?
        .call_method0("items")?
        .try_iter()?
    {
        let entry = entry?;
        add(true, 0, entry.get_item(0)?.extract()?, entry.get_item(1)?)?;
    }
    let workers: usize = options
        .get_item("max_workers")?
        .map(|v| v.extract())
        .transpose()?
        .unwrap_or(16);
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(workers.clamp(1, 32))
        .build()
        .map_err(|e| error(py, e.to_string()))?;
    let results: Vec<PyResult<f64>> = py.detach(|| {
        pool.install(|| {
            jobs.par_iter()
                .map(|(_, _, _, url, metadata)| {
                    let payload = loader.load(url)?;
                    Python::attach(|py| {
                        decode_hrrr_zarr_point(
                            py,
                            &pyo3::types::PyBytes::new(py, &payload),
                            metadata.bind(py).cast::<PyDict>()?,
                            iy,
                            ix,
                        )
                    })
                })
                .collect()
        })
    });
    let mut columns = std::collections::HashMap::new();
    for field in &fields {
        columns.insert(field.clone(), vec![f64::NAN; levels.len()]);
    }
    let surface = PyDict::new(py);
    for ((ground, index, field, _, _), value) in jobs.iter().zip(results) {
        let value = value?;
        if *ground {
            surface.set_item(field, value)?;
        } else {
            columns.get_mut(field).unwrap()[*index] = value;
        }
    }
    let column_dict = PyDict::new(py);
    for (field, values) in columns {
        column_dict.set_item(field, numpy::IntoPyArray::into_pyarray(values, py))?;
    }
    let dataset = normalize::point_dataset(
        py,
        &plan.getattr("levels")?,
        &column_dict,
        selected_lat,
        selected_lon,
        run,
        lat,
        lon,
        Some(&surface),
    )?;
    api.call_method1(
        "_write_cached_point",
        (
            &cache,
            run,
            lat,
            lon,
            selected_lat,
            selected_lon,
            &plan,
            &surface_plan,
            &column_dict,
            &surface,
        ),
    )?;
    let kwargs = PyDict::new(py);
    kwargs.set_item(
        "downloaded_bytes",
        loader.downloaded.load(Ordering::Relaxed),
    )?;
    let combined = plan.getattr("fields")?.call_method1(
        "__add__",
        (PyTuple::new(
            py,
            surface_plan
                .getattr("arrays")?
                .try_iter()?
                .collect::<PyResult<Vec<_>>>()?,
        )?,),
    )?;
    let source = api
        .getattr("HrrrZarrSource")?
        .call((&root, &root, combined), Some(&kwargs))?;
    Ok((dataset, source).into_pyobject(py)?.into_any())
}
pub(super) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(fetch_hrrr_zarr_native, module)?)
}

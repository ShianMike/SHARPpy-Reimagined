use super::*;
struct Progress(Py<PyAny>);
impl Drop for Progress {
    fn drop(&mut self) {
        Python::attach(|py| {
            let _ = self
                .0
                .bind(py)
                .call_method1("__exit__", (py.None(), py.None(), py.None()));
        });
    }
}
#[pyfunction]
pub(super) fn open_model_sounding<'py>(
    py: Python<'py>,
    params: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyAny>> {
    let p = PyModule::import(py, "sharpmod.bridge.params")?;
    p.call_method1(
        "keys",
        (
            params,
            ("model", "forecast_hour", "latitude", "longitude"),
            ("run_time", "location", "progress_id", "box"),
        ),
    )?;
    let api = PyModule::import(py, "sharpmod.bridge.sounding_open")?;
    let context = api.call_method1("track", (params.get_item("progress_id")?,))?;
    let progress = context.call_method0("__enter__")?;
    let _progress = Progress(context.unbind());
    let mut output = Output::new(py)?;
    let key: String = p
        .call_method1("text", (params, "model", 1, 40))?
        .extract()?;
    let fxx = p.call_method1("integer", (params, "forecast_hour", 0, 1000))?;
    let lat: f64 = p
        .call_method1("number", (params, "latitude", -90., 90.))?
        .extract()?;
    let lon: f64 = p
        .call_method1("number", (params, "longitude", -180., 180.))?
        .extract()?;
    let run = if params.get_item("run_time")?.is_some_and(|v| !v.is_none()) {
        p.call_method1("time", (params, "run_time"))?
    } else {
        py.None().into_bound(py)
    };
    let mut location = p.call_method1("optional_text", (params, "location", 64))?;
    let era = PyModule::import(py, "sharpmod.tools.era5_extract")?;
    let model = PyModule::import(py, "sharpmod.tools.model_extract")?;
    let config = model
        .call_method1("get_config", (&key,))
        .and_then(|c| model.call_method1("_require_selectable", (c,)))
        .map_err(|failure| {
            if failure.is_instance_of::<pyo3::exceptions::PyKeyError>(py)
                || failure.is_instance(py, &era.getattr("ERA5ExtractionError").unwrap())
            {
                error(
                    py,
                    "INVALID_ARGUMENT",
                    format!(
                        "The forecast model {key:?} is not available: {}",
                        failure.value(py)
                    ),
                    false,
                )
                .unwrap_or(failure)
            } else {
                failure
            }
        })?;
    let path = output.path(py)?;
    let cancel = PyModule::import(py, "sharpmod.bridge.cancellation")?;
    let stop = cancel.call_method0("event")?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("run_time", run)?;
    kwargs.set_item("fxx", &fxx)?;
    kwargs.set_item("out_path", path.str()?)?;
    kwargs.set_item("loc", &location)?;
    let root = output.cache.bind(py).getattr("root")?;
    kwargs.set_item(
        "download_dir",
        root.call_method1("__truediv__", (format!("{}-grib", output.id),))?
            .str()?,
    )?;
    kwargs.set_item("progress_callback", progress.getattr("stage")?)?;
    kwargs.set_item("on_bytes", progress.getattr("receive")?)?;
    kwargs.set_item(
        "cancelled",
        if stop.is_none() {
            pyo3::types::PyCFunction::new_closure(py, None, None, |_, _| Ok::<_, PyErr>(false))?
                .into_any()
        } else {
            stop.getattr("is_set")?
        },
    )?;
    kwargs.set_item(
        "source_cache",
        output.cache.bind(py).getattr("model_hours")?,
    )?;
    let box_bounds = params.get_item("box")?.filter(|b| !b.is_none());
    let extract = if let Some(bounds) = box_bounds {
        kwargs.set_item("box_bounds", bounds)?;
        PyModule::import(py, "sharpmod_rs")?.getattr("extract_box_sounding")?
    } else {
        model.getattr("extract")?
    };
    let written = extract
        .call((config.getattr("key")?, lat, lon), Some(&kwargs))
        .map_err(|failure| {
            if failure.is_instance(
                py,
                &PyModule::import(py, "sharpmod.models.model_transport")
                    .unwrap()
                    .getattr("DownloadCancelled")
                    .unwrap(),
            ) {
                return PyErr::from_value(cancel.getattr("Cancelled").unwrap().call0().unwrap());
            }
            if failure.is_instance(py, &era.getattr("ParameterRangeError").unwrap()) {
                return error(py, "INVALID_ARGUMENT", failure.value(py).to_string(), false)
                    .unwrap_or(failure);
            }
            if failure.is_instance(py, &era.getattr("ERA5ExtractionError").unwrap()) {
                return error(
                    py,
                    "SOUNDING_FETCH_FAILED",
                    format!(
                        "The {} sounding could not be retrieved: {}",
                        config.getattr("label").unwrap(),
                        first_line(py, &failure)
                    ),
                    true,
                )
                .unwrap_or(failure);
            }
            failure
        })?;
    let (written, box_metadata) = if kwargs.contains("box_bounds")? {
        let metadata = written.get_item(1)?;
        location = metadata.get_item("location")?;
        (written.get_item(0)?, Some(metadata))
    } else {
        (written, None)
    };
    let data_path = if written.is_truthy()? {
        PyModule::import(py, "pathlib")?
            .getattr("Path")?
            .call1((written,))?
    } else {
        path
    };
    cancel.call_method0("check")?;
    progress.call_method1("stage", ("calculating",))?;
    let collection = api.call_method1("decode", (&data_path,))?.get_item(0)?;
    cancel.call_method0("check")?;
    let fields = PyDict::new(py);
    let point = point(lat, lon);
    let place = if location.is_truthy()? {
        location.str()?.to_str()?.to_owned()
    } else {
        point
    };
    fields.set_item("title", format!("{} {place}", config.getattr("label")?))?;
    fields.set_item("location", place)?;
    fields.set_item("latitude", lat)?;
    fields.set_item("longitude", lon)?;
    let mut run = api.call_method1("meta", (&collection, "run"))?;
    if !run.is_truthy()? {
        run = api.call_method1("meta", (&collection, "base_time"))?;
    }
    fields.set_item("run_time", p.call_method1("iso", (run,))?)?;
    fields.set_item("forecast_hour", fxx)?;
    fields.set_item("model", config.getattr("label")?)?;
    fields.set_item("provider", config.getattr("key")?)?;
    fields.set_item("provider_name", config.getattr("label")?)?;
    if let Some(metadata) = box_metadata {
        fields.set_item("box", metadata)?;
    }
    let summary = api
        .getattr("summary")?
        .call((&output.id, "model", &collection), Some(&fields))?;
    cancel.call_method0("check")?;
    let result = api.call_method1("register", (&output.id, data_path, summary, collection))?;
    output.committed = true;
    Ok(result)
}

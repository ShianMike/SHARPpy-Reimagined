use super::*;
use pyo3::types::{PyDict, PyTuple};

#[allow(clippy::too_many_arguments)] // Shared transport hooks and explicit HTTP mode.
fn transfer(
    py: Python<'_>,
    url: &str,
    plan: Vec<ByteRange>,
    output: PathBuf,
    session: Option<Py<PyAny>>,
    options: &Bound<'_, PyDict>,
    timeout: (f64, f64),
    full: bool,
) -> PyResult<u64> {
    let callback = |key| -> PyResult<Option<Py<PyAny>>> {
        Ok(options
            .get_item(key)?
            .filter(|v| !v.is_none())
            .map(Bound::unbind))
    };
    let callbacks = Arc::new(Callbacks {
        cancelled: callback("cancelled")?,
        progress: callback("progress")?,
        bytes: callback("on_bytes")?,
        error: Mutex::new(None),
    });
    let runtime = crate::weather::runtime()?;
    let transport: Box<dyn Transport> = if let Some(session) = session {
        Box::new(sessions::Sessions::new(session, None, timeout, false))
    } else {
        // Preserve monkeypatched Session adapters used by private integrations.
        let requests = PyModule::import(py, "requests")?;
        let factory = requests.getattr("Session")?;
        if !factory.is(&requests.getattr("sessions")?.getattr("Session")?) {
            Box::new(sessions::Sessions::new(
                factory.call0()?.unbind(),
                None,
                timeout,
                true,
            ))
        } else {
            Box::new(NativeTransport {
                client: Arc::new(
                    RangeClient::new(timeout.0, timeout.1)
                        .map_err(|e| transfer_error(py, Error::Unavailable(e)))?,
                ),
                runtime,
                callbacks: callbacks.clone(),
                stop: Arc::new(AtomicBool::new(false)),
            })
        }
    };
    let chunk = options
        .get_item("chunk_size")?
        .map(|v| v.extract::<usize>())
        .transpose()?
        .unwrap_or(256 * 1024);
    let result = py.detach(|| {
        range_transfer::compat::download(
            transport.as_ref(),
            callbacks.as_ref(),
            url,
            &plan,
            &output,
            chunk,
            full,
        )
    });
    result.map_err(|e| transfer_error(py, e))
}

#[pyfunction]
fn download_herbie_compat<'py>(
    py: Python<'py>,
    herbie: &Bound<'py, PyAny>,
    search: &str,
    options: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyTuple>> {
    let api = PyModule::import(py, "sharpmod.models.model_transport")?;
    let source = herbie.getattr("grib")?;
    if source.is_none() || !source.is_truthy()? {
        return Err(transfer_error(
            py,
            Error::Unavailable("Herbie compatibility fallback has no source payload".into()),
        ));
    }
    let os = PyModule::import(py, "os")?;
    let source: String = os.call_method1("fspath", (source,))?.extract()?;
    let pathlib = PyModule::import(py, "pathlib")?.getattr("Path")?;
    if let Some(dir) = options.get_item("save_dir")?.filter(|v| !v.is_none()) {
        herbie.setattr(
            "save_dir",
            pathlib.call1((dir,))?.call_method0("expanduser")?,
        )?;
    }
    let planned = (|| {
        let inventory =
            if let Some(value) = options.get_item("inventory")?.filter(|v| !v.is_none()) {
                value
            } else {
                herbie.call_method1("inventory", (search,))?
            }
            .call_method0("copy")?;
        let inventory = api.call_method1("inclusive_end_bytes", (herbie, inventory))?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("max_gap", 0)?;
        kwargs.set_item("max_overhead_ratio", 0.)?;
        let ranges = api
            .getattr("ranges_from_inventory")?
            .call((inventory,), Some(&kwargs))?;
        let plan = ranges
            .try_iter()?
            .map(|r| {
                let r = r?;
                Ok(ByteRange {
                    start: r.getattr("start")?.extract()?,
                    end: r.getattr("end")?.extract()?,
                })
            })
            .collect::<PyResult<Vec<_>>>()?;
        let output = pathlib.call1((herbie.call_method1("get_localFilePath", (search,))?,))?;
        Ok::<_, PyErr>((plan, output))
    })()
    .map_err(|e| {
        transfer_error(
            py,
            Error::Unavailable(format!(
                "Herbie compatibility fallback could not plan the subset: {}",
                e.value(py)
            )),
        )
    })?;
    let (plan, output) = planned;
    let timeout = options
        .get_item("timeout")?
        .map(|v| v.extract())
        .transpose()?
        .unwrap_or((10., 30.));
    let session = options
        .get_item("session")?
        .filter(|v| !v.is_none())
        .map(Bound::unbind);
    let count = transfer(
        py,
        &source,
        plan,
        output.extract()?,
        session,
        options,
        timeout,
        false,
    )?;
    (output.call_method0("resolve")?, count).into_pyobject(py)
}

#[pyfunction]
#[allow(clippy::too_many_arguments)] // Preserve the public NOMADS adapter signature.
fn download_nomads_native<'py>(
    py: Python<'py>,
    herbie: &Bound<'py, PyAny>,
    config: &Bound<'py, PyAny>,
    search: &str,
    fields: &Bound<'py, PyAny>,
    lat: f64,
    lon: f64,
    options: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyTuple>> {
    let api = PyModule::import(py, "sharpmod.models.model_sources")?;
    let source = herbie
        .getattr("SOURCES")?
        .call_method1("get", ("nomads",))?;
    if !source.is_truthy()? {
        return Err(PyErr::from_value(
            api.getattr("SourceRoutingUnavailable")?
                .call1(("Herbie model has no NOMADS source",))?,
        ));
    }
    let url = api
        .call_method1(
            "build_nomads_subset_url",
            (config, source.str()?, lat, lon, fields),
        )?
        .extract::<String>()?;
    let path = PyModule::import(py, "pathlib")?.getattr("Path")?;
    if let Some(dir) = options.get_item("save_dir")?.filter(|v| !v.is_none()) {
        herbie.setattr("save_dir", path.call1((dir,))?.call_method0("expanduser")?)?;
    }
    let output = path.call1((herbie.call_method1("get_localFilePath", (search,))?,))?;
    if api.call_method1("_valid_grib", (&output,))?.is_truthy()? {
        let count = output.call_method0("stat")?.getattr("st_size")?;
        return (output, count, url).into_pyobject(py);
    }
    if options
        .get_item("throttle")?
        .map(|v| v.is_truthy())
        .transpose()?
        .unwrap_or(true)
    {
        api.call_method1("_wait_for_nomads_slot", (options.get_item("cancelled")?,))?;
    }
    let session = options
        .get_item("session")?
        .filter(|v| !v.is_none())
        .map(Bound::unbind);
    let count = transfer(
        py,
        &url,
        Vec::new(),
        output.extract()?,
        session,
        options,
        (10., 120.),
        true,
    )
    .map_err(|failure| {
        if failure.is_instance(
            py,
            &PyModule::import(py, "sharpmod.models.model_transport")
                .unwrap()
                .getattr("DownloadCancelled")
                .unwrap(),
        ) {
            failure
        } else {
            PyErr::from_value(
                api.getattr("SourceRoutingUnavailable")
                    .unwrap()
                    .call1((format!(
                        "NOMADS geographic subset failed: {}",
                        failure.value(py)
                    ),))
                    .unwrap(),
            )
        }
    })?;
    (output, count, url).into_pyobject(py)
}
pub(super) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(download_herbie_compat, module)?)?;
    module.add_function(wrap_pyfunction!(download_nomads_native, module)?)
}

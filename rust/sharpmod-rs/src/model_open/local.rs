use super::*;
#[pyfunction]
pub(super) fn open_era5_sounding<'py>(
    py: Python<'py>,
    params: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyAny>> {
    let p = PyModule::import(py, "sharpmod.bridge.params")?;
    p.call_method1(
        "keys",
        (params, ("time", "latitude", "longitude"), ("location",)),
    )?;
    let when = p.call_method1("time", (params, "time"))?;
    if ["minute", "second", "microsecond"].into_iter().any(|k| {
        when.getattr(k)
            .and_then(|v| v.extract::<u32>())
            .is_ok_and(|v| v != 0)
    }) {
        return Err(PyErr::from_value(p.call_method1(
            "invalid",
            ("ERA5 time must be a whole UTC hour",),
        )?));
    }
    let lat: f64 = p
        .call_method1("number", (params, "latitude", -90., 90.))?
        .extract()?;
    let lon: f64 = p
        .call_method1("number", (params, "longitude", -180., 180.))?
        .extract()?;
    let location = p.call_method1("optional_text", (params, "location", 64))?;
    let mut output = Output::new(py)?;
    let path = output.path(py)?;
    let era = PyModule::import(py, "sharpmod.tools.era5_extract")?;
    let kwargs = PyDict::new(py);
    kwargs.set_item(
        "loc",
        if location.is_truthy()? {
            location.clone()
        } else {
            "ERA5pt".into_pyobject(py)?.into_any()
        },
    )?;
    let written = era
        .getattr("extract")?
        .call((lat, lon, when, path.str()?), Some(&kwargs))
        .map_err(|failure| {
            if failure.is_instance(py, &era.getattr("ParameterRangeError").unwrap()) {
                return error(py, "INVALID_ARGUMENT", failure.value(py).to_string(), false)
                    .unwrap_or(failure);
            }
            if failure.is_instance(py, &era.getattr("ERA5ExtractionError").unwrap()) {
                return error(
                    py,
                    "SOUNDING_FETCH_FAILED",
                    format!(
                        "The ERA5 sounding could not be retrieved: {}",
                        first_line(py, &failure)
                    ),
                    true,
                )
                .unwrap_or(failure);
            }
            failure
        })?;
    let path = if written.is_truthy()? {
        PyModule::import(py, "pathlib")?
            .getattr("Path")?
            .call1((written,))?
    } else {
        path
    };
    let cancel = PyModule::import(py, "sharpmod.bridge.cancellation")?;
    cancel.call_method0("check")?;
    let api = PyModule::import(py, "sharpmod.bridge.sounding_open")?;
    let collection = api.call_method1("decode", (&path,))?.get_item(0)?;
    let place = if location.is_truthy()? {
        location.str()?.to_str()?.to_owned()
    } else {
        point(lat, lon)
    };
    let fields = PyDict::new(py);
    fields.set_item("title", format!("ERA5 {place}"))?;
    fields.set_item("location", place)?;
    for (key, value) in [
        ("model", "ERA5"),
        ("provider", "cds"),
        ("provider_name", "Copernicus Climate Data Store"),
        (
            "source_url",
            "https://cds.climate.copernicus.eu/datasets/reanalysis-era5-pressure-levels",
        ),
    ] {
        fields.set_item(key, value)?;
    }
    let summary = api
        .getattr("summary")?
        .call((&output.id, "era5", &collection), Some(&fields))?;
    cancel.call_method0("check")?;
    let result = api.call_method1("register", (&output.id, path, summary, collection))?;
    output.committed = true;
    Ok(result)
}

#[pyfunction]
pub(super) fn open_wrf_sounding<'py>(
    py: Python<'py>,
    params: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyAny>> {
    let p = PyModule::import(py, "sharpmod.bridge.params")?;
    p.call_method1(
        "keys",
        (params, ("file_path", "time", "latitude", "longitude")),
    )?;
    let api = PyModule::import(py, "sharpmod.bridge.sounding_wrf")?;
    let source = api.call_method1("_wrf_path", (params,))?;
    let when = p.call_method1("time", (params, "time"))?;
    let lat: f64 = p
        .call_method1("number", (params, "latitude", -90., 90.))?
        .extract()?;
    let lon: f64 = p
        .call_method1("number", (params, "longitude", -180., 180.))?
        .extract()?;
    let wrf = PyModule::import(py, "sharpmod.tools.wrf_extract")?;
    let cancel = PyModule::import(py, "sharpmod.bridge.cancellation")?;
    let mut output = Output::new(py)?;
    let path = output.path(py)?;
    let collection = (|| {
        let domain = wrf.call_method1("inspect_file", (source.str()?,))?;
        let requested = p.call_method1("iso", (&when,))?;
        let mut present = false;
        for time in domain.get_item("times")?.try_iter()? {
            if p.call_method1("iso", (time?,))?.eq(&requested)? {
                present = true;
                break;
            }
        }
        if !present {
            return Err(PyErr::from_value(p.call_method1(
                "invalid",
                ("time must be one of the WRF file's forecast times",),
            )?));
        }
        if !wrf
            .call_method1("point_in_domain", (domain, lat, lon))?
            .is_truthy()?
        {
            return Err(PyErr::from_value(p.call_method1(
                "invalid",
                ("the requested point is outside this WRF domain",),
            )?));
        }
        let kwargs = PyDict::new(py);
        kwargs.set_item("valid_time", when)?;
        wrf.getattr("extract")?
            .call((source.str()?, lat, lon, path.str()?), Some(&kwargs))?;
        cancel.call_method0("check")?;
        api.call_method1("decode", (&path,))?.get_item(0)
    })()
    .map_err(|failure| {
        if failure.is_instance(py, &wrf.getattr("RetrievalError").unwrap())
            || failure.is_instance(py, &wrf.getattr("ParameterRangeError").unwrap())
            || failure.is_instance_of::<pyo3::exceptions::PyImportError>(py)
        {
            error(
                py,
                "SOUNDING_INVALID",
                failure.value(py).to_string().chars().take(500).collect(),
                false,
            )
            .unwrap_or(failure)
        } else {
            failure
        }
    })?;
    let name = source.getattr("name")?;
    let fields = PyDict::new(py);
    fields.set_item("title", format!("WRF {name}"))?;
    fields.set_item("file_name", name)?;
    for (k, v) in [
        ("model", "WRF-ARW"),
        ("provider", "local"),
        ("provider_name", "Local WRF NetCDF"),
    ] {
        fields.set_item(k, v)?;
    }
    let summary = api
        .getattr("summary")?
        .call((&output.id, "file", &collection), Some(&fields))?;
    cancel.call_method0("check")?;
    let result = api.call_method1("register", (&output.id, path, summary, collection))?;
    output.committed = true;
    Ok(result)
}

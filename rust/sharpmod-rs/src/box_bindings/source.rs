use super::*;

#[pyclass]
pub(super) struct Loader {
    pub config: Py<PyAny>,
    pub run: Py<PyAny>,
    pub fxx: u32,
}

#[pymethods]
impl Loader {
    #[pyo3(signature=(directory, **callbacks))]
    fn __call__<'py>(
        &self,
        py: Python<'py>,
        directory: String,
        callbacks: Option<&Bound<'py, PyDict>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let kwargs = callbacks
            .map(PyDictMethods::copy)
            .transpose()?
            .unwrap_or_else(|| PyDict::new(py));
        kwargs.set_item("download_dir", directory)?;
        PyModule::import(py, "sharpmod.tools.model_extract")?
            .getattr("_retrieve_dataset")?
            .call(
                (self.config.bind(py), self.run.bind(py), self.fxx),
                Some(&kwargs),
            )
    }
}

#[allow(clippy::too_many_arguments)]
pub(super) fn extract<'py>(
    py: Python<'py>,
    mx: &Bound<'py, PyModule>,
    lease: &Bound<'py, PyAny>,
    run: &Bound<'py, PyAny>,
    model: &str,
    fxx: u32,
    out_path: &str,
    bounds: Bounds,
    limits: Limits,
    progress: Option<&Py<PyAny>>,
    cancelled: Option<&Py<PyAny>>,
    started: Instant,
) -> PyResult<(String, Bound<'py, PyDict>)> {
    let entry = lease.get_item(0)?;
    let source_hit: bool = lease.get_item(1)?.extract()?;
    let source_ms = started.elapsed().as_secs_f64() * 1000.0;
    if is_cancelled(cancelled) {
        return Err(failure(py, Error::Cancelled));
    }
    let dataset = entry.getattr("dataset")?;
    if !dataset.is_instance(&mx.getattr("_LocalGribDataset")?)? {
        return Err(failure(
            py,
            Error::Data(
                "This model source did not provide native GRIB columns for area averaging".into(),
            ),
        ));
    }
    let stage = |value| -> PyResult<()> {
        if let Some(progress) = progress {
            progress.bind(py).call1((value,))?;
        }
        Ok(())
    };
    stage("decoding")?;
    let library: String = PyModule::import(py, "sharpmod.backends.rust_backend")?
        .getattr("_eccodes_library_path")?
        .call0()?
        .extract()?;
    let path: String = PyModule::import(py, "os")?
        .getattr("fspath")?
        .call1((dataset.getattr("path")?,))?
        .extract()?;
    let output = decode(py, path, library, bounds, limits, cancelled)?;
    let metadata = metadata::build(py, &output)?;
    metadata.set_item("source_cache_hit", source_hit)?;
    let timings = metadata
        .get_item("timings")?
        .expect("native metadata has timings");
    timings.set_item("data_access_ms", source_ms)?;
    let (longitude, latitude) = bounds.center();
    let label = format!(
        "Box {:.2}{} {:.2}{}",
        latitude.abs(),
        if latitude >= 0.0 { "N" } else { "S" },
        longitude.abs(),
        if longitude >= 0.0 { "E" } else { "W" }
    );
    metadata.set_item("location", &label)?;
    let decoded_tuple = crate::decoded_point_into_py(py, output.averaged.profile)?;
    let decoded = PyModule::import(py, "sharpmod.backends.grib")?
        .getattr("DecodedPoint")?
        .call1(decoded_tuple)?;
    let delta_args = PyDict::new(py);
    delta_args.set_item("hours", fxx)?;
    let valid = run.add(
        PyModule::import(py, "datetime")?
            .getattr("timedelta")?
            .call((), Some(&delta_args))?,
    )?;
    let dataset = mx.getattr("DecodedModelPointDataset")?.call1((
        decoded,
        valid,
        "Rust native area-weighted columns",
        "area-weighted pressure-aligned relative vorticity",
    ))?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("run_time", run)?;
    kwargs.set_item("fxx", fxx)?;
    kwargs.set_item("out_path", out_path)?;
    kwargs.set_item("loc", &label)?;
    kwargs.set_item("dataset", dataset)?;
    kwargs.set_item("source_grib", entry.getattr("source_grib")?)?;
    kwargs.set_item("source_fields", entry.getattr("source_fields")?)?;
    kwargs.set_item("source_transport", entry.getattr("source_transport")?)?;
    kwargs.set_item("progress_callback", progress)?;
    kwargs.set_item("cancelled", cancelled)?;
    stage("extracting")?;
    let writing = Instant::now();
    let written = mx
        .getattr("extract")?
        .call((model, latitude, longitude), Some(&kwargs))?
        .extract::<String>()?;
    trace_artifact::persist(py, &written, &output.display_profiles)?;
    timings.set_item("artifact_ms", writing.elapsed().as_secs_f64() * 1000.0)?;
    timings.set_item("total_ms", started.elapsed().as_secs_f64() * 1000.0)?;
    if is_cancelled(cancelled) {
        return Err(failure(py, Error::Cancelled));
    }
    metadata::persist(py, &written, &metadata)?;
    Ok((written, metadata))
}

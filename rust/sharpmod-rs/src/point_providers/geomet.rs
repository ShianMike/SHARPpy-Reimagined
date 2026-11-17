use super::*;
use rayon::prelude::*;
use std::collections::HashMap;
type PointResult = (String, Option<u16>, Py<PyAny>);

#[pyfunction]
#[allow(clippy::too_many_arguments)] // Keep the existing provider adapter signature.
fn geomet_point_value<'py>(
    py: Python<'py>,
    capability: &Bound<'py, PyAny>,
    variable: &str,
    level: Option<u16>,
    lat: f64,
    lon: f64,
    valid: &Bound<'py, PyAny>,
    reference: &Bound<'py, PyAny>,
    request_get: &Bound<'py, PyAny>,
    cancelled: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let api = PyModule::import(py, "sharpmod.providers.eccc_geomet")?;
    let params = api.call_method1(
        "build_feature_info_params",
        (capability, variable, level, lat, lon, valid, reference),
    )?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("params", &params)?;
    kwargs.set_item("cancelled", cancelled)?;
    let response = api
        .getattr("_request")?
        .call((request_get,), Some(&kwargs))?;
    let parsed = (|| {
        let feature = response
            .call_method0("json")?
            .get_item("features")?
            .get_item(0)?;
        let props = feature.get_item("properties")?;
        let coords = feature.get_item("geometry")?.get_item("coordinates")?;
        let number = py.get_type::<pyo3::types::PyFloat>();
        Ok::<_, PyErr>((
            number
                .call1((props.get_item("value")?,))?
                .extract::<f64>()?,
            number.call1((coords.get_item(1)?,))?.extract::<f64>()?,
            number.call1((coords.get_item(0)?,))?.extract::<f64>()?,
            api.call_method1("_as_utc", (props.get_item("time")?,))?,
            api.call_method1("_as_utc", (props.get_item("dim_reference_time")?,))?,
        ))
    })()
    .map_err(|e| {
        error(
            &api,
            "RetrievalError",
            format!(
                "GeoMet returned no usable point value for {}",
                params.get_item("LAYERS").unwrap()
            ),
        )
        .unwrap_or(e)
    })?;
    let (value, selected_lat, selected_lon, returned_valid, returned_reference) = parsed;
    if !value.is_finite() {
        return Err(error(
            &api,
            "RetrievalError",
            format!(
                "GeoMet returned a non-finite value for {}",
                params.get_item("LAYERS")?
            ),
        )?);
    }
    if !selected_lat.is_finite() || !selected_lon.is_finite() || selected_lat.abs() > 90. {
        return Err(error(
            &api,
            "RetrievalError",
            "GeoMet returned invalid point coordinates".into(),
        )?);
    }
    for (actual, expected, label) in [
        (&returned_valid, valid, "valid time"),
        (&returned_reference, reference, "run"),
    ] {
        if !actual.eq(api.call_method1("_as_utc", (expected,))?)? {
            return Err(error(
                &api,
                "RetrievalError",
                format!(
                    "GeoMet selected {label} {} instead of {}",
                    api.call_method1("_iso", (actual,))?,
                    api.call_method1("_iso", (expected,))?
                ),
            )?);
        }
    }
    let fields = PyDict::new(py);
    fields.set_item("variable", variable)?;
    fields.set_item("level", level)?;
    fields.set_item("value", value)?;
    fields.set_item("selected_lat", selected_lat)?;
    fields.set_item(
        "selected_lon",
        (selected_lon + 180.).rem_euclid(360.) - 180.,
    )?;
    fields.set_item("valid_time", returned_valid)?;
    fields.set_item("reference_time", returned_reference)?;
    api.getattr("_PointValue")?.call((), Some(&fields))
}

#[pyfunction]
fn fetch_geomet_point<'py>(
    py: Python<'py>,
    model: &Bound<'py, PyAny>,
    lat: f64,
    lon: f64,
    options: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyAny>> {
    let api = PyModule::import(py, "sharpmod.providers.eccc_geomet")?;
    let capability = api.call_method1("get_capability", (model,))?;
    let label = capability.getattr("label")?.extract::<String>()?;
    let lon = (lon + 180.).rem_euclid(360.) - 180.;
    if !(-90. ..=90.).contains(&lat) {
        return Err(error(
            &api,
            "ParameterRangeError",
            format!("latitude {lat:.4} is outside [-90, 90]"),
        )?);
    }
    if !api
        .call_method1(
            "point_in_domain",
            (capability.getattr("model_key")?, lat, lon),
        )?
        .is_truthy()?
    {
        return Err(error(
            &api,
            "ParameterRangeError",
            format!("{label} does not cover {lat:.4}, {lon:.4}"),
        )?);
    }
    let fxx: i64 = options
        .get_item("fxx")?
        .map(|v| v.extract())
        .transpose()?
        .unwrap_or(0);
    if !capability.getattr("forecast_hours")?.contains(fxx)? {
        return Err(error(
            &api,
            "ParameterRangeError",
            format!("{label} forecast hour F{fxx:03} is unavailable"),
        )?);
    }
    let request = options
        .get_item("request_get")?
        .filter(|v| !v.is_none())
        .unwrap_or(api.getattr("_default_get")?);
    let cancelled = options
        .get_item("cancelled")?
        .unwrap_or_else(|| py.None().into_bound(py));
    let progress = options
        .get_item("progress_callback")?
        .unwrap_or_else(|| py.None().into_bound(py));
    api.call_method1("_emit", (&progress, "locating"))?;
    let run = if let Some(run) = options.get_item("run_time")?.filter(|v| !v.is_none()) {
        api.call_method1("_floor_cycle", (run, capability.getattr("cycles")?))?
    } else {
        let kw = PyDict::new(py);
        kw.set_item("request_get", &request)?;
        kw.set_item("cancelled", &cancelled)?;
        api.getattr("latest_reference_time")?
            .call((&capability,), Some(&kw))?
    };
    let kw = PyDict::new(py);
    kw.set_item("hours", fxx)?;
    let valid = run.call_method1(
        "__add__",
        (PyModule::import(py, "datetime")?
            .getattr("timedelta")?
            .call((), Some(&kw))?,),
    )?;
    let workers: usize = api
        .call_method1("worker_count", (options.get_item("max_workers")?,))?
        .extract()?;
    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(workers)
        .build()
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    api.call_method1("_emit", (&progress, "downloading"))?;
    let analysis: Vec<String> = api
        .getattr("_ANALYSIS_ONLY_SURFACE")?
        .try_iter()?
        .map(|v| v?.extract())
        .collect::<PyResult<_>>()?;
    let execute = |tasks: &[(String, Option<u16>)]| -> PyResult<Vec<PointResult>> {
        let cap = capability.clone().unbind();
        let request = request.clone().unbind();
        let cancelled = cancelled.clone().unbind();
        let run = run.clone().unbind();
        let valid = valid.clone().unbind();
        let results: Vec<_> = py.detach(|| {
            pool.install(|| {
                tasks
                    .par_iter()
                    .map(|(variable, level)| {
                        Python::attach(|py| {
                            if !cancelled.bind(py).is_none()
                                && cancelled.bind(py).call0()?.is_truthy()?
                            {
                                return Err(PyErr::from_value(
                                    PyModule::import(py, "sharpmod.models.model_transport")?
                                        .getattr("DownloadCancelled")?
                                        .call1(("ECCC GeoMet extraction cancelled",))?,
                                ));
                            }
                            let api = PyModule::import(py, "sharpmod.providers.eccc_geomet")?;
                            let kwargs = PyDict::new(py);
                            kwargs.set_item("request_get", request.bind(py))?;
                            kwargs.set_item("cancelled", cancelled.bind(py))?;
                            let when = if level.is_none() && analysis.contains(variable) {
                                run.bind(py)
                            } else {
                                valid.bind(py)
                            };
                            api.getattr("_fetch_value")?
                                .call(
                                    (cap.bind(py), variable, level, lat, lon, when, run.bind(py)),
                                    Some(&kwargs),
                                )
                                .map(|p| (variable.clone(), *level, p.unbind()))
                        })
                    })
                    .collect()
            })
        });
        let mut values = Vec::new();
        for ((variable, level), result) in tasks.iter().zip(results) {
            match result {
                Ok(value) => values.push(value),
                Err(failure) => {
                    if failure.is_instance(
                        py,
                        &PyModule::import(py, "sharpmod.models.model_transport")?
                            .getattr("DownloadCancelled")?,
                    ) {
                        return Err(failure);
                    }
                    if variable == "VerticalVelocity" {
                        continue;
                    }
                    let message = if let Some(level) = level {
                        format!(
                            "{label} point profile is incomplete at {level} mb ({variable}): {}",
                            failure.value(py)
                        )
                    } else {
                        format!(
                            "{label} verified surface is incomplete ({variable}): {}",
                            failure.value(py)
                        )
                    };
                    return Err(error(&api, "RetrievalError", message)?);
                }
            }
        }
        Ok(values)
    };
    let surface_names: Vec<String> = api.getattr("_SURFACE_VARIABLES")?.extract()?;
    let surface_tasks: Vec<_> = surface_names.into_iter().map(|n| (n, None)).collect();
    let mut points = execute(&surface_tasks)?;
    let values=|points:&[(String,Option<u16>,Py<PyAny>)]|->PyResult<HashMap<(String,Option<u16>),f64>> {points.iter().map(|(name,level,p)|Ok(((name.clone(),*level),p.bind(py).getattr("value")?.extract()?))).collect()};
    let mut data = values(&points)?;
    let mut pressure = data[&("SurfacePressure".into(), None)];
    if pressure > 2000. {
        pressure /= 100.;
    }
    let requested: Vec<u16> = capability.getattr("pressure_levels")?.extract()?;
    let levels: Vec<_> = requested
        .iter()
        .copied()
        .filter(|&p| f64::from(p) < pressure)
        .collect();
    if levels.is_empty() {
        return Err(error(
            &api,
            "RetrievalError",
            format!("{label} surface pressure {pressure:.1} hPa leaves no atmospheric levels"),
        )?);
    }
    let required: Vec<String> = api.getattr("_REQUIRED_VARIABLES")?.extract()?;
    let mut tasks = Vec::new();
    for &level in &levels {
        for name in &required {
            tasks.push((name.clone(), Some(level)));
        }
    }
    for level in capability.getattr("omega_levels")?.extract::<Vec<u16>>()? {
        if levels.contains(&level) {
            tasks.push(("VerticalVelocity".into(), Some(level)));
        }
    }
    let result = execute(&tasks)?;
    data.extend(values(&result)?);
    points.extend(result);
    let mut lats = Vec::new();
    let mut lons = Vec::new();
    for (_, _, point) in &points {
        lats.push(point.bind(py).getattr("selected_lat")?.extract::<f64>()?);
        lons.push(point.bind(py).getattr("selected_lon")?.extract::<f64>()?);
    }
    let median = |mut v: Vec<f64>| {
        v.sort_by(f64::total_cmp);
        let n = v.len();
        if n.is_multiple_of(2) {
            (v[n / 2 - 1] + v[n / 2]) * 0.5
        } else {
            v[n / 2]
        }
    };
    let selected_lat = median(lats.clone());
    let selected_lon = median(lons.clone());
    if lats.iter().zip(&lons).any(|(a, b)| {
        (a - selected_lat).abs() > 0.25
            || ((b - selected_lon + 180.).rem_euclid(360.) - 180.).abs() > 0.25
    }) {
        return Err(error(
            &api,
            "RetrievalError",
            "GeoMet layers selected inconsistent grid points".into(),
        )?);
    }
    let get = |name: &str, level: Option<u16>| {
        data.get(&(name.to_owned(), level))
            .copied()
            .unwrap_or(f64::NAN)
    };
    let mut rows = Vec::new();
    for &level in &levels {
        let p = f64::from(level);
        let q = get("SpecificHumidity", Some(level));
        let vapor = q * p / (0.622 + 0.378 * q);
        let l = (vapor.max(1e-6) / 6.112).ln();
        let dew = 243.04 * l / (17.625 - l);
        let dir = get("WindDir", Some(level));
        let speed = get("WindSpeed", Some(level));
        let r = dir.to_radians();
        rows.push([
            p,
            get("GeopotentialHeight", Some(level)),
            get("AirTemp", Some(level)),
            dew,
            dir,
            speed * 1.94384449,
            get("VerticalVelocity", Some(level)),
            -speed * r.sin(),
            -speed * r.cos(),
        ]);
    }
    let speed = get("SurfaceWindSpeed", None);
    let dir = get("SurfaceWindDir", None).to_radians();
    let merge = merged(
        py,
        &rows,
        [
            pressure,
            get("SurfaceHeight", None),
            get("SurfaceTemperature", None),
            get("SurfaceDewpoint", None),
            -speed * dir.sin(),
            -speed * dir.cos(),
        ],
    )?;
    if merge.is_none() {
        return Err(error(
            &api,
            "RetrievalError",
            format!("{label} verified surface fields failed physical validation"),
        )?);
    }
    api.call_method1("_emit", (&progress, "extracting"))?;
    let fields = PyDict::new(py);
    fields.set_item("capability", capability)?;
    fields.set_item("columns", merge.getattr("columns")?)?;
    for (k, v) in [
        ("requested_lat", lat),
        ("requested_lon", lon),
        ("selected_lat", selected_lat),
        ("selected_lon", selected_lon),
    ] {
        fields.set_item(k, v)?;
    }
    fields.set_item("run_time", run)?;
    fields.set_item("valid_time", valid)?;
    fields.set_item("fxx", fxx)?;
    fields.set_item("request_count", surface_tasks.len() + tasks.len())?;
    fields.set_item("max_workers", workers)?;
    fields.set_item("surface_pressure_hpa", merge.getattr("surface_pressure")?)?;
    fields.set_item(
        "below_ground_levels_removed",
        requested.len() - levels.len() + merge.getattr("removed_levels")?.extract::<usize>()?,
    )?;
    api.getattr("GeoMetPointDataset")?.call((), Some(&fields))
}
pub(super) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(fetch_geomet_point, module)?)?;
    module.add_function(wrap_pyfunction!(geomet_point_value, module)?)
}

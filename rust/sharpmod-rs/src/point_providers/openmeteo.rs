use super::*;
#[pyfunction]
#[pyo3(signature=(hourly,name,index,api_model))]
pub(super) fn openmeteo_hourly_value(
    hourly: &Bound<'_, PyDict>,
    name: &str,
    index: usize,
    api_model: &str,
) -> PyResult<Option<f64>> {
    let py = hourly.py();
    let value = hourly
        .get_item(name)?
        .filter(|v| !v.is_none())
        .or(hourly.get_item(format!("{name}_{api_model}"))?);
    let Some(series) = value else {
        return Ok(None);
    };
    if !series.is_instance_of::<pyo3::types::PyList>()
        && !series.is_instance_of::<pyo3::types::PyTuple>()
    {
        return Ok(None);
    }
    if index >= series.len()? {
        return Ok(None);
    }
    let value = series.get_item(index)?;
    if value.is_none() {
        return Ok(None);
    }
    Ok(py
        .get_type::<pyo3::types::PyFloat>()
        .call1((value,))
        .and_then(|v| v.extract::<f64>())
        .ok()
        .filter(|v| v.is_finite()))
}
#[pyfunction]
pub(super) fn openmeteo_surface_height(pairs: Vec<(f64, f64)>, pressure: f64) -> PyResult<f64> {
    let compute = || -> Option<f64> {
        if pairs.len() < 2 || pressure <= 0. {
            return None;
        }
        let pair = pairs
            .windows(2)
            .find(|p| p[0].0 >= pressure && pressure >= p[1].0)
            .unwrap_or_else(|| {
                if pressure > pairs[0].0 {
                    &pairs[..2]
                } else {
                    &pairs[pairs.len() - 2..]
                }
            });
        let [(p1, h1), (p2, h2)] = pair else {
            unreachable!()
        };
        if *p1 <= 0. || *p2 <= 0. || p1 == p2 {
            return None;
        }
        let result = h1 + (h2 - h1) / (p2.ln() - p1.ln()) * (pressure.ln() - p1.ln());
        result.is_finite().then_some(result)
    };
    compute().ok_or_else(|| {
        pyo3::exceptions::PyValueError::new_err(
            "Open-Meteo pressure levels are unusable for a surface height",
        )
    })
}
#[pyfunction]
fn fetch_openmeteo_point<'py>(
    py: Python<'py>,
    model_key: &str,
    lat: f64,
    lon: f64,
    options: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyAny>> {
    let api = PyModule::import(py, "sharpmod.providers.openmeteo")?;
    let runtime = PyModule::import(py, "sharpmod.providers.openmeteo_runtime")?;
    let capability = api.call_method1("get_capability", (model_key,))?;
    let label = capability.getattr("label")?.extract::<String>()?;
    let access = options
        .get_item("access")?
        .unwrap_or_else(|| py.None().into_bound(py));
    let access = if access.is_none() {
        runtime.call_method0("resolve_access")?
    } else {
        access
    };
    access.call_method0("require_ready")?;
    let lon = (lon + 180.).rem_euclid(360.) - 180.;
    if !(-90. ..=90.).contains(&lat) {
        return Err(error(
            &api,
            "ParameterRangeError",
            format!("latitude {lat:.4} is outside -90..90"),
        )?);
    }
    if !api
        .call_method1("point_in_domain", (&capability, lat, lon))?
        .is_truthy()?
    {
        return Err(error(
            &api,
            "ParameterRangeError",
            format!("{lat:.4}, {lon:.4} is outside the {label} domain"),
        )?);
    }
    let kwargs = PyDict::new(py);
    kwargs.set_item("now", options.get_item("now")?)?;
    let run = api
        .getattr("resolve_run_time")?
        .call((&capability, options.get_item("run_time")?), Some(&kwargs))?;
    if !api
        .call_method1("run_is_archived", (&capability, &run))?
        .is_truthy()?
    {
        return Err(error(
            &api,
            "RetrievalError",
            format!(
                "{label} runs are archived from {}; {} is earlier",
                capability
                    .getattr("archive_start")?
                    .call_method0("isoformat")?,
                run.call_method1("strftime", ("%Y-%m-%d %H:%MZ",))?
            ),
        )?);
    }
    let hour: i64 = options
        .get_item("fxx")?
        .map(|v| v.extract())
        .transpose()?
        .unwrap_or(0);
    let available: Vec<i64> = capability
        .call_method1("hours_for_cycle", (run.getattr("hour")?,))?
        .extract()?;
    if !available.contains(&hour) {
        return Err(error(
            &api,
            "ParameterRangeError",
            format!(
                "F{hour:03} is not published by the {label} {:02}Z run (available up to F{:03})",
                run.getattr("hour")?.extract::<i64>()?,
                available.iter().max().copied().unwrap_or(0)
            ),
        )?);
    }
    let delta = PyDict::new(py);
    delta.set_item("hours", hour)?;
    let valid = run.call_method1(
        "__add__",
        (PyModule::import(py, "datetime")?
            .getattr("timedelta")?
            .call((), Some(&delta))?,),
    )?;
    let cancel = options.get_item("cancelled")?;
    let progress = options.get_item("progress_callback")?;
    api.call_method1("_check_cancelled", (&cancel,))?;
    api.call_method1("_emit", (&progress, "locating"))?;
    let query = api.call_method1("build_query", (&capability, lat, lon, &run, &valid))?;
    api.call_method1("_emit", (&progress, "downloading"))?;
    let kwargs = PyDict::new(py);
    kwargs.set_item("session", options.get_item("session")?)?;
    kwargs.set_item("request_get", options.get_item("request_get")?)?;
    let response = runtime
        .getattr("fetch_json")?
        .call((&access, query), Some(&kwargs))?;
    api.call_method1("_check_cancelled", (&cancel,))?;
    api.call_method1("_emit", (&progress, "extracting"))?;
    let selected_lat: f64 = api
        .call_method1(
            "_require_float",
            (response.call_method1("get", ("latitude",))?, "latitude"),
        )?
        .extract()?;
    let selected_lon: f64 = api
        .call_method1(
            "_require_float",
            (response.call_method1("get", ("longitude",))?, "longitude"),
        )?
        .extract()?;
    let selected_lon = (selected_lon + 180.).rem_euclid(360.) - 180.;
    let hourly = response.call_method1("get", ("hourly",))?;
    let hourly = match hourly.cast::<PyDict>() {
        Ok(hourly) => hourly,
        Err(_) => {
            return Err(error(
                &api,
                "RetrievalError",
                format!("Open-Meteo response carried no hourly block for {label}"),
            )?)
        }
    };
    let index: usize = api
        .call_method1(
            "_select_time_index",
            (hourly.get_item("time")?, &valid, &capability, &run),
        )?
        .extract()?;
    let model = capability.getattr("api_model")?.extract::<String>()?;
    let surface_names: Vec<String> = api.getattr("SURFACE_VARIABLES")?.extract()?;
    let mut surface = std::collections::HashMap::new();
    for name in surface_names {
        let Some(value) = openmeteo_hourly_value(hourly, &name, index, &model)? else {
            return Err(error(
                &api,
                "RetrievalError",
                format!(
                    "{label} verified surface is incomplete at {}: {name} is missing",
                    valid.call_method1("strftime", ("%Y-%m-%d %H:%MZ",))?
                ),
            )?);
        };
        surface.insert(name, value);
    }
    let mut pressure = surface["surface_pressure"];
    if pressure > 2000. {
        pressure /= 100.;
    }
    let requested: Vec<f64> = capability.getattr("pressure_levels")?.extract()?;
    let families: Vec<String> = api.getattr("PRESSURE_FAMILIES")?.extract()?;
    let mut complete = Vec::new();
    for &level in &requested {
        let mut row = std::collections::HashMap::new();
        for family in &families {
            if let Some(value) = openmeteo_hourly_value(
                hourly,
                &format!("{family}_{}hPa", level as i64),
                index,
                &model,
            )? {
                row.insert(family.as_str(), value);
            } else {
                break;
            }
        }
        if row.len() == families.len() {
            complete.push((level, row));
        }
    }
    if complete.len() < 2 {
        return Err(error(&api,"RetrievalError",format!("{label} returned no usable pressure level at {}. Every level needs all of: {}. A whole family may be unpublished for this model.",valid.call_method1("strftime",("%Y-%m-%d %H:%MZ",))?,families.join(", ")))?);
    }
    let height = openmeteo_surface_height(
        complete
            .iter()
            .map(|(p, r)| (*p, r["geopotential_height"]))
            .collect(),
        pressure,
    )
    .map_err(|e| error(&api, "RetrievalError", e.value(py).to_string()).unwrap_or(e))?;
    let mut rows = Vec::new();
    let knots: f64 = api.getattr("KNOTS_PER_MS")?.extract()?;
    for (p, row) in complete {
        if p >= pressure {
            continue;
        }
        let direction = row["wind_direction"];
        let speed = row["wind_speed"];
        let radians = direction.to_radians();
        rows.push([
            p,
            row["geopotential_height"],
            row["temperature"],
            row["dew_point"],
            direction,
            speed * knots,
            -9999.,
            -speed * radians.sin(),
            -speed * radians.cos(),
        ]);
    }
    let minimum: usize = api.getattr("MIN_USABLE_LEVELS")?.extract()?;
    if rows.len() < minimum {
        return Err(error(&api,"RetrievalError",format!("{label} returned only {} complete pressure levels above {pressure:0.1} hPa; at least {minimum} are needed",rows.len()))?);
    }
    let top: f64 = api.getattr("REQUIRED_TOP_PRESSURE_HPA")?.extract()?;
    let highest = rows.iter().map(|r| r[0]).fold(f64::INFINITY, f64::min);
    if highest > top {
        return Err(error(
            &api,
            "RetrievalError",
            format!("{label} profile stops at {highest:0.1} hPa and must reach {top:0.1} hPa"),
        )?);
    }
    let direction = surface["wind_direction_10m"].to_radians();
    let speed = surface["wind_speed_10m"];
    let merge = merged(
        py,
        &rows,
        [
            pressure,
            height,
            surface["temperature_2m"],
            surface["dew_point_2m"],
            -speed * direction.sin(),
            -speed * direction.cos(),
        ],
    )?;
    if merge.is_none() {
        return Err(error(&api,"RetrievalError",format!("{label} verified surface fields failed physical validation (surface pressure {pressure:0.1} hPa, height {height:0.1} m)"))?);
    }
    let fields = PyDict::new(py);
    fields.set_item("capability", &capability)?;
    fields.set_item("columns", merge.getattr("columns")?)?;
    for (k, v) in [
        ("requested_lat", lat),
        ("requested_lon", lon),
        ("selected_lat", selected_lat),
        ("selected_lon", selected_lon),
        ("surface_height_m", height),
    ] {
        fields.set_item(k, v)?;
    }
    fields.set_item(
        "provider_elevation_m",
        api.call_method1("_provider_elevation", (&response,))?,
    )?;
    fields.set_item("run_time", run)?;
    fields.set_item("valid_time", valid)?;
    fields.set_item("fxx", hour)?;
    fields.set_item("request_count", 1)?;
    let variables = capability.call_method0("variable_count")?;
    fields.set_item("variable_count", &variables)?;
    fields.set_item(
        "weighted_units",
        api.call_method1("weighted_units", (variables,))?,
    )?;
    fields.set_item("surface_pressure_hpa", merge.getattr("surface_pressure")?)?;
    fields.set_item("levels_requested", requested.len())?;
    fields.set_item("levels_retained", rows.len())?;
    fields.set_item(
        "below_ground_levels_removed",
        requested.len() - rows.len() + merge.getattr("removed_levels")?.extract::<usize>()?,
    )?;
    fields.set_item("access_mode", access.getattr("mode")?)?;
    fields.set_item(
        "generation_time_ms",
        response.call_method1("get", ("generationtime_ms", 0.))?,
    )?;
    api.getattr("OpenMeteoPointDataset")?
        .call((), Some(&fields))
}
pub(super) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(fetch_openmeteo_point, module)?)?;
    module.add_function(wrap_pyfunction!(openmeteo_hourly_value, module)?)?;
    module.add_function(wrap_pyfunction!(openmeteo_surface_height, module)?)
}

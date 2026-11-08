//! Native box orchestration through the existing source lease and artifact writer.

mod metadata;
mod source;
#[cfg(test)]
mod source_cache_tests;
#[cfg(test)]
mod tests;
mod trace_artifact;
mod trace_plot;
mod trace_render;
#[cfg(test)]
mod trace_tests;

use crate::{
    box_soundings::{geometry::Bounds, Error, Limits},
    grib::area,
};
use pyo3::{
    prelude::*,
    types::{PyBool, PyDict},
};
use std::{path::Path, time::Instant};

fn failure(py: Python<'_>, error: Error) -> PyErr {
    let (module, kind) = match error {
        Error::Cancelled => ("sharpmod.models.model_transport", "DownloadCancelled"),
        Error::Invalid(_) => ("sharpmod.tools.era5_extract", "ParameterRangeError"),
        Error::Data(_) => ("sharpmod.tools.era5_extract", "RetrievalError"),
    };
    let construct = || -> PyResult<PyErr> {
        Ok(PyErr::from_value(
            PyModule::import(py, module)?
                .getattr(kind)?
                .call1((error.to_string(),))?,
        ))
    };
    construct().unwrap_or_else(|error| error)
}

fn bounds(value: &Bound<'_, PyAny>) -> PyResult<Bounds> {
    let value = value.cast::<PyDict>().map_err(|_| {
        failure(
            value.py(),
            Error::Invalid("box must be a geographic bounds object".into()),
        )
    })?;
    if value.len() != 4 {
        return Err(failure(
            value.py(),
            Error::Invalid("box must contain west, south, east and north".into()),
        ));
    }
    let number = |key| -> PyResult<f64> {
        let number = value
            .get_item(key)?
            .ok_or_else(|| failure(value.py(), Error::Invalid(format!("box is missing {key}"))))?;
        if number.is_instance_of::<PyBool>() {
            return Err(failure(
                value.py(),
                Error::Invalid(format!("box {key} must be a number")),
            ));
        }
        number.extract::<f64>().map_err(|_| {
            failure(
                value.py(),
                Error::Invalid(format!("box {key} must be a number")),
            )
        })
    };
    Bounds::new(
        number("west")?,
        number("south")?,
        number("east")?,
        number("north")?,
    )
    .map_err(|error| failure(value.py(), error))
}

fn configured_limits(py: Python<'_>) -> PyResult<Limits> {
    let defaults = Limits::default();
    let parse = |key: &str, default: f64| -> PyResult<f64> {
        match std::env::var(key) {
            Ok(value) => value
                .parse()
                .map_err(|_| failure(py, Error::Invalid(format!("Invalid {key} server setting")))),
            Err(std::env::VarError::NotPresent) => Ok(default),
            Err(_) => Err(failure(
                py,
                Error::Invalid(format!("Invalid {key} server setting")),
            )),
        }
    };
    let max_columns = parse("SHARPMOD_BOX_MAX_COLUMNS", defaults.max_columns as f64)?;
    if max_columns.fract() != 0.0 || !(1.0..=65536.0).contains(&max_columns) {
        return Err(failure(
            py,
            Error::Invalid("Invalid SHARPMOD_BOX_MAX_COLUMNS server setting".into()),
        ));
    }
    Limits {
        max_columns: max_columns as usize,
        max_area_km2: parse("SHARPMOD_BOX_MAX_AREA_KM2", defaults.max_area_km2)?,
        minimum_coverage: parse("SHARPMOD_BOX_MIN_COVERAGE", defaults.minimum_coverage)?,
    }
    .validate()
    .map_err(|error| failure(py, error))
}

fn is_cancelled(callback: Option<&Py<PyAny>>) -> bool {
    callback.is_some_and(|callback| {
        Python::attach(|py| {
            callback
                .bind(py)
                .call0()
                .and_then(|value| value.is_truthy())
        })
        .unwrap_or(true)
    })
}

fn decode(
    py: Python<'_>,
    path: String,
    library: String,
    bounds: Bounds,
    limits: Limits,
    cancelled: Option<&Py<PyAny>>,
) -> PyResult<area::Output> {
    py.detach(|| {
        area::decode(
            Path::new(&path),
            Path::new(&library),
            bounds,
            limits,
            &|| is_cancelled(cancelled),
        )
    })
    .map_err(|error| failure(py, error))
}

#[pyfunction(signature=(path, eccodes_library_path, box_bounds, cancelled=None))]
fn decode_grib_box<'py>(
    py: Python<'py>,
    path: String,
    eccodes_library_path: String,
    box_bounds: &Bound<'py, PyAny>,
    cancelled: Option<Py<PyAny>>,
) -> PyResult<(crate::PyDecodedPoint<'py>, Bound<'py, PyDict>)> {
    let output = decode(
        py,
        path,
        eccodes_library_path,
        bounds(box_bounds)?,
        configured_limits(py)?,
        cancelled.as_ref(),
    )?;
    let metadata = metadata::build(py, &output)?;
    Ok((
        crate::decoded_point_into_py(py, output.averaged.profile)?,
        metadata,
    ))
}

#[pyfunction(signature=(model, lat, lon, *, box_bounds, run_time=None, fxx=0, out_path, loc=None, download_dir=None, progress_callback=None, cancelled=None, on_bytes=None, source_cache))]
#[allow(clippy::too_many_arguments)]
fn extract_box_sounding<'py>(
    py: Python<'py>,
    model: String,
    lat: f64,
    lon: f64,
    box_bounds: &Bound<'py, PyAny>,
    run_time: Option<Py<PyAny>>,
    fxx: u32,
    out_path: String,
    loc: Option<String>,
    download_dir: Option<String>,
    progress_callback: Option<Py<PyAny>>,
    cancelled: Option<Py<PyAny>>,
    on_bytes: Option<Py<PyAny>>,
    source_cache: &Bound<'py, PyAny>,
) -> PyResult<(String, Bound<'py, PyDict>)> {
    let _ = (loc, download_dir); // Existing extract call's compatibility arguments.
    let started = Instant::now();
    let bounds = bounds(box_bounds)?;
    let (longitude, latitude) = bounds.center();
    if (latitude - lat).abs() > 1e-6 || (longitude - lon).abs() > 1e-6 {
        return Err(failure(
            py,
            Error::Invalid("The box centre must match the sounding coordinates".into()),
        ));
    }
    let limits = configured_limits(py)?;
    if is_cancelled(cancelled.as_ref()) {
        return Err(failure(py, Error::Cancelled));
    }
    let mx = PyModule::import(py, "sharpmod.tools.model_extract")?;
    let config = mx
        .getattr("_require_selectable")?
        .call1((mx.getattr("get_config")?.call1((&model,))?,))?;
    let model = config.getattr("key")?.extract::<String>()?;
    if mx.getattr("OPENMETEO_POINT_KEYS")?.contains(&model)?
        || mx.getattr("ECCC_POINT_KEYS")?.contains(&model)?
    {
        return Err(failure(py, Error::Invalid("This source supplies point profiles only; choose a native GRIB model for a box sounding".into())));
    }
    let run = mx.getattr("_run_datetime")?.call1((run_time, &config))?;
    let hours: Vec<u32> = mx.getattr("forecast_hours")?.call1((&config,))?.extract()?;
    if !hours.contains(&fxx) {
        return Err(failure(
            py,
            Error::Invalid("The forecast hour is unavailable for this model".into()),
        ));
    }
    let key_type =
        PyModule::import(py, "sharpmod.models.model_hour_cache")?.getattr("ModelHourKey")?;
    let key_kwargs = PyDict::new(py);
    key_kwargs.set_item(
        "member",
        config.getattr("kwargs")?.call_method1("get", ("member",))?,
    )?;
    // Share compatible full-grid leases with point extraction. Coordinate-bound
    // point sources need a separate full-grid identity for every box in this hour.
    if !mx
        .getattr("spatial_cache_key")?
        .call1((&config, latitude, longitude))?
        .is_none()
    {
        key_kwargs.set_item("spatial", "native-box-full-grid-v1")?;
    }
    let key = key_type.call_method("create", (&model, &run, fxx), Some(&key_kwargs))?;
    let loader = Py::new(
        py,
        source::Loader {
            config: config.unbind(),
            run: run.clone().unbind(),
            fxx,
        },
    )?;
    let lease_kwargs = PyDict::new(py);
    lease_kwargs.set_item("cancelled", cancelled.as_ref())?;
    lease_kwargs.set_item("progress_callback", progress_callback.as_ref())?;
    lease_kwargs.set_item("on_bytes", on_bytes.as_ref())?;
    let context = source_cache.call_method("lease", (key, loader), Some(&lease_kwargs))?;
    let lease = context.call_method0("__enter__")?;
    let result = source::extract(
        py,
        &mx,
        &lease,
        &run,
        &model,
        fxx,
        &out_path,
        bounds,
        limits,
        progress_callback.as_ref(),
        cancelled.as_ref(),
        started,
    );
    let release = context.call_method1("__exit__", (py.None(), py.None(), py.None()));
    match result {
        Ok(result) => {
            release?;
            Ok(result)
        }
        Err(error) => {
            if let Err(release_error) = release {
                release_error.write_unraisable(py, Some(&context));
            }
            Err(error)
        }
    }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(decode_grib_box, module)?)?;
    module.add_function(wrap_pyfunction!(extract_box_sounding, module)?)?;
    trace_render::register(module)?;
    Ok(())
}

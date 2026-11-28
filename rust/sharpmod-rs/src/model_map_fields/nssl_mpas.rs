//! NSSL MPAS archive maps using the shared HRRR product and overlay definitions.

use crate::{
    grib::nssl_mpas_grid,
    model_http_bindings::{cancelled, field_error},
    nssl_mpas_archive as archive,
};
use numpy::IntoPyArray;
use pyo3::{
    prelude::*,
    types::{PyDict, PyList},
};
use sharpmod_weather::model_http::{Error, ModelClient};
use std::{path::Path, time::Duration};
use time::{format_description::well_known::Rfc3339, OffsetDateTime, UtcOffset};

#[derive(Clone, Copy)]
struct Schedule {
    key: &'static str,
    label: &'static str,
    cycles: &'static [u8],
    limit: u32,
}

const MODELS: [Schedule; 2] = [
    Schedule {
        key: "nssl-mpas-htpo",
        label: "NSSL MPAS-HTPO",
        cycles: &[0, 6, 12, 18],
        limit: 48,
    },
    Schedule {
        key: "nssl-mpas-rn",
        label: "NSSL MPAS-RN",
        cycles: &[0, 12],
        limit: 84,
    },
];

const NOTES: &str = "Maps only. NOAA posts these runs to the NSSL archive after the forecasts finish, several hours after initialization.";

impl Schedule {
    fn limits(self) -> Vec<u32> {
        (0..24)
            .map(|hour| {
                if self.cycles.contains(&hour) {
                    self.limit
                } else {
                    0
                }
            })
            .collect()
    }
}

fn schedule(model: &str) -> Option<Schedule> {
    MODELS.into_iter().find(|entry| entry.key == model)
}

pub(crate) fn catalogue(py: Python<'_>) -> PyResult<Vec<Py<PyAny>>> {
    let templates = PyModule::import(py, "sharpmod.providers.hrrr_products")?
        .call_method1("available_products", ("hrrr",))?;
    let replace = PyModule::import(py, "dataclasses")?.getattr("replace")?;
    let mut products = Vec::new();
    for template in templates.try_iter()? {
        let template = template?;
        let key: String = template.getattr("key")?.extract()?;
        if matches!(
            key.as_str(),
            "uh-0-3km-run" | "uh-2-5km-run" | "uh-2-5km-3h"
        ) {
            // Each archive member is a large compressed file. Limit recipes
            // to a single member and use the published APCP run total.
            continue;
        }
        let mut minimum = template.getattr("max_hours")?.extract::<u32>()?;
        let mut supported = true;
        for field in template.getattr("all_fields")?.try_iter()? {
            let field = field?;
            let name = field.getattr("name")?;
            let Some(record) = nssl_mpas_grid::record(name.extract()?) else {
                supported = false;
                break;
            };
            minimum = minimum.max(record.minimum_hour);
        }
        if supported {
            let updates = PyDict::new(py);
            updates.set_item("max_hours", minimum)?;
            let caveat: String = template.getattr("caveat")?.extract()?;
            updates.set_item("caveat", caveat.replace("HRRR", "NSSL MPAS"))?;
            products.push(replace.call((template,), Some(&updates))?.unbind());
        }
    }
    Ok(products)
}

pub(super) fn download_nssl_mpas_map_fields(
    py: Python<'_>,
    product: &Bound<'_, PyAny>,
    run: OffsetDateTime,
    hour: u32,
    timeout: f64,
    should_cancel: Option<Py<PyAny>>,
    model: &str,
) -> PyResult<Py<PyDict>> {
    // The shared setting is normally a per-read budget. MPAS also needs time
    // to find the TAR member and transfer a complete compressed forecast hour.
    let timeout = Duration::try_from_secs_f64(timeout)
        .map_err(|error| field_error(py, Error::Invalid(error.to_string())))?
        .max(Duration::from_secs(90));
    let records = product
        .getattr("all_fields")?
        .try_iter()?
        .map(|field| {
            let name: String = field?.getattr("name")?.extract()?;
            let record = nssl_mpas_grid::record(&name)
                .filter(|record| hour >= record.minimum_hour)
                .ok_or_else(|| field_error(py, Error::Unavailable(404)))?;
            Ok((name, record))
        })
        .collect::<PyResult<Vec<_>>>()?;
    let library: String = PyModule::import(py, "sharpmod.backends.rust_backend")?
        .getattr("_eccodes_library_path")?
        .call0()?
        .extract()?;
    let stop = || cancelled(should_cancel.as_ref());
    let data = py
        .detach(|| {
            let payload = archive::hour_grib(model, run, hour, timeout, &stop)?;
            nssl_mpas_grid::decode(Path::new(&library), &payload, &records, hour, &stop)
        })
        .map_err(|error| field_error(py, error))?;
    if data.len() != records.len() {
        return Err(field_error(py, Error::Unavailable(404)));
    }
    let output = PyDict::new(py);
    for (name, field) in data {
        output.set_item(name, field.into_pyarray(py))?;
    }
    Ok(output.unbind())
}

#[pyfunction]
fn nssl_mpas_cycle_limits(model: &str) -> Vec<u32> {
    schedule(model).map(Schedule::limits).unwrap_or_default()
}

#[pyfunction]
fn nssl_mpas_archive_url(py: Python<'_>, model: &str, run: OffsetDateTime) -> PyResult<String> {
    archive::archive_url(model, run).ok_or_else(|| field_error(py, Error::Unavailable(404)))
}

fn published_archive(
    runtime: &tokio::runtime::Runtime,
    model: &str,
    run: OffsetDateTime,
    timeout: f64,
) -> Result<(), Error> {
    let url = archive::archive_url(model, run).ok_or(Error::Unavailable(404))?;
    runtime
        .block_on(ModelClient::shared().identity(&url, timeout, || Ok(false)))
        .map(|_| ())
        .map_err(|error| match error {
            Error::Http(410) => Error::Unavailable(410),
            error => error,
        })
}

#[pyfunction]
fn nssl_mpas_model_status(
    py: Python<'_>,
    model: &str,
    run: OffsetDateTime,
    hour: u32,
    now: OffsetDateTime,
) -> PyResult<Py<PyDict>> {
    let runtime = crate::weather::runtime()?;
    let run = run.to_offset(UtcOffset::UTC);
    let check = if schedule(model)
        .is_some_and(|entry| entry.cycles.contains(&run.hour()) && hour <= entry.limit)
    {
        py.detach(|| published_archive(runtime, model, run, 2.0))
    } else {
        Err(Error::Unavailable(404))
    };
    let (state, message) = match check {
        Ok(()) => (
            "available",
            "NOAA forecast archive found. The requested hour is checked when downloaded.",
        ),
        Err(Error::Unavailable(_)) => (
            "unavailable",
            "The NOAA forecast archive is unavailable for this selection.",
        ),
        Err(_) => (
            "unknown",
            "NOAA forecast archive availability could not be confirmed.",
        ),
    };
    let row = PyDict::new(py);
    let format = |value: OffsetDateTime| {
        value
            .format(&Rfc3339)
            .map_err(|error| field_error(py, Error::Invalid(error.to_string())))
    };
    row.set_item("model", model)?;
    row.set_item("provider", "NOAA/NSSL THREDDS")?;
    row.set_item("run_time", format(run)?)?;
    row.set_item("forecast_hour", hour)?;
    row.set_item("state", state)?;
    row.set_item("message", message)?;
    row.set_item("checked_at", format(now)?)?;
    Ok(row.unbind())
}

#[pyfunction]
fn nssl_mpas_forecast_models(
    py: Python<'_>,
    now: OffsetDateTime,
    bounds: &Bound<'_, PyDict>,
    outline: &Bound<'_, PyAny>,
    count: usize,
) -> PyResult<Vec<Py<PyDict>>> {
    let runtime = crate::weather::runtime()?;
    let ready = py.detach(|| {
        let mut ready = Vec::new();
        for model in MODELS {
            let Ok(mut runs) = archive::recent_runs(model.key, now, Duration::from_secs(8)) else {
                break; // Both models share the same catalogue.
            };
            // A listing can appear before its download is ready. Try the
            // previous listed run when the latest object has not published yet.
            for _ in 0..2 {
                let Some(&run) = runs.first() else { break };
                match published_archive(runtime, model.key, run, 3.0) {
                    Ok(()) => {
                        ready.push((model, runs));
                        break;
                    }
                    Err(Error::Unavailable(404)) => {
                        runs.remove(0);
                    }
                    Err(_) => break,
                }
            }
        }
        ready
    });
    ready
        .into_iter()
        .map(|(model, runs)| {
            let runs = runs
                .into_iter()
                .take(count.max(1))
                .map(|run| run.format(&Rfc3339))
                .collect::<Result<Vec<_>, _>>()
                .map_err(|error| field_error(py, Error::Invalid(error.to_string())))?;
            let row = PyDict::new(py);
            row.set_item("key", model.key)?;
            row.set_item("label", model.label)?;
            row.set_item("soundings_available", false)?;
            row.set_item("domain", "CONUS")?;
            row.set_item("bounds", bounds)?;
            row.set_item("outline", outline)?;
            row.set_item("cycles", PyList::new(py, model.cycles.iter().copied())?)?;
            row.set_item("forecast_hours", (0..=model.limit).collect::<Vec<_>>())?;
            row.set_item("max_forecast_hours", model.limits())?;
            row.set_item("default_forecast_hour", 1)?;
            row.set_item("grid_spacing_km", 3.0)?;
            row.set_item("notes", NOTES)?;
            row.set_item("members", Vec::<String>::new())?;
            row.set_item("suggested_run", &runs[0])?;
            row.set_item("recent_runs", runs)?;
            Ok(row.unbind())
        })
        .collect()
}

pub(super) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(nssl_mpas_cycle_limits, module)?)?;
    module.add_function(wrap_pyfunction!(nssl_mpas_archive_url, module)?)?;
    module.add_function(wrap_pyfunction!(nssl_mpas_model_status, module)?)?;
    module.add_function(wrap_pyfunction!(nssl_mpas_forecast_models, module)?)?;
    Ok(())
}

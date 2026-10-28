//! Restore the existing surface objects only after native parsing and matching.

mod source;

use pyo3::{exceptions::PyValueError, prelude::*, types::PyTuple};
use sharpmod_weather::surface::{
    self, Dataset, FetchError, Observation, Request, Station, SurfaceClient,
};
use std::sync::OnceLock;
use time::{Duration, OffsetDateTime};

fn station(kind: &Bound<'_, PyAny>, row: Station) -> PyResult<Py<PyAny>> {
    Ok(kind
        .call1((
            row.id,
            row.name,
            row.lat,
            row.lon,
            row.elevation_m,
            row.source_url,
        ))?
        .unbind())
}

fn observation(
    kind: &Bound<'_, PyAny>,
    station: &Bound<'_, PyAny>,
    row: Observation,
) -> PyResult<Py<PyAny>> {
    Ok(kind
        .call1((
            station,
            row.observed_at,
            row.temperature_c,
            row.dewpoint_c,
            row.direction_deg,
            row.speed_kt,
            row.gust_kt,
            row.text,
            row.source_url,
        ))?
        .unbind())
}

fn dataset(py: Python<'_>, value: Dataset) -> PyResult<Py<PyAny>> {
    let module = PyModule::import(py, "sharpmod.providers.surface_observations")?;
    let station_type = module.getattr("SurfaceStation")?;
    let observation_type = module.getattr("SurfaceObservation")?;
    let catalog: Vec<_> = value
        .catalog
        .into_iter()
        .map(|row| station(&station_type, row))
        .collect::<PyResult<_>>()?;
    let matched: Vec<_> = value
        .matched
        .into_iter()
        .map(|(index, row)| observation(&observation_type, catalog[index].bind(py), row))
        .collect::<PyResult<_>>()?;
    let ids = |indices: Vec<usize>| -> PyResult<Bound<'_, PyTuple>> {
        PyTuple::new(
            py,
            indices
                .into_iter()
                .map(|index| catalog[index].bind(py).getattr("station_id"))
                .collect::<PyResult<Vec<_>>>()?,
        )
    };
    Ok(module
        .getattr("SurfaceObservationSet")?
        .call1((
            value.requested,
            value.lat,
            value.lon,
            PyTuple::new(py, matched)?,
            catalog.len(),
            ids(value.unmatched)?,
            value.retrieved_at,
            value.source_url,
            ids(value.queried)?,
            PyTuple::new(py, &catalog)?,
            ids(value.failed)?,
        ))?
        .unbind())
}

#[pyfunction]
fn parse_surface_stations(py: Python<'_>, payload: &Bound<'_, PyAny>) -> PyResult<Vec<Py<PyAny>>> {
    let payload = source::value(payload)?;
    let rows = py.detach(move || surface::stations(&payload));
    let kind = PyModule::import(py, "sharpmod.providers.surface_observations")?
        .getattr("SurfaceStation")?;
    rows.into_iter().map(|row| station(&kind, row)).collect()
}

#[pyfunction]
fn parse_surface_observations(
    py: Python<'_>,
    payload: &Bound<'_, PyAny>,
    station: &Bound<'_, PyAny>,
) -> PyResult<Vec<Py<PyAny>>> {
    let payload = source::value(payload)?;
    let rows = py.detach(move || surface::observations(&payload));
    let kind = PyModule::import(py, "sharpmod.providers.surface_observations")?
        .getattr("SurfaceObservation")?;
    rows.into_iter()
        .map(|row| observation(&kind, station, row))
        .collect()
}

#[pyfunction]
fn select_surface_observation(
    py: Python<'_>,
    values: Vec<Py<PyAny>>,
    requested: Option<OffsetDateTime>,
    tolerance_seconds: f64,
) -> PyResult<Option<Py<PyAny>>> {
    let Some(requested) = requested else {
        return Ok(None);
    };
    let tolerance = Duration::checked_seconds_f64(tolerance_seconds)
        .ok_or_else(|| PyValueError::new_err("invalid surface-observation tolerance"))?;
    let times = values
        .iter()
        .map(|item| {
            item.bind(py)
                .getattr("observed_at")?
                .extract::<OffsetDateTime>()
        })
        .collect::<PyResult<Vec<_>>>()?;
    Ok(
        surface::nearest_index(times, requested, tolerance)
            .map(|index| values[index].clone_ref(py)),
    )
}

#[pyfunction(signature = (latitude, longitude, requested, tolerance_seconds=3600., max_stations=24, workers=6, timeout=15., opener=None, cancel=None))]
#[allow(clippy::too_many_arguments)] // Preserve the existing provider's public options.
fn fetch_surface_observations(
    py: Python<'_>,
    latitude: f64,
    longitude: f64,
    requested: OffsetDateTime,
    tolerance_seconds: f64,
    max_stations: i64,
    workers: i64,
    timeout: f64,
    opener: Option<Py<PyAny>>,
    cancel: Option<Py<PyAny>>,
) -> PyResult<Py<PyAny>> {
    let request = Request {
        lat: latitude,
        lon: longitude,
        requested,
        tolerance_seconds,
        max_stations,
        workers,
        timeout,
    };
    let runtime = crate::weather::runtime()?;
    let result = py.detach(move || {
        let cancelled = || {
            Python::attach(|py| source::cancelled(py, cancel.as_ref()))
                .map_err(|error| error.to_string())
        };
        if let Some(opener) = opener {
            runtime.block_on(surface::fetch(
                source::Opener::new(opener),
                request,
                cancelled,
            ))
        } else {
            static CLIENT: OnceLock<SurfaceClient> = OnceLock::new();
            runtime.block_on(surface::fetch(
                CLIENT.get_or_init(Default::default).clone(),
                request,
                cancelled,
            ))
        }
    });
    match result {
        Ok(value) => dataset(py, value),
        Err(error) => {
            let module = PyModule::import(py, "sharpmod.providers.surface_observations")?;
            let (kind, message) = match error {
                FetchError::Cancelled => (
                    "SurfaceObservationCancelled",
                    "surface-observation request cancelled".into(),
                ),
                FetchError::Provider(message) => ("SurfaceObservationError", message),
            };
            Err(PyErr::from_value(module.getattr(kind)?.call1((message,))?))
        }
    }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(parse_surface_stations, module)?)?;
    module.add_function(wrap_pyfunction!(parse_surface_observations, module)?)?;
    module.add_function(wrap_pyfunction!(select_surface_observation, module)?)?;
    module.add_function(wrap_pyfunction!(fetch_surface_observations, module)?)?;
    Ok(())
}

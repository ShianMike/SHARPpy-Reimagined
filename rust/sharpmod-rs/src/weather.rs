//! Compatibility calls release the interpreter while native weather work runs.

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use sharpmod_weather::radar::VelocityInventory;
use std::sync::OnceLock;
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

static INVENTORY: OnceLock<VelocityInventory> = OnceLock::new();
pyo3::create_exception!(sharpmod_rs, WeatherHistoryUnavailable, PyValueError);

pub(crate) fn runtime() -> PyResult<&'static tokio::runtime::Runtime> {
    static RUNTIME: OnceLock<Result<tokio::runtime::Runtime, String>> = OnceLock::new();
    RUNTIME
        .get_or_init(|| {
            tokio::runtime::Builder::new_multi_thread()
                .worker_threads(2)
                .enable_all()
                .build()
                .map_err(|e| e.to_string())
        })
        .as_ref()
        .map_err(PyRuntimeError::new_err)
}

#[pyfunction(signature = (site, valid_time=None))]
fn radar_velocity_tilts(
    py: Python<'_>,
    site: String,
    valid_time: Option<&str>,
) -> PyResult<String> {
    let site = sharpmod_weather::radar_sites::sites()
        .iter()
        .find(|entry| entry.id.eq_ignore_ascii_case(&site))
        .ok_or_else(|| PyValueError::new_err("unknown radar site"))?;
    let target = valid_time
        .map(|value| OffsetDateTime::parse(value, &Rfc3339))
        .transpose()
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    let runtime = runtime()?;
    py.detach(move || {
        let inventory = INVENTORY.get_or_init(VelocityInventory::new);
        let result = runtime
            .block_on(inventory.availability(site, target, OffsetDateTime::now_utc()))
            .map_err(PyValueError::new_err)?;
        serde_json::to_string(&result).map_err(|error| PyRuntimeError::new_err(error.to_string()))
    })
}

#[pyfunction]
fn clear_weather_cache() {
    if let Some(inventory) = INVENTORY.get() {
        inventory.clear();
    }
}

#[pyfunction]
fn radar_sites() -> PyResult<String> {
    serde_json::to_string(&serde_json::json!({"sites": sharpmod_weather::radar_sites::sites()}))
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))
}

#[pyfunction]
fn weather_alerts(py: Python<'_>, valid_time: &str) -> PyResult<String> {
    let valid_time = OffsetDateTime::parse(valid_time, &Rfc3339)
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    let runtime = runtime()?;
    py.detach(move || {
        static ALERTS: OnceLock<sharpmod_weather::alerts::AlertsClient> = OnceLock::new();
        let alerts = ALERTS.get_or_init(Default::default);
        let result = runtime
            .block_on(alerts.fetch(valid_time, OffsetDateTime::now_utc()))
            .map_err(|error| {
                if error == sharpmod_weather::alerts::HISTORY_UNAVAILABLE {
                    WeatherHistoryUnavailable::new_err(error)
                } else {
                    PyValueError::new_err(error)
                }
            })?;
        serde_json::to_string(&result).map_err(|error| PyRuntimeError::new_err(error.to_string()))
    })
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(radar_velocity_tilts, module)?)?;
    module.add_function(wrap_pyfunction!(radar_sites, module)?)?;
    module.add_function(wrap_pyfunction!(clear_weather_cache, module)?)?;
    module.add_function(wrap_pyfunction!(weather_alerts, module)?)?;
    module.add(
        "WeatherHistoryUnavailable",
        module.py().get_type::<WeatherHistoryUnavailable>(),
    )?;
    Ok(())
}

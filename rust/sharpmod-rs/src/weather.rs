//! Compatibility calls release the interpreter while native weather work runs.

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use sharpmod_weather::radar::VelocityInventory;
use std::sync::OnceLock;
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

static INVENTORY: OnceLock<VelocityInventory> = OnceLock::new();
pyo3::create_exception!(sharpmod_rs, WeatherHistoryUnavailable, PyValueError);
pyo3::create_exception!(sharpmod_rs, WeatherRadarScanMissing, PyRuntimeError);

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
    sharpmod_weather::australian_radar::client().clear();
}

#[pyfunction(signature = (public=false))]
fn radar_sites(public: bool) -> PyResult<String> {
    let sites = sharpmod_weather::radar_sites::sites();
    let result = if public {
        let sites: Vec<_> = sites
            .iter()
            .map(|site| {
                let mut entry =
                    serde_json::json!({ "id": site.id, "lat": site.lat, "lon": site.lon });
                if site.country.as_deref() == Some("AU") {
                    entry["name"] = serde_json::json!(site.name);
                    entry["country"] = serde_json::json!(site.country);
                    entry["products"] = serde_json::json!(site.products);
                }
                entry
            })
            .collect();
        serde_json::json!({"sites": sites})
    } else {
        serde_json::json!({"sites": sites})
    };
    serde_json::to_string(&result).map_err(|error| PyRuntimeError::new_err(error.to_string()))
}

#[pyfunction]
fn australian_radar_frame(py: Python<'_>, request: &str) -> PyResult<Option<String>> {
    let request = serde_json::from_str(request)
        .map_err(|error| PyValueError::new_err(format!("Invalid radar request: {error}")))?;
    let runtime = runtime()?;
    py.detach(move || {
        let frame = runtime
            .block_on(
                sharpmod_weather::australian_radar::client()
                    .fetch(request, OffsetDateTime::now_utc()),
            )
            .map_err(|error| match error {
                sharpmod_weather::australian_radar::Error::Invalid(message) => {
                    PyValueError::new_err(message)
                }
                sharpmod_weather::australian_radar::Error::ScanMissing => {
                    WeatherRadarScanMissing::new_err(error.to_string())
                }
                sharpmod_weather::australian_radar::Error::Fetch(message) => {
                    PyRuntimeError::new_err(message)
                }
            })?;
        frame
            .map(|frame| {
                serde_json::to_string(frame.as_ref())
                    .map_err(|error| PyRuntimeError::new_err(error.to_string()))
            })
            .transpose()
    })
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
    module.add_function(wrap_pyfunction!(australian_radar_frame, module)?)?;
    module.add_function(wrap_pyfunction!(clear_weather_cache, module)?)?;
    module.add_function(wrap_pyfunction!(weather_alerts, module)?)?;
    module.add(
        "WeatherHistoryUnavailable",
        module.py().get_type::<WeatherHistoryUnavailable>(),
    )?;
    module.add(
        "WeatherRadarScanMissing",
        module.py().get_type::<WeatherRadarScanMissing>(),
    )?;
    Ok(())
}

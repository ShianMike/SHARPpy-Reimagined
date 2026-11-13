//! Compatibility calls release the interpreter while native weather work runs.

use numpy::ndarray::Array2;
use numpy::{IntoPyArray, PyArray2};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use sharpmod_weather::radar::VelocityInventory;
use std::sync::OnceLock;
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

static INVENTORY: OnceLock<VelocityInventory> = OnceLock::new();
pyo3::create_exception!(sharpmod_rs, WeatherHistoryUnavailable, PyValueError);
pyo3::create_exception!(sharpmod_rs, WeatherRadarScanMissing, PyRuntimeError);
pyo3::create_exception!(sharpmod_rs, UwyoStationUnavailable, PyValueError);

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

#[pyfunction(signature = (site, product="reflectivity", valid_time=None, cache_dir=None))]
fn radar_level2_tilts(
    py: Python<'_>,
    site: String,
    product: &str,
    valid_time: Option<&str>,
    cache_dir: Option<String>,
) -> PyResult<String> {
    let site = sharpmod_weather::radar_sites::sites()
        .iter()
        .find(|entry| entry.id.eq_ignore_ascii_case(&site) && entry.country.is_none())
        .ok_or_else(|| PyValueError::new_err("unknown radar site"))?;
    let product = product.to_owned();
    let target = valid_time
        .map(|value| OffsetDateTime::parse(value, &Rfc3339))
        .transpose()
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    let runtime = runtime()?;
    py.detach(move || {
        let result = runtime
            .block_on(sharpmod_weather::nexrad_level2::client().availability(
                site,
                &product,
                target,
                OffsetDateTime::now_utc(),
                cache_dir.as_deref().map(std::path::Path::new),
            ))
            .map_err(|error| match error {
                sharpmod_weather::radar_provider::Error::Invalid(message) => {
                    PyValueError::new_err(message)
                }
                sharpmod_weather::radar_provider::Error::ScanMissing => {
                    WeatherRadarScanMissing::new_err(error.to_string())
                }
                sharpmod_weather::radar_provider::Error::Fetch(message) => {
                    PyRuntimeError::new_err(message)
                }
            })?;
        serde_json::to_string(&result).map_err(|error| PyRuntimeError::new_err(error.to_string()))
    })
}

#[pyfunction(signature = (request, pixels=None, cache_dir=None))]
fn radar_level2_frame(
    py: Python<'_>,
    request: &str,
    pixels: Option<usize>,
    cache_dir: Option<String>,
) -> PyResult<Option<String>> {
    let request: sharpmod_weather::radar_provider::Request = serde_json::from_str(request)
        .map_err(|error| PyValueError::new_err(format!("Invalid radar request: {error}")))?;
    let site = sharpmod_weather::radar_provider::resolve(&request)
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    let Some(site) = site.filter(|entry| entry.country.is_none()) else {
        return Ok(None);
    };
    let runtime = runtime()?;
    py.detach(move || {
        let frame = runtime
            .block_on(
                sharpmod_weather::nexrad_level2::client().fetch_site_with_pixels(
                    request,
                    OffsetDateTime::now_utc(),
                    site,
                    pixels.unwrap_or(4096),
                    cache_dir.as_deref().map(std::path::Path::new),
                ),
            )
            .map_err(|error| match error {
                sharpmod_weather::radar_provider::Error::Invalid(message) => {
                    PyValueError::new_err(message)
                }
                sharpmod_weather::radar_provider::Error::ScanMissing => {
                    WeatherRadarScanMissing::new_err(error.to_string())
                }
                sharpmod_weather::radar_provider::Error::Fetch(message) => {
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
fn clear_weather_cache() {
    if let Some(inventory) = INVENTORY.get() {
        inventory.clear();
    }
    sharpmod_weather::radar_provider::clear();
    sharpmod_weather::spc_outlook::client().clear();
}

#[pyfunction(signature = (public=false, include_discovered=true))]
fn radar_sites(py: Python<'_>, public: bool, include_discovered: bool) -> PyResult<String> {
    let result = if public {
        let sites = if include_discovered {
            let runtime = runtime()?;
            py.detach(move || runtime.block_on(sharpmod_weather::radar_provider::sites()))
        } else {
            sharpmod_weather::radar_sites::sites().to_vec()
        };
        let sites: Vec<_> = sites
            .iter()
            .map(|site| {
                let mut entry =
                    serde_json::json!({ "id": site.id, "lat": site.lat, "lon": site.lon });
                if let Some(name) = &site.name {
                    entry["name"] = serde_json::json!(name);
                }
                if site.country.is_some() {
                    entry["country"] = serde_json::json!(site.country);
                    entry["products"] = serde_json::json!(site.products);
                }
                if let Some(provider) = &site.provider {
                    entry["provider"] = serde_json::json!(provider);
                }
                entry
            })
            .collect();
        serde_json::json!({"sites": sites})
    } else {
        serde_json::json!({"sites": sharpmod_weather::radar_sites::sites()})
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
            .block_on(sharpmod_weather::radar_provider::fetch_international(
                request,
                OffsetDateTime::now_utc(),
            ))
            .map_err(|error| match error {
                sharpmod_weather::radar_provider::Error::Invalid(message) => {
                    PyValueError::new_err(message)
                }
                sharpmod_weather::radar_provider::Error::ScanMissing => {
                    WeatherRadarScanMissing::new_err(error.to_string())
                }
                sharpmod_weather::radar_provider::Error::Fetch(message) => {
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
fn radar_site_frame(py: Python<'_>, request: &str) -> PyResult<Option<String>> {
    let request = serde_json::from_str(request)
        .map_err(|error| PyValueError::new_err(format!("Invalid radar request: {error}")))?;
    let runtime = runtime()?;
    py.detach(move || {
        let frame = runtime
            .block_on(sharpmod_weather::radar_provider::fetch(
                request,
                OffsetDateTime::now_utc(),
            ))
            .map_err(|error| match error {
                sharpmod_weather::radar_provider::Error::Invalid(message) => {
                    PyValueError::new_err(message)
                }
                sharpmod_weather::radar_provider::Error::ScanMissing => {
                    WeatherRadarScanMissing::new_err(error.to_string())
                }
                sharpmod_weather::radar_provider::Error::Fetch(message) => {
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
fn radar_level3_scans(payload: &[u8]) -> PyResult<Vec<(i64, String)>> {
    let entries = sharpmod_weather::radar::scan_entries(payload).map_err(PyValueError::new_err)?;
    Ok(entries
        .into_iter()
        .map(|(dt, name)| (dt.unix_timestamp(), name))
        .collect())
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

#[pyfunction]
fn place_labels_json() -> PyResult<String> {
    sharpmod_weather::place_names::place_labels_json().map_err(PyRuntimeError::new_err)
}

#[pyfunction]
fn is_conus_point(lat: f64, lon: f64) -> bool {
    sharpmod_weather::place_names::is_conus_point(lat, lon)
}

#[pyfunction(signature = (lat, lon, max_distance_km=200.0))]
fn offline_conus_town_name(lat: f64, lon: f64, max_distance_km: f64) -> String {
    sharpmod_weather::place_names::offline_conus_town_name(lat, lon, max_distance_km)
}

#[pyfunction(signature = (valid_time, now=None, product="cat"))]
fn spc_outlook_candidates(valid_time: &str, now: Option<&str>, product: &str) -> PyResult<String> {
    let valid_time = OffsetDateTime::parse(valid_time, &Rfc3339)
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    let now = now
        .map(|value| OffsetDateTime::parse(value, &Rfc3339))
        .transpose()
        .map_err(|error| PyValueError::new_err(error.to_string()))?
        .unwrap_or_else(OffsetDateTime::now_utc);
    let items = sharpmod_weather::spc_outlook::candidates_for(valid_time, now, product);
    serde_json::to_string(&items).map_err(|error| PyRuntimeError::new_err(error.to_string()))
}

#[pyfunction(signature = (payload, source_url="", day=1, label="", product="cat"))]
fn spc_outlook_parse(
    py: Python<'_>,
    payload: &[u8],
    source_url: &str,
    day: u8,
    label: &str,
    product: &str,
) -> PyResult<String> {
    let payload = payload.to_vec();
    let source_url = source_url.to_owned();
    let label = label.to_owned();
    let product = product.to_owned();
    let layer = py
        .detach(move || {
            sharpmod_weather::spc_outlook::parse_outlook(
                &payload,
                &source_url,
                day,
                &label,
                &product,
            )
        })
        .map_err(PyValueError::new_err)?;
    serde_json::to_string(&layer).map_err(|error| PyRuntimeError::new_err(error.to_string()))
}

#[pyfunction(signature = (valid_time, now=None, product="cat"))]
fn spc_outlook_fetch(
    py: Python<'_>,
    valid_time: &str,
    now: Option<&str>,
    product: &str,
) -> PyResult<Option<String>> {
    let valid_time = OffsetDateTime::parse(valid_time, &Rfc3339)
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    let now = now
        .map(|value| OffsetDateTime::parse(value, &Rfc3339))
        .transpose()
        .map_err(|error| PyValueError::new_err(error.to_string()))?
        .unwrap_or_else(OffsetDateTime::now_utc);
    let product = product.to_owned();
    let runtime = runtime()?;
    py.detach(move || {
        let layer = runtime
            .block_on(sharpmod_weather::spc_outlook::client().fetch(valid_time, now, &product))
            .map_err(PyRuntimeError::new_err)?;
        layer
            .map(|item| {
                serde_json::to_string(item.as_ref())
                    .map_err(|error| PyRuntimeError::new_err(error.to_string()))
            })
            .transpose()
    })
}

#[pyfunction]
fn spc_outlook_clear_cache() {
    sharpmod_weather::spc_outlook::client().clear();
}

#[pyfunction]
fn radar_wms_capabilities(py: Python<'_>, payload: &[u8]) -> PyResult<String> {
    let bytes = payload.to_vec();
    let layers = py
        .detach(move || sharpmod_weather::radar_time::parse_capabilities(&bytes))
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    let mapped: std::collections::HashMap<String, Vec<String>> = layers
        .into_iter()
        .map(|(key, times)| {
            (
                key,
                times
                    .into_iter()
                    .map(sharpmod_weather::format_rfc3339_utc)
                    .collect(),
            )
        })
        .collect();
    serde_json::to_string(&mapped).map_err(|error| PyRuntimeError::new_err(error.to_string()))
}

#[pyfunction(signature = (product=None, valid_time=None))]
fn radar_mosaic_frame(
    py: Python<'_>,
    product: Option<&str>,
    valid_time: Option<&str>,
) -> PyResult<String> {
    let product = product.map(str::to_owned);
    let valid_time = valid_time
        .map(|value| OffsetDateTime::parse(value, &Rfc3339))
        .transpose()
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    let runtime = runtime()?;
    py.detach(move || {
        let frame = runtime
            .block_on(sharpmod_weather::radar_mosaic::client().fetch(
                product.as_deref(),
                valid_time,
                OffsetDateTime::now_utc(),
            ))
            .map_err(|error| match error {
                sharpmod_weather::radar_mosaic::MosaicError::Invalid(msg) => {
                    PyValueError::new_err(msg)
                }
                sharpmod_weather::radar_mosaic::MosaicError::ScanMissing(msg) => {
                    WeatherRadarScanMissing::new_err(msg)
                }
                sharpmod_weather::radar_mosaic::MosaicError::Fetch(msg) => {
                    PyRuntimeError::new_err(msg)
                }
            })?;
        frame.to_json().map_err(PyRuntimeError::new_err)
    })
}

#[pyfunction]
fn uwyo_parse_stations(raw: &[u8]) -> PyResult<String> {
    let records = sharpmod_weather::uwyo::parse_station_list(raw).map_err(PyValueError::new_err)?;
    serde_json::to_string(&records).map_err(|error| PyRuntimeError::new_err(error.to_string()))
}

type PyUwyoDecoded<'py> = (Bound<'py, PyArray2<f64>>, Option<String>, String, f64, f64);

#[pyfunction]
fn uwyo_decode_text<'py>(py: Python<'py>, text: String) -> PyResult<PyUwyoDecoded<'py>> {
    let decoded = py
        .detach(move || sharpmod_weather::uwyo::decode_uwyo_text(&text))
        .map_err(|error| match error {
            sharpmod_weather::uwyo::DecodeError::Unavailable(msg) => {
                UwyoStationUnavailable::new_err(msg)
            }
            sharpmod_weather::uwyo::DecodeError::Parse(msg) => PyValueError::new_err(msg),
        })?;
    let levels = decoded.pres.len();
    let mut flat = Vec::with_capacity(6 * levels);
    flat.extend(decoded.pres);
    flat.extend(decoded.hght);
    flat.extend(decoded.tmpc);
    flat.extend(decoded.dwpc);
    flat.extend(decoded.wdir);
    flat.extend(decoded.wspd);
    let matrix = Array2::from_shape_vec((6, levels), flat)
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
    Ok((
        matrix.into_pyarray(py),
        decoded.valid_iso,
        decoded.loc,
        decoded.lat,
        decoded.lon,
    ))
}

#[pyfunction]
fn threshold_metrics_catalog() -> PyResult<String> {
    serde_json::to_string(&sharpmod_weather::threshold_catalog::metrics_catalog())
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))
}

type PyMetricControl = (String, String, String, u8, f64, f64, f64, f64, bool);

#[pyfunction]
fn threshold_control_for(key: &str) -> PyMetricControl {
    let entry = sharpmod_weather::threshold_catalog::control_for(key);
    (
        entry.key,
        entry.label,
        entry.units,
        entry.decimals,
        entry.minimum,
        entry.maximum,
        entry.step,
        entry.suggested,
        entry.derived,
    )
}

#[pyfunction]
fn threshold_fraction_text(numerator: isize, denominator: isize, decimals: usize) -> String {
    sharpmod_weather::threshold_catalog::fraction_text(numerator, denominator, decimals)
}

#[pyfunction]
#[pyo3(signature = (metric, operator, value, upper=None, units=""))]
fn threshold_condition_summary(
    metric: &str,
    operator: &str,
    value: f64,
    upper: Option<f64>,
    units: &str,
) -> String {
    sharpmod_weather::threshold_catalog::condition_summary(metric, operator, value, upper, units)
}

#[pyfunction]
#[pyo3(signature = (operator, value, upper, candidate))]
fn threshold_evaluate_condition(
    operator: &str,
    value: f64,
    upper: Option<f64>,
    candidate: f64,
) -> Option<bool> {
    sharpmod_weather::threshold_catalog::evaluate_condition(operator, value, upper, candidate)
}

#[pyfunction]
fn threshold_export_csv(results_json: &str) -> PyResult<String> {
    let results: Vec<sharpmod_weather::threshold_catalog::CsvResultInput> =
        serde_json::from_str(results_json)
            .map_err(|error| PyValueError::new_err(error.to_string()))?;
    sharpmod_weather::threshold_catalog::export_csv_text(&results).map_err(PyRuntimeError::new_err)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(radar_velocity_tilts, module)?)?;
    module.add_function(wrap_pyfunction!(radar_level2_tilts, module)?)?;
    module.add_function(wrap_pyfunction!(radar_level2_frame, module)?)?;
    module.add_function(wrap_pyfunction!(radar_sites, module)?)?;
    module.add_function(wrap_pyfunction!(australian_radar_frame, module)?)?;
    module.add_function(wrap_pyfunction!(radar_site_frame, module)?)?;
    module.add_function(wrap_pyfunction!(radar_level3_scans, module)?)?;
    module.add_function(wrap_pyfunction!(clear_weather_cache, module)?)?;
    module.add_function(wrap_pyfunction!(weather_alerts, module)?)?;
    module.add_function(wrap_pyfunction!(place_labels_json, module)?)?;
    module.add_function(wrap_pyfunction!(is_conus_point, module)?)?;
    module.add_function(wrap_pyfunction!(offline_conus_town_name, module)?)?;
    module.add_function(wrap_pyfunction!(spc_outlook_candidates, module)?)?;
    module.add_function(wrap_pyfunction!(spc_outlook_parse, module)?)?;
    module.add_function(wrap_pyfunction!(spc_outlook_fetch, module)?)?;
    module.add_function(wrap_pyfunction!(spc_outlook_clear_cache, module)?)?;
    module.add_function(wrap_pyfunction!(uwyo_parse_stations, module)?)?;
    module.add_function(wrap_pyfunction!(uwyo_decode_text, module)?)?;
    module.add_function(wrap_pyfunction!(radar_wms_capabilities, module)?)?;
    module.add_function(wrap_pyfunction!(radar_mosaic_frame, module)?)?;
    module.add_function(wrap_pyfunction!(threshold_metrics_catalog, module)?)?;
    module.add_function(wrap_pyfunction!(threshold_control_for, module)?)?;
    module.add_function(wrap_pyfunction!(threshold_fraction_text, module)?)?;
    module.add_function(wrap_pyfunction!(threshold_condition_summary, module)?)?;
    module.add_function(wrap_pyfunction!(threshold_evaluate_condition, module)?)?;
    module.add_function(wrap_pyfunction!(threshold_export_csv, module)?)?;
    module.add(
        "WeatherHistoryUnavailable",
        module.py().get_type::<WeatherHistoryUnavailable>(),
    )?;
    module.add(
        "WeatherRadarScanMissing",
        module.py().get_type::<WeatherRadarScanMissing>(),
    )?;
    module.add(
        "UwyoStationUnavailable",
        module.py().get_type::<UwyoStationUnavailable>(),
    )?;
    Ok(())
}

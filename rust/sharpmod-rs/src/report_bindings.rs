//! Keep legacy report objects at the boundary while Rust owns IEM parsing and HTTP.

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use sharpmod_weather::reports::{report_options, shared_client, ReportsClient};
use std::sync::OnceLock;
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

#[pyfunction]
fn parse_storm_reports(py: Python<'_>, text: String) -> PyResult<Vec<Py<PyAny>>> {
    let reports = py.detach(move || sharpmod_weather::reports::parse(&text));
    let module = PyModule::import(py, "sharpmod.providers.storm_reports")?;
    let hazards = module.getattr("HAZARDS")?;
    let report_type = module.getattr("StormReport")?;
    reports
        .into_iter()
        .map(|row| {
            let hazard = hazards.get_item(row.code)?;
            Ok(report_type
                .call1((
                    hazard,
                    row.valid,
                    row.lat,
                    row.lon,
                    row.magnitude,
                    row.city,
                    row.county,
                    row.state,
                    row.wfo,
                    row.source,
                    row.remark,
                ))?
                .unbind())
        })
        .collect()
}

#[pyfunction]
fn fetch_storm_reports(
    py: Python<'_>,
    url: String,
    timeout: f64,
    limit: usize,
) -> PyResult<Py<PyBytes>> {
    let runtime = crate::weather::runtime()?;
    let bytes = py
        .detach(move || {
            static REPORTS: OnceLock<ReportsClient> = OnceLock::new();
            runtime.block_on(
                REPORTS
                    .get_or_init(Default::default)
                    .fetch(&url, timeout, limit),
            )
        })
        .map_err(PyRuntimeError::new_err)?;
    Ok(PyBytes::new(py, &bytes).unbind())
}

#[pyfunction]
fn report_options_json() -> PyResult<String> {
    serde_json::to_string(&report_options())
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))
}

#[pyfunction]
#[pyo3(signature = (valid_time, window_s=21600, hazard_filter="all"))]
fn storm_reports_json(
    py: Python<'_>,
    valid_time: &str,
    window_s: u32,
    hazard_filter: &str,
) -> PyResult<String> {
    let when = OffsetDateTime::parse(valid_time, &Rfc3339)
        .map_err(|error| pyo3::exceptions::PyValueError::new_err(error.to_string()))?;
    let filter = hazard_filter.to_owned();
    let runtime = crate::weather::runtime()?;
    let snapshot = py
        .detach(move || {
            runtime.block_on(shared_client().fetch_snapshot(
                when,
                window_s,
                Some(&filter),
                OffsetDateTime::now_utc(),
            ))
        })
        .map_err(PyRuntimeError::new_err)?;
    serde_json::to_string(&snapshot).map_err(|error| PyRuntimeError::new_err(error.to_string()))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(parse_storm_reports, module)?)?;
    module.add_function(wrap_pyfunction!(fetch_storm_reports, module)?)?;
    module.add_function(wrap_pyfunction!(report_options_json, module)?)?;
    module.add_function(wrap_pyfunction!(storm_reports_json, module)?)?;
    Ok(())
}

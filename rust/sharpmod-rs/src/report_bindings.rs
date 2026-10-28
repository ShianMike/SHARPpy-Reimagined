//! Keep legacy report objects at the boundary while Rust owns IEM parsing and HTTP.

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use sharpmod_weather::reports::ReportsClient;
use std::sync::OnceLock;

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

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(parse_storm_reports, module)?)?;
    module.add_function(wrap_pyfunction!(fetch_storm_reports, module)?)?;
    Ok(())
}

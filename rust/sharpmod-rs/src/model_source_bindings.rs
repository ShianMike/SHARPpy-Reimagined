//! Python object boundary; mirror policy and acquisition stay native.

use crate::model_sources::{Probe, Request, Router, Source};
use pyo3::{prelude::*, types::PyDict};
use sharpmod_weather::model_http::ModelClient;
use std::{
    sync::OnceLock,
    time::{SystemTime, UNIX_EPOCH},
};

#[pyfunction(signature=(herbie, ttl_seconds=21600.0))]
fn select_model_provider(
    py: Python<'_>,
    herbie: &Bound<'_, PyAny>,
    ttl_seconds: f64,
) -> PyResult<Py<PyAny>> {
    let getattr = PyModule::import(py, "builtins")?.getattr("getattr")?;
    let current = getattr.call1((herbie, "grib_source", py.None()))?;
    let flag: String = PyModule::import(py, "os")?
        .getattr("environ")?
        .call_method1("get", ("SHARPMOD_PROVIDER_RACING", "1"))?
        .extract()?;
    if matches!(
        flag.trim().to_lowercase().as_str(),
        "0" | "false" | "no" | "off"
    ) {
        return Ok(current.unbind());
    }
    let mut sources = Vec::new();
    let declared = getattr.call1((herbie, "SOURCES", PyDict::new(py)))?;
    for item in declared.call_method0("items")?.try_iter()? {
        let item = item?;
        let Ok(name) = item.get_item(0)?.extract::<String>() else {
            continue;
        };
        if !matches!(name.as_str(), "aws" | "google" | "azure" | "ecmwf") {
            continue;
        }
        let url = item.get_item(1)?.str()?.to_str()?.to_owned();
        if url.starts_with("http://") || url.starts_with("https://") {
            sources.push(Source { name, url });
        }
    }
    if sources.len() < 2 {
        return Ok(current.unbind());
    }
    let request = Request {
        model: getattr
            .call1((herbie, "model", ""))?
            .str()?
            .to_str()?
            .to_owned(),
        product: getattr
            .call1((herbie, "product", ""))?
            .str()?
            .to_str()?
            .to_owned(),
        reference: Some(
            getattr
                .call1((herbie, "grib", ""))?
                .str()?
                .to_str()?
                .to_owned(),
        ),
        sources,
        ttl: ttl_seconds,
    };
    let runtime = crate::weather::runtime()?;
    let selected = py.detach(|| {
        static ROUTER: OnceLock<Router> = OnceLock::new();
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs_f64();
        ROUTER
            .get_or_init(Router::default)
            .select(&request, now, |sources| {
                runtime
                    .block_on(ModelClient::probe_sources(
                        sources.iter().map(|source| source.url.clone()).collect(),
                    ))
                    .into_iter()
                    .map(|result| result.map(|(size, elapsed)| Probe { size, elapsed }))
                    .collect()
            })
    });
    let selected = &request.sources[selected];
    herbie.setattr("grib_source", &selected.name)?;
    herbie.setattr("grib", &selected.url)?;
    Ok(selected.name.clone().into_pyobject(py)?.into_any().unbind())
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(select_model_provider, module)?)?;
    Ok(())
}

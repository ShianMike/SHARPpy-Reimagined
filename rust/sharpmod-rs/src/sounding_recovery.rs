//! Adapt the process-owned sounding cache; all recovery format and I/O logic is Rust.
use pyo3::{prelude::*, types::PyDict};
use sharpmod_weather::sounding_recovery::{pack, unpack};
use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::PathBuf,
};

fn failure(py: Python<'_>, message: String) -> PyErr {
    match PyModule::import(py, "sharpmod.bridge.errors")
        .and_then(|module| module.getattr("BridgeError"))
        .and_then(|class| class.call1(("SOUNDING_INVALID", message.clone(), false)))
    {
        Ok(error) => PyErr::from_value(error),
        Err(_) => pyo3::exceptions::PyValueError::new_err(message),
    }
}

fn argument<'py>(params: &Bound<'py, PyDict>, name: &str) -> PyResult<String> {
    if params.len() != 1 {
        return Err(failure(params.py(), format!("Expected only {name}.")));
    }
    params
        .get_item(name)?
        .ok_or_else(|| failure(params.py(), format!("Missing {name}.")))?
        .extract()
}

#[pyfunction]
fn sounding_snapshot<'py>(
    py: Python<'py>,
    params: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyDict>> {
    let id = argument(params, "sounding_id")?;
    let cache = PyModule::import(py, "sharpmod.bridge.sounding_cache")?.call_method0("cache")?;
    let entry = cache.call_method1("get", (&id,))?;
    let path: PathBuf = entry.getattr("path")?.extract()?;
    let text: String = PyModule::import(py, "json")?
        .call_method1("dumps", (entry.getattr("summary")?,))?
        .extract()?;
    let summary = serde_json::from_str(&text).map_err(|error| failure(py, error.to_string()))?;
    let payload = py
        .detach(move || pack(&path, summary))
        .map_err(|error| failure(py, error))?;
    let out = PyDict::new(py);
    out.set_item("sounding_id", id)?;
    out.set_item("payload", payload)?;
    Ok(out)
}

struct Pending {
    paths: Vec<PathBuf>,
    committed: bool,
}
impl Pending {
    fn write(&mut self, path: PathBuf, bytes: &[u8]) -> std::io::Result<()> {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)?;
        self.paths.push(path);
        file.write_all(bytes)?;
        file.sync_all()
    }
}
impl Drop for Pending {
    fn drop(&mut self) {
        if !self.committed {
            for path in &self.paths {
                let _ = fs::remove_file(path);
            }
        }
    }
}

#[pyfunction]
fn restore_sounding_snapshot<'py>(
    py: Python<'py>,
    params: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyAny>> {
    let payload = argument(params, "payload")?;
    let source = py
        .detach(move || unpack(&payload))
        .map_err(|error| failure(py, error))?;
    let cache = PyModule::import(py, "sharpmod.bridge.sounding_cache")?.call_method0("cache")?;
    let id: String = cache.call_method0("new_id")?.extract()?;
    let path: PathBuf = cache
        .call_method1("data_path", (&id, &source.suffix))?
        .extract()?;
    let write_path = path.clone();
    let mut pending = py
        .detach(move || {
            let mut pending = Pending {
                paths: Vec::new(),
                committed: false,
            };
            pending.write(write_path.clone(), &source.bytes)?;
            if let Some(bytes) = source.sidecar {
                pending.write(write_path.with_extension("json"), &bytes)?;
            }
            Ok::<_, std::io::Error>(pending)
        })
        .map_err(|error| failure(py, error.to_string()))?;
    let api = PyModule::import(py, "sharpmod.bridge.sounding_summary")?;
    let collection = api.call_method1("decode", (&path,))?.get_item(0)?;
    let text =
        serde_json::to_string(&source.summary).map_err(|error| failure(py, error.to_string()))?;
    let summary = PyModule::import(py, "json")?
        .call_method1("loads", (text,))?
        .cast_into::<PyDict>()?;
    summary.set_item("sounding_id", &id)?;
    let decoded = api.call_method1(
        "summary",
        (&id, summary.get_item("kind")?.unwrap(), &collection),
    )?;
    for field in ["level_count", "time_count"] {
        if !decoded
            .get_item(field)?
            .eq(summary.get_item(field)?.unwrap())?
        {
            return Err(failure(
                py,
                "The saved profile does not match its sounding metadata.".into(),
            ));
        }
    }
    let result = api.call_method1("register", (&id, &path, &summary, collection))?;
    pending.committed = true;
    Ok(result)
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(sounding_snapshot, module)?)?;
    module.add_function(wrap_pyfunction!(restore_sounding_snapshot, module)?)
}

#[cfg(test)]
mod tests;

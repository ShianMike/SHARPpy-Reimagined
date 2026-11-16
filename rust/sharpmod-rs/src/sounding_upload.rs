//! Validate uploaded profiles and stage them for the existing format decoders.
use crate::sounding_files::Pending;
use pyo3::{
    prelude::*,
    types::{PyDict, PyString},
};
use std::path::{Path, PathBuf};

const MAX_UPLOAD_BYTES: usize = 5 * 1024 * 1024;

fn failure(py: Python<'_>, code: &str, message: &str) -> PyErr {
    match PyModule::import(py, "sharpmod.bridge.errors")
        .and_then(|module| module.getattr("BridgeError"))
        .and_then(|class| class.call1((code, message, false)))
    {
        Ok(error) => PyErr::from_value(error),
        Err(_) => pyo3::exceptions::PyValueError::new_err(message.to_string()),
    }
}

fn invalid(py: Python<'_>, message: &str) -> PyErr {
    failure(
        py,
        "INVALID_ARGUMENT",
        &format!("The request was not valid: {message}."),
    )
}

fn text(params: &Bound<'_, PyDict>, key: &str) -> PyResult<String> {
    let value = params
        .get_item(key)?
        .and_then(|value| value.cast_into::<PyString>().ok())
        .ok_or_else(|| invalid(params.py(), &format!("{key} must be text")))?;
    value
        .extract()
        .map_err(|_| invalid(params.py(), &format!("{key} must be valid Unicode text")))
}

fn file_name(value: &str) -> Result<(String, String), &'static str> {
    let value = value.trim();
    if !(1..=255).contains(&value.chars().count()) || value.contains('\0') {
        return Err("file_name must be text of 1 to 255 characters without NUL");
    }
    // Requests can originate on Windows even when the backend runs on Linux.
    let name = value.rsplit(['/', '\\']).next().unwrap_or("");
    if name.is_empty() || matches!(name, "." | "..") {
        return Err("file_name must include a file name");
    }
    let extension = Path::new(name)
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("");
    let suffix = if (1..=12).contains(&extension.len())
        && extension
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-'))
    {
        format!(".{}", extension.to_ascii_lowercase())
    } else {
        ".txt".to_string()
    };
    Ok((name.to_string(), suffix))
}

#[pyfunction]
#[pyo3(signature = (params, max_bytes=MAX_UPLOAD_BYTES))]
fn open_sounding_file<'py>(
    py: Python<'py>,
    params: &Bound<'py, PyDict>,
    max_bytes: usize,
) -> PyResult<Bound<'py, PyAny>> {
    if params.len() != 2 || !params.contains("file_name")? || !params.contains("content_base64")? {
        return Err(invalid(py, "expected only file_name and content_base64"));
    }
    let (name, suffix) =
        file_name(&text(params, "file_name")?).map_err(|message| invalid(py, message))?;
    let encoded = text(params, "content_base64")?;
    let max_bytes = max_bytes.min(MAX_UPLOAD_BYTES);
    if encoded.len() > max_bytes.div_ceil(3) * 4 {
        return Err(invalid(py, "content_base64 exceeds the upload size limit"));
    }
    let bytes = py
        .detach(move || {
            use base64::Engine as _;
            base64::engine::general_purpose::STANDARD.decode(encoded)
        })
        .map_err(|_| invalid(py, "content_base64 is not valid base64"))?;
    if bytes.len() > max_bytes {
        return Err(invalid(py, "the file exceeds the upload size limit"));
    }
    if bytes.is_empty() {
        return Err(failure(
            py,
            "SOUNDING_INVALID",
            &format!("{name} is empty."),
        ));
    }
    let cache = PyModule::import(py, "sharpmod.bridge.sounding_cache")?.call_method0("cache")?;
    let id: String = cache.call_method0("new_id")?.extract()?;
    let path: PathBuf = cache.call_method1("data_path", (&id, suffix))?.extract()?;
    let write_path = path.clone();
    let mut pending = py.detach(move || {
        let mut pending = Pending::default();
        pending.write(write_path, &bytes)?;
        Ok::<_, std::io::Error>(pending)
    })?;
    // Keep the existing decoder and summary interfaces, including custom formats.
    let api = PyModule::import(py, "sharpmod.bridge.sounding_open")?;
    let keywords = PyDict::new(py);
    keywords.set_item("display", &name)?;
    let decoded = api.getattr("decode")?.call((&path,), Some(&keywords))?;
    let collection = decoded.get_item(0)?;
    let station = decoded.get_item(1)?;
    let model = api.call_method1("meta", (&collection, "model"))?;
    let forecast = model.is_truthy()?
        && !api
            .call_method1("meta", (&collection, "observed"))?
            .is_truthy()?;
    let fields = PyDict::new(py);
    let title = if station.is_truthy()? {
        format!("{} {name}", station.str()?.to_str()?)
            .trim()
            .to_string()
    } else {
        name.clone()
    };
    fields.set_item("title", title)?;
    fields.set_item(
        "location",
        if station.is_truthy()? {
            station
        } else {
            py.None().into_bound(py)
        },
    )?;
    fields.set_item("file_name", &name)?;
    fields.set_item(
        "model",
        if forecast {
            model.str()?.into_any()
        } else {
            py.None().into_bound(py)
        },
    )?;
    fields.set_item(
        "run_time",
        if forecast {
            api.getattr("p")?
                .call_method1("iso", (api.call_method1("meta", (&collection, "run"))?,))?
        } else {
            py.None().into_bound(py)
        },
    )?;
    let summary = api
        .getattr("summary")?
        .call((&id, "file", &collection), Some(&fields))?;
    let result = api.call_method1("register", (&id, &path, &summary, collection))?;
    pending.committed = true;
    Ok(result)
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(open_sounding_file, module)?)
}

#[cfg(test)]
mod tests;

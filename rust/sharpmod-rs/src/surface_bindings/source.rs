//! Compatibility for caller-supplied openers; production HTTP stays in Rust.

use pyo3::{
    exceptions::{PyTypeError, PyValueError},
    prelude::*,
    types::{PyBool, PyByteArray, PyBytes, PyDict, PyList, PyTuple},
};
use serde_json::{Map, Value};
use sharpmod_weather::surface::{JsonSource, MAX_BYTES};
use std::{sync::Arc, time::Duration};

pub(super) fn value(input: &Bound<'_, PyAny>) -> PyResult<Value> {
    if input.is_none() {
        return Ok(Value::Null);
    }
    if let Ok(boolean) = input.cast::<PyBool>() {
        return Ok(Value::Bool(boolean.is_true()));
    }
    if let Ok(text) = input.extract::<String>() {
        return Ok(Value::String(text));
    }
    if let Ok(dict) = input.cast::<PyDict>() {
        let mut output = Map::new();
        for (key, entry) in dict {
            output.insert(key.extract::<String>()?, value(&entry)?);
        }
        return Ok(Value::Object(output));
    }
    if let Ok(list) = input.cast::<PyList>() {
        return list
            .iter()
            .map(|entry| value(&entry))
            .collect::<PyResult<Vec<_>>>()
            .map(Value::Array);
    }
    if let Ok(tuple) = input.cast::<PyTuple>() {
        return tuple
            .iter()
            .map(|entry| value(&entry))
            .collect::<PyResult<Vec<_>>>()
            .map(Value::Array);
    }
    if let Ok(number) = input.extract::<f64>() {
        return Ok(serde_json::Number::from_f64(number)
            .map(Value::Number)
            .unwrap_or(Value::Null));
    }
    if let Ok(items) = input.call_method0("items") {
        let mut output = Map::new();
        for pair in items.try_iter()? {
            let pair = pair?;
            output.insert(
                pair.get_item(0)?.extract::<String>()?,
                value(&pair.get_item(1)?)?,
            );
        }
        return Ok(Value::Object(output));
    }
    Err(PyValueError::new_err(
        "NWS API response contains an unsupported value",
    ))
}

fn response(input: &Bound<'_, PyAny>) -> PyResult<Value> {
    if input.hasattr("items")? {
        return value(input);
    }
    if let Ok(check) = input.getattr("raise_for_status") {
        if check.is_callable() {
            check.call0()?;
        }
    }
    let raw = if input.is_instance_of::<PyBytes>() || input.is_instance_of::<PyByteArray>() {
        Some(input.clone())
    } else if let Some(content) = input
        .getattr("content")
        .ok()
        .filter(|content| !content.is_none())
    {
        Some(content)
    } else if let Some(read) = input.getattr("read").ok().filter(|read| read.is_callable()) {
        Some(read.call0()?)
    } else {
        None
    };
    let output = if let Some(raw) = raw {
        let bytes: Vec<u8> = PyModule::import(input.py(), "builtins")?
            .getattr("bytes")?
            .call1((raw,))?
            .extract()?;
        if bytes.len() > MAX_BYTES {
            return Err(PyValueError::new_err(
                "NWS API response exceeds the safety limit",
            ));
        }
        serde_json::from_slice(&bytes).map_err(|error| {
            PyValueError::new_err(format!("NWS API returned invalid JSON: {error}"))
        })?
    } else if let Ok(decode) = input.getattr("json") {
        value(&decode.call0()?)?
    } else {
        Value::Null
    };
    if !output.is_object() {
        return Err(PyValueError::new_err(
            "NWS API response root is not an object",
        ));
    }
    Ok(output)
}

pub(super) fn cancelled(py: Python<'_>, cancel: Option<&Py<PyAny>>) -> PyResult<bool> {
    let Some(cancel) = cancel else {
        return Ok(false);
    };
    let cancel = cancel.bind(py);
    if cancel.is_callable() {
        return cancel.call0()?.is_truthy();
    }
    if let Ok(check) = cancel.getattr("is_set") {
        if check.is_callable() {
            return check.call0()?.is_truthy();
        }
    }
    cancel.is_truthy()
}

#[derive(Clone)]
pub(super) struct Opener(Arc<Py<PyAny>>);

impl Opener {
    pub(super) fn new(opener: Py<PyAny>) -> Self {
        Self(Arc::new(opener))
    }
}

impl JsonSource for Opener {
    async fn get_json(&self, url: &str, timeout: Duration) -> Result<Value, String> {
        let opener = self.0.clone();
        let url = url.to_owned();
        tokio::task::spawn_blocking(move || {
            Python::attach(|py| {
                let kwargs = PyDict::new(py);
                kwargs.set_item("timeout", timeout.as_secs_f64())?;
                let opened = match opener.bind(py).call((&url,), Some(&kwargs)) {
                    Err(error) if error.is_instance_of::<PyTypeError>(py) => {
                        opener.bind(py).call1((&url,))?
                    }
                    result => result?,
                };
                response(&opened)
            })
            .map_err(|error: PyErr| error.to_string())
        })
        .await
        .map_err(|error| error.to_string())?
    }
}

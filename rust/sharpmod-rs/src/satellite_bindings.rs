//! GOES transport shares the model HTTP pool and keeps caller-supplied openers.

use pyo3::{
    exceptions::PyTypeError,
    prelude::*,
    types::{PyByteArray, PyBytes, PyDict},
};
use sharpmod_weather::model_http::{Error, ModelClient};
use std::sync::OnceLock;

fn provider_error(py: Python<'_>, kind: &str, message: impl Into<String>) -> PyErr {
    let error = || -> PyResult<PyErr> {
        let kind = PyModule::import(py, "sharpmod.providers.satellite_context")?.getattr(kind)?;
        Ok(PyErr::from_value(kind.call1((message.into(),))?))
    };
    error().unwrap_or_else(|error| error)
}

fn response_bytes(
    response: Bound<'_, PyAny>,
    limit: usize,
    cancelled: impl Fn() -> PyResult<bool>,
) -> PyResult<Py<PyBytes>> {
    let py = response.py();
    let bytes = PyModule::import(py, "builtins")?.getattr("bytes")?;
    let payload =
        if response.is_instance_of::<PyBytes>() || response.is_instance_of::<PyByteArray>() {
            bytes.call1((&response,))?.cast_into::<PyBytes>()?
        } else if let Some(stream) = response.getattr_opt("iter_content")? {
            if let Some(check) = response
                .getattr_opt("raise_for_status")?
                .filter(|check| check.is_callable())
            {
                check.call0()?;
            }
            let kwargs = PyDict::new(py);
            kwargs.set_item("chunk_size", 256 * 1024)?;
            let mut output = Vec::new();
            for chunk in stream.call((), Some(&kwargs))?.try_iter()? {
                let chunk = chunk?;
                if cancelled()? {
                    return Err(provider_error(
                        py,
                        "SatelliteCancelled",
                        "satellite request cancelled",
                    ));
                }
                if !chunk.is_truthy()? {
                    continue;
                }
                let chunk = bytes.call1((chunk,))?.cast_into::<PyBytes>()?;
                if chunk.as_bytes().len() > limit - output.len() {
                    return Err(provider_error(
                        py,
                        "SatelliteContextError",
                        "GOES response exceeds the safety limit",
                    ));
                }
                output.extend_from_slice(chunk.as_bytes());
            }
            PyBytes::new(py, &output)
        } else {
            let content = match response
                .getattr_opt("content")?
                .filter(|content| !content.is_none())
            {
                Some(content) => content,
                None => match response
                    .getattr_opt("read")?
                    .filter(|read| read.is_callable())
                {
                    Some(read) => read.call0()?,
                    None => py.None().into_bound(py),
                },
            };
            if content.is_none() {
                return Err(provider_error(
                    py,
                    "SatelliteContextError",
                    "GOES provider returned no readable body",
                ));
            }
            bytes.call1((content,))?.cast_into::<PyBytes>()?
        };
    if payload.as_bytes().len() > limit {
        return Err(provider_error(
            py,
            "SatelliteContextError",
            "GOES response exceeds the safety limit",
        ));
    }
    Ok(payload.unbind())
}

#[pyfunction(signature=(url, timeout, limit, opener=None, cancel=None))]
fn fetch_satellite_bytes(
    py: Python<'_>,
    url: String,
    timeout: f64,
    limit: usize,
    opener: Option<Py<PyAny>>,
    cancel: Option<Py<PyAny>>,
) -> PyResult<Py<PyBytes>> {
    let cancel = cancel
        .map(|value| -> PyResult<_> {
            if value.bind(py).is_callable() {
                return Ok((value, true));
            }
            if let Some(check) = value
                .bind(py)
                .getattr_opt("is_set")?
                .filter(|check| check.is_callable())
            {
                return Ok((check.unbind(), true));
            }
            Ok((value, false))
        })
        .transpose()?;
    let cancelled = || {
        Python::attach(|py| match &cancel {
            None => Ok(false),
            Some((value, true)) => value.bind(py).call0()?.is_truthy(),
            Some((value, false)) => value.bind(py).is_truthy(),
        })
    };
    if cancelled()? {
        return Err(provider_error(
            py,
            "SatelliteCancelled",
            "satellite request cancelled",
        ));
    }
    if let Some(opener) = opener {
        let kwargs = PyDict::new(py);
        kwargs.set_item("timeout", timeout)?;
        let response = match opener.bind(py).call((&url,), Some(&kwargs)) {
            Err(error) if error.is_instance_of::<PyTypeError>(py) => {
                opener.bind(py).call1((&url,))?
            }
            result => result?,
        };
        return response_bytes(response, limit, cancelled);
    }
    let runtime = crate::weather::runtime()?;
    let callback_error = OnceLock::new();
    let result = py.detach(|| {
        runtime.block_on(ModelClient::shared().fetch(&url, timeout, limit, None, || {
            cancelled().map_err(|error| {
                let message = error.to_string();
                let _ = callback_error.set(error);
                message
            })
        }))
    });
    if let Some(error) = callback_error.into_inner() {
        return Err(error);
    }
    match result {
        Ok(bytes) => Ok(PyBytes::new(py, &bytes).unbind()),
        Err(Error::Cancelled) => Err(provider_error(
            py,
            "SatelliteCancelled",
            "satellite request cancelled",
        )),
        Err(error) => {
            let message = match error {
                Error::Budget => "GOES response exceeds the safety limit".into(),
                Error::Http(status) | Error::Unavailable(status) => {
                    format!("GOES request failed: HTTP {status}")
                }
                Error::Offline(message) | Error::Invalid(message) => {
                    format!("GOES request failed: {message}")
                }
                Error::Cancelled => unreachable!("cancellation handled above"),
            };
            Err(provider_error(py, "SatelliteContextError", message))
        }
    }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(fetch_satellite_bytes, module)?)?;
    Ok(())
}

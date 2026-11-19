use super::{columns, observed_error};
use crate::portable_qc::portable_profile_issues;
use pyo3::{
    exceptions::{PyAttributeError, PyTypeError, PyValueError},
    prelude::*,
    types::{PyDict, PyList, PyString, PyTuple},
};

fn validated(py: Python<'_>, result: &Bound<'_, PyAny>) -> PyResult<()> {
    let validate = || -> PyResult<()> {
        let profile = result.getattr("profile")?;
        let arrays = columns(py, &profile)?;
        let count = arrays.get_item("pres")?.unwrap().len()?;
        let limit = std::env::var("SHARPMOD_MAX_PROFILE_LEVELS")
            .ok()
            .and_then(|value| value.parse::<usize>().ok())
            .filter(|value| *value > 0)
            .unwrap_or(20_000);
        if !(2..=limit).contains(&count) {
            return Err(PyValueError::new_err(
                "observed sounding has an invalid level count",
            ));
        }
        match profile.getattr("omeg") {
            Ok(omega) if !omega.is_none() => {
                let numpy = PyModule::import(py, "numpy")?;
                let dtype = PyDict::new(py);
                dtype.set_item("dtype", numpy.getattr("float64")?)?;
                let array = numpy.call_method("asarray", (omega,), Some(&dtype))?;
                if array.getattr("ndim")?.extract::<usize>()? != 1 || array.len()? != count {
                    return Err(PyValueError::new_err(
                        "observed omega must match the profile levels",
                    ));
                }
            }
            Ok(_) => {}
            Err(error) if error.is_instance_of::<PyAttributeError>(py) => {}
            Err(error) => return Err(error),
        }
        let metadata = result.getattr("metadata")?;
        for (name, maximum) in [("lat", 90.), ("lon", 180.)] {
            let coordinate = metadata.call_method1("get", (name, 0.))?.extract::<f64>()?;
            if !coordinate.is_finite() || coordinate.abs() > maximum {
                return Err(PyValueError::new_err(format!(
                    "observed {name} is out of range"
                )));
            }
        }
        PyModule::import(py, "sharpmod.providers.observations")?
            .call_method1("_utc_datetime", (result.getattr("valid")?,))?;
        let issues = portable_profile_issues(py, &arrays)?;
        let fatal: Vec<String> = issues
            .iter()
            .map(|value| value.extract::<String>())
            .collect::<PyResult<Vec<_>>>()?
            .into_iter()
            .filter(|value| value != "dewpoint_above_temperature")
            .collect();
        if fatal.is_empty() {
            Ok(())
        } else {
            Err(PyValueError::new_err(format!(
                "observed sounding failed physical quality control: {}",
                fatal.join(", ")
            )))
        }
    };
    validate().map_err(|error| {
        if error.is_instance_of::<PyAttributeError>(py)
            || error.is_instance_of::<PyTypeError>(py)
            || error.is_instance_of::<PyValueError>(py)
            || error.is_instance_of::<pyo3::exceptions::PyOverflowError>(py)
        {
            observed_error(py, "ObservedParseError", error.to_string())
        } else {
            error
        }
    })
}

#[pyfunction]
pub(super) fn fetch_observed<'py>(
    py: Python<'py>,
    station: &Bound<'py, PyAny>,
    when: &Bound<'py, PyAny>,
    providers: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let providers = PyTuple::new(py, providers.try_iter()?.collect::<PyResult<Vec<_>>>()?)?;
    if providers.is_empty() {
        return Err(PyValueError::new_err(
            "at least one observed-sounding provider is required",
        ));
    }
    let api = PyModule::import(py, "sharpmod.providers.observations")?;
    let provider_error = api.getattr("ObservedProviderError")?;
    let protocol = api.getattr("ObservedSoundingProvider")?;
    let attempts = PyList::empty(py);
    for input in providers {
        let provider = if input.is_instance_of::<PyString>() {
            api.call_method1("get_observed_provider", (&input,))?
        } else {
            input
        };
        if !provider.is_instance(&protocol)? {
            return Err(PyTypeError::new_err(
                "provider does not implement ObservedSoundingProvider",
            ));
        }
        let result = provider
            .call_method1("fetch", (station, when))
            .and_then(|result| {
                validated(py, &result)?;
                Ok(result)
            });
        let result = match result {
            Ok(result) => result,
            Err(error) if error.is_instance(py, &provider_error) => {
                let attempt = PyDict::new(py);
                attempt.set_item("provider", provider.getattr("info")?.getattr("key")?)?;
                attempt.set_item("error_type", error.get_type(py).getattr("__name__")?)?;
                attempt.set_item("error", error.value(py).str()?)?;
                attempts.append(attempt)?;
                continue;
            }
            Err(error) => return Err(error),
        };
        if attempts.is_empty() {
            return Ok(result);
        }
        let metadata = PyDict::new(py);
        let reported = py
            .get_type::<PyDict>()
            .call1((result.getattr("metadata")?,))?;
        metadata.update(reported.cast::<PyDict>()?.as_mapping())?;
        metadata.set_item("fallback_attempts", PyTuple::new(py, attempts.iter())?)?;
        api.call_method1(
            "_set_profile_metadata",
            (result.getattr("profile")?, &metadata),
        )?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("metadata", metadata)?;
        return PyModule::import(py, "dataclasses")?
            .getattr("replace")?
            .call((result,), Some(&kwargs));
    }
    Err(PyErr::from_value(
        api.getattr("ObservedFallbackError")?.call1((attempts,))?,
    ))
}

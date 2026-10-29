//! IEM sounding transport and owned columns; existing provider DTOs stay public.

use crate::parsing::python_float_literal;
use numpy::IntoPyArray;
use pyo3::{
    exceptions::{PyTypeError, PyValueError},
    prelude::*,
    types::{PyDict, PyFloat, PyList, PyString},
};
use sharpmod_weather::model_http::{Error, ModelClient};

const MAX_BYTES: usize = 8 * 1024 * 1024;

fn observed_error(py: Python<'_>, kind: &str, message: impl Into<String>) -> PyErr {
    let error = || -> PyResult<PyErr> {
        let kind = PyModule::import(py, "sharpmod.providers.observations")?.getattr(kind)?;
        Ok(PyErr::from_value(kind.call1((message.into(),))?))
    };
    error().unwrap_or_else(|error| error)
}

#[pyfunction]
fn fetch_iem_json<'py>(py: Python<'py>, url: &str, timeout: f64) -> PyResult<Bound<'py, PyAny>> {
    let runtime = crate::weather::runtime()?;
    let bytes = py
        .detach(|| runtime.block_on(ModelClient::shared().fetch_once(url, timeout, MAX_BYTES)))
        .map_err(|error| {
            let (kind, message) = match error {
                Error::Unavailable(status) | Error::Http(status) if matches!(status, 404 | 422) => {
                    (
                        "ObservedUnavailableError",
                        format!("IEM has no sounding matching this request ({status})"),
                    )
                }
                Error::Unavailable(status) | Error::Http(status) => (
                    "ObservedRetrievalError",
                    format!("IEM request failed with HTTP {status}: {url}"),
                ),
                Error::Offline(message) if message == "request timed out" => (
                    "ObservedRetrievalError",
                    format!("IEM request timed out after {timeout}s: {url}"),
                ),
                Error::Offline(message) | Error::Invalid(message) => (
                    "ObservedRetrievalError",
                    format!("IEM service could not be reached ({message}): {url}"),
                ),
                Error::Budget => (
                    "ObservedRetrievalError",
                    "IEM response exceeds the safety limit".into(),
                ),
                Error::Cancelled => {
                    unreachable!("single IEM transfers have no cancellation callback")
                }
            };
            observed_error(py, kind, message)
        })?;
    let text = std::str::from_utf8(&bytes).map_err(|error| {
        observed_error(
            py,
            "ObservedParseError",
            format!("IEM returned invalid JSON for {url}: {error}"),
        )
    })?;
    // Keep the standard JSON primitive at the object boundary, including large integers.
    PyModule::import(py, "json")?
        .call_method1("loads", (text,))
        .map_err(|error| {
            if error.is_instance_of::<PyValueError>(py) {
                observed_error(
                    py,
                    "ObservedParseError",
                    format!("IEM returned invalid JSON for {url}: {error}"),
                )
            } else {
                error
            }
        })
}

fn number_text(value: &str) -> Result<f64, ()> {
    python_float_literal(value)?.trim().parse().map_err(|_| ())
}

fn number(value: &Bound<'_, PyAny>) -> PyResult<f64> {
    if let Ok(text) = value.cast::<PyString>() {
        return number_text(text.to_str()?)
            .map_err(|()| PyValueError::new_err("invalid IEM number"));
    }
    value.py().get_type::<PyFloat>().call1((value,))?.extract()
}

#[pyfunction(signature=(rows, missing=-9999.))]
fn iem_sounding_columns<'py>(
    py: Python<'py>,
    rows: &Bound<'py, PyList>,
    missing: f64,
) -> PyResult<Bound<'py, PyDict>> {
    let mut columns: [Vec<f64>; 6] = std::array::from_fn(|_| Vec::with_capacity(rows.len()));
    let inputs = ["pres", "hght", "tmpc", "dwpc", "drct", "sknt"];
    for (index, row) in rows.iter().enumerate() {
        let row = row.cast::<PyDict>().map_err(|_| {
            observed_error(
                py,
                "ObservedParseError",
                format!("IEM sounding level {index} is not an object"),
            )
        })?;
        let Some(pressure) = row.get_item("pres")?.filter(|pressure| !pressure.is_none()) else {
            continue;
        };
        let pressure = number(&pressure).map_err(|error| {
            if error.is_instance_of::<PyTypeError>(py) || error.is_instance_of::<PyValueError>(py) {
                observed_error(
                    py,
                    "ObservedParseError",
                    format!("IEM sounding level {index} has invalid pressure"),
                )
            } else {
                error
            }
        })?;
        columns[0].push(pressure);
        for column in 1..6 {
            let value = match row
                .get_item(inputs[column])?
                .filter(|value| !value.is_none())
            {
                Some(value) => number(&value).map_err(|error| {
                    if error.is_instance_of::<PyTypeError>(py)
                        || error.is_instance_of::<PyValueError>(py)
                    {
                        observed_error(
                            py,
                            "ObservedParseError",
                            format!("IEM sounding level {index} has invalid {}", inputs[column]),
                        )
                    } else {
                        error
                    }
                })?,
                None => missing,
            };
            columns[column].push(value);
        }
    }
    if columns[0].is_empty() {
        return Err(observed_error(
            py,
            "ObservedParseError",
            "IEM sounding contains no pressure levels",
        ));
    }
    let output = PyDict::new(py);
    for (name, values) in ["pres", "hght", "tmpc", "dwpc", "wdir", "wspd"]
        .into_iter()
        .zip(columns)
    {
        output.set_item(name, values.into_pyarray(py))?;
    }
    Ok(output)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(fetch_iem_json, module)?)?;
    module.add_function(wrap_pyfunction!(iem_sounding_columns, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::number_text;

    #[test]
    fn numeric_strings_keep_python_decimal_underscores_and_nonfinite_values() {
        for (text, expected) in [
            (" 1_012.٥ ", 1012.5),
            ("-2.5e1", -25.),
            ("+0", 0.),
            (".5", 0.5),
        ] {
            assert_eq!(number_text(text).unwrap(), expected);
        }
        assert!(number_text("nan").unwrap().is_nan());
        assert_eq!(number_text("-Infinity").unwrap(), f64::NEG_INFINITY);
        for text in ["", "1__0", "_10", "10_", "2 5", "5m", "0x10"] {
            assert!(number_text(text).is_err(), "{text}");
        }
    }
}

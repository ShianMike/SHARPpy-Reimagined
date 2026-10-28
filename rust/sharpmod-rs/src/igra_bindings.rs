//! Restore existing IGRA objects after native fixed-width parsing.

use crate::igra::{self, Error, Header};
use pyo3::{
    exceptions::{PyOverflowError, PyValueError, PyZeroDivisionError},
    prelude::*,
};

fn error(error: Error) -> PyErr {
    match error {
        Error::DivisionByZero => PyZeroDivisionError::new_err("float division by zero"),
        Error::Overflow => PyOverflowError::new_err("math range error"),
        Error::PastEnd => PyValueError::new_err("header points past end of file"),
    }
}

fn header_object(class: &Bound<'_, PyAny>, row: Header) -> PyResult<Py<PyAny>> {
    Ok(class
        .call1((
            row.station,
            row.date[0],
            row.date[1],
            row.date[2],
            row.date[3],
            row.release,
            row.levels,
            &row.sources[0],
            &row.sources[1],
            row.position[0],
            row.position[1],
            row.line,
        ))?
        .unbind())
}

#[pyfunction]
fn igra_header(py: Python<'_>, line: &str, index: usize) -> PyResult<Py<PyAny>> {
    let class = PyModule::import(py, "sharpmod.io.igra2")?.getattr("IGRAHeader")?;
    header_object(&class, igra::header(line, index))
}

#[pyfunction]
fn igra_headers(py: Python<'_>, text: &str) -> PyResult<Vec<Py<PyAny>>> {
    // Python strings are immutable and the argument keeps their storage alive.
    let rows = py.detach(|| igra::headers(text));
    let class = PyModule::import(py, "sharpmod.io.igra2")?.getattr("IGRAHeader")?;
    rows.into_iter()
        .map(|row| header_object(&class, row))
        .collect()
}

#[pyfunction]
fn igra_level(py: Python<'_>, line: &str) -> PyResult<Option<Py<PyAny>>> {
    igra::level(line)
        .map_err(error)?
        .map(|row| {
            let class = PyModule::import(py, "sharpmod.io.igra2")?.getattr("IGRALevel")?;
            let [pressure, height, temperature, dewpoint, direction, speed] = row.values;
            Ok(class
                .call1((
                    pressure,
                    height,
                    temperature,
                    dewpoint,
                    direction,
                    speed,
                    row.surface,
                    row.reconstructed,
                ))?
                .unbind())
        })
        .transpose()
}

#[pyfunction]
fn igra_dewpoint_from_relative_humidity(temperature: f64, humidity: f64) -> PyResult<f64> {
    igra::dewpoint(temperature, humidity).map_err(error)
}

#[pyfunction]
fn igra_sounding_rows(
    py: Python<'_>,
    text: &str,
    index: usize,
    count: i64,
    station: &str,
) -> PyResult<([Vec<f64>; 6], usize, usize)> {
    let rows = match py.detach(|| igra::sounding_rows(text, index, count)) {
        Ok(rows) => rows,
        Err(Error::PastEnd) => {
            let exception = PyModule::import(py, "sharpmod.io.igra2")?
                .getattr("IGRASoundingParseError")?
                .call1((format!("IGRA header for {station} points past end of file"),))?;
            return Err(PyErr::from_value(exception));
        }
        Err(other) => return Err(error(other)),
    };
    Ok((rows.columns, rows.reconstructed, rows.dropped))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(igra_header, module)?)?;
    module.add_function(wrap_pyfunction!(igra_headers, module)?)?;
    module.add_function(wrap_pyfunction!(igra_level, module)?)?;
    module.add_function(wrap_pyfunction!(igra_sounding_rows, module)?)?;
    module.add_function(wrap_pyfunction!(
        igra_dewpoint_from_relative_humidity,
        module
    )?)?;
    Ok(())
}

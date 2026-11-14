//! Normalize existing Python profile inputs once, then evaluate owned Rust data.

use numpy::{IntoPyArray, PyReadonlyArray1, PyReadonlyArrayDyn, PyUntypedArrayMethods};
use pyo3::{
    exceptions::{PyAttributeError, PyTypeError, PyValueError},
    prelude::*,
    types::{PyDict, PySlice},
};

use crate::profile_statistics;

type DistributionResult = (
    usize,
    Option<f64>,
    Option<f64>,
    Option<f64>,
    Option<f64>,
    Option<f64>,
);

fn invalid_scalar(py: Python<'_>, error: &PyErr) -> bool {
    error.is_instance_of::<PyTypeError>(py) || error.is_instance_of::<PyValueError>(py)
}

#[pyfunction]
fn profile_metric_distribution(
    py: Python<'_>,
    values: &Bound<'_, PyAny>,
) -> PyResult<DistributionResult> {
    let masked = PyModule::import(py, "numpy.ma")?.getattr("is_masked")?;
    let float = PyModule::import(py, "builtins")?.getattr("float")?;
    let mut numbers = Vec::new();
    for value in values.try_iter()? {
        let value = value?;
        if value.is_none() {
            continue;
        }
        match masked
            .call1((&value,))
            .and_then(|result| result.is_truthy())
        {
            Ok(true) => continue,
            Ok(false) => (),
            Err(error) if invalid_scalar(py, &error) => (),
            Err(error) => return Err(error),
        }
        let number = match float
            .call1((value,))
            .and_then(|result| result.extract::<f64>())
        {
            Ok(number) => number,
            Err(error) if invalid_scalar(py, &error) => continue,
            Err(error) => return Err(error),
        };
        if number.is_finite() && number > -9998.0 {
            numbers.push(number);
        }
    }
    let result = py.detach(|| profile_statistics::distribution(numbers));
    Ok((
        result.count,
        result.p10,
        result.median,
        result.p90,
        result.minimum,
        result.maximum,
    ))
}

fn column_samples(
    py: Python<'_>,
    pressure: &Bound<'_, PyAny>,
    values: &Bound<'_, PyAny>,
) -> PyResult<Vec<(f64, f64)>> {
    let numpy = PyModule::import(py, "numpy")?;
    let masked = PyModule::import(py, "numpy.ma")?;
    let keywords = PyDict::new(py);
    keywords.set_item("dtype", numpy.getattr("float64")?)?;
    let pressure = masked.call_method("asarray", (pressure,), Some(&keywords))?;
    let values = masked.call_method("asarray", (values,), Some(&keywords))?;
    let length = pressure
        .getattr("size")?
        .extract::<isize>()?
        .min(values.getattr("size")?.extract::<isize>()?);
    let slice = PySlice::new(py, 0, length, 1);
    let pressure = pressure.get_item(&slice)?;
    let values = values.get_item(slice)?;
    let pressure_data = pressure.getattr("data")?;
    let values_data = values.getattr("data")?;
    let pressure_mask = masked.call_method1("getmaskarray", (pressure,))?;
    let values_mask = masked.call_method1("getmaskarray", (values,))?;
    let pressure = pressure_data.extract::<PyReadonlyArrayDyn<'_, f64>>()?;
    let values = values_data.extract::<PyReadonlyArrayDyn<'_, f64>>()?;
    let pressure_mask = pressure_mask.extract::<PyReadonlyArrayDyn<'_, bool>>()?;
    let values_mask = values_mask.extract::<PyReadonlyArrayDyn<'_, bool>>()?;
    if pressure.shape() != values.shape() {
        return Err(PyValueError::new_err("profile column shapes differ"));
    }
    let samples: Vec<_> = pressure
        .as_array()
        .iter()
        .zip(values.as_array().iter())
        .zip(pressure_mask.as_array().iter())
        .zip(values_mask.as_array().iter())
        .filter_map(|(((&p, &value), &p_mask), &value_mask)| {
            (!p_mask
                && !value_mask
                && p.is_finite()
                && p > 0.0
                && value.is_finite()
                && value > -9998.0)
                .then_some((p, value))
        })
        .collect();
    if samples.len() < 2 {
        return Ok(samples);
    }
    // The old helper used NumPy's default argsort before choosing the first
    // duplicate. Keep that platform-specific tie ordering at this boundary.
    let coordinates: Vec<_> = samples.iter().map(|sample| sample.0).collect();
    let order = numpy.call_method1("argsort", (coordinates.into_pyarray(py),))?;
    let order = order.extract::<PyReadonlyArray1<'_, isize>>()?;
    Ok(order
        .as_array()
        .iter()
        .map(|&index| samples[index as usize])
        .collect())
}

#[pyfunction]
fn profile_metric_column(
    py: Python<'_>,
    profile: &Bound<'_, PyAny>,
    field: &str,
    levels: Vec<f64>,
) -> PyResult<Vec<Option<f64>>> {
    let pressure = profile_attribute(profile, "pres")?;
    let values = profile_attribute(profile, field)?;
    let samples = match (pressure, values) {
        (Some(pressure), Some(values)) => match column_samples(py, &pressure, &values) {
            Ok(samples) => samples,
            Err(error) if invalid_scalar(py, &error) => Vec::new(),
            Err(error) => return Err(error),
        },
        _ => Vec::new(),
    };
    Ok(py.detach(|| profile_statistics::pressure_column(samples, &levels)))
}

fn profile_attribute<'py>(
    profile: &Bound<'py, PyAny>,
    name: &str,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    match profile.getattr(name) {
        Ok(value) => Ok((!value.is_none()).then_some(value)),
        Err(error) if error.is_instance_of::<PyAttributeError>(profile.py()) => Ok(None),
        Err(error) => Err(error),
    }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(profile_metric_distribution, module)?)?;
    module.add_function(wrap_pyfunction!(profile_metric_column, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use pyo3::types::PyList;

    #[test]
    fn metric_boundary_keeps_masked_missing_and_numeric_string_behavior() {
        Python::initialize();
        Python::attach(|py| {
            let values = PyList::empty(py);
            values.append(1.0).unwrap();
            values.append("5").unwrap();
            values.append(9.0).unwrap();
            values.append(py.None()).unwrap();
            values.append("unavailable").unwrap();
            values.append(f64::NAN).unwrap();
            values.append(f64::INFINITY).unwrap();
            values.append(-9998.0).unwrap();
            values.append(-9999.0).unwrap();
            values
                .append(
                    PyModule::import(py, "numpy.ma")
                        .unwrap()
                        .getattr("masked")
                        .unwrap(),
                )
                .unwrap();
            let iterator = values.call_method0("__iter__").unwrap();
            let actual = profile_metric_distribution(py, &iterator).unwrap();
            assert_eq!(
                actual,
                (3, Some(1.8), Some(5.0), Some(8.2), Some(1.0), Some(9.0))
            );
        });
    }

    #[test]
    fn column_boundary_aligns_short_masked_columns_without_modifying_them() {
        Python::initialize();
        Python::attach(|py| {
            let masked = PyModule::import(py, "numpy.ma").unwrap();
            let keywords = PyDict::new(py);
            keywords
                .set_item("mask", [false, true, false, false, false])
                .unwrap();
            let pressure = masked
                .call_method(
                    "array",
                    ([1000.0, 850.0, 700.0, 500.0, 200.0],),
                    Some(&keywords),
                )
                .unwrap();
            let profile = PyModule::import(py, "types")
                .unwrap()
                .getattr("SimpleNamespace")
                .unwrap()
                .call0()
                .unwrap();
            profile.setattr("pres", &pressure).unwrap();
            profile.setattr("tmpc", [20.0, 80.0, 0.0, -20.0]).unwrap();
            let actual = profile_metric_column(
                py,
                &profile,
                "tmpc",
                vec![1100.0, 1000.0, 700.0, 500.0, 400.0],
            )
            .unwrap();
            assert_eq!(actual, vec![None, Some(20.0), Some(0.0), Some(-20.0), None]);
            assert_eq!(
                pressure
                    .getattr("size")
                    .unwrap()
                    .extract::<usize>()
                    .unwrap(),
                5
            );
            assert_eq!(
                profile
                    .getattr("tmpc")
                    .unwrap()
                    .extract::<Vec<f64>>()
                    .unwrap(),
                [20.0, 80.0, 0.0, -20.0]
            );
            assert_eq!(
                profile_metric_column(py, &profile, "dwpc", vec![750.0]).unwrap(),
                vec![None]
            );
            profile.setattr("tmpc", ["invalid"]).unwrap();
            assert_eq!(
                profile_metric_column(py, &profile, "tmpc", vec![750.0]).unwrap(),
                vec![None]
            );
        });
    }
}

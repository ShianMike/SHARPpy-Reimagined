//! Edits and inspection records; Qt remains the calibrated coordinate boundary.
mod geometry;
use pyo3::{
    exceptions::PyValueError,
    prelude::*,
    types::{PyDict, PyList},
};

#[pyfunction]
fn apply_sounding_edits(
    py: Python<'_>,
    collection: &Bound<'_, PyAny>,
    edits: &Bound<'_, PyList>,
) -> PyResult<()> {
    let profile = collection.call_method0("getHighlightedProf")?;
    let count = profile.getattr("pres")?.len()?;
    let arrays = PyDict::new(py);
    let mut thermal = Vec::new();
    let mut storms = Vec::new();
    let number = PyModule::import(py, "sharpmod.bridge.params")?.getattr("num")?;
    for edit in edits {
        let index: i64 = edit.get_item("index")?.extract()?;
        let values = edit.get_item("values")?.cast_into::<PyDict>()?;
        if index < 0 {
            if ![-1, -2].contains(&index)
                || values.len() != 2
                || !values.contains("u")?
                || !values.contains("v")?
            {
                return Err(PyValueError::new_err(
                    "Storm motion needs both wind components.",
                ));
            }
            storms.push((
                index,
                values.get_item("u")?.unwrap(),
                values.get_item("v")?.unwrap(),
            ));
            continue;
        }
        let index = index as usize;
        if index >= count {
            return Err(PyValueError::new_err(
                "The edited level does not exist in this sounding.",
            ));
        }
        for (key, value) in values {
            let key: String = key.extract()?;
            let source = profile.getattr(key.as_str())?;
            if number.call1((source.get_item(index)?,))?.is_none() {
                return Err(PyValueError::new_err(
                    "Missing observations cannot be edited.",
                ));
            }
            let column = match arrays.get_item(&key)? {
                Some(column) => column,
                None => {
                    let column = source.call_method0("copy")?;
                    arrays.set_item(&key, &column)?;
                    column
                }
            };
            column.set_item(index, value)?;
            if key == "tmpc" || key == "dwpc" {
                thermal.push(index);
            }
        }
    }
    let temperature = arrays.get_item("tmpc")?.unwrap_or(profile.getattr("tmpc")?);
    let moisture = arrays.get_item("dwpc")?.unwrap_or(profile.getattr("dwpc")?);
    for index in thermal {
        if moisture.get_item(index)?.gt(temperature.get_item(index)?)? {
            return Err(PyValueError::new_err("Dewpoint cannot exceed temperature."));
        }
    }
    if !arrays.is_empty() {
        collection.call_method("modify", (-999,), Some(&arrays))?;
    }
    for (index, u, v) in storms {
        collection.call_method1(
            "modifyStormMotion",
            (if index == -1 { "right" } else { "left" }, u, v),
        )?;
    }
    Ok(())
}

#[pyfunction]
fn sounding_original_values<'py>(
    py: Python<'py>,
    profile: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyDict>> {
    let prepare = PyModule::import(py, "sharpmod.backends._common")?.getattr("prepare_1d")?;
    let result = PyDict::new(py);
    for key in ["tmpc", "dwpc", "u", "v"] {
        let values = PyList::empty(py);
        for value in crate::profile_input::column(&prepare, &profile.getattr(key)?, key)? {
            values.append(value.is_finite().then_some(value))?;
        }
        result.set_item(key, values)?;
    }
    // Storm motion is a heterogeneous four-scalar tuple, not a masked column.
    let number = PyModule::import(py, "sharpmod.bridge.params")?.getattr("num")?;
    let storm = PyList::empty(py);
    for value in profile.getattr("srwind")?.try_iter()? {
        storm.append(number.call1((value?,))?)?;
    }
    result.set_item("storm", storm)?;
    Ok(result)
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(apply_sounding_edits, module)?)?;
    module.add_function(wrap_pyfunction!(sounding_original_values, module)?)?;
    geometry::register(module)
}

#[cfg(test)]
mod tests;

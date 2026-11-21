//! Cache metadata presentation through the existing public result objects.

use pyo3::{
    exceptions::{PyKeyError, PyOverflowError, PyTypeError, PyValueError},
    prelude::*,
    types::{PyDict, PyList, PyTuple},
};

fn metadata_integer<'py>(
    py: Python<'py>,
    integer: &Bound<'py, PyAny>,
    payload: &Bound<'py, PyDict>,
    name: &str,
) -> PyResult<Bound<'py, PyAny>> {
    let Some(value) = payload.get_item(name)? else {
        return Ok(0_i64.into_pyobject(py)?.into_any());
    };
    match integer.call1((value,)) {
        Ok(value) => Ok(value),
        Err(error)
            if error.is_instance_of::<PyTypeError>(py)
                || error.is_instance_of::<PyValueError>(py)
                || error.is_instance_of::<PyOverflowError>(py) =>
        {
            Ok(0_i64.into_pyobject(py)?.into_any())
        }
        Err(error) => Err(error),
    }
}

fn optional_text(payload: &Bound<'_, PyDict>, name: &str) -> PyResult<Option<String>> {
    let Some(value) = payload.get_item(name)? else {
        return Ok(None);
    };
    if value.is_none() {
        return Ok(None);
    }
    let text = value.str()?.to_string();
    Ok((!text.is_empty()).then_some(text))
}

#[pyfunction]
fn model_cache_entries<'py>(
    py: Python<'py>,
    raw: &Bound<'py, PyList>,
) -> PyResult<Bound<'py, PyList>> {
    let mut rows = Vec::with_capacity(raw.len());
    for item in raw.iter() {
        let item = item.cast_into::<PyDict>()?;
        let accessed: f64 = item
            .get_item("accessed")?
            .ok_or_else(|| PyKeyError::new_err("accessed"))?
            .extract()?;
        rows.push((if accessed.is_finite() { accessed } else { 0.0 }, item));
    }
    // Stable sorting preserves traversal order for equal times, including ±0.
    rows.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap());
    let constructor =
        PyModule::import(py, "sharpmod.models.model_disk_cache")?.getattr("CacheEntry")?;
    let integer = PyModule::import(py, "builtins")?.getattr("int")?;
    let result = PyList::empty(py);
    for (accessed, item) in rows {
        let payload = item
            .get_item("payload")?
            .ok_or_else(|| PyKeyError::new_err("payload"))?
            .cast_into::<PyDict>()?;
        let kwargs = PyDict::new(py);
        for name in [
            "path",
            "size",
            "protected",
            "pinned",
            "valid_grib",
            "valid_sounding",
            "file_count",
        ] {
            kwargs.set_item(
                name,
                item.get_item(name)?
                    .ok_or_else(|| PyKeyError::new_err(name))?,
            )?;
        }
        for (name, default) in [("model", "unknown"), ("run", "")] {
            let text = match payload.get_item(name)? {
                Some(value) => value.str()?.to_string(),
                None => default.to_owned(),
            };
            kwargs.set_item(name, text)?;
        }
        for name in ["fxx", "contract_version"] {
            kwargs.set_item(name, metadata_integer(py, &integer, &payload, name)?)?;
        }
        for name in ["member", "spatial", "source_url", "source_transport"] {
            kwargs.set_item(name, optional_text(&payload, name)?)?;
        }
        let mut fields = Vec::new();
        if let Some(value) = payload.get_item("source_fields")? {
            if !value.is_none() {
                match value.try_iter() {
                    Ok(iter) => {
                        for field in iter {
                            fields.push(field?.str()?.to_string());
                        }
                    }
                    Err(error) if error.is_instance_of::<PyTypeError>(py) => {}
                    Err(error) => return Err(error),
                }
            }
        }
        kwargs.set_item("source_fields", PyTuple::new(py, fields)?)?;
        kwargs.set_item("accessed", accessed)?;
        result.append(constructor.call((), Some(&kwargs))?)?;
    }
    Ok(result)
}

pub(super) fn maintenance_rows<'py>(cache: &Bound<'py, PyAny>) -> PyResult<Bound<'py, PyList>> {
    let scan = cache.getattr("_entries")?;
    // An older cache owner or a replacement scan may only accept the original
    // no-argument call. Only the built-in scan advertises the new option.
    if let Some(built_in) = cache.getattr_opt("_entries_with_payload_option")? {
        if scan.eq(built_in)? {
            let options = PyDict::new(cache.py());
            options.set_item("validate_payloads", false)?;
            return Ok(scan.call((), Some(&options))?.cast_into::<PyList>()?);
        }
    }
    Ok(scan.call0()?.cast_into::<PyList>()?)
}

#[pyfunction]
fn model_cache_storage_entries<'py>(
    py: Python<'py>,
    cache: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyList>> {
    model_cache_entries(py, &maintenance_rows(cache)?)
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(model_cache_entries, module)?)?;
    module.add_function(wrap_pyfunction!(model_cache_storage_entries, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests;

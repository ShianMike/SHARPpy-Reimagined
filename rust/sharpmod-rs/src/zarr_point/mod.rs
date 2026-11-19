//! HRRR F000 Zarr planning and sampling. Keep numcodecs' compiled decompressor.
use pyo3::{
    prelude::*,
    types::{PyDict, PyTuple},
};
mod fetch;
mod normalize;
fn error(py: Python<'_>, message: impl Into<String>) -> PyErr {
    let build = || -> PyResult<PyErr> {
        Ok(PyErr::from_value(
            PyModule::import(py, "sharpmod.providers.hrrr_zarr")?
                .getattr("ZarrBackendUnavailable")?
                .call1((message.into(),))?,
        ))
    };
    build().unwrap_or_else(|e| e)
}
#[pyfunction]
fn hrrr_pressure_plan<'py>(
    py: Python<'py>,
    metadata: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyAny>> {
    let mut available = std::collections::BTreeMap::<
        u64,
        std::collections::HashMap<String, (String, Py<PyAny>)>,
    >::new();
    for (key, value) in metadata.iter() {
        let key = key.str()?.to_str()?.to_owned();
        let parts: Vec<_> = key.split('/').collect();
        if parts.len() != 5 || parts[0] != parts[2] || parts[1] != parts[3] || parts[4] != ".zarray"
        {
            continue;
        }
        let Some(raw) = parts[0].strip_suffix("mb") else {
            continue;
        };
        if raw.is_empty()
            || !raw.chars().all(|c| c.is_ascii_digit() || c == '.')
            || !parts[1]
                .chars()
                .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit())
        {
            continue;
        }
        let Ok(level) = raw.parse::<f64>() else {
            continue;
        };
        if !level.is_finite() || level <= 0. {
            continue;
        }
        available.entry(level.to_bits()).or_default().insert(
            parts[1].into(),
            (key.trim_end_matches("/.zarray").into(), value.unbind()),
        );
    }
    if available.is_empty() {
        return Err(error(
            py,
            "HRRR Zarr metadata contains no pressure-level arrays",
        ));
    }
    let levels: Vec<f64> = available.keys().rev().map(|&v| f64::from_bits(v)).collect();
    let mut fields = vec!["HGT", "TMP", "UGRD", "VGRD"];
    for &level in &levels {
        let absent: Vec<_> = fields
            .iter()
            .filter(|&&f| !available[&level.to_bits()].contains_key(f))
            .copied()
            .collect();
        if !absent.is_empty() {
            return Err(error(
                py,
                format!(
                    "HRRR Zarr level {level} mb is missing {}",
                    absent.join(", ")
                ),
            ));
        }
    }
    let complete = |f: &str| available.values().all(|a| a.contains_key(f));
    if complete("RH") {
        fields.push("RH");
    } else if complete("SPFH") {
        fields.push("SPFH");
    } else {
        return Err(error(
            py,
            "HRRR Zarr pressure levels lack one complete humidity field",
        ));
    }
    for optional in ["VVEL", "ABSV"] {
        if complete(optional) {
            fields.push(optional);
        }
    }
    let arrays = PyDict::new(py);
    for &level in &levels {
        for &field in &fields {
            let (path, meta) = &available[&level.to_bits()][field];
            arrays.set_item((level, field), (path, meta.bind(py)))?;
        }
    }
    PyModule::import(py, "sharpmod.providers.hrrr_zarr")?
        .getattr("PressurePlan")?
        .call1((PyTuple::new(py, levels)?, PyTuple::new(py, fields)?, arrays))
}
#[pyfunction]
fn hrrr_surface_plan<'py>(
    py: Python<'py>,
    metadata: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyAny>> {
    let api = PyModule::import(py, "sharpmod.providers.hrrr_zarr")?;
    let paths = api.getattr("_SURFACE_ARRAY_PATHS")?;
    let arrays = PyDict::new(py);
    let mut absent = Vec::new();
    for entry in paths.call_method0("items")?.try_iter()? {
        let entry = entry?;
        let field = entry.get_item(0)?;
        let key = entry.get_item(1)?.extract::<String>()?;
        if let Some(value) = metadata.get_item(&key)?.filter(|v| !v.is_none()) {
            arrays.set_item(field, (key.trim_end_matches("/.zarray"), value))?;
        } else {
            absent.push(field.extract::<String>()?);
        }
    }
    if !absent.is_empty() {
        return Err(error(
            py,
            format!(
                "HRRR Zarr metadata is missing verified surface arrays: {}",
                absent.join(", ")
            ),
        ));
    }
    api.getattr("SurfacePlan")?.call1((arrays,))
}
pub(super) fn geometry(
    meta: &Bound<'_, PyDict>,
    iy: usize,
    ix: usize,
) -> PyResult<([usize; 2], [usize; 2], [usize; 2])> {
    let shape: [usize; 2] = meta
        .get_item("shape")?
        .ok_or_else(|| pyo3::exceptions::PyValueError::new_err("missing Zarr shape"))?
        .extract()?;
    let chunks: [usize; 2] = meta
        .get_item("chunks")?
        .ok_or_else(|| pyo3::exceptions::PyValueError::new_err("missing Zarr chunks"))?
        .extract()?;
    if chunks.contains(&0) || shape.contains(&0) || iy >= shape[0] || ix >= shape[1] {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "Zarr point or chunk dimensions are out of range",
        ));
    }
    let cell = [iy / chunks[0], ix / chunks[1]];
    let edge = [
        chunks[0].min(shape[0] - cell[0] * chunks[0]),
        chunks[1].min(shape[1] - cell[1] * chunks[1]),
    ];
    Ok((chunks, edge, [iy % chunks[0], ix % chunks[1]]))
}
#[pyfunction]
pub(super) fn decode_hrrr_zarr_point(
    py: Python<'_>,
    payload: &Bound<'_, PyAny>,
    metadata: &Bound<'_, PyDict>,
    iy: usize,
    ix: usize,
) -> PyResult<f64> {
    let result = (|| {
        if metadata.get_item("filters")?.is_some_and(|v| {
            !v.is_none() && (!v.is_instance_of::<pyo3::types::PyList>() || v.len().ok() != Some(0))
        }) {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "filtered HRRR Zarr arrays are unsupported",
            ));
        }
        let (chunks, edge, local) = geometry(metadata, iy, ix)?;
        let compressor = metadata
            .get_item("compressor")?
            .filter(|v| v.is_truthy().unwrap_or(false));
        let raw = if let Some(codec) = compressor {
            PyModule::import(py, "numcodecs")?
                .call_method1("get_codec", (codec,))?
                .call_method1("decode", (payload,))?
        } else {
            payload.clone()
        };
        let np = PyModule::import(py, "numpy")?;
        let dtype = metadata
            .get_item("dtype")?
            .ok_or_else(|| pyo3::exceptions::PyValueError::new_err("missing Zarr dtype"))?;
        let dtype = np.call_method1("dtype", (dtype,))?;
        let kind: String = dtype.getattr("kind")?.extract()?;
        if dtype.getattr("hasobject")?.is_truthy()? || !["f", "i", "u"].contains(&kind.as_str()) {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "unsupported Zarr numeric dtype",
            ));
        }
        let kwargs = PyDict::new(py);
        kwargs.set_item("dtype", dtype)?;
        let values = np.call_method("frombuffer", (raw,), Some(&kwargs))?;
        let size = values.len()?;
        let shape = if chunks[0].checked_mul(chunks[1]) == Some(size) {
            chunks
        } else if edge[0].checked_mul(edge[1]) == Some(size) {
            edge
        } else {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "decoded chunk has an unexpected size",
            ));
        };
        let order = metadata
            .get_item("order")?
            .map(|v| v.extract::<String>())
            .transpose()?
            .unwrap_or_else(|| "C".into());
        let index = match order.as_str() {
            "C" => local[0] * shape[1] + local[1],
            "F" => local[1] * shape[0] + local[0],
            _ => {
                return Err(pyo3::exceptions::PyValueError::new_err(
                    "unsupported Zarr storage order",
                ))
            }
        };
        values.get_item(index)?.extract()
    })();
    result.map_err(|e| {
        error(
            py,
            format!("could not decode an HRRR Zarr chunk: {}", e.value(py)),
        )
    })
}
pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(hrrr_pressure_plan, module)?)?;
    module.add_function(wrap_pyfunction!(hrrr_surface_plan, module)?)?;
    module.add_function(wrap_pyfunction!(decode_hrrr_zarr_point, module)?)?;
    fetch::register(module)?;
    normalize::register(module)
}
#[cfg(test)]
mod tests;

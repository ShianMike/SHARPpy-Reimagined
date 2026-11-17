//! Shared portable-file boundary. NumPy supplies its established NPY codec;
//! Rust owns limits, shape/scalar checks, physical QC and pair publication.
use numpy::PyReadonlyArray1;
use pyo3::{
    exceptions::PyValueError,
    prelude::*,
    types::{PyDict, PyString},
};
use std::path::PathBuf;
mod transaction;

pub(super) fn limit(name: &str, default: u64) -> u64 {
    std::env::var(name)
        .ok()
        .and_then(|v| v.parse().ok())
        .filter(|v| *v > 0)
        .unwrap_or(default)
}
fn invalid(message: impl Into<String>) -> PyErr {
    PyValueError::new_err(message.into())
}

#[pyfunction]
fn validate_portable_archive(py: Python<'_>, path: PathBuf) -> PyResult<()> {
    let maximum = limit("SHARPMOD_MAX_NPZ_BYTES", 64 * 1024 * 1024);
    let size = std::fs::metadata(&path)
        .map_err(|e| invalid(format!("invalid portable sounding archive: {e}")))?;
    if !size.is_file() || size.len() > maximum {
        return Err(invalid("portable sounding archive is missing or oversized"));
    }
    // ZipFile's central-directory parser and NumPy's codec are format primitives,
    // not alternative validation paths. Never extract archive members to disk.
    let archive = PyModule::import(py, "zipfile")?
        .getattr("ZipFile")?
        .call1((&path,))
        .map_err(|e| invalid(format!("invalid portable sounding archive: {e}")))?;
    let result = (|| {
        let members = archive.call_method0("infolist")?;
        if members.len()? > 64 {
            return Err(invalid("portable sounding contains too many arrays"));
        }
        let mut expanded = 0_u64;
        for member in members.try_iter()? {
            let member = member?;
            let bytes: u64 = member.getattr("file_size")?.extract()?;
            let name: String = member.getattr("filename")?.extract()?;
            if bytes > maximum || !name.ends_with(".npy") {
                return Err(invalid(
                    "portable sounding contains an invalid array member",
                ));
            }
            expanded = expanded.saturating_add(bytes);
        }
        if expanded > maximum {
            return Err(invalid("portable sounding expands beyond the safety limit"));
        }
        Ok(())
    })();
    archive.call_method0("close")?;
    result
}

#[pyfunction]
#[pyo3(signature=(data, key, expected_levels=None))]
fn portable_profile_array<'py>(
    py: Python<'py>,
    data: &Bound<'py, PyAny>,
    key: &str,
    expected_levels: Option<usize>,
) -> PyResult<Bound<'py, PyAny>> {
    if !data.contains(key)? {
        return Err(invalid(format!(
            "portable sounding is missing required field {key:?}"
        )));
    }
    let np = PyModule::import(py, "numpy")?;
    let value = np.call_method1("asarray", (data.get_item(key)?,))?;
    let dtype = value.getattr("dtype")?;
    let kind: String = dtype.getattr("kind")?.extract()?;
    if dtype.getattr("hasobject")?.is_truthy()? || !["f", "i", "u"].contains(&kind.as_str()) {
        return Err(invalid(format!(
            "portable sounding field {key:?} must be a numeric array"
        )));
    }
    if value.getattr("ndim")?.extract::<usize>()? != 1 {
        return Err(invalid(format!(
            "portable sounding field {key:?} must be one-dimensional"
        )));
    }
    let count = value.len()?;
    if count < 2 || count as u64 > limit("SHARPMOD_MAX_PROFILE_LEVELS", 20_000) {
        return Err(invalid(format!(
            "portable sounding field {key:?} has an invalid level count"
        )));
    }
    if expected_levels.is_some_and(|expected| expected != count) {
        return Err(invalid(format!(
            "portable sounding field {key:?} has {count} levels; expected {}",
            expected_levels.unwrap()
        )));
    }
    let options = PyDict::new(py);
    options.set_item("dtype", np.getattr("float64")?)?;
    np.call_method("asarray", (value,), Some(&options))
}

#[pyfunction]
#[pyo3(signature=(data, key, default=None))]
fn portable_scalar<'py>(
    py: Python<'py>,
    data: &Bound<'py, PyAny>,
    key: &str,
    default: Option<&Bound<'py, PyAny>>,
) -> PyResult<Bound<'py, PyAny>> {
    if !data.contains(key)? {
        return default.cloned().ok_or_else(|| {
            invalid(format!(
                "portable sounding is missing required field {key:?}"
            ))
        });
    }
    let value = PyModule::import(py, "numpy")?.call_method1("asarray", (data.get_item(key)?,))?;
    if value.getattr("dtype")?.getattr("hasobject")?.is_truthy()? {
        return Err(invalid(format!(
            "portable sounding field {key:?} uses unsafe object data"
        )));
    }
    if value.getattr("size")?.extract::<usize>()? != 1 {
        return Err(invalid(format!(
            "portable sounding field {key:?} must contain one value"
        )));
    }
    value
        .call_method1("reshape", (-1,))?
        .get_item(0)?
        .call_method0("item")
}

#[pyfunction]
fn validate_portable_pair<'py>(
    py: Python<'py>,
    input: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let path = PyModule::import(py, "pathlib")?
        .getattr("Path")?
        .call1((input,))?
        .call_method0("expanduser")?;
    let native: PathBuf = path.extract()?;
    if native
        .extension()
        .and_then(|e| e.to_str())
        .is_none_or(|e| !e.eq_ignore_ascii_case("npz"))
    {
        return Err(invalid(
            "portable sounding cache payload must be an .npz file",
        ));
    }
    validate_portable_archive(py, native.clone())
        .map_err(|e| invalid(format!("invalid portable sounding archive: {e}")))?;
    let sidecar = native.with_extension("json");
    let sidecar_limit = limit("SHARPMOD_MAX_SIDECAR_BYTES", 1024 * 1024);
    if !std::fs::metadata(&sidecar).is_ok_and(|m| m.is_file() && m.len() <= sidecar_limit) {
        return Err(invalid("portable sounding sidecar is missing or oversized"));
    }
    let options = PyDict::new(py);
    options.set_item("allow_pickle", false)?;
    let data = PyModule::import(py, "numpy")?.call_method("load", (&native,), Some(&options))?;
    let checked = (|| {
        let columns = PyDict::new(py);
        let mut count = None;
        for name in ["pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "omeg"] {
            let column = portable_profile_array(py, &data, name, count)?;
            count = Some(column.len()?);
            columns.set_item(name, column)?;
        }
        let values = ["pres", "hght", "tmpc", "dwpc", "wdir", "wspd"]
            .map(|name| -> PyResult<Vec<f64>> {
                Ok(columns
                    .get_item(name)?
                    .unwrap()
                    .extract::<PyReadonlyArray1<'_, f64>>()?
                    .as_array()
                    .iter()
                    .copied()
                    .collect())
            })
            .into_iter()
            .collect::<PyResult<Vec<_>>>()?;
        let issues = py
            .detach(move || {
                crate::quality_control::profile_qc(
                    std::array::from_fn(|i| values[i].as_slice()),
                    Some(-9999.),
                    false,
                )
            })
            .map_err(invalid)?
            .issues;
        let fatal: Vec<_> = issues
            .into_iter()
            .filter(|v| v != "dewpoint_above_temperature")
            .collect();
        if !fatal.is_empty() {
            return Err(invalid(format!(
                "portable sounding failed physical quality control: {}",
                fatal.join(", ")
            )));
        }
        let datetime = PyModule::import(py, "datetime")?.getattr("datetime")?;
        for name in ["valid", "run"] {
            let value = portable_scalar(py, &data, name, None)?
                .str()?
                .to_str()?
                .trim()
                .to_owned();
            if datetime
                .call_method1("strptime", (&value, "%Y-%m-%d %H:%M"))
                .is_err()
                && datetime
                    .call_method1("strptime", (&value, "%Y-%m-%dT%H:%M:%S"))
                    .is_err()
            {
                return Err(invalid(format!(
                    "portable sounding field {name:?} is not a supported date/time"
                )));
            }
        }
        portable_scalar(py, &data, "loc", None)?;
        let float = py.get_type::<pyo3::types::PyFloat>();
        for (name, max) in [("lat", 90.), ("lon", 180.)] {
            if name == "lon" && !data.contains(name)? {
                continue;
            }
            let value: f64 = float
                .call1((portable_scalar(py, &data, name, None)?,))?
                .extract()?;
            if !value.is_finite() || value.abs() > max {
                return Err(invalid(format!(
                    "portable sounding {} is out of range",
                    if name == "lat" {
                        "latitude"
                    } else {
                        "longitude"
                    }
                )));
            }
        }
        let metadata: serde_json::Value = serde_json::from_slice(&std::fs::read(sidecar)?)
            .map_err(|e| invalid(format!("invalid portable sounding pair: {e}")))?;
        let object = metadata
            .as_object()
            .ok_or_else(|| invalid("portable sounding sidecar must contain a JSON object"))?;
        if data.contains("pair_generation")? || object.contains_key("pair_generation") {
            let generation = portable_scalar(py, &data, "pair_generation", None)?;
            let value = generation
                .cast::<PyString>()
                .ok()
                .and_then(|v| v.to_str().ok());
            if value.is_none_or(|v| {
                v.trim().is_empty()
                    || object.get("pair_generation").and_then(|s| s.as_str()) != Some(v)
            }) {
                return Err(invalid(
                    "portable sounding pair generation is missing or mismatched",
                ));
            }
        }
        Ok(())
    })();
    data.call_method0("close")?;
    checked.map_err(|e| invalid(format!("invalid portable sounding pair: {e}")))?;
    Ok(path)
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(validate_portable_archive, module)?)?;
    module.add_function(wrap_pyfunction!(portable_profile_array, module)?)?;
    module.add_function(wrap_pyfunction!(portable_scalar, module)?)?;
    module.add_function(wrap_pyfunction!(validate_portable_pair, module)?)?;
    transaction::register(module)
}

#[cfg(test)]
mod tests;

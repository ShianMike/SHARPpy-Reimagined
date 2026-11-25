use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple};
use ring::rand::{SecureRandom, SystemRandom};
use serde_json::{Map, Value};
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

const TRANSPORT: &str = "nomads-static-ranges";

pub(crate) fn provenance_sidecar_path(combined_path: &Path) -> PathBuf {
    let name = combined_path
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_default();
    let sidecar_name = format!("{name}.provenance.json");
    match combined_path.parent() {
        Some(parent) if !parent.as_os_str().is_empty() => parent.join(sidecar_name),
        _ => PathBuf::from(sidecar_name),
    }
}

fn random_suffix() -> Result<String, std::io::Error> {
    let mut bytes = [0u8; 8];
    SystemRandom::new()
        .fill(&mut bytes)
        .map_err(|_| std::io::Error::other("system RNG unavailable"))?;
    let mut out = String::with_capacity(16);
    for b in bytes {
        out.push_str(&format!("{b:02x}"));
    }
    Ok(out)
}

pub(crate) fn write_provenance_file(
    combined_path: &Path,
    fields: &[String],
    source_url: &str,
    pressure_fields: &[String],
) -> Result<PathBuf, std::io::Error> {
    let target = provenance_sidecar_path(combined_path);
    if let Some(parent) = target.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent)?;
        }
    }

    let mut payload = Map::new();
    payload.insert(
        "fields".to_string(),
        Value::Array(fields.iter().map(|s| Value::String(s.clone())).collect()),
    );
    payload.insert(
        "pressure_fields".to_string(),
        Value::Array(
            pressure_fields
                .iter()
                .map(|s| Value::String(s.clone()))
                .collect(),
        ),
    );
    payload.insert(
        "source_url".to_string(),
        Value::String(source_url.to_string()),
    );
    payload.insert(
        "transport".to_string(),
        Value::String(TRANSPORT.to_string()),
    );
    payload.insert("version".to_string(), Value::Number(1.into()));
    let encoded = serde_json::to_vec(&Value::Object(payload)).map_err(std::io::Error::other)?;

    let target_name = target
        .file_name()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| "provenance.json".to_string());
    let temp_path = loop {
        let suffix = random_suffix()?;
        let candidate = target.with_file_name(format!("{target_name}.{suffix}.tmp"));
        match OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&candidate)
        {
            Ok(mut handle) => {
                if let Err(err) = handle.write_all(&encoded) {
                    drop(handle);
                    let _ = fs::remove_file(&candidate);
                    return Err(err);
                }
                break candidate;
            }
            Err(err) if err.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(err) => return Err(err),
        }
    };

    if let Err(err) = fs::rename(&temp_path, &target) {
        let _ = fs::remove_file(&temp_path);
        return Err(err);
    }
    Ok(target)
}

fn json_item_to_py_str(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Null => "None".to_string(),
        Value::Number(n) => n.to_string(),
        other => other.to_string(),
    }
}

pub(crate) fn read_provenance_data(
    combined_path: &Path,
) -> Option<(Vec<String>, Vec<String>, String)> {
    let bytes = fs::read(provenance_sidecar_path(combined_path)).ok()?;
    let value: Value = serde_json::from_slice(&bytes).ok()?;
    let obj = value.as_object()?;
    let version = obj.get("version")?;
    if version.as_i64() != Some(1) {
        return None;
    }
    let fields_arr = obj.get("fields")?.as_array()?;
    let pressure_arr = obj.get("pressure_fields")?.as_array()?;
    let source_url = obj.get("source_url")?.as_str()?;
    if fields_arr.is_empty() || pressure_arr.is_empty() || source_url.is_empty() {
        return None;
    }
    let fields = fields_arr.iter().map(json_item_to_py_str).collect();
    let pressure_fields = pressure_arr.iter().map(json_item_to_py_str).collect();
    Some((fields, pressure_fields, source_url.to_string()))
}

fn collect_string_list(values: &Bound<'_, PyAny>) -> PyResult<Vec<String>> {
    let mut out = Vec::new();
    for item in values.try_iter()? {
        let item = item?;
        if let Ok(s) = item.extract::<String>() {
            out.push(s);
        } else {
            out.push(item.str()?.to_str()?.to_owned());
        }
    }
    Ok(out)
}

#[pyfunction]
pub(super) fn rrfs_write_provenance(
    combined_path: PathBuf,
    fields: &Bound<'_, PyAny>,
    source_url: &Bound<'_, PyAny>,
    pressure_fields: &Bound<'_, PyAny>,
) -> PyResult<String> {
    let field_list = collect_string_list(fields)?;
    let pressure_list = collect_string_list(pressure_fields)?;
    let url = if let Ok(s) = source_url.extract::<String>() {
        s
    } else {
        source_url.str()?.to_str()?.to_owned()
    };
    let target = write_provenance_file(&combined_path, &field_list, &url, &pressure_list)?;
    Ok(target.to_string_lossy().into_owned())
}

#[pyfunction]
pub(super) fn rrfs_read_provenance<'py>(
    py: Python<'py>,
    combined_path: PathBuf,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let Some((fields, pressure_fields, source_url)) = read_provenance_data(&combined_path) else {
        return Ok(None);
    };
    let dict = PyDict::new(py);
    dict.set_item("fields", PyTuple::new(py, fields)?)?;
    dict.set_item("pressure_fields", PyTuple::new(py, pressure_fields)?)?;
    dict.set_item("source_url", source_url)?;
    Ok(Some(dict))
}

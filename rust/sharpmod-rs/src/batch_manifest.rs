use pyo3::exceptions::{PyOSError, PyRuntimeError, PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};
use ring::digest::{digest, Context, SHA256};
use serde_json::Value;
use std::collections::{BTreeMap, HashSet};
use std::fs::{self, File};
use std::io::{self, Read};
use std::path::{Component, Path, PathBuf};

const MANIFEST_SCHEMA: &str = "sharpmod.batch-manifest";
const MANIFEST_VERSION: i64 = 1;
const CHUNK_BYTES: usize = 1024 * 1024;

fn batch_error(py: Python<'_>, cls_name: &str, message: &str) -> PyErr {
    py.import("sharpmod.analysis.batch_extract")
        .and_then(|module| module.getattr(cls_name))
        .and_then(|cls| cls.call1((message,)))
        .map(PyErr::from_value)
        .unwrap_or_else(|_| PyRuntimeError::new_err(message.to_string()))
}

fn py_repr_str(text: &str) -> String {
    if !text.contains('\'') {
        format!("'{text}'")
    } else if !text.contains('"') {
        format!("\"{text}\"")
    } else {
        format!("'{}'", text.replace('\\', "\\\\").replace('\'', "\\'"))
    }
}

pub(crate) fn safe_identifier_str(value: &str) -> String {
    let safe: String = value
        .chars()
        .map(|ch| {
            if ch.is_alphanumeric() || ch == '-' || ch == '_' {
                ch
            } else {
                '_'
            }
        })
        .collect();
    let trimmed = safe.trim_matches(|ch| ch == '_' || ch == '-');
    if trimmed.is_empty() {
        "request".to_string()
    } else {
        trimmed.to_string()
    }
}

/// Mirror Python `Path.resolve(strict=False)` accurately when paths don't exist yet.
pub(crate) fn resolve_path_non_strict(path: &Path) -> io::Result<PathBuf> {
    let abs = if path.is_absolute() {
        path.to_path_buf()
    } else {
        std::env::current_dir()?.join(path)
    };
    let mut resolved = PathBuf::new();
    let mut existing_broken = false;
    for comp in abs.components() {
        match comp {
            Component::Prefix(p) => resolved.push(p.as_os_str()),
            Component::RootDir => resolved.push(comp.as_os_str()),
            Component::CurDir => {}
            Component::ParentDir => {
                if !matches!(
                    resolved.components().next_back(),
                    Some(Component::RootDir) | Some(Component::Prefix(_))
                ) {
                    resolved.pop();
                }
            }
            Component::Normal(part) => {
                resolved.push(part);
                if !existing_broken {
                    if let Ok(canonical) = fs::canonicalize(&resolved) {
                        resolved = canonical;
                    } else if fs::symlink_metadata(&resolved).is_err() {
                        existing_broken = true;
                    }
                }
            }
        }
    }
    Ok(resolved)
}

pub(crate) fn contained_npz_path(root: &Path, relative: &str) -> Result<PathBuf, String> {
    let candidate = Path::new(relative);
    if candidate.is_absolute() {
        return Err("batch output paths must be relative to output_dir".to_string());
    }
    let root_resolved = resolve_path_non_strict(root)
        .map_err(|err| format!("failed to resolve output_dir: {err}"))?;
    let joined = root_resolved.join(candidate);
    let resolved = resolve_path_non_strict(&joined)
        .map_err(|err| format!("failed to resolve batch path: {err}"))?;
    if !resolved.starts_with(&root_resolved) {
        return Err(format!(
            "batch output path escapes output_dir: {}",
            py_repr_str(relative)
        ));
    }
    let is_npz = resolved
        .extension()
        .and_then(|ext| ext.to_str())
        .is_some_and(|ext| ext.eq_ignore_ascii_case("npz"));
    if !is_npz {
        return Err(format!(
            "batch output path must end in .npz: {}",
            py_repr_str(relative)
        ));
    }
    Ok(resolved)
}

pub(crate) fn file_sha256_hex(path: &Path) -> io::Result<String> {
    let mut file = File::open(path)?;
    let mut ctx = Context::new(&SHA256);
    let mut buf = vec![0u8; CHUNK_BYTES];
    loop {
        let read = file.read(&mut buf)?;
        if read == 0 {
            break;
        }
        ctx.update(&buf[..read]);
    }
    let out = ctx.finish();
    Ok(bytes_to_hex(out.as_ref()))
}

fn bytes_to_hex(bytes: &[u8]) -> String {
    let mut hex = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        hex.push_str(&format!("{b:02x}"));
    }
    hex
}

fn push_ascii_json_string(out: &mut String, text: &str) {
    out.push('"');
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\x08' => out.push_str("\\b"),
            '\x0c' => out.push_str("\\f"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c if (c as u32) <= 0x7f => out.push(c),
            c if (c as u32) <= 0xffff => {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c => {
                let mut pair = [0u16; 2];
                for unit in c.encode_utf16(&mut pair) {
                    out.push_str(&format!("\\u{:04x}", *unit));
                }
            }
        }
    }
    out.push('"');
}

fn write_canonical_ascii_json(out: &mut String, value: &Value) -> Result<(), String> {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(true) => out.push_str("true"),
        Value::Bool(false) => out.push_str("false"),
        Value::Number(num) => out.push_str(&num.to_string()),
        Value::String(text) => push_ascii_json_string(out, text),
        Value::Array(items) => {
            out.push('[');
            for (idx, item) in items.iter().enumerate() {
                if idx > 0 {
                    out.push(',');
                }
                write_canonical_ascii_json(out, item)?;
            }
            out.push(']');
        }
        Value::Object(map) => {
            out.push('{');
            let mut entries: Vec<(&String, &Value)> = map.iter().collect();
            entries.sort_by(|a, b| a.0.cmp(b.0));
            for (idx, (k, v)) in entries.into_iter().enumerate() {
                if idx > 0 {
                    out.push(',');
                }
                push_ascii_json_string(out, k);
                out.push(':');
                write_canonical_ascii_json(out, v)?;
            }
            out.push('}');
        }
    }
    Ok(())
}

pub(crate) fn canonical_fingerprint_from_value(value: &Value) -> Result<String, String> {
    let mut encoded = String::new();
    write_canonical_ascii_json(&mut encoded, value)?;
    let out = digest(&SHA256, encoded.as_bytes());
    Ok(bytes_to_hex(out.as_ref()))
}

fn py_to_json_value(obj: &Bound<'_, PyAny>) -> PyResult<Value> {
    if obj.is_none() {
        return Ok(Value::Null);
    }
    if obj.is_instance_of::<PyBool>() {
        return Ok(Value::Bool(obj.extract::<bool>()?));
    }
    if obj.is_instance_of::<PyInt>() {
        if let Ok(v) = obj.extract::<i64>() {
            return Ok(Value::Number(v.into()));
        }
        if let Ok(v) = obj.extract::<u64>() {
            return Ok(Value::Number(v.into()));
        }
        return Err(PyValueError::new_err("integer out of range"));
    }
    if obj.is_instance_of::<PyFloat>() {
        let v = obj.extract::<f64>()?;
        let Some(num) = serde_json::Number::from_f64(v) else {
            return Err(PyValueError::new_err(
                "Out of range float values are not JSON compliant",
            ));
        };
        return Ok(Value::Number(num));
    }
    if let Ok(s) = obj.cast::<PyString>() {
        return Ok(Value::String(s.to_str()?.to_owned()));
    }
    if let Ok(dict) = obj.cast::<PyDict>() {
        let mut sorted: BTreeMap<String, Value> = BTreeMap::new();
        for (k, v) in dict.iter() {
            let key_str = if let Ok(s) = k.extract::<String>() {
                s
            } else if k.is_instance_of::<PyBool>()
                || k.is_instance_of::<PyInt>()
                || k.is_instance_of::<PyFloat>()
                || k.is_none()
            {
                k.str()?.to_str()?.to_owned()
            } else {
                return Err(PyTypeError::new_err(
                    "keys must be str, int, float, bool or None",
                ));
            };
            sorted.insert(key_str, py_to_json_value(&v)?);
        }
        let mut map = serde_json::Map::new();
        for (k, v) in sorted {
            map.insert(k, v);
        }
        return Ok(Value::Object(map));
    }
    if obj.is_instance_of::<PyList>() || obj.is_instance_of::<PyTuple>() {
        let mut items = Vec::new();
        for elem in obj.try_iter()? {
            items.push(py_to_json_value(&elem?)?);
        }
        return Ok(Value::Array(items));
    }
    if let Ok(v) = obj.extract::<i64>() {
        return Ok(Value::Number(v.into()));
    }
    if let Ok(v) = obj.extract::<f64>() {
        let Some(num) = serde_json::Number::from_f64(v) else {
            return Err(PyValueError::new_err(
                "Out of range float values are not JSON compliant",
            ));
        };
        return Ok(Value::Number(num));
    }
    Err(PyTypeError::new_err("Object is not JSON serializable"))
}

fn json_to_py_val(py: Python<'_>, value: &Value) -> PyResult<Py<PyAny>> {
    match value {
        Value::Null => Ok(py.None()),
        Value::Bool(b) => Ok(b.into_pyobject(py)?.to_owned().into_any().unbind()),
        Value::Number(n) => {
            if let Some(i) = n.as_i64() {
                Ok(i.into_pyobject(py)?.into_any().unbind())
            } else if let Some(u) = n.as_u64() {
                Ok(u.into_pyobject(py)?.into_any().unbind())
            } else if let Some(f) = n.as_f64() {
                Ok(f.into_pyobject(py)?.into_any().unbind())
            } else {
                Ok(py.None())
            }
        }
        Value::String(s) => Ok(s.into_pyobject(py)?.into_any().unbind()),
        Value::Array(items) => {
            let list = PyList::empty(py);
            for item in items {
                list.append(json_to_py_val(py, item)?)?;
            }
            Ok(list.into_any().unbind())
        }
        Value::Object(map) => {
            let dict = PyDict::new(py);
            for (k, v) in map {
                dict.set_item(k, json_to_py_val(py, v)?)?;
            }
            Ok(dict.into_any().unbind())
        }
    }
}

fn json_scalar_str(value: &Value) -> String {
    match value {
        Value::String(s) => s.clone(),
        Value::Bool(true) => "True".to_string(),
        Value::Bool(false) => "False".to_string(),
        Value::Null => "None".to_string(),
        Value::Number(n) => n.to_string(),
        other => other.to_string(),
    }
}

pub(crate) type ParsedManifestItem = (
    String,
    String,
    String,
    String,
    bool,
    Option<Vec<(String, String)>>,
);

pub(crate) struct ParsedManifest {
    pub(crate) manifest_path: PathBuf,
    pub(crate) job_id: String,
    pub(crate) completed: usize,
    pub(crate) failed: usize,
    pub(crate) cancelled: usize,
    pub(crate) skipped: usize,
    pub(crate) items: Vec<ParsedManifestItem>,
    pub(crate) raw_value: Value,
}

pub(crate) fn parse_manifest_file(
    manifest_path: &Path,
    output_dir: Option<&Path>,
    incomplete_as_cancelled: bool,
) -> Result<ParsedManifest, String> {
    let manifest_resolved = resolve_path_non_strict(manifest_path).map_err(|err| {
        format!(
            "could not read batch manifest {}: {err}",
            manifest_path.display()
        )
    })?;
    let output_root = match output_dir {
        Some(dir) => resolve_path_non_strict(dir)
            .map_err(|err| format!("could not resolve output_dir: {err}"))?,
        None => manifest_resolved
            .parent()
            .unwrap_or_else(|| Path::new("."))
            .to_path_buf(),
    };
    let text = fs::read_to_string(&manifest_resolved).map_err(|err| {
        format!(
            "could not read batch manifest {}: {err}",
            manifest_resolved.display()
        )
    })?;
    let manifest_val: Value = serde_json::from_str(&text).map_err(|err| {
        format!(
            "could not read batch manifest {}: {err}",
            manifest_resolved.display()
        )
    })?;
    let Some(obj) = manifest_val.as_object() else {
        return Err("batch manifest must be a JSON object".to_string());
    };
    let schema_ok = obj.get("schema").and_then(|v| v.as_str()) == Some(MANIFEST_SCHEMA);
    let version_ok = obj.get("version").and_then(|v| v.as_i64()) == Some(MANIFEST_VERSION);
    if !schema_ok || !version_ok {
        return Err(format!(
            "manifest {} has an unsupported schema/version",
            manifest_resolved.display()
        ));
    }
    let Some(job_id) = obj
        .get("job_id")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
    else {
        return Err("batch manifest has no valid job id".to_string());
    };
    let Some(records) = obj
        .get("requests")
        .and_then(|v| v.as_array())
        .filter(|a| !a.is_empty())
    else {
        return Err("batch manifest has no request records".to_string());
    };

    let mut seen_ids = HashSet::with_capacity(records.len());
    let mut items = Vec::with_capacity(records.len());
    let mut completed = 0usize;
    let mut failed = 0usize;
    let mut cancelled = 0usize;
    let mut skipped = 0usize;

    for record in records {
        let Some(rec) = record.as_object() else {
            return Err("batch manifest request must be an object".to_string());
        };
        let Some(request_id) = rec
            .get("id")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
        else {
            return Err("batch manifest request has no valid id".to_string());
        };
        if !seen_ids.insert(request_id.to_string()) {
            return Err(format!(
                "batch manifest has duplicate request id {}",
                py_repr_str(request_id)
            ));
        }
        let Some(output) = rec.get("output").and_then(|v| v.as_str()) else {
            return Err(format!(
                "batch manifest request {} has no output",
                py_repr_str(request_id)
            ));
        };
        let output_path = contained_npz_path(&output_root, output)?;
        let sidecar_path = output_path.with_extension("json");

        let raw_status = match rec.get("status") {
            None | Some(Value::Null) | Some(Value::Bool(false)) => String::new(),
            Some(Value::String(s)) => s.clone(),
            Some(other) => json_scalar_str(other),
        };
        let status =
            if matches!(raw_status.as_str(), "pending" | "running") && incomplete_as_cancelled {
                "cancelled".to_string()
            } else if matches!(raw_status.as_str(), "completed" | "failed" | "cancelled") {
                raw_status
            } else {
                return Err(format!(
                    "batch manifest request {} has non-final status {}",
                    py_repr_str(request_id),
                    py_repr_str(&raw_status)
                ));
            };
        match status.as_str() {
            "completed" => completed += 1,
            "failed" => failed += 1,
            "cancelled" => cancelled += 1,
            _ => {}
        }

        let resumed = match rec.get("resumed") {
            Some(Value::Bool(b)) => *b,
            Some(Value::Number(n)) => n.as_f64().is_some_and(|x| x != 0.0),
            Some(Value::String(s)) => !s.is_empty(),
            Some(Value::Array(a)) => !a.is_empty(),
            Some(Value::Object(m)) => !m.is_empty(),
            _ => false,
        };
        if resumed {
            skipped += 1;
        }
        let error_pairs = rec.get("error").and_then(|v| v.as_object()).map(|err_map| {
            err_map
                .iter()
                .map(|(k, v)| (k.clone(), json_scalar_str(v)))
                .collect()
        });
        items.push((
            request_id.to_string(),
            status,
            output_path.to_string_lossy().into_owned(),
            sidecar_path.to_string_lossy().into_owned(),
            resumed,
            error_pairs,
        ));
    }

    Ok(ParsedManifest {
        manifest_path: manifest_resolved,
        job_id: job_id.to_string(),
        completed,
        failed,
        cancelled,
        skipped,
        items,
        raw_value: manifest_val,
    })
}

#[pyfunction]
fn batch_safe_identifier(value: &Bound<'_, PyAny>) -> PyResult<String> {
    let raw = value.str()?.to_str()?.to_owned();
    Ok(safe_identifier_str(&raw))
}

#[pyfunction]
fn batch_contained_path(py: Python<'_>, root: PathBuf, relative: &str) -> PyResult<String> {
    let resolved = contained_npz_path(&root, relative)
        .map_err(|msg| batch_error(py, "BatchSpecError", &msg))?;
    Ok(resolved.to_string_lossy().into_owned())
}

#[pyfunction]
fn batch_file_sha256(path: PathBuf) -> PyResult<String> {
    file_sha256_hex(&path).map_err(|err| PyOSError::new_err(err.to_string()))
}

#[pyfunction]
fn batch_json_fingerprint(payload: &Bound<'_, PyAny>) -> PyResult<String> {
    let val = py_to_json_value(payload)?;
    canonical_fingerprint_from_value(&val).map_err(PyValueError::new_err)
}

#[pyfunction]
#[allow(clippy::type_complexity)]
#[pyo3(signature = (manifest_path, output_dir = None, incomplete_as_cancelled = false))]
fn batch_load_manifest<'py>(
    py: Python<'py>,
    manifest_path: PathBuf,
    output_dir: Option<PathBuf>,
    incomplete_as_cancelled: bool,
) -> PyResult<(
    String,
    String,
    usize,
    usize,
    usize,
    usize,
    Vec<(String, String, String, String, bool, Option<Py<PyDict>>)>,
    Py<PyAny>,
)> {
    let parsed = parse_manifest_file(
        &manifest_path,
        output_dir.as_deref(),
        incomplete_as_cancelled,
    )
    .map_err(|msg| batch_error(py, "BatchManifestError", &msg))?;

    let mut out_items = Vec::with_capacity(parsed.items.len());
    for (id, status, out_p, sidecar_p, resumed, err_pairs) in parsed.items {
        let py_err = match err_pairs {
            Some(pairs) => {
                let d = PyDict::new(py);
                for (k, v) in pairs {
                    d.set_item(k, v)?;
                }
                Some(d.unbind())
            }
            None => None,
        };
        out_items.push((id, status, out_p, sidecar_p, resumed, py_err));
    }
    let py_manifest = json_to_py_val(py, &parsed.raw_value)?;
    Ok((
        parsed.manifest_path.to_string_lossy().into_owned(),
        parsed.job_id,
        parsed.completed,
        parsed.failed,
        parsed.cancelled,
        parsed.skipped,
        out_items,
        py_manifest,
    ))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(batch_safe_identifier, module)?)?;
    module.add_function(wrap_pyfunction!(batch_contained_path, module)?)?;
    module.add_function(wrap_pyfunction!(batch_file_sha256, module)?)?;
    module.add_function(wrap_pyfunction!(batch_json_fingerprint, module)?)?;
    module.add_function(wrap_pyfunction!(batch_load_manifest, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn identifier_and_containment_and_ascii_fingerprint() {
        assert_eq!(safe_identifier_str("  bad/id:1! "), "bad_id_1");
        assert_eq!(safe_identifier_str("---"), "request");

        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let root = std::env::temp_dir().join(format!("sharpmod_batch_test_{nanos}"));
        fs::create_dir_all(&root).expect("mkdir");

        let ok = contained_npz_path(&root, "sub/test.npz").expect("valid path");
        assert!(ok.ends_with("test.npz"));
        assert!(contained_npz_path(&root, "/abs.npz").is_err());
        assert!(contained_npz_path(&root, "../escape.npz").is_err());
        assert!(contained_npz_path(&root, "bad.txt").is_err());

        let v1 = serde_json::json!({"b": "café", "a": 1});
        let v2 = serde_json::json!({"a": 1, "b": "café"});
        assert_eq!(
            canonical_fingerprint_from_value(&v1).unwrap(),
            canonical_fingerprint_from_value(&v2).unwrap()
        );

        let bin = root.join("payload.bin");
        fs::write(&bin, b"abc").expect("write");
        assert_eq!(
            file_sha256_hex(&bin).unwrap(),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        let _ = fs::remove_dir_all(&root);
    }
}

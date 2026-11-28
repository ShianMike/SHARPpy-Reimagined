use pyo3::exceptions::{PyOSError, PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyFloat, PyInt, PyList, PyString, PyTuple};
use ring::digest::{digest, SHA256};
use ring::rand::{SecureRandom, SystemRandom};
use serde_json::{Map, Number, Value};
use sharpmod_weather::bridge_cache::{
    collect_live_cache_entries, compress_gzip_level1, now_epoch_seconds,
    read_gzip_json_object_from_bytes, read_live_cache_file, scan_storage_files,
    write_bytes_atomically,
};
use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;
use std::sync::Mutex;
use time::OffsetDateTime;

const MAX_RESPONSE_BYTES: usize = 8 * 1024 * 1024;
static LIVE_CACHE_LOCK: Mutex<()> = Mutex::new(());

fn py_to_json(obj: &Bound<'_, PyAny>, sort_keys: bool) -> PyResult<Value> {
    if obj.is_none() {
        return Ok(Value::Null);
    }
    if obj.is_instance_of::<PyBool>() {
        return Ok(Value::Bool(obj.extract::<bool>()?));
    }
    if obj.is_instance_of::<PyInt>() {
        if let Ok(v) = obj.extract::<i64>() {
            return Ok(Value::Number(Number::from(v)));
        }
        if let Ok(v) = obj.extract::<u64>() {
            return Ok(Value::Number(Number::from(v)));
        }
        return Err(PyValueError::new_err("integer out of range for JSON"));
    }
    if obj.is_instance_of::<PyFloat>() {
        let v = obj.extract::<f64>()?;
        let Some(num) = Number::from_f64(v) else {
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
        let mut ordered: BTreeMap<String, Value> = BTreeMap::new();
        let mut map = Map::new();
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
            let json_val = py_to_json(&v, sort_keys)?;
            if sort_keys {
                ordered.insert(key_str, json_val);
            } else {
                map.insert(key_str, json_val);
            }
        }
        if sort_keys {
            for (k, v) in ordered {
                map.insert(k, v);
            }
        }
        return Ok(Value::Object(map));
    }
    if obj.is_instance_of::<PyList>() || obj.is_instance_of::<PyTuple>() {
        let mut items = Vec::new();
        for elem in obj.try_iter()? {
            items.push(py_to_json(&elem?, sort_keys)?);
        }
        return Ok(Value::Array(items));
    }
    if let Ok(v) = obj.extract::<i64>() {
        return Ok(Value::Number(Number::from(v)));
    }
    if let Ok(v) = obj.extract::<f64>() {
        let Some(num) = Number::from_f64(v) else {
            return Err(PyValueError::new_err(
                "Out of range float values are not JSON compliant",
            ));
        };
        return Ok(Value::Number(num));
    }
    Err(PyTypeError::new_err("Object is not JSON serializable"))
}

fn json_to_py(py: Python<'_>, value: &Value) -> PyResult<Py<PyAny>> {
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
                list.append(json_to_py(py, item)?)?;
            }
            Ok(list.into_any().unbind())
        }
        Value::Object(map) => {
            let dict = PyDict::new(py);
            for (k, v) in map {
                dict.set_item(k, json_to_py(py, v)?)?;
            }
            Ok(dict.into_any().unbind())
        }
    }
}

fn json_map_to_py<'py>(py: Python<'py>, map: Map<String, Value>) -> PyResult<Bound<'py, PyDict>> {
    let dict = PyDict::new(py);
    for (k, v) in map {
        dict.set_item(k, json_to_py(py, &v)?)?;
    }
    Ok(dict)
}

fn sha256_hex(bytes: &[u8]) -> String {
    let out = digest(&SHA256, bytes);
    let mut hex = String::with_capacity(64);
    for b in out.as_ref() {
        hex.push_str(&format!("{b:02x}"));
    }
    hex
}

fn random_hex8() -> Result<String, PyErr> {
    let mut bytes = [0u8; 8];
    SystemRandom::new()
        .fill(&mut bytes)
        .map_err(|_| PyOSError::new_err("system RNG unavailable"))?;
    let mut hex = String::with_capacity(16);
    for b in bytes {
        hex.push_str(&format!("{b:02x}"));
    }
    Ok(hex)
}

#[pyfunction]
fn live_cache_digest(method: &str, params: &Bound<'_, PyAny>) -> PyResult<String> {
    let params_json = py_to_json(params, true)?;
    let pair = Value::Array(vec![Value::String(method.to_string()), params_json]);
    let canonical = serde_json::to_vec(&pair).map_err(|e| PyValueError::new_err(e.to_string()))?;
    Ok(sha256_hex(&canonical))
}

#[pyfunction]
fn live_cache_ttl(method: &str, params: &Bound<'_, PyAny>) -> f64 {
    if !matches!(
        method,
        "radar.mosaic" | "radar.site" | "satellite.goes" | "alerts.weather"
    ) {
        return 60.0;
    }
    let Ok(raw_time) = params
        .get_item("valid_time")
        .and_then(|v| v.extract::<String>())
    else {
        return 60.0;
    };
    let normalized = raw_time.replace('Z', "+00:00");
    let when = if let Ok(dt) = OffsetDateTime::parse(
        &normalized,
        &time::format_description::well_known::Iso8601::DEFAULT,
    ) {
        dt
    } else if let Ok(date_time) = time::PrimitiveDateTime::parse(
        &normalized,
        &time::format_description::well_known::Iso8601::DEFAULT,
    ) {
        date_time.assume_utc()
    } else {
        return 60.0;
    };

    let now = OffsetDateTime::now_utc();
    let now_minute = now
        .replace_second(0)
        .and_then(|d| d.replace_nanosecond(0))
        .unwrap_or(now);
    let cutoff = now - time::Duration::minutes(15);
    if (method == "alerts.weather" && when < now_minute) || when <= cutoff {
        return f64::INFINITY;
    }
    60.0
}

#[pyfunction]
#[pyo3(signature = (path, ttl_seconds, now_epoch_s = None, max_bytes = MAX_RESPONSE_BYTES))]
fn live_cache_read<'py>(
    py: Python<'py>,
    path: PathBuf,
    ttl_seconds: f64,
    now_epoch_s: Option<f64>,
    max_bytes: usize,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let now = now_epoch_s.unwrap_or_else(now_epoch_seconds);
    let Some(map) = read_live_cache_file(&path, ttl_seconds, now, max_bytes) else {
        return Ok(None);
    };
    Ok(Some(json_map_to_py(py, map)?))
}

#[pyfunction]
#[pyo3(signature = (path, result, max_entries = 128, max_bytes = 128 * 1024 * 1024, max_response_bytes = MAX_RESPONSE_BYTES, unlink_cb = None))]
fn live_cache_write(
    py: Python<'_>,
    path: PathBuf,
    result: &Bound<'_, PyAny>,
    max_entries: usize,
    max_bytes: u64,
    max_response_bytes: usize,
    unlink_cb: Option<&Bound<'_, PyAny>>,
) -> PyResult<()> {
    let value = py_to_json(result, false)?;
    let encoded = serde_json::to_vec(&value).map_err(|e| PyValueError::new_err(e.to_string()))?;
    if encoded.len() > max_response_bytes {
        return Ok(());
    }
    let compressed =
        compress_gzip_level1(&encoded).map_err(|e| PyOSError::new_err(e.to_string()))?;
    if (compressed.len() as u64) > max_bytes {
        return Ok(());
    }

    let _guard = LIVE_CACHE_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let Some(parent) = path.parent() else {
        return Ok(());
    };
    if fs::create_dir_all(parent).is_err() {
        return Ok(());
    }
    let search_root = parent.parent().unwrap_or(parent);
    let Ok(entries) = collect_live_cache_entries(search_root, &path) else {
        return Ok(());
    };
    let mut total: u64 = entries.iter().map(|e| e.size).sum();
    let mut count = entries.len();
    let compressed_len = compressed.len() as u64;

    let pathlib_path = if unlink_cb.is_some() {
        Some(py.import("pathlib")?.getattr("Path")?)
    } else {
        None
    };

    for entry in entries {
        if count < max_entries && total + compressed_len <= max_bytes {
            break;
        }
        let unlinked = match (unlink_cb, pathlib_path.as_ref()) {
            (Some(cb), Some(path_cls)) => {
                let Ok(py_path) = path_cls.call1((entry.path.to_string_lossy().as_ref(),)) else {
                    continue;
                };
                let kwargs = PyDict::new(py);
                let _ = kwargs.set_item("missing_ok", true);
                cb.call((py_path,), Some(&kwargs)).is_ok()
            }
            _ => match fs::remove_file(&entry.path) {
                Ok(()) => true,
                Err(err) if err.kind() == std::io::ErrorKind::NotFound => true,
                Err(_) => false,
            },
        };
        if !unlinked {
            continue;
        }
        count -= 1;
        total = total.saturating_sub(entry.size);
    }

    if count >= max_entries || total + compressed_len > max_bytes {
        return Ok(());
    }

    let suffix = random_hex8()?;
    let _ = write_bytes_atomically(&path, &compressed, &suffix);
    Ok(())
}

#[pyfunction]
fn frame_cache_version(kind: &str, model: &str, product: &str, legacy: usize) -> PyResult<u8> {
    let precipitation_type = matches!(product, "prate-ptype" | "qpf-1h-ptype" | "refd-1km-ptype");
    if kind == "contours" {
        let versions = match product {
            // Do not satisfy a pressure-and-wind request with a saved wind-only frame.
            "tmp-2m-wind" | "dpt-2m-wind" | "thetae-2m" => [8, 7, 7],
            "tmp-2m" | "dpt-2m" if model == "refs" => [8, 7, 6],
            _ if product == "mslp" || precipitation_type => [8, 6, 5],
            _ if product.starts_with("hgt-wind-") => [6, 3, 1],
            _ => [1, 1, 1],
        };
        return versions
            .get(legacy)
            .copied()
            .ok_or_else(|| PyValueError::new_err("invalid contour cache revision"));
    }
    Ok(match kind {
        "grid" if precipitation_type => {
            if legacy == 0 {
                4
            } else {
                3
            }
        }
        "grid" => {
            if legacy == 0 {
                3
            } else {
                2
            }
        }
        _ => 1,
    })
}

#[pyfunction]
#[pyo3(signature = (compressed, max_bytes = 64 * 1024 * 1024))]
fn frame_cache_read_gzip<'py>(
    py: Python<'py>,
    compressed: &[u8],
    max_bytes: usize,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let Some(map) = read_gzip_json_object_from_bytes(compressed, max_bytes) else {
        return Ok(None);
    };
    Ok(Some(json_map_to_py(py, map)?))
}

#[pyfunction]
fn frame_cache_compress_gzip<'py>(
    py: Python<'py>,
    value: &Bound<'_, PyAny>,
) -> PyResult<Bound<'py, PyBytes>> {
    let json_val = py_to_json(value, false)?;
    let encoded =
        serde_json::to_vec(&json_val).map_err(|e| PyValueError::new_err(e.to_string()))?;
    let compressed =
        compress_gzip_level1(&encoded).map_err(|e| PyOSError::new_err(e.to_string()))?;
    Ok(PyBytes::new(py, &compressed))
}

#[pyfunction]
fn storage_scan_files(
    root: Option<PathBuf>,
    patterns: Vec<String>,
    now_epoch_s: Option<f64>,
) -> PyResult<Vec<(String, u64, bool)>> {
    let Some(root_path) = root else {
        return Ok(Vec::new());
    };
    let cutoff = now_epoch_s.unwrap_or_else(now_epoch_seconds) - 3600.0;
    let items = scan_storage_files(&root_path, &patterns, cutoff)
        .map_err(|e| PyOSError::new_err(e.to_string()))?;
    Ok(items
        .into_iter()
        .map(|entry| {
            (
                entry.path.to_string_lossy().into_owned(),
                entry.size,
                entry.ready,
            )
        })
        .collect())
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(live_cache_digest, module)?)?;
    module.add_function(wrap_pyfunction!(live_cache_ttl, module)?)?;
    module.add_function(wrap_pyfunction!(live_cache_read, module)?)?;
    module.add_function(wrap_pyfunction!(live_cache_write, module)?)?;
    module.add_function(wrap_pyfunction!(frame_cache_version, module)?)?;
    module.add_function(wrap_pyfunction!(frame_cache_read_gzip, module)?)?;
    module.add_function(wrap_pyfunction!(frame_cache_compress_gzip, module)?)?;
    module.add_function(wrap_pyfunction!(storage_scan_files, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pressure_frames_refresh_and_fall_back_only_to_saved_pressure_geometry() {
        for product in ["tmp-2m-wind", "dpt-2m-wind", "thetae-2m"] {
            for legacy in 0..3 {
                assert_eq!(
                    frame_cache_version("contours", "hrrr", product, legacy).unwrap(),
                    [8, 7, 7][legacy]
                );
            }
            assert_eq!(frame_cache_version("grid", "hrrr", product, 0).unwrap(), 3);
        }
        for (model, product, expected) in [
            ("refs", "tmp-2m", [8, 7, 6]),
            ("refs-m5", "qpf-1h-ptype", [8, 6, 5]),
            ("gfs", "prate-ptype", [8, 6, 5]),
            ("hrrr", "refd-1km-ptype", [8, 6, 5]),
            ("icon", "mslp", [8, 6, 5]),
            ("gfs", "hgt-wind-500", [6, 3, 1]),
            ("hrrr", "tmp-2m", [1, 1, 1]),
        ] {
            for (legacy, version) in expected.into_iter().enumerate() {
                assert_eq!(
                    frame_cache_version("contours", model, product, legacy).unwrap(),
                    version,
                    "{model} {product}"
                );
            }
        }
    }

    #[test]
    fn canonical_json_digest_is_order_independent() {
        Python::attach(|py| {
            let d1 = PyDict::new(py);
            d1.set_item("b", 2).unwrap();
            d1.set_item("a", 1).unwrap();
            let d2 = PyDict::new(py);
            d2.set_item("a", 1).unwrap();
            d2.set_item("b", 2).unwrap();
            let h1 = live_cache_digest("radar.mosaic", d1.as_any()).unwrap();
            let h2 = live_cache_digest("radar.mosaic", d2.as_any()).unwrap();
            assert_eq!(h1, h2);
        });
    }
}

//! Bounded, self-contained source copies for sounding session recovery.
use base64::{engine::general_purpose::STANDARD, Engine as _};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{fs, io::Read, path::Path};

pub const MAX_PAYLOAD_BYTES: usize = 7 * 1024 * 1024 + 512 * 1024;
const MAX_SOURCE_BYTES: usize = 5 * 1024 * 1024;
const MAX_SIDECAR_BYTES: usize = 256 * 1024;

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Payload {
    version: u8,
    suffix: String,
    source: String,
    sidecar: Option<String>,
    summary: Value,
}

pub struct Source {
    pub suffix: String,
    pub bytes: Vec<u8>,
    pub sidecar: Option<Vec<u8>>,
    pub summary: Value,
}

fn valid_suffix(suffix: &str) -> bool {
    (2..=13).contains(&suffix.len())
        && suffix.starts_with('.')
        && suffix[1..]
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-')
}

fn bounded_read(path: &Path, limit: usize) -> Result<Vec<u8>, String> {
    let metadata = fs::symlink_metadata(path).map_err(|error| error.to_string())?;
    if !metadata.is_file() || metadata.len() > limit as u64 {
        return Err("The sounding source is too large or is not a regular file.".into());
    }
    let mut bytes = Vec::new();
    fs::File::open(path)
        .map_err(|error| error.to_string())?
        .take(limit as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| error.to_string())?;
    if bytes.len() > limit {
        return Err("The sounding source exceeds the recovery limit.".into());
    }
    Ok(bytes)
}

fn validate_summary(summary: &Value) -> Result<(), String> {
    let invalid = || "The recovery source has invalid sounding metadata.".to_string();
    let fields = summary.as_object().ok_or_else(invalid)?;
    if serde_json::to_vec(summary)
        .map_err(|error| error.to_string())?
        .len()
        > 64 * 1024
    {
        return Err(invalid());
    }
    for (name, limit, required) in [
        ("sounding_id", 64, true),
        ("title", 300, true),
        ("location", 300, false),
        ("station_id", 300, false),
        ("station_name", 300, false),
        ("valid_time", 300, false),
        ("run_time", 300, false),
        ("model", 300, false),
        ("provider", 300, false),
        ("provider_name", 300, false),
        ("source_url", 2000, false),
        ("file_name", 300, false),
    ] {
        match fields.get(name) {
            Some(Value::String(text)) if text.len() <= limit && (!required || !text.is_empty()) => {
            }
            Some(Value::Null) if !required => {}
            _ => return Err(invalid()),
        }
    }
    if !matches!(
        summary["kind"].as_str(),
        Some("observed" | "model" | "era5" | "file")
    ) || !matches!(summary["level_count"].as_u64(), Some(2..=20_000))
        || !matches!(summary["time_count"].as_u64(), Some(1..=1_000_000))
    {
        return Err(invalid());
    }
    for (name, limit) in [("latitude", 90.0), ("longitude", 180.0)] {
        if !summary[name].is_null()
            && !summary[name]
                .as_f64()
                .is_some_and(|value| value.is_finite() && value.abs() <= limit)
        {
            return Err(invalid());
        }
    }
    if !summary["forecast_hour"].is_null()
        && !matches!(summary["forecast_hour"].as_u64(), Some(0..=1000))
    {
        return Err(invalid());
    }
    if let Some(times) = fields.get("times") {
        let times = times.as_array().ok_or_else(invalid)?;
        if times.len() as u64 != summary["time_count"].as_u64().unwrap()
            || times
                .iter()
                .any(|value| !value.is_null() && value.as_str().is_none_or(|text| text.len() > 40))
        {
            return Err(invalid());
        }
    }
    Ok(())
}

pub fn pack(path: &Path, summary: Value) -> Result<String, String> {
    validate_summary(&summary)?;
    let suffix = path
        .extension()
        .and_then(|value| value.to_str())
        .ok_or("Missing source file suffix.")?;
    let suffix = format!(".{suffix}");
    if !valid_suffix(&suffix) {
        return Err("The sounding source has an unsupported suffix.".into());
    }
    let source = bounded_read(path, MAX_SOURCE_BYTES)?;
    if source.is_empty() {
        return Err("The sounding source is empty.".into());
    }
    let sidecar_path = path.with_extension("json");
    let sidecar = if sidecar_path != path
        && sidecar_path
            .try_exists()
            .map_err(|error| error.to_string())?
    {
        Some(STANDARD.encode(bounded_read(&sidecar_path, MAX_SIDECAR_BYTES)?))
    } else {
        None
    };
    let payload = serde_json::to_string(&Payload {
        version: 1,
        suffix,
        source: STANDARD.encode(source),
        sidecar,
        summary,
    })
    .map_err(|error| error.to_string())?;
    if payload.len() > MAX_PAYLOAD_BYTES {
        return Err("The recovery source exceeds the recovery limit.".into());
    }
    Ok(payload)
}

pub fn unpack(payload: &str) -> Result<Source, String> {
    if payload.len() > MAX_PAYLOAD_BYTES {
        return Err("The recovery source exceeds the recovery limit.".into());
    }
    let saved: Payload = serde_json::from_str(payload)
        .map_err(|_| "The recovery source is damaged or unsupported.")?;
    if saved.version != 1 || !valid_suffix(&saved.suffix) {
        return Err("The recovery source has an unsupported version or suffix.".into());
    }
    validate_summary(&saved.summary)?;
    let bytes = STANDARD
        .decode(saved.source)
        .map_err(|_| "The saved sounding source is damaged.")?;
    if bytes.is_empty() || bytes.len() > MAX_SOURCE_BYTES {
        return Err("The saved sounding source has an invalid size.".into());
    }
    let sidecar = saved
        .sidecar
        .map(|text| {
            STANDARD
                .decode(text)
                .map_err(|_| "The saved sounding metadata is damaged.")
        })
        .transpose()?;
    if sidecar
        .as_ref()
        .is_some_and(|bytes| bytes.len() > MAX_SIDECAR_BYTES)
    {
        return Err("The saved sounding metadata is too large.".into());
    }
    Ok(Source {
        suffix: saved.suffix,
        bytes,
        sidecar,
        summary: saved.summary,
    })
}

#[cfg(test)]
mod tests;

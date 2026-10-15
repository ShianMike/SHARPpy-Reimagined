//! Metadata for local raw WRF sounding extraction.
use crate::dto::checks::{check_lat_lon, check_text};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct WrfFile {
    pub file_path: String,
    pub file_name: String,
    pub times: Vec<String>,
    pub latitude: f64,
    pub longitude: f64,
}

impl WrfFile {
    pub fn validate(&self) -> Result<(), String> {
        check_text("filePath", &self.file_path, 1, 4096)?;
        check_text("fileName", &self.file_name, 1, 255)?;
        if self.times.is_empty() {
            return Err("WRF file must have forecast times".into());
        }
        for time in &self.times {
            check_text("time", time, 10, 40)?;
        }
        check_lat_lon(Some(self.latitude), Some(self.longitude))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::dto::soundings::OpenSoundingRequest;

    #[test]
    fn raw_wrf_requests_and_metadata_validate_and_translate() {
        let file_path = std::env::temp_dir().join("wrfout_d01.nc");
        let request: OpenSoundingRequest = serde_json::from_value(serde_json::json!({
            "kind": "wrf", "filePath": file_path, "time": "2014-06-16T20:00:00Z",
            "latitude": 41, "longitude": -96,
        }))
        .unwrap();
        request.validate().unwrap();
        let (method, params) = request.bridge_call();
        assert_eq!(method, "soundings.open_wrf");
        assert_eq!(params["file_path"], file_path.to_string_lossy().as_ref());
        let mut result: WrfFile = serde_json::from_value(serde_json::json!({
            "file_path": file_path, "file_name": "wrfout_d01.nc",
            "times": ["2014-06-16T20:00:00Z"], "latitude": 41, "longitude": -96,
        }))
        .unwrap();
        result.validate().unwrap();
        assert_eq!(
            serde_json::to_value(&result).unwrap()["filePath"],
            params["file_path"]
        );
        result.times.clear();
        assert!(result.validate().is_err());
        let bad: OpenSoundingRequest = serde_json::from_value(serde_json::json!({
            "kind": "wrf", "filePath": "relative.nc", "time": "2014-06-16T20:00:00Z",
            "latitude": 41, "longitude": -96,
        }))
        .unwrap();
        assert!(bad.validate().is_err());
    }
}

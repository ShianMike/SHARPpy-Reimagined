//! `open_sounding` and `render_sounding` DTOs.
//!
//! The bridge sends summaries in snake_case and the webview receives them in
//! camelCase, so each type deserializes snake_case and serializes camelCase.
//! Rust is the trust boundary in front of the webview: every payload is
//! checked with `validate` before it is returned.

use base64::Engine as _;
use serde::{Deserialize, Serialize};

use crate::dto::checks::{check_lat_lon, check_opt_text, check_text};

pub const MAX_TEXT: usize = 300;
/// Five MiB of data, leaving room for UTF-8 names and the 8 MiB wire envelope.
pub const MAX_UPLOAD_BASE64: usize = (5 * 1024 * 1024_usize).div_ceil(3) * 4;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ObservedProvider {
    Auto,
    Uwyo,
    Iem,
    Igra,
}

impl ObservedProvider {
    pub fn as_str(self) -> &'static str {
        match self {
            ObservedProvider::Auto => "auto",
            ObservedProvider::Uwyo => "uwyo",
            ObservedProvider::Iem => "iem",
            ObservedProvider::Igra => "igra",
        }
    }
}

/// The `request` argument of `open_sounding`.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    tag = "kind",
    rename_all = "camelCase",
    rename_all_fields = "camelCase",
    deny_unknown_fields
)]
pub enum OpenSoundingRequest {
    Observed {
        station_id: String,
        /// RFC 3339; the observation time.
        time: String,
        #[serde(default)]
        provider: Option<ObservedProvider>,
    },
    Model {
        model: String,
        #[serde(default)]
        run_time: Option<String>,
        forecast_hour: u32,
        latitude: f64,
        longitude: f64,
        #[serde(default)]
        location: Option<String>,
    },
    Era5 {
        time: String,
        latitude: f64,
        longitude: f64,
        #[serde(default)]
        location: Option<String>,
    },
    File {
        file_name: String,
        content_base64: String,
    },
    Wrf {
        file_path: String,
        time: String,
        latitude: f64,
        longitude: f64,
    },
}

impl OpenSoundingRequest {
    pub fn validate(&self) -> Result<(), String> {
        match self {
            OpenSoundingRequest::Observed {
                station_id, time, ..
            } => {
                check_text("stationId", station_id.trim(), 1, 16)?;
                check_text("time", time, 10, 40)
            }
            OpenSoundingRequest::Model {
                model,
                run_time,
                forecast_hour,
                latitude,
                longitude,
                location,
            } => {
                check_text("model", model, 1, 40)?;
                check_opt_text("runTime", run_time.as_ref(), 40)?;
                check_opt_text("location", location.as_ref(), 64)?;
                if *forecast_hour > 1000 {
                    return Err("forecastHour must be at most 1000".into());
                }
                if !latitude.is_finite() || !longitude.is_finite() {
                    return Err("latitude and longitude must be finite".into());
                }
                check_lat_lon(Some(*latitude), Some(*longitude))
            }
            OpenSoundingRequest::Era5 {
                time,
                latitude,
                longitude,
                location,
            } => {
                check_text("time", time, 10, 40)?;
                check_opt_text("location", location.as_ref(), 64)?;
                check_lat_lon(Some(*latitude), Some(*longitude))
            }
            OpenSoundingRequest::File {
                file_name,
                content_base64,
            } => {
                check_text("fileName", file_name, 1, 255)?;
                if content_base64.is_empty()
                    || content_base64.len() > MAX_UPLOAD_BASE64
                    || base64::engine::general_purpose::STANDARD
                        .decode(content_base64)
                        .map_or(true, |data| data.len() > 5 * 1024 * 1024)
                {
                    return Err("the file must be 1 byte to 5 MiB".into());
                }
                Ok(())
            }
            OpenSoundingRequest::Wrf {
                file_path,
                time,
                latitude,
                longitude,
            } => {
                check_text("filePath", file_path, 1, 4096)?;
                if !std::path::Path::new(file_path).is_absolute() {
                    return Err("filePath must be absolute".into());
                }
                check_text("time", time, 10, 40)?;
                check_lat_lon(Some(*latitude), Some(*longitude))
            }
        }
    }

    /// The bridge method and its snake_case parameters.
    pub fn bridge_call(&self) -> (&'static str, serde_json::Value) {
        use serde_json::json;
        match self {
            OpenSoundingRequest::Observed {
                station_id,
                time,
                provider,
            } => (
                "soundings.open_observed",
                json!({
                    "station_id": station_id.trim(),
                    "time": time,
                    "provider": provider.unwrap_or(ObservedProvider::Auto).as_str(),
                }),
            ),
            OpenSoundingRequest::Model {
                model,
                run_time,
                forecast_hour,
                latitude,
                longitude,
                location,
            } => (
                "soundings.open_model",
                json!({
                    "model": model,
                    "run_time": run_time,
                    "forecast_hour": forecast_hour,
                    "latitude": latitude,
                    "longitude": longitude,
                    "location": location,
                }),
            ),
            OpenSoundingRequest::Era5 {
                time,
                latitude,
                longitude,
                location,
            } => (
                "soundings.open_era5",
                json!({ "time": time, "latitude": latitude, "longitude": longitude, "location": location }),
            ),
            OpenSoundingRequest::File {
                file_name,
                content_base64,
            } => (
                "soundings.open_file",
                json!({ "file_name": file_name, "content_base64": content_base64 }),
            ),
            OpenSoundingRequest::Wrf {
                file_path,
                time,
                latitude,
                longitude,
            } => (
                "soundings.open_wrf",
                json!({ "file_path": file_path, "time": time, "latitude": latitude, "longitude": longitude }),
            ),
        }
    }

    /// What the request is for, in the words of an error message.
    pub fn resource(&self) -> String {
        match self {
            OpenSoundingRequest::Observed {
                station_id, time, ..
            } => format!("observed sounding {} at {time}", station_id.trim()),
            OpenSoundingRequest::Model {
                model,
                forecast_hour,
                ..
            } => format!("{model} model sounding, forecast hour {forecast_hour}"),
            OpenSoundingRequest::Era5 { time, .. } => format!("ERA5 sounding at {time}"),
            OpenSoundingRequest::File { file_name, .. } => format!("sounding file {file_name}"),
            OpenSoundingRequest::Wrf { file_path, .. } => format!("WRF file {file_path}"),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SoundingKind {
    Observed,
    Model,
    Era5,
    File,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct SoundingSummary {
    pub sounding_id: String,
    pub kind: SoundingKind,
    pub title: String,
    pub location: Option<String>,
    pub station_id: Option<String>,
    pub station_name: Option<String>,
    pub latitude: Option<f64>,
    pub longitude: Option<f64>,
    pub valid_time: Option<String>,
    pub run_time: Option<String>,
    pub forecast_hour: Option<u32>,
    pub model: Option<String>,
    pub provider: Option<String>,
    pub provider_name: Option<String>,
    pub source_url: Option<String>,
    pub file_name: Option<String>,
    pub level_count: u32,
    pub time_count: u32,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub times: Vec<Option<String>>,
}

impl SoundingSummary {
    pub fn validate(&self) -> Result<(), String> {
        check_text("soundingId", &self.sounding_id, 1, 64)?;
        check_text("title", &self.title, 1, MAX_TEXT)?;
        for (field, value) in [
            ("location", &self.location),
            ("stationId", &self.station_id),
            ("stationName", &self.station_name),
            ("validTime", &self.valid_time),
            ("runTime", &self.run_time),
            ("model", &self.model),
            ("provider", &self.provider),
            ("providerName", &self.provider_name),
            ("fileName", &self.file_name),
        ] {
            check_opt_text(field, value.as_ref(), MAX_TEXT)?;
        }
        check_opt_text("sourceUrl", self.source_url.as_ref(), 2000)?;
        if !self.times.is_empty() && self.times.len() != self.time_count as usize {
            return Err("times must match timeCount".into());
        }
        for time in &self.times {
            check_opt_text("times", time.as_ref(), 40)?;
            if let Some(value) = time {
                time::OffsetDateTime::parse(value, &time::format_description::well_known::Rfc3339)
                    .map_err(|_| "times must contain RFC 3339 timestamps or null")?;
            }
        }
        check_lat_lon(self.latitude, self.longitude)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "UPPERCASE")]
pub enum RenderParcel {
    Sfc,
    Ml,
    Fcst,
    #[default]
    Mu,
    Eff,
}

impl RenderParcel {
    pub fn as_str(self) -> &'static str {
        match self {
            RenderParcel::Sfc => "SFC",
            RenderParcel::Ml => "ML",
            RenderParcel::Fcst => "FCST",
            RenderParcel::Mu => "MU",
            RenderParcel::Eff => "EFF",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum ImageMode {
    #[default]
    Hd,
    Uhd,
}

impl ImageMode {
    pub fn as_str(self) -> &'static str {
        match self {
            ImageMode::Hd => "hd",
            ImageMode::Uhd => "uhd",
        }
    }
}

/// The bridge's `soundings.render` result; the webview receives only the PNG bytes.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RenderResult {
    pub sounding_id: String,
    pub parcel: RenderParcel,
    pub image_mode: ImageMode,
    pub width: u32,
    pub height: u32,
    pub byte_length: u64,
    pub png_base64: String,
}

#[cfg(test)]
mod tests;

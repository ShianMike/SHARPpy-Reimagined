//! GOES satellite frame operation, served by the Python bridge
//! (`sharpmod/bridge/satellite.py`).
//!
//! | Operation | Bridge method | Deadline |
//! |---|---|---|
//! | `get_satellite_frame` | `satellite.goes` | 120 s |
//!
//! The frame reaches the webview as one binary SHRF frame holding the rendered
//! PNG, the same shape as the radar frames (`backend/map_ops.rs`).

use std::time::Duration;

use serde_json::json;

use crate::backend::map_ops::{context, shrf_frame, time_ok};
use crate::backend::router::Router;
use crate::backend::sounding_ops::{invalid, rejected};
use crate::dto::satellite::SatelliteResult;
use crate::error::BackendError;

pub const OP_SATELLITE: &str = "get_satellite_frame";

const SATELLITE_TIMEOUT: Duration = Duration::from_secs(120);
const PNG_SIGNATURE: &[u8] = b"\x89PNG\r\n\x1a\n";

fn channel_ok(channel: &str) -> bool {
    matches!(channel, "visible" | "infrared")
}

impl Router {
    /// The GOES frame nearest `valid_time`, over the map's fixed extent.
    pub async fn satellite_frame(
        &self,
        valid_time: &str,
        channel: Option<&str>,
    ) -> Result<Vec<u8>, BackendError> {
        let context = context(OP_SATELLITE, &format!("GOES {valid_time}"));
        if !time_ok(valid_time) {
            return Err(invalid(&context, "validTime must be an RFC 3339 time"));
        }
        let params = match channel {
            Some(key) if channel_ok(key) => json!({ "valid_time": valid_time, "channel": key }),
            Some(_) => {
                return Err(invalid(
                    &context,
                    "channel must be \"visible\" or \"infrared\"",
                ));
            }
            None => json!({ "valid_time": valid_time }),
        };
        let frame: SatelliteResult = self
            .call(&context, "satellite.goes", params, SATELLITE_TIMEOUT)
            .await?;
        frame
            .validate()
            .map_err(|reason| rejected(&context, "satellite.goes", reason))?;
        let (bytes, body_offset) = shrf_frame(&frame.header(), &frame.png_base64)
            .map_err(|reason| rejected(&context, "satellite.goes", reason))?;
        let body = &bytes[body_offset..];
        if !body.starts_with(PNG_SIGNATURE)
            || u64::try_from(body.len()).ok() != Some(frame.byte_length)
        {
            return Err(rejected(
                &context,
                "satellite.goes",
                "the frame is not the PNG that was described".to_owned(),
            ));
        }
        Ok(bytes)
    }
}

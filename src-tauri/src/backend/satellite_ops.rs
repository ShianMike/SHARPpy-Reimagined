//! GOES satellite frame operation, served natively by `sharpmod_weather::satellite`.
//!
//! | Operation | Backend | Deadline |
//! |---|---|---|
//! | `get_satellite_frame` | `sharpmod_weather::satellite` | 30 s |
//!
//! The frame reaches the webview as one binary SHRF frame holding the rendered
//! PNG, the same shape as the radar frames (`backend/map_ops.rs`).

use time::{format_description::well_known::Rfc3339, OffsetDateTime};

use crate::backend::map_ops::{context, time_ok};
use crate::backend::router::Router;
use crate::backend::sounding_ops::{invalid, rejected};
use crate::dto::models::Bounds as DtoBounds;
use crate::dto::satellite::SatelliteFrameHeader;
use crate::error::{BackendError, ErrorCode};

pub const OP_SATELLITE: &str = "get_satellite_frame";

const MAX_IMAGE_BYTES: usize = 6 * 1024 * 1024 - 4096;
const PNG_SIGNATURE: &[u8] = b"\x89PNG\r\n\x1a\n";

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
        let when = OffsetDateTime::parse(valid_time, &Rfc3339)
            .map_err(|_| invalid(&context, "validTime must be an RFC 3339 time"))?;
        let channel = match channel {
            Some(key @ ("visible" | "infrared")) => key,
            Some(_) => {
                return Err(invalid(
                    &context,
                    "channel must be \"visible\" or \"infrared\"",
                ));
            }
            None => "infrared",
        };
        let frame = sharpmod_weather::satellite::client()
            .fetch(
                when,
                sharpmod_weather::satellite::FRAME_BOUNDS,
                channel,
                OffsetDateTime::now_utc(),
            )
            .await
            .map_err(|error| BackendError {
                code: ErrorCode::OverlayFetchFailed,
                message: format!("The GOES satellite frame could not be loaded: {error}."),
                retryable: true,
                context: Some(Box::new(context.clone())),
                details: None,
            })?;
        if frame.png_bytes.len() > MAX_IMAGE_BYTES || !frame.png_bytes.starts_with(PNG_SIGNATURE) {
            return Err(rejected(
                &context,
                "satellite.goes",
                "the GOES service did not send a usable PNG frame".to_owned(),
            ));
        }
        let header = SatelliteFrameHeader {
            kind: "satellite",
            product: frame.product,
            satellite: frame.satellite,
            label: frame.label,
            units: String::new(),
            bounds: DtoBounds {
                west: frame.bounds.west,
                east: frame.bounds.east,
                south: frame.bounds.south,
                north: frame.bounds.north,
            },
            requested_time: frame.requested_time,
            retrieved_at: Some(frame.retrieved_at),
            valid_time: frame.valid_time,
            update_interval_s: frame.update_interval_s,
            coverage_fraction: frame.coverage_fraction,
            native_resolution_km: frame.native_resolution_km,
            attribution: frame.attribution,
            source_url: frame.source_url,
        };
        let json = serde_json::to_vec(&header)
            .map_err(|reason| rejected(&context, "satellite.goes", reason.to_string()))?;
        let mut bytes = Vec::with_capacity(12 + json.len() + frame.png_bytes.len());
        bytes.extend_from_slice(b"SHRF\x01\x00\x00\x00");
        bytes.extend_from_slice(&(json.len() as u32).to_le_bytes());
        bytes.extend_from_slice(&json);
        bytes.extend_from_slice(&frame.png_bytes);
        Ok(bytes)
    }
}

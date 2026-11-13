//! Radar volume/directory checks shared by both tilt pickers and preloading.

use crate::backend::map_ops::{context, key_ok, shrf_frame, time_ok};
use crate::backend::router::Router;
use crate::backend::sounding_ops::{invalid, rejected};
use crate::dto::map::{RadarFrameHeader, RadarSiteCatalogPublic, RadarSiteResult};
use crate::dto::models::Bounds as DtoBounds;
use crate::dto::radar::RadarTilts;
use crate::dto::weather_alerts::alert_time;
use crate::error::{BackendError, ErrorCode, ErrorContext};
use serde_json::json;
use std::sync::Arc;
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

pub const OP_RADAR: &str = "get_radar_frame";
pub const OP_RADAR_SITE: &str = "get_radar_site_frame";
const MAX_IMAGE_BYTES: usize = 6 * 1024 * 1024 - 4096;
const PNG_SIGNATURE: &[u8] = b"\x89PNG\r\n\x1a\n";

impl Router {
    /// The bundled and currently available OPERA radar sites.
    pub async fn radar_sites(&self) -> Result<Arc<RadarSiteCatalogPublic>, BackendError> {
        Ok(Arc::new(RadarSiteCatalogPublic {
            sites: sharpmod_weather::radar_provider::sites()
                .await
                .iter()
                .map(crate::dto::radar::RadarSite::from)
                .collect(),
        }))
    }

    pub async fn radar_tilts(
        &self,
        site: &str,
        product: &str,
        valid_time: Option<&str>,
    ) -> Result<RadarTilts, BackendError> {
        let context = ErrorContext::new(
            "get_radar_tilts",
            Some("noaa-radar"),
            Some(&format!("radar tilts for {site}")),
        );
        if site.len() != 4
            || !site.bytes().all(|c| c.is_ascii_alphanumeric())
            || !matches!(
                product,
                "reflectivity" | "velocity" | "correlation-coefficient"
            )
        {
            return Err(invalid(
                &context,
                "a radar site, tiltable product and valid time are required",
            ));
        }
        let time = valid_time
            .map(alert_time)
            .transpose()
            .map_err(|_| invalid(&context, "a valid radar time is required"))?;
        if sharpmod_weather::radar_sites::sites()
            .iter()
            .any(|candidate| {
                candidate.id.eq_ignore_ascii_case(site)
                    && (matches!(candidate.country.as_deref(), Some("AU" | "DE"))
                        || candidate.provider.as_deref() == Some("MRRL"))
            })
        {
            return Err(invalid(
                &context,
                "This radar does not offer selectable NEXRAD elevations.",
            ));
        }
        let site = sharpmod_weather::radar_sites::sites()
            .iter()
            .find(|candidate| candidate.id.eq_ignore_ascii_case(site))
            .ok_or_else(|| invalid(&context, "unknown radar site"))?;
        if product == "velocity" {
            return self
                .velocity_inventory
                .availability(site, time, time::OffsetDateTime::now_utc())
                .await
                .map(RadarTilts::from)
                .map_err(|message| BackendError {
                    code: ErrorCode::OverlayFetchFailed,
                    message: format!("Radar tilts could not be checked: {message}"),
                    retryable: true,
                    context: Some(Box::new(context)),
                    details: None,
                });
        }
        sharpmod_weather::nexrad_level2::client()
            .availability(site, product, time, time::OffsetDateTime::now_utc(), None)
            .await
            .map(RadarTilts::from)
            .map_err(|error| BackendError {
                code: ErrorCode::OverlayFetchFailed,
                message: format!("Radar tilts could not be checked: {error}"),
                retryable: !matches!(error, sharpmod_weather::radar_provider::Error::Invalid(_)),
                context: Some(Box::new(context)),
                details: None,
            })
    }

    /// A latest or genuinely time-addressed radar frame holding the service's PNG.
    pub async fn radar_frame(
        &self,
        product: Option<&str>,
        valid_time: Option<&str>,
    ) -> Result<Vec<u8>, BackendError> {
        let context = context(OP_RADAR, "MRMS radar mosaic");
        if let Some(key) = product {
            if !key_ok(key) {
                return Err(invalid(&context, "product must be a key"));
            }
        }
        let when = match valid_time {
            Some(raw) => {
                if !time_ok(raw) {
                    return Err(invalid(&context, "validTime must be an RFC 3339 time"));
                }
                Some(
                    OffsetDateTime::parse(raw, &Rfc3339)
                        .map_err(|_| invalid(&context, "validTime must be an RFC 3339 time"))?,
                )
            }
            None => None,
        };
        let product_key = match product {
            Some("composite-reflectivity" | "base-reflectivity" | "echo-tops") => product,
            Some(_) | None => Some("composite-reflectivity"),
        };
        let frame = sharpmod_weather::radar_mosaic::client()
            .fetch(product_key, when, OffsetDateTime::now_utc())
            .await
            .map_err(|error| {
                let retryable = !matches!(
                    error,
                    sharpmod_weather::radar_mosaic::MosaicError::Invalid(_)
                );
                let details = matches!(
                    error,
                    sharpmod_weather::radar_mosaic::MosaicError::ScanMissing(_)
                )
                .then(|| {
                    json!({ "reason": "scanMissing" })
                        .as_object()
                        .expect("object")
                        .clone()
                });
                BackendError {
                    code: ErrorCode::OverlayFetchFailed,
                    message: format!("The radar mosaic could not be loaded: {error}."),
                    retryable,
                    context: Some(Box::new(context.clone())),
                    details,
                }
            })?;
        if when.is_some() && frame.valid_time.is_none() {
            return Err(rejected(
                &context,
                "radar.mosaic",
                "a requested scan has no actual valid_time".to_owned(),
            ));
        }
        if frame.png_bytes.len() > MAX_IMAGE_BYTES || !frame.png_bytes.starts_with(PNG_SIGNATURE) {
            return Err(rejected(
                &context,
                "radar.mosaic",
                "the radar service did not send a usable PNG within the 6 MiB transfer limit"
                    .to_owned(),
            ));
        }
        let header = RadarFrameHeader {
            legend_png_base64: frame
                .legend_png_bytes
                .as_deref()
                .map(sharpmod_weather::base64_encode),
            kind: "radar",
            product: frame.product.clone(),
            label: frame.label.clone(),
            units: frame.units.clone(),
            bounds: DtoBounds {
                west: frame.bounds.west,
                east: frame.bounds.east,
                south: frame.bounds.south,
                north: frame.bounds.north,
            },
            retrieved_at: Some(frame.retrieved_at.clone()),
            valid_time: frame.valid_time.clone(),
            update_interval_s: frame.update_interval_s,
            attribution: frame.attribution.clone(),
            source_url: frame.source_url.clone(),
        };
        let json = serde_json::to_vec(&header)
            .map_err(|reason| rejected(&context, "radar.mosaic", reason.to_string()))?;
        let mut bytes = Vec::with_capacity(12 + json.len() + frame.png_bytes.len());
        bytes.extend_from_slice(b"SHRF\x01\x00\x00\x00");
        bytes.extend_from_slice(&(json.len() as u32).to_le_bytes());
        bytes.extend_from_slice(&json);
        bytes.extend_from_slice(&frame.png_bytes);
        Ok(bytes)
    }

    /// One antenna's own frame, pinned by site id or served for a map point.
    /// `site` is `Some(id)` to pin, else `latitude`/`longitude` serve the nearest.
    pub async fn radar_site_frame(
        &self,
        product: Option<&str>,
        site: Option<&str>,
        latitude: Option<f64>,
        longitude: Option<f64>,
        valid_time: Option<&str>,
        tilt: Option<&str>,
    ) -> Result<Vec<u8>, BackendError> {
        let id = site.or(product).unwrap_or("default");
        let context = context(OP_RADAR_SITE, &format!("single-site radar {id}"));
        let mut params = match (site, latitude, longitude) {
            (Some(id), _, _) => {
                if !key_ok(id) {
                    return Err(invalid(&context, "site must be a radar identifier"));
                }
                match product {
                    Some(key) if key_ok(key) => json!({ "product": key, "site_id": id }),
                    Some(_) => return Err(invalid(&context, "product must be a key")),
                    None => json!({ "site_id": id }),
                }
            }
            (None, Some(lat), Some(lon)) => {
                if !lat.is_finite()
                    || !(-90.0..=90.0).contains(&lat)
                    || !lon.is_finite()
                    || !(-180.0..=180.0).contains(&lon)
                {
                    return Err(invalid(&context, "latitude and longitude must be in range"));
                }
                match product {
                    Some(key) if key_ok(key) => {
                        json!({ "product": key, "latitude": lat, "longitude": lon })
                    }
                    Some(_) => return Err(invalid(&context, "product must be a key")),
                    None => json!({ "latitude": lat, "longitude": lon }),
                }
            }
            _ => {
                return Err(invalid(
                    &context,
                    "site, or latitude and longitude, must be given",
                ));
            }
        };
        if let Some(time) = valid_time {
            if !time_ok(time) {
                return Err(invalid(&context, "validTime must be an RFC 3339 time"));
            }
            params["valid_time"] = json!(time);
        }
        if let Some(cut) = tilt {
            if !matches!(cut, "x" | "y" | "z" | "0" | "a" | "1" | "b" | "2" | "3") {
                return Err(invalid(&context, "tilt must be a supported NEXRAD cut"));
            }
            params["tilt"] = json!(cut);
        }
        let native_request = serde_json::from_value(params.clone())
            .map_err(|reason| invalid(&context, &format!("Invalid radar request: {reason}")))?;
        match sharpmod_weather::radar_provider::fetch(
            native_request,
            time::OffsetDateTime::now_utc(),
        )
        .await
        {
            Ok(Some(frame)) => {
                if valid_time.is_some() && frame.valid_time.is_none() {
                    return Err(rejected(
                        &context,
                        "radar.site",
                        "a requested scan has no actual valid_time".to_owned(),
                    ));
                }
                let frame: RadarSiteResult = serde_json::from_value(
                    serde_json::to_value(frame.as_ref())
                        .map_err(|reason| rejected(&context, "radar.site", reason.to_string()))?,
                )
                .map_err(|reason| rejected(&context, "radar.site", reason.to_string()))?;
                shrf_frame(&frame.header(), &frame.png_base64)
                    .map(|(bytes, _)| bytes)
                    .map_err(|reason| rejected(&context, "radar.site", reason))
            }
            Ok(None) => {
                if let Some(id) = site {
                    Err(invalid(&context, &format!("unknown radar site {id:?}")))
                } else {
                    Err(BackendError {
                        code: ErrorCode::OverlayFetchFailed,
                        message: "No radar site is within range of this view.".into(),
                        retryable: false,
                        context: Some(Box::new(context)),
                        details: None,
                    })
                }
            }
            Err(error) => Err(BackendError {
                code: if matches!(error, sharpmod_weather::radar_provider::Error::Invalid(_)) {
                    ErrorCode::InvalidArgument
                } else {
                    ErrorCode::OverlayFetchFailed
                },
                message: error.to_string(),
                retryable: !matches!(error, sharpmod_weather::radar_provider::Error::Invalid(_)),
                context: Some(Box::new(context)),
                details: matches!(error, sharpmod_weather::radar_provider::Error::ScanMissing)
                    .then(|| {
                        json!({"reason": "scanMissing"})
                            .as_object()
                            .expect("object")
                            .clone()
                    }),
            }),
        }
    }
}

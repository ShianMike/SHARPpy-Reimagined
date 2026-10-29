//! Radar volume/directory checks shared by both tilt pickers and preloading.

use crate::backend::router::Router;
use crate::backend::sounding_ops::{invalid, rejected};
use crate::dto::map::RadarSiteCatalogPublic;
use crate::dto::radar::RadarTilts;
use crate::dto::weather_alerts::alert_time;
use crate::error::{BackendError, ErrorCode, ErrorContext, PYTHON_PROVIDER};
use serde_json::json;
use std::sync::{Arc, PoisonError};
use std::time::Duration;

impl Router {
    /// The bundled US and Australian catalogue backing the single-site picker.
    pub async fn radar_sites(&self) -> Result<Arc<RadarSiteCatalogPublic>, BackendError> {
        if let Some(sites) = self
            .radar_sites
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clone()
        {
            return Ok(sites);
        }
        let catalog = Arc::new(RadarSiteCatalogPublic {
            sites: sharpmod_weather::radar_sites::sites()
                .iter()
                .map(crate::dto::radar::RadarSite::from)
                .collect(),
        });
        *self
            .radar_sites
            .lock()
            .unwrap_or_else(PoisonError::into_inner) = Some(Arc::clone(&catalog));
        Ok(catalog)
    }

    pub async fn radar_tilts(
        &self,
        site: &str,
        product: &str,
        valid_time: Option<&str>,
    ) -> Result<RadarTilts, BackendError> {
        let context = ErrorContext::new(
            "get_radar_tilts",
            Some(if product == "velocity" {
                "noaa-radar"
            } else {
                PYTHON_PROVIDER
            }),
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
                    && candidate.country.as_deref() == Some("AU")
            })
        {
            return Err(invalid(
                &context,
                "Australian radar uses its lowest available elevation.",
            ));
        }
        if product == "velocity" {
            let site = sharpmod_weather::radar_sites::sites()
                .iter()
                .find(|candidate| candidate.id.eq_ignore_ascii_case(site))
                .ok_or_else(|| invalid(&context, "unknown radar site"))?;
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
        let mut params = json!({"site_id": site, "product": product});
        if let Some(time) = valid_time {
            params["valid_time"] = json!(time);
        }
        let result: RadarTilts = self
            .call(&context, "radar.tilts", params, Duration::from_secs(120))
            .await?;
        result
            .validate()
            .map_err(|reason| rejected(&context, "radar.tilts", reason))?;
        if !result.site_id.eq_ignore_ascii_case(site) || result.product != product {
            return Err(rejected(
                &context,
                "radar.tilts",
                "the checked site or product does not match".into(),
            ));
        }
        Ok(result)
    }
}

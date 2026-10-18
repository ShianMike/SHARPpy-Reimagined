//! Radar volume/directory checks shared by both tilt pickers and preloading.

use crate::backend::map_ops::context;
use crate::backend::router::Router;
use crate::backend::sounding_ops::{invalid, rejected};
use crate::dto::radar::RadarTilts;
use crate::dto::weather_alerts::alert_time;
use crate::error::BackendError;
use serde_json::json;
use std::time::Duration;

impl Router {
    pub async fn radar_tilts(
        &self,
        site: &str,
        product: &str,
        valid_time: Option<&str>,
    ) -> Result<RadarTilts, BackendError> {
        let context = context("get_radar_tilts", &format!("radar tilts for {site}"));
        if site.len() != 4
            || !site.bytes().all(|c| c.is_ascii_alphanumeric())
            || !matches!(
                product,
                "reflectivity" | "velocity" | "correlation-coefficient"
            )
            || valid_time.is_some_and(|time| alert_time(time).is_err())
        {
            return Err(invalid(
                &context,
                "a radar site, tiltable product and valid time are required",
            ));
        }
        let mut params = json!({"site_id": site, "product": product});
        if let Some(time) = valid_time {
            params["valid_time"] = json!(time);
        }
        let timeout = Duration::from_secs(if product == "velocity" { 60 } else { 120 });
        let result: RadarTilts = self.call(&context, "radar.tilts", params, timeout).await?;
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

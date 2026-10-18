//! Session-cached NWS warning and watch polygons.

use crate::backend::map_ops::context;
use crate::backend::router::Router;
use crate::backend::sounding_ops::{invalid, rejected};
use crate::dto::weather_alerts::{alert_time, WeatherAlerts};
use crate::error::BackendError;
use serde_json::json;
use std::time::Duration;

impl Router {
    pub async fn weather_alerts(&self, valid_time: &str) -> Result<WeatherAlerts, BackendError> {
        let context = context("get_weather_alerts", "NWS warnings and watches");
        if alert_time(valid_time).is_err() {
            return Err(invalid(&context, "validTime must be an RFC 3339 time"));
        }
        let result: WeatherAlerts = self
            .call(
                &context,
                "alerts.weather",
                json!({"valid_time": valid_time}),
                Duration::from_secs(90),
            )
            .await?;
        result
            .validate()
            .map_err(|reason| rejected(&context, "alerts.weather", reason))?;
        Ok(result)
    }
}

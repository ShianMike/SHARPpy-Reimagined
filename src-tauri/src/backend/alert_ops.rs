//! NWS warning and watch polygons, served natively by `sharpmod_weather::alerts`.

use crate::backend::map_ops::context;
use crate::backend::router::Router;
use crate::backend::sounding_ops::{invalid, rejected};
use crate::dto::weather_alerts::{alert_time, WeatherAlert, WeatherAlerts};
use crate::error::{BackendError, ErrorCode};
use serde_json::json;
use std::sync::OnceLock;
use time::OffsetDateTime;

impl Router {
    pub async fn weather_alerts(&self, valid_time: &str) -> Result<WeatherAlerts, BackendError> {
        let context = context("get_weather_alerts", "NWS warnings and watches");
        let when = alert_time(valid_time)
            .map_err(|_| invalid(&context, "validTime must be an RFC 3339 time"))?;
        static ALERTS: OnceLock<sharpmod_weather::alerts::AlertsClient> = OnceLock::new();
        let snapshot = ALERTS
            .get_or_init(Default::default)
            .fetch(when, OffsetDateTime::now_utc())
            .await
            .map_err(|error| {
                let history_unavailable = error == sharpmod_weather::alerts::HISTORY_UNAVAILABLE;
                let snippet: String = error.chars().take(250).collect();
                BackendError {
                    code: ErrorCode::OverlayFetchFailed,
                    message: format!("NWS warnings and watches could not be loaded: {snippet}"),
                    retryable: !history_unavailable,
                    context: Some(Box::new(context.clone())),
                    details: history_unavailable.then(|| {
                        json!({ "reason": "historyUnavailable" })
                            .as_object()
                            .expect("object")
                            .clone()
                    }),
                }
            })?;
        let result = WeatherAlerts {
            valid_time: snapshot.valid_time,
            fetched_at: snapshot.fetched_at,
            alerts: snapshot
                .alerts
                .into_iter()
                .map(|alert| WeatherAlert {
                    id: alert.id,
                    event: alert.event,
                    kind: alert.kind.to_owned(),
                    office: alert.office,
                    issued: alert.issued,
                    expires: alert.expires,
                    colour: alert.colour,
                    source_url: alert.source_url,
                    polygons: alert.polygons,
                })
                .collect(),
        };
        result
            .validate()
            .map_err(|reason| rejected(&context, "alerts.weather", reason))?;
        Ok(result)
    }
}

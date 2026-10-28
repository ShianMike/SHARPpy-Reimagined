//! Current NWS warning/watch polygons; history remains in the session cache.

mod parse;
pub use parse::{Alert, Snapshot};

use reqwest::{Client, Url};
use serde_json::Value;
use std::sync::{Arc, Mutex, PoisonError};
use std::time::Duration;
use time::OffsetDateTime;

const BASE: &str =
    "https://mapservices.weather.noaa.gov/eventdriven/rest/services/WWA/watch_warn_adv/MapServer";
const CAP: &str = "https://api.weather.gov/alerts/active";
const MAX_BYTES: usize = 12 * 1024 * 1024;
pub const HISTORY_UNAVAILABLE: &str =
    "Warning and watch history is available only for snapshots cached during this session";

pub struct AlertsClient {
    client: Result<Client, String>,
    colours: [Mutex<Option<Arc<parse::Colours>>>; 2],
}

impl Default for AlertsClient {
    fn default() -> Self {
        let _ = rustls::crypto::ring::default_provider().install_default();
        Self {
            client: Client::builder()
                .timeout(Duration::from_secs(25))
                .user_agent("SHARPpy-Reimagined (https://github.com/ShianMike/SHARPpy-Reimagined)")
                .build()
                .map_err(|error| error.to_string()),
            colours: std::array::from_fn(|_| Mutex::new(None)),
        }
    }
}

impl AlertsClient {
    pub async fn fetch(
        &self,
        valid_time: OffsetDateTime,
        now: OffsetDateTime,
    ) -> Result<Snapshot, String> {
        if valid_time <= now - time::Duration::minutes(1)
            || valid_time > now + time::Duration::seconds(5)
        {
            return Err(HISTORY_UNAVAILABLE.into());
        }
        let ((storm_colours, zone_colours), (storms, zones)) = tokio::try_join!(
            async { tokio::try_join!(self.colours(0), self.colours(1)) },
            async { tokio::try_join!(self.collection(0), self.collection(1)) }
        )?;
        let snapshot = parse::snapshot(
            valid_time,
            now,
            storms,
            zones,
            &storm_colours,
            &zone_colours,
        )?;
        if snapshot.1 {
            parse::confirm_empty(
                self.json(Url::parse(CAP).expect("static CAP URL")).await?,
                now,
            )?;
        }
        Ok(snapshot.0)
    }

    async fn colours(&self, layer: usize) -> Result<Arc<parse::Colours>, String> {
        if let Some(colours) = self.colours[layer]
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clone()
        {
            return Ok(colours);
        }
        let mut url = Url::parse(&format!("{BASE}/{layer}")).expect("static NWS URL");
        url.query_pairs_mut().append_pair("f", "json");
        let colours = Arc::new(parse::colours(self.json(url).await?, layer)?);
        *self.colours[layer]
            .lock()
            .unwrap_or_else(PoisonError::into_inner) = Some(colours.clone());
        Ok(colours)
    }

    async fn collection(&self, layer: usize) -> Result<Value, String> {
        let mut url = Url::parse(&format!("{BASE}/{layer}/query")).expect("static NWS URL");
        url.query_pairs_mut().extend_pairs([
            ("f", "geojson"),
            ("where", "sig IN ('W','A')"),
            ("outFields", "*"),
            ("outSR", "4326"),
            ("geometryPrecision", "6"),
            ("resultRecordCount", "4000"),
        ]);
        if layer == 1 {
            url.query_pairs_mut()
                .append_pair("maxAllowableOffset", "0.001");
        }
        self.json(url).await
    }

    async fn json(&self, url: Url) -> Result<Value, String> {
        let response = self
            .client
            .as_ref()
            .map_err(Clone::clone)?
            .get(url)
            .header("Accept", "application/geo+json, application/json")
            .send()
            .await
            .map_err(|error| format!("NWS alerts could not be read: {error}"))?
            .error_for_status()
            .map_err(|error| format!("NWS alerts could not be read: {error}"))?;
        let body = crate::http::body(response, MAX_BYTES).await?;
        let payload: Value = serde_json::from_slice(&body)
            .map_err(|error| format!("NWS alerts could not be read: {error}"))?;
        if !payload.is_object() || payload.get("error").is_some() {
            return Err("NWS did not return a usable alert collection".into());
        }
        Ok(payload)
    }
}

#[cfg(test)]
mod tests;

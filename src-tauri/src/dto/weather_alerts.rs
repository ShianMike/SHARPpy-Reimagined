//! NWS warning/watch snapshots: exact polygons and official hazard colours.

use crate::dto::checks::{check_lat_lon, check_text};
use crate::dto::map::colour_ok;
use serde::{Deserialize, Serialize};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct WeatherAlert {
    pub id: String,
    pub event: String,
    pub kind: String,
    pub office: String,
    pub issued: String,
    pub expires: String,
    pub colour: String,
    pub source_url: String,
    pub polygons: Vec<Vec<Vec<[f64; 2]>>>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(
    rename_all(serialize = "camelCase", deserialize = "snake_case"),
    deny_unknown_fields
)]
pub struct WeatherAlerts {
    pub valid_time: String,
    pub fetched_at: String,
    pub alerts: Vec<WeatherAlert>,
}

pub fn alert_time(value: &str) -> Result<OffsetDateTime, String> {
    check_text("alert time", value, 20, 40)?;
    OffsetDateTime::parse(value, &Rfc3339).map_err(|_| "invalid RFC 3339 alert time".into())
}

impl WeatherAlerts {
    pub fn validate(&self) -> Result<(), String> {
        alert_time(&self.valid_time)?;
        let fetched = alert_time(&self.fetched_at)?;
        if self.alerts.len() > 4000 {
            return Err("too many weather alerts".into());
        }
        let mut seen = std::collections::BTreeSet::new();
        let mut points = 0;
        for alert in &self.alerts {
            for (label, value, limit) in [
                ("id", &alert.id, 500),
                ("event", &alert.event, 120),
                ("office", &alert.office, 20),
                ("sourceUrl", &alert.source_url, 1000),
            ] {
                check_text(label, value, 1, limit)?;
            }
            if !seen.insert(&alert.id)
                || !matches!(alert.kind.as_str(), "warning" | "watch")
                || !colour_ok(&alert.colour)
            {
                return Err("invalid weather alert identity, kind or colour".into());
            }
            let url = tauri::Url::parse(&alert.source_url).map_err(|_| "invalid alert URL")?;
            if url.scheme() != "https"
                || url.host_str() != Some("api.weather.gov")
                || !url.username().is_empty()
                || url.password().is_some()
                || url.port().is_some()
            {
                return Err("unsupported alert URL".into());
            }
            if !(alert_time(&alert.issued)? <= fetched && fetched < alert_time(&alert.expires)?) {
                return Err("alert must be active when fetched".into());
            }
            if alert.polygons.is_empty() {
                return Err("an alert needs polygons".into());
            }
            for polygon in &alert.polygons {
                if polygon.is_empty() {
                    return Err("empty alert polygon".into());
                }
                for ring in polygon {
                    points += ring.len();
                    if ring.len() < 4 || ring.first() != ring.last() || points > 250_000 {
                        return Err("invalid or excessive alert boundary".into());
                    }
                    for [lon, lat] in ring {
                        check_lat_lon(Some(*lat), Some(*lon))?;
                    }
                }
            }
        }
        Ok(())
    }
}

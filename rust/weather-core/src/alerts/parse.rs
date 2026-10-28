use crate::format_rfc3339_utc;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

pub(super) type Colours = HashMap<Vec<String>, String>;
type Polygons = Vec<Vec<Vec<[f64; 2]>>>;

#[derive(Debug, Serialize)]
pub struct Alert {
    pub id: String,
    pub event: String,
    pub kind: &'static str,
    pub office: String,
    pub issued: String,
    pub expires: String,
    pub colour: String,
    pub source_url: String,
    pub polygons: Polygons,
}

#[derive(Debug, Serialize)]
pub struct Snapshot {
    pub valid_time: String,
    pub fetched_at: String,
    pub alerts: Vec<Alert>,
}

#[derive(Deserialize)]
struct Collection {
    features: Vec<Feature>,
    #[serde(default, rename = "exceededTransferLimit")]
    incomplete: bool,
}

#[derive(Deserialize)]
struct Feature {
    properties: Value,
    #[serde(default)]
    geometry: Value,
}

#[derive(Deserialize)]
struct Properties {
    cap_id: String,
    sig: String,
    phenom: String,
    prod_type: String,
    issuance: String,
    expiration: String,
    #[serde(default)]
    ends: Option<String>,
    wfo: String,
    url: String,
}

#[derive(Deserialize)]
#[serde(tag = "type", content = "coordinates")]
enum Geometry {
    Polygon(Vec<Vec<[f64; 2]>>),
    MultiPolygon(Polygons),
}

fn text(value: &str, maximum: usize) -> Result<String, String> {
    let value = value.trim();
    if value.is_empty() || value.chars().count() > maximum {
        return Err("NWS supplied invalid alert metadata".into());
    }
    Ok(value.to_owned())
}

fn timestamp(value: &str) -> Result<OffsetDateTime, String> {
    OffsetDateTime::parse(value.trim(), &Rfc3339)
        .map_err(|_| "NWS supplied an invalid alert time".into())
}

pub(super) fn colours(payload: Value, layer: usize) -> Result<Colours, String> {
    let groups = payload
        .pointer("/drawingInfo/renderer/uniqueValueGroups")
        .and_then(Value::as_array)
        .ok_or("NWS alert colours are unavailable")?;
    let mut result = HashMap::new();
    for group in groups {
        for item in group
            .get("classes")
            .and_then(Value::as_array)
            .ok_or("NWS supplied invalid alert colours")?
        {
            let path = if layer == 0 {
                "/symbol/outline/color"
            } else {
                "/symbol/color"
            };
            let rgb = item
                .pointer(path)
                .and_then(Value::as_array)
                .ok_or("NWS supplied an invalid alert colour")?;
            if rgb.len() < 3 {
                return Err("NWS supplied an invalid alert colour".into());
            }
            let mut bytes = [0u8; 3];
            for (to, from) in bytes.iter_mut().zip(rgb) {
                *to = from
                    .as_u64()
                    .and_then(|n| u8::try_from(n).ok())
                    .ok_or("NWS supplied an invalid alert colour")?;
            }
            for values in item
                .get("values")
                .and_then(Value::as_array)
                .ok_or("NWS supplied invalid alert colours")?
            {
                let values: Vec<String> = serde_json::from_value(values.clone())
                    .map_err(|_| "NWS supplied invalid alert colours")?;
                result.insert(
                    values,
                    format!("#{:02x}{:02x}{:02x}", bytes[0], bytes[1], bytes[2]),
                );
            }
        }
    }
    if result.is_empty() {
        return Err("NWS alert colours are unavailable".into());
    }
    Ok(result)
}

pub(super) fn snapshot(
    valid_time: OffsetDateTime,
    now: OffsetDateTime,
    storms: Value,
    zones: Value,
    storm_colours: &Colours,
    zone_colours: &Colours,
) -> Result<(Snapshot, bool), String> {
    let mut alerts: Vec<Alert> = Vec::new();
    let mut identities = HashMap::new();
    let mut preferred = HashSet::new();
    let mut points = 0usize;
    let mut empty_zones = false;
    for (layer, payload, colours) in [(0, storms, storm_colours), (1, zones, zone_colours)] {
        let collection: Collection = serde_json::from_value(payload)
            .map_err(|_| "NWS returned an incomplete alert collection")?;
        if collection.incomplete || collection.features.len() > 4000 {
            return Err("NWS returned an incomplete alert collection".into());
        }
        if layer == 1 {
            empty_zones = collection.features.is_empty();
        }
        for feature in collection.features {
            if !matches!(feature.properties["sig"].as_str(), Some("W" | "A")) {
                continue;
            }
            let props: Properties = serde_json::from_value(feature.properties)
                .map_err(|_| "NWS supplied invalid alert metadata")?;
            let issued = timestamp(&props.issuance)?;
            let mut expires = timestamp(&props.expiration)?;
            if let Some(ends) = props.ends.as_deref().filter(|ends| !ends.trim().is_empty()) {
                expires = expires.min(timestamp(ends)?);
            }
            if !(issued <= now && now < expires) {
                continue;
            }
            let id = text(&props.cap_id, 500)?;
            if layer == 1 && preferred.contains(&id) {
                continue;
            }
            let geometry: Geometry = serde_json::from_value(feature.geometry)
                .map_err(|_| "NWS supplied an invalid alert geometry")?;
            let polygons = match geometry {
                Geometry::Polygon(polygon) => vec![polygon],
                Geometry::MultiPolygon(polygons) => polygons,
            };
            if polygons.is_empty() {
                return Err("NWS supplied an empty alert geometry".into());
            }
            for polygon in &polygons {
                if polygon.is_empty() {
                    return Err("NWS supplied an empty alert polygon".into());
                }
                for ring in polygon {
                    points += ring.len();
                    if ring.len() < 4 || points > 250_000 {
                        return Err("NWS alert geometry exceeds the supported limits".into());
                    }
                    if ring.first() != ring.last() {
                        return Err("NWS supplied an unclosed alert boundary".into());
                    }
                    if ring.iter().any(|[lon, lat]| {
                        !lon.is_finite()
                            || !lat.is_finite()
                            || !(-180.0..=180.0).contains(lon)
                            || !(-90.0..=90.0).contains(lat)
                    }) {
                        return Err("NWS supplied an invalid alert coordinate".into());
                    }
                }
            }
            if layer == 0 {
                preferred.insert(id.clone());
            }
            let key = if layer == 0 {
                vec![props.phenom, props.sig.clone()]
            } else {
                vec![props.prod_type.clone()]
            };
            let colour = colours
                .get(&key)
                .ok_or("NWS did not identify this alert's display colour")?
                .clone();
            let source_url = text(&props.url, 1000)?;
            let url = reqwest::Url::parse(&source_url)
                .map_err(|_| "NWS supplied an unsupported alert link")?;
            if url.scheme() != "https"
                || url.host_str() != Some("api.weather.gov")
                || !url.username().is_empty()
                || url.password().is_some()
                || url.port_or_known_default() != Some(443)
            {
                return Err("NWS supplied an unsupported alert link".into());
            }
            let event = text(&props.prod_type, 120)?;
            let office = text(&props.wfo, 20)?;
            if let Some(index) = identities.get(&id) {
                let alert: &mut Alert = &mut alerts[*index];
                alert.polygons.extend(polygons);
            } else {
                identities.insert(id.clone(), alerts.len());
                alerts.push(Alert {
                    id,
                    event,
                    kind: if props.sig == "W" { "warning" } else { "watch" },
                    office,
                    issued: format_rfc3339_utc(issued),
                    expires: format_rfc3339_utc(expires),
                    colour,
                    source_url,
                    polygons,
                });
            }
        }
    }
    if alerts.len() > 4000 {
        return Err("NWS returned too many active alerts".into());
    }
    let confirm = empty_zones || alerts.is_empty();
    Ok((
        Snapshot {
            valid_time: format_rfc3339_utc(valid_time),
            fetched_at: format_rfc3339_utc(now),
            alerts,
        },
        confirm,
    ))
}

pub(super) fn confirm_empty(payload: Value, now: OffsetDateTime) -> Result<(), String> {
    if payload
        .pointer("/pagination/next")
        .is_some_and(|next| !next.is_null() && next != "")
    {
        return Err("NWS did not provide a complete alert status check".into());
    }
    let collection: Collection = serde_json::from_value(payload)
        .map_err(|_| "NWS did not provide a complete alert status check")?;
    if collection.incomplete || collection.features.len() > 4000 {
        return Err("NWS did not provide a complete alert status check".into());
    }
    for feature in collection.features {
        let props = &feature.properties;
        let event = text(
            props["event"]
                .as_str()
                .ok_or("NWS supplied invalid alert metadata")?,
            120,
        )?;
        if (event.ends_with(" Warning") || event.ends_with(" Watch"))
            && props["status"] == "Actual"
            && props["messageType"] != "Cancel"
            && timestamp(
                props["expires"]
                    .as_str()
                    .ok_or("NWS supplied an invalid alert time")?,
            )? > now
        {
            return Err("NWS map areas are temporarily incomplete; active warnings or watches still exist. Refresh to check again".into());
        }
    }
    Ok(())
}

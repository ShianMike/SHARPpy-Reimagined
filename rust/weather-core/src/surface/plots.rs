use super::{Dataset, Observation, SurfaceClient};
use crate::format_rfc3339_utc;
use serde::Serialize;
use std::collections::HashSet;
use std::sync::OnceLock;
use time::{OffsetDateTime, UtcOffset};

pub const MAX_PLOTS: usize = 64;

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct StaffSpec {
    pub angle_deg: f64,
    pub pennants: u32,
    pub barbs: u32,
    pub halves: u32,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct SurfacePlot {
    pub id: String,
    pub longitude: f64,
    pub latitude: f64,
    pub valid: Option<String>,
    pub offset_min: Option<f64>,
    pub stale: bool,
    pub availability: String,
    pub wind_state: String,
    pub staff: Option<StaffSpec>,
    pub temperature_c: Option<f64>,
    pub dewpoint_c: Option<f64>,
    pub upper_text: String,
    pub lower_text: String,
    pub label: String,
    pub description: String,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct SurfacePlotCounts {
    pub matched: u32,
    pub stale: u32,
    pub no_observation: u32,
    pub failed: u32,
    pub nearby: u32,
    pub queried: u32,
    pub unqueried: u32,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct SurfacePlotsSnapshot {
    pub valid_time: String,
    pub counts: SurfacePlotCounts,
    pub plots: Vec<SurfacePlot>,
}

fn wind_state(obs: &Observation) -> &'static str {
    let Some(speed) = obs.speed_kt else {
        return "missing";
    };
    if speed.abs() < 0.5 {
        return "calm";
    }
    if obs.direction_deg.is_none() {
        return "direction-missing";
    }
    "available"
}

fn wind_uv_kt(obs: &Observation, state: &str) -> Option<(f64, f64)> {
    if state == "calm" {
        return Some((0.0, 0.0));
    }
    if state != "available" {
        return None;
    }
    let dir = obs.direction_deg?;
    let speed = obs.speed_kt?;
    let angle = dir.to_radians();
    Some((-speed * angle.sin(), -speed * angle.cos()))
}

fn staff_spec(u: f64, v: f64) -> Option<StaffSpec> {
    let speed = u.hypot(v);
    if speed < 0.5 {
        return None;
    }
    let mut remaining = ((speed / 5.0).round() as u32) * 5;
    let pennants = remaining / 50;
    remaining %= 50;
    let barbs = remaining / 10;
    remaining %= 10;
    let halves = u32::from(remaining >= 5);
    let mut angle = v.atan2(-u).to_degrees().rem_euclid(360.0);
    if angle >= 360.0 {
        angle = 0.0;
    }
    Some(StaffSpec {
        angle_deg: angle,
        pennants,
        barbs,
        halves,
    })
}

fn reading(value: Option<f64>) -> String {
    match value {
        None => "—".to_owned(),
        Some(v) => {
            let rounded = v.round();
            if rounded == 0.0 {
                "0".to_owned()
            } else {
                format!("{v:.0}")
            }
        }
    }
}

fn sample_text(value: Option<f64>) -> String {
    match value {
        None => "missing".to_owned(),
        Some(v) => format!("{v:.1} °C"),
    }
}

fn wind_text(obs: &Observation, state: &str) -> String {
    let mut text = match state {
        "calm" => "calm (0 kt)".to_owned(),
        "direction-missing" => format!("{:.0} kt; direction missing", obs.speed_kt.unwrap_or(0.0)),
        "available" => format!(
            "{:.0}° at {:.0} kt",
            obs.direction_deg.unwrap_or(0.0),
            obs.speed_kt.unwrap_or(0.0)
        ),
        _ => "missing (not calm)".to_owned(),
    };
    if let Some(gust) = obs.gust_kt {
        text.push_str(&format!(", gusting {gust:.0} kt"));
    }
    text
}

fn fmt_utc_ymdhms(dt: OffsetDateTime) -> String {
    let u = dt.to_offset(UtcOffset::UTC);
    format!(
        "{:04}-{:02}-{:02} {:02}:{:02}:{:02}Z",
        u.year(),
        u8::from(u.month()),
        u.day(),
        u.hour(),
        u.minute(),
        u.second()
    )
}

fn fmt_utc_ymdhm(dt: OffsetDateTime) -> String {
    let u = dt.to_offset(UtcOffset::UTC);
    format!(
        "{:04}-{:02}-{:02} {:02}:{:02}Z",
        u.year(),
        u8::from(u.month()),
        u.day(),
        u.hour(),
        u.minute()
    )
}

pub fn build_plots(dataset: &Dataset) -> Result<SurfacePlotsSnapshot, String> {
    let requested_stamp = fmt_utc_ymdhm(dataset.requested);
    let mut out = Vec::with_capacity(dataset.matched.len() + dataset.unmatched.len());
    let mut stale_count = 0_u32;
    for (station_idx, obs) in &dataset.matched {
        let station = &dataset.catalog[*station_idx];
        let state = wind_state(obs);
        let staff = wind_uv_kt(obs, state).and_then(|(u, v)| staff_spec(u, v));
        let offset_secs = (obs.observed_at - dataset.requested).as_seconds_f64();
        let offset = offset_secs / 60.0;
        let offset_rounded = (offset * 10.0).round() / 10.0;
        let offset_min = if offset_rounded == 0.0 {
            0.0
        } else {
            offset_rounded
        };
        let stale = offset_secs.abs() > 1800.0;
        if stale {
            stale_count += 1;
        }
        let offset_whole = if offset.round() == 0.0 { 0.0 } else { offset };
        let mut body = vec![
            format!(
                "Observed {} ({:+.0} min from requested)",
                fmt_utc_ymdhms(obs.observed_at),
                offset_whole
            ),
            format!(
                "Temperature {}; dewpoint {}; wind {}",
                sample_text(obs.temperature_c),
                sample_text(obs.dewpoint_c),
                wind_text(obs, state)
            ),
            "Source: NOAA/NWS API station observation".to_owned(),
        ];
        if stale {
            body.push("Stale/mismatched: more than 30 minutes from requested time".to_owned());
        }
        out.push(SurfacePlot {
            id: station.id.clone(),
            longitude: station.lon,
            latitude: station.lat,
            valid: Some(format_rfc3339_utc(obs.observed_at)),
            offset_min: Some(offset_min),
            stale,
            availability: "available".to_owned(),
            wind_state: state.to_owned(),
            staff,
            temperature_c: obs.temperature_c,
            dewpoint_c: obs.dewpoint_c,
            upper_text: reading(obs.temperature_c),
            lower_text: reading(obs.dewpoint_c),
            label: format!("{} — {}", station.id, station.name),
            description: body.join("\n"),
        });
    }
    let failed_set: HashSet<usize> = dataset.failed.iter().copied().collect();
    let mut unmatched_unique = HashSet::new();
    for &station_idx in &dataset.unmatched {
        let Some(station) = dataset.catalog.get(station_idx) else {
            continue;
        };
        unmatched_unique.insert(station_idx);
        let no_match = !failed_set.contains(&station_idx);
        let availability = if no_match { "unavailable" } else { "unknown" };
        let body = if no_match {
            format!(
                "No observation was available within ±60 minutes of the requested {requested_stamp}.\nSource: NOAA/NWS API station catalogue"
            )
        } else {
            "The station query failed; observation availability is unknown.\nSource: NOAA/NWS API station catalogue".to_owned()
        };
        out.push(SurfacePlot {
            id: station.id.clone(),
            longitude: station.lon,
            latitude: station.lat,
            valid: None,
            offset_min: None,
            stale: false,
            availability: availability.to_owned(),
            wind_state: "missing".to_owned(),
            staff: None,
            temperature_c: None,
            dewpoint_c: None,
            upper_text: "—".to_owned(),
            lower_text: "—".to_owned(),
            label: format!(
                "{} — {}",
                station.id,
                if no_match {
                    "no observation"
                } else {
                    "query failed"
                }
            ),
            description: body,
        });
    }
    if out.len() > MAX_PLOTS {
        return Err("The surface set has more station plots than the map draws.".into());
    }
    let no_observation = unmatched_unique.difference(&failed_set).count() as u32;
    let discovered = dataset.catalog.len() as u32;
    let queried = dataset.queried.len() as u32;
    Ok(SurfacePlotsSnapshot {
        valid_time: format_rfc3339_utc(dataset.requested),
        counts: SurfacePlotCounts {
            matched: dataset.matched.len() as u32,
            stale: stale_count,
            no_observation,
            failed: failed_set.len() as u32,
            nearby: discovered,
            queried,
            unqueried: discovered.saturating_sub(queried),
        },
        plots: out,
    })
}

pub async fn fetch_plots(
    lat: f64,
    lon: f64,
    when: OffsetDateTime,
) -> Result<SurfacePlotsSnapshot, String> {
    static CLIENT: OnceLock<SurfaceClient> = OnceLock::new();
    let request = super::Request {
        lat,
        lon,
        requested: when,
        tolerance_seconds: 3600.0,
        max_stations: 24,
        workers: super::MAX_WORKERS as i64,
        timeout: 15.0,
    };
    let dataset = super::fetch(
        CLIENT.get_or_init(Default::default).clone(),
        request,
        || Ok(false),
    )
    .await
    .map_err(|error| match error {
        super::FetchError::Cancelled => "surface-observation request cancelled".to_owned(),
        super::FetchError::Provider(msg) => msg,
    })?;
    build_plots(&dataset)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::surface::Station;

    #[test]
    fn build_plots_matches_contract_fixture() {
        let parse_ts = |s: &str| {
            OffsetDateTime::parse(s, &time::format_description::well_known::Rfc3339).unwrap()
        };
        let requested = parse_ts("2026-10-04T21:00:00Z");
        let station = |id: &str, name: &str, lat: f64, lon: f64| Station {
            id: id.into(),
            name: name.into(),
            lat,
            lon,
            elevation_m: Some(300.0),
            source_url: format!("https://api.weather.gov/stations/{id}"),
        };
        let dataset = Dataset {
            requested,
            lat: 35.2,
            lon: -97.4,
            catalog: vec![
                station("KOUN", "Norman", 35.2, -97.4),
                station("KOKC", "Oklahoma City", 35.4, -97.6),
                station("KSPS", "Wichita Falls", 33.9, -98.5),
                station("KSWA", "Wichita", 37.6, -97.4),
                station("KHBR", "Hobart", 35.0, -99.0),
                station("KFOO", "Foo", 34.0, -97.0),
            ],
            matched: vec![
                (
                    0,
                    Observation {
                        observed_at: requested,
                        temperature_c: Some(21.5),
                        dewpoint_c: Some(12.2),
                        direction_deg: Some(270.0),
                        speed_kt: Some(20.0),
                        gust_kt: Some(28.0),
                        text: String::new(),
                        source_url: String::new(),
                    },
                ),
                (
                    1,
                    Observation {
                        observed_at: requested,
                        temperature_c: Some(18.0),
                        dewpoint_c: Some(10.0),
                        direction_deg: None,
                        speed_kt: Some(0.0),
                        gust_kt: None,
                        text: String::new(),
                        source_url: String::new(),
                    },
                ),
                (
                    2,
                    Observation {
                        observed_at: requested,
                        temperature_c: Some(33.0),
                        dewpoint_c: Some(20.0),
                        direction_deg: None,
                        speed_kt: Some(12.0),
                        gust_kt: None,
                        text: String::new(),
                        source_url: String::new(),
                    },
                ),
                (
                    3,
                    Observation {
                        observed_at: requested - time::Duration::minutes(45),
                        temperature_c: Some(26.0),
                        dewpoint_c: Some(9.0),
                        direction_deg: Some(180.0),
                        speed_kt: Some(8.0),
                        gust_kt: None,
                        text: String::new(),
                        source_url: String::new(),
                    },
                ),
            ],
            queried: vec![0, 1, 2, 3, 4, 5],
            unmatched: vec![4, 5],
            failed: vec![5],
            retrieved_at: requested,
            source_url: String::new(),
        };
        let actual = serde_json::to_value(build_plots(&dataset).unwrap()).unwrap();
        let expected: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../src-tauri/contracts/bridge/map/surface.result.json"
        ))
        .unwrap();
        assert_eq!(actual, expected);
    }
}

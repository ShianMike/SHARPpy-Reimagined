use super::{Observation, Station};
use serde_json::Value;
use time::{format_description::well_known::Rfc3339, Duration, OffsetDateTime, UtcOffset};

fn finite(value: &Value) -> Option<f64> {
    value
        .as_f64()
        .or_else(|| value.as_str()?.trim().parse().ok())
        .filter(|number| number.is_finite())
}

enum Unit {
    Celsius,
    Knots,
    Degrees,
    Metres,
}

fn quantity(value: Option<&Value>, target: Unit) -> Option<f64> {
    let value = value?;
    let number = finite(value.get("value")?)?;
    let unit = value
        .get("unitCode")?
        .as_str()?
        .rsplit(':')
        .next()?
        .replace('_', "")
        .to_lowercase();
    match target {
        Unit::Celsius if unit.contains("degc") || matches!(unit.as_str(), "c" | "celsius") => {
            Some(number)
        }
        Unit::Celsius if unit.contains("degf") || matches!(unit.as_str(), "f" | "fahrenheit") => {
            Some((number - 32.) * 5. / 9.)
        }
        Unit::Celsius if matches!(unit.as_str(), "k" | "kelvin") => Some(number - 273.15),
        Unit::Knots if unit.contains("m/s") || unit.replace('^', "").contains("ms-1") => {
            Some(number * 1.9438444924406)
        }
        Unit::Knots if unit.contains("kmh-1") || unit.contains("km/h") => {
            Some(number * 0.539956803)
        }
        Unit::Knots if unit.contains("kt") || unit.contains("knot") => Some(number),
        Unit::Knots if unit.contains("mih-1") || unit.contains("mph") => Some(number * 0.868976242),
        Unit::Degrees if unit.contains("degree") || unit == "deg" => Some(number.rem_euclid(360.)),
        Unit::Metres if matches!(unit.as_str(), "m" | "meter" | "metre") => Some(number),
        Unit::Metres if unit.contains("ft") || unit.contains("foot") => Some(number * 0.3048),
        _ => None,
    }
}

pub fn stations(payload: &Value) -> Vec<Station> {
    payload
        .get("features")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|feature| {
            let properties = feature.get("properties")?;
            let coordinates = feature.get("geometry")?.get("coordinates")?.as_array()?;
            let lon = finite(coordinates.first()?)?;
            let lat = finite(coordinates.get(1)?)?;
            let id = properties
                .get("stationIdentifier")?
                .as_str()?
                .trim()
                .to_uppercase();
            if id.is_empty() || !(-90.0..=90.0).contains(&lat) || !(-180.0..=180.0).contains(&lon) {
                return None;
            }
            Some(Station {
                name: properties
                    .get("name")
                    .and_then(Value::as_str)
                    .filter(|name| !name.is_empty())
                    .unwrap_or(&id)
                    .into(),
                id,
                lat,
                lon,
                elevation_m: quantity(properties.get("elevation"), Unit::Metres),
                source_url: feature
                    .get("id")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .into(),
            })
        })
        .collect()
}

pub fn observations(payload: &Value) -> Vec<Observation> {
    let mut observations: Vec<_> = payload
        .get("features")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|feature| {
            let properties = feature.get("properties")?;
            let observed_at =
                OffsetDateTime::parse(properties.get("timestamp")?.as_str()?, &Rfc3339)
                    .ok()?
                    .checked_to_offset(UtcOffset::UTC)?;
            if !(1..=9999).contains(&observed_at.year()) {
                return None;
            }
            Some(Observation {
                observed_at,
                temperature_c: quantity(properties.get("temperature"), Unit::Celsius),
                dewpoint_c: quantity(properties.get("dewpoint"), Unit::Celsius),
                direction_deg: quantity(properties.get("windDirection"), Unit::Degrees),
                speed_kt: quantity(properties.get("windSpeed"), Unit::Knots),
                gust_kt: quantity(properties.get("windGust"), Unit::Knots),
                text: properties
                    .get("textDescription")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .into(),
                source_url: feature
                    .get("id")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .into(),
            })
        })
        .collect();
    observations.sort_by_key(|observation| observation.observed_at);
    observations
}

pub fn nearest_index(
    times: impl IntoIterator<Item = OffsetDateTime>,
    requested: OffsetDateTime,
    tolerance: Duration,
) -> Option<usize> {
    times
        .into_iter()
        .enumerate()
        .min_by_key(|(_, time)| ((*time - requested).abs(), *time))
        .filter(|(_, time)| (*time - requested).abs() <= tolerance)
        .map(|(index, _)| index)
}

#[cfg(test)]
mod tests;

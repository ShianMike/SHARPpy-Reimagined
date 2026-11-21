use time::{parsing::Parsable, OffsetDateTime, PrimitiveDateTime};

use super::{is_fresh, sort_and_cap, Position, Source, SpotterMotion};

const MAX_DISPLAY_NAME_UNITS: usize = 80;

#[derive(Default)]
struct Object<'a> {
    coordinates: Option<(f64, f64)>,
    popup: Option<Popup<'a>>,
    ambiguous_popup: bool,
}

struct Popup<'a> {
    first_line: &'a str,
    timestamp: OffsetDateTime,
    motion: Option<SpotterMotion>,
}

pub(super) fn parse(text: &str, now: OffsetDateTime, source: Source) -> Option<Vec<Position>> {
    let format = time::format_description::parse_borrowed::<2>(
        "[year]-[month]-[day] [hour]:[minute]:[second] UTC",
    )
    .expect("static Spotter Network timestamp format");
    let mut title = false;
    let mut refresh = false;
    let mut current: Option<Object<'_>> = None;
    let mut saw_object = false;
    let mut saw_valid_object = false;
    let mut positions = Vec::new();

    for line in text.trim_start_matches('\u{feff}').lines().map(str::trim) {
        if line.is_empty() || line.starts_with(';') {
            continue;
        }
        let (directive, value) = line.split_once(':')?;
        let value = value.trim();
        match directive {
            "Title" if current.is_none() && !title && !value.is_empty() => title = true,
            "Refresh" if current.is_none() && !refresh => {
                refresh = value.parse::<u32>().ok()? > 0;
            }
            "Object" if title && refresh && current.is_none() => {
                saw_object = true;
                current = Some(Object {
                    coordinates: coordinates(value),
                    ..Object::default()
                });
            }
            "End" if value.is_empty() => {
                let object = current.take()?;
                if object.ambiguous_popup {
                    continue;
                }
                if let (Some((latitude, longitude)), Some(popup)) =
                    (object.coordinates, object.popup)
                {
                    saw_valid_object = true;
                    if is_fresh(popup.timestamp, now) {
                        positions.push(Position {
                            latitude,
                            longitude,
                            updated_at: popup.timestamp,
                            chase_cam: source == Source::Video,
                            display_name: match source {
                                Source::General => display_name(popup.first_line),
                                Source::Video => None,
                            },
                            motion: popup.motion,
                        });
                    }
                }
            }
            "Icon" => {
                let Some(object) = current.as_mut() else {
                    continue;
                };
                // Decorative icons have no timestamped popup.
                let Some(popup) = icon_popup(value, &format) else {
                    continue;
                };
                if object.popup.is_some() {
                    object.ambiguous_popup = true;
                } else {
                    object.popup = Some(popup);
                }
            }
            // The named feed draws a separate Text label inside each object.
            // Only the Icon popup supplies position details.
            "Text" => {}
            "IconFile" | "Font" | "Color" | "Threshold" if current.is_none() => {}
            _ => return None,
        }
    }

    // A header-only placefile and a placefile with only expired records are
    // both successful empty checks. A truncated or invalid feed is a failure.
    if !title || !refresh || current.is_some() || (saw_object && !saw_valid_object) {
        return None;
    }
    sort_and_cap(&mut positions);
    Some(positions)
}

fn coordinates(value: &str) -> Option<(f64, f64)> {
    let (lat, lon) = value.split_once(',')?;
    let latitude = lat.trim().parse().ok()?;
    let longitude = lon.trim().parse().ok()?;
    ((-90.0..=90.0).contains(&latitude) && (-180.0..=180.0).contains(&longitude))
        .then_some((latitude, longitude))
}

fn icon_popup<'a>(value: &'a str, format: &impl Parsable) -> Option<Popup<'a>> {
    let mut fields = value.splitn(6, ',');
    for _ in 0..3 {
        if !fields.next()?.trim().parse::<f64>().ok()?.is_finite() {
            return None;
        }
    }
    for _ in 0..2 {
        fields.next()?.trim().parse::<u16>().ok()?;
    }
    let quoted = fields.next()?.trim().strip_prefix('"')?.strip_suffix('"')?;
    let mut lines = quoted.split(r"\n");
    let first_line = lines.next()?;
    let timestamp = lines.next()?;
    if timestamp.len() != 23 {
        return None;
    }
    Some(Popup {
        first_line,
        timestamp: PrimitiveDateTime::parse(timestamp, format)
            .ok()?
            .assume_utc(),
        // Both feeds put motion immediately after the timestamp. Later lines
        // may contain contact information and other free text.
        motion: lines.next().and_then(motion),
    })
}

fn display_name(raw: &str) -> Option<String> {
    if raw
        .chars()
        .any(|ch| ch.is_control() || matches!(ch, '\u{2028}' | '\u{2029}'))
    {
        return None;
    }
    let name = raw.trim();
    let (name, callsign) = if let Some((name, callsign)) = name
        .strip_suffix(')')
        .and_then(|label| label.rsplit_once(" ("))
    {
        let base = match callsign.split_once('/') {
            Some((base, "P" | "M" | "MM" | "AM")) => base,
            Some(_) => return None,
            None => callsign,
        };
        if !(3..=7).contains(&base.len())
            || !base.bytes().all(|byte| byte.is_ascii_alphanumeric())
            || !base.bytes().any(|byte| byte.is_ascii_alphabetic())
            || !base.bytes().any(|byte| byte.is_ascii_digit())
        {
            return None;
        }
        (name.trim_end(), Some(callsign))
    } else {
        (name, None)
    };
    // Name punctuation is allowed within words, with periods only at word
    // ends for initials and suffixes. Addresses and contact fields fail this grammar.
    if name.is_empty()
        || !name.split_whitespace().all(|word| {
            let word = word.strip_suffix('.').unwrap_or(word);
            word.chars().any(char::is_alphabetic)
                && word
                    .chars()
                    .all(|ch| ch.is_alphabetic() || matches!(ch, '\'' | '’' | '-' | '‐' | '‑'))
        })
    {
        return None;
    }

    // Reserve space so clipping the name never leaves a partial suffix.
    let max_name_units = MAX_DISPLAY_NAME_UNITS - callsign.map_or(0, |value| value.len() + 3);
    let mut units = 0;
    let end = name
        .char_indices()
        .find_map(|(index, ch)| {
            units += ch.len_utf16();
            (units > max_name_units).then_some(index)
        })
        .unwrap_or(name.len());
    let name = name[..end].trim_end();
    Some(match callsign {
        Some(callsign) => format!("{name} ({callsign})"),
        None => name.to_owned(),
    })
}

fn motion(line: &str) -> Option<SpotterMotion> {
    let line = line.trim();
    if line == "STATIONARY" {
        return Some(SpotterMotion::Stationary);
    }
    let (direction, number) = line.strip_prefix("Heading: ")?.split_once(" (")?;
    if !matches!(
        direction,
        "N" | "NNE"
            | "NE"
            | "ENE"
            | "E"
            | "ESE"
            | "SE"
            | "SSE"
            | "S"
            | "SSW"
            | "SW"
            | "WSW"
            | "W"
            | "WNW"
            | "NW"
            | "NNW"
    ) {
        return None;
    }
    let digits = number.strip_suffix(')')?;
    if !(1..=3).contains(&digits.len()) || !digits.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    let degrees = digits.parse::<u16>().ok()?;
    match degrees {
        0..=359 => Some(SpotterMotion::Heading { degrees }),
        360 => Some(SpotterMotion::Heading { degrees: 0 }),
        _ => None,
    }
}

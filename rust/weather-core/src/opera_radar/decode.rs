//! Bounded ODIM 2.0–2.4 single-site reflectivity sweeps.

use crate::polar_radar::{Radial, Sweep};
use crate::radar_sites::RadarSite;
use hdf5_reader::{group::Group, ByteOrder, Datatype, Hdf5File, OpenOptions};
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

type AngleBounds = (Vec<f64>, Vec<f64>);

fn text(group: &Group, name: &str) -> Result<String, String> {
    group
        .attribute(name)
        .and_then(|a| a.read_string())
        .map_err(|e| e.to_string())
}

fn number(group: &Group, name: &str) -> Result<f64, String> {
    let value = group
        .attribute(name)
        .and_then(|a| a.read_as_f64())
        .map_err(|e| e.to_string())?;
    if value.is_finite() {
        Ok(value)
    } else {
        Err(format!("ODIM {name} is not finite"))
    }
}

fn time(group: &Group, prefix: &str) -> Result<OffsetDateTime, String> {
    let date = text(group, &format!("{prefix}date"))?;
    let clock = text(group, &format!("{prefix}time"))?;
    if date.len() != 8
        || clock.len() != 6
        || !date
            .bytes()
            .chain(clock.bytes())
            .all(|b| b.is_ascii_digit())
    {
        return Err("Invalid ODIM scan time".into());
    }
    let stamp = format!(
        "{}-{}-{}T{}:{}:{}Z",
        &date[0..4],
        &date[4..6],
        &date[6..8],
        &clock[0..2],
        &clock[2..4],
        &clock[4..6]
    );
    OffsetDateTime::parse(&stamp, &Rfc3339).map_err(|e| e.to_string())
}

fn index(name: &str, prefix: &str) -> Option<usize> {
    let suffix = name.strip_prefix(prefix)?;
    if suffix.is_empty() || !suffix.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    suffix
        .parse::<usize>()
        .ok()
        .filter(|n| (1..=64).contains(n))
}

fn read_samples(dtype: &Datatype, raw: &[u8], expected: usize) -> Result<Vec<f64>, String> {
    match *dtype {
        Datatype::FixedPoint {
            size: 1,
            signed: false,
            ..
        } if raw.len() == expected => Ok(raw.iter().map(|&b| f64::from(b)).collect()),
        Datatype::FixedPoint {
            size: 1,
            signed: true,
            ..
        } if raw.len() == expected => Ok(raw.iter().map(|&b| f64::from(b as i8)).collect()),
        Datatype::FixedPoint {
            size: 2,
            signed: false,
            byte_order,
        } if raw.len() == expected * 2 => Ok(raw
            .as_chunks::<2>()
            .0
            .iter()
            .map(|&bytes| {
                f64::from(match byte_order {
                    ByteOrder::LittleEndian => u16::from_le_bytes(bytes),
                    ByteOrder::BigEndian => u16::from_be_bytes(bytes),
                })
            })
            .collect()),
        Datatype::FixedPoint {
            size: 2,
            signed: true,
            byte_order,
        } if raw.len() == expected * 2 => Ok(raw
            .as_chunks::<2>()
            .0
            .iter()
            .map(|&bytes| {
                f64::from(match byte_order {
                    ByteOrder::LittleEndian => i16::from_le_bytes(bytes),
                    ByteOrder::BigEndian => i16::from_be_bytes(bytes),
                })
            })
            .collect()),
        Datatype::FloatingPoint {
            size: 4,
            byte_order,
        } if raw.len() == expected * 4 => Ok(raw
            .as_chunks::<4>()
            .0
            .iter()
            .map(|&bytes| {
                f64::from(match byte_order {
                    ByteOrder::LittleEndian => f32::from_le_bytes(bytes),
                    ByteOrder::BigEndian => f32::from_be_bytes(bytes),
                })
            })
            .collect()),
        Datatype::FloatingPoint {
            size: 8,
            byte_order,
        } if raw.len() == expected * 8 => Ok(raw
            .as_chunks::<8>()
            .0
            .iter()
            .map(|&bytes| match byte_order {
                ByteOrder::LittleEndian => f64::from_le_bytes(bytes),
                ByteOrder::BigEndian => f64::from_be_bytes(bytes),
            })
            .collect()),
        _ => Err("Unsupported ODIM reflectivity array".into()),
    }
}

pub(super) fn decode(
    bytes: &[u8],
    site: &RadarSite,
    target: OffsetDateTime,
    max_age: time::Duration,
) -> Result<Sweep, String> {
    if bytes.len() > super::MAX_ODIM_BYTES {
        return Err("ODIM file exceeds the byte limit".into());
    }
    let file = Hdf5File::from_bytes_with_options(
        bytes,
        OpenOptions {
            chunk_cache_bytes: 0,
            max_chunk_bytes: 8 * 1024 * 1024,
            max_read_bytes: 8 * 1024 * 1024,
            max_object_header_bytes: 2 * 1024 * 1024,
            ..Default::default()
        },
    )
    .map_err(|e| e.to_string())?;
    let what = file.group("/what").map_err(|e| e.to_string())?;
    let object = text(&what, "object")?;
    let version = text(&what, "version")?;
    let rstart_scale = match version.as_str() {
        // ODIM 2.0 through 2.3 store rstart in kilometres; ODIM 2.4+ uses metres.
        "H5rad 2.0" | "H5rad 2.1" | "H5Rad 2.1" | "H5rad 2.2" | "H5rad 2.3" => 1000.0,
        "H5rad 2.4" | "H5rad 2.41" | "H5rad 2.42" => 1.0,
        _ => return Err("Unsupported ODIM object or version".into()),
    };
    if !matches!(object.as_str(), "PVOL" | "SCAN") {
        return Err("Unsupported ODIM object or version".into());
    }
    let nod = site
        .id
        .rsplit('-')
        .next()
        .ok_or("Invalid OPERA station id")?;
    let source = text(&what, "source")?;
    let mut has_nod = false;
    let mut nod_matches = false;
    let mut has_id = false;
    for part in source.split(',') {
        let part = part.trim();
        if let Some(value) = part.strip_prefix("NOD:") {
            has_nod = true;
            nod_matches |= value.eq_ignore_ascii_case(nod);
        } else if part.starts_with("WMO:") || part.starts_with("RAD:") || part.starts_with("WIGOS:")
        {
            has_id = true;
        }
    }
    if if has_nod { !nod_matches } else { !has_id } {
        return Err("ODIM source identifies another radar".into());
    }
    let where_ = file.group("/where").map_err(|e| e.to_string())?;
    if (number(&where_, "lat")? - site.lat).abs() > 0.02
        || (number(&where_, "lon")? - site.lon).abs() > 0.02
    {
        return Err("ODIM site coordinates differ from the catalogue".into());
    }
    let (groups, _) = file
        .root_group()
        .and_then(|g| g.member_names())
        .map_err(|e| e.to_string())?;
    let mut candidates = Vec::new();
    for name in groups {
        if index(&name, "dataset").is_none() {
            continue;
        }
        let group = file.group(&format!("/{name}")).map_err(|e| e.to_string())?;
        let where_ = group.group("where").map_err(|e| e.to_string())?;
        let angle = number(&where_, "elangle")?;
        let what = group.group("what").map_err(|e| e.to_string())?;
        let start = time(&what, "start")?;
        let end = time(&what, "end")?;
        if !(0.0..=30.0).contains(&angle) || end < start || end > target || end < target - max_age {
            continue;
        }
        let (fields, _) = group.member_names().map_err(|e| e.to_string())?;
        for field in fields {
            if index(&field, "data").is_none() {
                continue;
            }
            let data = group.group(&field).map_err(|e| e.to_string())?;
            if text(&data.group("what").map_err(|e| e.to_string())?, "quantity")? == "DBZH" {
                candidates.push((angle, end, name.clone(), field));
            }
        }
    }
    candidates.sort_by(|a, b| a.0.total_cmp(&b.0).then_with(|| b.1.cmp(&a.1)));
    let (_, end, name, field) = candidates.first().ok_or("No recent ODIM DBZH sweep")?;
    let group = file.group(&format!("/{name}")).map_err(|e| e.to_string())?;
    let geometry = group.group("where").map_err(|e| e.to_string())?;
    let angle = number(&geometry, "elangle")?;
    let nrays = number(&geometry, "nrays")?;
    let nbins = number(&geometry, "nbins")?;
    if nrays.fract() != 0.0
        || nbins.fract() != 0.0
        || !(1.0..=1440.0).contains(&nrays)
        || !(1.0..=4096.0).contains(&nbins)
    {
        return Err("Unsupported ODIM scan dimensions".into());
    }
    let (nrays, nbins) = (nrays as usize, nbins as usize);
    let spacing = number(&geometry, "rscale")?;
    let first = number(&geometry, "rstart")? * rstart_scale + spacing / 2.0;
    if spacing <= 0.0 || first < 0.0 || first + spacing * nbins as f64 > 500_000.0 {
        return Err("Unsupported ODIM range geometry".into());
    }
    let data_group = group.group(field).map_err(|e| e.to_string())?;
    let calibration = data_group.group("what").map_err(|e| e.to_string())?;
    let (gain, offset, nodata, undetect) = (
        number(&calibration, "gain")?,
        number(&calibration, "offset")?,
        number(&calibration, "nodata")?,
        number(&calibration, "undetect")?,
    );
    if gain <= 0.0 || !offset.is_finite() || !nodata.is_finite() || !undetect.is_finite() {
        return Err("Unsupported ODIM reflectivity calibration".into());
    }
    let data = data_group.dataset("data").map_err(|e| e.to_string())?;
    if data.shape() != [nrays as u64, nbins as u64] {
        return Err("Unsupported ODIM reflectivity array".into());
    }
    let pixels = data.read_raw_bytes().map_err(|e| e.to_string())?;
    let samples = read_samples(data.dtype(), &pixels, nrays * nbins)?;
    let how = group.group("how").ok();
    let root_how = (object == "SCAN")
        .then(|| file.group("/how").ok())
        .flatten();
    let attribute = |name: &str| {
        how.as_ref()
            .and_then(|g| g.attribute(name).ok())
            .or_else(|| root_how.as_ref().and_then(|g| g.attribute(name).ok()))
    };
    let read_pair = |start: &str, stop: &str| -> Result<Option<AngleBounds>, String> {
        match (attribute(start), attribute(stop)) {
            (Some(starts), Some(stops)) => {
                let starts = starts.read_1d::<f64>().map_err(|e| e.to_string())?;
                let stops = stops.read_1d::<f64>().map_err(|e| e.to_string())?;
                if starts.len() == 1 && stops.len() == 1 && nrays > 1 {
                    return Ok(None);
                }
                if starts.len() != nrays || stops.len() != nrays {
                    return Err("Invalid ODIM ray angle arrays".into());
                }
                Ok(Some((starts, stops)))
            }
            (None, None) => Ok(None),
            _ => Err("Incomplete ODIM ray angle arrays".into()),
        }
    };
    let mut angles = read_pair("startazA", "stopazA")?;
    if angles.is_none() {
        if let Some(attr) = attribute("azangles").or_else(|| attribute("azangels")) {
            let text = attr.read_string().map_err(|e| e.to_string())?;
            let pairs = text
                .split(',')
                .map(str::trim)
                .filter(|part| !part.is_empty())
                .map(|part| {
                    let (a, b) = part
                        .split_once(':')
                        .ok_or_else(|| "Invalid ODIM azimuth pair".to_owned())?;
                    Ok((
                        a.parse::<f64>().map_err(|e| e.to_string())?,
                        b.parse::<f64>().map_err(|e| e.to_string())?,
                    ))
                })
                .collect::<Result<Vec<_>, String>>()?;
            if pairs.len() != nrays {
                return Err("Invalid ODIM ray angle arrays".into());
            }
            angles = Some(pairs.into_iter().unzip());
        }
    }
    let elevation_bounds = read_pair("startelA", "stopelA")?;
    let elevations = attribute("elangles")
        .map(|a| a.read_1d::<f64>())
        .transpose()
        .map_err(|e| e.to_string())?;
    if elevations
        .as_ref()
        .is_some_and(|values| values.len() != nrays)
    {
        return Err("Invalid ODIM elevation array".into());
    }
    let offset_angle = attribute("astart")
        .map(|a| a.read_as_f64())
        .transpose()
        .map_err(|e| e.to_string())?
        .unwrap_or(0.0);
    let uniform_width = 360.0 / nrays as f64;
    if angles.is_none() && (!offset_angle.is_finite() || offset_angle.abs() > uniform_width / 2.0) {
        return Err("Invalid ODIM azimuth offset".into());
    }
    let mut radials = Vec::with_capacity(nrays);
    for (row, codes) in samples.chunks_exact(nbins).enumerate() {
        let (start, width) = match &angles {
            Some((starts, stops)) => (starts[row], (stops[row] - starts[row]).rem_euclid(360.0)),
            None => (offset_angle + row as f64 * uniform_width, uniform_width),
        };
        if elevation_bounds.as_ref().is_some_and(|(starts, stops)| {
            !starts[row].is_finite()
                || !stops[row].is_finite()
                || !(0.0..=30.0).contains(&starts[row])
                || !(0.0..=30.0).contains(&stops[row])
        }) {
            return Err("Invalid ODIM ray elevations".into());
        }
        let elevation = elevation_bounds.as_ref().map_or_else(
            || elevations.as_ref().map_or(angle, |values| values[row]),
            |(starts, stops)| (starts[row] + stops[row]) / 2.0,
        );
        if !start.is_finite()
            || !width.is_finite()
            || !(0.0..=5.0).contains(&width)
            || width == 0.0
            || !elevation.is_finite()
            || !(0.0..=30.0).contains(&elevation)
        {
            return Err("Invalid ODIM ray geometry".into());
        }
        let values = codes
            .iter()
            .map(|&code| {
                if !code.is_finite() || code == nodata || code == undetect {
                    Ok(f32::NAN)
                } else {
                    let value = (code * gain + offset) as f32;
                    if !value.is_finite() || !(-100.0..=120.0).contains(&value) {
                        Err("Unsupported ODIM reflectivity calibration".to_owned())
                    } else {
                        Ok(value)
                    }
                }
            })
            .collect::<Result<Vec<_>, String>>()?;
        radials.push(Radial {
            azimuth: (start + width / 2.0).rem_euclid(360.0),
            width,
            elevation_angle: Some(elevation),
            first_gate_m: first,
            gate_spacing_m: spacing,
            values,
        });
    }
    radials.sort_by(|a, b| a.azimuth.total_cmp(&b.azimuth));
    Ok(Sweep {
        site_id: site.id.clone(),
        valid_time: end.format(&Rfc3339).map_err(|e| e.to_string())?,
        elevation_angle: angle,
        radials,
    })
}

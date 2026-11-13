//! DWD DX precipitation scans: RVP6 reflectivity, zero runs and per-ray angles.
//!
//! DX is not an ODIM volume. The native samples have 1 km gates and one-degree
//! azimuths; RVP6 converts to dBZ as code / 2 - 32.5. A flagged clutter gate is
//! masked instead of being presented as a trustworthy precipitation return.

use crate::polar_radar::{Radial, Sweep};
use crate::radar_sites::RadarSite;
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

pub(super) fn decode(bytes: &[u8], site: &RadarSite) -> Result<Sweep, String> {
    if bytes.len() > 512 * 1024 {
        return Err("DWD scan exceeds the byte limit".into());
    }
    let end = bytes
        .iter()
        .position(|byte| *byte == 3)
        .ok_or("DWD scan has no header terminator")?;
    let header = std::str::from_utf8(&bytes[..end]).map_err(|_| "DWD scan header is not ASCII")?;
    if header.len() < 20 || !header.is_ascii() || !header.starts_with("DX") {
        return Err("Invalid DWD DX header".into());
    }
    if Some(&header[8..13]) != site.wmo.as_deref() {
        return Err("DWD scan belongs to another station".into());
    }
    let declared: usize = header
        .split_once("BY")
        .and_then(|(_, rest)| rest.split_once("VS"))
        .ok_or("DWD scan has no byte count")?
        .0
        .trim()
        .parse()
        .map_err(|_| "Invalid DWD scan byte count")?;
    if declared.abs_diff(bytes.len()) > 1 {
        return Err("DWD scan byte count does not match".into());
    }
    let valid_time = format!(
        "20{}-{}-{}T{}:{}:00Z",
        &header[15..17],
        &header[13..15],
        &header[2..4],
        &header[4..6],
        &header[6..8]
    );
    OffsetDateTime::parse(&valid_time, &Rfc3339).map_err(|_| "Invalid DWD scan time")?;
    let mut start = end + 1;
    while bytes.get(start) == Some(&3) {
        start += 1;
    }
    let mut data = &bytes[start..];
    if data.len() % 2 == 1 && matches!(data.last(), Some(3 | 4)) {
        data = &data[..data.len() - 1];
    }
    let (words, remainder) = data.as_chunks::<2>();
    if !remainder.is_empty() {
        return Err("Truncated DWD scan word".into());
    }
    let words = words
        .iter()
        .map(|word| u16::from_le_bytes(*word))
        .collect::<Vec<_>>();
    if words.first() != Some(&0x2000) {
        return Err("DWD scan has no first ray".into());
    }
    let starts = words
        .iter()
        .enumerate()
        .filter_map(|(index, word)| (*word == 0x2000).then_some(index))
        .collect::<Vec<_>>();
    if starts.is_empty() || starts.len() > 720 {
        return Err("Invalid DWD ray count".into());
    }
    let mut radials = Vec::with_capacity(starts.len());
    for (index, &start) in starts.iter().enumerate() {
        let stop = starts.get(index + 1).copied().unwrap_or(words.len());
        let ray = words.get(start..stop).ok_or("Truncated DWD ray")?;
        if ray.len() < 3 {
            return Err("Truncated DWD ray header".into());
        }
        let azimuth = f64::from(ray[1] & 0x0fff) / 10.0;
        let elevation = f64::from(ray[2] & 0x0fff) / 10.0;
        if azimuth > 360.0 || elevation > 30.0 {
            return Err("Invalid DWD ray angles".into());
        }
        let mut values = Vec::with_capacity(128);
        for &word in &ray[3..] {
            if word & 0x1000 != 0 {
                let count = usize::from(word & 0x0fff);
                if count == 0 || values.len() + count > 128 {
                    return Err("Invalid DWD zero run".into());
                }
                values.resize(values.len() + count, -32.5);
            } else {
                let code = word & 0x1fff;
                if code > 255 || values.len() == 128 {
                    return Err("Invalid DWD reflectivity gate".into());
                }
                values.push(if word & 0x8000 != 0 {
                    f32::NAN
                } else {
                    f32::from(code) * 0.5 - 32.5
                });
            }
        }
        if values.len() != 128 {
            return Err("DWD ray does not contain 128 gates".into());
        }
        radials.push(Radial {
            azimuth: azimuth.rem_euclid(360.0),
            width: 1.0,
            elevation_angle: Some(elevation),
            first_gate_m: 500.0,
            gate_spacing_m: 1000.0,
            values,
        });
    }
    radials.sort_by(|a, b| a.azimuth.total_cmp(&b.azimuth));
    radials.dedup_by(|a, b| (a.azimuth - b.azimuth).abs() < 0.05);
    let elevation_angle = radials
        .iter()
        .filter_map(|ray| ray.elevation_angle)
        .fold(f64::INFINITY, f64::min);
    Ok(Sweep {
        site_id: site.id.clone(),
        valid_time,
        elevation_angle,
        radials,
    })
}

//! WGS84 sampling keeps the source gates and missing azimuths intact.

use super::{decode::Sweep, Bounds, Palette};
use crate::radar_sites::RadarSite;
use geographiclib_rs::{DirectGeodesic, Geodesic, InverseGeodesic};

#[cfg(not(test))]
const PIXELS: usize = 1024;
#[cfg(test)]
const PIXELS: usize = 64;

pub(super) fn render(
    sweep: &Sweep,
    site: &RadarSite,
    palette: &Palette,
) -> Result<(Bounds, Vec<u8>), String> {
    let mut values = sweep
        .radials
        .iter()
        .flat_map(|radial| radial.values.iter())
        .copied()
        .filter(|value| value.is_finite())
        .collect::<Vec<_>>();
    values.sort_unstable_by(f32::total_cmp);
    values.dedup();
    if values.len() > 254 {
        return Err("Radar sweep exceeds indexed gate precision.".into());
    }
    let factor = if palette.units == "mph" {
        3600.0 / 1609.344
    } else {
        1.0
    };
    let mut lookup = vec![None, None];
    lookup.extend(values.iter().map(|&value| Some(f64::from(value) * factor)));
    lookup.resize(256, None);
    let mut rgb = vec![0_u8; 768];
    let mut alpha = vec![0_u8; 256];
    let folded = (palette.units == "mph").then_some(1);
    if folded.is_some() {
        rgb[3..6].copy_from_slice(&[128, 0, 128]);
        alpha[1] = 255;
    }
    for (index, value) in values.iter().enumerate() {
        rgb[(index + 2) * 3..(index + 3) * 3]
            .copy_from_slice(&colour(f64::from(*value) * factor, palette)?);
        alpha[index + 2] = 255;
    }
    let codes = sweep
        .radials
        .iter()
        .map(|radial| {
            radial
                .values
                .iter()
                .map(|value| {
                    if *value == f32::INFINITY && folded.is_some() {
                        1
                    } else if !value.is_finite() {
                        0
                    } else {
                        (values
                            .binary_search_by(|entry| entry.total_cmp(value))
                            .expect("collected source gate")
                            + 2) as u8
                    }
                })
                .collect::<Vec<_>>()
        })
        .collect::<Vec<_>>();
    let radius = sweep
        .radials
        .iter()
        .map(|radial| {
            radial.first_gate_m + radial.gate_spacing_m * (radial.values.len() as f64 - 0.5)
        })
        .fold(0.0, f64::max);
    if !(0.0..=500_000.0).contains(&radius) || radius == 0.0 {
        return Err("Unsupported radar gate range.".into());
    }
    let earth = Geodesic::wgs84();
    let (north, _): (f64, f64) = earth.direct(site.lat, site.lon, 0.0, radius);
    let (south, _): (f64, f64) = earth.direct(site.lat, site.lon, 180.0, radius);
    // A conservative extent includes the circle's poleward edge.
    let longitude_span =
        (radius / 6_350_000.0).to_degrees() / north.abs().max(south.abs()).to_radians().cos();
    let bounds = Bounds {
        west: site.lon - longitude_span,
        east: site.lon + longitude_span,
        south,
        north,
    };
    if bounds.west < -180.0 || bounds.east > 180.0 {
        return Err("Radar extent crosses the antimeridian.".into());
    }
    let mut azimuths = sweep
        .radials
        .iter()
        .enumerate()
        .map(|(index, radial)| {
            (
                (radial.azimuth - radial.width / 2.0).rem_euclid(360.0),
                index,
            )
        })
        .collect::<Vec<_>>();
    azimuths.sort_unstable_by(|a, b| a.0.total_cmp(&b.0));
    let mut pixels = vec![0_u8; PIXELS * PIXELS];
    for (row, output) in pixels.as_chunks_mut::<PIXELS>().0.iter_mut().enumerate() {
        let latitude =
            bounds.north - (row as f64 + 0.5) / PIXELS as f64 * (bounds.north - bounds.south);
        for (column, output) in output.iter_mut().enumerate() {
            let longitude =
                bounds.west + (column as f64 + 0.5) / PIXELS as f64 * (bounds.east - bounds.west);
            let (distance, bearing, _, _): (f64, f64, f64, f64) =
                earth.inverse(site.lat, site.lon, latitude, longitude);
            let bearing = bearing.rem_euclid(360.0);
            let index = azimuths.partition_point(|(start, _)| *start <= bearing);
            let &(start, radial_index) = &azimuths[if index == 0 {
                azimuths.len() - 1
            } else {
                index - 1
            }];
            let radial = &sweep.radials[radial_index];
            let gate = ((distance - radial.first_gate_m) / radial.gate_spacing_m + 0.5).floor();
            if (bearing - start).rem_euclid(360.0) < radial.width && gate >= 0.0 {
                *output = codes[radial_index].get(gate as usize).copied().unwrap_or(0);
            }
        }
    }
    let mut png = Vec::new();
    let mut encoder = png::Encoder::new(&mut png, PIXELS as u32, PIXELS as u32);
    encoder.set_color(png::ColorType::Indexed);
    encoder.set_depth(png::BitDepth::Eight);
    encoder.set_palette(rgb);
    encoder.set_trns(alpha);
    encoder.set_compression(png::Compression::Fast);
    encoder
        .add_text_chunk(
            "sharppy-radar".into(),
            serde_json::json!({ "values": lookup, "folded": folded, "lowerBounds": [] })
                .to_string(),
        )
        .map_err(|error| error.to_string())?;
    let mut writer = encoder.write_header().map_err(|error| error.to_string())?;
    writer
        .write_image_data(&pixels)
        .map_err(|error| error.to_string())?;
    writer.finish().map_err(|error| error.to_string())?;
    Ok((bounds, png))
}

fn colour(value: f64, palette: &Palette) -> Result<[u8; 3], String> {
    let high = palette
        .stops
        .partition_point(|(stop, _)| *stop <= value)
        .min(palette.stops.len() - 1);
    let low = high.saturating_sub(1);
    let (a, a_hex) = &palette.stops[low];
    let (b, b_hex) = &palette.stops[high];
    let fraction = if b > a {
        ((value - a) / (b - a)).clamp(0.0, 1.0)
    } else {
        0.0
    };
    let mut colour = [0; 3];
    for (channel, output) in colour.iter_mut().enumerate() {
        let first = 1 + channel * 2;
        let a =
            u8::from_str_radix(&a_hex[first..first + 2], 16).map_err(|error| error.to_string())?;
        let b =
            u8::from_str_radix(&b_hex[first..first + 2], 16).map_err(|error| error.to_string())?;
        *output = (f64::from(a) + fraction * (f64::from(b) - f64::from(a))) as u8;
    }
    Ok(colour)
}

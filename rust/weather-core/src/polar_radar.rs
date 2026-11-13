//! WGS84 sampling keeps the source gates and missing azimuths intact.

use crate::radar_sites::RadarSite;
use base64::{engine::general_purpose::STANDARD, Engine};
use geographiclib_rs::{DirectGeodesic, Geodesic, InverseGeodesic};
use serde::{Deserialize, Serialize};

#[derive(Clone, Serialize, Deserialize)]
pub struct Palette {
    pub stops: Vec<(f64, String)>,
    pub units: String,
    pub display: String,
    pub stepped: bool,
    pub floor: Option<f64>,
    pub ceiling: Option<f64>,
    pub ticks: Vec<f64>,
}

#[derive(Clone, Copy, Serialize)]
pub struct Bounds {
    pub west: f64,
    pub east: f64,
    pub south: f64,
    pub north: f64,
}

#[derive(Debug)]
pub(crate) struct Sweep {
    pub site_id: String,
    pub valid_time: String,
    pub elevation_angle: f64,
    pub radials: Vec<Radial>,
}

#[derive(Debug)]
pub(crate) struct Radial {
    /// Centre angle, clockwise from north, in degrees.
    pub azimuth: f64,
    /// Angular sample width; missing sectors are never widened.
    pub width: f64,
    /// Elevation for terrain-following scans; None retains existing ground-range sampling.
    pub elevation_angle: Option<f64>,
    /// Distance to the centre of the first gate, in metres.
    pub first_gate_m: f64,
    pub gate_spacing_m: f64,
    /// Physical values; NaN is missing and positive infinity is range folded.
    pub values: Vec<f32>,
}

#[cfg(not(test))]
const PIXELS: usize = 1024;
#[cfg(test)]
const PIXELS: usize = 64;

pub(crate) fn render(
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
    if values.len() > 65_534 {
        return Err("Radar sweep exceeds indexed gate precision.".into());
    }
    let wide = values.len() > 254;
    let factor = if palette.units == "mph" {
        3600.0 / 1609.344
    } else {
        1.0
    };
    let mut lookup = vec![None, None];
    lookup.extend(values.iter().map(|&value| Some(f64::from(value) * factor)));
    if !wide {
        lookup.resize(256, None);
    }
    let mut rgb = vec![0_u8; 768];
    let mut alpha = vec![0_u8; 256];
    let folded = (palette.units == "mph").then_some(1);
    if folded.is_some() {
        rgb[3..6].copy_from_slice(&[128, 0, 128]);
        alpha[1] = 255;
    }
    let opaque_start = values.partition_point(|&value| {
        palette
            .floor
            .is_some_and(|floor| f64::from(value) * factor < floor)
    });
    if !wide {
        for (index, value) in values.iter().enumerate() {
            rgb[(index + 2) * 3..(index + 3) * 3]
                .copy_from_slice(&colour(f64::from(*value) * factor, palette)?);
            alpha[index + 2] = if index < opaque_start { 0 } else { 255 };
        }
    } else {
        let opaque = &values[opaque_start..];
        let slots = opaque.len().min(253);
        for slot in 0..slots {
            let sample = opaque[slot * (opaque.len() - 1) / (slots - 1).max(1)];
            rgb[(slot + 3) * 3..(slot + 4) * 3]
                .copy_from_slice(&colour(f64::from(sample) * factor, palette)?);
            alpha[slot + 3] = 255;
        }
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
                        (1_u8, 1_u16)
                    } else if !value.is_finite() {
                        (0_u8, 0_u16)
                    } else {
                        let index = values
                            .binary_search_by(|entry| entry.total_cmp(value))
                            .expect("collected source gate");
                        let code = (index + 2) as u16;
                        let visual = if !wide {
                            code as u8
                        } else if index < opaque_start {
                            2
                        } else {
                            let opaque = values.len() - opaque_start;
                            let slots = opaque.min(253);
                            (3 + (index - opaque_start) * (slots - 1) / (opaque - 1).max(1)) as u8
                        };
                        (visual, code)
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
    let mut grid = vec![0_u16; if wide { PIXELS * PIXELS } else { 0 }];
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
            // Convert ground distance back to slant range for a 4/3-Earth beam.
            // The inverse geodesic above supplies the ground azimuth and distance.
            let range = if let Some(elevation) = radial.elevation_angle {
                let earth_radius = 6_371_000.0 * 4.0 / 3.0;
                let arc = distance / earth_radius;
                earth_radius * arc.sin() / (elevation.to_radians() + arc).cos()
            } else {
                distance
            };
            let gate = ((range - radial.first_gate_m) / radial.gate_spacing_m + 0.5).floor();
            if (bearing - start).rem_euclid(360.0) < radial.width && gate >= 0.0 {
                let (visual, code) = codes[radial_index]
                    .get(gate as usize)
                    .copied()
                    .unwrap_or((0, 0));
                *output = visual;
                if wide {
                    grid[row * PIXELS + column] = code;
                }
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
    if wide {
        let bytes = grid
            .iter()
            .flat_map(|code| code.to_le_bytes())
            .collect::<Vec<_>>();
        encoder
            .add_ztxt_chunk(
                "sharppy-radar".into(),
                serde_json::json!({
                    "values": lookup,
                    "folded": folded,
                    "lowerBounds": [],
                    "codes": STANDARD.encode(bytes),
                })
                .to_string(),
            )
            .map_err(|error| error.to_string())?;
    } else {
        encoder
            .add_text_chunk(
                "sharppy-radar".into(),
                serde_json::json!({ "values": lookup, "folded": folded, "lowerBounds": [] })
                    .to_string(),
            )
            .map_err(|error| error.to_string())?;
    }
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

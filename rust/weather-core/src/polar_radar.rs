//! WGS84 sampling keeps the source gates and missing azimuths intact.

use crate::radar_sites::RadarSite;
use base64::{engine::general_purpose::STANDARD, Engine};
use geographiclib_rs::{DirectGeodesic, Geodesic, InverseGeodesic};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::sync::{Arc, Mutex, OnceLock, PoisonError};

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

#[derive(Debug)]
pub(crate) struct IndexedRadial {
    pub azimuth: f64,
    pub width: f64,
    pub first_gate_m: f64,
    pub gate_spacing_m: f64,
    pub codes: Vec<u8>,
}

pub(crate) struct IndexedPalette<'a> {
    pub rgb: &'a [u8; 768],
    pub alpha: &'a [u8; 256],
    pub lookup: &'a [Option<f64>],
    pub folded: Option<u8>,
    pub lower_bounds: &'a [usize],
}

const MAX_PIXELS: usize = 4096;

#[cfg(not(test))]
const PIXELS: usize = 1024;
#[cfg(test)]
const PIXELS: usize = 64;

#[derive(Clone, Copy)]
pub(crate) struct RenderOptions {
    pub pixels: usize,
    pub bounds: Option<Bounds>,
    pub folded_rgb: Option<[u8; 3]>,
}

impl Default for RenderOptions {
    fn default() -> Self {
        Self {
            pixels: PIXELS,
            bounds: None,
            folded_rgb: None,
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
struct ProjectionKey {
    site_lat: u64,
    site_lon: u64,
    west: u64,
    east: u64,
    south: u64,
    north: u64,
    width: usize,
    height: usize,
}

struct Projection {
    bearings: Vec<f32>,
    ranges: Vec<f32>,
}

// Retain two reusable geometries within the previous maximum of one square frame.
const PROJECTION_CACHE_BYTES: usize = MAX_PIXELS * MAX_PIXELS * 2 * std::mem::size_of::<f32>();
const PROJECTION_CACHE_ENTRIES: usize = 2;

impl Projection {
    fn bytes(&self) -> usize {
        (self.bearings.capacity() + self.ranges.capacity()) * std::mem::size_of::<f32>()
    }
}

#[derive(Default)]
struct ProjectionCache {
    entries: VecDeque<(ProjectionKey, Arc<Projection>)>,
}

impl ProjectionCache {
    fn get(&mut self, key: ProjectionKey) -> Option<Arc<Projection>> {
        let index = self.entries.iter().position(|(cached, _)| *cached == key)?;
        let entry = self.entries.remove(index).expect("projection in cache");
        let projection = Arc::clone(&entry.1);
        self.entries.push_back(entry);
        Some(projection)
    }

    fn insert(&mut self, key: ProjectionKey, projection: Arc<Projection>) {
        let bytes = projection.bytes();
        if bytes > PROJECTION_CACHE_BYTES {
            return;
        }
        self.entries.retain(|(cached, _)| *cached != key);
        while self.entries.len() >= PROJECTION_CACHE_ENTRIES
            || self
                .entries
                .iter()
                .map(|(_, cached)| cached.bytes())
                .sum::<usize>()
                > PROJECTION_CACHE_BYTES - bytes
        {
            self.entries.pop_front();
        }
        self.entries.push_back((key, projection));
    }

    fn clear(&mut self) {
        self.entries.clear();
    }
}

fn projection_cache() -> &'static Mutex<ProjectionCache> {
    static CACHE: OnceLock<Mutex<ProjectionCache>> = OnceLock::new();
    CACHE.get_or_init(Mutex::default)
}

pub(crate) fn clear_cache() {
    projection_cache()
        .lock()
        .unwrap_or_else(PoisonError::into_inner)
        .clear();
}

fn projection(site: &RadarSite, bounds: Bounds, width: usize, height: usize) -> Arc<Projection> {
    let key = ProjectionKey {
        site_lat: site.lat.to_bits(),
        site_lon: site.lon.to_bits(),
        west: bounds.west.to_bits(),
        east: bounds.east.to_bits(),
        south: bounds.south.to_bits(),
        north: bounds.north.to_bits(),
        width,
        height,
    };
    if let Some(cached) = projection_cache()
        .lock()
        .unwrap_or_else(PoisonError::into_inner)
        .get(key)
    {
        return cached;
    }
    let total = width * height;
    let mut bearings = vec![0_f32; total];
    let mut ranges = vec![0_f32; total];
    let workers = if total >= 65_536 {
        std::thread::available_parallelism()
            .map(|n| n.get().clamp(1, 4))
            .unwrap_or(1)
    } else {
        1
    };
    let rows_per_chunk = height.div_ceil(workers).max(1);
    let lat_step = (bounds.north - bounds.south) / height as f64;
    let lon_step = (bounds.east - bounds.west) / width as f64;
    std::thread::scope(|scope| {
        let mut bearing_chunks = bearings.chunks_mut(rows_per_chunk * width);
        let mut range_chunks = ranges.chunks_mut(rows_per_chunk * width);
        let mut first_row = 0;
        while let (Some(b_chunk), Some(r_chunk)) = (bearing_chunks.next(), range_chunks.next()) {
            let chunk_rows = b_chunk.len() / width;
            scope.spawn(move || {
                let earth = Geodesic::wgs84();
                for local_row in 0..chunk_rows {
                    let row = first_row + local_row;
                    let latitude = bounds.north - (row as f64 + 0.5) * lat_step;
                    let row_start = local_row * width;
                    for column in 0..width {
                        let longitude = bounds.west + (column as f64 + 0.5) * lon_step;
                        let (distance, bearing, _, _): (f64, f64, f64, f64) =
                            earth.inverse(site.lat, site.lon, latitude, longitude);
                        b_chunk[row_start + column] = bearing.rem_euclid(360.0) as f32;
                        r_chunk[row_start + column] = distance as f32;
                    }
                }
            });
            first_row += chunk_rows;
        }
    });
    let built = Arc::new(Projection { bearings, ranges });
    projection_cache()
        .lock()
        .unwrap_or_else(PoisonError::into_inner)
        .insert(key, Arc::clone(&built));
    built
}

pub(crate) fn render(
    sweep: &Sweep,
    site: &RadarSite,
    palette: &Palette,
) -> Result<(Bounds, Vec<u8>), String> {
    render_with_options(
        sweep,
        site,
        palette,
        RenderOptions {
            folded_rgb: (palette.units == "mph").then_some([128, 0, 128]),
            ..RenderOptions::default()
        },
    )
}

pub(crate) fn render_with_options(
    sweep: &Sweep,
    site: &RadarSite,
    palette: &Palette,
    options: RenderOptions,
) -> Result<(Bounds, Vec<u8>), String> {
    if options.pixels == 0 || options.pixels > MAX_PIXELS {
        return Err("Unsupported radar output resolution.".into());
    }
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
    let folded = options.folded_rgb.map(|_| 1);
    if let Some(folded_rgb) = options.folded_rgb {
        rgb[3..6].copy_from_slice(&folded_rgb);
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
    let (bounds, width, height) = if let Some(bounds) = options.bounds {
        if !bounds.west.is_finite()
            || !bounds.east.is_finite()
            || !bounds.south.is_finite()
            || !bounds.north.is_finite()
            || bounds.west >= bounds.east
            || bounds.south >= bounds.north
            || bounds.west < -180.0
            || bounds.east > 180.0
            || bounds.south < -90.0
            || bounds.north > 90.0
        {
            return Err("Radar bounds are out of range.".into());
        }
        let lon_span = (bounds.east - bounds.west).max(1e-6);
        let lat_span = (bounds.north - bounds.south).max(1e-6);
        let min_edge = options.pixels.min(256);
        let (width, height) = if lon_span >= lat_span {
            (
                options.pixels,
                ((options.pixels as f64 * lat_span / lon_span).round() as usize).max(min_edge),
            )
        } else {
            (
                ((options.pixels as f64 * lon_span / lat_span).round() as usize).max(min_edge),
                options.pixels,
            )
        };
        (bounds, width, height)
    } else {
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
        (bounds, options.pixels, options.pixels)
    };
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
    let proj = projection(site, bounds, width, height);
    let total = width * height;
    let mut pixels = vec![0_u8; total];
    let mut grid = vec![0_u16; if wide { total } else { 0 }];
    let workers = if total >= 262_144 {
        std::thread::available_parallelism()
            .map(|n| n.get().clamp(1, 4))
            .unwrap_or(1)
    } else {
        1
    };
    let chunk_len = total.div_ceil(workers).max(1);
    let radials = &sweep.radials;
    let azimuths_ref = &azimuths;
    let codes_ref = &codes;
    std::thread::scope(|scope| {
        let mut pixel_chunks = pixels.chunks_mut(chunk_len);
        let mut bearing_chunks = proj.bearings.chunks(chunk_len);
        let mut range_chunks = proj.ranges.chunks(chunk_len);
        let mut grid_chunks = if wide {
            Some(grid.chunks_mut(chunk_len))
        } else {
            None
        };
        while let (Some(p_chunk), Some(b_chunk), Some(d_chunk)) = (
            pixel_chunks.next(),
            bearing_chunks.next(),
            range_chunks.next(),
        ) {
            let mut g_chunk = grid_chunks.as_mut().and_then(|iter| iter.next());
            scope.spawn(move || {
                for (idx, ((&bearing, &distance), output)) in b_chunk
                    .iter()
                    .zip(d_chunk.iter())
                    .zip(p_chunk.iter_mut())
                    .enumerate()
                {
                    let bearing = f64::from(bearing);
                    let distance = f64::from(distance);
                    let index = azimuths_ref.partition_point(|(start, _)| *start <= bearing);
                    let &(start, radial_index) = &azimuths_ref[if index == 0 {
                        azimuths_ref.len() - 1
                    } else {
                        index - 1
                    }];
                    let radial = &radials[radial_index];
                    // Convert ground distance back to slant range for a 4/3-Earth beam.
                    // The inverse geodesic above supplies the ground azimuth and distance.
                    let range = if let Some(elevation) = radial.elevation_angle {
                        let earth_radius = 6_371_000.0 * 4.0 / 3.0;
                        let arc = distance / earth_radius;
                        earth_radius * arc.sin() / (elevation.to_radians() + arc).cos()
                    } else {
                        distance
                    };
                    let gate =
                        ((range - radial.first_gate_m) / radial.gate_spacing_m + 0.5).floor();
                    if (bearing - start).rem_euclid(360.0) < radial.width && gate >= 0.0 {
                        let (visual, code) = codes_ref[radial_index]
                            .get(gate as usize)
                            .copied()
                            .unwrap_or((0, 0));
                        *output = visual;
                        if let Some(ref mut g) = g_chunk {
                            g[idx] = code;
                        }
                    }
                }
            });
        }
    });
    let mut png = Vec::new();
    let mut encoder = png::Encoder::new(&mut png, width as u32, height as u32);
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

pub(crate) fn render_indexed_256(
    radials: &[IndexedRadial],
    site: &RadarSite,
    bounds: Bounds,
    pixels: usize,
    palette: IndexedPalette<'_>,
) -> Result<(Bounds, Vec<u8>), String> {
    if pixels == 0 || pixels > MAX_PIXELS {
        return Err("Unsupported radar output resolution.".into());
    }
    if !bounds.west.is_finite()
        || !bounds.east.is_finite()
        || !bounds.south.is_finite()
        || !bounds.north.is_finite()
        || bounds.west >= bounds.east
        || bounds.south >= bounds.north
        || bounds.west < -180.0
        || bounds.east > 180.0
        || bounds.south < -90.0
        || bounds.north > 90.0
    {
        return Err("Radar bounds are out of range.".into());
    }
    let lon_span = (bounds.east - bounds.west).max(1e-6);
    let lat_span = (bounds.north - bounds.south).max(1e-6);
    let min_edge = pixels.min(256);
    let (width, height) = if lon_span >= lat_span {
        (
            pixels,
            ((pixels as f64 * lat_span / lon_span).round() as usize).max(min_edge),
        )
    } else {
        (
            ((pixels as f64 * lon_span / lat_span).round() as usize).max(min_edge),
            pixels,
        )
    };
    let mut azimuths = radials
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
    let proj = projection(site, bounds, width, height);
    let total = width * height;
    let mut out_pixels = vec![0_u8; total];
    let workers = if total >= 262_144 {
        std::thread::available_parallelism()
            .map(|n| n.get().clamp(1, 4))
            .unwrap_or(1)
    } else {
        1
    };
    let chunk_len = total.div_ceil(workers).max(1);
    let azimuths_ref = &azimuths;
    std::thread::scope(|scope| {
        let mut pixel_chunks = out_pixels.chunks_mut(chunk_len);
        let mut bearing_chunks = proj.bearings.chunks(chunk_len);
        let mut range_chunks = proj.ranges.chunks(chunk_len);
        while let (Some(p_chunk), Some(b_chunk), Some(d_chunk)) = (
            pixel_chunks.next(),
            bearing_chunks.next(),
            range_chunks.next(),
        ) {
            scope.spawn(move || {
                for ((&bearing, &distance), output) in
                    b_chunk.iter().zip(d_chunk.iter()).zip(p_chunk.iter_mut())
                {
                    let bearing = f64::from(bearing);
                    let distance = f64::from(distance);
                    let index = azimuths_ref.partition_point(|(start, _)| *start <= bearing);
                    let &(start, radial_index) = &azimuths_ref[if index == 0 {
                        azimuths_ref.len() - 1
                    } else {
                        index - 1
                    }];
                    let radial = &radials[radial_index];
                    let gate =
                        ((distance - radial.first_gate_m) / radial.gate_spacing_m + 0.5).floor();
                    if (bearing - start).rem_euclid(360.0) < radial.width && gate >= 0.0 {
                        *output = radial.codes.get(gate as usize).copied().unwrap_or(0);
                    }
                }
            });
        }
    });
    let mut png = Vec::new();
    let mut encoder = png::Encoder::new(&mut png, width as u32, height as u32);
    encoder.set_color(png::ColorType::Indexed);
    encoder.set_depth(png::BitDepth::Eight);
    encoder.set_palette(palette.rgb.as_slice());
    encoder.set_trns(palette.alpha.as_slice());
    encoder.set_compression(png::Compression::Fast);
    encoder
        .add_text_chunk(
            "sharppy-radar".into(),
            serde_json::json!({
                "values": palette.lookup,
                "folded": palette.folded,
                "lowerBounds": palette.lower_bounds,
            })
            .to_string(),
        )
        .map_err(|error| error.to_string())?;
    let mut writer = encoder.write_header().map_err(|error| error.to_string())?;
    writer
        .write_image_data(&out_pixels)
        .map_err(|error| error.to_string())?;
    writer.finish().map_err(|error| error.to_string())?;
    Ok((bounds, png))
}

pub(crate) fn colour(value: f64, palette: &Palette) -> Result<[u8; 3], String> {
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

#[cfg(test)]
mod projection_cache_tests {
    use super::*;

    fn key(pixels: usize) -> ProjectionKey {
        ProjectionKey {
            site_lat: 35.0_f64.to_bits(),
            site_lon: (-97.0_f64).to_bits(),
            west: (-102.0_f64).to_bits(),
            east: (-92.0_f64).to_bits(),
            south: 30.0_f64.to_bits(),
            north: 40.0_f64.to_bits(),
            width: pixels,
            height: pixels,
        }
    }

    fn buffers(pixels: usize) -> Arc<Projection> {
        Arc::new(Projection {
            bearings: Vec::with_capacity(pixels * pixels),
            ranges: Vec::with_capacity(pixels * pixels),
        })
    }

    #[test]
    fn retains_exact_geometries_and_evicts_the_least_recent() {
        let mut cache = ProjectionCache::default();
        let a = key(32);
        let b = key(64);
        let c = ProjectionKey {
            site_lat: a.site_lat + 1,
            ..a
        };
        let existing_a = buffers(a.width);
        cache.insert(a, Arc::clone(&existing_a));
        cache.insert(b, buffers(b.width));

        assert!(cache.get(c).is_none());
        assert!(Arc::ptr_eq(&cache.get(a).unwrap(), &existing_a));

        cache.insert(c, buffers(c.width));
        assert!(cache.get(b).is_none());
        assert!(Arc::ptr_eq(&cache.get(a).unwrap(), &existing_a));

        let new_c = buffers(c.width);
        cache.insert(c, Arc::clone(&new_c));
        assert!(Arc::ptr_eq(&cache.get(c).unwrap(), &new_c));
        assert!(cache.get(a).is_some());
        assert_eq!(cache.entries.len(), 2);
    }

    #[test]
    fn enforces_the_previous_maximum_and_releases_entries_on_clear() {
        let mut cache = ProjectionCache::default();
        let small = key(32);
        let large = key(MAX_PIXELS);
        let largest = buffers(MAX_PIXELS);
        assert_eq!(largest.bytes(), PROJECTION_CACHE_BYTES);

        cache.insert(small, buffers(small.width));
        cache.insert(large, Arc::clone(&largest));
        assert!(cache.get(small).is_none());
        assert_eq!(cache.entries.len(), 1);

        cache.insert(small, buffers(small.width));
        assert!(cache.get(large).is_none());
        assert_eq!(Arc::strong_count(&largest), 1);
        cache.clear();
        assert!(cache.entries.is_empty());
    }
}

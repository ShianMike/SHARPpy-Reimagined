//! GOES ABI-L2-CMIPC S3 discovery, NetCDF4/HDF5 decoding, and fixed-grid PNG rendering.

use crate::format_rfc3339_utc;
use crate::polar_radar::Bounds;
use hdf5_reader::{ByteOrder, Datatype, Hdf5File, OpenOptions};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::OnceLock;
use std::time::Duration;
use time::{Date, OffsetDateTime, PrimitiveDateTime, Time, UtcOffset};

pub const FRAME_BOUNDS: Bounds = Bounds {
    west: -134.5,
    east: -60.5,
    south: 20.5,
    north: 53.0,
};
pub const MAX_OUTPUT_PIXELS: usize = 4_000_000;
pub const MAX_SOURCE_BYTES: usize = 96 * 1024 * 1024;
const MAX_LISTING_BYTES: usize = 4 * 1024 * 1024;
const DEFAULT_TIME_TOLERANCE: time::Duration = time::Duration::minutes(20);
pub const ATTRIBUTION: &str = "NOAA/NESDIS GOES · NOAA Open Data Dissemination";

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SatelliteCandidate {
    pub satellite: String,
    pub channel: String,
    pub scan_start: String,
    pub key: String,
    pub url: String,
    pub size_bytes: Option<usize>,
    #[serde(skip)]
    pub scan_unix: i64,
    #[serde(skip)]
    pub(crate) scan_dt: Option<OffsetDateTime>,
}

pub struct SatelliteFrame {
    pub product: String,
    pub satellite: String,
    pub label: String,
    pub bounds: Bounds,
    pub requested_time: String,
    pub retrieved_at: String,
    pub valid_time: String,
    pub update_interval_s: f64,
    pub coverage_fraction: f64,
    pub native_resolution_km: f64,
    pub attribution: String,
    pub source_url: String,
    pub png_bytes: Vec<u8>,
}

fn bucket_url(satellite: &str) -> Option<&'static str> {
    match satellite {
        "G18" => Some("https://noaa-goes18.s3.amazonaws.com"),
        "G19" => Some("https://noaa-goes19.s3.amazonaws.com"),
        _ => None,
    }
}

fn channel_spec(channel: &str) -> Option<(u8, &'static str, f64)> {
    match channel {
        "visible" => Some((2, "Visible (0.64 µm)", 0.5)),
        "infrared" => Some((13, "Clean IR (10.3 µm)", 2.0)),
        _ => None,
    }
}

pub fn choose_satellite(bounds: Bounds) -> &'static str {
    let centre = (bounds.west + bounds.east) * 0.5;
    if centre < -114.0 {
        "G18"
    } else {
        "G19"
    }
}

fn xml_tag<'a>(xml: &'a str, tag: &str) -> Option<&'a str> {
    let open = format!("<{tag}>");
    let close = format!("</{tag}>");
    let (_, rest) = xml.split_once(&open)?;
    let (value, _) = rest.split_once(&close)?;
    Some(value)
}

fn parse_abi_channel(key: &str) -> Option<u8> {
    let bytes = key.as_bytes();
    for i in 0..bytes.len().saturating_sub(4) {
        if bytes[i] == b'M' {
            let mut j = i + 1;
            while j < bytes.len() && bytes[j].is_ascii_digit() {
                j += 1;
            }
            if j > i + 1
                && j + 2 < bytes.len()
                && bytes[j] == b'C'
                && bytes[j + 1].is_ascii_digit()
                && bytes[j + 2].is_ascii_digit()
            {
                if let Ok(ch) = key[j + 1..j + 3].parse::<u8>() {
                    return Some(ch);
                }
            }
        }
    }
    None
}

fn parse_abi_scan_start(key: &str) -> Option<OffsetDateTime> {
    let bytes = key.as_bytes();
    for i in 0..bytes.len().saturating_sub(15) {
        if &bytes[i..i + 2] == b"_s" && bytes[i + 2..i + 15].iter().all(u8::is_ascii_digit) {
            let s = &key[i + 2..i + 15];
            let year: i32 = s[0..4].parse().ok()?;
            let ordinal: u16 = s[4..7].parse().ok()?;
            let hour: u8 = s[7..9].parse().ok()?;
            let minute: u8 = s[9..11].parse().ok()?;
            let second: u8 = s[11..13].parse().ok()?;
            let date = Date::from_ordinal_date(year, ordinal).ok()?;
            let time = Time::from_hms(hour, minute, second).ok()?;
            return Some(PrimitiveDateTime::new(date, time).assume_utc());
        }
    }
    None
}

pub fn parse_listing(
    payload: &[u8],
    satellite: &str,
    channel: &str,
) -> Result<Vec<SatelliteCandidate>, String> {
    let base = bucket_url(satellite).ok_or("unknown GOES satellite or channel")?;
    let (wanted_channel, _, _) =
        channel_spec(channel).ok_or("unknown GOES satellite or channel")?;
    let text = std::str::from_utf8(payload)
        .map_err(|error| format!("GOES listing is not valid XML: {error}"))?;
    if text.to_ascii_uppercase().contains("<!DOCTYPE")
        || text.to_ascii_uppercase().contains("<!ENTITY")
        || !text.contains("<ListBucketResult")
    {
        return Err("GOES listing is not valid XML".into());
    }
    let marker = format!("_{satellite}_");
    let mut candidates = Vec::new();
    for chunk in text.split("<Contents>").skip(1) {
        let Some((body, _)) = chunk.split_once("</Contents>") else {
            return Err("GOES listing is not valid XML".into());
        };
        let Some(key) = xml_tag(body, "Key") else {
            continue;
        };
        if !key.ends_with(".nc") || !key.contains(&marker) {
            continue;
        }
        if parse_abi_channel(key) != Some(wanted_channel) {
            continue;
        }
        let Some(scan) = parse_abi_scan_start(key) else {
            continue;
        };
        let size_bytes = xml_tag(body, "Size").and_then(|s| s.trim().parse::<usize>().ok());
        candidates.push(SatelliteCandidate {
            satellite: satellite.to_owned(),
            channel: channel.to_owned(),
            scan_start: format_rfc3339_utc(scan),
            key: key.to_owned(),
            url: format!("{base}/{key}"),
            size_bytes,
            scan_unix: scan.unix_timestamp(),
            scan_dt: Some(scan),
        });
    }
    candidates.sort_by_key(|item| item.scan_dt);
    Ok(candidates)
}

pub fn render_dimensions(bounds: Bounds, resolution_km: f64, max_pixels: usize) -> (usize, usize) {
    let latitude = ((bounds.south + bounds.north) * 0.5).to_radians();
    let width = 2_usize.max(
        ((bounds.east - bounds.west).abs() * 111.2 * latitude.cos().max(0.15) / resolution_km)
            as usize,
    );
    let height =
        2_usize.max(((bounds.north - bounds.south).abs() * 111.2 / resolution_km) as usize);
    let scale = 1.0_f64.min((max_pixels as f64 / (width * height) as f64).sqrt());
    (
        2_usize.max((width as f64 * scale) as usize),
        2_usize.max((height as f64 * scale) as usize),
    )
}

fn read_scaled_array(
    dataset: &hdf5_reader::Dataset,
    expected_len: usize,
    default_fill: Option<f64>,
) -> Result<Vec<f64>, String> {
    let raw = dataset.read_raw_bytes().map_err(|e| e.to_string())?;
    let fill = dataset
        .attribute("_FillValue")
        .ok()
        .and_then(|a| a.read_as_f64().ok())
        .or(default_fill);
    let scale = dataset
        .attribute("scale_factor")
        .ok()
        .and_then(|a| a.read_as_f64().ok())
        .filter(|v| v.is_finite())
        .unwrap_or(1.0);
    let offset = dataset
        .attribute("add_offset")
        .ok()
        .and_then(|a| a.read_as_f64().ok())
        .filter(|v| v.is_finite())
        .unwrap_or(0.0);
    let unsigned_attr = dataset
        .attribute("_Unsigned")
        .ok()
        .and_then(|a| a.read_string().ok())
        .is_some_and(|s| s.eq_ignore_ascii_case("true"));
    let mut out = Vec::with_capacity(expected_len);
    match *dataset.dtype() {
        Datatype::FixedPoint {
            size: 1,
            signed: false,
            ..
        } if raw.len() == expected_len => {
            for &b in &raw {
                let v = f64::from(b);
                out.push(if fill == Some(v) {
                    f64::NAN
                } else {
                    v * scale + offset
                });
            }
        }
        Datatype::FixedPoint {
            size: 1,
            signed: true,
            ..
        } if raw.len() == expected_len => {
            for &b in &raw {
                let v = if unsigned_attr {
                    f64::from(b)
                } else {
                    f64::from(b as i8)
                };
                out.push(if fill == Some(v) {
                    f64::NAN
                } else {
                    v * scale + offset
                });
            }
        }
        Datatype::FixedPoint {
            size: 2,
            signed: false,
            byte_order,
        } if raw.len() == expected_len * 2 => {
            for &bytes in raw.as_chunks::<2>().0 {
                let v = f64::from(match byte_order {
                    ByteOrder::LittleEndian => u16::from_le_bytes(bytes),
                    ByteOrder::BigEndian => u16::from_be_bytes(bytes),
                });
                out.push(if fill == Some(v) {
                    f64::NAN
                } else {
                    v * scale + offset
                });
            }
        }
        Datatype::FixedPoint {
            size: 2,
            signed: true,
            byte_order,
        } if raw.len() == expected_len * 2 => {
            for &bytes in raw.as_chunks::<2>().0 {
                let v = if unsigned_attr {
                    f64::from(match byte_order {
                        ByteOrder::LittleEndian => u16::from_le_bytes(bytes),
                        ByteOrder::BigEndian => u16::from_be_bytes(bytes),
                    })
                } else {
                    f64::from(match byte_order {
                        ByteOrder::LittleEndian => i16::from_le_bytes(bytes),
                        ByteOrder::BigEndian => i16::from_be_bytes(bytes),
                    })
                };
                out.push(if fill == Some(v) {
                    f64::NAN
                } else {
                    v * scale + offset
                });
            }
        }
        Datatype::FloatingPoint {
            size: 4,
            byte_order,
        } if raw.len() == expected_len * 4 => {
            for &bytes in raw.as_chunks::<4>().0 {
                let v = f64::from(match byte_order {
                    ByteOrder::LittleEndian => f32::from_le_bytes(bytes),
                    ByteOrder::BigEndian => f32::from_be_bytes(bytes),
                });
                if !v.is_finite() || fill.is_some_and(|f| (v - f).abs() <= 1e-6 * f.abs().max(1.0))
                {
                    out.push(f64::NAN);
                } else {
                    out.push(v * scale + offset);
                }
            }
        }
        Datatype::FloatingPoint {
            size: 8,
            byte_order,
        } if raw.len() == expected_len * 8 => {
            for &bytes in raw.as_chunks::<8>().0 {
                let v = match byte_order {
                    ByteOrder::LittleEndian => f64::from_le_bytes(bytes),
                    ByteOrder::BigEndian => f64::from_be_bytes(bytes),
                };
                if !v.is_finite() || fill.is_some_and(|f| (v - f).abs() <= 1e-12 * f.abs().max(1.0))
                {
                    out.push(f64::NAN);
                } else {
                    out.push(v * scale + offset);
                }
            }
        }
        _ => return Err("GOES netCDF lacks supported imagery array dtype".into()),
    }
    Ok(out)
}

fn nearest_index(axis: &[f64], ascending: bool, value: f64) -> Option<usize> {
    if !value.is_finite() {
        return None;
    }
    if ascending {
        if value < axis[0] || value > axis[axis.len() - 1] {
            return None;
        }
        let upper = axis
            .partition_point(|&entry| entry < value)
            .clamp(1, axis.len() - 1);
        let lower = upper - 1;
        Some(
            if (axis[upper] - value).abs() < (axis[lower] - value).abs() {
                upper
            } else {
                lower
            },
        )
    } else {
        if value > axis[0] || value < axis[axis.len() - 1] {
            return None;
        }
        let upper = axis
            .partition_point(|&entry| entry > value)
            .clamp(1, axis.len() - 1);
        let lower = upper - 1;
        Some(
            if (axis[upper] - value).abs() < (axis[lower] - value).abs() {
                upper
            } else {
                lower
            },
        )
    }
}

fn colour_visible(value: f64) -> [u8; 3] {
    let brightness = (value.clamp(0.0, 1.2) / 1.2).sqrt();
    let grey = (brightness * 255.0).round() as u8;
    [grey, grey, grey]
}

fn colour_infrared(value: f64) -> [u8; 3] {
    let norm = ((310.0 - value) / 120.0).clamp(0.0, 1.0);
    let stops: [(f64, [f64; 3]); 5] = [
        (0.0, [15.0, 20.0, 30.0]),
        (0.35, [95.0, 110.0, 175.0]),
        (0.58, [230.0, 230.0, 255.0]),
        (0.78, [250.0, 120.0, 80.0]),
        (1.0, [255.0, 255.0, 255.0]),
    ];
    let high = stops
        .partition_point(|(s, _)| *s <= norm)
        .min(stops.len() - 1);
    let low = high.saturating_sub(1);
    let (s0, c0) = stops[low];
    let (s1, c1) = stops[high];
    let frac = if s1 > s0 {
        ((norm - s0) / (s1 - s0)).clamp(0.0, 1.0)
    } else {
        0.0
    };
    [
        (c0[0] + frac * (c1[0] - c0[0])).round() as u8,
        (c0[1] + frac * (c1[1] - c0[1])).round() as u8,
        (c0[2] + frac * (c1[2] - c0[2])).round() as u8,
    ]
}

pub fn render_goes_frame(
    payload: &[u8],
    bounds: Bounds,
    channel: &str,
    max_output_pixels: usize,
) -> Result<(Vec<u8>, f64, f64), String> {
    let (_, _, source_resolution) =
        channel_spec(channel).ok_or_else(|| format!("unsupported imagery choice {channel:?}"))?;
    if !bounds.west.is_finite()
        || !bounds.east.is_finite()
        || !bounds.south.is_finite()
        || !bounds.north.is_finite()
        || !(-180.0..=180.0).contains(&bounds.west)
        || !(-180.0..=180.0).contains(&bounds.east)
        || bounds.west >= bounds.east
        || !(-90.0..=90.0).contains(&bounds.south)
        || !(-90.0..=90.0).contains(&bounds.north)
        || bounds.south >= bounds.north
    {
        return Err("map bounds are outside ordered lon/lat limits".into());
    }
    if payload.is_empty() || payload.len() > MAX_SOURCE_BYTES {
        return Err("GOES source payload is empty or too large".into());
    }
    let file = Hdf5File::from_bytes_with_options(
        payload,
        OpenOptions {
            chunk_cache_bytes: 0,
            max_chunk_bytes: 32 * 1024 * 1024,
            max_read_bytes: 160 * 1024 * 1024,
            max_object_header_bytes: 4 * 1024 * 1024,
            ..Default::default()
        },
    )
    .map_err(|e| format!("GOES netCDF could not be opened: {e}"))?;

    let x_ds = file
        .dataset("x")
        .map_err(|_| "GOES netCDF lacks fixed-grid imagery fields".to_owned())?;
    let y_ds = file
        .dataset("y")
        .map_err(|_| "GOES netCDF lacks fixed-grid imagery fields".to_owned())?;
    let cmi_ds = file
        .dataset("CMI")
        .map_err(|_| "GOES netCDF lacks fixed-grid imagery fields".to_owned())?;
    let proj_ds = file
        .dataset("goes_imager_projection")
        .map_err(|_| "GOES netCDF lacks fixed-grid imagery fields".to_owned())?;

    let x_shape = x_ds.shape();
    let y_shape = y_ds.shape();
    let cmi_shape = cmi_ds.shape();
    if x_shape.len() != 1
        || y_shape.len() != 1
        || x_shape[0] < 2
        || y_shape[0] < 2
        || cmi_shape != [y_shape[0], x_shape[0]]
    {
        return Err("GOES image and coordinate dimensions disagree".into());
    }
    let nx = x_shape[0] as usize;
    let ny = y_shape[0] as usize;
    let x_axis = read_scaled_array(&x_ds, nx, None)?;
    let y_axis = read_scaled_array(&y_ds, ny, None)?;
    if x_axis.iter().any(|v| !v.is_finite()) || y_axis.iter().any(|v| !v.is_finite()) {
        return Err("GOES fixed-grid coordinate axis is malformed".into());
    }
    let x_ascending = x_axis[nx - 1] > x_axis[0];
    let y_ascending = y_axis[ny - 1] > y_axis[0];
    let data = read_scaled_array(&cmi_ds, nx * ny, None)?;
    let dqf = match file.dataset("DQF") {
        Ok(dqf_ds) if dqf_ds.shape() == cmi_shape => {
            read_scaled_array(&dqf_ds, nx * ny, Some(255.0)).ok()
        }
        _ => None,
    };

    let height = proj_ds
        .attribute("perspective_point_height")
        .and_then(|a| a.read_as_f64())
        .map_err(|e| format!("GOES netCDF could not be opened: {e}"))?;
    let lon_origin = proj_ds
        .attribute("longitude_of_projection_origin")
        .and_then(|a| a.read_as_f64())
        .map_err(|e| format!("GOES netCDF could not be opened: {e}"))?;
    let sweep = proj_ds
        .attribute("sweep_angle_axis")
        .and_then(|a| a.read_string())
        .unwrap_or_else(|_| "x".into());
    let semi_major = proj_ds
        .attribute("semi_major_axis")
        .and_then(|a| a.read_as_f64())
        .unwrap_or(6_378_137.0);
    let semi_minor = proj_ds
        .attribute("semi_minor_axis")
        .and_then(|a| a.read_as_f64())
        .unwrap_or(6_356_752.314_14);

    let (width, height_px) = render_dimensions(bounds, source_resolution, max_output_pixels);
    let total_pixels = width * height_px;
    let mut rgba = vec![0_u8; total_pixels * 4];
    let e2 = (semi_major * semi_major - semi_minor * semi_minor) / (semi_major * semi_major);
    let ratio_sq = (semi_minor / semi_major).powi(2);
    let inv_ratio_sq = (semi_major / semi_minor).powi(2);
    let r_sat = semi_major + height;
    let sweep_y = sweep.eq_ignore_ascii_case("y");
    let visible = channel == "visible";
    let workers = if total_pixels >= 65_536 {
        std::thread::available_parallelism()
            .map(|n| n.get().clamp(1, 4))
            .unwrap_or(1)
    } else {
        1
    };
    let rows_per_chunk = height_px.div_ceil(workers).max(1);
    let x_ref = &x_axis;
    let y_ref = &y_axis;
    let data_ref = &data;
    let dqf_ref = dqf.as_deref();

    let counts: Vec<usize> = std::thread::scope(|scope| {
        let mut handles = Vec::new();
        let mut row_start = 0;
        for chunk in rgba.chunks_mut(rows_per_chunk * width * 4) {
            let chunk_rows = chunk.len() / (width * 4);
            let first_row = row_start;
            row_start += chunk_rows;
            handles.push(scope.spawn(move || {
                let mut local_valid = 0_usize;
                for local_row in 0..chunk_rows {
                    let row = first_row + local_row;
                    let lat = if height_px > 1 {
                        bounds.north
                            + (bounds.south - bounds.north) * (row as f64)
                                / ((height_px - 1) as f64)
                    } else {
                        bounds.north
                    };
                    let phi = lat.to_radians();
                    let phi_c = (ratio_sq * phi.tan()).atan();
                    let cos_phic = phi_c.cos();
                    let sin_phic = phi_c.sin();
                    let r_c = semi_minor / (1.0 - e2 * cos_phic * cos_phic).sqrt();
                    let sz = r_c * sin_phic;
                    let row_offset = local_row * width * 4;
                    for col in 0..width {
                        let lon = if width > 1 {
                            bounds.west
                                + (bounds.east - bounds.west) * (col as f64) / ((width - 1) as f64)
                        } else {
                            bounds.west
                        };
                        let lam = (lon - lon_origin).to_radians();
                        let sx = r_sat - r_c * cos_phic * lam.cos();
                        let sy = -r_c * cos_phic * lam.sin();
                        if r_sat * (r_sat - sx) < sy * sy + inv_ratio_sq * sz * sz {
                            continue;
                        }
                        let dist = (sx * sx + sy * sy + sz * sz).sqrt();
                        let (x_rad, y_rad) = if sweep_y {
                            ((-sy / sx).atan(), (sz / dist).asin())
                        } else {
                            ((-sy / dist).asin(), (sz / sx).atan())
                        };
                        let (Some(xi), Some(yi)) = (
                            nearest_index(x_ref, x_ascending, x_rad),
                            nearest_index(y_ref, y_ascending, y_rad),
                        ) else {
                            continue;
                        };
                        let cell = yi * nx + xi;
                        let val = data_ref[cell];
                        if !val.is_finite() {
                            continue;
                        }
                        if dqf_ref.is_some_and(|q| !q[cell].is_finite() || q[cell] > 1.0) {
                            continue;
                        }
                        let rgb = if visible {
                            colour_visible(val)
                        } else {
                            colour_infrared(val)
                        };
                        let px = row_offset + col * 4;
                        chunk[px] = rgb[0];
                        chunk[px + 1] = rgb[1];
                        chunk[px + 2] = rgb[2];
                        chunk[px + 3] = 255;
                        local_valid += 1;
                    }
                }
                local_valid
            }));
        }
        handles
            .into_iter()
            .map(|h| h.join().expect("satellite worker"))
            .collect()
    });
    let valid_count: usize = counts.into_iter().sum();
    let coverage = (valid_count as f64) / (total_pixels as f64);
    if coverage < 0.01 {
        return Err("the selected GOES sector does not cover this map view".into());
    }
    let mut png = Vec::new();
    let mut encoder = png::Encoder::new(&mut png, width as u32, height_px as u32);
    encoder.set_color(png::ColorType::Rgba);
    encoder.set_depth(png::BitDepth::Eight);
    encoder.set_compression(png::Compression::Fast);
    let mut writer = encoder.write_header().map_err(|e| e.to_string())?;
    writer.write_image_data(&rgba).map_err(|e| e.to_string())?;
    writer.finish().map_err(|e| e.to_string())?;
    Ok((png, coverage, source_resolution))
}

pub struct SatelliteClient {
    client: Result<Client, String>,
}

pub fn client() -> &'static SatelliteClient {
    static CLIENT: OnceLock<SatelliteClient> = OnceLock::new();
    CLIENT.get_or_init(SatelliteClient::default)
}

impl Default for SatelliteClient {
    fn default() -> Self {
        let _ = rustls::crypto::ring::default_provider().install_default();
        let timeout = std::env::var("SHARPMOD_REMOTE_TIMEOUT")
            .ok()
            .and_then(|v| v.parse::<f64>().ok())
            .filter(|v| v.is_finite() && *v > 0.0)
            .unwrap_or(30.0);
        Self {
            client: Client::builder()
                .user_agent("SHARPpy-Reimagined")
                .connect_timeout(Duration::from_secs(8))
                .timeout(Duration::from_secs_f64(timeout))
                .build()
                .map_err(|error| error.to_string()),
        }
    }
}

impl SatelliteClient {
    pub async fn fetch(
        &self,
        when: OffsetDateTime,
        bounds: Bounds,
        channel: &str,
        now: OffsetDateTime,
    ) -> Result<SatelliteFrame, String> {
        let (_, channel_title, _) = channel_spec(channel)
            .ok_or_else(|| format!("unsupported imagery choice {channel:?}"))?;
        let satellite = choose_satellite(bounds);
        let base_url = bucket_url(satellite).expect("known satellite");
        let requested = when.to_offset(UtcOffset::UTC);
        let base_hour = requested
            .replace_minute(0)
            .and_then(|t| t.replace_second(0))
            .and_then(|t| t.replace_nanosecond(0))
            .expect("hour start");
        let client = self.client.as_ref().map_err(Clone::clone)?;
        let mut found: HashMap<String, SatelliteCandidate> = HashMap::new();
        for offset in [-1_i64, 0, 1] {
            let hour = base_hour + time::Duration::hours(offset);
            let prefix = format!(
                "ABI-L2-CMIPC/{:04}/{:03}/{:02}/OR_ABI-L2-CMIPC-M",
                hour.year(),
                hour.ordinal(),
                hour.hour()
            );
            let mut url = reqwest::Url::parse(&format!("{base_url}/")).expect("static GOES URL");
            url.query_pairs_mut()
                .extend_pairs([("list-type", "2"), ("prefix", &prefix)]);
            let resp = client
                .get(url)
                .timeout(Duration::from_secs(20))
                .send()
                .await
                .map_err(|e| format!("GOES request failed: {e}"))?
                .error_for_status()
                .map_err(|e| format!("GOES request failed: {e}"))?;
            let bytes = crate::http::body(resp, MAX_LISTING_BYTES).await?;
            for candidate in parse_listing(&bytes, satellite, channel)? {
                found.insert(candidate.key.clone(), candidate);
            }
        }
        let winner = found
            .into_values()
            .min_by_key(|item| {
                let dt = item.scan_dt.expect("parsed scan_dt");
                ((dt - requested).abs(), dt)
            })
            .ok_or_else(|| "no GOES frame was published near this time".to_owned())?;
        let selected = winner.scan_dt.expect("parsed scan_dt");
        let delta = (selected - requested).abs();
        if delta > DEFAULT_TIME_TOLERANCE {
            return Err(format!(
                "nearest GOES frame is {:.0} minutes away; the allowed mismatch is {:.0} minutes",
                delta.as_seconds_f64() / 60.0,
                DEFAULT_TIME_TOLERANCE.as_seconds_f64() / 60.0
            ));
        }
        if winner.size_bytes.is_some_and(|s| s > MAX_SOURCE_BYTES) {
            return Err("GOES source object exceeds the safety limit".into());
        }
        let resp = client
            .get(&winner.url)
            .send()
            .await
            .map_err(|e| format!("GOES request failed: {e}"))?
            .error_for_status()
            .map_err(|e| format!("GOES request failed: {e}"))?;
        let payload = crate::http::body(resp, MAX_SOURCE_BYTES).await?;
        let channel_owned = channel.to_owned();
        let (png_bytes, coverage_fraction, native_resolution_km) =
            tokio::task::spawn_blocking(move || {
                render_goes_frame(&payload, bounds, &channel_owned, MAX_OUTPUT_PIXELS)
            })
            .await
            .map_err(|e| e.to_string())??;
        Ok(SatelliteFrame {
            product: channel.to_owned(),
            satellite: winner.satellite,
            label: format!("{satellite} {channel_title}"),
            bounds,
            requested_time: format_rfc3339_utc(requested),
            retrieved_at: format_rfc3339_utc(now),
            valid_time: format_rfc3339_utc(selected),
            update_interval_s: 300.0,
            coverage_fraction,
            native_resolution_km,
            attribution: ATTRIBUTION.to_owned(),
            source_url: winner.url,
            png_bytes,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn listing_and_dimensions_match_contract() {
        let key = "ABI-L2-CMIPC/2026/256/01/OR_ABI-L2-CMIPC-M6C13_G19_s20262560146120_e20262560148505_c20262560149001.nc";
        let ignored = key.replace("C13", "C02");
        let xml = format!("<ListBucketResult xmlns=\"http://s3.amazonaws.com/doc/2006-03-01/\"><Contents><Key>{key}</Key><Size>1234</Size></Contents><Contents><Key>{ignored}</Key><Size>1234</Size></Contents></ListBucketResult>");
        let got = parse_listing(xml.as_bytes(), "G19", "infrared").unwrap();
        assert_eq!(got.len(), 1);
        assert_eq!(got[0].scan_start, "2026-09-13T01:46:12Z");
        assert_eq!(
            render_dimensions(
                Bounds {
                    west: -100.0,
                    east: -99.0,
                    south: 35.0,
                    north: 36.0
                },
                2.0,
                MAX_OUTPUT_PIXELS
            ),
            (45, 55)
        );
        assert_eq!(
            choose_satellite(Bounds {
                west: -125.0,
                east: -115.0,
                south: 30.0,
                north: 40.0
            }),
            "G18"
        );
        assert_eq!(choose_satellite(FRAME_BOUNDS), "G19");
    }
}

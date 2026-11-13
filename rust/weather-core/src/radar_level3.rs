//! NOAA NEXRAD Level III NIDS decoding, directory lookup, and polar PNG rendering.

use crate::format_rfc3339_utc;
use crate::polar_radar::{self, Bounds, IndexedPalette, IndexedRadial, Palette};
use crate::radar::{self, scan_time};
use crate::radar_provider::{Error, Frame};
use crate::radar_sites::RadarSite;
use base64::{engine::general_purpose::STANDARD, Engine};
use bzip2::read::BzDecoder;
use flate2::{Decompress, FlushDecompress, Status};
use reqwest::{Client, StatusCode};
use std::collections::VecDeque;
use std::io::Read;
use std::sync::{Arc, Mutex, OnceLock, PoisonError};
use std::time::{Duration, Instant};
use time::OffsetDateTime;
use tokio::sync::Semaphore;

const BASE: &str = "https://tgftp.nws.noaa.gov/SL.us008001/DF.of/DC.radar";
const ATTRIBUTION: &str = "NOAA/NWS NEXRAD Level III";
const MPH_PER_MPS: f64 = 3600.0 / 1609.344;
const RANGE_FOLDED_RGB: [u8; 3] = [128, 0, 255];
const MAX_FRAME_BYTES: usize = 2 * 1024 * 1024;
const MAX_DECOMPRESSED_BYTES: usize = 16 * 1024 * 1024;
const DIR_CACHE_TTL: Duration = Duration::from_secs(45);
const DIR_CACHE_LIMIT: usize = 128;
const FRAME_CACHE_LIMIT: usize = 12;

const TILTS: [(&str, f64, f64); 9] = [
    ("x", -0.5, -0.1),
    ("y", -0.1, 0.25),
    ("z", 0.25, 0.45),
    ("0", 0.45, 0.85),
    ("a", 0.85, 1.15),
    ("1", 1.15, 1.65),
    ("b", 1.65, 2.15),
    ("2", 2.15, 2.75),
    ("3", 2.75, 3.75),
];

pub fn echo_tops_palette() -> Palette {
    Palette {
        stops: vec![
            (0.0, "#17305a".into()),
            (10.0, "#246db2".into()),
            (20.0, "#27bad0".into()),
            (30.0, "#36b34a".into()),
            (40.0, "#e9df32".into()),
            (50.0, "#f38b29".into()),
            (60.0, "#d72f36".into()),
            (70.0, "#b72bbd".into()),
            (80.0, "#f0d3ff".into()),
        ],
        units: "kft".into(),
        display: "fixed".into(),
        stepped: false,
        floor: None,
        ceiling: None,
        ticks: (0..=80).step_by(10).map(f64::from).collect(),
    }
}

pub fn palette_for(product: &str) -> Palette {
    match product {
        "velocity" => serde_json::from_str(include_str!(
            "../resources/australian_velocity.palette.json"
        ))
        .expect("bundled velocity palette"),
        "echo-tops" => echo_tops_palette(),
        _ => serde_json::from_str(include_str!(
            "../resources/australian_reflectivity.palette.json"
        ))
        .expect("bundled reflectivity palette"),
    }
}

fn site_bounds(site: &RadarSite) -> Bounds {
    let span = site.half_span_deg;
    Bounds {
        west: (site.lon - span).max(-180.0),
        east: (site.lon + span).min(180.0),
        south: (site.lat - span).max(-90.0),
        north: (site.lat + span).min(90.0),
    }
}

#[derive(Debug)]
pub struct DecodedLevel3 {
    pub site_id: String,
    pub code: u16,
    pub lat: f64,
    pub lon: f64,
    pub max_range_km: f64,
    pub el_angle: f64,
    pub vol_time: OffsetDateTime,
    pub delta_time: i64,
    pub scan_kind: scan_time::ScanKind,
    pub nbins: usize,
    pub radials: Vec<DecodedRadial>,
    pub values: [Option<f64>; 256],
    pub lower_bounds: Vec<usize>,
}

#[derive(Debug)]
pub struct DecodedRadial {
    pub start_az: f64,
    pub end_az: f64,
    pub gates: Vec<u8>,
}

fn read_i16_be(buf: &[u8], off: usize) -> Option<i16> {
    let bytes: [u8; 2] = buf.get(off..off + 2)?.try_into().ok()?;
    Some(i16::from_be_bytes(bytes))
}

fn read_u16_be(buf: &[u8], off: usize) -> Option<u16> {
    let bytes: [u8; 2] = buf.get(off..off + 2)?.try_into().ok()?;
    Some(u16::from_be_bytes(bytes))
}

fn read_i32_be(buf: &[u8], off: usize) -> Option<i32> {
    let bytes: [u8; 4] = buf.get(off..off + 4)?.try_into().ok()?;
    Some(i32::from_be_bytes(bytes))
}

fn read_u32_be(buf: &[u8], off: usize) -> Option<u32> {
    let bytes: [u8; 4] = buf.get(off..off + 4)?.try_into().ok()?;
    Some(u32::from_be_bytes(bytes))
}

fn parse_wmo_header(data: &[u8]) -> (Option<&'static str>, Option<String>, usize) {
    let head = &data[..data.len().min(64)];
    let wmo = ["NXUS", "SDUS", "NOUS"].into_iter().find(|prefix| {
        head.windows(6)
            .any(|w| &w[..4] == prefix.as_bytes() && w[4].is_ascii_digit() && w[5].is_ascii_digit())
    });
    let Some(wmo_code) = wmo else {
        return (None, None, 0);
    };
    let Some(first_end) = head.windows(3).position(|w| w == b"\r\r\n") else {
        return (None, None, 0);
    };
    let mut end = first_end;
    if let Some(rel) = head[first_end + 3..]
        .windows(3)
        .position(|w| w == b"\r\r\n")
    {
        let second = &head[first_end + 3..first_end + 3 + rel];
        if !second.is_empty()
            && second.len() <= 12
            && second
                .iter()
                .all(|b| b.is_ascii_alphanumeric() || *b == b' ')
        {
            end = first_end + 3 + rel;
        }
    }
    let line = &head[..end];
    let site = if line.len() >= 3 && line[line.len() - 3..].iter().all(u8::is_ascii_alphanumeric) {
        Some(String::from_utf8_lossy(&line[line.len() - 3..]).into_owned())
    } else {
        None
    };
    (Some(wmo_code), site, end + 3)
}

fn strip_end_bytes(data: &[u8]) -> &[u8] {
    if data.len() >= 4 {
        let tail = &data[data.len() - 4..data.len() - 1];
        if tail == b"\r\r\n" || tail == b"\xff\xff\n" {
            return &data[..data.len() - 4];
        }
    }
    data
}

fn zlib_decompress_all_frames(data: &[u8]) -> Result<Vec<u8>, String> {
    let mut out = Vec::new();
    let mut rem = data;
    while !rem.is_empty() {
        let mut decomp = Decompress::new(true);
        let mut frame = Vec::new();
        let mut pos = 0_usize;
        let mut chunk = [0_u8; 16 * 1024];
        let ok = loop {
            let before_in = decomp.total_in() as usize;
            let res = decomp.decompress(&rem[pos..], &mut chunk, FlushDecompress::None);
            let produced = decomp.total_out() as usize - frame.len();
            if produced > 0 {
                if out.len() + frame.len() + produced > MAX_DECOMPRESSED_BYTES {
                    return Err("Level III product exceeded decompressed size limit".into());
                }
                frame.extend_from_slice(&chunk[..produced]);
            }
            pos = decomp.total_in() as usize;
            match res {
                Ok(Status::StreamEnd) => break true,
                Ok(Status::Ok | Status::BufError) => {
                    if pos == before_in && produced == 0 {
                        break false;
                    }
                }
                Err(_) => break false,
            }
        };
        if ok && pos > 0 {
            out.extend_from_slice(&frame);
            rem = &rem[pos..];
        } else {
            if out.len() + rem.len() > MAX_DECOMPRESSED_BYTES {
                return Err("Level III product exceeded decompressed size limit".into());
            }
            out.extend_from_slice(rem);
            break;
        }
    }
    Ok(out)
}

fn nexrad_to_datetime(julian_date: u16, seconds_since_midnight: i32) -> Option<OffsetDateTime> {
    if julian_date == 0 || seconds_since_midnight < 0 {
        return None;
    }
    let secs = i64::from(julian_date - 1) * 86_400 + i64::from(seconds_since_midnight);
    OffsetDateTime::from_unix_timestamp(secs).ok()
}

pub fn decode_level3(payload: &[u8]) -> Result<DecodedLevel3, String> {
    if payload.is_empty() || payload.len() > MAX_FRAME_BYTES {
        return Err("Level III payload is empty or exceeds size limit".into());
    }
    let (wmo1, site1, skip1) = parse_wmo_header(payload);
    if wmo1 == Some("NOUS") {
        return Err("Level III payload is a free text message".into());
    }
    let trimmed = strip_end_bytes(&payload[skip1..]);
    let decompressed = zlib_decompress_all_frames(trimmed)?;
    let (_, site2, skip2) = parse_wmo_header(&decompressed);
    let mut msg = decompressed[skip2..].to_vec();
    let site_id = site2.or(site1).unwrap_or_default().to_ascii_uppercase();
    if msg.len() < 120 {
        return Err("Level III payload is truncated before product header".into());
    }
    let code = read_u16_be(&msg, 0).ok_or("missing message header")?;
    let max_range_km: f64 = match code {
        94 => 460.0,
        99 => 300.0,
        135 => 345.0,
        _ => return Err(format!("unsupported Level III product code {code}")),
    };
    let pdb = &msg[18..120];
    if read_i16_be(pdb, 0) != Some(-1) {
        return Err("invalid Level III product description divider".into());
    }
    let lat = f64::from(read_i32_be(pdb, 2).ok_or("missing lat")?) * 0.001;
    let lon = f64::from(read_i32_be(pdb, 6).ok_or("missing lon")?) * 0.001;
    let vol_date = read_u16_be(pdb, 22).ok_or("missing vol_date")?;
    let vol_start_time = read_i32_be(pdb, 24).ok_or("missing vol_start_time")?;
    let vol_time =
        nexrad_to_datetime(vol_date, vol_start_time).ok_or("invalid Level III volume timestamp")?;
    let dep3 = read_i16_be(pdb, 40).ok_or("missing dep3")?;
    let el_angle = f64::from(dep3) * 0.1;
    let mut thresholds = [0_i16; 16];
    for (i, slot) in thresholds.iter_mut().enumerate() {
        *slot = read_i16_be(pdb, 42 + i * 2).ok_or("missing thresholds")?;
    }
    let mut dep_tail = [0_i16; 7];
    for (i, slot) in dep_tail.iter_mut().enumerate() {
        *slot = read_i16_be(pdb, 74 + i * 2).ok_or("missing dependent values")?;
    }
    let dep7 = dep_tail[3] as u16;
    let delta_time = i64::from(dep7 >> 5);
    let scan_kind = match dep7 & 0x001F {
        2 => scan_time::ScanKind::Sails,
        1 => scan_time::ScanKind::Mrle,
        _ => scan_time::ScanKind::Ordinary,
    };
    let compression = dep_tail[4] != 0;
    let sym_off_hw = read_u32_be(pdb, 90).ok_or("missing symbology offset")? as usize;
    if sym_off_hw == 0 {
        return Err("Level III product has no symbology block".into());
    }

    if compression {
        let mut decoder = BzDecoder::new(&msg[120..]);
        let mut body = Vec::new();
        let mut chunk = [0_u8; 16 * 1024];
        let mut ok = true;
        loop {
            match decoder.read(&mut chunk) {
                Ok(0) => break,
                Ok(n) => {
                    if body.len() + n > MAX_DECOMPRESSED_BYTES {
                        return Err("Level III bzip2 payload exceeded size limit".into());
                    }
                    body.extend_from_slice(&chunk[..n]);
                }
                Err(_) => {
                    ok = false;
                    break;
                }
            }
        }
        if ok {
            msg.truncate(120);
            msg.extend_from_slice(&body);
        }
    }

    let sym_off = sym_off_hw * 2;
    if sym_off + 16 > msg.len() {
        return Err("Level III symbology block offset out of bounds".into());
    }
    if read_i16_be(&msg, sym_off) != Some(-1) || read_i16_be(&msg, sym_off + 2) != Some(1) {
        return Err("invalid Level III symbology block header".into());
    }
    let nlayer = read_u16_be(&msg, sym_off + 8).ok_or("missing nlayer")?;
    if nlayer == 0 {
        return Err("Level III symbology block contains no layers".into());
    }
    let layer_hdr_off = sym_off + 10;
    if read_i16_be(&msg, layer_hdr_off) != Some(-1) {
        return Err("invalid Level III symbology layer divider".into());
    }
    let layer_len = read_u32_be(&msg, layer_hdr_off + 2).ok_or("missing layer length")? as usize;
    let layer_start = layer_hdr_off + 6;
    if layer_start + layer_len > msg.len() || layer_len < 14 {
        return Err("Level III symbology layer out of bounds".into());
    }
    let packet_code = read_u16_be(&msg, layer_start).ok_or("missing packet code")?;
    if packet_code != 16 {
        return Err(format!("unsupported Level III packet code {packet_code}"));
    }
    let pkt = layer_start + 2;
    let ind_first = read_u16_be(&msg, pkt).ok_or("missing first bin")?;
    let nbins = read_u16_be(&msg, pkt + 2).ok_or("missing nbins")? as usize;
    let num_rad = read_u16_be(&msg, pkt + 10).ok_or("missing num_rad")? as usize;
    if ind_first != 0 || !(1..=2400).contains(&nbins) || !(1..=1440).contains(&num_rad) {
        return Err("unsupported NEXRAD geometry or units".into());
    }
    let mut cursor = pkt + 12;
    let mut radials = Vec::with_capacity(num_rad);
    for _ in 0..num_rad {
        let num_bytes = read_u16_be(&msg, cursor).ok_or("truncated radial header")? as usize;
        let start_raw = read_i16_be(&msg, cursor + 2).ok_or("truncated start_az")?;
        let delta_raw = read_i16_be(&msg, cursor + 4).ok_or("truncated delta_az")?;
        cursor += 6;
        let gate_bytes = msg
            .get(cursor..cursor + num_bytes)
            .ok_or("truncated radial gate payload")?;
        cursor += num_bytes;
        let start_az = (f64::from(start_raw) * 0.1).rem_euclid(360.0);
        let end_az = (start_az + f64::from(delta_raw) * 0.1).rem_euclid(360.0);
        let mut gates = Vec::with_capacity(nbins);
        if gate_bytes.len() >= nbins {
            gates.extend_from_slice(&gate_bytes[..nbins]);
        } else {
            gates.extend_from_slice(gate_bytes);
            gates.resize(nbins, 0);
        }
        radials.push(DecodedRadial {
            start_az,
            end_az,
            gates,
        });
    }

    if !max_range_km.is_finite()
        || !(0.0 < max_range_km && max_range_km <= 500.0)
        || !lat.is_finite()
        || !(-90.0..=90.0).contains(&lat)
        || !lon.is_finite()
        || !(-180.0..=180.0).contains(&lon)
    {
        return Err("unsupported NEXRAD geometry or units".into());
    }

    let mut values = [None; 256];
    let mut lower_bounds = Vec::new();
    if code == 135 {
        let data_mask = (thresholds[0] as u16) as u8;
        let scale = f64::from(thresholds[1]);
        let offset = f64::from(thresholds[2]);
        let topped_mask = (thresholds[3] as u16) as u8;
        if !scale.is_finite() || scale == 0.0 {
            return Err("invalid Level III EET threshold scale".into());
        }
        for (i, slot) in values.iter_mut().enumerate().skip(2) {
            let byte = i as u8;
            let v = (f64::from(byte & data_mask) - offset) / scale;
            if v.is_finite() && v >= 0.0 {
                *slot = Some(v);
            }
            if (byte & topped_mask) != 0 {
                lower_bounds.push(i);
            }
        }
    } else {
        let min_val = f64::from(thresholds[0]) * 0.1;
        let inc = f64::from(thresholds[1] as u16) * 0.1;
        let num_levels = usize::from(thresholds[2] as u16).min(254);
        let factor = if code == 99 { MPH_PER_MPS } else { 1.0 };
        for i in 0..num_levels {
            let v = (min_val + (i as f64) * inc) * factor;
            if v.is_finite() {
                values[2 + i] = Some(v);
            }
        }
    }

    Ok(DecodedLevel3 {
        site_id,
        code,
        lat,
        lon,
        max_range_km,
        el_angle,
        vol_time,
        delta_time,
        scan_kind,
        nbins,
        radials,
        values,
        lower_bounds,
    })
}

pub fn render_level3(
    decoded: &DecodedLevel3,
    site: &RadarSite,
    product: &str,
    pixels: usize,
) -> Result<(Bounds, Vec<u8>), String> {
    let palette = palette_for(product);
    let echo_tops = decoded.code == 135;
    let mut rgb = [0_u8; 768];
    let mut alpha = [0_u8; 256];
    if !echo_tops {
        rgb[3..6].copy_from_slice(&RANGE_FOLDED_RGB);
        alpha[1] = 255;
    }
    for (i, slot) in decoded.values.iter().enumerate() {
        if let Some(v) = slot {
            let c = polar_radar::colour(*v, &palette)?;
            rgb[i * 3..i * 3 + 3].copy_from_slice(&c);
            alpha[i] = 255;
        }
    }
    let gate_width = if echo_tops {
        1000.0
    } else {
        decoded.max_range_km * 1000.0 / (decoded.nbins as f64)
    };
    let radials = decoded
        .radials
        .iter()
        .map(|r| {
            let width = (r.end_az - r.start_az).rem_euclid(360.0);
            let azimuth = (r.start_az + width * 0.5).rem_euclid(360.0);
            IndexedRadial {
                azimuth,
                width,
                first_gate_m: gate_width * 0.5,
                gate_spacing_m: gate_width,
                codes: r.gates.clone(),
            }
        })
        .collect::<Vec<_>>();
    let bounds = site_bounds(site);
    let folded = (!echo_tops).then_some(1);
    polar_radar::render_indexed_256(
        &radials,
        site,
        bounds,
        pixels,
        IndexedPalette {
            rgb: &rgb,
            alpha: &alpha,
            lookup: &decoded.values,
            folded,
            lower_bounds: &decoded.lower_bounds,
        },
    )
}

fn valid_scan_time(
    actual: OffsetDateTime,
    target: OffsetDateTime,
    now: OffsetDateTime,
    historical: bool,
) -> bool {
    actual <= target
        && actual <= now
        && actual
            >= if historical {
                target - time::Duration::minutes(20)
            } else {
                now - time::Duration::minutes(70)
            }
}

struct CachedDir {
    path: String,
    cached_at: Instant,
    entries: Vec<(OffsetDateTime, String)>,
}

pub struct Level3Client {
    client: Result<Client, String>,
    requests: Semaphore,
    directories: Mutex<VecDeque<CachedDir>>,
    frames: Mutex<VecDeque<(String, Arc<Frame>)>>,
}

pub fn client() -> &'static Level3Client {
    static CLIENT: OnceLock<Level3Client> = OnceLock::new();
    CLIENT.get_or_init(Level3Client::default)
}

impl Default for Level3Client {
    fn default() -> Self {
        let _ = rustls::crypto::ring::default_provider().install_default();
        let timeout = std::env::var("SHARPMOD_REMOTE_TIMEOUT")
            .ok()
            .and_then(|value| value.parse::<f64>().ok())
            .filter(|value| value.is_finite() && *value > 0.0)
            .unwrap_or(30.0);
        Self {
            client: Client::builder()
                .user_agent("sharpmod")
                .connect_timeout(Duration::from_secs(8))
                .timeout(Duration::from_secs_f64(timeout))
                .build()
                .map_err(|error| format!("radar HTTP client could not be created: {error}")),
            requests: Semaphore::new(4),
            directories: Mutex::new(VecDeque::new()),
            frames: Mutex::new(VecDeque::new()),
        }
    }
}

impl Level3Client {
    pub fn clear(&self) {
        self.directories
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clear();
        self.frames
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clear();
    }

    pub async fn fetch_site(
        &self,
        request: crate::radar_provider::Request,
        now: OffsetDateTime,
        site: &'static RadarSite,
    ) -> Result<Option<Arc<Frame>>, Error> {
        let product = request.product.as_deref().unwrap_or("velocity");
        let echo_tops = product == "echo-tops";
        let expected_code = match product {
            "velocity" => 99_u16,
            "echo-tops" => 135_u16,
            "reflectivity" => 94_u16,
            _ => return Ok(None),
        };
        let tilt = request.tilt.as_deref().unwrap_or("0");
        let Some(&(_, min_el, max_el)) = TILTS.iter().find(|(key, _, _)| *key == tilt) else {
            return Err(Error::Invalid("unknown radar product or tilt".into()));
        };
        if echo_tops && tilt != "0" {
            return Err(Error::Invalid(
                "Enhanced Echo Tops does not have a selectable tilt".into(),
            ));
        }
        let requested = request
            .valid_time
            .as_deref()
            .map(|value| {
                OffsetDateTime::parse(value, &time::format_description::well_known::Rfc3339)
            })
            .transpose()
            .map_err(|_| Error::Invalid("A valid radar time is required.".into()))?;
        let target = requested.unwrap_or(now);
        if target < now - time::Duration::minutes(70) || target > now + time::Duration::minutes(1) {
            return Err(Error::Invalid(
                "radar history is available only for the past 60 minutes".into(),
            ));
        }
        let family = if echo_tops {
            "135et".to_owned()
        } else if product == "velocity" {
            format!("p99v{tilt}")
        } else {
            format!("p94r{tilt}")
        };
        let directory = format!("DS.{family}/SI.{}/", site.id.to_ascii_lowercase());
        let entries = self
            .directory_scans(&directory)
            .await
            .map_err(Error::Fetch)?;
        let earliest = if requested.is_some() {
            target - time::Duration::minutes(30)
        } else {
            now - time::Duration::minutes(70)
        };
        let recent: Vec<(OffsetDateTime, String)> = entries
            .into_iter()
            .filter(|(date, _)| *date >= earliest && *date <= target.min(now))
            .collect();
        let (modified, name) = recent
            .into_iter()
            .max_by_key(|(date, _)| *date)
            .ok_or(Error::ScanMissing)?;
        let pixels = if requested.is_some() { 4096 } else { 2048 };
        let cache_key = format!(
            "level3:{product}:{}:{tilt}:{name}:{}:{pixels}",
            site.id,
            modified.unix_timestamp()
        );
        if let Some(frame) = self
            .frames
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .iter()
            .find(|(k, _)| k == &cache_key)
            .map(|(_, f)| Arc::clone(f))
        {
            let valid_ok = frame
                .actual_time
                .is_some_and(|actual| valid_scan_time(actual, target, now, requested.is_some()));
            if valid_ok {
                return Ok(Some(frame));
            }
        }
        let url = format!("{BASE}/{directory}{name}");
        let payload = self.fetch_bytes(&url, MAX_FRAME_BYTES).await?;
        let site_tail = site
            .id
            .get(site.id.len().saturating_sub(3)..)
            .unwrap_or(&site.id)
            .to_ascii_uppercase();
        let product_owned = product.to_owned();
        let rendered = tokio::task::spawn_blocking(move || {
            let decoded = decode_level3(&payload)?;
            let actual = scan_time::level3(decoded.vol_time, decoded.delta_time, decoded.scan_kind)
                .map_err(str::to_owned)?;
            if decoded.site_id != site_tail
                || decoded.code != expected_code
                || (!echo_tops
                    && (!decoded.el_angle.is_finite()
                        || !(min_el..=max_el).contains(&decoded.el_angle)))
            {
                return Err("radar identity, product or actual scan time does not match".into());
            }
            if !valid_scan_time(actual, target, now, requested.is_some()) {
                return Ok(None);
            }
            let el = (!echo_tops).then_some(decoded.el_angle);
            let (bounds, png) = render_level3(&decoded, site, &product_owned, pixels)?;
            Ok::<_, String>(Some((actual, el, bounds, png)))
        })
        .await
        .map_err(|error| Error::Fetch(error.to_string()))?
        .map_err(|error| {
            Error::Fetch(format!("numeric radar scan could not be decoded: {error}"))
        })?;
        let (actual, elevation_angle, bounds, png) = rendered.ok_or(Error::ScanMissing)?;
        let label = match product {
            "echo-tops" => "Enhanced Echo Tops".into(),
            "velocity" => format!("Velocity · {:.1}°", elevation_angle.unwrap_or(0.0)),
            _ => format!("Base Reflectivity · {:.1}°", elevation_angle.unwrap_or(0.0)),
        };
        let units = match product {
            "echo-tops" => "kft",
            "velocity" => "mph",
            _ => "dBZ",
        };
        let frame = Arc::new(Frame {
            actual_time: Some(actual),
            product: product.into(),
            site_id: site.id.clone(),
            elevation_angle,
            palette: Some(palette_for(product)),
            legend_png_base64: None,
            label,
            units: units.into(),
            bounds,
            retrieved_at: format_rfc3339_utc(now),
            valid_time: Some(format_rfc3339_utc(actual)),
            update_interval_s: 150.0,
            attribution: ATTRIBUTION.into(),
            source_url: url,
            byte_length: png.len(),
            png_base64: STANDARD.encode(png),
        });
        let mut cache = self.frames.lock().unwrap_or_else(PoisonError::into_inner);
        cache.retain(|(k, _)| k != &cache_key);
        if cache.len() >= FRAME_CACHE_LIMIT {
            cache.pop_front();
        }
        cache.push_back((cache_key, Arc::clone(&frame)));
        Ok(Some(frame))
    }

    async fn directory_scans(&self, path: &str) -> Result<Vec<(OffsetDateTime, String)>, String> {
        if let Some(entries) = self
            .directories
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .iter()
            .find(|entry| entry.path == path && entry.cached_at.elapsed() < DIR_CACHE_TTL)
            .map(|entry| entry.entries.clone())
        {
            return Ok(entries);
        }
        let (status, body) = self.fetch_status(&format!("{BASE}/{path}")).await?;
        let entries = match status {
            StatusCode::NOT_FOUND => Vec::new(),
            StatusCode::FORBIDDEN => {
                let (parent, site) = path
                    .trim_end_matches('/')
                    .rsplit_once('/')
                    .expect("radar directory paths include product and site");
                let (parent_status, parent_body) =
                    self.fetch_status(&format!("{BASE}/{parent}/")).await?;
                if !parent_status.is_success() || !radar::site_absent(&parent_body, site) {
                    return Err(
                        "radar product directory could not verify the site's absence".into(),
                    );
                }
                Vec::new()
            }
            status if status.is_success() => {
                let parsed = radar::scan_entries(&body)?;
                if parsed.is_empty() && !String::from_utf8_lossy(&body).contains("Index of /") {
                    return Err("radar scan directory was not a directory listing".into());
                }
                parsed
            }
            status => return Err(format!("radar directory returned HTTP {status}")),
        };
        let mut cache = self
            .directories
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        cache.retain(|entry| entry.path != path);
        if cache.len() >= DIR_CACHE_LIMIT {
            cache.pop_front();
        }
        cache.push_back(CachedDir {
            path: path.into(),
            cached_at: Instant::now(),
            entries: entries.clone(),
        });
        Ok(entries)
    }

    async fn fetch_status(&self, url: &str) -> Result<(StatusCode, Vec<u8>), String> {
        let client = self.client.as_ref().map_err(Clone::clone)?;
        let _permit = self
            .requests
            .acquire()
            .await
            .map_err(|error| error.to_string())?;
        let response = client
            .get(url)
            .send()
            .await
            .map_err(|error| error.to_string())?;
        let status = response.status();
        if !status.is_success() {
            return Ok((status, Vec::new()));
        }
        Ok((
            status,
            crate::http::body(response, radar::BYTE_LIMIT).await?,
        ))
    }

    async fn fetch_bytes(&self, url: &str, limit: usize) -> Result<Vec<u8>, Error> {
        let client = self
            .client
            .as_ref()
            .map_err(|error| Error::Fetch(error.clone()))?;
        let _permit = self
            .requests
            .acquire()
            .await
            .map_err(|error| Error::Fetch(error.to_string()))?;
        let response = client
            .get(url)
            .send()
            .await
            .map_err(|error| Error::Fetch(error.to_string()))?
            .error_for_status()
            .map_err(|error| Error::Fetch(error.to_string()))?;
        crate::http::body(response, limit)
            .await
            .map_err(Error::Fetch)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use flate2::{write::ZlibEncoder, Compression};

    #[test]
    fn historical_level3_time_does_not_accept_future_or_old_scans() {
        let parse = |raw| {
            OffsetDateTime::parse(raw, &time::format_description::well_known::Rfc3339).unwrap()
        };
        let now = parse("2026-10-17T12:12:00Z");
        let target = parse("2026-10-17T12:10:00Z");
        assert!(valid_scan_time(
            parse("2026-10-17T11:55:00Z"),
            target,
            now,
            true
        ));
        assert!(!valid_scan_time(
            parse("2026-10-17T12:11:00Z"),
            target,
            now,
            true
        ));
        assert!(!valid_scan_time(
            parse("2026-10-17T11:49:59Z"),
            target,
            now,
            true
        ));
    }
    use std::io::Write;

    fn build_synthetic_nids(
        site3: &str,
        code: u16,
        el_tenth: i16,
        dep7: u16,
        thresholds: [i16; 16],
        radials: &[(i16, i16, &[u8])],
        bzip_sym: bool,
    ) -> Vec<u8> {
        let nbins = radials.first().map_or(1, |r| r.2.len()) as u16;
        let mut sym = Vec::new();
        let mut layer_body = Vec::new();
        layer_body.extend_from_slice(&16_u16.to_be_bytes());
        layer_body.extend_from_slice(&0_u16.to_be_bytes());
        layer_body.extend_from_slice(&nbins.to_be_bytes());
        layer_body.extend_from_slice(&0_i16.to_be_bytes());
        layer_body.extend_from_slice(&0_i16.to_be_bytes());
        layer_body.extend_from_slice(&1000_i16.to_be_bytes());
        layer_body.extend_from_slice(&(radials.len() as u16).to_be_bytes());
        for &(start_tenth, delta_tenth, gates) in radials {
            layer_body.extend_from_slice(&(gates.len() as u16).to_be_bytes());
            layer_body.extend_from_slice(&start_tenth.to_be_bytes());
            layer_body.extend_from_slice(&delta_tenth.to_be_bytes());
            layer_body.extend_from_slice(gates);
        }
        let block_len = (16 + layer_body.len()) as u32;
        sym.extend_from_slice(&(-1_i16).to_be_bytes());
        sym.extend_from_slice(&1_i16.to_be_bytes());
        sym.extend_from_slice(&block_len.to_be_bytes());
        sym.extend_from_slice(&1_u16.to_be_bytes());
        sym.extend_from_slice(&(-1_i16).to_be_bytes());
        sym.extend_from_slice(&(layer_body.len() as u32).to_be_bytes());
        sym.extend_from_slice(&layer_body);

        let sym_payload = if bzip_sym {
            let mut enc = bzip2::write::BzEncoder::new(Vec::new(), bzip2::Compression::fast());
            enc.write_all(&sym).unwrap();
            enc.finish().unwrap()
        } else {
            sym.clone()
        };

        let mut body = Vec::new();
        let msg_len = (120 + sym_payload.len()) as u32;
        body.extend_from_slice(&code.to_be_bytes());
        body.extend_from_slice(&20744_u16.to_be_bytes());
        body.extend_from_slice(&43200_i32.to_be_bytes());
        body.extend_from_slice(&msg_len.to_be_bytes());
        body.extend_from_slice(&1_i16.to_be_bytes());
        body.extend_from_slice(&0_i16.to_be_bytes());
        body.extend_from_slice(&2_i16.to_be_bytes());

        body.extend_from_slice(&(-1_i16).to_be_bytes());
        body.extend_from_slice(&35_333_i32.to_be_bytes());
        body.extend_from_slice(&(-97_278_i32).to_be_bytes());
        body.extend_from_slice(&367_i16.to_be_bytes());
        body.extend_from_slice(&(code as i16).to_be_bytes());
        body.extend_from_slice(&2_i16.to_be_bytes());
        body.extend_from_slice(&215_i16.to_be_bytes());
        body.extend_from_slice(&1_i16.to_be_bytes());
        body.extend_from_slice(&1_i16.to_be_bytes());
        body.extend_from_slice(&20744_u16.to_be_bytes());
        body.extend_from_slice(&43200_i32.to_be_bytes());
        body.extend_from_slice(&20744_u16.to_be_bytes());
        body.extend_from_slice(&43260_i32.to_be_bytes());
        body.extend_from_slice(&0_i16.to_be_bytes());
        body.extend_from_slice(&0_i16.to_be_bytes());
        body.extend_from_slice(&1_i16.to_be_bytes());
        body.extend_from_slice(&el_tenth.to_be_bytes());
        for thr in thresholds {
            body.extend_from_slice(&thr.to_be_bytes());
        }
        body.extend_from_slice(&0_i16.to_be_bytes());
        body.extend_from_slice(&0_i16.to_be_bytes());
        body.extend_from_slice(&0_i16.to_be_bytes());
        body.extend_from_slice(&(dep7 as i16).to_be_bytes());
        body.extend_from_slice(&(i16::from(bzip_sym)).to_be_bytes());
        body.extend_from_slice(&0_i16.to_be_bytes());
        body.extend_from_slice(&(sym.len() as i16).to_be_bytes());
        body.push(0);
        body.push(0);
        body.extend_from_slice(&60_u32.to_be_bytes());
        body.extend_from_slice(&0_u32.to_be_bytes());
        body.extend_from_slice(&0_u32.to_be_bytes());
        assert_eq!(body.len(), 120);
        body.extend_from_slice(&sym_payload);

        let mut inner = format!("SDUS54 KOUN 171200\r\r\nN0U{site3}\r\r\n").into_bytes();
        inner.extend_from_slice(&body);
        let mut zlib = ZlibEncoder::new(Vec::new(), Compression::fast());
        zlib.write_all(&inner).unwrap();
        let compressed = zlib.finish().unwrap();
        let mut full = format!("SDUS54 KOUN 171200\r\r\nN0U{site3}\r\r\n").into_bytes();
        full.extend_from_slice(&compressed);
        full.extend_from_slice(b"\r\r\n\n");
        full
    }

    #[test]
    fn decodes_velocity_and_echo_tops_and_renders_indexed_png() {
        let mut vel_thr = [0_i16; 16];
        vel_thr[0] = -635;
        vel_thr[1] = 5;
        vel_thr[2] = 254;
        let vel_nids = build_synthetic_nids(
            "TLX",
            99,
            5,
            (120 << 5) | 2,
            vel_thr,
            &[
                (0, 900, &[0, 1, 129, 255]),
                (900, 900, &[0, 1, 129, 255]),
                (1800, 900, &[0, 1, 129, 255]),
            ],
            true,
        );
        let decoded = decode_level3(&vel_nids).unwrap();
        assert_eq!(decoded.site_id, "TLX");
        assert_eq!(decoded.code, 99);
        assert!((decoded.el_angle - 0.5).abs() < 1e-6);
        assert_eq!(decoded.delta_time, 120);
        assert!(matches!(decoded.scan_kind, scan_time::ScanKind::Sails));
        assert_eq!(
            scan_time::level3(decoded.vol_time, decoded.delta_time, decoded.scan_kind).unwrap(),
            decoded.vol_time
        );
        assert!((decoded.values[129].unwrap() - 0.0).abs() < 1e-6);
        let site = RadarSite {
            id: "KTLX".into(),
            lat: 35.333,
            lon: -97.278,
            half_span_deg: 0.5,
            products: vec![],
            name: None,
            country: None,
            wmo: None,
            provider: None,
        };
        let (_bounds, png) = render_level3(&decoded, &site, "velocity", 64).unwrap();
        assert!(png.starts_with(b"\x89PNG\r\n\x1a\n"));

        let mut eet_thr = [0_i16; 16];
        eet_thr[0] = 127;
        eet_thr[1] = 1;
        eet_thr[2] = 2;
        eet_thr[3] = 128;
        let eet_nids = build_synthetic_nids(
            "TLX",
            135,
            0,
            0,
            eet_thr,
            &[(0, 900, &[0, 1, 42, 170])],
            false,
        );
        let eet = decode_level3(&eet_nids).unwrap();
        assert_eq!(eet.values[42], Some(40.0));
        assert_eq!(eet.values[170], Some(40.0));
        assert!(eet.values[128].is_none());
        assert!(eet.lower_bounds.contains(&170));
        assert!(!eet.lower_bounds.contains(&42));

        let mut ref_thr = [0_i16; 16];
        ref_thr[0] = -320;
        ref_thr[1] = 5;
        ref_thr[2] = 254;
        let ref_nids = build_synthetic_nids(
            "TLX",
            94,
            5,
            0,
            ref_thr,
            &[(0, 900, &[0, 1, 100, 150])],
            false,
        );
        let refl = decode_level3(&ref_nids).unwrap();
        assert_eq!(refl.code, 94);
        assert!((refl.values[100].unwrap() - 17.0).abs() < 1e-6);
        let (_bounds, eet_png) = render_level3(&eet, &site, "echo-tops", 64).unwrap();
        let reader = png::Decoder::new(std::io::Cursor::new(eet_png))
            .read_info()
            .unwrap();
        let meta: serde_json::Value = serde_json::from_str(
            &reader
                .info()
                .uncompressed_latin1_text
                .iter()
                .find(|t| t.keyword == "sharppy-radar")
                .unwrap()
                .text,
        )
        .unwrap();
        assert_eq!(meta["folded"], serde_json::Value::Null);
        assert!(meta["lowerBounds"]
            .as_array()
            .unwrap()
            .contains(&serde_json::json!(170)));
        assert!(!meta["lowerBounds"]
            .as_array()
            .unwrap()
            .contains(&serde_json::json!(42)));
    }
}

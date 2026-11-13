//! Native US NEXRAD Level II (Archive II) BZip2 + Message 5/31 decoder.

use crate::polar_radar::{Radial, Sweep};
use crate::radar_sites::RadarSite;
use bzip2::bufread::BzDecoder;
use std::borrow::Cow;
use std::collections::HashMap;
use std::io::Read;
use time::{format_description::well_known::Rfc3339, Duration, OffsetDateTime};

pub(super) const MAX_FILE_BYTES: usize = 64 * 1024 * 1024;
const MAX_DECOMPRESSED_BYTES: usize = 256 * 1024 * 1024;
const MAX_RECORDS: usize = 100_000;
const MAX_CUTS: usize = 64;
const MAX_RADIALS_PER_SWEEP: usize = 1440;
const MAX_GATES: usize = 2400;

pub(super) const TILTS: [(&str, f64, f64); 7] = [
    ("z", 0.25, 0.45),
    ("0", 0.45, 0.85),
    ("a", 0.85, 1.15),
    ("1", 1.15, 1.65),
    ("b", 1.65, 2.15),
    ("2", 2.15, 2.75),
    ("3", 2.75, 3.75),
];

#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub(super) enum MomentKind {
    Reflectivity,
    CorrelationCoefficient,
}

impl MomentKind {
    pub(super) fn from_product(product: &str) -> Option<Self> {
        match product {
            "reflectivity" => Some(Self::Reflectivity),
            "correlation-coefficient" => Some(Self::CorrelationCoefficient),
            _ => None,
        }
    }
}

#[derive(Clone, PartialEq)]
struct MomentGeometry {
    num_gates: usize,
    first_gate_m: f64,
    gate_spacing_m: f64,
    scale: f32,
    offset: f32,
}

struct MomentRay {
    azimuth: f64,
    width: f64,
    angle: f64,
    time: OffsetDateTime,
    lat: f64,
    lon: f64,
    geometry: MomentGeometry,
    values: Vec<f32>,
}

struct ParsedRadial {
    elevation: u8,
    angle: f64,
    time: OffsetDateTime,
    status: u8,
    lat: Option<f64>,
    lon: Option<f64>,
    azimuth: f64,
    width: f64,
    ref_moment: Option<(MomentGeometry, Vec<f32>)>,
    rho_moment: Option<(MomentGeometry, Vec<f32>)>,
}

pub(super) struct DecodedCut {
    pub(super) sweep: Sweep,
    pub(super) when: OffsetDateTime,
    pub(super) num_gates: usize,
}

pub(super) struct DecodedVolume {
    pub(super) cuts: HashMap<(MomentKind, &'static str), DecodedCut>,
}

pub(super) fn decode_volume(
    bytes: &[u8],
    site: &RadarSite,
    expected: Option<OffsetDateTime>,
) -> Result<DecodedVolume, String> {
    if !(24..=MAX_FILE_BYTES).contains(&bytes.len()) || !bytes.starts_with(b"AR2V") {
        return Err("not a Level II archive volume".into());
    }
    let vol_site = std::str::from_utf8(&bytes[20..24])
        .map_err(|_| "invalid Level II volume site identifier".to_owned())?;
    let vol_time = scan_time(u32_at(bytes, 12)?, u32_at(bytes, 16)?)?;
    if !vol_site.eq_ignore_ascii_case(&site.id)
        || expected.is_some_and(|exp| (vol_time - exp).abs() > Duration::seconds(1))
    {
        return Err("volume site or time does not match the requested scan".into());
    }
    let records = unpack(&bytes[24..], MAX_DECOMPRESSED_BYTES)?;
    let mut offset = 0;
    let mut record_count = 0;
    let mut vcp_angles: Vec<f64> = Vec::new();
    let mut sweeps: Vec<Vec<ParsedRadial>> = Vec::new();
    let mut current_el: Option<u8> = None;

    while offset < records.len() {
        record_count += 1;
        if record_count > MAX_RECORDS {
            return Err("too many Archive II records".into());
        }
        let header = records
            .get(offset + 12..offset + 28)
            .ok_or("truncated Archive II message header")?;
        let halfwords = u16_at(header, 0)?;
        let kind = header[3];
        let length = match halfwords {
            0 => 2432,
            u16::MAX => (u32_at(header, 12)? as usize)
                .checked_add(12)
                .ok_or("oversized Archive II message")?,
            _ if matches!(kind, 29 | 31) => 12 + usize::from(halfwords) * 2,
            _ if halfwords <= 1208 => 2432,
            _ => return Err("invalid legacy Archive II record length".into()),
        };
        if !(28..=256 * 1024).contains(&length) {
            return Err("invalid Archive II message length".into());
        }
        let message = records
            .get(offset..offset + length)
            .ok_or("truncated Archive II message")?;
        if halfwords == 0 {
            if header.iter().any(|byte| *byte != 0) {
                return Err("invalid Archive II padding record".into());
            }
        } else if kind == 5 {
            if let Some(angles) = parse_vcp(&message[28..])? {
                vcp_angles = angles;
            }
        } else if kind == 31 {
            if halfwords != u16::MAX && (u16_at(header, 12)? != 1 || u16_at(header, 14)? != 1) {
                return Err("segmented Archive II radials are unsupported".into());
            }
            let radial = read_ray(&message[28..], site.id.as_bytes())?;
            let started = matches!(radial.status, 0 | 3 | 5);
            if sweeps.is_empty() || started || current_el != Some(radial.elevation) {
                if sweeps.len() >= MAX_CUTS {
                    return Err("too many Archive II elevations".into());
                }
                current_el = Some(radial.elevation);
                sweeps.push(Vec::new());
            }
            sweeps.last_mut().expect("sweep created above").push(radial);
        }
        offset += length;
    }

    let mut cuts: HashMap<(MomentKind, &'static str), DecodedCut> = HashMap::new();
    for moment_kind in [MomentKind::Reflectivity, MomentKind::CorrelationCoefficient] {
        for (sweep_index, sweep_rays) in sweeps.iter().enumerate() {
            let mut rays: Vec<MomentRay> = Vec::new();
            for ray in sweep_rays {
                let chosen = match moment_kind {
                    MomentKind::Reflectivity => ray.ref_moment.as_ref(),
                    MomentKind::CorrelationCoefficient => ray.rho_moment.as_ref(),
                };
                let Some((geometry, values)) = chosen else {
                    continue;
                };
                let (Some(lat), Some(lon)) = (ray.lat, ray.lon) else {
                    return Err("unsupported Level II moment geometry".into());
                };
                rays.push(MomentRay {
                    azimuth: ray.azimuth,
                    width: ray.width,
                    angle: ray.angle,
                    time: ray.time,
                    lat,
                    lon,
                    geometry: geometry.clone(),
                    values: values.clone(),
                });
            }
            if rays.is_empty() {
                continue;
            }
            if rays.len() > MAX_RADIALS_PER_SWEEP {
                return Err("unsupported Level II moment geometry".into());
            }
            let angle = vcp_angles
                .get(sweep_index)
                .copied()
                .unwrap_or_else(|| median_angle(&rays));
            let Some(tilt) = tilt_for_angle(angle) else {
                continue;
            };
            let first = &rays[0];
            if !first.lat.is_finite()
                || !first.lon.is_finite()
                || (first.lat - site.lat).abs() > 1.0
                || (first.lon - site.lon).abs() > 1.0
            {
                return Err("unsupported Level II moment geometry".into());
            }
            let geometry = &first.geometry;
            if !valid_geometry(geometry) {
                return Err("unsupported Level II moment geometry".into());
            }
            for ray in &rays[1..] {
                if ray.geometry != *geometry || ray.values.len() != geometry.num_gates {
                    return Err("inconsistent Level II moment gates".into());
                }
            }
            if expected
                .is_some_and(|exp| first.time < exp || first.time > exp + Duration::minutes(15))
            {
                return Err("invalid Level II sweep time".into());
            }
            let sweep_time = first.time;
            let first_gate_m = geometry.first_gate_m;
            let gate_spacing_m = geometry.gate_spacing_m;
            let num_gates = geometry.num_gates;
            let radials = rays
                .into_iter()
                .map(|ray| Radial {
                    azimuth: ray.azimuth,
                    width: ray.width,
                    elevation_angle: None,
                    first_gate_m,
                    gate_spacing_m,
                    values: ray.values,
                })
                .collect::<Vec<_>>();
            let candidate = DecodedCut {
                sweep: Sweep {
                    site_id: site.id.clone(),
                    valid_time: sweep_time.format(&Rfc3339).map_err(|e| e.to_string())?,
                    elevation_angle: angle,
                    radials,
                },
                when: sweep_time,
                num_gates,
            };
            let replace = match cuts.get(&(moment_kind, tilt)) {
                None => true,
                Some(prev) => (candidate.num_gates, candidate.when) > (prev.num_gates, prev.when),
            };
            if replace {
                cuts.insert((moment_kind, tilt), candidate);
            }
        }
    }
    if cuts.is_empty() {
        return Err("volume contains no usable radar cuts".into());
    }
    Ok(DecodedVolume { cuts })
}

fn tilt_for_angle(angle: f64) -> Option<&'static str> {
    TILTS
        .iter()
        .find(|(_, low, high)| *low <= angle && angle < *high)
        .map(|(tilt, _, _)| *tilt)
}

fn median_angle(rays: &[MomentRay]) -> f64 {
    let mut angles = rays.iter().map(|r| r.angle).collect::<Vec<_>>();
    angles.sort_unstable_by(f64::total_cmp);
    let mid = angles.len() / 2;
    if angles.len().is_multiple_of(2) {
        (angles[mid - 1] + angles[mid]) * 0.5
    } else {
        angles[mid]
    }
}

fn valid_geometry(geometry: &MomentGeometry) -> bool {
    (1..=MAX_GATES).contains(&geometry.num_gates)
        && geometry.gate_spacing_m > 0.0
        && geometry.gate_spacing_m <= 1000.0
        && geometry.first_gate_m >= 0.0
        && geometry.first_gate_m <= 10_000.0
        && geometry.first_gate_m + (geometry.num_gates as f64) * geometry.gate_spacing_m
            <= 500_000.0
        && geometry.scale.is_finite()
        && geometry.scale > 0.0
        && geometry.offset.is_finite()
}

fn parse_vcp(bytes: &[u8]) -> Result<Option<Vec<f64>>, String> {
    if bytes.len() < 22 {
        return Ok(None);
    }
    let size_hw = u16_at(bytes, 0)?;
    if size_hw == 0 {
        return Ok(None);
    }
    let num_cuts = usize::from(u16_at(bytes, 6)?);
    if num_cuts > MAX_CUTS || 22 + num_cuts * 46 > bytes.len() {
        return Ok(None);
    }
    let mut angles = Vec::with_capacity(num_cuts);
    for index in 0..num_cuts {
        let raw = u16_at(bytes, 22 + index * 46)?;
        angles.push(f64::from(raw) * 360.0 / 65_536.0);
    }
    Ok(Some(angles))
}

fn unpack(bytes: &[u8], limit: usize) -> Result<Cow<'_, [u8]>, String> {
    if bytes.get(4..7) != Some(b"BZh") {
        return if bytes.len() <= limit {
            Ok(Cow::Borrowed(bytes))
        } else {
            Err("Archive II decompressed byte limit exceeded".into())
        };
    }
    let mut output = Vec::new();
    let mut offset = 0;
    while offset < bytes.len() {
        let control = i32::from_be_bytes(field(bytes, offset)?);
        let length = control.unsigned_abs() as usize;
        offset += 4;
        if length == 0 || length > MAX_FILE_BYTES || (control < 0 && offset + length != bytes.len())
        {
            return Err("invalid Archive II compression block length".into());
        }
        let block = bytes
            .get(offset..offset + length)
            .ok_or("truncated Archive II compression block")?;
        let mut decoder = BzDecoder::new(block);
        decoder
            .by_ref()
            .take((limit - output.len() + 1) as u64)
            .read_to_end(&mut output)
            .map_err(|e| format!("invalid Archive II BZip2 block: {e}"))?;
        if output.len() > limit {
            return Err("Archive II decompressed byte limit exceeded".into());
        }
        if decoder.total_in() != length as u64 {
            return Err("trailing bytes in Archive II compression block".into());
        }
        offset += length;
    }
    Ok(Cow::Owned(output))
}

fn read_ray(bytes: &[u8], site: &[u8]) -> Result<ParsedRadial, String> {
    if bytes.len() < 32 || !bytes[..4].eq_ignore_ascii_case(site) {
        return Err("truncated Archive II radial or mismatched site".into());
    }
    let time = scan_time(u32::from(u16_at(bytes, 8)?), u32_at(bytes, 4)?)?;
    let azimuth = f64::from(f32_at(bytes, 12)?);
    let angle = f64::from(f32_at(bytes, 24)?);
    let elevation = bytes[22];
    let status = bytes[21] & 0x0f;
    if !azimuth.is_finite()
        || !(0.0..360.0).contains(&azimuth)
        || !angle.is_finite()
        || !(-1.0..=90.0).contains(&angle)
        || elevation == 0
        || status > 5
        || bytes[16] != 0
    {
        return Err("invalid or unsupported Archive II radial header".into());
    }
    let width = match bytes[20] {
        1 => 0.5,
        2 => 1.0,
        _ => return Err("unsupported Archive II angular spacing".into()),
    };
    let radial_length = usize::from(u16_at(bytes, 18)?);
    let count = usize::from(u16_at(bytes, 30)?);
    let table_end = 32 + count * 4;
    if !(1..=10).contains(&count) || radial_length < table_end || radial_length > bytes.len() {
        return Err("invalid Archive II radial length or block count".into());
    }
    let bytes = &bytes[..radial_length];
    let mut pointers = Vec::with_capacity(count);
    for index in 0..count {
        let pointer = u32_at(bytes, 32 + 4 * index)? as usize;
        if pointer != 0 {
            if pointer < table_end || pointer > radial_length.saturating_sub(4) {
                return Err("Archive II block pointer is outside radial data".into());
            }
            pointers.push(pointer);
        }
    }
    pointers.sort_unstable();
    let mut lat = None;
    let mut lon = None;
    let mut ref_moment = None;
    let mut rho_moment = None;
    for (index, &start) in pointers.iter().enumerate() {
        let end = pointers.get(index + 1).copied().unwrap_or(radial_length);
        let block = &bytes[start..end];
        if block.len() < 4 {
            return Err("duplicate or overlapping Archive II block pointers".into());
        }
        match block[0] {
            b'R' => {
                let length = usize::from(u16_at(block, 4)?);
                if length < 6 || length > block.len() {
                    return Err("truncated Archive II constant block".into());
                }
                if &block[1..4] == b"VOL" && block.len() >= 16 {
                    lat = Some(f64::from(f32_at(block, 8)?));
                    lon = Some(f64::from(f32_at(block, 12)?));
                }
            }
            b'D' => {
                if block.len() < 28 {
                    return Err("truncated Archive II moment header".into());
                }
                let name = &block[1..4];
                let is_ref = name == b"REF";
                let is_rho = name == b"RHO";
                if !is_ref && !is_rho {
                    continue;
                }
                let gates = usize::from(u16_at(block, 8)?);
                if block[19] != 8 {
                    return Err("unsupported Archive II moment word size".into());
                }
                if gates > MAX_GATES || 28 + gates > block.len() {
                    return Err("truncated or oversized Archive II gate data".into());
                }
                let scale = f32_at(block, 20)?;
                let offset = f32_at(block, 24)?;
                let geometry = MomentGeometry {
                    num_gates: gates,
                    first_gate_m: f64::from(u16_at(block, 10)?),
                    gate_spacing_m: f64::from(u16_at(block, 12)?),
                    scale,
                    offset,
                };
                if !valid_geometry(&geometry) {
                    return Err("unsupported Level II moment geometry".into());
                }
                let mut values = Vec::with_capacity(gates);
                for &raw in &block[28..28 + gates] {
                    let value = match raw {
                        0 => f32::NAN,
                        1 if is_ref => f32::INFINITY,
                        1 => f32::NAN,
                        _ => {
                            let value = (f32::from(raw) - offset) / scale;
                            if !value.is_finite() {
                                return Err("non-finite calibrated Archive II moment".into());
                            }
                            value
                        }
                    };
                    values.push(value);
                }
                if is_ref {
                    if ref_moment.is_some() {
                        return Err("duplicate REF moment in radial".into());
                    }
                    ref_moment = Some((geometry, values));
                } else {
                    if rho_moment.is_some() {
                        return Err("duplicate RHO moment in radial".into());
                    }
                    rho_moment = Some((geometry, values));
                }
            }
            _ => return Err("unknown Archive II radial data block".into()),
        }
    }
    Ok(ParsedRadial {
        elevation,
        angle,
        time,
        status,
        lat,
        lon,
        azimuth,
        width,
        ref_moment,
        rho_moment,
    })
}

fn scan_time(day: u32, millis: u32) -> Result<OffsetDateTime, String> {
    if day == 0 || millis >= 86_400_000 {
        return Err("invalid Archive II acquisition time".into());
    }
    let nanos = ((i128::from(day) - 1) * 86_400_000 + i128::from(millis)) * 1_000_000;
    OffsetDateTime::from_unix_timestamp_nanos(nanos)
        .map_err(|e| format!("invalid Archive II acquisition date: {e}"))
}

fn field<const N: usize>(bytes: &[u8], offset: usize) -> Result<[u8; N], String> {
    bytes
        .get(offset..)
        .and_then(|rest| rest.get(..N))
        .and_then(|value| value.try_into().ok())
        .ok_or_else(|| "truncated Archive II field".into())
}

fn u16_at(bytes: &[u8], offset: usize) -> Result<u16, String> {
    field(bytes, offset).map(u16::from_be_bytes)
}

fn u32_at(bytes: &[u8], offset: usize) -> Result<u32, String> {
    field(bytes, offset).map(u32::from_be_bytes)
}

fn f32_at(bytes: &[u8], offset: usize) -> Result<f32, String> {
    field(bytes, offset).map(f32::from_be_bytes)
}

//! Native MRRL Archive II message 31 reflectivity and velocity decoding.
//!
//! Field layout follows NOAA RDA/RPG ICD 2620002, message 31. Archive control
//! words frame independent BZip2 streams; they are not radial boundaries.
//! REF values remain in dBZ and VEL values in m/s, using each moment's source
//! scale and offset. Missing gates are NaN; range-folded velocity is +infinity.

use bzip2::bufread::BzDecoder;
use std::borrow::Cow;
use std::io::Read;
use time::{format_description::well_known::Rfc3339, OffsetDateTime};

const MAX_FILE_BYTES: usize = 32 * 1024 * 1024;
const MAX_DECOMPRESSED_BYTES: usize = 64 * 1024 * 1024;
const MAX_RECORDS: usize = 100_000;
const MAX_CUTS: usize = 64;
const MAX_RADIALS: usize = 720;
const MAX_GATES: usize = 8192;

#[derive(Debug)]
pub struct Sweep {
    pub site_id: String,
    /// Earliest radial acquisition time in the selected complete sweep (UTC).
    pub valid_time: String,
    pub elevation_angle: f64,
    pub radials: Vec<Radial>,
}

#[derive(Debug)]
pub struct Radial {
    /// Centre angle, clockwise from north, in degrees.
    pub azimuth: f64,
    /// Encoded angular sample width, in degrees; never widened over missing rays.
    pub width: f64,
    /// Range to the centre of the first gate, in metres.
    pub first_gate_m: f64,
    pub gate_spacing_m: f64,
    /// Unclamped physical dBZ (REF) or m/s (VEL). Missing gates are NaN;
    /// range-folded VEL is +infinity, while range-folded REF remains NaN.
    pub values: Vec<f32>,
}

struct Cut {
    number: u8,
    angle: f64,
    time: OffsetDateTime,
    started: bool,
    seen: [bool; MAX_RADIALS],
    radials: Vec<Radial>,
}

struct Ray {
    number: usize,
    elevation: u8,
    angle: f64,
    time: OffsetDateTime,
    status: u8,
    data: Option<Radial>,
}

/// Decode the lowest elevation containing the requested moment with both start
/// and end markers. Missing sectors remain absent rather than being interpolated
/// into neighbouring radial widths.
pub fn decode(bytes: &[u8], product: &str) -> Result<Sweep, String> {
    let moment = match product {
        "reflectivity" => b"REF",
        "velocity" => b"VEL",
        _ => return Err("Australian Archive II supports reflectivity and velocity only".into()),
    };
    if !(24..=MAX_FILE_BYTES).contains(&bytes.len()) || !bytes.starts_with(b"AR2V0006.") {
        return Err("invalid or oversized Australian Archive II volume".into());
    }
    let site = &bytes[20..24];
    if !site.iter().all(u8::is_ascii_alphanumeric) {
        return Err("invalid Archive II site identifier".into());
    }
    let site_id = String::from_utf8(site.to_vec()).map_err(|e| e.to_string())?;
    scan_time(u32_at(bytes, 12)?, u32_at(bytes, 16)?)?;
    let records = unpack(&bytes[24..], MAX_DECOMPRESSED_BYTES)?;
    let mut offset = 0;
    let mut record_count = 0;
    let mut cuts = 0;
    let mut current: Option<Cut> = None;
    let mut best: Option<Cut> = None;
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
        } else if kind == 31 {
            if halfwords != u16::MAX && (u16_at(header, 12)? != 1 || u16_at(header, 14)? != 1) {
                return Err("segmented Archive II radials are unsupported".into());
            }
            let ray = read_ray(&message[28..], site, moment)?;
            let started = matches!(ray.status, 0 | 3 | 5);
            if started || current.as_ref().map(|cut| cut.number) != Some(ray.elevation) {
                cuts += 1;
                if cuts > MAX_CUTS {
                    return Err("too many Archive II elevations".into());
                }
                current = Some(Cut {
                    number: ray.elevation,
                    angle: ray.angle,
                    time: ray.time,
                    started,
                    seen: [false; MAX_RADIALS],
                    radials: Vec::new(),
                });
            }
            let cut = current.as_mut().ok_or("missing Archive II elevation")?;
            if (cut.angle - ray.angle).abs() > 0.3 || cut.seen[ray.number - 1] {
                return Err("inconsistent elevation or duplicate Archive II radial".into());
            }
            cut.seen[ray.number - 1] = true;
            cut.time = cut.time.min(ray.time);
            if let Some(radial) = ray.data {
                cut.radials.push(radial);
            }
            if matches!(ray.status, 2 | 4) {
                let mut completed = current.take().ok_or("missing Archive II elevation")?;
                if completed.started
                    && !completed.radials.is_empty()
                    && best.as_ref().is_none_or(|cut| completed.angle < cut.angle)
                {
                    completed
                        .radials
                        .sort_unstable_by(|a, b| a.azimuth.total_cmp(&b.azimuth));
                    best = Some(completed);
                }
            }
        } else if kind == 1 {
            return Err(
                "legacy Archive II message 1 is unsupported; message 31 is required".into(),
            );
        }
        offset += length;
    }
    let cut =
        best.ok_or_else(|| format!("no complete {product} elevation in Archive II volume"))?;
    Ok(Sweep {
        site_id,
        valid_time: cut.time.format(&Rfc3339).map_err(|e| e.to_string())?,
        elevation_angle: cut.angle,
        radials: cut.radials,
    })
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

fn read_ray(bytes: &[u8], site: &[u8], moment: &[u8; 3]) -> Result<Ray, String> {
    if bytes.len() < 32 || &bytes[..4] != site {
        return Err("truncated Archive II radial or mismatched site".into());
    }
    let time = scan_time(u32::from(u16_at(bytes, 8)?), u32_at(bytes, 4)?)?;
    let number = usize::from(u16_at(bytes, 10)?);
    let azimuth = f64::from(f32_at(bytes, 12)?);
    let angle = f64::from(f32_at(bytes, 24)?);
    let elevation = bytes[22];
    let status = bytes[21] & 0x0f;
    if !(1..=MAX_RADIALS).contains(&number)
        || !azimuth.is_finite()
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
    if !(1..=10).contains(&count)
        || radial_length < table_end
        || radial_length > bytes.len()
        || bytes.len() - radial_length > 1
    {
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
    let mut data = None;
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
            }
            b'D' => {
                if block.len() < 28 {
                    return Err("truncated Archive II moment header".into());
                }
                let gates = usize::from(u16_at(block, 8)?);
                let word_bytes = match block[19] {
                    8 => 1,
                    16 => 2,
                    _ => return Err("unsupported Archive II moment word size".into()),
                };
                if gates > MAX_GATES || 28 + gates * word_bytes > block.len() {
                    return Err("truncated or oversized Archive II gate data".into());
                }
                let scale = f32_at(block, 20)?;
                let offset = f32_at(block, 24)?;
                if !scale.is_finite() || scale <= 0.0 || !offset.is_finite() {
                    return Err("invalid Archive II moment calibration".into());
                }
                if &block[1..4] == moment {
                    if data.is_some() || u16_at(block, 12)? == 0 {
                        return Err("duplicate requested moment or invalid gate spacing".into());
                    }
                    let mut values = Vec::with_capacity(gates);
                    for gate in 0..gates {
                        let raw = if word_bytes == 1 {
                            u16::from(block[28 + gate])
                        } else {
                            u16_at(block, 28 + 2 * gate)?
                        };
                        let value = match raw {
                            0 => f32::NAN,
                            1 if moment == b"VEL" => f32::INFINITY,
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
                    data = Some(Radial {
                        azimuth,
                        width,
                        first_gate_m: f64::from(i16::from_be_bytes(field(block, 10)?)),
                        gate_spacing_m: f64::from(u16_at(block, 12)?),
                        values,
                    });
                }
            }
            _ => return Err("unknown Archive II radial data block".into()),
        }
    }
    // Bad-data status and spot blanking must not turn into painted sectors.
    if bytes[21] & 0xf0 != 0 || bytes[28] & 7 != 0 {
        data = None;
    }
    Ok(Ray {
        number,
        elevation,
        angle,
        time,
        status,
        data,
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

#[cfg(test)]
#[path = "decode_tests.rs"]
mod tests;

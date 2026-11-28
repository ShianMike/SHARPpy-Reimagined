//! Decode only the requested fields from an hourly NSSL MPAS GRIB2 member.

use super::*;
use numpy::ndarray::Array2;

const WIDTH: usize = 1799;
const HEIGHT: usize = 1059;
const POINTS: usize = WIDTH * HEIGHT;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Surface(u8, i32);

const SFC: Surface = Surface(1, 0);
const SEA_LEVEL: Surface = Surface(101, 0);
const ATMOSPHERE: Surface = Surface(10, 0);
const AGL_2M: Surface = Surface(103, 2);
const AGL_10M: Surface = Surface(103, 10);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Period {
    Instant,
    RunTotal,
    HourMaximum,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct Record {
    category: u8,
    number: u8,
    first: Surface,
    second: Option<Surface>,
    period: Period,
    pub(crate) minimum_hour: u32,
}

fn instant(category: u8, number: u8, first: Surface, minimum_hour: u32) -> Record {
    Record {
        category,
        number,
        first,
        second: None,
        period: Period::Instant,
        minimum_hour,
    }
}

fn layer(mut record: Record, second: Surface) -> Record {
    record.second = Some(second);
    record
}

pub(crate) fn record(name: &str) -> Option<Record> {
    let surface = match name {
        "mslp" => instant(3, 1, SEA_LEVEL, 0),
        "pres" => instant(3, 0, SFC, 1),
        "tmp2m" => instant(0, 0, AGL_2M, 1),
        "dpt2m" => instant(0, 6, AGL_2M, 1),
        "u10" => instant(2, 2, AGL_10M, 1),
        "v10" => instant(2, 3, AGL_10M, 1),
        "gust" => instant(2, 22, SFC, 1),
        "pwat" => instant(1, 3, ATMOSPHERE, 1),
        "snod" => instant(1, 11, SFC, 1),
        "crain" => instant(1, 33, SFC, 1),
        "cfrzr" => instant(1, 34, SFC, 1),
        "cicep" => instant(1, 35, SFC, 1),
        "csnow" => instant(1, 36, SFC, 1),
        "prate" => instant(1, 7, SFC, 1),
        "apcp" => Record {
            period: Period::RunTotal,
            ..instant(1, 8, SFC, 1)
        },
        "refc" => instant(16, 196, ATMOSPHERE, 1),
        "refd1km" => instant(16, 195, Surface(103, 1000), 1),
        "sbcape" => instant(7, 6, SFC, 1),
        "sbcin" => instant(7, 7, SFC, 1),
        "mlcape" => layer(instant(7, 6, Surface(108, 18_000), 1), Surface(108, 0)),
        "mlcin" => layer(instant(7, 7, Surface(108, 18_000), 1), Surface(108, 0)),
        "mucape" => layer(instant(7, 6, Surface(108, 25_500), 1), Surface(108, 0)),
        "lcl" => instant(3, 5, Surface(5, 0), 1),
        "srh01" => layer(instant(7, 8, Surface(103, 1000), 1), Surface(103, 0)),
        "srh03" => layer(instant(7, 8, Surface(103, 3000), 1), Surface(103, 0)),
        "ushr01" => layer(instant(2, 15, Surface(103, 0), 1), Surface(103, 1000)),
        "vshr01" => layer(instant(2, 16, Surface(103, 0), 1), Surface(103, 1000)),
        "ushr06" => layer(instant(2, 15, Surface(103, 0), 1), Surface(103, 6000)),
        "vshr06" => layer(instant(2, 16, Surface(103, 0), 1), Surface(103, 6000)),
        "uh03" | "uh25" => {
            let (top, bottom) = if name == "uh03" {
                (3000, 0)
            } else {
                (5000, 2000)
            };
            Record {
                period: Period::HourMaximum,
                ..layer(instant(7, 199, Surface(103, top), 1), Surface(103, bottom))
            }
        }
        _ => {
            let (quantity, pressure) = name.split_at_checked(3)?;
            let (category, number) = match quantity {
                "hgt" => (3, 5),
                "tmp" => (0, 0),
                _ => return pressure_wind_record(name),
            };
            let pressure: i32 = pressure.parse().ok()?;
            if !matches!(pressure, 500 | 700 | 850 | 1000) {
                return None;
            }
            instant(category, number, Surface(100, pressure * 100), 0)
        }
    };
    Some(surface)
}

fn pressure_wind_record(name: &str) -> Option<Record> {
    let (component, pressure) = name.split_at_checked(4)?;
    let pressure: i32 = pressure.parse().ok()?;
    if !matches!(pressure, 500 | 700 | 850) {
        return None;
    }
    let number = match component {
        "ugrd" => 2,
        "vgrd" => 3,
        _ => return None,
    };
    Some(instant(2, number, Surface(100, pressure * 100), 0))
}

type GetDoubleArray =
    unsafe extern "C" fn(*const c_void, *const c_char, *mut f64, *mut usize) -> c_int;

pub(crate) fn decode(
    library: &Path,
    payload: &[u8],
    records: &[(String, Record)],
    hour: u32,
    stopped: &impl Fn() -> Result<bool, String>,
) -> Result<HashMap<String, Array2<f32>>, sharpmod_weather::model_http::Error> {
    use sharpmod_weather::model_http::Error;
    let malformed = |error: GribError| Error::Invalid(format!("NSSL MPAS GRIB2: {error}"));
    let boundaries = scan_message_boundaries(payload).map_err(malformed)?;
    let api = EccodesApi::cached(library).map_err(malformed)?;
    // SAFETY: the retained library exports this documented ecCodes function.
    let get_array: GetDoubleArray =
        unsafe { load_symbol(&api._library, b"codes_get_double_array\0").map_err(malformed)? };
    let mut fields = HashMap::with_capacity(records.len());
    let mut values = vec![0.0; POINTS];
    for boundary in boundaries {
        if stopped().map_err(Error::Invalid)? {
            return Err(Error::Cancelled);
        }
        let message = &payload[boundary.offset..boundary.offset + boundary.length];
        if boundary.edition != 2 || message[6] != 0 {
            continue;
        }
        let _lock = ECCODES_CALL_LOCK
            .lock()
            .map_err(|_| Error::Invalid("ecCodes call lock was poisoned".into()))?;
        let _multi = api.enable_multi_support();
        let mut cursor = message.as_ptr().cast_mut().cast::<c_void>();
        let mut remaining = message.len();
        while let Some(handle) = api
            .next_multi_handle(message, &mut cursor, &mut remaining)
            .map_err(malformed)?
        {
            let raw = handle.as_ptr();
            let Some(address) = address(api, raw, hour) else {
                continue;
            };
            for (name, request) in records {
                if !address.matches(*request) || fields.contains_key(name) {
                    continue;
                }
                validate_grid(api, raw).map_err(malformed)?;
                // Complex packing can encode missing grid points without a bitmap.
                let missing = api.get_optional_double(raw, MISSING_VALUE_KEY);
                let mut length = values.len();
                // SAFETY: the output buffer is writable for exactly `length` f64 values.
                let code = unsafe {
                    get_array(
                        raw,
                        VALUES_KEY.as_ptr().cast(),
                        values.as_mut_ptr(),
                        &mut length,
                    )
                };
                api.check(code, "read NSSL MPAS values")
                    .map_err(malformed)?;
                if length != POINTS {
                    return Err(Error::Invalid("NSSL MPAS grid length changed".into()));
                }
                let values = values
                    .iter()
                    .map(|value| {
                        if !value.is_finite() || Some(*value) == missing {
                            f32::NAN
                        } else {
                            *value as f32
                        }
                    })
                    .collect();
                let array = Array2::from_shape_vec((HEIGHT, WIDTH), values)
                    .expect("the decoded NSSL grid has the validated shape");
                fields.insert(name.clone(), array);
            }
        }
        if fields.len() == records.len() {
            return Ok(fields);
        }
    }
    Ok(fields)
}

impl Record {
    fn matches(self, other: Self) -> bool {
        self.category == other.category
            && self.number == other.number
            && self.period == other.period
            && ((self.first == other.first && self.second == other.second)
                || (self.second == Some(other.first) && other.second == Some(self.first)))
    }
}

fn address(api: &EccodesApi, raw: *const c_void, hour: u32) -> Option<Record> {
    let integer = |key: &'static [u8]| api.get_long_value(raw, key).ok();
    let surface = |kind, factor, value| {
        let kind = u8::try_from(integer(kind)?).ok()?;
        let scaled = match kind {
            1 | 5 | 10 | 101 | 200 => 0,
            100 | 103 | 108 => {
                let scale = i32::try_from(integer(factor)?).ok()?;
                let value = integer(value)? as f64 * 10_f64.powi(-scale);
                if !value.is_finite() || value < i32::MIN as f64 || value > i32::MAX as f64 {
                    return None;
                }
                value.round() as i32
            }
            _ => return None,
        };
        Some(Surface(if kind == 200 { 10 } else { kind }, scaled))
    };
    let category = u8::try_from(integer(b"parameterCategory\0")?).ok()?;
    let number = match (category, u8::try_from(integer(b"parameterNumber\0")?).ok()?) {
        (16, 4) => 195,
        (16, 5) => 196,
        (_, number) => number,
    };
    let first = surface(
        b"typeOfFirstFixedSurface\0",
        b"scaleFactorOfFirstFixedSurface\0",
        b"scaledValueOfFirstFixedSurface\0",
    )?;
    let second = match integer(b"typeOfSecondFixedSurface\0")? {
        255 => None,
        _ => Some(surface(
            b"typeOfSecondFixedSurface\0",
            b"scaleFactorOfSecondFixedSurface\0",
            b"scaledValueOfSecondFixedSurface\0",
        )?),
    };
    let period = match integer(b"productDefinitionTemplateNumber\0")? {
        0 => Period::Instant,
        8 if integer(b"stepUnits\0")? == 1 && integer(b"endStep\0")? == hour as c_long => {
            match (
                integer(b"typeOfStatisticalProcessing\0")?,
                integer(b"startStep\0")?,
            ) {
                (1, 0) => Period::RunTotal,
                (2, start) if start.checked_add(1) == Some(hour as c_long) => Period::HourMaximum,
                _ => return None,
            }
        }
        _ => return None,
    };
    Some(Record {
        category,
        number,
        first,
        second,
        period,
        minimum_hour: 0,
    })
}

fn validate_grid(api: &EccodesApi, raw: *const c_void) -> Result<(), GribError> {
    for (key, expected) in [
        (b"Nx\0".as_slice(), WIDTH as c_long),
        (b"Ny\0", HEIGHT as c_long),
        (NUMBER_OF_POINTS_KEY, POINTS as c_long),
        (b"iScansNegatively\0", 0),
        (b"jScansPositively\0", 1),
        (b"jPointsAreConsecutive\0", 0),
        (b"alternativeRowScanning\0", 0),
        (b"uvRelativeToGrid\0", 1),
    ] {
        if api.get_long_value(raw, key)? != expected {
            return Err(GribError::new(format!(
                "unexpected MPAS grid ({})",
                String::from_utf8_lossy(&key[..key.len() - 1])
            )));
        }
    }
    for (key, expected) in [
        (FIRST_LATITUDE_KEY, 21.138123),
        (FIRST_LONGITUDE_KEY, 237.280472),
        (b"LoVInDegrees\0", 262.5),
        (b"LaDInDegrees\0", 38.5),
        (b"Latin1InDegrees\0", 38.5),
        (b"Latin2InDegrees\0", 38.5),
        (b"DxInMetres\0", 3000.0),
        (b"DyInMetres\0", 3000.0),
        (b"radius\0", 6371229.0),
    ] {
        if (api.get_double_value(raw, key)? - expected).abs() > 1e-5 {
            return Err(GribError::new(format!(
                "unexpected MPAS projection ({})",
                String::from_utf8_lossy(&key[..key.len() - 1])
            )));
        }
    }
    if api.get_string_value(raw, b"gridType\0")? != "lambert" {
        return Err(GribError::new("MPAS grid is not Lambert conformal"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use sharpmod_weather::bridge_cache::decompress_gzip_bounded;

    #[test]
    #[ignore = "requires NOAA NSSL MPAS F001 samples and ecCodes"]
    fn nssl_mpas_f001_archive_fields() {
        let directory = PathBuf::from(std::env::var_os("SHARPMOD_MPAS_SAMPLE_DIR").unwrap());
        let library = PathBuf::from(std::env::var_os("SHARPMOD_MPAS_ECCODES").unwrap());
        let names = [
            "mslp", "pres", "tmp2m", "dpt2m", "u10", "v10", "gust", "pwat", "snod", "crain",
            "cfrzr", "cicep", "csnow", "prate", "apcp", "refc", "refd1km", "sbcape", "sbcin",
            "mlcape", "mlcin", "mucape", "lcl", "srh01", "srh03", "ushr01", "vshr01", "ushr06",
            "vshr06", "uh03", "uh25", "tmp700", "tmp500", "hgt1000", "hgt850", "hgt700", "hgt500",
            "ugrd850", "vgrd850", "ugrd700", "vgrd700", "ugrd500", "vgrd500",
        ];
        let records = names
            .into_iter()
            .map(|name| (name.to_owned(), record(name).unwrap()))
            .collect::<Vec<_>>();
        for name in ["HTPO", "RN"] {
            let gzip = File::open(directory.join(format!("{name}-F001.grib2.gz"))).unwrap();
            let grib = decompress_gzip_bounded(gzip, 192 * 1024 * 1024).unwrap();
            let fields = decode(&library, &grib, &records, 1, &|| Ok(false)).unwrap();
            let missing = names
                .into_iter()
                .filter(|key| !fields.contains_key(*key))
                .collect::<Vec<_>>();
            assert!(missing.is_empty(), "{name} missing GRIB fields {missing:?}");
            assert_eq!(fields["mslp"].dim(), (HEIGHT, WIDTH));
            let pressure = fields["mslp"]
                .iter()
                .copied()
                .filter(|value| value.is_finite());
            let (valid, min, max) = pressure.fold(
                (0, f32::INFINITY, f32::NEG_INFINITY),
                |(count, min, max), value| (count + 1, value.min(min), value.max(max)),
            );
            assert!(
                valid >= POINTS / 2
                    && (85_000.0..110_000.0).contains(&min)
                    && (85_000.0..110_000.0).contains(&max),
                "{name} MSLP has {valid} valid points and a range of {min}–{max} Pa"
            );
            assert!(fields["u10"].iter().any(|&value| value.abs() > 5.0));
            assert!(fields["hgt500"]
                .iter()
                .zip(fields["hgt1000"].iter())
                .any(|(&top, &bottom)| (4500.0..6500.0).contains(&(top - bottom))));
            assert!(fields["crain"].iter().any(|&value| value == 1.0));
        }
    }
}

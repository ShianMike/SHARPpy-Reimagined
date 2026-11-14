//! Decode one validated GDPS Datamart field into the map's regular grid order.

use super::*;
use crate::gdps_fields::{Frame, Model, Period, Record, HEIGHT, SPACING, WIDTH};
use crate::regional_grids::{
    RDPS_HEIGHT, RDPS_POLE_LAT, RDPS_POLE_LON, RDPS_SPACING, RDPS_WIDTH, RDPS_X0, RDPS_Y0,
};

type GetDoubleArray =
    unsafe extern "C" fn(*const c_void, *const c_char, *mut f64, *mut usize) -> c_int;

pub(crate) fn decode(
    library: &Path,
    payload: &[u8],
    frame: &Frame,
    record: Record,
) -> Result<Vec<f32>, GribError> {
    if payload.len() < 20
        || !payload.starts_with(b"GRIB")
        || payload[7] != 2
        || u64::from_be_bytes(payload[8..16].try_into().expect("checked GRIB header"))
            != payload.len() as u64
        || !payload.ends_with(b"7777")
    {
        return Err(GribError::new("GDPS did not return a complete GRIB2 field"));
    }
    let _lock = ECCODES_CALL_LOCK
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    let api = EccodesApi::cached(library)?;
    let _multi = api.enable_multi_support();
    let mut cursor = payload.as_ptr().cast_mut().cast::<c_void>();
    let mut remaining = payload.len();
    let handle = api
        .next_multi_handle(payload, &mut cursor, &mut remaining)?
        .ok_or_else(|| GribError::new("GDPS GRIB2 field could not be opened"))?;
    let pointer = handle.as_ptr();
    let regional = frame.model == Model::Rdps;
    let (width, height, spacing) = if regional {
        (RDPS_WIDTH, RDPS_HEIGHT, RDPS_SPACING)
    } else {
        (WIDTH, HEIGHT, SPACING)
    };
    let (level_type, level) = record.level.metadata();
    let (step_type, start, divisor) = match record.period {
        Period::Instant => ("instant", frame.hour, 1.0),
        Period::RunTotal => ("accum", 0, 1.0),
        Period::OneHour => (
            "accum",
            frame.hour.checked_sub(1).ok_or_else(|| {
                GribError::new("RDPS winter rates require one complete forecast hour")
            })?,
            1.0,
        ),
        // ECCC publishes the liquid-equivalent amount over this interval.
        Period::ThreeHours => (
            "accum",
            frame
                .hour
                .checked_sub(3)
                .ok_or_else(|| GribError::new("GDPS winter rates require three forecast hours"))?,
            3.0,
        ),
    };
    let integers: &[(&[u8], c_long)] = &[
        (b"Ni\0", width as c_long),
        (b"Nj\0", height as c_long),
        (b"iScansNegatively\0", 0),
        (b"jScansPositively\0", 1),
        (b"jPointsAreConsecutive\0", 0),
        (b"alternativeRowScanning\0", 0),
        (b"uvRelativeToGrid\0", c_long::from(regional)),
        (b"dataDate\0", frame.date as c_long),
        (b"dataTime\0", c_long::from(frame.cycle) * 100),
        (b"stepUnits\0", 1),
        (b"startStep\0", c_long::from(start)),
        (b"endStep\0", c_long::from(frame.hour)),
        (b"discipline\0", 0),
        (b"parameterCategory\0", c_long::from(record.category)),
        (b"parameterNumber\0", c_long::from(record.number)),
        (LEVEL_KEY, c_long::from(level)),
    ];
    let floats: &[(&[u8], f64)] = &[
        (FIRST_LATITUDE_KEY, if regional { RDPS_Y0 } else { -90.0 }),
        (
            FIRST_LONGITUDE_KEY,
            if regional { RDPS_X0 + 360.0 } else { 0.0 },
        ),
        (b"iDirectionIncrementInDegrees\0", spacing),
        (b"jDirectionIncrementInDegrees\0", spacing),
    ];
    let strings: &[(&[u8], &str)] = &[
        (b"centre\0", "cwao"),
        (
            b"gridType\0",
            if regional { "rotated_ll" } else { "regular_ll" },
        ),
        (TYPE_OF_LEVEL_KEY, level_type),
        (b"stepType\0", step_type),
    ];
    for (key, expected) in integers {
        if api.get_long_value(pointer, key)? != *expected {
            return Err(wrong_record(key));
        }
    }
    for (key, expected) in floats {
        let value = api.get_double_value(pointer, key)?;
        if !value.is_finite() || (value - expected).abs() > 1e-8 {
            return Err(wrong_record(key));
        }
    }
    if regional {
        for (key, expected) in [
            (
                b"latitudeOfSouthernPoleInDegrees\0".as_slice(),
                RDPS_POLE_LAT,
            ),
            (
                b"longitudeOfSouthernPoleInDegrees\0".as_slice(),
                RDPS_POLE_LON,
            ),
            (b"angleOfRotationInDegrees\0".as_slice(), 0.0),
        ] {
            let actual = api.get_double_value(pointer, key)?;
            if !actual.is_finite() || (actual - expected).abs() > 1e-8 {
                return Err(wrong_record(key));
            }
        }
    }
    for (key, expected) in strings {
        if api.get_string_value(pointer, key)? != *expected {
            return Err(wrong_record(key));
        }
    }
    let missing = if api.get_long_value(pointer, b"bitmapPresent\0")? != 0 {
        Some(api.get_double_value(pointer, MISSING_VALUE_KEY)?)
    } else {
        None
    };
    // SAFETY: the retained ecCodes library exports this documented ABI, and
    // the output buffer has exactly the capacity passed through `length`.
    let get_array: GetDoubleArray =
        unsafe { load_symbol(&api._library, b"codes_get_double_array\0")? };
    let mut values = vec![0.0; width * height];
    let mut length = values.len();
    let code = unsafe {
        get_array(
            pointer,
            VALUES_KEY.as_ptr().cast(),
            values.as_mut_ptr(),
            &mut length,
        )
    };
    api.check(code, "decode GDPS map values")?;
    if length != values.len() {
        return Err(GribError::new(
            "GDPS grid value count does not match its dimensions",
        ));
    }
    if regional {
        Ok(values
            .into_iter()
            .map(|value| {
                if value.is_finite() && Some(value) != missing {
                    (value / divisor) as f32
                } else {
                    f32::NAN
                }
            })
            .collect())
    } else {
        Ok(map_order(&values, width, missing, divisor))
    }
}

fn wrong_record(key: &[u8]) -> GribError {
    GribError::new(format!(
        "Canadian field did not match its published grid or record address ({})",
        String::from_utf8_lossy(&key[..key.len() - 1])
    ))
}

fn map_order(values: &[f64], width: usize, missing: Option<f64>, divisor: f64) -> Vec<f32> {
    // MSC starts at the South Pole and Greenwich; map cells start north-west.
    values
        .chunks_exact(width)
        .rev()
        .flat_map(|row| row[width / 2..].iter().chain(&row[..width / 2]))
        .map(|&value| {
            if value.is_finite() && Some(value) != missing {
                (value / divisor) as f32
            } else {
                f32::NAN
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reorders_poles_and_date_line_without_interpolation_and_keeps_missing_cells() {
        let values = [0., 3., 6., 9., 12., 15., 9999., 21.];
        let output = map_order(&values, 4, Some(9999.), 3.);
        assert!(output[0].is_nan());
        assert_eq!(output[1..], [7., 4., 5., 2., 3., 0., 1.]);
        assert_eq!(map_order(&values, 4, None, 1.)[0], 9999.);
    }
}

//! Decode the native DWD ICON Global grid in its published cell order.

use super::*;
use crate::icon_fields::{File, Run};

const CELLS: usize = 2_949_120;
const GRID_UUID: &str = "a27b8de618c411e4820ab5b098c6a5c0";

type GetDoubleArray =
    unsafe extern "C" fn(*const c_void, *const c_char, *mut f64, *mut usize) -> c_int;

pub(crate) fn decode(
    library: &Path,
    message: &[u8],
    run: Run,
    file: File,
) -> Result<Vec<f32>, GribError> {
    if message.len() < 20
        || !message.starts_with(b"GRIB")
        || message[7] != 2
        || u64::from_be_bytes(message[8..16].try_into().expect("checked GRIB header"))
            != message.len() as u64
        || !message.ends_with(b"7777")
    {
        return Err(GribError::new(
            "DWD did not return a complete ICON Global GRIB2 field",
        ));
    }
    let _lock = ECCODES_CALL_LOCK
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    let api = EccodesApi::cached(library)?;
    let mut cursor = message.as_ptr().cast_mut().cast::<c_void>();
    let mut remaining = message.len();
    let handle = api
        .next_multi_handle(message, &mut cursor, &mut remaining)?
        .ok_or_else(|| GribError::new("Could not open ICON Global GRIB2 field"))?;
    let pointer = handle.as_ptr();
    let (category, parameter) = file.parameter();
    let (level_kind, level) = file.level();
    let (step_kind, start_hour) = file.step();
    let time_scale = match api.get_long_value(pointer, b"stepUnits\0")? {
        0 => 60,
        1 => 1,
        _ => return Err(wrong_record(b"stepUnits\0")),
    };
    for (key, expected) in [
        (b"numberOfDataPoints\0".as_slice(), CELLS as c_long),
        (b"numberOfGridUsed\0", 26),
        (b"numberOfGridInReference\0", 1),
        (b"dataDate\0", run.date as c_long),
        (b"dataTime\0", c_long::from(run.cycle) * 100),
        (b"startStep\0", c_long::from(start_hour) * time_scale),
        (b"endStep\0", c_long::from(file.hour) * time_scale),
        (b"discipline\0", 0),
        (b"parameterCategory\0", c_long::from(category)),
        (b"parameterNumber\0", c_long::from(parameter)),
        (LEVEL_KEY, c_long::from(level)),
    ] {
        if api.get_long_value(pointer, key)? != expected {
            return Err(wrong_record(key));
        }
    }
    for (key, expected) in [
        (b"centre\0".as_slice(), "edzw"),
        (b"gridType\0", "unstructured_grid"),
        (b"uuidOfHGrid\0", GRID_UUID),
        (b"stepType\0", step_kind),
        (TYPE_OF_LEVEL_KEY, level_kind),
    ] {
        if api.get_string_value(pointer, key)? != expected {
            return Err(wrong_record(key));
        }
    }
    let missing = if api.get_long_value(pointer, b"bitmapPresent\0")? != 0 {
        Some(api.get_double_value(pointer, MISSING_VALUE_KEY)?)
    } else {
        None
    };
    // SAFETY: ecCodes exports this stable ABI, and `length` is the destination capacity.
    let get_array: GetDoubleArray =
        unsafe { load_symbol(&api._library, b"codes_get_double_array\0")? };
    let mut values = vec![0.0; CELLS];
    let mut length = values.len();
    let code = unsafe {
        get_array(
            pointer,
            VALUES_KEY.as_ptr().cast(),
            values.as_mut_ptr(),
            &mut length,
        )
    };
    api.check(code, "decode ICON Global field values")?;
    if length != CELLS {
        return Err(GribError::new(
            "ICON Global grid value count does not match its mesh",
        ));
    }
    let scale = file.scale();
    Ok(values
        .into_iter()
        .map(|value| {
            if value.is_finite() && Some(value) != missing {
                (value * scale) as f32
            } else {
                f32::NAN
            }
        })
        .collect())
}

fn wrong_record(key: &[u8]) -> GribError {
    GribError::new(format!(
        "ICON Global field did not match its published grid or record address ({})",
        String::from_utf8_lossy(&key[..key.len() - 1]),
    ))
}

//! Read SPC GREFS probabilities by GRIB record address and forecast interval.

use super::*;
use crate::box_soundings::grid::Lambert;
use crate::map_raster::reproject;
use numpy::ndarray::{Array1, Array2};
use time::OffsetDateTime;

const NX: usize = 185;
const NY: usize = 129;

pub(crate) struct ProbabilityGrid {
    values: Array2<f32>,
    projection: Lambert,
    origin: (f64, f64),
    dx: f64,
    dy: f64,
}

impl ProbabilityGrid {
    pub(crate) fn reproject(self, bounds: (f64, f64, f64, f64)) -> Result<Array2<f32>, GribError> {
        // This smaller frame still oversamples SPC's roughly 40 km grid.
        let (width, height) = (720, 360);
        let (west, east, south, north) = bounds;
        let mut x = Vec::with_capacity(width * height);
        let mut y = Vec::with_capacity(width * height);
        for row in 0..height {
            let latitude = north - (row as f64 + 0.5) * (north - south) / height as f64;
            for column in 0..width {
                let longitude = west + (column as f64 + 0.5) * (east - west) / width as f64;
                let (gx, gy) = self.projection.project(longitude, latitude);
                x.push((gx - self.origin.0) / self.dx);
                y.push((gy - self.origin.1) / self.dy);
            }
        }
        reproject::regional(
            self.values.view(),
            Array1::from_vec(x).view(),
            Array1::from_vec(y).view(),
            (width, height),
        )
        .map_err(GribError::new)
    }
}

pub(crate) fn decode(
    library: &Path,
    payload: &[u8],
    run: OffsetDateTime,
    hour: u16,
    duration: u16,
    number: i32,
) -> Result<ProbabilityGrid, GribError> {
    let start = hour
        .checked_sub(duration)
        .ok_or_else(|| GribError::new("incomplete GREFS interval"))?;
    let _lock = ECCODES_CALL_LOCK
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    let api = EccodesApi::cached(library)?;
    let _multi = api.enable_multi_support();
    let mut cursor = payload.as_ptr().cast_mut().cast::<c_void>();
    let mut remaining = payload.len();
    let date = run.year() * 10_000 + i32::from(u8::from(run.month())) * 100 + i32::from(run.day());
    while let Some(handle) = api.next_multi_handle(payload, &mut cursor, &mut remaining)? {
        let raw = handle.as_ptr();
        if api.get_long_value(raw, b"parameterNumber\0")? != number as c_long {
            continue;
        }
        for (key, expected) in [
            (b"centre\0".as_slice(), 7),
            (b"discipline\0", 0),
            (b"parameterCategory\0", 19),
            (b"productDefinitionTemplateNumber\0", 9),
            (b"typeOfFirstFixedSurface\0", 1),
            (b"dataDate\0", date as c_long),
            (b"dataTime\0", c_long::from(run.hour()) * 100),
            (b"stepUnits\0", 1),
            (b"startStep\0", c_long::from(start)),
            (b"endStep\0", c_long::from(hour)),
            (b"Nx\0", NX as c_long),
            (b"Ny\0", NY as c_long),
            (NUMBER_OF_POINTS_KEY, (NX * NY) as c_long),
            (b"iScansNegatively\0", 0),
            (b"jScansPositively\0", 1),
            (b"jPointsAreConsecutive\0", 0),
            (b"alternativeRowScanning\0", 0),
        ] {
            if api.get_long_value(raw, key)? != expected {
                return Err(GribError::new(format!(
                    "GREFS record does not match the requested grid or period ({})",
                    String::from_utf8_lossy(&key[..key.len() - 1])
                )));
            }
        }
        if api.get_string_value(raw, b"gridType\0")? != "lambert" {
            return Err(GribError::new("GREFS grid is not Lambert conformal"));
        }
        let radius = api
            .get_optional_double(raw, b"radius\0")
            .or_else(|| api.get_optional_double(raw, b"radiusInMetres\0"))
            .ok_or_else(|| GribError::new("GREFS grid did not declare an Earth radius"))?;
        let projection = Lambert::new(
            api.get_double_value(raw, b"LoVInDegrees\0")?,
            api.get_double_value(raw, b"Latin1InDegrees\0")?,
            api.get_double_value(raw, b"Latin2InDegrees\0")?,
            radius,
        )
        .map_err(|error| GribError::new(error.to_string()))?;
        let origin = projection.project(
            api.get_double_value(raw, FIRST_LONGITUDE_KEY)?,
            api.get_double_value(raw, FIRST_LATITUDE_KEY)?,
        );
        let dx = api.get_double_value(raw, b"DxInMetres\0")?;
        let dy = api.get_double_value(raw, b"DyInMetres\0")?;
        if ![origin.0, origin.1, dx, dy].into_iter().all(f64::is_finite) || dx <= 0.0 || dy <= 0.0 {
            return Err(GribError::new("GREFS grid origin or spacing is invalid"));
        }
        let missing = (api.get_long_value(raw, b"bitmapPresent\0")? != 0)
            .then(|| api.get_double_value(raw, MISSING_VALUE_KEY))
            .transpose()?;
        let indexes: Vec<c_int> = (0..(NX * NY) as c_int).collect();
        let values = api
            .get_elements(raw, &indexes)?
            .into_iter()
            .map(|value| {
                if (0.0..=100.0).contains(&value) && Some(value) != missing {
                    value as f32
                } else {
                    f32::NAN
                }
            })
            .collect();
        let values = Array2::from_shape_vec((NY, NX), values)
            .expect("value count matches the validated GREFS grid");
        return Ok(ProbabilityGrid {
            values,
            projection,
            origin,
            dx,
            dy,
        });
    }
    Err(GribError::new(
        "requested probability record is absent from GREFS file",
    ))
}

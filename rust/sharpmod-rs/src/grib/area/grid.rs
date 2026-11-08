//! Validate geometry once at the ecCodes metadata boundary.

use super::*;
use crate::box_soundings::grid::{Grid, Lambert, Projection, Scanning};

pub(super) fn read(
    api: &EccodesApi,
    mapping: &[u8],
    inventory: &GribInventory,
) -> Result<(Grid, String, String), Error> {
    for message in &inventory.messages {
        let Some(field) = message
            .fields
            .iter()
            .find(|field| matches!(field.pressure_field, Some((_, FieldKind::Temperature))))
        else {
            continue;
        };
        let bytes =
            &mapping[message.boundary.offset..message.boundary.offset + message.boundary.length];
        let mut cursor = bytes.as_ptr().cast_mut().cast();
        let mut remaining = bytes.len();
        for index in 0..=field.field_index {
            let handle = api
                .next_multi_handle(bytes, &mut cursor, &mut remaining)?
                .ok_or_else(|| Error::Data("The native grid message is incomplete".into()))?;
            if index != field.field_index {
                continue;
            }
            let raw = handle.as_ptr();
            let kind = api.get_string_value(raw, b"gridType\0")?;
            if api.get_long_value(raw, b"earthIsOblate\0").unwrap_or(0) != 0 {
                return Err(Error::Data(
                    "Box soundings currently require the model's spherical-Earth grid geometry"
                        .into(),
                ));
            }
            let radius = api
                .get_optional_double(raw, b"radius\0")
                .or_else(|| api.get_optional_double(raw, b"radiusInMetres\0"))
                .ok_or_else(|| Error::Data("The model does not declare an Earth radius".into()))?;
            let nx = api
                .get_long_value(raw, b"Nx\0")
                .or_else(|_| api.get_long_value(raw, b"Ni\0"))?;
            let ny = api
                .get_long_value(raw, b"Ny\0")
                .or_else(|_| api.get_long_value(raw, b"Nj\0"))?;
            if nx <= 0
                || ny <= 0
                || i128::from(nx) * i128::from(ny)
                    != i128::from(api.get_long_value(raw, NUMBER_OF_POINTS_KEY)?)
            {
                return Err(Error::Data(
                    "This native grid is not a supported complete rectangular mesh".into(),
                ));
            }
            let projection = match kind.as_str() {
                "regular_ll" => Projection::RegularLatLon {
                    longitude_increment: api
                        .get_double_value(raw, b"iDirectionIncrementInDegrees\0")?,
                    latitude_increment: api
                        .get_double_value(raw, b"jDirectionIncrementInDegrees\0")?,
                },
                "lambert" => Projection::Lambert {
                    projection: Lambert::new(
                        api.get_double_value(raw, b"LoVInDegrees\0")?,
                        api.get_double_value(raw, b"Latin1InDegrees\0")?,
                        api.get_double_value(raw, b"Latin2InDegrees\0")?,
                        radius,
                    )?,
                    dx: api.get_double_value(raw, b"DxInMetres\0")?,
                    dy: api.get_double_value(raw, b"DyInMetres\0")?,
                },
                _ => {
                    return Err(Error::Data(format!(
                        "Box soundings do not yet support the model's {kind} native grid"
                    )))
                }
            };
            let grid = Grid {
                nx: nx as usize,
                ny: ny as usize,
                radius,
                projection,
                first_longitude: api.get_double_value(raw, FIRST_LONGITUDE_KEY)?,
                first_latitude: api.get_double_value(raw, FIRST_LATITUDE_KEY)?,
                scanning: Scanning {
                    i_negative: api.get_long_value(raw, b"iScansNegatively\0")? != 0,
                    j_positive: api.get_long_value(raw, b"jScansPositively\0")? != 0,
                    j_consecutive: api.get_long_value(raw, b"jPointsAreConsecutive\0")? != 0,
                    alternating: api.get_long_value(raw, b"alternativeRowScanning\0")? != 0,
                },
            }
            .validate()?;
            return Ok((grid, field.grid_key.clone(), kind));
        }
    }
    Err(Error::Data(
        "The source has no native pressure-level temperature grid".into(),
    ))
}

//! Vector reads and existing point assembly, never repeated per-column file opens.

use super::*;
use crate::box_soundings::grid::Cell;

type Columns = (Vec<DecodedPoint>, Vec<Vec<(f64, f64)>>, f64, f64);

pub(super) fn decode(
    api: &EccodesApi,
    mapping: &[u8],
    inventory: &GribInventory,
    grid_key: &str,
    cells: &[Cell],
    cancelled: &dyn Fn() -> bool,
) -> Result<Columns, Error> {
    let points: Vec<_> = cells
        .iter()
        .map(|cell| GridPoint {
            index: cell.index as c_int,
            latitude: cell.latitude,
            longitude: cell.longitude,
        })
        .collect();
    let indexes: Vec<_> = points.iter().map(|point| point.index).collect();
    let mut states: Vec<_> = cells.iter().map(|_| PointDecodeState::default()).collect();
    let mut unpack = Duration::ZERO;
    let extraction_started = Instant::now();
    for message in &inventory.messages {
        if cancelled() {
            return Err(Error::Cancelled);
        }
        let bytes =
            &mapping[message.boundary.offset..message.boundary.offset + message.boundary.length];
        let mut cursor = bytes.as_ptr().cast_mut().cast();
        let mut remaining = bytes.len();
        let mut field_index = 0;
        let mut selected = 0;
        while let Some(handle) = api.next_multi_handle(bytes, &mut cursor, &mut remaining)? {
            if let Some(field) = message
                .fields
                .get(selected)
                .filter(|field| field.field_index == field_index)
            {
                if field.grid_key != grid_key {
                    return Err(Error::Data(
                        "Box soundings require pressure and surface fields on the same native grid"
                            .into(),
                    ));
                }
                let reading = Instant::now();
                let values = api.get_elements(handle.as_ptr(), &indexes)?;
                unpack += reading.elapsed();
                apply_field_values(field, &points, &values, &mut states, Some(-9999.0))?;
                selected += 1;
            }
            field_index += 1;
        }
        if selected != message.fields.len() {
            return Err(Error::Data(
                "A GRIB field changed while the box was being decoded".into(),
            ));
        }
    }
    let vorticity: Vec<Vec<_>> = states
        .iter()
        .zip(cells)
        .map(|(state, cell)| {
            let mut values: Vec<_> = state
                .assembler
                .levels
                .iter()
                .filter_map(|level| {
                    let value = usable(level.relative_vorticity, Some(-9999.0)).or_else(|| {
                        usable(level.absolute_vorticity, Some(-9999.0))
                            .map(|value| value - coriolis_parameter(cell.latitude))
                    })?;
                    Some((level.pressure, value))
                })
                .collect();
            values.sort_by(|a, b| b.0.total_cmp(&a.0));
            values
        })
        .collect();
    let profiles = assemble_decoded_points(states, Some(-9999.0))?;
    let vorticity = vorticity
        .into_iter()
        .zip(&profiles)
        .map(|(mut values, profile)| {
            if profile.surface_merged {
                values.retain(|(pressure, _)| *pressure <= profile.matrix[0]);
            } else {
                values.clear();
            }
            values
        })
        .collect();
    Ok((
        profiles,
        vorticity,
        unpack.as_secs_f64() * 1000.0,
        extraction_started
            .elapsed()
            .saturating_sub(unpack)
            .as_secs_f64()
            * 1000.0,
    ))
}

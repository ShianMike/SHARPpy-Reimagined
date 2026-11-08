//! Shared message ingestion for mapped files and incremental download readers.
use super::*;

pub(super) struct PointDecoder {
    coordinates: Vec<(f64, f64)>,
    missing: Option<f64>,
    grids: HashMap<String, Vec<GridPoint>>,
    pub(super) states: Vec<PointDecodeState>,
    order: usize,
}

impl PointDecoder {
    pub(super) fn new(coordinates: &[(f64, f64)], missing: Option<f64>) -> Self {
        Self {
            coordinates: coordinates.to_vec(),
            missing,
            grids: HashMap::new(),
            states: coordinates
                .iter()
                .map(|_| PointDecodeState::default())
                .collect(),
            order: 0,
        }
    }

    /// The caller holds the ecCodes lock and has enabled multi-field support.
    pub(super) fn message(
        &mut self,
        api: &EccodesApi,
        message: &[u8],
        boundary: MessageBoundary,
    ) -> Result<Option<InventoryMessage>, GribError> {
        let mut cursor = message.as_ptr().cast_mut().cast::<c_void>();
        let mut remaining = message.len();
        let mut field_index = 0;
        let mut fields = Vec::new();
        loop {
            let Some(handle) = api.next_multi_handle(message, &mut cursor, &mut remaining)? else {
                break;
            };
            let order = self.order;
            self.order += 1;
            let edition = api.get_long_value(handle.as_ptr(), EDITION_KEY)?;
            if edition != c_long::from(boundary.edition) {
                return Err(GribError::new(format!(
                    "ecCodes edition {edition} disagrees with GRIB{} boundary at byte {}",
                    boundary.edition, boundary.offset
                )));
            }
            let Ok(short_name) = api.get_string_value(handle.as_ptr(), SHORT_NAME_KEY) else {
                field_index += 1;
                continue;
            };
            let Ok(type_of_level) = api.get_string_value(handle.as_ptr(), TYPE_OF_LEVEL_KEY) else {
                field_index += 1;
                continue;
            };
            let Ok(level) = api.get_double_value(handle.as_ptr(), LEVEL_KEY) else {
                field_index += 1;
                continue;
            };
            let pressure_field =
                pressure_hpa(&type_of_level, level).zip(classify_field(&short_name));
            let surface_field = classify_surface_field(&short_name, &type_of_level, level);
            if pressure_field.is_none() && surface_field.is_none() {
                field_index += 1;
                continue;
            }
            let grid_key = api
                .get_optional_string(handle.as_ptr(), GRID_HASH_KEY)
                .filter(|value| !value.is_empty())
                .unwrap_or_else(|| format!("message:{}", boundary.offset));
            let field = InventoryField {
                field_index,
                order,
                pressure_field,
                surface_field,
                grid_key,
                missing_value: api.get_optional_double(handle.as_ptr(), MISSING_VALUE_KEY),
                wind_rotation: if matches!(
                    pressure_field,
                    Some((_, FieldKind::UWind | FieldKind::VWind))
                ) || matches!(
                    surface_field,
                    Some(SurfaceFieldKind::UWind | SurfaceFieldKind::VWind)
                ) {
                    WindRotation::from_grib(api, handle.as_ptr())?
                } else {
                    WindRotation::default()
                },
            };
            if !self.coordinates.is_empty() {
                let points = grid_points_for(
                    api,
                    handle.as_ptr(),
                    &field.grid_key,
                    &self.coordinates,
                    &mut self.grids,
                )?;
                let indexes = points.iter().map(|point| point.index).collect::<Vec<_>>();
                let values = api.get_elements(handle.as_ptr(), &indexes)?;
                apply_field_values(&field, points, &values, &mut self.states, self.missing)?;
            }
            fields.push(field);
            field_index += 1;
        }
        Ok((!fields.is_empty()).then_some(InventoryMessage { boundary, fields }))
    }
}

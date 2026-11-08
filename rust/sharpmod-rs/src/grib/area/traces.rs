//! A bounded, deterministic view of actual columns; averaging always uses all cells.
use super::*;

use crate::box_soundings::MAX_DISPLAY_COLUMNS;

pub(super) fn select(columns: &[Column<'_>]) -> Arc<[DecodedPoint]> {
    let valid: Vec<_> = columns
        .iter()
        .filter(|column| column.contributes())
        .collect();
    let count = valid.len().min(MAX_DISPLAY_COLUMNS);
    (0..count)
        .map(|index| {
            // Even native scan-order quantiles retain the edges and interior.
            let selected = if count <= 1 {
                0
            } else {
                index * (valid.len() - 1) / (count - 1)
            };
            valid[selected].profile.clone()
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn display_selection_is_bounded_repeatable_and_keeps_real_columns() {
        let points: Vec<_> = (0..400)
            .map(|index| DecodedPoint {
                matrix: vec![
                    1000.0, 900.0, 0.0, 1000.0, 20.0, 10.0, 15.0, 5.0, 180.0, 180.0, 10.0, 20.0,
                    -9999.0, -9999.0, 0.0, 0.0, 10.0, 20.0,
                ],
                level_count: 2,
                selected_latitude: 35.0,
                selected_longitude: index as f64,
                surface_relative_vorticity: None,
                surface_merged: index != 200,
                below_ground_levels_removed: 0,
            })
            .collect();
        let columns: Vec<_> = points
            .iter()
            .map(|profile| Column {
                profile,
                overlap_area: 1.0,
                vorticity: &[],
            })
            .collect();
        let selected = select(&columns);
        assert_eq!(selected.len(), MAX_DISPLAY_COLUMNS);
        assert_eq!(selected, select(&columns));
        assert_eq!(selected.first().unwrap(), &points[0]);
        assert_eq!(selected.last().unwrap(), &points[399]);
        for point in selected.iter() {
            assert!(point.surface_merged);
            assert!(points.contains(point));
        }
        assert_eq!(&*select(&columns[..4]), &points[..4]);
        assert!(select(&[]).is_empty());
    }
}

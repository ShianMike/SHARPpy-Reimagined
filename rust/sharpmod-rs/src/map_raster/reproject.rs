//! Resample native model grids to map pixel centres.

use numpy::ndarray::{Array2, ArrayView1, ArrayView2};
use rayon::prelude::*;

pub(crate) fn regular_global(
    field: ArrayView2<'_, f32>,
    (width, height): (usize, usize),
    (west, east, south, north): (f64, f64, f64, f64),
) -> Result<Array2<f32>, &'static str> {
    let (rows, columns) = field.dim();
    let cells = frame_cells(width, height)?;
    if rows < 2 || columns < 2 {
        return Err("expected a regular global grid with at least two rows and columns");
    }
    let span = east - west;
    if !(span > 0.0 && span <= 360.0 && south >= -90.0 && north <= 90.0 && north > south) {
        return Err("invalid map frame bounds");
    }

    // The source has a row at each pole and starts at -180° without a duplicate
    // longitude column. The output has half-cell insets at all four edges.
    let columns = (0..width)
        .map(|x| {
            let longitude = west + (x as f64 + 0.5) * span / width as f64;
            let column = (longitude + 180.0).rem_euclid(360.0) * columns as f64 / 360.0;
            let left = column.floor() as usize % columns;
            (left, (left + 1) % columns, column.fract())
        })
        .collect::<Vec<_>>();
    let mut values = vec![0.0; cells];
    values
        .par_chunks_mut(width)
        .enumerate()
        .for_each(|(y, row)| {
            let latitude = north - (y as f64 + 0.5) * (north - south) / height as f64;
            let source_y = (90.0 - latitude) * (rows - 1) as f64 / 180.0;
            let top = source_y.floor() as usize;
            let bottom = (top + 1).min(rows - 1);
            let ty = source_y.fract();
            for (cell, &(left, right, tx)) in row.iter_mut().zip(&columns) {
                *cell = bilinear(
                    &field,
                    (left, right, tx),
                    (top, bottom, ty),
                    (usize::from(tx >= 0.5), usize::from(ty >= 0.5)),
                );
            }
        });
    Array2::from_shape_vec((height, width), values).map_err(|_| "invalid map frame dimensions")
}

pub(crate) fn regional(
    field: ArrayView2<'_, f32>,
    x: ArrayView1<'_, f64>,
    y: ArrayView1<'_, f64>,
    (width, height): (usize, usize),
) -> Result<Array2<f32>, &'static str> {
    let cells = frame_cells(width, height)?;
    let (rows, columns) = field.dim();
    if rows == 0 || columns == 0 || x.len() != cells || y.len() != cells {
        return Err("projected cell coordinates do not match the map frame");
    }
    let mut values = vec![f32::NAN; cells];
    values.par_iter_mut().enumerate().for_each(|(index, cell)| {
        let (x, y) = (x[index], y[index]);
        // Match the original projected domain, including the outer half cells.
        let (nearest_x, nearest_y) = (x.round_ties_even(), y.round_ties_even());
        if !(0.0 <= nearest_x
            && nearest_x < columns as f64
            && 0.0 <= nearest_y
            && nearest_y < rows as f64)
        {
            return;
        }
        let x = x.clamp(0.0, (columns - 1) as f64);
        let y = y.clamp(0.0, (rows - 1) as f64);
        let (left, top) = (x.floor() as usize, y.floor() as usize);
        *cell = bilinear(
            &field,
            (left, (left + 1).min(columns - 1), x.fract()),
            (top, (top + 1).min(rows - 1), y.fract()),
            (nearest_x as usize - left, nearest_y as usize - top),
        );
    });
    Array2::from_shape_vec((height, width), values).map_err(|_| "invalid map frame dimensions")
}

fn frame_cells(width: usize, height: usize) -> Result<usize, &'static str> {
    width
        .checked_mul(height)
        .filter(|&cells| cells > 0 && cells <= isize::MAX as usize / size_of::<f32>())
        .ok_or("invalid map frame dimensions")
}

fn bilinear(
    field: &ArrayView2<'_, f32>,
    (left, right, tx): (usize, usize, f64),
    (top, bottom, ty): (usize, usize, f64),
    (near_x, near_y): (usize, usize),
) -> f32 {
    let samples = [
        field[[top, left]],
        field[[top, right]],
        field[[bottom, left]],
        field[[bottom, right]],
    ];
    if !samples[2 * near_y + near_x].is_finite() {
        // Keep the missing footprint of the source cell.
        return f32::NAN;
    }
    let weights = [
        (1.0 - tx) * (1.0 - ty),
        tx * (1.0 - ty),
        (1.0 - tx) * ty,
        tx * ty,
    ];
    let mut value = 0.0;
    let mut weight = 0.0;
    for (sample, w) in samples.into_iter().zip(weights) {
        if sample.is_finite() {
            value += f64::from(sample) * w;
            weight += w;
        }
    }
    (value / weight) as f32
}

#[cfg(test)]
mod tests {
    use super::*;
    use numpy::ndarray::array;

    #[test]
    fn upsampling_reconstructs_the_gradient_without_repeating_cells() {
        let source = array![[0.0, 90.0, 180.0, 270.0], [180.0, 270.0, 360.0, 450.0]];
        let projected = regular_global(source.view(), (4, 2), (-180.0, 0.0, -90.0, 90.0)).unwrap();
        assert_eq!(
            projected,
            array![[67.5, 112.5, 157.5, 202.5], [157.5, 202.5, 247.5, 292.5]]
        );
    }

    #[test]
    fn bilinear_neighborhood_wraps_across_the_antimeridian() {
        let source = array![[20.0, 40.0, 60.0, 100.0], [20.0, 40.0, 60.0, 100.0]];
        let left = regular_global(source.view(), (2, 1), (90.0, 180.0, -1.0, 1.0)).unwrap();
        let right = regular_global(source.view(), (2, 1), (-180.0, -90.0, -1.0, 1.0)).unwrap();
        let seam = regular_global(source.view(), (1, 1), (179.5, 180.5, -1.0, 1.0)).unwrap();
        assert_eq!(left, array![[80.0, 40.0]]);
        assert_eq!(seam, array![[20.0]]);
        assert_eq!(right, array![[25.0, 35.0]]);
    }

    #[test]
    fn missing_samples_keep_their_footprint_and_never_bleed_into_valid_pixels() {
        let source = array![[10.0, f32::NAN, 30.0, 40.0], [90.0, 70.0, 60.0, 50.0]];
        let projected =
            regular_global(source.view(), (2, 2), (-180.0, -90.0, -90.0, 90.0)).unwrap();
        assert!(projected[[0, 1]].is_nan());
        assert!((projected[[0, 0]] - 430.0 / 13.0).abs() < 1e-5);
        assert!((10.0..=90.0).contains(&projected[[1, 0]]));
        assert!((10.0..=90.0).contains(&projected[[1, 1]]));
    }

    #[test]
    fn regional_sampling_keeps_domain_edges_and_projected_cell_positions() {
        let source = array![[10.0, 40.0], [90.0, 120.0]];
        let x = array![-0.51, -0.50, 0.25, 1.49, 1.50, f64::INFINITY];
        let y = array![0.25, 0.25, 0.25, 0.75, 0.75, 0.25];
        let values = regional(source.view(), x.view(), y.view(), (3, 2)).unwrap();
        assert!(values[[0, 0]].is_nan());
        assert_eq!(values[[0, 1]], 30.0);
        assert_eq!(values[[0, 2]], 37.5);
        assert_eq!(values[[1, 0]], 100.0);
        assert!(values[[1, 1]].is_nan());
        assert!(values[[1, 2]].is_nan());
    }
}

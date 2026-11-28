//! Resample native model grids to map pixel centres.

use numpy::ndarray::{Array2, ArrayView1, ArrayView2};
use rayon::prelude::*;

/// Value encoding bounds for precipitation-type bicubic reprojection.
#[derive(Clone, Copy)]
pub(crate) struct PtypeScale {
    pub stride: f64,
    pub floor: f64,
    pub clamp: f64,
}

impl PtypeScale {
    fn validate(self) -> Result<(), &'static str> {
        if !(self.stride.is_finite()
            && self.floor.is_finite()
            && self.clamp.is_finite()
            && self.stride > 0.0
            && self.floor >= 0.0
            && self.clamp > self.floor
            && self.clamp < self.stride)
        {
            return Err("invalid precipitation-type reprojection bounds");
        }
        Ok(())
    }
}

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

    let columns_map = global_columns(width, west, span, columns);
    let mut values = vec![0.0; cells];
    values
        .par_chunks_mut(width)
        .enumerate()
        .for_each(|(y, row)| {
            let (top, ty) = global_row(y, height, south, north, rows);
            let bottom = (top + 1).min(rows - 1);
            let near_y = usize::from(ty >= 0.5);
            for (cell, &(left, right, tx)) in row.iter_mut().zip(&columns_map) {
                let near_x = usize::from(tx >= 0.5);
                let inner = [
                    field[[top, left]],
                    field[[top, right]],
                    field[[bottom, left]],
                    field[[bottom, right]],
                ];
                let xs = [
                    (left + columns - 1) % columns,
                    left,
                    right,
                    (left + 2) % columns,
                ];
                *cell = bicubic_scalar(inner, (tx, ty), (near_x, near_y), |sx, oy| {
                    let iy = top as isize + oy;
                    let col = xs[sx];
                    extrap_axis(iy, rows, |ry| f64::from(field[[ry, col]]))
                });
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
        let (right, bottom) = ((left + 1).min(columns - 1), (top + 1).min(rows - 1));
        let (tx, ty) = (x.fract(), y.fract());
        let inner = [
            field[[top, left]],
            field[[top, right]],
            field[[bottom, left]],
            field[[bottom, right]],
        ];
        *cell = bicubic_scalar(
            inner,
            (tx, ty),
            (nearest_x as usize - left, nearest_y as usize - top),
            |sx, oy| {
                let ix = left as isize + (sx as isize - 1);
                let iy = top as isize + oy;
                extrap_axis(ix, columns, |rx| {
                    extrap_axis(iy, rows, |ry| f64::from(field[[ry, rx]]))
                })
            },
        );
    });
    Array2::from_shape_vec((height, width), values).map_err(|_| "invalid map frame dimensions")
}

pub(crate) fn regular_global_ptype(
    field: ArrayView2<'_, f32>,
    (width, height): (usize, usize),
    (west, east, south, north): (f64, f64, f64, f64),
    scale: PtypeScale,
) -> Result<Array2<f32>, &'static str> {
    scale.validate()?;
    let (rows, columns) = field.dim();
    let cells = frame_cells(width, height)?;
    if rows < 2 || columns < 2 {
        return Err("expected a regular global grid with at least two rows and columns");
    }
    let span = east - west;
    if !(span > 0.0 && span <= 360.0 && south >= -90.0 && north <= 90.0 && north > south) {
        return Err("invalid map frame bounds");
    }

    let columns_map = global_columns(width, west, span, columns);
    let mut values = vec![f32::NAN; cells];
    values
        .par_chunks_mut(width)
        .enumerate()
        .for_each(|(y, row)| {
            let (top, ty) = global_row(y, height, south, north, rows);
            let bottom = (top + 1).min(rows - 1);
            for (cell, &(left, right, tx)) in row.iter_mut().zip(&columns_map) {
                let inner = [
                    field[[top, left]],
                    field[[top, right]],
                    field[[bottom, left]],
                    field[[bottom, right]],
                ];
                let xs = [
                    (left + columns - 1) % columns,
                    left,
                    right,
                    (left + 2) % columns,
                ];
                *cell = bicubic_ptype(inner, (tx, ty), scale, |sx, oy| {
                    let iy = top as isize + oy;
                    let col = xs[sx];
                    extrap_axis(iy, rows, |ry| {
                        decode_ptype(field[[ry, col]], scale.stride).1
                    })
                    .max(0.0)
                });
            }
        });
    Array2::from_shape_vec((height, width), values).map_err(|_| "invalid map frame dimensions")
}

pub(crate) fn regional_ptype(
    field: ArrayView2<'_, f32>,
    x: ArrayView1<'_, f64>,
    y: ArrayView1<'_, f64>,
    (width, height): (usize, usize),
    scale: PtypeScale,
) -> Result<Array2<f32>, &'static str> {
    scale.validate()?;
    let cells = frame_cells(width, height)?;
    let (rows, columns) = field.dim();
    if rows == 0 || columns == 0 || x.len() != cells || y.len() != cells {
        return Err("projected cell coordinates do not match the map frame");
    }
    let mut values = vec![f32::NAN; cells];
    values.par_iter_mut().enumerate().for_each(|(index, cell)| {
        let (x, y) = (x[index], y[index]);
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
        let (right, bottom) = ((left + 1).min(columns - 1), (top + 1).min(rows - 1));
        let (tx, ty) = (x.fract(), y.fract());
        let inner = [
            field[[top, left]],
            field[[top, right]],
            field[[bottom, left]],
            field[[bottom, right]],
        ];
        *cell = bicubic_ptype(inner, (tx, ty), scale, |sx, oy| {
            let ix = left as isize + (sx as isize - 1);
            let iy = top as isize + oy;
            extrap_axis(ix, columns, |rx| {
                extrap_axis(iy, rows, |ry| decode_ptype(field[[ry, rx]], scale.stride).1)
            })
            .max(0.0)
        });
    });
    Array2::from_shape_vec((height, width), values).map_err(|_| "invalid map frame dimensions")
}

fn global_columns(width: usize, west: f64, span: f64, columns: usize) -> Vec<(usize, usize, f64)> {
    // The source has a row at each pole and starts at -180° without a duplicate
    // longitude column. The output has half-cell insets at all four edges.
    (0..width)
        .map(|x| {
            let longitude = west + (x as f64 + 0.5) * span / width as f64;
            let column = (longitude + 180.0).rem_euclid(360.0) * columns as f64 / 360.0;
            let left = column.floor() as usize % columns;
            (left, (left + 1) % columns, column.fract())
        })
        .collect()
}

fn global_row(y: usize, height: usize, south: f64, north: f64, rows: usize) -> (usize, f64) {
    let latitude = north - (y as f64 + 0.5) * (north - south) / height as f64;
    let source_y = (90.0 - latitude) * (rows - 1) as f64 / 180.0;
    (source_y.floor() as usize, source_y.fract())
}

fn frame_cells(width: usize, height: usize) -> Result<usize, &'static str> {
    width
        .checked_mul(height)
        .filter(|&cells| cells > 0 && cells <= isize::MAX as usize / size_of::<f32>())
        .ok_or("invalid map frame dimensions")
}

fn extrap_axis(i: isize, len: usize, read: impl Fn(usize) -> f64) -> f64 {
    if len == 1 {
        read(0)
    } else if i < 0 {
        2.0 * read(0) - read(1)
    } else if i >= len as isize {
        2.0 * read(len - 1) - read(len - 2)
    } else {
        read(i as usize)
    }
}

fn catmull_rom_weights(t: f64) -> [f64; 4] {
    let t2 = t * t;
    let t3 = t2 * t;
    [
        -0.5 * t + t2 - 0.5 * t3,
        1.0 - 2.5 * t2 + 1.5 * t3,
        0.5 * t + 2.0 * t2 - 1.5 * t3,
        -0.5 * t2 + 0.5 * t3,
    ]
}

fn bicubic_scalar(
    inner: [f32; 4],
    (tx, ty): (f64, f64),
    (near_x, near_y): (usize, usize),
    sample: impl Fn(usize, isize) -> f64,
) -> f32 {
    if !inner[2 * near_y + near_x].is_finite() {
        // Keep the missing footprint of the source cell.
        return f32::NAN;
    }
    let wx = catmull_rom_weights(tx);
    let wy = catmull_rom_weights(ty);
    let mut cubic = 0.0;
    let mut all_finite = true;
    for (uy, &wy_val) in wy.iter().enumerate() {
        let oy = uy as isize - 1;
        for (ux, &wx_val) in wx.iter().enumerate() {
            let v = sample(ux, oy);
            if !v.is_finite() {
                all_finite = false;
            } else if all_finite {
                cubic += v * wx_val * wy_val;
            }
        }
    }
    let weights = [
        (1.0 - tx) * (1.0 - ty),
        tx * (1.0 - ty),
        (1.0 - tx) * ty,
        tx * ty,
    ];
    let mut low = f64::INFINITY;
    let mut high = f64::NEG_INFINITY;
    let mut value = 0.0;
    let mut weight = 0.0;
    for (sample_v, w) in inner.into_iter().zip(weights) {
        if sample_v.is_finite() {
            let v = f64::from(sample_v);
            low = low.min(v);
            high = high.max(v);
            value += v * w;
            weight += w;
        }
    }
    if all_finite {
        cubic.clamp(low, high) as f32
    } else {
        (value / weight) as f32
    }
}

fn decode_ptype(value: f32, stride: f64) -> (Option<usize>, f64) {
    if !value.is_finite() {
        return (None, 0.0);
    }
    let v = f64::from(value);
    let cat = ((v / stride).floor() as i32).clamp(0, 3) as usize;
    (Some(cat), (v - cat as f64 * stride).max(0.0))
}

fn bicubic_ptype(
    inner: [f32; 4],
    (tx, ty): (f64, f64),
    scale: PtypeScale,
    rate_at: impl Fn(usize, isize) -> f64,
) -> f32 {
    let decoded = inner.map(|value| decode_ptype(value, scale.stride));
    let mut low = f64::INFINITY;
    let mut high = f64::NEG_INFINITY;
    for (_, rate) in decoded {
        low = low.min(rate);
        high = high.max(rate);
    }
    // The interpolated rate is clamped to these corners, so even a wet outer
    // sample cannot make this cell visible when all four rates are below the floor.
    if high < scale.floor {
        return f32::NAN;
    }

    let wx = catmull_rom_weights(tx);
    let wy = catmull_rom_weights(ty);
    let mut cubic = 0.0;
    for (uy, &wy_val) in wy.iter().enumerate() {
        let oy = uy as isize - 1;
        for (ux, &wx_val) in wx.iter().enumerate() {
            cubic += rate_at(ux, oy) * wx_val * wy_val;
        }
    }
    let rate = cubic.clamp(low, high);
    if rate < scale.floor {
        return f32::NAN;
    }
    let weights = [
        (1.0 - tx) * (1.0 - ty),
        tx * (1.0 - ty),
        (1.0 - tx) * ty,
        tx * ty,
    ];
    let mut support = [0.0_f64; 4];
    for ((cat, _), w) in decoded.into_iter().zip(weights) {
        if let Some(c) = cat {
            support[c] += w;
        }
    }
    // Hazard precedence on ties: Frzr (2) > Sleet (3) > Snow (1) > Rain (0).
    let mut best_cat = None;
    let mut best_support = 0.0;
    for cat in [2usize, 3, 1, 0] {
        if support[cat] > best_support {
            best_support = support[cat];
            best_cat = Some(cat);
        }
    }
    let Some(cat) = best_cat else {
        return f32::NAN;
    };
    (cat as f64 * scale.stride + rate.min(scale.clamp)) as f32
}

#[cfg(test)]
mod tests {
    use super::*;
    use numpy::ndarray::{array, Array1};

    #[test]
    fn invisible_ptype_cells_skip_the_outer_stencil() {
        let scale = PtypeScale {
            stride: 10.0,
            floor: 0.01,
            clamp: 9.9,
        };
        let invisible = bicubic_ptype(
            [f32::NAN, 0.004, 20.002, 0.0],
            (0.25, 0.75),
            scale,
            |_, _| panic!("invisible cell should skip cubic sampling"),
        );
        assert!(invisible.is_nan());
    }

    #[test]
    fn upsampling_reconstructs_the_gradient_without_repeating_cells() {
        let source = array![[0.0, 90.0, 180.0, 270.0], [180.0, 270.0, 360.0, 450.0]];
        let projected = regular_global(source.view(), (4, 2), (-180.0, 0.0, -90.0, 90.0)).unwrap();
        // Interior columns [2, 3] reconstruct the linear ramp identically;
        // column [0, 1] smooth across the 270 -> 0 wrap seam without overshooting.
        assert_eq!(projected[[0, 2]], 157.5);
        assert_eq!(projected[[0, 3]], 202.5);
        assert_eq!(projected[[1, 2]], 247.5);
        assert_eq!(projected[[1, 3]], 292.5);
        assert!((0.0..=270.0).contains(&projected[[0, 0]]));
        assert!((0.0..=270.0).contains(&projected[[0, 1]]));
        assert!((0.0..=270.0).contains(&projected[[1, 0]]));
        assert!((0.0..=270.0).contains(&projected[[1, 1]]));
        assert!(projected[[0, 0]] < projected[[0, 1]] && projected[[0, 1]] < projected[[0, 2]]);
        assert!(projected[[1, 0]] < projected[[1, 1]] && projected[[1, 1]] < projected[[1, 2]]);
    }

    #[test]
    fn bicubic_preserves_native_nodes_and_clamps_overshoot() {
        // Step jump across column 1->2: Catmull-Rom would ring without inner 2x2 clamping.
        let source = array![[0.0, 0.0, 100.0, 100.0], [0.0, 0.0, 100.0, 100.0],];
        // Native grid nodes at tx = 0 stay exact; points in each interval stay inside [low, high].
        let x = array![0.0, 0.25, 1.0, 1.25, 1.75, 2.0, 2.75, 3.0];
        let y = Array1::from_elem(8, 0.0);
        let out = regional(source.view(), x.view(), y.view(), (8, 1)).unwrap();
        assert_eq!(out[[0, 0]], 0.0);
        assert_eq!(out[[0, 1]], 0.0);
        assert_eq!(out[[0, 2]], 0.0);
        assert!((0.0..=100.0).contains(&out[[0, 3]]));
        assert!((0.0..=100.0).contains(&out[[0, 4]]));
        assert_eq!(out[[0, 5]], 100.0);
        assert_eq!(out[[0, 6]], 100.0);
        assert_eq!(out[[0, 7]], 100.0);
    }

    #[test]
    fn bilinear_neighborhood_wraps_across_the_antimeridian() {
        let source = array![[20.0, 40.0, 60.0, 100.0], [20.0, 40.0, 60.0, 100.0]];
        let left = regular_global(source.view(), (2, 1), (90.0, 180.0, -1.0, 1.0)).unwrap();
        let right = regular_global(source.view(), (2, 1), (-180.0, -90.0, -1.0, 1.0)).unwrap();
        let seam = regular_global(source.view(), (1, 1), (179.5, 180.5, -1.0, 1.0)).unwrap();
        assert!((left[[0, 0]] - 86.09375).abs() < 1e-4);
        assert!((left[[0, 1]] - 35.78125).abs() < 1e-4);
        assert_eq!(seam, array![[20.0]]);
        assert_eq!(right[[0, 0]], 20.0);
        assert!((right[[0, 1]] - 32.65625).abs() < 1e-4);
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

    #[test]
    fn ptype_reprojection_never_invents_categories_or_lets_heavy_rain_swallow_frzr() {
        let scale = PtypeScale {
            stride: 10.0,
            floor: 0.01,
            clamp: 9.9,
        };
        // Col 0: Sleet (30.3), Col 1: Rain (0.3) -> never produces Snow (10..) or Frzr (20..).
        let mix_and_rain = array![[30.3, 0.3], [30.3, 0.3]];
        let x = array![0.0, 0.25, 0.75, 1.0];
        let y = Array1::from_elem(4, 0.5);
        let out = regional_ptype(mix_and_rain.view(), x.view(), y.view(), (4, 1), scale).unwrap();
        for &v in out.iter() {
            assert!(v.is_finite());
            let cat = (v / 10.0).floor() as i32;
            assert!(cat == 0 || cat == 3, "unexpected category {cat} in {v}");
        }
        assert_eq!((out[[0, 1]] / 10.0).floor() as i32, 3);
        assert_eq!((out[[0, 2]] / 10.0).floor() as i32, 0);

        // Thin light freezing rain (20.04) next to heavy rain (0.40) stays Frzr until midpoint.
        let frzr_and_rain = array![[20.04, 0.40], [20.04, 0.40]];
        let x2 = array![0.0, 0.4, 0.5, 0.6, 1.0];
        let y2 = Array1::from_elem(5, 0.5);
        let band =
            regional_ptype(frzr_and_rain.view(), x2.view(), y2.view(), (5, 1), scale).unwrap();
        assert_eq!((band[[0, 0]] / 10.0).floor() as i32, 2);
        assert_eq!((band[[0, 1]] / 10.0).floor() as i32, 2);
        // Tie at x=0.5 resolves to Frzr (2) by hazard precedence.
        assert_eq!((band[[0, 2]] / 10.0).floor() as i32, 2);
        assert_eq!((band[[0, 3]] / 10.0).floor() as i32, 0);
        assert_eq!((band[[0, 4]] / 10.0).floor() as i32, 0);
    }

    #[test]
    fn ptype_feathers_wet_to_dry_edges_on_global_and_dbz_grids() {
        let rate_scale = PtypeScale {
            stride: 10.0,
            floor: 0.01,
            clamp: 9.9,
        };
        let dry_edge = array![
            [0.25, f32::NAN, f32::NAN, f32::NAN],
            [0.25, f32::NAN, f32::NAN, f32::NAN],
        ];
        // Native node at x=0 stays 0.25; x=0.25 decays smoothly rather than hard-chopping; x=0.99 drops below floor -> NaN.
        let x = array![0.0, 0.25, 0.99];
        let y = Array1::from_elem(3, 0.5);
        let feathered =
            regional_ptype(dry_edge.view(), x.view(), y.view(), (3, 1), rate_scale).unwrap();
        assert!((feathered[[0, 0]] - 0.25).abs() < 1e-5);
        assert!(
            feathered[[0, 1]].is_finite() && feathered[[0, 1]] > 0.01 && feathered[[0, 1]] < 0.25
        );
        assert!(feathered[[0, 2]].is_nan());

        // Global rate & HRRR 100-stride dBZ test.
        let global_ptype = array![
            [10.20, 10.20, 0.40, 0.40],
            [10.20, f32::NAN, 0.40, f32::NAN],
        ];
        let global_out = regular_global_ptype(
            global_ptype.view(),
            (8, 4),
            (-180.0, 180.0, -90.0, 90.0),
            rate_scale,
        )
        .unwrap();
        assert_eq!(global_out.dim(), (4, 8));
        assert!(global_out.iter().any(|v| v.is_finite()));

        let dbz_scale = PtypeScale {
            stride: 100.0,
            floor: 5.0,
            clamp: 80.0,
        };
        let dbz_grid = array![[135.0, f32::NAN], [135.0, 325.0]];
        let dx = array![0.0, 0.3, 1.0, 1.4];
        let dy = array![0.0, 0.0, 1.0, 0.5];
        let dbz_out =
            regional_ptype(dbz_grid.view(), dx.view(), dy.view(), (4, 1), dbz_scale).unwrap();
        assert!((dbz_out[[0, 0]] - 135.0).abs() < 1e-4);
        assert!(dbz_out[[0, 1]] > 105.0 && dbz_out[[0, 1]] < 135.0);
        assert!((dbz_out[[0, 2]] - 325.0).abs() < 1e-4);
        assert!(!dbz_out[[0, 3]].is_finite() || (dbz_out[[0, 3]] as i32) / 100 != 2);
    }
}

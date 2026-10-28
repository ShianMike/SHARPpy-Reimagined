//! Sample native winds, rotate grid-relative directions, and pack display cells.

use rayon::prelude::*;

pub struct LambertGrid {
    pub width: usize,
    pub x0: f64,
    pub y0: f64,
    pub spacing: f64,
    pub rho0: f64,
}

pub fn pack(
    u: &[f32],
    v: &[f32],
    indices: &[usize],
    inside: &[bool],
    grid: Option<LambertGrid>,
) -> Result<Vec<u16>, String> {
    if u.len() != v.len()
        || u.len() != indices.len()
        || indices.len() != inside.len()
        || grid.as_ref().is_some_and(|grid| grid.width == 0)
    {
        return Err("wind components or mapping do not match the model grid".into());
    }
    indices
        .par_iter()
        .zip(inside)
        .enumerate()
        .with_min_len(4096)
        .map(|(sample, (&index, &inside))| {
            let (u, v) = (u[sample], v[sample]);
            if !inside || !u.is_finite() || !v.is_finite() {
                return Ok(u16::MAX);
            }
            let steps = (u.hypot(v) * 1.943_844_4_f32 / 5.0 + 0.5).floor();
            if !steps.is_finite() || steps > 127.0 {
                return Err("wind exceeds the display encoding range".into());
            }
            // Keep the existing float32 rounding at the radians and degrees stages.
            let angle =
                (f64::from(v).atan2(f64::from(u)) as f32) * (180.0_f32 / std::f32::consts::PI);
            let wind_from = 270.0_f32 - angle;
            let direction = if let Some(grid) = &grid {
                let x = grid.x0 + (index % grid.width) as f64 * grid.spacing;
                let y = grid.y0 + (index / grid.width) as f64 * grid.spacing;
                let convergence = x.atan2(grid.rho0 - y).to_degrees();
                (f64::from(wind_from) + convergence)
                    .rem_euclid(360.0)
                    .round_ties_even()
                    % 360.0
            } else {
                f64::from(wind_from.rem_euclid(360.0).round_ties_even() % 360.0)
            };
            Ok(((steps as u16) << 9) | direction as u16)
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn packs_cardinal_calm_missing_and_masked_winds() {
        let u = [10., 0., -10., 0., 0., f32::NAN, 10.];
        let v = [0., 10., 0., -10., 0., 0., 0.];
        let cells = pack(
            &u,
            &v,
            &[0, 1, 2, 3, 4, 5, 6],
            &[true, true, true, true, true, true, false],
            None,
        )
        .unwrap();
        assert_eq!(
            cells
                .iter()
                .map(|cell| cell & 511)
                .take(5)
                .collect::<Vec<_>>(),
            [270, 180, 90, 0, 270]
        );
        assert_eq!(
            cells
                .iter()
                .map(|cell| cell >> 9)
                .take(5)
                .collect::<Vec<_>>(),
            [4, 4, 4, 4, 0]
        );
        assert_eq!(&cells[5..], &[u16::MAX, u16::MAX]);
    }

    #[test]
    fn rotates_each_sample_using_its_native_grid_position() {
        let grid = LambertGrid {
            width: 3,
            x0: -1000.,
            y0: 0.,
            spacing: 1000.,
            rho0: 1000.,
        };
        let cells = pack(&[10.; 3], &[0.; 3], &[0, 1, 2], &[true; 3], Some(grid)).unwrap();
        assert_eq!(
            cells.iter().map(|cell| cell & 511).collect::<Vec<_>>(),
            [225, 270, 315]
        );
    }

    #[test]
    fn preserves_direction_at_a_half_degree_quantization_boundary() {
        let grid = LambertGrid {
            width: 1,
            x0: 5.980_907_159_767_19_f64.to_radians().tan(),
            y0: 0.,
            spacing: 1.,
            rho0: 1.,
        };
        let cells = pack(&[-4.833_859_4], &[-32.270_584], &[0], &[true], Some(grid)).unwrap();
        assert_eq!(cells, [6670]);
    }

    #[test]
    fn rejects_bad_shapes_and_out_of_range_speeds() {
        assert!(pack(&[1.], &[], &[0], &[true], None).is_err());
        assert!(pack(&[1.], &[1.], &[], &[true], None).is_err());
        assert!(pack(&[330.], &[0.], &[0], &[true], None)
            .unwrap_err()
            .contains("encoding range"));
        assert_eq!(
            pack(&[f32::INFINITY], &[0.], &[0], &[true], None).unwrap(),
            [u16::MAX]
        );
    }

    #[test]
    fn packs_selected_samples_without_resampling_and_preserves_global_rounding() {
        let cells = pack(
            &[-1.856_247, 10.],
            &[-1.476_526_5, 0.],
            &[600_000, 1],
            &[true, true],
            None,
        )
        .unwrap();
        assert_eq!(cells, [563, 2318]);
    }
}

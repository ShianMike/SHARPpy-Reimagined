//! Grid-scale smoothing for displayed mean sea-level pressure.

use super::Projection;

// Target roughly 11 km wherever the map grid resolves it. Bound the kernel
// at high latitudes and on very fine grids to keep the work per cell bounded.
const SIGMA_DEGREES: f64 = 0.10;
const MAX_SIGMA_SAMPLES: f64 = 4.0;
const REFERENCE_HPA: f64 = 1000.0;

fn weights(degrees_per_sample: f64, samples: usize) -> Vec<f64> {
    let sigma = (SIGMA_DEGREES / degrees_per_sample).min(MAX_SIGMA_SAMPLES);
    let radius = ((3.0 * sigma) as usize).min(samples - 1) as isize;
    let mut weights: Vec<_> = (-radius..=radius)
        .map(|offset| (-0.5 * (offset as f64 / sigma).powi(2)).exp())
        .collect();
    let sum: f64 = weights.iter().sum();
    weights.iter_mut().for_each(|weight| *weight /= sum);
    weights
}

/// Filter the display grid before tracing contours. Carry weighted sums and
/// valid coverage through both axes so missing samples cannot dilute pressure.
/// The source mask is retained and full-width maps wrap at the date line.
pub(crate) fn smooth_grid(
    values: &[f64],
    height: usize,
    width: usize,
    projection: &Projection,
) -> Vec<f64> {
    let latitude_step = projection.latitude_span / projection.height;
    let longitude_step = projection.longitude_span / projection.width;
    let wraps = projection.longitude_span == 360.0;
    let vertical_weights = weights(latitude_step, height);
    let vertical_radius = vertical_weights.len() / 2;

    let mut horizontal = vec![(0.0, 0.0); values.len()];
    let mut padded = Vec::with_capacity(width + 2 * (3.0 * MAX_SIGMA_SAMPLES) as usize);
    for (row, (source, filtered)) in values
        .chunks_exact(width)
        .zip(horizontal.chunks_exact_mut(width))
        .enumerate()
    {
        let latitude = projection.north - (row as f64 + 0.5) * latitude_step;
        let kernel = weights(longitude_step * latitude.to_radians().cos(), width);
        let radius = kernel.len() / 2;
        let (samples, offset) = if wraps && radius > 0 {
            padded.clear();
            padded.extend_from_slice(&source[width - radius..]);
            padded.extend_from_slice(source);
            padded.extend_from_slice(&source[..radius]);
            (padded.as_slice(), radius)
        } else {
            (source, 0)
        };
        for (col, (sum, coverage)) in filtered.iter_mut().enumerate() {
            let center = col + offset;
            let start = center.saturating_sub(radius);
            let end = (center + radius + 1).min(samples.len());
            let first_weight = radius - (center - start);
            for (&pressure, &weight) in samples[start..end].iter().zip(&kernel[first_weight..]) {
                if pressure.is_finite() {
                    *sum += (pressure - REFERENCE_HPA) * weight;
                    *coverage += weight;
                }
            }
        }
    }

    let mut result = Vec::with_capacity(values.len());
    let mut totals = vec![(0.0, 0.0); width];
    for (row, source) in values.chunks_exact(width).enumerate() {
        totals.fill((0.0, 0.0));
        let top = row.saturating_sub(vertical_radius);
        let bottom = (row + vertical_radius + 1).min(height);
        let first_weight = vertical_radius - (row - top);
        for (samples, &weight) in horizontal[top * width..bottom * width]
            .chunks_exact(width)
            .zip(&vertical_weights[first_weight..])
        {
            for ((sum, coverage), &(across, valid)) in totals.iter_mut().zip(samples) {
                *sum += across * weight;
                *coverage += valid * weight;
            }
        }
        result.extend(source.iter().zip(&totals).map(|(&raw, &(sum, coverage))| {
            if raw.is_finite() {
                // The reprojected input is f32. Keep that precision so constant
                // pressure cannot straddle a contour through rounding noise.
                f64::from((REFERENCE_HPA + sum / coverage) as f32)
            } else {
                f64::NAN
            }
        }));
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clamps_regional_edges_and_wraps_global_longitude_without_filling_masked_samples() {
        let (height, width) = (3, 720);
        let mut source = vec![1000.0; height * width];
        source[width] = 1008.0;
        source[0] = f64::NAN;
        let global = Projection::new(height, width, (-180.0, 180.0, 75.0, 76.5)).unwrap();
        let regional = Projection::new(height, width, (-180.0, 179.0, 75.0, 76.5)).unwrap();
        let wrapped = smooth_grid(&source, height, width, &global);
        let clamped = smooth_grid(&source, height, width, &regional);
        assert!(wrapped[0].is_nan() && clamped[0].is_nan());
        assert!(wrapped[2 * width - 1] > 1000.0);
        assert_eq!(clamped[2 * width - 1], 1000.0);
        assert!(wrapped[width] < 1008.0);
        assert!(wrapped
            .iter()
            .filter(|v| v.is_finite())
            .all(|v| (1000.0..=1008.0).contains(v)));
    }

    #[test]
    fn keeps_flat_pressure_at_its_exact_level() {
        let (height, width) = (11, 13);
        let projection = Projection::new(height, width, (-88.0, -87.5, 31.0, 31.5)).unwrap();
        let mut source = vec![1018.0; height * width];
        source[3 * width + 5] = f64::NAN;
        let filtered = smooth_grid(&source, height, width, &projection);
        for (before, after) in source.into_iter().zip(filtered) {
            assert!((before.is_nan() && after.is_nan()) || before == after);
        }
    }
}

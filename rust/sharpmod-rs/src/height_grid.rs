//! First finite height bracket for terrain-relative model fields.

#[derive(Clone, Copy)]
pub(crate) struct Precision {
    pub heights_f32: bool,
    pub values_f32: bool,
    pub target_f32: bool,
}

pub(crate) fn interpolate(
    heights: &[f64],
    values: &[f64],
    target: &[f64],
    levels: usize,
    precision: Precision,
    missing: f64,
) -> Vec<f64> {
    let cells = target.len();
    target
        .iter()
        .enumerate()
        .map(|(cell, &target)| {
            for level in 0..levels.saturating_sub(1) {
                let lower = level * cells + cell;
                let upper = lower + cells;
                let (low, high) = (heights[lower], heights[upper]);
                if !(low <= target && target <= high && high > low) {
                    continue;
                }
                let depth = if precision.heights_f32 {
                    (high as f32 - low as f32) as f64
                } else {
                    high - low
                };
                // NumPy rounds the input subtraction/division before its f64 weight buffer.
                let weight = if precision.heights_f32 && precision.target_f32 {
                    ((target as f32 - low as f32) / depth as f32) as f64
                } else {
                    (target - low) / depth
                };
                let delta = if precision.values_f32 {
                    (values[upper] as f32 - values[lower] as f32) as f64
                } else {
                    values[upper] - values[lower]
                };
                let candidate = values[lower] + delta * weight;
                if candidate.is_finite() {
                    return candidate;
                }
            }
            (missing as f32) as f64
        })
        .collect()
}

#[cfg(test)]
mod tests;

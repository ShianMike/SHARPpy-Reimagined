//! Ensemble distributions and pressure-aligned thermodynamic columns.

#[derive(Debug, PartialEq)]
pub(crate) struct Distribution {
    pub count: usize,
    pub p10: Option<f64>,
    pub median: Option<f64>,
    pub p90: Option<f64>,
    pub minimum: Option<f64>,
    pub maximum: Option<f64>,
}

/// Inputs have already passed the profile metric boundary's missing-value rules.
pub(crate) fn distribution(mut values: Vec<f64>) -> Distribution {
    values.sort_by(f64::total_cmp);
    let percentile = |fraction: f64| {
        if values.is_empty() {
            return None;
        }
        let position = (values.len() - 1) as f64 * fraction;
        let lower = position.floor() as usize;
        let upper = position.ceil() as usize;
        let weight = position - lower as f64;
        let (a, b) = (values[lower], values[upper]);
        let difference = b - a;
        // Match NumPy's linear percentile arithmetic on either side of 0.5.
        Some(if weight >= 0.5 {
            b - difference * (1.0 - weight)
        } else {
            a + difference * weight
        })
    };
    Distribution {
        count: values.len(),
        p10: percentile(0.1),
        median: percentile(0.5),
        p90: percentile(0.9),
        minimum: values.first().copied(),
        maximum: values.last().copied(),
    }
}

/// Sorted samples retain the legacy NumPy tie order for duplicate pressures.
pub(crate) fn pressure_column(mut samples: Vec<(f64, f64)>, levels: &[f64]) -> Vec<Option<f64>> {
    samples.dedup_by(|next, previous| next.0 == previous.0);
    if samples.len() < 2 {
        return vec![None; levels.len()];
    }
    let lower = samples[0].0;
    let upper = samples[samples.len() - 1].0;
    let coordinates: Vec<_> = samples.iter().map(|&(pressure, _)| pressure.ln()).collect();
    levels
        .iter()
        .map(|&level| {
            if level < lower || level > upper {
                return None;
            }
            let target = level.ln();
            if target.is_nan() {
                return Some(f64::NAN);
            }
            let index = coordinates.partition_point(|&pressure| pressure <= target);
            if index == coordinates.len() {
                return Some(samples[samples.len() - 1].1);
            }
            let previous = index.saturating_sub(1);
            let (x0, y0) = (coordinates[previous], samples[previous].1);
            if x0 == target || index == 0 {
                return Some(y0);
            }
            let (x1, y1) = (coordinates[index], samples[index].1);
            // np.interp multiplies by a precomputed slope rather than a weight.
            let slope = (y1 - y0) / (x1 - x0);
            let mut value = slope * (target - x0) + y0;
            if value.is_nan() {
                value = slope * (target - x1) + y1;
                if value.is_nan() && y0 == y1 {
                    value = y0;
                }
            }
            Some(value)
        })
        .collect()
}

#[cfg(test)]
mod tests;

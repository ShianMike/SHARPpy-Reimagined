use super::slice_or_vec;
use numpy::PyReadonlyArray1;
use pyo3::prelude::*;

pub(crate) fn percentile_linear(sorted: &[f64], pct: f64) -> f64 {
    debug_assert!(!sorted.is_empty());
    if sorted.len() == 1 {
        return sorted[0];
    }
    let pos = (pct / 100.0) * ((sorted.len() - 1) as f64);
    let lo = pos.floor() as usize;
    let hi = (lo + 1).min(sorted.len() - 1);
    let frac = pos - (lo as f64);
    sorted[lo] + frac * (sorted[hi] - sorted[lo])
}

/// Count, minimum, maximum, mean, median, p10, p90, and extreme index.
pub(crate) type FieldStatistics = (usize, f64, f64, f64, f64, f64, f64, usize);

pub(crate) fn compute_field_statistics(
    values: &[f64],
    notable_high: bool,
) -> Option<FieldStatistics> {
    if values.is_empty() {
        return None;
    }
    let count = values.len();
    let mut sum = 0.0;
    let mut extreme_index = 0usize;
    let mut extreme_value = values[0];
    let mut min_val = values[0];
    let mut max_val = values[0];
    for (idx, &v) in values.iter().enumerate() {
        sum += v;
        if v < min_val {
            min_val = v;
        }
        if v > max_val {
            max_val = v;
        }
        if idx > 0 && ((notable_high && v > extreme_value) || (!notable_high && v < extreme_value))
        {
            extreme_value = v;
            extreme_index = idx;
        }
    }
    let mut sorted = values.to_vec();
    sorted.sort_by(f64::total_cmp);
    let mean = sum / (count as f64);
    let median = percentile_linear(&sorted, 50.0);
    let p10 = percentile_linear(&sorted, 10.0);
    let p90 = percentile_linear(&sorted, 90.0);
    Some((
        count,
        min_val,
        max_val,
        mean,
        median,
        p10,
        p90,
        extreme_index,
    ))
}

pub(crate) type EnvelopeSeries = (
    Vec<Option<f64>>,
    Vec<Option<f64>>,
    Vec<Option<f64>>,
    Vec<Option<f64>>,
    Vec<Option<f64>>,
    Vec<usize>,
);

pub(crate) fn compute_envelope_stats(
    columns: &[Vec<Option<f64>>],
    level_count: usize,
    low_pct: f64,
    high_pct: f64,
    circular_wdir: bool,
) -> EnvelopeSeries {
    let mut minimum = Vec::with_capacity(level_count);
    let mut low = Vec::with_capacity(level_count);
    let mut median = Vec::with_capacity(level_count);
    let mut high = Vec::with_capacity(level_count);
    let mut maximum = Vec::with_capacity(level_count);
    let mut counts = Vec::with_capacity(level_count);

    for idx in 0..level_count {
        let mut present: Vec<f64> = columns
            .iter()
            .filter_map(|col| col.get(idx).copied().flatten())
            .collect();
        counts.push(present.len());
        if present.is_empty() {
            minimum.push(None);
            low.push(None);
            median.push(None);
            high.push(None);
            maximum.push(None);
            continue;
        }
        if circular_wdir {
            let mut sin_sum = 0.0;
            let mut cos_sum = 0.0;
            let mut any_gt_180 = false;
            for &deg in &present {
                if deg > 180.0 {
                    any_gt_180 = true;
                }
                let rad = deg.rem_euclid(360.0).to_radians();
                sin_sum += rad.sin();
                cos_sum += rad.cos();
            }
            let n = present.len() as f64;
            let mut center = (sin_sum / n)
                .atan2(cos_sum / n)
                .to_degrees()
                .rem_euclid(360.0);
            if center < 180.0 && any_gt_180 {
                center += 360.0;
            }
            for deg in &mut present {
                *deg = center + ((*deg - center + 180.0).rem_euclid(360.0) - 180.0);
            }
        }
        present.sort_by(f64::total_cmp);
        minimum.push(Some(present[0]));
        low.push(Some(percentile_linear(&present, low_pct)));
        median.push(Some(percentile_linear(&present, 50.0)));
        high.push(Some(percentile_linear(&present, high_pct)));
        maximum.push(Some(present[present.len() - 1]));
    }

    (minimum, low, median, high, maximum, counts)
}

#[pyfunction]
pub(super) fn box_field_statistics(
    values: PyReadonlyArray1<'_, f64>,
    notable_high: bool,
) -> Option<FieldStatistics> {
    let slice = slice_or_vec(&values);
    compute_field_statistics(&slice, notable_high)
}

#[pyfunction]
pub(super) fn box_envelope_stats(
    columns: Vec<Vec<Option<f64>>>,
    level_count: usize,
    low_pct: f64,
    high_pct: f64,
    circular_wdir: bool,
) -> EnvelopeSeries {
    compute_envelope_stats(&columns, level_count, low_pct, high_pct, circular_wdir)
}

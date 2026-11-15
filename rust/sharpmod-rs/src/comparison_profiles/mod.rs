//! Height-aligned comparison of two sounding profiles.
//!
//! Pressure is an annotation from the reference level, not an interpolation
//! coordinate. The binding retains NumPy's established duplicate sort order.

mod bindings;

pub(crate) use bindings::register;

const FIELDS: [&str; 4] = ["tmpc", "dwpc", "u", "v"];

struct Interpolator {
    pairs: Vec<(f64, f64)>,
    max_gap: f64,
}

impl Interpolator {
    fn from_ordered_indices(pairs: &[(f64, f64)], order: &[isize], max_gap: f64) -> Self {
        let mut sorted = Vec::with_capacity(pairs.len());
        for &index in order {
            let pair = pairs[index as usize];
            if sorted.last().is_none_or(|&(x, _)| x != pair.0) {
                sorted.push(pair);
            }
        }
        Self {
            pairs: sorted,
            max_gap,
        }
    }

    fn sample(&self, target: f64) -> Option<f64> {
        if !target.is_finite() {
            return None;
        }
        let tolerance = 0.01 + 1e-5 * target.abs();
        let first_close = self.pairs.partition_point(|&(x, _)| x < target - tolerance);
        if let Some(&(x, value)) = self.pairs.get(first_close) {
            if (x - target).abs() <= tolerance {
                return Some(value);
            }
        }
        let upper = self.pairs.partition_point(|&(x, _)| x <= target);
        if upper == 0 || upper == self.pairs.len() {
            return None;
        }
        let (x0, y0) = self.pairs[upper - 1];
        let (x1, y1) = self.pairs[upper];
        if x1 - x0 > self.max_gap {
            return None;
        }
        let weight = (target - x0) / (x1 - x0);
        Some(y0 + weight * (y1 - y0))
    }
}

fn finite_pairs(heights: &[f64], values: &[f64]) -> Vec<(f64, f64)> {
    heights
        .iter()
        .zip(values)
        .filter_map(|(&height, &value)| {
            (height.is_finite() && value.is_finite()).then_some((height, value))
        })
        .collect()
}

fn bounds(heights: &[f64]) -> Option<(f64, f64)> {
    let mut valid = heights.iter().copied().filter(|value| value.is_finite());
    let first = valid.next()?;
    Some(valid.fold((first, first), |(low, high), value| {
        // NumPy's min/max reduction chooses the last value for equal zeros.
        (
            if value <= low { value } else { low },
            if value >= high { value } else { high },
        )
    }))
}

enum Coverage {
    MissingHeights,
    Disjoint,
    Common(f64, f64),
}

fn coverage(reference: &[f64], candidate: &[f64]) -> Coverage {
    let (Some((ref_bottom, ref_top)), Some((cand_bottom, cand_top))) =
        (bounds(reference), bounds(candidate))
    else {
        return Coverage::MissingHeights;
    };
    // Python's max/min retain the first operand when values compare equal.
    let bottom = if ref_bottom >= cand_bottom {
        ref_bottom
    } else {
        cand_bottom
    };
    let top = if ref_top <= cand_top {
        ref_top
    } else {
        cand_top
    };
    if top < bottom {
        Coverage::Disjoint
    } else {
        Coverage::Common(bottom, top)
    }
}

#[derive(Debug)]
struct Level {
    height: f64,
    pressure: Option<f64>,
    differences: [Option<f64>; 4],
}

fn difference_levels(
    heights: &[f64],
    pressure: &[f64],
    reference: &[Vec<f64>; 4],
    candidate: &[Option<Interpolator>; 4],
    bottom: f64,
    top: f64,
) -> Vec<Level> {
    heights
        .iter()
        .copied()
        .enumerate()
        .filter_map(|(index, height)| {
            if !height.is_finite() || height < bottom || height > top {
                return None;
            }
            let differences = std::array::from_fn(|field| {
                let reference = *reference[field].get(index)?;
                if !reference.is_finite() {
                    return None;
                }
                Some(candidate[field].as_ref()?.sample(height)? - reference)
            });
            Some(Level {
                height,
                pressure: pressure
                    .get(index)
                    .copied()
                    .filter(|value| value.is_finite()),
                differences,
            })
        })
        .collect()
}

#[cfg(test)]
mod tests;

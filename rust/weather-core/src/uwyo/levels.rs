//! Select intact UWyo rows before writing the strictly ordered analysis profile.

use super::{DecodeError, MISSING};

fn reported(value: f64) -> bool {
    value.is_finite() && value != MISSING
}

pub(super) fn ordered_indices(columns: &[Vec<f64>; 6]) -> Result<Vec<usize>, DecodeError> {
    let [pressure, height, ..] = columns;
    let mut rows: Vec<_> = (0..pressure.len())
        .filter(|&i| pressure[i].is_finite() && pressure[i] > 0.0)
        .collect();
    let completeness = |i: usize| {
        (
            reported(height[i]),
            columns[2..].iter().filter(|col| reported(col[i])).count(),
        )
    };
    // At the archive's reported precision, different altitudes can have the
    // same pressure. Retain one measured row, preferring a reported height and
    // the most complete measurements. A stable sort keeps source order on ties.
    rows.sort_by(|&a, &b| {
        pressure[b]
            .total_cmp(&pressure[a])
            .then_with(|| completeness(b).cmp(&completeness(a)))
    });
    rows.dedup_by(|&mut a, &mut b| pressure[a] == pressure[b]);

    // For reported heights, retain a longest strictly increasing sequence.
    // One high outlier must not cause all subsequent good levels to be dropped.
    // Pressure-only rows remain usable and do not participate in this check.
    let mut tails: Vec<usize> = Vec::new();
    let mut previous = vec![None; height.len()];
    for &i in &rows {
        if !reported(height[i]) {
            continue;
        }
        let position = tails.partition_point(|&j| height[j] < height[i]);
        if position > 0 {
            previous[i] = Some(tails[position - 1]);
        }
        if position == tails.len() {
            tails.push(i);
        } else if height[i] < height[tails[position]] {
            tails[position] = i;
        }
    }
    if tails.len() < 2 {
        return Err(DecodeError::Parse(
            "UWyo sounding has fewer than two levels with decreasing pressure and increasing height"
                .into(),
        ));
    }

    let mut keep = vec![false; height.len()];
    let mut next = tails.last().copied();
    while let Some(i) = next {
        keep[i] = true;
        next = previous[i];
    }
    rows.retain(|&i| keep[i] || !reported(height[i]));
    Ok(rows)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_observations_without_two_distinct_measured_heights() {
        let mut columns = [
            vec![0.; 3],
            vec![0.; 3],
            vec![0.; 3],
            vec![0.; 3],
            vec![0.; 3],
            vec![0.; 3],
        ];
        columns[0] = vec![1010., 965., 925.];
        columns[1] = vec![30., 30., MISSING];
        assert!(matches!(
            ordered_indices(&columns),
            Err(DecodeError::Parse(_))
        ));
    }
}

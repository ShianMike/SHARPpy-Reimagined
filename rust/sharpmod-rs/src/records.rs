//! Sorting and deduplication primitives for pressure-level records.

fn is_missing(value: f64, missing: Option<f64>) -> bool {
    !value.is_finite() || missing.is_some_and(|sentinel| value == sentinel)
}

/// Return source indexes ordered by descending pressure with duplicates removed.
///
/// Non-finite, configured-missing, zero, and negative pressures are omitted.
/// Sorting is stable and exact duplicate pressures retain the earliest source
/// record.  The input slice is never modified.
pub fn pressure_sort_dedup_indices(pressure: &[f64], missing: Option<f64>) -> Vec<usize> {
    let mut levels: Vec<(usize, f64)> = pressure
        .iter()
        .copied()
        .enumerate()
        .filter(|(_, value)| !is_missing(*value, missing) && *value > 0.0)
        .collect();

    levels.sort_by(
        |(left_index, left_pressure), (right_index, right_pressure)| {
            right_pressure
                .total_cmp(left_pressure)
                .then_with(|| left_index.cmp(right_index))
        },
    );

    let mut indexes = Vec::with_capacity(levels.len());
    let mut last_pressure = None;
    for (index, value) in levels {
        if last_pressure == Some(value) {
            continue;
        }
        indexes.push(index);
        last_pressure = Some(value);
    }
    indexes
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sorts_ascending_input_into_descending_pressure_order() {
        let pressure = [700.0, 850.0, 1000.0, 925.0];
        assert_eq!(
            pressure_sort_dedup_indices(&pressure, Some(-9999.0)),
            vec![2, 3, 1, 0]
        );
    }

    #[test]
    fn already_descending_input_keeps_its_order() {
        let pressure = [1000.0, 925.0, 850.0, 700.0];
        assert_eq!(
            pressure_sort_dedup_indices(&pressure, Some(-9999.0)),
            vec![0, 1, 2, 3]
        );
    }

    #[test]
    fn exact_duplicates_keep_the_earliest_source_record() {
        let pressure = [900.0, 1000.0, 900.0, 850.0, 1000.0];
        assert_eq!(
            pressure_sort_dedup_indices(&pressure, Some(-9999.0)),
            vec![1, 0, 3]
        );
    }

    #[test]
    fn invalid_and_configured_missing_pressures_are_dropped() {
        let pressure = [1000.0, f64::NAN, 925.0, f64::INFINITY, 0.0, -1.0, 850.0];
        assert_eq!(
            pressure_sort_dedup_indices(&pressure, Some(925.0)),
            vec![0, 6]
        );
    }

    #[test]
    fn a_positive_sentinel_is_only_dropped_when_configured() {
        let pressure = [1000.0, 925.0, 850.0];
        assert_eq!(pressure_sort_dedup_indices(&pressure, None), vec![0, 1, 2]);
        assert_eq!(
            pressure_sort_dedup_indices(&pressure, Some(925.0)),
            vec![0, 2]
        );
    }

    #[test]
    fn nearby_but_unequal_pressures_are_not_collapsed() {
        let immediately_below_1000 = f64::from_bits(1000.0_f64.to_bits() - 1);
        let pressure = [1000.0, immediately_below_1000, 999.999];
        assert_eq!(
            pressure_sort_dedup_indices(&pressure, Some(-9999.0)),
            vec![0, 1, 2]
        );
    }

    #[test]
    fn empty_and_single_element_inputs_are_supported() {
        assert!(pressure_sort_dedup_indices(&[], Some(-9999.0)).is_empty());
        assert_eq!(
            pressure_sort_dedup_indices(&[1000.0], Some(-9999.0)),
            vec![0]
        );
        assert!(pressure_sort_dedup_indices(&[f64::NAN], Some(-9999.0)).is_empty());
    }
}

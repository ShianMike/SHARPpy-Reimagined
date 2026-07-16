//! One-dimensional profile interpolation matching NumPy's duplicate behavior.

use std::cmp::Ordering;

fn is_missing(value: f64, missing: Option<f64>) -> bool {
    !value.is_finite() || missing.is_some_and(|sentinel| value == sentinel)
}

fn upper_bound(pairs: &[(f64, f64)], target: f64) -> usize {
    let mut low = 0;
    let mut high = pairs.len();

    while low < high {
        let middle = low + (high - low) / 2;
        if pairs[middle].0 <= target {
            low = middle + 1;
        } else {
            high = middle;
        }
    }

    low
}

fn finish_value(raw: f64, log_output: bool) -> f64 {
    if log_output {
        10.0_f64.powf(raw)
    } else {
        raw
    }
}

fn value_from_upper(pairs: &[(f64, f64)], target: f64, upper: usize) -> f64 {
    if upper == pairs.len() {
        return pairs[pairs.len() - 1].1;
    }
    let lower = upper - 1;
    if pairs[lower].0 == target {
        return pairs[lower].1;
    }
    let (x0, y0) = pairs[lower];
    let (x1, y1) = pairs[upper];
    y0 + ((target - x0) / (x1 - x0)) * (y1 - y0)
}

/// Linearly interpolate profile values at one or more target coordinates.
///
/// Coordinate/value pairs containing a non-finite value or an exact `missing`
/// sentinel are discarded. Remaining pairs are stable-sorted in ascending
/// coordinate order. Duplicate coordinates are retained: an exact duplicate
/// target resolves to the last duplicate, interpolation below the duplicate
/// uses its first value, and interpolation above it uses its last value. This
/// matches `numpy.interp` after a stable sort.
///
/// Targets outside the available range, non-finite targets, and exact missing
/// sentinels produce NaN. If fewer than two usable pairs remain, every target
/// produces NaN. When `log_output` is true, valid results are transformed with
/// `10_f64.powf(result)`.
pub fn interpolate_1d(
    targets: &[f64],
    coordinates: &[f64],
    values: &[f64],
    missing: Option<f64>,
    log_output: bool,
) -> Result<Vec<f64>, String> {
    if coordinates.len() != values.len() {
        return Err(format!(
            "coordinate and value lengths differ: {} != {}",
            coordinates.len(),
            values.len()
        ));
    }

    let mut pairs: Vec<(f64, f64)> = coordinates
        .iter()
        .copied()
        .zip(values.iter().copied())
        .filter(|(coordinate, value)| {
            !is_missing(*coordinate, missing) && !is_missing(*value, missing)
        })
        .collect();

    // Avoid an O(n log n) sort for the two common profile layouts. Reversing a
    // strictly decreasing sequence is equivalent to a stable ascending sort;
    // equality is intentionally excluded because reversing duplicates would
    // change NumPy's stable tie order.
    let ascending = pairs.windows(2).all(|pair| pair[0].0 <= pair[1].0);
    if !ascending {
        let strictly_descending = pairs.windows(2).all(|pair| pair[0].0 > pair[1].0);
        if strictly_descending {
            pairs.reverse();
        } else {
            // `sort_by` is stable. Treat positive and negative zero as equal so
            // their original order matches NumPy's stable argsort.
            pairs.sort_by(|left, right| left.0.partial_cmp(&right.0).unwrap_or(Ordering::Equal));
        }
    }

    if pairs.len() < 2 {
        return Ok(vec![f64::NAN; targets.len()]);
    }

    let first_coordinate = pairs[0].0;
    let last_coordinate = pairs[pairs.len() - 1].0;
    let mut output = vec![f64::NAN; targets.len()];

    // Ordered targets can be merged with ordered coordinates in O(n + m), as
    // atmospheric pressure/height grids usually are. Missing targets do not
    // affect the monotonicity decision.
    let mut previous = None;
    let mut nondecreasing = true;
    let mut nonincreasing = true;
    for &target in targets {
        if is_missing(target, missing) {
            continue;
        }
        if let Some(previous_target) = previous {
            if target < previous_target {
                nondecreasing = false;
            }
            if target > previous_target {
                nonincreasing = false;
            }
        }
        previous = Some(target);
    }

    if nondecreasing || nonincreasing {
        let mut upper = 0;
        let mut visit = |index: usize| {
            let target = targets[index];
            if is_missing(target, missing) || target < first_coordinate || target > last_coordinate
            {
                return;
            }
            while upper < pairs.len() && pairs[upper].0 <= target {
                upper += 1;
            }
            output[index] = finish_value(value_from_upper(&pairs, target, upper), log_output);
        };
        if nondecreasing {
            for index in 0..targets.len() {
                visit(index);
            }
        } else {
            for index in (0..targets.len()).rev() {
                visit(index);
            }
        }
    } else {
        for (index, &target) in targets.iter().enumerate() {
            if is_missing(target, missing) || target < first_coordinate || target > last_coordinate
            {
                continue;
            }
            let upper = upper_bound(&pairs, target);
            output[index] = finish_value(value_from_upper(&pairs, target, upper), log_output);
        }
    }

    Ok(output)
}

#[cfg(test)]
mod tests {
    use super::interpolate_1d;

    const EPSILON: f64 = 1.0e-12;

    fn assert_close(actual: f64, expected: f64) {
        assert!(
            (actual - expected).abs() <= EPSILON,
            "expected {expected}, got {actual}"
        );
    }

    #[test]
    fn interpolation_preserves_target_order_and_marks_boundaries() {
        let output = interpolate_1d(
            &[150.0, -1.0, 0.0, 50.0, 200.0, 201.0],
            &[0.0, 100.0, 200.0],
            &[0.0, 10.0, 20.0],
            None,
            false,
        )
        .unwrap();

        assert_close(output[0], 15.0);
        assert!(output[1].is_nan());
        assert_close(output[2], 0.0);
        assert_close(output[3], 5.0);
        assert_close(output[4], 20.0);
        assert!(output[5].is_nan());
    }

    #[test]
    fn coordinates_are_stable_sorted_in_ascending_order() {
        let targets = [25.0, 75.0, 125.0, 175.0];
        let output = interpolate_1d(
            &targets,
            &[200.0, 0.0, 150.0, 50.0, 100.0],
            &[20.0, 0.0, 15.0, 5.0, 10.0],
            None,
            false,
        )
        .unwrap();

        for (actual, expected) in output.iter().zip([2.5, 7.5, 12.5, 17.5]) {
            assert_close(*actual, expected);
        }
    }

    #[test]
    fn duplicate_coordinates_match_numpy_interp_semantics() {
        let output = interpolate_1d(
            &[49.0, 50.0, 51.0],
            &[0.0, 50.0, 50.0, 100.0],
            &[0.0, 10.0, 20.0, 30.0],
            None,
            false,
        )
        .unwrap();

        assert_close(output[0], 9.8);
        assert_close(output[1], 20.0);
        assert_close(output[2], 20.2);

        // Stable ordering matters when duplicate coordinates arrive in a
        // descending profile: the last duplicate after the stable sort differs.
        let reverse = interpolate_1d(
            &[49.0, 50.0, 51.0],
            &[100.0, 50.0, 50.0, 0.0],
            &[30.0, 20.0, 10.0, 0.0],
            None,
            false,
        )
        .unwrap();
        assert_close(reverse[0], 19.6);
        assert_close(reverse[1], 10.0);
        assert_close(reverse[2], 10.4);
    }

    #[test]
    fn nonfinite_and_sentinel_pairs_are_filtered() {
        let missing = -9999.0;
        let output = interpolate_1d(
            &[0.0, 50.0, 100.0, missing, f64::NAN],
            &[0.0, 25.0, 50.0, 75.0, 100.0, 125.0],
            &[0.0, f64::NAN, missing, f64::INFINITY, 10.0, 12.5],
            Some(missing),
            false,
        )
        .unwrap();

        assert_close(output[0], 0.0);
        assert_close(output[1], 5.0);
        assert_close(output[2], 10.0);
        assert!(output[3].is_nan());
        assert!(output[4].is_nan());
    }

    #[test]
    fn fewer_than_two_valid_pairs_make_every_target_nan() {
        let targets = [0.0, 1.0, 2.0];
        let one = interpolate_1d(&targets, &[0.0], &[7.0], None, false).unwrap();
        assert!(one.iter().all(|value| value.is_nan()));

        let none = interpolate_1d(
            &targets,
            &[f64::NAN, -9999.0],
            &[1.0, 2.0],
            Some(-9999.0),
            false,
        )
        .unwrap();
        assert!(none.iter().all(|value| value.is_nan()));
    }

    #[test]
    fn logarithmic_output_is_ten_to_the_interpolated_value() {
        let output =
            interpolate_1d(&[0.0, 50.0, 100.0], &[0.0, 100.0], &[2.0, 4.0], None, true).unwrap();

        assert_close(output[0], 100.0);
        assert_close(output[1], 1000.0);
        assert_close(output[2], 10000.0);
    }

    #[test]
    fn mismatched_lengths_are_rejected_and_empty_targets_are_valid() {
        assert!(interpolate_1d(&[0.0], &[0.0, 1.0], &[0.0], None, false).is_err());

        let output = interpolate_1d(&[], &[0.0, 1.0], &[0.0, 1.0], None, false).unwrap();
        assert!(output.is_empty());
    }
}

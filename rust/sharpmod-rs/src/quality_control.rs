//! Basic, non-mutating sounding-profile quality-control checks.
//!
//! These checks deliberately stay small and deterministic.  They validate the
//! six core reported-level columns without attempting to repair, sort, or
//! otherwise reinterpret a sounding.

/// Result returned by [`basic_sounding_qc`].
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct QualityControlResult {
    /// `true` when no quality-control issue was found.
    pub valid: bool,
    /// Rows with both a non-missing pressure and a non-missing height.
    pub valid_level_count: usize,
    /// Stable, machine-readable issue codes in check order.
    pub issues: Vec<String>,
}

fn is_missing(value: f64, missing: Option<f64>) -> bool {
    !value.is_finite() || missing.is_some_and(|sentinel| value == sentinel)
}

/// Run basic quality-control checks on the six core sounding columns.
///
/// Every slice must describe the same number of levels.  Non-finite values and
/// values exactly equal to `missing` are treated as missing.  Pressure is a
/// structural coordinate, so it must be complete, positive, and strictly
/// decreasing.  Missing heights are compressed before checking that at least
/// two remain and are strictly increasing.  Missing thermodynamic and wind
/// values are ignored by their range checks.
pub fn basic_sounding_qc(
    pres: &[f64],
    hght: &[f64],
    tmpc: &[f64],
    dwpc: &[f64],
    wdir: &[f64],
    wspd: &[f64],
    missing: Option<f64>,
) -> Result<QualityControlResult, String> {
    profile_qc([pres, hght, tmpc, dwpc, wdir, wspd], missing, true)
}

/// Shared scan; portable archives intentionally retain their existing range
/// checks rather than inheriting the stricter absolute-zero checks.
pub(crate) fn profile_qc(
    columns: [&[f64]; 6],
    missing: Option<f64>,
    check_absolute_zero: bool,
) -> Result<QualityControlResult, String> {
    let [pres, hght, tmpc, dwpc, wdir, wspd] = columns;
    let expected = pres.len();
    let lengths = [
        expected,
        hght.len(),
        tmpc.len(),
        dwpc.len(),
        wdir.len(),
        wspd.len(),
    ];
    if lengths.iter().any(|&length| length != expected) {
        return Err(format!(
            "sounding arrays must have equal lengths: pres={}, hght={}, tmpc={}, dwpc={}, wdir={}, wspd={}",
            lengths[0], lengths[1], lengths[2], lengths[3], lengths[4], lengths[5]
        ));
    }

    let mut valid_level_count = 0;
    let mut pressure_count = 0;
    let mut height_count = 0;
    let mut previous_pressure = None;
    let mut previous_height = None;
    let mut nonpositive_pressure = false;
    let mut unordered_pressure = false;
    let mut unordered_height = false;
    let mut absolute_temperature = false;
    let mut absolute_dewpoint = false;
    let mut warm_dewpoint = false;
    let mut invalid_direction = false;
    let mut negative_speed = false;

    for i in 0..expected {
        let pressure = pres[i];
        let height = hght[i];
        let pressure_valid = !is_missing(pressure, missing);
        let height_valid = !is_missing(height, missing);
        valid_level_count += usize::from(pressure_valid && height_valid);
        if pressure_valid {
            pressure_count += 1;
            nonpositive_pressure |= pressure <= 0.0;
            unordered_pressure |= previous_pressure.is_some_and(|previous| previous <= pressure);
            previous_pressure = Some(pressure);
        }
        if height_valid {
            height_count += 1;
            unordered_height |= previous_height.is_some_and(|previous| previous >= height);
            previous_height = Some(height);
        }
        let temperature_valid = !is_missing(tmpc[i], missing);
        let dewpoint_valid = !is_missing(dwpc[i], missing);
        absolute_temperature |= temperature_valid && tmpc[i] <= -273.15;
        absolute_dewpoint |= dewpoint_valid && dwpc[i] <= -273.15;
        warm_dewpoint |= temperature_valid && dewpoint_valid && dwpc[i] > tmpc[i];
        invalid_direction |= !is_missing(wdir[i], missing) && !(0.0..=360.0).contains(&wdir[i]);
        negative_speed |= !is_missing(wspd[i], missing) && wspd[i] < 0.0;
    }

    let issues: Vec<String> = [
        (pressure_count < 2, "too_few_levels"),
        (pressure_count != expected, "missing_pressure"),
        (nonpositive_pressure, "nonpositive_pressure"),
        (unordered_pressure, "pressure_not_strictly_decreasing"),
        (height_count < 2, "insufficient_height"),
        (unordered_height, "height_not_strictly_increasing"),
        (
            check_absolute_zero && absolute_temperature,
            "temperature_below_absolute_zero",
        ),
        (
            check_absolute_zero && absolute_dewpoint,
            "dewpoint_below_absolute_zero",
        ),
        (warm_dewpoint, "dewpoint_above_temperature"),
        (invalid_direction, "wind_direction_out_of_range"),
        (negative_speed, "negative_wind_speed"),
    ]
    .into_iter()
    .filter(|(failed, _)| *failed)
    .map(|(_, code)| code.to_owned())
    .collect();

    Ok(QualityControlResult {
        valid: issues.is_empty(),
        valid_level_count,
        issues,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const MISSING: f64 = -9999.0;

    type SoundingColumns = (Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>);

    fn valid_columns() -> SoundingColumns {
        (
            vec![1000.0, 900.0, 800.0],
            vec![100.0, 1_000.0, 2_000.0],
            vec![20.0, 10.0, 0.0],
            vec![15.0, 5.0, -5.0],
            vec![0.0, 180.0, 360.0],
            vec![0.0, 20.0, 40.0],
        )
    }

    #[test]
    fn accepts_a_valid_profile_and_inclusive_360_degree_wind() {
        let (pres, hght, tmpc, dwpc, wdir, wspd) = valid_columns();
        let result =
            basic_sounding_qc(&pres, &hght, &tmpc, &dwpc, &wdir, &wspd, Some(MISSING)).unwrap();

        assert!(result.valid);
        assert_eq!(result.valid_level_count, 3);
        assert!(result.issues.is_empty());
    }

    #[test]
    fn rejects_mismatched_lengths() {
        let (pres, hght, tmpc, dwpc, wdir, mut wspd) = valid_columns();
        wspd.pop();

        let error =
            basic_sounding_qc(&pres, &hght, &tmpc, &dwpc, &wdir, &wspd, Some(MISSING)).unwrap_err();

        assert!(error.contains("equal lengths"));
        assert!(error.contains("wspd=2"));
    }

    #[test]
    fn pressure_is_complete_positive_and_strictly_decreasing() {
        let pres = vec![1000.0, f64::NAN, 1000.0, 0.0];
        let hght = vec![100.0, 500.0, 1_000.0, 1_500.0];
        let tmpc = vec![20.0, 15.0, 10.0, 5.0];
        let dwpc = vec![15.0, 10.0, 5.0, 0.0];
        let wdir = vec![0.0, 90.0, 180.0, 270.0];
        let wspd = vec![0.0, 10.0, 20.0, 30.0];

        let result =
            basic_sounding_qc(&pres, &hght, &tmpc, &dwpc, &wdir, &wspd, Some(MISSING)).unwrap();

        assert_eq!(result.valid_level_count, 3);
        assert_eq!(
            result.issues,
            vec![
                "missing_pressure",
                "nonpositive_pressure",
                "pressure_not_strictly_decreasing",
            ]
        );
    }

    #[test]
    fn height_check_compresses_missing_values() {
        let (pres, mut hght, tmpc, dwpc, wdir, wspd) = valid_columns();
        hght[1] = MISSING;

        let result =
            basic_sounding_qc(&pres, &hght, &tmpc, &dwpc, &wdir, &wspd, Some(MISSING)).unwrap();

        assert!(result.valid);
        assert_eq!(result.valid_level_count, 2);
    }

    #[test]
    fn reports_range_issues_in_deterministic_order() {
        let (pres, hght, mut tmpc, mut dwpc, mut wdir, mut wspd) = valid_columns();
        tmpc[0] = -273.15;
        dwpc[1] = -300.0;
        wdir[1] = 360.01;
        wspd[2] = -0.01;

        let result =
            basic_sounding_qc(&pres, &hght, &tmpc, &dwpc, &wdir, &wspd, Some(MISSING)).unwrap();

        assert_eq!(
            result.issues,
            vec![
                "temperature_below_absolute_zero",
                "dewpoint_below_absolute_zero",
                "dewpoint_above_temperature",
                "wind_direction_out_of_range",
                "negative_wind_speed",
            ]
        );
    }

    #[test]
    fn missing_optional_values_do_not_fail_range_checks() {
        let (pres, hght, mut tmpc, mut dwpc, mut wdir, mut wspd) = valid_columns();
        tmpc[0] = f64::INFINITY;
        dwpc[1] = MISSING;
        wdir[2] = f64::NAN;
        wspd[0] = MISSING;

        let result =
            basic_sounding_qc(&pres, &hght, &tmpc, &dwpc, &wdir, &wspd, Some(MISSING)).unwrap();

        assert!(result.valid);
        assert_eq!(result.valid_level_count, 3);
    }

    #[test]
    fn empty_and_single_level_profiles_are_invalid_without_panicking() {
        let empty = basic_sounding_qc(&[], &[], &[], &[], &[], &[], Some(MISSING)).unwrap();
        assert_eq!(empty.issues, vec!["too_few_levels", "insufficient_height"]);

        let one = basic_sounding_qc(
            &[1000.0],
            &[100.0],
            &[20.0],
            &[15.0],
            &[180.0],
            &[10.0],
            Some(MISSING),
        )
        .unwrap();
        assert_eq!(one.valid_level_count, 1);
        assert_eq!(one.issues, vec!["too_few_levels", "insufficient_height"]);
    }

    #[test]
    fn dewpoint_warmer_than_temperature_is_rejected() {
        let (pres, hght, tmpc, mut dwpc, wdir, wspd) = valid_columns();
        dwpc[0] = tmpc[0] + 10.0;

        let result =
            basic_sounding_qc(&pres, &hght, &tmpc, &dwpc, &wdir, &wspd, Some(MISSING)).unwrap();

        assert!(!result.valid);
        assert_eq!(result.issues, vec!["dewpoint_above_temperature"]);
    }
}

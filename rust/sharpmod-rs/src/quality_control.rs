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

    let valid_level_count = pres
        .iter()
        .zip(hght)
        .filter(|(pressure, height)| {
            !is_missing(**pressure, missing) && !is_missing(**height, missing)
        })
        .count();

    let valid_pressure: Vec<f64> = pres
        .iter()
        .copied()
        .filter(|&value| !is_missing(value, missing))
        .collect();
    let mut issues = Vec::new();

    if valid_pressure.len() < 2 {
        issues.push("too_few_levels".to_owned());
    }
    if valid_pressure.len() != expected {
        issues.push("missing_pressure".to_owned());
    }
    if valid_pressure.iter().any(|&value| value <= 0.0) {
        issues.push("nonpositive_pressure".to_owned());
    }
    if valid_pressure.windows(2).any(|pair| pair[0] <= pair[1]) {
        issues.push("pressure_not_strictly_decreasing".to_owned());
    }

    let valid_height: Vec<f64> = hght
        .iter()
        .copied()
        .filter(|&value| !is_missing(value, missing))
        .collect();
    if valid_height.len() < 2 {
        issues.push("insufficient_height".to_owned());
    } else if valid_height.windows(2).any(|pair| pair[0] >= pair[1]) {
        issues.push("height_not_strictly_increasing".to_owned());
    }

    if tmpc
        .iter()
        .copied()
        .filter(|&value| !is_missing(value, missing))
        .any(|value| value <= -273.15)
    {
        issues.push("temperature_below_absolute_zero".to_owned());
    }
    if dwpc
        .iter()
        .copied()
        .filter(|&value| !is_missing(value, missing))
        .any(|value| value <= -273.15)
    {
        issues.push("dewpoint_below_absolute_zero".to_owned());
    }
    if wdir
        .iter()
        .copied()
        .filter(|&value| !is_missing(value, missing))
        .any(|value| !(0.0..=360.0).contains(&value))
    {
        issues.push("wind_direction_out_of_range".to_owned());
    }
    if wspd
        .iter()
        .copied()
        .filter(|&value| !is_missing(value, missing))
        .any(|value| value < 0.0)
    {
        issues.push("negative_wind_speed".to_owned());
    }

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
    fn dewpoint_warmer_than_temperature_is_not_an_extra_qc_rule() {
        let (pres, hght, tmpc, mut dwpc, wdir, wspd) = valid_columns();
        dwpc[0] = tmpc[0] + 10.0;

        let result =
            basic_sounding_qc(&pres, &hght, &tmpc, &dwpc, &wdir, &wspd, Some(MISSING)).unwrap();

        assert!(result.valid);
    }
}

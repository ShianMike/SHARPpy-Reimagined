use super::*;
use std::cell::Cell;

fn point(pressures: &[f64], heights: &[f64], temperatures: &[f64], q: f64, u: f64) -> DecodedPoint {
    let n = pressures.len();
    let mut matrix = vec![MISSING; 9 * n];
    for index in 0..n {
        matrix[index] = pressures[index];
        matrix[n + index] = heights[index];
        matrix[2 * n + index] = temperatures[index];
        matrix[3 * n + index] = dewpoint_from_specific_humidity(q, pressures[index]);
        matrix[4 * n + index] = if u < 0.0 { 90.0 } else { 270.0 };
        matrix[5 * n + index] = u.abs() * MPS_TO_KNOTS;
        matrix[7 * n + index] = u;
        matrix[8 * n + index] = 0.0;
    }
    DecodedPoint {
        matrix,
        level_count: n,
        selected_latitude: 35.0,
        selected_longitude: -97.0,
        surface_relative_vorticity: None,
        surface_merged: true,
        below_ground_levels_removed: 0,
    }
}

fn column(profile: &DecodedPoint, overlap_area: f64) -> Column<'_> {
    Column {
        profile,
        overlap_area,
        vorticity: &[],
    }
}

fn run(columns: &[Column<'_>], area: f64) -> Result<Averaged, Error> {
    aggregate(
        columns,
        Bounds::new(-97.1, 34.9, -96.9, 35.1).unwrap(),
        area,
        Limits::default(),
        &|| false,
    )
}

#[test]
fn identical_columns_and_a_single_column_preserve_the_original_profile() {
    let profile = point(
        &[1000.0, 900.0, 800.0],
        &[100.0, 1000.0, 2000.0],
        &[30.0, 20.0, 10.0],
        0.004,
        10.0,
    );
    for weights in [vec![1.0], vec![1.0, 3.0, 7.0, 9.0]] {
        let columns: Vec<_> = weights
            .iter()
            .map(|&weight| column(&profile, weight))
            .collect();
        let result = run(&columns, weights.iter().sum()).unwrap();
        assert_eq!(result.selected_columns, weights.len());
        assert_eq!(result.valid_columns, weights.len());
        assert_eq!(result.profile.level_count, profile.level_count);
        for (actual, expected) in result.profile.matrix.iter().zip(&profile.matrix) {
            assert!(
                (actual - expected).abs() < 1.0e-10,
                "{actual} != {expected}"
            );
        }
        assert!(result
            .coverage
            .iter()
            .all(|coverage| (coverage.fraction - 1.0).abs() < 1.0e-12));
    }
}

#[test]
fn averages_water_mass_and_wind_vectors_with_physical_overlap_weights() {
    let dry = point(
        &[1000.0, 800.0],
        &[100.0, 2000.0],
        &[30.0, 20.0],
        0.002,
        10.0,
    );
    let moist = point(
        &[1000.0, 800.0],
        &[200.0, 2200.0],
        &[30.0, 20.0],
        0.010,
        -10.0,
    );
    let result = run(&[column(&dry, 1.0), column(&moist, 3.0)], 4.0).unwrap();
    let n = result.profile.level_count;
    let row = &result.profile.matrix;
    assert!((row[n] - 175.0).abs() < 1.0e-12);
    assert!((specific_humidity_from_dewpoint(row[3 * n], row[0]) - 0.008).abs() < 1.0e-12);
    let wrong_dewpoint_mean = (dry.matrix[3 * n] + 3.0 * moist.matrix[3 * n]) / 4.0;
    assert!((row[3 * n] - wrong_dewpoint_mean).abs() > 0.5);
    assert!((row[7 * n] + 5.0).abs() < 1.0e-12);
    assert!((row[4 * n] - 90.0).abs() < 1.0e-12);
    assert!((row[5 * n] - 5.0 * MPS_TO_KNOTS).abs() < 1.0e-12);
}

#[test]
fn aligns_native_levels_on_log_pressure_without_extrapolation() {
    let low = point(&[1000.0, 800.0], &[0.0, 2000.0], &[30.0, 10.0], 0.002, 10.0);
    let high = point(
        &[900.0, 800.0],
        &[1000.0, 2000.0],
        &[40.0, 20.0],
        0.002,
        20.0,
    );
    let result = run(&[column(&low, 1.0), column(&high, 1.0)], 2.0).unwrap();
    let ratio = 0.9_f64.ln() / 0.8_f64.ln();
    assert_eq!(&result.profile.matrix[..2], &[900.0, 800.0]);
    assert!((result.profile.matrix[4] - ((30.0 - 20.0 * ratio) + 40.0) / 2.0).abs() < 1.0e-10);
    assert!((result.profile.matrix[2] - (2000.0 * ratio + 1000.0) / 2.0).abs() < 1.0e-10);
    assert_eq!(result.surface_pressure_hpa, 900.0);
}

#[test]
fn complex_terrain_uses_one_common_above_ground_base() {
    let valley = point(
        &[1000.0, 800.0, 600.0],
        &[0.0, 2000.0, 4000.0],
        &[35.0, 25.0, 15.0],
        0.001,
        5.0,
    );
    let mountain = point(
        &[800.0, 700.0, 600.0],
        &[2100.0, 3000.0, 4100.0],
        &[20.0, 15.0, 10.0],
        0.001,
        15.0,
    );
    let result = run(&[column(&valley, 1.0), column(&mountain, 1.0)], 2.0).unwrap();
    assert_eq!(result.surface_pressure_hpa, 800.0);
    assert_eq!(result.profile.matrix[0], 800.0);
    assert_eq!(result.profile.matrix[result.profile.level_count], 2050.0);
    assert_eq!(result.profile.matrix[2 * result.profile.level_count], 22.5);
    assert!(result
        .coverage
        .iter()
        .all(|level| level.pressure_hpa <= 800.0));
}

#[test]
fn missing_fields_use_joint_coverage_and_do_not_bridge_a_missing_native_level() {
    let a = point(
        &[1000.0, 800.0, 700.0, 600.0],
        &[0.0, 2000.0, 3000.0, 4000.0],
        &[30.0, 20.0, 10.0, 0.0],
        0.001,
        5.0,
    );
    let mut b = a.clone();
    b.matrix[2 * 4 + 2] = MISSING;
    let result = run(&[column(&a, 1.0), column(&b, 1.0)], 2.0).unwrap();
    let missing = result
        .coverage
        .iter()
        .find(|level| level.pressure_hpa == 700.0)
        .unwrap();
    assert_eq!(missing.fraction, 0.5);
    assert_eq!(missing.valid_columns, 1);
    assert!(!missing.retained);
    assert_eq!(
        &result.profile.matrix[..result.profile.level_count],
        &[1000.0, 800.0, 600.0]
    );
    b.surface_merged = false;
    assert!(matches!(
        run(&[column(&a, 0.75), column(&b, 0.25)], 1.0),
        Err(Error::Data(_))
    ));
    assert!(matches!(run(&[column(&a, 1.0)], 2.0), Err(Error::Data(_))));
}

#[test]
fn optional_vorticity_is_aligned_and_optional_omega_is_not_invented() {
    let a = point(
        &[1000.0, 900.0, 800.0],
        &[0.0, 1000.0, 2000.0],
        &[30.0, 20.0, 10.0],
        0.001,
        5.0,
    );
    let vort = [(900.0, 1.0e-4), (800.0, 2.0e-4)];
    let result = run(
        &[Column {
            profile: &a,
            overlap_area: 1.0,
            vorticity: &vort,
        }],
        1.0,
    )
    .unwrap();
    assert_eq!(result.profile.surface_relative_vorticity, Some(1.0e-4));
    assert!(result.profile.matrix[6 * 3..7 * 3]
        .iter()
        .all(|value| *value == MISSING));
}

#[test]
fn cancellation_and_workload_limits_are_enforced_before_returning_a_result() {
    let a = point(
        &[1000.0, 900.0, 800.0],
        &[0.0, 1000.0, 2000.0],
        &[30.0, 20.0, 10.0],
        0.001,
        5.0,
    );
    let calls = Cell::new(0);
    let stop = || {
        calls.set(calls.get() + 1);
        calls.get() > 1
    };
    let bounds = Bounds::new(-98.0, 35.0, -97.0, 36.0).unwrap();
    assert_eq!(
        aggregate(&[column(&a, 1.0)], bounds, 1.0, Limits::default(), &stop).unwrap_err(),
        Error::Cancelled
    );
    let limits = Limits {
        max_columns: 1,
        ..Limits::default()
    };
    assert!(matches!(
        aggregate(
            &[column(&a, 1.0), column(&a, 1.0)],
            bounds,
            2.0,
            limits,
            &|| false
        ),
        Err(Error::Invalid(_))
    ));
}

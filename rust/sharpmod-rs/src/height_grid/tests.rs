use super::*;

const DOUBLE: Precision = Precision {
    heights_f32: false,
    values_f32: false,
    target_f32: false,
};

#[test]
fn edges_outside_and_missing_brackets_keep_the_first_finite_value() {
    assert_eq!(
        interpolate(
            &[0., 0., 1000., 1000.],
            &[20., 20., 10., 0.],
            &[500., 750.],
            2,
            DOUBLE,
            f64::NAN
        ),
        vec![15., 5.],
    );
    let heights = [0., 1000., 2000., 1000., 3000.];
    let values = [20., 10., f64::NAN, 30., 0.];
    for (target, expected) in [
        (0., 20.),
        (500., 15.),
        (1000., 10.),
        (1500., 22.5),
        (3000., 0.),
    ] {
        assert_eq!(
            interpolate(&heights, &values, &[target], 5, DOUBLE, -9999.),
            vec![expected]
        );
    }
    for target in [-1., 3001., f64::NAN, f64::INFINITY] {
        assert_eq!(
            interpolate(&heights, &values, &[target], 5, DOUBLE, -9999.),
            vec![-9999.]
        );
    }
}

#[test]
fn flat_layers_empty_columns_and_nonfinite_candidates_stay_missing() {
    for (heights, values) in [
        (vec![0., 0.], vec![20., 10.]),
        (vec![0., 1000.], vec![f64::INFINITY, 10.]),
        (vec![f64::NAN, 1000.], vec![20., 10.]),
        (vec![0.], vec![20.]),
        (vec![], vec![]),
    ] {
        assert!(interpolate(&heights, &values, &[0.], heights.len(), DOUBLE, f64::NAN)[0].is_nan());
    }
    assert!(interpolate(&[], &[], &[], 0, DOUBLE, f64::NAN).is_empty());
}

#[test]
fn a_finite_missing_representation_does_not_mean_an_assigned_value() {
    let result = interpolate(
        &[0., 1000., 2000.],
        &[-9999., -9999., 30.],
        &[1000.],
        3,
        DOUBLE,
        -9999.,
    );
    assert_eq!(result, vec![-9999.]);
}

#[test]
fn float32_weights_and_value_deltas_round_before_float64_output() {
    let single = Precision {
        heights_f32: true,
        values_f32: true,
        target_f32: true,
    };
    let result = interpolate(&[0., 3.], &[20., 10.], &[1.], 2, single, f64::NAN);
    assert_eq!(
        result,
        vec![20. + f64::from(-10_f32) * f64::from(1_f32 / 3_f32)]
    );
    assert_ne!(
        result,
        interpolate(&[0., 3.], &[20., 10.], &[1.], 2, DOUBLE, f64::NAN)
    );
    let mixed = Precision {
        target_f32: false,
        ..single
    };
    assert_eq!(
        interpolate(&[0., 3.], &[20., 10.], &[1.], 2, mixed, f64::NAN),
        vec![20. - 10. / 3.]
    );
}

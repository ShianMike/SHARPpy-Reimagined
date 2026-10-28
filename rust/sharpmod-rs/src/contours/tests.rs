use super::*;

#[test]
fn interpolates_levels_and_preserves_saddle_pairing_order() {
    assert_eq!(
        extract(&[0., 2., 0., 2.], 2, 2, 1., 100).unwrap(),
        vec![((0.5, 0.), (0.5, 1.)), ((1., 0.), (1., 1.))]
    );
    assert_eq!(
        extract(&[0., 2., 2., 0.], 2, 2, 1., 100).unwrap(),
        vec![
            ((0.5, 0.), (1., 0.5)),
            ((0.5, 1.), (0., 0.5)),
            ((1., 0.), (1., 0.)),
            ((0., 1.), (0., 1.))
        ]
    );
}

#[test]
fn nonfinite_endpoints_flat_fields_and_invalid_intervals_do_not_add_crossings() {
    assert!(extract(&[f64::NAN; 4], 2, 2, 1., 100).unwrap().is_empty());
    assert!(extract(&[1.; 4], 2, 2, 1., 100).unwrap().is_empty());
    for interval in [0., -1., f64::NAN, f64::INFINITY] {
        assert!(extract(&[0., 2., 0., 2.], 2, 2, interval, 100)
            .unwrap()
            .is_empty());
    }
    assert_eq!(
        extract(&[f64::NAN, 0., 0., 2.], 2, 2, 1., 100).unwrap(),
        vec![((1., 0.5), (0.5, 1.)), ((1., 1.), (1., 1.))]
    );
    assert!(extract(&[1.], 1, 1, 1., 100).unwrap().is_empty());
}

#[test]
fn rejects_mismatched_shapes_and_dense_or_extreme_level_sets() {
    assert!(extract(&[1.], 2, 2, 1., 100).is_err());
    assert_eq!(
        extract(&[0., 2., 0., 2.], 2, 2, 1., 1).unwrap_err(),
        TOO_DENSE
    );
    assert_eq!(
        extract(&[0., 1., 0., 1.], 2, 2, 1e-20, 100_000).unwrap_err(),
        TOO_DENSE
    );
}

#[test]
fn projects_from_cell_centres_with_fixed_decimal_precision() {
    let mut segments = [((0.5, 0.), (0.5, 1.))];
    project(&mut segments, 2, 2, (-100., -96., 32., 36.)).unwrap();
    assert_eq!(segments, [((-98., 35.), (-98., 33.))]);
    assert!(project(&mut segments, 2, 2, (-181., -96., 32., 36.)).is_err());
}

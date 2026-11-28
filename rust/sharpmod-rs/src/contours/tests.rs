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
fn grid_scan_keeps_level_then_case_then_cell_order() {
    let values = [2., 4., 0., 2., 0., 4., -2., 4., 0.];
    assert_eq!(
        extract_display_levels(&values, 3, 3, 2., 100).unwrap(),
        [
            (0., ((1. / 3., 2.), (0., 1.5))),
            (2., ((1., 0.5), (0., 1.))),
            (2., ((1.5, 0.), (2., 0.5))),
            (2., ((1.5, 1.), (1., 0.5))),
            (2., ((0., 1.), (1., 1.5))),
            (2., ((2. / 3., 2.), (0., 1.))),
            (2., ((1.5, 1.), (2., 1.5))),
            (2., ((1.5, 2.), (1., 1.5))),
            (4., ((1., 0.), (1., 0.))),
            (4., ((1., 2.), (1., 2.))),
            (4., ((1., 0.), (2., 1.))),
            (4., ((2., 1.), (1., 0.))),
            (4., ((2., 1.), (2., 1.))),
            (4., ((1., 2.), (1., 2.))),
        ]
    );
}

#[test]
fn global_level_bounds_and_actual_crossings_are_distinct() {
    let level = 17.0_f64 * 0.1;
    let low = level.next_down();
    assert_eq!(low / 0.1, 17.0);
    // A separate lower sample includes the level in the original global range.
    let values = [low, level, 1.65, low, level, 1.65];
    assert_eq!(
        extract_display_levels(&values, 2, 3, 0.1, 100).unwrap(),
        [(level, ((1., 0.), (1., 1.))), (level, ((1., 0.), (1., 1.)))]
    );
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

#[test]
fn projection_fast_rounding_keeps_decimal_ties_and_signed_zero() {
    for step in [-18_000_000, -12_345_678, -1, 0, 1, 1_234_567, 17_999_999] {
        let tie = (f64::from(step) + 0.5) / 100_000.0;
        for coordinate in [tie.next_down(), tie, tie.next_up(), -0.0] {
            let expected = format!("{coordinate:.5}").parse::<f64>().unwrap();
            assert_eq!(round_coordinate(coordinate).to_bits(), expected.to_bits());
        }
    }
}

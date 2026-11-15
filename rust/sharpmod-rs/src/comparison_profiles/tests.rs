use super::*;

fn prepared(pairs: &[(f64, f64)], order: &[isize], max_gap: f64) -> Interpolator {
    Interpolator::from_ordered_indices(pairs, order, max_gap)
}

#[test]
fn interpolation_respects_snap_first_match_and_gap_limit() {
    let interpolator = prepared(&[(1000., 10.), (0., 0.), (2500., 20.)], &[1, 0, 2], 1500.);
    assert_eq!(interpolator.sample(-0.009), Some(0.));
    assert_eq!(interpolator.sample(500.), Some(5.));
    assert_eq!(interpolator.sample(1750.), Some(15.));
    assert_eq!(interpolator.sample(2500.02), Some(20.));
    assert_eq!(interpolator.sample(2500.04), None);
    assert_eq!(interpolator.sample(f64::NAN), None);
    let limited = prepared(&[(0., 0.), (1500.1, 10.)], &[0, 1], 1500.);
    assert_eq!(limited.sample(750.), None);
    assert_eq!(limited.sample(1500.1), Some(10.));
    let nan_limit = prepared(&[(0., 0.), (2000., 10.)], &[0, 1], f64::NAN);
    assert_eq!(nan_limit.sample(1000.), Some(5.));
}

#[test]
fn duplicate_levels_keep_first_value_in_numpy_supplied_order() {
    let pairs = [(100., 1.), (100., 2.), (200., 3.), (100., 4.)];
    let interpolator = prepared(&pairs, &[3, 1, 0, 2], 1000.);
    assert_eq!(interpolator.pairs, [(100., 4.), (200., 3.)]);
    assert_eq!(interpolator.sample(100.), Some(4.));
    assert_eq!(interpolator.sample(150.), Some(3.5));
    let close_levels = prepared(&[(100., 11.), (100.01, 12.)], &[0, 1], 100.);
    assert_eq!(close_levels.sample(100.009), Some(11.));
}

#[test]
fn finite_pairs_preserve_field_specific_masks_and_truncate_lengths() {
    let heights = [0., 100., f64::NAN, 300., 400.];
    let values = [1., f64::INFINITY, 3., 4.];
    assert_eq!(finite_pairs(&heights, &values), [(0., 1.), (300., 4.)]);
    assert!(finite_pairs(&heights, &[]).is_empty());
}

#[test]
fn coverage_uses_finite_heights_even_without_usable_field_data() {
    assert!(matches!(
        coverage(&[f64::NAN], &[1.]),
        Coverage::MissingHeights
    ));
    assert!(matches!(
        coverage(&[0., 100.], &[200., 300.]),
        Coverage::Disjoint
    ));
    assert!(matches!(
        coverage(&[200., 0., 100.], &[50., 300.]),
        Coverage::Common(50., 200.)
    ));
    assert!(matches!(
        coverage(&[0., 100.], &[100., 300.]),
        Coverage::Common(100., 100.)
    ));
}

#[test]
fn differences_preserve_reference_order_duplicate_pressures_and_missing_values() {
    let heights = [1000., 0., 500., 500., f64::NAN];
    let pressures = [900., 1000., 950., 950.];
    let reference = [
        vec![10., 0., 5., 5.],
        vec![2.],
        vec![1., 2., f64::NAN, 4.],
        vec![],
    ];
    let candidate = [
        Some(prepared(&[(0., 2.), (1000., 12.)], &[0, 1], 1500.)),
        Some(prepared(&[(0., 4.), (1000., 6.)], &[0, 1], 1500.)),
        Some(prepared(&[(0., 2.), (1000., 4.)], &[0, 1], 1500.)),
        None,
    ];
    let rows = difference_levels(&heights, &pressures, &reference, &candidate, 0., 1000.);
    assert_eq!(rows.len(), 4);
    assert_eq!(
        rows.iter().map(|row| row.height).collect::<Vec<_>>(),
        [1000., 0., 500., 500.]
    );
    assert_eq!(
        rows.iter().map(|row| row.pressure).collect::<Vec<_>>(),
        [Some(900.), Some(1000.), Some(950.), Some(950.)]
    );
    assert_eq!(rows[0].differences, [Some(2.), Some(4.), Some(3.), None]);
    assert_eq!(rows[1].differences, [Some(2.), None, Some(0.), None]);
    assert_eq!(rows[2].differences, [Some(2.), None, None, None]);
    assert_eq!(rows[3].differences, [Some(2.), None, Some(-1.), None]);
}

#[test]
fn overlapping_columns_can_have_no_reference_levels() {
    assert!(matches!(
        coverage(&[0., 1000.], &[200., 300.]),
        Coverage::Common(200., 300.)
    ));
    let reference = std::array::from_fn(|_| vec![1., 2.]);
    let candidate = std::array::from_fn(|_| None);
    assert!(difference_levels(&[0., 1000.], &[], &reference, &candidate, 200., 300.).is_empty());
}

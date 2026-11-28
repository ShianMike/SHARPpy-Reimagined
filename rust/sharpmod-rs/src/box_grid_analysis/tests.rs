use super::lattice::{
    plan_box_lattice_impl, region_contains_impl, region_from_bounds_impl, region_from_corners_impl,
};
use super::stats::{compute_envelope_stats, compute_field_statistics};
use super::transect::compute_transect_column;

#[test]
fn region_bounds_antimeridian_and_containment() {
    let (lat0, lat1, lon0, span) = region_from_corners_impl(35.0, 190.0, 40.0, 170.0).unwrap();
    assert_eq!((lat0, lat1, lon0, span), (35.0, 40.0, 170.0, 20.0));
    assert!(region_contains_impl(lat0, lat1, lon0, span, 37.0, -175.0));
    assert!(!region_contains_impl(lat0, lat1, lon0, span, 37.0, -165.0));

    let world = region_from_bounds_impl(-180.0, 180.0, -30.0, 30.0).unwrap();
    assert_eq!(world.3, 360.0);
    assert!(region_from_corners_impl(35.0, -97.0, 35.0, -96.0).is_err());
}

#[test]
fn lattice_planning_snaps_coarsens_and_orders_north_to_south() {
    let (spacing, rows, cols, points, notes) = plan_box_lattice_impl(
        34.0,
        37.0,
        -98.0,
        4.0,
        3.0,
        false,
        "HRRR",
        Some(1.0),
        None,
        Some(16),
    )
    .expect("planned");
    assert!(rows * cols <= 16);
    assert!(spacing > 3.0);
    assert_eq!(notes.len(), 2);
    assert_eq!(points[0].0, 0);
    assert!(points[0].2 > points[points.len() - 1].2);
}

#[test]
fn transect_circular_wdir_crosses_north() {
    let pres = [900.0, 700.0];
    let wdir = [350.0, 10.0];
    let wspd = [20.0, 20.0];
    let log_ladder = [(800.0_f64).log10()];
    let out = compute_transect_column(&pres, &wdir, Some(&wspd), &log_ladder, true).unwrap();
    let mid_dir = out[0].unwrap();
    assert!(
        !(5.0..=355.0).contains(&mid_dir),
        "expected ~0 deg, got {mid_dir}"
    );
}

#[test]
fn statistics_and_circular_envelope_wrap_correctly() {
    let vals = [100.0, 300.0, 200.0, 400.0];
    let stats = compute_field_statistics(&vals, true).unwrap();
    assert_eq!(stats.0, 4);
    assert_eq!(stats.1, 100.0);
    assert_eq!(stats.2, 400.0);
    assert!((stats.3 - 250.0).abs() < 1e-12);
    assert!((stats.4 - 250.0).abs() < 1e-12);
    assert!((stats.5 - 130.0).abs() < 1e-12);
    assert!((stats.6 - 370.0).abs() < 1e-12);
    assert_eq!(stats.7, 3);

    let cols = vec![vec![Some(350.0), None], vec![Some(10.0), Some(180.0)]];
    let env = compute_envelope_stats(&cols, 2, 10.0, 90.0, true);
    assert_eq!(env.5, vec![2, 1]);
    assert!((env.0[0].unwrap() - 350.0).abs() < 1e-9);
    assert!((env.2[0].unwrap() - 360.0).abs() < 1e-9);
    assert!((env.4[0].unwrap() - 370.0).abs() < 1e-9);
}

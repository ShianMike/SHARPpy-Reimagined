use super::*;

fn profile(t: &[f64], tw: &[f64]) -> WinterProfile {
    let n = t.len();
    let c = vec![
        (0..n).map(|i| 1000. - i as f64 * 100.).collect(),
        (0..n).map(|i| i as f64 * 1000.).collect(),
        t.to_vec(),
        t.to_vec(),
        tw.to_vec(),
        vec![-0.3; n],
        vec![10.; n],
        vec![0.; n],
    ];
    WinterProfile::new(&c, 0, -9999.).unwrap()
}

#[test]
fn saturated_dgz_has_exact_interpolated_boundaries_and_pressure_weighted_moisture() {
    let p = profile(&[-5., -10., -20., -30.], &[-5., -10., -20., -30.]);
    assert_eq!(p.dgz.len(), 1);
    let l = &p.dgz[0];
    assert!((l.bottom.z - 1200.).abs() < 1e-8);
    assert!((l.top.z - 1800.).abs() < 1e-8);
    assert!((l.means()[0] - 100.).abs() < 1e-8);
    assert!(l.means()[1] > 110.);
    assert!((l.means()[2] + 3.).abs() < 1e-8);
}

#[test]
fn warm_nose_and_surface_refreeze_integrate_exact_zero_crossings() {
    let p = profile(&[-4., 4., -4., -20.], &[-4., 4., -4., -20.]);
    assert_eq!(p.warm.len(), 1);
    let warm = &p.warm[0];
    assert_eq!(warm.bottom.z, 500.);
    assert_eq!(warm.top.z, 1500.);
    assert!((warm.energy - 9.80665 / 273.15 * 2000.).abs() < 1e-8);
    let diagnostics = p.diagnostics(700.);
    let surface_cold = diagnostics
        .thermal
        .iter()
        .find(|l| l.bottom.z == 0.)
        .unwrap();
    assert!((surface_cold.energy + 9.80665 / 273.15 * 1000.).abs() < 1e-8);
    assert_eq!(p.wet_zero.len(), 2);
}

#[test]
fn missing_temperature_does_not_bridge_layers_or_fabricate_zero() {
    let p = profile(
        &[-12., -15., f64::NAN, -15., -18.],
        &[-12., -15., f64::NAN, -15., -18.],
    );
    assert_eq!(p.dgz.len(), 2);
    assert_eq!(p.warm.len(), 0);
    assert!(p.wet_zero.is_empty());
}

#[test]
fn multiple_dgz_crossings_stay_separate() {
    let p = profile(&[-5., -20., -5., -25.], &[-5., -20., -5., -25.]);
    assert_eq!(p.dgz.len(), 3);
}

#[test]
fn vapor_pressure_and_wind_chill_match_reference_values() {
    assert!((vapor(0., false) - 611.213).abs() < 0.1);
    assert!((vapor(-20., true) - 103.252).abs() < 0.1);
    // NWS chart: 0 F and 10 mph is approximately -16 F.
    let chill = wind_chill(-32. / 1.8, 10. / 1.150779448).unwrap() * 1.8 + 32.;
    assert!((chill + 15.9).abs() < 0.2);
    assert!(wind_chill(11., 20.).is_none());
    assert!(wind_chill(-10., 0.).is_none());
}

#[test]
fn no_dgz_and_truncated_profiles_are_explicitly_unavailable() {
    let p = profile(&[15., 10., 5.], &[12., 8., 3.]);
    assert!(p.dgz.is_empty());
    assert!(p.dgz_means()[0].is_nan());
    assert!(p.snow_ratio().is_none());
    assert!(WinterProfile::new(&[], 0, -9999.).is_err());
}

#[test]
fn zero_isotherm_does_not_create_melting_or_refreezing_energy() {
    let p = profile(&[0., 0., 0.], &[0., 0., 0.]);
    assert!(p.warm.is_empty());
    assert!(p.diagnostics(800.).thermal.is_empty());
}

#[test]
fn winter_precipitation_tracks_snow_melting_and_deep_refreezing() {
    let snow = profile(&[-4., -8., -12., -20.], &[-4., -8., -12., -20.]);
    assert_eq!(snow.diagnostics(750.).ptype, "Snow");
    let rain = profile(&[-1., 4., -12., -20.], &[-1., 4., -12., -20.]);
    assert_eq!(rain.diagnostics(750.).ptype, "Freezing rain");
    let pellets = profile(&[-15., -12., 4., -12., -20.], &[-15., -12., 4., -12., -20.]);
    assert_eq!(pellets.diagnostics(650.).ptype, "Ice pellets");
    let drizzle = profile(&[-2., -3., -4.], &[-2., -3., -4.]);
    assert_eq!(drizzle.diagnostics(850.).ptype, "Freezing drizzle");
    assert_eq!(drizzle.diagnostics(-9999.).ptype, "No saturated source");
}

#[test]
fn winter_diagnostics_reject_missing_coverage_and_incomplete_omega() {
    let mut p = profile(&[-4., -8., -14., -20.], &[-4., -8., -14., -20.]);
    let d = p.diagnostics(750.);
    assert!(d.oprh < 0. && d.dgz_pw > 0.);
    assert_eq!(d.dgz_wind, [10., 0.]);
    assert!(d.max_wind == 10.);
    p.levels[1].u = f64::NAN;
    assert!(p.diagnostics(750.).max_wind.is_nan());
    p.levels[1].z = f64::NAN;
    assert!(p.section(0., 2000.).is_none());
    assert_eq!(p.diagnostics(750.).ptype, "Unavailable");
    let short = profile(&[-4., -8.], &[-4., -8.]);
    let d = short.diagnostics(950.);
    assert!(d.squall.is_nan() && d.max_wind.is_nan());
    assert_eq!(d.drizzle[1], None); // Cloud continues above the truncated profile.
    assert_eq!(d.drizzle[2], None);
}

#[test]
fn winter_squall_matches_banacos_equation_and_warm_surface_gate() {
    let mut p = profile(&[-1., -10., -20., -30.], &[-1., -10., -20., -30.]);
    let dtheta = p.levels[2].theta_e() - p.levels[0].theta_e();
    let expected = (100. - 60.) / 15. * ((4. - dtheta) / 4.).max(0.) * (10. * 0.514444 / 9.);
    assert!((p.diagnostics(750.).squall - expected).abs() < 1e-10);
    p.levels[0].tw = 2.;
    assert_eq!(p.diagnostics(750.).squall, 0.);
    p.levels[1].rh = f64::NAN;
    assert!(p.diagnostics(750.).squall.is_nan());
}

#[test]
fn winter_fog_and_drizzle_checks_keep_unknowns_distinct_from_unmet() {
    let mut p = profile(&[-4., -2., -12., -20.], &[-4., -2., -12., -20.]);
    for l in &mut p.levels {
        l.u = 4.;
    }
    let d = p.diagnostics(750.);
    assert_eq!(d.fog, [Some(true); 4]);
    assert!(d.pbl.unwrap().z > 0. && d.pbl.unwrap().z < 1000.);
    assert_eq!(d.drizzle[1], None); // Still saturated at the profile top.
    p.levels[0].u = f64::NAN;
    assert_eq!(p.diagnostics(750.).fog[2], None);
}

#[test]
fn winter_interpolation_preserves_dgz_humidity_across_zero_celsius() {
    let p = profile(&[2., -20., -30.], &[2., -20., -30.]);
    assert!(p.dgz_means()[1] > 100.);
    assert!((p.dgz_means()[0] - 100.).abs() < 1e-8);
    let at = p.at_pressure(950.).unwrap();
    assert!(at.z > 0. && at.z < 1000.);
    assert!(p.at_pressure(1100.).is_none());
}

use super::*;

#[test]
fn ehi_keeps_signed_helicity_scaling_and_missing_inputs() {
    assert_eq!(evaluate(Diagnostic::Ehi, &[1600.0f64, 200.0]), 2.0);
    assert_eq!(evaluate(Diagnostic::Ehi, &[1600.0f32, -100.0]), -1.0);
    assert!(evaluate(Diagnostic::Ehi, &[f64::NAN, 200.0]).is_nan());
}

#[test]
fn supercell_shear_gate_and_saturation_match_the_fixed_layer_definition() {
    for (shear, expected) in [(9.99, 0.0), (10.0, 1.0), (20.0, 2.0), (50.0, 2.0)] {
        assert_eq!(
            evaluate(Diagnostic::Scp, &[1000.0f64, 100.0, shear, 0.0]),
            expected
        );
    }
    assert_eq!(
        evaluate(Diagnostic::Scp, &[-100.0f32, 100.0, 20.0, 0.0]),
        0.0
    );
    assert!(evaluate(Diagnostic::Scp, &[f64::NAN, 100.0, 20.0, 0.0]).is_nan());
    assert!(evaluate(Diagnostic::Scp, &[1000.0, 100.0, f64::NAN, 0.0]).is_nan());
}

#[test]
fn tornado_lcl_cin_and_shear_terms_preserve_their_thresholds() {
    let base = [1500.0f64, 1000.0, 150.0, 20.0, 0.0, -50.0];
    assert_eq!(evaluate(Diagnostic::Stp, &base), 1.0);
    for (index, value, expected) in [
        (1, 1500.0, 0.5),
        (1, 2000.0, 0.0),
        (3, 12.49, 0.0),
        (3, 30.0, 1.5),
        (3, 40.0, 1.5),
        (5, -125.0, 0.5),
        (5, -200.0, 0.0),
    ] {
        let mut values = base;
        values[index] = value;
        assert_eq!(evaluate(Diagnostic::Stp, &values), expected);
    }
    for index in 0..base.len() {
        let mut values = base;
        values[index] = f64::NAN;
        assert!(evaluate(Diagnostic::Stp, &values).is_nan());
    }
}

#[test]
fn lapse_uses_geopotential_thickness_and_refuses_collapsed_layers() {
    assert_eq!(
        evaluate(Diagnostic::Lapse, &[280.0f32, 260.0, 3000.0, 5500.0]),
        8.0
    );
    for top in [2900.0, 3000.0, 3100.0, f64::NAN] {
        assert!(evaluate(Diagnostic::Lapse, &[280.0f64, 260.0, 3000.0, top]).is_nan());
    }
}

#[test]
fn precipitation_type_is_a_flag_and_keeps_invalid_cells_missing() {
    assert_eq!(
        evaluate(Diagnostic::PrecipitationType, &[0.001f64, 1.0]),
        3.6
    );
    assert_eq!(
        evaluate(Diagnostic::PrecipitationType, &[0.001f32, 0.0]),
        0.0
    );
    for pair in [
        [-0.001, 1.0],
        [f64::INFINITY, 1.0],
        [0.001, 0.5],
        [0.001, 2.0],
        [0.001, f64::NAN],
    ] {
        assert!(evaluate(Diagnostic::PrecipitationType, &pair).is_nan());
    }
    assert!(precipitation_type(0.001f32, 1.0 + 1e-12).is_nan());
}

#[test]
fn thetae_reuses_bolton_liquid_water_physics_and_requires_actual_surface_pressure() {
    let result = evaluate(Diagnostic::ThetaE, &[85000.0f64, 293.15, 291.15]);
    assert!((result - 353.898874).abs() < 0.000001);
    for pressure in [0.0, -1.0, f64::NAN, f64::INFINITY] {
        assert!(evaluate(Diagnostic::ThetaE, &[pressure, 293.15, 288.15]).is_nan());
    }
    assert!(evaluate(Diagnostic::ThetaE, &[100000.0f64, 0.0, 288.15]).is_nan());
}

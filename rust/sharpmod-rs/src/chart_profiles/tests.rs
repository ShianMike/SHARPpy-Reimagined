use super::*;

#[test]
fn height_grid_stops_below_the_reported_top_and_uses_the_surface() {
    let grid = height_grid(
        &[100., 300., 800., 1300., 1700.],
        1,
        GridSpec::new(12000., 250.).unwrap(),
    )
    .unwrap();
    assert_eq!(grid.samples, [0., 250., 500., 750., 1000., 1250.]);
    assert_eq!(grid.valid, [false, true, true, true, true]);
    assert_eq!(grid.agl[0], -200.);
    assert!(height_grid(&[0., 100.], 0, GridSpec::new(1000., 250.).unwrap()).is_none());
    assert!(height_grid(&[0., 1000.], 2, GridSpec::new(1000., 250.).unwrap()).is_none());
    assert_eq!(
        height_grid(&[0., 1000.], 0, GridSpec::new(f64::INFINITY, 250.).unwrap())
            .unwrap()
            .samples
            .last(),
        Some(&1000.)
    );
    assert!(height_grid(
        &[0., 1000.],
        0,
        GridSpec::new(1000., f64::MIN_POSITIVE).unwrap()
    )
    .is_none());
    for (maximum, step) in [(0., 100.), (1000., 0.), (1000., -1.), (f64::NAN, 100.)] {
        assert!(GridSpec::new(maximum, step).is_none());
    }
}

#[test]
fn interpolation_keeps_first_duplicate_and_leaves_unobserved_ends_missing() {
    let values = interpolate(
        &[-1., 0., 250., 500., 1000., 1250.],
        &[1000., 0., 500., 500., 1500.],
        &[true; 5],
        &[20., 0., 10., 999., f64::NAN],
    )
    .unwrap();
    assert!(values[0].is_nan());
    assert_eq!(&values[1..5], &[0., 5., 10., 20.]);
    assert!(values[5].is_nan());
    // Negative and positive zero are one height, retaining report order.
    assert_eq!(
        interpolate(&[0., 500.], &[0., -0., 1000.], &[true; 3], &[7., 99., 17.]).unwrap(),
        [7., 12.]
    );
    assert!(interpolate(&[0.], &[0., 0.], &[true; 2], &[1., 2.]).is_none());
}

#[test]
fn wind_components_keep_independent_missing_endpoints() {
    let grid = height_grid(&[0., 500., 1000.], 0, GridSpec::new(1000., 250.).unwrap()).unwrap();
    let speeds =
        storm_relative_wind(&grid, &[f64::NAN, 10., 20.], &[0., 5., f64::NAN], [0., 0.]).unwrap();
    assert!(speeds[0].is_nan() && speeds[1].is_nan() && speeds[3].is_nan() && speeds[4].is_nan());
    assert!((speeds[2] - 10_f64.hypot(5.)).abs() < 1e-12);
}

#[test]
fn streamwiseness_retains_rotation_sign_and_suppresses_zero_shear() {
    let heights: Vec<_> = (0..=12).map(|i| i as f64 * 500.).collect();
    for direction in [-1., 1.] {
        let u: Vec<_> = heights
            .iter()
            .map(|&z| 20. * (z / 6000. * std::f64::consts::FRAC_PI_2).cos())
            .collect();
        let v: Vec<_> = heights
            .iter()
            .map(|&z| direction * 20. * (z / 6000. * std::f64::consts::FRAC_PI_2).sin())
            .collect();
        let data = streamwiseness(
            &heights,
            &u,
            &v,
            0,
            [0., 0.],
            GridSpec::new(6000., 100.).unwrap(),
        )
        .unwrap();
        assert_eq!(data.height.last(), Some(&6.));
        assert!(data.percent.iter().all(|v| (0. ..=100.).contains(v)));
        assert!(data.signed[1..data.signed.len() - 1]
            .iter()
            .all(|v| v * direction < -98.));
    }
    assert!(streamwiseness(
        &heights,
        &[10.; 13],
        &[5.; 13],
        0,
        [0., 0.],
        GridSpec::new(6000., 100.).unwrap()
    )
    .is_none());
}

#[test]
fn streamwiseness_drops_joint_missing_levels_and_clamps_the_low_endpoint() {
    let data = streamwiseness(
        &[0., 1000., 2000.],
        &[f64::NAN, 10., 30.],
        &[0., 10., 0.],
        0,
        [0., 0.],
        GridSpec::new(2000., 500.).unwrap(),
    )
    .unwrap();
    assert!(data.percent[0].is_nan()); // Endpoint-clamped wind has no shear.
    assert!(data.percent[1].is_nan());
    assert!(data.percent[2..].iter().all(|v| v.is_finite()));
}

#[test]
fn kelvin_normalization_uses_finite_median_and_preserves_gaps() {
    let values = kelvin(vec![f64::NAN, 30., 40., f64::INFINITY]);
    assert!(values[0].is_nan());
    assert_eq!(&values[1..3], &[303.15, 313.15]);
    assert_eq!(values[3], f64::INFINITY);
    assert_eq!(kelvin(vec![149., 151., 300.]), [149., 151., 300.]);
    assert_eq!(kelvin(vec![149., 151.]), [149., 151.]);
}

#[test]
#[ignore = "requires pinned Qt/NumPy and SHARPMOD_CHART_REFERENCE_DIR containing the original source snapshots"]
fn native_chart_profiles_match_original_numpy_functions() {
    use numpy::IntoPyArray;
    use pyo3::{prelude::*, types::PyDict};
    pyo3::Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let extension = PyModule::import(py, "sharpmod_rs")?;
        register(&extension)?;
        let np = PyModule::import(py, "numpy")?;
        let namespace = PyModule::import(py, "types")?.getattr("SimpleNamespace")?;
        let root = std::env::var("SHARPMOD_CHART_REFERENCE_DIR").unwrap();
        let original = |name: &str, file: &str| -> PyResult<Bound<'_, PyModule>> {
            let module = PyModule::new(py, name)?;
            PyModule::import(py, "sys")?.getattr("modules")?.set_item(name, &module)?;
            let source = std::fs::read_to_string(std::path::Path::new(&root).join(file)).unwrap();
            PyModule::import(py, "builtins")?.call_method1("exec", (source, module.dict()))?;
            Ok(module)
        };
        let wind_reference = original("_chart_wind_reference", "streamwiseness-reference.txt")?;
        let height_reference = original("_chart_height_reference", "height-charts-reference.txt")?;
        let current_wind = PyModule::import(py, "sharpmod.viz.streamwiseness")?;
        let current_height = PyModule::import(py, "sharpmod.viz.height_charts")?;
        let tolerance = PyDict::new(py);
        tolerance.set_item("rtol", 2e-12)?;
        tolerance.set_item("atol", 1e-10)?;
        tolerance.set_item("equal_nan", true)?;
        let close = |a: &Bound<'_, PyAny>, b: &Bound<'_, PyAny>| -> PyResult<()> {
            if a.is_none() || b.is_none() { assert_eq!(a.is_none(), b.is_none()); return Ok(()); }
            np.getattr("testing")?.call_method("assert_allclose", (a, b), Some(&tolerance))?;
            Ok(())
        };
        let mut comparisons = 0;
        for case in 0..64 {
            let mut height: Vec<_> = (0..=18).map(|i| 250. + i as f64 * 500. + (i as f64 * 0.9).sin() * 30.).collect();
            let mut u: Vec<_> = (0..=18).map(|i| 3. + i as f64 * 2.5).collect();
            let mut v: Vec<_> = (0..=18).map(|i| (i as f64 * 0.4).sin() * 12.).collect();
            match case % 8 {
                1 => { height[5] = height[4]; u[5] = 150.; },
                2 => { height.swap(4, 8); u.swap(4, 8); v.swap(4, 8); },
                3 => { u[0] = f64::NAN; v[18] = f64::NAN; v[5] = f64::NAN; },
                4 => { height[6] = f64::NAN; u[8] = f64::INFINITY; },
                5 => { u.fill(10.); v.fill(-5.); },
                6 => { height[0] = f64::NAN; },
                _ => {},
            }
            if case % 16 == 12 {
                for i in 0..height.len() {
                    height[i] = i as f64 * 1e-300;
                    u[i] = if i % 2 == 0 { 1e308 } else { -1e308 };
                    v[i] = if i % 3 == 0 { -1e308 } else { 1e308 };
                }
            }
            let fields = PyDict::new(py);
            fields.set_item("hght", height.into_pyarray(py))?;
            fields.set_item("u", u.into_pyarray(py))?;
            fields.set_item("v", v.into_pyarray(py))?;
            let surface: i32 = match case % 16 {
                13 => -1,
                14 => 200,
                _ => if case % 8 == 7 { 3 } else { 0 },
            };
            fields.set_item("sfc", surface)?;
            if case % 16 != 9 { fields.set_item("srwind", (10., -8., -10., 8.))?; }
            let theta: Vec<_> = (0..=18).map(|i| if case % 2 == 0 { 300. + i as f64 } else { 30. + i as f64 }).collect();
            fields.set_item("theta", theta.clone().into_pyarray(py))?;
            fields.set_item("thetae", theta.iter().map(|t| t + 12.).collect::<Vec<_>>().into_pyarray(py))?;
            if case % 16 == 10 {
                let mask: Vec<_> = (0..=18).map(|i| i == 4 || i == 12).collect();
                let kwargs = PyDict::new(py);
                kwargs.set_item("mask", mask.into_pyarray(py))?;
                fields.set_item("u", np.getattr("ma")?.call_method("masked_array", (fields.get_item("u")?.unwrap(),), Some(&kwargs))?)?;
            }
            if case % 16 == 11 {
                // Non-contiguous columns must retain logical sample order.
                let slice = pyo3::types::PySlice::new(py, 0, 19, 2);
                for key in ["hght", "u", "v", "theta", "thetae"] {
                    fields.set_item(key, fields.get_item(key)?.unwrap().get_item(&slice)?)?;
                }
            }
            let profile = namespace.call((), Some(&fields))?;
            let options = PyDict::new(py);
            options.set_item("max_height_m", if case % 3 == 0 { 4000. } else { 9000. })?;
            options.set_item("step_m", [100., 250., 333.3, 500.][case % 4])?;
            if case % 16 == 12 {
                options.set_item("max_height_m", 18e-300)?;
                options.set_item("step_m", 1e-300)?;
            }
            for left in [false, true] {
                options.set_item("use_left", left)?;
                for (reference, current, function, stream) in [
                    (&wind_reference, &current_wind, "streamwiseness_profile", true),
                    (&height_reference, &current_height, "storm_relative_wind_profile", false),
                ] {
                    let a = reference.getattr(function)?.call((&profile,), Some(&options))?;
                    let b = current.getattr(function)?.call((&profile,), Some(&options))?;
                    if a.is_none() || b.is_none() { assert_eq!(a.is_none(), b.is_none(), "{function} case {case}"); }
                    else {
                        close(&a.getattr("height_km")?, &b.getattr("height_km")?)?;
                        if stream {
                            for field in ["percent", "signed_percent"] { close(&a.getattr(field)?, &b.getattr(field)?)?; }
                        } else { close(&a.getattr("values")?.get_item("srw")?, &b.getattr("values")?.get_item("srw")?)?; }
                    }
                    comparisons += 1;
                }
            }
            options.del_item("use_left")?;
            let a = height_reference.getattr("theta_profile")?.call((&profile,), Some(&options))?;
            let b = current_height.getattr("theta_profile")?.call((&profile,), Some(&options))?;
            if a.is_none() || b.is_none() { assert_eq!(a.is_none(), b.is_none()); }
            else {
                close(&a.getattr("height_km")?, &b.getattr("height_km")?)?;
                for field in ["theta", "thetae"] { close(&a.getattr("values")?.get_item(field)?, &b.getattr("values")?.get_item(field)?)?; }
            }
            comparisons += 1;
        }
        eprintln!("{comparisons} original/native chart comparisons passed across 64 profiles, both storm motions, masks, gaps, duplicates and strided inputs");
        Ok(())
    }).unwrap();
}

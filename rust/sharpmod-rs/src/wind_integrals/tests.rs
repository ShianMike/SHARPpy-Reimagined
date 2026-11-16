use super::*;
use pyo3::{ffi::c_str, prelude::*, types::PyDict};

#[test]
fn means_reject_unusable_bounds_and_missing_component_pairs() {
    let winds = PressureWinds::new(&[2.0, 3.0], &[10.0, 20.0], &[0.0, 30.0]).unwrap();
    for (bottom, top, step) in [
        (100.0, 900.0, -1.0),
        (900.0, 100.0, 0.0),
        (f64::NAN, 100.0, 1.0),
        (900.0, 100.0, f64::INFINITY),
    ] {
        assert!(winds
            .mean([bottom, top], step, [0.0; 2], true)
            .unwrap()
            .iter()
            .all(|x| x.is_nan()));
    }
    assert!(winds.mean([1e9, 100.0], -1.0, [0.0; 2], true).is_err());
    let empty = PressureWinds::new(&[2.0, 3.0], &[10.0, 20.0], &[f64::NAN, 30.0]).unwrap();
    assert!(empty
        .mean([900.0, 100.0], -1.0, [0.0; 2], false)
        .unwrap()
        .iter()
        .all(|x| x.is_nan()));
    assert!(PressureWinds::new(&[2.0], &[], &[1.0]).is_err());
}

#[test]
fn helicity_retains_signed_cross_products_and_knots_conversion() {
    let pressure = [1000.0_f64, 800.0, 600.0, 400.0];
    let logp = pressure.map(f64::log10);
    let u = [0.0, 20.0, 40.0, 60.0];
    let v = [0.0, 5.0, 30.0, 40.0];
    let winds = PressureWinds::new(&logp, &u, &v).unwrap();
    let values = winds
        .helicity(&pressure, &u, &v, [1000.0, 400.0], [0.0; 2])
        .unwrap();
    let factor = 1.943_844_492_440_604_6_f64.powi(2);
    for (actual, expected) in values.into_iter().zip([-200.0, 200.0, -400.0]) {
        assert!((actual - expected / factor).abs() < 1e-10);
    }
}

#[test]
fn wind_integral_bindings_match_existing_python_sampling_and_masks() {
    Python::initialize();
    Python::attach(|py| {
        let module = PyModule::new(py, "wind_integral_test").unwrap();
        crate::wind_integral_bindings::register(&module).unwrap();
        let scope = PyDict::new(py);
        scope.set_item("native", module).unwrap();
        py.run(c_str!(r#"
import numpy as np
import sharpmod_rs
from types import SimpleNamespace
from unittest.mock import patch
from sharpmod.sharptab import winds

def equal(actual, expected, label):
    assert isinstance(actual, tuple), label
    assert len(actual) == len(expected), label
    for a, b in zip(actual, expected):
        if np.ma.is_masked(b):
            assert a is np.ma.masked, (label, actual, expected)
        else:
            np.testing.assert_allclose(a, b, rtol=2e-11, atol=1e-9, err_msg=str(label))

rng = np.random.default_rng(1642)
with patch.object(sharpmod_rs, 'pressure_layer_mean_wind', None, create=True), patch.object(sharpmod_rs, 'profile_layer_helicity', None, create=True):
    for trial in range(60):
        count = [8, 32, 128][trial % 3]
        height = np.linspace(400., 12400., count)
        pressure = 1004. * np.exp(-(height - 400.) / 8000.)
        u = rng.normal(20., 30., count)
        v = rng.normal(5., 20., count)
        columns = []
        for data in (pressure, height, u, v):
            backing = np.empty(count * 2)
            backing[::2] = data
            mask = rng.random(count) < (0.12 if trial % 2 else 0.)
            mask[0] = mask[-1] = False
            values = np.ma.array(backing[::2], mask=mask, copy=False)
            values.setflags(write=False)
            columns.append(values)
        p, h, u, v = columns
        profile = SimpleNamespace(pres=p, hght=h, u=u, v=v, logp=np.ma.log10(p), wdir=np.zeros(count), sfc=0, _sharpmod_profile_kinematics=None)
        saved = [(a.data.copy(), np.ma.getmaskarray(a).copy()) for a in columns]
        for bottom, top, step in [(1004., float(p[-1]), -1.), (925.4, 500.3, -2.5), (800., 650., .5), (700., 700., -1.), (500., 900., -1.), (900., 500., 0.), (1100., 200., -1.)]:
            for weighted in (True, False):
                operation = winds.mean_wind if weighted else winds.mean_wind_npw
                args = (profile, bottom, top, step, 8.5, -12.25)
                equal(native.pressure_layer_mean_wind(*args, weighted), operation(*args), ('mean', trial, bottom, top, step, weighted))
        for lower, upper in [(0., 500.), (125., 2700.), (1000., 6000.), (6000., 1000.), (-100., 1500.), (0., 15000.)]:
            args = (profile, lower, upper, 8.5, -12.25)
            equal(native.profile_layer_helicity(*args), winds.helicity(*args), ('helicity', trial, lower, upper))
        for a, (data, mask) in zip(columns, saved):
            np.testing.assert_array_equal(a.data, data)
            np.testing.assert_array_equal(np.ma.getmaskarray(a), mask)
            assert not a.flags.writeable

    p = np.array([1000., 850., 700., 700., 500., 300.])
    profile = SimpleNamespace(pres=p, logp=np.log10(p), hght=np.array([0., 1000., 2000., 2100., 5000., 9000.]),
        u=np.array([1., 3., 5., 7., 9., 11.]), v=np.array([2., 4., 6., 8., 10., 12.]), wdir=np.zeros(6), sfc=0, _sharpmod_profile_kinematics=None)
    for weighted in (True, False):
        operation = winds.mean_wind if weighted else winds.mean_wind_npw
        equal(native.pressure_layer_mean_wind(profile, 950., 500., -.7, 0., 0., weighted), operation(profile, 950., 500., -.7), ('duplicate pressure', weighted))
    for lower, upper in [(150., 4500.), (5000., 2000.)]:
        equal(native.profile_layer_helicity(profile, lower, upper, 0., 0.), winds.helicity(profile, lower, upper), 'duplicate helicity')
    for name in ('u', 'v'):
        for value in (None, 'absent'):
            source = SimpleNamespace(**vars(profile))
            if value is None:
                setattr(source, name, None)
            else:
                delattr(source, name)
            for weighted in (True, False):
                operation = winds.mean_wind if weighted else winds.mean_wind_npw
                equal(native.pressure_layer_mean_wind(source, 950., 500., -1., 0., 0., weighted), operation(source, 950., 500.), 'absent component')
    for storm in [(np.nan, 0.), (np.inf, 0.)]:
        equal(native.pressure_layer_mean_wind(profile, 950., 500., -1., *storm, True), winds.mean_wind(profile, 950., 500., -1., *storm), 'nonfinite storm')
"#), Some(&scope), Some(&scope)).unwrap();
    });
}

#[test]
#[ignore = "manual microbenchmark of the installed release extension"]
fn measure_public_wind_adapters() {
    use numpy::IntoPyArray;
    use std::time::Instant;
    Python::initialize();
    Python::attach(|py| {
        let native = PyModule::import(py, "sharpmod_rs").unwrap();
        let public = PyModule::import(py, "sharpmod.sharptab.winds").unwrap();
        let backend = public
            .getattr("_backends")
            .unwrap()
            .call_method0("get_backend")
            .unwrap();
        assert_eq!(
            backend
                .getattr("name")
                .unwrap()
                .extract::<String>()
                .unwrap(),
            "rust"
        );
        let fields = PyDict::new(py);
        let height: Vec<_> = (0..128).map(|i| i as f64 * 12_000.0 / 127.0).collect();
        let pressure: Vec<_> = height
            .iter()
            .map(|h| 1004.0 * (-h / 8000.0).exp())
            .collect();
        let logp: Vec<_> = pressure.iter().map(|p| p.log10()).collect();
        fields
            .set_item(
                "u",
                height
                    .iter()
                    .map(|h| 10.0 + h / 200.0)
                    .collect::<Vec<_>>()
                    .into_pyarray(py),
            )
            .unwrap();
        fields
            .set_item(
                "v",
                height
                    .iter()
                    .map(|h| 8.0 + (h / 2000.0).sin() * 20.0)
                    .collect::<Vec<_>>()
                    .into_pyarray(py),
            )
            .unwrap();
        fields.set_item("pres", pressure.into_pyarray(py)).unwrap();
        fields.set_item("logp", logp.into_pyarray(py)).unwrap();
        fields.set_item("hght", height.into_pyarray(py)).unwrap();
        fields
            .set_item("wdir", vec![220.0; 128].into_pyarray(py))
            .unwrap();
        fields.set_item("sfc", 0).unwrap();
        fields
            .set_item("_sharpmod_profile_kinematics", py.None())
            .unwrap();
        let profile = PyModule::import(py, "types")
            .unwrap()
            .getattr("SimpleNamespace")
            .unwrap()
            .call((), Some(&fields))
            .unwrap();
        let mean_args = (profile.clone(), 925.4, 500.3, -1.0, 10.0, -5.0)
            .into_pyobject(py)
            .unwrap();
        let helicity_args = (profile.clone(), 125.0, 2700.0, 10.0, -5.0)
            .into_pyobject(py)
            .unwrap();
        let mut reports = Vec::new();
        for (label, name, operation, arguments) in [
            (
                "weighted_mean",
                "mean_wind",
                "pressure_layer_mean_wind",
                mean_args.clone(),
            ),
            (
                "unweighted_mean",
                "mean_wind_npw",
                "pressure_layer_mean_wind",
                mean_args,
            ),
            (
                "custom_helicity",
                "helicity",
                "profile_layer_helicity",
                helicity_args,
            ),
        ] {
            let migrated = native.getattr(operation).unwrap();
            let function = public.getattr(name).unwrap();
            native.setattr(operation, py.None()).unwrap();
            let expected = function.call(arguments.clone(), None).unwrap();
            native.setattr(operation, migrated.clone()).unwrap();
            let actual = function.call(arguments.clone(), None).unwrap();
            PyModule::import(py, "numpy.testing")
                .unwrap()
                .call_method1("assert_allclose", (actual, expected, 2e-11, 1e-9))
                .unwrap();
            let mut samples = [Vec::new(), Vec::new()];
            for repeat in 0..7 {
                for mode in if repeat % 2 == 0 { [0, 1] } else { [1, 0] } {
                    native
                        .setattr(
                            operation,
                            if mode == 0 {
                                py.None().into_bound(py)
                            } else {
                                migrated.clone()
                            },
                        )
                        .unwrap();
                    for _ in 0..20 {
                        function.call(arguments.clone(), None).unwrap();
                    }
                    let start = Instant::now();
                    for _ in 0..1000 {
                        function.call(arguments.clone(), None).unwrap();
                    }
                    samples[mode].push(start.elapsed().as_secs_f64() * 1e6 / 1000.0);
                }
            }
            native.setattr(operation, migrated).unwrap();
            reports.push(serde_json::json!({"operation": label, "reference_us": samples[0], "native_us": samples[1]}));
        }
        let output = serde_json::json!({"profile_levels": 128, "iterations_per_repeat": 1000,
            "native_extension": native.getattr("__file__").unwrap().extract::<String>().unwrap(),
            "python": PyModule::import(py, "sys").unwrap().getattr("version").unwrap().extract::<String>().unwrap(),
            "samples": reports});
        let path = std::env::var("SHARPMOD_WIND_BENCHMARK_OUTPUT").expect("benchmark output path");
        std::fs::write(path, serde_json::to_vec_pretty(&output).unwrap()).unwrap();
    });
}

use super::*;
use pyo3::{ffi::c_str, prelude::*, types::PyDict};

fn ground() -> [f64; 6] {
    [900.0, 1000.0, 10.0, 5.0, 3.0, 4.0]
}

#[test]
fn surface_ranges_and_saturation_tolerance_match_provider_contract() {
    for (field, low, high) in [(0, 100., 1100.), (1, -1000., 10_000.), (2, -120., 70.)] {
        for value in [low, high] {
            let mut values = ground();
            values[field] = value;
            values[3] = -140.;
            assert!(GroundLevel::verified(values, Some(-9999.)).is_some());
        }
        for value in [low - 0.01, high + 0.01, f64::NAN, f64::INFINITY, -9999.] {
            let mut values = ground();
            values[field] = value;
            assert!(GroundLevel::verified(values, Some(-9999.)).is_none());
        }
    }
    for excess in [0., 1e-9, 0.04, 0.5] {
        let mut values = ground();
        values[3] = values[2] + excess;
        let row = GroundLevel::verified(values, None).unwrap().row(-9999.);
        assert_eq!(row[2], row[3]);
    }
    for dewpoint in [-150.01, 10.5001, f64::NEG_INFINITY] {
        let mut values = ground();
        values[3] = dewpoint;
        assert!(GroundLevel::verified(values, None).is_none());
    }
}

#[test]
fn wind_bounds_units_and_missing_omega_are_preserved() {
    let row = GroundLevel::verified(ground(), Some(-9999.))
        .unwrap()
        .row(-9999.);
    assert!((row[4] - 216.86989764584402).abs() < 1e-12);
    assert_eq!(row[5], 5. * MPS_TO_KNOTS);
    assert_eq!(row[6], -9999.);
    assert_eq!(&row[7..], &[3., 4.]);
    for field in [4, 5] {
        for value in [-200., 200., 0.] {
            let mut values = ground();
            values[field] = value;
            assert!(GroundLevel::verified(values, Some(-9999.)).is_some());
        }
        for value in [-200.01, 200.01, f64::NAN, f64::INFINITY, -9999.] {
            let mut values = ground();
            values[field] = value;
            assert!(GroundLevel::verified(values, Some(-9999.)).is_none());
        }
    }
    assert!(GroundLevel::verified(ground(), Some(3.)).is_none());
}

#[test]
fn merge_keeps_reported_order_duplicates_and_alignment() {
    let pressure = vec![
        1000.,
        850.,
        900.,
        f64::NAN,
        700.,
        -9999.,
        0.,
        -5.,
        f64::INFINITY,
        850.,
    ];
    let columns = std::array::from_fn(|field| {
        if field == 0 {
            pressure.clone()
        } else {
            (0..pressure.len())
                .map(|i| 100. * field as f64 + i as f64)
                .collect()
        }
    });
    let merged = merge(
        columns,
        GroundLevel::verified(ground(), None).unwrap(),
        -9999.,
    );
    assert_eq!(merged.removed, 7);
    assert_eq!(merged.columns[0], [900., 850., 700., 850.]);
    assert_eq!(merged.columns[1], [1000., 101., 104., 109.]);
    assert_eq!(merged.columns[6], [-9999., 601., 604., 609.]);
    assert_eq!(merged.columns[8], [4., 801., 804., 809.]);
}

#[test]
fn empty_or_entirely_below_ground_profile_retains_only_surface() {
    for pressure in [vec![], vec![1000., 900., -9999., f64::NAN]] {
        let columns = std::array::from_fn(|_| pressure.clone());
        let merged = merge(
            columns,
            GroundLevel::verified(ground(), None).unwrap(),
            -9999.,
        );
        assert_eq!(merged.removed, pressure.len());
        for values in merged.columns {
            assert_eq!(values.len(), 1);
        }
    }
}

#[test]
fn provider_binding_matches_python_reference_without_mutating_inputs() {
    Python::initialize();
    Python::attach(|py| {
        let native = PyModule::new(py, "surface_test").unwrap();
        register(&native).unwrap();
        let locals = PyDict::new(py);
        locals.set_item("native", native).unwrap();
        py.run(c_str!(r#"
import numpy as np
from unittest.mock import patch
from types import SimpleNamespace
from sharpmod.models import model_surface as api

surface = dict(pres=900., hght=1000., tmpc=10., dwpc=10.04, u=3., v=4.)
base = np.arange(36., dtype=float).reshape(6, 6)
forms = [base, base.T, base[:, ::-1], base.astype('f4'), base.astype('>f8'), base.tolist(), 42., np.empty((0, 2)), np.ma.array(base, mask=base % 2 == 0)]
for form in forms:
    columns = {name: form for name in api.PROFILE_COLUMN_NAMES}
    columns['pres'] = np.linspace(1000., 500., np.asarray(form).size).reshape(np.asarray(form).shape)
    for value in columns.values():
        if isinstance(value, np.ndarray):
            value.setflags(write=False)
    before = {name: np.asarray(value).copy() for name, value in columns.items()}
    before_masks = {name: np.ma.getmaskarray(value).copy() for name, value in columns.items()}
    with patch('sharpmod.backends.get_backend', return_value=SimpleNamespace(name='python')):
        reference = api.merge_surface_level(columns, surface)
    actual = native.merge_model_surface(columns, surface)
    assert actual.surface_pressure == reference.surface_pressure
    assert actual.removed_levels == reference.removed_levels
    for name in api.PROFILE_COLUMN_NAMES:
        np.testing.assert_allclose(actual.columns[name], reference.columns[name], rtol=1e-14, atol=1e-12, equal_nan=True)
        np.testing.assert_array_equal(np.asarray(columns[name]), before[name])
        np.testing.assert_array_equal(np.ma.getmaskarray(columns[name]), before_masks[name])
        assert actual.columns[name].dtype == np.dtype('float64')
        assert actual.columns[name].flags.c_contiguous

columns = {name: np.array([1000., 850., 700.]) for name in api.PROFILE_COLUMN_NAMES}
for missing in [-9999., 850., float('nan'), float('inf')]:
    for change in [{}, {'u': None}, {'v': []}, {'tmpc': float('nan')}, {'dwpc': 10.6}, {'pres': [900., 1000.]}, {'hght': []}, {'u': [3., 900.]}, {'tmpc': None, 'u': 10**1000}]:
        values = surface | change
        with patch('sharpmod.backends.get_backend', return_value=SimpleNamespace(name='python')):
            reference = api.merge_surface_level(columns, values, missing=missing)
        actual = native.merge_model_surface(columns, values, missing)
        assert (reference is None) == (actual is None), (missing, change)
        if actual is not None:
            assert actual.removed_levels == reference.removed_levels
            for name in api.PROFILE_COLUMN_NAMES:
                np.testing.assert_allclose(actual.columns[name], reference.columns[name], rtol=1e-14, atol=1e-12, equal_nan=True)
assert native.merge_model_surface(columns | {'v': [1.]}, surface) is None
assert native.merge_model_surface({name: value for name, value in columns.items() if name != 'u'}, surface) is None
"#), Some(&locals), Some(&locals)).unwrap();
    });
}

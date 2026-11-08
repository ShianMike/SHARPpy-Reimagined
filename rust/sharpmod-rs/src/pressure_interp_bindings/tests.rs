use super::*;
use numpy::ndarray::arr1;
use pyo3::types::PySlice;

#[test]
fn pressure_value_preserves_duplicate_order_and_endpoint_tolerance() {
    let pairs = [(1., 4.), (2., 8.), (2., 10.), (3., 16.)];
    for (target, expected) in [
        (1. - 0.000_009, 4.),
        (1.5, 6.),
        (2., 10.),
        (2.5, 13.),
        (3. + 0.000_029, 16.),
    ] {
        assert_eq!(pressure_value(&pairs, target), Some(expected));
    }
    assert!(pressure_value(&pairs, 0.99).unwrap().is_nan());
    assert!(pressure_value(&pairs, 3.01).unwrap().is_nan());
    assert!(pressure_value(&pairs, f64::NAN).is_none());
    assert_eq!(pressure_value(&[(1., 4.), (1.000_001, 5.)], 1.), Some(5.));
    assert_eq!(pressure_value(&[(2., 3.)], 2.000_01), Some(3.));
}

#[test]
fn ordered_pairs_apply_both_masks_without_sorting_or_filling() {
    let coordinates = arr1(&[3., f64::NAN, 1., 0.]);
    let values = arr1(&[7., 6., -9999., f64::INFINITY]);
    let coordinate_mask = arr1(&[false, true, false, false]);
    let value_mask = arr1(&[false, false, false, true]);
    let ordered = ordered_pairs(
        coordinates.slice(numpy::ndarray::s![..;-1]),
        values.slice(numpy::ndarray::s![..;-1]),
        Some(coordinate_mask.slice(numpy::ndarray::s![..;-1])),
        Some(value_mask.slice(numpy::ndarray::s![..;-1])),
    );
    assert_eq!(ordered, Some(vec![(1., -9999.), (3., 7.)]));
    assert!(ordered_pairs(
        coordinates.view(),
        values.view(),
        Some(coordinate_mask.view()),
        Some(value_mask.view()),
    )
    .is_none());
    assert!(ordered_pairs(
        coordinates.view(),
        values.view(),
        Some(arr1(&[false]).view()),
        None,
    )
    .is_none());
}

#[test]
#[ignore = "requires the pinned SHARPpy and NumPy environment"]
fn native_pressure_interpolation_matches_the_installed_reference() {
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let original =
            PyModule::import(py, "sharppy.sharptab.interp")?.getattr("generic_interp_pres")?;
        let native = RenderPressureInterpolation::new(py, original.clone())?;
        let masked_array = native.masked_array.bind(py);
        let assert_equal = PyModule::import(py, "numpy.testing")?.getattr("assert_array_equal")?;
        let reverse = PySlice::new(py, 4, -6, -1);
        let coordinates = masked_array
            .call1((vec![3.7, 2.6, 2.6, 2.2, 1.3], vec![false; 5]))?
            .get_item(&reverse)?;
        let values = masked_array
            .call1((
                vec![-2.1, 8.2, -14.7, -9999., 2.5],
                vec![false, false, false, true, false],
            ))?
            .get_item(&reverse)?;
        let probes = [1.2, 1.299_995, 1.56, 2.6, 3.1, 3.700_02, 4.];
        for target in probes {
            let target = native.float64.bind(py).call1((target,))?;
            assert!(native.interpolate(&target, &coordinates, &values).is_some());
            let actual = native.__call__(&target, &coordinates, &values)?;
            let reference = original.call1((&target, &coordinates, &values))?;
            assert!(actual.get_type().is(&reference.get_type()));
            if reference.is(native.masked.bind(py)) {
                assert!(actual.is(&reference));
            } else {
                assert_eq!(
                    actual.extract::<f64>()?.to_bits(),
                    reference.extract::<f64>()?.to_bits()
                );
            }
        }
        for (targets, fast) in [
            (PyArray1::from_slice(py, &probes).into_any(), true),
            (PyArray1::from_slice(py, &[1.4, 2.9]).into_any(), true),
            (PyArray1::from_slice(py, &[1.5, f64::NAN]).into_any(), false),
            (
                masked_array.call1((vec![1.5, 2.6], vec![true, false]))?,
                false,
            ),
        ] {
            assert_eq!(
                native
                    .interpolate(&targets, &coordinates, &values)
                    .is_some(),
                fast
            );
            let actual = native.__call__(&targets, &coordinates, &values)?;
            let reference = original.call1((&targets, &coordinates, &values))?;
            assert!(actual.get_type().is(&reference.get_type()));
            for attr in ["data", "mask"] {
                assert_equal.call1((actual.getattr(attr)?, reference.getattr(attr)?))?;
            }
        }
        Ok(())
    })
    .unwrap();
}

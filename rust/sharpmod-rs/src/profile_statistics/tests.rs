use super::{distribution, pressure_column, Distribution};
use numpy::PyReadonlyArray1;
use pyo3::prelude::*;

#[test]
fn empty_and_single_member_distributions_retain_their_denominator() {
    assert_eq!(
        distribution(Vec::new()),
        Distribution {
            count: 0,
            p10: None,
            median: None,
            p90: None,
            minimum: None,
            maximum: None,
        }
    );
    assert_eq!(
        distribution(vec![42.0]),
        Distribution {
            count: 1,
            p10: Some(42.0),
            median: Some(42.0),
            p90: Some(42.0),
            minimum: Some(42.0),
            maximum: Some(42.0),
        }
    );
}

#[test]
fn percentile_interpolation_matches_numpy_across_member_counts() {
    Python::initialize();
    Python::attach(|py| {
        let numpy = PyModule::import(py, "numpy").unwrap();
        let mut state = 7u64;
        for count in [2, 3, 4, 7, 10, 21, 31, 50, 100, 1001] {
            let values: Vec<_> = (0..count)
                .map(|_| {
                    state = state.wrapping_mul(6364136223846793005).wrapping_add(1);
                    (state >> 11) as f64 / (1u64 << 53) as f64 * 12_000.0 - 2000.0
                })
                .collect();
            let reference = numpy
                .call_method1("percentile", (values.clone(), [10.0, 50.0, 90.0]))
                .unwrap();
            let reference = reference.extract::<PyReadonlyArray1<'_, f64>>().unwrap();
            let actual = distribution(values);
            assert_eq!(actual.count, count);
            for (actual, &expected) in [actual.p10, actual.median, actual.p90]
                .iter()
                .zip(reference.as_array().iter())
            {
                assert_eq!(actual.unwrap(), expected);
            }
        }
    });
}

#[test]
fn repeated_values_and_extremes_keep_linear_percentile_behavior() {
    let result = distribution(vec![-2000.0, 0.0, 0.0, 0.0, 10_000.0]);
    assert_eq!(result.median, Some(0.0));
    assert_eq!(result.minimum, Some(-2000.0));
    assert_eq!(result.maximum, Some(10_000.0));
    let extremes = distribution(vec![-f64::MAX, f64::MAX]);
    assert_eq!(extremes.median, Some(f64::NEG_INFINITY));
}

#[test]
fn pressure_columns_keep_exact_levels_and_the_first_duplicate() {
    let result = pressure_column(
        vec![(500.0, -20.0), (750.0, 0.0), (750.0, 99.0), (1000.0, 20.0)],
        &[1000.0, 750.0, 500.0, (500.0_f64 * 750.0).sqrt()],
    );
    assert_eq!(&result[..3], &[Some(20.0), Some(0.0), Some(-20.0)]);
    assert!((result[3].unwrap() + 10.0).abs() < 1e-12);
}

#[test]
fn pressure_columns_never_extrapolate_or_treat_one_sample_as_a_column() {
    assert_eq!(
        pressure_column(vec![(750.0, 10.0)], &[750.0, 800.0]),
        vec![None, None]
    );
    assert_eq!(
        pressure_column(vec![(750.0, 10.0), (750.0, 11.0)], &[750.0]),
        vec![None]
    );
    assert_eq!(pressure_column(Vec::new(), &[750.0]), vec![None]);
    assert_eq!(
        pressure_column(
            vec![(500.0, -20.0), (1000.0, 20.0)],
            &[1000.0001, 499.9999, 0.0, f64::INFINITY]
        ),
        vec![None; 4]
    );
}

#[test]
fn logarithmic_pressure_interpolation_matches_numpy_reference() {
    Python::initialize();
    Python::attach(|py| {
        let numpy = PyModule::import(py, "numpy").unwrap();
        let pressure = [100.0, 225.0, 500.0, 725.0, 850.0, 1000.0];
        let temperature = [-72.0, -60.0, -20.0, 4.0, 13.0, 28.0];
        let levels: Vec<_> = (100..=1000).step_by(13).map(f64::from).collect();
        let x = numpy.call_method1("log", (pressure,)).unwrap();
        let target = numpy.call_method1("log", (levels.clone(),)).unwrap();
        let reference = numpy
            .call_method1("interp", (target, x, temperature))
            .unwrap();
        let reference = reference.extract::<PyReadonlyArray1<'_, f64>>().unwrap();
        let actual = pressure_column(pressure.into_iter().zip(temperature).collect(), &levels);
        for (actual, &expected) in actual.iter().zip(reference.as_array().iter()) {
            assert!((actual.unwrap() - expected).abs() < 1e-10);
        }
    });
}

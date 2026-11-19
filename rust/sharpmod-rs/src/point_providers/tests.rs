use super::*;
#[test]
fn point_fields_preserve_nulls_suffixes_and_surface_height_bracketing() {
    let _guard = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let data = PyDict::new(py);
        data.set_item("temperature_model", vec![Some(3.), None, Some(f64::NAN)])?;
        assert_eq!(
            openmeteo::openmeteo_hourly_value(&data, "temperature", 0, "model")?,
            Some(3.)
        );
        assert_eq!(
            openmeteo::openmeteo_hourly_value(&data, "temperature", 1, "model")?,
            None
        );
        assert_eq!(
            openmeteo::openmeteo_hourly_value(&data, "temperature", 2, "model")?,
            None
        );
        assert_eq!(
            openmeteo::openmeteo_hourly_value(&data, "temperature", 3, "model")?,
            None
        );
        let height = openmeteo::openmeteo_surface_height(
            vec![(1000., 100.), (900., 1000.), (800., 2000.)],
            950.,
        )?;
        let expected =
            100. + 900. * (950_f64.ln() - 1000_f64.ln()) / (900_f64.ln() - 1000_f64.ln());
        assert!((height - expected).abs() < 1e-10);
        assert!(
            openmeteo::openmeteo_surface_height(vec![(1000., 100.), (1000., 200.)], 950.).is_err()
        );
        Ok(())
    })
    .unwrap();
}

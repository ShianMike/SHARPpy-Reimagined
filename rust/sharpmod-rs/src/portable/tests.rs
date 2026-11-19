use super::*;
#[test]
fn portable_boundary_checks_once_and_rejects_shape_and_scalar_objects() {
    let _guard = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let np = PyModule::import(py, "numpy")?;
        let data = PyDict::new(py);
        data.set_item(
            "pres",
            np.call_method1("array", (vec![1000., 900., 800.],))?,
        )?;
        assert_eq!(
            portable_profile_array(py, &data, "pres", Some(3))?.len()?,
            3
        );
        assert!(portable_profile_array(py, &data, "pres", Some(2)).is_err());
        assert!(portable_scalar(py, &data, "pres", None).is_err());
        data.set_item("loc", np.call_method1("array", ("Station",))?)?;
        assert_eq!(
            portable_scalar(py, &data, "loc", None)?.extract::<String>()?,
            "Station"
        );
        Ok(())
    })
    .unwrap();
}

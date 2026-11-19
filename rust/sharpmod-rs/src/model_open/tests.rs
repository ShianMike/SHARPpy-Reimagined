use super::*;
#[test]
fn failed_open_removes_owned_outputs_until_registration_commits() {
    let _guard = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let fake = PyModule::import(py, "unittest.mock")?
            .getattr("Mock")?
            .call0()?;
        {
            let _output = Output {
                cache: fake.clone().unbind(),
                id: "owned".into(),
                committed: false,
            };
        }
        fake.getattr("_remove_files")?
            .call_method1("assert_called_once_with", ("owned",))?;
        fake.call_method0("reset_mock")?;
        {
            let _output = Output {
                cache: fake.clone().unbind(),
                id: "registered".into(),
                committed: true,
            };
        }
        fake.getattr("_remove_files")?
            .call_method0("assert_not_called")?;
        Ok(())
    })
    .unwrap();
}

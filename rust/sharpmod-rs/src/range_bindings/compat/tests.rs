use super::*;
use pyo3::types::PyBytes;
use std::fs;

#[test]
fn nomads_models_download_reuse_and_route_failures_with_real_python_adapter() {
    let _guard = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let providers = PyModule::import(py, "sharpmod.models.model_sources")?;
        let endpoints = providers.getattr("NOMADS_ENDPOINTS")?.cast_into::<PyDict>()?;
        let unavailable = providers.getattr("SourceRoutingUnavailable")?;
        let mock = PyModule::import(py, "unittest.mock")?.getattr("MagicMock")?;
        let namespace = PyModule::import(py, "types")?.getattr("SimpleNamespace")?;
        let temporary = PyModule::import(py, "tempfile")?
            .getattr("TemporaryDirectory")?
            .call0()?;
        let directory: PathBuf = temporary.getattr("name")?.extract()?;
        let payload = b"GRIBfixture7777";

        for (key, endpoint) in &endpoints {
            let key = key.extract::<String>()?;
            let endpoint = endpoint.extract::<String>()?;
            let output = directory.join(format!("{key}.grib2"));
            let source = format!(
                "https://nomads.ncep.noaa.gov/pub/data/nccf/com/model/prod/{}.20261119/00/model.grib2",
                key.replace('-', "_"),
            );
            let config_options = PyDict::new(py);
            config_options.set_item("key", &key)?;
            let config = namespace.call((), Some(&config_options))?;
            let sources = PyDict::new(py);
            sources.set_item("nomads", source)?;
            let herbie = mock.call0()?;
            herbie.setattr("SOURCES", sources)?;
            let local_file = herbie.getattr("get_localFilePath")?;
            local_file.setattr("return_value", &output)?;

            let session = mock.call0()?;
            let get = session.getattr("get")?;
            let response = get
                .getattr("return_value")?
                .getattr("__enter__")?
                .getattr("return_value")?;
            response.setattr("status_code", 200)?;
            response.setattr("headers", PyDict::new(py))?;
            response
                .getattr("iter_content")?
                .setattr("return_value", (PyBytes::new(py, payload),))?;
            let options = PyDict::new(py);
            options.set_item("session", session)?;
            options.set_item("throttle", false)?;
            let fields = PyTuple::new(py, ["TMP", "HGT"])?;
            let download = || {
                download_nomads_native(
                    py, &herbie, &config, "fixture", fields.as_any(), 38.1545, -121.122, &options,
                )
            };

            let (path, bytes, url) = download()
                .unwrap_or_else(|failure| panic!("{key}: {failure}"))
                .extract::<(PathBuf, u64, String)>()?;
            assert_eq!(path, output);
            assert_eq!(bytes, payload.len() as u64);
            assert!(url.contains(&format!("/cgi-bin/{endpoint}?")));
            assert_eq!(fs::read(&path).unwrap(), payload);

            get.call_method0("reset_mock")?;
            let cached = download()?.extract::<(PathBuf, u64, String)>()?;
            assert_eq!(cached, (path.clone(), bytes, url));
            get.call_method0("assert_not_called")?;

            fs::write(&path, b"truncated").unwrap();
            response.setattr("status_code", 503)?;
            let failure = download().unwrap_err();
            assert!(failure.is_instance(py, &unavailable), "{key}: {failure}");
            assert_eq!(fs::read(path).unwrap(), b"truncated");
        }
        temporary.call_method0("cleanup")?;
        Ok(())
    })
    .unwrap();
}

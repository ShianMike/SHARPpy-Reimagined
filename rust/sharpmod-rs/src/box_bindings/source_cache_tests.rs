use super::*;

#[pyclass]
struct FixtureSource {
    dataset: Py<PyAny>,
}

#[pymethods]
impl FixtureSource {
    fn __call__(&self, py: Python<'_>, _directory: String) -> (Py<PyAny>, Py<PyAny>) {
        (self.dataset.clone_ref(py), py.None())
    }
}

#[test]
#[ignore = "requires SHARPMOD_BOX_SHARED_SOURCE_FIXTURE (ECMWF 9km) and the existing Python environment"]
fn existing_full_grid_point_source_is_reused_without_another_download() {
    let fixture = std::env::var("SHARPMOD_BOX_SHARED_SOURCE_FIXTURE").unwrap();
    let output = std::env::var("SHARPMOD_BOX_SHARED_SOURCE_OUTPUT").unwrap();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let mx = PyModule::import(py, "sharpmod.tools.model_extract")?;
        let dataset = mx.getattr("_LocalGribDataset")?.call1((fixture,))?;
        let loader = Py::new(
            py,
            FixtureSource {
                dataset: dataset.unbind(),
            },
        )?;
        let cache_module = PyModule::import(py, "sharpmod.models.model_hour_cache")?;
        let source_cache = cache_module.getattr("ModelHourCache")?.call0()?;
        let run = PyModule::import(py, "datetime")?
            .getattr("datetime")?
            .call_method1("fromisoformat", ("2026-11-07T00:00:00+00:00",))?;
        // This is the existing ordinary full-grid point-extraction key.
        let key = cache_module
            .getattr("ModelHourKey")?
            .call_method1("create", ("ecmwf-ifs-9km", &run, 3))?;
        let context = source_cache.call_method1("lease", (key, loader))?;
        context.call_method0("__enter__")?;
        context.call_method1("__exit__", (py.None(), py.None(), py.None()))?;
        let bounds = PyDict::new(py);
        for (key, value) in [
            ("west", -97.46),
            ("east", -97.42),
            ("south", 35.16),
            ("north", 35.20),
        ] {
            bounds.set_item(key, value)?;
        }
        let (_, metadata) = extract_box_sounding(
            py,
            "ecmwf-ifs-9km".into(),
            35.18,
            -97.44,
            bounds.as_any(),
            Some(run.unbind()),
            3,
            output.clone(),
            None,
            None,
            None,
            None,
            None,
            &source_cache,
        )?;
        assert!(
            metadata
                .get_item("source_cache_hit")?
                .unwrap()
                .is_truthy()?,
            "compatible point source must be leased, not downloaded again"
        );
        assert!(
            metadata
                .get_item("valid_columns")?
                .unwrap()
                .extract::<usize>()?
                > 0
        );
        let saved: serde_json::Value = serde_json::from_slice(
            &std::fs::read(Path::new(&output).with_extension("json")).unwrap(),
        )
        .unwrap();
        assert_eq!(saved["box"]["method"], crate::box_soundings::METHOD_VERSION);
        source_cache.call_method0("clear")?;
        Ok(())
    })
    .unwrap();
}

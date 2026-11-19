use super::*;
#[test]
fn zarr_geometry_rejects_bad_chunks_and_handles_partial_edges() {
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let meta = PyDict::new(py);
        meta.set_item("shape", [5, 7])?;
        meta.set_item("chunks", [3, 4])?;
        assert_eq!(geometry(&meta, 4, 6)?, ([3, 4], [2, 3], [1, 2]));
        assert!(geometry(&meta, 5, 0).is_err());
        meta.set_item("chunks", [0, 4])?;
        assert!(geometry(&meta, 0, 0).is_err());
        Ok(())
    })
    .unwrap();
}

#[test]
fn archive_failures_allow_grib_fallback_but_cancellation_stops() {
    let _guard = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        use pyo3::types::{PyBytes, PyCFunction};

        let api = PyModule::import(py, "sharpmod.providers.hrrr_zarr")?;
        let unavailable = api.getattr("ZarrBackendUnavailable")?;
        let cancelled = api.getattr("DownloadCancelled")?;
        let run = PyModule::import(py, "datetime")?
            .getattr("datetime")?
            .call1((2026, 6, 25, 6))?;
        let options = PyDict::new(py);

        let missing = PyModule::import(py, "sharpmod.providers.hrrr_field")?
            .getattr("HrrrFieldUnavailable")?;
        for (raised, expected, should_wrap) in [
            (&missing, &unavailable, true),
            (&cancelled, &cancelled, false),
            (&unavailable, &unavailable, false),
        ] {
            let failure = PyErr::from_value(raised.call1(("test archive failure",))?);
            options.set_item(
                "get_bytes",
                PyCFunction::new_closure(py, None, None, move |args, _| {
                    Err::<(), _>(failure.clone_ref(args.py()))
                })?,
            )?;
            let failure = fetch::fetch_hrrr_zarr_native(py, &run, 0, 36.68, -95.66, &options)
                .expect_err("missing archive or cancellation");
            assert!(failure.is_instance(py, expected), "{failure}");
            if should_wrap {
                assert!(failure.cause(py).is_some_and(|e| e.is_instance(py, raised)));
            } else {
                assert!(failure.cause(py).is_none());
            }
        }
        options.set_item(
            "get_bytes",
            PyCFunction::new_closure(py, None, None, |args, _| {
                PyBytes::new(args.py(), b"invalid metadata response").unbind()
            })?,
        )?;
        let failure = fetch::fetch_hrrr_zarr_native(py, &run, 0, 36.68, -95.66, &options)
            .expect_err("invalid archive metadata");
        assert!(failure.is_instance(py, &unavailable), "{failure}");
        Ok(())
    })
    .unwrap();
}

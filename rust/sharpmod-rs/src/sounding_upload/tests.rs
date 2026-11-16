use super::*;
use std::{
    fs,
    sync::atomic::{AtomicU64, Ordering},
};
static SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[test]
fn file_names_are_portable_and_never_used_as_cache_paths() {
    for value in [
        "station.NPZ",
        "/download/station.NPZ",
        r"C:\download\station.NPZ",
        r"\\server\share\station.NPZ",
    ] {
        assert_eq!(
            file_name(value).unwrap(),
            ("station.NPZ".into(), ".npz".into())
        );
    }
    assert_eq!(file_name("雷.npz").unwrap().0, "雷.npz");
    for value in ["", "   ", "..", ".", "folder/", "bad\0.npz"] {
        assert!(file_name(value).is_err());
    }
    assert!(file_name(&format!("{}.npz", "雷".repeat(252))).is_err());
    for value in [
        "profile",
        ".npz",
        "profile.verylongsuffix",
        "profile.bad$ext",
    ] {
        assert_eq!(file_name(value).unwrap().1, ".txt");
    }
}

#[test]
fn uploads_keep_real_formats_metadata_and_source_bytes() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    let root = std::env::temp_dir().join(format!(
        "sharpmod-upload-{}-{}",
        std::process::id(),
        SEQUENCE.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir(&root).unwrap();
    Python::attach(|py| -> PyResult<()> {
        let cache_api = PyModule::import(py, "sharpmod.bridge.sounding_cache")?;
        cache_api.call_method1("reset_cache", (&root,))?;
        for name in [
            "hrrr_point_36.68N_95.66W_f018.npz",
            "hrrr_kbvo_20260625_06z.buf",
            "14061619.OAX",
        ] {
            let bytes = fs::read(
                PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                    .join("../../examples/soundings")
                    .join(name),
            )
            .unwrap();
            let params = PyDict::new(py);
            params.set_item("file_name", format!(r"C:\download\{name}"))?;
            params.set_item("content_base64", sharpmod_weather::base64_encode(&bytes))?;
            let summary = open_sounding_file(py, &params, MAX_UPLOAD_BYTES)?;
            assert_eq!(summary.get_item("kind")?.extract::<String>()?, "file");
            assert_eq!(summary.get_item("file_name")?.extract::<String>()?, name);
            assert!(summary.get_item("level_count")?.extract::<usize>()? > 10);
            if name.ends_with(".buf") {
                assert_eq!(summary.get_item("time_count")?.extract::<usize>()?, 49);
            }
            let id: String = summary.get_item("sounding_id")?.extract()?;
            let entry = cache_api
                .call_method0("cache")?
                .call_method1("get", (id,))?;
            let path: PathBuf = entry.getattr("path")?.extract()?;
            assert!(path.starts_with(&root));
            assert_eq!(fs::read(path).unwrap(), bytes);
        }
        cache_api.call_method1("reset_cache", (py.None(),))?;
        Ok(())
    })
    .unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn failures_do_not_leave_upload_files_or_replace_existing_files() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    let root = std::env::temp_dir().join(format!(
        "sharpmod-upload-errors-{}-{}",
        std::process::id(),
        SEQUENCE.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir(&root).unwrap();
    Python::attach(|py| -> PyResult<()> {
        let cache_api = PyModule::import(py, "sharpmod.bridge.sounding_cache")?;
        cache_api.call_method1("reset_cache", (&root,))?;
        let before: Vec<_> = fs::read_dir(&root)
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .collect();
        for (content, limit, code) in [
            ("", MAX_UPLOAD_BYTES, "SOUNDING_INVALID"),
            ("@@@", MAX_UPLOAD_BYTES, "INVALID_ARGUMENT"),
            ("YWJjZA==", 3, "INVALID_ARGUMENT"),
            ("bm90IGEgc291bmRpbmcK", MAX_UPLOAD_BYTES, "SOUNDING_INVALID"),
        ] {
            let params = PyDict::new(py);
            params.set_item("file_name", "profile.txt")?;
            params.set_item("content_base64", content)?;
            let error = open_sounding_file(py, &params, limit).unwrap_err();
            assert_eq!(error.value(py).getattr("code")?.extract::<String>()?, code);
            let after: Vec<_> = fs::read_dir(&root)
                .unwrap()
                .map(|entry| entry.unwrap().path())
                .collect();
            assert_eq!(after, before);
        }
        let api = PyModule::import(py, "sharpmod.bridge.sounding_open")?;
        let keywords = PyDict::new(py);
        keywords.set_item(
            "side_effect",
            pyo3::exceptions::PyRuntimeError::new_err("registration failed").value(py),
        )?;
        let patch = PyModule::import(py, "unittest.mock")?
            .getattr("patch")?
            .getattr("object")?
            .call((&api, "register"), Some(&keywords))?;
        let params = PyDict::new(py);
        params.set_item("file_name", "profile.npz")?;
        let bytes = fs::read(
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("../../examples/soundings/hrrr_point_36.68N_95.66W_f018.npz"),
        )
        .unwrap();
        params.set_item("content_base64", sharpmod_weather::base64_encode(&bytes))?;
        patch.call_method0("__enter__")?;
        let error = open_sounding_file(py, &params, MAX_UPLOAD_BYTES).unwrap_err();
        patch.call_method1("__exit__", (py.None(), py.None(), py.None()))?;
        assert!(error.to_string().contains("registration failed"));
        assert_eq!(
            fs::read_dir(&root)
                .unwrap()
                .map(|entry| entry.unwrap().path())
                .collect::<Vec<_>>(),
            before
        );
        // A create-new collision must preserve bytes that this operation did not create.
        let path = root.join("staged.npz");
        {
            let mut pending = Pending::default();
            pending.write(path.clone(), b"source")?;
            assert!(pending.write(path.clone(), b"replacement").is_err());
            assert_eq!(fs::read(&path)?, b"source");
        }
        assert!(!path.exists());
        cache_api.call_method1("reset_cache", (py.None(),))?;
        Ok(())
    })
    .unwrap();
    fs::remove_dir_all(root).unwrap();
}

use super::*;
use pyo3::types::PyDict;
use std::fs;
use std::sync::atomic::{AtomicU64, Ordering};
static SEQUENCE: AtomicU64 = AtomicU64::new(0);

#[test]
fn point_and_multi_time_sources_restore_after_the_original_cache_is_removed() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    for name in [
        "hrrr_point_36.68N_95.66W_f018.npz",
        "hrrr_kbvo_20260625_06z.buf",
    ] {
        let root = std::env::temp_dir().join(format!(
            "sharpmod-recovery-bindings-{}-{}",
            std::process::id(),
            SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root).unwrap();
        Python::attach(|py| -> PyResult<()> {
            let source_path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .join("../../examples/soundings")
                .join(name);
            let cache_api = PyModule::import(py, "sharpmod.bridge.sounding_cache")?;
            cache_api.call_method1("reset_cache", (&root,))?;
            let params = PyDict::new(py);
            params.set_item("file_name", name)?;
            let bytes = fs::read(&source_path).unwrap();
            params.set_item("content_base64", sharpmod_weather::base64_encode(&bytes))?;
            let summary = PyModule::import(py, "sharpmod.bridge.sounding_open")?
                .call_method1("open_file", (&params,))?;
            let old_id: String = summary.get_item("sounding_id")?.extract()?;
            let request = PyDict::new(py);
            request.set_item("sounding_id", &old_id)?;
            let snapshot = sounding_snapshot(py, &request)?;
            let payload: String = snapshot.get_item("payload")?.unwrap().extract()?;
            cache_api.call_method1("reset_cache", (py.None(),))?;
            fs::remove_dir_all(&root).unwrap();
            fs::create_dir(&root).unwrap();
            cache_api.call_method1("reset_cache", (&root,))?;
            let request = PyDict::new(py);
            request.set_item("payload", &payload)?;
            let restored = restore_sounding_snapshot(py, &request)?;
            let id: String = restored.get_item("sounding_id")?.extract()?;
            assert_ne!(old_id, id);
            for field in [
                "title",
                "kind",
                "level_count",
                "time_count",
                "valid_time",
                "run_time",
            ] {
                assert!(
                    summary.get_item(field)?.eq(restored.get_item(field)?)?,
                    "{field}"
                );
            }
            let entry = cache_api
                .call_method0("cache")?
                .call_method1("get", (&id,))?;
            let path: PathBuf = entry.getattr("path")?.extract()?;
            assert_eq!(fs::read(path).unwrap(), bytes);
            cache_api.call_method1("reset_cache", (py.None(),))?;
            Ok(())
        })
        .unwrap();
        fs::remove_dir_all(root).unwrap();
    }
}

#[test]
fn temporary_files_are_removed_when_restore_does_not_commit() {
    let root = std::env::temp_dir().join(format!(
        "sharpmod-recovery-rollback-{}-{}",
        std::process::id(),
        SEQUENCE.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir(&root).unwrap();
    let path = root.join("source.npz");
    {
        let mut pending = Pending::default();
        pending.write(path.clone(), b"profile").unwrap();
        assert!(path.exists());
        assert!(pending.write(path.clone(), b"replacement").is_err());
    }
    assert!(!path.exists());
    fs::remove_dir_all(root).unwrap();
}

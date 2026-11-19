use super::*;
use pyo3::types::PyDict;
use std::sync::atomic::{AtomicU64, Ordering};

static NEXT: AtomicU64 = AtomicU64::new(0);

#[test]
fn retention_uses_the_public_and_wire_protocol_with_native_argument_errors() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let args = PyDict::new(py);
        args.set_item("soundingIds", Vec::<String>::new())?;
        let translated = PyModule::import(py, "sharpmod.bridge.public")?
            .call_method1("bridge_request", ("retain_soundings", args))?;
        let method = translated.get_item(0)?;
        assert_eq!(method.extract::<String>()?, "soundings.retain");
        let params = translated.get_item(1)?;
        let protocol = PyModule::import(py, "sharpmod.bridge.protocol")?;
        let request = protocol.call_method1("Request", (1, &method, &params))?;
        protocol.call_method1("check_params", (request,))?;
        let handler = PyModule::import(py, "sharpmod.bridge.handlers")?
            .getattr("HANDLERS")?
            .get_item(method)?;
        assert!(handler
            .call1((&params,))?
            .get_item("retained")?
            .is_truthy()?);
        params.set_item("sounding_ids", "invalid non-list")?;
        let error = handler.call1((params,)).unwrap_err();
        assert_eq!(
            error.value(py).getattr("code")?.extract::<String>()?,
            "INVALID_ARGUMENT"
        );
        Ok(())
    })
    .unwrap();
}

#[test]
fn retained_open_profiles_survive_repeated_background_opens_and_release_when_closed() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    let root = std::env::temp_dir().join(format!(
        "sounding-retain-{}-{}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    let renders = root.join("renders");
    fs::create_dir_all(&renders).unwrap();
    Python::attach(|py| -> PyResult<()> {
        let cache = SoundingEntries::new(root.clone(), renders.clone(), 2)?;
        let constructor =
            PyModule::import(py, "sharpmod.bridge.sounding_cache")?.getattr("Entry")?;
        let first = root.join("first.npz");
        fs::write(&first, b"edited source").unwrap();
        cache.add(py, &constructor.call1(("first", &first, PyDict::new(py)))?)?;
        cache.retain(vec!["first".into()])?;
        for index in 0..100 {
            let id = format!("background{index}");
            let path = root.join(format!("{id}.npz"));
            fs::write(&path, b"background source").unwrap();
            cache.add(py, &constructor.call1((&id, &path, PyDict::new(py)))?)?;
            assert!(
                first.is_file(),
                "open profile disappeared at background open {index}"
            );
            assert!(cache.get(py, "first").is_ok());
        }
        assert_eq!(cache.ids().len(), 2);
        assert_eq!(fs::read_dir(&root).unwrap().count(), 3);
        cache.retain(vec![])?;
        // Make the closed profile oldest, then apply cache pressure again.
        drop(cache.get(py, "background99")?);
        let path = root.join("final.npz");
        fs::write(&path, b"final source").unwrap();
        cache.add(py, &constructor.call1(("final", &path, PyDict::new(py)))?)?;
        assert!(!first.exists(), "closed source stayed pinned");
        Ok(())
    })
    .unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn registry_eviction_preserves_in_flight_sources_until_the_last_request_releases_them() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    let root = std::env::temp_dir().join(format!(
        "sounding-lease-{}-{}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir(&root).unwrap();
    let renders = root.join("renders");
    fs::create_dir(&renders).unwrap();
    Python::attach(|py| -> PyResult<()> {
        let cache = SoundingEntries::new(root.clone(), renders.clone(), 1)?;
        let constructor =
            PyModule::import(py, "sharpmod.bridge.sounding_cache")?.getattr("Entry")?;
        let first = root.join("first.npz");
        let source = root.join("first-grib");
        fs::write(&first, b"measured profile").unwrap();
        fs::create_dir(&source).unwrap();
        fs::write(source.join("model.grib2"), b"GRIB").unwrap();
        let entry = constructor.call1(("first", &first, PyDict::new(py)))?;
        cache.add(py, &entry)?;
        drop(entry);
        let held = cache.get(py, "first")?;
        let second = root.join("second.npz");
        fs::write(&second, b"second profile").unwrap();
        let entry = constructor.call1(("second", &second, PyDict::new(py)))?;
        cache.add(py, &entry)?;
        assert!(cache.get(py, "first").is_err());
        assert!(
            first.is_file() && source.is_dir(),
            "eviction destroyed a request's source"
        );
        assert_eq!(cache.ids(), ["second"]);
        cache.add(py, held.bind(py))?;
        assert!(
            first.is_file(),
            "re-adding an evicted entry replaced its file owner"
        );
        cache.add(py, &entry)?;
        assert!(
            first.is_file(),
            "the active request lost its source after re-eviction"
        );
        drop(held);
        assert!(
            !first.exists() && !source.exists(),
            "released sources were not removed"
        );
        assert!(second.is_file());
        cache.add(py, &entry)?;
        assert!(
            second.is_file(),
            "re-adding the same entry deleted its source"
        );
        Ok(())
    })
    .unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn cleanup_preserves_neighboring_ids_and_refuses_unsafe_cache_ids() {
    let root = std::env::temp_dir().join(format!(
        "sounding-cleanup-{}-{}",
        std::process::id(),
        NEXT.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir(&root).unwrap();
    let renders = root.join("renders");
    fs::create_dir(&renders).unwrap();
    fs::write(root.join("first.npz"), b"old").unwrap();
    fs::write(root.join("first2.npz"), b"neighbor").unwrap();
    fs::write(renders.join("first-mu-hd.png"), b"old image").unwrap();
    fs::write(renders.join("first2-mu-hd.png"), b"neighbor image").unwrap();
    cleanup(&root, &renders, "first").unwrap();
    assert!(!root.join("first.npz").exists());
    assert!(!renders.join("first-mu-hd.png").exists());
    assert!(root.join("first2.npz").is_file());
    assert!(renders.join("first2-mu-hd.png").is_file());
    for id in ["", "../", "first*", "first/other", "file\\child"] {
        assert!(valid_id(id).is_err());
    }
    fs::remove_dir_all(root).unwrap();
}

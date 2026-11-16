use super::*;
use pyo3::{
    exceptions::PyRuntimeError,
    types::{PyCFunction, PyList},
};
use std::sync::{
    atomic::{AtomicU64, AtomicUsize, Ordering},
    Arc, Barrier,
};

const PNG: &[u8] = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR\0\0\x11\xd4\0\0\x0c\x05";

struct Fixture<'py> {
    py: Python<'py>,
    root: PathBuf,
    entry: Bound<'py, PyAny>,
    params: Bound<'py, PyDict>,
    patch: Bound<'py, PyAny>,
    calls: Arc<AtomicUsize>,
    behavior: Arc<AtomicUsize>,
}

impl<'py> Fixture<'py> {
    fn new(py: Python<'py>) -> PyResult<Self> {
        static SEQUENCE: AtomicU64 = AtomicU64::new(0);
        let root = std::env::temp_dir().join(format!(
            "sharpmod-render-cache-{}-{}",
            std::process::id(),
            SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root)?;
        let source = root.join("source.npz");
        fs::write(&source, b"original sounding source")?;
        let api = PyModule::import(py, "sharpmod.bridge.sounding_cache")?;
        let cache = api.call_method1("reset_cache", (&root,))?;
        let summary = PyDict::new(py);
        summary.set_item("time_count", 2)?;
        let entry = api.call_method1("Entry", ("render-cache-test", source, summary))?;
        cache.call_method1("add", (&entry,))?;
        let calls = Arc::new(AtomicUsize::new(0));
        let behavior = Arc::new(AtomicUsize::new(0));
        let count = calls.clone();
        let state = behavior.clone();
        let renderer = PyCFunction::new_closure(
            py,
            Some(c"render_cached_test_image"),
            None,
            move |args: &Bound<'_, PyTuple>,
                  kwargs: Option<&Bound<'_, PyDict>>|
                  -> PyResult<Py<PyDict>> {
                let py = args.py();
                count.fetch_add(1, Ordering::Relaxed);
                let path: PathBuf = args.get_item(1)?.extract()?;
                match state.load(Ordering::Relaxed) {
                    1 => fs::write(&path, b"partial invalid image")?,
                    2 => fs::write(&path, [PNG, &vec![0; 1024]].concat())?,
                    3 => {
                        fs::write(&path, b"incomplete render")?;
                        return Err(PyRuntimeError::new_err("worker stopped"));
                    }
                    5 => {
                        py.detach(|| std::thread::sleep(std::time::Duration::from_millis(40)));
                        fs::write(&path, PNG)?;
                    }
                    _ => fs::write(&path, PNG)?,
                }
                let metadata = PyDict::new(py);
                if state.load(Ordering::Relaxed) != 4 {
                    if let Some(view) = kwargs.unwrap().get_item("view")? {
                        let edits = if view.contains("drag")? {
                            PyModule::import(py, "json")?.call_method1(
                                "loads",
                                (r#"[{"index": 0, "values": {"tmpc": 30}}]"#,),
                            )?
                        } else {
                            view.get_item("edits")?
                        };
                        metadata.set_item("edits", edits)?;
                        let selection = PyDict::new(py);
                        selection.set_item("chart", view.get_item("chart")?)?;
                        selection.set_item("panel", view.get_item("panel")?)?;
                        metadata.set_item("selection", selection)?;
                    }
                }
                Ok(metadata.unbind())
            },
        )?;
        let fields = PyDict::new(py);
        fields.set_item("render", renderer)?;
        let worker = PyModule::import(py, "types")?
            .getattr("SimpleNamespace")?
            .call((), Some(&fields))?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("return_value", worker)?;
        let patch = PyModule::import(py, "unittest.mock")?
            .getattr("patch")?
            .call(("sharpmod.bridge.render_client.client",), Some(&kwargs))?;
        patch.call_method0("start")?;
        let params = PyDict::new(py);
        params.set_item("sounding_id", "render-cache-test")?;
        Ok(Self {
            py,
            root,
            entry,
            params,
            patch,
            calls,
            behavior,
        })
    }

    fn view(&self, drag: bool) -> PyResult<()> {
        let view = PyDict::new(self.py);
        view.set_item("edits", PyList::empty(self.py))?;
        if drag {
            view.set_item(
                "drag",
                PyModule::import(self.py, "json")?
                    .call_method1("loads", (r#"{"target": "tmpc:0", "x": 0.5, "y": 0.5}"#,))?,
            )?;
        }
        self.params.set_item("view", view)
    }

    fn render(&self, interactive: bool, limit: usize) -> PyResult<Bound<'py, PyDict>> {
        if interactive {
            render_sounding_view(self.py, &self.params, limit, 4)
        } else {
            render_sounding_png(self.py, &self.params, limit)
        }
    }

    fn output(&self) -> PathBuf {
        fs::read_dir(self.root.join("renders"))
            .unwrap()
            .map(|item| item.unwrap().path())
            .find(|path| path.extension().is_some_and(|ext| ext == "png"))
            .unwrap()
    }

    fn assert_source_and_lock(&self) {
        assert_eq!(
            fs::read(self.root.join("source.npz")).unwrap(),
            b"original sounding source"
        );
        assert!(!self
            .entry
            .getattr("render_lock")
            .unwrap()
            .call_method0("locked")
            .unwrap()
            .is_truthy()
            .unwrap());
        assert!(!fs::read_dir(self.root.join("renders"))
            .unwrap()
            .any(|item| item.unwrap().path().is_dir()));
    }
}

impl Drop for Fixture<'_> {
    fn drop(&mut self) {
        self.patch.call_method0("stop").unwrap();
        PyModule::import(self.py, "sharpmod.bridge.sounding_cache")
            .unwrap()
            .call_method1("reset_cache", (self.py.None(),))
            .unwrap();
        fs::remove_dir_all(&self.root).unwrap();
    }
}

#[test]
fn failed_outputs_are_not_published_and_retries_release_the_lock() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        for interactive in [false, true] {
            for behavior in [1, 2, 3] {
                let fixture = Fixture::new(py)?;
                if interactive {
                    fixture.view(false)?;
                }
                fixture.behavior.store(behavior, Ordering::Relaxed);
                let error = fixture.render(interactive, 100).unwrap_err();
                if behavior != 3 {
                    assert_eq!(
                        error.value(py).getattr("code")?.extract::<String>()?,
                        "RENDER_FAILED"
                    );
                }
                assert_eq!(fs::read_dir(fixture.root.join("renders"))?.count(), 0);
                assert_eq!(fixture.entry.getattr("view_renders")?.len()?, 0);
                fixture.assert_source_and_lock();
                fixture.behavior.store(0, Ordering::Relaxed);
                let result = fixture.render(interactive, 100)?;
                assert_eq!(result.get_item("width")?.unwrap().extract::<u32>()?, 4564);
                assert_eq!(fixture.calls.load(Ordering::Relaxed), 2);
                fixture.assert_source_and_lock();
            }
        }
        Ok(())
    })
    .unwrap();
}

#[test]
fn public_bridge_facades_repair_cached_pngs_without_reopening_the_source() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        for (module, method, interactive) in [
            ("sharpmod.bridge.soundings", "render", false),
            ("sharpmod.bridge.sounding_view", "render_view", true),
        ] {
            let fixture = Fixture::new(py)?;
            if interactive {
                fixture.view(false)?;
            }
            let api = PyModule::import(py, module)?;
            let original = api.call_method1(method, (&fixture.params,))?;
            fs::write(fixture.output(), b"damaged cached PNG")?;
            let repaired = api.call_method1(method, (&fixture.params,))?;
            assert!(repaired.eq(original)?);
            assert_eq!(fixture.calls.load(Ordering::Relaxed), 2);
            fixture.assert_source_and_lock();
        }
        Ok(())
    })
    .unwrap();
}

#[test]
fn damaged_or_oversized_cached_images_are_replaced_without_reopening() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        for interactive in [false, true] {
            let fixture = Fixture::new(py)?;
            if interactive {
                fixture.view(true)?;
            }
            let original = fixture.render(interactive, 100)?;
            for bytes in [
                b"corrupt".to_vec(),
                [PNG, &vec![0; 1024]].concat(),
                Vec::new(),
            ] {
                fs::write(fixture.output(), bytes)?;
                let repaired = fixture.render(interactive, 100)?;
                assert!(repaired.eq(&original)?);
                assert_eq!(fs::read(fixture.output())?, PNG);
                fixture.assert_source_and_lock();
            }
            assert_eq!(fixture.calls.load(Ordering::Relaxed), 4);
            if interactive {
                let resolved = fixture.params.copy()?;
                let view = PyDict::new(py);
                view.set_item("edits", original.get_item("edits")?.unwrap())?;
                resolved.set_item("view", view)?;
                assert!(render_sounding_view(py, &resolved, 100, 4)?.eq(&original)?);
                assert_eq!(fixture.calls.load(Ordering::Relaxed), 4);
            }
        }
        Ok(())
    })
    .unwrap();
}

#[test]
fn missing_cached_files_and_failed_repairs_do_not_keep_stale_aliases() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let fixture = Fixture::new(py)?;
        fixture.view(true)?;
        fixture.render(true, 100)?;
        assert_eq!(fixture.entry.getattr("view_renders")?.len()?, 2);
        fs::remove_file(fixture.output())?;
        fixture.behavior.store(3, Ordering::Relaxed);
        assert!(fixture.render(true, 100).is_err());
        assert_eq!(fixture.entry.getattr("view_renders")?.len()?, 0);
        fixture.assert_source_and_lock();
        fixture.behavior.store(0, Ordering::Relaxed);
        fixture.render(true, 100)?;
        assert_eq!(fixture.calls.load(Ordering::Relaxed), 3);
        Ok(())
    })
    .unwrap();
}

#[test]
fn incomplete_worker_metadata_cannot_publish_a_view_image() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let fixture = Fixture::new(py)?;
        fixture.view(false)?;
        fixture.behavior.store(4, Ordering::Relaxed);
        assert!(fixture.render(true, 100).is_err());
        assert_eq!(fs::read_dir(fixture.root.join("renders"))?.count(), 0);
        fixture.assert_source_and_lock();
        fixture.behavior.store(0, Ordering::Relaxed);
        fixture.render(true, 100)?;
        Ok(())
    })
    .unwrap();
}

#[test]
fn static_cache_separates_quality_parcel_and_colour_style() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let fixture = Fixture::new(py)?;
        for (parcel, quality, style) in [
            ("MU", "hd", "standard"),
            ("MU", "uhd", "standard"),
            ("ML", "hd", "standard"),
            ("MU", "hd", "inverted"),
            ("MU", "hd", "protanopia"),
        ] {
            fixture.params.set_item("parcel", parcel)?;
            fixture.params.set_item("image_mode", quality)?;
            fixture.params.set_item("color_style", style)?;
            let result = fixture.render(false, 100)?;
            assert!(fixture.render(false, 100)?.eq(result)?);
        }
        assert_eq!(fixture.calls.load(Ordering::Relaxed), 5);
        assert_eq!(fs::read_dir(fixture.root.join("renders"))?.count(), 5);
        fixture.assert_source_and_lock();
        assert_eq!(
            PyModule::import(py, "sharpmod.bridge.soundings")?
                .getattr("MAX_IMAGE_BYTES")?
                .extract::<usize>()?,
            MAX_IMAGE_BYTES
        );
        Ok(())
    })
    .unwrap();
}

#[test]
fn concurrent_static_requests_share_one_completed_render() {
    let _backend = crate::lock_python_backend_test();
    Python::initialize();
    Python::attach(|py| -> PyResult<()> {
        let fixture = Fixture::new(py)?;
        fixture.behavior.store(5, Ordering::Relaxed);
        let first = fixture.params.clone().unbind();
        let second = fixture.params.clone().unbind();
        let barrier = Arc::new(Barrier::new(2));
        py.detach(move || {
            let handles = [first, second].map(|params| {
                let barrier = barrier.clone();
                std::thread::spawn(move || {
                    barrier.wait();
                    Python::attach(|py| {
                        let result = render_sounding_png(py, params.bind(py), 100).unwrap();
                        result
                            .get_item("png_base64")
                            .unwrap()
                            .unwrap()
                            .extract::<String>()
                            .unwrap()
                    })
                })
            });
            let [first, second] = handles.map(|handle| handle.join().unwrap());
            assert_eq!(first, second);
        });
        assert_eq!(fixture.calls.load(Ordering::Relaxed), 1);
        fixture.assert_source_and_lock();
        Ok(())
    })
    .unwrap();
}

#[test]
fn bounded_image_reads_and_staging_cleanup_preserve_existing_files() {
    let root = std::env::temp_dir().join(format!("sharpmod-render-files-{}", std::process::id()));
    fs::create_dir(&root).unwrap();
    let saved = root.join("keep.png");
    fs::write(&saved, PNG).unwrap();
    assert!(Image::read(&saved, PNG.len()).is_ok());
    assert!(matches!(
        Image::read(&saved, PNG.len() - 1),
        Err(ReadError::TooLarge)
    ));
    {
        let first = Staged::new(&root).unwrap();
        fs::write(&first.image, b"partial").unwrap();
        let second = Staged::new(&root).unwrap();
        assert_ne!(first.image, second.image);
    }
    assert_eq!(fs::read_dir(&root).unwrap().count(), 1);
    assert_eq!(fs::read(saved).unwrap(), PNG);
    fs::remove_dir_all(root).unwrap();
}

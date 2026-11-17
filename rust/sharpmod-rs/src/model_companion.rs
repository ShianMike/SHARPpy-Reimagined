//! Independent companion acquisition, joined cancellation and owned cache copies.
use crate::{
    model_field_cache::{Cache, WaitError},
    model_http_bindings::{cancelled, field_error},
};
use pyo3::{
    prelude::*,
    types::{PyDict, PyTuple},
};
use sharpmod_weather::model_http::Error;
use std::{
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc, Condvar, Mutex, OnceLock,
    },
    thread::JoinHandle,
    time::{Duration, Instant},
};

const MAX_BYTES: usize = 32 * 1024 * 1024;
struct Blob {
    bytes: Vec<u8>,
    url: String,
    fields: Vec<String>,
    created: Instant,
    // The key includes the Herbie class identity. Keep that identity alive
    // so a later class cannot reuse its address within the cache TTL.
    _herbie: Option<Py<PyAny>>,
}
fn cache() -> &'static Arc<Cache<String, Blob>> {
    static CACHE: OnceLock<Arc<Cache<String, Blob>>> = OnceLock::new();
    CACHE.get_or_init(|| Cache::new(64 * 1024 * 1024, 16))
}
pub(crate) fn clear_cache() {
    cache().clear();
}

#[pyclass]
struct CompanionCancel {
    stop: Arc<AtomicBool>,
    parent: Option<Py<PyAny>>,
}
#[pymethods]
impl CompanionCancel {
    fn __call__(&self) -> PyResult<bool> {
        if self.stop.load(Ordering::Acquire) {
            return Ok(true);
        }
        cancelled(self.parent.as_ref()).map_err(pyo3::exceptions::PyRuntimeError::new_err)
    }
}

static ACTIVE: (Mutex<usize>, Condvar) = (Mutex::new(0), Condvar::new());
struct Permit;
impl Permit {
    fn acquire(stop: &AtomicBool) -> Option<Self> {
        let mut count = ACTIVE.0.lock().unwrap();
        while *count >= 2 {
            if stop.load(Ordering::Acquire) {
                return None;
            }
            count = ACTIVE
                .1
                .wait_timeout(count, Duration::from_millis(50))
                .unwrap()
                .0;
        }
        if stop.load(Ordering::Acquire) {
            return None;
        }
        *count += 1;
        Some(Self)
    }
}
impl Drop for Permit {
    fn drop(&mut self) {
        *ACTIVE.0.lock().unwrap() -= 1;
        ACTIVE.1.notify_one();
    }
}

fn materialize(py: Python<'_>, directory: &Path, blob: &Blob) -> PyResult<Py<PyAny>> {
    static SEQUENCE: AtomicU64 = AtomicU64::new(0);
    std::fs::create_dir_all(directory)?;
    let path = directory.join(format!(
        "model-companion-{}-{}.grib2",
        std::process::id(),
        SEQUENCE.fetch_add(1, Ordering::Relaxed)
    ));
    let mut file = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&path)?;
    if let Err(error) = file.write_all(&blob.bytes) {
        drop(file);
        let _ = std::fs::remove_file(&path);
        return Err(error.into());
    }
    drop(file);
    let result = (path, 0u64, &blob.url, &blob.fields).into_pyobject(py)?;
    Ok(result.into_any().unbind())
}

#[pyclass]
struct ModelCompanionFetch {
    stop: Arc<AtomicBool>,
    worker: Option<JoinHandle<PyResult<Py<PyAny>>>>,
}

#[pymethods]
impl ModelCompanionFetch {
    #[new]
    #[allow(clippy::too_many_arguments)]
    fn new(
        py: Python<'_>,
        herbie: Py<PyAny>,
        config: Py<PyAny>,
        run: Py<PyAny>,
        hour: u32,
        member: Py<PyAny>,
        directory: Option<PathBuf>,
        parent: Option<Py<PyAny>>,
        on_bytes: Option<Py<PyAny>>,
        surface: bool,
        height: bool,
    ) -> PyResult<Self> {
        let stop = Arc::new(AtomicBool::new(false));
        if !surface && !height {
            return Ok(Self { stop, worker: None });
        }
        let model: String = config.bind(py).getattr("key")?.extract()?;
        let product: String = config
            .bind(py)
            .getattr("product")?
            .str()?
            .to_str()?
            .to_string();
        let time: String = run.bind(py).call_method0("isoformat")?.extract()?;
        let key = format!(
            "{model}/{product}/{time}/{}/{}/{height}/{:p}",
            if height { 0 } else { hour },
            member.bind(py).repr()?.to_str()?,
            herbie.as_ptr()
        );
        let callback = Py::new(
            py,
            CompanionCancel {
                stop: Arc::clone(&stop),
                parent,
            },
        )?;
        let thread_stop = Arc::clone(&stop);
        let worker = std::thread::spawn(move || {
            let _permit = Permit::acquire(&thread_stop)
                .ok_or_else(|| Python::attach(|py| field_error(py, Error::Cancelled)))?;
            let ticket = cache().reserve(directory.as_ref().map(|_| key));
            let invoke = |py: Python<'_>| -> PyResult<Py<PyAny>> {
                if callback.bind(py).call0()?.is_truthy()? {
                    return Err(field_error(py, Error::Cancelled));
                }
                let api = PyModule::import(py, "sharpmod.tools.model_extract")?;
                let kwargs = PyDict::new(py);
                kwargs.set_item("on_bytes", on_bytes.as_ref().map(|value| value.bind(py)))?;
                let method = if height {
                    "_surface_height_companion"
                } else {
                    "_surface_companion"
                };
                let args = if height {
                    PyTuple::new(
                        py,
                        [
                            herbie.clone_ref(py),
                            config.clone_ref(py),
                            run.clone_ref(py),
                            member.clone_ref(py),
                            directory.clone().into_pyobject(py)?.into_any().unbind(),
                            callback.clone_ref(py).into_any(),
                        ],
                    )?
                } else {
                    PyTuple::new(
                        py,
                        [
                            herbie.clone_ref(py),
                            config.clone_ref(py),
                            run.clone_ref(py),
                            hour.into_pyobject(py)?.into_any().unbind(),
                            member.clone_ref(py),
                            directory.clone().into_pyobject(py)?.into_any().unbind(),
                            callback.clone_ref(py).into_any(),
                        ],
                    )?
                };
                Ok(api.getattr(method)?.call(args, Some(&kwargs))?.unbind())
            };
            if !ticket.leader {
                let cached = ticket.wait(|| {
                    if thread_stop.load(Ordering::Acquire) {
                        return Ok(true);
                    }
                    Python::attach(|py| callback.bind(py).call0()?.extract::<bool>())
                        .map_err(|error| error.to_string())
                });
                match cached {
                    Ok(blob) if blob.created.elapsed() < Duration::from_secs(300) => {
                        return Python::attach(|py| {
                            materialize(py, directory.as_ref().unwrap(), &blob)
                        });
                    }
                    Ok(_) => cache().clear(),
                    Err(WaitError::Cancelled) => {
                        return Err(Python::attach(|py| field_error(py, Error::Cancelled)))
                    }
                    Err(_) => {}
                }
                return Python::attach(invoke);
            }
            let result = Python::attach(invoke);
            if let Ok(value) = &result {
                let blob = Python::attach(|py| -> PyResult<Blob> {
                    let value = value.bind(py);
                    let path: PathBuf = value.get_item(0)?.extract()?;
                    let mut bytes = Vec::new();
                    std::fs::File::open(path)?
                        .take(MAX_BYTES as u64 + 1)
                        .read_to_end(&mut bytes)?;
                    if bytes.len() > MAX_BYTES
                        || !bytes.starts_with(b"GRIB")
                        || !bytes.ends_with(b"7777")
                    {
                        return Err(pyo3::exceptions::PyValueError::new_err(
                            "companion is not eligible for bounded reuse",
                        ));
                    }
                    Ok(Blob {
                        bytes,
                        url: value.get_item(2)?.extract()?,
                        fields: value.get_item(3)?.extract()?,
                        created: Instant::now(),
                        _herbie: Some(herbie.clone_ref(py)),
                    })
                });
                match blob {
                    Ok(blob) => {
                        let size = blob.bytes.len();
                        ticket.complete(Ok(blob), size);
                    }
                    Err(_) => ticket.complete(Err("companion is not cacheable".into()), 0),
                }
            }
            result
        });
        Ok(Self {
            stop,
            worker: Some(worker),
        })
    }

    fn result(&mut self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        match self.worker.take() {
            None => Ok(py.None()),
            Some(worker) => py.detach(|| worker.join()).map_err(|_| {
                pyo3::exceptions::PyRuntimeError::new_err("model companion worker stopped")
            })?,
        }
    }

    fn close(&mut self, py: Python<'_>) {
        self.stop.store(true, Ordering::Release);
        if let Some(worker) = self.worker.take() {
            let _ = py.detach(|| worker.join());
        }
    }
}

impl Drop for ModelCompanionFetch {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(worker) = self.worker.take() {
            // Explicit close is the normal path. A dropped Python owner still
            // cancels and joins its child without holding the interpreter lock.
            Python::try_attach(|py| {
                let _ = py.detach(|| worker.join());
            });
        }
    }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<ModelCompanionFetch>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    static FETCHES: AtomicU64 = AtomicU64::new(0);

    #[pyfunction]
    fn cancel_immediately() -> bool {
        true
    }

    #[test]
    fn cancelled_companions_never_invoke_the_herbie_loader_and_release_their_permit() {
        let _lock = crate::lock_python_backend_test();
        Python::initialize();
        Python::attach(|py| -> PyResult<()> {
            let config = PyModule::import(py, "sharpmod.tools.model_extract")?
                .call_method1("get_config", ("ecmwf-ifs",))?
                .unbind();
            let run = PyModule::import(py, "datetime")?
                .getattr("datetime")?
                .call_method1("fromisoformat", ("2026-11-17T00:00:00",))?
                .unbind();
            let mut fetch = ModelCompanionFetch::new(
                py,
                py.None(),
                config,
                run,
                1,
                py.None(),
                None,
                Some(
                    wrap_pyfunction!(cancel_immediately, py)?
                        .into_any()
                        .unbind(),
                ),
                None,
                false,
                true,
            )?;
            let error = fetch.result(py).unwrap_err();
            let cancelled = PyModule::import(py, "sharpmod.models.model_transport")?
                .getattr("DownloadCancelled")?;
            assert!(error.is_instance(py, &cancelled));
            fetch.close(py);
            assert_eq!(*ACTIVE.0.lock().unwrap(), 0);
            Ok(())
        })
        .unwrap();
    }

    #[pyfunction(signature=(_herbie,_config,_run,_member,directory,cancelled,on_bytes=None))]
    #[allow(clippy::too_many_arguments)] // Match the existing companion callback.
    fn fixture_height(
        py: Python<'_>,
        _herbie: Py<PyAny>,
        _config: Py<PyAny>,
        _run: Py<PyAny>,
        _member: Py<PyAny>,
        directory: PathBuf,
        cancelled: Py<PyAny>,
        on_bytes: Option<Py<PyAny>>,
    ) -> PyResult<(PathBuf, u64, String, Vec<String>)> {
        let _ = on_bytes;
        FETCHES.fetch_add(1, Ordering::Relaxed);
        py.detach(|| std::thread::sleep(Duration::from_millis(80)));
        if cancelled.bind(py).call0()?.extract::<bool>()? {
            return Err(field_error(py, Error::Cancelled));
        }
        std::fs::create_dir_all(&directory)?;
        let path = directory.join("original.grib2");
        std::fs::write(&path, b"GRIB-fixture-7777")?;
        Ok((
            path,
            17,
            "https://fixture/terrain".into(),
            vec!["HGT".into()],
        ))
    }

    #[test]
    fn independent_work_overlaps_and_invariant_height_copies_have_separate_ownership() {
        let _lock = crate::lock_python_backend_test();
        Python::initialize();
        clear_cache();
        FETCHES.store(0, Ordering::Relaxed);
        let directory =
            std::env::temp_dir().join(format!("sharpmod-companion-{}", std::process::id()));
        Python::attach(|py| -> PyResult<()> {
            let api = PyModule::import(py, "sharpmod.tools.model_extract")?;
            let old = api.getattr("_surface_height_companion")?.unbind();
            api.setattr(
                "_surface_height_companion",
                wrap_pyfunction!(fixture_height, py)?,
            )?;
            let kwargs = PyDict::new(py);
            kwargs.set_item("key", "companion-test")?;
            kwargs.set_item("product", "oper")?;
            let config = PyModule::import(py, "types")?
                .getattr("SimpleNamespace")?
                .call((), Some(&kwargs))?
                .unbind();
            let run = PyModule::import(py, "datetime")?
                .getattr("datetime")?
                .call_method1("fromisoformat", ("2026-11-17T00:00:00",))?
                .unbind();
            let mut first = ModelCompanionFetch::new(
                py,
                py.None(),
                config.clone_ref(py),
                run.clone_ref(py),
                1,
                py.None(),
                Some(directory.join("first")),
                None,
                None,
                false,
                true,
            )?;
            py.detach(|| {
                let deadline = Instant::now() + Duration::from_secs(2);
                while FETCHES.load(Ordering::Relaxed) == 0 && Instant::now() < deadline {
                    std::thread::sleep(Duration::from_millis(1));
                }
            });
            assert_eq!(FETCHES.load(Ordering::Relaxed), 1);
            assert!(!first.worker.as_ref().unwrap().is_finished());
            py.detach(|| std::thread::sleep(Duration::from_millis(80)));
            let original: PathBuf = first.result(py)?.bind(py).get_item(0)?.extract()?;
            let mut next = ModelCompanionFetch::new(
                py,
                py.None(),
                config,
                run,
                2,
                py.None(),
                Some(directory.join("second")),
                None,
                None,
                false,
                true,
            )?;
            let duplicate: PathBuf = next.result(py)?.bind(py).get_item(0)?.extract()?;
            assert_ne!(original, duplicate);
            assert_eq!(std::fs::read(&original)?, std::fs::read(&duplicate)?);
            std::fs::remove_file(original)?;
            assert!(duplicate.exists());
            assert_eq!(FETCHES.load(Ordering::Relaxed), 1);
            first.close(py);
            next.close(py);
            api.setattr("_surface_height_companion", old)?;
            Ok(())
        })
        .unwrap();
        std::fs::remove_dir_all(directory).unwrap();
        clear_cache();
    }
    #[test]
    fn pending_companion_waits_are_cancellable_and_retain_no_permit() {
        let stop = AtomicBool::new(true);
        assert!(Permit::acquire(&stop).is_none());
    }
    #[test]
    fn cached_terrain_is_bounded_and_independent_from_caller_file_lifetime() {
        let cache = Cache::<String, Blob>::new(64, 2);
        let owner = cache.reserve(Some("run/terrain".into()));
        let bytes = b"GRIB12347777".to_vec();
        owner.complete(
            Ok(Blob {
                bytes: bytes.clone(),
                url: "source".into(),
                fields: vec!["HGT".into()],
                created: Instant::now(),
                _herbie: None,
            }),
            bytes.len(),
        );
        let follower = cache.reserve(Some("run/terrain".into()));
        assert_eq!(follower.wait(|| Ok(false)).unwrap().bytes, bytes);
        cache.clear();
        assert!(cache.reserve(Some("run/terrain".into())).leader);
    }
}

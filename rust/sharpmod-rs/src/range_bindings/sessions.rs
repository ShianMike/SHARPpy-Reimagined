//! Compatibility for caller-supplied Python sessions; transfer policy is native.

use crate::range_transfer::{Body, Error, Response, Transport};
use pyo3::{exceptions::PyStopIteration, prelude::*, types::PyDict};
use std::{
    collections::HashMap,
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc, Mutex,
    },
};

struct Shared {
    coordinator: Py<PyAny>,
    factory: Option<Py<PyAny>>,
    sessions: Mutex<HashMap<usize, Py<PyAny>>>,
    active: Mutex<HashMap<u64, Py<PyAny>>>,
    next: AtomicU64,
    stopped: AtomicBool,
}

pub(super) struct Sessions {
    shared: Arc<Shared>,
    timeout: (f64, f64),
    owned: bool,
}

impl Sessions {
    pub fn new(
        session: Py<PyAny>,
        factory: Option<Py<PyAny>>,
        timeout: (f64, f64),
        owned: bool,
    ) -> Self {
        Self {
            shared: Arc::new(Shared {
                coordinator: session,
                factory,
                sessions: Mutex::new(HashMap::new()),
                active: Mutex::new(HashMap::new()),
                next: AtomicU64::new(0),
                stopped: AtomicBool::new(false),
            }),
            timeout,
            owned,
        }
    }
}

fn failure(error: PyErr) -> Error {
    Error::Transient(error.to_string(), 0.)
}

struct PythonBody {
    iterator: Py<PyAny>,
    context: Option<Py<PyAny>>,
    shared: Arc<Shared>,
    id: u64,
}

impl Body for PythonBody {
    fn next(&mut self) -> Result<Option<Vec<u8>>, Error> {
        Python::attach(|py| match self.iterator.bind(py).call_method0("__next__") {
            Ok(bytes) => bytes.extract().map(Some).map_err(failure),
            Err(error) if error.is_instance_of::<PyStopIteration>(py) => Ok(None),
            Err(error) => Err(failure(error)),
        })
    }
    fn close(&mut self) {
        self.shared.active.lock().unwrap().remove(&self.id);
        if let Some(context) = self.context.take() {
            Python::attach(|py| {
                let _ = context
                    .bind(py)
                    .call_method1("__exit__", (py.None(), py.None(), py.None()));
            });
        }
    }
}

impl Drop for PythonBody {
    fn drop(&mut self) {
        self.close();
    }
}

impl Transport for Sessions {
    fn get(
        &self,
        worker: usize,
        url: &str,
        headers: &[(String, String)],
        chunk_size: usize,
    ) -> Result<Response, Error> {
        Python::attach(|py| {
            let session = match (worker, self.shared.factory.as_ref()) {
                (0, _) | (_, None) => self.shared.coordinator.clone_ref(py),
                (_, Some(factory)) => {
                    let existing = self
                        .shared
                        .sessions
                        .lock()
                        .unwrap()
                        .get(&worker)
                        .map(|session| session.clone_ref(py));
                    if let Some(session) = existing {
                        session
                    } else {
                        let session = factory.bind(py).call0()?.unbind();
                        self.shared
                            .sessions
                            .lock()
                            .unwrap()
                            .insert(worker, session.clone_ref(py));
                        session
                    }
                }
            };
            let kwargs = PyDict::new(py);
            let request_headers = PyDict::new(py);
            for (key, value) in headers {
                request_headers.set_item(key, value)?;
            }
            kwargs.set_item("headers", request_headers)?;
            kwargs.set_item("stream", true)?;
            kwargs.set_item("timeout", self.timeout)?;
            let context = session.bind(py).call_method("get", (url,), Some(&kwargs))?;
            let response = context.call_method0("__enter__")?;
            let id = self.shared.next.fetch_add(1, Ordering::Relaxed);
            self.shared
                .active
                .lock()
                .unwrap()
                .insert(id, response.clone().unbind());
            let mut body = PythonBody {
                iterator: py.None(),
                context: Some(context.unbind()),
                shared: self.shared.clone(),
                id,
            };
            if self.shared.stopped.load(Ordering::Relaxed) {
                if let Ok(close) = response.getattr("close") {
                    let _ = close.call0();
                }
                return Ok(Response {
                    status: 0,
                    headers: HashMap::new(),
                    body: Box::new(body),
                });
            }
            let status = response.getattr("status_code")?.extract()?;
            let mut headers = HashMap::new();
            let values = response.getattr("headers")?;
            for item in values.call_method0("items")?.try_iter()? {
                let (key, value): (String, String) = item?.extract()?;
                headers.insert(key.to_ascii_lowercase(), value);
            }
            let kwargs = PyDict::new(py);
            kwargs.set_item("chunk_size", chunk_size)?;
            body.iterator = response
                .call_method("iter_content", (), Some(&kwargs))?
                .try_iter()?
                .into_any()
                .unbind();
            Ok::<_, PyErr>(Response {
                status,
                headers,
                body: Box::new(body),
            })
        })
        .map_err(failure)
    }

    fn close_active(&self) {
        self.shared.stopped.store(true, Ordering::Relaxed);
        Python::attach(|py| {
            let responses: Vec<_> = self
                .shared
                .active
                .lock()
                .unwrap()
                .values()
                .map(|response| response.clone_ref(py))
                .collect();
            let sessions: Vec<_> = self
                .shared
                .sessions
                .lock()
                .unwrap()
                .values()
                .map(|session| session.clone_ref(py))
                .collect();
            for value in responses
                .into_iter()
                .chain(sessions)
                .chain(std::iter::once(self.shared.coordinator.clone_ref(py)))
            {
                if let Ok(close) = value.bind(py).getattr("close") {
                    let _ = close.call0();
                }
            }
        });
    }
}

impl Drop for Sessions {
    fn drop(&mut self) {
        Python::attach(|py| {
            let sessions: Vec<_> = self
                .shared
                .sessions
                .lock()
                .unwrap()
                .values()
                .map(|session| session.clone_ref(py))
                .collect();
            for session in sessions {
                if let Ok(close) = session.bind(py).getattr("close") {
                    let _ = close.call0();
                }
            }
            if self.owned {
                if let Ok(close) = self.shared.coordinator.bind(py).getattr("close") {
                    let _ = close.call0();
                }
            }
        });
    }
}

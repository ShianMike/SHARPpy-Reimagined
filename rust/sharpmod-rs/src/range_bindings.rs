//! Existing Python transport objects at the boundary of the native downloader.

mod sessions;

use pyo3::prelude::*;
use sharpmod_weather::range_http::{RangeClient, RangeResponse};
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
};

use crate::range_transfer::{
    self, Body, ByteRange, Error, Hooks, Metrics, Options, Response, Transport,
};

pub(super) struct Callbacks {
    cancelled: Option<Py<PyAny>>,
    progress: Option<Py<PyAny>>,
    bytes: Option<Py<PyAny>>,
    error: Mutex<Option<PyErr>>,
}

impl Callbacks {
    fn result(&self, result: PyResult<()>) -> Result<(), Error> {
        result.map_err(|error| {
            let message = error.to_string();
            self.error.lock().unwrap().get_or_insert(error);
            Error::Callback(message)
        })
    }
}

impl Hooks for Callbacks {
    fn cancelled(&self) -> bool {
        let Some(callback) = &self.cancelled else {
            return false;
        };
        match Python::attach(|py| callback.bind(py).call0()?.is_truthy()) {
            Ok(cancelled) => cancelled,
            Err(error) => {
                self.error.lock().unwrap().get_or_insert(error);
                true
            }
        }
    }

    fn progress(&self, completed: u64, total: u64) -> Result<(), Error> {
        self.result(Python::attach(|py| match &self.progress {
            Some(callback) => callback.bind(py).call1((completed, total)).map(|_| ()),
            None => Ok(()),
        }))
    }

    fn bytes(&self, count: u64) -> Result<(), Error> {
        self.result(Python::attach(|py| match &self.bytes {
            Some(callback) => callback.bind(py).call1((count,)).map(|_| ()),
            None => Ok(()),
        }))
    }
}

struct NativeTransport {
    client: Arc<RangeClient>,
    runtime: &'static tokio::runtime::Runtime,
    callbacks: Arc<Callbacks>,
    stop: Arc<AtomicBool>,
}

enum Session {
    Native(Arc<RangeClient>),
    Injected {
        coordinator: Py<PyAny>,
        factory: Py<PyAny>,
    },
}

#[pyclass]
struct ModelRangeSession {
    session: Option<Session>,
}

#[pymethods]
impl ModelRangeSession {
    fn close(&mut self, py: Python<'_>) -> PyResult<()> {
        if let Some(Session::Injected { coordinator, .. }) = self.session.take() {
            if let Ok(close) = coordinator.bind(py).getattr("close") {
                close.call0()?;
            }
        }
        Ok(())
    }
}

#[pyfunction]
fn model_range_session(py: Python<'_>, timeout: (f64, f64)) -> PyResult<ModelRangeSession> {
    let requests = PyModule::import(py, "requests")?;
    let factory = requests.getattr("Session")?;
    let original = requests.getattr("sessions")?.getattr("Session")?;
    let session = if factory.is(&original) {
        Session::Native(Arc::new(
            RangeClient::new(timeout.0, timeout.1)
                .map_err(|error| transfer_error(py, Error::Unavailable(error)))?,
        ))
    } else {
        Session::Injected {
            coordinator: factory.call0()?.unbind(),
            factory: factory.unbind(),
        }
    };
    Ok(ModelRangeSession {
        session: Some(session),
    })
}

fn network_error(error: String) -> Error {
    if error == "__cancelled__" {
        Error::Cancelled
    } else {
        Error::Transient(error, 0.)
    }
}

struct NativeBody {
    response: RangeResponse,
    runtime: &'static tokio::runtime::Runtime,
    callbacks: Arc<Callbacks>,
    stop: Arc<AtomicBool>,
    chunk_size: usize,
    finished: bool,
}

impl Body for NativeBody {
    fn next(&mut self) -> Result<Option<Vec<u8>>, Error> {
        if self.finished {
            return Ok(None);
        }
        let mut bytes = Vec::new();
        while bytes.len() < self.chunk_size {
            let chunk = self
                .runtime
                .block_on(
                    self.response
                        .chunk(|| self.stop.load(Ordering::Relaxed) || self.callbacks.cancelled()),
                )
                .map_err(network_error)?;
            match chunk {
                Some(chunk) => bytes.extend_from_slice(&chunk),
                None => {
                    self.finished = true;
                    break;
                }
            }
        }
        Ok((!bytes.is_empty()).then_some(bytes))
    }
}

impl Transport for NativeTransport {
    fn get(
        &self,
        _worker: usize,
        url: &str,
        headers: &[(String, String)],
        chunk_size: usize,
    ) -> Result<Response, Error> {
        let response = self
            .runtime
            .block_on(self.client.get(url, headers, || {
                self.stop.load(Ordering::Relaxed) || self.callbacks.cancelled()
            }))
            .map_err(network_error)?;
        Ok(Response {
            status: response.status,
            headers: response.headers.clone(),
            body: Box::new(NativeBody {
                response,
                runtime: self.runtime,
                callbacks: self.callbacks.clone(),
                stop: self.stop.clone(),
                chunk_size: chunk_size.max(1),
                finished: false,
            }),
        })
    }
    fn close_active(&self) {
        self.stop.store(true, Ordering::Relaxed);
    }
}

fn transfer_error(py: Python<'_>, error: Error) -> PyErr {
    let (kind, message) = match error {
        Error::Cancelled => (
            "DownloadCancelled",
            "forecast-model download cancelled".to_owned(),
        ),
        Error::ObjectChanged(message) => ("_ObjectChanged", message),
        Error::Unavailable(message) | Error::Callback(message) | Error::Transient(message, _) => {
            ("OptimizedTransportUnavailable", message)
        }
    };
    let construct = || -> PyResult<PyErr> {
        let error = PyModule::import(py, "sharpmod.models.model_transport")?.getattr(kind)?;
        Ok(PyErr::from_value(error.call1((message,))?))
    };
    construct().unwrap_or_else(|error| error)
}

#[pyfunction(signature=(session,url,ranges,output,timeout,chunk_size,cancelled,progress,on_bytes,workers,session_factory,retries,retry_backoff,metrics))]
#[allow(clippy::too_many_arguments)]
fn download_model_ranges(
    py: Python<'_>,
    session: Option<Py<PyAny>>,
    url: String,
    ranges: Vec<(u64, u64)>,
    output: PathBuf,
    timeout: (f64, f64),
    chunk_size: usize,
    cancelled: Option<Py<PyAny>>,
    progress: Option<Py<PyAny>>,
    on_bytes: Option<Py<PyAny>>,
    workers: usize,
    session_factory: Option<Py<PyAny>>,
    retries: isize,
    retry_backoff: f64,
    metrics: Option<Py<PyAny>>,
) -> PyResult<PathBuf> {
    let callbacks = Arc::new(Callbacks {
        cancelled,
        progress,
        bytes: on_bytes,
        error: Mutex::new(None),
    });
    let plan: Vec<_> = ranges
        .into_iter()
        .map(|(start, end)| ByteRange { start, end })
        .collect();
    let context = session
        .as_ref()
        .and_then(|session| session.bind(py).cast::<ModelRangeSession>().ok());
    let options = Options {
        workers: if session.is_some() && context.is_none() && session_factory.is_none() {
            1
        } else {
            workers
        },
        retries: retries.max(0) as usize,
        retry_backoff,
        chunk_size,
    };
    let runtime = crate::weather::runtime()?;
    let native = |client| {
        Box::new(NativeTransport {
            client,
            runtime,
            callbacks: callbacks.clone(),
            stop: Arc::new(AtomicBool::new(false)),
        }) as Box<dyn Transport>
    };
    let transport: Box<dyn Transport> = if let Some(context) = context {
        let context = context.borrow();
        match context.session.as_ref().ok_or_else(|| {
            pyo3::exceptions::PyValueError::new_err("model transfer session is closed")
        })? {
            Session::Native(client) => native(client.clone()),
            Session::Injected {
                coordinator,
                factory,
            } => Box::new(sessions::Sessions::new(
                coordinator.clone_ref(py),
                Some(factory.clone_ref(py)),
                timeout,
                false,
            )),
        }
    } else if let Some(session) = session {
        Box::new(sessions::Sessions::new(
            session,
            session_factory,
            timeout,
            false,
        ))
    } else {
        native(Arc::new(RangeClient::new(timeout.0, timeout.1).map_err(
            |error| transfer_error(py, Error::Unavailable(error)),
        )?))
    };
    let mut counts = Metrics::default();
    let result = py.detach(|| {
        range_transfer::download(
            transport.as_ref(),
            callbacks.as_ref(),
            &url,
            &plan,
            &output,
            &options,
            &mut counts,
        )
    });
    if let Some(metrics) = metrics {
        let metrics = metrics.bind(py);
        for (key, value) in [
            ("planned_bytes", counts.planned_bytes),
            ("transferred_bytes", counts.transferred_bytes),
            ("reused_bytes", counts.reused_bytes),
            ("request_count", counts.request_count),
            ("retry_count", counts.retry_count),
            ("worker_count", counts.worker_count as u64),
        ] {
            metrics.setattr(key, value)?;
        }
        metrics.setattr("fallback_used", counts.fallback_used)?;
    }
    if let Some(error) = callbacks.error.lock().unwrap().take() {
        return Err(error);
    }
    result.map_err(|error| transfer_error(py, error))?;
    Ok(output)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(model_range_session, module)?)?;
    module.add_function(wrap_pyfunction!(download_model_ranges, module)?)?;
    Ok(())
}

//! Fixed-input HTTP fixtures shared by native regression tests and benchmarks.
use pyo3::prelude::*;
use std::{
    io::{BufRead, BufReader, Write},
    net::{TcpListener, TcpStream},
    sync::{
        atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering},
        Arc, Mutex,
    },
    thread::JoinHandle,
    time::Duration,
};

static URL: Mutex<String> = Mutex::new(String::new());
#[pyfunction(signature=(_run,_hour,_source,_model="hrrr",*,release=None))]
fn fixture_url(
    _run: &Bound<'_, PyAny>,
    _hour: u32,
    _source: &str,
    _model: &str,
    release: Option<&Bound<'_, PyAny>>,
) -> String {
    let _ = release;
    URL.lock().unwrap().clone()
}

pub(super) struct Provider {
    api: Py<PyModule>,
    url: Py<PyAny>,
    decoder: Option<Py<PyAny>>,
}
impl Provider {
    pub fn new(
        py: Python<'_>,
        server: &Server,
        decoder: Option<Bound<'_, PyAny>>,
    ) -> PyResult<Self> {
        *URL.lock().unwrap() = format!("{}/fixture.grib2", server.url);
        let api = PyModule::import(py, "sharpmod.providers.hrrr_field")?;
        let url = api.getattr("grib_url")?.unbind();
        api.setattr("grib_url", wrap_pyfunction!(fixture_url, py)?)?;
        let original = if let Some(decoder) = decoder {
            let original = api.getattr("decode_message")?.unbind();
            api.setattr("decode_message", decoder)?;
            Some(original)
        } else {
            None
        };
        Ok(Self {
            api: api.unbind(),
            url,
            decoder: original,
        })
    }
}
impl Drop for Provider {
    fn drop(&mut self) {
        Python::attach(|py| {
            self.api
                .bind(py)
                .setattr("grib_url", self.url.bind(py))
                .unwrap();
            if let Some(decoder) = &self.decoder {
                self.api
                    .bind(py)
                    .setattr("decode_message", decoder.bind(py))
                    .unwrap();
            }
        });
    }
}
#[derive(Clone)]
struct Version {
    body: Arc<Vec<u8>>,
    index: Arc<Vec<u8>>,
    etag: String,
}

pub(super) struct Server {
    pub url: String,
    version: Arc<Mutex<Version>>,
    stop: Arc<AtomicBool>,
    worker: Option<JoinHandle<()>>,
    pub bytes: Arc<AtomicU64>,
    pub requests: Arc<AtomicU64>,
    pub active: Arc<AtomicUsize>,
}

impl Server {
    pub fn replace(&self, body: Vec<u8>, index: Vec<u8>, etag: &str) {
        *self.version.lock().unwrap() = Version {
            body: Arc::new(body),
            index: Arc::new(index),
            etag: etag.into(),
        };
    }
    pub fn start(body: Vec<u8>, index: Vec<u8>) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let stop = Arc::new(AtomicBool::new(false));
        let bytes = Arc::new(AtomicU64::new(0));
        let requests = Arc::new(AtomicU64::new(0));
        let active = Arc::new(AtomicUsize::new(0));
        let (stopping, count, sent, inflight) = (
            stop.clone(),
            requests.clone(),
            bytes.clone(),
            active.clone(),
        );
        let version = Arc::new(Mutex::new(Version {
            body: Arc::new(body),
            index: Arc::new(index),
            etag: "\"fixed-hrrr-fixture\"".into(),
        }));
        let current = version.clone();
        let worker = std::thread::spawn(move || {
            let mut workers = Vec::new();
            for stream in listener.incoming() {
                let stream = stream.unwrap();
                if stopping.load(Ordering::Acquire) {
                    break;
                }
                let Version { body, index, etag } = current.lock().unwrap().clone();
                let (body, index, count, sent, inflight) = (
                    body.clone(),
                    index.clone(),
                    count.clone(),
                    sent.clone(),
                    inflight.clone(),
                );
                workers.push(std::thread::spawn(move || {
                    stream.set_read_timeout(Some(Duration::from_secs(10))).unwrap();
                    let mut input=BufReader::new(stream);
                    let mut line=String::new();
                    if input.read_line(&mut line).is_err() {return;}
                    let path=line.split_whitespace().nth(1).unwrap_or("").to_string();
                    let mut range=None;
                    loop {
                        line.clear();
                        if input.read_line(&mut line).unwrap_or(0)==0 || line=="\r\n" {break;}
                        if let Some(value)=line.to_ascii_lowercase().strip_prefix("range: bytes=") {
                            if let Some((start,end))=value.trim().split_once('-') {range=Some((start.parse::<usize>().unwrap(),end.parse::<usize>().unwrap()));}
                        }
                    }
                    let mut stream=input.into_inner();
                    count.fetch_add(1,Ordering::Relaxed);
                    let (data,status,content_range)=if path.ends_with(".idx") {
                        (index.as_slice(),"200 OK",String::new())
                    } else if let Some((start,end))=range {
                        let end=end.min(body.len()-1);
                        (&body[start..=end],"206 Partial Content",format!("Content-Range: bytes {start}-{end}/{}\r\n",body.len()))
                    } else {(body.as_slice(),"200 OK",String::new())};
                    let header=format!("HTTP/1.1 {status}\r\nContent-Length: {}\r\n{content_range}ETag: {etag}\r\nConnection: close\r\n\r\n",data.len());
                    if stream.write_all(header.as_bytes()).is_err(){return;}
                    inflight.fetch_add(1,Ordering::Relaxed);
                    for (index,chunk) in data.chunks(128 * 1024).enumerate() {
                        if stream.write_all(chunk).is_err(){break;}
                        sent.fetch_add(chunk.len() as u64,Ordering::Relaxed);
                        if (index + 1) * 128 * 1024 < data.len() {
                            std::thread::sleep(Duration::from_millis(4));
                        }
                    }
                    inflight.fetch_sub(1,Ordering::Relaxed);
                }));
            }
            for worker in workers {
                worker.join().unwrap();
            }
        });
        Self {
            url,
            version,
            stop,
            worker: Some(worker),
            bytes,
            requests,
            active,
        }
    }
}
impl Drop for Server {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        let _ = TcpStream::connect(self.url.trim_start_matches("http://"));
        self.worker.take().unwrap().join().unwrap();
    }
}

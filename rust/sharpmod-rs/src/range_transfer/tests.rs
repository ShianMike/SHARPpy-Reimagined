use super::*;
use std::{
    collections::VecDeque,
    fs,
    path::PathBuf,
    sync::{
        atomic::{AtomicU64, AtomicUsize},
        Arc,
    },
    time::{SystemTime, UNIX_EPOCH},
};

mod basic;
mod identity;
mod parallel;
mod retry;

struct Directory(PathBuf);
impl Directory {
    fn new() -> Self {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let path = std::env::temp_dir().join(format!(
            "sharpmod-range-test-{}-{nonce}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }
    fn output(&self) -> PathBuf {
        self.0.join("subset.grib2")
    }
    fn fragments(&self) -> PathBuf {
        self.0.join(".subset.grib2.ranges")
    }
    fn seed(&self, range: ByteRange, bytes: &[u8], manifest: Option<serde_json::Value>) {
        fs::create_dir_all(self.fragments()).unwrap();
        fs::write(
            self.fragments()
                .join(format!("{}-{}.part", range.start, range.end)),
            bytes,
        )
        .unwrap();
        if let Some(value) = manifest {
            fs::write(
                self.fragments().join("manifest.json"),
                serde_json::to_vec(&value).unwrap(),
            )
            .unwrap();
        }
    }
    fn no_temporary_files(&self) -> bool {
        fs::read_dir(&self.0).unwrap().all(|entry| {
            entry
                .unwrap()
                .path()
                .extension()
                .is_none_or(|ext| ext != "tmp")
        })
    }
}
impl Drop for Directory {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}

fn payload() -> Vec<u8> {
    b"GRIBabcdefghijklmnopqrstuvwx7777".to_vec()
}
fn single(data: &[u8]) -> Vec<ByteRange> {
    vec![ByteRange {
        start: 0,
        end: data.len() as u64 - 1,
    }]
}
fn halves(data: &[u8]) -> Vec<ByteRange> {
    let middle = data.len() as u64 / 2;
    vec![
        ByteRange {
            start: 0,
            end: middle - 1,
        },
        ByteRange {
            start: middle,
            end: data.len() as u64 - 1,
        },
    ]
}
fn options(workers: usize) -> Options {
    Options {
        workers,
        retry_backoff: 0.0,
        ..Options::default()
    }
}
fn manifest(plan: &[ByteRange], identity: serde_json::Value) -> serde_json::Value {
    serde_json::json!({"version":2, "url":"https://fixture/model", "ranges": plan.iter().map(|r|[r.start,r.end]).collect::<Vec<_>>(), "identity":identity})
}

#[derive(Clone, Debug)]
struct Request {
    worker: usize,
    range: ByteRange,
    conditional: Option<String>,
    chunk_size: usize,
    sequence: usize,
}

struct Chunks(VecDeque<Result<Vec<u8>, Error>>);
impl Body for Chunks {
    fn next(&mut self) -> Result<Option<Vec<u8>>, Error> {
        self.0.pop_front().transpose()
    }
}

fn answer(request: &Request, data: &[u8]) -> Response {
    Response {
        status: 206,
        headers: HashMap::from([
            (
                "content-range".into(),
                format!(
                    "bytes {}-{}/{}",
                    request.range.start,
                    request.range.end,
                    data.len()
                ),
            ),
            ("etag".into(), "\"v1\"".into()),
        ]),
        body: Box::new(Chunks(
            data[request.range.start as usize..=request.range.end as usize]
                .chunks(request.chunk_size)
                .map(|chunk| Ok(chunk.to_vec()))
                .collect(),
        )),
    }
}

type Respond = dyn Fn(&Request, &[u8]) -> Result<Response, Error> + Sync;
struct Fake {
    data: Vec<u8>,
    seen: Mutex<Vec<Request>>,
    respond: Box<Respond>,
    closed: AtomicUsize,
}
impl Fake {
    fn new(data: &[u8]) -> Self {
        Self::with(data, |request, data| Ok(answer(request, data)))
    }
    fn with(
        data: &[u8],
        respond: impl Fn(&Request, &[u8]) -> Result<Response, Error> + Sync + 'static,
    ) -> Self {
        Self {
            data: data.to_vec(),
            seen: Mutex::new(Vec::new()),
            respond: Box::new(respond),
            closed: AtomicUsize::new(0),
        }
    }
    fn requests(&self) -> Vec<Request> {
        self.seen.lock().unwrap().clone()
    }
}
impl Transport for Fake {
    fn get(
        &self,
        worker: usize,
        url: &str,
        headers: &[(String, String)],
        chunk_size: usize,
    ) -> Result<Response, Error> {
        assert_eq!(url, "https://fixture/model");
        let headers: HashMap<_, _> = headers.iter().cloned().collect();
        let (start, end) = headers["Range"]
            .strip_prefix("bytes=")
            .unwrap()
            .split_once('-')
            .unwrap();
        let mut seen = self.seen.lock().unwrap();
        let request = Request {
            worker,
            range: ByteRange {
                start: start.parse().unwrap(),
                end: end.parse().unwrap(),
            },
            conditional: headers.get("If-Range").cloned(),
            chunk_size,
            sequence: seen.len(),
        };
        seen.push(request.clone());
        drop(seen);
        (self.respond)(&request, &self.data)
    }
    fn close_active(&self) {
        self.closed.fetch_add(1, Ordering::SeqCst);
    }
}

#[derive(Default)]
struct Record {
    progress: Mutex<Vec<(u64, u64)>>,
    bytes: AtomicU64,
    cancel: Arc<AtomicBool>,
    cancel_after: Option<u64>,
    fail_bytes: bool,
}
impl Hooks for Record {
    fn cancelled(&self) -> bool {
        self.cancel.load(Ordering::SeqCst)
    }
    fn progress(&self, completed: u64, total: u64) -> Result<(), Error> {
        self.progress.lock().unwrap().push((completed, total));
        if self.cancel_after.is_some_and(|limit| completed >= limit) {
            self.cancel.store(true, Ordering::SeqCst);
        }
        Ok(())
    }
    fn bytes(&self, count: u64) -> Result<(), Error> {
        self.bytes.fetch_add(count, Ordering::SeqCst);
        if self.fail_bytes {
            Err(Error::Callback("fixture callback failed".into()))
        } else {
            Ok(())
        }
    }
}

fn run(
    server: &dyn Transport,
    hooks: &dyn Hooks,
    plan: &[ByteRange],
    directory: &Directory,
    options: &Options,
    metrics: &mut Metrics,
) -> Result<(), Error> {
    download(
        server,
        hooks,
        "https://fixture/model",
        plan,
        &directory.output(),
        options,
        metrics,
    )
}

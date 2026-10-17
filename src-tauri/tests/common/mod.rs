//! Test doubles shared by `tests/supervisor.rs` and `tests/commands.rs`: a fake
//! launcher whose children are scripted tasks on in-memory pipes, and a
//! recording status sink.
#![allow(dead_code)]

use std::collections::VecDeque;
use std::future::Future;
use std::path::Path;
use std::pin::Pin;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader, DuplexStream};
use tokio::sync::watch;
use tokio::task::AbortHandle;
use tokio::time::Instant;

use sharppy_reimagined_lib::dto::health::BridgeStatus;
use sharppy_reimagined_lib::error::BridgeFailure;
use sharppy_reimagined_lib::events::StatusSink;
use sharppy_reimagined_lib::python::launcher::{
    ChildControl, ExitFuture, LaunchedProcess, ProcessLauncher,
};

/// The child's side of its three pipes.
pub struct FakeIo {
    requests: BufReader<DuplexStream>,
    stdout: DuplexStream,
    pub stderr: DuplexStream,
}

impl FakeIo {
    /// The next request frame, or `None` at EOF.
    pub async fn next_request(&mut self) -> Option<Value> {
        let mut line = String::new();
        match self.requests.read_line(&mut line).await {
            Ok(0) | Err(_) => None,
            Ok(_) => Some(serde_json::from_str(&line).expect("the supervisor writes valid JSON")),
        }
    }

    pub async fn send_raw(&mut self, bytes: &[u8]) {
        let _ = self.stdout.write_all(bytes).await;
    }

    pub async fn send(&mut self, frame: Value) {
        let mut bytes = serde_json::to_vec(&frame).unwrap();
        bytes.push(b'\n');
        self.send_raw(&bytes).await;
    }

    pub async fn reply(&mut self, id: &Value, result: Value) {
        self.send(json!({ "id": id, "result": result })).await;
    }

    pub async fn reply_error(&mut self, id: &Value, error: Value) {
        self.send(json!({ "id": id, "error": error })).await;
    }

    /// Reads requests until `method` arrives; answers `hello` along the way.
    pub async fn expect(&mut self, method: &str) -> Option<Value> {
        loop {
            let request = self.next_request().await?;
            if request["method"] == method {
                return Some(request);
            }
            if request["method"] == "hello" {
                self.reply(&request["id"], hello_result()).await;
            }
        }
    }
}

pub fn hello_result() -> Value {
    json!({"protocol_version": 1, "sharpmod_version": "3.0.0", "python_version": "3.11.14",
           "python_executable": "C:\\fake\\python.exe", "pid": 4242})
}

pub fn fixture(relative: &str) -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("contracts")
        .join(relative);
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

/// A well-behaved bridge: answers every Phase 1 method from the fixtures,
/// counts `hello` requests, and exits 0 on `shutdown` or at EOF.
pub async fn bridge(mut io: FakeIo, hellos: Arc<AtomicUsize>) -> i32 {
    while let Some(request) = io.next_request().await {
        let id = request["id"].clone();
        match request["method"].as_str() {
            Some("hello") => {
                hellos.fetch_add(1, Ordering::SeqCst);
                io.reply(&id, hello_result()).await;
            }
            Some("health") => io.reply(&id, fixture("bridge/health.result.ok.json")).await,
            Some("soundings.progress") => {
                assert_eq!(request["params"], json!({"progress_id": "download1"}));
                io.reply(&id, fixture("bridge/soundings/progress.result.json"))
                    .await;
            }
            Some("soundings.open_model") => {
                assert_eq!(request["params"]["progress_id"], "download1");
                io.reply(&id, fixture("bridge/soundings/open.result.example.json"))
                    .await;
            }
            Some("storage.usage") => io.reply(&id, fixture("bridge/storage/usage.json")).await,
            Some("storage.clear") => {
                assert_eq!(request["params"], json!({"cache": "all"}));
                io.reply(&id, fixture("bridge/storage/clear.json")).await;
            }
            Some("stations.catalog") => {
                io.reply(&id, fixture("bridge/stations.catalog.result.sample.json"))
                    .await
            }
            Some("shutdown") => {
                io.reply(&id, json!({"ok": true})).await;
                return 0;
            }
            _ => {
                let error =
                    json!({"code": "METHOD_NOT_FOUND", "message": "Unknown.", "retryable": false});
                io.reply_error(&id, error).await;
            }
        }
    }
    0
}

pub type Script = Box<dyn FnOnce(FakeIo) -> Pin<Box<dyn Future<Output = i32> + Send>> + Send>;

pub fn script<F, Fut>(f: F) -> Script
where
    F: FnOnce(FakeIo) -> Fut + Send + 'static,
    Fut: Future<Output = i32> + Send + 'static,
{
    Box::new(move |io| Box::pin(f(io)))
}

fn set_once(exit: &mut Option<Option<i32>>, code: Option<i32>) -> bool {
    if exit.is_some() {
        return false;
    }
    *exit = Some(code);
    true
}

struct FakeControl {
    exit: watch::Receiver<Option<Option<i32>>>,
    set_exit: watch::Sender<Option<Option<i32>>>,
    task: AbortHandle,
}

impl ChildControl for FakeControl {
    fn wait(&mut self) -> ExitFuture<'_> {
        Box::pin(async move {
            self.exit
                .wait_for(Option::is_some)
                .await
                .ok()
                .and_then(|code| *code)
                .flatten()
        })
    }

    fn start_kill(&mut self) {
        self.task.abort();
        self.set_exit.send_if_modified(|exit| set_once(exit, None));
    }
}

/// Launches scripted children: the queued scripts first, then well-behaved bridges.
#[derive(Default)]
pub struct FakeLauncher {
    scripts: Mutex<VecDeque<Script>>,
    pub spawns: AtomicUsize,
    pub hellos: Arc<AtomicUsize>,
}

impl FakeLauncher {
    pub fn with(scripts: Vec<Script>) -> Arc<Self> {
        Arc::new(Self {
            scripts: Mutex::new(scripts.into()),
            ..Self::default()
        })
    }

    pub fn spawns(&self) -> usize {
        self.spawns.load(Ordering::SeqCst)
    }
}

impl ProcessLauncher for FakeLauncher {
    fn launch(&self) -> Result<LaunchedProcess, BridgeFailure> {
        self.spawns.fetch_add(1, Ordering::SeqCst);
        let (stdin, child_stdin) = tokio::io::duplex(1 << 16);
        let (child_stdout, stdout) = tokio::io::duplex(1 << 16);
        let (child_stderr, stderr) = tokio::io::duplex(1 << 16);
        let io = FakeIo {
            requests: BufReader::new(child_stdin),
            stdout: child_stdout,
            stderr: child_stderr,
        };
        let next = self.scripts.lock().unwrap().pop_front();
        let hellos = Arc::clone(&self.hellos);
        let run: Script = next.unwrap_or_else(|| script(move |io| bridge(io, hellos)));
        let (set_exit, exit) = watch::channel(None);
        let on_exit = set_exit.clone();
        let task = tokio::spawn(async move {
            let code = run(io).await;
            on_exit.send_if_modified(|exit| set_once(exit, Some(code)));
        });
        Ok(LaunchedProcess {
            program: "C:\\fake\\python.exe".to_owned(),
            pid: Some(1000 + u32::try_from(self.spawns()).unwrap()),
            stdin: Box::new(stdin),
            stdout: Box::new(stdout),
            stderr: Box::new(stderr),
            control: Box::new(FakeControl {
                exit,
                set_exit,
                task: task.abort_handle(),
            }),
        })
    }
}

/// A launcher with no interpreter configured.
pub struct NotConfigured;

impl ProcessLauncher for NotConfigured {
    fn launch(&self) -> Result<LaunchedProcess, BridgeFailure> {
        Err(BridgeFailure::NotConfigured {
            message: "The Python backend is not configured.".to_owned(),
        })
    }
}

/// Records every published status with the (possibly paused) time it was published.
#[derive(Default)]
pub struct RecordingSink {
    pub events: Mutex<Vec<(Instant, BridgeStatus)>>,
}

impl StatusSink for RecordingSink {
    fn publish(&self, status: &BridgeStatus) {
        self.events
            .lock()
            .unwrap()
            .push((Instant::now(), status.clone()));
    }
}

impl RecordingSink {
    pub fn statuses(&self) -> Vec<BridgeStatus> {
        self.events
            .lock()
            .unwrap()
            .iter()
            .map(|(_, s)| s.clone())
            .collect()
    }

    pub fn names(&self) -> Vec<&'static str> {
        self.events
            .lock()
            .unwrap()
            .iter()
            .map(|(_, s)| s.name())
            .collect()
    }

    /// The time of the `n`th (0-based) published status matching `pred`.
    pub fn time_of(&self, n: usize, pred: impl Fn(&BridgeStatus) -> bool) -> Option<Instant> {
        self.events
            .lock()
            .unwrap()
            .iter()
            .filter(|(_, s)| pred(s))
            .nth(n)
            .map(|(t, _)| *t)
    }

    /// Waits (in small steps of possibly paused time) until `count` statuses match `pred`.
    pub async fn wait_for(&self, count: usize, pred: impl Fn(&BridgeStatus) -> bool) {
        for _ in 0..100_000 {
            if self
                .events
                .lock()
                .unwrap()
                .iter()
                .filter(|(_, s)| pred(s))
                .count()
                >= count
            {
                return;
            }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        }
        panic!("status never reached; got {:?}", self.names());
    }
}

pub fn is_up(status: &BridgeStatus) -> bool {
    matches!(status, BridgeStatus::Up { .. })
}

pub fn is_unavailable(status: &BridgeStatus) -> bool {
    matches!(status, BridgeStatus::Unavailable { .. })
}

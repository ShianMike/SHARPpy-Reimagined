//! The process supervisor (DESIGN-bridge B1 and B6): one Python child per app
//! instance, a state machine with restart and backoff, request multiplexing by
//! id, per-request timeouts with the two request phases, and shutdown.
//!
//! The supervisor publishes every transition through a [`StatusSink`], so it
//! does not depend on the Tauri runtime and is tested with a fake launcher and
//! a paused clock.

mod control;
mod io;
mod requests;
mod state;
mod transitions;

use std::collections::{HashMap, VecDeque};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::Duration;

use serde::de::DeserializeOwned;
use serde_json::{json, Value};
use tokio::runtime::Handle;
use tokio::sync::{mpsc, oneshot, watch, Notify};
use tokio::time::{timeout, timeout_at, Instant};

use crate::dto::health::BridgeStatus;
use crate::dto::rfc3339_utc_now;
use crate::error::{BackendError, BridgeFailure, RequestPhase};
use crate::events::StatusSink;
use crate::python::launcher::ProcessLauncher;
use crate::python::protocol::{
    decode_payload, encode_request, Envelope, ErrorPayload, HelloResult, PROTOCOL_VERSION,
};

/// Timeouts and restart policy. `default()` holds the production values.
#[derive(Debug, Clone)]
pub struct SupervisorConfig {
    pub spawn_timeout: Duration,
    pub health_timeout: Duration,
    pub catalog_timeout: Duration,
    pub ping_timeout: Duration,
    pub shutdown_timeout: Duration,
    pub backoff_initial: Duration,
    pub backoff_max: Duration,
    /// Crashes are counted in this sliding window, and a child that stays up
    /// this long resets the restart attempt counter.
    pub crash_window: Duration,
    /// The crash inside the window that stops automatic restarts.
    pub max_crashes: usize,
}

impl Default for SupervisorConfig {
    fn default() -> Self {
        Self {
            spawn_timeout: Duration::from_secs(15),
            health_timeout: Duration::from_secs(20),
            catalog_timeout: Duration::from_secs(15),
            ping_timeout: Duration::from_secs(3),
            shutdown_timeout: Duration::from_secs(2),
            backoff_initial: Duration::from_millis(500),
            backoff_max: Duration::from_secs(30),
            crash_window: Duration::from_secs(60),
            max_crashes: 5,
        }
    }
}

/// What a request sees: send to the current child, wait, or fail at once.
#[derive(Debug, Clone)]
enum Gate {
    Up(u64),
    Waiting(&'static str),
    Closed(BackendError),
}

/// Events from one child's I/O tasks to the lifecycle task.
#[derive(Debug)]
enum ChildEvent {
    Exited(Option<i32>),
    Eof,
    Corrupt(String),
    PingFailed,
}

type Reply = Result<Value, BridgeFailure>;

struct Pending {
    tx: oneshot::Sender<Reply>,
    generation: u64,
    method: String,
    request_id: Option<String>,
}

struct ChildSlot {
    generation: u64,
    program: String,
    writer: mpsc::UnboundedSender<Vec<u8>>,
    events: mpsc::UnboundedSender<ChildEvent>,
    kill: Arc<Notify>,
    exit: watch::Receiver<Option<Option<i32>>>,
    up_since: Option<Instant>,
    ping_in_flight: bool,
}

struct Inner {
    status: BridgeStatus,
    epoch: u64,
    started: bool,
    shutting_down: bool,
    generation: u64,
    child: Option<ChildSlot>,
    pending: HashMap<u64, Pending>,
    timed_out: VecDeque<(u64, String)>,
    hello: Option<HelloResult>,
    ups: u32,
    crashes: VecDeque<Instant>,
    attempt: u32,
}

struct Shared {
    config: SupervisorConfig,
    launcher: Arc<dyn ProcessLauncher>,
    sink: Arc<dyn StatusSink>,
    rt: Handle,
    inner: Mutex<Inner>,
    next_id: AtomicU64,
    gate: watch::Sender<Gate>,
    epoch: watch::Sender<u64>,
    restart_lock: tokio::sync::Mutex<()>,
}

/// Handle to the supervisor; clones share one child.
#[derive(Clone)]
pub struct Supervisor {
    shared: Arc<Shared>,
}

const TIMED_OUT_MEMORY: usize = 64;

impl Supervisor {
    pub fn new(
        config: SupervisorConfig,
        launcher: Arc<dyn ProcessLauncher>,
        sink: Arc<dyn StatusSink>,
        rt: Handle,
    ) -> Self {
        let inner = Inner {
            status: BridgeStatus::Starting {
                since: rfc3339_utc_now(),
            },
            epoch: 0,
            started: false,
            shutting_down: false,
            generation: 0,
            child: None,
            pending: HashMap::new(),
            timed_out: VecDeque::new(),
            hello: None,
            ups: 0,
            crashes: VecDeque::new(),
            attempt: 0,
        };
        let shared = Shared {
            config,
            launcher,
            sink,
            rt,
            inner: Mutex::new(inner),
            next_id: AtomicU64::new(1),
            gate: watch::Sender::new(Gate::Waiting("starting")),
            epoch: watch::Sender::new(0),
            restart_lock: tokio::sync::Mutex::new(()),
        };
        Self {
            shared: Arc::new(shared),
        }
    }

    pub fn config(&self) -> &SupervisorConfig {
        &self.shared.config
    }

    fn lock(&self) -> MutexGuard<'_, Inner> {
        self.shared
            .inner
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
    }

    /// Starts the first child in the background. Later calls do nothing.
    pub fn start(&self) {
        let epoch = {
            let mut inner = self.lock();
            if inner.started || inner.shutting_down {
                return;
            }
            inner.started = true;
            let status = BridgeStatus::Starting {
                since: rfc3339_utc_now(),
            };
            self.transition(&mut inner, status, Gate::Waiting("starting"));
            inner.epoch
        };
        self.spawn_lifecycle(epoch);
    }

    /// The current state; never waits on Python.
    pub fn status(&self) -> BridgeStatus {
        self.lock().status.clone()
    }

    /// The cached `hello` facts, while the bridge is `up`.
    pub fn hello(&self) -> Option<HelloResult> {
        let inner = self.lock();
        matches!(inner.status, BridgeStatus::Up { .. })
            .then(|| inner.hello.clone())
            .flatten()
    }

    fn transition(&self, inner: &mut Inner, status: BridgeStatus, gate: Gate) {
        log::info!(
            "bridge status: {} -> {}",
            inner.status.name(),
            status.name()
        );
        inner.status = status;
        self.shared.gate.send_replace(gate);
        self.shared.sink.publish(&inner.status);
    }
}

fn payload_mismatch(method: &str, reason: String) -> BridgeFailure {
    let mut details = serde_json::Map::new();
    details.insert("method".into(), Value::from(method));
    BridgeFailure::Protocol {
        reason,
        details: Some(details),
    }
}

/// Fails every pending request of `generation` (or of every child).
fn fail_pending(inner: &mut Inner, generation: Option<u64>, failure: impl Fn() -> BridgeFailure) {
    let ids: Vec<u64> = inner
        .pending
        .iter()
        .filter(|(_, p)| generation.is_none_or(|g| p.generation == g))
        .map(|(id, _)| *id)
        .collect();
    for id in ids {
        if let Some(pending) = inner.pending.remove(&id) {
            let _ = pending.tx.send(Err(failure()));
        }
    }
}

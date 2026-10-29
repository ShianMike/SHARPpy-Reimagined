use super::*;
use std::{
    sync::Condvar,
    time::{Duration, Instant},
};

#[test]
fn cancellation_after_first_chunk_preserves_the_fragment() {
    let directory = Directory::new();
    let data = payload();
    let server = Fake::new(&data);
    let mut metrics = Metrics::default();
    let result = run(
        &server,
        &Record {
            cancel_after: Some(4),
            ..Record::default()
        },
        &single(&data),
        &directory,
        &Options {
            chunk_size: 4,
            ..options(1)
        },
        &mut metrics,
    );
    assert_eq!(result, Err(Error::Cancelled));
    assert_eq!(metrics.transferred_bytes, 4);
    assert_eq!(
        fs::read(directory.fragments().join("0-31.part")).unwrap(),
        b"GRIB"
    );
    assert!(!directory.output().exists());
}

#[test]
fn cancelled_parallel_transfer_joins_then_resumes_retained_fragments() {
    let directory = Directory::new();
    let data = payload();
    let plan = halves(&data);
    let server = Fake::new(&data);
    let hooks = Record {
        cancel_after: Some(4),
        ..Record::default()
    };
    let mut metrics = Metrics::default();
    let opts = Options {
        chunk_size: 4,
        ..options(2)
    };
    assert_eq!(
        run(&server, &hooks, &plan, &directory, &opts, &mut metrics),
        Err(Error::Cancelled)
    );
    assert!(directory.fragments().exists());
    let retained: u64 = plan
        .iter()
        .map(|r| {
            fs::metadata(
                directory
                    .fragments()
                    .join(format!("{}-{}.part", r.start, r.end)),
            )
            .map_or(0, |m| m.len())
        })
        .sum();
    assert!(retained > 0 && retained < data.len() as u64);
    assert!(server.requests().len() <= 3);
    assert_eq!(metrics.transferred_bytes, retained + 1);
    let resumed = Fake::new(&data);
    run(
        &resumed,
        &Record::default(),
        &plan,
        &directory,
        &opts,
        &mut metrics,
    )
    .unwrap();
    assert_eq!(metrics.reused_bytes, retained);
    assert_eq!(
        metrics.transferred_bytes + metrics.reused_bytes,
        data.len() as u64 + 1
    );
    assert!(resumed.requests()[1..]
        .iter()
        .any(|r| !plan.iter().any(|p| p.start == r.range.start)));
    assert_eq!(fs::read(directory.output()).unwrap(), data);
    assert!(!directory.fragments().exists());
}

#[derive(Default)]
struct Gate {
    entered: Mutex<bool>,
    changed: Condvar,
    closed: AtomicUsize,
    active: AtomicUsize,
    close_while_active: AtomicBool,
}
impl Gate {
    fn enter(&self) {
        *self.entered.lock().unwrap() = true;
        self.changed.notify_all();
    }
    fn wait_until_entered(&self) {
        assert!(
            *self
                .changed
                .wait_timeout_while(self.entered.lock().unwrap(), Duration::from_secs(3), |v| {
                    !*v
                })
                .unwrap()
                .0
        );
    }
    fn close(&self) {
        self.close_while_active
            .fetch_or(self.active.load(Ordering::SeqCst) > 0, Ordering::SeqCst);
        self.closed.fetch_add(1, Ordering::SeqCst);
        self.changed.notify_all();
    }
}

struct BlockedBody {
    gate: Arc<Gate>,
    after: usize,
    started: bool,
}
impl Body for BlockedBody {
    fn next(&mut self) -> Result<Option<Vec<u8>>, Error> {
        if !self.started {
            self.gate.active.fetch_add(1, Ordering::SeqCst);
            self.started = true;
        }
        self.gate.enter();
        let _guard = self
            .gate
            .changed
            .wait_timeout_while(
                self.gate.entered.lock().unwrap(),
                Duration::from_secs(3),
                |_| self.gate.closed.load(Ordering::SeqCst) <= self.after,
            )
            .unwrap()
            .0;
        assert!(
            self.gate.closed.load(Ordering::SeqCst) > self.after,
            "blocked body was not closed"
        );
        Err(Error::Transient("closed blocked body".into(), 0.0))
    }
}
impl Drop for BlockedBody {
    fn drop(&mut self) {
        if self.started {
            self.gate.active.fetch_sub(1, Ordering::SeqCst);
        }
    }
}

struct BlockingTransport {
    data: Vec<u8>,
    gate: Arc<Gate>,
    fatal_peer: bool,
    late_response: bool,
    calls: AtomicUsize,
}
impl Transport for BlockingTransport {
    fn get(
        &self,
        worker: usize,
        _url: &str,
        headers: &[(String, String)],
        chunk_size: usize,
    ) -> Result<Response, Error> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        let (start, end) = headers
            .iter()
            .find(|(key, _)| key == "Range")
            .unwrap()
            .1
            .strip_prefix("bytes=")
            .unwrap()
            .split_once('-')
            .unwrap();
        let request = Request {
            worker,
            range: ByteRange {
                start: start.parse().unwrap(),
                end: end.parse().unwrap(),
            },
            conditional: None,
            chunk_size,
            sequence: 0,
        };
        let mut response = answer(&request, &self.data);
        if request.range.end == 0 {
            return Ok(response);
        }
        if self.fatal_peer && request.range.start > 0 {
            self.gate.wait_until_entered();
            response.headers.insert("etag".into(), "\"changed\"".into());
            return Ok(response);
        }
        if self.late_response {
            self.gate.enter();
            let _guard = self
                .gate
                .changed
                .wait_timeout_while(
                    self.gate.entered.lock().unwrap(),
                    Duration::from_secs(3),
                    |_| self.gate.closed.load(Ordering::SeqCst) == 0,
                )
                .unwrap()
                .0;
            assert!(self.gate.closed.load(Ordering::SeqCst) > 0);
        }
        response.body = Box::new(BlockedBody {
            gate: self.gate.clone(),
            after: usize::from(self.late_response),
            started: false,
        });
        Ok(response)
    }
    fn close_active(&self) {
        self.gate.close();
    }
}

#[test]
fn monitor_unblocks_cancelled_body_and_late_arriving_response() {
    for late_response in [false, true] {
        let directory = Directory::new();
        let data = payload();
        let gate = Arc::new(Gate::default());
        let server = BlockingTransport {
            data: data.clone(),
            gate: gate.clone(),
            fatal_peer: false,
            late_response,
            calls: AtomicUsize::new(0),
        };
        let hooks = Record::default();
        let mut metrics = Metrics::default();
        let started = Instant::now();
        std::thread::scope(|scope| {
            scope.spawn(|| {
                gate.wait_until_entered();
                hooks.cancel.store(true, Ordering::SeqCst);
            });
            assert_eq!(
                run(
                    &server,
                    &hooks,
                    &single(&data),
                    &directory,
                    &options(1),
                    &mut metrics
                ),
                Err(Error::Cancelled)
            );
        });
        assert!(started.elapsed() < Duration::from_secs(2));
        assert_eq!(gate.active.load(Ordering::SeqCst), 0);
        assert!(gate.closed.load(Ordering::SeqCst) >= if late_response { 2 } else { 1 });
        assert!(!directory.output().exists());
        assert_eq!(metrics.retry_count, 0);
    }
}

#[test]
fn fatal_peer_closes_blocked_worker_before_join_and_source_cleanup() {
    let directory = Directory::new();
    let data = payload();
    let gate = Arc::new(Gate::default());
    let server = BlockingTransport {
        data: data.clone(),
        gate: gate.clone(),
        fatal_peer: true,
        late_response: false,
        calls: AtomicUsize::new(0),
    };
    let mut metrics = Metrics::default();
    let started = Instant::now();
    let plan = (0..4)
        .map(|index| ByteRange {
            start: index * 8,
            end: index * 8 + 7,
        })
        .collect::<Vec<_>>();
    let result = run(
        &server,
        &Record::default(),
        &plan,
        &directory,
        &options(2),
        &mut metrics,
    );
    assert!(matches!(result, Err(Error::ObjectChanged(_))));
    assert!(started.elapsed() < Duration::from_secs(2));
    assert!(gate.close_while_active.load(Ordering::SeqCst));
    assert_eq!(gate.active.load(Ordering::SeqCst), 0);
    assert!(!directory.fragments().exists());
    assert!(!directory.output().exists());
    assert_eq!(metrics.request_count, 3);
    assert_eq!(metrics.retry_count, 0);
    assert_eq!(server.calls.load(Ordering::SeqCst), 3);
}

#[test]
fn fixed_worker_slots_are_bounded_and_callbacks_are_serialized() {
    struct SerialHooks {
        busy: AtomicUsize,
        progress: Mutex<Vec<u64>>,
        bytes: AtomicU64,
    }
    impl Hooks for SerialHooks {
        fn cancelled(&self) -> bool {
            false
        }
        fn bytes(&self, count: u64) -> Result<(), Error> {
            assert_eq!(self.busy.fetch_add(1, Ordering::SeqCst), 0);
            self.bytes.fetch_add(count, Ordering::SeqCst);
            std::thread::yield_now();
            self.busy.fetch_sub(1, Ordering::SeqCst);
            Ok(())
        }
        fn progress(&self, completed: u64, _total: u64) -> Result<(), Error> {
            assert_eq!(self.busy.fetch_add(1, Ordering::SeqCst), 0);
            self.progress.lock().unwrap().push(completed);
            std::thread::yield_now();
            self.busy.fetch_sub(1, Ordering::SeqCst);
            Ok(())
        }
    }
    struct SlowBody {
        inner: Box<dyn Body>,
        active: Arc<AtomicUsize>,
        peak: Arc<AtomicUsize>,
        entered: bool,
    }
    impl Body for SlowBody {
        fn next(&mut self) -> Result<Option<Vec<u8>>, Error> {
            if !self.entered {
                self.entered = true;
                let count = self.active.fetch_add(1, Ordering::SeqCst) + 1;
                self.peak.fetch_max(count, Ordering::SeqCst);
                std::thread::sleep(Duration::from_millis(10));
            }
            self.inner.next()
        }
    }
    impl Drop for SlowBody {
        fn drop(&mut self) {
            if self.entered {
                self.active.fetch_sub(1, Ordering::SeqCst);
            }
        }
    }
    let directory = Directory::new();
    let mut data = vec![b'a'; 128];
    data[..4].copy_from_slice(b"GRIB");
    data[124..].copy_from_slice(b"7777");
    let plan: Vec<_> = (0..16)
        .map(|index| ByteRange {
            start: index * 8,
            end: index * 8 + 7,
        })
        .collect();
    let active = Arc::new(AtomicUsize::new(0));
    let peak = Arc::new(AtomicUsize::new(0));
    let server = Fake::with(&data, {
        let active = active.clone();
        let peak = peak.clone();
        move |request, data| {
            let mut response = answer(request, data);
            if request.worker > 0 {
                response.body = Box::new(SlowBody {
                    inner: response.body,
                    active: active.clone(),
                    peak: peak.clone(),
                    entered: false,
                });
            }
            Ok(response)
        }
    });
    let hooks = SerialHooks {
        busy: AtomicUsize::new(0),
        progress: Mutex::new(Vec::new()),
        bytes: AtomicU64::new(0),
    };
    let mut metrics = Metrics::default();
    run(
        &server,
        &hooks,
        &plan,
        &directory,
        &options(999),
        &mut metrics,
    )
    .unwrap();
    assert_eq!(metrics.worker_count, 8);
    assert!((2..=8).contains(&peak.load(Ordering::SeqCst)));
    assert_eq!(active.load(Ordering::SeqCst), 0);
    assert!(server.requests()[1..]
        .iter()
        .all(|request| (1..=8).contains(&request.worker)));
    let progress = hooks.progress.lock().unwrap();
    assert!(progress.windows(2).all(|pair| pair[0] <= pair[1]));
    assert_eq!(progress.last(), Some(&128));
    assert_eq!(hooks.bytes.load(Ordering::SeqCst), 128);
    assert_eq!(metrics.transferred_bytes, 129);
    assert_eq!(fs::read(directory.output()).unwrap(), data);
}

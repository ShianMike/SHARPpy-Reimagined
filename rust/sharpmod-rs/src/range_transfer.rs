//! Resumable range acquisition shared by native HTTP and injected transports.

use std::{
    collections::HashMap,
    path::Path,
    sync::{
        atomic::{AtomicBool, Ordering},
        Condvar, Mutex, OnceLock,
    },
    time::Duration,
};

use rayon::{prelude::*, ThreadPool, ThreadPoolBuilder};

mod disk;
mod requests;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct ByteRange {
    pub start: u64,
    pub end: u64,
}

impl ByteRange {
    fn size(self) -> u64 {
        self.end - self.start + 1
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum Error {
    Cancelled,
    ObjectChanged(String),
    Unavailable(String),
    Transient(String, f64),
    Callback(String),
}

pub trait Body: Send {
    fn next(&mut self) -> Result<Option<Vec<u8>>, Error>;
    fn close(&mut self) {}
}

pub struct Response {
    pub status: u16,
    /// Response names are normalized once by the transport boundary.
    pub headers: HashMap<String, String>,
    pub body: Box<dyn Body>,
}

pub trait Transport: Sync {
    fn get(
        &self,
        worker: usize,
        url: &str,
        headers: &[(String, String)],
        chunk_size: usize,
    ) -> Result<Response, Error>;
    fn close_active(&self) {}
}

pub trait Hooks: Sync {
    fn cancelled(&self) -> bool;
    fn progress(&self, completed: u64, total: u64) -> Result<(), Error>;
    fn bytes(&self, count: u64) -> Result<(), Error>;
}

#[derive(Clone, Debug)]
pub struct Options {
    pub workers: usize,
    pub retries: usize,
    pub retry_backoff: f64,
    pub chunk_size: usize,
}

impl Default for Options {
    fn default() -> Self {
        Self {
            workers: 1,
            retries: 2,
            retry_backoff: 0.25,
            chunk_size: 256 * 1024,
        }
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Metrics {
    pub planned_bytes: u64,
    pub transferred_bytes: u64,
    pub reused_bytes: u64,
    pub request_count: u64,
    pub retry_count: u64,
    pub worker_count: usize,
    pub fallback_used: bool,
}

struct State {
    sizes: Vec<u64>,
    completed: u64,
    metrics: Metrics,
}

struct Transfer<'a> {
    transport: &'a dyn Transport,
    hooks: &'a dyn Hooks,
    url: &'a str,
    plan: &'a [ByteRange],
    options: &'a Options,
    store: disk::Store,
    state: Mutex<State>,
    stop: AtomicBool,
}

impl Transfer<'_> {
    fn check(&self) -> Result<(), Error> {
        if self.stop.load(Ordering::Acquire) || self.hooks.cancelled() {
            self.stop.store(true, Ordering::Release);
            return Err(Error::Cancelled);
        }
        Ok(())
    }

    fn progress(&self, index: usize, size: u64, bytes: u64) -> Result<(), Error> {
        let mut state = self.state.lock().unwrap();
        if bytes > 0 {
            self.hooks.bytes(bytes)?;
        }
        state.completed = state.completed - state.sizes[index] + size;
        state.sizes[index] = size;
        self.hooks
            .progress(state.completed, state.metrics.planned_bytes)
    }

    fn run(&self, output: &Path) -> Result<(), Error> {
        let mut workers = self.state.lock().unwrap().metrics.worker_count;
        let stored = self.store.identity();
        let parts_exist = self.store.has_parts()?;
        if workers > 1 || (parts_exist && !stored.is_empty()) {
            let identity = requests::probe(self)?;
            // A weak ETag cannot protect the byte representation with If-Range.
            if workers > 1 && identity.if_range().is_none() {
                workers = 1;
                self.state.lock().unwrap().metrics.worker_count = 1;
            }
            if parts_exist && !stored.matches(&identity) {
                self.store.reset()?;
            }
            self.store.set_identity(identity)?;
        } else {
            self.store.save()?;
        }

        let sizes = self.store.sizes(self.plan)?;
        let incomplete: Vec<_> = sizes
            .iter()
            .enumerate()
            .filter_map(|(index, &size)| (size != self.plan[index].size()).then_some(index))
            .collect();
        {
            let mut state = self.state.lock().unwrap();
            state.completed = sizes.iter().sum();
            state.metrics.reused_bytes = state.completed;
            state.sizes = sizes;
            if state.completed > 0 {
                self.hooks
                    .progress(state.completed, state.metrics.planned_bytes)?;
            }
        }
        if workers == 1 {
            for index in incomplete {
                requests::fragment(self, index, 0)?;
            }
        } else if !incomplete.is_empty() {
            self.parallel(&incomplete, workers)?;
        }
        self.check()?;
        self.store.assemble(self.plan, output, || self.check())?;
        self.store.remove()
    }

    fn parallel(&self, incomplete: &[usize], workers: usize) -> Result<(), Error> {
        static POOL: OnceLock<Result<ThreadPool, String>> = OnceLock::new();
        let pool = POOL
            .get_or_init(|| {
                ThreadPoolBuilder::new()
                    .num_threads(8)
                    .thread_name(|index| format!("sharpmod-range-{index}"))
                    .build()
                    .map_err(|error| error.to_string())
            })
            .as_ref()
            .map_err(|error| Error::Unavailable(error.clone()))?;
        let queue = Mutex::new((0usize, None::<Error>));
        pool.install(|| {
            (1..=workers).into_par_iter().for_each(|worker| loop {
                let index = {
                    let mut queue = queue.lock().unwrap();
                    if queue.1.is_some()
                        || self.stop.load(Ordering::Acquire)
                        || queue.0 == incomplete.len()
                    {
                        break;
                    }
                    let index = incomplete[queue.0];
                    queue.0 += 1;
                    index
                };
                if let Err(error) = requests::fragment(self, index, worker) {
                    self.stop.store(true, Ordering::Release);
                    {
                        let mut queue = queue.lock().unwrap();
                        if queue.1.is_none()
                            || matches!(error, Error::ObjectChanged(_))
                            || matches!(queue.1, Some(Error::Cancelled))
                        {
                            queue.1 = Some(error);
                        }
                    }
                    // Close before joining: another worker may be blocked in IO.
                    self.transport.close_active();
                    break;
                }
            });
        });
        queue.into_inner().unwrap().1.map_or(Ok(()), Err)
    }
}

pub fn download(
    transport: &dyn Transport,
    hooks: &dyn Hooks,
    url: &str,
    plan: &[ByteRange],
    output: &Path,
    options: &Options,
    metrics: &mut Metrics,
) -> Result<(), Error> {
    *metrics = Metrics::default();
    if plan.is_empty() {
        return Err(Error::Unavailable("byte-range plan is empty".into()));
    }
    let mut seen = std::collections::HashSet::new();
    for &item in plan {
        let size = item
            .end
            .checked_sub(item.start)
            .and_then(|n| n.checked_add(1));
        let Some(total) = size.and_then(|n| metrics.planned_bytes.checked_add(n)) else {
            return Err(Error::Unavailable(
                "inventory contains an invalid byte range".into(),
            ));
        };
        if !seen.insert(item) {
            return Err(Error::Unavailable(
                "inventory contains duplicate byte ranges".into(),
            ));
        }
        metrics.planned_bytes = total;
    }
    metrics.worker_count = options.workers.clamp(1, 8).min(plan.len());
    if disk::valid_grib(output) {
        metrics.worker_count = 0;
        metrics.reused_bytes = metrics.planned_bytes;
        return Ok(());
    }
    let transfer = Transfer {
        transport,
        hooks,
        url,
        plan,
        options,
        store: disk::Store::open(output, url, plan)?,
        state: Mutex::new(State {
            sizes: vec![0; plan.len()],
            completed: 0,
            metrics: metrics.clone(),
        }),
        stop: AtomicBool::new(false),
    };
    let done = (Mutex::new(false), Condvar::new());
    let result = std::thread::scope(|scope| {
        scope.spawn(|| loop {
            let finished = done
                .1
                .wait_timeout_while(done.0.lock().unwrap(), Duration::from_millis(50), |v| !*v)
                .unwrap()
                .0;
            if *finished {
                break;
            }
            drop(finished);
            if transfer.stop.load(Ordering::Acquire) || hooks.cancelled() {
                transfer.stop.store(true, Ordering::Release);
                // Repeat until requests join, including late-arriving responses.
                transport.close_active();
            }
        });
        let result = transfer.run(output);
        *done.0.lock().unwrap() = true;
        done.1.notify_one();
        result
    });
    *metrics = transfer.state.into_inner().unwrap().metrics;
    if matches!(result, Err(Error::ObjectChanged(_))) {
        transfer.store.remove()?;
    }
    result
}

#[cfg(test)]
mod tests;

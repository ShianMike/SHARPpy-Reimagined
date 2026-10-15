//! State transitions of the lifecycle: `up`, a lost child, crash
//! accounting with backoff, a new attempt, and `unavailable`.

use super::state::Outcome;
use super::*;
use crate::error::ErrorContext;

impl Supervisor {
    /// Removes child `generation` and kills it. Its stdin closes with the slot.
    pub(super) fn drop_child(&self, generation: u64) {
        let mut inner = self.lock();
        if inner
            .child
            .as_ref()
            .is_some_and(|c| c.generation == generation)
        {
            if let Some(child) = inner.child.take() {
                child.kill.notify_one();
            }
        }
    }

    pub(super) fn go_up(&self, epoch: u64, generation: u64, hello: HelloResult) -> bool {
        let mut inner = self.lock();
        if inner.epoch != epoch {
            return false;
        }
        let Some(child) = inner.child.as_mut().filter(|c| c.generation == generation) else {
            return false;
        };
        child.up_since = Some(Instant::now());
        log::info!(
            "bridge up: {} (interpreter pid {})",
            child.program,
            hello.pid
        );
        inner.ups += 1;
        let status = BridgeStatus::Up {
            protocol_version: hello.protocol_version,
            pid: hello.pid,
            since: rfc3339_utc_now(),
            restarts: inner.ups - 1,
        };
        inner.hello = Some(hello);
        self.transition(&mut inner, status, Gate::Up(generation));
        true
    }

    pub(super) fn lose_up_child(
        &self,
        epoch: u64,
        generation: u64,
        pending: Box<dyn Fn() -> BridgeFailure + Send>,
        error: BackendError,
        up_since: Instant,
    ) -> Outcome {
        {
            let mut inner = self.lock();
            if inner.epoch != epoch {
                return Outcome::Cancelled;
            }
            fail_pending(&mut inner, Some(generation), pending);
            inner.hello = None;
        }
        self.drop_child(generation);
        Outcome::Crashed {
            error,
            up_since: Some(up_since),
        }
    }

    /// Counts a crash; returns the backoff delay, or `None` when the crash
    /// budget is spent (state `unavailable`) or the lifecycle was cancelled.
    pub(super) fn record_crash(
        &self,
        epoch: u64,
        error: BackendError,
        up_since: Option<Instant>,
    ) -> Option<Duration> {
        let config = &self.shared.config;
        let mut inner = self.lock();
        if inner.epoch != epoch {
            return None;
        }
        let now = Instant::now();
        if up_since.is_some_and(|since| now.duration_since(since) >= config.crash_window) {
            inner.attempt = 0;
        }
        inner.crashes.push_back(now);
        while inner
            .crashes
            .front()
            .is_some_and(|t| now.duration_since(*t) >= config.crash_window)
        {
            inner.crashes.pop_front();
        }
        if inner.crashes.len() >= config.max_crashes {
            log::error!(
                "bridge crashed {} times within {:?}; automatic restarts paused",
                inner.crashes.len(),
                config.crash_window
            );
            let error = BridgeFailure::Unavailable { last: error.code }
                .into_backend_error(&ErrorContext::start_backend());
            self.transition(
                &mut inner,
                BridgeStatus::Unavailable {
                    error: error.clone(),
                },
                Gate::Closed(error),
            );
            return None;
        }
        inner.attempt += 1;
        let factor = 1u32 << (inner.attempt - 1).min(16);
        let delay = config
            .backoff_initial
            .saturating_mul(factor)
            .min(config.backoff_max);
        let status = BridgeStatus::Restarting {
            attempt: inner.attempt,
            retry_in_ms: u64::try_from(delay.as_millis()).unwrap_or(u64::MAX),
            last_error: error,
        };
        self.transition(&mut inner, status, Gate::Waiting("restarting"));
        Some(delay)
    }

    pub(super) fn begin_attempt(&self, epoch: u64) -> bool {
        let mut inner = self.lock();
        if inner.epoch != epoch {
            return false;
        }
        let status = BridgeStatus::Starting {
            since: rfc3339_utc_now(),
        };
        self.transition(&mut inner, status, Gate::Waiting("starting"));
        true
    }

    pub(super) fn go_unavailable(&self, epoch: u64, error: BackendError) {
        let mut inner = self.lock();
        if inner.epoch != epoch {
            return;
        }
        if let Some(child) = inner.child.take() {
            child.kill.notify_one();
        }
        inner.hello = None;
        log::error!(
            "bridge unavailable: {} ({})",
            error.message,
            error.code.as_str()
        );
        self.transition(
            &mut inner,
            BridgeStatus::Unavailable {
                error: error.clone(),
            },
            Gate::Closed(error),
        );
    }
}

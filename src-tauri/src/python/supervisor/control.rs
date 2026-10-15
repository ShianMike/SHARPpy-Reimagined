//! Explicit control: `restart_backend` and app shutdown.

use super::*;

impl Supervisor {
    /// Kills any child, clears the crash history, and starts a new child.
    /// Returns once `hello` has succeeded or failed. A call while a restart is
    /// running joins that restart.
    pub async fn restart(&self) -> BridgeStatus {
        let guard = match self.shared.restart_lock.try_lock() {
            Ok(guard) => guard,
            Err(_) => {
                let _joined = self.shared.restart_lock.lock().await;
                return self.status();
            }
        };
        let epoch = {
            let mut inner = self.lock();
            if inner.shutting_down {
                return inner.status.clone();
            }
            let failure = || BridgeFailure::Crashed {
                restart_requested: true,
                details: None,
            };
            fail_pending(&mut inner, None, failure);
            if let Some(child) = inner.child.take() {
                child.kill.notify_one();
            }
            inner.crashes.clear();
            inner.attempt = 0;
            inner.started = true;
            inner.epoch += 1;
            self.shared.epoch.send_replace(inner.epoch);
            let status = BridgeStatus::Starting {
                since: rfc3339_utc_now(),
            };
            self.transition(&mut inner, status, Gate::Waiting("starting"));
            inner.epoch
        };
        self.spawn_lifecycle(epoch);
        let mut gate = self.shared.gate.subscribe();
        let limit = self.shared.config.spawn_timeout + Duration::from_secs(1);
        let _ = timeout(
            limit,
            gate.wait_for(|g| !matches!(g, Gate::Waiting("starting"))),
        )
        .await;
        drop(guard);
        self.status()
    }

    /// App exit requested: ask the child to shut down, without blocking, and
    /// stop automatic restarts.
    pub fn begin_shutdown(&self) {
        let generation = {
            let mut inner = self.lock();
            if inner.shutting_down {
                return;
            }
            inner.shutting_down = true;
            inner.epoch += 1;
            self.shared.epoch.send_replace(inner.epoch);
            inner.child.as_ref().map(|c| c.generation)
        };
        if let Some(generation) = generation {
            log::info!("bridge shutdown requested");
            let _ = self.send_on_child(generation, "shutdown", json!({}));
        }
    }

    /// App exit: waits up to the shutdown timeout for the child to exit, then
    /// kills it. Returns the child's exit code when it exited by itself.
    pub async fn finish_shutdown(&self) -> Option<i32> {
        self.begin_shutdown();
        let child = self.lock().child.take();
        let mut child = child?;
        let exited = timeout(
            self.shared.config.shutdown_timeout,
            child.exit.wait_for(Option::is_some),
        )
        .await
        .map(|waited| waited.map(|code| *code));
        match exited {
            Ok(Ok(code)) => {
                log::info!("bridge exited with {code:?} on shutdown");
                code.flatten()
            }
            _ => {
                log::warn!(
                    "bridge did not exit within {:?}; killing it",
                    self.shared.config.shutdown_timeout
                );
                child.kill.notify_one();
                let _ = timeout(
                    self.shared.config.shutdown_timeout,
                    child.exit.wait_for(Option::is_some),
                )
                .await;
                None
            }
        }
    }
}

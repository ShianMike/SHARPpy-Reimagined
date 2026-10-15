//! The lifecycle task: spawn, handshake, `up`, crash classification, backoff,
//! and the move to `unavailable` (DESIGN-bridge B1 and B6).
//!
//! Each lifecycle runs under one epoch. `restart()` and shutdown bump the
//! epoch, which ends the old lifecycle without counting a crash.

use super::*;
use crate::error::ErrorContext;

pub(super) enum Outcome {
    Cancelled,
    Fatal(BackendError),
    Crashed {
        error: BackendError,
        up_since: Option<Instant>,
    },
}

/// How long a child that exited may still deliver its `hello` reply when its
/// stdout has not reached EOF yet (for example a pipe held by a grandchild).
const EXIT_GRACE: Duration = Duration::from_secs(2);

async fn cancelled(epochs: &mut watch::Receiver<u64>, epoch: u64) {
    let _ = epochs.wait_for(|current| *current != epoch).await;
}

fn details(key: &str, value: Value) -> Option<serde_json::Map<String, Value>> {
    let mut map = serde_json::Map::new();
    map.insert(key.to_owned(), value);
    Some(map)
}

fn millis(duration: Duration) -> Value {
    Value::from(u64::try_from(duration.as_millis()).unwrap_or(u64::MAX))
}

/// `"3.11.14"` and newer pass; anything older or unparsable fails.
fn python_supported(version: &str) -> bool {
    let mut parts = version.split('.').map(|p| p.parse::<u32>().ok());
    matches!((parts.next().flatten(), parts.next().flatten()), (Some(major), Some(minor)) if (major, minor) >= (3, 11))
}

impl Supervisor {
    pub(super) fn spawn_lifecycle(&self, epoch: u64) {
        let this = self.clone();
        self.shared
            .rt
            .spawn(async move { this.lifecycle(epoch).await });
    }

    async fn lifecycle(&self, epoch: u64) {
        let mut epochs = self.shared.epoch.subscribe();
        loop {
            match self.run_child(epoch, &mut epochs).await {
                Outcome::Cancelled => return,
                Outcome::Fatal(error) => return self.go_unavailable(epoch, error),
                Outcome::Crashed { error, up_since } => {
                    let Some(delay) = self.record_crash(epoch, error, up_since) else {
                        return;
                    };
                    tokio::select! {
                        () = tokio::time::sleep(delay) => {}
                        () = cancelled(&mut epochs, epoch) => return,
                    }
                    if !self.begin_attempt(epoch) {
                        return;
                    }
                }
            }
        }
    }

    async fn run_child(&self, epoch: u64, epochs: &mut watch::Receiver<u64>) -> Outcome {
        let start = ErrorContext::start_backend();
        let process = match self.shared.launcher.launch() {
            Ok(process) => process,
            Err(failure) => return Outcome::Fatal(failure.into_backend_error(&start)),
        };
        let program = process.program.clone();
        let Some((generation, mut events, tail)) = self.attach(epoch, process) else {
            return Outcome::Cancelled;
        };
        let limit = self.shared.config.spawn_timeout;
        let deadline = Instant::now() + limit;
        let params = json!({ "protocol_version": PROTOCOL_VERSION });
        let (hello_id, hello_rx) = match self.send_on_child(generation, "hello", params) {
            Some((id, rx)) => (Some(id), Some(rx)),
            // The child is already gone: wait for its exit event (or the deadline).
            None => (None, None),
        };
        let hello_wait = async move {
            match hello_rx {
                Some(rx) => rx.await,
                None => std::future::pending().await,
            }
        };
        tokio::pin!(hello_wait);
        // A child may answer `hello` and exit at once. The reader routes every
        // frame before it reports EOF, so an exit counts as "before the
        // handshake" only once stdout is drained (or after a short grace) and
        // the reply still has not arrived. `biased` lets a ready reply win.
        let mut exited: Option<Option<i32>> = None;
        let mut eof = false;
        let reply = loop {
            let wait_until = match exited {
                Some(_) => deadline.min(Instant::now() + EXIT_GRACE),
                None => deadline,
            };
            if exited.is_some() && eof {
                match timeout(Duration::ZERO, &mut hello_wait).await {
                    Ok(reply) => break Ok(reply),
                    Err(_) => {
                        return self.exited_before_hello(
                            generation,
                            &program,
                            exited.flatten(),
                            &tail,
                        )
                    }
                }
            }
            tokio::select! {
                biased;
                reply = timeout_at(wait_until, &mut hello_wait) => match (reply, exited) {
                    (Err(_), Some(code)) => return self.exited_before_hello(generation, &program, code, &tail),
                    (reply, _) => break reply,
                },
                event = events.recv() => match event {
                    Some(ChildEvent::Exited(code)) => exited = Some(code),
                    Some(ChildEvent::Eof) => eof = true,
                    Some(ChildEvent::Corrupt(reason)) => return self.lose_before_up(generation, protocol(&reason)),
                    Some(ChildEvent::PingFailed) => {}
                    None => return Outcome::Cancelled,
                },
                () = cancelled(epochs, epoch) => return Outcome::Cancelled,
            }
        };
        let hello = match reply {
            Err(_elapsed) => {
                if let Some(id) = hello_id {
                    self.lock().pending.remove(&id);
                }
                log::error!("bridge handshake timed out after {limit:?}");
                let failure = BridgeFailure::Spawn {
                    reason: format!(
                        "{program} did not complete the handshake within {} s",
                        limit.as_secs()
                    ),
                    details: details("timeoutMs", millis(limit)),
                };
                return self.lose_before_up(generation, failure.into_backend_error(&start));
            }
            Ok(Err(_)) => {
                return self.lose_before_up(generation, protocol("the handshake reply was lost"))
            }
            Ok(Ok(Err(failure))) => {
                return self.fatal_before_up(generation, failure.into_backend_error(&start))
            }
            Ok(Ok(Ok(value))) => match decode_payload::<HelloResult>(value) {
                Ok(hello) => hello,
                Err(reason) => {
                    return self.fatal_before_up(generation, protocol(&format!("hello: {reason}")))
                }
            },
        };
        if hello.protocol_version != PROTOCOL_VERSION {
            let reason = format!(
                "the bridge speaks protocol {}, the app speaks {PROTOCOL_VERSION}",
                hello.protocol_version
            );
            return self.fatal_before_up(generation, protocol(&reason));
        }
        if !python_supported(&hello.python_version) {
            let failure = BridgeFailure::Spawn {
                reason: format!(
                    "{program} runs Python {}, but Python 3.11 or newer is required",
                    hello.python_version
                ),
                details: None,
            };
            return self.fatal_before_up(generation, failure.into_backend_error(&start));
        }
        let up_since = Instant::now();
        if !self.go_up(epoch, generation, hello) {
            return Outcome::Cancelled;
        }
        // The child answered and then went away while the reply was in flight.
        let already_gone = match (exited, eof) {
            (Some(code), _) => Some(ChildEvent::Exited(code)),
            (None, true) => Some(ChildEvent::Eof),
            (None, false) => None,
        };
        if let Some(event) = already_gone {
            let (pending, error) = classify(event);
            return self.lose_up_child(epoch, generation, pending, error, up_since);
        }
        tokio::select! {
            event = events.recv() => match event.map(classify) {
                Some((pending, error)) => self.lose_up_child(epoch, generation, pending, error, up_since),
                None => Outcome::Cancelled,
            },
            () = cancelled(epochs, epoch) => Outcome::Cancelled,
        }
    }

    fn exited_before_hello(
        &self,
        generation: u64,
        program: &str,
        code: Option<i32>,
        tail: &Mutex<VecDeque<String>>,
    ) -> Outcome {
        let lines: Vec<String> = tail
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .iter()
            .cloned()
            .collect();
        log::error!(
            "bridge exited with code {code:?} before the handshake; last stderr lines:\n{}",
            lines.join("\n")
        );
        let code_text = code.map_or_else(|| "an unknown code".to_owned(), |c| format!("code {c}"));
        let failure = BridgeFailure::Spawn {
            reason: format!(
                "{program} exited with {code_text} before the handshake. Check that sharpmod is installed in that \
                 interpreter and that it is Python 3.11 or newer"
            ),
            details: details("exitCode", code.map_or(Value::Null, Value::from)),
        };
        self.drop_child(generation);
        Outcome::Fatal(failure.into_backend_error(&ErrorContext::start_backend()))
    }

    fn lose_before_up(&self, generation: u64, error: BackendError) -> Outcome {
        self.drop_child(generation);
        Outcome::Crashed {
            error,
            up_since: None,
        }
    }

    fn fatal_before_up(&self, generation: u64, error: BackendError) -> Outcome {
        self.drop_child(generation);
        Outcome::Fatal(error)
    }
}

fn protocol(reason: &str) -> BackendError {
    BridgeFailure::Protocol {
        reason: reason.to_owned(),
        details: None,
    }
    .into_backend_error(&ErrorContext::start_backend())
}

type PendingFailure = Box<dyn Fn() -> BridgeFailure + Send>;

/// How the loss of an `up` child fails its pending requests, and the error the
/// `restarting` state reports.
fn classify(event: ChildEvent) -> (PendingFailure, BackendError) {
    match event {
        ChildEvent::Exited(code) => {
            log::error!("bridge exited with code {code:?}");
            crash(details("exitCode", code.map_or(Value::Null, Value::from)))
        }
        ChildEvent::Eof => crash(details("reason", Value::from("stdoutClosed"))),
        ChildEvent::PingFailed => crash(details("reason", Value::from("livenessPingFailed"))),
        ChildEvent::Corrupt(reason) => {
            log::error!("bridge stream corrupt: {reason}");
            let error = protocol(&reason);
            let failure = move || BridgeFailure::Protocol {
                reason: reason.clone(),
                details: None,
            };
            (Box::new(failure), error)
        }
    }
}

fn crash(details: Option<serde_json::Map<String, Value>>) -> (PendingFailure, BackendError) {
    let error = BridgeFailure::Crashed {
        restart_requested: false,
        details: details.clone(),
    }
    .into_backend_error(&ErrorContext::start_backend());
    let failure = move || BridgeFailure::Crashed {
        restart_requested: false,
        details: details.clone(),
    };
    (Box::new(failure), error)
}

#[cfg(test)]
mod tests {
    use super::python_supported;

    #[test]
    fn python_version_floor() {
        assert!(python_supported("3.11.14"));
        assert!(python_supported("3.13.0"));
        assert!(python_supported("4.0"));
        assert!(!python_supported("3.10.12"));
        assert!(!python_supported("3"));
        assert!(!python_supported("x.y"));
    }
}

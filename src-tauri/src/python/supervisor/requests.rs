//! Requests: sending by id, the two request phases, the liveness ping, and
//! routing replies by the DESIGN-bridge B6 frame classes.

use super::*;

impl Supervisor {
    /// Sends one request and waits for its reply, within `limit` from now.
    pub async fn request(&self, method: &str, params: Value, limit: Duration) -> Reply {
        let deadline = Instant::now() + limit;
        let mut gate = self.shared.gate.subscribe();
        loop {
            let current = gate.borrow_and_update().clone();
            match current {
                Gate::Closed(error) => return Err(BridgeFailure::Reported(Box::new(error))),
                Gate::Up(generation) => {
                    if let Some((id, rx)) = self.send_on_child(generation, method, params.clone()) {
                        return match timeout_at(deadline, rx).await {
                            Ok(Ok(reply)) => reply,
                            Ok(Err(_)) => Err(BridgeFailure::Crashed {
                                restart_requested: false,
                                details: None,
                            }),
                            Err(_) => {
                                self.on_request_timeout(id, generation, method);
                                Err(BridgeFailure::Timeout {
                                    phase: RequestPhase::AwaitingReply,
                                    state: None,
                                    after: limit,
                                })
                            }
                        };
                    }
                }
                Gate::Waiting(_) => {}
            }
            if timeout_at(deadline, gate.changed()).await.is_err() {
                let state = match &*gate.borrow() {
                    Gate::Waiting(state) => *state,
                    _ => "starting",
                };
                return Err(BridgeFailure::Timeout {
                    phase: RequestPhase::WaitingForBridge,
                    state: Some(state),
                    after: limit,
                });
            }
        }
    }

    /// [`Supervisor::request`] plus the typed decode of the result payload. A
    /// payload that does not decode fails this request alone with `BRIDGE_PROTOCOL`.
    pub async fn request_typed<T: DeserializeOwned>(
        &self,
        method: &str,
        params: Value,
        limit: Duration,
    ) -> Result<T, BridgeFailure> {
        let value = self.request(method, params, limit).await?;
        decode_payload(value).map_err(|reason| {
            log::error!("bridge {method}: result payload rejected: {reason}");
            payload_mismatch(method, reason)
        })
    }

    /// Writes a request to child `generation` if it is still the current child.
    pub(super) fn send_on_child(
        &self,
        generation: u64,
        method: &str,
        params: Value,
    ) -> Option<(u64, oneshot::Receiver<Reply>)> {
        let mut inner = self.lock();
        let child = inner
            .child
            .as_ref()
            .filter(|c| c.generation == generation)?;
        let id = self.shared.next_id.fetch_add(1, Ordering::Relaxed);
        let (tx, rx) = oneshot::channel();
        let request_id = params
            .get("request_id")
            .and_then(Value::as_str)
            .filter(|value| {
                !value.is_empty()
                    && value.len() <= 64
                    && value.bytes().all(|c| c.is_ascii_alphanumeric())
            })
            .map(str::to_owned);
        let frame = match encode_request(id, method, params) {
            Ok(frame) => frame,
            Err(reason) => {
                let _ = tx.send(Err(BridgeFailure::Protocol {
                    reason,
                    details: None,
                }));
                return Some((id, rx));
            }
        };
        child.writer.send(frame).ok()?;
        inner.pending.insert(
            id,
            Pending {
                tx,
                generation,
                method: method.to_owned(),
                request_id,
            },
        );
        Some((id, rx))
    }

    fn on_request_timeout(&self, id: u64, generation: u64, method: &str) {
        let mut inner = self.lock();
        let request_id = inner
            .pending
            .remove(&id)
            .and_then(|pending| pending.request_id);
        inner.timed_out.push_back((id, method.to_owned()));
        while inner.timed_out.len() > TIMED_OUT_MEMORY {
            inner.timed_out.pop_front();
        }
        let up = matches!(inner.status, BridgeStatus::Up { .. });
        let Some(child) = inner
            .child
            .as_mut()
            .filter(|c| c.generation == generation && up)
        else {
            return;
        };
        let mut cancel_ack = None;
        if method != "cancel" && method != "hello" {
            let cancel_id = self.shared.next_id.fetch_add(1, Ordering::Relaxed);
            if let Ok(frame) = encode_request(cancel_id, "cancel", json!({"id": id})) {
                let _ = child.writer.send(frame);
                cancel_ack = Some(cancel_id);
            }
        }
        let ping_in_flight = child.ping_in_flight;
        child.ping_in_flight = true;
        if let Some(cancel_id) = cancel_ack {
            inner.timed_out.push_back((cancel_id, "cancel".to_owned()));
            while inner.timed_out.len() > TIMED_OUT_MEMORY {
                inner.timed_out.pop_front();
            }
        }
        drop(inner);
        log::warn!("bridge timeout method={method} wire_id={id} generation={generation} request_id={request_id:?} liveness_ping={}", !ping_in_flight);
        if ping_in_flight {
            return;
        }
        let this = self.clone();
        self.shared
            .rt
            .spawn(async move { this.ping(generation).await });
    }

    async fn ping(&self, generation: u64) {
        let limit = self.shared.config.ping_timeout;
        let params = json!({ "protocol_version": PROTOCOL_VERSION });
        let ok = match self.send_on_child(generation, "hello", params) {
            Some((id, rx)) => match timeout(limit, rx).await {
                Ok(Ok(Ok(value))) => decode_payload::<HelloResult>(value).is_ok(),
                Ok(_) => false,
                Err(_) => {
                    self.on_request_timeout_quiet(id);
                    false
                }
            },
            None => return,
        };
        let mut inner = self.lock();
        if let Some(child) = inner.child.as_mut().filter(|c| c.generation == generation) {
            child.ping_in_flight = false;
            if !ok {
                log::error!("bridge liveness ping failed; restarting the child");
                let _ = child.events.send(ChildEvent::PingFailed);
            }
        }
    }

    fn on_request_timeout_quiet(&self, id: u64) {
        let mut inner = self.lock();
        inner.pending.remove(&id);
        inner.timed_out.push_back((id, "hello".to_owned()));
    }

    /// Routes one valid envelope from child `generation` (DESIGN-bridge B6 frame classes).
    pub(super) fn route(&self, generation: u64, envelope: Envelope) {
        let mut inner = self.lock();
        let (id, reply) = match envelope {
            Envelope::Error { id: None, error } => {
                log::error!("bridge could not parse a request frame: {error}");
                let failure = || BridgeFailure::Protocol {
                    reason: "the bridge could not parse a request frame".into(),
                    details: None,
                };
                fail_pending(&mut inner, Some(generation), failure);
                return;
            }
            Envelope::Result { id, result } => (id, Ok(result)),
            Envelope::Error {
                id: Some(id),
                error,
            } => (id, Err(error)),
        };
        let pending = inner
            .pending
            .remove(&id)
            .filter(|p| p.generation == generation);
        let Some(pending) = pending else {
            let method = inner
                .timed_out
                .iter()
                .find(|(t, _)| *t == id)
                .map(|(_, m)| m.as_str());
            if let Some(method) = method {
                if let Err(error) = &reply {
                    log::warn!("bridge late error wire_id={id} method={method}: {error}; dropped");
                } else {
                    log::debug!("bridge late reply wire_id={id} method={method}; dropped");
                }
            } else {
                log::warn!("bridge reply for unknown wire_id={id}; dropped");
            }
            return;
        };
        drop(inner);
        let reply = reply.map_err(|error| match decode_payload::<ErrorPayload>(error) {
            Ok(p) => {
                let mut details = p.details.unwrap_or_default();
                details.insert("method".into(), Value::from(pending.method.clone()));
                details.insert("wire_id".into(), Value::from(id));
                details.insert("generation".into(), Value::from(generation));
                if let Some(token) = pending.request_id.as_ref() {
                    details.insert("request_id".into(), Value::from(token.clone()));
                }
                BridgeFailure::Python {
                    code: p.code,
                    message: p.message,
                    retryable: p.retryable,
                    details: Some(details),
                }
            }
            Err(reason) => {
                log::error!("bridge {}: error frame rejected: {reason}", pending.method);
                payload_mismatch(&pending.method, reason)
            }
        });
        let _ = pending.tx.send(reply);
    }
}

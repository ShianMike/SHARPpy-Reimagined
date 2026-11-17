//! Bounded download/decode work for one group of requested fields.
use super::*;

pub(super) fn load(
    py: Python<'_>,
    fields: &[plan::Field],
    identities: &HashMap<String, Option<ObjectIdentity>>,
    timeout: f64,
    should_cancel: Option<&Py<PyAny>>,
    model: &str,
) -> PyResult<Vec<Arc<Grid>>> {
    let api = PyModule::import(py, "sharpmod.providers.hrrr_field")?;
    let decoder = api.getattr("decode_message")?.unbind();
    let workers: usize = api.getattr("_RANGE_WORKERS")?.extract()?;
    let runtime = crate::weather::runtime()?;
    py.detach(|| -> Result<Vec<Arc<Grid>>, Error> {
        let mut retried = false;
        loop {
            let tickets: Vec<Arc<FieldTicket>> = fields
                .iter()
                .map(|field| {
                    let key = identities[&field.url].clone().map(|identity| Key {
                        url: field.url.clone(),
                        identity,
                        offset: field.offset,
                        length: field.length,
                        subfield: field.subfield,
                        model: model.to_string(),
                    });
                    let ticket = cache().reserve(key);
                    if !ticket.leader {
                        SHARED.fetch_add(1, Ordering::Relaxed);
                    }
                    Arc::new(ticket)
                })
                .collect();
            let cancelled = || {
                let stopped = cancelled(should_cancel)?;
                Ok(stopped
                    && !tickets
                        .iter()
                        .any(|ticket| ticket.leader && ticket.shared()))
            };
            let mut jobs = Vec::new();
            for (url, identity) in identities {
                let selected: Vec<_> = fields
                    .iter()
                    .enumerate()
                    .filter(|(index, field)| field.url == *url && tickets[*index].leader)
                    .collect();
                let spans: Vec<_> = selected
                    .iter()
                    .map(|(_, field)| (field.offset, field.length))
                    .collect();
                let gap = sharpmod_weather::transfer_policy::shared().range_gap(url, 65536);
                for group in
                    map_fields::merge_map_ranges_bounded(&spans, gap as i64, 64 * 1024 * 1024)?
                {
                    let end = if group.end == 0 {
                        group.start.saturating_add(8 * 1024 * 1024)
                    } else {
                        group.end
                    };
                    let length = end
                        .checked_sub(group.start)
                        .and_then(|n| n.checked_add(1))
                        .and_then(|n| usize::try_from(n).ok())
                        .ok_or_else(|| Error::Invalid("map field range is out of bounds".into()))?;
                    jobs.push(Job {
                        request: FieldJob {
                            url: url.clone(),
                            limit: length,
                            range: Some((group.start, end)),
                        },
                        identity: identity.clone(),
                        members: group
                            .members
                            .into_iter()
                            .map(|index| selected[index].0)
                            .collect(),
                    });
                }
            }
            std::thread::scope(|scope| -> Result<(), Error> {
                let (sender, mut receiver) = tokio::sync::mpsc::channel::<(usize, Vec<u8>)>(1);
                let (jobs, fields, tickets, model, decoder, cancelled) =
                    (&jobs, &fields, &tickets, &model, &decoder, &cancelled);
                let decode = scope.spawn(move || -> Result<(), String> {
                    while let Some((job_index, bytes)) = receiver.blocking_recv() {
                        if cancelled()? {
                            return Err("field download cancelled".into());
                        }
                        let job = &jobs[job_index];
                        let start = job.request.range.unwrap().0;
                        for &index in &job.members {
                            let field = &fields[index];
                            let begin = usize::try_from(field.offset - start)
                                .map_err(|_| "field offset is out of bounds")?;
                            let end = if field.length == 0 {
                                bytes.len()
                            } else {
                                begin
                                    .checked_add(field.length as usize)
                                    .ok_or("field length is out of bounds")?
                            };
                            let payload = bytes
                                .get(begin..end)
                                .ok_or("model returned a truncated field")?;
                            let started = std::time::Instant::now();
                            #[cfg(test)]
                            DECODING.fetch_add(1, Ordering::Relaxed);
                            let grid = Python::attach(|py| -> PyResult<Grid> {
                                let kwargs = PyDict::new(py);
                                kwargs.set_item("model", model)?;
                                kwargs.set_item("field_index", field.subfield)?;
                                let value = decoder
                                    .bind(py)
                                    .call((PyBytes::new(py, payload),), Some(&kwargs))?;
                                let array = value.cast::<PyArray2<f32>>()?;
                                let shape = (array.shape()[0], array.shape()[1]);
                                Ok(Grid {
                                    shape,
                                    values: array.readonly().as_array().iter().copied().collect(),
                                })
                            })
                            .map_err(|error| error.to_string());
                            #[cfg(test)]
                            DECODING.fetch_sub(1, Ordering::Relaxed);
                            match grid {
                                Ok(grid) => {
                                    sharpmod_weather::transfer_policy::shared()
                                        .observe_decode(payload.len() as u64, started.elapsed());
                                    DECODED.fetch_add(1, Ordering::Relaxed);
                                    let size = grid.values.len() * size_of::<f32>();
                                    tickets[index].complete(Ok(grid), size);
                                }
                                Err(error) => {
                                    tickets[index].complete(Err(error.clone()), 0);
                                    return Err(error);
                                }
                            }
                        }
                    }
                    Ok(())
                });
                let requests = jobs
                    .iter()
                    .map(|job| (job.request.clone(), job.identity.clone()))
                    .collect();
                let transfer = runtime.block_on(map_fields::visit_model_fields(
                    ModelClient::shared(),
                    requests,
                    timeout,
                    workers,
                    &cancelled,
                    |index, bytes| {
                        let sender = sender.clone();
                        async move {
                            sender
                                .send((index, bytes))
                                .await
                                .map_err(|_| Error::Invalid("map decoder stopped".into()))
                        }
                    },
                ));
                drop(sender);
                let decoded = decode
                    .join()
                    .map_err(|_| Error::Invalid("map decoder thread stopped".into()))?;
                if crate::model_http_bindings::cancelled(should_cancel).map_err(Error::Invalid)? {
                    return Err(Error::Cancelled);
                }
                // A failed consumer closes the channel. Retain its real decode
                // error instead of the sender's secondary "decoder stopped".
                decoded.map_err(Error::Invalid)?;
                transfer
            })?;
            let result: Result<Vec<Arc<Grid>>, WaitError> = tickets
                .iter()
                .map(|ticket| ticket.wait(|| crate::model_http_bindings::cancelled(should_cancel)))
                .collect();
            match result {
                Ok(output) => return Ok(output),
                // A consumer may join immediately after its producer decided it
                // had no remaining users. Retry that abandoned flight once.
                Err(WaitError::Abandoned) if !retried => {
                    retried = true;
                    continue;
                }
                Err(WaitError::Cancelled) => return Err(Error::Cancelled),
                Err(WaitError::Abandoned) => {
                    return Err(Error::Invalid("shared field producer stopped".into()))
                }
                Err(WaitError::Failed(message)) => return Err(Error::Invalid(message)),
            }
        }
    })
    .map_err(|error| field_error(py, error))
}

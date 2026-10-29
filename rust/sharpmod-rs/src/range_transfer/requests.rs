use std::{
    fs::{self, OpenOptions},
    io::Write,
    sync::atomic::{AtomicU64, Ordering},
    time::{Duration, Instant},
};

use super::{
    disk::{io_error, Identity},
    Body, ByteRange, Error, Response, Transfer,
};

struct ClosingBody(Box<dyn Body>);
impl Drop for ClosingBody {
    fn drop(&mut self) {
        self.0.close();
    }
}

fn content_range(value: Option<&String>) -> Option<(u64, u64, u64)> {
    let (unit, value) = value?.split_once(' ')?;
    if !unit.eq_ignore_ascii_case("bytes") {
        return None;
    }
    let (span, total) = value.split_once('/')?;
    let (start, end) = span.split_once('-')?;
    let (start, end, total) = (start.parse().ok()?, end.parse().ok()?, total.parse().ok()?);
    (start <= end && end < total).then_some((start, end, total))
}

fn retry_after(response: &Response) -> f64 {
    response
        .headers
        .get("retry-after")
        .and_then(|value| value.parse::<f64>().ok())
        .filter(|value| value.is_finite())
        .unwrap_or(0.0)
        .clamp(0.0, 30.0)
}

fn response(
    ctx: &Transfer<'_>,
    worker: usize,
    range: ByteRange,
    if_range: Option<&str>,
    chunk_size: usize,
) -> Result<Response, Error> {
    let mut headers = vec![(
        "Range".into(),
        format!("bytes={}-{}", range.start, range.end),
    )];
    if let Some(value) = if_range {
        headers.push(("If-Range".into(), value.into()));
    }
    ctx.state.lock().unwrap().metrics.request_count += 1;
    ctx.transport.get(worker, ctx.url, &headers, chunk_size)
}

fn retry<T>(
    ctx: &Transfer<'_>,
    kind: &str,
    mut operation: impl FnMut() -> Result<T, Error>,
) -> Result<T, Error> {
    for attempt in 0..=ctx.options.retries {
        ctx.check()?;
        let (message, after) = match operation() {
            Ok(value) => return Ok(value),
            Err(Error::Transient(message, after)) => (message, after),
            Err(error) => return Err(error),
        };
        if ctx.stop.load(Ordering::Acquire) {
            return Err(Error::Cancelled);
        }
        if attempt == ctx.options.retries {
            return Err(Error::Unavailable(format!(
                "optimized HTTP {kind} failed: {message}"
            )));
        }
        ctx.state.lock().unwrap().metrics.retry_count += 1;
        retry_wait(ctx, attempt, after)?;
    }
    unreachable!()
}

fn retry_wait(ctx: &Transfer<'_>, attempt: usize, after: f64) -> Result<(), Error> {
    static JITTER: AtomicU64 = AtomicU64::new(0);
    let seed = JITTER
        .fetch_add(1, Ordering::Relaxed)
        .wrapping_mul(6364136223846793005)
        .wrapping_add(1442695040888963407);
    let jitter = 0.75 + ((seed >> 32) as f64 / u32::MAX as f64) * 0.5;
    let base = ctx.options.retry_backoff.max(0.0) * 2.0_f64.powi(attempt.min(1024) as i32);
    let delay = (base * jitter).max(after).clamp(0.0, 30.0);
    let deadline = Instant::now() + Duration::from_secs_f64(delay);
    loop {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Ok(());
        }
        ctx.check()?;
        std::thread::sleep(remaining.min(Duration::from_millis(50)));
    }
}

pub(super) fn probe(ctx: &Transfer<'_>) -> Result<Identity, Error> {
    retry(ctx, "identity probe", || {
        let response = response(ctx, 0, ByteRange { start: 0, end: 0 }, None, 1)?;
        let after = retry_after(&response);
        let mut body = ClosingBody(response.body);
        if response.status == 429 || (500..=599).contains(&response.status) {
            return Err(Error::Transient(
                format!("source identity probe returned HTTP {}", response.status),
                after,
            ));
        }
        if response.status != 206 {
            return Err(Error::Unavailable(
                "source did not honor the HTTP identity range request".into(),
            ));
        }
        let Some((0, 0, total)) = content_range(response.headers.get("content-range")) else {
            return Err(Error::Unavailable(
                "source returned a mismatched identity range".into(),
            ));
        };
        let mut received = 0u64;
        while let Some(chunk) = body.0.next()? {
            received += chunk.len() as u64;
            ctx.state.lock().unwrap().metrics.transferred_bytes += chunk.len() as u64;
            if received > 1 {
                break;
            }
        }
        if received != 1 {
            return Err(Error::Unavailable(
                "source returned an incomplete identity range".into(),
            ));
        }
        Ok(Identity::response(&response.headers, total))
    })
}

pub(super) fn fragment(ctx: &Transfer<'_>, index: usize, worker: usize) -> Result<(), Error> {
    let item = ctx.plan[index];
    let path = ctx.store.part(item);
    retry(ctx, "range transfer", || {
        let mut existing = ctx.store.size(item)?;
        if existing == item.size() {
            return ctx.progress(index, existing, 0);
        }
        let identity = ctx.store.identity();
        let conditional = identity.if_range();
        let response = response(
            ctx,
            worker,
            ByteRange {
                start: item.start + existing,
                end: item.end,
            },
            conditional,
            ctx.options.chunk_size.max(1),
        )?;
        let after = retry_after(&response);
        let mut body = ClosingBody(response.body);
        if response.status == 429 || (500..=599).contains(&response.status) {
            return Err(Error::Transient(
                format!("range request returned HTTP {}", response.status),
                after,
            ));
        }
        if response.status != 206 {
            if response.status == 200 && conditional.is_some() {
                return Err(Error::ObjectChanged(
                    "source changed: no longer matches the pinned object".into(),
                ));
            }
            return Err(Error::Unavailable(
                "source did not honor the HTTP range request".into(),
            ));
        }
        let details = content_range(response.headers.get("content-range"));
        let Some((start, end, total)) =
            details.filter(|&(start, end, _)| start == item.start + existing && end == item.end)
        else {
            return Err(Error::Unavailable(
                "source returned a mismatched HTTP range".into(),
            ));
        };
        debug_assert_eq!((start, end), (item.start + existing, item.end));
        ctx.store.pin(&response.headers, total)?;
        let mut file = OpenOptions::new()
            .create(true)
            .write(true)
            .append(existing > 0)
            .truncate(existing == 0)
            .open(&path)
            .map_err(io_error)?;
        while let Some(chunk) = body.0.next()? {
            if chunk.is_empty() {
                continue;
            }
            let count = chunk.len() as u64;
            ctx.state.lock().unwrap().metrics.transferred_bytes += count;
            if count > item.size() - existing {
                drop(file);
                fs::remove_file(&path).map_err(io_error)?;
                ctx.progress(index, 0, count)?;
                return Err(Error::Unavailable(
                    "source returned more bytes than requested".into(),
                ));
            }
            file.write_all(&chunk).map_err(io_error)?;
            existing += count;
            ctx.progress(index, existing, count)?;
            ctx.check()?;
        }
        file.flush().map_err(io_error)?;
        if existing == item.size() {
            Ok(())
        } else {
            Err(Error::Transient(
                "source returned an incomplete HTTP range".into(),
                0.0,
            ))
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    struct Empty;
    impl Body for Empty {
        fn next(&mut self) -> Result<Option<Vec<u8>>, Error> {
            Ok(None)
        }
    }

    #[test]
    fn numeric_retry_after_is_clamped_without_accepting_http_dates() {
        for (value, expected) in [
            ("0", 0.0),
            ("1.25", 1.25),
            ("-2", 0.0),
            ("999", 30.0),
            ("NaN", 0.0),
            ("Wed, 22 Jul 2026 00:00:00 GMT", 0.0),
        ] {
            let response = Response {
                status: 429,
                headers: HashMap::from([("retry-after".into(), value.into())]),
                body: Box::new(Empty),
            };
            assert_eq!(retry_after(&response), expected, "{value}");
        }
    }
}

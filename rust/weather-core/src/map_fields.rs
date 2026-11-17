//! Inventory and byte-range planning for native map-field transfers.

use crate::model_http::{Error, ModelClient, ObjectIdentity};
use std::{collections::HashMap, future::poll_fn, future::Future, task::Poll};

#[derive(Debug, PartialEq, Eq)]
pub struct InventoryRow {
    pub index: i64,
    pub offset: u64,
    pub length: u64,
    pub variable: String,
    pub level: String,
    pub forecast: String,
    pub field_index: usize,
}

/// Keep wgrib2 record order, including subfields sharing a message offset.
pub fn parse_wgrib_inventory(text: &str) -> Vec<InventoryRow> {
    let mut rows = Vec::new();
    let mut offsets = Vec::new();
    let mut lengths = HashMap::new();
    for line in text.lines() {
        let mut parts = line.trim().split(':');
        let (Some(number), Some(offset), Some(_), Some(variable), Some(level), Some(forecast)) = (
            parts.next(),
            parts.next(),
            parts.next(),
            parts.next(),
            parts.next(),
            parts.next(),
        ) else {
            continue;
        };
        if !offset.bytes().all(|byte| byte.is_ascii_digit()) {
            continue;
        }
        let (number, field) = number.split_once('.').unwrap_or((number, ""));
        let (Ok(index), Ok(offset)) = (number.trim().parse(), offset.parse::<u64>()) else {
            continue;
        };
        let field_index = if field.is_empty() {
            0
        } else if let Some(index) = field
            .trim()
            .parse::<usize>()
            .ok()
            .and_then(|index| index.checked_sub(1))
        {
            index
        } else {
            continue;
        };
        if let std::collections::hash_map::Entry::Vacant(entry) = lengths.entry(offset) {
            entry.insert(0);
            offsets.push(offset);
        }
        rows.push(InventoryRow {
            index,
            offset,
            length: 0,
            variable: variable.into(),
            level: level.into(),
            forecast: forecast.into(),
            field_index,
        });
    }
    for pair in offsets.windows(2) {
        lengths.insert(pair[0], pair[1].saturating_sub(pair[0]));
    }
    for row in &mut rows {
        row.length = lengths[&row.offset];
    }
    rows
}

#[derive(Debug, PartialEq, Eq)]
pub struct RangeGroup {
    pub start: u64,
    /// Zero retains the provider's open-ended, final-message sentinel.
    pub end: u64,
    pub members: Vec<usize>,
}

/// Coalesce overlapping and nearby records without losing their caller indices.
pub fn merge_map_ranges(rows: &[(u64, u64)], slack: i64) -> Result<Vec<RangeGroup>, Error> {
    merge_map_ranges_bounded(rows, slack, u64::MAX)
}

/// Keep complete messages while bounding each coalesced response in memory.
pub fn merge_map_ranges_bounded(
    rows: &[(u64, u64)],
    slack: i64,
    maximum: u64,
) -> Result<Vec<RangeGroup>, Error> {
    let mut ordered: Vec<_> = rows.iter().enumerate().collect();
    ordered.sort_by_key(|(_, (offset, _))| *offset);
    let mut groups: Vec<RangeGroup> = Vec::new();
    for (index, &(offset, length)) in ordered {
        if length > maximum {
            return Err(Error::Budget);
        }
        let end = if length == 0 {
            0
        } else {
            offset
                .checked_add(length - 1)
                .ok_or_else(|| Error::Invalid("model byte range is out of bounds".into()))?
        };
        if let Some(previous) = groups.last_mut() {
            if end != 0
                && previous.end != 0
                && i128::from(offset) - i128::from(previous.end) - 1 <= i128::from(slack)
                && end.max(previous.end) - previous.start < maximum
            {
                previous.end = previous.end.max(end);
                previous.members.push(index);
                continue;
            }
        }
        groups.push(RangeGroup {
            start: offset,
            end,
            members: vec![index],
        });
    }
    Ok(groups)
}

#[derive(Clone)]
pub struct FieldJob {
    pub url: String,
    pub limit: usize,
    pub range: Option<(u64, u64)>,
}

/// Transfer a bounded batch through the existing pooled, validated HTTP client.
pub async fn fetch_model_fields(
    client: &ModelClient,
    jobs: Vec<FieldJob>,
    timeout: f64,
    workers: usize,
    should_cancel: impl Fn() -> Result<bool, String>,
) -> Result<Vec<Vec<u8>>, Error> {
    let mut output = vec![Vec::new(); jobs.len()];
    visit_model_fields(
        client,
        jobs.into_iter().map(|job| (job, None)).collect(),
        timeout,
        workers,
        should_cancel,
        |index, bytes| {
            output[index] = bytes;
            std::future::ready(Ok(()))
        },
    )
    .await?;
    Ok(output)
}

/// Emit complete validated ranges as soon as they arrive. The consumer's
/// bounded channel supplies backpressure without retaining every payload.
pub async fn visit_model_fields<F, Fut>(
    client: &ModelClient,
    jobs: Vec<(FieldJob, Option<ObjectIdentity>)>,
    timeout: f64,
    workers: usize,
    should_cancel: impl Fn() -> Result<bool, String>,
    mut complete: F,
) -> Result<(), Error>
where
    F: FnMut(usize, Vec<u8>) -> Fut,
    Fut: Future<Output = Result<(), Error>>,
{
    if should_cancel().map_err(Error::Invalid)? {
        return Err(Error::Cancelled);
    }
    let started = std::time::Instant::now();
    let bytes = jobs
        .iter()
        .map(|(job, _)| {
            job.range.map_or(job.limit as u64, |(start, end)| {
                end.saturating_sub(start).saturating_add(1)
            })
        })
        .sum::<u64>();
    let first_url = jobs.first().map(|(job, _)| job.url.clone());
    let workers = workers.clamp(1, 8).min(jobs.len().max(1));
    let workers = first_url.as_ref().map_or(workers, |url| {
        crate::transfer_policy::shared().workers(url, bytes, workers)
    });
    let cancel = &should_cancel;
    let mut pending = jobs
        .into_iter()
        .enumerate()
        .map(|(index, (job, identity))| {
            Box::pin(async move {
                let result = client
                    .fetch_version(
                        &job.url,
                        timeout,
                        job.limit,
                        job.range,
                        identity.as_ref(),
                        cancel,
                    )
                    .await;
                (index, result)
            })
        });
    let workers = workers.clamp(1, 8);
    let mut active = Vec::with_capacity(workers);
    loop {
        while active.len() < workers {
            let Some(job) = pending.next() else { break };
            active.push(job);
        }
        if active.is_empty() {
            if let Some(url) = &first_url {
                crate::transfer_policy::shared().observe(url, bytes, started.elapsed(), workers);
            }
            return Ok(());
        }
        let (position, (index, result)) = poll_fn(|context| {
            for (position, job) in active.iter_mut().enumerate() {
                if let Poll::Ready(result) = job.as_mut().poll(context) {
                    return Poll::Ready((position, result));
                }
            }
            Poll::Pending
        })
        .await;
        drop(active.swap_remove(position));
        // Returning on the first error drops all in-flight request futures and
        // leaves queued jobs unstarted; detached transfers cannot outlive a batch.
        complete(index, result?).await?;
    }
}

#[cfg(test)]
mod tests;

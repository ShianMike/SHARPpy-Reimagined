//! Inventory and byte-range planning for native map-field transfers.

use crate::model_http::{Error, ModelClient};
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
    let mut ordered: Vec<_> = rows.iter().enumerate().collect();
    ordered.sort_by_key(|(_, (offset, _))| *offset);
    let mut groups: Vec<RangeGroup> = Vec::new();
    for (index, &(offset, length)) in ordered {
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
    if should_cancel().map_err(Error::Invalid)? {
        return Err(Error::Cancelled);
    }
    let mut output = vec![Vec::new(); jobs.len()];
    let cancel = &should_cancel;
    let mut pending = jobs.into_iter().enumerate().map(|(index, job)| {
        Box::pin(async move {
            let result = client
                .fetch(&job.url, timeout, job.limit, job.range, cancel)
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
            return Ok(output);
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
        output[index] = result?;
    }
}

#[cfg(test)]
mod tests;

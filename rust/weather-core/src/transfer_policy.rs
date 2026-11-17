//! Learn only from completed transfers; keep request caps and byte budgets intact.
use reqwest::Url;
use std::{
    collections::HashMap,
    sync::{Mutex, OnceLock},
    time::Duration,
};

#[derive(Clone, Default)]
struct Sample {
    count: usize,
    seconds_per_byte: f64,
}
#[derive(Default)]
struct History {
    calls: usize,
    workers: HashMap<usize, Sample>,
}
#[derive(Default)]
pub struct Policy {
    histories: Mutex<HashMap<(String, u8), History>>,
    decode: Mutex<Option<Sample>>,
    latency: Mutex<HashMap<String, (usize, f64)>>,
}

fn bucket(bytes: u64) -> u8 {
    if bytes < 1024 * 1024 {
        0
    } else if bytes < 16 * 1024 * 1024 {
        1
    } else {
        2
    }
}
fn host(url: &str) -> Option<String> {
    let parsed = Url::parse(url).ok()?;
    Some(format!(
        "{}://{}:{}",
        parsed.scheme(),
        parsed.host_str()?,
        parsed.port_or_known_default()?
    ))
}

impl Policy {
    pub fn observe_latency(&self, url: &str, elapsed: Duration) {
        let Some(host) = host(url) else { return };
        let mut latency = self.latency.lock().unwrap();
        if !latency.contains_key(&host) && latency.len() >= 64 {
            latency.clear();
        }
        let value = latency.entry(host).or_default();
        value.1 = if value.0 == 0 {
            elapsed.as_secs_f64()
        } else {
            0.75 * value.1 + 0.25 * elapsed.as_secs_f64()
        };
        value.0 = value.0.saturating_add(1);
    }

    pub fn range_gap(&self, url: &str, ceiling: u64) -> u64 {
        let Some(host) = host(url) else {
            return ceiling;
        };
        let latency = self
            .latency
            .lock()
            .unwrap()
            .get(&host)
            .filter(|value| value.0 >= 2)
            .map(|value| value.1);
        let histories = self.histories.lock().unwrap();
        let rate = histories
            .iter()
            .filter(|((source, _), _)| source == &host)
            .flat_map(|(_, history)| history.workers.values())
            .filter(|sample| sample.count >= 2)
            .map(|sample| sample.seconds_per_byte)
            .min_by(f64::total_cmp);
        match (latency, rate) {
            (Some(latency), Some(rate)) if rate > 0. => {
                ((latency / rate) as u64).clamp(ceiling.min(16384), ceiling)
            }
            _ => ceiling,
        }
    }
    pub fn observe_decode(&self, bytes: u64, elapsed: Duration) {
        if bytes < 65536 || elapsed.is_zero() {
            return;
        }
        let mut decode = self.decode.lock().unwrap();
        let sample = decode.get_or_insert_with(Sample::default);
        let rate = elapsed.as_secs_f64() / bytes as f64;
        sample.seconds_per_byte = if sample.count == 0 {
            rate
        } else {
            0.75 * sample.seconds_per_byte + 0.25 * rate
        };
        sample.count = sample.count.saturating_add(1);
    }

    pub fn decode_seconds(&self, bytes: u64) -> Option<f64> {
        self.decode
            .lock()
            .unwrap()
            .as_ref()
            .filter(|sample| sample.count >= 2)
            .map(|sample| sample.seconds_per_byte * bytes as f64)
    }
    pub fn observe(&self, url: &str, bytes: u64, elapsed: Duration, workers: usize) {
        if bytes < 65536 || elapsed.is_zero() || !(1..=8).contains(&workers) {
            return;
        }
        let Some(host) = host(url) else { return };
        let mut histories = self.histories.lock().unwrap();
        let key = (host, bucket(bytes));
        if !histories.contains_key(&key) && histories.len() >= 192 {
            histories.clear();
        }
        let sample = histories
            .entry(key)
            .or_default()
            .workers
            .entry(workers)
            .or_default();
        let rate = elapsed.as_secs_f64() / bytes as f64;
        sample.seconds_per_byte = if sample.count == 0 {
            rate
        } else {
            0.75 * sample.seconds_per_byte + 0.25 * rate
        };
        sample.count = sample.count.saturating_add(1);
    }

    pub fn seconds(&self, url: &str, bytes: u64) -> Option<f64> {
        let key = (host(url)?, bucket(bytes));
        let histories = self.histories.lock().unwrap();
        histories
            .get(&key)?
            .workers
            .values()
            .filter(|sample| sample.count >= 2)
            .map(|sample| bytes as f64 * sample.seconds_per_byte)
            .min_by(f64::total_cmp)
    }

    pub fn workers(&self, url: &str, bytes: u64, ceiling: usize) -> usize {
        let ceiling = ceiling.clamp(1, 8);
        if bytes < 65536 {
            return ceiling;
        }
        let Some(host) = host(url) else {
            return ceiling;
        };
        let mut histories = self.histories.lock().unwrap();
        if !histories.contains_key(&(host.clone(), bucket(bytes))) && histories.len() >= 192 {
            histories.clear();
        }
        let history = histories.entry((host, bucket(bytes))).or_default();
        history.calls = history.calls.wrapping_add(1);
        let candidates: Vec<_> = [ceiling, (ceiling / 2).max(1), 1].into_iter().collect();
        // Start with the established cap. Calibrate fewer workers only after two
        // actual samples; never race duplicate downloads just to benchmark.
        if history
            .workers
            .get(&ceiling)
            .is_none_or(|sample| sample.count < 2)
        {
            return ceiling;
        }
        let best = candidates
            .iter()
            .filter(|candidate| {
                history
                    .workers
                    .get(candidate)
                    .is_some_and(|sample| sample.count >= 2)
            })
            .min_by(|a, b| {
                history.workers[a]
                    .seconds_per_byte
                    .total_cmp(&history.workers[b].seconds_per_byte)
            })
            .copied()
            .unwrap_or(ceiling);
        if history.calls.is_multiple_of(16) {
            candidates[(history.calls / 16) % candidates.len()]
        } else {
            best
        }
    }
}

pub fn shared() -> &'static Policy {
    static POLICY: OnceLock<Policy> = OnceLock::new();
    POLICY.get_or_init(Policy::default)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn range_coalescing_uses_measured_latency_without_exceeding_the_caller_budget() {
        let policy = Policy::default();
        let url = "https://slow.example/a";
        assert_eq!(policy.range_gap(url, 2 * 1024 * 1024), 2 * 1024 * 1024);
        for _ in 0..2 {
            policy.observe(url, 1024 * 1024, Duration::from_secs(1), 1);
            policy.observe_latency(url, Duration::from_millis(50));
        }
        let gap = policy.range_gap(url, 2 * 1024 * 1024);
        assert!((50000..55000).contains(&gap));
        assert_eq!(policy.range_gap(url, 16384), 16384);
        assert_eq!(policy.range_gap(url, 0), 0);
    }
    #[test]
    fn learns_complete_transfers_and_respects_caps_sizes_and_hosts() {
        let policy = Policy::default();
        let url = "https://aws.example/model.grib2";
        assert_eq!(policy.workers(url, 8 * 1024 * 1024, 4), 4);
        for workers in [4, 2, 1] {
            for _ in 0..2 {
                policy.observe(
                    url,
                    8 * 1024 * 1024,
                    Duration::from_secs(workers as u64),
                    workers,
                );
            }
        }
        assert_eq!(policy.workers(url, 8 * 1024 * 1024, 4), 1);
        assert!(policy.seconds(url, 8 * 1024 * 1024).unwrap() < 1.1);
        assert!(policy
            .seconds("https://other.example/model.grib2", 8 * 1024 * 1024)
            .is_none());
        assert_eq!(policy.workers(url, 4096, 8), 8);
        assert_eq!(policy.workers(url, 8 * 1024 * 1024, 1), 1);
        assert_eq!(policy.workers(url, 64 * 1024 * 1024, 4), 4);
    }
}

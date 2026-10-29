//! Exact-object mirror selection and bounded, revalidated routing decisions.

use std::{collections::HashMap, sync::Mutex};

#[derive(Clone, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub(crate) struct Source {
    pub name: String,
    pub url: String,
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct Probe {
    pub size: u64,
    pub elapsed: f64,
}

pub(crate) struct Request {
    pub model: String,
    pub product: String,
    pub sources: Vec<Source>,
    pub reference: Option<String>,
    pub ttl: f64,
}

type Key = (String, String, Vec<Source>);

#[derive(Clone)]
struct Cached {
    expires: f64,
    name: String,
    size: u64,
}

#[derive(Default)]
pub(crate) struct Router {
    cache: Mutex<HashMap<Key, Cached>>,
}

// The object boundary has already required at least two eligible sources.
pub(crate) fn choose(
    sources: &[Source],
    probes: &[Option<Probe>],
    reference: Option<&str>,
) -> usize {
    let first = sources
        .iter()
        .position(|source| Some(source.url.as_str()) == reference)
        .unwrap_or(0);
    let expected = probes[first].filter(|probe| probe.size > 0).map(|p| p.size);
    let expected = expected.or_else(|| {
        let mut counts = HashMap::<u64, usize>::new();
        for probe in probes.iter().flatten().filter(|probe| probe.size > 0) {
            *counts.entry(probe.size).or_default() += 1;
        }
        counts
            .into_iter()
            .max_by_key(|&(size, count)| (count, size))
            .map(|(size, _)| size)
    });
    let mut best: Option<(usize, f64)> = None;
    for (index, probe) in probes.iter().enumerate() {
        let Some(probe) = probe.filter(|probe| Some(probe.size) == expected) else {
            continue;
        };
        if best.is_none_or(|(current, elapsed)| {
            probe.elapsed < elapsed
                || (probe.elapsed == elapsed && sources[index].name < sources[current].name)
        }) {
            best = Some((index, probe.elapsed));
        }
    }
    best.map_or(first, |(index, _)| index)
}

impl Router {
    pub fn select(
        &self,
        request: &Request,
        now: f64,
        probe: impl Fn(&[Source]) -> Vec<Option<Probe>>,
    ) -> usize {
        let mut identity = request.sources.clone();
        identity.sort();
        let key = (request.model.clone(), request.product.clone(), identity);
        let cached = {
            let mut cache = self.cache.lock().unwrap();
            cache.retain(|_, entry| entry.expires > now);
            cache.get(&key).cloned()
        };
        if let Some(cached) = cached {
            let index = request
                .sources
                .iter()
                .position(|source| source.name == cached.name)
                .unwrap();
            let result = probe(&request.sources[index..index + 1]);
            if result[0].is_some_and(|probe| probe.size == cached.size) {
                return index;
            }
        }
        let probes = probe(&request.sources);
        let selected = choose(&request.sources, &probes, request.reference.as_deref());
        let mut cache = self.cache.lock().unwrap();
        cache.insert(
            key,
            Cached {
                expires: now + request.ttl.max(0.0),
                name: request.sources[selected].name.clone(),
                size: probes[selected].map_or(0, |probe| probe.size),
            },
        );
        if cache.len() > 256 {
            let oldest = cache
                .iter()
                .min_by(|(_, a), (_, b)| a.expires.total_cmp(&b.expires))
                .map(|(key, _)| key.clone())
                .unwrap();
            cache.remove(&oldest);
        }
        selected
    }
}

#[cfg(test)]
mod tests;

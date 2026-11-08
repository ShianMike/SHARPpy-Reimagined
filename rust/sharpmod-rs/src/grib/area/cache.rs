//! Bounded final-profile reuse keyed by current compressed bytes and scientific choices.

use super::*;

#[derive(Clone, Debug, Eq, PartialEq)]
pub(super) struct Key {
    content: Fingerprint,
    bounds: [u64; 4],
    columns: usize,
    area: u64,
    coverage: u64,
    generation: Option<u64>,
    method: &'static str,
}

impl Key {
    pub(super) fn new(
        content: Fingerprint,
        bounds: Bounds,
        limits: Limits,
        generation: Option<u64>,
    ) -> Self {
        Self {
            content,
            bounds: [bounds.west, bounds.south, bounds.east, bounds.north].map(f64::to_bits),
            columns: limits.max_columns,
            area: limits.max_area_km2.to_bits(),
            coverage: limits.minimum_coverage.to_bits(),
            generation,
            method: crate::box_soundings::METHOD_VERSION,
        }
    }
}

static RESULTS: OnceLock<Mutex<VecDeque<(Key, Output)>>> = OnceLock::new();
const MAX_RESULTS: usize = 16;

pub(super) fn get(key: &Key) -> Option<Output> {
    let generation = inventory_cache();
    if !generation.enabled || key.generation != Some(generation.generation) {
        return None;
    }
    let mut entries = RESULTS
        .get_or_init(Mutex::default)
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let index = entries.iter().position(|(candidate, _)| candidate == key)?;
    let entry = entries.remove(index).expect("cached entry exists");
    let output = entry.1.clone();
    entries.push_back(entry);
    Some(output)
}

pub(super) fn insert(key: Key, output: Output) {
    let generation = inventory_cache();
    if !generation.enabled || key.generation != Some(generation.generation) {
        return;
    }
    let mut entries = RESULTS
        .get_or_init(Mutex::default)
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    entries.retain(|(candidate, _)| candidate != &key);
    entries.push_back((key, output));
    while entries.len() > MAX_RESULTS {
        entries.pop_front();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn source_geometry_science_limits_and_cache_clear_change_identity() {
        let bounds = Bounds::new(-98.0, 35.0, -97.0, 36.0).unwrap();
        let limits = Limits::default();
        let key = Key::new([1; 32], bounds, limits, Some(1));
        assert_ne!(key, Key::new([2; 32], bounds, limits, Some(1)));
        assert_ne!(
            key,
            Key::new(
                [1; 32],
                Bounds {
                    west: -98.01,
                    ..bounds
                },
                limits,
                Some(1)
            )
        );
        assert_ne!(
            key,
            Key::new(
                [1; 32],
                bounds,
                Limits {
                    minimum_coverage: 0.9,
                    ..limits
                },
                Some(1)
            )
        );
        assert_ne!(key, Key::new([1; 32], bounds, limits, Some(2)));
    }
}

//! Exact repeat point results. Key by the current compressed bytes, never by
//! modification time alone, and keep only the small final column matrices.
use super::{DecodedPoint, COLUMN_COUNT};
use std::collections::VecDeque;

pub(super) const MIN_FILE_BYTES: usize = 64 * 1024 * 1024;
const MAX_POINTS: usize = 16;
const MAX_VALUES: usize = COLUMN_COUNT * 512;

pub(super) type Fingerprint = [u8; 32];

pub(super) fn fingerprint(bytes: &[u8]) -> Fingerprint {
    ring::digest::digest(&ring::digest::SHA256, bytes)
        .as_ref()
        .try_into()
        .expect("SHA-256 has 32 bytes")
}

#[derive(Debug, Eq, PartialEq)]
pub(super) struct Key {
    content: Fingerprint,
    latitude: u64,
    longitude: u64,
    missing: Option<u64>,
}

impl Key {
    pub(super) fn new(content: Fingerprint, point: (f64, f64), missing: Option<f64>) -> Self {
        Self {
            content,
            latitude: point.0.to_bits(),
            longitude: point.1.to_bits(),
            missing: missing.map(f64::to_bits),
        }
    }
}

#[derive(Debug, Default)]
pub(super) struct PointCache(VecDeque<(Key, DecodedPoint)>);

impl PointCache {
    pub(super) fn get(&mut self, key: &Key) -> Option<DecodedPoint> {
        let index = self.0.iter().position(|(candidate, _)| candidate == key)?;
        let entry = self.0.remove(index).expect("cache entry exists");
        let point = entry.1.clone();
        self.0.push_back(entry);
        Some(point)
    }

    pub(super) fn insert(&mut self, key: Key, point: &DecodedPoint) {
        if point.matrix.len() > MAX_VALUES {
            return;
        }
        self.0.retain(|(candidate, _)| candidate != &key);
        if self.0.len() == MAX_POINTS {
            self.0.pop_front();
        }
        self.0.push_back((key, point.clone()));
    }

    pub(super) fn clear(&mut self) {
        self.0.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reuse_requires_the_same_bytes_point_and_missing_convention() {
        let mut cache = PointCache::default();
        let content = fingerprint(b"GRIB A 7777");
        let point = DecodedPoint {
            matrix: vec![900.0; COLUMN_COUNT],
            level_count: 1,
            selected_latitude: 35.0,
            selected_longitude: -97.0,
            surface_relative_vorticity: None,
            surface_merged: true,
            below_ground_levels_removed: 0,
        };
        let key = || Key::new(content, (35.0, -97.0), Some(-9999.0));
        cache.insert(key(), &point);
        let mut read = cache.get(&key()).unwrap();
        read.matrix[0] = 800.0;
        assert_eq!(cache.get(&key()), Some(point));
        for different in [
            Key::new(fingerprint(b"GRIB B 7777"), (35.0, -97.0), Some(-9999.0)),
            Key::new(content, (35.01, -97.0), Some(-9999.0)),
            Key::new(content, (35.0, -97.0), None),
        ] {
            assert!(cache.get(&different).is_none());
        }
        cache.clear();
        assert!(cache.get(&key()).is_none());
    }

    #[test]
    fn recent_columns_survive_eviction_and_oversized_columns_are_not_retained() {
        let mut cache = PointCache::default();
        let key = |index| Key::new(fingerprint(b"GRIB 7777"), (index as f64, -97.0), None);
        let mut point = DecodedPoint {
            matrix: vec![900.0; COLUMN_COUNT * 512],
            level_count: 512,
            selected_latitude: 35.0,
            selected_longitude: -97.0,
            surface_relative_vorticity: None,
            surface_merged: true,
            below_ground_levels_removed: 0,
        };
        for index in 0..MAX_POINTS {
            cache.insert(key(index), &point);
        }
        assert!(cache.get(&key(0)).is_some());
        cache.insert(key(MAX_POINTS), &point);
        assert!(cache.get(&key(1)).is_none());
        assert!(cache.get(&key(0)).is_some());
        assert!(cache.get(&key(MAX_POINTS)).is_some());

        point.matrix.extend([900.0; COLUMN_COUNT]);
        point.level_count += 1;
        cache.insert(key(MAX_POINTS + 1), &point);
        assert!(cache.get(&key(MAX_POINTS + 1)).is_none());
        for index in 2..MAX_POINTS {
            assert!(cache.get(&key(index)).is_some());
        }
        assert!(cache.get(&key(0)).is_some());
        assert!(cache.get(&key(MAX_POINTS)).is_some());
    }
}

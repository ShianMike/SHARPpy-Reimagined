//! Bounded reuse of ecCodes geometry lookups, never decoded field values.

use super::{c_long, GridPoint, VecDeque};

const GRID_POINT_CACHE_MAX: usize = 256;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(super) struct GridFingerprint {
    digest: [u8; 16],
    point_count: c_long,
}

impl GridFingerprint {
    pub(super) fn new(hash: &str, point_count: c_long) -> Option<Self> {
        if hash.len() != 32 || point_count <= 0 {
            return None;
        }
        let mut digest = [0; 16];
        for (byte, pair) in digest.iter_mut().zip(hash.as_bytes().chunks_exact(2)) {
            let high = char::from(pair[0]).to_digit(16)?;
            let low = char::from(pair[1]).to_digit(16)?;
            *byte = (high * 16 + low) as u8;
        }
        Some(Self {
            digest,
            point_count,
        })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct PointKey {
    grid: GridFingerprint,
    latitude_bits: u64,
    longitude_bits: u64,
}

impl PointKey {
    fn new(grid: GridFingerprint, latitude: f64, longitude: f64) -> Self {
        Self {
            grid,
            latitude_bits: latitude.to_bits(),
            longitude_bits: longitude.to_bits(),
        }
    }
}

#[derive(Debug, Default)]
pub(super) struct GridPointCache {
    entries: VecDeque<(PointKey, GridPoint)>,
}

impl GridPointCache {
    pub(super) fn get(
        &mut self,
        grid: GridFingerprint,
        latitude: f64,
        longitude: f64,
    ) -> Option<GridPoint> {
        let key = PointKey::new(grid, latitude, longitude);
        let position = self
            .entries
            .iter()
            .position(|(candidate, _)| *candidate == key)?;
        let entry = self.entries.remove(position).unwrap();
        let point = entry.1;
        self.entries.push_back(entry);
        Some(point)
    }

    pub(super) fn insert(
        &mut self,
        grid: GridFingerprint,
        latitude: f64,
        longitude: f64,
        point: GridPoint,
    ) {
        if point.index < 0 || i128::from(point.index) >= i128::from(grid.point_count) {
            return;
        }
        let key = PointKey::new(grid, latitude, longitude);
        if let Some(position) = self
            .entries
            .iter()
            .position(|(candidate, _)| *candidate == key)
        {
            self.entries.remove(position);
        }
        self.entries.push_back((key, point));
        if self.entries.len() > GRID_POINT_CACHE_MAX {
            self.entries.pop_front();
        }
    }

    pub(super) fn clear(&mut self) {
        self.entries.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const HASH: &str = "0123456789abcdef0123456789abcdef";

    #[test]
    fn requires_a_complete_eccodes_fingerprint_and_positive_point_count() {
        for hash in [
            "",
            "message:1234",
            "0123456789abcdef0123456789abcdeg",
            "é123456789abcdef0123456789abcdef",
        ] {
            assert!(GridFingerprint::new(hash, 100).is_none());
        }
        assert!(GridFingerprint::new(HASH, 0).is_none());
        assert!(GridFingerprint::new(HASH, -1).is_none());
        assert_eq!(
            GridFingerprint::new(HASH, 100),
            GridFingerprint::new(&HASH.to_uppercase(), 100)
        );
    }

    #[test]
    fn cache_uses_exact_grid_coordinate_bits_and_bounds_indexes() {
        let grid = GridFingerprint::new(HASH, 100).unwrap();
        let mut cache = GridPointCache::default();
        let point = GridPoint {
            index: 12,
            latitude: 35.2,
            longitude: -97.4,
        };
        cache.insert(grid, 35.18, -97.44, point);
        assert_eq!(cache.get(grid, 35.18, -97.44).unwrap().index, 12);
        assert!(cache.get(grid, 35.180000001, -97.44).is_none());
        assert!(cache.get(grid, 35.18, -97.440000001).is_none());
        assert!(cache
            .get(GridFingerprint::new(HASH, 101).unwrap(), 35.18, -97.44)
            .is_none());
        assert!(cache
            .get(
                GridFingerprint::new("1123456789abcdef0123456789abcdef", 100).unwrap(),
                35.18,
                -97.44
            )
            .is_none());
        for index in [-1, 100] {
            cache.insert(grid, 0.0, 0.0, GridPoint { index, ..point });
            assert!(cache.get(grid, 0.0, 0.0).is_none());
        }
        cache.insert(grid, -0.0, 0.0, point);
        assert!(cache.get(grid, 0.0, 0.0).is_none());
        assert!(cache.get(grid, -0.0, 0.0).is_some());
        cache.clear();
        assert!(cache.get(grid, 35.18, -97.44).is_none());
    }

    #[test]
    fn cache_is_bounded_and_refreshes_the_least_recently_used_entry() {
        let grid = GridFingerprint::new(HASH, 1000).unwrap();
        let mut cache = GridPointCache::default();
        for index in 0..GRID_POINT_CACHE_MAX {
            cache.insert(
                grid,
                0.0,
                index as f64,
                GridPoint {
                    index: index as i32,
                    latitude: 0.0,
                    longitude: index as f64,
                },
            );
        }
        assert!(cache.get(grid, 0.0, 0.0).is_some());
        cache.insert(
            grid,
            1.0,
            0.0,
            GridPoint {
                index: 500,
                latitude: 1.0,
                longitude: 0.0,
            },
        );
        assert_eq!(cache.entries.len(), GRID_POINT_CACHE_MAX);
        assert!(cache.get(grid, 0.0, 1.0).is_none());
        assert!(cache.get(grid, 0.0, 0.0).is_some());
    }

    #[test]
    fn resetting_inventories_also_discards_retained_grid_indexes() {
        let mut cache = super::super::InventoryCache::default();
        let grid = GridFingerprint::new(HASH, 100).unwrap();
        cache.grid_points.insert(
            grid,
            35.18,
            -97.44,
            GridPoint {
                index: 12,
                latitude: 35.2,
                longitude: -97.4,
            },
        );
        cache.clear(false);
        assert!(cache.grid_points.get(grid, 35.18, -97.44).is_none());
    }
}

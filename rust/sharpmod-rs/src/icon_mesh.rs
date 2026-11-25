//! Nearest-neighbor remapping from native ICON cell centers to a regular grid.

use rayon::prelude::*;

pub(crate) const WIDTH: usize = 2880;
pub(crate) const HEIGHT: usize = 1441;
pub(crate) const SPACING: f64 = 0.125;

const LEAF_SIZE: usize = 16;
const PARALLEL_BUILD_MIN_POINTS: usize = 32 * 1024;

/// Return native cell indices for the 0.125° grid in row-major order. Latitude
/// decreases from 90° to -90° and longitude increases from -180° to 179.875°.
/// Input coordinates are degrees in [-90, 90] and [-180, 180], respectively.
/// Equal distances select the lowest original cell index.
pub(crate) fn nearest_indices(lats: &[f32], lons: &[f32]) -> Result<Vec<u32>, String> {
    if lats.is_empty() || lats.len() != lons.len() {
        return Err("ICON cell latitudes and longitudes must have matching nonzero lengths".into());
    }
    if u32::try_from(lats.len() - 1).is_err() {
        return Err("ICON cell indices exceed the u32 range".into());
    }

    let mut north = 0;
    let mut south = 0;
    for (index, (&lat, &lon)) in lats.iter().zip(lons).enumerate() {
        if !(-90.0..=90.0).contains(&lat) || !(-180.0..=180.0).contains(&lon) {
            return Err(format!(
                "invalid ICON coordinates at cell {index} ({lat}, {lon})"
            ));
        }
        if lat > lats[north] {
            north = index;
        }
        if lat < lats[south] {
            south = index;
        }
    }

    let mut cells = Vec::new();
    cells
        .try_reserve_exact(lats.len())
        .map_err(|err| format!("cannot allocate ICON cell tree ({err})"))?;
    lats.par_iter()
        .zip(lons)
        .enumerate()
        .map(|(index, (&lat, &lon))| Cell::from_degrees(lat.into(), lon.into(), index as u32))
        .collect_into_vec(&mut cells);
    build_tree(&mut cells);

    let longitudes: Vec<_> = (0..WIDTH)
        .map(|column| longitude_sin_cos(-180.0 + column as f64 * SPACING))
        .collect();
    let mut indices = vec![0; WIDTH * HEIGHT];
    indices
        .par_chunks_mut(WIDTH)
        .enumerate()
        .for_each(|(row_index, row)| {
            // Distance to a pole depends only on latitude. The first cell at
            // the extreme latitude wins, regardless of roundoff in longitude.
            if row_index == 0 {
                row.fill(north as u32);
                return;
            }
            if row_index == HEIGHT - 1 {
                row.fill(south as u32);
                return;
            }
            let lat = 90.0 - row_index as f64 * SPACING;
            let (sin_lat, cos_lat) = lat.to_radians().sin_cos();
            for (index, &(sin_lon, cos_lon)) in row.iter_mut().zip(&longitudes) {
                *index = nearest_index(&cells, [cos_lat * cos_lon, cos_lat * sin_lon, sin_lat]);
            }
        });
    Ok(indices)
}

fn longitude_sin_cos(lon: f64) -> (f64, f64) {
    if lon.abs() == 180.0 {
        (0.0, -1.0)
    } else {
        lon.to_radians().sin_cos()
    }
}

struct Cell {
    point: [f64; 3],
    index: u32,
    split_axis: u8,
}

impl Cell {
    fn from_degrees(lat: f64, lon: f64, index: u32) -> Self {
        let point = if lat.abs() == 90.0 {
            [0.0, 0.0, lat.signum()]
        } else {
            let (sin_lat, cos_lat) = lat.to_radians().sin_cos();
            let (sin_lon, cos_lon) = longitude_sin_cos(lon);
            [cos_lat * cos_lon, cos_lat * sin_lon, sin_lat]
        };
        Self {
            point,
            index,
            split_axis: 0,
        }
    }
}

// Each subtree occupies a contiguous slice, with its split point at the median.
// Small leaves are scanned directly so queries can reuse adjacent cache lines.
fn build_tree(cells: &mut [Cell]) {
    let len = cells.len();
    if len <= LEAF_SIZE {
        return;
    }
    let axis = widest_axis(cells);
    let (left, median, right) = cells.select_nth_unstable_by(len / 2, |a, b| {
        a.point[axis]
            .total_cmp(&b.point[axis])
            .then_with(|| a.index.cmp(&b.index))
    });
    median.split_axis = axis as u8;
    if len >= PARALLEL_BUILD_MIN_POINTS {
        rayon::join(|| build_tree(left), || build_tree(right));
    } else {
        build_tree(left);
        build_tree(right);
    }
}

fn widest_axis(cells: &[Cell]) -> usize {
    let mut lower = cells[0].point;
    let mut upper = lower;
    for cell in &cells[1..] {
        for axis in 0..3 {
            lower[axis] = lower[axis].min(cell.point[axis]);
            upper[axis] = upper[axis].max(cell.point[axis]);
        }
    }
    let spreads = [
        upper[0] - lower[0],
        upper[1] - lower[1],
        upper[2] - lower[2],
    ];
    let mut axis = 0;
    for candidate in 1..3 {
        if spreads[candidate] > spreads[axis] {
            axis = candidate;
        }
    }
    axis
}

struct Nearest {
    distance_squared: f64,
    index: u32,
}

impl Nearest {
    fn consider(&mut self, cell: &Cell, target: &[f64; 3]) {
        let dx = target[0] - cell.point[0];
        let dy = target[1] - cell.point[1];
        let dz = target[2] - cell.point[2];
        let distance_squared = dx * dx + dy * dy + dz * dz;
        if distance_squared < self.distance_squared
            || (distance_squared == self.distance_squared && cell.index < self.index)
        {
            self.distance_squared = distance_squared;
            self.index = cell.index;
        }
    }
}

fn nearest_index(cells: &[Cell], target: [f64; 3]) -> u32 {
    // Chord distance increases monotonically with angular distance on the sphere.
    let mut best = Nearest {
        distance_squared: f64::INFINITY,
        index: u32::MAX,
    };
    search(cells, &target, &mut best);
    best.index
}

fn search(cells: &[Cell], target: &[f64; 3], best: &mut Nearest) {
    if cells.len() <= LEAF_SIZE {
        for cell in cells {
            best.consider(cell, target);
        }
        return;
    }

    let (left, partition) = cells.split_at(cells.len() / 2);
    let (median, right) = partition.split_first().unwrap();
    best.consider(median, target);
    let axis = usize::from(median.split_axis);
    let delta = target[axis] - median.point[axis];
    let (near, far) = if delta < 0.0 {
        (left, right)
    } else {
        (right, left)
    };
    search(near, target, best);
    // The splitting plane bounds the far subtree. Include equality so that a
    // tied cell with a smaller native index can still win.
    if delta * delta <= best.distance_squared {
        search(far, target, best);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tree_matches_brute_force_near_poles_and_dateline() {
        let mut cells: Vec<_> = (0..257)
            .map(|index| {
                let lat = ((index * 73 + 11) % 179) as f64 - 89.0;
                let lon = ((index * 149 + 17) % 359) as f64 - 179.0;
                Cell::from_degrees(lat, lon, index)
            })
            .collect();
        build_tree(&mut cells);

        for lat in [-90.0, -89.875, -82.0, -30.0, 0.0, 20.0, 79.0, 89.875, 90.0] {
            for lon in [-180.0, -179.875, -90.0, -0.125, 0.0, 90.0, 179.875, 180.0] {
                let target = Cell::from_degrees(lat, lon, 0).point;
                let expected = cells
                    .iter()
                    .map(|cell| {
                        let distance_squared: f64 = cell
                            .point
                            .iter()
                            .zip(target)
                            .map(|(&x, y)| {
                                let delta = x - y;
                                delta * delta
                            })
                            .sum();
                        (distance_squared, cell.index)
                    })
                    .min_by(|a, b| a.0.total_cmp(&b.0).then_with(|| a.1.cmp(&b.1)))
                    .unwrap()
                    .1;
                assert_eq!(nearest_index(&cells, target), expected, "at ({lat}, {lon})");
            }
        }
    }

    #[test]
    fn dateline_aliases_tie_across_tree_splits() {
        let mut cells: Vec<_> = (0..3 * LEAF_SIZE)
            .map(|index| {
                let lon = if index % 2 == 0 { 180.0 } else { -180.0 };
                Cell::from_degrees(0.0, lon, index as u32)
            })
            .collect();
        build_tree(&mut cells);
        assert_eq!(nearest_index(&cells, [-1.0, 0.0, 0.0]), 0);
    }

    #[test]
    fn search_finds_neighbor_across_dateline_near_north_pole() {
        let cells = [
            Cell::from_degrees(89.875, -179.875, 0),
            Cell::from_degrees(89.75, 179.875, 1),
            Cell::from_degrees(90.0, 0.0, 2),
        ];
        let target = Cell::from_degrees(89.875, 179.875, 0).point;
        assert_eq!(nearest_index(&cells, target), 0);
    }

    #[test]
    fn output_rows_follow_grid_order_and_use_one_index_per_pole() {
        let lats = [89.75, 89.75, -89.75, -89.75, 0.0, 0.0, 0.0, 0.125, -0.125];
        let lons = [
            117.0, -11.0, -13.0, 141.0, -180.0, -179.875, 179.875, -180.0, -180.0,
        ];
        let indices = nearest_indices(&lats, &lons).unwrap();
        assert_eq!(indices.len(), WIDTH * HEIGHT);
        assert!(indices[..WIDTH].iter().all(|&index| index == 0));
        assert!(indices[(HEIGHT - 1) * WIDTH..]
            .iter()
            .all(|&index| index == 2));

        let equator = (HEIGHT / 2) * WIDTH;
        assert_eq!(indices[equator - WIDTH], 7);
        assert_eq!(indices[equator], 4);
        assert_eq!(indices[equator + 1], 5);
        assert_eq!(indices[equator + WIDTH - 1], 6);
        assert_eq!(indices[equator + WIDTH], 8);
    }

    #[test]
    fn rejects_invalid_coordinate_slices() {
        assert!(nearest_indices(&[], &[]).is_err());
        assert!(nearest_indices(&[0.0], &[]).is_err());
        assert!(nearest_indices(&[f32::NAN], &[0.0]).is_err());
        assert!(nearest_indices(&[0.0], &[f32::INFINITY]).is_err());
        assert!(nearest_indices(&[90.125], &[0.0]).is_err());
        assert!(nearest_indices(&[0.0], &[-180.125]).is_err());
    }
}

//! Synoptic High/Low detection for mean sea level pressure fields.
//!
//! Contour lines show the shape of the pressure field; a surface analysis also
//! names its systems. `detect` finds the synoptic centers a Pivotal-Weather-style
//! analysis would draw: the locally dominant maxima (Highs) and minima (Lows)
//! of the sea level pressure, strong enough to matter on a weather map and far
//! enough apart to stay readable.
//!
//! High-resolution grids carry terrain-reduction noise over mountains and small
//! mesoscale wiggles everywhere, so detection runs on a lightly smoothed copy of
//! the field. Non-finite cells (missing data, below-ground samples, unmapped
//! regions) are preserved as holes instead of being interpolated across.

use std::cmp::Ordering;
use std::collections::VecDeque;
use std::ops::Range;

use crate::contours::Projection;

/// One synoptic pressure center, ready to draw on a mean-sea-level analysis.
#[derive(Debug, Clone, PartialEq)]
pub struct PressureCenter {
    pub lon: f64,
    pub lat: f64,
    /// `"H"` marks a high, `"L"` a low.
    pub kind: &'static str,
    /// Center pressure in hPa from the unsmoothed field, rounded to a whole.
    pub value: i32,
}

/// Prominence over the surrounding ring a center must stand out by, in hPa.
/// Weaker undulations stay nameless contour bending instead of becoming
/// centers, which is what keeps the display at analysis-chart density.
const MIN_PROMINENCE_HPA: f64 = 2.0;
/// Sanity bounds on the smoothed center pressure: real surface highs and lows
/// stay beyond these, so garbage inversion survivors are rejected.
const HIGH_MIN_SMOOTHED_HPA: f64 = 1002.0;
const LOW_MAX_SMOOTHED_HPA: f64 = 1024.0;
/// Radius the local-extreme scan and its prominence ring cover, in degrees.
const NEIGHBORHOOD_DEGREES: f64 = 3.5;
/// Cell-count clamps for that radius across grid resolutions.
const MIN_RADIUS_CELLS: f64 = 4.0;
const MAX_RADIUS_CELLS: f64 = 24.0;
/// Border margins a candidate center must sit inside, in cells.
const BORDER_MARGIN: usize = 2;
/// Finite ring samples a prominence median needs to be trusted.
const MIN_RING_SAMPLES: usize = 8;
/// Same-kind centers closer than this collapse to the stronger one.
const DEDUP_DEGREES: f64 = 8.0;
/// Display bound on the centers of one frame, strongest first.
const MAX_CENTERS: usize = 64;
/// Finite-aware 3x3 box passes over the raw field before scanning.
const SMOOTHING_PASSES: usize = 2;

/// The winning `(value, flat index)` of one centered scan window.
type WindowWinner = (f64, usize);

/// Column indices in scan order, with enough padding to cross the global
/// longitude seam. Each index still refers to its original grid column.
struct ColumnWindows {
    width: usize,
    padding: usize,
    indices: Vec<usize>,
}

impl ColumnWindows {
    fn new(width: usize, max_radius: usize, wraps: bool) -> Self {
        let padding = if wraps { max_radius.min(width / 2) } else { 0 };
        let indices = (width - padding..width)
            .chain(0..width)
            .chain(0..padding)
            .collect();
        Self {
            width,
            padding,
            indices,
        }
    }

    fn range(&self, col: usize, radius: usize) -> Range<usize> {
        if self.padding == 0 {
            return col.saturating_sub(radius)..(col + radius + 1).min(self.width);
        }
        let center = col + self.padding;
        // At most one revolution, even on a narrow global grid.
        let west = radius.min(self.padding);
        let east = west.min((self.width - 1) / 2);
        center - west..center + east + 1
    }

    fn neighbors(&self, col: usize, radius: usize) -> &[usize] {
        &self.indices[self.range(col, radius)]
    }
}

/// One extrema candidate before projection and deduplication.
struct Candidate {
    row: usize,
    col: usize,
    kind: &'static str,
    /// Prominence of the smoothed value over its ring median, always positive.
    prominence: f64,
    /// Center pressure from the unsmoothed field, rounded to a whole.
    value: i32,
}

/// The synoptic Highs and Lows of a mean-sea-level pressure field.
///
/// `bounds` is `[west, south, east, north]` in degrees. `values` is a row-major
/// `(height, width)` grid in the unit named by `unit`; only `"hPa"` fields carry
/// centers, and everything else returns an empty vec, as do undersized grids,
/// length mismatches and invalid bounds. Centers come out strongest first, at
/// most [`MAX_CENTERS`] of them.
pub fn detect(
    values: &[f32],
    height: usize,
    width: usize,
    bounds: &[f64; 4],
    unit: &str,
) -> Vec<PressureCenter> {
    if unit != "hPa" || height < 5 || width < 5 || height.checked_mul(width) != Some(values.len()) {
        return Vec::new();
    }
    let [west, south, east, north] = *bounds;
    if !(-180.0..=180.0).contains(&west)
        || !(-180.0..=180.0).contains(&east)
        || !(-90.0..=90.0).contains(&south)
        || !(-90.0..=90.0).contains(&north)
        || west >= east
        || south >= north
    {
        return Vec::new();
    }
    let Ok(projection) = Projection::new(height, width, (west, east, south, north)) else {
        return Vec::new();
    };
    let radius_cols = cell_radius(NEIGHBORHOOD_DEGREES, (east - west) / width as f64);
    let radius_rows = cell_radius(NEIGHBORHOOD_DEGREES, (north - south) / height as f64);
    let wraps = west == -180.0 && east == 180.0;
    let columns = ColumnWindows::new(width, radius_cols, wraps);
    // Two 3x3 passes tame single-cell noise without flattening synoptic shape.
    // The mask is preserved in both directions, so holes stay holes.
    let mut field: Vec<f64> = values.iter().map(|&value| value as f64).collect();
    for _ in 0..SMOOTHING_PASSES {
        field = box_smooth(&field, height, &columns);
    }
    let (maxima, minima) = (
        window_extrema(&field, height, &columns, radius_rows, radius_cols, true),
        window_extrema(&field, height, &columns, radius_rows, radius_cols, false),
    );
    let mut candidates = Vec::new();
    let mut ring = Vec::new();
    let col_margin = if wraps { 0 } else { BORDER_MARGIN };
    for row in BORDER_MARGIN..height - BORDER_MARGIN {
        for col in col_margin..width - col_margin {
            let index = row * width + col;
            let value = field[index];
            if !value.is_finite() {
                continue;
            }
            // A cell whose own window declares it the winner is the one
            // deterministic representative of its neighborhood extreme;
            // every other cell of a plateau loses the flat-index contest.
            let kind = if maxima[index] == (value, index) {
                "H"
            } else if minima[index] == (value, index) {
                "L"
            } else {
                continue;
            };
            if matches!(kind, "H" if value <= HIGH_MIN_SMOOTHED_HPA)
                || matches!(kind, "L" if value >= LOW_MAX_SMOOTHED_HPA)
            {
                continue;
            }
            let Some(median) = ring_median(
                &field,
                height,
                &columns,
                (row, col),
                (radius_rows, radius_cols),
                &mut ring,
            ) else {
                continue;
            };
            let prominence = if kind == "H" {
                value - median
            } else {
                median - value
            };
            if prominence < MIN_PROMINENCE_HPA {
                continue;
            }
            // The smoothing preserves the raw mask, so the winner cell always
            // holds a finite raw sample; the smoothed reading only guards
            // future kernel changes that could ever break that invariant.
            let raw = values[index];
            let center = if raw.is_finite() { raw } else { value as f32 };
            candidates.push(Candidate {
                row,
                col,
                kind,
                prominence,
                value: center.round() as i32,
            });
        }
    }
    // Strongest first: dedup keeps the most prominent center of a same-kind
    // cluster, and the display cap keeps the strongest systems.
    candidates.sort_by(|a, b| {
        b.prominence
            .partial_cmp(&a.prominence)
            .unwrap_or(Ordering::Equal)
            .then_with(|| match (a.kind, b.kind) {
                ("H", "H") => b.value.cmp(&a.value),
                ("L", "L") => a.value.cmp(&b.value),
                _ => Ordering::Equal,
            })
            .then_with(|| (a.row * width + a.col).cmp(&(b.row * width + b.col)))
    });
    let mut centers: Vec<PressureCenter> = Vec::new();
    'candidates: for candidate in candidates {
        let [lon, lat] = projection.point([candidate.col as f64, candidate.row as f64]);
        for center in &centers {
            if center.kind == candidate.kind
                && angular_distance((center.lon, center.lat), (lon, lat)) <= DEDUP_DEGREES
            {
                continue 'candidates;
            }
        }
        centers.push(PressureCenter {
            lon,
            lat,
            kind: candidate.kind,
            value: candidate.value,
        });
        if centers.len() == MAX_CENTERS {
            break;
        }
    }
    centers
}

/// Neighborhood radius in cells for one axis, near 3.5 degrees of spacing but
/// clamped to a workable window at every grid resolution.
fn cell_radius(degrees: f64, spacing: f64) -> usize {
    (degrees / spacing)
        .round()
        .clamp(MIN_RADIUS_CELLS, MAX_RADIUS_CELLS) as usize
}

/// One finite-aware 3x3 box pass. A finite cell averages the finite samples of
/// its window, including itself. Non-finite cells preserve the field's mask.
fn box_smooth(field: &[f64], height: usize, columns: &ColumnWindows) -> Vec<f64> {
    let width = columns.width;
    (0..field.len())
        .map(|index| {
            let value = field[index];
            if !value.is_finite() {
                return f64::NAN;
            }
            let (row, col) = (index / width, index % width);
            let (top, bottom) = (row.saturating_sub(1), (row + 1).min(height - 1));
            let neighbors = columns.neighbors(col, 1);
            let mut sum = 0.0;
            let mut count = 0usize;
            for r in top..=bottom {
                for &c in neighbors {
                    let value = field[r * width + c];
                    if value.is_finite() {
                        sum += value;
                        count += 1;
                    }
                }
            }
            sum / count as f64
        })
        .collect()
}

/// The winning `(value, flat index)` of every centered `(rows, cols)` window.
///
/// The winner is the window's strict extreme; exact ties inside a flat plateau
/// resolve to the lowest original flat index, including across the longitude
/// seam. Each pass slides a monotonic deque, keeping the scan linear.
fn window_extrema(
    field: &[f64],
    height: usize,
    columns: &ColumnWindows,
    radius_rows: usize,
    radius_cols: usize,
    maximum: bool,
) -> Vec<WindowWinner> {
    let width = columns.width;
    let sentinel = if maximum {
        f64::NEG_INFINITY
    } else {
        f64::INFINITY
    };
    // Pass one: extreme over each row's column window.
    let mut horizontal = vec![(sentinel, 0usize); field.len()];
    let mut deque = VecDeque::new();
    for row in 0..height {
        let base = row * width;
        deque.clear();
        let mut next_col = columns.range(0, radius_cols).start;
        for center in 0..width {
            let window = columns.range(center, radius_cols);
            while next_col < window.end {
                let index = base + columns.indices[next_col];
                let sample = field[index];
                let value = if sample.is_finite() { sample } else { sentinel };
                push_sample(&mut deque, next_col, (value, index), maximum);
                next_col += 1;
            }
            while deque.front().is_some_and(|&(pos, _)| pos < window.start) {
                deque.pop_front();
            }
            horizontal[base + center] = deque
                .front()
                .map_or((sentinel, base + center), |&(_, winner)| winner);
        }
    }
    // Pass two: extreme of the row winners over each column's row window.
    let mut windows = vec![(sentinel, 0usize); field.len()];
    for col in 0..width {
        deque.clear();
        let mut next_row = 0usize;
        for center in 0..height {
            let window_end = (center + radius_rows).min(height - 1);
            while next_row <= window_end {
                let sample = horizontal[next_row * width + col];
                push_sample(&mut deque, next_row, sample, maximum);
                next_row += 1;
            }
            let window_start = center.saturating_sub(radius_rows);
            while deque.front().is_some_and(|&(pos, _)| pos < window_start) {
                deque.pop_front();
            }
            windows[center * width + col] = deque
                .front()
                .map_or((sentinel, center * width + col), |&(_, winner)| winner);
        }
    }
    windows
}

/// Scan positions expire in order. Ties use the original index because a
/// wrapped column can enter the deque before a lower-index neighbor.
fn push_sample(
    deque: &mut VecDeque<(usize, WindowWinner)>,
    position: usize,
    (value, index): WindowWinner,
    maximum: bool,
) {
    while deque.back().is_some_and(|&(_, (back, back_index))| {
        if value == back {
            index < back_index
        } else if maximum {
            value > back
        } else {
            value < back
        }
    }) {
        deque.pop_back();
    }
    deque.push_back((position, (value, index)));
}

/// Median of the finite samples on the perimeter of the candidate's `(rows,
/// cols)` window. `None` when too few samples are finite
/// to trust a median at all (a candidate buried in missing data).
fn ring_median(
    field: &[f64],
    height: usize,
    columns: &ColumnWindows,
    (row, col): (usize, usize),
    (radius_rows, radius_cols): (usize, usize),
    samples: &mut Vec<f64>,
) -> Option<f64> {
    let width = columns.width;
    samples.clear();
    let top = row.saturating_sub(radius_rows);
    let bottom = (row + radius_rows).min(height - 1);
    let neighbors = columns.neighbors(col, radius_cols);
    for &c in neighbors {
        for r in [top, bottom] {
            let value = field[r * width + c];
            if value.is_finite() {
                samples.push(value);
            }
        }
    }
    let edges = [neighbors[0], neighbors[neighbors.len() - 1]];
    for r in top + 1..bottom {
        for c in edges {
            let value = field[r * width + c];
            if value.is_finite() {
                samples.push(value);
            }
        }
    }
    if samples.len() < MIN_RING_SAMPLES {
        return None;
    }
    samples.sort_by(|a, b| a.partial_cmp(b).unwrap_or(Ordering::Equal));
    let middle = samples.len() / 2;
    Some(if samples.len() % 2 == 1 {
        samples[middle]
    } else {
        (samples[middle - 1] + samples[middle]) / 2.0
    })
}

/// Great-circle angular distance between two `(lon, lat)` points, in degrees.
fn angular_distance((lon1, lat1): (f64, f64), (lon2, lat2): (f64, f64)) -> f64 {
    let (lon1, lat1, lon2, lat2) = (
        lon1.to_radians(),
        lat1.to_radians(),
        lon2.to_radians(),
        lat2.to_radians(),
    );
    let (dlat, dlon) = ((lat2 - lat1) / 2.0, (lon2 - lon1) / 2.0);
    let haversine = (dlat.sin().powi(2) + lat1.cos() * lat2.cos() * dlon.sin().powi(2)).min(1.0);
    2.0 * haversine.sqrt().asin().to_degrees()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A `[west, south, east, north]` domain of 0.25-degree cells: a 60x80
    /// grid over a 20x15-degree window, so every scan radius is 14 cells.
    const BOUNDS: [f64; 4] = [-100.0, 30.0, -80.0, 45.0];

    /// Base pressure plus smooth Gaussian features `(row, col, amplitude,
    /// sigma)` in hPa and cells, the shape a synoptic system actually takes.
    fn gaussian_field(
        height: usize,
        width: usize,
        base: f32,
        features: &[(usize, usize, f32, f64)],
    ) -> Vec<f32> {
        (0..height * width)
            .map(|index| {
                let (row, col) = (index / width, index % width);
                let mut value = f64::from(base);
                for &(feature_row, feature_col, amplitude, sigma) in features {
                    let (dr, dc) = (
                        f64::from(row as i32 - feature_row as i32),
                        f64::from(col as i32 - feature_col as i32),
                    );
                    value +=
                        f64::from(amplitude) * (-(dr * dr + dc * dc) / (2.0 * sigma * sigma)).exp();
                }
                value as f32
            })
            .collect()
    }

    #[test]
    fn finds_a_synoptic_high_and_low_with_projected_positions_and_values() {
        let field = gaussian_field(60, 80, 1013.0, &[(40, 25, 22.0, 5.0), (20, 55, -14.0, 5.0)]);
        let centers = detect(&field, 60, 80, &BOUNDS, "hPa");
        assert_eq!(centers.len(), 2);
        let (high, low) = (&centers[0], &centers[1]);
        assert_eq!(
            (high.kind, high.value, high.lon, high.lat),
            ("H", 1035, -93.625, 34.875)
        );
        assert_eq!(
            (low.kind, low.value, low.lon, low.lat),
            ("L", 999, -86.125, 39.875)
        );
    }

    #[test]
    fn global_centers_match_regional_profiles_across_the_antimeridian() {
        let (height, width) = (60, 360);
        let interior = gaussian_field(
            height,
            width,
            1013.0,
            &[(20, 180, 22.0, 1.3), (40, 179, -16.0, 1.3)],
        );
        let regional: Vec<_> = interior
            .chunks_exact(width)
            .flat_map(|row| row[140..220].iter().copied())
            .collect();
        let expected = detect(&regional, height, 80, &[-40.0, -30.0, 40.0, 30.0], "hPa");
        assert_eq!(expected.len(), 2);
        assert_eq!((expected[0].kind, expected[0].value), ("H", 1035));
        assert_eq!((expected[1].kind, expected[1].value), ("L", 997));

        // Move the same profiles to either edge and across the seam.
        for shift in [179, 180, 181] {
            let mut wrapped = interior.clone();
            for row in wrapped.chunks_exact_mut(width) {
                row.rotate_left(shift);
            }
            let centers = detect(
                &wrapped,
                height,
                width,
                &[-180.0, -30.0, 180.0, 30.0],
                "hPa",
            );
            let shifted: Vec<_> = expected
                .iter()
                .cloned()
                .map(|mut center| {
                    center.lon = (center.lon - shift as f64 + 180.0).rem_euclid(360.0) - 180.0;
                    center
                })
                .collect();
            assert_eq!(centers, shifted, "longitude shift {shift}");
            assert!(detect(
                &wrapped,
                height,
                width,
                &[-180.0, -30.0, 179.0, 30.0],
                "hPa"
            )
            .is_empty());
        }
    }

    #[test]
    fn a_plateau_across_the_antimeridian_uses_its_lowest_flat_index() {
        let (height, width) = (24, 360);
        let field: Vec<_> = (0..height * width)
            .map(|index| {
                let (row, col) = (index / width, index % width);
                if (8..16).contains(&row) && !(4..width - 4).contains(&col) {
                    1028.0
                } else {
                    1013.0
                }
            })
            .collect();
        let centers = detect(&field, height, width, &[-180.0, -12.0, 180.0, 12.0], "hPa");
        assert_eq!(centers.len(), 1);
        let center = &centers[0];
        assert_eq!(
            (center.kind, center.value, center.lon, center.lat),
            ("H", 1028, -179.5, 1.5)
        );
    }

    #[test]
    fn rounds_center_values_from_the_unsmoothed_field() {
        // A 1034.6 hPa peak center must read 1035, not the smoothed reading.
        let field = gaussian_field(60, 80, 1013.0, &[(40, 25, 21.6, 5.0)]);
        let centers = detect(&field, 60, 80, &BOUNDS, "hPa");
        assert_eq!(centers.len(), 1);
        assert_eq!((centers[0].kind, centers[0].value), ("H", 1035));
    }

    #[test]
    fn returns_nothing_for_other_units_undersized_grids_or_length_mismatches() {
        let field = gaussian_field(60, 80, 1013.0, &[(40, 25, 22.0, 5.0)]);
        assert!(detect(&field, 60, 80, &BOUNDS, "Pa").is_empty());
        assert!(detect(&field, 60, 80, &BOUNDS, "dam").is_empty());
        assert!(detect(&field, 4, 80, &BOUNDS, "hPa").is_empty());
        assert!(detect(&field, 60, 4, &BOUNDS, "hPa").is_empty());
        assert!(detect(&field[..79], 60, 80, &BOUNDS, "hPa").is_empty());
        assert!(detect(&field, 60, 80, &[-80.0, 45.0, -100.0, 30.0], "hPa").is_empty());
    }

    #[test]
    fn non_finite_edges_and_missing_data_do_not_disturb_interior_centers() {
        let mut field =
            gaussian_field(60, 80, 1013.0, &[(40, 25, 22.0, 5.0), (20, 55, -14.0, 5.0)]);
        let clean = detect(&field, 60, 80, &BOUNDS, "hPa");
        assert_eq!(clean.len(), 2);
        // Two-cell border margins and an interior hole near the low, inside
        // its prominence ring but outside its smoothing neighborhood.
        for row in 0..2 {
            for col in 0..80 {
                field[row * 80 + col] = f32::NAN;
                field[(59 - row) * 80 + col] = f32::NAN;
            }
        }
        for row in 0..60 {
            for col in 0..2 {
                field[row * 80 + col] = f32::NAN;
                field[row * 80 + 79 - col] = f32::NAN;
            }
        }
        for row in 14..27 {
            for col in 58..69 {
                field[row * 80 + col] = f32::NAN;
            }
        }
        assert_eq!(detect(&field, 60, 80, &BOUNDS, "hPa"), clean);
        let all_missing = vec![f32::NAN; 60 * 80];
        assert!(detect(&all_missing, 60, 80, &BOUNDS, "hPa").is_empty());
    }

    #[test]
    fn small_perturbations_do_not_become_centers() {
        let mut field: Vec<f32> = (0..60 * 80)
            .map(|index| {
                let (row, col) = (index / 80, index % 80);
                1013.0 + 0.35 * (1.7 * row as f32).sin() * (2.3 * col as f32).cos()
            })
            .collect();
        assert!(detect(&field, 60, 80, &BOUNDS, "hPa").is_empty());
        // The same noise on top of a real high neither hides nor multiplies it.
        for (index, value) in field.iter_mut().enumerate() {
            let (row, col) = (index / 80, index % 80);
            let (dr, dc) = ((row as f64 - 40.0), (col as f64 - 25.0));
            *value += (22.0 * (-(dr * dr + dc * dc) / 50.0).exp()) as f32;
        }
        let centers = detect(&field, 60, 80, &BOUNDS, "hPa");
        assert_eq!(centers.len(), 1);
        assert_eq!(
            (
                centers[0].kind,
                centers[0].value,
                centers[0].lon,
                centers[0].lat
            ),
            ("H", 1035, -93.625, 34.875)
        );
    }

    #[test]
    fn flat_plateaus_resolve_to_one_center_by_lowest_flat_index() {
        // A 40x40-cell 1025 hPa mesa; two smoothing passes leave a broad exact
        // plateau whose first fully-interior cell is its one active winner.
        let field: Vec<f32> = (0..60 * 80)
            .map(|index| {
                let (row, col) = (index / 80, index % 80);
                if (10..50).contains(&row) && (10..50).contains(&col) {
                    1025.0
                } else {
                    1013.0
                }
            })
            .collect();
        let centers = detect(&field, 60, 80, &BOUNDS, "hPa");
        assert_eq!(centers.len(), 1);
        assert_eq!(
            (
                centers[0].kind,
                centers[0].value,
                centers[0].lon,
                centers[0].lat
            ),
            ("H", 1025, -96.875, 41.875)
        );
    }

    #[test]
    fn close_same_kind_centers_deduplicate_to_the_stronger_one() {
        // 30 columns apart: 7.5 degrees of longitude, well inside the 8-degree
        // same-kind dedup, so only the 1035 hPa high survives.
        let field = gaussian_field(60, 80, 1013.0, &[(40, 25, 22.0, 5.0), (40, 55, 9.0, 5.0)]);
        let centers = detect(&field, 60, 80, &BOUNDS, "hPa");
        assert_eq!(centers.len(), 1);
        assert_eq!(
            (
                centers[0].kind,
                centers[0].value,
                centers[0].lon,
                centers[0].lat
            ),
            ("H", 1035, -93.625, 34.875)
        );
    }

    #[test]
    fn a_close_high_and_low_pair_is_never_deduplicated() {
        // Five degrees apart at this latitude, but different kinds compete for
        // nothing; a sharp pressure couplet keeps its High and its Low.
        let field = gaussian_field(60, 80, 1013.0, &[(40, 25, 22.0, 5.0), (40, 45, -14.0, 5.0)]);
        let centers = detect(&field, 60, 80, &BOUNDS, "hPa");
        assert_eq!(centers.len(), 2);
        assert_eq!(
            (
                centers[0].kind,
                centers[0].value,
                centers[0].lon,
                centers[0].lat
            ),
            ("H", 1035, -93.625, 34.875)
        );
        assert_eq!(
            (
                centers[1].kind,
                centers[1].value,
                centers[1].lon,
                centers[1].lat
            ),
            ("L", 999, -88.625, 34.875)
        );
    }

    #[test]
    fn sanity_bounds_reject_thermally_implausible_extremes() {
        // A shallow low inside a warm high: no low reads below 1024 hPa.
        let warm_high = gaussian_field(60, 80, 1035.0, &[(30, 40, -6.0, 5.0)]);
        assert!(detect(&warm_high, 60, 80, &BOUNDS, "hPa").is_empty());
        // A modest ridge inside a cold regime: no high reads above 1002 hPa.
        let cold_low = gaussian_field(60, 80, 992.0, &[(30, 40, 10.0, 5.0)]);
        assert!(detect(&cold_low, 60, 80, &BOUNDS, "hPa").is_empty());
    }

    #[test]
    fn tiny_grids_work_and_border_margins_are_excluded() {
        // The smallest accepted grid: the radius clamps to four cells, so the
        // window and ring are the clipped whole grid around the center.
        let tiny = gaussian_field(5, 5, 1013.0, &[(2, 2, 22.0, 0.7)]);
        let centers = detect(&tiny, 5, 5, &[0.0, 0.0, 10.0, 10.0], "hPa");
        assert_eq!(centers.len(), 1);
        assert_eq!(
            (
                centers[0].kind,
                centers[0].value,
                centers[0].lon,
                centers[0].lat
            ),
            ("H", 1035, 5.0, 5.0)
        );
        // The same feature one cell inside the border is outside the scan.
        let marginal = gaussian_field(60, 80, 1013.0, &[(1, 1, 22.0, 5.0)]);
        assert!(detect(&marginal, 60, 80, &BOUNDS, "hPa").is_empty());
    }
}

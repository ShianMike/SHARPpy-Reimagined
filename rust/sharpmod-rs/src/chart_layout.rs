//! Rust mirror of the hodograph height-level marker layout
//! (`sharpmod/viz/hodo_levels.py`), the Skew-T annotation geometry
//! (`sharpmod/render_patches/geometry.py`), and the compact unit-suffix split
//! (`sharpmod/viz/unit_text.py`).
//!
//! The layouts are deterministic: candidate marker centres are generated in a
//! fixed seed-and-ring order, rounded to six decimals, and deduplicated
//! order-preservingly exactly like the Python reference; the Skew-T label
//! packer packs rows with at least `gap` logical pixels of clearance while
//! returning the rectangles in the caller's input order.  Every function is
//! arithmetic on plain floats and strings, so nothing here needs a Python
//! interpreter of its own.

use pyo3::prelude::*;

pub(crate) type HeightSample = (String, f64, f64, f64);
pub(crate) type PlacedMarker = (String, f64, f64, f64, f64, f64);
/// Widget/plot rectangle in `(left, top, right, bottom)` order.
pub(crate) type PixelBounds = (f64, f64, f64, f64);
/// Exclusion rectangle in `(left, top, right, bottom)` order.
pub(crate) type Exclusion = (f64, f64, f64, f64);
/// Rectangle in `(left, top, width, height)` order.
pub(crate) type Rect = (f64, f64, f64, f64);
/// Surface-label request in `(center_x, line_y, width, height)` order.
pub(crate) type LabelRequest = (f64, f64, f64, f64);

const MARKER_GAP_PX: f64 = 2.0;

/// CPython's two-argument `min`/`max` keep the first operand when the second
/// is NaN, and every clamp below is written with them so a NaN label or limit
/// propagates exactly like the Python reference.
fn py_max(a: f64, b: f64) -> f64 {
    if b > a {
        b
    } else {
        a
    }
}

fn py_min(a: f64, b: f64) -> f64 {
    if b < a {
        b
    } else {
        a
    }
}

/// `hodo_levels._label_for_height`: the height in kilometres with one decimal,
/// trailing zero(es) and then a trailing decimal point stripped.
fn hodo_label_for_height_core(height_m: f64) -> String {
    let height_km = height_m / 1000.0;
    format!("{height_km:.1}")
        .trim_end_matches('0')
        .trim_end_matches('.')
        .to_string()
}

/// `np.interp` over a strictly increasing, duplicate-free height axis, using
/// numpy's exact `slope * (x - xp[j]) + fp[j]` expression so knot values come
/// back bit-for-bit.
fn np_interp(level: f64, heights: &[f64], values: &[f64]) -> f64 {
    if level <= heights[0] {
        return values[0];
    }
    // Last index with heights[j] <= level, the segment numpy's binary search
    // picks, so a level that lands on a knot interpolates from that knot.
    let j = heights.partition_point(|&height| height <= level) - 1;
    if j >= heights.len() - 1 {
        return values[heights.len() - 1];
    }
    let slope = (values[j + 1] - values[j]) / (heights[j + 1] - heights[j]);
    slope * (level - heights[j]) + values[j]
}

/// `hodo_levels.height_level_points`, fed with pre-extracted profile arrays:
/// keep the finite `(height, u, v)` triples, sort stably by height, keep the
/// first sample of each duplicated height (`np.unique(..., return_index=True)`),
/// then linearly interpolate every in-range target level.
fn prepare_height_wind_samples_core(
    heights: &[f64],
    u_vals: &[f64],
    v_vals: &[f64],
    target_levels: &[f64],
) -> Vec<HeightSample> {
    let usable = heights.len().min(u_vals.len()).min(v_vals.len());
    let mut sampled: Vec<(f64, f64, f64)> = (0..usable)
        .map(|index| (heights[index], u_vals[index], v_vals[index]))
        .filter(|(height, u, v)| height.is_finite() && u.is_finite() && v.is_finite())
        .collect();
    if sampled.len() < 2 {
        return Vec::new();
    }
    sampled.sort_by(|a, b| a.0.total_cmp(&b.0));
    let mut profile: Vec<(f64, f64, f64)> = Vec::with_capacity(sampled.len());
    for (height, u, v) in sampled {
        if profile
            .last()
            .is_some_and(|&(kept_height, _, _)| kept_height == height)
        {
            continue;
        }
        profile.push((height, u, v));
    }
    if profile.len() < 2 {
        return Vec::new();
    }
    let profile_heights: Vec<f64> = profile.iter().map(|&(height, _, _)| height).collect();
    let profile_u: Vec<f64> = profile.iter().map(|&(_, u, _)| u).collect();
    let profile_v: Vec<f64> = profile.iter().map(|&(_, _, v)| v).collect();

    let mut samples: Vec<HeightSample> = Vec::new();
    for &level in target_levels {
        if !level.is_finite() {
            continue;
        }
        let first = profile_heights[0];
        let last = profile_heights[profile_heights.len() - 1];
        if level < first || level > last {
            continue;
        }
        samples.push((
            hodo_label_for_height_core(level),
            level,
            np_interp(level, &profile_heights, &profile_u),
            np_interp(level, &profile_heights, &profile_v),
        ));
    }
    samples
}

/// `hodo_levels._inside_marker_bounds`: the whole marker circle must stay
/// inside the plot box.
fn inside_marker_bounds(x: f64, y: f64, bounds: PixelBounds, radius: f64) -> bool {
    let (left, top, right, bottom) = bounds;
    left + radius <= x && x <= right - radius && top + radius <= y && y <= bottom - radius
}

/// `hodo_levels._overlaps_exclusion`: strict-inequality rectangle test.
fn overlaps_exclusion(x: f64, y: f64, exclusion: Exclusion, radius: f64) -> bool {
    let (left, top, right, bottom) = exclusion;
    x + radius > left && x - radius < right && y + radius > top && y - radius < bottom
}

/// `hodo_levels._projection_candidates`: the four positions just outside an
/// exclusion rectangle, sorted by squared distance from the wind tip and,
/// like Python's `candidates.index(point)`, by first occurrence on ties.
fn projection_candidates(x: f64, y: f64, exclusion: Exclusion, radius: f64) -> Vec<(f64, f64)> {
    let (left, top, right, bottom) = exclusion;
    let clearance = radius + MARKER_GAP_PX;
    let candidates = [
        (right + clearance, y),
        (x, bottom + clearance),
        (left - clearance, y),
        (x, top - clearance),
    ];
    let mut tagged: Vec<(f64, usize, (f64, f64))> = candidates
        .iter()
        .enumerate()
        .map(|(index, &point)| {
            let first = candidates
                .iter()
                .position(|other| *other == point)
                .unwrap_or(index);
            let dx = point.0 - x;
            let dy = point.1 - y;
            (dx * dx + dy * dy, first, point)
        })
        .collect();
    // A stable sort keeps the Python tuple key's tie behaviour.
    tagged.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.cmp(&b.1)));
    tagged.into_iter().map(|(_, _, point)| point).collect()
}

/// `round(value, 6)` as prescribed: ties-to-even on the scaled value.
fn round6(value: f64) -> f64 {
    (value * 1.0e6).round_ties_even() / 1.0e6
}

/// `hodo_levels._candidate_centers`: the seed itself, the projections of any
/// overlapping exclusion, then five rings of the 16 documented angles around
/// every seed, with the deduplicated 6-decimal-rounded centres like
/// `tuple(dict.fromkeys(...))`.
fn candidate_centers(
    x: f64,
    y: f64,
    exclusions: &[Exclusion],
    minimum_separation: f64,
    radius: f64,
) -> Vec<(f64, f64)> {
    const ANGLES: [f64; 16] = [
        0.0, 90.0, 180.0, 270.0, 45.0, 135.0, 225.0, 315.0, 30.0, 60.0, 120.0, 150.0, 210.0, 240.0,
        300.0, 330.0,
    ];
    let mut seeds: Vec<(f64, f64)> = vec![(x, y)];
    for &exclusion in exclusions {
        if overlaps_exclusion(x, y, exclusion, radius) {
            seeds.extend(projection_candidates(x, y, exclusion, radius));
        }
    }
    let mut candidates: Vec<(f64, f64)> = seeds.clone();
    for &(seed_x, seed_y) in &seeds {
        for ring in 1..=5 {
            let distance = minimum_separation * f64::from(ring);
            for &angle in &ANGLES {
                let radians = angle.to_radians();
                candidates.push((
                    seed_x + radians.cos() * distance,
                    seed_y + radians.sin() * distance,
                ));
            }
        }
    }
    let mut unique: Vec<(f64, f64)> = Vec::new();
    for (cx, cy) in candidates {
        let rounded = (round6(cx), round6(cy));
        if !unique.contains(&rounded) {
            unique.push(rounded);
        }
    }
    unique
}

/// `hodo_levels.layout_height_level_markers`: place each numbered marker at
/// the first candidate centre that stays inside the plot box, clears every
/// usable exclusion rectangle, and keeps `2 * radius + gap` pixels from every
/// marker placed before it.
fn layout_height_level_markers_core(
    points: &[HeightSample],
    bounds: PixelBounds,
    exclusions: &[Exclusion],
    radius: f64,
) -> Vec<PlacedMarker> {
    if !radius.is_finite() || radius <= 0.0 {
        return Vec::new();
    }
    let (left, top, right, bottom) = bounds;
    let bounds_finite =
        left.is_finite() && top.is_finite() && right.is_finite() && bottom.is_finite();
    if !bounds_finite || left >= right || top >= bottom {
        return Vec::new();
    }
    let active_exclusions: Vec<Exclusion> = exclusions
        .iter()
        .copied()
        .filter(|&(left, top, right, bottom)| {
            left.is_finite()
                && top.is_finite()
                && right.is_finite()
                && bottom.is_finite()
                && left < right
                && top < bottom
        })
        .collect();
    let minimum_separation = radius * 2.0 + MARKER_GAP_PX;

    let mut placed: Vec<(f64, f64)> = Vec::new();
    let mut layout: Vec<PlacedMarker> = Vec::new();
    for (label, height_m, source_x, source_y) in points {
        let (height_m, source_x, source_y) = (*height_m, *source_x, *source_y);
        if !source_x.is_finite() || !source_y.is_finite() {
            continue;
        }
        if !inside_marker_bounds(source_x, source_y, bounds, radius) {
            continue;
        }
        let candidates = candidate_centers(
            source_x,
            source_y,
            &active_exclusions,
            minimum_separation,
            radius,
        );
        let mut center: Option<(f64, f64)> = None;
        for &(candidate_x, candidate_y) in &candidates {
            if !inside_marker_bounds(candidate_x, candidate_y, bounds, radius) {
                continue;
            }
            if active_exclusions
                .iter()
                .any(|&exclusion| overlaps_exclusion(candidate_x, candidate_y, exclusion, radius))
            {
                continue;
            }
            if placed.iter().any(|&(placed_x, placed_y)| {
                (candidate_x - placed_x).hypot(candidate_y - placed_y) < minimum_separation
            }) {
                continue;
            }
            center = Some((candidate_x, candidate_y));
            break;
        }
        let Some((center_x, center_y)) = center else {
            continue;
        };
        placed.push((center_x, center_y));
        layout.push((
            label.clone(),
            height_m,
            source_x,
            source_y,
            center_x,
            center_y,
        ));
    }
    layout
}

/// `geometry._fit_rect_to_skewt_plot` with pad-inclusive limits.
#[allow(clippy::too_many_arguments)]
fn skewt_fit_rect_core(
    x: f64,
    y: f64,
    width: f64,
    height: f64,
    left_limit: f64,
    right_limit: f64,
    top_limit: f64,
    bottom_limit: f64,
) -> Rect {
    if right_limit <= left_limit || bottom_limit <= top_limit {
        return (x, y, width, height);
    }
    let fitted_width = py_min(width, py_max(1.0, right_limit - left_limit));
    let fitted_height = py_min(height, py_max(1.0, bottom_limit - top_limit));
    let max_left = right_limit - fitted_width;
    let max_top = bottom_limit - fitted_height;
    let left = py_min(py_max(x, left_limit), max_left);
    let top = py_min(py_max(y, top_limit), max_top);
    (left, top, fitted_width, fitted_height)
}

/// `geometry._skewt_surface_label_rect` with pad-inclusive limits: below the
/// line where it fits, flipped above it otherwise, then fitted to the plot.
#[allow(clippy::too_many_arguments)]
fn skewt_surface_label_rect_core(
    center_x: f64,
    line_y: f64,
    width: f64,
    height: f64,
    left_limit: f64,
    right_limit: f64,
    top_limit: f64,
    bottom_limit: f64,
    below_offset: f64,
) -> Rect {
    let left = center_x - width / 2.0;
    let mut top = line_y + below_offset;
    if top + height > bottom_limit {
        top = line_y - below_offset - height;
    }
    skewt_fit_rect_core(
        left,
        top,
        width,
        height,
        left_limit,
        right_limit,
        top_limit,
        bottom_limit,
    )
}

/// `geometry._centered_rect_in_bounds` for inset titles and similar labels.
fn skewt_centered_rect_core(
    center_x: f64,
    top: f64,
    width: f64,
    height: f64,
    left_limit: f64,
    right_limit: f64,
) -> Rect {
    let width = py_min(width, py_max(1.0, right_limit - left_limit));
    let left = center_x - width / 2.0;
    let max_left = right_limit - width;
    let left = py_min(py_max(left, left_limit), max_left);
    (left, top, width, height)
}

struct PreparedLabel {
    index: usize,
    center_x: f64,
    line_y: f64,
    width: f64,
    height: f64,
    preferred: Rect,
}

/// `geometry._layout_skewt_surface_labels`: pack surface-value labels into
/// bounded, non-overlapping rows.  `labels` carry `(center_x, line_y, width,
/// height)`; the returned rectangles follow the input order, even though the
/// anchors are sorted internally for layout.
fn skewt_layout_surface_labels_core(
    labels: &[LabelRequest],
    left_limit: f64,
    right_limit: f64,
    top_limit: f64,
    bottom_limit: f64,
    gap: f64,
) -> Vec<Rect> {
    if labels.is_empty() {
        return Vec::new();
    }
    let gap = py_max(0.0, gap);
    let available = py_max(1.0, right_limit - left_limit);
    let max_height = py_max(1.0, bottom_limit - top_limit);

    let mut prepared: Vec<PreparedLabel> = labels
        .iter()
        .enumerate()
        .map(|(index, &(center_x, line_y, width, height))| {
            let width = py_min(py_max(1.0, width), available);
            let height = py_min(py_max(1.0, height), max_height);
            let preferred = skewt_surface_label_rect_core(
                center_x,
                line_y,
                width,
                height,
                left_limit,
                right_limit,
                top_limit,
                bottom_limit,
                4.0,
            );
            PreparedLabel {
                index,
                center_x,
                line_y,
                width,
                height,
                preferred,
            }
        })
        .collect();
    // Python sorts by (center_x, index); the stable sort over the
    // index-ordered preparation keeps index order on center_x ties.
    prepared.sort_by(|a, b| a.center_x.total_cmp(&b.center_x));

    // Split rows only when the complete cluster cannot fit into one.
    let mut rows: Vec<Vec<usize>> = Vec::new();
    let mut row: Vec<usize> = Vec::new();
    let mut used = 0.0;
    for (position, item) in prepared.iter().enumerate() {
        let mut extra = item.width + if row.is_empty() { 0.0 } else { gap };
        if !row.is_empty() && used + extra > available {
            rows.push(std::mem::take(&mut row));
            used = 0.0;
            extra = item.width;
        }
        row.push(position);
        used += extra;
    }
    if !row.is_empty() {
        rows.push(row);
    }

    let mut result: Vec<Rect> = vec![(0.0, 0.0, 0.0, 0.0); labels.len()];
    let above_count = prepared
        .iter()
        .filter(|item| item.preferred.1 + item.preferred.3 / 2.0 < item.line_y)
        .count();
    let primary_above = above_count as f64 >= prepared.len() as f64 / 2.0;
    let direction = if primary_above { -1.0 } else { 1.0 };
    let base_top = prepared
        .iter()
        .map(|item| item.preferred.1)
        .reduce(py_min)
        .unwrap_or_default();

    let row_fits = |top: f64, height: f64, placed_rows: &[(f64, f64)]| -> bool {
        if top < top_limit || top + height > bottom_limit {
            return false;
        }
        placed_rows.iter().all(|&(other_top, other_height)| {
            top + height + gap <= other_top || top >= other_top + other_height + gap
        })
    };

    let mut placed_rows: Vec<(f64, f64)> = Vec::new();
    for (row_index, current) in rows.iter().enumerate() {
        let row_height = current
            .iter()
            .map(|&position| prepared[position].height)
            .reduce(py_max)
            .unwrap_or_default();
        let row_top = if row_index == 0 {
            py_min(
                py_max(base_top, top_limit),
                py_max(top_limit, bottom_limit - row_height),
            )
        } else {
            let highest_top = placed_rows
                .iter()
                .map(|placed| placed.0)
                .reduce(py_min)
                .unwrap_or_default();
            let lowest_bottom = placed_rows
                .iter()
                .map(|placed| placed.0 + placed.1)
                .reduce(py_max)
                .unwrap_or_default();
            let above = highest_top - gap - row_height;
            let below = lowest_bottom + gap;
            let candidates: [f64; 2] = if direction < 0.0 {
                [above, below]
            } else {
                [below, above]
            };
            match candidates
                .iter()
                .copied()
                .find(|&candidate| row_fits(candidate, row_height, &placed_rows))
            {
                Some(candidate) => candidate,
                None => {
                    // Search every remaining bounded vertical slot, nearest
                    // the preferred surface-label row first.
                    let mut slot_candidates: Vec<f64> = vec![top_limit];
                    for &(other_top, other_height) in &placed_rows {
                        slot_candidates.push(other_top - gap - row_height);
                        slot_candidates.push(other_top + other_height + gap);
                    }
                    slot_candidates
                        .sort_by(|a, b| (a - base_top).abs().total_cmp(&(b - base_top).abs()));
                    let found = slot_candidates
                        .iter()
                        .copied()
                        .find(|&candidate| row_fits(candidate, row_height, &placed_rows));
                    match found {
                        Some(candidate) => candidate,
                        None => {
                            // Too small for another row: keep it bounded and
                            // minimize overlap instead of stacking on one edge.
                            let bounded = [above, below, base_top].map(|candidate| {
                                py_min(
                                    py_max(candidate, top_limit),
                                    py_max(top_limit, bottom_limit - row_height),
                                )
                            });
                            let cost = |candidate: f64| -> f64 {
                                placed_rows
                                    .iter()
                                    .map(|&(other_top, other_height)| {
                                        let overlap = py_min(
                                            candidate + row_height,
                                            other_top + other_height,
                                        ) - py_max(candidate, other_top);
                                        py_max(0.0, overlap + gap)
                                    })
                                    .sum()
                            };
                            let mut best = bounded[0];
                            let mut best_cost = cost(best);
                            let mut best_distance = (best - base_top).abs();
                            for &candidate in &bounded[1..] {
                                let candidate_cost = cost(candidate);
                                let candidate_distance = (candidate - base_top).abs();
                                if candidate_cost < best_cost
                                    || (candidate_cost == best_cost
                                        && candidate_distance < best_distance)
                                {
                                    best = candidate;
                                    best_cost = candidate_cost;
                                    best_distance = candidate_distance;
                                }
                            }
                            best
                        }
                    }
                }
            }
        };
        placed_rows.push((row_top, row_height));

        // Preferred left positions, then forward, backward, and forward
        // passes that keep the row inside the plot.
        let desired: Vec<f64> = current
            .iter()
            .map(|&position| {
                py_min(
                    py_max(prepared[position].preferred.0, left_limit),
                    right_limit - prepared[position].width,
                )
            })
            .collect();
        let mut lefts: Vec<f64> = Vec::with_capacity(current.len());
        let mut cursor = left_limit;
        for (position, &item) in current.iter().enumerate() {
            let left = py_max(desired[position], cursor);
            lefts.push(left);
            cursor = left + prepared[item].width + gap;
        }
        let mut cursor = right_limit;
        for position in (0..current.len()).rev() {
            let item = current[position];
            lefts[position] = py_min(lefts[position], cursor - prepared[item].width);
            cursor = lefts[position] - gap;
        }
        let mut cursor = left_limit;
        for (position, &item) in current.iter().enumerate() {
            lefts[position] = py_max(lefts[position], cursor);
            cursor = lefts[position] + prepared[item].width + gap;
        }

        for (position, &item) in current.iter().enumerate() {
            let label = &prepared[item];
            let mut top = row_top;
            if direction < 0.0 {
                // Bottom-align mixed font heights within an upward row.
                top += row_height - label.height;
            }
            result[label.index] = (lefts[position], top, label.width, label.height);
        }
    }
    result
}

/// `unit_text._UNIT_SUFFIXES`, Python's `sorted(..., key=len, reverse=True)`
/// order with stable ties preserved, then `unit_text._DEGREE_SUFFIXES`.
const UNIT_SUFFIXES: [&str; 17] = [
    " degrees C/km",
    " J kg\u{207b}\u{00b9} m\u{207b}\u{00b9}",
    " degrees C",
    " J kg\u{207b}\u{00b9}",
    " J/kg/m",
    " m\u{00b3}/s\u{00b3}",
    " m\u{00b2}/s\u{00b2}",
    " m AGL",
    " m2/s2",
    " C/km",
    " g/kg",
    " J/kg",
    " m/s",
    " kt",
    " cm",
    " in",
    " m",
];
const DEGREE_SUFFIXES: [&str; 3] = [" \u{00b0}F", "\u{00b0}F", "\u{00b0}C"];

/// `unit_text.split_value_unit`: split a sounding value from a recognized
/// trailing unit suffix, requiring a non-blank value prefix.
fn split_value_unit_core(text: &str) -> Option<(String, String)> {
    if text.is_empty() {
        return None;
    }
    for &suffix in UNIT_SUFFIXES.iter().chain(DEGREE_SUFFIXES.iter()) {
        if let Some(value) = text.strip_suffix(suffix) {
            if !value.is_empty() && !value.trim().is_empty() {
                return Some((value.to_string(), suffix.to_string()));
            }
        }
    }
    None
}

/// The display label for one AGL height (metres), in kilometres.
#[pyfunction]
fn hodo_label_for_height(height_m: f64) -> String {
    hodo_label_for_height_core(height_m)
}

/// Interpolated `(label, level_m, u, v)` samples for the requested AGL levels.
#[pyfunction]
fn hodo_prepare_height_wind_samples(
    heights: Vec<f64>,
    u_vals: Vec<f64>,
    v_vals: Vec<f64>,
    target_levels: Vec<f64>,
) -> Vec<HeightSample> {
    prepare_height_wind_samples_core(&heights, &u_vals, &v_vals, &target_levels)
}

/// `(label, height_m, source_x, source_y, center_x, center_y)` markers that
/// were placed without marker or annotation overlap.
#[pyfunction]
#[pyo3(signature = (points, bounds, exclusions = vec![], radius = 6.0))]
fn hodo_layout_height_level_markers(
    points: Vec<HeightSample>,
    bounds: PixelBounds,
    exclusions: Vec<Exclusion>,
    radius: f64,
) -> Vec<PlacedMarker> {
    layout_height_level_markers_core(&points, bounds, &exclusions, radius)
}

/// `rect` shifted and shrunk so it stays inside the Skew-T plot box.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn skewt_fit_rect(
    x: f64,
    y: f64,
    width: f64,
    height: f64,
    left_limit: f64,
    right_limit: f64,
    top_limit: f64,
    bottom_limit: f64,
) -> Rect {
    skewt_fit_rect_core(
        x,
        y,
        width,
        height,
        left_limit,
        right_limit,
        top_limit,
        bottom_limit,
    )
}

/// A near-surface label placed below its line, or above it when needed.
#[pyfunction]
#[pyo3(signature = (center_x, line_y, width, height, left_limit, right_limit, top_limit, bottom_limit, below_offset = 4.0))]
#[allow(clippy::too_many_arguments)]
fn skewt_surface_label_rect(
    center_x: f64,
    line_y: f64,
    width: f64,
    height: f64,
    left_limit: f64,
    right_limit: f64,
    top_limit: f64,
    bottom_limit: f64,
    below_offset: f64,
) -> Rect {
    skewt_surface_label_rect_core(
        center_x,
        line_y,
        width,
        height,
        left_limit,
        right_limit,
        top_limit,
        bottom_limit,
        below_offset,
    )
}

/// Surface-value label rectangles packed into bounded, non-overlapping rows.
#[pyfunction]
#[pyo3(signature = (labels, left_limit, right_limit, top_limit, bottom_limit, gap = 4.0))]
fn skewt_layout_surface_labels(
    labels: Vec<LabelRequest>,
    left_limit: f64,
    right_limit: f64,
    top_limit: f64,
    bottom_limit: f64,
    gap: f64,
) -> Vec<Rect> {
    skewt_layout_surface_labels_core(
        labels.as_slice(),
        left_limit,
        right_limit,
        top_limit,
        bottom_limit,
        gap,
    )
}

/// A `width x height` rect centered on `center_x`, clamped to the bounds.
#[pyfunction]
fn skewt_centered_rect(
    center_x: f64,
    top: f64,
    width: f64,
    height: f64,
    left_limit: f64,
    right_limit: f64,
) -> Rect {
    skewt_centered_rect_core(center_x, top, width, height, left_limit, right_limit)
}

/// Split a sounding value from a recognized trailing unit suffix.
#[pyfunction]
fn unit_split_value_suffix(text: &str) -> Option<(String, String)> {
    split_value_unit_core(text)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(hodo_label_for_height, module)?)?;
    module.add_function(wrap_pyfunction!(hodo_prepare_height_wind_samples, module)?)?;
    module.add_function(wrap_pyfunction!(hodo_layout_height_level_markers, module)?)?;
    module.add_function(wrap_pyfunction!(skewt_fit_rect, module)?)?;
    module.add_function(wrap_pyfunction!(skewt_surface_label_rect, module)?)?;
    module.add_function(wrap_pyfunction!(skewt_layout_surface_labels, module)?)?;
    module.add_function(wrap_pyfunction!(skewt_centered_rect, module)?)?;
    module.add_function(wrap_pyfunction!(unit_split_value_suffix, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    const STANDARD_LEVELS: [(f64, &str); 6] = [
        (500.0, "0.5"),
        (1000.0, "1"),
        (3000.0, "3"),
        (6000.0, "6"),
        (9000.0, "9"),
        (12000.0, "12"),
    ];

    fn markers_at(levels: &[(f64, &str)], x: f64, y: f64) -> Vec<HeightSample> {
        levels
            .iter()
            .map(|&(height, label)| (label.to_string(), height, x, y))
            .collect()
    }

    #[test]
    fn hodo_label_formats_heights_in_km() {
        assert_eq!(hodo_label_for_height_core(500.0), "0.5");
        assert_eq!(hodo_label_for_height_core(1000.0), "1");
        assert_eq!(hodo_label_for_height_core(1500.0), "1.5");
        assert_eq!(hodo_label_for_height_core(2500.0), "2.5");
        assert_eq!(hodo_label_for_height_core(9500.0), "9.5");
        assert_eq!(hodo_label_for_height_core(12000.0), "12");
        assert_eq!(hodo_label_for_height_core(0.0), "0");
        // 250 m formats as 0.25 km with banker's rounding at the first decimal.
        assert_eq!(hodo_label_for_height_core(250.0), "0.2");
    }

    #[test]
    fn hodo_prepare_matches_the_widget_profile_exact() {
        let heights = [0.0, 500.0, 1000.0, 3000.0, 6000.0, 9000.0, 12000.0, 13000.0];
        let u = [0.0, 5.0, 10.0, 30.0, 60.0, 90.0, 120.0, 130.0];
        let v = [0.0, 2.5, 5.0, 15.0, 30.0, 45.0, 60.0, 65.0];
        let levels = [500.0, 1000.0, 3000.0, 6000.0, 9000.0, 12000.0];
        let samples = prepare_height_wind_samples_core(&heights, &u, &v, &levels);
        assert_eq!(
            samples,
            vec![
                ("0.5".to_string(), 500.0, 5.0, 2.5),
                ("1".to_string(), 1000.0, 10.0, 5.0),
                ("3".to_string(), 3000.0, 30.0, 15.0),
                ("6".to_string(), 6000.0, 60.0, 30.0),
                ("9".to_string(), 9000.0, 90.0, 45.0),
                ("12".to_string(), 12000.0, 120.0, 60.0),
            ]
        );
    }

    #[test]
    fn hodo_prepare_filters_nonfinite_and_out_of_range() {
        let heights = [100.0, f64::NAN, 600.0, f64::INFINITY, 1100.0];
        let u = [1.0, 5.0, 4.0, 2.0, 9.0];
        let v = [1.0, 2.0, 3.0, 4.0, 5.0];
        let levels = [1000.0, f64::NAN, 700.0, 2500.0, -5.0];
        let samples = prepare_height_wind_samples_core(&heights, &u, &v, &levels);
        assert_eq!(
            samples,
            vec![
                ("1".to_string(), 1000.0, 8.0, 4.6),
                ("0.7".to_string(), 700.0, 5.0, 3.4),
            ]
        );
    }

    #[test]
    fn hodo_prepare_rejects_thin_or_duplicate_profiles() {
        let two = [0.0, 1000.0];
        let one = [0.0];
        let valid = [1.0, 2.0];
        assert_eq!(
            prepare_height_wind_samples_core(&two, &valid, &valid, &two).len(),
            2
        );
        assert!(prepare_height_wind_samples_core(&one, &one, &one, &one).is_empty());
        // Fewer than two finite triples.
        let gappy = [0.0, f64::NAN];
        assert!(prepare_height_wind_samples_core(&gappy, &valid, &valid, &gappy).is_empty());
        // Fewer than two unique levels, like np.unique collapsing.
        let duplicated = [500.0, 500.0, 500.0];
        let dup_values = [1.0, 2.0, 3.0];
        assert!(
            prepare_height_wind_samples_core(&duplicated, &dup_values, &dup_values, &two)
                .is_empty()
        );
        // Mismatched vector lengths zip down to the common prefix.
        let long_u = [1.0, 2.0, 7.0];
        let samples = prepare_height_wind_samples_core(&two, &long_u, &valid, &[500.0]);
        assert_eq!(samples, vec![("0.5".to_string(), 500.0, 1.5, 1.5)]);
    }

    #[test]
    fn hodo_prepare_keeps_the_first_of_duplicate_heights() {
        let heights = [1000.0, 1000.0, 2000.0];
        let u = [1.0, 2.0, 4.0];
        let v = [1.0, 2.0, 4.0];
        let samples = prepare_height_wind_samples_core(&heights, &u, &v, &[1500.0]);
        // The first (u=1, v=1) sample is kept, so 1500 m interpolates to 2.5;
        // keeping the second sample would have produced 3.0.
        assert_eq!(samples, vec![("1.5".to_string(), 1500.0, 2.5, 2.5)]);
    }

    #[test]
    fn hodo_layout_deconflicts_collocated_markers_deterministically() {
        let points = markers_at(&STANDARD_LEVELS, 150.0, 150.0);
        let bounds = (0.0, 0.0, 320.0, 300.0);
        let first = layout_height_level_markers_core(&points, bounds, &[], 6.0);
        let second = layout_height_level_markers_core(&points, bounds, &[], 6.0);
        assert_eq!(first, second);
        assert_eq!(first.len(), STANDARD_LEVELS.len());
        assert_eq!(
            first[0],
            ("0.5".to_string(), 500.0, 150.0, 150.0, 150.0, 150.0)
        );
        let minimum_separation = 6.0 * 2.0 + MARKER_GAP_PX;
        for left in &first {
            for right in &first {
                let distance = (left.4 - right.4).hypot(left.5 - right.5);
                if left != right {
                    assert!(distance >= minimum_separation - 1e-6, "{distance}");
                }
            }
        }
    }

    #[test]
    fn hodo_layout_validates_bounds_and_radius() {
        let points = markers_at(&STANDARD_LEVELS, 150.0, 150.0);
        assert!(
            layout_height_level_markers_core(&points, (f64::NAN, 0.0, 320.0, 300.0), &[], 6.0)
                .is_empty()
        );
        assert!(
            layout_height_level_markers_core(&points, (0.0, f64::NAN, 320.0, 300.0), &[], 6.0)
                .is_empty()
        );
        // Degenerate bounds: left >= right or top >= bottom.
        assert!(
            layout_height_level_markers_core(&points, (100.0, 0.0, 100.0, 300.0), &[], 6.0)
                .is_empty()
        );
        assert!(
            layout_height_level_markers_core(&points, (0.0, 0.0, 320.0, 0.0), &[], 6.0).is_empty()
        );
        // Radius must be finite and positive.
        assert!(
            layout_height_level_markers_core(&points, (0.0, 0.0, 320.0, 300.0), &[], 0.0)
                .is_empty()
        );
        assert!(
            layout_height_level_markers_core(&points, (0.0, 0.0, 320.0, 300.0), &[], f64::NAN)
                .is_empty()
        );
    }

    #[test]
    fn hodo_layout_moves_markers_outside_the_locator() {
        let points = markers_at(&STANDARD_LEVELS, 80.0, 50.0);
        let locator = (1.0, 1.0, 151.0, 97.0);
        let layout =
            layout_height_level_markers_core(&points, (2.0, 2.0, 418.0, 318.0), &[locator], 6.0);
        assert_eq!(layout.len(), STANDARD_LEVELS.len());
        // Heights and sources pass through untouched.
        assert_eq!(
            layout.iter().map(|marker| marker.1).collect::<Vec<f64>>(),
            vec![500.0, 1000.0, 3000.0, 6000.0, 9000.0, 12000.0]
        );
        assert!(layout
            .iter()
            .all(|marker| marker.2 == 80.0 && marker.3 == 50.0));
        for marker in &layout {
            let (x, y) = (marker.4, marker.5);
            assert!(
                x - 6.0 >= locator.2
                    || x + 6.0 <= locator.0
                    || y - 6.0 >= locator.3
                    || y + 6.0 <= locator.1,
                "({x}, {y})"
            );
        }
    }

    #[test]
    fn hodo_layout_skips_unusable_input() {
        // A source whose circle leaves the inner plot box is dropped.
        let cramped = vec![("0.5".to_string(), 500.0, 2.0, 50.0)];
        assert!(
            layout_height_level_markers_core(&cramped, (0.0, 0.0, 100.0, 100.0), &[], 6.0)
                .is_empty()
        );
        // Non-finite sources are dropped.
        let nonfinite = vec![("0.5".to_string(), 500.0, f64::NAN, 50.0)];
        assert!(
            layout_height_level_markers_core(&nonfinite, (0.0, 0.0, 100.0, 100.0), &[], 6.0)
                .is_empty()
        );
        // Degenerate exclusions are ignored instead of blocking placement.
        let inner = vec![("0.5".to_string(), 500.0, 15.0, 15.0)];
        let degenerate = [(10.0, 10.0, 10.0, 20.0), (f64::NAN, 0.0, 60.0, 60.0)];
        let layout =
            layout_height_level_markers_core(&inner, (0.0, 0.0, 100.0, 100.0), &degenerate, 6.0);
        assert_eq!(layout.len(), 1);
        assert_eq!(layout[0].4, 15.0);
        assert_eq!(layout[0].5, 15.0);
    }

    #[test]
    fn hodo_projection_candidates_sort_by_distance() {
        // Like Python's sorted(candidates, key=lambda point: (dist, index)).
        assert_eq!(
            projection_candidates(80.0, 50.0, (1.0, 1.0, 151.0, 97.0), 6.0),
            vec![(80.0, 105.0), (80.0, -7.0), (159.0, 50.0), (-7.0, 50.0)]
        );
        // Equal distances keep the candidate's original order.
        assert_eq!(
            projection_candidates(60.0, 60.0, (10.0, 10.0, 100.0, 100.0), 6.0),
            vec![(108.0, 60.0), (60.0, 108.0), (2.0, 60.0), (60.0, 2.0)]
        );
    }

    #[test]
    fn hodo_candidate_centers_round_and_dedupe() {
        // Two exclusions with identical projections extend the same four
        // candidates twice; the dict.fromkeys dedupe collapses them.
        let exclusions = [(10.0, 10.0, 100.0, 100.0), (15.0, 15.0, 100.0, 100.0)];
        let centers = candidate_centers(60.0, 60.0, &exclusions, 14.0, 6.0);
        assert_eq!(centers[0], (60.0, 60.0));
        assert_eq!(centers[1], (108.0, 60.0));
        assert_eq!(centers[2], (60.0, 108.0));
        assert_eq!(centers.iter().filter(|c| **c == (108.0, 60.0)).count(), 1);
        // Ring candidates are rounded to six decimals like round(value, 6).
        assert!(centers.contains(&(69.899495, 69.899495)));
        // Total raw candidates are 9 seeds + 9 * 80 ring points = 729; every
        // duplicate seed's rings collapse back onto the first seed's.
        assert!(centers.len() < 729);
        assert!(centers.len() >= 405);
        // No exclusions: seed plus 5 rings of 16 angles.
        let plain = candidate_centers(100.0, 100.0, &[], 14.0, 6.0);
        assert_eq!(plain[0], (100.0, 100.0));
        assert_eq!(plain[1], (114.0, 100.0));
    }

    #[test]
    fn skewt_fit_rect_clamps_inside_the_plot() {
        // Inside the plot box: unchanged.
        assert_eq!(
            skewt_fit_rect_core(50.0, 20.0, 30.0, 10.0, 40.0, 560.0, 10.0, 690.0),
            (50.0, 20.0, 30.0, 10.0)
        );
        // Outside: shifted inside with the same size.
        assert_eq!(
            skewt_fit_rect_core(0.0, -10.0, 200.0, 50.0, 40.0, 560.0, 10.0, 690.0),
            (40.0, 10.0, 200.0, 50.0)
        );
        // Oversized: shrunk to the plot's width.
        assert_eq!(
            skewt_fit_rect_core(0.0, 0.0, 1000.0, 50.0, 40.0, 560.0, 10.0, 690.0),
            (40.0, 10.0, 520.0, 50.0)
        );
        // Degenerate plot box: returned as-is.
        assert_eq!(
            skewt_fit_rect_core(0.0, 0.0, 100.0, 100.0, 560.0, 40.0, 10.0, 690.0),
            (0.0, 0.0, 100.0, 100.0)
        );
    }

    #[test]
    fn skewt_surface_label_rect_flips_above_near_the_bottom() {
        // Below the line, comfortably inside the plot.
        assert_eq!(
            skewt_surface_label_rect_core(100.0, 50.0, 40.0, 20.0, 40.0, 560.0, 10.0, 690.0, 4.0),
            (80.0, 54.0, 40.0, 20.0)
        );
        // Too close to the bottom limit: flipped above the line.
        assert_eq!(
            skewt_surface_label_rect_core(100.0, 680.0, 40.0, 20.0, 40.0, 560.0, 10.0, 690.0, 4.0),
            (80.0, 656.0, 40.0, 20.0)
        );
        // An oversized width centers on the raw width, then the fit pulls it inside.
        assert_eq!(
            skewt_surface_label_rect_core(100.0, 50.0, 700.0, 20.0, 40.0, 560.0, 10.0, 690.0, 4.0),
            (40.0, 54.0, 520.0, 20.0)
        );
        // Degenerate plot box: returned as-is.
        assert_eq!(
            skewt_surface_label_rect_core(100.0, 50.0, 40.0, 20.0, 560.0, 40.0, 10.0, 690.0, 4.0),
            (80.0, 54.0, 40.0, 20.0)
        );
    }

    #[test]
    fn skewt_layout_single_row_keeps_input_order() {
        let labels = [
            (200.0, 560.0, 50.0, 14.0),
            (120.0, 560.0, 50.0, 14.0),
            (400.0, 560.0, 50.0, 14.0),
        ];
        let result = skewt_layout_surface_labels_core(&labels, 40.0, 560.0, 10.0, 690.0, 4.0);
        assert_eq!(
            result,
            vec![
                (175.0, 564.0, 50.0, 14.0),
                (95.0, 564.0, 50.0, 14.0),
                (375.0, 564.0, 50.0, 14.0),
            ]
        );
    }

    #[test]
    fn skewt_layout_splits_rows_when_narrow() {
        // Three 60-wide labels in a 130-wide plot: two fit in a row, the third
        // opens the row below.
        let labels = [
            (160.0, 400.0, 60.0, 14.0),
            (100.0, 400.0, 60.0, 14.0),
            (130.0, 400.0, 60.0, 14.0),
        ];
        let result = skewt_layout_surface_labels_core(&labels, 40.0, 170.0, 10.0, 690.0, 4.0);
        assert_eq!(
            result,
            vec![
                (110.0, 422.0, 60.0, 14.0),
                (46.0, 404.0, 60.0, 14.0),
                (110.0, 404.0, 60.0, 14.0),
            ]
        );
    }

    #[test]
    fn skewt_layout_stacks_rows_upward_when_majority_above() {
        // Both anchors sit near the bottom limit, so both flip above their
        // lines: the majority is above and the single row grows upward with
        // bottom-aligned mixed heights.
        let labels = [(100.0, 680.0, 30.0, 14.0), (160.0, 680.0, 30.0, 20.0)];
        let result = skewt_layout_surface_labels_core(&labels, 40.0, 560.0, 10.0, 690.0, 4.0);
        assert_eq!(
            result,
            vec![(85.0, 662.0, 30.0, 14.0), (145.0, 656.0, 30.0, 20.0),]
        );
    }

    #[test]
    fn skewt_centered_rect_clamps_width_and_center() {
        assert_eq!(
            skewt_centered_rect_core(50.0, 5.0, 40.0, 10.0, 40.0, 200.0),
            (40.0, 5.0, 40.0, 10.0)
        );
        // An oversized width shrinks to the available span.
        assert_eq!(
            skewt_centered_rect_core(100.0, 5.0, 400.0, 10.0, 40.0, 200.0),
            (40.0, 5.0, 160.0, 10.0)
        );
        // A centered rect that already fits is centered exactly.
        assert_eq!(
            skewt_centered_rect_core(100.0, 5.0, 40.0, 10.0, 40.0, 200.0),
            (80.0, 5.0, 40.0, 10.0)
        );
    }

    #[test]
    fn split_value_unit_matches_python() {
        let split = |text: &str| split_value_unit_core(text);
        assert_eq!(
            split("14.86 g/kg"),
            Some(("14.86".to_string(), " g/kg".to_string()))
        );
        assert_eq!(
            split("245/23 kt"),
            Some(("245/23".to_string(), " kt".to_string()))
        );
        assert_eq!(
            split("90\u{00b0}F"),
            Some(("90".to_string(), "\u{00b0}F".to_string()))
        );
        assert_eq!(
            split("81 \u{00b0}F"),
            Some(("81".to_string(), " \u{00b0}F".to_string()))
        );
        assert_eq!(
            split("16 m\u{00b2}/s\u{00b2}"),
            Some(("16".to_string(), " m\u{00b2}/s\u{00b2}".to_string()))
        );
        assert_eq!(
            split("16744 m\u{00b3}/s\u{00b3}"),
            Some(("16744".to_string(), " m\u{00b3}/s\u{00b3}".to_string()))
        );
        assert_eq!(
            split("942 J kg\u{207b}\u{00b9}"),
            Some(("942".to_string(), " J kg\u{207b}\u{00b9}".to_string()))
        );
        assert_eq!(
            split("0.23 J kg\u{207b}\u{00b9} m\u{207b}\u{00b9}"),
            Some((
                "0.23".to_string(),
                " J kg\u{207b}\u{00b9} m\u{207b}\u{00b9}".to_string()
            ))
        );
        // A non-break between value and unit is not a recognized suffix.
        assert_eq!(split("2.23\u{a0}in"), None);
        assert_eq!(
            split(">= 2 in"),
            Some((">= 2".to_string(), " in".to_string()))
        );
        assert_eq!(split("Supercell Comp = "), None);
        assert_eq!(split(""), None);
        assert_eq!(split(" m"), None);
        assert_eq!(
            split("5 degrees C/km"),
            Some(("5".to_string(), " degrees C/km".to_string()))
        );
        assert_eq!(
            split("12 C/km"),
            Some(("12".to_string(), " C/km".to_string()))
        );
        assert_eq!(
            split("5 m AGL"),
            Some(("5".to_string(), " m AGL".to_string()))
        );
        assert_eq!(
            split("5 J/kg/m"),
            Some(("5".to_string(), " J/kg/m".to_string()))
        );
    }
}

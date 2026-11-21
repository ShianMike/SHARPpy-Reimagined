use super::geometry::{
    bounds, center_longitude, clip_to_window, scales_at, validate_ring, wrapped_point, LAT_LIMIT,
    LON_LIMIT, MAX_VERTICES,
};
use i_overlay::i_float::int::point::IntPoint;
use i_overlay::i_shape::int::area::Area;
use i_overlay::i_shape::int::shape::{IntContour, IntShape};
use std::ops::RangeInclusive;

const TARGET_VERTICES: usize = 12;
const MAX_EDIT_ERROR_KM: f64 = 20.0;

pub(super) fn points(shapes: &[IntShape]) -> Option<Vec<[f64; 2]>> {
    // Use one continuous exterior before the display geometry is cut at ±180°.
    // Very wide envelopes still need a seed within the authored longitude limit.
    let shape = shapes
        .iter()
        .flat_map(|shape| {
            let (min, max) = bounds(&shape[0]);
            if max.x - min.x >= LON_LIMIT {
                let center = center_longitude(&shape[0]);
                let half_span = LON_LIMIT / 2 - 1;
                clip_to_window(shape, center - half_span, center + half_span)
            } else if min.y < -LAT_LIMIT || max.y > LAT_LIMIT {
                clip_to_window(shape, min.x, max.x)
            } else {
                vec![shape.clone()]
            }
        })
        .max_by_key(|shape| -shape[0].area_two())?;
    let mut ring = shape.into_iter().next()?;
    let start = (0..ring.len()).min_by_key(|&i| (ring[i].x, ring[i].y))?;
    ring.rotate_left(start);

    let (min, max) = bounds(&ring);
    let (lon_scale, lat_scale) = scales_at(min.y.midpoint(max.y));
    let local: Vec<_> = ring
        .iter()
        .chain(ring.first())
        .map(|p| {
            [
                f64::from(p.x - ring[0].x) * lon_scale,
                f64::from(p.y - ring[0].y) * lat_scale,
            ]
        })
        .collect();
    let opposite = (1..ring.len()).max_by(|&a, &b| {
        let [ax, ay] = local[a];
        let [bx, by] = local[b];
        (ax * ax + ay * ay).total_cmp(&(bx * bx + by * by))
    })?;
    let area = -ring.area_two();
    let candidate = |tolerance, allowed_area: RangeInclusive<i64>| {
        let keep = simplify(&local, opposite, tolerance);
        let candidate: IntContour = ring
            .iter()
            .zip(keep)
            .filter_map(|(&point, keep)| keep.then_some(point))
            .collect();
        if !(3..=MAX_VERTICES).contains(&candidate.len())
            || !allowed_area.contains(&-candidate.area_two())
        {
            return None;
        }
        let points = candidate.into_iter().map(wrapped_point).collect::<Vec<_>>();
        validate_ring(&points).ok()?;
        Some(points)
    };

    // Twice the area divided by the perimeter estimates the effective width
    // in km, even for a long, thin diagonal contour.
    let perimeter: f64 = local
        .windows(2)
        .map(|edge| (edge[1][0] - edge[0][0]).hypot(edge[1][1] - edge[0][1]))
        .sum();
    let tolerance_limit =
        (0.2 * area as f64 * lon_scale * lat_scale / perimeter).min(MAX_EDIT_ERROR_KM);
    let area_margin = area * 8 / 100;
    let allowed_area = area - area_margin..=area + area_margin;
    let mut best: Option<Vec<_>> = None;
    for step in [1.0_f64, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0, 16.0, 20.0] {
        let tolerance = step.min(tolerance_limit);
        if let Some(points) = candidate(tolerance, allowed_area.clone()) {
            if points.len() <= TARGET_VERTICES {
                return Some(points);
            }
            if best.as_ref().is_none_or(|best| points.len() < best.len()) {
                best = Some(points);
            }
        }
        if tolerance == tolerance_limit {
            break;
        }
    }
    if best.is_some() {
        return best;
    }

    // A small envelope may lose too much area even at the first tolerance.
    let first_tolerance = 1.0_f64.min(tolerance_limit);
    for fraction in [0.5, 0.25] {
        if let Some(points) = candidate(first_tolerance * fraction, allowed_area.clone()) {
            return Some(points);
        }
    }

    // Keep the existing recovery path for contours with no valid candidate
    // within the edit error budget.
    for tolerance in [1.0, 0.25, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0] {
        if let Some(points) = candidate(tolerance, (area + 1) / 2..=i64::MAX) {
            return Some(points);
        }
    }

    // A narrow concavity can block a shortcut even at a small tolerance. For
    // dense rings, remove only ears that do not cross the rest of the ring.
    let points = cap_vertices(ring)
        .into_iter()
        .map(wrapped_point)
        .collect::<Vec<_>>();
    validate_ring(&points).ok()?;
    Some(points)
}

fn simplify(points: &[[f64; 2]], opposite: usize, tolerance: f64) -> Vec<bool> {
    let last = points.len() - 1;
    let mut keep = vec![false; points.len()];
    keep[0] = true;
    keep[opposite] = true;
    let mut ranges = vec![(0, opposite), (opposite, last)];
    while let Some((start, end)) = ranges.pop() {
        let [ax, ay] = points[start];
        let [bx, by] = points[end];
        let (dx, dy) = (bx - ax, by - ay);
        let length_squared = (dx * dx + dy * dy).max(f64::MIN_POSITIVE);
        let mut distance_squared = tolerance * tolerance;
        let mut furthest = None;
        for (index, &[x, y]) in points.iter().enumerate().take(end).skip(start + 1) {
            let x = x - ax;
            let y = y - ay;
            let t = ((x * dx + y * dy) / length_squared).clamp(0.0, 1.0);
            let distance = (x - t * dx).powi(2) + (y - t * dy).powi(2);
            if distance > distance_squared {
                distance_squared = distance;
                furthest = Some(index);
            }
        }
        if let Some(index) = furthest {
            keep[index] = true;
            ranges.extend([(start, index), (index, end)]);
        }
    }
    keep
}

fn cap_vertices(mut ring: IntContour) -> IntContour {
    let mut cursor = 0;
    let mut remaining = ring.len();
    while ring.len() > MAX_VERTICES && remaining > 0 {
        let n = ring.len();
        let i = cursor % n;
        let before = (i + n - 1) % n;
        let after = (i + 1) % n;
        let [a, b, c] = [ring[before], ring[i], ring[after]];
        let turn =
            |a: IntPoint, b: IntPoint, c: IntPoint| b.subtract(a).cross_product(c.subtract(a));
        if turn(a, b, c) >= 0
            && !ring.iter().enumerate().any(|(j, &p)| {
                j != before
                    && j != i
                    && j != after
                    && turn(a, b, p) >= 0
                    && turn(b, c, p) >= 0
                    && turn(c, a, p) >= 0
            })
        {
            ring.remove(i);
            remaining = ring.len();
        } else {
            remaining -= 1;
            cursor += 1;
        }
    }
    ring
}

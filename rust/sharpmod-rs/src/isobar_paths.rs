//! Connected display paths for pressure, thickness and height contours.

use std::collections::{HashMap, HashSet};

use crate::contours::Segment;

type Point = [f64; 2];

#[derive(Clone, Copy, Eq, Hash, PartialEq)]
struct AxisKey {
    sample: usize,
    fraction: Option<u32>,
}

impl AxisKey {
    fn new(coordinate: f64) -> Self {
        let sample = coordinate.floor();
        let fraction = coordinate - sample;
        Self {
            sample: sample as usize,
            // Keep the unrounded sample interval and distinguish exact vertices
            // from crossings. Nearby crossings on distinct edges cannot weld
            // together when the rounded fraction reaches a grid vertex.
            fraction: (fraction != 0.0).then(|| (fraction * 1e9).round() as u32),
        }
    }
}

struct Node {
    point: Point,
    degree: usize,
    incident: [usize; 2],
}

impl Node {
    fn attach(&mut self, edge: usize) {
        // Only degree-two nodes need adjacency to continue a path.
        if self.degree < 2 {
            self.incident[self.degree] = edge;
        }
        self.degree += 1;
    }
}

struct Trace {
    level: f64,
    points: Vec<Point>,
    ring: bool,
}

struct Curve {
    controls: [Point; 3],
    steps: usize,
}

impl Curve {
    fn point(&self, t: f64) -> Point {
        let [start, corner, end] = self.controls;
        let u = 1.0 - t;
        [0, 1].map(|axis| u * u * start[axis] + 2.0 * u * t * corner[axis] + t * t * end[axis])
    }
}

/// Connect and round display contours in their original sample-grid coordinates.
///
/// Input is the validated output of the display contour extractor (at most
/// 100,000 segments), with its original grid. The result has at most two vertices
/// per input segment, including repeated closing vertices. A corner remains
/// intact when it cannot be rounded within this budget or over finite samples.
/// Heights, widths, coordinates and levels stay in their original units.
pub(crate) fn smooth_paths(
    segments: &[(f64, Segment)],
    values: &[f64],
    height: usize,
    width: usize,
) -> Vec<(f64, Vec<[f64; 2]>)> {
    let mut nodes: Vec<Node> = Vec::new();
    let mut edges = Vec::with_capacity(segments.len());
    {
        let mut by_position = HashMap::new();
        let mut unique_edges = HashSet::new();
        for &(level, (a, b)) in segments {
            let level_key = if level == 0.0 { 0 } else { level.to_bits() };
            let [a, b] = [[a.0, a.1], [b.0, b.1]].map(|point| {
                let key = (level_key, point.map(AxisKey::new));
                *by_position.entry(key).or_insert_with(|| {
                    let index = nodes.len();
                    nodes.push(Node {
                        point,
                        degree: 0,
                        incident: [0; 2],
                    });
                    index
                })
            });
            // Exact-level extrema can produce zero-length or duplicate segments.
            if a == b || !unique_edges.insert((a.min(b), a.max(b))) {
                continue;
            }
            nodes[a].attach(edges.len());
            nodes[b].attach(edges.len());
            edges.push((level, [a, b]));
        }
    }

    let mut traces = Vec::new();
    let mut visited = vec![false; edges.len()];
    // Start with open paths and junctions; remaining components are rings.
    for open_only in [true, false] {
        for (index, &(level, [a, b])) in edges.iter().enumerate() {
            if visited[index] {
                continue;
            }
            let start = if nodes[a].degree != 2 {
                a
            } else if nodes[b].degree != 2 {
                b
            } else if open_only {
                continue;
            } else {
                a
            };
            let mut points = vec![nodes[start].point];
            let (mut node, mut edge) = (start, index);
            while !visited[edge] {
                visited[edge] = true;
                let [a, b] = edges[edge].1;
                node = if node == a { b } else { a };
                let next = &nodes[node];
                points.push(next.point);
                if next.degree != 2 {
                    break;
                }
                edge = if next.incident[0] == edge {
                    next.incident[1]
                } else {
                    next.incident[0]
                };
            }
            remove_collinear(&mut points);
            traces.push(Trace {
                level,
                points,
                // A path that returns to a junction still pins its endpoints.
                ring: node == start && nodes[start].degree == 2,
            });
        }
    }

    let mut requested = 0;
    let curves: Vec<Vec<_>> = traces
        .iter()
        .map(|Trace { points, ring, .. }| {
            let last = points.len() - 1;
            (usize::from(!ring)..last)
                .filter_map(|index| {
                    let previous = if index == 0 { last - 1 } else { index - 1 };
                    let curve = round_corner(
                        points[previous],
                        points[index],
                        points[index + 1],
                        values,
                        height,
                        width,
                    )?;
                    requested += curve.steps;
                    Some((index, curve))
                })
                .collect()
        })
        .collect();
    // A trace of E edges uses at most E+1 vertices. Keep every original path
    // within the existing wire budget and share the spare points among bends.
    let base_points = traces.iter().map(|trace| trace.points.len()).sum::<usize>();
    let extras = (2 * segments.len() - base_points).min(requested) as u64;
    let requested = requested as u64;
    let mut balance = requested / 2;
    traces
        .into_iter()
        .zip(curves)
        .map(|(trace, curves)| {
            let Trace {
                level,
                points,
                ring,
            } = trace;
            if curves.is_empty() || extras == 0 {
                return (level, points);
            }
            let mut rounded = Vec::with_capacity(points.len());
            let mut start = 0;
            for (index, curve) in curves {
                balance += curve.steps as u64 * extras;
                let steps = (balance / requested) as usize;
                balance %= requested;
                rounded.extend_from_slice(&points[start..index]);
                if steps == 0 {
                    rounded.push(points[index]);
                } else {
                    rounded.extend((0..=steps).map(|step| curve.point(step as f64 / steps as f64)));
                }
                start = index + 1;
            }
            rounded.extend_from_slice(&points[start..points.len() - usize::from(ring)]);
            if ring {
                rounded.push(rounded[0]);
            }
            (level, rounded)
        })
        .collect()
}

fn remove_collinear(points: &mut Vec<Point>) {
    let mut previous = points[0];
    let mut write = 1;
    let last = points.len() - 1;
    for i in 1..last {
        let current = points[i];
        let next = points[i + 1];
        let (dx, dy) = (current[0] - previous[0], current[1] - previous[1]);
        let (ex, ey) = (next[0] - current[0], next[1] - current[1]);
        // Only remove a point on a straight continuation, never a reversal.
        if dx * ey != dy * ex || dx * ex + dy * ey <= 0.0 {
            points[write] = current;
            write += 1;
        }
        previous = current;
    }
    points[write] = points[last];
    points.truncate(write + 1);
}

fn round_corner(
    a: Point,
    corner: Point,
    b: Point,
    values: &[f64],
    height: usize,
    width: usize,
) -> Option<Curve> {
    let (dx, dy) = (a[0] - corner[0], a[1] - corner[1]);
    let (ex, ey) = (b[0] - corner[0], b[1] - corner[1]);
    let (incoming, outgoing) = (dx.hypot(dy), ex.hypot(ey));
    if (dx * ey - dy * ex).abs() <= 1e-8 * incoming * outgoing {
        return None;
    }
    // Use equal cuts to bisect the bend. Even along an enlarged extraction
    // cell's edge, rounding stays within half an original sample of the corner.
    let cut = (0.45 * incoming.min(outgoing)).min(0.5);
    // The largest deviation of the quadratic from the bevel is at its midpoint.
    // Leave near-straight joins alone and spend vertices on the visible bends.
    let deviation =
        cut * (dx / incoming + ex / outgoing).hypot(dy / incoming + ey / outgoing) / 4.0;
    let steps = (deviation / 0.0125).sqrt().ceil() as usize;
    if steps <= 1 {
        return None;
    }
    let before = [
        corner[0] + dx * (cut / incoming),
        corner[1] + dy * (cut / incoming),
    ];
    let after = [
        corner[0] + ex * (cut / outgoing),
        corner[1] + ey * (cut / outgoing),
    ];
    if before == corner || after == corner || before == after {
        return None;
    }
    let left = before[0].min(after[0]).min(corner[0]).floor() as usize;
    let right = (before[0].max(after[0]).max(corner[0]).ceil() as usize).min(width - 1);
    let top = before[1].min(after[1]).min(corner[1]).floor() as usize;
    let bottom = (before[1].max(after[1]).max(corner[1]).ceil() as usize).min(height - 1);
    // Include all samples in cells touched by the cut's bounding footprint,
    // including those supporting the original corner. The cut limit keeps the
    // work per corner constant, independent of the grid's size.
    (top..=bottom)
        .all(|row| {
            values[row * width + left..=row * width + right]
                .iter()
                .all(|value| value.is_finite())
        })
        .then_some(Curve {
            controls: [before, corner, after],
            steps,
        })
}

#[cfg(test)]
mod tests;

//! Connected display paths for mean sea-level pressure contours.

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

/// Connect and round display contours in their original sample-grid coordinates.
///
/// Input is the validated output of the display contour extractor (at most
/// 100,000 segments), with its original grid. The result has at most two vertices
/// per input segment, including repeated closing vertices. A corner remains
/// intact when it cannot be rounded within this budget or over finite samples.
/// Heights, widths, coordinates and levels stay in their original units.
pub(crate) fn smooth_isobars(
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

    // A trace of E edges uses E+1 vertices before simplification. Every trace
    // has at least one edge, so all traces fit even if every corner stays intact.
    let mut spare =
        2 * segments.len() - traces.iter().map(|trace| trace.points.len()).sum::<usize>();
    traces
        .into_iter()
        .map(|trace| {
            let Trace {
                level,
                points,
                ring,
            } = trace;
            if points.len() < 3 || spare == 0 {
                return (level, points);
            }
            let mut rounded = Vec::with_capacity(points.len());
            rounded.push(points[0]);
            for window in points.windows(3) {
                let (a, corner, b) = (window[0], window[1], window[2]);
                if spare > 0 {
                    if let Some((before, after)) = round_corner(a, corner, b, values, height, width)
                    {
                        rounded.extend([before, after]);
                        spare -= 1;
                        continue;
                    }
                }
                rounded.push(corner);
            }
            let last = points[points.len() - 1];
            if ring && spare > 0 {
                if let Some((before, after)) = round_corner(
                    points[points.len() - 2],
                    last,
                    points[1],
                    values,
                    height,
                    width,
                ) {
                    rounded[0] = after;
                    rounded.extend([before, after]);
                    spare -= 1;
                    return (level, rounded);
                }
            }
            rounded.push(last);
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
) -> Option<(Point, Point)> {
    let (dx, dy) = (a[0] - corner[0], a[1] - corner[1]);
    let (ex, ey) = (b[0] - corner[0], b[1] - corner[1]);
    let (incoming, outgoing) = (dx.hypot(dy), ex.hypot(ey));
    if (dx * ey - dy * ex).abs() <= 1e-8 * incoming * outgoing {
        return None;
    }
    // Use equal cuts to bisect the bend. Even along an enlarged extraction
    // cell's edge, rounding stays within half an original sample of the corner.
    let cut = (0.25 * incoming.min(outgoing)).min(0.5);
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
        .then_some((before, after))
}

#[cfg(test)]
mod tests;

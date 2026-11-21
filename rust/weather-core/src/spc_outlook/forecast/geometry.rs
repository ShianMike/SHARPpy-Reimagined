use super::RISK_SPACING_KM;
use i_overlay::core::extract::BooleanExtractionBuffer;
use i_overlay::core::fill_rule::FillRule;
use i_overlay::core::overlay::{IntOverlayOptions, Overlay, ShapeType};
use i_overlay::core::overlay_rule::OverlayRule;
use i_overlay::i_float::fix_vec::FixVec;
use i_overlay::i_float::int::point::IntPoint;
use i_overlay::i_shape::int::area::Area;
use i_overlay::i_shape::int::shape::{IntContour, IntShape, IntShapes};
use i_overlay::mesh::outline::offset::OutlineOffset;
use i_overlay::mesh::style::{LineJoin, OutlineStyle};
use std::f64::consts::PI;

pub(super) const MAX_VERTICES: usize = 256;
// A shared microdegree grid for validation, overlays and antimeridian cuts.
pub(super) const GRID: i32 = 1_000_000;
pub(super) const LON_LIMIT: i32 = 180 * GRID;
pub(super) const LAT_LIMIT: i32 = 90 * GRID;
const WORLD_WIDTH: i32 = 2 * LON_LIMIT;
const KM_PER_DEGREE: f64 = 111.195;
const CORNER_TRIM_FRACTION: f64 = 0.20;
const CORNER_TRIM_KM: f64 = 32.0;
const CORNER_SEGMENTS: usize = 6;

pub(super) fn validate_ring(points: &[[f64; 2]]) -> Result<IntContour, &'static str> {
    if !(3..=MAX_VERTICES).contains(&points.len()) {
        return Err("a ring needs 3 to 256 vertices");
    }
    let mut ring: IntContour = points
        .iter()
        .map(|&[lon, lat]| {
            if !(-180.0..=180.0).contains(&lon) || !(-90.0..=90.0).contains(&lat) {
                return Err("coordinates must be finite longitude/latitude pairs within the world");
            }
            Ok(IntPoint::new(
                (lon * f64::from(GRID)).round() as i32,
                (lat * f64::from(GRID)).round() as i32,
            ))
        })
        .collect::<Result<_, _>>()?;
    let anchor = ring[0].x;
    for point in &mut ring {
        let delta = (point.x - anchor + LON_LIMIT).rem_euclid(WORLD_WIDTH) - LON_LIMIT;
        point.x = anchor + delta;
    }
    let (min, max) = bounds(&ring);
    if max.x - min.x >= LON_LIMIT {
        return Err("a ring must span less than 180 degrees of longitude");
    }

    let n = ring.len();
    for (i, &a) in ring.iter().enumerate() {
        let b = ring[(i + 1) % n];
        if a == b {
            return Err("a ring must be open and have distinct consecutive vertices");
        }
        let ab = b.subtract(a);
        let previous = ring[(i + n - 1) % n].subtract(a);
        if ab.cross_product(previous) == 0 && ab.dot_product(previous) > 0 {
            return Err("a ring must have no backtracking edges");
        }
        for j in (i + 2)..n {
            if i == 0 && j == n - 1 {
                continue;
            }
            if segments_intersect(a, b, ring[j], ring[(j + 1) % n]) {
                return Err("a ring must have no self-intersections");
            }
        }
    }
    let area = ring.area_two();
    if area == 0 {
        return Err("a ring must enclose a nonzero area");
    }
    // i_overlay's signed area is negative for a counterclockwise ring.
    if area > 0 {
        ring.reverse();
    }
    Ok(ring)
}

/// Round the derived contour on the unwrapped grid before buffering or clipping it.
pub(super) fn round_ring(ring: IntContour) -> IntContour {
    let (min, max) = bounds(&ring);
    let (lon_scale, lat_scale) = scales_at(min.y.midpoint(max.y));
    let mut rounded = Vec::with_capacity(ring.len() * (CORNER_SEGMENTS + 1));
    let mut overlay = Overlay::new_custom(
        rounded.capacity(),
        IntOverlayOptions::keep_all_points(),
        Default::default(),
    );
    let area_limit = ring.area_two().div_euclid(2);
    for scale in [1.0, 0.25] {
        rounded.clear();
        let trim = |edge: FixVec| {
            let (x, y) = (edge.x as f64, edge.y as f64);
            let km = (x * lon_scale).hypot(y * lat_scale);
            let fraction = scale * CORNER_TRIM_FRACTION.min(CORNER_TRIM_KM / km);
            [x * fraction, y * fraction]
        };
        for (index, &corner) in ring.iter().enumerate() {
            let previous = ring[(index + ring.len() - 1) % ring.len()];
            let next = ring[(index + 1) % ring.len()];
            if let Some(curve) = rounded_corner(previous, corner, next, trim) {
                rounded.extend(curve);
            } else {
                rounded.push(corner);
            }
        }
        rounded.dedup();
        if rounded == ring {
            return ring;
        }
        // A curve can meet a nonadjacent edge across a narrow gap. Only accept
        // a simple contour with its original winding and at least half its area.
        if rounded.area_two() <= area_limit
            && overlay
                .simplify_contour(&rounded, FillRule::NonZero)
                .is_none()
        {
            return rounded;
        }
    }
    ring
}

fn rounded_corner(
    previous: IntPoint,
    corner: IntPoint,
    next: IntPoint,
    trim: impl Fn(FixVec) -> [f64; 2],
) -> Option<[IntPoint; CORNER_SEGMENTS + 1]> {
    let incoming = previous.subtract(corner);
    let outgoing = next.subtract(corner);
    let turn = incoming.cross_product(outgoing).signum();
    if turn == 0 {
        return None;
    }
    let [ax, ay] = trim(incoming);
    let [bx, by] = trim(outgoing);
    let curve = std::array::from_fn(|step| {
        let t = step as f64 / CORNER_SEGMENTS as f64;
        let a = (1.0 - t) * (1.0 - t);
        let b = t * t;
        // The authored corner is the control point. Round offsets so that
        // unwrapping from a different starting vertex gives the same curve.
        IntPoint::new(
            corner.x + (a * ax + b * bx).round() as i32,
            corner.y + (a * ay + b * by).round() as i32,
        )
    });
    if curve[0] == curve[CORNER_SEGMENTS] || curve.contains(&corner) {
        return None;
    }

    let mut before = previous;
    let mut current = curve[0];
    for after in curve[1..].iter().copied().chain([next]) {
        if current == after {
            continue;
        }
        let back = before.subtract(current);
        let forward = after.subtract(current);
        let bend = back.cross_product(forward).signum();
        // Quantization can reverse the turn or make a very thin arc double back.
        if bend == -turn || (bend == 0 && back.dot_product(forward) > 0) {
            return None;
        }
        before = current;
        current = after;
    }
    Some(curve)
}

fn segments_intersect(a: IntPoint, b: IntPoint, c: IntPoint, d: IntPoint) -> bool {
    if a.x.max(b.x) < c.x.min(d.x)
        || c.x.max(d.x) < a.x.min(b.x)
        || a.y.max(b.y) < c.y.min(d.y)
        || c.y.max(d.y) < a.y.min(b.y)
    {
        return false;
    }
    let ab = b.subtract(a);
    let cd = d.subtract(c);
    ab.cross_product(c.subtract(a)).signum() * ab.cross_product(d.subtract(a)).signum() <= 0
        && cd.cross_product(a.subtract(c)).signum() * cd.cross_product(b.subtract(c)).signum() <= 0
}

pub(super) fn buffer(shapes: &[IntShape], radius: f64) -> IntShapes {
    let style = OutlineStyle::new(radius).line_join(LineJoin::Round(PI / 12.0));
    let mut buffered = Vec::new();
    for shape in shapes {
        let (min, max) = bounds(&shape[0]);
        let center = IntPoint::new(min.x + (max.x - min.x) / 2, min.y.midpoint(max.y));
        let (lon_scale, lat_scale) = scales_at(center.y);
        let local: Vec<Vec<_>> = shape
            .iter()
            .map(|path| {
                path.iter()
                    .map(|p| {
                        [
                            f64::from(p.x - center.x) * lon_scale,
                            f64::from(p.y - center.y) * lat_scale,
                        ]
                    })
                    .collect()
            })
            .collect();
        buffered.extend(local.outline(&style).into_iter().map(|shape| {
            shape
                .into_iter()
                .map(|path| {
                    path.into_iter()
                        .map(|[x, y]| {
                            IntPoint::new(
                                center.x + (x / lon_scale).round() as i32,
                                center.y + (y / lat_scale).round() as i32,
                            )
                        })
                        .collect()
                })
                .collect()
        }));
    }
    buffered
}

pub(super) fn center_longitude(ring: &[IntPoint]) -> i32 {
    let (min, max) = bounds(ring);
    min.x + (max.x - min.x) / 2
}

pub(super) fn replace_envelope(
    mut ring: IntContour,
    source_longitude: i32,
    higher: &[IntShape],
    automatic: &[IntShape],
) -> IntShapes {
    let center = center_longitude(&ring);
    let delta = (center - source_longitude + LON_LIMIT).rem_euclid(WORLD_WIDTH) - LON_LIMIT;
    let shift = source_longitude + delta - center;
    for point in &mut ring {
        point.x += shift;
    }

    // The handles edit the exterior. Preserve the automatic interior holes
    // outside the higher risk and its clearance.
    let mut overlay = Overlay::with_contours(&[ring], &[]);
    for hole in automatic.iter().flat_map(|shape| &shape[1..]) {
        overlay.add_contour(hole, ShapeType::Clip);
    }
    let mut replacement = overlay.overlay(OverlayRule::Difference, FillRule::NonZero);
    replacement.extend(higher.iter().cloned());
    replacement.extend(buffer(higher, RISK_SPACING_KM));
    union(replacement)
}

pub(super) fn scales_at(lat: i32) -> (f64, f64) {
    let latitude = f64::from(lat) / f64::from(GRID);
    let lat_scale = KM_PER_DEGREE / f64::from(GRID);
    // Bound the longitude scale at 85° to keep the geometry local near a pole.
    let lon_scale = lat_scale * latitude.abs().min(85.0).to_radians().cos();
    (lon_scale, lat_scale)
}

pub(super) fn bounds(ring: &[IntPoint]) -> (IntPoint, IntPoint) {
    ring.iter().fold((ring[0], ring[0]), |(min, max), p| {
        (
            IntPoint::new(min.x.min(p.x), min.y.min(p.y)),
            IntPoint::new(max.x.max(p.x), max.y.max(p.y)),
        )
    })
}

pub(super) fn clip_to_window(shape: &IntShape, west: i32, east: i32) -> IntShapes {
    let window = vec![
        IntPoint::new(west, -LAT_LIMIT),
        IntPoint::new(east, -LAT_LIMIT),
        IntPoint::new(east, LAT_LIMIT),
        IntPoint::new(west, LAT_LIMIT),
    ];
    Overlay::with_contours(shape, &[window]).overlay(OverlayRule::Intersect, FillRule::NonZero)
}

/// Cut on the world edges before shifting pieces across the antimeridian.
/// This keeps the paths local and lets coverage from either side union normally.
pub(super) fn world_parts(shapes: IntShapes) -> IntShapes {
    let mut parts = Vec::new();
    for shape in shapes {
        let (min, max) = bounds(&shape[0]);
        if min.x >= -LON_LIMIT && max.x <= LON_LIMIT && min.y >= -LAT_LIMIT && max.y <= LAT_LIMIT {
            parts.push(shape);
            continue;
        }
        let first = (min.x + LON_LIMIT).div_euclid(WORLD_WIDTH);
        let last = (max.x + LON_LIMIT).div_euclid(WORLD_WIDTH);
        for world in first..=last {
            let shift = world * WORLD_WIDTH;
            let mut clipped = clip_to_window(&shape, shift - LON_LIMIT, shift + LON_LIMIT);
            for point in clipped.iter_mut().flatten().flatten() {
                point.x -= shift;
            }
            parts.extend(clipped);
        }
    }
    parts
}

pub(super) fn wrapped_point(point: IntPoint) -> [f64; 2] {
    let x = (point.x + LON_LIMIT).rem_euclid(WORLD_WIDTH) - LON_LIMIT;
    [
        f64::from(x) / f64::from(GRID),
        f64::from(point.y) / f64::from(GRID),
    ]
}

pub(super) fn union(shapes: IntShapes) -> IntShapes {
    Overlay::with_shapes(&shapes, &[]).overlay(OverlayRule::Subject, FillRule::NonZero)
}

pub(super) fn clip_hatch(shape: &[IntShape], envelope: &[IntShape]) -> (IntShapes, bool) {
    if envelope.is_empty() {
        return (Vec::new(), false);
    }
    let mut overlay = Overlay::with_shapes(shape, envelope);
    let Some(graph) = overlay.build_graph_view(FillRule::NonZero) else {
        return (Vec::new(), false);
    };
    let mut buffer = BooleanExtractionBuffer::default();
    let inside = graph.extract_shapes(OverlayRule::Intersect, &mut buffer);
    let clipped = !inside.is_empty()
        && !graph
            .extract_shapes(OverlayRule::Difference, &mut buffer)
            .is_empty();
    (inside, clipped)
}

pub(super) fn visible_hatch(shapes: &[IntShape], higher: &[IntShapes]) -> IntShapes {
    let mut overlay = Overlay::with_shapes(shapes, &[]);
    for shapes in higher {
        overlay.add_shapes(shapes, ShapeType::Clip);
    }
    overlay.overlay(OverlayRule::Difference, FillRule::NonZero)
}

pub(super) fn geographic_ring(ring: IntContour) -> Vec<[f64; 2]> {
    let mut points = Vec::with_capacity(ring.len());
    let scale = f64::from(GRID);
    let mut previous = ring[ring.len() - 1];
    for point in ring {
        // A union of wide areas can have long collinear edges. Keep each
        // segment shorter than 180° even if the combined area spans more.
        let steps = (point.x - previous.x).abs() / LON_LIMIT + 1;
        for step in 1..steps {
            let t = f64::from(step) / f64::from(steps);
            points.push([
                (f64::from(previous.x) + t * f64::from(point.x - previous.x)) / scale,
                (f64::from(previous.y) + t * f64::from(point.y - previous.y)) / scale,
            ]);
        }
        points.push([f64::from(point.x) / scale, f64::from(point.y) / scale]);
        previous = point;
    }
    points
}

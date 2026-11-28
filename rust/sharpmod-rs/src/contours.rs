//! Marching squares with the existing edge-pair convention and segment order.

use std::collections::BTreeMap;

pub(crate) mod pressure;

pub type Segment = ((f64, f64), (f64, f64));
pub const TOO_DENSE: &str = "The contour lines of this product are too dense to draw.";

fn crossing(a: f64, b: f64, level: f64) -> f64 {
    if a.is_finite() && b.is_finite() && (a < level) != (b < level) {
        (a - level) / (a - b)
    } else {
        f64::NAN
    }
}

pub fn extract(
    values: &[f64],
    height: usize,
    width: usize,
    interval: f64,
    max_segments: usize,
) -> Result<Vec<Segment>, String> {
    extract_strict(
        values,
        height,
        width,
        interval,
        max_segments,
        |_, segment| segment,
    )
}

fn extract_strict<T>(
    values: &[f64],
    height: usize,
    width: usize,
    interval: f64,
    max_segments: usize,
    with_level: impl Fn(f64, Segment) -> T,
) -> Result<Vec<T>, String> {
    if height.checked_mul(width) != Some(values.len()) {
        return Err("contour grid dimensions do not match its values".into());
    }
    if height < 2 || width < 2 || !interval.is_finite() || interval <= 0.0 {
        return Ok(Vec::new());
    }
    let (mut minimum, mut maximum) = (f64::INFINITY, f64::NEG_INFINITY);
    for &value in values.iter().filter(|value| value.is_finite()) {
        minimum = minimum.min(value);
        maximum = maximum.max(value);
    }
    if !minimum.is_finite() {
        return Ok(Vec::new());
    }
    let first = (minimum / interval).floor() + 1.0;
    let last = (maximum / interval).floor();
    // Bound level work even when isolated finite islands have no crossings.
    if !first.is_finite()
        || !last.is_finite()
        || first.abs() >= (1u64 << 53) as f64
        || last.abs() >= (1u64 << 53) as f64
        || last - first >= 100_000.0
    {
        return Err(TOO_DENSE.into());
    }
    let levels: Vec<_> = (first as i64..=last as i64)
        .map(|step| step as f64 * interval)
        .collect();
    if levels.is_empty() {
        return Ok(Vec::new());
    }
    // Each sample is bracketed once. Comparing with the actual levels preserves
    // crossings exactly where division by a fractional interval would round.
    let bracket = |value: f64| {
        if value.is_finite() {
            levels.partition_point(|&level| level <= value)
        } else {
            usize::MAX
        }
    };
    let mut rows = values.chunks_exact(width);
    let mut upper = rows.next().expect("validated grid dimensions");
    let mut upper_ranks: Vec<_> = upper.iter().copied().map(bracket).collect();
    let mut lower_ranks = vec![0; width];
    let mut by_level = BTreeMap::<usize, [Vec<T>; 7]>::new();
    let limit = max_segments.min(100_000);
    let mut count = 0;
    for (row, lower) in rows.enumerate() {
        for (rank, &value) in lower_ranks.iter_mut().zip(lower) {
            *rank = bracket(value);
        }
        for col in 0..width - 1 {
            let ranks = [
                upper_ranks[col],
                upper_ranks[col + 1],
                lower_ranks[col],
                lower_ranks[col + 1],
            ];
            let mut first_rank = levels.len();
            let mut last_rank = 0;
            for rank in ranks.into_iter().filter(|&rank| rank != usize::MAX) {
                first_rank = first_rank.min(rank);
                last_rank = last_rank.max(rank);
            }
            if first_rank >= last_rank {
                continue;
            }
            let [a, b, c, d] = [upper[col], upper[col + 1], lower[col], lower[col + 1]];
            for (offset, &level) in levels[first_rank..last_rank].iter().enumerate() {
                let fractions = [
                    crossing(a, b, level),
                    crossing(b, d, level),
                    crossing(c, d, level),
                    crossing(a, c, level),
                ];
                let code = fractions
                    .iter()
                    .enumerate()
                    .fold(0u8, |code, (index, value)| {
                        code | (u8::from(value.is_finite()) << index)
                    });
                let (bucket, pairs): (usize, &[(usize, usize)]) = match code {
                    3 => (0, &[(0, 1)]),
                    5 => (1, &[(0, 2)]),
                    6 => (2, &[(1, 2)]),
                    9 => (3, &[(0, 3)]),
                    10 => (4, &[(1, 3)]),
                    12 => (5, &[(2, 3)]),
                    15 => (6, &[(0, 1), (2, 3)]),
                    _ => continue,
                };
                let (x, y) = (col as f64, row as f64);
                let points = [
                    (x + fractions[0], y),
                    (x + 1.0, y + fractions[1]),
                    (x + fractions[2], y + 1.0),
                    (x, y + fractions[3]),
                ];
                count += pairs.len();
                if count > limit {
                    return Err(TOO_DENSE.into());
                }
                let cases = by_level.entry(first_rank + offset).or_default();
                for &(start, end) in pairs {
                    cases[bucket].push(with_level(level, (points[start], points[end])));
                }
            }
        }
        upper = lower;
        std::mem::swap(&mut upper_ranks, &mut lower_ranks);
    }
    // Preserve the original level, crossing case, and row-major ordering.
    let mut output = Vec::with_capacity(count);
    output.extend(by_level.into_values().flatten().flatten());
    Ok(output)
}

/// Complete display geometry, with the requested interval and at most 100,000 segments.
///
/// Ordinary fields retain the strict extractor's geometry and ordering. On a density
/// failure, progressively larger cells sample the original grid (never a filtered
/// weather field). All original samples inside an enlarged cell must be finite.
/// Thus a display segment cannot cross a missing-data hole hidden by downsampling.
///
/// Sampling steps double, retaining the last row/column even for uneven dimensions.
/// Crossings use those original coordinates; projection must still use the original
/// height and width. Affine fields are exact before projection's existing rounding.
/// For other fields, a crossing is bracketed by original edge samples, with spatial
/// uncertainty no greater than that enlarged edge's span. Subcell closed features
/// can disappear; this display approximation is not a replacement for weather data.
pub fn extract_display(
    values: &[f64],
    height: usize,
    width: usize,
    interval: f64,
    max_segments: usize,
) -> Result<Vec<Segment>, String> {
    extract_display_levels(values, height, width, interval, max_segments)
        .map(|segments| segments.into_iter().map(|(_, segment)| segment).collect())
}

/// Display segments paired with the actual level used by marching squares.
///
/// Levels are computed during extraction, including adaptive attempts; callers
/// must not infer them from output order or resample the original weather field.
/// An over-budget attempt is discarded in full, never truncated. If even one cell
/// needs more levels than the budget permits, this returns `TOO_DENSE` rather than
/// widening the interval. Auxiliary memory is O(min(max_segments, 100,000)); no
/// reduced grid, mask, or list of levels is allocated. Each fallback attempt scans
/// at most four times the original sample count and visits at most the segment
/// limit's worth of levels. There are O(log(max(height, width))) attempts.
pub fn extract_display_levels(
    values: &[f64],
    height: usize,
    width: usize,
    interval: f64,
    max_segments: usize,
) -> Result<Vec<(f64, Segment)>, String> {
    match extract_strict(
        values,
        height,
        width,
        interval,
        max_segments,
        |level, segment| (level, segment),
    ) {
        Ok(segments) => return Ok(segments),
        Err(error) if error == TOO_DENSE => (),
        Err(error) => return Err(error),
    }
    let limit = max_segments.min(100_000);
    if limit == 0 {
        return Err(TOO_DENSE.into());
    }
    // Shape/interval validation belongs to the strict extractor above.
    let maximum_stride = (height - 1).max(width - 1);
    let mut stride = 2.min(maximum_stride);
    loop {
        if let Ok(segments) =
            extract_display_at_stride(values, height, width, interval, limit, stride)
        {
            return Ok(segments);
        }
        if stride == maximum_stride {
            return Err(TOO_DENSE.into());
        }
        stride = stride.saturating_mul(2).min(maximum_stride);
    }
}

fn extract_display_at_stride(
    values: &[f64],
    height: usize,
    width: usize,
    interval: f64,
    limit: usize,
    stride: usize,
) -> Result<Vec<(f64, Segment)>, String> {
    let mut output = Vec::with_capacity(limit);
    let mut level_work = 0;
    for row in (0..height - 1).step_by(stride) {
        let next_row = row.saturating_add(stride).min(height - 1);
        for col in (0..width - 1).step_by(stride) {
            let next_col = col.saturating_add(stride).min(width - 1);
            if !(row..=next_row).all(|row| {
                values[row * width + col..=row * width + next_col]
                    .iter()
                    .all(|value| value.is_finite())
            }) {
                continue;
            }
            let [a, b, c, d] = [
                values[row * width + col],
                values[row * width + next_col],
                values[next_row * width + col],
                values[next_row * width + next_col],
            ];
            let minimum = a.min(b).min(c).min(d);
            let maximum = a.max(b).max(c).max(d);
            let first = (minimum / interval).floor() + 1.0;
            let last = (maximum / interval).floor();
            if !first.is_finite()
                || !last.is_finite()
                || first.abs() >= (1u64 << 53) as f64
                || last.abs() >= (1u64 << 53) as f64
                || last - first >= (limit - level_work) as f64
            {
                return Err(TOO_DENSE.into());
            }
            let mut step = first;
            while step <= last {
                let level = step * interval;
                level_work += 1;
                let fractions = [
                    crossing(a, b, level),
                    crossing(b, d, level),
                    crossing(c, d, level),
                    crossing(a, c, level),
                ];
                let code = fractions.iter().enumerate().fold(0u8, |code, (i, value)| {
                    code | (u8::from(value.is_finite()) << i)
                });
                let pairs: &[(usize, usize)] = match code {
                    3 => &[(0, 1)],
                    5 => &[(0, 2)],
                    6 => &[(1, 2)],
                    9 => &[(0, 3)],
                    10 => &[(1, 3)],
                    12 => &[(2, 3)],
                    15 => &[(0, 1), (2, 3)],
                    _ => &[],
                };
                let (x, y) = (col as f64, row as f64);
                let (dx, dy) = ((next_col - col) as f64, (next_row - row) as f64);
                let points = [
                    (x + fractions[0] * dx, y),
                    (next_col as f64, y + fractions[1] * dy),
                    (x + fractions[2] * dx, next_row as f64),
                    (x, y + fractions[3] * dy),
                ];
                for &(start, end) in pairs {
                    if output.len() == limit {
                        return Err(TOO_DENSE.into());
                    }
                    output.push((level, (points[start], points[end])));
                }
                step += 1.0;
            }
        }
    }
    Ok(output)
}

pub fn project(
    segments: &mut [Segment],
    height: usize,
    width: usize,
    bounds: (f64, f64, f64, f64),
) -> Result<(), String> {
    let projection = Projection::new(height, width, bounds)?;
    for segment in segments {
        for point in [&mut segment.0, &mut segment.1] {
            let [lon, lat] = projection.point([point.0, point.1]);
            *point = (lon, lat);
        }
    }
    Ok(())
}

pub(crate) struct Projection {
    width: f64,
    height: f64,
    west: f64,
    north: f64,
    longitude_span: f64,
    latitude_span: f64,
}

impl Projection {
    pub(crate) fn new(
        height: usize,
        width: usize,
        (west, east, south, north): (f64, f64, f64, f64),
    ) -> Result<Self, String> {
        if width == 0
            || height == 0
            || !(-180.0..=180.0).contains(&west)
            || !(-180.0..=180.0).contains(&east)
            || !(-90.0..=90.0).contains(&south)
            || !(-90.0..=90.0).contains(&north)
            || west >= east
            || south >= north
        {
            return Err("contour bounds are invalid".into());
        }
        Ok(Self {
            width: width as f64,
            height: height as f64,
            west,
            north,
            longitude_span: east - west,
            latitude_span: north - south,
        })
    }

    pub(crate) fn point(&self, [x, y]: [f64; 2]) -> [f64; 2] {
        let longitude = self.west + (x + 0.5) * self.longitude_span / self.width;
        let latitude = self.north - (y + 0.5) * self.latitude_span / self.height;
        [longitude, latitude].map(round_coordinate)
    }
}

fn round_coordinate(coordinate: f64) -> f64 {
    const SCALE: f64 = 100_000.0;
    let scaled = coordinate * SCALE;
    // Multiplication can round a near-halfway value onto the tie. Keep the
    // original decimal rounding in that case, with no string on the common path.
    if scaled.fract().abs() == 0.5 {
        format!("{coordinate:.5}")
            .parse()
            .expect("formatted finite coordinate")
    } else {
        scaled.round() / SCALE
    }
}

#[cfg(test)]
mod tests;

#[cfg(test)]
mod display_tests;

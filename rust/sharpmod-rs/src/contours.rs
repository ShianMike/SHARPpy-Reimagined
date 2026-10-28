//! Marching squares with the existing edge-pair convention and segment order.

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
    let mut output = Vec::new();
    let mut step = first;
    while step <= last {
        let level = step * interval;
        let mut cases: [Vec<Segment>; 7] = std::array::from_fn(|_| Vec::new());
        let mut count = output.len();
        for row in 0..height - 1 {
            for col in 0..width - 1 {
                let index = row * width + col;
                let [a, b, c, d] = [
                    values[index],
                    values[index + 1],
                    values[index + width],
                    values[index + width + 1],
                ];
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
                for &(start, end) in pairs {
                    count += 1;
                    if count > max_segments.min(100_000) {
                        return Err(TOO_DENSE.into());
                    }
                    cases[bucket].push((points[start], points[end]));
                }
            }
        }
        for case in cases {
            output.extend(case);
        }
        step += 1.0;
    }
    Ok(output)
}

pub fn project(
    segments: &mut [Segment],
    height: usize,
    width: usize,
    bounds: (f64, f64, f64, f64),
) -> Result<(), String> {
    let (west, east, south, north) = bounds;
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
    for segment in segments {
        for (x, y) in [&mut segment.0, &mut segment.1] {
            let longitude = west + (*x + 0.5) * (east - west) / width as f64;
            let latitude = north - (*y + 0.5) * (north - south) / height as f64;
            // Decimal formatting keeps Python's five-place rounding at binary ties.
            *x = format!("{longitude:.5}")
                .parse()
                .expect("formatted finite longitude");
            *y = format!("{latitude:.5}")
                .parse()
                .expect("formatted finite latitude");
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests;

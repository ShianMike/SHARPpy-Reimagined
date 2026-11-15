//! Numerical preparation for the existing height-profile chart widgets.
mod bindings;

pub(crate) use bindings::register;

const KTS_TO_MS: f64 = 0.514_444_444_444_444_5;

#[derive(Clone, Copy)]
struct GridSpec {
    maximum: f64,
    step: f64,
}

impl GridSpec {
    fn new(maximum: f64, step: f64) -> Option<Self> {
        (step.is_finite() && maximum > 0. && step > 0.).then_some(Self { maximum, step })
    }

    fn sample(self, top: f64) -> Option<Vec<f64>> {
        let top = top.min(self.maximum);
        if top < self.step {
            return None;
        }
        // Match arange(0, floor(top / step) * step + step * .5, step).
        let stop = (top / self.step).floor() * self.step + self.step * 0.5;
        let count = (stop / self.step).ceil();
        if !count.is_finite() || count > (isize::MAX as usize / size_of::<f64>()) as f64 {
            return None;
        }
        let count = count as usize;
        (count >= 2).then(|| (0..count).map(|i| i as f64 * self.step).collect())
    }
}

struct HeightGrid {
    samples: Vec<f64>,
    agl: Vec<f64>,
    valid: Vec<bool>,
}

fn height_grid(height: &[f64], surface: usize, spec: GridSpec) -> Option<HeightGrid> {
    let base = *height.get(surface)?;
    if !base.is_finite() {
        return None;
    }
    let agl: Vec<_> = height.iter().map(|value| value - base).collect();
    let valid: Vec<_> = agl.iter().map(|z| z.is_finite() && *z >= 0.).collect();
    let mut usable = agl
        .iter()
        .zip(&valid)
        .filter_map(|(&z, &valid)| valid.then_some(z));
    let first = usable.next()?;
    let second = usable.next()?;
    let top = usable.fold(first.max(second), f64::max);
    Some(HeightGrid {
        samples: spec.sample(top)?,
        agl,
        valid,
    })
}

fn sort_unique(pairs: &mut Vec<(f64, f64)>) {
    // The caller has removed non-finite heights. Stable sorting followed by
    // deduplication keeps the first usable report at each duplicate height,
    // including -0/+0, as argsort(kind="stable") + unique(return_index=True).
    pairs.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap());
    pairs.dedup_by(|a, b| a.0 == b.0);
}

fn linear_sample(pairs: &[(f64, f64)], samples: &[f64], clamp: bool) -> Vec<f64> {
    let first = pairs[0];
    let last = pairs[pairs.len() - 1];
    samples
        .iter()
        .map(|&target| {
            if target.is_nan() {
                return f64::NAN;
            }
            if target < first.0 {
                return if clamp { first.1 } else { f64::NAN };
            }
            if target > last.0 {
                return if clamp { last.1 } else { f64::NAN };
            }
            let upper = pairs.partition_point(|&(z, _)| z <= target);
            let (z0, v0) = pairs[upper - 1];
            if z0 == target {
                return v0;
            }
            let (z1, v1) = pairs[upper];
            let slope = (v1 - v0) / (z1 - z0);
            let value = slope * (target - z0) + v0;
            if !value.is_nan() {
                return value;
            }
            // NumPy retries from the upper anchor after indeterminate arithmetic.
            let value = slope * (target - z1) + v1;
            if value.is_nan() && v0 == v1 {
                v0
            } else {
                value
            }
        })
        .collect()
}

fn interpolate(samples: &[f64], agl: &[f64], valid: &[bool], values: &[f64]) -> Option<Vec<f64>> {
    if agl.len() != valid.len() || agl.len() != values.len() {
        return None;
    }
    let mut pairs: Vec<_> = agl
        .iter()
        .zip(valid)
        .zip(values)
        .filter_map(|((&z, &valid), &value)| {
            (valid && z.is_finite() && value.is_finite()).then_some((z, value))
        })
        .collect();
    sort_unique(&mut pairs);
    (pairs.len() >= 2).then(|| linear_sample(&pairs, samples, false))
}

fn storm_relative_wind(
    grid: &HeightGrid,
    u: &[f64],
    v: &[f64],
    motion: [f64; 2],
) -> Option<Vec<f64>> {
    let u = interpolate(&grid.samples, &grid.agl, &grid.valid, u)?;
    let v = interpolate(&grid.samples, &grid.agl, &grid.valid, v)?;
    let speed: Vec<_> = u
        .iter()
        .zip(&v)
        .map(|(&u, &v)| (u * KTS_TO_MS - motion[0]).hypot(v * KTS_TO_MS - motion[1]) / KTS_TO_MS)
        .collect();
    speed.iter().any(|value| value.is_finite()).then_some(speed)
}

struct Streamwiseness {
    height: Vec<f64>,
    percent: Vec<f64>,
    signed: Vec<f64>,
}

fn streamwiseness(
    height: &[f64],
    u: &[f64],
    v: &[f64],
    surface: usize,
    motion: [f64; 2],
    spec: GridSpec,
) -> Option<Streamwiseness> {
    if height.len() != u.len() || height.len() != v.len() {
        return None;
    }
    let base = *height.get(surface)?;
    if !base.is_finite() {
        return None;
    }
    // Unlike the other height charts, the established streamwiseness chart
    // slices from sfc and uses a joint height/u/v mask before interpolation.
    let mut levels: Vec<_> = (surface..height.len())
        .filter_map(|i| {
            let z = height[i] - base;
            (z.is_finite() && u[i].is_finite() && v[i].is_finite()).then_some((z, u[i], v[i]))
        })
        .collect();
    levels.sort_by(|a, b| a.0.partial_cmp(&b.0).unwrap());
    levels.dedup_by(|a, b| a.0 == b.0);
    if levels.len() < 2 {
        return None;
    }
    let samples = spec.sample(levels.last()?.0)?;
    let u: Vec<_> = linear_sample(
        &levels.iter().map(|&(z, u, _)| (z, u)).collect::<Vec<_>>(),
        &samples,
        true,
    )
    .into_iter()
    .map(|value| value * KTS_TO_MS)
    .collect();
    let v: Vec<_> = linear_sample(
        &levels.iter().map(|&(z, _, v)| (z, v)).collect::<Vec<_>>(),
        &samples,
        true,
    )
    .into_iter()
    .map(|value| value * KTS_TO_MS)
    .collect();
    let gradient = |values: &[f64], i: usize| {
        if i == 0 {
            (values[1] - values[0]) / spec.step
        } else if i == values.len() - 1 {
            (values[i] - values[i - 1]) / spec.step
        } else {
            (values[i + 1] - values[i - 1]) / (2. * spec.step)
        }
    };
    let mut percent = vec![f64::NAN; samples.len()];
    let mut signed = percent.clone();
    let mut usable = false;
    for i in 0..samples.len() {
        let omega = [-gradient(&v, i), gradient(&u, i)];
        let magnitude = omega[0].hypot(omega[1]);
        let sr = [u[i] - motion[0], v[i] - motion[1]];
        let speed = sr[0].hypot(sr[1]);
        if magnitude > 1e-6 && speed > 0.1 {
            let projection = omega[0] * (sr[0] / speed) + omega[1] * (sr[1] / speed);
            percent[i] = (projection.abs() / magnitude * 100.).clamp(0., 100.);
            signed[i] = if projection == 0. {
                0.
            } else {
                projection.signum() * percent[i]
            };
            usable = true;
        }
    }
    usable.then(|| Streamwiseness {
        height: samples.into_iter().map(|z| z / 1000.).collect(),
        percent,
        signed,
    })
}

fn kelvin(mut values: Vec<f64>) -> Vec<f64> {
    let mut finite: Vec<_> = values.iter().copied().filter(|v| v.is_finite()).collect();
    finite.sort_by(f64::total_cmp);
    if !finite.is_empty() {
        let mid = finite.len() / 2;
        let median = if finite.len() % 2 == 0 {
            (finite[mid - 1] + finite[mid]) / 2.
        } else {
            finite[mid]
        };
        if median < 150. {
            for value in &mut values {
                *value += 273.15;
            }
        }
    }
    values
}

#[cfg(test)]
mod tests;

//! SharpTab custom wind layers, retaining exact top sampling and separate masks.

use crate::interpolation::PreparedInterpolation;

pub(crate) struct PressureWinds {
    u: PreparedInterpolation,
    v: PreparedInterpolation,
}

impl PressureWinds {
    pub fn new(logp: &[f64], u: &[f64], v: &[f64]) -> Result<Self, String> {
        Ok(Self {
            u: PreparedInterpolation::new(logp, u, None)?,
            v: PreparedInterpolation::new(logp, v, None)?,
        })
    }

    fn components(&self, pressure: f64) -> [f64; 2] {
        let target = pressure.log10();
        [self.u.scalar(target, false), self.v.scalar(target, false)]
    }

    pub fn mean(
        &self,
        bounds: [f64; 2],
        increment: f64,
        storm: [f64; 2],
        weighted: bool,
    ) -> Result<[f64; 2], String> {
        let [bottom, top] = bounds;
        let step = -increment.abs();
        if !bottom.is_finite()
            || !top.is_finite()
            || !step.is_finite()
            || step == 0.0
            || bottom < top
        {
            return Ok([f64::NAN; 2]);
        }
        let count = ((bottom - top) / -step).ceil();
        // Match the existing native layer budget without allocating a grid.
        if !count.is_finite() || count > 1_000_000.0 {
            return Err("wind layer has excessive pressure samples".into());
        }
        let count = count as usize;
        // NumPy arange uses the representable difference at its starting value.
        let actual_step = (bottom + step) - bottom;
        let snapped =
            count > 0 && (bottom + (count - 1) as f64 * actual_step - top).abs() <= 1.0e-9;
        let samples = count + usize::from(!snapped);
        let mut sums = [0.0; 2];
        let mut weights = [0.0; 2];
        for index in 0..samples {
            let pressure = if index == samples - 1 {
                top
            } else {
                bottom + index as f64 * actual_step
            };
            let weight = if weighted { pressure } else { 1.0 };
            for (component, value) in self.components(pressure).into_iter().enumerate() {
                if value.is_finite() {
                    sums[component] += value * weight;
                    weights[component] += weight;
                }
            }
        }
        if weights.contains(&0.0) {
            return Ok([f64::NAN; 2]);
        }
        let values = [0, 1].map(|i| sums[i] / weights[i] - storm[i]);
        Ok(if values.iter().any(|x| x.is_nan()) {
            [f64::NAN; 2]
        } else {
            values
        })
    }

    pub fn helicity(
        &self,
        pressure: &[f64],
        u: &[f64],
        v: &[f64],
        bounds: [f64; 2],
        storm: [f64; 2],
    ) -> Result<[f64; 3], String> {
        if pressure.len() != u.len() || pressure.len() != v.len() {
            return Err("wind profile pressure/component lengths differ".into());
        }
        let [bottom, top] = bounds;
        let Some(first) = pressure.iter().position(|&p| bottom >= p) else {
            return Ok([f64::NAN; 3]);
        };
        let Some(last) = pressure.iter().rposition(|&p| top <= p) else {
            return Ok([f64::NAN; 3]);
        };
        let lower = self.components(bottom);
        let upper = self.components(top);
        if lower.iter().chain(&upper).any(|x| !x.is_finite()) {
            return Ok([f64::NAN; 3]);
        }
        let knots_per_ms = 1.943_844_492_440_604_6;
        let relative = |wind: [f64; 2]| [0, 1].map(|i| (wind[i] - storm[i]) / knots_per_ms);
        let mut previous = relative(lower);
        let mut positive = 0.0;
        let mut negative = 0.0;
        let interior = (first..=last).filter_map(|index| {
            (u[index].is_finite() && v[index].is_finite()).then_some([u[index], v[index]])
        });
        for wind in interior.chain(std::iter::once(upper)) {
            let current = relative(wind);
            let area = current[0] * previous[1] - previous[0] * current[1];
            if area > 0.0 {
                positive += area;
            }
            if area < 0.0 {
                negative += area;
            }
            previous = current;
        }
        Ok([positive + negative, positive, negative])
    }
}

#[cfg(test)]
mod tests;

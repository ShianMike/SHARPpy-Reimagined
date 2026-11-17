//! SharpTab display diagnostics, retaining its pressure-grid and mask conventions.
use super::{mix_ratio, vapor_pressure};
use crate::interpolation::PreparedInterpolation;
pub(crate) struct Display {
    pub pressure: Vec<f64>,
    pub height: Vec<f64>,
    pub surface: usize,
    fields: [PreparedInterpolation; 6], // height, T, Td, Tv, u, v at log(p)
    height_pressure: PreparedInterpolation,
}
impl Display {
    pub fn new(columns: &[Vec<f64>], surface: usize, missing: Option<f64>) -> Result<Self, String> {
        let p = &columns[0];
        if surface >= p.len() || columns.iter().any(|c| c.len() != p.len()) {
            return Err("thermodynamic display profile columns disagree".into());
        }
        let logp: Vec<_> = p
            .iter()
            .map(|&v| {
                if super::is_missing(v, missing) {
                    f64::NAN
                } else {
                    v.log10()
                }
            })
            .collect();
        let mut prepared = Vec::new();
        for c in &columns[1..] {
            // SharpTab reverses pressure coordinates; reverse duplicate ties too.
            let x: Vec<_> = logp.iter().copied().rev().collect();
            let y: Vec<_> = c.iter().copied().rev().collect();
            prepared.push(PreparedInterpolation::new(&x, &y, missing)?);
        }
        Ok(Self {
            pressure: p.clone(),
            height: columns[1].clone(),
            surface,
            fields: prepared
                .try_into()
                .map_err(|_| "thermodynamic display requires seven columns")?,
            height_pressure: PreparedInterpolation::new(&columns[1], &logp, missing)?,
        })
    }
    pub fn at(&self, field: usize, p: f64) -> f64 {
        self.fields[field].scalar_sharppy(p.log10(), false)
    }
    pub fn pressure_at(&self, agl: f64) -> f64 {
        self.height_pressure
            .scalar_sharppy(self.height[self.surface] + agl, true)
    }
    pub fn lapse(&self, lower: f64, upper: f64, pressure: bool) -> f64 {
        let (p1, p2, z1, z2) = if pressure {
            (lower, upper, self.at(0, lower), self.at(0, upper))
        } else {
            (
                self.pressure_at(lower),
                self.pressure_at(upper),
                lower,
                upper,
            )
        };
        (self.at(3, p2) - self.at(3, p1)) / (z2 - z1) * -1000.
    }
    fn samples(bottom: f64, top: f64) -> impl Iterator<Item = f64> {
        let n = if bottom.is_finite() && top.is_finite() && bottom >= top {
            (bottom - top + 1.).ceil().min(20_000.) as usize
        } else {
            0
        };
        (0..n).map(move |i| bottom - i as f64)
    }
    pub fn mean_wind(&self, bottom: f64, top: f64) -> [f64; 2] {
        let mut sum = [0.; 2];
        let mut weights = [0.; 2];
        for p in Self::samples(bottom, top) {
            for i in 0..2 {
                let v = self.at(4 + i, p);
                if v.is_finite() {
                    sum[i] += v * p;
                    weights[i] += p;
                }
            }
        }
        [0, 1].map(|i| sum[i] / weights[i])
    }
    pub fn humidity(&self, mut bottom: f64, top: f64) -> f64 {
        if !self.at(1, bottom).is_finite() {
            bottom = self.pressure[self.surface];
        }
        if !self.at(1, top).is_finite() {
            return f64::NAN;
        }
        let mut sum = 0.;
        let mut weights = 0.;
        for p in Self::samples(bottom, top) {
            let rh = 100. * vapor_pressure(self.at(2, p)) / vapor_pressure(self.at(1, p));
            if rh.is_finite() {
                sum += p * rh;
                weights += p;
            }
        }
        sum / weights
    }
    pub fn water(&self) -> f64 {
        let bottom = self.pressure[self.surface];
        let top = 400_f64.max(*self.pressure.last().unwrap());
        let mut previous: Option<(f64, f64)> = None;
        let mut sum = 0.;
        let mut count = 0;
        for p in Self::samples(bottom, top) {
            let w = mix_ratio(p, self.at(2, p));
            if let Some((old_p, old_w)) = previous {
                let value = (old_w + w) * 0.5 * (old_p - p) * 0.00040173;
                if value.is_finite() {
                    sum += value;
                    count += 1;
                }
            }
            previous = Some((p, w));
        }
        if count == 0 {
            f64::NAN
        } else {
            sum
        }
    }
    pub fn maximum_lapse(&self) -> [f64; 3] {
        let mut best = [f64::NAN; 3];
        for i in 0..9 {
            let lower = 2000. + i as f64 * 250.;
            let p1 = self.pressure_at(lower);
            let p2 = self.pressure_at(lower + 2000.);
            let rate = (self.at(3, p2) - self.at(3, p1)) * -0.5;
            if rate.is_finite() && (!best[0].is_finite() || rate > best[0]) {
                best = [rate, p1, p2];
            }
        }
        best
    }
    pub fn max_temp(&self) -> f64 {
        let sfc = self.pressure[self.surface];
        let p = sfc - 100.;
        ((self.at(1, p) + 275.15) * (sfc / p).powf(super::ROCP) - 273.15) * 1.8 + 32.
    }
    pub fn advection(
        &self,
        latitude: Option<f64>,
        winds: bool,
    ) -> Option<(Vec<f64>, Vec<[f64; 2]>)> {
        if !winds || self.pressure.iter().all(|p| *p <= 100.) {
            return None;
        }
        let top = *self.pressure.iter().filter(|&&p| p >= 100.).next_back()?;
        let bottom = self.pressure[self.surface];
        let count = ((bottom - top) / 100.).ceil().max(0.) as usize;
        let mut values = Vec::with_capacity(count.saturating_sub(1));
        let mut bounds = Vec::with_capacity(values.capacity());
        let multiplier = latitude.unwrap_or(f64::NAN).to_radians().sin()
            * (4. * std::f64::consts::PI / 86164.)
            / 9.81
            * std::f64::consts::PI
            / 180.;
        for i in 1..count {
            let b = bottom - (i - 1) as f64 * 100.;
            let t = bottom - i as f64 * 100.;
            let [u, v] = self.mean_wind(b, t);
            let speed = u.hypot(v) * 0.514444;
            let direction =
                |p| (270. - self.at(5, p).atan2(self.at(4, p)).to_degrees()).rem_euclid(360.);
            let theta = (direction(t) + 180. - direction(b)).rem_euclid(360.) - 180.;
            let avg = (self.at(1, t) + self.at(1, b) + 546.3) * 2.;
            values.push(
                multiplier * speed.powi(2) * avg * theta / (self.at(0, t) - self.at(0, b)) * 3600.,
            );
            bounds.push([b, t]);
        }
        Some((values, bounds))
    }
    pub fn freezing_height(&self, temperature: &[f64]) -> f64 {
        let points: Vec<_> = self
            .pressure
            .iter()
            .copied()
            .zip(temperature.iter().copied())
            .filter(|(p, t)| p.is_finite() && t.is_finite())
            .collect();
        if let Some(&(p, _)) = points.iter().find(|(_, t)| *t == 0.) {
            return self.at(0, p);
        }
        for pair in points.windows(2) {
            let [(p1, t1), (p2, t2)] = pair else {
                unreachable!()
            };
            if t1 * t2 < 0. {
                let pressure =
                    10_f64.powf(p2.log10() + (p1.log10() - p2.log10()) * (0. - t2) / (t1 - t2));
                return self.at(0, pressure);
            }
        }
        f64::NAN
    }
    pub fn mmp(&self, mucape: f64, winds: bool, inputs: [Option<f64>; 2]) -> Option<f64> {
        if !mucape.is_finite() || !winds {
            return None;
        }
        let z = self.height[self.surface];
        let mut shear = f64::NAN;
        for &lower in self.height.iter().filter(|&&h| h - z <= 1000.) {
            let p = self.height_pressure.scalar_sharppy(lower, true);
            let u = self.at(4, p);
            let v = self.at(5, p);
            for &upper in self
                .height
                .iter()
                .filter(|&&h| h - z >= 6000. && h - z < 10000.)
            {
                let q = self.height_pressure.scalar_sharppy(upper, true);
                let s = (self.at(4, q) - u).hypot(self.at(5, q) - v);
                if s.is_finite() && (!shear.is_finite() || s > shear) {
                    shear = s;
                }
            }
        }
        let lapse = inputs[0].unwrap_or_else(|| self.lapse(3000., 8000., false));
        let [u, v] = self.mean_wind(self.pressure_at(3000.), self.pressure_at(12000.));
        let value = 13.
            - 4.59e-2 * shear * 0.514444
            - 1.16 * lapse
            - 6.17e-4 * mucape
            - 0.17 * inputs[1].unwrap_or_else(|| u.hypot(v)) * 0.514444;
        value.is_finite().then_some(value)
    }
}
pub(crate) fn ship([cape, mut ratio, lapse, mut temp, mut shear, freezing]: [f64; 6]) -> f64 {
    if [cape, ratio, lapse, temp, shear]
        .iter()
        .any(|v| !v.is_finite())
    {
        return f64::NAN;
    }
    shear = shear.clamp(7., 27.);
    ratio = ratio.clamp(11., 13.6);
    temp = temp.min(-5.5);
    let mut value = -(cape * ratio * lapse * temp * shear) / 42_000_000.;
    if cape < 1300. {
        value *= cape / 1300.;
    }
    if lapse < 5.8 {
        value *= lapse / 5.8;
    }
    if freezing < 2400. {
        value *= freezing / 2400.;
    }
    value
}

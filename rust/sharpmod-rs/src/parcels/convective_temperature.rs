//! Preserve the cheap CAPE/CIN and heating search used by SHARPpy convective temperature.

use super::{
    dry_lift, is_missing, mix_ratio, temperature_at_mix_ratio, theta, virtual_temperature,
    wet_lift, GRAVITY, MAX_PRESSURE_SPAN,
};
use crate::interpolation::PreparedInterpolation;

pub struct TemperatureSearch<'a> {
    pressure: &'a [f64],
    height: &'a [f64],
    temperature: &'a [f64],
    environment: &'a [f64],
    surface: usize,
    fields: [PreparedInterpolation; 4],
}

impl<'a> TemperatureSearch<'a> {
    pub fn new(
        [pressure, height, temperature, dewpoint, environment]: [&'a [f64]; 5],
        surface: usize,
        missing: Option<f64>,
    ) -> Result<Self, String> {
        if [
            height.len(),
            temperature.len(),
            dewpoint.len(),
            environment.len(),
        ]
        .iter()
        .any(|&size| size != pressure.len())
        {
            return Err("convective-temperature profile column lengths differ".into());
        }
        if surface >= pressure.len() {
            return Err("sfc is outside the convective-temperature profile".into());
        }
        let log_pressure: Vec<_> = pressure
            .iter()
            .rev()
            .map(|&p| {
                if is_missing(p, missing) {
                    f64::NAN
                } else {
                    p.log10()
                }
            })
            .collect();
        let [heights, temperatures, dewpoints, virtuals] =
            [height, temperature, dewpoint, environment].map(|values| {
                let values: Vec<_> = values.iter().copied().rev().collect();
                PreparedInterpolation::new(&log_pressure, &values, missing)
            });
        Ok(Self {
            pressure,
            height,
            temperature,
            environment,
            surface,
            fields: [heights?, temperatures?, dewpoints?, virtuals?],
        })
    }

    fn at(&self, field: usize, pressure: f64) -> f64 {
        self.fields[field].scalar_sharppy(pressure.log10(), false)
    }

    /// `cape(..., flag=5, trunc=True)` has a different final layer from parcelx.
    pub fn cape(&self, start: (f64, f64, f64)) -> Option<(f64, f64)> {
        let (p, t, td) = start;
        let top = *self.pressure.last()?;
        let mut bottom = self.pressure[self.surface].min(p);
        if [p, t, td, top, bottom].iter().any(|v| !v.is_finite())
            || p <= 0.
            || bottom - top > MAX_PRESSURE_SPAN
            || !self.at(3, bottom).is_finite()
            || !self.at(3, top).is_finite()
        {
            return None;
        }
        let (lcl_p, lcl_t) = dry_lift(p, t, td);
        if !lcl_p.is_finite() || !lcl_t.is_finite() || lcl_p < top {
            return None;
        }
        let parcel_theta = theta(lcl_p, lcl_t, 1000.);
        let ratio = mix_ratio(p, td);
        let mut negative = 0.;
        let mut previous_p = bottom;
        let mut next_p = bottom - 1.;
        while next_p > lcl_p - 1. {
            let deficit = |p| {
                let env = virtual_temperature(p, theta(p, self.at(1, p), 1000.), self.at(2, p));
                let parcel =
                    virtual_temperature(p, parcel_theta, temperature_at_mix_ratio(ratio, p));
                (parcel - env) / (env + 273.15)
            };
            let energy = GRAVITY * (deficit(previous_p) + deficit(next_p)) / 2.
                * (self.at(0, next_p) - self.at(0, previous_p));
            if energy < 0. {
                negative += energy;
            }
            previous_p = next_p;
            next_p -= 1.;
        }
        bottom = bottom.min(lcl_p);
        let first = self.pressure.iter().position(|&p| bottom > p)?;
        let last = self.pressure.iter().rposition(|&p| top < p)?;
        let (mut p1, mut h1, mut env1, mut t1) =
            (bottom, self.at(0, bottom), self.at(3, bottom), lcl_t);
        let mut positive = 0.;
        for i in first..self.pressure.len() {
            if !self.temperature[i].is_finite() {
                continue;
            }
            let p2 = self.pressure[i];
            let h2 = self.height[i];
            let env2 = self.environment[i];
            let t2 = wet_lift(p1, t1, p2);
            let deficit = |p, t, env| (virtual_temperature(p, t, t) - env) / (env + 273.15);
            let energy = GRAVITY * (deficit(p1, t1, env1) + deficit(p2, t2, env2)) / 2. * (h2 - h1);
            if energy > 0. {
                positive += energy;
            } else if p2 > 500. {
                negative += energy;
            }
            (p1, h1, env1, t1) = (p2, h2, env2, t2);
            if p2 <= 500. || i >= last {
                if energy > 0. {
                    positive -= energy;
                } else if p2 > 500. {
                    negative += energy;
                }
                let top_t = wet_lift(p2, t2, top);
                let partial =
                    GRAVITY * (deficit(p2, t2, env2) + deficit(top, top_t, self.at(3, top))) / 2.
                        * (self.at(0, top) - h2);
                if partial > 0. {
                    positive += partial;
                } else if top > 500. {
                    negative += partial;
                }
                if positive == 0. {
                    negative = 0.;
                }
                return Some((positive, negative));
            }
        }
        None
    }

    pub fn mean_mixing_ratio(&self) -> f64 {
        let surface = self.pressure[self.surface];
        if !self.at(1, surface - 100.).is_finite() {
            return f64::NAN;
        }
        let (mut sum, mut count) = (0., 0);
        for step in 0..=100 {
            let p = surface - f64::from(step);
            let value = mix_ratio(p, self.at(2, p));
            if value.is_finite() {
                sum += value;
                count += 1;
            }
        }
        sum / f64::from(count)
    }

    pub fn convective_temperature(&self, mean_mixing_ratio: f64) -> Option<f64> {
        let pressure = self.pressure[self.surface];
        let mut temperature = self.temperature[self.surface];
        let dewpoint = temperature_at_mix_ratio(mean_mixing_ratio, pressure);
        let (cape, cin) = self.cape((pressure, temperature + 25., dewpoint))?;
        if cape == 0. || !cin.is_finite() || cin < 0. {
            return None;
        }
        let excess = dewpoint - temperature;
        if excess > 0. {
            temperature += excess + 4.;
        }
        for _ in 0..200 {
            let (cape, cin) = self
                .cape((pressure, temperature, dewpoint))
                .unwrap_or((0., f64::NAN));
            if cape != 0. && cin.is_finite() && cin >= 0. {
                return Some(temperature);
            }
            temperature += if cape != 0. && cin < -100. { 2. } else { 0.5 };
        }
        None
    }
}

#[cfg(test)]
mod tests;

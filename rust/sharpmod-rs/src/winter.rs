//! Winter profile diagnostics. Pressures in hPa, heights in m AGL, winds in kt.
//! Wet bulb is the existing SHARPpy profile trace; new layer integration lives here.
pub(crate) mod diagnostics;
mod precipitation;
#[derive(Clone, Copy, Debug)]
pub(crate) struct Level {
    pub p: f64,
    pub z: f64,
    pub t: f64,
    pub td: f64,
    pub tw: f64,
    pub rh: f64,
    pub rhi: f64,
    pub omega: f64,
    pub u: f64,
    pub v: f64,
}

impl Level {
    fn between(self, other: Self, f: f64) -> Self {
        if f <= 0. {
            return self;
        }
        if f >= 1. {
            return other;
        }
        let lerp = |a: f64, b: f64| a + f * (b - a);
        let t = lerp(self.t, other.t);
        let td = lerp(self.td, other.td);
        let e = vapor(td, false);
        Self {
            p: lerp(self.p.ln(), other.p.ln()).exp(),
            z: lerp(self.z, other.z),
            t,
            td,
            tw: lerp(self.tw, other.tw),
            rh: 100. * e / vapor(t, false),
            rhi: 100. * e / vapor(t, t <= 0.),
            omega: lerp(self.omega, other.omega),
            u: lerp(self.u, other.u),
            v: lerp(self.v, other.v),
        }
    }
    pub fn speed(self) -> f64 {
        self.u.hypot(self.v)
    }
    pub fn theta_e(self) -> f64 {
        crate::ecape::thermodynamics::theta_e(self.p, self.t + 273.15, self.td + 273.15)
    }
    fn specific_humidity(self) -> f64 {
        let e = vapor(self.td, false) / 100.;
        if e >= self.p {
            return f64::NAN;
        }
        let r = 0.62195691 * e / (self.p - e);
        r / (1. + r)
    }
    fn theta_v(self) -> f64 {
        (self.t + 273.15)
            * (1. + 0.6078 * self.specific_humidity())
            * (1000. / self.p).powf(2. / 7.)
    }
}

#[derive(Clone, Debug)]
pub(crate) struct Layer {
    pub bottom: Level,
    pub top: Level,
    pub energy: f64,
    pub extreme: f64,
    sums: [f64; 3],
    weights: [f64; 3],
}
impl Layer {
    pub fn depth(&self) -> f64 {
        self.top.z - self.bottom.z
    }
    #[cfg(test)]
    pub fn means(&self) -> [f64; 3] {
        std::array::from_fn(|i| {
            if self.weights[i] > 0. && (self.weights[i] - (self.bottom.p - self.top.p)).abs() < 0.01
            {
                self.sums[i] / self.weights[i]
            } else {
                f64::NAN
            }
        })
    }
}

pub(crate) struct WinterProfile {
    pub levels: Vec<Level>,
    pub dgz: Vec<Layer>,
    pub warm: Vec<Layer>,
    pub icing: Vec<Layer>,
    pub freezing: Vec<Level>,
    pub wet_zero: Vec<Level>,
}

// Murphy & Koop (2005), equations 7 and 10, natural logarithms, pressure in Pa.
// https://www.eol.ucar.edu/data-software/conventions-and-standards/water-vapor-pressure-formulations
fn vapor(c: f64, ice: bool) -> f64 {
    if !(-100. ..=65.).contains(&c) {
        return f64::NAN;
    }
    let t = c + 273.15;
    if ice {
        (9.550426 - 5723.265 / t + 3.53068 * t.ln() - 0.00728332 * t).exp()
    } else {
        (54.842763 - 6763.22 / t - 4.210 * t.ln()
            + 0.000367 * t
            + (0.0415 * (t - 218.8)).tanh()
                * (53.878 - 1331.22 / t - 9.44523 * t.ln() + 0.014025 * t))
            .exp()
    }
}

impl WinterProfile {
    pub fn new(columns: &[Vec<f64>], sfc: usize, missing: f64) -> Result<Self, String> {
        if columns.len() != 8
            || columns[0].len() < sfc + 2
            || columns.iter().any(|c| c.len() != columns[0].len())
        {
            return Err("Winter analysis needs matching pressure, height, temperature, moisture and wind columns.".into());
        }
        let clean = |v: f64| {
            if v == missing || !v.is_finite() {
                f64::NAN
            } else {
                v
            }
        };
        let ground = clean(columns[1][sfc]);
        if !ground.is_finite() {
            return Err("Winter analysis needs a surface height.".into());
        }
        let levels: Vec<_> = (sfc..columns[0].len())
            .map(|i| {
                let [p, z, t, td, tw, omega, u, v] = std::array::from_fn(|k| clean(columns[k][i]));
                let e = vapor(td, false);
                Level {
                    p,
                    z: z - ground,
                    t,
                    td,
                    tw,
                    omega: omega * 10.,
                    u,
                    v,
                    rh: 100. * e / vapor(t, false),
                    rhi: 100. * e / vapor(t, t <= 0.),
                }
            })
            .collect();
        Ok(Self {
            dgz: layers(&levels, |l| l.t, -18., -12., false),
            warm: layers(&levels, |l| l.tw, 0., 65., false)
                .into_iter()
                .filter(|l| l.energy > 0.)
                .collect(),
            icing: layers(&levels, |l| l.t, -15., 0., true),
            freezing: crossings(&levels, |l| l.t),
            wet_zero: crossings(&levels, |l| l.tw),
            levels,
        })
    }

    pub fn dgz_means(&self) -> [f64; 3] {
        std::array::from_fn(|i| {
            let weight: f64 = self.dgz.iter().map(|l| l.weights[i]).sum();
            let total: f64 = self.dgz.iter().map(|l| l.bottom.p - l.top.p).sum();
            if weight > 0. && (weight - total).abs() < 0.01 {
                self.dgz.iter().map(|l| l.sums[i]).sum::<f64>() / weight
            } else {
                f64::NAN
            }
        })
    }
    pub fn snow_ratio(&self) -> Option<f64> {
        // Require coverage through 500 hPa; do not extrapolate a truncated profile.
        if self.levels.last()?.p > 500. {
            return None;
        }
        let values: Vec<_> = self.levels.iter().filter(|l| l.p >= 500.).collect();
        if values.is_empty() || values.iter().any(|l| !l.t.is_finite()) {
            return None;
        }
        let endpoint = self.at_pressure(500.)?.t;
        if !endpoint.is_finite() {
            return None;
        }
        let max = values.iter().map(|l| l.t).fold(endpoint, f64::max);
        let delta = 271.16 - (max + 273.15);
        Some((12. + delta * if delta < 0. { 2. } else { 1. }).max(0.))
    }
}

fn valid_pair(a: Level, b: Level) -> bool {
    a.p.is_finite()
        && b.p.is_finite()
        && a.p > b.p
        && b.p > 0.
        && a.z.is_finite()
        && b.z.is_finite()
        && b.z > a.z
}

fn interval(a: f64, b: f64, low: f64, high: f64) -> Option<(f64, f64)> {
    if !a.is_finite() || !b.is_finite() {
        return None;
    }
    if (b - a).abs() < 1e-12 {
        return (a >= low && a <= high).then_some((0., 1.));
    }
    let x = (low - a) / (b - a);
    let y = (high - a) / (b - a);
    let from = x.min(y).max(0.);
    let to = x.max(y).min(1.);
    (to > from).then_some((from, to))
}

fn layers(
    levels: &[Level],
    value: fn(Level) -> f64,
    low: f64,
    high: f64,
    moist: bool,
) -> Vec<Layer> {
    let mut result: Vec<Layer> = Vec::new();
    for pair in levels.windows(2) {
        let (a, b) = (pair[0], pair[1]);
        if !valid_pair(a, b) {
            continue;
        }
        let Some((mut from, mut to)) = interval(value(a), value(b), low, high) else {
            continue;
        };
        if moist {
            let Some((l, r)) = interval(a.rh, b.rh, 90., f64::MAX) else {
                continue;
            };
            from = from.max(l);
            to = to.min(r);
        }
        if to <= from {
            continue;
        }
        let bottom = a.between(b, from);
        let top = a.between(b, to);
        let energy = 9.80665 / 273.15 * (bottom.tw + top.tw) * 0.5 * (top.z - bottom.z);
        let weight = bottom.p - top.p;
        let mut sums = [0.; 3];
        let mut weights = [0.; 3];
        for (i, (lo, hi)) in [
            (bottom.rh, top.rh),
            (bottom.rhi, top.rhi),
            (bottom.omega, top.omega),
        ]
        .into_iter()
        .enumerate()
        {
            if lo.is_finite() && hi.is_finite() {
                sums[i] = (lo + hi) * 0.5 * weight;
                weights[i] = weight;
            }
        }
        let extreme = if high <= 0. {
            value(bottom).min(value(top))
        } else {
            value(bottom).max(value(top))
        };
        if let Some(last) = result
            .last_mut()
            .filter(|l| (l.top.z - bottom.z).abs() < 0.01)
        {
            last.top = top;
            last.energy += energy;
            last.extreme = if high <= 0. {
                last.extreme.min(extreme)
            } else {
                last.extreme.max(extreme)
            };
            for i in 0..3 {
                last.sums[i] += sums[i];
                last.weights[i] += weights[i];
            }
        } else {
            result.push(Layer {
                bottom,
                top,
                energy,
                extreme,
                sums,
                weights,
            });
        }
    }
    result
}

fn crossings(levels: &[Level], value: fn(Level) -> f64) -> Vec<Level> {
    let mut result: Vec<Level> = Vec::new();
    for pair in levels.windows(2) {
        let (a, b) = (pair[0], pair[1]);
        let (x, y) = (value(a), value(b));
        if !valid_pair(a, b) || !x.is_finite() || !y.is_finite() || x * y > 0. || x == y {
            continue;
        }
        let zero = a.between(b, -x / (y - x));
        if result.last().is_none_or(|l| (l.z - zero.z).abs() > 0.01) {
            result.push(zero);
        }
    }
    result
}

pub(crate) fn wind_chill(t: f64, speed: f64) -> Option<f64> {
    let mph = speed * 1.150779448;
    let f = t * 1.8 + 32.;
    if !f.is_finite() || !mph.is_finite() || f > 50. || mph <= 3. {
        return None;
    }
    Some((35.74 + 0.6215 * f - 35.75 * mph.powf(0.16) + 0.4275 * f * mph.powf(0.16) - 32.) / 1.8)
}

#[cfg(test)]
mod tests;

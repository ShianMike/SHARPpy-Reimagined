//! MetPy 1.7 liquid-water conventions, separate from SHARPpy's parcel physics.
//! Adapted from MetPy (BSD-3-Clause); see the repository NOTICE.

pub(super) const RD: f64 = 287.04749097718457;
const RV: f64 = 461.52311572606084;
pub(super) const CP: f64 = 1004.6662184201462;
const CP_V: f64 = 1860.078011865639;
const CP_L: f64 = 4219.400000000001;
pub(super) const LV: f64 = 2500840.;
pub(super) const EPSILON: f64 = 0.6219569100577033;
pub(super) const KAPPA: f64 = 0.28571428571428564;
pub(super) const G: f64 = 9.80665;
const T0: f64 = 273.16;

pub(super) fn close(a: f64, b: f64) -> bool {
    (a - b).abs() <= 1e-8 + 1e-5 * b.abs()
}

// Pressure is hPa; temperature is kelvin throughout this module.
pub(super) fn vapor_pressure(t: f64) -> f64 {
    let heat_diff = CP_L - CP_V;
    let latent = LV - heat_diff * (t - T0);
    6.112 * (T0 / t).powf(heat_diff / RV) * ((LV / T0 - latent / t) / RV).exp()
}

pub(super) fn mixing_ratio(p: f64, td: f64) -> f64 {
    let e = vapor_pressure(td);
    if e >= p {
        return f64::NAN;
    }
    EPSILON * e / (p - e)
}

pub(super) fn specific_humidity(p: f64, td: f64) -> f64 {
    let r = mixing_ratio(p, td);
    r / (1. + r)
}

pub(super) fn dewpoint_from_humidity(p: f64, q: f64) -> f64 {
    let r = q / (1. - q);
    let log = (p * r / (EPSILON + r) / 6.112).ln();
    273.15 + 243.5 * log / (17.67 - log)
}

pub(super) fn theta_e(p: f64, t: f64, td: f64) -> f64 {
    let r = mixing_ratio(p, td);
    let tl = 56. + 1. / (1. / (td - 56.) + (t / td).ln() / 800.);
    let theta_l = t * (1000. / (p - vapor_pressure(td))).powf(KAPPA) * (t / tl).powf(0.28 * r);
    theta_l * (r * (1. + 0.448 * r) * (3036. / tl - 1.78)).exp()
}

pub(super) fn virtual_temperature(t: f64, r: f64) -> f64 {
    t * (r + EPSILON) / (EPSILON * (1. + r))
}

pub(super) fn lcl(p: f64, t: f64, td: f64) -> Option<(f64, f64)> {
    let q = specific_humidity(p, td);
    let heat_ratio = (CP + q * (CP_V - CP)) / (RD + q * (RV - RD));
    let a = heat_ratio + (CP_L - CP_V) / RV;
    let c = -(LV + (CP_L - CP_V) * T0) / (RV * t * a);
    let x = (vapor_pressure(td) / vapor_pressure(t)).powf(1. / a) * c * c.exp();
    if !(-1. / std::f64::consts::E..0.).contains(&x) {
        return None;
    }
    // Solve w + ln(-w) = ln(-x) on Lambert W's negative branch.
    let log = (-x).ln();
    let mut w = log - (-log).ln();
    for _ in 0..40 {
        let delta = (w + (-w).ln() - log) / (1. + 1. / w);
        w -= delta;
        if delta.abs() < 1e-13 {
            let tl = c / w * t;
            return Some((p * (tl / t).powf(heat_ratio), tl));
        }
    }
    None
}

fn moist_gradient(log_p: f64, t: f64) -> f64 {
    let r = mixing_ratio(log_p.exp(), t);
    (RD * t + LV * r) / (CP + LV * LV * r * EPSILON / (RD * t * t))
}

pub(super) fn parcel_profile(p: &[f64], t: f64, td: f64) -> Option<Vec<f64>> {
    let (pl, _) = lcl(p[0], t, td)?;
    let mut log_p = pl.ln();
    let initial_moist_t = t * (pl / p[0]).powf(KAPPA);
    let mut moist_t = initial_moist_t;
    let mut trace = Vec::with_capacity(p.len());
    for &pressure in p {
        if pressure >= pl {
            trace.push(t * (pressure / p[0]).powf(KAPPA));
            continue;
        }
        if close(pressure, pl) {
            trace.push(initial_moist_t);
            continue;
        }
        let target = pressure.ln();
        // Scalar RK4 in log-pressure avoids a solver dependency and singular
        // pressure steps near the top. The fixed 0.005 step is validated against
        // MetPy's LSODA result, well inside the existing ECAPE tolerance.
        let steps = ((log_p - target) / 0.005).ceil().max(1.) as usize;
        let step = (target - log_p) / steps as f64;
        for _ in 0..steps {
            let k1 = moist_gradient(log_p, moist_t);
            let k2 = moist_gradient(log_p + step / 2., moist_t + step * k1 / 2.);
            let k3 = moist_gradient(log_p + step / 2., moist_t + step * k2 / 2.);
            let k4 = moist_gradient(log_p + step, moist_t + step * k3);
            moist_t += step / 6. * (k1 + 2. * k2 + 2. * k3 + k4);
            log_p += step;
        }
        log_p = target;
        if !moist_t.is_finite() {
            return None;
        }
        trace.push(moist_t);
    }
    Some(trace)
}

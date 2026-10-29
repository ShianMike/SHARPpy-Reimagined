//! Pressure-integrated Bunkers inflow, matching MetPy's ECAPE convention.

use super::thermodynamics::close;

fn interpolate(x: &[f64], y: &[f64], target: f64) -> f64 {
    let upper = x.partition_point(|&v| v < target);
    if upper == 0 {
        return y[0];
    }
    if upper == x.len() {
        return y[y.len() - 1];
    }
    let lo = upper - 1;
    y[lo] + (y[upper] - y[lo]) * (target - x[lo]) / (x[upper] - x[lo])
}

fn mean(p: &[f64], z: &[f64], u: &[f64], v: &[f64], bottom: f64, top: f64) -> Option<[f64; 2]> {
    if bottom < z[0] || top > z[z.len() - 1] && !close(top, z[z.len() - 1]) {
        return None;
    }
    let pb = interpolate(z, p, bottom);
    let pt = interpolate(z, p, top);
    let mut pressures: Vec<_> = p
        .iter()
        .copied()
        .filter(|&p| (p < pb || close(p, pb)) && (p > pt || close(p, pt)))
        .collect();
    for bound in [pb, pt] {
        if !pressures.iter().any(|&p| close(bound, p)) {
            pressures.push(bound);
        }
    }
    pressures.sort_by(|a, b| a.total_cmp(b));
    let logp: Vec<_> = p.iter().rev().map(|p| p.ln()).collect();
    let columns = [u, v].map(|data| data.iter().rev().copied().collect::<Vec<_>>());
    Some(columns.map(|data| {
        let values: Vec<_> = pressures
            .iter()
            .map(|p| interpolate(&logp, &data, p.ln()))
            .collect();
        pressures
            .windows(2)
            .zip(values.windows(2))
            .map(|(p, w)| (w[0] + w[1]) * 0.5 * (p[1] - p[0]))
            .sum::<f64>()
            / (pressures[pressures.len() - 1] - pressures[0])
    }))
}

pub(super) fn storm_relative(p: &[f64], z: &[f64], u: &[f64], v: &[f64]) -> Option<f64> {
    let base = z[0];
    let motion = mean(p, z, u, v, base, base + 6000.)?;
    let lower = mean(p, z, u, v, base, base + 500.)?;
    let upper = mean(p, z, u, v, base + 5500., base + 6000.)?;
    let shear = [upper[0] - lower[0], upper[1] - lower[1]];
    let magnitude = shear[0].hypot(shear[1]);
    let storm = [
        motion[0] + 7.5 * shear[1] / magnitude,
        motion[1] - 7.5 * shear[0] / magnitude,
    ];
    let mut sum = 0.;
    let mut count = 0;
    for i in 0..z.len() {
        if z[i] >= base && z[i] <= base + 1000. {
            sum += (u[i] - storm[0]).hypot(v[i] - storm[1]);
            count += 1;
        }
    }
    let mean = sum / f64::from(count);
    mean.is_finite().then_some(mean)
}

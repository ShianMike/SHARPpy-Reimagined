//! Reference LFC/EL selection and virtual-temperature CAPE for ECAPE only.
//! Adapted from MetPy (BSD-3-Clause); see the repository NOTICE.

use super::thermodynamics::{
    close, lcl, mixing_ratio, parcel_profile, theta_e, virtual_temperature, RD,
};

fn crossings(p: &[f64], difference: &[f64], direction: i8) -> Vec<f64> {
    let mut result = Vec::new();
    for i in 0..p.len().saturating_sub(1) {
        let a = difference[i];
        let b = difference[i + 1];
        let sign = |value: f64| i8::from(value > 0.) - i8::from(value < 0.);
        if sign(a) == sign(b) || !a.is_finite() || !b.is_finite() {
            continue;
        }
        if direction > 0 && b <= 0. || direction < 0 && b >= 0. {
            continue;
        }
        let intersection = ((b * p[i].ln() - a * p[i + 1].ln()) / (b - a)).exp();
        if result.last().is_none_or(|last| *last != intersection) {
            result.push(intersection);
        }
    }
    result
}

pub(super) fn lfc_el(
    p: &[f64],
    t: &[f64],
    td: &[f64],
    trace: &[f64],
    bottom_lfc: bool,
) -> Option<(Option<f64>, Option<f64>)> {
    let delta: Vec<_> = trace.iter().zip(t).map(|(a, b)| a - b).collect();
    // ECAPE supplies Celsius environmental values to lfc's explicit first-point
    // comparison; cape_cin supplies virtual temperatures in kelvin.
    let offset = if bottom_lfc { 0. } else { 273.15 };
    let first = usize::from(close(trace[0] - offset, t[0] - offset));
    let candidates = crossings(&p[first..], &delta[first..], 1);
    let (lfc_lcl, _) = lcl(p[0], trace[0], td[0])?;
    let valid: Vec<_> = candidates
        .iter()
        .copied()
        .filter(|&p| p < lfc_lcl)
        .collect();
    let lfc = if !valid.is_empty() {
        if bottom_lfc {
            valid.first()
        } else {
            valid.last()
        }
        .copied()
    } else if candidates.is_empty() {
        p.iter()
            .enumerate()
            .any(|(i, &p)| p < lfc_lcl && delta[i] > 0. && !close(trace[i], t[i]))
            .then_some(lfc_lcl)
    } else {
        let early_el = crossings(&p[1..], &delta[1..], -1)
            .into_iter()
            .reduce(f64::min);
        (!early_el.is_some_and(|el| el > lfc_lcl)).then_some(lfc_lcl)
    };
    let (el_lcl, _) = lcl(p[0], t[0], td[0])?;
    let el = if trace[trace.len() - 1] > t[t.len() - 1] {
        None
    } else {
        crossings(&p[1..], &delta[1..], -1)
            .into_iter()
            .rfind(|&p| p < el_lcl)
    };
    Some((lfc, el))
}

fn linear_at(p: &[f64], values: &[f64], target: f64) -> f64 {
    let upper = p.partition_point(|&pressure| pressure >= target);
    if upper == 0 {
        values[0]
    } else if upper == p.len() {
        values[values.len() - 1]
    } else {
        let lo = upper - 1;
        values[lo] + (values[upper] - values[lo]) * (target - p[lo]) / (p[upper] - p[lo])
    }
}

pub(super) fn most_unstable(p: &[f64], t: &[f64], td: &[f64]) -> Option<usize> {
    let target = p[0] - 300.;
    if target < p[p.len() - 1] && !close(target, p[p.len() - 1]) {
        return None;
    }
    let nearest = p
        .iter()
        .min_by(|a, b| (**a - target).abs().total_cmp(&(**b - target).abs()))?;
    let count = p.partition_point(|&pressure| pressure > *nearest || close(pressure, *nearest));
    // np.argmax selects the first parcel when theta-e values tie.
    (0..count).reduce(|best, index| {
        if theta_e(p[index], t[index], td[index]) > theta_e(p[best], t[best], td[best]) {
            index
        } else {
            best
        }
    })
}

pub(super) fn cape(p: &[f64], t: &[f64], td: &[f64], start: usize) -> Option<f64> {
    let (p, t, td) = (&p[start..], &t[start..], &td[start..]);
    let mut trace = parcel_profile(p, t[0], td[0])?;
    let (pl, tl) = lcl(p[0], t[0], td[0])?;
    let mut pressure = p.to_vec();
    let mut temperature = t.to_vec();
    let mut dewpoint = td.to_vec();
    // LCL environmental values use linear pressure; intersections use log p.
    let index = p.partition_point(|&p| p >= pl);
    let insert_t = linear_at(p, t, pl);
    let insert_td = linear_at(p, td, pl);
    pressure.insert(index, pl);
    temperature.insert(index, insert_t);
    dewpoint.insert(index, insert_td);
    trace.insert(index, tl);
    let r0 = mixing_ratio(pressure[0], dewpoint[0]);
    for i in 0..pressure.len() {
        let r = if pressure[i] > pl {
            r0
        } else {
            mixing_ratio(pressure[i], trace[i])
        };
        trace[i] = virtual_temperature(trace[i], r);
        temperature[i] =
            virtual_temperature(temperature[i], mixing_ratio(pressure[i], dewpoint[i]));
    }
    let Some((Some(lfc), el)) = lfc_el(&pressure, &temperature, &dewpoint, &trace, true) else {
        return Some(0.);
    };
    let el = el.unwrap_or(pressure[pressure.len() - 1]);
    let delta: Vec<_> = trace.iter().zip(temperature).map(|(a, b)| a - b).collect();
    let mut points: Vec<_> = pressure
        .iter()
        .copied()
        .zip(delta.iter().copied())
        .collect();
    points.extend(
        crossings(&pressure[1..], &delta[1..], 0)
            .into_iter()
            .map(|p| (p, 0.)),
    );
    points.sort_by(|a, b| a.0.total_cmp(&b.0));
    points.dedup_by(|a, b| (a.0 - b.0).abs() <= 1e-6);
    let selected: Vec<_> = points
        .into_iter()
        .filter(|&(p, _)| (p < lfc || close(p, lfc)) && (p > el || close(p, el)))
        .collect();
    let area = selected
        .windows(2)
        .map(|pair| (pair[0].1 + pair[1].1) * 0.5 * (pair[1].0.ln() - pair[0].0.ln()))
        .sum::<f64>()
        * RD;
    area.is_finite().then_some(area)
}

//! ECAPE's expensive unit-aware reference workspace, evaluated without Python.
//! This does not substitute SHARPpy parcels for the different MetPy convention.

mod buoyancy;
mod inflow;
#[cfg(test)]
mod tests;
mod thermodynamics;

use thermodynamics::{dewpoint_from_humidity, parcel_profile, specific_humidity, CP, G, LV};

#[derive(Debug)]
pub(crate) struct Blocks {
    pub cape: f64,
    pub ncape: Option<f64>,
    pub sr_wind: Option<f64>,
    pub el_z: Option<f64>,
}

pub(crate) fn building_blocks(columns: [Vec<f64>; 6]) -> Result<Option<Blocks>, String> {
    let [p, z, mut t, mut td, mut u, mut v] = columns;
    if p.len() < 3
        || [&z, &t, &td, &u, &v]
            .iter()
            .any(|column| column.len() != p.len())
    {
        return Err("ECAPE requires six equal-length columns with at least three levels".into());
    }
    if [&p, &z, &t, &td, &u, &v]
        .iter()
        .any(|column| column.iter().any(|v| !v.is_finite()))
        || p.iter().any(|&p| p <= 0.)
        || p.windows(2).any(|p| p[0] < p[1])
    {
        return Err("ECAPE requires finite columns and decreasing positive pressure".into());
    }
    for value in t.iter_mut().chain(&mut td) {
        *value += 273.15;
    }
    let humidity: Vec<_> = p
        .iter()
        .zip(&td)
        .map(|(&p, &td)| specific_humidity(p, td))
        .collect();
    for i in 0..td.len() {
        td[i] = dewpoint_from_humidity(p[i], humidity[i]);
    }
    for value in u.iter_mut().chain(&mut v) {
        *value /= 1.9438444924406046;
    }
    let Some(start) = buoyancy::most_unstable(&p, &t, &td) else {
        return Ok(None);
    };
    let Some(cape) = buoyancy::cape(&p, &t, &td, start) else {
        return Ok(None);
    };
    if cape <= 0. {
        return Ok(Some(Blocks {
            cape,
            ncape: None,
            sr_wind: None,
            el_z: None,
        }));
    }
    let Some(trace) = parcel_profile(&p, t[start], td[start]) else {
        return Ok(None);
    };
    let Some((Some(lfc), Some(el))) = buoyancy::lfc_el(&p, &t, &td, &trace, false) else {
        return Ok(None);
    };
    let Some(lfc_idx) = p.iter().rposition(|&p| p > lfc) else {
        return Ok(None);
    };
    let Some(el_idx) = p.iter().rposition(|&p| p > el) else {
        return Ok(None);
    };
    let mut mse_sum = 0.;
    let mut integral = Vec::with_capacity(el_idx + 1);
    // Do not evaluate liquid saturation in the unused upper stratosphere.
    for i in 0..=el_idx {
        mse_sum += CP * t[i] + G * z[i] + LV * humidity[i];
        let saturated = CP * t[i] + G * z[i] + LV * thermodynamics::mixing_ratio(p[i], t[i]);
        integral.push(-G / (CP * t[i]) * (mse_sum / (i + 1) as f64 - saturated));
    }
    let ncape = if el_idx <= lfc_idx {
        0.
    } else {
        (lfc_idx..el_idx)
            .map(|i| (integral[i] + integral[i + 1]) * 0.5 * (z[i + 1] - z[i]))
            .sum()
    };
    let Some(sr_wind) = inflow::storm_relative(&p, &z, &u, &v) else {
        return Ok(None);
    };
    Ok(ncape.is_finite().then_some(Blocks {
        cape,
        ncape: Some(ncape),
        sr_wind: Some(sr_wind),
        el_z: Some(z[el_idx]),
    }))
}

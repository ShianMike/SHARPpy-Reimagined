use crate::interpolation::PreparedInterpolation;
use crate::wind::wind_to_components;
use numpy::PyReadonlyArray1;
use pyo3::prelude::*;

const SHARPPY_KTS2MS: f64 = 0.514444;
const SHARPPY_MS2KTS: f64 = 1.94384449;
const MAX_ARANGE_SAMPLES: usize = 5_000;

fn is_missing(value: f64, missing: Option<f64>) -> bool {
    !value.is_finite() || missing.is_some_and(|sentinel| value == sentinel)
}

fn qc_number(value: f64, missing: Option<f64>) -> bool {
    !is_missing(value, missing) && value != -9999.0
}

fn arange_desc_1hpa(pbot: f64, ptop: f64) -> Option<Vec<f64>> {
    if !pbot.is_finite() || !ptop.is_finite() {
        return None;
    }
    let stop = ptop - 1.0;
    let raw_count = (stop - pbot) / -1.0;
    if !raw_count.is_finite() || raw_count <= 0.0 {
        return Some(Vec::new());
    }
    let count = raw_count.ceil() as usize;
    if count > MAX_ARANGE_SAMPLES {
        return None;
    }
    let mut ps = Vec::with_capacity(count);
    for idx in 0..count {
        ps.push(pbot - idx as f64);
    }
    Some(ps)
}

fn masked_weighted_mean(values: &[f64], weights: Option<&[f64]>, missing: Option<f64>) -> f64 {
    let mut num = 0.0;
    let mut den = 0.0;
    let mut count = 0usize;
    for (idx, &val) in values.iter().enumerate() {
        if is_missing(val, missing) {
            continue;
        }
        if let Some(w) = weights {
            let weight = w[idx];
            if !weight.is_finite() {
                continue;
            }
            num += val * weight;
            den += weight;
        } else {
            num += val;
            count += 1;
        }
    }
    if weights.is_some() {
        if den == 0.0 || !den.is_finite() {
            f64::NAN
        } else {
            num / den
        }
    } else if count == 0 {
        f64::NAN
    } else {
        num / count as f64
    }
}

fn stp_cin_value(mlcape: f64, esrh: f64, ebwd_ms: f64, mllcl: f64, mlcinh: f64) -> f64 {
    let cape_term = mlcape / 1500.0;
    let eshr_term = esrh / 150.0;
    let ebwd_term = if ebwd_ms < 12.5 {
        0.0
    } else if ebwd_ms > 30.0 {
        1.5
    } else {
        ebwd_ms / 20.0
    };
    let lcl_term = if mllcl < 1000.0 {
        1.0
    } else if mllcl > 2000.0 {
        0.0
    } else {
        (2000.0 - mllcl) / 1000.0
    };
    let cinh_term = if mlcinh > -50.0 {
        1.0
    } else if mlcinh < -200.0 {
        0.0
    } else {
        (mlcinh + 200.0) / 150.0
    };
    (cape_term * eshr_term * ebwd_term * lcl_term * cinh_term).max(0.0)
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn compute_fast_effective_stp_cin(
    pres: &[f64],
    hght: &[f64],
    u: &[f64],
    v: &[f64],
    sfc: Option<usize>,
    missing: Option<f64>,
    effective_bottom: f64,
    effective_top: f64,
    mu_cape: f64,
    mu_cin: f64,
    mu_el_height: f64,
    ml_cape: f64,
    ml_cin: f64,
    ml_lcl_height: f64,
    latitude: f64,
) -> Result<Option<f64>, String> {
    if !(pres.len() == hght.len() && pres.len() == u.len() && pres.len() == v.len()) {
        return Ok(None);
    }
    if !effective_bottom.is_finite() || !effective_top.is_finite() {
        return Ok(Some(0.0));
    }
    if ![
        mu_cape,
        mu_cin,
        mu_el_height,
        ml_cape,
        ml_cin,
        ml_lcl_height,
    ]
    .iter()
    .all(|val| val.is_finite())
    {
        return Ok(None);
    }

    let sfc_idx = match sfc {
        Some(idx) if idx < pres.len() => idx,
        None => match pres
            .iter()
            .position(|&p| !is_missing(p, missing) && p > 0.0)
        {
            Some(idx) => idx,
            None => return Ok(None),
        },
        _ => return Ok(None),
    };

    let sfc_pres = pres[sfc_idx];
    let sfc_hght = hght[sfc_idx];
    if is_missing(sfc_pres, missing) || is_missing(sfc_hght, missing) {
        return Ok(None);
    }

    let logp: Vec<f64> = pres
        .iter()
        .map(|&p| {
            if !is_missing(p, missing) && p > 0.0 {
                p.log10()
            } else {
                f64::NAN
            }
        })
        .collect();
    let logp_to_hght = PreparedInterpolation::new(&logp, hght, missing)?;
    let logp_to_u = PreparedInterpolation::new(&logp, u, missing)?;
    let logp_to_v = PreparedInterpolation::new(&logp, v, missing)?;
    let hght_to_logp = PreparedInterpolation::new(hght, &logp, missing)?;

    let valid_winds = u
        .iter()
        .zip(v.iter())
        .any(|(&ui, &vi)| !is_missing(ui, missing) && !is_missing(vi, missing));
    if !valid_winds {
        return Ok(None);
    }

    let interp_hght = |p: f64| -> f64 {
        if !p.is_finite() || p <= 0.0 {
            f64::NAN
        } else {
            logp_to_hght.scalar_sharppy(p.log10(), false)
        }
    };
    let interp_pres = |h: f64| -> f64 {
        if !h.is_finite() {
            f64::NAN
        } else {
            hght_to_logp.scalar_sharppy(h, true)
        }
    };
    let interp_comp = |p: f64| -> (f64, f64) {
        if !p.is_finite() || p <= 0.0 {
            return (f64::NAN, f64::NAN);
        }
        let lp = p.log10();
        (
            logp_to_u.scalar_sharppy(lp, false),
            logp_to_v.scalar_sharppy(lp, false),
        )
    };

    let h_bot_msl = interp_hght(effective_bottom);
    if !h_bot_msl.is_finite() {
        return Ok(None);
    }
    let base_agl = h_bot_msl - sfc_hght;

    let d = 7.5 * SHARPPY_MS2KTS;
    let (rstu, rstv, lstu, lstv) = if mu_cape > 100.0 && qc_number(mu_el_height, missing) {
        let htop = base_agl + (mu_el_height - base_agl) * 0.65;
        let ptop = interp_pres(sfc_hght + htop);
        if !qc_number(effective_bottom, missing) || !qc_number(ptop, missing) {
            return Ok(None);
        }
        let Some(ps) = arange_desc_1hpa(effective_bottom, ptop) else {
            return Ok(None);
        };
        if ps.is_empty() {
            return Ok(None);
        }
        let mut us = Vec::with_capacity(ps.len());
        let mut vs = Vec::with_capacity(ps.len());
        for &p in &ps {
            let (ui, vi) = interp_comp(p);
            us.push(ui);
            vs.push(vi);
        }
        let mnu = masked_weighted_mean(&us, Some(&ps), missing);
        let mnv = masked_weighted_mean(&vs, Some(&ps), missing);
        let (ubot, vbot) = interp_comp(effective_bottom);
        let (utop, vtop) = interp_comp(ptop);
        let sru = utop - ubot;
        let srv = vtop - vbot;
        let srmag = sru.hypot(srv);
        if ![mnu, mnv, srmag].iter().all(|x| x.is_finite()) || srmag == 0.0 {
            return Ok(None);
        }
        let uchg = d / srmag * srv;
        let vchg = d / srmag * sru;
        (mnu + uchg, mnv - vchg, mnu - uchg, mnv + vchg)
    } else {
        let p6km = interp_pres(sfc_hght + 6000.0);
        if !qc_number(sfc_pres, missing) || !qc_number(p6km, missing) {
            return Ok(None);
        }
        let Some(ps) = arange_desc_1hpa(sfc_pres, p6km) else {
            return Ok(None);
        };
        if ps.is_empty() {
            return Ok(None);
        }
        let mut us = Vec::with_capacity(ps.len());
        let mut vs = Vec::with_capacity(ps.len());
        for &p in &ps {
            let (ui, vi) = interp_comp(p);
            us.push(ui);
            vs.push(vi);
        }
        let mnu6 = masked_weighted_mean(&us, None, missing);
        let mnv6 = masked_weighted_mean(&vs, None, missing);
        let (ubot, vbot) = interp_comp(sfc_pres);
        let (utop, vtop) = interp_comp(p6km);
        let shru = utop - ubot;
        let shrv = vtop - vbot;
        let mag = shru.hypot(shrv);
        if ![mnu6, mnv6, mag].iter().all(|x| x.is_finite()) || mag == 0.0 {
            return Ok(None);
        }
        let tmp = d / mag;
        (
            mnu6 + tmp * shrv,
            mnv6 - tmp * shru,
            mnu6 - tmp * shrv,
            mnv6 + tmp * shru,
        )
    };

    let h_top_msl = interp_hght(effective_top);
    if !h_top_msl.is_finite() {
        return Ok(None);
    }
    let bottom_agl = base_agl;
    let top_agl = h_top_msl - sfc_hght;
    let depth = (mu_el_height - bottom_agl) / 2.0;
    let shear_top = interp_pres(sfc_hght + bottom_agl + depth);
    if !qc_number(shear_top, missing) {
        return Ok(None);
    }
    let (ubot, vbot) = interp_comp(effective_bottom);
    let (utop, vtop) = interp_comp(shear_top);
    let bulk_shear_kt = (utop - ubot).hypot(vtop - vbot);
    if !bulk_shear_kt.is_finite() {
        return Ok(None);
    }

    let lat = if latitude.is_finite() { latitude } else { 0.0 };
    let (stu, stv) = if lat < 0.0 {
        (lstu, lstv)
    } else {
        (rstu, rstv)
    };
    if !qc_number(bottom_agl, missing)
        || !qc_number(top_agl, missing)
        || !qc_number(stu, missing)
        || !qc_number(stv, missing)
    {
        return Ok(None);
    }

    let mut helicity = if bottom_agl == top_agl {
        0.0
    } else {
        let plower = interp_pres(sfc_hght + bottom_agl);
        let pupper = interp_pres(sfc_hght + top_agl);
        if !plower.is_finite() || !pupper.is_finite() {
            return Ok(None);
        }
        let ind1 = pres
            .iter()
            .enumerate()
            .filter(|(_, &p)| !is_missing(p, missing) && plower >= p)
            .map(|(i, _)| i)
            .min();
        let ind2 = pres
            .iter()
            .enumerate()
            .filter(|(_, &p)| !is_missing(p, missing) && pupper <= p)
            .map(|(i, _)| i)
            .max();
        let (Some(ind1), Some(ind2)) = (ind1, ind2) else {
            return Ok(None);
        };
        if ind2 < ind1 {
            return Ok(None);
        }
        let (u1, v1) = interp_comp(plower);
        let (u2, v2) = interp_comp(pupper);
        if ![u1, v1, u2, v2].iter().all(|x| x.is_finite()) {
            return Ok(None);
        }
        let mut layer_u = Vec::with_capacity(ind2 - ind1 + 3);
        let mut layer_v = Vec::with_capacity(ind2 - ind1 + 3);
        layer_u.push(u1);
        layer_v.push(v1);
        for idx in ind1..=ind2 {
            if !is_missing(u[idx], missing) && !is_missing(v[idx], missing) {
                layer_u.push(u[idx]);
                layer_v.push(v[idx]);
            }
        }
        layer_u.push(u2);
        layer_v.push(v2);

        let mut total = 0.0;
        for idx in 0..layer_u.len() - 1 {
            let sru0 = (layer_u[idx] - stu) * SHARPPY_KTS2MS;
            let srv0 = (layer_v[idx] - stv) * SHARPPY_KTS2MS;
            let sru1 = (layer_u[idx + 1] - stu) * SHARPPY_KTS2MS;
            let srv1 = (layer_v[idx + 1] - stv) * SHARPPY_KTS2MS;
            total += sru1 * srv0 - sru0 * srv1;
        }
        total
    };

    if lat < 0.0 {
        helicity = -helicity;
    }
    let mut value = stp_cin_value(
        ml_cape,
        helicity,
        bulk_shear_kt * SHARPPY_KTS2MS,
        ml_lcl_height,
        ml_cin,
    );
    if lat < 0.0 {
        value = -value;
    }
    Ok(value.is_finite().then_some(value))
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
#[pyo3(signature = (
    pres,
    hght,
    wind_a,
    wind_b,
    winds_are_components = true,
    sfc = None,
    missing = Some(-9999.0),
    effective_bottom = f64::NAN,
    effective_top = f64::NAN,
    mu_cape = f64::NAN,
    mu_cin = f64::NAN,
    mu_el_height = f64::NAN,
    ml_cape = f64::NAN,
    ml_cin = f64::NAN,
    ml_lcl_height = f64::NAN,
    latitude = 0.0
))]
pub(super) fn fast_effective_stp_cin(
    pres: PyReadonlyArray1<'_, f64>,
    hght: PyReadonlyArray1<'_, f64>,
    wind_a: PyReadonlyArray1<'_, f64>,
    wind_b: PyReadonlyArray1<'_, f64>,
    winds_are_components: bool,
    sfc: Option<usize>,
    missing: Option<f64>,
    effective_bottom: f64,
    effective_top: f64,
    mu_cape: f64,
    mu_cin: f64,
    mu_el_height: f64,
    ml_cape: f64,
    ml_cin: f64,
    ml_lcl_height: f64,
    latitude: f64,
) -> PyResult<Option<f64>> {
    let pres_s = pres.as_slice()?;
    let hght_s = hght.as_slice()?;
    let wind_a_s = wind_a.as_slice()?;
    let wind_b_s = wind_b.as_slice()?;

    let (u_buf, v_buf);
    let (u_ref, v_ref) = if winds_are_components {
        (wind_a_s, wind_b_s)
    } else {
        let (u_vec, v_vec) = wind_to_components(wind_a_s, wind_b_s, missing)
            .map_err(pyo3::exceptions::PyValueError::new_err)?;
        u_buf = u_vec;
        v_buf = v_vec;
        (u_buf.as_slice(), v_buf.as_slice())
    };

    compute_fast_effective_stp_cin(
        pres_s,
        hght_s,
        u_ref,
        v_ref,
        sfc,
        missing,
        effective_bottom,
        effective_top,
        mu_cape,
        mu_cin,
        mu_el_height,
        ml_cape,
        ml_cin,
        ml_lcl_height,
        latitude,
    )
    .map_err(pyo3::exceptions::PyValueError::new_err)
}

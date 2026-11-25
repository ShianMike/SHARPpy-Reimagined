use crate::interpolation::PreparedInterpolation;
use crate::kinematics::KTS_PER_MS;
use crate::parcels::theta_e;
use numpy::PyReadonlyArray1;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

const VENDORED_MISSING: f64 = -9999.0;

// DCP constants
const DCAPE_NORM: f64 = 980.0;
const MUCAPE_NORM: f64 = 2000.0;
const SHEAR_NORM: f64 = 20.0;
const MNWIND_NORM: f64 = 16.0;

// EHI & VGP & HPI constants
const EHI_NORM: f64 = 160000.0;
const VGP_TOP_AGL: f64 = 4000.0;
const HPI_CAPE_NORM: f64 = 500.0;
const HPI_WBZ_CEILING: f64 = 3350.0;

// Peskov constants
const PESKOV_CAPE_NORM: f64 = 1000.0;
const PESKOV_DD_NORM: f64 = 5.0;
const PESKOV_DD_PRES: f64 = 700.0;

// LSCP constants
const LSCP_MUCAPE_NORM: f64 = 1000.0;
const LSCP_ESRH_NORM: f64 = 50.0;
const LSCP_EBWD_NORM: f64 = 20.0;
const LSCP_MUCIN_NORM: f64 = -40.0;

// NSTP constants
const NSTP_LR_NORM: f64 = 9.0;
const NSTP_MLCAPE3_NORM: f64 = 100.0;
const NSTP_MLCIN_BASE: f64 = 225.0;
const NSTP_MLCIN_NORM: f64 = 200.0;
const NSTP_SHEAR_BASE_MS: f64 = 18.0;
const NSTP_SHEAR_NORM_MS: f64 = 5.0;
const NSTP_VORT_NORM_S: f64 = 8.0e-5;

// MOSHE constants
const MOSHE_LLLR_OFFSET: f64 = 4.0;
const MOSHE_LLLR_NORM: f64 = 4.0;
const MOSHE_SHEAR_OFFSET_MS: f64 = 8.0;
const MOSHE_SHEAR_NORM_MS: f64 = 10.0;
const MOSHE_TEVV_OFFSET: f64 = 10.0;
const MOSHE_TEVV_NORM: f64 = 9.0;
const MOSHE_LAYER_DEPTH_KM: f64 = 2.0;

// Hazard constants
const MUCAPE_MIN_CONVECTION: f64 = 25.0;
const STP_TORNADO: f64 = 1.0;
const SCP_SUPERCELL: f64 = 1.0;
const ESRH_TORNADO: f64 = 100.0;
const EBWD_SUPERCELL: f64 = 40.0;
const EBWD_ORGANIZED: f64 = 30.0;
const SHIP_HAIL: f64 = 1.0;
const MUCAPE_WIND: f64 = 1000.0;
const MUCAPE_MARGINAL: f64 = 500.0;
const EBWD_MARGINAL: f64 = 20.0;

// Winter Kuchera constants
const KUCHERA_PIVOT_K: f64 = 271.16;
const KUCHERA_BASE_RATIO: f64 = 12.0;
const KUCHERA_WARM_FACTOR: f64 = 2.0;
const ZERO_CELSIUS_K: f64 = 273.15;

// Fire constants
const KNOTS_TO_MS: f64 = 1852.0 / 3600.0;

fn extract_usable_scalar(value: Option<&Bound<'_, PyAny>>) -> Option<f64> {
    let obj = value?;
    if obj.is_none() {
        return None;
    }
    if let Ok(mask) = obj.getattr("mask") {
        if mask.is_truthy().unwrap_or(false) {
            return None;
        }
    }
    let num = obj.extract::<f64>().ok()?;
    if !num.is_finite() || num == VENDORED_MISSING {
        return None;
    }
    Some(num)
}

pub(crate) fn compute_max_tevv(
    pres: &[f64],
    hght: &[f64],
    tmpc: &[f64],
    dwpc: &[f64],
    omeg: &[f64],
    sfc: usize,
    missing: Option<f64>,
) -> Result<Option<f64>, String> {
    if !(pres.len() == hght.len()
        && pres.len() == tmpc.len()
        && pres.len() == dwpc.len()
        && pres.len() == omeg.len())
    {
        return Ok(None);
    }
    if sfc >= hght.len() || !hght[sfc].is_finite() || missing.is_some_and(|m| hght[sfc] == m) {
        return Ok(None);
    }
    let sfc_hght = hght[sfc];
    let logp: Vec<f64> = pres
        .iter()
        .map(|&p| {
            if p.is_finite() && !missing.is_some_and(|m| p == m) && p > 0.0 {
                p.log10()
            } else {
                f64::NAN
            }
        })
        .collect();
    let omeg_clean: Vec<f64> = omeg
        .iter()
        .map(|&w| {
            if w.is_finite() && w > -9000.0 && !missing.is_some_and(|m| w == m) {
                w
            } else {
                f64::NAN
            }
        })
        .collect();
    if omeg_clean.iter().all(|w| !w.is_finite()) {
        return Ok(None);
    }

    let h_to_logp = PreparedInterpolation::new(hght, &logp, missing)?;
    let h_to_t = PreparedInterpolation::new(hght, tmpc, missing)?;
    let h_to_td = PreparedInterpolation::new(hght, dwpc, missing)?;
    let h_to_w = PreparedInterpolation::new(hght, &omeg_clean, missing)?;

    let thetae_agl = |h_agl: f64| -> Option<f64> {
        let h_msl = sfc_hght + h_agl;
        let p = h_to_logp.scalar_sharppy(h_msl, true);
        let t = h_to_t.scalar_sharppy(h_msl, false);
        let td = h_to_td.scalar_sharppy(h_msl, false);
        if ![p, t, td].iter().all(|v| v.is_finite()) {
            return None;
        }
        let te = theta_e(p, t, td);
        te.is_finite().then_some(te)
    };

    let mut best: Option<f64> = None;
    for step in 0..9 {
        let top = 2000.0 + (step as f64) * 500.0;
        let bottom = top - MOSHE_LAYER_DEPTH_KM * 1000.0;
        let Some(te_bot) = thetae_agl(bottom) else {
            continue;
        };
        let Some(te_top) = thetae_agl(top) else {
            continue;
        };
        let omega_top = h_to_w.scalar_sharppy(sfc_hght + top, false);
        if !omega_top.is_finite() {
            continue;
        }
        let value = ((te_bot - te_top) / MOSHE_LAYER_DEPTH_KM) * (-omega_top);
        if value.is_finite() {
            best = Some(match best {
                Some(prev) => prev.max(value),
                None => value,
            });
        }
    }
    Ok(best)
}

#[pyfunction]
#[pyo3(signature = (pres, hght, tmpc, dwpc, omeg, sfc = 0, missing = Some(-9999.0)))]
pub(super) fn max_thetae_vertical_velocity(
    pres: PyReadonlyArray1<'_, f64>,
    hght: PyReadonlyArray1<'_, f64>,
    tmpc: PyReadonlyArray1<'_, f64>,
    dwpc: PyReadonlyArray1<'_, f64>,
    omeg: PyReadonlyArray1<'_, f64>,
    sfc: usize,
    missing: Option<f64>,
) -> PyResult<Option<f64>> {
    compute_max_tevv(
        pres.as_slice()?,
        hght.as_slice()?,
        tmpc.as_slice()?,
        dwpc.as_slice()?,
        omeg.as_slice()?,
        sfc,
        missing,
    )
    .map_err(PyValueError::new_err)
}

#[pyfunction]
pub(super) fn derived_dcp(
    dcape: f64,
    mucape: f64,
    shear06_kt: f64,
    mnwind06_kt: f64,
) -> Option<f64> {
    if ![dcape, mucape, shear06_kt, mnwind06_kt]
        .iter()
        .all(|v| v.is_finite())
    {
        return None;
    }
    if dcape == 0.0 || mucape == 0.0 {
        return Some(0.0);
    }
    let val = (dcape / DCAPE_NORM)
        * (mucape / MUCAPE_NORM)
        * (shear06_kt / SHEAR_NORM)
        * (mnwind06_kt / MNWIND_NORM);
    val.is_finite().then_some(val)
}

#[pyfunction]
pub(super) fn derived_ncape_ncin(
    mucape: Option<f64>,
    cin: Option<f64>,
    lfc_agl: Option<f64>,
    el_agl: Option<f64>,
    mu_start_agl: Option<f64>,
) -> (Option<f64>, Option<f64>) {
    let ncape = match (mucape, lfc_agl, el_agl) {
        (Some(c), Some(lfc), Some(el)) if c.is_finite() && lfc.is_finite() && el.is_finite() => {
            let depth = el - lfc;
            if depth > 0.0 {
                let v = c / depth;
                v.is_finite().then_some(v)
            } else {
                None
            }
        }
        _ => None,
    };
    let ncin = match (cin, lfc_agl, mu_start_agl) {
        (Some(c), Some(lfc), Some(start))
            if c.is_finite() && lfc.is_finite() && start.is_finite() =>
        {
            let depth = lfc - start;
            if depth > 0.0 {
                let v = c / depth;
                v.is_finite().then_some(v)
            } else {
                None
            }
        }
        _ => None,
    };
    (ncape, ncin)
}

#[pyfunction]
pub(super) fn derived_ehi(cape: f64, srh: f64) -> Option<f64> {
    if !cape.is_finite() || !srh.is_finite() {
        return None;
    }
    let val = (cape * srh) / EHI_NORM;
    val.is_finite().then_some(val)
}

#[pyfunction]
pub(super) fn derived_vgp(cape: f64, shear_kt: f64) -> Option<f64> {
    if !cape.is_finite() || !shear_kt.is_finite() || cape < 0.0 {
        return None;
    }
    if cape == 0.0 {
        return Some(0.0);
    }
    let shear_s = (shear_kt / KTS_PER_MS) / VGP_TOP_AGL;
    let val = cape.sqrt() * shear_s;
    val.is_finite().then_some(val)
}

#[pyfunction]
pub(super) fn derived_hpi(hgz_cape: f64, wbz_agl: f64) -> Option<f64> {
    if !hgz_cape.is_finite() || !wbz_agl.is_finite() {
        return None;
    }
    let melt_factor =
        (1.0 - (wbz_agl - HPI_WBZ_CEILING).max(0.0) / HPI_WBZ_CEILING).clamp(0.0, 1.0);
    let val = (hgz_cape / HPI_CAPE_NORM) * melt_factor;
    val.is_finite().then_some(val)
}

#[pyfunction]
pub(super) fn derived_peskov(kidx: f64, sbcape: f64, t700: f64, td700: f64) -> Option<f64> {
    if ![kidx, sbcape, t700, td700].iter().all(|v| v.is_finite()) {
        return None;
    }
    let dd700 = t700 - td700;
    let val = kidx + (sbcape / PESKOV_CAPE_NORM) - (dd700 / PESKOV_DD_NORM);
    val.is_finite().then_some(val)
}

#[pyfunction]
#[pyo3(signature = (pres, tmpc, dwpc, sbcape, missing = Some(-9999.0)))]
pub(super) fn derived_peskov_profile(
    pres: PyReadonlyArray1<'_, f64>,
    tmpc: PyReadonlyArray1<'_, f64>,
    dwpc: PyReadonlyArray1<'_, f64>,
    sbcape: f64,
    missing: Option<f64>,
) -> PyResult<Option<f64>> {
    if !sbcape.is_finite() || missing.is_some_and(|m| sbcape == m) {
        return Ok(None);
    }
    let pres_s = pres.as_slice()?;
    let tmpc_s = tmpc.as_slice()?;
    let dwpc_s = dwpc.as_slice()?;
    if pres_s.len() != tmpc_s.len() || pres_s.len() != dwpc_s.len() {
        return Ok(None);
    }
    let logp: Vec<f64> = pres_s
        .iter()
        .map(|&p| {
            if p.is_finite() && !missing.is_some_and(|m| p == m) && p > 0.0 {
                p.log10()
            } else {
                f64::NAN
            }
        })
        .collect();
    let logp_to_t =
        PreparedInterpolation::new(&logp, tmpc_s, missing).map_err(PyValueError::new_err)?;
    let logp_to_td =
        PreparedInterpolation::new(&logp, dwpc_s, missing).map_err(PyValueError::new_err)?;

    let t8 = logp_to_t.scalar_sharppy(850.0_f64.log10(), false);
    let t7 = logp_to_t.scalar_sharppy(PESKOV_DD_PRES.log10(), false);
    let t5 = logp_to_t.scalar_sharppy(500.0_f64.log10(), false);
    let td8 = logp_to_td.scalar_sharppy(850.0_f64.log10(), false);
    let td7 = logp_to_td.scalar_sharppy(PESKOV_DD_PRES.log10(), false);
    if ![t8, t7, t5, td8, td7].iter().all(|v| v.is_finite()) {
        return Ok(None);
    }
    let kidx = t8 - t5 + td8 - (t7 - td7);
    Ok(derived_peskov(kidx, sbcape, t7, td7))
}

#[pyfunction]
pub(super) fn derived_lscp(mucape: f64, mucin: f64, left_esrh: f64, ebwd_kt: f64) -> Option<f64> {
    if ![mucape, mucin, left_esrh, ebwd_kt]
        .iter()
        .all(|v| v.is_finite())
    {
        return None;
    }
    if mucape <= 0.0 {
        return Some(0.0);
    }
    let mut ebwd_ms = ebwd_kt / KTS_PER_MS;
    if ebwd_ms > 20.0 {
        ebwd_ms = 20.0;
    } else if ebwd_ms < 10.0 {
        ebwd_ms = 0.0;
    }
    let mucin_term = if mucin > LSCP_MUCIN_NORM || mucin >= 0.0 {
        1.0
    } else {
        LSCP_MUCIN_NORM / mucin
    };
    let val = (mucape / LSCP_MUCAPE_NORM)
        * (left_esrh / LSCP_ESRH_NORM)
        * (ebwd_ms / LSCP_EBWD_NORM)
        * mucin_term;
    val.is_finite().then_some(val)
}

#[pyfunction]
pub(super) fn derived_nstp(
    lr01: f64,
    mlcape3: f64,
    mlcin: f64,
    shear06_ms: f64,
    sfc_vort: f64,
) -> Option<f64> {
    if ![lr01, mlcape3, mlcin, shear06_ms, sfc_vort]
        .iter()
        .all(|v| v.is_finite())
    {
        return None;
    }
    let val = (lr01 / NSTP_LR_NORM)
        * (mlcape3 / NSTP_MLCAPE3_NORM)
        * ((NSTP_MLCIN_BASE - mlcin) / NSTP_MLCIN_NORM)
        * ((NSTP_SHEAR_BASE_MS - shear06_ms) / NSTP_SHEAR_NORM_MS)
        * (sfc_vort / NSTP_VORT_NORM_S);
    val.is_finite().then_some(val)
}

#[pyfunction]
pub(super) fn derived_moshe(lllr: f64, s15mg_ms: f64, ebwspd_kt: f64, maxtevv: f64) -> Option<f64> {
    if ![lllr, s15mg_ms, ebwspd_kt, maxtevv]
        .iter()
        .all(|v| v.is_finite())
    {
        return None;
    }
    let eshr_ms = ebwspd_kt / KTS_PER_MS;
    let val = ((lllr - MOSHE_LLLR_OFFSET).powi(2) / MOSHE_LLLR_NORM)
        * ((s15mg_ms - MOSHE_SHEAR_OFFSET_MS) / MOSHE_SHEAR_NORM_MS)
        * ((eshr_ms - MOSHE_SHEAR_OFFSET_MS) / MOSHE_SHEAR_NORM_MS)
        * ((maxtevv + MOSHE_TEVV_OFFSET) / MOSHE_TEVV_NORM);
    val.is_finite().then_some(val)
}

pub(crate) fn classify_hazard_values(
    mucape: f64,
    esrh: f64,
    ebwd: f64,
    stp: f64,
    scp: f64,
    ship: f64,
) -> &'static str {
    if mucape < MUCAPE_MIN_CONVECTION {
        return "none";
    }
    if stp >= STP_TORNADO && scp >= SCP_SUPERCELL && esrh >= ESRH_TORNADO && ebwd >= EBWD_ORGANIZED
    {
        return "tornado";
    }
    if scp >= SCP_SUPERCELL || ebwd >= EBWD_SUPERCELL {
        return "supercell";
    }
    if ship >= SHIP_HAIL {
        return "hail";
    }
    if mucape >= MUCAPE_WIND && ebwd >= EBWD_ORGANIZED {
        return "wind";
    }
    if mucape >= MUCAPE_MARGINAL || ebwd >= EBWD_MARGINAL {
        return "marginal";
    }
    "none"
}

#[pyfunction]
pub(super) fn classify_hazard_inputs(
    mucape: f64,
    esrh: f64,
    ebwd: f64,
    stp: f64,
    scp: f64,
    ship: f64,
) -> &'static str {
    classify_hazard_values(mucape, esrh, ebwd, stp, scp, ship)
}

pub(crate) fn kuchera_ratio_from_c(temp_c: f64) -> Option<f64> {
    if !temp_c.is_finite() || temp_c == VENDORED_MISSING {
        return None;
    }
    let max_temp_k = temp_c + ZERO_CELSIUS_K;
    let offset = KUCHERA_PIVOT_K - max_temp_k;
    let ratio = if max_temp_k > KUCHERA_PIVOT_K {
        KUCHERA_BASE_RATIO + KUCHERA_WARM_FACTOR * offset
    } else {
        KUCHERA_BASE_RATIO + offset
    };
    Some(ratio.max(0.0))
}

#[pyfunction]
#[pyo3(signature = (max_temp_c = None))]
pub(super) fn kuchera_snow_liquid_ratio(max_temp_c: Option<&Bound<'_, PyAny>>) -> Option<f64> {
    kuchera_ratio_from_c(extract_usable_scalar(max_temp_c)?)
}

pub(crate) fn compute_column_max_temp_c(pres: &[f64], tmpc: &[f64], ptop: f64) -> Option<f64> {
    if pres.len() != tmpc.len() || !ptop.is_finite() {
        return None;
    }
    let mut warmest: Option<f64> = None;
    for (&p, &t) in pres.iter().zip(tmpc.iter()) {
        if !p.is_finite() || p == VENDORED_MISSING || p < ptop {
            continue;
        }
        if !t.is_finite() || t == VENDORED_MISSING {
            continue;
        }
        warmest = Some(match warmest {
            Some(prev) => prev.max(t),
            None => t,
        });
    }
    warmest
}

#[pyfunction]
pub(super) fn column_max_temperature_c(
    pres: PyReadonlyArray1<'_, f64>,
    tmpc: PyReadonlyArray1<'_, f64>,
    ptop: f64,
) -> PyResult<Option<f64>> {
    Ok(compute_column_max_temp_c(
        pres.as_slice()?,
        tmpc.as_slice()?,
        ptop,
    ))
}

#[pyfunction]
#[pyo3(signature = (mixing_height_m = None, transport_wind_kt = None))]
pub(super) fn fire_ventilation_rate(
    mixing_height_m: Option<&Bound<'_, PyAny>>,
    transport_wind_kt: Option<&Bound<'_, PyAny>>,
) -> Option<f64> {
    let height = extract_usable_scalar(mixing_height_m)?;
    let speed = extract_usable_scalar(transport_wind_kt)?;
    if height < 0.0 || speed < 0.0 {
        return None;
    }
    let val = height * speed * KNOTS_TO_MS;
    val.is_finite().then_some(val)
}

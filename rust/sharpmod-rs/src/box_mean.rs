//! Area-composite box-mean sounding thermodynamics, wind vectors, and stacking.

use numpy::ndarray::Array2;
use numpy::{IntoPyArray, PyArray1, PyArray2, PyReadonlyArray1};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

const MISSING: f64 = -9999.0;
const C1: f64 = 0.0498646455;
const C2: f64 = 2.4082965;
const C3: f64 = 7.07475;
const C4: f64 = 38.9114;
const C5: f64 = 0.0915;
const C6: f64 = 1.2035;
const ZEROCNK: f64 = 273.15;

struct MemberInput {
    pres: Vec<f64>,
    hght: Option<Vec<f64>>,
    tmpc: Option<Vec<f64>>,
    dwpc: Option<Vec<f64>>,
    wdir: Option<Vec<f64>>,
    wspd: Option<Vec<f64>>,
    omeg: Option<Vec<f64>>,
    uwnd: Option<Vec<f64>>,
    vwnd: Option<Vec<f64>>,
    lat: Option<f64>,
    lon: Option<f64>,
}

fn vappres(t: f64) -> f64 {
    let mut pol = t * (1.1112018e-17 + t * -3.0994571e-20);
    pol = t * (2.1874425e-13 + t * (-1.789232e-15 + pol));
    pol = t * (4.3884180e-09 + t * (-2.988388e-11 + pol));
    pol = t * (7.8736169e-05 + t * (-6.111796e-07 + pol));
    pol = 0.99999683 + t * (-9.082695e-03 + pol);
    6.1078 / pol.powi(8)
}

fn mixratio(p: f64, td: f64) -> f64 {
    if !p.is_finite() || p <= 0.0 || !td.is_finite() {
        return f64::NAN;
    }
    let x = 0.02 * (td - 12.5 + 7500.0 / p);
    let wfw = 1.0 + 0.0000045 * p + 0.0014 * x * x;
    let fwesw = wfw * vappres(td);
    let ratio = 621.97 * (fwesw / (p - fwesw));
    if ratio.is_finite() && ratio > 0.0 {
        ratio
    } else {
        f64::NAN
    }
}

fn temp_at_mixrat(w: f64, p: f64) -> f64 {
    if !p.is_finite() || p <= 0.0 || !w.is_finite() || w <= 0.0 {
        return f64::NAN;
    }
    let x = (w * p / (622.0 + w)).log10();
    let t = (10.0_f64.powf(C1 * x + C2) - C3 + C4 * (10.0_f64.powf(C5 * x) - C6).powi(2)) - ZEROCNK;
    if t.is_finite() {
        t
    } else {
        f64::NAN
    }
}

fn interpolate_log(target_log: &[f64], source_log: &[f64], values: &[f64]) -> Vec<f64> {
    let mut pairs: Vec<(f64, f64)> = source_log
        .iter()
        .zip(values.iter())
        .filter(|(s, v)| s.is_finite() && v.is_finite())
        .map(|(&s, &v)| (s, v))
        .collect();
    if pairs.len() < 2 {
        return vec![f64::NAN; target_log.len()];
    }
    pairs.sort_by(|a, b| a.0.total_cmp(&b.0));
    target_log
        .iter()
        .map(|&t| {
            if t < pairs[0].0 || t > pairs[pairs.len() - 1].0 {
                return f64::NAN;
            }
            let idx = pairs.partition_point(|(s, _)| *s <= t);
            if idx == 0 {
                pairs[0].1
            } else if idx >= pairs.len() {
                pairs[pairs.len() - 1].1
            } else {
                let (x0, y0) = pairs[idx - 1];
                let (x1, y1) = pairs[idx];
                if x1 == x0 {
                    y0
                } else {
                    y0 + (t - x0) * (y1 - y0) / (x1 - x0)
                }
            }
        })
        .collect()
}

fn nanmean_stack(stack: &[Vec<f64>], len: usize) -> (Vec<f64>, Vec<i64>) {
    let mut means = vec![f64::NAN; len];
    let mut counts = vec![0_i64; len];
    for i in 0..len {
        let mut sum = 0.0;
        let mut count = 0_i64;
        for row in stack {
            let v = row[i];
            if v.is_finite() {
                sum += v;
                count += 1;
            }
        }
        if count > 0 {
            means[i] = sum / (count as f64);
            counts[i] = count;
        }
    }
    (means, counts)
}

fn or_missing(v: f64) -> f64 {
    if v.is_finite() {
        v
    } else {
        MISSING
    }
}

fn read_opt_array<'py>(obj: Option<Bound<'py, PyAny>>) -> PyResult<Option<Vec<f64>>> {
    let Some(bound) = obj else {
        return Ok(None);
    };
    if bound.is_none() {
        return Ok(None);
    }
    let arr: PyReadonlyArray1<'py, f64> = bound.extract()?;
    Ok(Some(arr.as_slice()?.to_vec()))
}

type PyBoxMeanResult<'py> = (
    Bound<'py, PyArray2<f64>>,
    Bound<'py, PyArray1<i64>>,
    usize,
    usize,
    usize,
    f64,
    f64,
);

#[pyfunction(signature = (members, lat=None, lon=None))]
fn compute_box_mean<'py>(
    py: Python<'py>,
    members: Vec<Bound<'py, PyAny>>,
    lat: Option<f64>,
    lon: Option<f64>,
) -> PyResult<PyBoxMeanResult<'py>> {
    let mut parsed = Vec::with_capacity(members.len());
    for item in members {
        let pres_arr: PyReadonlyArray1<'py, f64> = item.get_item("pres")?.extract()?;
        let pres = pres_arr.as_slice()?.to_vec();
        let hght = read_opt_array(item.get_item("hght").ok())?;
        let tmpc = read_opt_array(item.get_item("tmpc").ok())?;
        let dwpc = read_opt_array(item.get_item("dwpc").ok())?;
        let wdir = read_opt_array(item.get_item("wdir").ok())?;
        let wspd = read_opt_array(item.get_item("wspd").ok())?;
        let omeg = read_opt_array(item.get_item("omeg").ok())?;
        let uwnd = read_opt_array(item.get_item("uwnd").ok())?;
        let vwnd = read_opt_array(item.get_item("vwnd").ok())?;
        let m_lat: Option<f64> =
            item.get_item("lat")
                .ok()
                .and_then(|v| if v.is_none() { None } else { v.extract().ok() });
        let m_lon: Option<f64> =
            item.get_item("lon")
                .ok()
                .and_then(|v| if v.is_none() { None } else { v.extract().ok() });
        parsed.push(MemberInput {
            pres,
            hght,
            tmpc,
            dwpc,
            wdir,
            wspd,
            omeg,
            uwnd,
            vwnd,
            lat: m_lat,
            lon: m_lon,
        });
    }

    let computed = py
        .detach(move || run_box_mean(parsed, lat, lon))
        .map_err(PyValueError::new_err)?;
    let level_count = computed.pres.len();
    let mut flat = Vec::with_capacity(9 * level_count);
    flat.extend(computed.pres);
    flat.extend(computed.hght);
    flat.extend(computed.tmpc);
    flat.extend(computed.dwpc);
    flat.extend(computed.wdir);
    flat.extend(computed.wspd);
    flat.extend(computed.omeg);
    flat.extend(computed.uwnd);
    flat.extend(computed.vwnd);
    let matrix = Array2::from_shape_vec((9, level_count), flat)
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
    Ok((
        matrix.into_pyarray(py),
        computed.counts.into_pyarray(py),
        computed.trimmed_below,
        computed.trimmed_above,
        computed.clamped_dewpoints,
        computed.lat,
        computed.lon,
    ))
}

struct ComputedMean {
    pres: Vec<f64>,
    hght: Vec<f64>,
    tmpc: Vec<f64>,
    dwpc: Vec<f64>,
    wdir: Vec<f64>,
    wspd: Vec<f64>,
    omeg: Vec<f64>,
    uwnd: Vec<f64>,
    vwnd: Vec<f64>,
    counts: Vec<i64>,
    trimmed_below: usize,
    trimmed_above: usize,
    clamped_dewpoints: usize,
    lat: f64,
    lon: f64,
}

fn run_box_mean(
    members: Vec<MemberInput>,
    lat: Option<f64>,
    lon: Option<f64>,
) -> Result<ComputedMean, String> {
    if members.is_empty() {
        return Err("no members".into());
    }
    let scale = lat.map(|v| v.to_radians().cos()).unwrap_or(1.0);
    let ref_idx = members
        .iter()
        .enumerate()
        .min_by(|(_, a), (_, b)| {
            let len_cmp = b.pres.len().cmp(&a.pres.len());
            if len_cmp != std::cmp::Ordering::Equal {
                return len_cmp;
            }
            let dist = |m: &MemberInput| -> f64 {
                match (lat, lon, m.lat, m.lon) {
                    (Some(la), Some(lo), Some(mla), Some(mlo)) => {
                        (mla - la).powi(2) + ((mlo - lo) * scale).powi(2)
                    }
                    _ => 0.0,
                }
            };
            dist(a).total_cmp(&dist(b))
        })
        .map(|(i, _)| i)
        .expect("nonempty members");

    let full_ladder = &members[ref_idx].pres;
    let floor = members
        .iter()
        .map(|m| m.pres[0])
        .fold(f64::INFINITY, f64::min);
    let ceiling = members
        .iter()
        .map(|m| *m.pres.last().expect("at least 2 levels"))
        .fold(f64::NEG_INFINITY, f64::max);

    let mut ladder = Vec::new();
    let mut trimmed_below = 0;
    let mut trimmed_above = 0;
    for &p in full_ladder {
        if p > floor + 1e-6 {
            trimmed_below += 1;
        } else if p < ceiling - 1e-6 {
            trimmed_above += 1;
        } else {
            ladder.push(p);
        }
    }
    if ladder.len() < 2 {
        return Err(
            "these soundings share fewer than two pressure levels; the box spans too much terrain to average as one column".into(),
        );
    }

    let target_log: Vec<f64> = ladder.iter().map(|p| p.log10()).collect();
    let mut stack_hght = Vec::with_capacity(members.len());
    let mut stack_tmpc = Vec::with_capacity(members.len());
    let mut stack_mixr = Vec::with_capacity(members.len());
    let mut stack_omeg = Vec::with_capacity(members.len());
    let mut stack_u = Vec::with_capacity(members.len());
    let mut stack_v = Vec::with_capacity(members.len());

    for m in &members {
        let source_log: Vec<f64> = m.pres.iter().map(|p| p.log10()).collect();
        let n = m.pres.len();
        let push_col = |opt: &Option<Vec<f64>>| match opt {
            Some(col) if col.len() == n => interpolate_log(&target_log, &source_log, col),
            _ => vec![f64::NAN; ladder.len()],
        };
        stack_hght.push(push_col(&m.hght));
        stack_tmpc.push(push_col(&m.tmpc));
        stack_omeg.push(push_col(&m.omeg));

        let mixr_col: Vec<f64> = match &m.dwpc {
            Some(dwpc) if dwpc.len() == n => m
                .pres
                .iter()
                .zip(dwpc.iter())
                .map(|(&p, &td)| mixratio(p, td))
                .collect(),
            _ => vec![f64::NAN; n],
        };
        stack_mixr.push(interpolate_log(&target_log, &source_log, &mixr_col));

        let (u_col, v_col) = match (&m.uwnd, &m.vwnd) {
            (Some(u), Some(v)) if u.len() == n && v.len() == n => (u.clone(), v.clone()),
            _ => match (&m.wspd, &m.wdir) {
                (Some(spd), Some(dir)) if spd.len() == n && dir.len() == n => {
                    let mut u = Vec::with_capacity(n);
                    let mut v = Vec::with_capacity(n);
                    for (&s, &d) in spd.iter().zip(dir.iter()) {
                        let rad = d.to_radians();
                        u.push(-s * rad.sin());
                        v.push(-s * rad.cos());
                    }
                    (u, v)
                }
                _ => (vec![f64::NAN; n], vec![f64::NAN; n]),
            },
        };
        stack_u.push(interpolate_log(&target_log, &source_log, &u_col));
        stack_v.push(interpolate_log(&target_log, &source_log, &v_col));
    }

    let mlen = ladder.len();
    let (h_mean, h_cnt) = nanmean_stack(&stack_hght, mlen);
    let (t_mean, t_cnt) = nanmean_stack(&stack_tmpc, mlen);
    let (w_mean, _) = nanmean_stack(&stack_mixr, mlen);
    let (o_mean, _) = nanmean_stack(&stack_omeg, mlen);
    let (u_mean, _) = nanmean_stack(&stack_u, mlen);
    let (v_mean, _) = nanmean_stack(&stack_v, mlen);

    let mut keep_p = Vec::new();
    let mut keep_h = Vec::new();
    let mut keep_t = Vec::new();
    let mut keep_w = Vec::new();
    let mut keep_o = Vec::new();
    let mut keep_u = Vec::new();
    let mut keep_v = Vec::new();
    let mut keep_cnt = Vec::new();
    for i in 0..mlen {
        if h_mean[i].is_finite() && t_mean[i].is_finite() && h_cnt[i] > 0 && t_cnt[i] > 0 {
            keep_p.push(ladder[i]);
            keep_h.push(h_mean[i]);
            keep_t.push(t_mean[i]);
            keep_w.push(w_mean[i]);
            keep_o.push(o_mean[i]);
            keep_u.push(u_mean[i]);
            keep_v.push(v_mean[i]);
            keep_cnt.push(t_cnt[i].max(h_cnt[i]));
        }
    }
    if keep_p.len() < 2 {
        return Err("the averaged column has fewer than two usable levels".into());
    }

    let mut clamped_dewpoints = 0;
    let mut keep_td = Vec::with_capacity(keep_p.len());
    for i in 0..keep_p.len() {
        let mut td = temp_at_mixrat(keep_w[i], keep_p[i]);
        if td.is_finite() && keep_t[i].is_finite() && td > keep_t[i] {
            td = keep_t[i];
            clamped_dewpoints += 1;
        }
        keep_td.push(td);
    }

    let mut final_p = Vec::new();
    let mut final_h = Vec::new();
    let mut final_t = Vec::new();
    let mut final_td = Vec::new();
    let mut final_o = Vec::new();
    let mut final_u = Vec::new();
    let mut final_v = Vec::new();
    let mut final_cnt = Vec::new();
    let mut last_p: Option<f64> = None;
    let mut last_h: Option<f64> = None;
    for i in 0..keep_p.len() {
        let p = keep_p[i];
        let h = keep_h[i];
        if let (Some(lp), Some(lh)) = (last_p, last_h) {
            if !(p < lp && h > lh) {
                continue;
            }
        }
        last_p = Some(p);
        last_h = Some(h);
        final_p.push(p);
        final_h.push(h);
        final_t.push(or_missing(keep_t[i]));
        final_td.push(or_missing(keep_td[i]));
        final_o.push(or_missing(keep_o[i]));
        final_u.push(keep_u[i]);
        final_v.push(keep_v[i]);
        final_cnt.push(keep_cnt[i]);
    }
    if final_p.len() < 2 {
        return Err("the averaged column is not vertically ordered after averaging".into());
    }

    let mut final_wdir = Vec::with_capacity(final_u.len());
    let mut final_wspd = Vec::with_capacity(final_u.len());
    for i in 0..final_u.len() {
        let u = final_u[i];
        let v = final_v[i];
        if u.is_finite() && v.is_finite() {
            let speed = u.hypot(v);
            let dir = if speed < 1e-6 {
                0.0
            } else {
                ((-u).atan2(-v).to_degrees() + 360.0).rem_euclid(360.0)
            };
            final_wspd.push(speed);
            final_wdir.push(dir);
        } else {
            final_wspd.push(MISSING);
            final_wdir.push(MISSING);
        }
        final_u[i] = or_missing(u);
        final_v[i] = or_missing(v);
    }

    let (centre_lat, centre_lon) = match (lat, lon) {
        (Some(la), Some(lo)) => (la, lo),
        _ => {
            let lats: Vec<f64> = members.iter().filter_map(|m| m.lat).collect();
            let lons: Vec<f64> = members.iter().filter_map(|m| m.lon).collect();
            if lats.is_empty() || lons.is_empty() {
                return Err(
                    "these soundings carry no coordinates, so the centre of the averaged area is unknown; pass lat and lon".into(),
                );
            }
            let mean_lat = lats.iter().sum::<f64>() / (lats.len() as f64);
            let sin_sum: f64 = lons.iter().map(|l| l.to_radians().sin()).sum();
            let cos_sum: f64 = lons.iter().map(|l| l.to_radians().cos()).sum();
            let mean_lon = (sin_sum.atan2(cos_sum).to_degrees() + 180.0).rem_euclid(360.0) - 180.0;
            (mean_lat, mean_lon)
        }
    };

    Ok(ComputedMean {
        pres: final_p,
        hght: final_h,
        tmpc: final_t,
        dwpc: final_td,
        wdir: final_wdir,
        wspd: final_wspd,
        omeg: final_o,
        uwnd: final_u,
        vwnd: final_v,
        counts: final_cnt,
        trimmed_below,
        trimmed_above,
        clamped_dewpoints,
        lat: centre_lat,
        lon: centre_lon,
    })
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(compute_box_mean, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mixing_ratio_roundtrip_and_clamping() {
        let w = mixratio(1000.0, 20.0);
        assert!((w - 14.955321833573537).abs() < 1e-6);
        let td = temp_at_mixrat(w, 1000.0);
        assert!((td - 20.11753191083676).abs() < 1e-6);

        let m1 = MemberInput {
            pres: vec![1000.0, 500.0],
            hght: Some(vec![100.0, 5500.0]),
            tmpc: Some(vec![20.0, -15.0]),
            dwpc: Some(vec![19.0, -20.0]),
            wdir: Some(vec![350.0, 270.0]),
            wspd: Some(vec![20.0, 40.0]),
            omeg: None,
            uwnd: None,
            vwnd: None,
            lat: Some(35.0),
            lon: Some(-97.0),
        };
        let m2 = MemberInput {
            pres: vec![1000.0, 500.0],
            hght: Some(vec![100.0, 5500.0]),
            tmpc: Some(vec![20.0, -15.0]),
            dwpc: Some(vec![19.0, -20.0]),
            wdir: Some(vec![10.0, 270.0]),
            wspd: Some(vec![20.0, 40.0]),
            omeg: None,
            uwnd: None,
            vwnd: None,
            lat: Some(36.0),
            lon: Some(-97.0),
        };
        let out = run_box_mean(vec![m1, m2], None, None).unwrap();
        assert_eq!(out.pres, vec![1000.0, 500.0]);
        assert!(out.wdir[0] < 0.5 || out.wdir[0] > 359.5);
        assert!((out.lat - 35.5).abs() < 1e-9);
    }
}

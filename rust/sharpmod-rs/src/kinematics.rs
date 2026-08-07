//! Coarse-grained surface-layer profile kinematics.
//!
//! One call computes every requested surface-to-height layer plus the shared
//! Bunkers storm motion. Missing values are represented as NaN in the returned
//! matrix so the Python adapter can restore SharpTab's masked-value contract.

use crate::interpolation::interpolate_1d;

pub const LAYER_WIDTH: usize = 15;
const KTS_PER_MS: f64 = 1.943_844_492_440_604_6;
const MAX_PRESSURE_SAMPLES: usize = 5_000;
/// Pressures within this distance of the layer top count as the top itself, so
/// floating-point drift cannot append a duplicate level.
const SAMPLE_SNAP_HPA: f64 = 1.0e-9;
const NAN4: [f64; 4] = [f64::NAN; 4];

#[derive(Clone, Copy)]
struct BasicLayer {
    top_agl: f64,
    top_pressure: f64,
    pressure_shear_u: f64,
    pressure_shear_v: f64,
    height_shear_u: f64,
    height_shear_v: f64,
    mean_u: f64,
    mean_v: f64,
    mean_npw_u: f64,
    mean_npw_v: f64,
}

pub struct ProfileKinematics {
    pub storm_motion: [f64; 4],
    pub layers: Vec<[f64; LAYER_WIDTH]>,
}

fn is_missing(value: f64, missing: Option<f64>) -> bool {
    !value.is_finite() || missing.is_some_and(|sentinel| value == sentinel)
}

fn interp_scalar(
    target: f64,
    coordinate: &[f64],
    values: &[f64],
    missing: Option<f64>,
    log_output: bool,
) -> Result<f64, String> {
    interpolate_1d(&[target], coordinate, values, missing, log_output).map(|output| output[0])
}

/// Layer sample pressures for the 1 hPa layer means, ending exactly at `ptop`.
///
/// A fixed 1 hPa increment cannot land on `ptop` for a layer whose depth is not
/// a whole number of hectopascals, so stepping to `ptop - 1.0` takes one sample
/// *past* the layer top. When that overshoot leaves the reported profile it
/// interpolates to missing and is dropped from the mean, which makes the layer
/// mean wind depend on where the increment happens to fall rather than on the
/// requested layer. Sampling the exact top keeps the integration bounded by
/// `[pbot, ptop]` and matches `sharpmod.sharptab.winds._pressure_samples`.
fn pressure_samples(pbot: f64, ptop: f64) -> Result<Vec<f64>, String> {
    if !pbot.is_finite() || !ptop.is_finite() || pbot < ptop {
        return Ok(Vec::new());
    }
    let span = pbot - ptop;
    if span > MAX_PRESSURE_SAMPLES as f64 {
        return Err("profile kinematics pressure span exceeds the safety limit".to_string());
    }
    let mut samples = Vec::with_capacity(span.ceil() as usize + 1);
    let stop = ptop + SAMPLE_SNAP_HPA;
    let mut pressure = pbot;
    while pressure > stop {
        samples.push(pressure);
        pressure -= 1.0;
    }
    samples.push(ptop);
    Ok(samples)
}

fn mean(values: &[f64], weights: Option<&[f64]>, missing: Option<f64>) -> f64 {
    let mut numerator = 0.0;
    let mut denominator = 0.0;
    let mut count = 0usize;
    for (index, value) in values.iter().copied().enumerate() {
        if is_missing(value, missing) {
            continue;
        }
        if let Some(weights) = weights {
            let weight = weights[index];
            if !weight.is_finite() {
                continue;
            }
            numerator += value * weight;
            denominator += weight;
        } else {
            numerator += value;
            count += 1;
        }
    }
    if weights.is_some() {
        if denominator == 0.0 || !denominator.is_finite() {
            f64::NAN
        } else {
            numerator / denominator
        }
    } else if count == 0 {
        f64::NAN
    } else {
        numerator / count as f64
    }
}

fn difference(top: f64, bottom: f64) -> f64 {
    if top.is_finite() && bottom.is_finite() {
        top - bottom
    } else {
        f64::NAN
    }
}

#[allow(clippy::too_many_arguments)]
fn layer_basics(
    pres: &[f64],
    hght: &[f64],
    logp: &[f64],
    u: &[f64],
    v: &[f64],
    sfc: usize,
    top_agl: f64,
    missing: Option<f64>,
) -> Result<BasicLayer, String> {
    let surface_hght = hght[sfc];
    let surface_pres = pres[sfc];
    let target_hght = surface_hght + top_agl;
    let top_pressure = interp_scalar(target_hght, hght, logp, missing, true)?;

    let surface_logp = if surface_pres > 0.0 {
        surface_pres.log10()
    } else {
        f64::NAN
    };
    let surface_u_pressure = interp_scalar(surface_logp, logp, u, missing, false)?;
    let surface_v_pressure = interp_scalar(surface_logp, logp, v, missing, false)?;
    let surface_u_height = interp_scalar(surface_hght, hght, u, missing, false)?;
    let surface_v_height = interp_scalar(surface_hght, hght, v, missing, false)?;

    let top_logp = if top_pressure > 0.0 {
        top_pressure.log10()
    } else {
        f64::NAN
    };
    let top_u_pressure = interp_scalar(top_logp, logp, u, missing, false)?;
    let top_v_pressure = interp_scalar(top_logp, logp, v, missing, false)?;
    let top_u_height = interp_scalar(target_hght, hght, u, missing, false)?;
    let top_v_height = interp_scalar(target_hght, hght, v, missing, false)?;

    let samples = pressure_samples(surface_pres, top_pressure)?;
    let sample_logp: Vec<f64> = samples.iter().map(|value| value.log10()).collect();
    let sample_u = interpolate_1d(&sample_logp, logp, u, missing, false)?;
    let sample_v = interpolate_1d(&sample_logp, logp, v, missing, false)?;

    Ok(BasicLayer {
        top_agl,
        top_pressure,
        pressure_shear_u: difference(top_u_pressure, surface_u_pressure),
        pressure_shear_v: difference(top_v_pressure, surface_v_pressure),
        height_shear_u: difference(top_u_height, surface_u_height),
        height_shear_v: difference(top_v_height, surface_v_height),
        mean_u: mean(&sample_u, Some(&samples), missing),
        mean_v: mean(&sample_v, Some(&samples), missing),
        mean_npw_u: mean(&sample_u, None, missing),
        mean_npw_v: mean(&sample_v, None, missing),
    })
}

fn bunkers_motion(layer: BasicLayer) -> [f64; 4] {
    let values = [
        layer.mean_npw_u,
        layer.mean_npw_v,
        layer.pressure_shear_u,
        layer.pressure_shear_v,
    ];
    if values.iter().any(|value| !value.is_finite()) {
        return NAN4;
    }
    let [mean_u, mean_v, shear_u, shear_v] = values;
    let magnitude = shear_u.hypot(shear_v);
    if magnitude == 0.0 || !magnitude.is_finite() {
        return NAN4;
    }
    let deviation = (7.5 * KTS_PER_MS) / magnitude;
    [
        mean_u + deviation * shear_v,
        mean_v - deviation * shear_u,
        mean_u - deviation * shear_v,
        mean_v + deviation * shear_u,
    ]
}

#[allow(clippy::too_many_arguments)]
fn helicity(
    pres: &[f64],
    hght: &[f64],
    logp: &[f64],
    u: &[f64],
    v: &[f64],
    sfc: usize,
    top_agl: f64,
    stu: f64,
    stv: f64,
    missing: Option<f64>,
) -> Result<[f64; 3], String> {
    if !top_agl.is_finite() || !stu.is_finite() || !stv.is_finite() {
        return Ok([f64::NAN; 3]);
    }
    if top_agl == 0.0 {
        return Ok([0.0; 3]);
    }

    let lower_msl = hght[sfc];
    let upper_msl = lower_msl + top_agl;
    let plower = interp_scalar(lower_msl, hght, logp, missing, true)?;
    let pupper = interp_scalar(upper_msl, hght, logp, missing, true)?;
    if !plower.is_finite() || !pupper.is_finite() {
        return Ok([f64::NAN; 3]);
    }

    let ind1 = pres
        .iter()
        .enumerate()
        .filter(|(_, value)| !is_missing(**value, missing) && plower >= **value)
        .map(|(index, _)| index)
        .min();
    let ind2 = pres
        .iter()
        .enumerate()
        .filter(|(_, value)| !is_missing(**value, missing) && pupper <= **value)
        .map(|(index, _)| index)
        .max();
    let (Some(ind1), Some(ind2)) = (ind1, ind2) else {
        return Ok([f64::NAN; 3]);
    };
    if ind2 < ind1 {
        return Ok([f64::NAN; 3]);
    }

    let lower_logp = plower.log10();
    let upper_logp = pupper.log10();
    let u1 = interp_scalar(lower_logp, logp, u, missing, false)?;
    let v1 = interp_scalar(lower_logp, logp, v, missing, false)?;
    let u2 = interp_scalar(upper_logp, logp, u, missing, false)?;
    let v2 = interp_scalar(upper_logp, logp, v, missing, false)?;
    if [u1, v1, u2, v2].iter().any(|value| !value.is_finite()) {
        return Ok([f64::NAN; 3]);
    }

    let mut layer_u = Vec::with_capacity(ind2 - ind1 + 3);
    let mut layer_v = Vec::with_capacity(ind2 - ind1 + 3);
    layer_u.push(u1);
    layer_v.push(v1);
    for index in ind1..=ind2 {
        if !is_missing(u[index], missing) && !is_missing(v[index], missing) {
            layer_u.push(u[index]);
            layer_v.push(v[index]);
        }
    }
    layer_u.push(u2);
    layer_v.push(v2);

    let mut positive = 0.0;
    let mut negative = 0.0;
    for index in 0..layer_u.len() - 1 {
        let first_u = (layer_u[index] - stu) / KTS_PER_MS;
        let first_v = (layer_v[index] - stv) / KTS_PER_MS;
        let second_u = (layer_u[index + 1] - stu) / KTS_PER_MS;
        let second_v = (layer_v[index + 1] - stv) / KTS_PER_MS;
        let contribution = second_u * first_v - first_u * second_v;
        if contribution > 0.0 {
            positive += contribution;
        } else if contribution < 0.0 {
            negative += contribution;
        }
    }
    Ok([positive + negative, positive, negative])
}

/// Compute all requested surface layers and one shared Bunkers motion vector.
pub fn profile_kinematics(
    pres: &[f64],
    hght: &[f64],
    u: &[f64],
    v: &[f64],
    layer_tops_agl: &[f64],
    sfc: usize,
    missing: Option<f64>,
) -> Result<ProfileKinematics, String> {
    if !(pres.len() == hght.len() && pres.len() == u.len() && pres.len() == v.len()) {
        return Err(format!(
            "profile kinematics column lengths differ: pres={}, hght={}, u={}, v={}",
            pres.len(),
            hght.len(),
            u.len(),
            v.len()
        ));
    }
    if layer_tops_agl
        .iter()
        .any(|top| !top.is_finite() || *top < 0.0)
    {
        return Err("layer_tops_agl must contain finite, non-negative heights".to_string());
    }
    if pres.len() < 2 {
        if sfc != 0 {
            return Err("sfc is outside the profile".to_string());
        }
        return Ok(ProfileKinematics {
            storm_motion: NAN4,
            layers: layer_tops_agl
                .iter()
                .map(|top| {
                    let mut row = [f64::NAN; LAYER_WIDTH];
                    row[0] = *top;
                    row
                })
                .collect(),
        });
    }
    if sfc >= pres.len() {
        return Err("sfc is outside the profile".to_string());
    }

    let logp: Vec<f64> = pres
        .iter()
        .map(|pressure| {
            if !is_missing(*pressure, missing) && *pressure > 0.0 {
                pressure.log10()
            } else {
                f64::NAN
            }
        })
        .collect();
    let six_km = layer_basics(pres, hght, &logp, u, v, sfc, 6_000.0, missing)?;
    let storm_motion = bunkers_motion(six_km);
    let [rstu, rstv, _, _] = storm_motion;

    let mut layers = Vec::with_capacity(layer_tops_agl.len());
    for top in layer_tops_agl {
        let basics = if *top == 6_000.0 {
            six_km
        } else {
            layer_basics(pres, hght, &logp, u, v, sfc, *top, missing)?
        };
        let [srh_total, srh_positive, srh_negative] =
            helicity(pres, hght, &logp, u, v, sfc, *top, rstu, rstv, missing)?;
        let storm_relative_mean_u = if basics.mean_u.is_finite() && rstu.is_finite() {
            basics.mean_u - rstu
        } else {
            f64::NAN
        };
        let storm_relative_mean_v = if basics.mean_v.is_finite() && rstv.is_finite() {
            basics.mean_v - rstv
        } else {
            f64::NAN
        };
        layers.push([
            basics.top_agl,
            basics.top_pressure,
            basics.pressure_shear_u,
            basics.pressure_shear_v,
            basics.height_shear_u,
            basics.height_shear_v,
            basics.mean_u,
            basics.mean_v,
            basics.mean_npw_u,
            basics.mean_npw_v,
            srh_total,
            srh_positive,
            srh_negative,
            storm_relative_mean_u,
            storm_relative_mean_v,
        ]);
    }
    Ok(ProfileKinematics {
        storm_motion,
        layers,
    })
}

#[cfg(test)]
mod tests {
    use super::{pressure_samples, profile_kinematics};

    #[test]
    fn layer_samples_end_exactly_at_the_layer_top() {
        // Fractional layer depth: the last whole step must not overshoot the
        // top, and the exact top must be the final sample.
        let samples = pressure_samples(1_004.0, 479.017_644_789_354_56).unwrap();
        assert_eq!(*samples.last().unwrap(), 479.017_644_789_354_56);
        assert_eq!(samples.len(), 526);
        assert!(samples.iter().all(|value| *value >= 479.017_644_789_354_56));

        // Whole-hectopascal depth keeps the historical sample set.
        let whole = pressure_samples(1_000.0, 500.0).unwrap();
        assert_eq!(whole.len(), 501);
        assert_eq!(*whole.first().unwrap(), 1_000.0);
        assert_eq!(*whole.last().unwrap(), 500.0);

        // Degenerate and inverted layers keep their previous behaviour.
        assert_eq!(pressure_samples(700.0, 700.0).unwrap(), vec![700.0]);
        assert!(pressure_samples(500.0, 700.0).unwrap().is_empty());
        assert!(pressure_samples(f64::NAN, 700.0).unwrap().is_empty());
    }

    #[test]
    fn linear_profile_produces_all_requested_layers() {
        let hght = [0.0, 500.0, 1_000.0, 3_000.0, 6_000.0, 9_000.0];
        let pres: Vec<f64> = hght
            .iter()
            .map(|height| 1_000.0_f64 * (-*height / 8_000.0_f64).exp())
            .collect();
        let u = [0.0, 5.0, 10.0, 30.0, 60.0, 90.0];
        let v = [5.0, 7.0, 9.0, 15.0, 25.0, 35.0];
        let result = profile_kinematics(
            &pres,
            &hght,
            &u,
            &v,
            &[500.0, 1_000.0, 3_000.0, 6_000.0],
            0,
            None,
        )
        .unwrap();

        assert_eq!(result.layers.len(), 4);
        assert!(result.storm_motion.iter().all(|value| value.is_finite()));
        assert!((result.layers[0][4] - 5.0).abs() < 1.0e-12);
        assert!((result.layers[0][5] - 2.0).abs() < 1.0e-12);
        assert!(result.layers[0][10].is_finite());
    }

    #[test]
    fn shallow_profile_keeps_layer_shape_and_marks_motion_missing() {
        let result = profile_kinematics(
            &[1_000.0, 900.0],
            &[0.0, 1_000.0],
            &[0.0, 10.0],
            &[5.0, 10.0],
            &[500.0],
            0,
            None,
        )
        .unwrap();

        assert_eq!(result.layers.len(), 1);
        assert!(result.storm_motion.iter().all(|value| value.is_nan()));
        assert!(result.layers[0][4].is_finite());
        assert!(result.layers[0][10].is_nan());
    }

    #[test]
    fn invalid_inputs_are_rejected() {
        assert!(profile_kinematics(
            &[1_000.0, 900.0],
            &[0.0],
            &[0.0, 10.0],
            &[0.0, 10.0],
            &[500.0],
            0,
            None,
        )
        .is_err());
        assert!(profile_kinematics(
            &[1_000.0, 900.0],
            &[0.0, 1_000.0],
            &[0.0, 10.0],
            &[0.0, 10.0],
            &[-1.0],
            0,
            None,
        )
        .is_err());
    }
}

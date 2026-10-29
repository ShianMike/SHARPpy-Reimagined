//! Storm Slinky integration using SHARPpy's 25-second step and initial nudge.

use crate::interpolation::PreparedInterpolation;

pub struct TrajectoryState {
    pub cape: f64,
    pub lfc_pressure: f64,
    pub lfc_height: Option<f64>,
    pub el_height: Option<f64>,
    pub storm_motion: [Option<f64>; 2],
}

pub struct ParcelTrajectory {
    pub positions: Vec<[Option<f64>; 3]>,
    pub tilt: Option<f64>,
}

pub fn parcel_trajectory(
    [pressure, height, environment, u, v]: [&[f64]; 5],
    [trace_pressure, trace_temperature]: [&[f64]; 2],
    state: TrajectoryState,
    sfc: usize,
    missing: Option<f64>,
) -> Result<Option<ParcelTrajectory>, String> {
    if state.cape < 1.0e-3 {
        return Ok(None);
    }
    if [height.len(), environment.len(), u.len(), v.len()]
        .iter()
        .any(|&size| size != pressure.len())
    {
        return Err("trajectory profile column lengths differ".into());
    }
    if sfc >= height.len() {
        return Err("sfc is outside the trajectory profile".into());
    }
    let el = state.el_height.unwrap_or(height[height.len() - 1]);
    let mut position = [Some(0.), Some(0.), state.lfc_height];
    let mut positions = vec![position];
    let mut p = state.lfc_pressure;
    let mut vertical_speed = Some(5.);
    if position[2].is_some_and(|z| z < el) {
        let log_pressure: Vec<_> = pressure.iter().map(|p| p.log10()).collect();
        let reversed_pressure: Vec<_> = log_pressure.iter().copied().rev().collect();
        let [environment, u, v] = [environment, u, v].map(|values| {
            let reversed: Vec<_> = values.iter().copied().rev().collect();
            PreparedInterpolation::new(&reversed_pressure, &reversed, missing)
        });
        let (environment, u, v) = (environment?, u?, v?);
        let height_pressure = PreparedInterpolation::new(height, &log_pressure, missing)?;
        let trace_pressure: Vec<_> = trace_pressure.iter().rev().map(|p| p.log10()).collect();
        let trace_temperature: Vec<_> = trace_temperature.iter().copied().rev().collect();
        let parcel = PreparedInterpolation::new(&trace_pressure, &trace_temperature, missing)?;

        while position[2].is_some_and(|z| z < el) {
            // An unphysical trace must not leave a render worker looping forever.
            if positions.len() >= 4096 {
                return Ok(None);
            }
            let log_p = p.log10();
            let env_k = environment.scalar_sharppy(log_p, false) + 273.15;
            let parcel_k = parcel.scalar_sharppy(log_p, false) + 273.15;
            let acceleration = 9.8 * ((parcel_k - env_k) / env_k);
            let acceleration = acceleration.is_finite().then_some(acceleration);
            position[2] = position[2]
                .zip(acceleration.zip(vertical_speed))
                .map(|(z, (a, w))| 0.5 * a * 25_f64.powi(2) + w * 25. + z);
            vertical_speed = acceleration.zip(vertical_speed).map(|(a, w)| a * 25. + w);
            for (axis, wind) in [&u, &v].into_iter().enumerate() {
                let component = wind.scalar_sharppy(log_p, false);
                let displacement = component
                    .is_finite()
                    .then_some(component)
                    .zip(state.storm_motion[axis])
                    .map(|(wind, storm)| (wind - storm) * 0.514_444 * 25.);
                position[axis] = position[axis].zip(displacement).map(|(x, dx)| x + dx);
            }
            positions.push(position);
            p = height_pressure.scalar_sharppy(position[2].unwrap_or(f64::NAN) + height[sfc], true);
        }
    }
    let tilt = position[0]
        .zip(position[1])
        .zip(position[2])
        .map(|((x, y), z)| z.atan2((x.powi(2) + y.powi(2)).sqrt()).to_degrees());
    Ok(Some(ParcelTrajectory { positions, tilt }))
}

#[cfg(test)]
mod tests;

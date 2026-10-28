//! SHARPpy maximum-parcel-level energy integration using the existing lift routines.

use super::{
    dry_lift, is_missing, virtual_temperature, wet_lift, PressureSeries, GRAVITY,
    MAX_PRESSURE_SPAN, ZERO_C_K,
};

pub fn maximum_parcel_level(
    [pressure, height, temperature, environment]: [&[f64]; 4],
    start: (f64, f64, f64),
    el: f64,
    sfc: usize,
    missing: Option<f64>,
) -> Result<(f64, f64), String> {
    let absent = (f64::NAN, f64::NAN);
    if [height.len(), temperature.len(), environment.len()]
        .iter()
        .any(|&size| size != pressure.len())
    {
        return Err("maximum parcel level column lengths differ".into());
    }
    if sfc >= pressure.len() {
        return Err("sfc is outside the profile".into());
    }
    if pressure.len() < 2
        || [start.0, start.1, start.2, el, pressure[sfc], height[sfc]]
            .iter()
            .any(|&value| is_missing(value, missing))
        || start.0 <= 0.
        || el <= 0.
    {
        return Ok(absent);
    }
    let heights = PressureSeries::new(pressure, height, missing);
    let virtuals = PressureSeries::new(pressure, environment, missing);
    let (lcl_pressure, lcl_temperature) = dry_lift(start.0, start.1, start.2);
    let bottom = pressure[sfc].min(start.0).min(lcl_pressure);
    if !bottom.is_finite()
        || bottom <= 0.
        || bottom - pressure[pressure.len() - 1] > MAX_PRESSURE_SPAN
    {
        return Ok(absent);
    }
    let mut previous_pressure = bottom;
    let mut previous_height = heights.at(bottom);
    let mut previous_environment = virtuals.at(bottom);
    let mut previous_temperature = wet_lift(lcl_pressure, lcl_temperature, bottom);
    let mut previous_energy = 0.;
    let mut cumulative = 0.;
    let mut el_found = false;
    for index in 0..pressure.len() {
        let next_pressure = pressure[index];
        if is_missing(next_pressure, missing)
            || next_pressure > bottom
            || is_missing(temperature[index], missing)
        {
            continue;
        }
        let next_height = height[index];
        let next_environment = environment[index];
        let next_temperature = wet_lift(previous_pressure, previous_temperature, next_pressure);
        let previous_virtual = virtual_temperature(
            previous_pressure,
            previous_temperature,
            previous_temperature,
        );
        let next_virtual = virtual_temperature(next_pressure, next_temperature, next_temperature);
        let lower_deficit =
            (previous_virtual - previous_environment) / (previous_environment + ZERO_C_K);
        let upper_deficit = (next_virtual - next_environment) / (next_environment + ZERO_C_K);
        let energy =
            GRAVITY * (lower_deficit + upper_deficit) / 2. * (next_height - previous_height);
        cumulative += energy;
        if energy >= 0. && previous_energy <= 0. && previous_virtual <= previous_environment {
            cumulative = 0.;
        }
        if energy <= 0. && previous_energy >= 0. && next_pressure <= el {
            el_found = true;
        }
        if cumulative < 0. && el_found {
            let mut running = cumulative - energy;
            let mut refine_pressure = previous_pressure;
            let mut refine_temperature = previous_temperature;
            let mut refine_environment = previous_environment;
            let mut refine_height = previous_height;
            while running > 0. && refine_pressure > pressure[pressure.len() - 1] {
                let target_pressure = refine_pressure - 1.;
                let target_environment = virtuals.at(target_pressure);
                let target_temperature =
                    wet_lift(refine_pressure, refine_temperature, target_pressure);
                let target_height = heights.at(target_pressure);
                let refine_virtual =
                    virtual_temperature(refine_pressure, refine_temperature, refine_temperature);
                let target_virtual =
                    virtual_temperature(target_pressure, target_temperature, target_temperature);
                let refine_deficit =
                    (refine_virtual - refine_environment) / (refine_environment + ZERO_C_K);
                let target_deficit =
                    (target_virtual - target_environment) / (target_environment + ZERO_C_K);
                running += GRAVITY * (refine_deficit + target_deficit) / 2.
                    * (target_height - refine_height);
                refine_pressure = target_pressure;
                refine_temperature = target_temperature;
                refine_environment = target_environment;
                refine_height = target_height;
            }
            let agl = heights.at(refine_pressure) - height[sfc];
            return Ok((refine_pressure, agl));
        }
        previous_pressure = next_pressure;
        previous_height = next_height;
        previous_environment = next_environment;
        previous_temperature = next_temperature;
        previous_energy = energy;
    }
    Ok(absent)
}

#[cfg(test)]
mod tests;

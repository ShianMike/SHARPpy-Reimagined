//! Coarse-grained undiluted parcel and downdraft diagnostics.
//!
//! This is a Rust translation of the thermodynamic and parcel-selection
//! conventions used by SHARPpy 1.4.0a5. One call returns the surface,
//! most-unstable (lowest 300 hPa), and 100-hPa mixed-layer parcels. The
//! extended workspace also provides forecast, effective, and explicit parcel
//! ascents with plotting traces plus DCAPE.

use std::cmp::Ordering;

pub const PARCEL_COUNT: usize = 3;
pub const CONVECTIVE_PARCEL_COUNT: usize = 5;
pub const PARCEL_WIDTH: usize = 14;

const ZERO_C_K: f64 = 273.15;
const ROCP: f64 = 0.285_714_26;
const GRAVITY: f64 = 9.806_65;
const THERMO_TOLERANCE: f64 = 1.0e-10;
const MAX_PRESSURE_SPAN: f64 = 2_000.0;
const MAX_SATLIFT_ITERATIONS: usize = 200;
const EPS: f64 = 0.621_97;

#[derive(Clone, Copy)]
struct ParcelStart {
    pressure: f64,
    temperature: f64,
    dewpoint: f64,
}

#[derive(Clone)]
pub struct ParcelAscent {
    pub diagnostics: [f64; PARCEL_WIDTH],
    pub pressure_trace: Vec<f64>,
    pub temperature_trace: Vec<f64>,
}

pub struct ConvectiveParcelWorkspace {
    pub parcels: [ParcelAscent; CONVECTIVE_PARCEL_COUNT],
    pub effective_bottom_pressure: f64,
    pub effective_top_pressure: f64,
}

pub struct DowndraftDiagnostics {
    pub cape: f64,
    pub source_pressure: f64,
    pub downrush_temperature: f64,
    pub pressure_trace: Vec<f64>,
    pub temperature_trace: Vec<f64>,
}

#[derive(Clone, Copy)]
struct Level {
    pressure: f64,
    height: f64,
    env_virtual_temperature: f64,
}

#[derive(Clone)]
struct PressureSeries {
    pairs: Vec<(f64, f64)>,
}

impl PressureSeries {
    fn new(pressure: &[f64], values: &[f64], missing: Option<f64>) -> Self {
        let mut pairs: Vec<(f64, f64)> = pressure
            .iter()
            .copied()
            .zip(values.iter().copied())
            .filter(|(p, value)| {
                !is_missing(*p, missing) && *p > 0.0 && !is_missing(*value, missing)
            })
            .map(|(p, value)| (p.log10(), value))
            .collect();
        pairs.sort_by(|left, right| left.0.partial_cmp(&right.0).unwrap_or(Ordering::Equal));
        Self { pairs }
    }

    fn at(&self, pressure: f64) -> f64 {
        if !pressure.is_finite() || pressure <= 0.0 || self.pairs.len() < 2 {
            return f64::NAN;
        }
        let target = pressure.log10();
        if target < self.pairs[0].0 || target > self.pairs[self.pairs.len() - 1].0 {
            return f64::NAN;
        }
        let upper = self.pairs.partition_point(|pair| pair.0 <= target);
        if upper == 0 {
            return self.pairs[0].1;
        }
        if upper == self.pairs.len() {
            return self.pairs[self.pairs.len() - 1].1;
        }
        let lower = upper - 1;
        if self.pairs[lower].0 == target {
            return self.pairs[lower].1;
        }
        let (x0, y0) = self.pairs[lower];
        let (x1, y1) = self.pairs[upper];
        y0 + ((target - x0) / (x1 - x0)) * (y1 - y0)
    }
}

struct ParcelProfile {
    surface_pressure: f64,
    surface_height: f64,
    top_pressure: f64,
    pressure: Vec<f64>,
    temperature: Vec<f64>,
    dewpoint: Vec<f64>,
    temp_series: PressureSeries,
    dewpoint_series: PressureSeries,
    height_series: PressureSeries,
    env_virtual_series: PressureSeries,
    levels: Vec<Level>,
}

fn is_missing(value: f64, missing: Option<f64>) -> bool {
    !value.is_finite() || missing.is_some_and(|sentinel| value == sentinel)
}

fn theta(pressure: f64, temperature: f64, reference_pressure: f64) -> f64 {
    if !pressure.is_finite()
        || pressure <= 0.0
        || !temperature.is_finite()
        || !reference_pressure.is_finite()
        || reference_pressure <= 0.0
    {
        return f64::NAN;
    }
    (temperature + ZERO_C_K) * (reference_pressure / pressure).powf(ROCP) - ZERO_C_K
}

fn lcl_temperature(temperature: f64, dewpoint: f64) -> f64 {
    let spread = temperature - dewpoint;
    let delta = spread
        * (1.218_5
            + 0.001_278 * temperature
            + spread * (-0.002_19 + 1.173e-5 * spread - 0.000_005_2 * temperature));
    temperature - delta
}

fn dry_lift(pressure: f64, temperature: f64, dewpoint: f64) -> (f64, f64) {
    if [pressure, temperature, dewpoint]
        .iter()
        .any(|value| !value.is_finite())
        || pressure <= 0.0
    {
        return (f64::NAN, f64::NAN);
    }
    let lifted_temperature = lcl_temperature(temperature, dewpoint);
    let potential_temperature = theta(pressure, temperature, 1_000.0);
    let pressure_lcl = 1_000.0
        / ((potential_temperature + ZERO_C_K) / (lifted_temperature + ZERO_C_K)).powf(1.0 / ROCP);
    (pressure_lcl, lifted_temperature)
}

fn vapor_pressure(temperature: f64) -> f64 {
    let mut polynomial = temperature * (1.111_201_8e-17 + temperature * -3.099_457_1e-20);
    polynomial = temperature * (2.187_442_5e-13 + temperature * (-1.789_232e-15 + polynomial));
    polynomial = temperature * (4.388_418_0e-9 + temperature * (-2.988_388e-11 + polynomial));
    polynomial = temperature * (7.873_616_9e-5 + temperature * (-6.111_796e-7 + polynomial));
    polynomial = 0.999_996_83 + temperature * (-9.082_695e-3 + polynomial);
    6.107_8 / polynomial.powi(8)
}

fn mix_ratio(pressure: f64, temperature: f64) -> f64 {
    if !pressure.is_finite() || pressure <= 0.0 || !temperature.is_finite() {
        return f64::NAN;
    }
    let x = 0.02 * (temperature - 12.5 + (7_500.0 / pressure));
    let correction = 1.0 + (0.000_004_5 * pressure) + (0.001_4 * x * x);
    let vapor = correction * vapor_pressure(temperature);
    621.97 * vapor / (pressure - vapor)
}

fn temperature_at_mix_ratio(ratio: f64, pressure: f64) -> f64 {
    if !ratio.is_finite() || ratio <= 0.0 || !pressure.is_finite() || pressure <= 0.0 {
        return f64::NAN;
    }
    let x = (ratio * pressure / (622.0 + ratio)).log10();
    10.0_f64.powf((0.049_864_645_5 * x) + 2.408_296_5) - 7.074_75
        + 38.911_4 * (10.0_f64.powf(0.091_5 * x) - 1.203_5).powi(2)
        - ZERO_C_K
}

fn virtual_temperature(pressure: f64, temperature: f64, dewpoint: f64) -> f64 {
    if !temperature.is_finite() {
        return f64::NAN;
    }
    let ratio = 0.001 * mix_ratio(pressure, dewpoint);
    if !ratio.is_finite() {
        return temperature;
    }
    let kelvin = temperature + ZERO_C_K;
    kelvin * (1.0 + ratio / EPS) / (1.0 + ratio) - ZERO_C_K
}

fn wobus(temperature: f64) -> f64 {
    if !temperature.is_finite() {
        return f64::NAN;
    }
    let shifted = temperature - 20.0;
    if shifted <= 0.0 {
        let polynomial = 1.0
            + shifted
                * (-8.841_660_5e-3
                    + shifted
                        * (1.471_414_3e-4
                            + shifted
                                * (-9.671_989e-7
                                    + shifted * (-3.260_721_7e-8 + shifted * -3.859_807_3e-10))));
        15.13 / polynomial.powi(4)
    } else {
        let nested = shifted
            * (4.961_892_2e-7
                + shifted
                    * (-6.105_936_5e-9
                        + shifted
                            * (3.940_155_1e-11
                                + shifted * (-1.258_812_9e-13 + shifted * 1.668_828e-16))));
        let polynomial = 1.0 + shifted * (3.618_298_9e-3 + shifted * (-1.360_327_3e-5 + nested));
        29.93 / polynomial.powi(4) + 0.96 * shifted - 14.8
    }
}

fn saturated_lift(pressure: f64, moist_theta: f64) -> f64 {
    if !pressure.is_finite() || pressure <= 0.0 || !moist_theta.is_finite() {
        return f64::NAN;
    }
    if (pressure - 1_000.0).abs() <= 0.001 {
        return moist_theta;
    }
    let pressure_power = (pressure / 1_000.0).powf(ROCP);
    let mut first_temperature = (moist_theta + ZERO_C_K) * pressure_power - ZERO_C_K;
    let mut first_error = wobus(first_temperature) - wobus(moist_theta);
    let mut rate = 1.0;
    for _ in 0..MAX_SATLIFT_ITERATIONS {
        let second_temperature = first_temperature - first_error * rate;
        let mut second_error = (second_temperature + ZERO_C_K) / pressure_power - ZERO_C_K;
        second_error += wobus(second_temperature) - wobus(second_error) - moist_theta;
        let error = second_error * rate;
        if !error.is_finite() {
            return f64::NAN;
        }
        if error.abs() <= 0.1 {
            return second_temperature - error;
        }
        let denominator = second_error - first_error;
        if denominator == 0.0 || !denominator.is_finite() {
            return f64::NAN;
        }
        rate = (second_temperature - first_temperature) / denominator;
        first_temperature = second_temperature;
        first_error = second_error;
    }
    f64::NAN
}

fn wet_lift(pressure: f64, temperature: f64, target_pressure: f64) -> f64 {
    let potential_temperature = theta(pressure, temperature, 1_000.0);
    if !potential_temperature.is_finite() || !target_pressure.is_finite() {
        return f64::NAN;
    }
    let moist_theta = potential_temperature - wobus(potential_temperature) + wobus(temperature);
    saturated_lift(target_pressure, moist_theta)
}

fn wet_bulb(pressure: f64, temperature: f64, dewpoint: f64) -> f64 {
    let (lcl_pressure, lcl_temperature) = dry_lift(pressure, temperature, dewpoint);
    wet_lift(lcl_pressure, lcl_temperature, pressure)
}

fn theta_e(pressure: f64, temperature: f64, dewpoint: f64) -> f64 {
    let (lcl_pressure, lcl_temperature) = dry_lift(pressure, temperature, dewpoint);
    theta(
        100.0,
        wet_lift(lcl_pressure, lcl_temperature, 100.0),
        1_000.0,
    )
}

impl ParcelProfile {
    #[allow(clippy::too_many_arguments)]
    fn new(
        pres: &[f64],
        hght: &[f64],
        tmpc: &[f64],
        dwpc: &[f64],
        sfc: usize,
        missing: Option<f64>,
    ) -> Result<Option<Self>, String> {
        if !(pres.len() == hght.len() && pres.len() == tmpc.len() && pres.len() == dwpc.len()) {
            return Err(format!(
                "profile parcel column lengths differ: pres={}, hght={}, tmpc={}, dwpc={}",
                pres.len(),
                hght.len(),
                tmpc.len(),
                dwpc.len()
            ));
        }
        if pres.len() < 3 {
            if sfc != 0 {
                return Err("sfc is outside the profile".to_string());
            }
            return Ok(None);
        }
        if sfc >= pres.len() {
            return Err("sfc is outside the profile".to_string());
        }
        let surface_pressure = pres[sfc];
        let surface_height = hght[sfc];
        if is_missing(surface_pressure, missing)
            || surface_pressure <= 0.0
            || is_missing(surface_height, missing)
        {
            return Ok(None);
        }

        let env_virtual: Vec<f64> = pres
            .iter()
            .enumerate()
            .map(|(index, pressure)| {
                if is_missing(*pressure, missing) || is_missing(tmpc[index], missing) {
                    f64::NAN
                } else {
                    virtual_temperature(*pressure, tmpc[index], dwpc[index])
                }
            })
            .collect();
        let mut levels = Vec::new();
        for index in sfc..pres.len() {
            if is_missing(pres[index], missing)
                || pres[index] <= 0.0
                || is_missing(hght[index], missing)
                || !env_virtual[index].is_finite()
            {
                continue;
            }
            levels.push(Level {
                pressure: pres[index],
                height: hght[index],
                env_virtual_temperature: env_virtual[index],
            });
        }
        if levels.len() < 2 {
            return Ok(None);
        }
        levels.sort_by(|left, right| {
            right
                .pressure
                .partial_cmp(&left.pressure)
                .unwrap_or(Ordering::Equal)
        });
        let top_pressure = levels[levels.len() - 1].pressure;
        Ok(Some(Self {
            surface_pressure,
            surface_height,
            top_pressure,
            pressure: pres.to_vec(),
            temperature: tmpc.to_vec(),
            dewpoint: dwpc.to_vec(),
            temp_series: PressureSeries::new(pres, tmpc, missing),
            dewpoint_series: PressureSeries::new(pres, dwpc, missing),
            height_series: PressureSeries::new(pres, hght, missing),
            env_virtual_series: PressureSeries::new(pres, &env_virtual, missing),
            levels,
        }))
    }

    fn start_height(&self, pressure: f64) -> f64 {
        self.height_series.at(pressure) - self.surface_height
    }

    fn surface_start(&self, sfc: usize) -> ParcelStart {
        ParcelStart {
            pressure: self.surface_pressure,
            temperature: self.temperature[sfc],
            dewpoint: self.dewpoint[sfc],
        }
    }

    fn most_unstable_start(&self) -> ParcelStart {
        let top = self.surface_pressure - 300.0;
        if top <= 0.0
            || !self.temp_series.at(top).is_finite()
            || !self.dewpoint_series.at(top).is_finite()
        {
            return ParcelStart {
                pressure: f64::NAN,
                temperature: f64::NAN,
                dewpoint: f64::NAN,
            };
        }
        let mut best = None;
        let mut pressure = self.surface_pressure;
        while pressure > top - 1.0 {
            let temperature = self.temp_series.at(pressure);
            let dewpoint = self.dewpoint_series.at(pressure);
            let (lcl_pressure, lcl_temperature) = dry_lift(pressure, temperature, dewpoint);
            let theta_e_proxy = wet_lift(lcl_pressure, lcl_temperature, 1_000.0);
            if theta_e_proxy.is_finite()
                && best.is_none_or(|(_, best_value): (ParcelStart, f64)| {
                    theta_e_proxy > best_value + THERMO_TOLERANCE
                })
            {
                best = Some((
                    ParcelStart {
                        pressure,
                        temperature,
                        dewpoint,
                    },
                    theta_e_proxy,
                ));
            }
            pressure -= 1.0;
        }
        best.map_or(
            ParcelStart {
                pressure: f64::NAN,
                temperature: f64::NAN,
                dewpoint: f64::NAN,
            },
            |(start, _)| start,
        )
    }

    fn mixed_layer_start(&self) -> ParcelStart {
        let bottom = self.surface_pressure;
        let top = bottom - 100.0;
        let bottom_temperature = self.temp_series.at(bottom);
        let top_temperature = self.temp_series.at(top);
        let bottom_dewpoint = self.dewpoint_series.at(bottom);
        let top_dewpoint = self.dewpoint_series.at(top);
        if [
            bottom_temperature,
            top_temperature,
            bottom_dewpoint,
            top_dewpoint,
        ]
        .iter()
        .any(|value| !value.is_finite())
        {
            return ParcelStart {
                pressure: f64::NAN,
                temperature: f64::NAN,
                dewpoint: f64::NAN,
            };
        }

        let mut theta_sum = 0.5 * theta(bottom, bottom_temperature, 1_000.0)
            + 0.5 * theta(top, top_temperature, 1_000.0);
        let mut pressure_sum = 0.5 * bottom + 0.5 * top;
        let mut dewpoint_sum = 0.5 * bottom_dewpoint + 0.5 * top_dewpoint;
        let mut weight = 1.0;
        for index in 0..self.pressure.len() {
            let pressure = self.pressure[index];
            if pressure >= bottom
                || pressure <= top
                || !pressure.is_finite()
                || !self.temperature[index].is_finite()
                || !self.dewpoint[index].is_finite()
            {
                continue;
            }
            theta_sum += theta(pressure, self.temperature[index], 1_000.0);
            pressure_sum += pressure;
            dewpoint_sum += self.dewpoint[index];
            weight += 1.0;
        }
        let mean_theta = theta_sum / weight;
        let mean_pressure = pressure_sum / weight;
        let mean_dewpoint = dewpoint_sum / weight;
        let ratio = mix_ratio(mean_pressure, mean_dewpoint);
        ParcelStart {
            pressure: bottom,
            temperature: theta(1_000.0, mean_theta, bottom),
            dewpoint: temperature_at_mix_ratio(ratio, bottom),
        }
    }

    fn forecast_start(&self) -> ParcelStart {
        let top = self.surface_pressure - 100.0;
        let top_temperature = self.temp_series.at(top);
        let (_, mean_pressure, mean_dewpoint) = self.exact_layer_means(self.surface_pressure, top);
        let ratio = mix_ratio(mean_pressure, mean_dewpoint);
        ParcelStart {
            pressure: self.surface_pressure,
            temperature: if top_temperature.is_finite() && top > 0.0 {
                (top_temperature + ZERO_C_K + 2.0) * (self.surface_pressure / top).powf(ROCP)
                    - ZERO_C_K
            } else {
                f64::NAN
            },
            dewpoint: temperature_at_mix_ratio(ratio, self.surface_pressure),
        }
    }

    fn exact_layer_means(&self, bottom: f64, top: f64) -> (f64, f64, f64) {
        let bottom_temperature = self.temp_series.at(bottom);
        let top_temperature = self.temp_series.at(top);
        let bottom_dewpoint = self.dewpoint_series.at(bottom);
        let top_dewpoint = self.dewpoint_series.at(top);
        if [
            bottom_temperature,
            top_temperature,
            bottom_dewpoint,
            top_dewpoint,
        ]
        .iter()
        .any(|value| !value.is_finite())
        {
            return (f64::NAN, f64::NAN, f64::NAN);
        }
        let mut theta_sum = 0.5 * theta(bottom, bottom_temperature, 1_000.0)
            + 0.5 * theta(top, top_temperature, 1_000.0);
        let mut pressure_sum = 0.5 * bottom + 0.5 * top;
        let mut dewpoint_sum = 0.5 * bottom_dewpoint + 0.5 * top_dewpoint;
        let mut weight = 1.0;
        for index in 0..self.pressure.len() {
            let pressure = self.pressure[index];
            if pressure >= bottom
                || pressure <= top
                || !pressure.is_finite()
                || !self.temperature[index].is_finite()
                || !self.dewpoint[index].is_finite()
            {
                continue;
            }
            theta_sum += theta(pressure, self.temperature[index], 1_000.0);
            pressure_sum += pressure;
            dewpoint_sum += self.dewpoint[index];
            weight += 1.0;
        }
        (
            theta_sum / weight,
            pressure_sum / weight,
            dewpoint_sum / weight,
        )
    }

    fn effective_start(&self, bottom: f64, top: f64) -> ParcelStart {
        if !bottom.is_finite() || !top.is_finite() || bottom < top {
            return ParcelStart {
                pressure: f64::NAN,
                temperature: f64::NAN,
                dewpoint: f64::NAN,
            };
        }
        let mut theta_weighted_sum = 0.0;
        let mut theta_weight_sum = 0.0;
        let mut ratio_sum = 0.0;
        let mut ratio_count = 0.0;
        let mut pressure = bottom;
        while pressure >= top {
            let temperature = self.temp_series.at(pressure);
            let dewpoint = self.dewpoint_series.at(pressure);
            let potential_temperature = theta(pressure, temperature, 1_000.0);
            let ratio = mix_ratio(pressure, dewpoint);
            if potential_temperature.is_finite() {
                theta_weighted_sum += potential_temperature * pressure;
                theta_weight_sum += pressure;
            }
            if ratio.is_finite() {
                ratio_sum += ratio;
                ratio_count += 1.0;
            }
            pressure -= 1.0;
        }
        let mean_theta = theta_weighted_sum / theta_weight_sum;
        let mean_ratio = ratio_sum / ratio_count;
        let start_pressure = (bottom + top) / 2.0;
        ParcelStart {
            pressure: start_pressure,
            temperature: theta(1_000.0, mean_theta, start_pressure),
            dewpoint: temperature_at_mix_ratio(mean_ratio, start_pressure),
        }
    }
}

fn buoyancy_fraction(parcel_virtual: f64, environment_virtual: f64) -> f64 {
    if !parcel_virtual.is_finite() || !environment_virtual.is_finite() {
        return f64::NAN;
    }
    (parcel_virtual - environment_virtual) / (environment_virtual + ZERO_C_K)
}

fn refine_crossing(
    profile: &ParcelProfile,
    previous_pressure: f64,
    previous_parcel_temperature: f64,
    current_pressure: f64,
    from_nonpositive: bool,
) -> f64 {
    let mut pressure = previous_pressure;
    while pressure > current_pressure {
        let parcel_temperature = wet_lift(previous_pressure, previous_parcel_temperature, pressure);
        let parcel_virtual = virtual_temperature(pressure, parcel_temperature, parcel_temperature);
        let environment_virtual = profile.env_virtual_series.at(pressure);
        let difference = parcel_virtual - environment_virtual;
        if !difference.is_finite() {
            return f64::NAN;
        }
        let crossed = if from_nonpositive {
            difference >= 0.0
        } else {
            difference <= 0.0
        };
        if crossed {
            return pressure;
        }
        pressure -= 5.0;
    }
    current_pressure
}

fn partial_positive_energy(
    profile: &ParcelProfile,
    previous: Level,
    previous_parcel_temperature: f64,
    target_height: f64,
) -> f64 {
    let height_pairs: Vec<(f64, f64)> = profile
        .levels
        .iter()
        .map(|level| (level.height, level.pressure.log10()))
        .collect();
    if height_pairs.len() < 2 {
        return f64::NAN;
    }
    let upper = height_pairs.partition_point(|pair| pair.0 <= target_height);
    if upper == 0 || upper == height_pairs.len() {
        return f64::NAN;
    }
    let (h0, p0) = height_pairs[upper - 1];
    let (h1, p1) = height_pairs[upper];
    let target_pressure = 10.0_f64.powf(p0 + ((target_height - h0) / (h1 - h0)) * (p1 - p0));
    let target_parcel_temperature = wet_lift(
        previous.pressure,
        previous_parcel_temperature,
        target_pressure,
    );
    let previous_parcel_virtual = virtual_temperature(
        previous.pressure,
        previous_parcel_temperature,
        previous_parcel_temperature,
    );
    let target_parcel_virtual = virtual_temperature(
        target_pressure,
        target_parcel_temperature,
        target_parcel_temperature,
    );
    let previous_def = buoyancy_fraction(previous_parcel_virtual, previous.env_virtual_temperature);
    let target_def = buoyancy_fraction(
        target_parcel_virtual,
        profile.env_virtual_series.at(target_pressure),
    );
    let energy = GRAVITY * (previous_def + target_def) / 2.0 * (target_height - previous.height);
    energy.max(0.0)
}

fn lift_parcel(profile: &ParcelProfile, start: ParcelStart) -> ParcelAscent {
    let mut output = [f64::NAN; PARCEL_WIDTH];
    let mut pressure_trace = Vec::new();
    let mut temperature_trace = Vec::new();
    output[0] = start.pressure;
    output[1] = profile.start_height(start.pressure);
    output[2] = start.temperature;
    output[3] = start.dewpoint;
    if [start.pressure, start.temperature, start.dewpoint]
        .iter()
        .any(|value| !value.is_finite())
        || start.pressure <= 0.0
    {
        return ParcelAscent {
            diagnostics: output,
            pressure_trace,
            temperature_trace,
        };
    }
    pressure_trace.push(start.pressure);
    temperature_trace.push(virtual_temperature(
        start.pressure,
        start.temperature,
        start.dewpoint,
    ));
    let (lcl_pressure, lcl_temperature) =
        dry_lift(start.pressure, start.temperature, start.dewpoint);
    output[4] = lcl_pressure.min(profile.surface_pressure);
    output[5] = profile.start_height(lcl_pressure);
    if !lcl_pressure.is_finite()
        || !lcl_temperature.is_finite()
        || lcl_pressure < profile.top_pressure
        || start.pressure - profile.top_pressure > MAX_PRESSURE_SPAN
    {
        return ParcelAscent {
            diagnostics: output,
            pressure_trace,
            temperature_trace,
        };
    }
    pressure_trace.push(lcl_pressure);
    temperature_trace.push(virtual_temperature(
        lcl_pressure,
        lcl_temperature,
        lcl_temperature,
    ));

    let theta_parcel = theta(lcl_pressure, lcl_temperature, 1_000.0);
    let parcel_mix_ratio = mix_ratio(start.pressure, start.dewpoint);
    let mut negative_energy = 0.0;
    let mut dry_pressures = Vec::new();
    let mut pressure = start.pressure;
    while pressure > lcl_pressure - 1.0 {
        dry_pressures.push(pressure);
        pressure -= 1.0;
    }
    for pair in dry_pressures.windows(2) {
        let p1 = pair[0];
        let p2 = pair[1];
        let h1 = profile.height_series.at(p1);
        let h2 = profile.height_series.at(p2);
        let env_theta1 = theta(p1, profile.temp_series.at(p1), 1_000.0);
        let env_theta2 = theta(p2, profile.temp_series.at(p2), 1_000.0);
        let env_virtual1 = virtual_temperature(p1, env_theta1, profile.dewpoint_series.at(p1));
        let env_virtual2 = virtual_temperature(p2, env_theta2, profile.dewpoint_series.at(p2));
        let parcel_virtual1 = virtual_temperature(
            p1,
            theta_parcel,
            temperature_at_mix_ratio(parcel_mix_ratio, p1),
        );
        let parcel_virtual2 = virtual_temperature(
            p2,
            theta_parcel,
            temperature_at_mix_ratio(parcel_mix_ratio, p2),
        );
        let def1 = buoyancy_fraction(parcel_virtual1, env_virtual1);
        let def2 = buoyancy_fraction(parcel_virtual2, env_virtual2);
        let energy = GRAVITY * (def1 + def2) / 2.0 * (h2 - h1);
        if energy.is_finite() && energy < 0.0 {
            negative_energy += energy;
        }
    }

    let mut previous = Level {
        pressure: lcl_pressure,
        height: profile.height_series.at(lcl_pressure),
        env_virtual_temperature: profile.env_virtual_series.at(lcl_pressure),
    };
    let mut previous_parcel_temperature = lcl_temperature;
    let mut previous_def = buoyancy_fraction(
        virtual_temperature(lcl_pressure, lcl_temperature, lcl_temperature),
        previous.env_virtual_temperature,
    );
    let mut positive_energy = 0.0;
    let mut previous_energy = 0.0;
    let mut terminal_energy = 0.0;
    let mut terminal_pressure = f64::NAN;
    let mut cape_3km = f64::NAN;
    let mut cape_6km = f64::NAN;
    let lcl_height = output[5];
    if lcl_height >= 3_000.0 {
        cape_3km = 0.0;
    }
    if lcl_height >= 6_000.0 {
        cape_6km = 0.0;
    }

    let moist_levels: Vec<Level> = profile
        .levels
        .iter()
        .copied()
        .filter(|level| level.pressure <= lcl_pressure)
        .collect();
    for (level_index, current) in moist_levels.iter().copied().enumerate() {
        let parcel_temperature = wet_lift(
            previous.pressure,
            previous_parcel_temperature,
            current.pressure,
        );
        let parcel_virtual =
            virtual_temperature(current.pressure, parcel_temperature, parcel_temperature);
        pressure_trace.push(current.pressure);
        temperature_trace.push(parcel_virtual);
        let current_def = buoyancy_fraction(parcel_virtual, current.env_virtual_temperature);
        let energy =
            GRAVITY * (previous_def + current_def) / 2.0 * (current.height - previous.height);
        let positive_before = positive_energy;
        if level_index + 1 == moist_levels.len() {
            terminal_energy = energy;
            terminal_pressure = current.pressure;
        }
        if energy.is_finite() {
            if energy > 0.0 {
                positive_energy += energy;
            } else if current.pressure > 500.0 {
                negative_energy += energy;
            }
        }

        let previous_agl = previous.height - profile.surface_height;
        let current_agl = current.height - profile.surface_height;
        if !cape_3km.is_finite() && previous_agl <= 3_000.0 && current_agl >= 3_000.0 {
            cape_3km = positive_before
                + partial_positive_energy(
                    profile,
                    previous,
                    previous_parcel_temperature,
                    profile.surface_height + 3_000.0,
                );
        }
        if !cape_6km.is_finite() && previous_agl <= 6_000.0 && current_agl >= 6_000.0 {
            cape_6km = positive_before
                + partial_positive_energy(
                    profile,
                    previous,
                    previous_parcel_temperature,
                    profile.surface_height + 6_000.0,
                );
        }

        if energy >= 0.0 && previous_energy <= 0.0 {
            let crossing = refine_crossing(
                profile,
                previous.pressure,
                previous_parcel_temperature,
                current.pressure,
                true,
            );
            output[6] = crossing.min(lcl_pressure);
            output[7] = profile.start_height(output[6]).max(lcl_height);
            output[8] = f64::NAN;
            output[9] = f64::NAN;
        }
        if energy <= 0.0 && previous_energy >= 0.0 {
            output[8] = refine_crossing(
                profile,
                previous.pressure,
                previous_parcel_temperature,
                current.pressure,
                false,
            );
            output[9] = profile.start_height(output[8]);
        }

        previous = current;
        previous_parcel_temperature = parcel_temperature;
        previous_def = current_def;
        previous_energy = energy;
    }

    // SHARPpy's parcel integration treats the requested top pressure as a
    // fractional boundary and replaces the final full layer with that
    // fractional contribution. The default top is the last observed level,
    // so the replacement is zero-length and the terminal full layer must be
    // removed from the accumulated totals.
    if terminal_energy > 0.0 {
        positive_energy -= terminal_energy;
    } else if terminal_energy < 0.0 && terminal_pressure > 500.0 {
        negative_energy -= terminal_energy;
    }

    output[10] = positive_energy;
    output[11] = if positive_energy.floor() == 0.0 {
        0.0
    } else {
        negative_energy
    };
    output[12] = cape_3km;
    output[13] = cape_6km;
    ParcelAscent {
        diagnostics: output,
        pressure_trace,
        temperature_trace,
    }
}

/// Compute surface, most-unstable, and mixed-layer parcel summaries.
pub fn profile_parcels(
    pres: &[f64],
    hght: &[f64],
    tmpc: &[f64],
    dwpc: &[f64],
    sfc: usize,
    missing: Option<f64>,
) -> Result<[[f64; PARCEL_WIDTH]; PARCEL_COUNT], String> {
    let Some(profile) = ParcelProfile::new(pres, hght, tmpc, dwpc, sfc, missing)? else {
        return Ok([[f64::NAN; PARCEL_WIDTH]; PARCEL_COUNT]);
    };
    Ok([
        lift_parcel(&profile, profile.surface_start(sfc)).diagnostics,
        lift_parcel(&profile, profile.most_unstable_start()).diagnostics,
        lift_parcel(&profile, profile.mixed_layer_start()).diagnostics,
    ])
}

fn effective_layer(
    profile: &ParcelProfile,
    sfc: usize,
    most_unstable: &ParcelAscent,
) -> (f64, f64) {
    let cape = most_unstable.diagnostics[10];
    let cin = most_unstable.diagnostics[11];
    if !cape.is_finite() || !cin.is_finite() || cape < 100.0 || cin <= -250.0 {
        return (f64::NAN, f64::NAN);
    }

    let mut bottom_index = None;
    for index in sfc..profile.pressure.len().saturating_sub(1) {
        let start = ParcelStart {
            pressure: profile.pressure[index],
            temperature: profile.temperature[index],
            dewpoint: profile.dewpoint[index],
        };
        if [start.pressure, start.temperature, start.dewpoint]
            .iter()
            .any(|value| !value.is_finite())
        {
            continue;
        }
        let parcel = lift_parcel(profile, start);
        if parcel.diagnostics[10] >= 100.0 && parcel.diagnostics[11] > -250.0 {
            bottom_index = Some(index);
            break;
        }
    }
    let Some(bottom_index) = bottom_index else {
        return (f64::NAN, f64::NAN);
    };

    let bottom = profile.pressure[bottom_index];
    let mut previous_valid_pressure = bottom;
    for index in (bottom_index + 1)..profile.pressure.len().saturating_sub(1) {
        let start = ParcelStart {
            pressure: profile.pressure[index],
            temperature: profile.temperature[index],
            dewpoint: profile.dewpoint[index],
        };
        if [start.pressure, start.temperature, start.dewpoint]
            .iter()
            .any(|value| !value.is_finite())
        {
            continue;
        }
        let parcel = lift_parcel(profile, start);
        if parcel.diagnostics[10] < 100.0 || parcel.diagnostics[11] <= -250.0 {
            return (bottom, previous_valid_pressure.min(bottom));
        }
        previous_valid_pressure = start.pressure;
    }
    (f64::NAN, f64::NAN)
}

/// Compute surface, forecast, most-unstable, mixed-layer, and effective parcels.
pub fn profile_convective_parcels(
    pres: &[f64],
    hght: &[f64],
    tmpc: &[f64],
    dwpc: &[f64],
    sfc: usize,
    missing: Option<f64>,
) -> Result<ConvectiveParcelWorkspace, String> {
    let Some(profile) = ParcelProfile::new(pres, hght, tmpc, dwpc, sfc, missing)? else {
        let missing_ascent = ParcelAscent {
            diagnostics: [f64::NAN; PARCEL_WIDTH],
            pressure_trace: Vec::new(),
            temperature_trace: Vec::new(),
        };
        return Ok(ConvectiveParcelWorkspace {
            parcels: std::array::from_fn(|_| missing_ascent.clone()),
            effective_bottom_pressure: f64::NAN,
            effective_top_pressure: f64::NAN,
        });
    };

    let most_unstable_start = profile.most_unstable_start();
    let most_unstable = lift_parcel(&profile, most_unstable_start);
    let surface = if most_unstable_start.pressure == profile.surface_pressure {
        most_unstable.clone()
    } else {
        lift_parcel(&profile, profile.surface_start(sfc))
    };
    let forecast = lift_parcel(&profile, profile.forecast_start());
    let mixed_layer = lift_parcel(&profile, profile.mixed_layer_start());
    let (effective_bottom_pressure, effective_top_pressure) =
        effective_layer(&profile, sfc, &most_unstable);
    let effective = if effective_bottom_pressure.is_finite() && effective_top_pressure.is_finite() {
        lift_parcel(
            &profile,
            profile.effective_start(effective_bottom_pressure, effective_top_pressure),
        )
    } else {
        surface.clone()
    };

    Ok(ConvectiveParcelWorkspace {
        parcels: [surface, forecast, most_unstable, mixed_layer, effective],
        effective_bottom_pressure,
        effective_top_pressure,
    })
}

/// Lift one explicitly defined parcel and return its full plotting trace.
#[allow(clippy::too_many_arguments)]
pub fn explicit_parcel(
    pres: &[f64],
    hght: &[f64],
    tmpc: &[f64],
    dwpc: &[f64],
    parcel_pressure: f64,
    parcel_temperature: f64,
    parcel_dewpoint: f64,
    sfc: usize,
    missing: Option<f64>,
) -> Result<ParcelAscent, String> {
    let Some(profile) = ParcelProfile::new(pres, hght, tmpc, dwpc, sfc, missing)? else {
        return Ok(ParcelAscent {
            diagnostics: [f64::NAN; PARCEL_WIDTH],
            pressure_trace: Vec::new(),
            temperature_trace: Vec::new(),
        });
    };
    Ok(lift_parcel(
        &profile,
        ParcelStart {
            pressure: parcel_pressure,
            temperature: parcel_temperature,
            dewpoint: parcel_dewpoint,
        },
    ))
}

fn mean_theta_e(theta_e_series: &PressureSeries, bottom: f64, top: f64) -> f64 {
    if !bottom.is_finite() || !top.is_finite() || bottom < top {
        return f64::NAN;
    }
    let mut weighted_sum = 0.0;
    let mut weight_sum = 0.0;
    let mut pressure = bottom;
    while pressure >= top {
        let value = theta_e_series.at(pressure);
        if value.is_finite() {
            weighted_sum += value * pressure;
            weight_sum += pressure;
        }
        pressure -= 1.0;
    }
    weighted_sum / weight_sum
}

/// Compute SHARPpy-compatible downdraft CAPE and its temperature trace.
pub fn profile_dcape(
    pres: &[f64],
    hght: &[f64],
    tmpc: &[f64],
    dwpc: &[f64],
    sfc: usize,
    missing: Option<f64>,
) -> Result<DowndraftDiagnostics, String> {
    let Some(profile) = ParcelProfile::new(pres, hght, tmpc, dwpc, sfc, missing)? else {
        return Ok(DowndraftDiagnostics {
            cape: f64::NAN,
            source_pressure: f64::NAN,
            downrush_temperature: f64::NAN,
            pressure_trace: Vec::new(),
            temperature_trace: Vec::new(),
        });
    };

    let theta_e_values: Vec<f64> = pres
        .iter()
        .enumerate()
        .map(|(index, pressure)| theta_e(*pressure, tmpc[index], dwpc[index]))
        .collect();
    let theta_e_series = PressureSeries::new(pres, &theta_e_values, missing);
    let lower_bound = profile.surface_pressure - 400.0;
    let mut minimum = 1_000.0;
    let mut source_pressure = f64::NAN;
    for index in sfc..pres.len() {
        let pressure = pres[index];
        if pressure < lower_bound
            || is_missing(pressure, missing)
            || !theta_e_values[index].is_finite()
        {
            continue;
        }
        let mean = mean_theta_e(&theta_e_series, pressure, pressure - 100.0);
        if mean.is_finite() && mean < minimum {
            minimum = mean;
            source_pressure = pressure - 50.0;
        }
    }
    if !source_pressure.is_finite() {
        return Ok(DowndraftDiagnostics {
            cape: f64::NAN,
            source_pressure,
            downrush_temperature: f64::NAN,
            pressure_trace: Vec::new(),
            temperature_trace: Vec::new(),
        });
    }

    let mut valid_levels: Vec<Level> = profile
        .levels
        .iter()
        .copied()
        .filter(|level| {
            level.pressure <= profile.surface_pressure
                && level.pressure >= source_pressure
                && profile.temp_series.at(level.pressure).is_finite()
        })
        .collect();
    valid_levels.sort_by(|left, right| {
        left.pressure
            .partial_cmp(&right.pressure)
            .unwrap_or(Ordering::Equal)
    });

    let source_temperature = wet_bulb(
        source_pressure,
        profile.temp_series.at(source_pressure),
        profile.dewpoint_series.at(source_pressure),
    );
    let mut pressure_trace = vec![source_pressure];
    let mut temperature_trace = vec![source_temperature];
    let mut previous_pressure = source_pressure;
    let mut previous_temperature = source_temperature;
    let mut previous_environment = profile.temp_series.at(source_pressure);
    let mut previous_height = profile.height_series.at(source_pressure);
    let mut cape = 0.0;

    for level in valid_levels {
        let parcel_temperature = wet_lift(previous_pressure, previous_temperature, level.pressure);
        let environment_temperature = profile.temp_series.at(level.pressure);
        let def1 =
            (previous_temperature - previous_environment) / (previous_environment + ZERO_C_K);
        let def2 =
            (parcel_temperature - environment_temperature) / (environment_temperature + ZERO_C_K);
        let energy = 9.8 * (def1 + def2) / 2.0 * (level.height - previous_height);
        if energy.is_finite() {
            cape += energy;
        }
        pressure_trace.push(level.pressure);
        temperature_trace.push(parcel_temperature);
        previous_pressure = level.pressure;
        previous_temperature = parcel_temperature;
        previous_environment = environment_temperature;
        previous_height = level.height;
    }

    Ok(DowndraftDiagnostics {
        cape,
        source_pressure,
        downrush_temperature: previous_temperature,
        pressure_trace,
        temperature_trace,
    })
}

#[cfg(test)]
mod tests {
    use super::{
        explicit_parcel, profile_convective_parcels, profile_dcape, profile_parcels,
        CONVECTIVE_PARCEL_COUNT, PARCEL_COUNT, PARCEL_WIDTH,
    };

    fn unstable_profile() -> (Vec<f64>, Vec<f64>, Vec<f64>, Vec<f64>) {
        let height: Vec<f64> = (0..17).map(|index| index as f64 * 1_000.0).collect();
        let pressure: Vec<f64> = height
            .iter()
            .map(|height| 1_000.0 * (-height / 8_000.0).exp())
            .collect();
        let temperature: Vec<f64> = height
            .iter()
            .map(|height| 30.0 - 7.0 * height / 1_000.0)
            .collect();
        let dewpoint: Vec<f64> = temperature
            .iter()
            .enumerate()
            .map(|(index, value)| value - 3.0 - index as f64)
            .collect();
        (pressure, height, temperature, dewpoint)
    }

    #[test]
    fn unstable_profile_produces_three_finite_parcels() {
        let (pressure, height, temperature, dewpoint) = unstable_profile();
        let result = profile_parcels(&pressure, &height, &temperature, &dewpoint, 0, None).unwrap();

        assert_eq!(result.len(), PARCEL_COUNT);
        assert_eq!(result[0].len(), PARCEL_WIDTH);
        assert!(result[0][4].is_finite());
        assert!(result[0][10].is_finite());
        assert!(result[1][0].is_finite());
        assert!(result[2][0].is_finite());
    }

    #[test]
    fn extended_workspace_and_explicit_ascent_include_traces() {
        let (pressure, height, temperature, dewpoint) = unstable_profile();
        let result =
            profile_convective_parcels(&pressure, &height, &temperature, &dewpoint, 0, None)
                .unwrap();
        assert_eq!(result.parcels.len(), CONVECTIVE_PARCEL_COUNT);
        assert!(result
            .parcels
            .iter()
            .all(|parcel| parcel.pressure_trace.len() == parcel.temperature_trace.len()));
        assert!(result.parcels[0].pressure_trace.len() > 2);

        let user = explicit_parcel(
            &pressure,
            &height,
            &temperature,
            &dewpoint,
            pressure[2],
            temperature[2],
            dewpoint[2],
            0,
            None,
        )
        .unwrap();
        assert!(user.diagnostics[10].is_finite());
        assert_eq!(user.pressure_trace.len(), user.temperature_trace.len());
    }

    #[test]
    fn downdraft_workspace_has_finite_summary_and_trace() {
        let (pressure, height, temperature, dewpoint) = unstable_profile();
        let result = profile_dcape(&pressure, &height, &temperature, &dewpoint, 0, None).unwrap();
        assert!(result.cape.is_finite());
        assert!(result.source_pressure.is_finite());
        assert!(result.downrush_temperature.is_finite());
        assert!(result.pressure_trace.len() > 2);
        assert_eq!(result.pressure_trace.len(), result.temperature_trace.len());
    }

    #[test]
    fn invalid_and_shallow_profiles_are_handled() {
        assert!(profile_parcels(
            &[1_000.0, 900.0],
            &[0.0],
            &[20.0, 10.0],
            &[15.0, 5.0],
            0,
            None,
        )
        .is_err());
        let shallow = profile_parcels(
            &[1_000.0, 900.0],
            &[0.0, 1_000.0],
            &[20.0, 10.0],
            &[15.0, 5.0],
            0,
            None,
        )
        .unwrap();
        assert!(shallow.iter().flatten().all(|value| value.is_nan()));
    }
}

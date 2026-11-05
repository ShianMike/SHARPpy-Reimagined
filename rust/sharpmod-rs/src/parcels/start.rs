//! Parcel selection and independently masked layer averages.

use super::{
    dry_lift, is_missing, mix_ratio, temperature_at_mix_ratio, theta, wet_lift, ParcelProfile,
    ParcelStart, ROCP, THERMO_TOLERANCE, ZERO_C_K,
};

impl ParcelProfile<'_> {
    pub(super) fn surface_start(&self, sfc: usize) -> ParcelStart {
        ParcelStart {
            pressure: self.surface_pressure,
            temperature: self.temperature[sfc],
            dewpoint: self.dewpoint[sfc],
        }
    }

    pub(super) fn most_unstable_start(&self) -> ParcelStart {
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

    pub(super) fn mixed_layer_start(&self) -> ParcelStart {
        let bottom = self.surface_pressure;
        let top = bottom - 100.0;
        let (mean_theta, mean_pressure, mean_dewpoint) = self.exact_layer_means(bottom, top);
        let ratio = mix_ratio(mean_pressure, mean_dewpoint);
        ParcelStart {
            pressure: bottom,
            temperature: theta(1_000.0, mean_theta, bottom),
            dewpoint: temperature_at_mix_ratio(ratio, bottom),
        }
    }

    pub(super) fn forecast_start(&self) -> ParcelStart {
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

    pub(super) fn exact_layer_means(&self, bottom: f64, top: f64) -> (f64, f64, f64) {
        let bottom_temperature = self.temp_series.at(bottom);
        let top_temperature = self.temp_series.at(top);
        let bottom_dewpoint = self.dewpoint_series.at(bottom);
        let top_dewpoint = self.dewpoint_series.at(top);
        let mut theta_sum = 0.5 * theta(bottom, bottom_temperature, 1_000.0)
            + 0.5 * theta(top, top_temperature, 1_000.0);
        let mut pressure_sum = 0.5 * bottom + 0.5 * top;
        let mut dewpoint_sum = 0.5 * bottom_dewpoint + 0.5 * top_dewpoint;
        let mut theta_weight = 1.0;
        let mut moisture_weight = 1.0;
        for index in 0..self.pressure.len() {
            let pressure = self.pressure[index];
            if pressure >= bottom || pressure <= top || is_missing(pressure, self.missing) {
                continue;
            }
            if !is_missing(self.temperature[index], self.missing) {
                theta_sum += theta(pressure, self.temperature[index], 1_000.0);
                theta_weight += 1.0;
            }
            if !is_missing(self.dewpoint[index], self.missing) {
                pressure_sum += pressure;
                dewpoint_sum += self.dewpoint[index];
                moisture_weight += 1.0;
            }
        }
        (
            theta_sum / theta_weight,
            pressure_sum / moisture_weight,
            dewpoint_sum / moisture_weight,
        )
    }

    pub(super) fn effective_start(&self, bottom: f64, top: f64) -> ParcelStart {
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
        // SHARPpy samples this layer with
        // ``np.arange(bottom, top - 1.0, -1.0)``.  For a fractional upper
        // boundary that deliberately retains the final integer-spaced sample
        // just below ``top``; stopping at ``pressure >= top`` changes the
        // effective parcel's mean moisture and can move its LCL trace beyond
        // the public 0.05 C equivalence tolerance.
        while pressure > top - 1.0 {
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

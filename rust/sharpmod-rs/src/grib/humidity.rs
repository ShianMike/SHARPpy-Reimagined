//! The decoder's established Magnus conversion, also used by spatial aggregation.

const A: f64 = 17.625;
const B: f64 = 243.04;

pub(super) fn dewpoint_from_rh(temperature_c: f64, relative_humidity: f64) -> f64 {
    let rh = relative_humidity.clamp(1.0e-3, 100.0);
    let gamma = (rh / 100.0).ln() + (A * temperature_c) / (B + temperature_c);
    (B * gamma) / (A - gamma)
}

pub(crate) fn dewpoint_from_specific_humidity(specific_humidity: f64, pressure_hpa: f64) -> f64 {
    let vapor_pressure =
        ((specific_humidity * pressure_hpa) / (0.622 + 0.378 * specific_humidity)).max(1.0e-6);
    let logarithm = (vapor_pressure / 6.112).ln();
    (B * logarithm) / (A - logarithm)
}

pub(crate) fn specific_humidity_from_dewpoint(dewpoint_c: f64, pressure_hpa: f64) -> f64 {
    let vapor_pressure = 6.112 * (A * dewpoint_c / (B + dewpoint_c)).exp();
    0.622 * vapor_pressure / (pressure_hpa - 0.378 * vapor_pressure)
}

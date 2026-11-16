//! One verified ground-row contract for provider soundings and native GRIB.

mod bindings;

pub(crate) use bindings::register;

const COLUMN_NAMES: [&str; 9] = [
    "pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "omeg", "u", "v",
];
const MPS_TO_KNOTS: f64 = 1.94384449;
const SUPERSATURATION_TOLERANCE_C: f64 = 0.5;

#[derive(Clone, Copy, Debug)]
struct GroundThermodynamics {
    values: [f64; 4],
}

impl GroundThermodynamics {
    fn verified(mut values: [f64; 4], missing: Option<f64>) -> Option<Self> {
        if values
            .iter()
            .any(|value| !value.is_finite() || missing == Some(*value))
        {
            return None;
        }
        let [pressure, height, temperature, dewpoint] = values;
        if !(100.0..=1100.0).contains(&pressure)
            || !(-1000.0..=10_000.0).contains(&height)
            || !(-120.0..=70.0).contains(&temperature)
            || !(-150.0..=temperature + SUPERSATURATION_TOLERANCE_C).contains(&dewpoint)
        {
            return None;
        }
        if dewpoint > temperature {
            values[3] = temperature;
        }
        Some(Self { values })
    }

    fn with_wind(self, u: f64, v: f64, missing: Option<f64>) -> Option<GroundLevel> {
        if [u, v]
            .iter()
            .any(|&value| !value.is_finite() || missing == Some(value) || value.abs() > 200.0)
        {
            return None;
        }
        Some(GroundLevel { thermo: self, u, v })
    }
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct GroundLevel {
    thermo: GroundThermodynamics,
    u: f64,
    v: f64,
}

impl GroundLevel {
    /// Inputs are hPa, metres MSL, degrees C, and earth-relative m/s.
    pub(crate) fn verified(values: [f64; 6], missing: Option<f64>) -> Option<Self> {
        let [p, z, t, td, u, v] = values;
        GroundThermodynamics::verified([p, z, t, td], missing)?.with_wind(u, v, missing)
    }

    pub(crate) fn pressure(self) -> f64 {
        self.thermo.values[0]
    }

    pub(crate) fn row(self, missing: f64) -> [f64; 9] {
        let [pressure, height, temperature, dewpoint] = self.thermo.values;
        let (u, v) = (self.u, self.v);
        [
            pressure,
            height,
            temperature,
            dewpoint,
            (270.0 - v.atan2(u).to_degrees()).rem_euclid(360.0),
            u.hypot(v) * MPS_TO_KNOTS,
            missing,
            u,
            v,
        ]
    }
}

struct MergedProfile {
    columns: [Vec<f64>; 9],
    removed: usize,
}

/// The binding owns and aligns columns before releasing the interpreter.
fn merge(columns: [Vec<f64>; 9], ground: GroundLevel, missing: f64) -> MergedProfile {
    let keep: Vec<_> = columns[0]
        .iter()
        .enumerate()
        .filter_map(|(index, &pressure)| {
            (pressure.is_finite()
                && pressure != missing
                && pressure > 0.0
                && pressure < ground.pressure())
            .then_some(index)
        })
        .collect();
    let removed = columns[0].len() - keep.len();
    let row = ground.row(missing);
    let columns = std::array::from_fn(|field| {
        let mut merged = Vec::with_capacity(keep.len() + 1);
        merged.push(row[field]);
        merged.extend(keep.iter().map(|&index| columns[field][index]));
        merged
    });
    MergedProfile { columns, removed }
}

#[cfg(test)]
mod tests;

//! Interpolate columns on log pressure, then average conserved moisture and vectors.

use super::{geometry::Bounds, Error, Limits};
use crate::grib::{
    humidity::{dewpoint_from_specific_humidity, specific_humidity_from_dewpoint},
    DecodedPoint,
};
use std::time::{Duration, Instant};

const MISSING: f64 = -9999.0;
const MPS_TO_KNOTS: f64 = 1.94384449;

pub struct Column<'a> {
    pub profile: &'a DecodedPoint,
    pub overlap_area: f64,
    /// Native relative vorticity at above-ground pressure levels, descending.
    pub vorticity: &'a [(f64, f64)],
}

#[derive(Clone, Debug)]
pub struct Coverage {
    pub pressure_hpa: f64,
    pub valid_columns: usize,
    pub fraction: f64,
    pub retained: bool,
}

#[derive(Clone, Debug)]
pub struct Averaged {
    pub profile: DecodedPoint,
    pub selected_columns: usize,
    pub valid_columns: usize,
    pub surface_pressure_hpa: f64,
    pub coverage: Vec<Coverage>,
    pub interpolation_ms: f64,
    pub spatial_ms: f64,
}

fn present(value: f64) -> bool {
    value.is_finite() && value != MISSING
}

impl Column<'_> {
    pub(crate) fn contributes(&self) -> bool {
        self.overlap_area > 0.0
            && self.profile.surface_merged
            && self.profile.level_count >= 2
            && self.level(0).is_some()
    }

    /// One complete common-column state: height, temperature, q, u, v, omega.
    fn level(&self, index: usize) -> Option<[f64; 6]> {
        let n = self.profile.level_count;
        let get = |field| self.profile.matrix[field * n + index];
        let values = [get(1), get(2), get(3), get(7), get(8)];
        if !values.iter().copied().all(present) {
            return None;
        }
        let humidity = specific_humidity_from_dewpoint(values[2], get(0));
        if !humidity.is_finite() || !(0.0..1.0).contains(&humidity) {
            return None;
        }
        Some([values[0], values[1], humidity, values[3], values[4], get(6)])
    }

    fn at(&self, pressure: f64) -> Option<[f64; 6]> {
        let levels = &self.profile.matrix[..self.profile.level_count];
        let index = levels.partition_point(|value| *value > pressure);
        if levels
            .get(index)
            .is_some_and(|value| (*value - pressure).abs() < 1.0e-8)
        {
            return self.level(index);
        }
        if index == 0 || index == levels.len() {
            return None;
        }
        let a = self.level(index - 1)?;
        let b = self.level(index)?;
        let fraction =
            (pressure / levels[index - 1]).ln() / (levels[index] / levels[index - 1]).ln();
        Some(std::array::from_fn(|field| {
            if present(a[field]) && present(b[field]) {
                a[field] + fraction * (b[field] - a[field])
            } else {
                MISSING
            }
        }))
    }

    fn vorticity_at(&self, pressure: f64) -> Option<f64> {
        let index = self.vorticity.partition_point(|value| value.0 > pressure);
        if let Some(&(p, value)) = self.vorticity.get(index) {
            if (p - pressure).abs() < 1.0e-8 {
                return present(value).then_some(value);
            }
        }
        if index == 0 || index == self.vorticity.len() {
            return None;
        }
        let (p_a, a) = self.vorticity[index - 1];
        let (p_b, b) = self.vorticity[index];
        let fraction = (pressure / p_a).ln() / (p_b / p_a).ln();
        (present(a) && present(b)).then_some(a + fraction * (b - a))
    }
}

/// The base is the lowest surface pressure, hence above every contributing ground.
/// It is deliberately not an average of incompatible terrain-level measurements.
pub fn aggregate(
    columns: &[Column<'_>],
    bounds: Bounds,
    requested_area: f64,
    limits: Limits,
    cancelled: &dyn Fn() -> bool,
) -> Result<Averaged, Error> {
    let started = Instant::now();
    let mut interpolation = Duration::ZERO;
    let limits = limits.validate()?;
    if cancelled() {
        return Err(Error::Cancelled);
    }
    if columns.len() > limits.max_columns {
        return Err(Error::Invalid(format!(
            "The box exceeds the {} native-column limit; draw a smaller box",
            limits.max_columns
        )));
    }
    if !requested_area.is_finite() || requested_area <= 0.0 {
        return Err(Error::Invalid(
            "The geographic box has no physical area".into(),
        ));
    }
    if requested_area > limits.max_area_km2 * 1_000_000.0 {
        return Err(Error::Invalid(format!(
            "The box exceeds the {:.0} square-kilometre limit",
            limits.max_area_km2
        )));
    }
    let valid: Vec<_> = columns
        .iter()
        .filter(|column| column.contributes())
        .collect();
    let valid_area: f64 = valid.iter().map(|column| column.overlap_area).sum();
    if valid_area / requested_area + 1.0e-8 < limits.minimum_coverage {
        return Err(Error::Data(format!(
            "Only {:.1}% of the box has a verified atmospheric column; at least {:.0}% is required",
            100.0 * valid_area / requested_area,
            100.0 * limits.minimum_coverage
        )));
    }
    let surface = valid
        .iter()
        .map(|column| column.profile.matrix[0])
        .reduce(f64::min)
        .ok_or_else(|| Error::Data("The box contains no valid native model columns".into()))?;
    let mut pressures = vec![surface];
    for column in &valid {
        pressures.extend(
            column.profile.matrix[..column.profile.level_count]
                .iter()
                .copied()
                .filter(|pressure| present(*pressure) && *pressure > 0.0 && *pressure < surface),
        );
    }
    pressures.sort_by(|a, b| b.total_cmp(a));
    pressures.dedup_by(|a, b| (*a - *b).abs() < 1.0e-8);
    let mut rows = Vec::<[f64; 9]>::new();
    let mut coverage = Vec::with_capacity(pressures.len());
    let mut surface_vorticity = None;
    let mut sampled = Vec::with_capacity(valid.len());
    for pressure in pressures {
        if cancelled() {
            return Err(Error::Cancelled);
        }
        let mut sum = [0.0; 5];
        let mut weight = 0.0;
        let mut omega = (0.0, 0.0);
        let mut vorticity = (0.0, 0.0);
        let mut count = 0;
        sampled.clear();
        let sampling = Instant::now();
        for column in &valid {
            if let Some(values) = column.at(pressure) {
                sampled.push((column, values));
            }
        }
        interpolation += sampling.elapsed();
        for (column, values) in &sampled {
            let area = column.overlap_area;
            for field in 0..5 {
                sum[field] += area * values[field];
            }
            weight += area;
            count += 1;
            if present(values[5]) {
                omega.0 += area * values[5];
                omega.1 += area;
            }
            if let Some(value) = column.vorticity_at(pressure) {
                vorticity.0 += area * value;
                vorticity.1 += area;
            }
        }
        let fraction = (weight / requested_area).min(1.0);
        let retained = weight > 0.0 && fraction + 1.0e-8 >= limits.minimum_coverage;
        coverage.push(Coverage {
            pressure_hpa: pressure,
            valid_columns: count,
            fraction,
            retained,
        });
        if !retained {
            if pressure == surface {
                return Err(Error::Data(
                    "The common above-ground surface has insufficient valid coverage".into(),
                ));
            }
            continue;
        }
        let [height, temperature, humidity, u, v] = sum.map(|value| value / weight);
        let dewpoint = dewpoint_from_specific_humidity(humidity, pressure);
        if !dewpoint.is_finite() || dewpoint > temperature + 1.0e-6 {
            return Err(Error::Data("The area mean is supersaturated; reduce the box to avoid combining incompatible air masses".into()));
        }
        if rows.last().is_some_and(|row| height <= row[1]) {
            return Err(Error::Data(
                "Changing valid coverage produced inconsistent mean heights; use a smaller box"
                    .into(),
            ));
        }
        let vertical = if omega.1 / requested_area + 1.0e-8 >= limits.minimum_coverage {
            omega.0 / omega.1
        } else {
            MISSING
        };
        rows.push([
            pressure,
            height,
            temperature,
            dewpoint.min(temperature),
            (270.0 - v.atan2(u).to_degrees()).rem_euclid(360.0),
            u.hypot(v) * MPS_TO_KNOTS,
            vertical,
            u,
            v,
        ]);
        if surface_vorticity.is_none()
            && vorticity.1 / requested_area + 1.0e-8 >= limits.minimum_coverage
        {
            surface_vorticity = Some(vorticity.0 / vorticity.1);
        }
    }
    if rows.len() < 2 {
        return Err(Error::Data(
            "Too few common above-ground levels have adequate valid coverage".into(),
        ));
    }
    let level_count = rows.len();
    let (longitude, latitude) = bounds.center();
    Ok(Averaged {
        profile: DecodedPoint {
            matrix: (0..9)
                .flat_map(|field| rows.iter().map(move |row| row[field]))
                .collect(),
            level_count,
            selected_latitude: latitude,
            selected_longitude: longitude,
            surface_relative_vorticity: surface_vorticity,
            surface_merged: true,
            below_ground_levels_removed: valid
                .iter()
                .map(|column| column.profile.below_ground_levels_removed)
                .sum(),
        },
        selected_columns: columns.len(),
        valid_columns: valid.len(),
        surface_pressure_hpa: surface,
        coverage,
        interpolation_ms: interpolation.as_secs_f64() * 1000.0,
        spatial_ms: started
            .elapsed()
            .saturating_sub(interpolation)
            .as_secs_f64()
            * 1000.0,
    })
}

#[cfg(test)]
mod science_tests;
#[cfg(test)]
mod tests;

//! Resample native radial codes without interpolating through missing sectors.

use rayon::prelude::*;

pub struct Radials<'a> {
    codes: &'a [u8],
    count: usize,
    gates: usize,
    starts: &'a [f64],
    ends: &'a [f64],
    gate_width: f32,
    gate_start: f32,
}

impl<'a> Radials<'a> {
    pub fn new(
        codes: &'a [u8],
        count: usize,
        gates: usize,
        starts: &'a [f64],
        ends: &'a [f64],
        gate_width: f32,
        gate_start: f32,
    ) -> Result<Self, String> {
        if count == 0
            || gates == 0
            || count.checked_mul(gates) != Some(codes.len())
            || starts.len() != count
            || ends.len() != count
            || !gate_width.is_finite()
            || gate_width <= 0.
            || !gate_start.is_finite()
            || starts.iter().chain(ends).any(|angle| !angle.is_finite())
            || starts.windows(2).any(|pair| pair[0] > pair[1])
        {
            return Err("unsupported NEXRAD geometry or units".into());
        }
        Ok(Self {
            codes,
            count,
            gates,
            starts,
            ends,
            gate_width,
            gate_start,
        })
    }

    pub fn sample(&self, bearings: &[f32], ranges: &[f32]) -> Result<Vec<u8>, String> {
        if bearings.len() != ranges.len() {
            return Err("unsupported NEXRAD projection dimensions".into());
        }
        let Self {
            codes,
            count,
            gates,
            starts,
            ends,
            gate_width,
            gate_start,
        } = *self;
        Ok(bearings
            .par_iter()
            .zip(ranges)
            .with_min_len(4096)
            .map(|(&azimuth, &distance)| {
                if !azimuth.is_finite() || !distance.is_finite() {
                    return 0;
                }
                let azimuth = f64::from(azimuth.rem_euclid(360.));
                let right = starts.partition_point(|&start| start <= azimuth);
                let radial = if right == 0 { count - 1 } else { right - 1 };
                let gate = ((distance - gate_start) / gate_width).floor();
                let covered = (azimuth - starts[radial]).rem_euclid(360.)
                    < (ends[radial] - starts[radial]).rem_euclid(360.);
                if covered && gate >= 0. && gate < gates as f32 {
                    codes[radial * gates + gate as usize]
                } else {
                    0
                }
            })
            .collect())
    }
}

#[cfg(test)]
mod tests;

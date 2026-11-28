//! Bundled ERA5 1990-2019 6-hourly 500 hPa geopotential height climatology.
//!
//! Stores 16 longitudinal-delta-encoded `i16` harmonic planes (annual mean,
//! 6 annual Fourier pairs, and 3 diurnal tide components) on a global
//! `1.5 deg` (`121 x 240`, `+90..-90` lat, `-180..+178.5` lon) grid.

use flate2::read::GzDecoder;
use std::f64::consts::PI;
use std::io::Read;
use std::sync::OnceLock;
use time::{OffsetDateTime, UtcOffset};

pub const LAT_COUNT: usize = 121;
pub const LON_COUNT: usize = 240;
const PLANE_COUNT: usize = 16;
const HEADER_LEN: usize = 16;
const MAGIC: &[u8; 4] = b"H5CL";
const FORMAT_VERSION: u8 = 1;

const CLIMO_GZ: &[u8] = include_bytes!("hgt500_climo_era5_1990_2019.bin.gz");

struct Climatology {
    planes: Vec<f32>,
}

pub struct LonWeight {
    pub x0: usize,
    pub x1: usize,
    pub fx: f32,
}

fn decode_climatology() -> Climatology {
    let mut decoder = GzDecoder::new(CLIMO_GZ);
    let mut raw = Vec::new();
    decoder
        .read_to_end(&mut raw)
        .expect("bundled 500 hPa climatology gzip payload must decompress");
    assert!(
        raw.len() >= HEADER_LEN,
        "climatology header truncated: {} bytes",
        raw.len()
    );
    assert_eq!(&raw[..4], MAGIC, "invalid climatology magic");
    assert_eq!(raw[4], FORMAT_VERSION, "unsupported climatology version");
    assert_eq!(
        usize::from(raw[5]),
        PLANE_COUNT,
        "unexpected climatology plane count"
    );
    let nlat = usize::from(u16::from_le_bytes([raw[6], raw[7]]));
    let nlon = usize::from(u16::from_le_bytes([raw[8], raw[9]]));
    assert_eq!((nlat, nlon), (LAT_COUNT, LON_COUNT));

    let a0_offset_m = f32::from(i16::from_le_bytes([raw[10], raw[11]]));
    let scale_m = f32::from_le_bytes([raw[12], raw[13], raw[14], raw[15]]);
    let cells = PLANE_COUNT * LAT_COUNT * LON_COUNT;
    assert_eq!(
        raw.len(),
        HEADER_LEN + cells * 2,
        "climatology payload byte count mismatch"
    );

    let mut planes = Vec::with_capacity(cells);
    let mut pos = HEADER_LEN;
    for plane in 0..PLANE_COUNT {
        let offset = if plane == 0 { a0_offset_m } else { 0.0 };
        for _row in 0..LAT_COUNT {
            let mut acc: i16 = 0;
            for _col in 0..LON_COUNT {
                let delta = i16::from_le_bytes([raw[pos], raw[pos + 1]]);
                pos += 2;
                acc = acc.wrapping_add(delta);
                planes.push(f32::from(acc) * scale_m + offset);
            }
        }
    }
    Climatology { planes }
}

fn climatology() -> &'static Climatology {
    static CLIMATOLOGY: OnceLock<Climatology> = OnceLock::new();
    CLIMATOLOGY.get_or_init(decode_climatology)
}

fn basis_weights(valid_time: OffsetDateTime) -> [f32; PLANE_COUNT] {
    let utc = valid_time.to_offset(UtcOffset::UTC);
    let year_days = if time::util::is_leap_year(utc.year()) {
        366.0
    } else {
        365.0
    };
    let day_zero = f64::from(utc.ordinal() - 1);
    let hour_frac =
        f64::from(utc.hour()) + f64::from(utc.minute()) / 60.0 + f64::from(utc.second()) / 3600.0;
    let theta = 2.0 * PI * (day_zero + hour_frac / 24.0) / year_days;
    let phi = 2.0 * PI * hour_frac / 24.0;

    let mut weights = [0.0_f32; PLANE_COUNT];
    weights[0] = 1.0;
    for k in 1..=6usize {
        let angle = (k as f64) * theta;
        weights[2 * k - 1] = angle.cos() as f32;
        weights[2 * k] = angle.sin() as f32;
    }
    weights[13] = phi.cos() as f32;
    weights[14] = phi.sin() as f32;
    weights[15] = (2.0 * phi).cos() as f32;
    weights
}

/// Evaluate the ERA5 1990-2019 500 hPa geopotential height climatology (gpm)
/// on its native `121 x 240` north-to-south (`+90..-90`), `-180..+178.5` grid.
pub fn evaluate_grid(valid_time: OffsetDateTime) -> Vec<f32> {
    let climo = climatology();
    let weights = basis_weights(valid_time);
    let grid_len = LAT_COUNT * LON_COUNT;
    let mut grid = vec![0.0_f32; grid_len];
    for (plane, &weight) in weights.iter().enumerate() {
        let coeffs = &climo.planes[plane * grid_len..(plane + 1) * grid_len];
        for (dst, &coeff) in grid.iter_mut().zip(coeffs) {
            *dst += weight * coeff;
        }
    }
    grid
}

/// Precompute horizontal climatology interpolation weights for a global grid of `cols` columns
/// spanning `-180..+180` (`lon(col) = -180 + 360 * col / cols`).
pub fn lon_weights(cols: usize) -> Vec<LonWeight> {
    (0..cols)
        .map(|col| {
            let x = (col as f64) * (LON_COUNT as f64) / (cols as f64);
            let xf = x.floor();
            let x0 = (xf as usize) % LON_COUNT;
            let x1 = (x0 + 1) % LON_COUNT;
            LonWeight {
                x0,
                x1,
                fx: (x - xf) as f32,
            }
        })
        .collect()
}

/// Compute one latitude row of 500 hPa height anomalies (dam) from `hgt500_m` (gpm).
///
/// Assumes canonical north-to-south latitude spacing `lat(row) = 90 - 180 * row / (rows - 1)`.
pub fn anomaly_row_dam(
    climo: &[f32],
    weights: &[LonWeight],
    row: usize,
    rows: usize,
    hgt500_m: &[f32],
    out: &mut [f32],
) {
    assert!(rows >= 2 && row < rows);
    assert_eq!(weights.len(), hgt500_m.len());
    assert_eq!(out.len(), hgt500_m.len());

    let y = (row as f64) * ((LAT_COUNT - 1) as f64) / ((rows - 1) as f64);
    let y0 = (y.floor() as usize).min(LAT_COUNT - 2);
    let y1 = y0 + 1;
    let fy = (y - y0 as f64) as f32;
    let row0 = &climo[y0 * LON_COUNT..(y0 + 1) * LON_COUNT];
    let row1 = &climo[y1 * LON_COUNT..(y1 + 1) * LON_COUNT];

    for ((dst, &raw), lw) in out.iter_mut().zip(hgt500_m).zip(weights) {
        if !raw.is_finite() {
            *dst = f32::NAN;
            continue;
        }
        let c0 = row0[lw.x0] + lw.fx * (row0[lw.x1] - row0[lw.x0]);
        let c1 = row1[lw.x0] + lw.fx * (row1[lw.x1] - row1[lw.x0]);
        let climo_m = c0 + fy * (c1 - c0);
        *dst = (raw - climo_m) * 0.1_f32;
    }
}

/// Compute global 500 hPa height anomalies (dam) for a north-to-south `rows x cols` grid (gpm).
pub fn compute_anomaly_dam(
    hgt500_m: &[f32],
    rows: usize,
    cols: usize,
    valid_time: OffsetDateTime,
) -> Result<Vec<f32>, String> {
    if rows < 2 || cols == 0 {
        return Err(format!("invalid global grid dimensions ({rows}, {cols})"));
    }
    if hgt500_m.len() != rows * cols {
        return Err(format!(
            "expected {} grid values for ({rows}, {cols}), got {}",
            rows * cols,
            hgt500_m.len()
        ));
    }
    let climo = evaluate_grid(valid_time);
    let weights = lon_weights(cols);
    let mut out = vec![0.0_f32; rows * cols];
    for row in 0..rows {
        let start = row * cols;
        anomaly_row_dam(
            &climo,
            &weights,
            row,
            rows,
            &hgt500_m[start..start + cols],
            &mut out[start..start + cols],
        );
    }
    Ok(out)
}

/// Bilinearly sample the 500 hPa climatology (gpm) at `(lat_deg, lon_deg)`.
pub fn sample_gpm(lat_deg: f64, lon_deg: f64, valid_time: OffsetDateTime) -> f32 {
    let climo = evaluate_grid(valid_time);
    let lat = lat_deg.clamp(-90.0, 90.0);
    let y = (90.0 - lat) / 1.5;
    let y0 = (y.floor() as usize).min(LAT_COUNT - 2);
    let y1 = y0 + 1;
    let fy = (y - y0 as f64) as f32;

    let lon_wrapped = (lon_deg + 180.0).rem_euclid(360.0);
    let x = lon_wrapped / 1.5;
    let xf = x.floor();
    let x0 = (xf as usize) % LON_COUNT;
    let x1 = (x0 + 1) % LON_COUNT;
    let fx = (x - xf) as f32;

    let c00 = climo[y0 * LON_COUNT + x0];
    let c01 = climo[y0 * LON_COUNT + x1];
    let c10 = climo[y1 * LON_COUNT + x0];
    let c11 = climo[y1 * LON_COUNT + x1];
    let c0 = c00 + fx * (c01 - c00);
    let c1 = c10 + fx * (c11 - c10);
    c0 + fy * (c1 - c0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use time::format_description::well_known::Rfc3339;

    fn utc(iso: &str) -> OffsetDateTime {
        OffsetDateTime::parse(iso, &Rfc3339).expect("valid RFC3339 timestamp")
    }

    #[test]
    fn bundled_asset_decodes_and_evaluates_within_physical_bounds() {
        let grid = evaluate_grid(utc("2025-01-15T00:00:00Z"));
        assert_eq!(grid.len(), LAT_COUNT * LON_COUNT);
        assert!(grid.iter().all(|v| v.is_finite()));
        let min = grid.iter().copied().fold(f32::INFINITY, f32::min);
        let max = grid.iter().copied().fold(f32::NEG_INFINITY, f32::max);
        assert!(min > 4600.0 && max < 6100.0, "range {min}..{max}");
    }

    #[test]
    fn seasonal_and_geographic_climatology_spots_match_era5() {
        let jan15 = utc("2025-01-15T00:00:00Z");
        let jul15 = utc("2025-07-15T00:00:00Z");

        // Norman OK: ~565 dam in Jan, ~590 dam in Jul.
        let norman_jan = sample_gpm(35.25, -97.5, jan15) * 0.1;
        let norman_jul = sample_gpm(35.25, -97.5, jul15) * 0.1;
        assert!(
            (562.0..568.0).contains(&norman_jan),
            "Norman Jan={norman_jan}"
        );
        assert!(
            (588.0..593.0).contains(&norman_jul),
            "Norman Jul={norman_jul}"
        );

        // Equator stays ~585-588 dam year-round with tiny seasonal swing.
        let eq_jan = sample_gpm(0.0, 0.0, jan15) * 0.1;
        let eq_jul = sample_gpm(0.0, 0.0, jul15) * 0.1;
        assert!((584.0..589.0).contains(&eq_jan), "Eq Jan={eq_jan}");
        assert!((584.0..589.0).contains(&eq_jul), "Eq Jul={eq_jul}");
        assert!((eq_jan - eq_jul).abs() < 3.0);

        // Poles show opposite seasonal cycles.
        let np_jan = sample_gpm(90.0, 0.0, jan15) * 0.1;
        let np_jul = sample_gpm(90.0, 0.0, jul15) * 0.1;
        assert!((504.0..512.0).contains(&np_jan), "NP Jan={np_jan}");
        assert!((541.0..548.0).contains(&np_jul), "NP Jul={np_jul}");

        let sp_jan = sample_gpm(-90.0, 0.0, jan15) * 0.1;
        let sp_jul = sample_gpm(-90.0, 0.0, jul15) * 0.1;
        assert!(sp_jan > sp_jul + 15.0, "SP Jan={sp_jan} Jul={sp_jul}");
    }

    #[test]
    fn compute_anomaly_preserves_identity_shifts_and_nonfinite_cells() {
        let when = utc("2025-05-10T18:00:00Z");
        let base = evaluate_grid(when);
        let zero = compute_anomaly_dam(&base, LAT_COUNT, LON_COUNT, when).unwrap();
        let max_zero_err = zero.iter().map(|v| v.abs()).fold(0.0_f32, f32::max);
        assert!(max_zero_err < 1e-4, "max_zero_err={max_zero_err}");

        let mut shifted = base.clone();
        for (idx, val) in shifted.iter_mut().enumerate() {
            if idx % 2 == 0 {
                *val += 100.0; // +10.0 dam
            } else {
                *val -= 250.0; // -25.0 dam
            }
        }
        shifted[0] = f32::NAN;
        shifted[123] = f32::INFINITY;
        let anom = compute_anomaly_dam(&shifted, LAT_COUNT, LON_COUNT, when).unwrap();
        assert!(anom[0].is_nan());
        assert!(anom[123].is_nan());
        assert!((anom[2] - 10.0).abs() < 1e-4);
        assert!((anom[3] + 25.0).abs() < 1e-4);
    }

    #[test]
    fn leap_day_year_boundary_and_antimeridian_wrap_stay_continuous() {
        let leap = evaluate_grid(utc("2024-02-29T12:00:00Z"));
        assert!(leap.iter().all(|v| v.is_finite()));

        let dec31_00z = evaluate_grid(utc("2025-12-31T00:00:00Z"));
        let dec31_18z = evaluate_grid(utc("2025-12-31T18:00:00Z"));
        let jan01_00z = evaluate_grid(utc("2026-01-01T00:00:00Z"));
        let max_day_diff_dam = dec31_00z
            .iter()
            .zip(&jan01_00z)
            .map(|(a, b)| ((a - b) * 0.1).abs())
            .fold(0.0_f32, f32::max);
        assert!(
            max_day_diff_dam < 1.5,
            "max 24h year-end diff {max_day_diff_dam} dam"
        );
        let max_six_hr_dam = dec31_18z
            .iter()
            .zip(&jan01_00z)
            .map(|(a, b)| ((a - b) * 0.1).abs())
            .fold(0.0_f32, f32::max);
        assert!(
            max_six_hr_dam < 3.0,
            "max 6h year-end jump {max_six_hr_dam} dam"
        );

        // On a 0.25 deg global grid (721 x 1440), col 0 (-180) and col 1439 (+179.75)
        // lie 0.25 deg apart across the antimeridian at the equator.
        let when = utc("2025-10-04T12:00:00Z");
        let rows = 721usize;
        let cols = 1440usize;
        let eq_row = 360usize;
        let hgt = vec![5860.0_f32; rows * cols];
        let anom = compute_anomaly_dam(&hgt, rows, cols, when).unwrap();
        let left_seam = anom[eq_row * cols];
        let right_seam = anom[eq_row * cols + (cols - 1)];
        assert!(
            (left_seam - right_seam).abs() < 0.05,
            "seam mismatch left={left_seam} right={right_seam}"
        );
    }
}

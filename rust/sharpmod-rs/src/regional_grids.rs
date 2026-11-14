//! Grids verified against the operational RAP 130 and RDPS GRIB2 headers.

use pyo3::{exceptions::PyValueError, prelude::*, types::PyDict};
use time::{OffsetDateTime, UtcOffset};

pub const RDPS_WIDTH: usize = 1140;
pub const RDPS_HEIGHT: usize = 1045;
pub const RDPS_X0: f64 = -53.858588;
pub const RDPS_Y0: f64 = -48.805954;
pub const RDPS_SPACING: f64 = 0.090298;
pub const RDPS_POLE_LAT: f64 = -31.758312;
pub const RDPS_POLE_LON: f64 = 267.597031;

#[pyfunction]
pub fn rap_forecast_hours(cycle: u8) -> PyResult<Vec<u16>> {
    if cycle > 23 {
        return Err(PyValueError::new_err("Invalid RAP cycle hour"));
    }
    Ok((0..=if matches!(cycle, 3 | 9 | 15 | 21) {
        51
    } else {
        21
    })
        .collect())
}

#[pyfunction]
pub fn rap_map_url(run: OffsetDateTime, hour: u16) -> PyResult<String> {
    let run = run.to_offset(UtcOffset::UTC);
    if run.minute() != 0
        || run.second() != 0
        || run.nanosecond() != 0
        || !rap_forecast_hours(run.hour())?.contains(&hour)
    {
        return Err(PyValueError::new_err(
            "RAP map frames require a whole-hour run and a published forecast hour",
        ));
    }
    Ok(format!("https://noaa-rap-pds.s3.amazonaws.com/rap.{:04}{:02}{:02}/rap.t{:02}z.awp130pgrbf{hour:02}.grib2",
        run.year(), u8::from(run.month()), run.day(), run.hour()))
}

#[pyfunction]
pub fn validate_rap_map_grid(api: &Bound<'_, PyAny>, handle: &Bound<'_, PyAny>) -> PyResult<()> {
    for (key, expected) in [
        ("Ni", 451.0),
        ("Nj", 337.0),
        ("DxInMetres", 13545.0),
        ("DyInMetres", 13545.0),
        ("LoVInDegrees", 265.0),
        ("Latin1InDegrees", 25.0),
        ("Latin2InDegrees", 25.0),
        ("latitudeOfFirstGridPointInDegrees", 16.281),
        ("longitudeOfFirstGridPointInDegrees", 233.862),
        ("iScansNegatively", 0.0),
        ("jScansPositively", 1.0),
        ("uvRelativeToGrid", 1.0),
        ("jPointsAreConsecutive", 0.0),
        ("alternativeRowScanning", 0.0),
        ("radius", 6371229.0),
    ] {
        let actual = api
            .call_method1("codes_get", (handle, key))?
            .extract::<f64>()?;
        if !actual.is_finite() || (actual - expected).abs() > 1e-6 {
            return Err(PyValueError::new_err(format!(
                "RAP field does not match grid 130 ({key})"
            )));
        }
    }
    if api
        .call_method1("codes_get", (handle, "gridType"))?
        .extract::<String>()?
        != "lambert"
    {
        return Err(PyValueError::new_err("RAP field is not Lambert conformal"));
    }
    Ok(())
}

#[pyfunction]
pub fn regional_map_grid(py: Python<'_>, model: &str) -> PyResult<Py<PyAny>> {
    let kwargs = PyDict::new(py);
    match model {
        "rap" => {
            let module = PyModule::import(py, "sharpmod.models.lambert_grid")?;
            let template = module.getattr("NAM_GRID")?;
            let (x, y): (f64, f64) = template
                .call_method1("forward", (-126.138, 16.281))?
                .extract()?;
            kwargs.set_item("shape", (337, 451))?;
            kwargs.set_item("spacing", 13545.0)?;
            kwargs.set_item("x0", x)?;
            kwargs.set_item("y0", y)?;
            Ok(PyModule::import(py, "dataclasses")?
                .call_method("replace", (template,), Some(&kwargs))?
                .unbind())
        }
        "rdps" => {
            kwargs.set_item("shape", (RDPS_HEIGHT, RDPS_WIDTH))?;
            kwargs.set_item("spacing", RDPS_SPACING)?;
            kwargs.set_item("x0", RDPS_X0)?;
            kwargs.set_item("y0", RDPS_Y0)?;
            Ok(PyModule::import(py, "types")?
                .getattr("SimpleNamespace")?
                .call((), Some(&kwargs))?
                .unbind())
        }
        _ => Err(PyValueError::new_err("Expected RAP or RDPS")),
    }
}

#[pyfunction]
pub fn regional_map_projection(model: &str) -> PyResult<&'static str> {
    match model {
        "rap" => Ok("+proj=lcc +lat_0=25 +lon_0=265 +lat_1=25 +lat_2=25 +R=6371229 +units=m +no_defs"),
        "rdps" => Ok("+proj=ob_tran +o_proj=longlat +o_lon_p=0 +o_lat_p=31.758312 +lon_0=267.597031 +R=6371229 +no_defs"),
        _ => Err(PyValueError::new_err("Expected RAP or RDPS")),
    }
}

/// True-east/north components on the RDPS rotated sphere, preserving speed.
pub fn rotate_rdps(u: &mut [f32], v: &mut [f32]) {
    let (pole_sin, pole_cos) = (-RDPS_POLE_LAT).to_radians().sin_cos();
    for (index, (u, v)) in u.iter_mut().zip(v).enumerate() {
        if !u.is_finite() || !v.is_finite() {
            *u = f32::NAN;
            *v = f32::NAN;
            continue;
        }
        let (sx, cx) = (RDPS_X0 + (index % RDPS_WIDTH) as f64 * RDPS_SPACING)
            .to_radians()
            .sin_cos();
        let (sy, cy) = (RDPS_Y0 + (index / RDPS_WIDTH) as f64 * RDPS_SPACING)
            .to_radians()
            .sin_cos();
        let a = pole_sin * cy - pole_cos * sy * cx;
        let b = pole_cos * sx;
        let norm = a.hypot(b);
        let (east, north) = (
            (a * f64::from(*u) + b * f64::from(*v)) / norm,
            (-b * f64::from(*u) + a * f64::from(*v)) / norm,
        );
        *u = east as f32;
        *v = north as f32;
    }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(regional_map_grid, module)?)?;
    module.add_function(wrap_pyfunction!(regional_map_projection, module)?)?;
    module.add_function(wrap_pyfunction!(rap_forecast_hours, module)?)?;
    module.add_function(wrap_pyfunction!(rap_map_url, module)?)?;
    module.add_function(wrap_pyfunction!(validate_rap_map_grid, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rap_horizons_follow_the_published_extended_cycles() {
        for cycle in 0..24 {
            let hours = rap_forecast_hours(cycle).unwrap();
            assert_eq!(hours.last(), Some(&if cycle % 6 == 3 { 51 } else { 21 }));
        }
        assert!(rap_forecast_hours(24).is_err());
        let run = OffsetDateTime::parse(
            "2026-11-14T03:00:00Z",
            &time::format_description::well_known::Rfc3339,
        )
        .unwrap();
        assert_eq!(
            rap_map_url(run, 51).unwrap(),
            "https://noaa-rap-pds.s3.amazonaws.com/rap.20261114/rap.t03z.awp130pgrbf51.grib2"
        );
        assert!(rap_map_url(run + time::Duration::hours(1), 22).is_err());
        assert!(rap_map_url(run + time::Duration::minutes(1), 1).is_err());
    }

    #[test]
    fn rotated_winds_keep_speed_missing_cells_and_true_north_orientation() {
        let mut u = vec![0.0; RDPS_WIDTH * RDPS_HEIGHT];
        let mut v = vec![10.0; u.len()];
        u[1] = f32::NAN;
        rotate_rdps(&mut u, &mut v);
        assert!(u[1].is_nan() && v[1].is_nan());
        for i in [0, 1139, 595650, u.len() - 1] {
            assert!((u[i].hypot(v[i]) - 10.0).abs() < 1e-5);
        }
        // Increasing rotated latitude at the south-west corner moves west
        // of true north; verified independently with ecCodes coordinates.
        assert!(u[0] < 0.0 && v[0] > 0.0);
    }
}

//! NOAA SPC calibrated GREFS severe-weather probabilities for the REFS map catalogue.

use crate::grib::grefs_grid;
use crate::model_http_bindings::{cancelled, field_error};
use numpy::{IntoPyArray, PyArray2};
use pyo3::{
    exceptions::PyValueError,
    prelude::*,
    types::{PyDict, PyTuple},
};
use sharpmod_weather::model_http::{Error, ModelClient};
use std::path::Path;
use time::{OffsetDateTime, UtcOffset};

const ROOT: &str = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/spc_post/prod";
const MAX_FILE_BYTES: usize = 1024 * 1024;
// The SPC REFS viewer uses these GREFS bands for both guidance windows.
// The last stop supplies the overflow colour above the final break.
const TORNADO_STOPS: &[(f64, &str)] = &[
    (2.0, "#5da64d"),
    (5.0, "#a0745f"),
    (10.0, "#fae160"),
    (15.0, "#eb5127"),
    (30.0, "#bb30f6"),
    (45.0, "#6c49a0"),
    (60.0, "#a5cfe3"),
];
const HAIL_WIND_STOPS: &[(f64, &str)] = &[
    (5.0, "#a0745f"),
    (15.0, "#fae160"),
    (30.0, "#eb5127"),
    (45.0, "#bb30f6"),
    (60.0, "#6c49a0"),
];

#[derive(Clone, Copy)]
struct Product {
    key: &'static str,
    label: &'static str,
    hazard: &'static str,
    number: i32,
    duration: u16,
}

const PRODUCTS: [Product; 6] = [
    Product {
        key: "grefs-tor-4h",
        label: "GREFS 4-h Tornado",
        hazard: "tornado",
        number: 197,
        duration: 4,
    },
    Product {
        key: "grefs-hail-4h",
        label: "GREFS 4-h Hail",
        hazard: "hail",
        number: 198,
        duration: 4,
    },
    Product {
        key: "grefs-wind-4h",
        label: "GREFS 4-h Wind",
        hazard: "damaging wind",
        number: 199,
        duration: 4,
    },
    Product {
        key: "grefs-tor-24h",
        label: "GREFS 24-h Tornado",
        hazard: "tornado",
        number: 197,
        duration: 24,
    },
    Product {
        key: "grefs-hail-24h",
        label: "GREFS 24-h Hail",
        hazard: "hail",
        number: 198,
        duration: 24,
    },
    Product {
        key: "grefs-wind-24h",
        label: "GREFS 24-h Wind",
        hazard: "damaging wind",
        number: 199,
        duration: 24,
    },
];

fn hours(cycle: u8, duration: u16) -> Vec<u16> {
    if !matches!(cycle, 0 | 6 | 12 | 18) {
        return Vec::new();
    }
    if duration == 4 {
        return (4..=60).collect();
    }
    // The first convective-day period from 06Z, 12Z, and 18Z is published as a
    // shorter remainder. Full periods end at 12Z, after a complete 24 hours.
    let end = if cycle == 0 {
        36
    } else {
        60 - u16::from(cycle)
    };
    (end..=60).step_by(24).collect()
}

fn file_url(run: OffsetDateTime, duration: u16, hour: u16) -> String {
    let (year, month, day, cycle) = (run.year(), u8::from(run.month()), run.day(), run.hour());
    format!("{ROOT}/spc_post.{year:04}{month:02}{day:02}/grefs/spc_post.t{cycle:02}z.grefs_{cycle:02}.{duration}hr.f{hour:03}.grib2")
}

pub(super) fn catalogue(py: Python<'_>) -> PyResult<Vec<Py<PyAny>>> {
    let api = PyModule::import(py, "sharpmod.providers.hrrr_products")?;
    let field = api
        .getattr("FieldSpec")?
        .call1(("probability", "sfc", "PROB", "surface"))?;
    let derive = api.getattr("_single")?.call1(("probability",))?;
    PRODUCTS.into_iter().map(|product| {
        let stops = if product.number == 197 { TORNADO_STOPS } else { HAIL_WIND_STOPS };
        let palette_options = PyDict::new(py);
        palette_options.set_item("ticks", PyTuple::new(py, stops.iter().map(|&(value, _)| value))?)?;
        palette_options.set_item("stepped", true)?;
        palette_options.set_item("display", "fixed")?;
        let palette = api.getattr("Palette")?.call((PyTuple::new(py, stops.iter().copied())?, "%"), Some(&palette_options))?;
        let options = PyDict::new(py);
        options.set_item("key", product.key)?;
        options.set_item("label", product.label)?;
        options.set_item("chip", product.label.trim_start_matches("GREFS "))?;
        options.set_item("category", "Guidance")?;
        options.set_item("fields", (&field,))?;
        options.set_item("derive", &derive)?;
        options.set_item("palette", palette)?;
        options.set_item("max_hours", product.duration)?;
        options.set_item("description", format!(
            "SPC calibrated {} probability for the {} hours ending at the selected valid time.",
            product.hazard, product.duration,
        ))?;
        options.set_item("caveat", if product.duration == 24 {
            "Full 12Z–12Z convective days from the same-cycle GEFS and REFS. Guidance can publish after other REFS fields."
        } else {
            "Uses the same-cycle GEFS and REFS. Guidance can publish after other REFS fields."
        })?;
        Ok(api.getattr("HrrrProduct")?.call((), Some(&options))?.unbind())
    }).collect()
}

#[pyfunction]
fn grefs_forecast_hours(py: Python<'_>, run: OffsetDateTime) -> PyResult<Bound<'_, PyDict>> {
    let out = PyDict::new(py);
    let cycle = run.to_offset(UtcOffset::UTC).hour();
    for product in PRODUCTS {
        out.set_item(product.key, hours(cycle, product.duration))?;
    }
    let zarr_3h = super::refs_zarr::window_step_hours(cycle, 3);
    let zarr_6h = super::refs_zarr::window_step_hours(cycle, 6);
    let zarr_4h = hours(cycle, 4);
    let zarr_24h = super::refs_zarr::window_24h_hours(cycle);
    let zarr_48h = super::refs_zarr::window_48h_hours(cycle);
    for (key, list) in [
        ("qpf-3h", &zarr_3h),
        ("qpf-6h", &zarr_6h),
        ("qpf-24h", &zarr_24h),
        ("qpf-48h", &zarr_48h),
        ("wind-10m-4h", &zarr_4h),
        ("refc-4h", &zarr_4h),
        ("wind-10m-24h", &zarr_24h),
        ("refc-24h", &zarr_24h),
    ] {
        out.set_item(key, list)?;
    }
    Ok(out)
}

#[pyfunction(signature = (key, run, hour, bounds, timeout, should_cancel=None))]
fn grefs_map_field<'py>(
    py: Python<'py>,
    key: &str,
    run: OffsetDateTime,
    hour: u16,
    bounds: (f64, f64, f64, f64),
    timeout: f64,
    should_cancel: Option<Py<PyAny>>,
) -> PyResult<(Bound<'py, PyArray2<f32>>, String)> {
    let product = PRODUCTS
        .iter()
        .find(|product| product.key == key)
        .ok_or_else(|| PyValueError::new_err("unknown GREFS product"))?;
    let run = run.to_offset(UtcOffset::UTC);
    if !hours(run.hour(), product.duration).contains(&hour) {
        return Err(field_error(py, Error::Unavailable(404)));
    }
    let url = file_url(run, product.duration, hour);
    let runtime = crate::weather::runtime()?;
    let stop = || cancelled(should_cancel.as_ref());
    let payload = py
        .detach(|| {
            runtime.block_on(ModelClient::shared().fetch(&url, timeout, MAX_FILE_BYTES, None, stop))
        })
        .map_err(|error| field_error(py, error))?;
    let library: String = PyModule::import(py, "sharpmod.backends.rust_backend")?
        .call_method0("_eccodes_library_path")?
        .extract()?;
    // Share ecCodes' process-wide settings with the existing Python consumers.
    let lock = PyModule::import(py, "sharpmod.backends.grib")?.getattr("_ECCODES_LOCK")?;
    lock.call_method0("acquire")?;
    let decoded = py.detach(|| {
        grefs_grid::decode(
            Path::new(&library),
            &payload,
            run,
            hour,
            product.duration,
            product.number,
        )
    });
    lock.call_method0("release")?;
    let values = py
        .detach(|| {
            if stop().map_err(Error::Invalid)? {
                return Err(Error::Cancelled);
            }
            decoded
                .and_then(|grid| grid.reproject(bounds))
                .map_err(|err| Error::Invalid(err.to_string()))
        })
        .map_err(|error| field_error(py, error))?;
    Ok((values.into_pyarray(py), url))
}

pub(super) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(grefs_forecast_hours, module)?)?;
    module.add_function(wrap_pyfunction!(grefs_map_field, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use time::{Date, Month};

    #[test]
    fn complete_probability_windows_end_at_their_source_forecast_hours() {
        assert_eq!(hours(0, 24), [36, 60]);
        assert_eq!(hours(6, 24), [54]);
        assert_eq!(hours(12, 24), [48]);
        assert_eq!(hours(18, 24), [42]);
        assert_eq!(hours(6, 4), (4..=60).collect::<Vec<_>>());
        assert!(hours(3, 4).is_empty());
    }

    #[test]
    fn requests_the_matching_refs_and_gefs_cycle_on_the_operational_feed() {
        let run = Date::from_calendar_date(2026, Month::November, 22)
            .unwrap()
            .with_hms(0, 0, 0)
            .unwrap()
            .assume_utc();
        assert_eq!(
            file_url(run, 4, 4),
            format!("{ROOT}/spc_post.20261122/grefs/spc_post.t00z.grefs_00.4hr.f004.grib2")
        );
        assert!(file_url(run, 24, 36).ends_with("/spc_post.t00z.grefs_00.24hr.f036.grib2"));
    }
}

//! DWD ICON Global map products over the provider's decoded ingredient grids.

use super::{
    derive_refs_member_lcl,
    palettes::{lcl_stops, LCL_TICKS},
    pressure_product,
};
use crate::{numeric_grid, wind_layers::maximum_bulk_shear};
use pyo3::{
    exceptions::PyKeyError,
    prelude::*,
    types::{PyDict, PyTuple},
};
use rayon::prelude::*;

const KG_M2_S_TO_IN_H: f64 = 3600.0 / 25.4;
const MS_TO_KT: f64 = 3600.0 / 1852.0;
const MS_TO_MPH: f64 = 3600.0 / 1609.344;

// One pixel sample from the interior of each 1 mph swatch, from 14 to 75 mph.
const GUST_COLOURS: [&str; 62] = [
    "#ffffff", "#f0f8ff", "#dbf0fe", "#c6e7fd", "#b1dffc", "#9cd6fb", "#87cefa", "#81b7f1",
    "#7ba0e8", "#7688df", "#7071d6", "#6a5acd", "#8366d0", "#9c72d3", "#b47ed6", "#cd8ad9",
    "#e696dc", "#e08ad6", "#da7ed0", "#d472ca", "#ce66c4", "#c85abe", "#c04cb6", "#b83eae",
    "#b030a6", "#a8229e", "#a01496", "#a81080", "#b00c6a", "#b80854", "#c0043e", "#c80028",
    "#cc082c", "#d01030", "#d41834", "#d82038", "#dc283c", "#e03040", "#e43844", "#e84048",
    "#ec484c", "#f05050", "#f27054", "#f49058", "#f6b05c", "#f8d060", "#faf064", "#f4e65e",
    "#eedc58", "#e8d252", "#e2c84c", "#dcbe46", "#d6b440", "#d0aa3a", "#caa034", "#c4962e",
    "#be8c28", "#b88222", "#b2781c", "#ac6e16", "#a66410", "#a05a0a",
];

fn gust_stops() -> impl Iterator<Item = (u8, &'static str)> {
    // Close the last band so the legend shows the full 75 mph swatch.
    let last_colour = GUST_COLOURS[GUST_COLOURS.len() - 1];
    (14..=76).zip(GUST_COLOURS.into_iter().chain([last_colour]))
}

fn precip_type_rate([rate, ww]: [f64; 2]) -> f64 {
    let rate_in_h = (rate * KG_M2_S_TO_IN_H * 1000.0).round() / 1000.0;
    if !rate_in_h.is_finite() || rate_in_h < 0.01 || ww.fract() != 0.0 {
        return f64::NAN;
    }

    // DWD ICON Table 6.1 leaves the precipitation phase of thunderstorms unspecified.
    let offset = match ww as u8 {
        51 | 53 | 55 | 61 | 63 | 65 | 80..=82 => 0.0,
        71 | 73 | 75 | 77 | 85 | 86 => 10.0,
        56 | 57 | 66 | 67 => 20.0,
        95 | 96 => 30.0,
        _ => return f64::NAN,
    };
    // Keep the rate in its group and bias exact thresholds above frame quantization.
    offset + rate_in_h.min(9.99) + 0.0004
}

fn shear_sfc_500_kt([u10, v10, u500, v500]: [f64; 4]) -> f64 {
    maximum_bulk_shear([&[u10], &[v10]], [&[u500], &[v500]])
        .map_or(f64::NAN, |speed| speed * MS_TO_KT)
}

fn derive_cells<const N: usize>(
    py: Python<'_>,
    fields: &Bound<'_, PyDict>,
    names: [&str; N],
    evaluate: fn([f64; N]) -> f64,
) -> PyResult<Py<PyAny>> {
    let inputs = names
        .into_iter()
        .map(|name| {
            fields
                .get_item(name)?
                .ok_or_else(|| PyKeyError::new_err(format!("decoded field {name} is missing")))?
                .extract()
        })
        .collect::<PyResult<_>>()?;
    let grids = numeric_grid::broadcast(inputs)?;
    let values = py.detach(|| {
        (0..grids.cells)
            .into_par_iter()
            .with_min_len(4096)
            .map(|cell| {
                evaluate(std::array::from_fn(|field| {
                    grids.buffers[field].value(cell)
                }))
            })
            .collect()
    });
    let single = grids.buffers.iter().all(numeric_grid::Buffer::single);
    Ok(numeric_grid::output(py, &grids.shape, values, single))
}

#[pyfunction]
fn derive_prate_ptype(py: Python<'_>, fields: &Bound<'_, PyDict>) -> PyResult<Py<PyAny>> {
    derive_cells(py, fields, ["prate", "ww"], precip_type_rate)
}

#[pyfunction]
fn derive_shear_sfc_500(py: Python<'_>, fields: &Bound<'_, PyDict>) -> PyResult<Py<PyAny>> {
    derive_cells(
        py,
        fields,
        ["u10", "v10", "ugrd500", "vgrd500"],
        shear_sfc_500_kt,
    )
}

#[pyfunction]
fn derive_gust_mph(py: Python<'_>, fields: &Bound<'_, PyDict>) -> PyResult<Py<PyAny>> {
    derive_cells(py, fields, ["gust"], |[gust]| gust * MS_TO_MPH)
}

pub(crate) fn catalogue(py: Python<'_>) -> PyResult<Vec<Py<PyAny>>> {
    let source = PyModule::import(py, "sharpmod.providers.hrrr_products")?;
    let shared = source.getattr("PRODUCTS")?;
    let field_spec = source.getattr("FieldSpec")?;
    let product = source.getattr("HrrrProduct")?;
    let palette = source.getattr("Palette")?;
    let replace = PyModule::import(py, "dataclasses")?.getattr("replace")?;
    let u10 = source.getattr("F_U10")?;
    let v10 = source.getattr("F_V10")?;

    let mut products = Vec::with_capacity(21);
    // The provider supplies hgt* in geopotential metres for the shared diagnostics.
    for key in ["hgt-wind-500", "hgt-wind-700", "hgt-wind-850"] {
        products.push(shared.get_item(key)?.unbind());
    }
    products.push(
        source
            .call_method1("_height_wind_product", (925,))?
            .unbind(),
    );
    for key in ["tmp-2m", "dpt-2m", "rh-2m", "thetae-2m"] {
        products.push(shared.get_item(key)?.unbind());
    }

    let mslp = pressure_product(py, "PRMSL", None)?;
    let mslp = mslp.bind(py);
    let updates = PyDict::new(py);
    updates.set_item("label", "Mean Sea-level Pressure, 10 m Wind")?;
    updates.set_item("fields", (mslp.getattr("fields")?.get_item(0)?, &u10, &v10))?;
    updates.set_item("wind", true)?;
    updates.set_item(
        "description",
        "Mean sea-level pressure with isobars every 4 hPa and 10 m wind barbs in knots.",
    )?;
    updates.set_item("caveat", "Uses DWD's native PMSL field.")?;
    products.push(replace.call((mslp,), Some(&updates))?.unbind());

    let options = PyDict::new(py);
    options.set_item("units", "in/h")?;
    options.set_item("ptype_labels", ("Rain", "Snow", "Frzr", "Storm"))?;
    let ptype_palette = replace.call((source.getattr("PALETTE_QPF_PTYPE")?,), Some(&options))?;
    let fields = (
        source.getattr("F_PRATE")?,
        field_spec.call1(("ww", "sfc", "WW", "surface"))?,
    );
    let updates = PyDict::new(py);
    updates.set_item("key", "prate-ptype")?;
    updates.set_item("label", "Precipitation Type and Rate")?;
    updates.set_item("chip", "P-Type & Rate")?;
    updates.set_item("category", "Precipitation")?;
    updates.set_item("fields", fields)?;
    updates.set_item("derive", wrap_pyfunction!(derive_prate_ptype, py)?)?;
    updates.set_item("palette", ptype_palette)?;
    updates.set_item("max_hours", 1)?;
    updates.set_item(
        "description",
        "Average liquid-equivalent precipitation rate since the previous published forecast hour, in inches per hour.",
    )?;
    updates.set_item(
        "caveat",
        "Available from F001. The rate averages 1 hour through F078 and 3 hours from F081. \
         DWD WW at the end of the interval describes the final hour only. Storm in the legend \
         marks wet thunderstorms (WW 95/96) with unspecified precipitation phase.",
    )?;
    products.push(product.call((), Some(&updates))?.unbind());

    let apcp = shared.get_item("apcp")?;
    let single = source.getattr("_single")?;
    let mm_to_in = source.getattr("mm_to_in")?;
    for hours in [6, 24, 48, 120] {
        let name = format!("qpf{hours}h");
        let label = format!("{hours}-h QPF");
        let spec = field_spec.call1((&name, "sfc", "APCP", "surface"))?;
        let updates = PyDict::new(py);
        updates.set_item("key", format!("qpf-{hours}h"))?;
        updates.set_item("label", &label)?;
        updates.set_item("chip", &label)?;
        updates.set_item("fields", (spec,))?;
        updates.set_item("derive", single.call1((&name, &mm_to_in))?)?;
        updates.set_item("max_hours", hours)?;
        updates.set_item("description", format!("Liquid-equivalent precipitation over the {hours} hours ending at the selected forecast time, in inches."))?;
        updates.set_item("caveat", format!("Uses the difference between DWD TOT_PREC run totals. Available from F{hours:03} when both endpoints are published."))?;
        products.push(replace.call((&apcp,), Some(&updates))?.unbind());
    }
    let updates = PyDict::new(py);
    updates.set_item("label", "Total QPF")?;
    updates.set_item("chip", "Total QPF")?;
    updates.set_item(
        "caveat",
        "Uses DWD TOT_PREC, the liquid-equivalent total since F000.",
    )?;
    products.push(replace.call((&apcp,), Some(&updates))?.unbind());

    for key in ["pwat", "lapse-700-500"] {
        products.push(shared.get_item(key)?.unbind());
    }
    let updates = PyDict::new(py);
    updates.set_item(
        "fields",
        (field_spec.call1(("mlcape", "sfc", "CAPE", "mixed layer"))?,),
    )?;
    updates.set_item("description", "Mixed-layer convective available potential energy from DWD's native CAPE_ML field, in J/kg.")?;
    products.push(
        replace
            .call((shared.get_item("mlcape")?,), Some(&updates))?
            .unbind(),
    );

    let options = PyDict::new(py);
    options.set_item("stepped", true)?;
    options.set_item("ticks", PyTuple::new(py, LCL_TICKS)?)?;
    let lcl_palette = palette.call((PyTuple::new(py, lcl_stops())?, "m"), Some(&options))?;
    let updates = PyDict::new(py);
    updates.set_item("key", "lcl")?;
    updates.set_item("label", "Surface-Based LCL Height")?;
    updates.set_item("chip", "SBLCL")?;
    updates.set_item("category", "Instability")?;
    updates.set_item(
        "fields",
        (source.getattr("F_TMP2M")?, source.getattr("F_DPT2M")?),
    )?;
    updates.set_item("derive", wrap_pyfunction!(derive_refs_member_lcl, py)?)?;
    updates.set_item("palette", lcl_palette)?;
    updates.set_item(
        "description",
        "Lifting condensation level height above ground for a surface-based parcel, in metres.",
    )?;
    updates.set_item(
        "caveat",
        "Estimated from 2 m temperature and dewpoint depression at 125 m/K.",
    )?;
    products.push(product.call((), Some(&updates))?.unbind());

    let winds_500 = shared.get_item("hgt-wind-500")?.getattr("fields")?;
    let updates = PyDict::new(py);
    updates.set_item("key", "shear-sfc-500")?;
    updates.set_item("label", "Bulk Shear: Surface-500 mb")?;
    updates.set_item("chip", "Sfc-500 Shr")?;
    updates.set_item(
        "fields",
        (u10, v10, winds_500.get_item(0)?, winds_500.get_item(1)?),
    )?;
    updates.set_item("derive", wrap_pyfunction!(derive_shear_sfc_500, py)?)?;
    updates.set_item(
        "description",
        "Magnitude of the vector wind difference between 10 m above ground and 500 mb, in knots.",
    )?;
    products.push(
        replace
            .call((shared.get_item("shear-0-6km")?,), Some(&updates))?
            .unbind(),
    );

    let options = PyDict::new(py);
    options.set_item("stepped", true)?;
    options.set_item("floor", 15)?;
    options.set_item("ticks", PyTuple::new(py, (15u8..=75).step_by(5))?)?;
    options.set_item("display", "fixed")?;
    let gust_palette = palette.call(
        (PyTuple::new(py, gust_stops().collect::<Vec<_>>())?, "mph"),
        Some(&options),
    )?;
    let updates = PyDict::new(py);
    updates.set_item("key", "gust-6h-max")?;
    updates.set_item("label", "6-h Maximum 10 m Wind Gust")?;
    updates.set_item("chip", "6-h Max Gust")?;
    updates.set_item(
        "fields",
        (field_spec.call1(("gust", "sfc", "GUST", "10 m above ground", "max fcst"))?,),
    )?;
    updates.set_item("derive", wrap_pyfunction!(derive_gust_mph, py)?)?;
    updates.set_item("palette", gust_palette)?;
    updates.set_item("max_hours", 6)?;
    updates.set_item(
        "description",
        "Maximum wind gust at 10 m above ground over the preceding six hours, in mph.",
    )?;
    updates.set_item(
        "caveat",
        "Available from F006 when six complete trailing hourly DWD VMAX_10M maxima are published.",
    )?;
    products.push(
        replace
            .call((shared.get_item("gust")?,), Some(&updates))?
            .unbind(),
    );

    Ok(products)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn precipitation_uses_ww_phase_and_liquid_equivalent_inches_per_hour() {
        let rate = 2.54 / 3600.0;
        for (codes, offset) in [
            (
                &[51.0, 53.0, 55.0, 61.0, 63.0, 65.0, 80.0, 81.0, 82.0][..],
                0.0,
            ),
            (&[71.0, 73.0, 75.0, 77.0, 85.0, 86.0][..], 10.0),
            (&[56.0, 57.0, 66.0, 67.0][..], 20.0),
            (&[95.0, 96.0][..], 30.0),
        ] {
            for &ww in codes {
                let encoded = precip_type_rate([rate, ww]);
                assert!((encoded - (offset + 0.1004)).abs() < 1e-9, "WW {ww}");
            }
        }
    }

    #[test]
    fn precipitation_masks_unclassified_weather_and_keeps_heavy_rates_in_their_band() {
        let threshold = 0.01 / KG_M2_S_TO_IN_H;
        for ww in [
            0.0,
            45.0,
            48.0,
            61.5,
            68.0,
            99.0,
            255.0,
            f64::NAN,
            f64::INFINITY,
        ] {
            assert!(precip_type_rate([threshold, ww]).is_nan(), "WW {ww}");
        }
        for rate in [-1.0, 0.0, threshold / 2.0, f64::NAN, f64::INFINITY] {
            assert!(precip_type_rate([rate, 61.0]).is_nan());
            assert!(precip_type_rate([rate, 95.0]).is_nan());
        }
        assert!(precip_type_rate([threshold, 73.0]) >= 10.01);
        assert!(precip_type_rate([1.0, 73.0]) < 20.0);
    }

    #[test]
    fn shear_uses_the_vector_difference_in_knots() {
        assert!((shear_sfc_500_kt([3.0, 4.0, 6.0, 8.0]) - 9.719_222_462).abs() < 1e-9);
        assert_eq!(shear_sfc_500_kt([3.0, 4.0, 3.0, 4.0]), 0.0);
        assert!(shear_sfc_500_kt([3.0, f64::NAN, 6.0, 8.0]).is_nan());
    }

    #[test]
    fn gust_palette_preserves_sampled_mph_bands() {
        let stops: Vec<_> = gust_stops().collect();
        assert_eq!(stops.len(), 63);
        for (mph, colour) in [
            (14, "#ffffff"),
            (15, "#f0f8ff"),
            (16, "#dbf0fe"),
            (20, "#87cefa"),
            (25, "#6a5acd"),
            (30, "#e696dc"),
            (35, "#c85abe"),
            (40, "#a01496"),
            (45, "#c80028"),
            (50, "#dc283c"),
            (55, "#f05050"),
            (60, "#faf064"),
            (65, "#dcbe46"),
            (70, "#be8c28"),
            (75, "#a05a0a"),
            (76, "#a05a0a"),
        ] {
            assert_eq!(stops[usize::from(mph - 14)], (mph, colour));
        }
    }
}

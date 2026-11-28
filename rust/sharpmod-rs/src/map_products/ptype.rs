//! Synoptic precipitation-type composites shared by every map provider.
//!
//! One P-Type colour contract draws the synoptic winter products: four
//! 17-swatch Pivotal-style ramps laid end to end, with liquid-equivalent rate
//! (or reflectivity) inside each band and the dominant precipitation type
//! selecting the band. The thresholds, swatches, encoders, and 1000-500 mb
//! thickness helper live here so the Rust-built catalogues and the contour
//! bridge share one definition.

use super::pressure_to_hpa;
use numpy::{ndarray::IxDyn, IntoPyArray, PyArrayDyn, PyReadonlyArrayDyn, PyUntypedArrayMethods};
use pyo3::{
    exceptions::{PyKeyError, PyValueError},
    prelude::*,
    types::{PyDict, PyTuple},
};

/// The seventeen liquid-equivalent thresholds each ramp shades, in inches per hour.
pub(super) const RATE_THRESHOLDS: [f64; 17] = [
    0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.1, 0.12, 0.15, 0.17, 0.2, 0.25, 0.3, 0.4, 0.5,
];

/// The exact 4 x 17 Pivotal swatches, in Rain, Snow, Freezing Rain, Sleet order.
const GROUP_COLOURS: [[&str; 17]; 4] = [
    [
        "#bae6bd", "#a7d4a9", "#93c493", "#7db280", "#69a16b", "#569056", "#48894d", "#3a8143",
        "#2c7b3a", "#1e7430", "#116d28", "#f4ee70", "#f5db67", "#f8c75c", "#f7b250", "#f89d46",
        "#fa893d",
    ],
    [
        "#badef0", "#a4cee6", "#8bbfda", "#72aed1", "#5a9ec4", "#448fbb", "#3680a9", "#2d7098",
        "#276685", "#185672", "#094760", "#b03390", "#b6459a", "#c26ca8", "#d18abe", "#e2a9d4",
        "#efcde6",
    ],
    [
        "#eec8d9", "#edb7c0", "#eea9a9", "#ec9991", "#ec867b", "#ed7863", "#ed674e", "#eb5536",
        "#e74725", "#df4527", "#d0402b", "#c23c30", "#b43934", "#a53837", "#95363a", "#893440",
        "#793044",
    ],
    [
        "#e0c8eb", "#d7b5e6", "#cca2de", "#c48dd9", "#bc7ad4", "#b46acd", "#a954c8", "#a241c2",
        "#9a37bc", "#8c30a8", "#842c99", "#772787", "#6f2476", "#642262", "#5b2053", "#521e3e",
        "#471d2c",
    ],
];

/// Band the rate encoders place each type in: Rain +0, Snow +10, Frzr +20, Sleet +30.
pub(super) const RATE_OFFSETS: [f64; 4] = [0.0, 10.0, 20.0, 30.0];
/// Band the dBZ encoders place each type in, a stride of 100 so reflectivity never spills.
pub(super) const DBZ_OFFSETS: [f64; 4] = [0.0, 100.0, 200.0, 300.0];
/// Reflectivity stops spanning the composite palette's per-type ramp.
pub(super) const DBZ_STOPS: [f64; 17] = [
    5.0, 7.5, 10.0, 12.5, 15.0, 17.5, 20.0, 22.5, 25.0, 27.5, 30.0, 33.3, 36.7, 40.0, 43.3, 46.7,
    50.0,
];

/// Below this nothing is shaded, matching the ramps' first threshold.
pub(super) const RATE_FLOOR: f64 = 0.01;
/// Largest rate kept inside one band, with the quantization bias still below the next offset.
pub(super) const RATE_CLAMP: f64 = 9.9;
/// Bias exact swatch thresholds above the frame's quantization rounding.
pub(super) const QUANTIZATION_BIAS: f64 = 0.0004;
/// Reflectivity below 5 dBZ is noise; above 80 dBZ is off every published scale.
pub(super) const DBZ_FLOOR: f64 = 5.0;
pub(super) const DBZ_CLAMP: f64 = 80.0;

/// Printed legend ticks shared by the rate ramps.
pub(super) const RATE_TICKS: [f64; 4] = [0.05, 0.1, 0.2, 0.5];
/// Printed legend ticks shared by the reflectivity composite.
pub(super) const DBZ_TICKS: [f64; 5] = [10.0, 20.0, 30.0, 40.0, 50.0];
/// Legend labels for the four bands, in colour order.
pub(crate) const PTYPE_LABELS: [&str; 4] = ["Rain", "Snow", "Frzr", "Sleet"];

fn ramp_stops(offsets: [f64; 4], thresholds: [f64; 17]) -> Vec<(f64, &'static str)> {
    offsets
        .into_iter()
        .zip(GROUP_COLOURS)
        .flat_map(|(offset, colours)| {
            thresholds
                .into_iter()
                .zip(colours)
                .map(move |(threshold, colour)| {
                    // Match Python's round(offset + threshold, 2) band edges.
                    (((offset + threshold) * 100.0).round() / 100.0, colour)
                })
        })
        .collect()
}

fn ptype_palette<'py>(
    catalogue: &Bound<'py, PyModule>,
    stops: Vec<(f64, &'static str)>,
    units: &str,
    floor: f64,
    ticks: &[f64],
    labels: [&str; 4],
) -> PyResult<Bound<'py, PyAny>> {
    let py = catalogue.py();
    let options = PyDict::new(py);
    options.set_item("stepped", true)?;
    options.set_item("floor", floor)?;
    options.set_item("ticks", PyTuple::new(py, ticks.iter().copied())?)?;
    options.set_item("ptype_labels", labels)?;
    catalogue
        .getattr("Palette")?
        .call((PyTuple::new(py, stops)?, units), Some(&options))
}

/// The Rate + P-Type palette: four 17-swatch ramps on one 10-wide stride.
pub(crate) fn rate_palette<'py>(
    catalogue: &Bound<'py, PyModule>,
    units: &str,
    labels: [&str; 4],
) -> PyResult<Bound<'py, PyAny>> {
    ptype_palette(
        catalogue,
        ramp_stops(RATE_OFFSETS, RATE_THRESHOLDS),
        units,
        RATE_FLOOR,
        &RATE_TICKS,
        labels,
    )
}

/// The dBZ Reflectivity + P-Type palette: the same swatches on a 100-wide stride.
pub(super) fn dbz_palette<'py>(
    catalogue: &Bound<'py, PyModule>,
    labels: [&str; 4],
) -> PyResult<Bound<'py, PyAny>> {
    ptype_palette(
        catalogue,
        ramp_stops(DBZ_OFFSETS, DBZ_STOPS),
        "dBZ",
        DBZ_FLOOR,
        &DBZ_TICKS,
        labels,
    )
}

/// Mean-sea-level isobars on the shared contour field, published in pascals.
pub(crate) fn sea_level_contour<'py>(
    catalogue: &Bound<'py, PyModule>,
    interval: f64,
) -> PyResult<Bound<'py, PyAny>> {
    let py = catalogue.py();
    let options = PyDict::new(py);
    options.set_item("convert", wrap_pyfunction!(pressure_to_hpa, py)?)?;
    options.set_item("label", "hPa")?;
    catalogue
        .getattr("ContourSpec")?
        .call(("mslp", interval), Some(&options))
}

/// The dominant NCEP categorical offset: CFRZR, then CICEP, then CSNOW, then CRAIN.
///
/// Rain also claims any precipitating cell no categorical record marks, the
/// same fallback the reflectivity encoder uses.
pub(super) fn categorical_offset(cfrzr: f64, cicep: f64, csnow: f64, _crain: f64) -> f64 {
    if cfrzr > 0.0 {
        RATE_OFFSETS[2]
    } else if cicep > 0.0 {
        RATE_OFFSETS[3]
    } else if csnow > 0.0 {
        RATE_OFFSETS[1]
    } else {
        RATE_OFFSETS[0]
    }
}

/// Encode an instantaneous or averaged liquid-equivalent rate (in/h) in its band.
pub(super) fn encode_ptype_rate(rate_in_h: f64, offset: f64) -> f64 {
    if !rate_in_h.is_finite() || !offset.is_finite() || rate_in_h < RATE_FLOOR {
        return f64::NAN;
    }
    let rate = (rate_in_h * 1000.0).round() / 1000.0;
    if rate < RATE_FLOOR {
        return f64::NAN;
    }
    offset + rate.min(RATE_CLAMP) + QUANTIZATION_BIAS
}

/// Encode dBZ reflectivity in the dominant type's band, 100 per group.
pub(super) fn encode_ptype_dbz(dbz: f64, crain: f64, csnow: f64, cfrzr: f64, cicep: f64) -> f64 {
    if !dbz.is_finite() || dbz < DBZ_FLOOR {
        return f64::NAN;
    }
    let group = categorical_offset(cfrzr, cicep, csnow, crain) as usize / RATE_OFFSETS[1] as usize;
    DBZ_OFFSETS[group] + dbz.min(DBZ_CLAMP)
}

/// 1000-500 mb thickness in decametres. A 1000 mb height below sea level is
/// physical (a deep low) and stays in, matching every published thickness chart.
pub(crate) fn thickness_dam(hgt500: f64, hgt1000: f64) -> f64 {
    if !hgt500.is_finite() || !hgt1000.is_finite() {
        return f64::NAN;
    }
    (hgt500 - hgt1000) / 10.0
}

/// ECMWF WMO Table 4.201 phase code to band offset; unclassified codes stay unshaded.
pub(super) fn ecmwf_ptype_offset(code: f64) -> f64 {
    if !code.is_finite() {
        return f64::NAN;
    }
    match code.round() as i64 {
        1 => RATE_OFFSETS[0],
        5 | 6 => RATE_OFFSETS[1],
        3 | 12 => RATE_OFFSETS[2],
        7 | 8 => RATE_OFFSETS[3],
        _ => f64::NAN,
    }
}

/// ECCC HRDPS PTYPE_Sfc code to band offset; unclassified codes stay unshaded.
pub(super) fn eccc_ptype_offset(code: f64) -> f64 {
    if !code.is_finite() {
        return f64::NAN;
    }
    match code.round() as i64 {
        1 | 7 => RATE_OFFSETS[0],
        5 => RATE_OFFSETS[1],
        3 | 8 => RATE_OFFSETS[2],
        2 | 4 | 9 => RATE_OFFSETS[3],
        _ => f64::NAN,
    }
}

fn grid<'py>(fields: &Bound<'py, PyDict>, name: &str) -> PyResult<(Vec<usize>, Vec<f32>)> {
    let value = fields
        .get_item(name)?
        .ok_or_else(|| PyKeyError::new_err(format!("decoded field {name} is missing")))?;
    let grid = value.extract::<PyReadonlyArrayDyn<'_, f32>>()?;
    Ok((
        grid.shape().to_vec(),
        grid.as_array().iter().copied().collect(),
    ))
}

fn equal_shapes(what: &str, shape: &[usize], others: &[&[usize]]) -> PyResult<()> {
    if others.iter().any(|&other| other != shape) {
        return Err(PyValueError::new_err(format!(
            "{what} grids must have equal dimensions"
        )));
    }
    Ok(())
}

fn output<'py>(py: Python<'py>, shape: &[usize], values: Vec<f64>) -> Bound<'py, PyArrayDyn<f32>> {
    let values: Vec<f32> = py.detach(|| values.into_iter().map(|value| value as f32).collect());
    numpy::ndarray::ArrayD::from_shape_vec(IxDyn(shape), values)
        .expect("provider grids keep their validated dimensions")
        .into_pyarray(py)
}

/// NCEP PRATE is published in kg m-2 s-1; shade the dominant type's rate in in/h.
#[pyfunction]
fn derive_gfs_prate_ptype<'py>(
    py: Python<'py>,
    fields: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyArrayDyn<f32>>> {
    let (shape, rate) = grid(fields, "prate")?;
    let (crain_shape, crain) = grid(fields, "crain")?;
    let (csnow_shape, csnow) = grid(fields, "csnow")?;
    let (cfrzr_shape, cfrzr) = grid(fields, "cfrzr")?;
    let (cicep_shape, cicep) = grid(fields, "cicep")?;
    equal_shapes(
        "GFS precipitation type",
        &shape,
        &[&crain_shape, &csnow_shape, &cfrzr_shape, &cicep_shape],
    )?;
    let cells = rate.len();
    let values: Vec<f64> = py.detach(|| {
        (0..cells)
            .map(|cell| {
                encode_ptype_rate(
                    f64::from(rate[cell]) * 3600.0 / 25.4,
                    categorical_offset(
                        f64::from(cfrzr[cell]),
                        f64::from(cicep[cell]),
                        f64::from(csnow[cell]),
                        f64::from(crain[cell]),
                    ),
                )
            })
            .collect()
    });
    Ok(output(py, &shape, values))
}

/// ECMWF publishes tprate in kg m-2 s-1 with a single WMO Table 4.201 phase code.
#[pyfunction]
fn derive_ecmwf_prate_ptype<'py>(
    py: Python<'py>,
    fields: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyArrayDyn<f32>>> {
    let (shape, rate) = grid(fields, "prate")?;
    let (ptype_shape, ptype) = grid(fields, "ptype")?;
    equal_shapes("ECMWF precipitation type", &shape, &[&ptype_shape])?;
    let cells = rate.len();
    let values: Vec<f64> = py.detach(|| {
        (0..cells)
            .map(|cell| {
                encode_ptype_rate(
                    f64::from(rate[cell]) * 3600.0 / 25.4,
                    ecmwf_ptype_offset(f64::from(ptype[cell])),
                )
            })
            .collect()
    });
    Ok(output(py, &shape, values))
}

/// REFS members publish the trailing hour's APCP in millimetres with the
/// NCEP categorical flags; one hour of accumulation is that rate in in/h.
#[pyfunction]
fn derive_refs_member_qpf_ptype<'py>(
    py: Python<'py>,
    fields: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyArrayDyn<f32>>> {
    let (shape, apcp) = grid(fields, "apcp")?;
    let (crain_shape, crain) = grid(fields, "crain")?;
    let (csnow_shape, csnow) = grid(fields, "csnow")?;
    let (cfrzr_shape, cfrzr) = grid(fields, "cfrzr")?;
    let (cicep_shape, cicep) = grid(fields, "cicep")?;
    equal_shapes(
        "REFS member precipitation type",
        &shape,
        &[&crain_shape, &csnow_shape, &cfrzr_shape, &cicep_shape],
    )?;
    let cells = apcp.len();
    let values: Vec<f64> = py.detach(|| {
        (0..cells)
            .map(|cell| {
                encode_ptype_rate(
                    f64::from(apcp[cell]) / 25.4,
                    categorical_offset(
                        f64::from(cfrzr[cell]),
                        f64::from(cicep[cell]),
                        f64::from(csnow[cell]),
                        f64::from(crain[cell]),
                    ),
                )
            })
            .collect()
    });
    Ok(output(py, &shape, values))
}

/// HRRR 1 km AGL reflectivity with the categorical flags on a 100-per-type stride.
#[pyfunction]
fn derive_hrrr_refd_ptype<'py>(
    py: Python<'py>,
    fields: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyArrayDyn<f32>>> {
    let (shape, dbz) = grid(fields, "refd1km")?;
    let (crain_shape, crain) = grid(fields, "crain")?;
    let (csnow_shape, csnow) = grid(fields, "csnow")?;
    let (cfrzr_shape, cfrzr) = grid(fields, "cfrzr")?;
    let (cicep_shape, cicep) = grid(fields, "cicep")?;
    equal_shapes(
        "HRRR reflectivity type",
        &shape,
        &[&crain_shape, &csnow_shape, &cfrzr_shape, &cicep_shape],
    )?;
    let cells = dbz.len();
    let values: Vec<f64> = py.detach(|| {
        (0..cells)
            .map(|cell| {
                encode_ptype_dbz(
                    f64::from(dbz[cell]),
                    f64::from(crain[cell]),
                    f64::from(csnow[cell]),
                    f64::from(cfrzr[cell]),
                    f64::from(cicep[cell]),
                )
            })
            .collect()
    });
    Ok(output(py, &shape, values))
}

/// The type with the largest average rate. Types whose accumulation is missing
/// or below the shading floor are not candidates; a tie keeps the earlier
/// (rain, then snow, then freezing rain) group.
fn dominant_type(rates: [f64; 4]) -> Option<usize> {
    let mut best = None;
    for (index, &value) in rates.iter().enumerate() {
        if !value.is_finite() || value < RATE_FLOOR {
            continue;
        }
        if best.is_none_or(|current| value > rates[current]) {
            best = Some(index);
        }
    }
    best
}

/// MSC publishes each type's trailing 6-hour accumulation in millimetres.
/// The dominant type is the largest of the four converted average rates.
#[pyfunction]
fn derive_gdps_prate_ptype<'py>(
    py: Python<'py>,
    fields: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyArrayDyn<f32>>> {
    let (shape, rain) = grid(fields, "rain6h")?;
    let (snow_shape, snow) = grid(fields, "snow6h")?;
    let (frzr_shape, frzr) = grid(fields, "frzr6h")?;
    let (sleet_shape, sleet) = grid(fields, "sleet6h")?;
    equal_shapes(
        "GDPS 6-h accumulations",
        &shape,
        &[&snow_shape, &frzr_shape, &sleet_shape],
    )?;
    let cells = rain.len();
    let values: Vec<f64> = py.detach(|| {
        (0..cells)
            .map(|cell| {
                let rates = [
                    f64::from(rain[cell]) / (6.0 * 25.4),
                    f64::from(snow[cell]) / (6.0 * 25.4),
                    f64::from(frzr[cell]) / (6.0 * 25.4),
                    f64::from(sleet[cell]) / (6.0 * 25.4),
                ];
                dominant_type(rates)
                    .map(|group| encode_ptype_rate(rates[group], RATE_OFFSETS[group]))
                    .unwrap_or(f64::NAN)
            })
            .collect()
    });
    Ok(output(py, &shape, values))
}

/// One `prate-ptype` product on GFS or the shared ECMWF decoding.
fn prate_ptype_product(py: Python<'_>, model: &str) -> PyResult<Py<PyAny>> {
    let catalogue = PyModule::import(py, "sharpmod.providers.hrrr_products")?;
    let field_spec = catalogue.getattr("FieldSpec")?;
    let is_gfs = model == "gfs";
    let fields: Vec<Bound<'_, PyAny>> = if is_gfs {
        vec![
            catalogue.getattr("F_PRATE")?,
            field_spec.call1(("crain", "sfc", "CRAIN", "surface"))?,
            field_spec.call1(("csnow", "sfc", "CSNOW", "surface"))?,
            field_spec.call1(("cfrzr", "sfc", "CFRZR", "surface"))?,
            field_spec.call1(("cicep", "sfc", "CICEP", "surface"))?,
        ]
    } else {
        // ECMWF publishes its phase code as tprate/ptype on the surface file.
        vec![
            field_spec.call1(("prate", "sfc", "PRATE", "surface"))?,
            field_spec.call1(("ptype", "sfc", "ptype", "surface"))?,
        ]
    };
    let derive = if is_gfs {
        wrap_pyfunction!(derive_gfs_prate_ptype, py)?
    } else {
        wrap_pyfunction!(derive_ecmwf_prate_ptype, py)?
    };
    let options = PyDict::new(py);
    options.set_item("key", "prate-ptype")?;
    options.set_item("label", "Precipitation Type, Rate, 1000-500 mb Thickness")?;
    options.set_item("chip", "P-Type / Rate + Thick")?;
    options.set_item("category", "Precipitation")?;
    options.set_item("fields", PyTuple::new(py, fields)?)?;
    options.set_item("derive", derive)?;
    options.set_item("palette", rate_palette(&catalogue, "in/h", PTYPE_LABELS)?)?;
    options.set_item("contour", sea_level_contour(&catalogue, 2.0)?)?;
    options.set_item(
        "contour_fields",
        (
            field_spec.call1(("mslp", "sfc", "PRMSL", "mean sea level"))?,
            field_spec.call1(("hgt500", "prs", "HGT", "500 mb"))?,
            field_spec.call1(("hgt1000", "prs", "HGT", "1000 mb"))?,
        ),
    )?;
    options.set_item("thickness_interval", 6.0)?;
    options.set_item("max_hours", if is_gfs { 1u32 } else { 3u32 })?;
    options.set_item("description", "Instantaneous liquid-equivalent precipitation rate in inches per hour, shaded by the dominant precipitation type, with 2 hPa mean sea-level isobars and 1000-500 mb thickness lines.")?;
    options.set_item(
        "caveat",
        if is_gfs {
            "Uses GFS PRATE with its CRAIN/CSNOW/CFRZR/CICEP flags (freezing rain, then ice pellets, then snow outrank rain) and native HGT at 1000 and 500 mb for the thickness. Cells under 0.01 in/h are not shaded. Available from F001."
        } else {
            "Uses ECMWF tprate with the WMO Table 4.201 ptype (rain, snow, freezing rain, or ice pellets) and native geopotential height at 1000 and 500 mb for the thickness. Cells under 0.01 in/h are not shaded. Available from F003."
        },
    )?;
    Ok(catalogue
        .getattr("HrrrProduct")?
        .call((), Some(&options))?
        .unbind())
}

/// The HRRR composite's locator chip, held within the twelve ASCII characters
/// the Qt inset test allows; the label and parameter list keep the full name.
const HRRR_REFD_PTYPE_CHIP: &str = "1km P-Type";

/// HRRR 1 km AGL reflectivity with categorical type, isobars, and thickness.
pub(super) fn hrrr_refd_ptype_product(py: Python<'_>) -> PyResult<Py<PyAny>> {
    let catalogue = PyModule::import(py, "sharpmod.providers.hrrr_products")?;
    let field_spec = catalogue.getattr("FieldSpec")?;
    let options = PyDict::new(py);
    options.set_item("key", "refd-1km-ptype")?;
    options.set_item(
        "label",
        "1 km AGL Reflectivity, Type, 1000-500 mb Thickness",
    )?;
    options.set_item("chip", HRRR_REFD_PTYPE_CHIP)?;
    options.set_item("category", "Precipitation")?;
    options.set_item(
        "fields",
        (
            field_spec.call1(("refd1km", "sfc", "REFD", "1000 m above ground"))?,
            field_spec.call1(("crain", "sfc", "CRAIN", "surface"))?,
            field_spec.call1(("csnow", "sfc", "CSNOW", "surface"))?,
            field_spec.call1(("cfrzr", "sfc", "CFRZR", "surface"))?,
            field_spec.call1(("cicep", "sfc", "CICEP", "surface"))?,
        ),
    )?;
    options.set_item("derive", wrap_pyfunction!(derive_hrrr_refd_ptype, py)?)?;
    options.set_item("palette", dbz_palette(&catalogue, PTYPE_LABELS)?)?;
    options.set_item("contour", sea_level_contour(&catalogue, 2.0)?)?;
    options.set_item(
        "contour_fields",
        (
            field_spec.call1(("mslp", "sfc", "MSLMA", "mean sea level"))?,
            field_spec.call1(("hgt500", "sfc", "HGT", "500 mb"))?,
            field_spec.call1(("hgt1000", "sfc", "HGT", "1000 mb"))?,
        ),
    )?;
    options.set_item("thickness_interval", 6.0)?;
    options.set_item("max_hours", 1u32)?;
    options.set_item("description", "Instantaneous simulated reflectivity 1 km above ground, shaded by the dominant precipitation type, with 2 hPa mean sea-level isobars and 1000-500 mb thickness lines.")?;
    options.set_item("caveat", "Uses HRRR REFD:1000 m above ground with its CRAIN/CSNOW/CFRZR/CICEP flags (freezing rain, then ice pellets, then snow outrank rain) and native HGT at 1000 and 500 mb for the thickness. Reflectivity under 5 dBZ is not shaded. Available from F001.")?;
    Ok(catalogue
        .getattr("HrrrProduct")?
        .call((), Some(&options))?
        .unbind())
}

/// One REFS reduced member's 1-h QPF rate with its dominant categorical type.
pub(super) fn refs_member_qpf_ptype_product(py: Python<'_>) -> PyResult<Py<PyAny>> {
    let catalogue = PyModule::import(py, "sharpmod.providers.hrrr_products")?;
    let field_spec = catalogue.getattr("FieldSpec")?;
    let options = PyDict::new(py);
    options.set_item("key", "qpf-1h-ptype")?;
    options.set_item(
        "label",
        "1-h QPF Rate, Precipitation Type, 1000-500 mb Thickness",
    )?;
    options.set_item("chip", "1h QPF / P-Type + Thick")?;
    options.set_item("category", "Precipitation")?;
    options.set_item(
        "fields",
        (
            field_spec.call1(("apcp", "sfc", "APCP", "surface"))?,
            field_spec.call1(("crain", "sfc", "CRAIN", "surface"))?,
            field_spec.call1(("csnow", "sfc", "CSNOW", "surface"))?,
            field_spec.call1(("cfrzr", "sfc", "CFRZR", "surface"))?,
            field_spec.call1(("cicep", "sfc", "CICEP", "surface"))?,
        ),
    )?;
    options.set_item(
        "derive",
        wrap_pyfunction!(derive_refs_member_qpf_ptype, py)?,
    )?;
    options.set_item("palette", rate_palette(&catalogue, "in/h", PTYPE_LABELS)?)?;
    options.set_item("contour", sea_level_contour(&catalogue, 2.0)?)?;
    options.set_item(
        "contour_fields",
        (
            field_spec.call1(("mslp", "sfc", "MSLET", "mean sea level"))?,
            field_spec.call1(("hgt500", "prs", "HGT", "500 mb"))?,
            field_spec.call1(("hgt1000", "prs", "HGT", "1000 mb"))?,
        ),
    )?;
    options.set_item("thickness_interval", 6.0)?;
    options.set_item("max_hours", 1u32)?;
    options.set_item("description", "Average liquid-equivalent precipitation rate in inches per hour over the hour ending at the selected forecast time, shaded by the dominant precipitation type, with 2 hPa mean sea-level isobars and 1000-500 mb thickness lines.")?;
    options.set_item("caveat", "Uses the member's native 1-hour APCP accumulation with its CRAIN/CSNOW/CFRZR/CICEP flags (freezing rain, then ice pellets, then snow outrank rain); MSLET supplies the isobars and native HGT at 1000 and 500 mb the thickness. Available from F001.")?;
    Ok(catalogue
        .getattr("HrrrProduct")?
        .call((), Some(&options))?
        .unbind())
}

/// MSC GDPS/RDPS 6-hour average rate from each type's published accumulation.
pub(super) fn gdps_prate_ptype_product(py: Python<'_>) -> PyResult<Py<PyAny>> {
    let catalogue = PyModule::import(py, "sharpmod.providers.hrrr_products")?;
    let field_spec = catalogue.getattr("FieldSpec")?;
    let options = PyDict::new(py);
    options.set_item("key", "prate-ptype")?;
    options.set_item(
        "label",
        "Precipitation Type, 6-h Avg Rate, 1000-500 mb Thickness",
    )?;
    options.set_item("chip", "P-Type / 6h Rate + Thick")?;
    options.set_item("category", "Precipitation")?;
    options.set_item(
        "fields",
        (
            field_spec.call1(("rain6h", "sfc", "Rain-Accum6h", "surface"))?,
            field_spec.call1(("snow6h", "sfc", "Snow-Accum6h", "surface"))?,
            field_spec.call1(("frzr6h", "sfc", "FreezingRain-Accum6h", "surface"))?,
            field_spec.call1(("sleet6h", "sfc", "IcePellets-Accum6h", "surface"))?,
        ),
    )?;
    options.set_item("derive", wrap_pyfunction!(derive_gdps_prate_ptype, py)?)?;
    options.set_item("palette", rate_palette(&catalogue, "in/h", PTYPE_LABELS)?)?;
    options.set_item("contour", sea_level_contour(&catalogue, 2.0)?)?;
    options.set_item(
        "contour_fields",
        (
            field_spec.call1(("mslp", "sfc", "Pressure", "mean sea level"))?,
            field_spec.call1(("hgt500", "prs", "HGT", "500 mb"))?,
            field_spec.call1(("hgt1000", "prs", "HGT", "1000 mb"))?,
        ),
    )?;
    options.set_item("thickness_interval", 6.0)?;
    options.set_item("max_hours", 6u32)?;
    options.set_item("description", "Average liquid-equivalent precipitation rate in inches per hour over the six hours ending at the selected forecast time, shaded by the dominant precipitation type, with 2 hPa mean sea-level isobars and 1000-500 mb thickness lines.")?;
    options.set_item("caveat", "Uses MSC's 6-hour Rain/Snow/FreezingRain/IcePellets accumulations on the surface file (native 6-hour rolling files at 6-hour multiples, and 6-hour differences of run-total accumulations on intermediate hours); the type with the largest accumulation is dominant, and Pressure_MSL plus HGT at 500 and 1000 mb supply the overlays. Available from F006.")?;
    Ok(catalogue
        .getattr("HrrrProduct")?
        .call((), Some(&options))?
        .unbind())
}

/// Append the synoptic precipitation-type composites this model publishes.
pub(super) fn extend_catalogue(
    py: Python<'_>,
    model: &str,
    products: &mut Vec<Py<PyAny>>,
) -> PyResult<()> {
    match model {
        "gfs" | "openmeteo-ecmwf-ifs025" | "ecmwf-ifs-9km" => {
            products.push(prate_ptype_product(py, model)?);
        }
        _ => {}
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn precipitation_type_products_start_at_their_first_supported_positive_hour() {
        Python::initialize();
        Python::attach(|py| -> PyResult<()> {
            let catalogue = PyModule::import(py, "sharpmod.providers.hrrr_products")?;
            let mut checked = 0;
            for model in catalogue
                .getattr("FIELD_MODELS")?
                .extract::<Vec<String>>()?
            {
                let expected = match model.as_str() {
                    "hrrr" | "nssl-mpas-htpo" | "nssl-mpas-rn" | "gfs" | "refs" | "refs-m1"
                    | "refs-m2" | "refs-m3" | "refs-m4" | "refs-m5" => 1,
                    "ecmwf-ifs-9km" | "openmeteo-ecmwf-ifs025" | "openmeteo-icon-global" => 3,
                    "gdps" | "rdps" | "hrdps" => 6,
                    _ => 0,
                };
                let mut found = false;
                for product in catalogue
                    .call_method1("available_products", (&model,))?
                    .try_iter()?
                {
                    let product = product?;
                    if product
                        .getattr("palette")?
                        .getattr("ptype_labels")?
                        .is_none()
                    {
                        continue;
                    }
                    let key = product.getattr("key")?.extract::<String>()?;
                    assert!(key.ends_with("-ptype"), "{model} {key}");
                    let hour = product.getattr("max_hours")?.extract::<u32>()?;
                    assert!(expected > 0, "{model} {key}");
                    assert_eq!(hour, expected, "{model} {key}");
                    checked += 1;
                    found = true;
                }
                assert_eq!(found, expected != 0, "{model}");
            }
            assert_eq!(checked, 16);
            Ok(())
        })
        .unwrap();
    }

    #[test]
    fn rate_ramps_stay_on_their_exact_pivotal_swatches() {
        let stops = ramp_stops(RATE_OFFSETS, RATE_THRESHOLDS);
        assert_eq!(stops.len(), 68);
        for (index, expected) in [
            (0, (0.01, "#bae6bd")),
            (11, (0.17, "#f4ee70")),
            (16, (0.5, "#fa893d")),
            (17, (10.01, "#badef0")),
            (33, (10.5, "#efcde6")),
            (34, (20.01, "#eec8d9")),
            (40, (20.07, "#ed674e")),
            (41, (20.08, "#eb5536")),
            (51, (30.01, "#e0c8eb")),
            (57, (30.07, "#a954c8")),
            (58, (30.08, "#a241c2")),
            (67, (30.5, "#471d2c")),
        ] {
            assert_eq!(stops[index], expected);
        }
        assert!(stops.windows(2).all(|pair| pair[0].0 < pair[1].0));
        for tick in RATE_TICKS {
            assert!(stops.iter().any(|&(value, _)| value == tick));
        }
    }

    #[test]
    fn dbz_ramps_use_the_same_swatches_on_a_100_stride() {
        let stops = ramp_stops(DBZ_OFFSETS, DBZ_STOPS);
        assert_eq!(stops.len(), 68);
        assert_eq!(stops[0], (5.0, "#bae6bd"));
        assert_eq!(stops[11], (33.3, "#f4ee70"));
        assert_eq!(stops[17], (105.0, "#badef0"));
        assert_eq!(stops[34], (205.0, "#eec8d9"));
        assert_eq!(stops[51], (305.0, "#e0c8eb"));
        assert_eq!(stops[67], (350.0, "#471d2c"));
        assert!(stops.windows(2).all(|pair| pair[0].0 < pair[1].0));
        for tick in DBZ_TICKS {
            assert!(stops.iter().any(|&(value, _)| value == tick));
        }
    }

    #[test]
    fn the_refd_ptype_chip_fits_the_locator_inset() {
        assert!((1..=12).contains(&HRRR_REFD_PTYPE_CHIP.len()));
        assert!(HRRR_REFD_PTYPE_CHIP.is_ascii());
    }

    #[test]
    fn encodes_rate_inside_each_band_with_the_shared_floor_and_clamp() {
        for (offset, rate, expected) in [
            (0.0, 0.01, 0.0104),
            (10.0, 0.1, 10.1004),
            (20.0, 0.25, 20.2504),
            (30.0, 0.5, 30.5004),
            (10.0, 9.9, 19.9004),
        ] {
            assert!((encode_ptype_rate(rate, offset) - expected).abs() < 1e-12);
        }
        // Heavy rates clamp inside their own group, never spilling into the next.
        assert!((encode_ptype_rate(141.7, 10.0) - 19.9004).abs() < 1e-12);
        assert!((encode_ptype_rate(9.0, 0.0) - 9.0004).abs() < 1e-12);
        assert!((encode_ptype_rate(19.0, 0.0) - 9.9004).abs() < 1e-12);
        for missing_rate in [-1.0, 0.0, 0.009, f64::NAN, f64::INFINITY] {
            assert!(encode_ptype_rate(missing_rate, 10.0).is_nan());
        }
        for bad_offset in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            assert!(encode_ptype_rate(0.1, bad_offset).is_nan());
        }
        // Rounding happens before the floor, matching the published encoders.
        assert!((encode_ptype_rate(0.010_049, 0.0) - 0.0104).abs() < 1e-12);
    }

    #[test]
    fn categorical_flags_follow_freezing_rain_ice_pellets_snow_rain_priority() {
        assert_eq!(categorical_offset(0.0, 0.0, 0.0, 1.0), RATE_OFFSETS[0]);
        assert_eq!(categorical_offset(0.0, 0.0, 1.0, 0.0), RATE_OFFSETS[1]);
        assert_eq!(categorical_offset(1.0, 0.0, 0.0, 0.0), RATE_OFFSETS[2]);
        assert_eq!(categorical_offset(0.0, 1.0, 0.0, 0.0), RATE_OFFSETS[3]);
        assert_eq!(categorical_offset(1.0, 1.0, 1.0, 1.0), RATE_OFFSETS[2]);
        assert_eq!(categorical_offset(0.0, 1.0, 1.0, 1.0), RATE_OFFSETS[3]);
        assert_eq!(categorical_offset(0.0, 0.0, 0.0, 0.0), RATE_OFFSETS[0]);
    }

    #[test]
    fn encodes_dbz_with_the_dominant_type_clamps_and_rain_fallback() {
        assert_eq!(encode_ptype_dbz(30.0, 1.0, 0.0, 0.0, 0.0), 30.0);
        assert_eq!(encode_ptype_dbz(30.0, 0.0, 1.0, 0.0, 0.0), 130.0);
        assert_eq!(encode_ptype_dbz(30.0, 0.0, 0.0, 1.0, 0.0), 230.0);
        assert_eq!(encode_ptype_dbz(30.0, 0.0, 0.0, 0.0, 1.0), 330.0);
        assert_eq!(encode_ptype_dbz(30.0, 1.0, 1.0, 1.0, 1.0), 230.0);
        // All flags zero still shades reflectivity as rain.
        assert_eq!(encode_ptype_dbz(55.0, 0.0, 0.0, 0.0, 0.0), 55.0);
        assert_eq!(encode_ptype_dbz(90.0, 1.0, 0.0, 0.0, 0.0), 80.0);
        for missing in [4.9, 0.0, -20.0, f64::NAN, f64::INFINITY] {
            assert!(encode_ptype_dbz(missing, 1.0, 0.0, 0.0, 0.0).is_nan());
        }
    }

    #[test]
    fn ecmwf_and_eccc_phase_codes_map_to_their_bands() {
        assert_eq!(ecmwf_ptype_offset(1.0), RATE_OFFSETS[0]);
        assert_eq!(ecmwf_ptype_offset(5.0), RATE_OFFSETS[1]);
        assert_eq!(ecmwf_ptype_offset(6.0), RATE_OFFSETS[1]);
        assert_eq!(ecmwf_ptype_offset(3.0), RATE_OFFSETS[2]);
        assert_eq!(ecmwf_ptype_offset(12.0), RATE_OFFSETS[2]);
        assert_eq!(ecmwf_ptype_offset(7.0), RATE_OFFSETS[3]);
        assert_eq!(ecmwf_ptype_offset(8.0), RATE_OFFSETS[3]);
        for unclassified in [0.0, 2.0, 4.0, 255.0, f64::NAN] {
            assert!(ecmwf_ptype_offset(unclassified).is_nan());
        }
        assert_eq!(eccc_ptype_offset(1.0), RATE_OFFSETS[0]);
        assert_eq!(eccc_ptype_offset(7.0), RATE_OFFSETS[0]);
        assert_eq!(eccc_ptype_offset(5.0), RATE_OFFSETS[1]);
        assert_eq!(eccc_ptype_offset(3.0), RATE_OFFSETS[2]);
        assert_eq!(eccc_ptype_offset(8.0), RATE_OFFSETS[2]);
        assert_eq!(eccc_ptype_offset(2.0), RATE_OFFSETS[3]);
        assert_eq!(eccc_ptype_offset(4.0), RATE_OFFSETS[3]);
        assert_eq!(eccc_ptype_offset(9.0), RATE_OFFSETS[3]);
        for unclassified in [0.0, 6.0, f64::NAN] {
            assert!(eccc_ptype_offset(unclassified).is_nan());
        }
    }

    #[test]
    fn gdps_dominant_type_prefers_the_largest_and_keeps_the_earlier_on_ties() {
        assert_eq!(dominant_type([0.5, 0.1, 0.0, 0.0]), Some(0));
        assert_eq!(dominant_type([0.1, 0.5, 0.0, 0.0]), Some(1));
        assert_eq!(dominant_type([0.0, 0.1, 0.5, 0.0]), Some(2));
        assert_eq!(dominant_type([0.0, 0.0, 0.1, 0.5]), Some(3));
        // Rain keeps a tie against snow; snow keeps one against freezing rain.
        assert_eq!(dominant_type([0.5, 0.5, 0.0, 0.0]), Some(0));
        assert_eq!(dominant_type([0.0, 0.5, 0.5, 0.0]), Some(1));
        // Missing or unshaded accumulations are not candidates at all.
        assert_eq!(dominant_type([f64::NAN, 0.1, 0.0, f64::NAN]), Some(1));
        assert_eq!(dominant_type([0.0, 0.0, 0.0, 0.0]), None);
        assert_eq!(dominant_type([f64::NAN; 4]), None);
        assert_eq!(dominant_type([0.004, -1.0, 0.0, 0.0]), None);
    }

    #[test]
    fn thickness_accepts_below_sea_level_and_missing_heights() {
        assert_eq!(thickness_dam(5400.0, 0.0), 540.0);
        assert!((thickness_dam(5400.0, -150.0) - 555.0).abs() < 1e-9);
        assert!(thickness_dam(f64::NAN, 0.0).is_nan());
        assert!(thickness_dam(5400.0, f64::NAN).is_nan());
        assert!(thickness_dam(f64::INFINITY, 0.0).is_nan());
    }
}

//! ECCC HRDPS continental 2.5 km map product catalogue and native derivations.

use super::{
    derive_refs_member_ehi_0_3km, derive_refs_member_lcl,
    palettes::{lcl_stops, LCL_TICKS},
    pressure_product, ptype,
};
use numpy::{
    ndarray::{ArrayD, IxDyn},
    IntoPyArray, PyArrayDyn, PyReadonlyArrayDyn, PyUntypedArrayMethods,
};
use pyo3::{
    exceptions::{PyKeyError, PyValueError},
    prelude::*,
    types::{PyDict, PyTuple},
};

const MS_TO_KT: f32 = 1.943_844_5;
const SWE_MM_TO_SNOW_10_1_IN: f32 = 10.0 / 25.4;

fn extract_f32_grid(fields: &Bound<'_, PyDict>, name: &str) -> PyResult<(Vec<usize>, Vec<f32>)> {
    let value = fields
        .get_item(name)?
        .ok_or_else(|| PyKeyError::new_err(format!("decoded field {name} is missing")))?;
    let grid = value.extract::<PyReadonlyArrayDyn<'_, f32>>()?;
    Ok((
        grid.shape().to_vec(),
        grid.as_array().iter().copied().collect(),
    ))
}

/// Encode HRDPS's rolling 6-h accumulation as an average liquid-equivalent
/// rate (in/h) with the ECCC PTYPE_Sfc offsets: Rain (+0), Snow (+10),
/// Freezing Rain (+20), Sleet (+30).
fn encode_hrdps_prate_ptype(accum_mm: &[f32], ptype: &[f32]) -> Vec<f32> {
    accum_mm
        .iter()
        .zip(ptype)
        .map(|(&accum, &code)| {
            if !accum.is_finite() || !code.is_finite() {
                return f32::NAN;
            }
            let rate_in_h = f64::from(accum.max(0.0)) / (6.0 * 25.4);
            ptype::encode_ptype_rate(rate_in_h, ptype::eccc_ptype_offset(f64::from(code))) as f32
        })
        .collect()
}

fn snow_10_1_inches(weasn: &[f32]) -> Vec<f32> {
    weasn
        .iter()
        .map(|&swe_mm| {
            if swe_mm.is_finite() {
                swe_mm.max(0.0) * SWE_MM_TO_SNOW_10_1_IN
            } else {
                f32::NAN
            }
        })
        .collect()
}

fn bulk_shear_kt(u_low: &[f32], v_low: &[f32], u_high: &[f32], v_high: &[f32]) -> Vec<f32> {
    u_low
        .iter()
        .zip(v_low)
        .zip(u_high.iter().zip(v_high))
        .map(|((&u0, &v0), (&u1, &v1))| {
            if u0.is_finite() && v0.is_finite() && u1.is_finite() && v1.is_finite() {
                (u1 - u0).hypot(v1 - v0) * MS_TO_KT
            } else {
                f32::NAN
            }
        })
        .collect()
}

#[pyfunction]
fn derive_hrdps_prate_ptype<'py>(
    py: Python<'py>,
    fields: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyArrayDyn<f32>>> {
    let (shape, accum) = extract_f32_grid(fields, "qpf6h")?;
    let (ptype_shape, ptype) = extract_f32_grid(fields, "ptype")?;
    if ptype_shape != shape {
        return Err(PyValueError::new_err(
            "HRDPS accumulation and type grids must have equal dimensions",
        ));
    }
    let values = py.detach(|| encode_hrdps_prate_ptype(&accum, &ptype));
    Ok(ArrayD::from_shape_vec(IxDyn(&shape), values)
        .expect("preserved HRDPS precipitation grid dimensions")
        .into_pyarray(py))
}

#[pyfunction]
fn derive_hrdps_snow_10_1<'py>(
    py: Python<'py>,
    fields: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyArrayDyn<f32>>> {
    let (shape, weasn) = extract_f32_grid(fields, "weasn")?;
    let values = py.detach(|| snow_10_1_inches(&weasn));
    Ok(ArrayD::from_shape_vec(IxDyn(&shape), values)
        .expect("preserved HRDPS snow grid dimensions")
        .into_pyarray(py))
}

#[pyfunction]
fn derive_hrdps_shear_sfc_500<'py>(
    py: Python<'py>,
    fields: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyArrayDyn<f32>>> {
    let (shape, u10) = extract_f32_grid(fields, "u10")?;
    let (v10_shape, v10) = extract_f32_grid(fields, "v10")?;
    let (u500_shape, u500) = extract_f32_grid(fields, "ugrd500")?;
    let (v500_shape, v500) = extract_f32_grid(fields, "vgrd500")?;
    if v10_shape != shape || u500_shape != shape || v500_shape != shape {
        return Err(PyValueError::new_err(
            "HRDPS shear grids must have equal dimensions",
        ));
    }
    let values = py.detach(|| bulk_shear_kt(&u10, &v10, &u500, &v500));
    Ok(ArrayD::from_shape_vec(IxDyn(&shape), values)
        .expect("preserved HRDPS shear dimensions")
        .into_pyarray(py))
}

pub(crate) fn catalogue(py: Python<'_>) -> PyResult<Vec<Py<PyAny>>> {
    let source = PyModule::import(py, "sharpmod.providers.hrrr_products")?;
    let shared = source.getattr("PRODUCTS")?;
    let field_spec = source.getattr("FieldSpec")?;
    let replace = PyModule::import(py, "dataclasses")?.getattr("replace")?;
    let single = source.getattr("_single")?;
    let mm_to_in = source.getattr("mm_to_in")?;

    let mut products = Vec::with_capacity(crate::gdps_fields::HRDPS_PRODUCTS.len());
    for &key in crate::gdps_fields::HRDPS_PRODUCTS {
        let entry = match key {
            "hgt-wind-500" | "hgt-wind-700" | "hgt-wind-850" | "tmp-2m" | "dpt-2m"
            | "thetae-2m" | "rh-2m" | "lapse-700-500" => shared.get_item(key)?.unbind(),
            "gust" => {
                let template = shared.get_item("gust")?;
                let updates = PyDict::new(py);
                updates.set_item("label", "10 m AGL Wind Gusts")?;
                updates.set_item("chip", "10 m Gust")?;
                updates.set_item(
                    "description",
                    "Instantaneous 10 m AGL wind gust potential published by HRDPS, in knots.",
                )?;
                replace.call((template,), Some(&updates))?.unbind()
            }
            "prate-ptype" => {
                let ptype_palette = ptype::rate_palette(&source, "in/h", ptype::PTYPE_LABELS)?;
                // APCP-Accum6h is the average rate source; PTYPE_Sfc picks the band.
                let ptype_field = field_spec.call1(("ptype", "sfc", "PTYPE", "surface"))?;
                let qpf6h = field_spec.call1(("qpf6h", "sfc", "APCP-Accum6h", "surface"))?;
                let mslp = field_spec.call1(("mslp", "sfc", "PRMSL", "mean sea level"))?;
                let hgt500 = field_spec.call1(("hgt500", "prs", "HGT", "500 mb"))?;
                let hgt1000 = field_spec.call1(("hgt1000", "prs", "HGT", "1000 mb"))?;
                let kwargs = PyDict::new(py);
                kwargs.set_item("key", "prate-ptype")?;
                kwargs.set_item(
                    "label",
                    "Precipitation Type, 6-h Avg Rate, 1000-500 mb Thickness",
                )?;
                kwargs.set_item("chip", "P-Type / 6h Rate + Thick")?;
                kwargs.set_item("category", "Precipitation")?;
                kwargs.set_item("fields", (qpf6h, ptype_field))?;
                kwargs.set_item("derive", wrap_pyfunction!(derive_hrdps_prate_ptype, py)?)?;
                kwargs.set_item("palette", ptype_palette)?;
                kwargs.set_item("max_hours", 6u32)?;
                kwargs.set_item("contour", ptype::sea_level_contour(&source, 2.0)?)?;
                kwargs.set_item("contour_fields", (mslp, hgt500, hgt1000))?;
                kwargs.set_item("thickness_interval", 6.0)?;
                kwargs.set_item(
                    "description",
                    "Average liquid-equivalent precipitation rate in inches per hour over the six hours ending at the selected forecast time, shaded by HRDPS's surface precipitation type (rain, snow, freezing rain, or sleet), with 2 hPa mean sea-level isobars and 1000-500 mb thickness lines.",
                )?;
                kwargs.set_item(
                    "caveat",
                    "Combines HRDPS's rolling APCP-Accum6h_Sfc accumulation with PTYPE_Sfc (drizzle counts as rain, freezing drizzle as freezing rain, and mixed or ice-pellet phases as sleet); PRMSL_MSL supplies the isobars and HGT at 500 and 1000 mb the thickness. Available from F006.",
                )?;
                source
                    .getattr("HrrrProduct")?
                    .call((), Some(&kwargs))?
                    .unbind()
            }
            "mslp" => pressure_product(
                py,
                "PRMSL",
                Some(shared.get_item("mslp")?.getattr("palette")?.unbind()),
            )?,
            "qpf-6h" | "qpf-12h" | "qpf-24h" => {
                let (field_name, label, hours) = match key {
                    "qpf-6h" => ("qpf6h", "6-h QPF", 6u32),
                    "qpf-12h" => ("qpf12h", "12-h QPF", 12u32),
                    _ => ("qpf24h", "24-h QPF", 24u32),
                };
                let template = shared.get_item("apcp")?;
                let spec = field_spec.call1((field_name, "sfc", "APCP", "surface"))?;
                let updates = PyDict::new(py);
                updates.set_item("key", key)?;
                updates.set_item("label", label)?;
                updates.set_item("chip", label)?;
                updates.set_item("fields", (spec,))?;
                updates.set_item("derive", single.call1((field_name, &mm_to_in))?)?;
                updates.set_item("max_hours", hours)?;
                updates.set_item(
                    "description",
                    format!(
                        "Accumulated liquid-equivalent precipitation over the {hours} hours ending at the selected forecast time, in inches."
                    ),
                )?;
                updates.set_item(
                    "caveat",
                    format!(
                        "Uses HRDPS's native {hours}-hour rolling accumulation (APCP-Accum{hours}h_Sfc); available hourly from F{hours:03}."
                    ),
                )?;
                replace.call((template,), Some(&updates))?.unbind()
            }
            "apcp" => {
                let template = shared.get_item("apcp")?;
                let updates = PyDict::new(py);
                updates.set_item("label", "Total QPF")?;
                updates.set_item("chip", "Total QPF")?;
                updates.set_item("max_hours", 1u32)?;
                updates.set_item(
                    "caveat",
                    "Includes all precipitation types accumulated since F000; HRDPS publishes APCP_Sfc starting at F001.",
                )?;
                replace.call((template,), Some(&updates))?.unbind()
            }
            "snow-10-1" => {
                let spec = field_spec.call1(("weasn", "sfc", "WEASN", "surface"))?;
                let kwargs = PyDict::new(py);
                kwargs.set_item("key", "snow-10-1")?;
                kwargs.set_item("label", "Total Snowfall (10:1)")?;
                kwargs.set_item("chip", "Snow (10:1)")?;
                kwargs.set_item("category", "Winter Weather")?;
                kwargs.set_item("fields", (spec,))?;
                kwargs.set_item("derive", wrap_pyfunction!(derive_hrdps_snow_10_1, py)?)?;
                kwargs.set_item("palette", source.getattr("PALETTE_SNOW_DEPTH")?)?;
                kwargs.set_item("max_hours", 1u32)?;
                kwargs.set_item(
                    "description",
                    "Accumulated snowfall since the selected model run using a 10:1 snow-to-liquid ratio, in inches.",
                )?;
                kwargs.set_item(
                    "caveat",
                    "Computed as 10 times HRDPS accumulated snowfall water equivalent (WEASN_Sfc); assumes a constant 10:1 ratio and starts at F001.",
                )?;
                source
                    .getattr("HrrrProduct")?
                    .call((), Some(&kwargs))?
                    .unbind()
            }
            "sbcape" => {
                let template = shared.get_item("sbcape")?;
                let updates = PyDict::new(py);
                updates.set_item(
                    "description",
                    "Surface-based CAPE published by HRDPS, in J/kg.",
                )?;
                updates.set_item(
                    "caveat",
                    "Uses HRDPS's native surface CAPE diagnostic (CAPE_Sfc).",
                )?;
                replace.call((template,), Some(&updates))?.unbind()
            }
            "lcl" => {
                let palette_kwargs = PyDict::new(py);
                palette_kwargs.set_item("stepped", true)?;
                palette_kwargs.set_item("floor", py.None())?;
                palette_kwargs.set_item("ticks", PyTuple::new(py, LCL_TICKS)?)?;
                let lcl_palette = source
                    .getattr("Palette")?
                    .call((PyTuple::new(py, lcl_stops())?, "m"), Some(&palette_kwargs))?;
                let kwargs = PyDict::new(py);
                kwargs.set_item("key", "lcl")?;
                kwargs.set_item("label", "Surface-Based LCL Height")?;
                kwargs.set_item("chip", "SBLCL")?;
                kwargs.set_item("category", "Instability")?;
                kwargs.set_item(
                    "fields",
                    (source.getattr("F_TMP2M")?, source.getattr("F_DPT2M")?),
                )?;
                kwargs.set_item("derive", wrap_pyfunction!(derive_refs_member_lcl, py)?)?;
                kwargs.set_item("palette", lcl_palette)?;
                kwargs.set_item(
                    "description",
                    "Lifting condensation level height above ground for a surface-based parcel, in metres.",
                )?;
                kwargs.set_item(
                    "caveat",
                    "Derived from HRDPS 2-m temperature and dewpoint depression (125 m/K).",
                )?;
                source
                    .getattr("HrrrProduct")?
                    .call((), Some(&kwargs))?
                    .unbind()
            }
            "shear-sfc-500" => {
                let template = shared.get_item("shear-0-6km")?;
                let u500 = field_spec.call1(("ugrd500", "prs", "UGRD", "500 mb"))?;
                let v500 = field_spec.call1(("vgrd500", "prs", "VGRD", "500 mb"))?;
                let updates = PyDict::new(py);
                updates.set_item("key", "shear-sfc-500")?;
                updates.set_item("label", "Bulk Shear: Surface-500 mb")?;
                updates.set_item("chip", "Sfc-500 Shr")?;
                updates.set_item(
                    "fields",
                    (
                        source.getattr("F_U10")?,
                        source.getattr("F_V10")?,
                        u500,
                        v500,
                    ),
                )?;
                updates.set_item("derive", wrap_pyfunction!(derive_hrdps_shear_sfc_500, py)?)?;
                updates.set_item(
                    "description",
                    "Vector wind difference between 10 m AGL and 500 mb, in knots.",
                )?;
                updates.set_item(
                    "caveat",
                    "Uses HRDPS 10 m AGL wind as the surface vector and 500 mb isobaric wind as the upper vector.",
                )?;
                replace.call((template,), Some(&updates))?.unbind()
            }
            "srh-0-3km" => {
                let template = shared.get_item("srh-0-3km")?;
                let updates = PyDict::new(py);
                updates.set_item(
                    "description",
                    "Storm-relative helicity in the lowest three kilometres published by HRDPS, in m²/s².",
                )?;
                updates.set_item("caveat", "Uses HRDPS's native HLCY_Sfc diagnostic.")?;
                replace.call((template,), Some(&updates))?.unbind()
            }
            "ehi-0-3km" => {
                let template = shared.get_item("ehi-0-3km")?;
                let updates = PyDict::new(py);
                updates.set_item(
                    "fields",
                    (source.getattr("F_SBCAPE")?, source.getattr("F_SRH03")?),
                )?;
                updates.set_item(
                    "derive",
                    wrap_pyfunction!(derive_refs_member_ehi_0_3km, py)?,
                )?;
                updates.set_item(
                    "description",
                    "Surface-based CAPE times 0-3 km SRH, scaled by 160000.",
                )?;
                updates.set_item(
                    "caveat",
                    "Derived from HRDPS native surface-based CAPE (CAPE_Sfc) and 0-3 km SRH (HLCY_Sfc).",
                )?;
                replace.call((template,), Some(&updates))?.unbind()
            }
            other => {
                return Err(PyValueError::new_err(format!(
                    "Unhandled HRDPS product key {other}"
                )));
            }
        };
        products.push(entry);
    }
    Ok(products)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn encodes_eccc_ptype_codes_and_rate_thresholds() {
        // 0.10 in/h averaged over 6 h is 0.10 * 6 * 25.4 mm of accumulation.
        let a01 = 0.10 * 6.0 * 25.4;
        let a_trace = 0.004 * 6.0 * 25.4;
        let out = encode_hrdps_prate_ptype(
            &[
                a01,
                a01,
                a01,
                a01,
                a01,
                a01,
                a01,
                a01,
                a01,
                a_trace,
                f32::NAN,
            ],
            &[1.0, 7.0, 5.0, 3.0, 8.0, 2.0, 4.0, 9.0, 6.0, 1.0, 1.0],
        );
        // Rain (1, 7) -> 0 + 0.1004
        assert!((out[0] - 0.1004).abs() < 1e-5);
        assert!((out[1] - 0.1004).abs() < 1e-5);
        // Snow (5) -> 10 + 0.1004
        assert!((out[2] - 10.1004).abs() < 1e-5);
        // Frzr (3, 8) -> 20 + 0.1004
        assert!((out[3] - 20.1004).abs() < 1e-5);
        assert!((out[4] - 20.1004).abs() < 1e-5);
        // Mix (2, 4, 9) -> 30 + 0.1004
        assert!((out[5] - 30.1004).abs() < 1e-5);
        assert!((out[6] - 30.1004).abs() < 1e-5);
        assert!((out[7] - 30.1004).abs() < 1e-5);
        // None (6), below 0.01 in/h trace, and NaN -> NaN
        assert!(out[8].is_nan());
        assert!(out[9].is_nan());
        assert!(out[10].is_nan());
    }

    #[test]
    fn converts_snow_water_equivalent_at_ten_to_one_and_preserves_missing() {
        let out = snow_10_1_inches(&[0.0, 25.4, -0.1, f32::NAN]);
        assert_eq!(out[0], 0.0);
        assert!((out[1] - 10.0).abs() < 1e-5);
        assert_eq!(out[2], 0.0);
        assert!(out[3].is_nan());
    }

    #[test]
    fn computes_surface_to_500_mb_bulk_shear_in_knots() {
        let out = bulk_shear_kt(
            &[0.0, 3.0, f32::NAN],
            &[0.0, 4.0, 0.0],
            &[3.0, 3.0, 10.0],
            &[4.0, 4.0, 10.0],
        );
        assert!((out[0] - 5.0 * MS_TO_KT).abs() < 1e-5);
        assert_eq!(out[1], 0.0);
        assert!(out[2].is_nan());
    }
}

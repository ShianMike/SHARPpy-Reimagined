//! Native additions to the shared forecast map product catalogue.

mod convection;
mod height_anomaly;
mod hrdps;
mod icon;
mod palettes;
pub(crate) mod ptype;
mod regional;
mod surface_wind;

use numpy::{
    ndarray::ArrayD, AllowTypeChange, IntoPyArray, PyArrayDyn, PyArrayLikeDyn,
    PyUntypedArrayMethods,
};
use pyo3::{
    exceptions::PyKeyError,
    prelude::*,
    types::{PyDict, PyTuple},
};

use palettes::{
    ehi_stops, isotach_stops, lcl_stops, DEEP_SHEAR_MAX_KT, EHI_FLOOR, EHI_TICKS, ISOTACH_MIN_KT,
    LCL_TICKS, LOW_LEVEL_WIND_MAX_KT, WIND_500_MAX_KT,
};

fn record_for_model(model: &str) -> Option<&'static str> {
    match model {
        // These are the providers' published mean-sea-level records, never PRES:surface.
        "hrrr" => Some("MSLMA"),
        "gfs" | "gdps" | "openmeteo-ecmwf-ifs025" | "ecmwf-ifs-9km" => Some("PRMSL"),
        _ => None,
    }
}

fn pressure_hpa(value: f64) -> f32 {
    let converted = (value / 100.0) as f32;
    if value.is_finite() && converted.is_finite() && converted > 0.0 {
        converted
    } else {
        f32::NAN
    }
}

#[pyfunction]
fn pressure_to_hpa<'py>(
    py: Python<'py>,
    values: PyArrayLikeDyn<'py, f64, AllowTypeChange>,
) -> PyResult<Bound<'py, PyArrayDyn<f32>>> {
    let shape = values.shape().to_vec();
    let values: Vec<f64> = values.as_array().iter().copied().collect();
    let output = py.detach(move || values.into_iter().map(pressure_hpa).collect());
    Ok(ArrayD::from_shape_vec(shape, output)
        .expect("conversion preserves the validated input shape")
        .into_pyarray(py))
}

#[pyfunction]
fn derive_mean_sea_level_pressure<'py>(
    py: Python<'py>,
    fields: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyArrayDyn<f32>>> {
    let values = fields
        .get_item("mslp")?
        .ok_or_else(|| PyKeyError::new_err("decoded mean-sea-level pressure is missing"))?;
    pressure_to_hpa(py, values.extract()?)
}

fn derive_from_fields(
    py: Python<'_>,
    fields: &Bound<'_, PyDict>,
    diagnostic: &str,
    names: &[&str],
) -> PyResult<Py<PyAny>> {
    let mut values = Vec::with_capacity(names.len());
    for &name in names {
        values.push(
            fields
                .get_item(name)?
                .ok_or_else(|| PyKeyError::new_err(format!("decoded field {name} is missing")))?
                .extract()?,
        );
    }
    crate::map_diagnostic_bindings::derive_map_diagnostic(py, diagnostic, values)
}

#[pyfunction]
fn derive_refs_member_scp(py: Python<'_>, fields: &Bound<'_, PyDict>) -> PyResult<Py<PyAny>> {
    derive_from_fields(py, fields, "scp", &["sbcape", "srh03", "ushr06", "vshr06"])
}

#[pyfunction]
fn derive_refs_member_stp(py: Python<'_>, fields: &Bound<'_, PyDict>) -> PyResult<Py<PyAny>> {
    derive_from_fields(
        py,
        fields,
        "stp-surface-lcl",
        &[
            "sbcape", "tmp2m", "dpt2m", "srh01", "ushr06", "vshr06", "sbcin",
        ],
    )
}

#[pyfunction]
pub(crate) fn derive_refs_member_lcl(
    py: Python<'_>,
    fields: &Bound<'_, PyDict>,
) -> PyResult<Py<PyAny>> {
    derive_from_fields(py, fields, "surface-lcl", &["tmp2m", "dpt2m"])
}

#[pyfunction]
pub(crate) fn derive_refs_member_ehi_0_1km(
    py: Python<'_>,
    fields: &Bound<'_, PyDict>,
) -> PyResult<Py<PyAny>> {
    derive_from_fields(py, fields, "ehi", &["sbcape", "srh01"])
}

#[pyfunction]
pub(crate) fn derive_refs_member_ehi_0_3km(
    py: Python<'_>,
    fields: &Bound<'_, PyDict>,
) -> PyResult<Py<PyAny>> {
    derive_from_fields(py, fields, "ehi", &["sbcape", "srh03"])
}

#[pyfunction]
#[pyo3(signature = (model, products=None))]
fn map_pressure_products(
    py: Python<'_>,
    model: &str,
    products: Option<Vec<Py<PyAny>>>,
) -> PyResult<Vec<Py<PyAny>>> {
    let mut products = products.unwrap_or_default();
    let Some(variable) = record_for_model(model) else {
        return Ok(products);
    };
    if model == "hrrr" {
        let replace = PyModule::import(py, "dataclasses")?.getattr("replace")?;
        for product in &mut products {
            let entry = product.bind(py);
            let key = entry.getattr("key")?.extract::<String>()?;
            let (stops, ticks, floor) = match key.as_str() {
                "hgt-wind-500" | "hgt-wind-700" | "hgt-wind-850" | "shear-0-6km" => {
                    // These products use the shared wind colours at different thresholds.
                    let source = PyModule::import(py, "sharpmod.providers.hrrr_products")?
                        .getattr("PALETTE_ISOTACH_KT")?
                        .getattr("stops")?
                        .extract()?;
                    let (max_kt, source_step) = match key.as_str() {
                        "hgt-wind-500" => (WIND_500_MAX_KT, 1.0),
                        "shear-0-6km" => (DEEP_SHEAR_MAX_KT, 1.0),
                        _ => (LOW_LEVEL_WIND_MAX_KT, 2.0),
                    };
                    (
                        PyTuple::new(py, isotach_stops(source, max_kt, source_step))?,
                        PyTuple::new(py, (ISOTACH_MIN_KT..=max_kt).step_by(5))?,
                        f64::from(ISOTACH_MIN_KT),
                    )
                }
                "ehi-0-1km" | "ehi-0-3km" => (
                    PyTuple::new(py, ehi_stops())?,
                    PyTuple::new(py, EHI_TICKS)?,
                    EHI_FLOOR,
                ),
                _ => continue,
            };
            let options = PyDict::new(py);
            options.set_item("stepped", true)?;
            options.set_item("floor", floor)?;
            options.set_item("ticks", ticks)?;
            options.set_item("stops", stops)?;
            let palette = replace.call((entry.getattr("palette")?,), Some(&options))?;
            let updates = PyDict::new(py);
            updates.set_item("palette", palette)?;
            *product = replace.call((entry,), Some(&updates))?.unbind();
        }
    }
    if model == "hrrr" {
        products.push(ptype::hrrr_refd_ptype_product(py)?);
    }
    products.push(pressure_product(py, variable, None)?);
    Ok(products)
}

pub(crate) fn pressure_contour<'py>(
    catalogue: &Bound<'py, PyModule>,
) -> PyResult<Bound<'py, PyAny>> {
    let py = catalogue.py();
    let kwargs = PyDict::new(py);
    kwargs.set_item("convert", wrap_pyfunction!(pressure_to_hpa, py)?)?;
    kwargs.set_item("label", "hPa")?;
    catalogue
        .getattr("ContourSpec")?
        .call(("mslp", 4.0), Some(&kwargs))
}

fn pressure_product(
    py: Python<'_>,
    variable: &str,
    palette: Option<Py<PyAny>>,
) -> PyResult<Py<PyAny>> {
    // Adapt native product metadata to the existing provider catalogue objects.
    let catalogue = PyModule::import(py, "sharpmod.providers.hrrr_products")?;
    let field =
        catalogue
            .getattr("FieldSpec")?
            .call1(("mslp", "sfc", variable, "mean sea level"))?;
    let palette = match palette {
        Some(palette) => palette.into_bound(py),
        None => {
            let palette_kwargs = PyDict::new(py);
            palette_kwargs.set_item("ticks", (960.0, 980.0, 1000.0, 1020.0, 1040.0))?;
            palette_kwargs.set_item("display", "fixed")?;
            catalogue.getattr("Palette")?.call(
                (
                    (
                        (960.0, "#425e92"),
                        (980.0, "#659cc0"),
                        (1000.0, "#c0d7d1"),
                        (1020.0, "#e0c38e"),
                        (1040.0, "#b47755"),
                    ),
                    "hPa",
                ),
                Some(&palette_kwargs),
            )?
        }
    };
    let kwargs = PyDict::new(py);
    kwargs.set_item("key", "mslp")?;
    kwargs.set_item("label", "Mean Sea-level Pressure")?;
    kwargs.set_item("chip", "MSLP")?;
    kwargs.set_item("category", "Surface")?;
    kwargs.set_item("fields", (field,))?;
    kwargs.set_item(
        "derive",
        wrap_pyfunction!(derive_mean_sea_level_pressure, py)?,
    )?;
    kwargs.set_item("palette", palette)?;
    kwargs.set_item("contour", pressure_contour(&catalogue)?)?;
    kwargs.set_item(
        "description",
        "Mean sea-level pressure in hPa, with isobars every 4 hPa.",
    )?;
    kwargs.set_item(
        "caveat",
        "Uses the model's published sea-level reduction; surface pressure is not substituted.",
    )?;
    Ok(catalogue
        .getattr("HrrrProduct")?
        .call((), Some(&kwargs))?
        .unbind())
}

// Keep the bridge entry point used by the existing Python catalogue.
#[pyfunction(name = "map_pressure_catalogue")]
fn map_product_catalogue(
    py: Python<'_>,
    model: &str,
    products: Vec<Py<PyAny>>,
) -> PyResult<Vec<Py<PyAny>>> {
    if crate::nssl_mpas_archive::is_model(model) {
        return crate::model_map_fields::nssl_mpas::catalogue(py);
    }
    let mut products = catalogue_for_model(py, model, products)?;
    convection::extend_catalogue(py, model, &mut products)?;
    ptype::extend_catalogue(py, model, &mut products)?;
    height_anomaly::extend_catalogue(py, model, &mut products)?;
    surface_wind::extend_catalogue(py, model, &mut products)?;
    Ok(products)
}

fn catalogue_for_model(
    py: Python<'_>,
    model: &str,
    products: Vec<Py<PyAny>>,
) -> PyResult<Vec<Py<PyAny>>> {
    if model == "hrrr" {
        return Ok(products);
    }
    if model == "gdps" {
        return gdps_catalogue(py);
    }
    if model == "hrdps" {
        return hrdps::catalogue(py);
    }
    if model == "openmeteo-icon-global" {
        return icon::catalogue(py);
    }
    if model == "refs" {
        return crate::model_map_fields::refs::catalogue(py);
    }
    if matches!(model, "rap" | "rdps") {
        return regional::catalogue(py, model);
    }
    let is_gfs = model == "gfs";
    let refs_member = model.starts_with("refs-");
    let refs_helpers = if refs_member {
        Some((
            PyModule::import(py, "sharpmod.providers.hrrr_products")?,
            PyModule::import(py, "dataclasses")?.getattr("replace")?,
        ))
    } else {
        None
    };
    let mut result = Vec::with_capacity(products.len() + 9);
    for product in products {
        let entry = product.bind(py);
        let key = entry.getattr("key")?.extract::<String>()?;
        if matches!(key.as_str(), "mslp" | "refd-1km-ptype") || (is_gfs && key == "refc") {
            continue;
        }
        if let Some((catalogue, replace)) = &refs_helpers {
            if key == "apcp" {
                for (qpf_key, label, max_hours, description, caveat) in [
                    (
                        "qpf-1h",
                        "1-h QPF",
                        1u32,
                        "Accumulated liquid-equivalent precipitation over the hour ending at the selected forecast time, in inches.",
                        "Uses native 1-hour APCP accumulations from the selected run; available from F001.",
                    ),
                    (
                        "qpf-6h",
                        "6-h QPF",
                        6u32,
                        "Accumulated liquid-equivalent precipitation over the 6 hours ending at the selected forecast time, in inches.",
                        "Computed by summing native hourly APCP accumulations over the trailing 6-hour window; available from F006.",
                    ),
                    (
                        "qpf-12h",
                        "12-h QPF",
                        12u32,
                        "Accumulated liquid-equivalent precipitation over the 12 hours ending at the selected forecast time, in inches.",
                        "Computed by summing native hourly APCP accumulations over the trailing 12-hour window; available from F012.",
                    ),
                    (
                        "qpf-24h",
                        "24-h QPF",
                        24u32,
                        "Accumulated liquid-equivalent precipitation over the 24 hours ending at the selected forecast time, in inches.",
                        "Computed by summing native hourly APCP accumulations over the trailing 24-hour window; available from F024.",
                    ),
                ] {
                    let updates = PyDict::new(py);
                    updates.set_item("key", qpf_key)?;
                    updates.set_item("label", label)?;
                    updates.set_item("chip", label)?;
                    updates.set_item("max_hours", max_hours)?;
                    updates.set_item("description", description)?;
                    updates.set_item("caveat", caveat)?;
                    result.push(replace.call((entry,), Some(&updates))?.unbind());
                }
                let updates = PyDict::new(py);
                updates.set_item("label", "Total QPF")?;
                updates.set_item("chip", "Total QPF")?;
                result.push(replace.call((entry,), Some(&updates))?.unbind());
                result.push(ptype::refs_member_qpf_ptype_product(py)?);
                continue;
            }
            if key == "pwat" {
                let asnow = catalogue.getattr("FieldSpec")?.call1((
                    "asnow",
                    "sfc",
                    "ASNOW",
                    "surface",
                    "0-* acc fcst",
                ))?;
                let snow_updates = PyDict::new(py);
                snow_updates.set_item("key", "snow-1h")?;
                snow_updates.set_item("label", "1-h Snowfall")?;
                snow_updates.set_item("chip", "1-h Snow")?;
                snow_updates.set_item("category", "Winter Weather")?;
                snow_updates.set_item("fields", (asnow,))?;
                snow_updates.set_item(
                    "derive",
                    catalogue
                        .getattr("_single")?
                        .call1(("asnow", catalogue.getattr("m_to_in")?))?,
                )?;
                snow_updates.set_item("palette", catalogue.getattr("PALETTE_SNOW_DEPTH")?)?;
                snow_updates.set_item("max_hours", 1u32)?;
                snow_updates.set_item(
                    "description",
                    "Accumulated snowfall over the hour ending at the selected forecast time, in inches.",
                )?;
                snow_updates.set_item(
                    "caveat",
                    "Computed from consecutive cumulative ASNOW fields (0-* acc fcst) in the reduced REFS member stream; available from F001.",
                )?;
                let snow_1h = replace.call((entry,), Some(&snow_updates))?.unbind();
                result.push(product);
                result.push(snow_1h);
                continue;
            }
            if key == "sbcape" {
                let sbcin_updates = PyDict::new(py);
                sbcin_updates.set_item("key", "sbcin")?;
                sbcin_updates.set_item("label", "Surface-Based CIN")?;
                sbcin_updates.set_item("chip", "SBCIN")?;
                sbcin_updates.set_item("category", "Instability")?;
                sbcin_updates.set_item("fields", (catalogue.getattr("F_SBCIN")?,))?;
                sbcin_updates
                    .set_item("derive", catalogue.getattr("_single")?.call1(("sbcin",))?)?;
                sbcin_updates.set_item("palette", catalogue.getattr("PALETTE_CIN")?)?;
                sbcin_updates.set_item(
                    "description",
                    "Convective inhibition for a parcel lifted from the surface.",
                )?;
                sbcin_updates.set_item("caveat", "")?;
                let sbcin = replace.call((entry,), Some(&sbcin_updates))?.unbind();

                let palette_kwargs = PyDict::new(py);
                palette_kwargs.set_item("stepped", true)?;
                palette_kwargs.set_item("floor", py.None())?;
                palette_kwargs.set_item("ticks", PyTuple::new(py, LCL_TICKS)?)?;
                let lcl_palette = catalogue
                    .getattr("Palette")?
                    .call((PyTuple::new(py, lcl_stops())?, "m"), Some(&palette_kwargs))?;
                let lcl_updates = PyDict::new(py);
                lcl_updates.set_item("key", "lcl")?;
                lcl_updates.set_item("label", "Surface-Based LCL Height")?;
                lcl_updates.set_item("chip", "SBLCL")?;
                lcl_updates.set_item("category", "Instability")?;
                lcl_updates.set_item(
                    "fields",
                    (catalogue.getattr("F_TMP2M")?, catalogue.getattr("F_DPT2M")?),
                )?;
                lcl_updates.set_item("derive", wrap_pyfunction!(derive_refs_member_lcl, py)?)?;
                lcl_updates.set_item("palette", lcl_palette)?;
                lcl_updates.set_item(
                    "description",
                    "Lifting condensation level height above ground for a surface-based parcel, in metres.",
                )?;
                lcl_updates.set_item(
                    "caveat",
                    "Derived from 2-m temperature and dewpoint depression (125 m/K) from the reduced REFS member stream.",
                )?;
                let lcl = replace.call((entry,), Some(&lcl_updates))?.unbind();

                result.push(product);
                result.push(sbcin);
                result.push(lcl);
                continue;
            }
            if key == "ehi-0-1km" {
                let updates = PyDict::new(py);
                updates.set_item("label", "Energy Helicity Index (EHI): 0-1 km AGL")?;
                updates.set_item(
                    "fields",
                    (
                        catalogue.getattr("F_SBCAPE")?,
                        catalogue.getattr("F_SRH01")?,
                    ),
                )?;
                updates.set_item(
                    "derive",
                    wrap_pyfunction!(derive_refs_member_ehi_0_1km, py)?,
                )?;
                updates.set_item(
                    "description",
                    "Surface-based CAPE times 0-1 km SRH, scaled by 160000.",
                )?;
                updates.set_item(
                    "caveat",
                    "Uses surface-based CAPE and 0-1 km SRH from the reduced REFS member stream.",
                )?;
                result.push(replace.call((entry,), Some(&updates))?.unbind());
                continue;
            }
            if key == "ehi-0-3km" {
                let updates = PyDict::new(py);
                updates.set_item("label", "Energy Helicity Index (EHI): 0-3 km AGL")?;
                updates.set_item(
                    "fields",
                    (
                        catalogue.getattr("F_SBCAPE")?,
                        catalogue.getattr("F_SRH03")?,
                    ),
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
                    "Uses surface-based CAPE and 0-3 km SRH from the reduced REFS member stream.",
                )?;
                result.push(replace.call((entry,), Some(&updates))?.unbind());
                continue;
            }
            if key == "scp" {
                let updates = PyDict::new(py);
                updates.set_item(
                    "fields",
                    (
                        catalogue.getattr("F_SBCAPE")?,
                        catalogue.getattr("F_SRH03")?,
                        catalogue.getattr("F_USHR06")?,
                        catalogue.getattr("F_VSHR06")?,
                    ),
                )?;
                updates.set_item("derive", wrap_pyfunction!(derive_refs_member_scp, py)?)?;
                updates.set_item(
                    "description",
                    "Surface-based CAPE, 0-3 km SRH and 0-6 km shear combined into a fixed-layer supercell composite.",
                )?;
                updates.set_item(
                    "caveat",
                    "Fixed-layer form using surface-based CAPE, 0-3 km SRH, and 0-6 km bulk shear from the reduced REFS member stream.",
                )?;
                result.push(replace.call((entry,), Some(&updates))?.unbind());
                continue;
            }
            if key == "stp" {
                let updates = PyDict::new(py);
                updates.set_item(
                    "fields",
                    (
                        catalogue.getattr("F_SBCAPE")?,
                        catalogue.getattr("F_SBCIN")?,
                        catalogue.getattr("F_TMP2M")?,
                        catalogue.getattr("F_DPT2M")?,
                        catalogue.getattr("F_SRH01")?,
                        catalogue.getattr("F_USHR06")?,
                        catalogue.getattr("F_VSHR06")?,
                    ),
                )?;
                updates.set_item("derive", wrap_pyfunction!(derive_refs_member_stp, py)?)?;
                updates.set_item(
                    "caveat",
                    "Fixed-layer form using surface-based CAPE and CIN, 2-m LCL height, 0-1 km SRH, and 0-6 km bulk shear from the reduced REFS member stream.",
                )?;
                result.push(replace.call((entry,), Some(&updates))?.unbind());
                continue;
            }
        }
        result.push(product);
    }
    if let Some(variable) = record_for_model(model) {
        let shared =
            PyModule::import(py, "sharpmod.providers.hrrr_products")?.getattr("PRODUCTS")?;
        if is_gfs {
            // The primary 0.25° GFS grid uses HRRR's REFC:entire atmosphere address.
            result.push(shared.get_item("refc")?.unbind());
        }
        let palette = shared.get_item("mslp")?.getattr("palette")?.unbind();
        result.push(pressure_product(py, variable, Some(palette))?);
    }
    Ok(result)
}

fn gdps_catalogue(py: Python<'_>) -> PyResult<Vec<Py<PyAny>>> {
    let catalogue = PyModule::import(py, "sharpmod.providers.hrrr_products")?;
    let shared = catalogue.getattr("PRODUCTS")?;
    let replace = PyModule::import(py, "dataclasses")?.getattr("replace")?;
    crate::gdps_fields::PRODUCTS.iter().map(|&key| {
        if key == "prate-ptype" {
            // The precipitation-type composite is native; there is no shared
            // HRRR product to adapt.
            return ptype::gdps_prate_ptype_product(py);
        }
        let template = shared.get_item(if key == "cape" { "sbcape" } else { key })?;
        let updates = PyDict::new(py);
        match key {
            "mslp" => return pressure_product(py, "PRMSL", Some(template.getattr("palette")?.unbind())),
            "cape" => {
                updates.set_item("key", "cape")?;
                updates.set_item("label", "Convective Available Potential Energy")?;
                updates.set_item("chip", "CAPE")?;
                updates.set_item("description", "CAPE published by GDPS, in J/kg.")?;
                updates.set_item("caveat", "Uses GDPS's native diagnostic; it is not GFS most-unstable CAPE.")?;
            }
            "apcp" | "prate" => updates.set_item("max_hours", 3)?,
            "prate-frzr" | "prate-sleet" | "prate-snow" => {
                let name = match key {
                    "prate-frzr" => "gdps_frzr",
                    "prate-sleet" => "gdps_sleet",
                    _ => "gdps_snow",
                };
                let spec = catalogue.getattr("FieldSpec")?.call1((name, "sfc", name, "surface"))?;
                updates.set_item("fields", (spec,))?;
                updates.set_item("derive", catalogue.getattr("_single")?.call1((name,))?)?;
                updates.set_item("max_hours", 3)?;
                updates.set_item("description", "Average liquid-equivalent rate in mm/h over the previous 3 forecast hours.")?;
                updates.set_item("caveat", "Uses the published GDPS accumulation for this precipitation type.")?;
            }
            _ => return Ok(template.unbind()),
        }
        Ok(replace.call((template,), Some(&updates))?.unbind())
    }).collect()
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(pressure_to_hpa, module)?)?;
    module.add_function(wrap_pyfunction!(map_pressure_products, module)?)?;
    module.add_function(wrap_pyfunction!(map_product_catalogue, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn products_address_published_sea_level_records_without_a_surface_fallback() {
        assert_eq!(record_for_model("hrrr"), Some("MSLMA"));
        for model in ["gfs", "gdps", "ecmwf-ifs-9km", "openmeteo-ecmwf-ifs025"] {
            assert_eq!(record_for_model(model), Some("PRMSL"));
        }
        for unsupported in ["", "rrfs-a", "unknown", "PRES"] {
            assert_eq!(record_for_model(unsupported), None);
        }
    }

    #[test]
    #[ignore = "requires the existing Python catalogue and native extension"]
    fn gfs_catalogue_selects_the_published_composite_reflectivity_record() {
        Python::initialize();
        Python::attach(|py| -> PyResult<()> {
            let provider = PyModule::import(py, "sharpmod.providers.hrrr_products")?;
            let base = provider
                .call_method1("_available_products", ("gfs",))?
                .extract()?;
            let products = map_product_catalogue(py, "gfs", base)?;
            let mut reflectivity = None;
            for product in &products {
                let product = product.bind(py);
                if product.getattr("key")?.extract::<String>()? == "refc" {
                    assert!(reflectivity.replace(product).is_none(), "duplicate REFC");
                }
            }
            let product = reflectivity.expect("GFS must offer composite reflectivity");
            let field = product.getattr("fields")?.get_item(0)?;
            for forecast in ["anl", "24 hour fcst", "384 hour fcst"] {
                assert!(field
                    .call_method1("matches", ("REFC", "entire atmosphere", forecast))?
                    .extract::<bool>()?);
            }
            assert!(!field
                .call_method1("matches", ("REFD", "1 hybrid level", "24 hour fcst"))?
                .extract::<bool>()?);
            assert_eq!(
                product
                    .getattr("palette")?
                    .getattr("units")?
                    .extract::<String>()?,
                "dBZ"
            );
            Ok(())
        })
        .unwrap();
    }

    #[test]
    fn pressure_conversion_keeps_real_values_and_missing_cells() {
        assert_eq!(pressure_hpa(101_325.0), 1013.25);
        assert_eq!(pressure_hpa(96_000.0), 960.0);
        assert_eq!(pressure_hpa(104_000.0), 1040.0);
        for missing in [
            f64::NAN,
            f64::INFINITY,
            f64::NEG_INFINITY,
            f64::MAX,
            0.0,
            -9999.0,
        ] {
            assert!(pressure_hpa(missing).is_nan());
        }
    }
}

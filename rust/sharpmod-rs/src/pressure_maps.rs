//! Mean-sea-level pressure products and unit conversion owned by the native backend.

use numpy::{
    ndarray::ArrayD, AllowTypeChange, IntoPyArray, PyArrayDyn, PyArrayLikeDyn,
    PyUntypedArrayMethods,
};
use pyo3::{exceptions::PyKeyError, prelude::*, types::PyDict};

fn record_for_model(model: &str) -> Option<&'static str> {
    match model {
        // These are the providers' published mean-sea-level records, never PRES:surface.
        "hrrr" => Some("MSLMA"),
        "gfs" | "openmeteo-ecmwf-ifs025" | "ecmwf-ifs-9km" => Some("PRMSL"),
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

#[pyfunction]
fn map_pressure_products(py: Python<'_>, model: &str) -> PyResult<Vec<Py<PyAny>>> {
    let Some(variable) = record_for_model(model) else {
        return Ok(Vec::new());
    };
    Ok(vec![pressure_product(py, variable, None)?])
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
    let contour_kwargs = PyDict::new(py);
    contour_kwargs.set_item("convert", wrap_pyfunction!(pressure_to_hpa, py)?)?;
    contour_kwargs.set_item("label", "hPa")?;
    let contour = catalogue
        .getattr("ContourSpec")?
        .call(("mslp", 4.0), Some(&contour_kwargs))?;
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
    kwargs.set_item("contour", contour)?;
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

#[pyfunction]
fn map_pressure_catalogue(
    py: Python<'_>,
    model: &str,
    products: Vec<Py<PyAny>>,
) -> PyResult<Vec<Py<PyAny>>> {
    if model == "hrrr" {
        return Ok(products);
    }
    let mut result = Vec::with_capacity(products.len() + 1);
    for product in products {
        if product.bind(py).getattr("key")?.extract::<String>()? != "mslp" {
            result.push(product);
        }
    }
    if let Some(variable) = record_for_model(model) {
        let palette = PyModule::import(py, "sharpmod.providers.hrrr_products")?
            .getattr("PRODUCTS")?
            .get_item("mslp")?
            .getattr("palette")?
            .unbind();
        result.push(pressure_product(py, variable, Some(palette))?);
    }
    Ok(result)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(pressure_to_hpa, module)?)?;
    module.add_function(wrap_pyfunction!(map_pressure_products, module)?)?;
    module.add_function(wrap_pyfunction!(map_pressure_catalogue, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn products_address_published_sea_level_records_without_a_surface_fallback() {
        assert_eq!(record_for_model("hrrr"), Some("MSLMA"));
        for model in ["gfs", "ecmwf-ifs-9km", "openmeteo-ecmwf-ifs025"] {
            assert_eq!(record_for_model(model), Some("PRMSL"));
        }
        for unsupported in ["", "rrfs-a", "unknown", "PRES"] {
            assert_eq!(record_for_model(unsupported), None);
        }
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

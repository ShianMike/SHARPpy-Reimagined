//! Match HRRR products to published regional records without changing their palettes.

use super::*;
use numpy::{ndarray::IxDyn, PyReadonlyArrayDyn};
use pyo3::exceptions::PyValueError;

const SHEAR_LEVELS: [u16; 6] = [925, 850, 750, 650, 550, 450];

pub fn catalogue(py: Python<'_>, model: &str) -> PyResult<Vec<Py<PyAny>>> {
    let source = PyModule::import(py, "sharpmod.providers.hrrr_products")?;
    let shared = source.getattr("PRODUCTS")?;
    let replace = PyModule::import(py, "dataclasses")?.getattr("replace")?;
    if model == "rdps" {
        let mut products = super::gdps_catalogue(py)?;
        for product in &mut products {
            let entry = product.bind(py);
            let key = entry.getattr("key")?.extract::<String>()?;
            let updates = PyDict::new(py);
            if key == "cape" {
                updates.set_item("description", "CAPE published by RDPS, in J/kg.")?;
                updates.set_item("caveat", "Uses RDPS's native diagnostic; a mixed-layer or most-unstable parcel is not assumed.")?;
            } else if matches!(key.as_str(), "prate-frzr" | "prate-sleet" | "prate-snow") {
                updates.set_item("max_hours", 1)?;
                updates.set_item(
                    "description",
                    "Average liquid-equivalent rate in mm/h over the previous forecast hour.",
                )?;
                updates.set_item(
                    "caveat",
                    "Uses RDPS's published one-hour accumulation for this precipitation type.",
                )?;
            } else if matches!(key.as_str(), "apcp" | "prate") {
                updates.set_item("max_hours", 0)?;
            } else {
                continue;
            }
            *product = replace.call((entry,), Some(&updates))?.unbind();
        }
        return Ok(products);
    }
    let mut products = Vec::new();
    for entry in shared.call_method0("values")?.try_iter()? {
        let entry = entry?;
        let key = entry.getattr("key")?.extract::<String>()?;
        if matches!(
            key.as_str(),
            "ltng" | "stp" | "uh-0-3km" | "uh-2-5km-1h" | "uh-2-5km-3h"
        ) {
            continue;
        }
        if key != "shear-0-1km" {
            products.push(entry.unbind());
            continue;
        }
        let updates = PyDict::new(py);
        let spec = source.getattr("FieldSpec")?;
        let mut fields = vec![
            spec.call1(("sfchgt", "sfc", "HGT", "surface"))?.unbind(),
            spec.call1(("ugrd10", "sfc", "UGRD", "10 m above ground"))?
                .unbind(),
            spec.call1(("vgrd10", "sfc", "VGRD", "10 m above ground"))?
                .unbind(),
        ];
        for level in SHEAR_LEVELS {
            for variable in ["HGT", "UGRD", "VGRD"] {
                fields.push(
                    spec.call1((
                        format!("{}{level}", variable.to_lowercase()),
                        "prs",
                        variable,
                        format!("{level} mb"),
                    ))?
                    .unbind(),
                );
            }
        }
        updates.set_item("fields", PyTuple::new(py, fields)?)?;
        updates.set_item("derive", wrap_pyfunction!(derive_rap_shallow_shear, py)?)?;
        updates.set_item(
            "description",
            "Vector wind difference from 10 m to 1 km AGL, in knots.",
        )?;
        updates.set_item("caveat", "The 1 km wind is interpolated between pressure levels above the model terrain. The lower wind is at 10 m.")?;
        products.push(replace.call((entry,), Some(&updates))?.unbind());
    }
    Ok(products)
}

#[pyfunction]
fn derive_rap_shallow_shear<'py>(
    py: Python<'py>,
    fields: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyArrayDyn<f32>>> {
    let names =
        [
            "sfchgt".to_owned(),
            "ugrd10".to_owned(),
            "vgrd10".to_owned(),
        ]
        .into_iter()
        .chain(SHEAR_LEVELS.into_iter().flat_map(|level| {
            ["hgt", "ugrd", "vgrd"].map(move |prefix| format!("{prefix}{level}"))
        }));
    let mut grids = Vec::new();
    let mut shape = Vec::new();
    for name in names {
        let value = fields
            .get_item(&name)?
            .ok_or_else(|| PyKeyError::new_err(name))?;
        let grid = value.extract::<PyReadonlyArrayDyn<'_, f32>>()?;
        if grids.is_empty() {
            shape = grid.shape().to_vec();
        } else if grid.shape() != shape {
            return Err(PyValueError::new_err(
                "RAP shear grids must have equal dimensions",
            ));
        }
        grids.push(grid.as_array().iter().copied().collect::<Vec<_>>());
    }
    let values = py.detach(|| shallow_shear(&grids));
    Ok(ArrayD::from_shape_vec(IxDyn(&shape), values)
        .expect("preserved shear dimensions")
        .into_pyarray(py))
}

fn shallow_shear(grids: &[Vec<f32>]) -> Vec<f32> {
    (0..grids[0].len())
        .map(|cell| {
            let (surface, u, v) = (grids[0][cell], grids[1][cell], grids[2][cell]);
            let target = surface + 1000.0;
            let mut lower = (surface + 10.0, u, v);
            for level in 0..SHEAR_LEVELS.len() {
                let base = 3 + level * 3;
                let upper = (
                    grids[base][cell],
                    grids[base + 1][cell],
                    grids[base + 2][cell],
                );
                // Below-ground pressure levels never replace the surface anchor.
                if upper.0 <= surface + 10.0 {
                    continue;
                }
                if lower.0 <= target && target <= upper.0 && upper.0 > lower.0 {
                    let weight = (target - lower.0) / (upper.0 - lower.0);
                    return ((lower.1 + weight * (upper.1 - lower.1) - u)
                        .hypot(lower.2 + weight * (upper.2 - lower.2) - v))
                        * 1.9438445;
                }
                lower = upper;
            }
            f32::NAN
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shallow_shear_brackets_the_actual_terrain_and_preserves_missing_data() {
        let mut grids = vec![vec![0.0, 1500.0, 0.0, f32::NAN], vec![0.0; 4], vec![0.0; 4]];
        for height in [500.0, 1000.0, 2000.0, 3000.0, 4000.0, 5000.0] {
            grids.extend([vec![height; 4], vec![height / 100.0; 4], vec![0.0; 4]]);
        }
        grids[1][2] = f32::NAN;
        let values = shallow_shear(&grids);
        assert!((values[0] - 19.438445).abs() < 1e-5);
        assert!((values[1] - 48.59611).abs() < 1e-5);
        assert!(values[2].is_nan() && values[3].is_nan());
    }
}

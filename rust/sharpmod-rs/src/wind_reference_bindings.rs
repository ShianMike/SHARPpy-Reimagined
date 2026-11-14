//! Metadata and NumPy boundary for model-provider wind normalization.

use crate::{
    numeric_grid::{self, NumericGrid},
    wind_reference::{Precision, Reference},
};
use pyo3::{exceptions::PyValueError, prelude::*};
use rayon::prelude::*;

#[pyfunction]
fn grib_wind_reference(get: &Bound<'_, PyAny>) -> PyResult<Option<(f64, f64)>> {
    let relative = get.call1(("uvRelativeToGrid",))?;
    let relative = if relative.is_none() {
        let flags = get.call1(("resolutionAndComponentFlags",))?;
        if flags.is_none() {
            None
        } else {
            Some(i64::from(flags.extract::<i64>()? & 8 != 0))
        }
    } else {
        Some(relative.extract::<i64>()?)
    };
    let grid = get
        .call1(("gridType",))?
        .extract::<Option<String>>()?
        .unwrap_or_default();
    if relative == Some(0)
        || (relative.is_none()
            && matches!(
                grid.as_str(),
                "regular_ll" | "regular_gg" | "reduced_ll" | "reduced_gg"
            ))
    {
        return Ok(None);
    }
    if relative != Some(1) {
        return Err(PyValueError::new_err(
            "GRIB wind-reference flag is missing or invalid",
        ));
    }
    let reference = match grid.as_str() {
        "regular_ll" | "regular_gg" | "reduced_ll" | "reduced_gg" | "mercator" => return Ok(None),
        "lambert" => Reference::lambert(
            get.call1(("Latin1InDegrees",))?.extract()?,
            get.call1(("Latin2InDegrees",))?.extract()?,
            get.call1(("LoVInDegrees",))?.extract()?,
        ),
        "polar_stereographic" => Reference::polar(
            get.call1(("projectionCentreFlag",))?.extract::<i64>()? & 128 != 0,
            get.call1(("orientationOfTheGridInDegrees",))?.extract()?,
        ),
        _ => {
            return Err(PyValueError::new_err(format!(
                "unsupported grid-relative wind projection: {grid:?}"
            )))
        }
    }
    .map_err(PyValueError::new_err)?;
    Ok(Some((reference.cone, reference.origin)))
}

#[pyfunction(signature=(u,v,longitude,rotation,missing=-9999.0))]
fn rotate_model_winds(
    py: Python<'_>,
    u: &Bound<'_, PyAny>,
    v: &Bound<'_, PyAny>,
    longitude: &Bound<'_, PyAny>,
    rotation: Option<(f64, f64)>,
    missing: f64,
) -> PyResult<(Py<PyAny>, Py<PyAny>)> {
    let Some((cone, origin)) = rotation else {
        return Ok((u.clone().unbind(), v.clone().unbind()));
    };
    let inputs: Vec<NumericGrid<'_>> = vec![u.extract()?, v.extract()?, longitude.extract()?];
    let grids = numeric_grid::broadcast(inputs)?;
    let single = grids.buffers.iter().all(|v| v.single());
    let precision = Precision {
        source_f32: (grids.buffers[0].single(), grids.buffers[1].single()),
        longitude_f32: grids.buffers[2].single(),
        components_f32: single,
    };
    let reference = Reference { cone, origin };
    let (east, north) = py.detach(|| {
        (0..grids.cells)
            .into_par_iter()
            .with_min_len(4096)
            .map(|cell| {
                reference.masked_rotate(
                    grids.buffers[0].value(cell),
                    grids.buffers[1].value(cell),
                    grids.buffers[2].value(cell),
                    missing,
                    &precision,
                )
            })
            .unzip()
    });
    Ok((
        numeric_grid::output(py, &grids.shape, east, single),
        numeric_grid::output(py, &grids.shape, north, single),
    ))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(grib_wind_reference, module)?)?;
    module.add_function(wrap_pyfunction!(rotate_model_winds, module)?)?;
    Ok(())
}

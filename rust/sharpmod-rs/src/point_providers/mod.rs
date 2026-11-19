//! Provider orchestration and typed point columns. Capability manifests and
//! credential-bearing HTTP adapters remain compatible Python boundaries.
use numpy::IntoPyArray;
use pyo3::{prelude::*, types::PyDict};
mod geomet;
mod openmeteo;
#[cfg(test)]
mod tests;
fn error(api: &Bound<'_, PyModule>, kind: &str, message: String) -> PyResult<PyErr> {
    Ok(PyErr::from_value(api.getattr(kind)?.call1((message,))?))
}
fn merged<'py>(
    py: Python<'py>,
    rows: &[[f64; 9]],
    surface: [f64; 6],
) -> PyResult<Bound<'py, PyAny>> {
    let Some(ground) = crate::model_surface::GroundLevel::verified(surface, Some(-9999.)) else {
        return Ok(py.None().into_bound(py));
    };
    let columns = std::array::from_fn(|i| {
        rows.iter()
            .map(|r| if r[i].is_finite() { r[i] } else { -9999. })
            .collect()
    });
    let merged = py.detach(move || crate::model_surface::merge(columns, ground, -9999.));
    let arrays = PyDict::new(py);
    for (name, values) in [
        "pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "omeg", "u", "v",
    ]
    .into_iter()
    .zip(merged.columns)
    {
        arrays.set_item(name, values.into_pyarray(py))?;
    }
    PyModule::import(py, "sharpmod.models.model_surface")?
        .getattr("SurfaceMergeResult")?
        .call1((arrays, ground.pressure(), merged.removed))
}
pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    openmeteo::register(module)?;
    geomet::register(module)
}

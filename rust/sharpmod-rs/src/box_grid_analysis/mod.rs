mod lattice;
mod stats;
#[cfg(test)]
mod tests;
mod transect;

use pyo3::prelude::*;
use std::borrow::Cow;

pub(crate) fn slice_or_vec<'a>(arr: &'a numpy::PyReadonlyArray1<'_, f64>) -> Cow<'a, [f64]> {
    match arr.as_slice() {
        Ok(slice) => Cow::Borrowed(slice),
        Err(_) => Cow::Owned(arr.as_array().iter().copied().collect()),
    }
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(lattice::box_region_from_corners, module)?)?;
    module.add_function(wrap_pyfunction!(lattice::box_region_from_bounds, module)?)?;
    module.add_function(wrap_pyfunction!(lattice::box_region_contains, module)?)?;
    module.add_function(wrap_pyfunction!(lattice::plan_box_lattice, module)?)?;
    module.add_function(wrap_pyfunction!(transect::box_transect_column, module)?)?;
    module.add_function(wrap_pyfunction!(transect::box_transect_columns, module)?)?;
    module.add_function(wrap_pyfunction!(stats::box_field_statistics, module)?)?;
    module.add_function(wrap_pyfunction!(stats::box_envelope_stats, module)?)?;
    Ok(())
}

//! Owned profile columns and masked scalars at the scientific Python boundary.

use numpy::PyReadonlyArray1;
use pyo3::{prelude::*, types::PyDict};

pub(crate) fn masked_number(
    value: &Bound<'_, PyAny>,
    masked: &Bound<'_, PyModule>,
) -> PyResult<Option<f64>> {
    if masked.call_method1("is_masked", (value,))?.is_truthy()? {
        Ok(None)
    } else {
        value.extract().map(Some)
    }
}

pub(crate) fn column(
    prepare: &Bound<'_, PyAny>,
    value: &Bound<'_, PyAny>,
    name: &str,
) -> PyResult<Vec<f64>> {
    let keywords = PyDict::new(prepare.py());
    keywords.set_item("name", name)?;
    let array = prepare.call((value,), Some(&keywords))?;
    Ok(array
        .extract::<PyReadonlyArray1<'_, f64>>()?
        .as_slice()?
        .to_vec())
}

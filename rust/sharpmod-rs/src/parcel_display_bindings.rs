//! Small compatibility boundary for parcel display calculations.

use numpy::PyReadonlyArray1;
use pyo3::{exceptions::PyValueError, prelude::*};

#[pyfunction(signature=(pressure,height,temperature,environment,start,el,sfc=0,missing=Some(-9999.)))]
#[allow(clippy::too_many_arguments)] // Four reported columns and one parcel's state.
fn parcel_maximum_level(
    py: Python<'_>,
    pressure: PyReadonlyArray1<'_, f64>,
    height: PyReadonlyArray1<'_, f64>,
    temperature: PyReadonlyArray1<'_, f64>,
    environment: PyReadonlyArray1<'_, f64>,
    start: (f64, f64, f64),
    el: f64,
    sfc: usize,
    missing: Option<f64>,
) -> PyResult<(f64, f64)> {
    let columns = [
        pressure.as_slice()?.to_vec(),
        height.as_slice()?.to_vec(),
        temperature.as_slice()?.to_vec(),
        environment.as_slice()?.to_vec(),
    ];
    py.detach(move || {
        crate::parcels::maximum_parcel_level(
            columns.each_ref().map(Vec::as_slice),
            start,
            el,
            sfc,
            missing,
        )
    })
    .map_err(PyValueError::new_err)
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(parcel_maximum_level, module)?)?;
    Ok(())
}

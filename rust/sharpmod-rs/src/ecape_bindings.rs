//! One owned-buffer boundary; ECAPE workspace calculations release the GIL.

use numpy::PyReadonlyArray1;
use pyo3::{exceptions::PyValueError, prelude::*, types::PyDict};

#[pyfunction]
fn ecape_building_blocks<'py>(
    py: Python<'py>,
    pressure: PyReadonlyArray1<'py, f64>,
    height: PyReadonlyArray1<'py, f64>,
    temperature: PyReadonlyArray1<'py, f64>,
    dewpoint: PyReadonlyArray1<'py, f64>,
    u: PyReadonlyArray1<'py, f64>,
    v: PyReadonlyArray1<'py, f64>,
) -> PyResult<Option<Bound<'py, PyDict>>> {
    let columns = [pressure, height, temperature, dewpoint, u, v]
        .map(|column| column.as_slice().map(<[f64]>::to_vec));
    let [p, z, t, td, u, v] = columns;
    let columns = [p?, z?, t?, td?, u?, v?];
    let blocks = py
        .detach(move || crate::ecape::building_blocks(columns))
        .map_err(PyValueError::new_err)?;
    let Some(blocks) = blocks else {
        return Ok(None);
    };
    let result = PyDict::new(py);
    result.set_item("cape", blocks.cape)?;
    result.set_item("ncape", blocks.ncape)?;
    result.set_item("sr_wind", blocks.sr_wind)?;
    result.set_item("el_z", blocks.el_z)?;
    Ok(Some(result))
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(ecape_building_blocks, module)?)
}

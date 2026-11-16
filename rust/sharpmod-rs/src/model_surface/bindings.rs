//! NumPy conversion and public result adaptation for provider surface merges.

use numpy::{IntoPyArray, PyReadonlyArray1};
use pyo3::{
    exceptions::{PyIndexError, PyTypeError, PyValueError},
    prelude::*,
    types::{PyDict, PyFloat},
};

use super::{merge, GroundThermodynamics, COLUMN_NAMES};

fn surface_scalar(
    numpy: &Bound<'_, PyModule>,
    keywords: &Bound<'_, PyDict>,
    input: &Bound<'_, PyAny>,
) -> PyResult<Option<f64>> {
    if input.is_none() {
        return Ok(None);
    }
    let result = numpy
        .call_method("asarray", (input,), Some(keywords))
        .and_then(|array| array.call_method1("reshape", (-1,)))
        .and_then(|flat| flat.get_item(0))
        .and_then(|first| first.extract());
    match result {
        Ok(value) => Ok(Some(value)),
        Err(error)
            if error.is_instance_of::<PyIndexError>(numpy.py())
                || error.is_instance_of::<PyTypeError>(numpy.py())
                || error.is_instance_of::<PyValueError>(numpy.py()) =>
        {
            Ok(None)
        }
        Err(error) => Err(error),
    }
}

#[pyfunction(signature = (columns, surface, missing=-9999.0))]
fn merge_model_surface(
    py: Python<'_>,
    columns: &Bound<'_, PyAny>,
    surface: &Bound<'_, PyAny>,
    missing: f64,
) -> PyResult<Option<Py<PyAny>>> {
    let numpy = PyModule::import(py, "numpy")?;
    let keywords = PyDict::new(py);
    keywords.set_item("dtype", py.get_type::<PyFloat>())?;
    let mut owned: [Vec<f64>; 9] = std::array::from_fn(|_| Vec::new());
    let mut length = None;
    for (name, output) in COLUMN_NAMES.iter().zip(&mut owned) {
        if !columns.contains(name)? {
            return Ok(None);
        }
        // asarray intentionally follows the existing provider contract, which
        // reads underlying masked-array data rather than inventing new masks.
        let array = numpy
            .call_method("asarray", (columns.get_item(name)?,), Some(&keywords))?
            .call_method1("reshape", (-1,))?;
        let values = array.extract::<PyReadonlyArray1<'_, f64>>()?;
        let values = values.as_array();
        if length.is_some_and(|size| size != values.len()) {
            return Ok(None);
        }
        length = Some(values.len());
        output.extend(values.iter().copied());
    }
    let mut scalars = [None; 4];
    for (name, output) in ["pres", "hght", "tmpc", "dwpc"].iter().zip(&mut scalars) {
        let input = surface.call_method1("get", (name,))?;
        *output = surface_scalar(&numpy, &keywords, &input)?;
    }
    let [Some(p), Some(z), Some(t), Some(td)] = scalars else {
        return Ok(None);
    };
    let Some(thermo) = GroundThermodynamics::verified([p, z, t, td], Some(missing)) else {
        return Ok(None);
    };
    let u = surface_scalar(&numpy, &keywords, &surface.call_method1("get", ("u",))?)?;
    let v = surface_scalar(&numpy, &keywords, &surface.call_method1("get", ("v",))?)?;
    let (Some(u), Some(v)) = (u, v) else {
        return Ok(None);
    };
    let Some(ground) = thermo.with_wind(u, v, Some(missing)) else {
        return Ok(None);
    };
    let merged = py.detach(move || merge(owned, ground, missing));
    let arrays = PyDict::new(py);
    for (name, values) in COLUMN_NAMES.iter().zip(merged.columns) {
        arrays.set_item(name, values.into_pyarray(py))?;
    }
    Ok(Some(
        PyModule::import(py, "sharpmod.models.model_surface")?
            .getattr("SurfaceMergeResult")?
            .call1((arrays, ground.pressure(), merged.removed))?
            .unbind(),
    ))
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(merge_model_surface, module)?)?;
    Ok(())
}

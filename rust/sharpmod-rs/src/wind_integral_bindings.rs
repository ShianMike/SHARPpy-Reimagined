//! Owned, mask-aware inputs for SharpTab's custom wind-layer calculations.

use crate::{
    interpolation::PreparedInterpolation, profile_input::column, wind_integrals::PressureWinds,
};
use pyo3::{
    exceptions::{PyAttributeError, PyValueError},
    prelude::*,
    types::PyTuple,
};

fn result<'py, const N: usize>(py: Python<'py>, values: [f64; N]) -> PyResult<Bound<'py, PyTuple>> {
    let missing = PyModule::import(py, "numpy.ma")?.getattr("masked")?;
    PyTuple::new(
        py,
        values.map(|x| {
            if x.is_nan() {
                missing.clone()
            } else {
                x.into_pyobject(py).unwrap().into_any()
            }
        }),
    )
}

#[pyfunction]
#[pyo3(signature = (prof, bottom, top, increment=-1.0, storm_u=0.0, storm_v=0.0, weighted=true))]
#[allow(clippy::too_many_arguments)]
fn pressure_layer_mean_wind<'py>(
    py: Python<'py>,
    prof: &Bound<'py, PyAny>,
    bottom: f64,
    top: f64,
    increment: f64,
    storm_u: f64,
    storm_v: f64,
    weighted: bool,
) -> PyResult<Bound<'py, PyTuple>> {
    let prepare = PyModule::import(py, "sharpmod.backends._common")?.getattr("prepare_1d")?;
    let interp = PyModule::import(py, "sharpmod.sharptab.interp")?;
    let logp = column(&prepare, &interp.call_method1("_logp", (prof,))?, "logp")?;
    let mut components = [Vec::new(), Vec::new()];
    for (index, name) in ["u", "v"].into_iter().enumerate() {
        let value = match prof.getattr(name) {
            Ok(value) => value,
            Err(error) if error.is_instance_of::<PyAttributeError>(py) => {
                return result(py, [f64::NAN; 2])
            }
            Err(error) => return Err(error),
        };
        if value.is_none() {
            return result(py, [f64::NAN; 2]);
        }
        components[index] = column(&prepare, &value, name)?;
    }
    let [u, v] = components;
    let values = py
        .detach(move || {
            PressureWinds::new(&logp, &u, &v)?.mean(
                [bottom, top],
                increment,
                [storm_u, storm_v],
                weighted,
            )
        })
        .map_err(PyValueError::new_err)?;
    result(py, values)
}

#[pyfunction]
fn profile_layer_helicity<'py>(
    py: Python<'py>,
    prof: &Bound<'py, PyAny>,
    lower: f64,
    upper: f64,
    storm_u: f64,
    storm_v: f64,
) -> PyResult<Bound<'py, PyTuple>> {
    let prepare = PyModule::import(py, "sharpmod.backends._common")?.getattr("prepare_1d")?;
    let interp = PyModule::import(py, "sharpmod.sharptab.interp")?;
    let masked = PyModule::import(py, "numpy.ma")?;
    let logp = column(&prepare, &interp.call_method1("_logp", (prof,))?, "logp")?;
    let height = column(&prepare, &prof.getattr("hght")?, "hght")?;
    // np.where on a masked pressure comparison uses its underlying boolean data.
    let pressure = column(
        &prepare,
        &masked.call_method1("getdata", (prof.getattr("pres")?,))?,
        "pres",
    )?;
    let u = column(&prepare, &prof.getattr("u")?, "u")?;
    let v = column(&prepare, &prof.getattr("v")?, "v")?;
    let mut msl = [0.0; 2];
    for (index, height) in [lower, upper].into_iter().enumerate() {
        let target = interp.call_method1("to_msl", (prof, height))?;
        msl[index] = masked.call_method1("getdata", (target,))?.extract()?;
    }
    let values = py
        .detach(move || {
            let heights = PreparedInterpolation::new(&height, &logp, None)?;
            let bounds = msl.map(|h| heights.scalar(h, true));
            PressureWinds::new(&logp, &u, &v)?.helicity(
                &pressure,
                &u,
                &v,
                bounds,
                [storm_u, storm_v],
            )
        })
        .map_err(PyValueError::new_err)?;
    result(py, values)
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(pressure_layer_mean_wind, module)?)?;
    module.add_function(wrap_pyfunction!(profile_layer_helicity, module)?)?;
    Ok(())
}

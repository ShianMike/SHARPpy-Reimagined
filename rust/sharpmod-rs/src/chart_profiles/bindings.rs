//! Reuse the existing profile and chart data contracts at the Python boundary.
use super::*;
use numpy::{IntoPyArray, PyReadonlyArray1};
use pyo3::{
    exceptions::{PyAttributeError, PyTypeError, PyValueError},
    prelude::*,
    types::{PyDict, PyTuple},
};

fn field<'py>(profile: &Bound<'py, PyAny>, name: &str) -> PyResult<Bound<'py, PyAny>> {
    profile.getattr(name).or_else(|error| {
        if error.is_instance_of::<PyAttributeError>(profile.py()) {
            Ok(profile.py().None().into_bound(profile.py()))
        } else {
            Err(error)
        }
    })
}

fn vector(value: &Bound<'_, PyAny>) -> PyResult<Option<Vec<f64>>> {
    let py = value.py();
    let kwargs = PyDict::new(py);
    kwargs.set_item("minimum_size", 2)?;
    // This is the same mask/shape boundary used by the existing Qt charts.
    let array = PyModule::import(py, "sharpmod.viz.streamwiseness")?
        .getattr("_finite_vector")?
        .call((value,), Some(&kwargs))?;
    if array.is_none() {
        return Ok(None);
    }
    Ok(Some(
        array
            .extract::<PyReadonlyArray1<'_, f64>>()?
            .as_array()
            .iter()
            .copied()
            .collect(),
    ))
}

fn surface(profile: &Bound<'_, PyAny>) -> PyResult<Option<usize>> {
    let py = profile.py();
    let value = field(profile, "sfc")?;
    if !value.is_truthy()? {
        return Ok(Some(0));
    }
    match PyModule::import(py, "builtins")?
        .getattr("int")?
        .call1((&value,))
    {
        Ok(value) => Ok(value.extract::<usize>().ok()),
        Err(error)
            if error.is_instance_of::<PyTypeError>(py)
                || error.is_instance_of::<PyValueError>(py) =>
        {
            Ok(Some(0))
        }
        Err(error) => Err(error),
    }
}

fn winds(profile: &Bound<'_, PyAny>) -> PyResult<Option<(Vec<f64>, Vec<f64>)>> {
    let values = PyModule::import(profile.py(), "sharpmod.viz.streamwiseness")?
        .call_method1("_wind_components", (profile,))?;
    let u = values.get_item(0)?;
    let v = values.get_item(1)?;
    if u.is_none() || v.is_none() {
        return Ok(None);
    }
    Ok(Some((
        u.extract::<PyReadonlyArray1<'_, f64>>()?
            .as_array()
            .iter()
            .copied()
            .collect(),
        v.extract::<PyReadonlyArray1<'_, f64>>()?
            .as_array()
            .iter()
            .copied()
            .collect(),
    )))
}

fn motion(profile: &Bound<'_, PyAny>, left: bool) -> PyResult<Option<[f64; 2]>> {
    PyModule::import(profile.py(), "sharpmod.viz.streamwiseness")?
        .call_method1("_storm_motion", (profile, left))?
        .extract()
}

#[pyfunction]
fn height_chart_grid<'py>(
    py: Python<'py>,
    profile: &Bound<'py, PyAny>,
    max_height_m: f64,
    step_m: f64,
) -> PyResult<Bound<'py, PyAny>> {
    let unavailable = || PyTuple::new(py, [py.None(), py.None(), py.None()]).map(Bound::into_any);
    let Some(spec) = GridSpec::new(max_height_m, step_m) else {
        return unavailable();
    };
    let Some(height) = vector(&field(profile, "hght")?)? else {
        return unavailable();
    };
    let Some(surface) = surface(profile)? else {
        return unavailable();
    };
    let Some(grid) = py.detach(|| height_grid(&height, surface, spec)) else {
        return unavailable();
    };
    PyTuple::new(
        py,
        [
            grid.samples.into_pyarray(py).into_any(),
            grid.agl.into_pyarray(py).into_any(),
            grid.valid.into_pyarray(py).into_any(),
        ],
    )
    .map(Bound::into_any)
}

#[pyfunction]
fn height_chart_interpolate<'py>(
    py: Python<'py>,
    samples: PyReadonlyArray1<'py, f64>,
    agl: PyReadonlyArray1<'py, f64>,
    valid: PyReadonlyArray1<'py, bool>,
    source: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let Some(values) = vector(source)? else {
        return Ok(py.None().into_bound(py));
    };
    let samples: Vec<_> = samples.as_array().iter().copied().collect();
    let agl: Vec<_> = agl.as_array().iter().copied().collect();
    let valid: Vec<_> = valid.as_array().iter().copied().collect();
    Ok(
        match py.detach(|| interpolate(&samples, &agl, &valid, &values)) {
            Some(values) => values.into_pyarray(py).into_any(),
            None => py.None().into_bound(py),
        },
    )
}

#[pyfunction]
fn height_chart_kelvin<'py>(
    py: Python<'py>,
    values: PyReadonlyArray1<'py, f64>,
) -> Bound<'py, numpy::PyArray1<f64>> {
    let values = values.as_array().iter().copied().collect();
    py.detach(|| kelvin(values)).into_pyarray(py)
}

#[pyfunction(signature=(profile, use_left=false, max_height_m=12000., step_m=250.))]
fn storm_relative_wind_chart<'py>(
    py: Python<'py>,
    profile: &Bound<'py, PyAny>,
    use_left: bool,
    max_height_m: f64,
    step_m: f64,
) -> PyResult<Bound<'py, PyAny>> {
    let none = || py.None().into_bound(py);
    let Some(spec) = GridSpec::new(max_height_m, step_m) else {
        return Ok(none());
    };
    if profile.is_none() {
        return Ok(none());
    }
    let Some(height) = vector(&field(profile, "hght")?)? else {
        return Ok(none());
    };
    let Some(surface) = surface(profile)? else {
        return Ok(none());
    };
    let Some(grid) = py.detach(|| height_grid(&height, surface, spec)) else {
        return Ok(none());
    };
    let Some((u, v)) = winds(profile)? else {
        return Ok(none());
    };
    let Some(motion) = motion(profile, use_left)? else {
        return Ok(none());
    };
    let Some(speed) = py.detach(|| storm_relative_wind(&grid, &u, &v, motion)) else {
        return Ok(none());
    };
    let kwargs = PyDict::new(py);
    kwargs.set_item(
        "height_km",
        grid.samples
            .into_iter()
            .map(|z| z / 1000.)
            .collect::<Vec<_>>()
            .into_pyarray(py),
    )?;
    let values = PyDict::new(py);
    values.set_item("srw", speed.into_pyarray(py))?;
    kwargs.set_item("values", values)?;
    PyModule::import(py, "sharpmod.viz.height_charts")?
        .getattr("HeightSeries")?
        .call((), Some(&kwargs))
}

#[pyfunction(signature=(profile, use_left=false, max_height_m=6000., step_m=100.))]
fn streamwiseness_chart<'py>(
    py: Python<'py>,
    profile: &Bound<'py, PyAny>,
    use_left: bool,
    max_height_m: f64,
    step_m: f64,
) -> PyResult<Bound<'py, PyAny>> {
    let none = || py.None().into_bound(py);
    let Some(spec) = GridSpec::new(max_height_m, step_m) else {
        return Ok(none());
    };
    if profile.is_none() {
        return Ok(none());
    }
    let Some(height) = vector(&field(profile, "hght")?)? else {
        return Ok(none());
    };
    let Some((u, v)) = winds(profile)? else {
        return Ok(none());
    };
    let Some(surface) = surface(profile)? else {
        return Ok(none());
    };
    let Some(motion) = motion(profile, use_left)? else {
        return Ok(none());
    };
    let Some(data) = py.detach(|| streamwiseness(&height, &u, &v, surface, motion, spec)) else {
        return Ok(none());
    };
    let kwargs = PyDict::new(py);
    kwargs.set_item("height_km", data.height.into_pyarray(py))?;
    kwargs.set_item("percent", data.percent.into_pyarray(py))?;
    kwargs.set_item("signed_percent", data.signed.into_pyarray(py))?;
    PyModule::import(py, "sharpmod.viz.streamwiseness")?
        .getattr("StreamwisenessData")?
        .call((), Some(&kwargs))
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(height_chart_grid, module)?)?;
    module.add_function(wrap_pyfunction!(height_chart_interpolate, module)?)?;
    module.add_function(wrap_pyfunction!(height_chart_kelvin, module)?)?;
    module.add_function(wrap_pyfunction!(storm_relative_wind_chart, module)?)?;
    module.add_function(wrap_pyfunction!(streamwiseness_chart, module)?)?;
    Ok(())
}

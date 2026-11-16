//! Thin Python compatibility wiring; encoding is owned by weather-core.
use pyo3::{
    exceptions::PyValueError,
    prelude::*,
    types::{PyDict, PyList},
};

#[pyfunction]
fn encode_webp_frame<'py>(
    py: Python<'py>,
    request: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyDict>> {
    if request.len() != 1 {
        return Err(PyValueError::new_err("Expected only the PNG frame."));
    }
    let png: String = request
        .get_item("png")?
        .ok_or_else(|| PyValueError::new_err("PNG frame is required."))?
        .extract()?;
    let result = py
        .detach(move || sharpmod_weather::animation::encode_png(&png))
        .map_err(PyValueError::new_err)?;
    let out = PyDict::new(py);
    out.set_item("width", result.width)?;
    out.set_item("height", result.height)?;
    out.set_item("data_base64", result.data_base64)?;
    Ok(out)
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(encode_webp_frame, module)?)?;
    module.add_function(wrap_pyfunction!(apply_animation_axes, module)?)
}

#[pyfunction]
#[pyo3(signature = (win, radius, metadata, mode="severe"))]
fn apply_animation_axes(
    win: &Bound<'_, PyAny>,
    radius: f64,
    metadata: &Bound<'_, PyDict>,
    mode: &str,
) -> PyResult<()> {
    if !radius.is_finite() || !(40.0..=500.0).contains(&radius) {
        return Err(PyValueError::new_err(
            "Animation range must be between 40 and 500 kt.",
        ));
    }
    let py = win.py();
    let sw = win.getattr("spc_widget")?;
    let hodo = sw.getattr("hodo")?;
    let width: f64 = hodo.call_method0("width")?.extract()?;
    let height: f64 = hodo.call_method0("height")?.extract()?;
    hodo.setattr("center_loc", "centered")?;
    hodo.setattr("centered", (0.0, 0.0))?;
    hodo.setattr("hodomag", 2.0 * radius * width / width.min(height).max(1.0))?;
    hodo.call_method0("initUI")?;
    hodo.call_method0("clearData")?;
    hodo.call_method0("plotData")?;
    if mode == "winter" {
        crate::winter_view::refresh_hodograph_markers(&sw)?;
    }
    hodo.call_method0("update")?;
    let interactive = PyModule::import(py, "sharpmod.rendering.interactive")?;
    let positions = interactive
        .call_method1("targets", (&sw,))?
        .cast_into::<PyList>()?;
    let old = metadata
        .get_item("points")?
        .ok_or_else(|| PyValueError::new_err("View points are missing."))?;
    let points = PyList::empty(py);
    for point in old.try_iter()? {
        let point = point?.cast_into::<PyDict>()?;
        for current in positions.iter() {
            if current.get_item("id")?.eq(point.get_item("id")?.unwrap())? {
                point.set_item("x", current.get_item("x")?)?;
                point.set_item("y", current.get_item("y")?)?;
                points.append(&point)?;
                break;
            }
        }
    }
    metadata.set_item("points", points)?;
    metadata.set_item(
        "inspection",
        interactive.call_method1("inspection", (&sw,))?,
    )?;
    Ok(())
}

#[cfg(test)]
mod tests;

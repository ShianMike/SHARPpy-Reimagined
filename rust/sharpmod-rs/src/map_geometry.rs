//! Native map geometry bindings; no Python work runs in the extraction loop.

use numpy::PyReadonlyArrayDyn;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyDict;
use serde_json::{Map, Value};
use sharpmod_weather::spc_outlook::forecast::{build_forecast, ForecastPolygon, ForecastProduct};

#[pyfunction(signature = (field, interval, max_segments=100_000, bounds=None))]
fn contour_segments(
    py: Python<'_>,
    field: PyReadonlyArrayDyn<'_, f64>,
    interval: f64,
    max_segments: usize,
    bounds: Option<(f64, f64, f64, f64)>,
) -> PyResult<Py<PyAny>> {
    let field = field.as_array();
    if field.ndim() != 2 {
        return Ok(Vec::<crate::contours::Segment>::new()
            .into_pyobject(py)?
            .into_any()
            .unbind());
    }
    let (height, width) = (field.shape()[0], field.shape()[1]);
    let values: Vec<f64> = field.iter().copied().collect();
    let segments = py.detach(move || {
        let mut segments =
            crate::contours::extract_display(&values, height, width, interval, max_segments)
                .map_err(PyValueError::new_err)?;
        if let Some(bounds) = bounds {
            crate::contours::project(&mut segments, height, width, bounds)
                .map_err(PyValueError::new_err)?;
        }
        Ok::<_, PyErr>(segments)
    })?;
    if bounds.is_some() {
        let segments: Vec<[[f64; 2]; 2]> = segments
            .into_iter()
            .map(|(a, b)| [[a.0, a.1], [b.0, b.1]])
            .collect();
        Ok(segments.into_pyobject(py)?.into_any().unbind())
    } else {
        Ok(segments.into_pyobject(py)?.into_any().unbind())
    }
}

#[pyfunction]
fn build_forecast_drawing<'py>(
    py: Python<'py>,
    params: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyAny>> {
    let json = PyModule::import(py, "json")?;
    let params: String = json.call_method1("dumps", (params,))?.extract()?;
    let drawing = py
        .detach(move || {
            let mut args: Map<String, Value> =
                serde_json::from_str(&params).map_err(|error| error.to_string())?;
            if args.len() != 2 {
                return Err("expected product and polygons".to_owned());
            }
            let product: ForecastProduct =
                serde_json::from_value(args.remove("product").unwrap_or_default())
                    .map_err(|error| format!("product: {error}"))?;
            let polygons: Vec<ForecastPolygon> =
                serde_json::from_value(args.remove("polygons").unwrap_or_default())
                    .map_err(|error| format!("polygons: {error}"))?;
            build_forecast(product, &polygons)
        })
        .map_err(|message| {
            match PyModule::import(py, "sharpmod.bridge.params")
                .and_then(|module| module.call_method1("invalid", (message.trim_end_matches('.'),)))
            {
                Ok(error) => PyErr::from_value(error),
                Err(error) => error,
            }
        })?;
    let text = serde_json::to_string(&drawing)
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
    json.call_method1("loads", (text,))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(contour_segments, module)?)?;
    module.add_function(wrap_pyfunction!(build_forecast_drawing, module)?)
}

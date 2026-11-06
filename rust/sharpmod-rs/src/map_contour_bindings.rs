//! Complete display geometry and real contour-level label anchors for map frames.

use std::collections::HashSet;

use numpy::{AllowTypeChange, PyArrayLikeDyn, PyUntypedArrayMethods};
use pyo3::{exceptions::PyValueError, prelude::*, types::PyDict};

use crate::{
    contours::{self, Projection, Segment},
    isobar_paths::smooth_isobars,
};

struct Label {
    position: [f64; 2],
    value: f64,
}

#[derive(Default)]
struct Geometry {
    segments: Vec<Segment>,
    paths: Vec<Vec<[f64; 2]>>,
    labels: Vec<Label>,
}

fn label_anchors(
    candidates: impl IntoIterator<Item = (f64, [f64; 2])>,
    bounds: (f64, f64, f64, f64),
) -> Vec<Label> {
    let (west, east, south, north) = bounds;
    let mut occupied = HashSet::new();
    let mut labels = Vec::new();
    for (value, position) in candidates {
        let cell = (
            ((position[0] - west) / (east - west) * 24.0).floor() as i32,
            ((position[1] - south) / (north - south) * 12.0).floor() as i32,
        );
        if occupied.insert(cell) {
            labels.push(Label { position, value });
            if labels.len() == 256 {
                break;
            }
        }
    }
    labels
}

fn display_geometry(
    values: &[f64],
    height: usize,
    width: usize,
    interval: f64,
    bounds: (f64, f64, f64, f64),
    isobars: bool,
) -> Result<Geometry, String> {
    if !isobars {
        let mut segments = contours::extract_display(values, height, width, interval, 100_000)?;
        contours::project(&mut segments, height, width, bounds)?;
        return Ok(Geometry {
            segments,
            ..Geometry::default()
        });
    }
    let segments = contours::extract_display_levels(values, height, width, interval, 100_000)?;
    let projection = Projection::new(height, width, bounds)?;
    let mut paths = smooth_isobars(&segments, values, height, width);
    for (_, path) in &mut paths {
        for point in path {
            *point = projection.point(*point);
        }
    }
    let labels = label_anchors(
        paths.iter().flat_map(|(value, path)| {
            path.windows(2).map(|pair| {
                let [a, b] = [pair[0], pair[1]];
                (*value, [(a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0])
            })
        }),
        bounds,
    );
    Ok(Geometry {
        paths: paths.into_iter().map(|(_, path)| path).collect(),
        labels,
        ..Geometry::default()
    })
}

#[pyfunction(signature=(product,run_time,forecast_hour,unit,interval,field,bounds,wind=None))]
#[allow(clippy::too_many_arguments)]
fn map_contour_frame(
    py: Python<'_>,
    product: &str,
    run_time: &str,
    forecast_hour: u32,
    unit: &str,
    interval: f64,
    field: Option<PyArrayLikeDyn<'_, f64, AllowTypeChange>>,
    bounds: (f64, f64, f64, f64),
    wind: Option<Py<PyAny>>,
) -> PyResult<Py<PyAny>> {
    let Geometry {
        segments,
        paths,
        labels,
    } = match field {
        None => Geometry::default(),
        Some(field) => {
            if field.ndim() != 2 {
                return Err(PyValueError::new_err(
                    "contour field must be a two-dimensional grid",
                ));
            }
            let (height, width) = (field.shape()[0], field.shape()[1]);
            let values: Vec<f64> = field.as_array().iter().copied().collect();
            let isobars = product == "mslp";
            let output = py.detach(move || {
                display_geometry(&values, height, width, interval, bounds, isobars)
            });
            match output {
                Ok(output) => output,
                Err(message) => {
                    let kind =
                        PyModule::import(py, "sharpmod.bridge.errors")?.getattr("BridgeError")?;
                    return Err(PyErr::from_value(kind.call1((
                        "FIELD_FETCH_FAILED",
                        message,
                        false,
                    ))?));
                }
            }
        }
    };
    let result = PyDict::new(py);
    result.set_item("product", product)?;
    result.set_item("run_time", run_time)?;
    result.set_item("forecast_hour", forecast_hour)?;
    result.set_item("unit", unit)?;
    result.set_item("interval", interval)?;
    result.set_item(
        "segments",
        segments
            .into_iter()
            .map(|(a, b)| [[a.0, a.1], [b.0, b.1]])
            .collect::<Vec<_>>(),
    )?;
    result.set_item("wind", wind)?;
    if !paths.is_empty() {
        result.set_item("paths", paths)?;
    }
    if !labels.is_empty() {
        let labels = labels
            .into_iter()
            .map(|label| {
                let value = PyDict::new(py);
                value.set_item("position", label.position)?;
                value.set_item("value", label.value)?;
                Ok(value)
            })
            .collect::<PyResult<Vec<_>>>()?;
        result.set_item("labels", labels)?;
    }
    Ok(result.into_any().unbind())
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(map_contour_frame, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn numeric_labels_use_actual_levels_and_lie_on_their_own_segment() {
        let levels = [996.0, 1000.0, 1004.0];
        let segments = [
            ((0.0, 1.0), (0.0, 3.0)),
            ((10.0, 2.0), (12.0, 2.0)),
            ((20.0, 3.0), (22.0, 3.0)),
        ];
        let labels = label_anchors(
            levels
                .into_iter()
                .zip(segments)
                .map(|(value, (a, b))| (value, [(a.0 + b.0) / 2.0, (a.1 + b.1) / 2.0])),
            (-30.0, 30.0, 0.0, 10.0),
        );
        assert_eq!(labels.len(), 3);
        for (index, label) in labels.iter().enumerate() {
            assert_eq!(label.value, levels[index]);
            let (a, b) = segments[index];
            assert_eq!(label.position, [(a.0 + b.0) / 2.0, (a.1 + b.1) / 2.0]);
        }
    }

    #[test]
    fn pressure_labels_are_sparse_and_bounded_without_inventing_values() {
        let segments: Vec<_> = (0..100_000)
            .map(|index| {
                let x = f64::from(index % 1000) / 1000.0 * 360.0 - 180.0;
                let y = f64::from(index / 1000) / 100.0 * 170.0 - 85.0;
                ((x, y), (x + 0.001, y))
            })
            .collect();
        let labels = label_anchors(
            segments
                .into_iter()
                .map(|(a, b)| (1012.0, [(a.0 + b.0) / 2.0, (a.1 + b.1) / 2.0])),
            (-180.0, 180.0, -85.0, 85.0),
        );
        assert!(labels.len() > 10 && labels.len() <= 256);
        assert!(labels.iter().all(|label| label.value == 1012.0));
        assert!(label_anchors([], (-180.0, 180.0, -85.0, 85.0)).is_empty());
    }
}

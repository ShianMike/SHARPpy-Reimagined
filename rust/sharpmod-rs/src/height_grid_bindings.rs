//! Validate NumPy layouts once and detach owned, bounded interpolation batches.

use numpy::{
    ndarray::{ArrayD, IxDyn},
    AllowTypeChange, IntoPyArray, PyArrayDyn, PyArrayLikeDyn, PyReadonlyArrayDyn,
    PyUntypedArrayMethods,
};
use pyo3::{exceptions::PyValueError, prelude::*};
use std::borrow::Cow;

#[derive(FromPyObject)]
enum Grid<'py> {
    #[pyo3(transparent)]
    Single(PyReadonlyArrayDyn<'py, f32>),
    #[pyo3(transparent)]
    Double(PyArrayLikeDyn<'py, f64, AllowTypeChange>),
}

enum Buffer<'a> {
    Single(Cow<'a, [f32]>),
    Double(Cow<'a, [f64]>),
}

impl<'py> Grid<'py> {
    fn shape(&self) -> &[usize] {
        match self {
            Self::Single(array) => array.shape(),
            Self::Double(array) => array.shape(),
        }
    }

    fn buffer(&self) -> Buffer<'_> {
        match self {
            Self::Single(array) => Buffer::Single(match array.as_slice() {
                Ok(values) => Cow::Borrowed(values),
                Err(_) => Cow::Owned(array.as_array().iter().copied().collect()),
            }),
            Self::Double(array) => Buffer::Double(match array.as_slice() {
                Ok(values) => Cow::Borrowed(values),
                Err(_) => Cow::Owned(array.as_array().iter().copied().collect()),
            }),
        }
    }
}

impl Buffer<'_> {
    fn append(&self, first: usize, end: usize, output: &mut Vec<f64>) {
        match self {
            Self::Single(values) => {
                output.extend(values[first..end].iter().map(|&value| value as f64))
            }
            Self::Double(values) => output.extend_from_slice(&values[first..end]),
        }
    }
}

#[pyfunction(signature=(heights, values, target, missing=f64::NAN))]
fn interpolate_height_grid<'py>(
    py: Python<'py>,
    heights: Vec<Grid<'py>>,
    values: Vec<Grid<'py>>,
    target: Grid<'py>,
    missing: f64,
) -> PyResult<Bound<'py, PyArrayDyn<f64>>> {
    let output_shape = target.shape().to_vec();
    if heights.len() != values.len()
        || heights
            .iter()
            .chain(&values)
            .any(|grid| grid.shape() != output_shape)
    {
        return Err(PyValueError::new_err(
            "height/value layers and target grid do not match",
        ));
    }
    let levels = heights.len();
    let heights: Vec<_> = heights.iter().map(Grid::buffer).collect();
    let values: Vec<_> = values.iter().map(Grid::buffer).collect();
    let target = target.buffer();
    let precision = crate::height_grid::Precision {
        heights_f32: heights.iter().all(|grid| matches!(grid, Buffer::Single(_))),
        values_f32: values.iter().all(|grid| matches!(grid, Buffer::Single(_))),
        target_f32: matches!(target, Buffer::Single(_)),
    };
    let cells = output_shape.iter().product::<usize>();
    let mut output = Vec::with_capacity(cells);
    for first in (0..cells).step_by(131072) {
        let end = (first + 131072).min(cells);
        let mut height_batch = Vec::with_capacity((end - first) * levels);
        let mut value_batch = Vec::with_capacity((end - first) * levels);
        let mut target_batch = Vec::with_capacity(end - first);
        for (height, value) in heights.iter().zip(&values) {
            height.append(first, end, &mut height_batch);
            value.append(first, end, &mut value_batch);
        }
        target.append(first, end, &mut target_batch);
        output.extend(py.detach(move || {
            crate::height_grid::interpolate(
                &height_batch,
                &value_batch,
                &target_batch,
                levels,
                precision,
                missing,
            )
        }));
    }
    let output = ArrayD::from_shape_vec(IxDyn(&output_shape), output)
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    Ok(output.into_pyarray(py))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(interpolate_height_grid, module)?)?;
    Ok(())
}

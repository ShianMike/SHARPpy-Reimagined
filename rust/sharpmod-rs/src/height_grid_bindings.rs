//! Validate NumPy layouts once and detach owned, bounded interpolation batches.

use numpy::{
    ndarray::{ArrayD, ArrayViewD, IxDyn},
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

fn logical_values<T: Copy>(array: ArrayViewD<'_, T>) -> Cow<'_, [T]> {
    match array.to_slice() {
        Some(values) => Cow::Borrowed(values),
        None => Cow::Owned(array.iter().copied().collect()),
    }
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
            Self::Single(array) => Buffer::Single(logical_values(array.as_array())),
            Self::Double(array) => Buffer::Double(logical_values(array.as_array())),
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

#[cfg(test)]
mod tests {
    use super::*;
    use numpy::ndarray::{array, s, Array2, ShapeBuilder};

    #[test]
    fn c_fortran_transposed_and_strided_grids_keep_logical_cell_order() {
        let c = array![[1_f32, 2., 3.], [4., 5., 6.]];
        assert!(matches!(
            logical_values(c.view().into_dyn()),
            Cow::Borrowed(_)
        ));
        let fortran = Array2::from_shape_vec((2, 3).f(), vec![1_f32, 4., 2., 5., 3., 6.]).unwrap();
        assert_eq!(
            logical_values(fortran.view().into_dyn()).as_ref(),
            &[1., 2., 3., 4., 5., 6.]
        );
        assert_eq!(
            logical_values(c.t().into_dyn()).as_ref(),
            &[1., 4., 2., 5., 3., 6.]
        );
        assert_eq!(
            logical_values(c.slice(s![.., ..;-1]).into_dyn()).as_ref(),
            &[3., 2., 1., 6., 5., 4.]
        );
        let double = fortran.mapv(f64::from);
        assert_eq!(
            logical_values(double.view().into_dyn()).as_ref(),
            &[1., 2., 3., 4., 5., 6.]
        );
    }
}

//! NumPy layout and broadcasting boundary shared by native field calculations.

use numpy::{
    ndarray::{ArrayD, ArrayViewD, IxDyn},
    AllowTypeChange, IntoPyArray, PyArrayLike0, PyArrayLikeDyn, PyReadonlyArrayDyn,
    PyUntypedArrayMethods, TypeMustMatch,
};
use pyo3::{exceptions::PyValueError, prelude::*};

#[derive(FromPyObject)]
pub(crate) enum NumericGrid<'py> {
    #[pyo3(transparent)]
    Single(PyReadonlyArrayDyn<'py, f32>),
    #[pyo3(transparent)]
    SingleScalar(PyArrayLike0<'py, f32, TypeMustMatch>),
    #[pyo3(transparent)]
    Double(PyArrayLikeDyn<'py, f64, AllowTypeChange>),
}

pub(crate) enum Buffer {
    Single(Vec<f32>),
    Double(Vec<f64>),
}

impl Buffer {
    pub fn value(&self, index: usize) -> f64 {
        match self {
            Self::Single(values) => f64::from(values[if values.len() == 1 { 0 } else { index }]),
            Self::Double(values) => values[if values.len() == 1 { 0 } else { index }],
        }
    }
    pub fn single(&self) -> bool {
        matches!(self, Self::Single(_))
    }
}

pub(crate) struct Grids {
    pub shape: Vec<usize>,
    pub buffers: Vec<Buffer>,
    pub cells: usize,
}

fn materialize<T: Copy>(values: ArrayViewD<'_, T>) -> Vec<T> {
    values.iter().copied().collect()
}

fn expand<T: Copy>(shape: &[usize], dimensions: &[usize], values: Vec<T>) -> Vec<T> {
    if dimensions == shape || values.len() == 1 {
        values
    } else {
        ArrayD::from_shape_vec(IxDyn(dimensions), values)
            .expect("validated NumPy dimensions")
            .broadcast(IxDyn(shape))
            .expect("validated broadcast dimensions")
            .iter()
            .copied()
            .collect()
    }
}

pub(crate) fn broadcast(inputs: Vec<NumericGrid<'_>>) -> PyResult<Grids> {
    let mut shape = Vec::new();
    let mut arrays = Vec::with_capacity(inputs.len());
    for input in inputs {
        let (dimensions, buffer) = match input {
            NumericGrid::Single(array) => (
                array.shape().to_vec(),
                Buffer::Single(materialize(array.as_array())),
            ),
            NumericGrid::SingleScalar(array) => (
                Vec::new(),
                Buffer::Single(materialize(array.as_array().into_dyn())),
            ),
            NumericGrid::Double(array) => (
                array.shape().to_vec(),
                Buffer::Double(materialize(array.as_array())),
            ),
        };
        shape = common_shape(&shape, &dimensions).map_err(PyValueError::new_err)?;
        arrays.push((dimensions, buffer));
    }
    let cells = shape
        .iter()
        .try_fold(1usize, |count, &n| count.checked_mul(n))
        .ok_or_else(|| PyValueError::new_err("broadcast grid dimensions overflow"))?;
    let buffers = arrays
        .into_iter()
        .map(|(dimensions, buffer)| match buffer {
            Buffer::Single(values) => Buffer::Single(expand(&shape, &dimensions, values)),
            Buffer::Double(values) => Buffer::Double(expand(&shape, &dimensions, values)),
        })
        .collect();
    Ok(Grids {
        shape,
        buffers,
        cells,
    })
}

fn common_shape(left: &[usize], right: &[usize]) -> Result<Vec<usize>, &'static str> {
    let rank = left.len().max(right.len());
    let mut output = vec![1; rank];
    for (shape, offset) in [(left, rank - left.len()), (right, rank - right.len())] {
        for (index, &size) in shape.iter().enumerate() {
            let slot = &mut output[offset + index];
            if *slot == 1 {
                *slot = size;
            } else if size != 1 && *slot != size {
                return Err("field arrays cannot be broadcast together");
            }
        }
    }
    Ok(output)
}

pub(crate) fn output(py: Python<'_>, shape: &[usize], values: Vec<f64>, single: bool) -> Py<PyAny> {
    if single {
        ArrayD::from_shape_vec(IxDyn(shape), values.into_iter().map(|v| v as f32).collect())
            .expect("preserved field dimensions")
            .into_pyarray(py)
            .into_any()
            .unbind()
    } else {
        ArrayD::from_shape_vec(IxDyn(shape), values)
            .expect("preserved field dimensions")
            .into_pyarray(py)
            .into_any()
            .unbind()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use numpy::ndarray::{array, s, Array2, ShapeBuilder};

    #[test]
    fn fortran_transposed_and_negative_stride_inputs_keep_logical_cell_order() {
        let grid = array![[1.0f32, 2.0, 3.0], [4.0, 5.0, 6.0]];
        let fortran =
            Array2::from_shape_vec((2, 3).f(), vec![1.0f64, 4.0, 2.0, 5.0, 3.0, 6.0]).unwrap();
        assert_eq!(
            materialize(grid.view().into_dyn()),
            [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        );
        assert_eq!(
            materialize(fortran.view().into_dyn()),
            [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        );
        assert_eq!(
            materialize(grid.t().into_dyn()),
            [1.0, 4.0, 2.0, 5.0, 3.0, 6.0]
        );
        assert_eq!(
            materialize(grid.slice(s![..,..;-1]).into_dyn()),
            [3.0, 2.0, 1.0, 6.0, 5.0, 4.0]
        );
    }
    #[test]
    fn broadcasting_preserves_axes_scalars_empty_grids_and_rejects_conflicts() {
        assert_eq!(common_shape(&[2, 1], &[3]).unwrap(), [2, 3]);
        assert_eq!(common_shape(&[], &[2, 3]).unwrap(), [2, 3]);
        assert_eq!(common_shape(&[0, 3], &[1, 3]).unwrap(), [0, 3]);
        assert!(common_shape(&[2, 3], &[4]).is_err());
        assert_eq!(common_shape(&[], &[]).unwrap(), Vec::<usize>::new());
    }
}

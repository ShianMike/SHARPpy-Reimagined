//! NumPy compatibility boundary for model raster rendering and browser encoding.

use crate::{
    map_raster::{self, Palette},
    numeric_grid::{self, NumericGrid},
};
use numpy::{
    ndarray::{ArrayD, IxDyn},
    IntoPyArray, PyArrayDyn, PyReadonlyArrayDyn, PyReadwriteArrayDyn, PyUntypedArrayMethods,
};
use pyo3::{exceptions::PyValueError, prelude::*, types::PyBytes};

#[derive(FromPyObject)]
struct PaletteParameters {
    stops: Vec<(f64, String)>,
    stepped: bool,
    floor: Option<f64>,
    ceiling: Option<f64>,
}

impl PaletteParameters {
    fn palette(self) -> PyResult<Palette> {
        Palette::new(self.stops, self.stepped, self.floor, self.ceiling)
            .map_err(PyValueError::new_err)
    }
}

#[pyfunction(signature=(palette, size=1024))]
fn build_map_lut<'py>(
    py: Python<'py>,
    palette: PaletteParameters,
    size: usize,
) -> PyResult<(Bound<'py, PyArrayDyn<u8>>, f64, f64)> {
    let palette = palette.palette()?;
    let (low, high) = palette.limits();
    let table = py
        .detach(|| palette.lut(size))
        .map_err(PyValueError::new_err)?;
    Ok((
        ArrayD::from_shape_vec(IxDyn(&[size, 4]), table)
            .expect("validated lookup dimensions")
            .into_pyarray(py),
        low,
        high,
    ))
}

#[pyfunction(signature=(field,palette,size=1024))]
fn colorize_map_field<'py>(
    py: Python<'py>,
    field: NumericGrid<'py>,
    palette: PaletteParameters,
    size: usize,
) -> PyResult<Bound<'py, PyArrayDyn<u8>>> {
    let palette = palette.palette()?;
    let mut grids = numeric_grid::broadcast(vec![field])?;
    let pixels = py
        .detach(|| palette.colorize(&grids.buffers[0], grids.cells, size))
        .map_err(PyValueError::new_err)?;
    grids.shape.push(4);
    Ok(ArrayD::from_shape_vec(IxDyn(&grids.shape), pixels)
        .expect("preserved raster dimensions")
        .into_pyarray(py))
}

#[pyfunction(signature=(rgba,field,interval,colour,width=1.2))]
fn draw_map_contours(
    py: Python<'_>,
    mut rgba: PyReadwriteArrayDyn<'_, u8>,
    field: NumericGrid<'_>,
    interval: f64,
    colour: &str,
    width: f64,
) -> PyResult<()> {
    if !interval.is_finite() || interval <= 0.0 {
        return Ok(());
    }
    let grids = numeric_grid::broadcast(vec![field])?;
    if grids.shape.len() != 2
        || rgba.ndim() != 3
        || rgba.shape()[2] != 4
        || rgba.shape()[..2] != grids.shape
    {
        return Err(PyValueError::new_err(
            "contour field and RGBA image dimensions do not match",
        ));
    }
    let color = map_raster::rgb(colour).map_err(PyValueError::new_err)?;
    let mut pixels = rgba.as_array().iter().copied().collect::<Vec<_>>();
    py.detach(|| {
        map_raster::draw_contours(
            &mut pixels,
            &grids.buffers[0],
            grids.shape[0],
            grids.shape[1],
            interval,
            color,
            width,
        )
    });
    for (target, value) in rgba.as_array_mut().iter_mut().zip(pixels) {
        *target = value;
    }
    Ok(())
}

#[pyfunction]
fn quantize_map_field<'py>(
    py: Python<'py>,
    values: NumericGrid<'py>,
) -> PyResult<(Bound<'py, PyBytes>, f64, f64, f64)> {
    let grids = numeric_grid::broadcast(vec![values])?;
    let (bytes, scale, low, high) =
        py.detach(|| crate::map_quantization::quantize(&grids.buffers[0], grids.cells));
    Ok((PyBytes::new(py, &bytes), scale, low, high))
}

#[pyfunction]
fn encode_map_png<'py>(
    py: Python<'py>,
    rgba: PyReadonlyArrayDyn<'py, u8>,
    max_bytes: usize,
) -> PyResult<Bound<'py, PyBytes>> {
    let fail = |message: String| {
        crate::model_http_bindings::field_error(
            py,
            sharpmod_weather::model_http::Error::Invalid(message),
        )
    };
    if rgba.ndim() != 3 || rgba.shape()[2] != 4 {
        return Err(fail("field image must be an RGBA array".into()));
    }
    let (height, width) = (rgba.shape()[0], rgba.shape()[1]);
    let width = u32::try_from(width)
        .map_err(|_| fail("field image width exceeds PNG dimensions".into()))?;
    let height = u32::try_from(height)
        .map_err(|_| fail("field image height exceeds PNG dimensions".into()))?;
    let pixels = rgba.as_array().iter().copied().collect::<Vec<_>>();
    let encoded = py
        .detach(|| sharpmod_weather::raster_png::encode(width, height, &pixels, max_bytes))
        .map_err(fail)?;
    Ok(PyBytes::new(py, &encoded))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(build_map_lut, module)?)?;
    module.add_function(wrap_pyfunction!(colorize_map_field, module)?)?;
    module.add_function(wrap_pyfunction!(draw_map_contours, module)?)?;
    module.add_function(wrap_pyfunction!(quantize_map_field, module)?)?;
    module.add_function(wrap_pyfunction!(encode_map_png, module)?)?;
    Ok(())
}

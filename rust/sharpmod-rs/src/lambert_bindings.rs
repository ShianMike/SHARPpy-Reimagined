//! Preserve the public grid dataclass while native code owns its geometry.

use crate::lambert::{Grid, Specification};
use pyo3::{exceptions::PyValueError, prelude::*, types::PyTuple};

#[pyfunction]
fn lambert_normalize_longitude(lon: f64) -> f64 {
    crate::lambert::normalize_longitude(lon)
}

#[derive(FromPyObject)]
struct Parameters {
    lat_origin: f64,
    lon_origin: f64,
    lat_1: f64,
    lat_2: f64,
    radius: f64,
    shape: (usize, usize),
    spacing: f64,
    x0: f64,
    y0: f64,
}

impl Parameters {
    fn grid(self) -> PyResult<Grid> {
        Grid::new(Specification {
            lat_origin: self.lat_origin,
            lon_origin: self.lon_origin,
            lat_1: self.lat_1,
            lat_2: self.lat_2,
            radius: self.radius,
            shape: self.shape,
            spacing: self.spacing,
            x0: self.x0,
            y0: self.y0,
        })
        .map_err(PyValueError::new_err)
    }
}

#[pyfunction]
fn lambert_constants(grid: Parameters) -> PyResult<(f64, f64, f64)> {
    Ok(grid.grid()?.constants())
}
#[pyfunction]
fn lambert_extent(grid: Parameters) -> PyResult<(f64, f64)> {
    Ok(grid.grid()?.extent())
}
#[pyfunction]
fn lambert_forward(grid: Parameters, lon: f64, lat: f64) -> PyResult<(f64, f64)> {
    Ok(grid.grid()?.forward(lon, lat))
}
#[pyfunction]
fn lambert_inverse(grid: Parameters, x: f64, y: f64) -> PyResult<(f64, f64)> {
    Ok(grid.grid()?.inverse(x, y))
}
#[pyfunction]
fn lambert_contains(grid: Parameters, lat: f64, lon: f64) -> PyResult<bool> {
    Ok(grid.grid()?.contains(lat, lon))
}

#[pyfunction(signature=(grid, samples_per_edge=32))]
fn lambert_outline<'py>(
    py: Python<'py>,
    grid: Parameters,
    samples_per_edge: isize,
) -> PyResult<Bound<'py, PyTuple>> {
    let grid = grid.grid()?;
    let points = py.detach(|| grid.outline(samples_per_edge.max(2) as usize));
    PyTuple::new(py, points)
}

#[pyfunction(signature=(grid, samples_per_edge=32))]
fn lambert_bounds(grid: Parameters, samples_per_edge: isize) -> PyResult<(f64, f64, f64, f64)> {
    Ok(grid.grid()?.bounds(samples_per_edge.max(2) as usize))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(lambert_normalize_longitude, module)?)?;
    module.add_function(wrap_pyfunction!(lambert_constants, module)?)?;
    module.add_function(wrap_pyfunction!(lambert_extent, module)?)?;
    module.add_function(wrap_pyfunction!(lambert_forward, module)?)?;
    module.add_function(wrap_pyfunction!(lambert_inverse, module)?)?;
    module.add_function(wrap_pyfunction!(lambert_contains, module)?)?;
    module.add_function(wrap_pyfunction!(lambert_outline, module)?)?;
    module.add_function(wrap_pyfunction!(lambert_bounds, module)?)?;
    Ok(())
}

//! PyO3 bindings for SHARPpy Reimagined's optional acceleration kernels.

pub mod grib;
pub mod interpolation;
pub mod parsing;
pub mod quality_control;
pub mod records;
pub mod wind;

use numpy::ndarray::Array2;
use numpy::{IntoPyArray, PyArray1, PyArray2, PyReadonlyArray1};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

type PyArrayPair<'py> = (Bound<'py, PyArray1<f64>>, Bound<'py, PyArray1<f64>>);
type PyDecodedPoint<'py> = (Bound<'py, PyArray2<f64>>, f64, f64, Option<f64>);
const BACKEND_API_VERSION: u32 = 2;

fn value_error(message: String) -> PyErr {
    PyValueError::new_err(message)
}

#[pyfunction(
    name = "wind_to_components",
    signature = (direction, speed, missing=None)
)]
fn py_wind_to_components<'py>(
    py: Python<'py>,
    direction: PyReadonlyArray1<'py, f64>,
    speed: PyReadonlyArray1<'py, f64>,
    missing: Option<f64>,
) -> PyResult<PyArrayPair<'py>> {
    let (u, v) = wind::wind_to_components(direction.as_slice()?, speed.as_slice()?, missing)
        .map_err(value_error)?;
    Ok((u.into_pyarray(py), v.into_pyarray(py)))
}

#[pyfunction(
    name = "components_to_wind",
    signature = (u, v, missing=None)
)]
fn py_components_to_wind<'py>(
    py: Python<'py>,
    u: PyReadonlyArray1<'py, f64>,
    v: PyReadonlyArray1<'py, f64>,
    missing: Option<f64>,
) -> PyResult<PyArrayPair<'py>> {
    let (direction, speed) =
        wind::components_to_wind(u.as_slice()?, v.as_slice()?, missing).map_err(value_error)?;
    Ok((direction.into_pyarray(py), speed.into_pyarray(py)))
}

#[pyfunction(
    name = "interpolate_1d",
    signature = (targets, coordinates, values, missing=None, log_output=false)
)]
fn py_interpolate_1d<'py>(
    py: Python<'py>,
    targets: PyReadonlyArray1<'py, f64>,
    coordinates: PyReadonlyArray1<'py, f64>,
    values: PyReadonlyArray1<'py, f64>,
    missing: Option<f64>,
    log_output: bool,
) -> PyResult<Bound<'py, PyArray1<f64>>> {
    let output = interpolation::interpolate_1d(
        targets.as_slice()?,
        coordinates.as_slice()?,
        values.as_slice()?,
        missing,
        log_output,
    )
    .map_err(value_error)?;
    Ok(output.into_pyarray(py))
}

#[pyfunction(
    name = "basic_sounding_qc",
    signature = (pres, hght, tmpc, dwpc, wdir, wspd, missing=-9999.0)
)]
fn py_basic_sounding_qc(
    pres: PyReadonlyArray1<'_, f64>,
    hght: PyReadonlyArray1<'_, f64>,
    tmpc: PyReadonlyArray1<'_, f64>,
    dwpc: PyReadonlyArray1<'_, f64>,
    wdir: PyReadonlyArray1<'_, f64>,
    wspd: PyReadonlyArray1<'_, f64>,
    missing: Option<f64>,
) -> PyResult<(bool, usize, Vec<String>)> {
    let result = quality_control::basic_sounding_qc(
        pres.as_slice()?,
        hght.as_slice()?,
        tmpc.as_slice()?,
        dwpc.as_slice()?,
        wdir.as_slice()?,
        wspd.as_slice()?,
        missing,
    )
    .map_err(value_error)?;
    Ok((result.valid, result.valid_level_count, result.issues))
}

#[pyfunction(
    name = "pressure_sort_dedup_indices",
    signature = (pressure, missing=-9999.0)
)]
fn py_pressure_sort_dedup_indices<'py>(
    py: Python<'py>,
    pressure: PyReadonlyArray1<'py, f64>,
    missing: Option<f64>,
) -> PyResult<Bound<'py, PyArray1<usize>>> {
    let indices = records::pressure_sort_dedup_indices(pressure.as_slice()?, missing);
    Ok(indices.into_pyarray(py))
}

#[pyfunction(
    name = "parse_sounding_rows",
    signature = (text, missing=-9999.0)
)]
fn py_parse_sounding_rows<'py>(
    py: Python<'py>,
    text: &str,
    missing: Option<f64>,
) -> PyResult<Bound<'py, PyArray2<f64>>> {
    let rows =
        parsing::parse_sounding_rows(text, missing.unwrap_or(f64::NAN)).map_err(value_error)?;
    let row_count = rows.len();
    let flat = rows.into_iter().flatten().collect();
    let matrix = Array2::from_shape_vec((row_count, 6), flat)
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    Ok(matrix.into_pyarray(py))
}

#[pyfunction(
    name = "decode_grib_point",
    signature = (path, eccodes_library_path, latitude, longitude, missing=-9999.0)
)]
fn py_decode_grib_point<'py>(
    py: Python<'py>,
    path: String,
    eccodes_library_path: String,
    latitude: f64,
    longitude: f64,
    missing: Option<f64>,
) -> PyResult<PyDecodedPoint<'py>> {
    let decoded = py
        .detach(move || {
            grib::decode_grib_point(
                std::path::Path::new(&path),
                std::path::Path::new(&eccodes_library_path),
                latitude,
                longitude,
                missing,
            )
        })
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
    let grib::DecodedPoint {
        matrix,
        level_count,
        selected_latitude,
        selected_longitude,
        surface_relative_vorticity,
    } = decoded;
    let matrix = Array2::from_shape_vec((9, level_count), matrix)
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
    Ok((
        matrix.into_pyarray(py),
        selected_latitude,
        selected_longitude,
        surface_relative_vorticity,
    ))
}

#[pymodule]
fn sharpmod_rs(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    module.add("__backend_api_version__", BACKEND_API_VERSION)?;
    module.add_function(wrap_pyfunction!(py_wind_to_components, module)?)?;
    module.add_function(wrap_pyfunction!(py_components_to_wind, module)?)?;
    module.add_function(wrap_pyfunction!(py_interpolate_1d, module)?)?;
    module.add_function(wrap_pyfunction!(py_basic_sounding_qc, module)?)?;
    module.add_function(wrap_pyfunction!(py_pressure_sort_dedup_indices, module)?)?;
    module.add_function(wrap_pyfunction!(py_parse_sounding_rows, module)?)?;
    module.add_function(wrap_pyfunction!(py_decode_grib_point, module)?)?;
    Ok(())
}

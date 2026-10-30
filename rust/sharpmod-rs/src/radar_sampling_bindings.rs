use numpy::{
    ndarray::Array2, IntoPyArray, PyArray2, PyReadonlyArray1, PyReadonlyArray2,
    PyUntypedArrayMethods,
};
use pyo3::{exceptions::PyValueError, prelude::*};

#[pyfunction]
fn radar_level3_scan_time(
    volume_time: time::OffsetDateTime,
    delta_time: i64,
    supplemental_scan: &str,
) -> PyResult<time::OffsetDateTime> {
    let supplemental = match supplemental_scan {
        "Non-supplemental scan" => false,
        "SAILS scan" | "MRLE scan" => true,
        _ => {
            return Err(PyValueError::new_err(
                "unknown Level III supplemental scan type",
            ))
        }
    };
    sharpmod_weather::radar::scan_time::level3(volume_time, delta_time, supplemental)
        .map_err(PyValueError::new_err)
}

#[pyfunction(signature=(codes, starts, ends, bearings, ranges, gate_width, gate_start=0.))]
#[allow(clippy::too_many_arguments)] // Native radial geometry and one cached projection.
fn sample_radar_radials<'py>(
    py: Python<'py>,
    codes: PyReadonlyArray2<'py, u8>,
    starts: PyReadonlyArray1<'py, f64>,
    ends: PyReadonlyArray1<'py, f64>,
    bearings: PyReadonlyArray2<'py, f32>,
    ranges: PyReadonlyArray2<'py, f32>,
    gate_width: f32,
    gate_start: f32,
) -> PyResult<Bound<'py, PyArray2<u8>>> {
    if bearings.shape() != ranges.shape() || bearings.shape().iter().any(|&size| size > 4096) {
        return Err(PyValueError::new_err(
            "unsupported NEXRAD projection dimensions",
        ));
    }
    let (height, width) = (bearings.shape()[0], bearings.shape()[1]);
    let (count, gates) = (codes.shape()[0], codes.shape()[1]);
    let (codes, starts, ends) = (
        codes.as_slice()?.to_vec(),
        starts.as_slice()?.to_vec(),
        ends.as_slice()?.to_vec(),
    );
    let (bearings, ranges) = (bearings.as_slice()?, ranges.as_slice()?);
    let radials = crate::radar_sampling::Radials::new(
        &codes, count, gates, &starts, &ends, gate_width, gate_start,
    )
    .map_err(PyValueError::new_err)?;
    let mut sampled = Vec::with_capacity(height * width);
    // Keep the previous 64-row memory bound while detached work owns its input.
    for first in (0..height).step_by(64) {
        let cells = first * width..(first + 64).min(height) * width;
        let bearings = bearings[cells.clone()].to_vec();
        let ranges = ranges[cells].to_vec();
        let radials = &radials;
        sampled.extend(
            py.detach(move || radials.sample(&bearings, &ranges))
                .map_err(PyValueError::new_err)?,
        );
    }
    let sampled = Array2::from_shape_vec((height, width), sampled)
        .map_err(|error| PyValueError::new_err(error.to_string()))?;
    Ok(sampled.into_pyarray(py))
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(radar_level3_scan_time, module)?)?;
    module.add_function(wrap_pyfunction!(sample_radar_radials, module)?)?;
    Ok(())
}

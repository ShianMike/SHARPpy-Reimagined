//! PyO3 bindings for SHARPpy Reimagined's primary Rust numerical and direct
//! GRIB point-decoding backend.

pub mod batch_analysis;
pub mod grib;
pub mod interpolation;
pub mod kinematics;
pub mod parcels;
pub mod parsing;
pub mod quality_control;
pub mod records;
pub mod wind;

use numpy::ndarray::Array2;
use numpy::{IntoPyArray, PyArray1, PyArray2, PyReadonlyArray1};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;

type PyArrayPair<'py> = (Bound<'py, PyArray1<f64>>, Bound<'py, PyArray1<f64>>);
type PyDecodedPoint<'py> = (
    Bound<'py, PyArray2<f64>>,
    f64,
    f64,
    Option<f64>,
    bool,
    usize,
);
type PyDecodedPoints<'py> = Vec<PyDecodedPoint<'py>>;
type PyKinematics<'py> = (Bound<'py, PyArray1<f64>>, Bound<'py, PyArray2<f64>>);
type PyParcelAscent<'py> = (
    Bound<'py, PyArray1<f64>>,
    Bound<'py, PyArray1<f64>>,
    Bound<'py, PyArray1<f64>>,
);
type PyConvectiveParcels<'py> = (
    Bound<'py, PyArray2<f64>>,
    Bound<'py, PyArray1<f64>>,
    Bound<'py, PyArray1<f64>>,
    Bound<'py, PyArray1<f64>>,
    Bound<'py, PyArray1<usize>>,
);
type PyProfileThermodynamics<'py> = (
    Bound<'py, PyArray2<f64>>,
    PyConvectiveParcels<'py>,
    PyParcelAscent<'py>,
);
type PyBatchAnalysis<'py> = (
    Bound<'py, PyArray2<f64>>,
    Bound<'py, PyArray2<f64>>,
    Bound<'py, PyArray2<f64>>,
    Bound<'py, PyArray2<f64>>,
    Bound<'py, PyArray2<f64>>,
    Bound<'py, PyArray2<f64>>,
    u8,
    usize,
);
const BACKEND_API_VERSION: u32 = 7;

fn value_error(message: String) -> PyErr {
    PyValueError::new_err(message)
}

fn parcel_matrix_into_py<'py>(
    py: Python<'py>,
    rows: [[f64; parcels::PARCEL_WIDTH]; parcels::PARCEL_COUNT],
) -> PyResult<Bound<'py, PyArray2<f64>>> {
    let flat: Vec<f64> = rows.into_iter().flatten().collect();
    let matrix = Array2::from_shape_vec((parcels::PARCEL_COUNT, parcels::PARCEL_WIDTH), flat)
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
    Ok(matrix.into_pyarray(py))
}

fn convective_into_py<'py>(
    py: Python<'py>,
    result: parcels::ConvectiveParcelWorkspace,
) -> PyResult<PyConvectiveParcels<'py>> {
    let mut diagnostics =
        Vec::with_capacity(parcels::CONVECTIVE_PARCEL_COUNT * parcels::PARCEL_WIDTH);
    let trace_len = result
        .parcels
        .iter()
        .map(|ascent| ascent.pressure_trace.len())
        .sum();
    let mut pressure_buffer = Vec::with_capacity(trace_len);
    let mut temperature_buffer = Vec::with_capacity(trace_len);
    let mut offsets = Vec::with_capacity(parcels::CONVECTIVE_PARCEL_COUNT + 1);
    offsets.push(0);
    for ascent in result.parcels {
        if ascent.pressure_trace.len() != ascent.temperature_trace.len() {
            return Err(PyRuntimeError::new_err(
                "native parcel trace pressure/temperature lengths differ",
            ));
        }
        diagnostics.extend(ascent.diagnostics);
        pressure_buffer.extend(ascent.pressure_trace);
        temperature_buffer.extend(ascent.temperature_trace);
        offsets.push(pressure_buffer.len());
    }
    let matrix = Array2::from_shape_vec(
        (parcels::CONVECTIVE_PARCEL_COUNT, parcels::PARCEL_WIDTH),
        diagnostics,
    )
    .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
    let bounds = vec![
        result.effective_bottom_pressure,
        result.effective_top_pressure,
    ];
    Ok((
        matrix.into_pyarray(py),
        bounds.into_pyarray(py),
        pressure_buffer.into_pyarray(py),
        temperature_buffer.into_pyarray(py),
        offsets.into_pyarray(py),
    ))
}

fn parcel_ascent_into_py<'py>(
    py: Python<'py>,
    result: parcels::ParcelAscent,
) -> PyParcelAscent<'py> {
    (
        result.diagnostics.to_vec().into_pyarray(py),
        result.pressure_trace.into_pyarray(py),
        result.temperature_trace.into_pyarray(py),
    )
}

fn downdraft_into_py<'py>(
    py: Python<'py>,
    result: parcels::DowndraftDiagnostics,
) -> PyParcelAscent<'py> {
    (
        vec![
            result.cape,
            result.source_pressure,
            result.downrush_temperature,
        ]
        .into_pyarray(py),
        result.pressure_trace.into_pyarray(py),
        result.temperature_trace.into_pyarray(py),
    )
}

fn decoded_point_into_py<'py>(
    py: Python<'py>,
    decoded: grib::DecodedPoint,
) -> PyResult<PyDecodedPoint<'py>> {
    let grib::DecodedPoint {
        matrix,
        level_count,
        selected_latitude,
        selected_longitude,
        surface_relative_vorticity,
        surface_merged,
        below_ground_levels_removed,
    } = decoded;
    let matrix = Array2::from_shape_vec((9, level_count), matrix)
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
    Ok((
        matrix.into_pyarray(py),
        selected_latitude,
        selected_longitude,
        surface_relative_vorticity,
        surface_merged,
        below_ground_levels_removed,
    ))
}

#[allow(clippy::too_many_arguments)]
fn batch_inputs_from_flattened(
    pressure: &[f64],
    height: &[f64],
    temperature: &[f64],
    dewpoint: &[f64],
    wind_direction: &[f64],
    wind_speed: &[f64],
    offsets: &[usize],
    surface_indices: &[usize],
    missing: Option<f64>,
) -> Result<Vec<batch_analysis::BatchProfileInput>, String> {
    let value_count = pressure.len();
    for (name, actual) in [
        ("height", height.len()),
        ("temperature", temperature.len()),
        ("dewpoint", dewpoint.len()),
        ("wind_direction", wind_direction.len()),
        ("wind_speed", wind_speed.len()),
    ] {
        if actual != value_count {
            return Err(format!(
                "flattened batch column lengths differ: pressure={value_count}, {name}={actual}"
            ));
        }
    }
    if offsets.is_empty() || offsets[0] != 0 {
        return Err("batch offsets must start with zero".to_string());
    }
    if offsets.last().copied() != Some(value_count) {
        return Err(format!(
            "final batch offset must equal flattened length {value_count}"
        ));
    }
    if offsets.windows(2).any(|pair| pair[0] > pair[1]) {
        return Err("batch offsets must be monotonically nondecreasing".to_string());
    }
    let profile_count = offsets.len() - 1;
    if surface_indices.len() != profile_count {
        return Err(format!(
            "surface index count {} does not match profile count {profile_count}",
            surface_indices.len()
        ));
    }

    let mut inputs = Vec::with_capacity(profile_count);
    for (profile_index, window) in offsets.windows(2).enumerate() {
        let start = window[0];
        let end = window[1];
        let level_count = end - start;
        let surface_index = surface_indices[profile_index];
        if (level_count == 0 && surface_index != 0)
            || (level_count > 0 && surface_index >= level_count)
        {
            return Err(format!(
                "profile {profile_index}: surface_index {surface_index} is outside a profile with {level_count} levels"
            ));
        }
        inputs.push(batch_analysis::BatchProfileInput {
            pressure: pressure[start..end].to_vec(),
            height: height[start..end].to_vec(),
            temperature: temperature[start..end].to_vec(),
            dewpoint: dewpoint[start..end].to_vec(),
            wind_direction: wind_direction[start..end].to_vec(),
            wind_speed: wind_speed[start..end].to_vec(),
            surface_index,
            missing,
        });
    }
    Ok(inputs)
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
    name = "profile_kinematics",
    signature = (pres, hght, u, v, layer_tops_agl, sfc=0, missing=-9999.0)
)]
#[allow(clippy::too_many_arguments)]
fn py_profile_kinematics<'py>(
    py: Python<'py>,
    pres: PyReadonlyArray1<'py, f64>,
    hght: PyReadonlyArray1<'py, f64>,
    u: PyReadonlyArray1<'py, f64>,
    v: PyReadonlyArray1<'py, f64>,
    layer_tops_agl: PyReadonlyArray1<'py, f64>,
    sfc: usize,
    missing: Option<f64>,
) -> PyResult<PyKinematics<'py>> {
    // Own the compact profile before releasing the GIL. A typical sounding is
    // only tens of levels, while the grouped interpolation/integration work is
    // large enough to amortize these copies and can safely overlap elsewhere.
    let pres = pres.as_slice()?.to_vec();
    let hght = hght.as_slice()?.to_vec();
    let u = u.as_slice()?.to_vec();
    let v = v.as_slice()?.to_vec();
    let layer_tops_agl = layer_tops_agl.as_slice()?.to_vec();
    let result = py
        .detach(move || {
            kinematics::profile_kinematics(&pres, &hght, &u, &v, &layer_tops_agl, sfc, missing)
        })
        .map_err(value_error)?;
    let layer_count = result.layers.len();
    let flat = result.layers.into_iter().flatten().collect();
    let matrix = Array2::from_shape_vec((layer_count, kinematics::LAYER_WIDTH), flat)
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
    Ok((
        result.storm_motion.to_vec().into_pyarray(py),
        matrix.into_pyarray(py),
    ))
}

#[pyfunction(
    name = "profile_parcels",
    signature = (pres, hght, tmpc, dwpc, sfc=0, missing=-9999.0)
)]
#[allow(clippy::too_many_arguments)]
fn py_profile_parcels<'py>(
    py: Python<'py>,
    pres: PyReadonlyArray1<'py, f64>,
    hght: PyReadonlyArray1<'py, f64>,
    tmpc: PyReadonlyArray1<'py, f64>,
    dwpc: PyReadonlyArray1<'py, f64>,
    sfc: usize,
    missing: Option<f64>,
) -> PyResult<Bound<'py, PyArray2<f64>>> {
    let pres = pres.as_slice()?.to_vec();
    let hght = hght.as_slice()?.to_vec();
    let tmpc = tmpc.as_slice()?.to_vec();
    let dwpc = dwpc.as_slice()?.to_vec();
    let result = py
        .detach(move || parcels::profile_parcels(&pres, &hght, &tmpc, &dwpc, sfc, missing))
        .map_err(value_error)?;
    parcel_matrix_into_py(py, result)
}

#[pyfunction(
    name = "profile_convective_parcels",
    signature = (pres, hght, tmpc, dwpc, sfc=0, missing=-9999.0)
)]
#[allow(clippy::too_many_arguments)]
fn py_profile_convective_parcels<'py>(
    py: Python<'py>,
    pres: PyReadonlyArray1<'py, f64>,
    hght: PyReadonlyArray1<'py, f64>,
    tmpc: PyReadonlyArray1<'py, f64>,
    dwpc: PyReadonlyArray1<'py, f64>,
    sfc: usize,
    missing: Option<f64>,
) -> PyResult<PyConvectiveParcels<'py>> {
    let pres = pres.as_slice()?.to_vec();
    let hght = hght.as_slice()?.to_vec();
    let tmpc = tmpc.as_slice()?.to_vec();
    let dwpc = dwpc.as_slice()?.to_vec();
    let result = py
        .detach(move || {
            parcels::profile_convective_parcels(&pres, &hght, &tmpc, &dwpc, sfc, missing)
        })
        .map_err(value_error)?;
    convective_into_py(py, result)
}

#[pyfunction(
    name = "lift_parcel",
    signature = (
        pres,
        hght,
        tmpc,
        dwpc,
        parcel_pressure,
        parcel_temperature,
        parcel_dewpoint,
        sfc=0,
        missing=-9999.0
    )
)]
#[allow(clippy::too_many_arguments)]
fn py_lift_parcel<'py>(
    py: Python<'py>,
    pres: PyReadonlyArray1<'py, f64>,
    hght: PyReadonlyArray1<'py, f64>,
    tmpc: PyReadonlyArray1<'py, f64>,
    dwpc: PyReadonlyArray1<'py, f64>,
    parcel_pressure: f64,
    parcel_temperature: f64,
    parcel_dewpoint: f64,
    sfc: usize,
    missing: Option<f64>,
) -> PyResult<PyParcelAscent<'py>> {
    let pres = pres.as_slice()?.to_vec();
    let hght = hght.as_slice()?.to_vec();
    let tmpc = tmpc.as_slice()?.to_vec();
    let dwpc = dwpc.as_slice()?.to_vec();
    let result = py
        .detach(move || {
            parcels::explicit_parcel(
                &pres,
                &hght,
                &tmpc,
                &dwpc,
                parcel_pressure,
                parcel_temperature,
                parcel_dewpoint,
                sfc,
                missing,
            )
        })
        .map_err(value_error)?;
    Ok(parcel_ascent_into_py(py, result))
}

#[pyfunction(
    name = "profile_dcape",
    signature = (pres, hght, tmpc, dwpc, sfc=0, missing=-9999.0)
)]
#[allow(clippy::too_many_arguments)]
fn py_profile_dcape<'py>(
    py: Python<'py>,
    pres: PyReadonlyArray1<'py, f64>,
    hght: PyReadonlyArray1<'py, f64>,
    tmpc: PyReadonlyArray1<'py, f64>,
    dwpc: PyReadonlyArray1<'py, f64>,
    sfc: usize,
    missing: Option<f64>,
) -> PyResult<PyParcelAscent<'py>> {
    let pres = pres.as_slice()?.to_vec();
    let hght = hght.as_slice()?.to_vec();
    let tmpc = tmpc.as_slice()?.to_vec();
    let dwpc = dwpc.as_slice()?.to_vec();
    let result = py
        .detach(move || parcels::profile_dcape(&pres, &hght, &tmpc, &dwpc, sfc, missing))
        .map_err(value_error)?;
    Ok(downdraft_into_py(py, result))
}

#[pyfunction(
    name = "profile_thermodynamics",
    signature = (pres, hght, tmpc, dwpc, sfc=0, missing=-9999.0)
)]
#[allow(clippy::too_many_arguments)]
fn py_profile_thermodynamics<'py>(
    py: Python<'py>,
    pres: PyReadonlyArray1<'py, f64>,
    hght: PyReadonlyArray1<'py, f64>,
    tmpc: PyReadonlyArray1<'py, f64>,
    dwpc: PyReadonlyArray1<'py, f64>,
    sfc: usize,
    missing: Option<f64>,
) -> PyResult<PyProfileThermodynamics<'py>> {
    // Own every borrowed NumPy input before releasing the GIL.  The detached
    // closure never touches Python-owned memory.
    let pres = pres.as_slice()?.to_vec();
    let hght = hght.as_slice()?.to_vec();
    let tmpc = tmpc.as_slice()?.to_vec();
    let dwpc = dwpc.as_slice()?.to_vec();
    let result = py
        .detach(move || parcels::profile_thermodynamics(&pres, &hght, &tmpc, &dwpc, sfc, missing))
        .map_err(value_error)?;
    let parcels::ProfileThermodynamics {
        parcels,
        convective,
        downdraft,
    } = result;
    Ok((
        parcel_matrix_into_py(py, parcels)?,
        convective_into_py(py, convective)?,
        downdraft_into_py(py, downdraft),
    ))
}

#[pyfunction(
    name = "profile_batch_analysis",
    signature = (
        pressure,
        height,
        temperature,
        dewpoint,
        wind_direction,
        wind_speed,
        offsets,
        surface_indices,
        layer_tops_agl,
        missing=-9999.0,
        max_threads=None,
        parallel_threshold=8
    )
)]
#[allow(clippy::too_many_arguments)]
fn py_profile_batch_analysis<'py>(
    py: Python<'py>,
    pressure: PyReadonlyArray1<'py, f64>,
    height: PyReadonlyArray1<'py, f64>,
    temperature: PyReadonlyArray1<'py, f64>,
    dewpoint: PyReadonlyArray1<'py, f64>,
    wind_direction: PyReadonlyArray1<'py, f64>,
    wind_speed: PyReadonlyArray1<'py, f64>,
    offsets: PyReadonlyArray1<'py, usize>,
    surface_indices: PyReadonlyArray1<'py, usize>,
    layer_tops_agl: PyReadonlyArray1<'py, f64>,
    missing: Option<f64>,
    max_threads: Option<usize>,
    parallel_threshold: usize,
) -> PyResult<PyBatchAnalysis<'py>> {
    // Each segment is copied exactly once into an owned profile snapshot
    // before releasing the GIL. The Rayon workers never borrow Python memory.
    let inputs = batch_inputs_from_flattened(
        pressure.as_slice()?,
        height.as_slice()?,
        temperature.as_slice()?,
        dewpoint.as_slice()?,
        wind_direction.as_slice()?,
        wind_speed.as_slice()?,
        offsets.as_slice()?,
        surface_indices.as_slice()?,
        missing,
    )
    .map_err(value_error)?;
    let layer_tops_agl = layer_tops_agl.as_slice()?.to_vec();
    let profile_count = inputs.len();
    let layer_count = layer_tops_agl.len();
    let execution = py
        .detach(move || {
            let mut options = batch_analysis::BatchExecutionOptions::default();
            if let Some(max_threads) = max_threads {
                options.max_threads = max_threads;
            }
            options.parallel_threshold = parallel_threshold;
            if options == batch_analysis::BatchExecutionOptions::default() {
                batch_analysis::default_executor()?.analyze(&inputs, &layer_tops_agl)
            } else {
                let executor = batch_analysis::BatchAnalysisExecutor::new(options)?;
                executor.analyze(&inputs, &layer_tops_agl)
            }
        })
        .map_err(value_error)?;

    let mut parcel_values =
        Vec::with_capacity(profile_count * parcels::PARCEL_COUNT * parcels::PARCEL_WIDTH);
    let mut convective_values = Vec::with_capacity(
        profile_count * parcels::CONVECTIVE_PARCEL_COUNT * parcels::PARCEL_WIDTH,
    );
    let mut effective_bounds = Vec::with_capacity(profile_count * 2);
    let mut downdraft_values = Vec::with_capacity(profile_count * 3);
    let mut storm_motion = Vec::with_capacity(profile_count * 4);
    let mut layer_values =
        Vec::with_capacity(profile_count * layer_count * kinematics::LAYER_WIDTH);
    for diagnostics in execution.diagnostics {
        if diagnostics.kinematic_layers.len() != layer_count {
            return Err(PyRuntimeError::new_err(format!(
                "native batch returned {} layers; expected {layer_count}",
                diagnostics.kinematic_layers.len()
            )));
        }
        parcel_values.extend(diagnostics.parcels.into_iter().flatten());
        convective_values.extend(diagnostics.convective_parcels.into_iter().flatten());
        effective_bounds.extend([
            diagnostics.effective_bottom_pressure,
            diagnostics.effective_top_pressure,
        ]);
        downdraft_values.extend(diagnostics.downdraft);
        storm_motion.extend(diagnostics.storm_motion);
        layer_values.extend(diagnostics.kinematic_layers.into_iter().flatten());
    }
    let mode = match execution.mode {
        batch_analysis::BatchExecutionMode::SerialBelowThreshold => 0,
        batch_analysis::BatchExecutionMode::SerialSingleThread => 1,
        batch_analysis::BatchExecutionMode::SerialNestedRayon => 2,
        batch_analysis::BatchExecutionMode::BoundedParallel => 3,
    };
    let parcels = Array2::from_shape_vec(
        (profile_count, parcels::PARCEL_COUNT * parcels::PARCEL_WIDTH),
        parcel_values,
    )
    .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
    let convective = Array2::from_shape_vec(
        (
            profile_count,
            parcels::CONVECTIVE_PARCEL_COUNT * parcels::PARCEL_WIDTH,
        ),
        convective_values,
    )
    .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
    let bounds = Array2::from_shape_vec((profile_count, 2), effective_bounds)
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
    let downdraft = Array2::from_shape_vec((profile_count, 3), downdraft_values)
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
    let storm = Array2::from_shape_vec((profile_count, 4), storm_motion)
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
    let layers = Array2::from_shape_vec(
        (profile_count, layer_count * kinematics::LAYER_WIDTH),
        layer_values,
    )
    .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
    Ok((
        parcels.into_pyarray(py),
        convective.into_pyarray(py),
        bounds.into_pyarray(py),
        downdraft.into_pyarray(py),
        storm.into_pyarray(py),
        layers.into_pyarray(py),
        mode,
        execution.worker_count,
    ))
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
    decoded_point_into_py(py, decoded)
}

#[pyfunction(
    name = "decode_grib_points",
    signature = (path, eccodes_library_path, latitudes, longitudes, missing=-9999.0)
)]
fn py_decode_grib_points<'py>(
    py: Python<'py>,
    path: String,
    eccodes_library_path: String,
    latitudes: PyReadonlyArray1<'py, f64>,
    longitudes: PyReadonlyArray1<'py, f64>,
    missing: Option<f64>,
) -> PyResult<PyDecodedPoints<'py>> {
    let latitudes = latitudes.as_slice()?.to_vec();
    let longitudes = longitudes.as_slice()?.to_vec();
    if latitudes.len() != longitudes.len() {
        return Err(PyValueError::new_err(format!(
            "latitude/longitude lengths differ: {} != {}",
            latitudes.len(),
            longitudes.len()
        )));
    }
    let points: Vec<(f64, f64)> = latitudes.into_iter().zip(longitudes).collect();
    let decoded = py
        .detach(move || {
            grib::decode_grib_points(
                std::path::Path::new(&path),
                std::path::Path::new(&eccodes_library_path),
                &points,
                missing,
            )
        })
        .map_err(|error| PyRuntimeError::new_err(error.to_string()))?;
    decoded
        .into_iter()
        .map(|point| decoded_point_into_py(py, point))
        .collect()
}

#[pyfunction(name = "set_grib_inventory_cache_enabled")]
fn py_set_grib_inventory_cache_enabled(enabled: bool) {
    grib::set_grib_inventory_cache_enabled(enabled);
}

#[pyfunction(name = "clear_grib_inventory_cache", signature = (reset_stats=true))]
fn py_clear_grib_inventory_cache(reset_stats: bool) {
    grib::clear_grib_inventory_cache(reset_stats);
}

#[pyfunction(name = "grib_inventory_cache_info")]
fn py_grib_inventory_cache_info() -> (bool, usize, usize, u64, u64, u64, u64) {
    let info = grib::grib_inventory_cache_info();
    (
        info.enabled,
        info.size,
        info.max_size,
        info.hits,
        info.misses,
        info.evictions,
        info.invalidations,
    )
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
    module.add_function(wrap_pyfunction!(py_profile_kinematics, module)?)?;
    module.add_function(wrap_pyfunction!(py_profile_parcels, module)?)?;
    module.add_function(wrap_pyfunction!(py_profile_convective_parcels, module)?)?;
    module.add_function(wrap_pyfunction!(py_lift_parcel, module)?)?;
    module.add_function(wrap_pyfunction!(py_profile_dcape, module)?)?;
    module.add_function(wrap_pyfunction!(py_profile_thermodynamics, module)?)?;
    module.add_function(wrap_pyfunction!(py_profile_batch_analysis, module)?)?;
    module.add_function(wrap_pyfunction!(py_parse_sounding_rows, module)?)?;
    module.add_function(wrap_pyfunction!(py_decode_grib_point, module)?)?;
    module.add_function(wrap_pyfunction!(py_decode_grib_points, module)?)?;
    module.add_function(wrap_pyfunction!(
        py_set_grib_inventory_cache_enabled,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(py_clear_grib_inventory_cache, module)?)?;
    module.add_function(wrap_pyfunction!(py_grib_inventory_cache_info, module)?)?;
    Ok(())
}

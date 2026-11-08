//! Portable, bounded native-column traces, separate from the analysed mean arrays.
use super::*;
use crate::{box_soundings::MAX_DISPLAY_COLUMNS, grib::DecodedPoint};
use numpy::{IntoPyArray, PyReadonlyArray1};
use pyo3::{exceptions::PyValueError, types::PyList};

/// Preserve the existing mean and metadata arrays, closing before atomic replacement.
pub(super) fn persist(py: Python<'_>, path: &str, columns: &[DecodedPoint]) -> PyResult<()> {
    let numpy = PyModule::import(py, "numpy")?;
    let options = PyDict::new(py);
    options.set_item("allow_pickle", false)?;
    let archive = numpy.getattr("load")?.call((path,), Some(&options))?;
    let arrays = (|| -> PyResult<Bound<'_, PyDict>> {
        let arrays = PyDict::new(py);
        for name in archive.getattr("files")?.extract::<Vec<String>>()? {
            arrays.set_item(&name, archive.get_item(&name)?)?;
        }
        let mut values = Vec::new();
        let mut offsets = vec![0_u64];
        let mut coordinates = Vec::new();
        for column in columns {
            values.extend_from_slice(&column.matrix);
            offsets.push(values.len() as u64);
            coordinates.extend([column.selected_latitude, column.selected_longitude]);
        }
        arrays.set_item("box_trace_version", vec![1_u64].into_pyarray(py))?;
        arrays.set_item("box_trace_data", values.into_pyarray(py))?;
        arrays.set_item("box_trace_offsets", offsets.into_pyarray(py))?;
        arrays.set_item("box_trace_coordinates", coordinates.into_pyarray(py))?;
        Ok(arrays)
    })();
    let closed = archive.call_method0("close");
    let arrays = arrays?;
    closed?;
    let temporary = Path::new(path).with_extension("box-tmp.npz");
    numpy
        .getattr("savez_compressed")?
        .call((temporary.to_string_lossy().as_ref(),), Some(&arrays))?;
    std::fs::rename(&temporary, path).map_err(|error| {
        failure(
            py,
            Error::Data(format!("Could not preserve native grid traces: {error}")),
        )
    })
}

pub(super) struct Trace {
    pub matrix: Vec<f64>,
    pub levels: usize,
    pub latitude: f64,
    pub longitude: f64,
}

pub(super) fn load(py: Python<'_>, path: &str) -> PyResult<Vec<Trace>> {
    let numpy = PyModule::import(py, "numpy")?;
    let options = PyDict::new(py);
    options.set_item("allow_pickle", false)?;
    let archive = numpy.getattr("load")?.call((path,), Some(&options))?;
    let traces = read(&archive);
    let closed = archive.call_method0("close");
    let traces = traces?;
    closed?;
    Ok(traces)
}

fn read(archive: &Bound<'_, PyAny>) -> PyResult<Vec<Trace>> {
    let names = archive.getattr("files")?.cast_into::<PyList>()?;
    if !names.contains("box_trace_version")? {
        return Ok(Vec::new());
    }
    let invalid = || PyValueError::new_err("Invalid native box trace arrays in portable sounding");
    let version = archive.get_item("box_trace_version")?;
    let version = version.extract::<PyReadonlyArray1<'_, u64>>()?;
    if version.as_slice()? != [1] {
        return Err(invalid());
    }
    let values = archive.get_item("box_trace_data")?;
    let values = values.extract::<PyReadonlyArray1<'_, f64>>()?;
    let offsets = archive.get_item("box_trace_offsets")?;
    let offsets = offsets.extract::<PyReadonlyArray1<'_, u64>>()?;
    let coordinates = archive.get_item("box_trace_coordinates")?;
    let coordinates = coordinates.extract::<PyReadonlyArray1<'_, f64>>()?;
    let (values, offsets, coordinates) = (
        values.as_slice()?,
        offsets.as_slice()?,
        coordinates.as_slice()?,
    );
    if offsets.len() < 2
        || offsets.len() > MAX_DISPLAY_COLUMNS + 1
        || offsets.first() != Some(&0)
        || offsets.last().copied() != Some(values.len() as u64)
        || coordinates.len() != (offsets.len() - 1) * 2
        || !values
            .iter()
            .chain(coordinates)
            .all(|number| number.is_finite())
    {
        return Err(invalid());
    }
    let mut traces = Vec::with_capacity(offsets.len() - 1);
    for (index, range) in offsets.windows(2).enumerate() {
        if range[1] <= range[0] || range[1] > values.len() as u64 {
            return Err(invalid());
        }
        let matrix = &values[range[0] as usize..range[1] as usize];
        let levels = matrix.len() / 9;
        let (latitude, longitude) = (coordinates[2 * index], coordinates[2 * index + 1]);
        if matrix.len() % 9 != 0
            || !(2..=4096).contains(&levels)
            || !(-90.0..=90.0).contains(&latitude)
            || !(-180.0..=180.0).contains(&longitude)
            || matrix[..levels]
                .iter()
                .any(|pressure| *pressure <= 0.0 || *pressure > 1200.0)
            || matrix[..levels]
                .windows(2)
                .any(|pressure| pressure[0] <= pressure[1])
        {
            return Err(invalid());
        }
        traces.push(Trace {
            matrix: matrix.to_vec(),
            levels,
            latitude,
            longitude,
        });
    }
    Ok(traces)
}

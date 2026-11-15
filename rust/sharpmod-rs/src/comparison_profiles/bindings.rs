//! Compatibility with the existing NumPy/profile and dataclass boundaries.
use super::*;
use numpy::{IntoPyArray, PyReadonlyArray1};
use pyo3::{
    exceptions::{PyTypeError, PyValueError},
    prelude::*,
    types::{PyDict, PyFloat, PyTuple},
};

fn profile_values(
    module: &Bound<'_, PyModule>,
    profile: &Bound<'_, PyAny>,
    field: &str,
) -> PyResult<Vec<f64>> {
    // Reuse the existing profile boundary for masks, invalid attributes and
    // the -9998 sentinel; all alignment and interpolation is done below.
    let column = module.call_method1("_values", (profile, field))?;
    let array = column.extract::<PyReadonlyArray1<'_, f64>>()?;
    Ok(array.as_array().iter().copied().collect())
}

fn raw_values(py: Python<'_>, input: &Bound<'_, PyAny>) -> PyResult<Option<Vec<f64>>> {
    let convert = || -> PyResult<Vec<f64>> {
        let numpy = PyModule::import(py, "numpy")?;
        let kwargs = PyDict::new(py);
        kwargs.set_item("dtype", py.get_type::<PyFloat>())?;
        let masked = numpy
            .getattr("ma")?
            .call_method("asarray", (input,), Some(&kwargs))?;
        let filled = masked.call_method1("filled", (f64::NAN,))?;
        let flat = numpy
            .call_method("asarray", (filled,), Some(&kwargs))?
            .call_method1("reshape", (-1,))?;
        let array = flat.extract::<PyReadonlyArray1<'_, f64>>()?;
        Ok(array.as_array().iter().copied().collect())
    };
    match convert() {
        Ok(values) => Ok(Some(values)),
        Err(error)
            if error.is_instance_of::<PyTypeError>(py)
                || error.is_instance_of::<PyValueError>(py) =>
        {
            Ok(None)
        }
        Err(error) => Err(error),
    }
}

fn python_float(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<f64> {
    PyModule::import(py, "builtins")?
        .getattr("float")?
        .call1((value,))?
        .extract()
}

fn prepare(
    py: Python<'_>,
    heights: &[f64],
    values: &[f64],
    max_gap: &Bound<'_, PyAny>,
) -> PyResult<Option<Interpolator>> {
    let pairs = finite_pairs(heights, values);
    if pairs.is_empty() {
        return Ok(None);
    }
    // The old helper used np.argsort's default, intentionally unspecified tie
    // order before np.unique. Reuse that ordering so repeated heights retain
    // precisely the same source value with the user's installed NumPy.
    let coordinates: Vec<_> = pairs.iter().map(|&(height, _)| height).collect();
    let indices =
        PyModule::import(py, "numpy")?.call_method1("argsort", (coordinates.into_pyarray(py),))?;
    let indices = indices.extract::<PyReadonlyArray1<'_, isize>>()?;
    let order: Vec<_> = indices.as_array().iter().copied().collect();
    let max_gap = python_float(py, max_gap)?;
    Ok(Some(Interpolator::from_ordered_indices(
        &pairs, &order, max_gap,
    )))
}

fn result<'py>(
    module: &Bound<'py, PyModule>,
    reference_label: &Bound<'py, PyAny>,
    candidate_label: &Bound<'py, PyAny>,
    levels: Vec<Level>,
    bottom: Option<f64>,
    top: Option<f64>,
    reason: Option<&str>,
) -> PyResult<Bound<'py, PyAny>> {
    let py = module.py();
    let level_type = module.getattr("VerticalDifferenceLevel")?;
    let rows = levels
        .into_iter()
        .map(|row| {
            level_type.call1((
                row.height,
                row.pressure,
                row.differences[0],
                row.differences[1],
                row.differences[2],
                row.differences[3],
            ))
        })
        .collect::<PyResult<Vec<_>>>()?;
    let rows = PyTuple::new(py, rows)?;
    let string = PyModule::import(py, "builtins")?.getattr("str")?;
    let reference_label = string.call1((reference_label,))?;
    let candidate_label = string.call1((candidate_label,))?;
    module.getattr("VerticalDifference")?.call1((
        reference_label,
        candidate_label,
        rows,
        bottom,
        top,
        reason,
    ))
}

#[pyfunction]
fn comparison_interpolate_at(
    py: Python<'_>,
    heights: &Bound<'_, PyAny>,
    values: &Bound<'_, PyAny>,
    target: &Bound<'_, PyAny>,
    max_gap_m: &Bound<'_, PyAny>,
) -> PyResult<Option<f64>> {
    let Some(heights) = raw_values(py, heights)? else {
        return Ok(None);
    };
    let Some(values) = raw_values(py, values)? else {
        return Ok(None);
    };
    let Some(interpolator) = prepare(py, &heights, &values, max_gap_m)? else {
        return Ok(None);
    };
    Ok(interpolator.sample(python_float(py, target)?))
}

#[pyfunction]
fn comparison_vertical_profile_difference<'py>(
    py: Python<'py>,
    reference_profile: &Bound<'py, PyAny>,
    candidate_profile: &Bound<'py, PyAny>,
    reference_label: &Bound<'py, PyAny>,
    candidate_label: &Bound<'py, PyAny>,
    max_gap_m: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyAny>> {
    let module = PyModule::import(py, "sharpmod.analysis.comparison")?;
    if reference_profile.is_none() || candidate_profile.is_none() {
        return result(
            &module,
            reference_label,
            candidate_label,
            vec![],
            None,
            None,
            Some("one or both profiles are unavailable"),
        );
    }
    let reference_height = profile_values(&module, reference_profile, "hght")?;
    let candidate_height = profile_values(&module, candidate_profile, "hght")?;
    let (bottom, top) = match coverage(&reference_height, &candidate_height) {
        Coverage::MissingHeights => {
            return result(
                &module,
                reference_label,
                candidate_label,
                vec![],
                None,
                None,
                Some("one or both profiles have no valid heights"),
            )
        }
        Coverage::Disjoint => {
            return result(
                &module,
                reference_label,
                candidate_label,
                vec![],
                None,
                None,
                Some("profiles have no common vertical coverage"),
            )
        }
        Coverage::Common(bottom, top) => (bottom, top),
    };

    let mut reference: [Vec<f64>; 4] = std::array::from_fn(|_| Vec::new());
    let mut candidate: [Vec<f64>; 4] = std::array::from_fn(|_| Vec::new());
    for (column, field) in reference.iter_mut().zip(FIELDS) {
        *column = profile_values(&module, reference_profile, field)?;
    }
    for (column, field) in candidate.iter_mut().zip(FIELDS) {
        *column = profile_values(&module, candidate_profile, field)?;
    }
    let mut interpolators: [Option<Interpolator>; 4] = std::array::from_fn(|_| None);
    for (interpolator, column) in interpolators.iter_mut().zip(candidate.iter()) {
        *interpolator = prepare(py, &candidate_height, column, max_gap_m)?;
    }
    let pressure = profile_values(&module, reference_profile, "pres")?;
    let levels = py.detach(|| {
        difference_levels(
            &reference_height,
            &pressure,
            &reference,
            &interpolators,
            bottom,
            top,
        )
    });
    let reason = levels
        .is_empty()
        .then_some("no common reference levels are usable");
    result(
        &module,
        reference_label,
        candidate_label,
        levels,
        Some(bottom),
        Some(top),
        reason,
    )
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(comparison_interpolate_at, module)?)?;
    module.add_function(wrap_pyfunction!(
        comparison_vertical_profile_difference,
        module
    )?)?;
    Ok(())
}

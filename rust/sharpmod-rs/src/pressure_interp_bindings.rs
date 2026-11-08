//! Fast path for the vendored SHARPpy renderer's log-pressure interpolation.
//!
//! This keeps the input order, NumPy's linear interpolation and SHARPpy's
//! endpoint snaps. The public Rust interpolation API has different sorting and
//! missing-value rules; it cannot replace the renderer's reference function.

use numpy::ndarray::ArrayView1;
use numpy::{
    Element, IntoPyArray, PyArray1, PyArrayDescrMethods, PyArrayMethods, PyReadonlyArray1,
    PyUntypedArrayMethods,
};
use pyo3::{prelude::*, types::PyFloat};

fn read_1d<'py, T: Element>(value: &Bound<'py, PyAny>) -> Option<PyReadonlyArray1<'py, T>> {
    let array = value.cast::<PyArray1<T>>().ok()?;
    if !array.is_aligned() || array.dtype().is_native_byteorder() == Some(false) {
        return None;
    }
    array.try_readonly().ok()
}

fn ordered_pairs(
    coordinates: ArrayView1<'_, f64>,
    values: ArrayView1<'_, f64>,
    coordinate_mask: Option<ArrayView1<'_, bool>>,
    value_mask: Option<ArrayView1<'_, bool>>,
) -> Option<Vec<(f64, f64)>> {
    let len = coordinates.len();
    if values.len() != len
        || coordinate_mask
            .as_ref()
            .is_some_and(|mask| mask.len() != len)
        || value_mask.as_ref().is_some_and(|mask| mask.len() != len)
    {
        return None;
    }
    let mut pairs = Vec::<(f64, f64)>::with_capacity(len);
    for (index, (&x, &y)) in coordinates.iter().zip(&values).enumerate() {
        if coordinate_mask.as_ref().is_some_and(|mask| mask[index])
            || value_mask.as_ref().is_some_and(|mask| mask[index])
        {
            continue;
        }
        if !x.is_finite() || !y.is_finite() || pairs.last().is_some_and(|last| last.0 > x) {
            return None;
        }
        pairs.push((x, y));
    }
    // An empty intersection can mean an all-masked scalar or a reference error.
    (!pairs.is_empty()).then_some(pairs)
}

fn pressure_value(pairs: &[(f64, f64)], target: f64) -> Option<f64> {
    if !target.is_finite() {
        return None;
    }
    let first = pairs[0];
    let last = *pairs.last()?;
    // SHARPpy applies NumPy isclose at the first then last usable endpoint.
    for (x, y) in [last, first] {
        if (target - x).abs() <= 1e-8 + 1e-5 * x.abs() {
            return Some(y);
        }
    }
    if target < first.0 || target > last.0 {
        return Some(f64::NAN);
    }
    let upper = pairs.partition_point(|&(x, _)| x <= target);
    let (x0, y0) = pairs[upper - 1];
    if x0 == target {
        return Some(y0);
    }
    let (x1, y1) = pairs[upper];
    // Keep NumPy's arithmetic order; reassociation can change derived values.
    let slope = (y1 - y0) / (x1 - x0);
    let value = slope * (target - x0) + y0;
    value.is_finite().then_some(value)
}

enum Interpolated {
    Scalar(f64),
    Vector(Vec<f64>),
}

#[pyclass(frozen, module = "sharpmod_rs")]
struct RenderPressureInterpolation {
    original: Py<PyAny>,
    float64: Py<PyAny>,
    masked_array: Py<PyAny>,
    masked: Py<PyAny>,
    nomask: Py<PyAny>,
}

impl RenderPressureInterpolation {
    fn new(py: Python<'_>, original: Bound<'_, PyAny>) -> PyResult<Self> {
        let ma = PyModule::import(py, "numpy.ma")?;
        Ok(Self {
            original: original.unbind(),
            float64: PyModule::import(py, "numpy")?.getattr("float64")?.unbind(),
            masked_array: ma.getattr("MaskedArray")?.unbind(),
            masked: ma.getattr("masked")?.unbind(),
            nomask: ma.getattr("nomask")?.unbind(),
        })
    }

    fn mask<'py>(&self, array: &Bound<'py, PyAny>) -> Option<Option<PyReadonlyArray1<'py, bool>>> {
        let mask = array.getattr("mask").ok()?;
        if mask.is(self.nomask.bind(array.py())) {
            Some(None)
        } else {
            Some(Some(read_1d(&mask)?))
        }
    }

    fn interpolate(
        &self,
        target: &Bound<'_, PyAny>,
        coordinates: &Bound<'_, PyAny>,
        values: &Bound<'_, PyAny>,
    ) -> Option<Interpolated> {
        let masked_array = self.masked_array.bind(target.py());
        if !coordinates.get_type().is(masked_array) || !values.get_type().is(masked_array) {
            return None;
        }
        let coordinates_mask = self.mask(coordinates)?;
        let values_mask = self.mask(values)?;
        let coordinates = read_1d(coordinates)?;
        let values = read_1d(values)?;
        let pairs = ordered_pairs(
            coordinates.as_array(),
            values.as_array(),
            coordinates_mask.as_ref().map(PyReadonlyArray1::as_array),
            values_mask.as_ref().map(PyReadonlyArray1::as_array),
        )?;

        if target.is_exact_instance_of::<PyFloat>()
            || target.get_type().is(self.float64.bind(target.py()))
        {
            let number = target.extract().ok()?;
            return pressure_value(&pairs, number).map(Interpolated::Scalar);
        }
        if target.get_type().is(masked_array) {
            if self
                .mask(target)?
                .is_some_and(|mask| mask.as_array().iter().any(|&bit| bit))
            {
                return None;
            }
        } else if target.cast_exact::<PyArray1<f64>>().is_err() {
            return None;
        }
        read_1d::<f64>(target)?
            .as_array()
            .iter()
            .map(|&number| pressure_value(&pairs, number))
            .collect::<Option<Vec<_>>>()
            .map(Interpolated::Vector)
    }
}

#[pymethods]
impl RenderPressureInterpolation {
    fn __call__<'py>(
        &self,
        p: &Bound<'py, PyAny>,
        pres: &Bound<'py, PyAny>,
        field: &Bound<'py, PyAny>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let py = p.py();
        match self.interpolate(p, pres, field) {
            Some(Interpolated::Scalar(value)) if value.is_nan() => Ok(self.masked.bind(py).clone()),
            Some(Interpolated::Scalar(value)) => self.float64.bind(py).call1((value,)),
            Some(Interpolated::Vector(mut values)) => {
                let constructor = self.masked_array.bind(py);
                if values.iter().any(|value| value.is_nan()) {
                    let mask: Vec<bool> = values
                        .iter_mut()
                        .map(|value| {
                            if value.is_nan() {
                                // The reference ma.where leaves zero under a missing mask.
                                *value = 0.;
                                true
                            } else {
                                false
                            }
                        })
                        .collect();
                    constructor.call1((values.into_pyarray(py), mask.into_pyarray(py)))
                } else {
                    constructor.call1((values.into_pyarray(py),))
                }
            }
            None => self.original.bind(py).call1((p, pres, field)),
        }
    }
}

#[pyfunction]
fn install_render_pressure_interpolation(py: Python<'_>) -> PyResult<()> {
    let interp = PyModule::import(py, "sharppy.sharptab.interp")?;
    let original = interp.getattr("generic_interp_pres")?;
    if !original.is_instance_of::<RenderPressureInterpolation>() {
        interp.setattr(
            "generic_interp_pres",
            Py::new(py, RenderPressureInterpolation::new(py, original)?)?,
        )?;
    }
    Ok(())
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(
        install_render_pressure_interpolation,
        module
    )?)
}

#[cfg(test)]
mod tests;

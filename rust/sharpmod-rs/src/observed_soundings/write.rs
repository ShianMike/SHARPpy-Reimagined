use super::{columns, MISSING};
use pyo3::{
    exceptions::PyValueError,
    prelude::*,
    types::{PyDict, PyList},
};

struct Metadata<'py> {
    datetime: Bound<'py, PyAny>,
    mapping: Bound<'py, PyAny>,
    scalar: Bound<'py, PyAny>,
    utc: Bound<'py, PyAny>,
}

impl<'py> Metadata<'py> {
    fn normalize(&self, value: &Bound<'py, PyAny>, depth: usize) -> PyResult<Bound<'py, PyAny>> {
        if depth > 64 {
            return Err(PyValueError::new_err(
                "observed sounding metadata is nested too deeply",
            ));
        }
        let py = value.py();
        if value.is_instance(&self.datetime)? {
            return self
                .utc
                .call1((value,))?
                .call_method0("isoformat")?
                .call_method1("replace", ("+00:00", "Z"));
        }
        if value.is_instance(&self.mapping)? {
            let output = PyDict::new(py);
            for entry in value.call_method0("items")?.try_iter()? {
                let entry = entry?;
                output.set_item(
                    entry.get_item(0)?.str()?,
                    self.normalize(&entry.get_item(1)?, depth + 1)?,
                )?;
            }
            return Ok(output.into_any());
        }
        if value.is_instance_of::<PyList>() || value.is_instance_of::<pyo3::types::PyTuple>() {
            let output = PyList::empty(py);
            for item in value.try_iter()? {
                output.append(self.normalize(&item?, depth + 1)?)?;
            }
            return Ok(output.into_any());
        }
        if value.is_instance(&self.scalar)? {
            return self.normalize(&value.call_method0("item")?, depth + 1);
        }
        Ok(value.clone())
    }
}

#[pyfunction]
pub(super) fn write_observed_npz(
    py: Python<'_>,
    result: &Bound<'_, PyAny>,
    output: &Bound<'_, PyAny>,
    loc: &Bound<'_, PyAny>,
) -> PyResult<String> {
    let api = PyModule::import(py, "sharpmod.providers.observations")?;
    let numpy = PyModule::import(py, "numpy")?;
    let profile = result.getattr("profile")?;
    let metadata = py
        .get_type::<PyDict>()
        .call1((result.getattr("metadata")?,))?
        .cast_into::<PyDict>()?;
    let valid = api.call_method1("_utc_datetime", (result.getattr("valid")?,))?;
    let arrays = columns(py, &profile)?;
    let count = arrays.get_item("pres")?.unwrap().len()?;
    let label = [
        loc.clone(),
        metadata
            .get_item("loc")?
            .unwrap_or_else(|| py.None().into_bound(py)),
        metadata
            .get_item("station_name")?
            .unwrap_or_else(|| py.None().into_bound(py)),
        result.getattr("station_id")?,
    ]
    .into_iter()
    .find_map(|value| match value.is_truthy() {
        Ok(true) => Some(value.str().map(|value| value.into_any())),
        Ok(false) => None,
        Err(error) => Some(Err(error)),
    })
    .transpose()?
    .unwrap_or_else(|| py.None().into_bound(py));
    let omega = match profile.getattr("omeg") {
        Ok(value) => value,
        Err(error) if error.is_instance_of::<pyo3::exceptions::PyAttributeError>(py) => {
            py.None().into_bound(py)
        }
        Err(error) => return Err(error),
    };
    arrays.set_item(
        "omeg",
        if omega.is_none() {
            numpy.call_method1("full", ((count,), MISSING))?
        } else {
            let dtype = PyDict::new(py);
            dtype.set_item("dtype", numpy.getattr("float64")?)?;
            numpy
                .getattr("ma")?
                .getattr("asarray")?
                .call((omega,), Some(&dtype))?
                .call_method1("filled", (MISSING,))?
        },
    )?;
    let fallback = PyList::empty(py);
    if let Some(attempts) = metadata.get_item("fallback_attempts")? {
        let mapping = PyModule::import(py, "collections.abc")?.getattr("Mapping")?;
        for attempt in attempts.try_iter()? {
            let attempt = attempt?;
            if attempt.is_instance(&mapping)? {
                fallback.append(attempt.call_method1("get", ("provider", ""))?.str()?)?;
            }
        }
    }
    let dtype = PyDict::new(py);
    dtype.set_item("dtype", py.get_type::<pyo3::types::PyString>())?;
    arrays.set_item(
        "fallback_from",
        numpy.call_method("asarray", (fallback,), Some(&dtype))?,
    )?;
    for name in ["lat", "lon"] {
        arrays.set_item(
            name,
            metadata
                .get_item(name)?
                .map_or_else(|| Ok(0.), |value| value.extract::<f64>())?,
        )?;
    }
    arrays.set_item("loc", label)?;
    arrays.set_item("model", "Observed")?;
    for name in ["run", "valid"] {
        arrays.set_item(name, valid.call_method1("strftime", ("%Y-%m-%d %H:%M",))?)?;
    }
    arrays.set_item("fxx", 0)?;
    arrays.set_item("observed", true)?;
    for (name, attr) in [
        ("source", "provider"),
        ("source_provider", "provider"),
        ("source_provider_name", "provider_name"),
        ("source_station", "station_id"),
        ("source_url", "source_url"),
        ("requested_station", "requested_station"),
    ] {
        arrays.set_item(name, result.getattr(attr)?)?;
    }
    let serializer = Metadata {
        datetime: PyModule::import(py, "datetime")?.getattr("datetime")?,
        mapping: PyModule::import(py, "collections.abc")?.getattr("Mapping")?,
        scalar: numpy.getattr("generic")?,
        utc: api.getattr("_utc_datetime")?,
    };
    let sidecar = serializer
        .normalize(metadata.as_any(), 0)?
        .cast_into::<PyDict>()?;
    for (name, attr) in [
        ("provider", "provider"),
        ("provider_name", "provider_name"),
        ("source_station", "station_id"),
        ("requested_station", "requested_station"),
        ("source_url", "source_url"),
    ] {
        sidecar.set_item(name, result.getattr(attr)?)?;
    }
    let output = output.str()?.to_str()?.to_owned();
    sidecar.set_item(
        "valid",
        valid
            .call_method0("isoformat")?
            .call_method1("replace", ("+00:00", "Z"))?,
    )?;
    sidecar.set_item("levels", count)?;
    sidecar.set_item(
        "backend",
        format!("{} observed provider", result.getattr("provider")?.str()?),
    )?;
    sidecar.set_item("decoder", "provider-neutral observed-sounding adapter")?;
    sidecar.set_item("cache_hit", false)?;
    sidecar.set_item(
        "surface_vorticity_source",
        "not provided by observed archive",
    )?;
    sidecar.set_item("npz", &output)?;
    api.call_method1("_atomic_write_sounding_pair", (&output, arrays, sidecar))?;
    Ok(output)
}

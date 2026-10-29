//! Existing model extraction and collection objects, scheduled by native workers.

use std::path::PathBuf;

use pyo3::{
    exceptions::{PyOSError, PyValueError},
    prelude::*,
    types::{PyDict, PyList, PyTuple},
};

struct Request {
    model: String,
    lat: f64,
    lon: f64,
    run: Py<PyAny>,
    hour: i64,
    location: Py<PyAny>,
    directory: PathBuf,
    members: Vec<(String, Py<PyAny>)>,
    extract: Py<PyAny>,
    decode: Py<PyAny>,
    path: Py<PyAny>,
    range_error: Py<PyAny>,
    member_errors: Py<PyTuple>,
    bridge_error: Py<PyAny>,
    max_reason: usize,
}

enum Member {
    Loaded(Py<PyAny>),
    Failed(String),
}

#[pyclass]
#[derive(Default)]
struct WarningScope {
    active: usize,
    context: Option<Py<PyAny>>,
}

impl Request {
    fn member(&self, py: Python<'_>, index: usize) -> PyResult<Member> {
        let (label, argument) = &self.members[index];
        let path = self.directory.join(format!("{label}.npz"));
        let outcome = || -> PyResult<Py<PyAny>> {
            let kwargs = PyDict::new(py);
            kwargs.set_item("run_time", self.run.bind(py))?;
            kwargs.set_item("fxx", self.hour)?;
            kwargs.set_item("out_path", path.to_string_lossy())?;
            kwargs.set_item("loc", self.location.bind(py))?;
            kwargs.set_item("member", argument.bind(py))?;
            kwargs.set_item(
                "download_dir",
                self.directory
                    .join(format!("{label}-grib"))
                    .to_string_lossy(),
            )?;
            let written = self
                .extract
                .bind(py)
                .call((&self.model, self.lat, self.lon), Some(&kwargs))?;
            let path = if written.is_truthy()? {
                self.path.bind(py).call1((written,))?
            } else {
                self.path.bind(py).call1((path.to_string_lossy(),))?
            };
            let decoded = self.decode.bind(py).call1((path,))?;
            Ok(decoded.get_item(0)?.unbind())
        };
        match outcome() {
            Ok(collection) => Ok(Member::Loaded(collection)),
            Err(error) if error.is_instance(py, self.range_error.bind(py)) => {
                Err(PyErr::from_value(self.bridge_error.bind(py).call1((
                    "INVALID_ARGUMENT",
                    error.value(py).str()?,
                    false,
                ))?))
            }
            Err(error) if error.is_instance(py, self.member_errors.bind(py)) => {
                let lines = error
                    .value(py)
                    .str()?
                    .call_method0("strip")?
                    .call_method0("splitlines")?;
                let reason = if lines.len()? == 0 {
                    error.get_type(py).name()?.to_string()
                } else {
                    lines.get_item(0)?.extract::<String>()?
                };
                Ok(Member::Failed(
                    reason.chars().take(self.max_reason).collect(),
                ))
            }
            Err(error) => Err(error),
        }
    }
}

#[pyfunction(pass_module)]
#[allow(clippy::too_many_arguments)] // One validated ensemble request and its isolated directory.
fn acquire_ensemble_members<'py>(
    module: &Bound<'py, PyModule>,
    model: String,
    latitude: f64,
    longitude: f64,
    run_time: Py<PyAny>,
    forecast_hour: i64,
    location: Py<PyAny>,
    members: Vec<String>,
    allowed: &Bound<'py, PyDict>,
    directory: PathBuf,
) -> PyResult<Bound<'py, PyTuple>> {
    let py = module.py();
    if members.is_empty() || members.len() > 32 {
        return Err(PyValueError::new_err(
            "ensemble requires 1 to 32 validated members",
        ));
    }
    let model_api = PyModule::import(py, "sharpmod.tools.model_extract")?;
    let source = PyModule::import(py, "sharpmod.tools.era5_extract")?;
    let cache = PyModule::import(py, "sharpmod.bridge.sounding_cache")?;
    let request = Request {
        model,
        lat: latitude,
        lon: longitude,
        run: run_time,
        hour: forecast_hour,
        location,
        directory,
        members: members
            .into_iter()
            .map(|label| {
                let argument = allowed
                    .get_item(&label)?
                    .ok_or_else(|| PyValueError::new_err("member is not allowed"))?
                    .unbind();
                Ok((label, argument))
            })
            .collect::<PyResult<_>>()?,
        extract: model_api.getattr("extract")?.unbind(),
        decode: cache.getattr("decode_collection")?.unbind(),
        path: PyModule::import(py, "pathlib")?.getattr("Path")?.unbind(),
        range_error: source.getattr("ParameterRangeError")?.unbind(),
        member_errors: PyTuple::new(
            py,
            [
                source.getattr("ERA5ExtractionError")?,
                cache.getattr("SoundingDecodeError")?,
                py.get_type::<PyOSError>().into_any(),
                py.get_type::<PyValueError>().into_any(),
            ],
        )?
        .unbind(),
        bridge_error: PyModule::import(py, "sharpmod.bridge.errors")?
            .getattr("BridgeError")?
            .unbind(),
        max_reason: PyModule::import(py, "sharpmod.bridge.ensemble")?
            .getattr("MAX_REASON")?
            .extract()?,
    };
    // The explicitly selected xarray path redirects process-wide Python streams.
    let parallel = std::env::var("SHARPMOD_GRIB_DECODER")
        .unwrap_or_default()
        .trim()
        .to_lowercase()
        != "xarray";
    let results = if parallel {
        let pool = crate::ensemble::pool().map_err(PyValueError::new_err)?;
        let owner = module.getattr("_ensemble_warning_scope")?;
        {
            let mut scope = owner.cast::<WarningScope>()?.borrow_mut();
            if scope.active == 0 {
                let context = PyModule::import(py, "sharpmod.upstream.upstream_warnings")?
                    .call_method0("known_herbie_deprecations")?;
                context.call_method0("__enter__")?;
                scope.context = Some(context.unbind());
            }
            scope.active += 1;
        }
        let result = py.detach(|| {
            crate::ensemble::acquire(pool, request.members.len(), |index| {
                Python::attach(|py| request.member(py, index))
            })
        });
        // Nested legacy warning scopes must all join before restoring the original filters.
        {
            let mut scope = owner.cast::<WarningScope>()?.borrow_mut();
            scope.active -= 1;
            if scope.active == 0 {
                scope
                    .context
                    .take()
                    .unwrap()
                    .bind(py)
                    .call_method1("__exit__", (py.None(), py.None(), py.None()))?;
            }
        }
        result?
    } else {
        (0..request.members.len())
            .map(|index| request.member(py, index))
            .collect::<PyResult<_>>()?
    };
    let collections = PyList::empty(py);
    let labels = PyList::empty(py);
    let failures = PyList::empty(py);
    let failure =
        PyModule::import(py, "sharpmod.analysis.ensemble_members")?.getattr("MemberFailure")?;
    for ((label, _), result) in request.members.into_iter().zip(results) {
        match result {
            Member::Loaded(collection) => {
                collections.append(collection)?;
                labels.append(label)?;
            }
            Member::Failed(reason) => failures.append(failure.call1((label, "failed", reason))?)?,
        }
    }
    PyTuple::new(py, [collections, labels, failures])
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add(
        "_ensemble_warning_scope",
        Py::new(module.py(), WarningScope::default())?,
    )?;
    module.add_function(wrap_pyfunction!(acquire_ensemble_members, module)?)?;
    Ok(())
}

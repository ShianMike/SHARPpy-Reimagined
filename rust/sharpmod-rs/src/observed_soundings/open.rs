use crate::sounding_files::Pending;
use pyo3::{
    exceptions::PyValueError,
    prelude::*,
    types::{PyDict, PyList, PyTuple},
};
use std::path::PathBuf;

fn failure(py: Python<'_>, error: PyErr, station: &str, when: &Bound<'_, PyAny>) -> PyErr {
    let convert = || -> PyResult<PyErr> {
        let obs = PyModule::import(py, "sharpmod.providers.observations")?;
        let label = format!(
            "{station} at {}",
            when.call_method1("strftime", ("%Y-%m-%d %HZ",))?.str()?
        );
        let details = PyDict::new(py);
        let kinds = if error.is_instance(py, &obs.getattr("ObservedFallbackError")?) {
            let attempts = PyList::empty(py);
            let mut kinds = Vec::new();
            for attempt in error.value(py).getattr("attempts")?.try_iter()? {
                let attempt = attempt?;
                kinds.push(attempt.get_item("error_type")?.extract::<String>()?);
                let item = PyDict::new(py);
                item.set_item("provider", attempt.get_item("provider")?)?;
                let message = attempt
                    .get_item("error")?
                    .str()?
                    .to_str()?
                    .chars()
                    .take(200)
                    .collect::<String>();
                item.set_item("error", message)?;
                attempts.append(item)?;
            }
            details.set_item("attempts", attempts)?;
            kinds
        } else if error.is_instance_of::<PyValueError>(py) {
            vec!["ObservedParseError".into()]
        } else {
            vec![error
                .get_type(py)
                .getattr("__name__")?
                .extract::<String>()?]
        };
        let (code, retryable, message) = if kinds.iter().any(|kind| kind == "ObservedParseError") {
            ("SOUNDING_INVALID", false, format!(
                "The {label} sounding was received but could not be read. Try another provider or time."
            ))
        } else if kinds.iter().any(|kind| kind == "ObservedRetrievalError") {
            (
                "SOUNDING_FETCH_FAILED",
                true,
                format!(
                "The {label} sounding could not be downloaded. Check the connection and try again."
            ),
            )
        } else {
            (
                "SOUNDING_UNAVAILABLE",
                false,
                format!("No provider has a sounding for {label}. Try another time or station."),
            )
        };
        Ok(PyErr::from_value(
            PyModule::import(py, "sharpmod.bridge.errors")?
                .getattr("BridgeError")?
                .call1((code, message, retryable, details))?,
        ))
    };
    convert().unwrap_or(error)
}

#[pyfunction]
pub(super) fn open_observed_sounding<'py>(
    py: Python<'py>,
    params: &Bound<'py, PyDict>,
) -> PyResult<Bound<'py, PyAny>> {
    let validators = PyModule::import(py, "sharpmod.bridge.params")?;
    validators.call_method1("keys", (params, ("station_id", "time"), ("provider",)))?;
    let station: String = validators
        .call_method1("text", (params, "station_id", 1, 16))?
        .extract()?;
    let when = validators.call_method1("time", (params, "time"))?;
    let obs = PyModule::import(py, "sharpmod.providers.observations")?;
    let mut choices = vec!["auto".to_owned()];
    choices.extend(
        obs.call_method0("registered_provider_keys")?
            .extract::<Vec<String>>()?,
    );
    let provider: String = validators
        .call_method1(
            "choice",
            (params, "provider", PyTuple::new(py, choices)?, "auto"),
        )?
        .extract()?;
    let providers = if provider == "auto" {
        obs.getattr("DEFAULT_PROVIDER_ORDER")?
    } else {
        PyTuple::new(py, [provider])?.into_any()
    };
    let options = PyDict::new(py);
    options.set_item("providers", providers)?;
    let provider_error = obs.getattr("ObservedProviderError")?;
    let result = obs
        .getattr("fetch_observed")?
        .call((&station, &when), Some(&options))
        .map_err(|error| {
            if error.is_instance(py, &provider_error) {
                failure(py, error, &station, &when)
            } else {
                error
            }
        })?;
    let cancel = PyModule::import(py, "sharpmod.bridge.cancellation")?;
    cancel.call_method0("check")?;
    let cache = PyModule::import(py, "sharpmod.bridge.sounding_cache")?.call_method0("cache")?;
    let id: String = cache.call_method0("new_id")?.extract()?;
    let path: PathBuf = cache.call_method1("data_path", (&id, ".npz"))?.extract()?;
    // Own newly reserved files before the atomic writer runs. Every later
    // failure, including cancellation, decoding or registration, removes both.
    let mut pending = py.detach(|| {
        let mut pending = Pending::default();
        pending.write(path.clone(), &[])?;
        pending.write(path.with_extension("json"), &[])?;
        Ok::<_, std::io::Error>(pending)
    })?;
    obs.call_method1("write_observed_npz", (&result, &path))
        .map_err(|error| {
            if error.is_instance_of::<PyValueError>(py) {
                failure(py, error, &station, &when)
            } else {
                error
            }
        })?;
    cancel.call_method0("check")?;
    let api = PyModule::import(py, "sharpmod.bridge.sounding_summary")?;
    let collection = api.call_method1("decode", (&path,))?.get_item(0)?;
    let metadata = result.getattr("metadata")?;
    let name = metadata.call_method1("get", ("station_name",))?;
    let name = if name.is_truthy()? {
        name.str()?.to_str()?.trim().to_string()
    } else {
        String::new()
    };
    let fields = PyDict::new(py);
    let station_id = result.getattr("station_id")?;
    fields.set_item(
        "title",
        if name.is_empty() {
            station_id.str()?.to_str()?.to_owned()
        } else {
            format!("{} {name}", station_id.str()?)
        },
    )?;
    fields.set_item("location", &station_id)?;
    fields.set_item("station_id", &station_id)?;
    fields.set_item(
        "station_name",
        if name.is_empty() { None } else { Some(name) },
    )?;
    for (field, source) in [("latitude", "lat"), ("longitude", "lon")] {
        fields.set_item(
            field,
            validators.call_method1("num", (metadata.call_method1("get", (source,))?,))?,
        )?;
    }
    fields.set_item(
        "valid_time",
        validators.call_method1("iso", (result.getattr("valid")?,))?,
    )?;
    for field in ["provider", "provider_name"] {
        fields.set_item(field, result.getattr(field)?)?;
    }
    let source = result.getattr("source_url")?;
    fields.set_item(
        "source_url",
        if source.is_truthy()? {
            source
        } else {
            py.None().into_bound(py)
        },
    )?;
    let summary = api
        .getattr("summary")?
        .call((&id, "observed", &collection), Some(&fields))?;
    cancel.call_method0("check")?;
    let result = api.call_method1("register", (&id, &path, &summary, collection))?;
    pending.committed = true;
    Ok(result)
}

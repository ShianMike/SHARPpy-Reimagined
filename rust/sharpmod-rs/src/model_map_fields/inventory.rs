//! Cache the inventory with the exact GRIB revision, never with its URL alone.
use super::*;
use tokio::runtime::Runtime;

type InventoryKey = (String, ObjectIdentity);
type ProbeKey = (String, u64);
type ProbeResult = Result<Option<ObjectIdentity>, Error>;

fn cache() -> &'static Arc<Cache<InventoryKey, Py<PyAny>>> {
    static CACHE: OnceLock<Arc<Cache<InventoryKey, Py<PyAny>>>> = OnceLock::new();
    CACHE.get_or_init(|| Cache::new(16 * 1024 * 1024, 128))
}

fn probes() -> &'static Arc<Cache<ProbeKey, ProbeResult>> {
    static PROBES: OnceLock<Arc<Cache<ProbeKey, ProbeResult>>> = OnceLock::new();
    PROBES.get_or_init(|| Cache::new(0, 128))
}

pub(super) fn clear() {
    cache().clear();
    probes().clear();
}

fn probe(runtime: &Runtime, url: &str, timeout: f64, parent: Option<&Py<PyAny>>) -> ProbeResult {
    let stopped = || cancelled(parent);
    if stopped().map_err(Error::Invalid)? {
        return Err(Error::Cancelled);
    }
    let client = ModelClient::shared();
    let (consumer, producer) = probes().reserve_transient((url.to_owned(), timeout.to_bits()));
    if let Some(ticket) = producer {
        let url = url.to_owned();
        runtime.spawn(async move {
            let result = client
                .identity(&url, timeout, || Ok(!ticket.shared()))
                .await;
            ticket.complete_transient(result);
        });
    }

    match consumer.wait(stopped) {
        Ok(result) => (*result).clone(),
        Err(WaitError::Cancelled) => Err(Error::Cancelled),
        Err(WaitError::Failed(error)) => Err(Error::Invalid(error)),
        Err(WaitError::Abandoned) => runtime.block_on(client.identity(url, timeout, stopped)),
    }
}

pub(super) fn fetch(
    py: Python<'_>,
    url: &str,
    timeout: f64,
    parent: Option<&Py<PyAny>>,
) -> PyResult<(Py<PyAny>, Option<ObjectIdentity>)> {
    let runtime = crate::weather::runtime()?;
    let identity = py
        .detach(|| probe(runtime, url, timeout, parent))
        .map_err(|error| field_error(py, error))?;
    let key = identity.clone().map(|identity| (url.to_string(), identity));
    let mut retried = false;
    let ticket = loop {
        let ticket = cache().reserve(key.clone());
        if ticket.leader {
            break ticket;
        }
        match py.detach(|| ticket.wait(|| cancelled(parent))) {
            Ok(inventory) => return Ok((inventory.clone_ref(py), identity)),
            Err(WaitError::Abandoned) if !retried => {
                retried = true;
            }
            Err(error) => {
                return Err(field_error(
                    py,
                    match error {
                        WaitError::Cancelled => Error::Cancelled,
                        WaitError::Abandoned => {
                            Error::Invalid("shared inventory producer stopped".into())
                        }
                        WaitError::Failed(message) => Error::Invalid(message),
                    },
                ))
            }
        }
    };
    let is_ecmwf = url.contains("/ifs/");
    let index_url = if is_ecmwf {
        format!("{}.index", url.strip_suffix(".grib2").unwrap_or(url))
    } else {
        format!("{url}.idx")
    };
    let payload = py
        .detach(|| {
            runtime.block_on(ModelClient::shared().fetch(
                &index_url,
                timeout,
                4 * 1024 * 1024,
                None,
                || cancelled(parent).map(|stopped| stopped && !ticket.shared()),
            ))
        })
        .map_err(|error| field_error(py, error))?;
    let parser = PyModule::import(
        py,
        if is_ecmwf {
            "sharpmod.providers.ecmwf_fields"
        } else {
            "sharpmod.providers.hrrr_field"
        },
    )?;
    let rows = parser.call_method1(
        "parse_inventory",
        (String::from_utf8_lossy(&payload).as_ref(),),
    )?;
    let count = rows.len()?;
    if count == 0 {
        return Err(PyErr::from_value(
            PyModule::import(py, "sharpmod.providers.hrrr_field")?
                .getattr("HrrrFieldUnavailable")?
                .call1(("model inventory was empty or unreadable",))?,
        ));
    }
    // Include Python row/string overhead in the retention budget. Payloads that
    // exceed it still work; only their completed metadata is not retained.
    let size = payload
        .len()
        .saturating_mul(4)
        .saturating_add(count.saturating_mul(512));
    ticket.complete(Ok(rows.clone().unbind()), size);
    if cancelled(parent).map_err(pyo3::exceptions::PyRuntimeError::new_err)? {
        return Err(field_error(py, Error::Cancelled));
    }
    Ok((rows.unbind(), identity))
}

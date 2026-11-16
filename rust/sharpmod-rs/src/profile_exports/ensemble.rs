//! Keep requested, loaded and diagnostic membership distinct in ensemble CSVs.

use super::*;
use pyo3::{exceptions::PyTypeError, types::PySet};

const FIELDS: &[&str] = &[
    "record_type",
    "valid_time",
    "metric",
    "member",
    "status",
    "reason",
    "requested_count",
    "loaded_count",
    "summary_usable_count",
    "diagnostic_usable_count",
    "minimum",
    "p10",
    "median",
    "p90",
    "maximum",
    "distribution_available",
];

#[pyfunction(signature = (path, summary, acquisition=None))]
fn export_profile_ensemble_csv<'py>(
    py: Python<'py>,
    path: &Bound<'py, PyAny>,
    summary: &Bound<'py, PyAny>,
    acquisition: Option<&Bound<'py, PyAny>>,
) -> PyResult<Py<PyAny>> {
    let boundary = Boundary::new(py)?;
    let none = py.None().into_bound(py);
    let acquisition = acquisition.unwrap_or(&none);
    if !summary.is_instance(&boundary.api.getattr("EnsembleSummary")?)? {
        return Err(PyTypeError::new_err("summary must be an EnsembleSummary"));
    }
    let acquisition = if acquisition.is_truthy()? {
        acquisition.clone()
    } else {
        let loaded = summary.getattr("loaded_members")?;
        let loaded = if loaded.is_truthy()? {
            loaded
        } else {
            summary.getattr("members")?
        };
        let requested = summary.getattr("requested_members")?;
        let requested = if requested.is_truthy()? {
            requested
        } else {
            let combined = PyList::new(py, collect(&loaded)?)?;
            for member in summary.getattr("missing_members")?.try_iter()? {
                combined.append(member?)?;
            }
            py.get_type::<PyDict>()
                .call_method1("fromkeys", (combined,))?
        };
        boundary
            .api
            .getattr("EnsembleAcquisition")?
            .call1((requested, loaded))?
    };
    let requested = acquisition.getattr("requested_members")?;
    let requested = collect(&if requested.is_truthy()? {
        requested
    } else {
        summary.getattr("requested_members")?
    })?;
    let loaded = acquisition.getattr("loaded_members")?;
    let loaded = if loaded.is_truthy()? {
        loaded
    } else {
        summary.getattr("loaded_members")?
    };
    let loaded = PySet::new(py, collect(&loaded)?)?;
    let unusable = PySet::new(py, collect(&summary.getattr("unusable_members")?)?)?;
    let outcomes = PyDict::new(py);
    for outcome in summary.getattr("member_samples")?.try_iter()? {
        let outcome = outcome?;
        outcomes.set_item(outcome.getattr("member")?, &outcome)?;
    }
    let common = PyDict::new(py);
    common.set_item(
        "valid_time",
        boundary.time(&summary.getattr("valid_time")?)?,
    )?;
    let requested_count = summary.getattr("requested_member_count")?;
    common.set_item(
        "requested_count",
        if requested_count.is_none() {
            requested.len().into_pyobject(py)?.into_any()
        } else {
            requested_count
        },
    )?;
    let loaded_count = summary.getattr("loaded_member_count")?;
    common.set_item(
        "loaded_count",
        if loaded_count.is_none() {
            loaded.len().into_pyobject(py)?.into_any()
        } else {
            loaded_count
        },
    )?;
    common.set_item(
        "summary_usable_count",
        summary.getattr("available_member_count")?,
    )?;
    common.set_item(
        "distribution_available",
        summary.getattr("distribution_available")?,
    )?;
    let fields: Vec<_> = FIELDS
        .iter()
        .map(|name| pyo3::types::PyString::new(py, name).into_any())
        .collect();
    let mut output = boundary.output(path, &fields, true)?;
    for item in summary
        .getattr("scalar")?
        .call_method0("items")?
        .try_iter()?
    {
        let (metric, distribution): (Bound<'py, PyAny>, Bound<'py, PyAny>) = item?.extract()?;
        let row = common.copy()?;
        row.set_item("record_type", "diagnostic")?;
        row.set_item("metric", metric)?;
        row.set_item("diagnostic_usable_count", distribution.getattr("count")?)?;
        for field in ["minimum", "p10", "median", "p90", "maximum"] {
            row.set_item(field, distribution.getattr(field)?)?;
        }
        append(&mut output, &fields, &row)?;
    }
    for member in requested {
        let failure = acquisition.call_method1("failure_for", (&member,))?;
        let is_loaded = loaded.contains(&member)?;
        let (mut status, mut reason) = if is_loaded {
            if unusable.contains(&member)? {
                (
                    "loaded-unusable".into(),
                    "profile was not usable for the requested summary".into(),
                )
            } else {
                ("loaded".into(), String::new())
            }
        } else if !failure.is_none() {
            (
                text(&failure.getattr("status")?)?,
                truthy(&failure.getattr("reason")?)?,
            )
        } else {
            ("unavailable".into(), String::new())
        };
        if is_loaded {
            if let Some(diagnostic) = outcomes.get_item(&member)? {
                let outcome = diagnostic.getattr("outcome")?;
                if outcome.eq("failed")? || outcome.eq("cancelled")? {
                    status = format!("diagnostic-{}", outcome.str()?.to_str()?);
                }
                if !outcome.eq("completed")? {
                    reason = truthy(&diagnostic.getattr("reason")?)?;
                }
            }
        }
        let row = common.copy()?;
        row.set_item("record_type", "member")?;
        row.set_item("member", member)?;
        row.set_item("status", status)?;
        row.set_item("reason", reason)?;
        append(&mut output, &fields, &row)?;
    }
    output.finish()
}

pub(super) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(export_profile_ensemble_csv, module)?)?;
    Ok(())
}

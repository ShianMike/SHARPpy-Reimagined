//! Product metadata boundary. Preserve the existing inventory matching contracts.
use pyo3::{prelude::*, types::PyDict};
use sharpmod_weather::model_http::ObjectIdentity;
use std::collections::HashMap;

pub(super) struct Plan {
    pub fields: Vec<Field>,
    pub identities: HashMap<String, Option<ObjectIdentity>>,
    pub sum: bool,
    pub maximum: bool,
    pub diff: bool,
}

pub(super) struct Field {
    pub name: String,
    pub url: String,
    pub offset: u64,
    pub length: u64,
    pub subfield: usize,
}

// The first supported lead is catalogue metadata. Only these recipes
// combine a trailing window of records from the same initialization.
#[pyfunction]
pub(super) fn map_field_trailing_window(key: &str, hour: u32, minimum_hour: u32) -> u32 {
    match key {
        "uh-0-3km-run" | "uh-2-5km-run" => hour,
        "uh-0-3km" | "uh-2-5km-1h" | "uh-2-5km-3h" => minimum_hour,
        _ if key.starts_with("qpf-") && key != "qpf-1h-ptype" => minimum_hour,
        _ => 0,
    }
}

pub(super) fn fields(
    py: Python<'_>,
    product: &Bound<'_, PyAny>,
    run: &Bound<'_, PyAny>,
    hour: u32,
    timeout: f64,
    model: &str,
    parent: Option<&Py<PyAny>>,
) -> PyResult<Plan> {
    let api = PyModule::import(py, "sharpmod.providers.hrrr_field")?;
    let hours: Vec<u32> = PyModule::import(py, "sharpmod.tools.model_extract")?
        .call_method1("get_config", (model,))?
        .getattr("fxx_values")?
        .extract()?;
    if hours.into_iter().max().is_none_or(|maximum| hour > maximum) {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "forecast hour exceeds this model's published horizon",
        ));
    }
    let key: String = product.getattr("key")?.extract()?;
    let minimum_hour: u32 = product.getattr("max_hours")?.extract()?;
    if hour < minimum_hour {
        return Err(PyErr::from_value(
            api.getattr("HrrrFieldUnavailable")?.call1((format!(
                "{} needs {minimum_hour} complete forecast hours",
                product.getattr("label")?
            ),))?,
        ));
    }
    let nam_sum = matches!(model, "nam" | "nam-3km-conus") && key == "apcp";
    // The p-type composite reads one 1-h accumulation plus instantaneous
    // records, so it must not sweep the window into hourly sums.
    let hourly_sum = key.starts_with("qpf-") && key != "qpf-1h-ptype";
    let interval_accumulation = key == "qpf-1h-ptype";
    let hourly_diff = key == "snow-1h";
    let sum = nam_sum || hourly_sum;
    let window = map_field_trailing_window(&key, hour, minimum_hour);
    if key == "apcp" && hour == 0 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "F000 precipitation is supplied as a zero field by the provider",
        ));
    }
    let sources: Vec<(&str, u32)> = if nam_sum {
        (3..hour)
            .step_by(3)
            .chain(std::iter::once(hour))
            .map(|hour| ("sfc", hour))
            .collect()
    } else if hourly_diff {
        if hour == 1 {
            vec![("sfc", 1)]
        } else {
            vec![("sfc", hour - 1), ("sfc", hour)]
        }
    } else if window > 0 {
        (hour - window + 1..=hour)
            .map(|hour| ("sfc", hour))
            .collect()
    } else {
        vec![("sfc", hour), ("prs", hour)]
    };
    let specs: Vec<_> = product
        .getattr("all_fields")?
        .try_iter()?
        .collect::<PyResult<_>>()?;
    let mut fields = Vec::new();
    let mut identities = HashMap::new();
    let mut inventories: HashMap<String, Py<PyAny>> = HashMap::new();
    for (source, step) in sources {
        let selected: Vec<_> = specs
            .iter()
            .filter_map(|spec| {
                match spec
                    .getattr("source")
                    .and_then(|value| value.extract::<String>())
                {
                    Ok(value) if value == source => Some(Ok(spec)),
                    Ok(_) => None,
                    Err(error) => Some(Err(error)),
                }
            })
            .collect::<PyResult<_>>()?;
        if selected.is_empty() {
            continue;
        }
        let releases: Vec<Py<PyAny>> = if model == "rrfs-a" || model.starts_with("refs-m") {
            PyModule::import(py, "sharpmod.providers.rrfs_nomads")?
                .call_method0("_release_candidates")?
                .try_iter()?
                .map(|item| item.map(Bound::unbind))
                .collect::<PyResult<_>>()?
        } else {
            vec![py.None()]
        };
        let mut located = None;
        let mut last_error = None;
        for release in releases {
            let kwargs = PyDict::new(py);
            kwargs.set_item("release", release.bind(py))?;
            let url: String = api
                .getattr("grib_url")?
                .call((run, step, source, model), Some(&kwargs))?
                .extract()?;
            // Some models publish surface and pressure fields in one object.
            // Keep one revision throughout this request, including its offsets.
            if let Some(inventory) = inventories.get(&url) {
                located = Some((url, inventory.clone_ref(py)));
                break;
            }
            match super::inventory::fetch(py, &url, timeout, parent) {
                Ok((inventory, identity)) => {
                    if !release.bind(py).is_none() {
                        PyModule::import(py, "sharpmod.providers.rrfs_nomads")?
                            .call_method1("_remember_release", (release,))?;
                    }
                    identities.insert(url.clone(), identity);
                    inventories.insert(url.clone(), inventory.clone_ref(py));
                    located = Some((url, inventory));
                    break;
                }
                Err(error) => {
                    if !error.is_instance(py, &api.getattr("HrrrFieldUnavailable")?) {
                        return Err(error);
                    }
                    last_error = Some(error);
                }
            }
        }
        let Some((url, inventory)) = located else {
            return Err(last_error.unwrap_or_else(|| {
                pyo3::exceptions::PyValueError::new_err("no configured model release")
            }));
        };
        for spec in selected {
            let variable: String = spec.getattr("variable")?.extract()?;
            let adjusted = if interval_accumulation {
                if matches!(source, "sfc") && variable == "APCP" {
                    // The trailing 1-h accumulation; every other record is
                    // instantaneous (type flags, MSLET, and the HGT heights).
                    let kwargs = PyDict::new(py);
                    kwargs.set_item("forecast", format!("{}-{step} hour acc fcst", step - 1))?;
                    PyModule::import(py, "dataclasses")?
                        .getattr("replace")?
                        .call((spec,), Some(&kwargs))?
                } else {
                    spec.clone()
                }
            } else if !hourly_diff && (nam_sum || window > 0) {
                let kwargs = PyDict::new(py);
                kwargs.set_item(
                    "forecast",
                    if nam_sum {
                        format!("{}-{step} hour acc fcst", (step - 1) / 3 * 3)
                    } else if hourly_sum {
                        format!("{}-{step} hour acc fcst", step - 1)
                    } else {
                        format!("{}-{step} hour max fcst", step - 1)
                    },
                )?;
                PyModule::import(py, "dataclasses")?
                    .getattr("replace")?
                    .call((spec,), Some(&kwargs))?
            } else {
                spec.clone()
            };
            let row = api.call_method1("locate", (inventory.bind(py), adjusted))?;
            let offset: u64 = row.getattr("offset")?.extract()?;
            let mut length: u64 = row.getattr("length")?.extract()?;
            if let Some(identity) = &identities[&url] {
                let remaining = identity
                    .length
                    .checked_sub(offset)
                    .filter(|remaining| *remaining > 0)
                    .ok_or_else(|| {
                        pyo3::exceptions::PyValueError::new_err(
                            "inventory offset exceeds the GRIB object",
                        )
                    })?;
                if length == 0 {
                    length = remaining;
                }
                if length > remaining {
                    return Err(pyo3::exceptions::PyValueError::new_err(
                        "inventory length exceeds the GRIB object",
                    ));
                }
            }
            if length > 64 * 1024 * 1024 {
                return Err(super::field_error(py, super::Error::Budget));
            }
            fields.push(Field {
                name: spec.getattr("name")?.extract()?,
                url: url.clone(),
                offset,
                length,
                subfield: row.getattr("field_index")?.extract()?,
            });
        }
    }
    if fields.is_empty() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "map product has no supported fields",
        ));
    }
    Ok(Plan {
        fields,
        identities,
        sum,
        maximum: window > 0 && !sum && !hourly_diff,
        diff: hourly_diff,
    })
}

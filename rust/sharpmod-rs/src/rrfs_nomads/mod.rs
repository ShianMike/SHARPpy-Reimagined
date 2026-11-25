mod idx;
mod provenance;
#[cfg(test)]
mod tests;

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyInt};
use std::sync::Mutex;

const NOMADS_ROOT: &str = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/rrfs";
const RELEASE_PATHS: [&str; 3] = ["prod", "para", "v1.0"];

static RELEASE_CACHE: Mutex<Option<String>> = Mutex::new(None);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct RrfsDomainSpec {
    pub(crate) name: &'static str,
    pub(crate) tag: &'static str,
    pub(crate) resolution: &'static str,
    pub(crate) label: &'static str,
}

const DOMAINS: [(&str, RrfsDomainSpec); 5] = [
    (
        "conus",
        RrfsDomainSpec {
            name: "conus",
            tag: "conus",
            resolution: "3km",
            label: "3-km CONUS",
        },
    ),
    (
        "alaska",
        RrfsDomainSpec {
            name: "alaska",
            tag: "ak",
            resolution: "3km",
            label: "3-km Alaska",
        },
    ),
    (
        "hawaii",
        RrfsDomainSpec {
            name: "hawaii",
            tag: "hi",
            resolution: "2p5km",
            label: "2.5-km Hawaii",
        },
    ),
    (
        "puerto rico",
        RrfsDomainSpec {
            name: "puerto rico",
            tag: "pr",
            resolution: "2p5km",
            label: "2.5-km Puerto Rico",
        },
    ),
    (
        "north america",
        RrfsDomainSpec {
            name: "north america",
            tag: "na",
            resolution: "13km",
            label: "13-km North America",
        },
    ),
];

pub(crate) fn rrfs_unavailable(py: Python<'_>, message: impl Into<String>) -> PyErr {
    let msg = message.into();
    py.import("sharpmod.providers.rrfs_nomads")
        .and_then(|module| module.getattr("RrfsUnavailable"))
        .and_then(|cls| cls.call1((msg.as_str(),)))
        .map(PyErr::from_value)
        .unwrap_or_else(|_| PyRuntimeError::new_err(msg))
}

pub(crate) fn lookup_domain(raw: &str) -> Option<RrfsDomainSpec> {
    let normalized = raw
        .trim()
        .to_lowercase()
        .split(|c: char| c.is_whitespace() || c == '_' || c == '-')
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join(" ");
    DOMAINS
        .iter()
        .find_map(|(key, spec)| (*key == normalized).then_some(*spec))
}

fn domain_from_py(domain: &Bound<'_, PyAny>) -> PyResult<(String, String)> {
    if let (Ok(tag), Ok(resolution)) = (domain.getattr("tag"), domain.getattr("resolution")) {
        return Ok((tag.extract::<String>()?, resolution.extract::<String>()?));
    }
    let raw = if domain.is_none() {
        String::new()
    } else {
        domain.str()?.to_str()?.to_owned()
    };
    if let Some(spec) = lookup_domain(&raw) {
        return Ok((spec.tag.to_string(), spec.resolution.to_string()));
    }
    let repr = domain.repr()?.to_str()?.to_owned();
    Err(PyValueError::new_err(format!(
        "unknown RRFS domain: {repr}"
    )))
}

pub(crate) fn release_candidates_list() -> Vec<String> {
    let guard = RELEASE_CACHE
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    match &*guard {
        None => RELEASE_PATHS.iter().map(|s| (*s).to_string()).collect(),
        Some(cached) => {
            let mut out = Vec::with_capacity(RELEASE_PATHS.len() + 1);
            out.push(cached.clone());
            for item in RELEASE_PATHS {
                if item != cached {
                    out.push(item.to_string());
                }
            }
            out
        }
    }
}

pub(crate) fn remember_release_value(release: String) -> bool {
    let mut guard = RELEASE_CACHE
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if guard.as_deref() == Some(release.as_str()) {
        return false;
    }
    *guard = Some(release);
    true
}

pub(crate) fn forget_release_value() {
    let mut guard = RELEASE_CACHE
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    *guard = None;
}

#[pyfunction]
fn rrfs_domain_for(
    name: &Bound<'_, PyAny>,
) -> PyResult<(&'static str, &'static str, &'static str, &'static str)> {
    let raw = if name.is_none() {
        String::new()
    } else {
        name.str()?.to_str()?.to_owned()
    };
    if let Some(spec) = lookup_domain(&raw) {
        return Ok((spec.name, spec.tag, spec.resolution, spec.label));
    }
    let repr = name.repr()?.to_str()?.to_owned();
    Err(PyValueError::new_err(format!(
        "unknown RRFS domain: {repr}"
    )))
}

#[pyfunction]
#[pyo3(signature = (release, run_dt, fxx, product, domain, member = None))]
fn rrfs_build_url(
    release: &str,
    run_dt: &Bound<'_, PyAny>,
    fxx: &Bound<'_, PyAny>,
    product: &str,
    domain: &Bound<'_, PyAny>,
    member: Option<&Bound<'_, PyAny>>,
) -> PyResult<String> {
    let (tag, resolution) = domain_from_py(domain)?;
    let year: i32 = run_dt.getattr("year")?.extract()?;
    let month: u32 = run_dt.getattr("month")?.extract()?;
    let day: u32 = run_dt.getattr("day")?.extract()?;
    let hour: u32 = run_dt.getattr("hour")?.extract()?;

    if fxx.is_instance_of::<PyBool>() {
        return Err(PyValueError::new_err("fxx must be an integer"));
    }
    let fxx_val: i64 = if let Ok(v) = fxx.extract::<i64>() {
        v
    } else if let Ok(text) = fxx.extract::<String>() {
        text.trim()
            .parse::<i64>()
            .map_err(|_| PyValueError::new_err("fxx must be an integer"))?
    } else {
        return Err(PyValueError::new_err("fxx must be an integer"));
    };

    let mut directory = format!("rrfs.{year:04}{month:02}{day:02}/{hour:02}");
    let mut prefix = format!("rrfs.t{hour:02}z");
    let mut resolved_product = product.to_string();

    if let Some(member_obj) = member {
        if !member_obj.is_none() {
            if !member_obj.is_exact_instance_of::<PyInt>() {
                return Err(PyValueError::new_err(
                    "REFS member must be an integer from 1 to 5",
                ));
            }
            let m: i64 = member_obj.extract()?;
            if !(1..=5).contains(&m) {
                return Err(PyValueError::new_err(
                    "REFS member must be an integer from 1 to 5",
                ));
            }
            directory = format!("rrfsens.{year:04}{month:02}{day:02}/{hour:02}/m{m:03}");
            prefix.push_str(&format!(".m{m:03}"));
            resolved_product.push_str("nomads");
        }
    }

    Ok(format!(
        "{NOMADS_ROOT}/{release}/{directory}/{prefix}.{resolved_product}.{resolution}.f{fxx_val:03}.{tag}.grib2"
    ))
}

#[pyfunction]
fn rrfs_release_candidates() -> Vec<String> {
    release_candidates_list()
}

#[pyfunction]
fn rrfs_remember_release(release: &Bound<'_, PyAny>) -> PyResult<bool> {
    let text = if release.is_none() {
        return Err(PyValueError::new_err("release must not be None"));
    } else {
        release.str()?.to_str()?.to_owned()
    };
    Ok(remember_release_value(text))
}

#[pyfunction]
fn rrfs_forget_release() {
    forget_release_value();
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(rrfs_domain_for, module)?)?;
    module.add_function(wrap_pyfunction!(rrfs_build_url, module)?)?;
    module.add_function(wrap_pyfunction!(rrfs_release_candidates, module)?)?;
    module.add_function(wrap_pyfunction!(rrfs_remember_release, module)?)?;
    module.add_function(wrap_pyfunction!(rrfs_forget_release, module)?)?;
    module.add_function(wrap_pyfunction!(idx::rrfs_parse_idx, module)?)?;
    module.add_function(wrap_pyfunction!(idx::rrfs_select_records, module)?)?;
    module.add_function(wrap_pyfunction!(idx::rrfs_pressure_field_plan, module)?)?;
    module.add_function(wrap_pyfunction!(provenance::rrfs_write_provenance, module)?)?;
    module.add_function(wrap_pyfunction!(provenance::rrfs_read_provenance, module)?)?;
    Ok(())
}

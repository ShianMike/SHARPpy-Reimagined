//! Shared immutable map ingredients and a bounded download/decode pipeline.
use crate::{
    model_field_cache::{Cache, Ticket, WaitError},
    model_http_bindings::{cancelled, field_error},
};
use numpy::{ndarray::Array2, IntoPyArray, PyArray2, PyArrayMethods, PyUntypedArrayMethods};
use pyo3::{
    prelude::*,
    types::{PyBytes, PyDict},
};
use sharpmod_weather::{
    map_fields::{self, FieldJob},
    model_http::{Error, ModelClient, ObjectIdentity},
};
use std::{
    collections::HashMap,
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc, OnceLock,
    },
};

mod acquire;
#[cfg(test)]
mod benchmark;
#[cfg(test)]
mod fixtures;
pub(crate) mod grefs;
mod inventory;
mod plan;
pub(crate) mod refs;
pub(crate) mod refs_zarr;
#[cfg(test)]
mod tests;

#[derive(Clone, Hash, PartialEq, Eq)]
struct Key {
    url: String,
    identity: ObjectIdentity,
    offset: u64,
    length: u64,
    subfield: usize,
    model: String,
}
struct Grid {
    shape: (usize, usize),
    values: Vec<f32>,
}
type FieldTicket = Ticket<Key, Grid>;

fn cache() -> &'static Arc<Cache<Key, Grid>> {
    static CACHE: OnceLock<Arc<Cache<Key, Grid>>> = OnceLock::new();
    CACHE.get_or_init(|| Cache::new(128 * 1024 * 1024, 256))
}
static DECODED: AtomicUsize = AtomicUsize::new(0);
static SHARED: AtomicUsize = AtomicUsize::new(0);
#[cfg(test)]
static DECODING: AtomicUsize = AtomicUsize::new(0);

struct Job {
    request: FieldJob,
    identity: Option<ObjectIdentity>,
    members: Vec<usize>,
}

#[pyfunction]
fn clear_model_field_cache() {
    cache().clear();
    inventory::clear();
    crate::model_companion::clear_cache();
}

#[pyfunction]
fn model_field_cache_info(py: Python<'_>) -> PyResult<Bound<'_, PyDict>> {
    let result = PyDict::new(py);
    result.set_item("decoded", DECODED.load(Ordering::Relaxed))?;
    result.set_item("shared", SHARED.load(Ordering::Relaxed))?;
    Ok(result)
}

#[pyfunction]
fn download_map_fields(
    py: Python<'_>,
    product: &Bound<'_, PyAny>,
    run: &Bound<'_, PyAny>,
    hour: u32,
    timeout: f64,
    should_cancel: Option<Py<PyAny>>,
    model: String,
) -> PyResult<Py<PyDict>> {
    let plan::Plan {
        fields,
        identities,
        sum,
        maximum,
        diff,
    } = plan::fields(
        py,
        product,
        run,
        hour,
        timeout,
        &model,
        should_cancel.as_ref(),
    )?;
    let mut arrays: HashMap<String, Grid> = HashMap::new();
    // Long accumulated/max products must not pin every hour's full grid.
    // Eight fields keep decode working ahead while bounding temporary ownership.
    for batch in fields.chunks(8) {
        let output = acquire::load(
            py,
            batch,
            &identities,
            timeout,
            should_cancel.as_ref(),
            &model,
        )?;
        for (field, grid) in batch.iter().zip(output) {
            if let Some(previous) = arrays
                .get_mut(&field.name)
                .filter(|_| sum || maximum || diff)
            {
                if previous.shape != grid.shape {
                    return Err(field_error(
                        py,
                        Error::Invalid("accumulated field grids disagree".into()),
                    ));
                }
                for (a, b) in previous.values.iter_mut().zip(&grid.values) {
                    *a = if diff {
                        if a.is_nan() || b.is_nan() {
                            f32::NAN
                        } else {
                            (*b - *a).max(0.0)
                        }
                    } else if sum {
                        *a + *b
                    } else if a.is_nan() || b.is_nan() {
                        f32::NAN
                    } else {
                        a.max(*b)
                    };
                }
            } else {
                arrays.insert(
                    field.name.clone(),
                    Grid {
                        shape: grid.shape,
                        values: grid.values.clone(),
                    },
                );
            }
        }
    }
    let result = PyDict::new(py);
    for (name, grid) in arrays {
        result.set_item(
            name,
            Array2::from_shape_vec(grid.shape, grid.values)
                .map_err(|error| field_error(py, Error::Invalid(error.to_string())))?
                .into_pyarray(py),
        )?;
    }
    Ok(result.unbind())
}

pub fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    grefs::register(module)?;
    refs::register(module)?;
    refs_zarr::register(module)?;
    module.add_function(wrap_pyfunction!(download_map_fields, module)?)?;
    module.add_function(wrap_pyfunction!(clear_model_field_cache, module)?)?;
    module.add_function(wrap_pyfunction!(model_field_cache_info, module)?)?;
    Ok(())
}

//! Attach spatial background profiles only to the renderer's private collection.
use super::*;
use numpy::IntoPyArray;
use pyo3::types::PyList;

#[pyfunction]
pub(super) fn attach_box_profiles(
    py: Python<'_>,
    collection: &Bound<'_, PyAny>,
    path: &str,
) -> PyResult<usize> {
    let traces = trace_artifact::load(py, path)?;
    if traces.is_empty() {
        return Ok(0);
    }
    let create = PyModule::import(py, "sharppy.sharptab.profile")?.getattr("create_profile")?;
    let date = collection.call_method0("getCurrentDate")?;
    // Build everything before mutation. A malformed trace cannot partially attach.
    let profiles = PyDict::new(py);
    for (index, trace) in traces.iter().enumerate() {
        let kwargs = PyDict::new(py);
        for (field, name) in ["pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "omeg"]
            .iter()
            .enumerate()
        {
            kwargs.set_item(
                name,
                trace.matrix[field * trace.levels..(field + 1) * trace.levels]
                    .to_vec()
                    .into_pyarray(py),
            )?;
        }
        kwargs.set_item("date", &date)?;
        kwargs.set_item("latitude", trace.latitude)?;
        kwargs.set_item("longitude", trace.longitude)?;
        kwargs.set_item("missing", -9999.0)?;
        kwargs.set_item("strictQC", false)?;
        let profile = create.call((), Some(&kwargs))?;
        profile.setattr("_sharpmod_box_column", true)?;
        profiles.set_item(
            format!("Grid {:04}", index + 1),
            PyList::new(py, [profile])?,
        )?;
    }
    collection
        .getattr("_profs")?
        .call_method1("update", (profiles,))?;
    collection.call_method1("setMeta", ("native_box_traces", true))?;
    trace_plot::install(py)?;
    // The highlighted key and its diagnostic profile are deliberately untouched.
    Ok(traces.len())
}

pub(super) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(attach_box_profiles, module)?)
}

//! Keep one set of mean annotations while reusing SHARPpy's ensemble trace paths.
use super::*;
use pyo3::types::{PyCFunction, PyTuple};

pub(super) fn install(py: Python<'_>) -> PyResult<()> {
    let widget = PyModule::import(py, "sharppy.viz.skew")?.getattr("plotSkewT")?;
    if widget.hasattr("_sharpmod_native_box_traces")? {
        return Ok(());
    }
    let partialmethod = PyModule::import(py, "functools")?.getattr("partialmethod")?;
    let original = widget.getattr("drawTrace")?.unbind();
    let trace = PyCFunction::new_closure(
        py,
        Some(c"draw_box_trace"),
        None,
        move |args: &Bound<'_, PyTuple>,
              kwargs: Option<&Bound<'_, PyDict>>|
              -> PyResult<Py<PyAny>> {
            let py = args.py();
            let options = kwargs
                .map(PyDictMethods::copy)
                .transpose()?
                .unwrap_or_else(|| PyDict::new(py));
            let thin = options
                .get_item("width")?
                .is_some_and(|width| width.extract::<usize>().ok() == Some(1));
            if thin {
                let canvas = args.get_item(0)?;
                let collection = canvas
                    .getattr("prof_collections")?
                    .get_item(canvas.getattr("pc_idx")?)?;
                if collection
                    .getattr("_meta")?
                    .cast::<PyDict>()?
                    .get_item("native_box_traces")?
                    .is_some()
                {
                    options.set_item("label", false)?;
                }
            }
            Ok(original.bind(py).call(args, Some(&options))?.unbind())
        },
    )?;
    widget.setattr("drawTrace", partialmethod.call1((trace,))?)?;
    let original = widget.getattr("drawBarbs")?.unbind();
    let barbs = PyCFunction::new_closure(
        py,
        Some(c"draw_box_barbs"),
        None,
        move |args: &Bound<'_, PyTuple>,
              kwargs: Option<&Bound<'_, PyDict>>|
              -> PyResult<Py<PyAny>> {
            let py = args.py();
            if args.get_item(1)?.hasattr("_sharpmod_box_column")? {
                return Ok(py.None());
            }
            Ok(original.bind(py).call(args, kwargs)?.unbind())
        },
    )?;
    widget.setattr("drawBarbs", partialmethod.call1((barbs,))?)?;
    widget.setattr("_sharpmod_native_box_traces", true)?;
    Ok(())
}

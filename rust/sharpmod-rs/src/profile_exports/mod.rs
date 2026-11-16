//! Stable ensemble CSVs: adapt existing records once, then write owned Rust rows.

mod ensemble;
mod storage;

use pyo3::{
    prelude::*,
    types::{PyDict, PyList},
};

#[cfg(test)]
use storage::Table;

const ROW_BATCH: usize = 64;

struct Output<'py> {
    py: Python<'py>,
    destination: Bound<'py, PyAny>,
    export: storage::Export,
    rows: Vec<Vec<String>>,
}

impl Output<'_> {
    fn flush(&mut self) -> PyResult<()> {
        let rows = std::mem::take(&mut self.rows);
        let export = &mut self.export;
        self.py.detach(move || export.append(rows))?;
        Ok(())
    }

    fn finish(mut self) -> PyResult<Py<PyAny>> {
        self.flush()?;
        self.py.detach(move || self.export.finish())?;
        Ok(self.destination.unbind())
    }
}

struct Boundary<'py> {
    py: Python<'py>,
    api: Bound<'py, PyModule>,
    datetime: Bound<'py, PyAny>,
}

impl<'py> Boundary<'py> {
    fn new(py: Python<'py>) -> PyResult<Self> {
        Ok(Self {
            py,
            api: PyModule::import(py, "sharpmod.analysis.profile_metrics")?,
            datetime: PyModule::import(py, "datetime")?.getattr("datetime")?,
        })
    }

    fn time(&self, value: &Bound<'py, PyAny>) -> PyResult<String> {
        if value.is_instance(&self.datetime)? {
            value.call_method0("isoformat")?.extract()
        } else {
            Ok(String::new())
        }
    }

    fn output(
        &self,
        path: &Bound<'py, PyAny>,
        fields: &[Bound<'py, PyAny>],
        create_parents: bool,
    ) -> PyResult<Output<'py>> {
        let destination = PyModule::import(self.py, "pathlib")?
            .getattr("Path")?
            .call1((path,))?;
        let owned_path = destination.extract::<std::path::PathBuf>()?;
        let headers = fields.iter().map(text).collect::<PyResult<Vec<_>>>()?;
        let export = self
            .py
            .detach(move || storage::Export::new(owned_path, &headers, create_parents))?;
        Ok(Output {
            py: self.py,
            destination,
            export,
            rows: Vec::with_capacity(ROW_BATCH),
        })
    }
}

fn text(value: &Bound<'_, PyAny>) -> PyResult<String> {
    if value.is_none() {
        Ok(String::new())
    } else {
        value.str()?.extract()
    }
}

fn truthy(value: &Bound<'_, PyAny>) -> PyResult<String> {
    if value.is_truthy()? {
        text(value)
    } else {
        Ok(String::new())
    }
}

fn collect<'py>(value: &Bound<'py, PyAny>) -> PyResult<Vec<Bound<'py, PyAny>>> {
    value.try_iter()?.collect()
}

fn append(
    output: &mut Output<'_>,
    fields: &[Bound<'_, PyAny>],
    row: &Bound<'_, PyDict>,
) -> PyResult<()> {
    output.rows.push(
        fields
            .iter()
            .map(|key| {
                text(
                    &row.get_item(key)?
                        .unwrap_or_else(|| row.py().None().into_bound(row.py())),
                )
            })
            .collect::<PyResult<_>>()?,
    );
    if output.rows.len() == ROW_BATCH {
        output.flush()?;
    }
    Ok(())
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    ensemble::register(module)
}

#[cfg(test)]
mod tests;

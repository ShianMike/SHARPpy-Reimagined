use pyo3::{prelude::*, types::PyDict};
use std::path::PathBuf;

struct Staging {
    path: PathBuf,
    retain: bool,
}
impl Drop for Staging {
    fn drop(&mut self) {
        if !self.retain {
            let _ = std::fs::remove_dir_all(&self.path);
        }
    }
}

#[pyfunction]
#[pyo3(signature=(output, arrays, metadata, cancelled=None))]
fn write_portable_pair(
    py: Python<'_>,
    output: PathBuf,
    arrays: &Bound<'_, PyDict>,
    metadata: &Bound<'_, PyDict>,
    cancelled: Option<&Bound<'_, PyAny>>,
) -> PyResult<()> {
    let api = PyModule::import(py, "sharpmod.tools.era5_extract")?;
    let os = PyModule::import(py, "os")?;
    let output: PathBuf = os
        .getattr("path")?
        .call_method1("abspath", (&output,))?
        .extract()?;
    let sidecar = output.with_extension("json");
    let parent = output.parent().unwrap();
    std::fs::create_dir_all(parent)?;
    let options = PyDict::new(py);
    options.set_item("dir", parent)?;
    options.set_item("prefix", ".sharpmod-output-")?;
    let staging = PyModule::import(py, "tempfile")?
        .call_method("mkdtemp", (), Some(&options))?
        .extract::<PathBuf>()?;
    let mut staging = Staging {
        path: staging,
        retain: false,
    };
    let npz = staging.path.join("new.npz");
    let json = staging.path.join("new.json");
    let generation = PyModule::import(py, "uuid")?
        .call_method0("uuid4")?
        .getattr("hex")?;
    let marked_arrays = arrays.copy()?;
    marked_arrays.set_item("pair_generation", &generation)?;
    let marked_metadata = metadata.copy()?;
    marked_metadata.set_item("pair_generation", generation)?;
    // Keep the existing serializer/fault-injection seams; transaction ownership,
    // backup, cancellation and rollback decisions live here.
    api.call_method1("_atomic_write_npz", (&npz, marked_arrays))?;
    api.call_method1("_atomic_write_json", (&json, marked_metadata))?;
    api.call_method1("validate_portable_sounding_pair", (&npz,))?;
    let targets = [output, sidecar];
    let backups = [
        staging.path.join("previous.npz"),
        staging.path.join("previous.json"),
    ];
    let mut existed = [false; 2];
    for i in 0..2 {
        if targets[i].exists() {
            PyModule::import(py, "shutil")?.call_method1("copy2", (&targets[i], &backups[i]))?;
            existed[i] = true;
        }
    }
    let mut published = 0;
    let result = (|| {
        for (source, target) in [&npz, &json].into_iter().zip(&targets) {
            api.call_method1("_check_cancelled", (cancelled,))?;
            os.call_method1("replace", (source, target))?;
            published += 1;
        }
        api.call_method1("_check_cancelled", (cancelled,))?;
        Ok(())
    })();
    if let Err(failure) = result {
        let mut errors = Vec::new();
        for i in (0..published).rev() {
            let rolled_back = if existed[i] {
                os.call_method1("replace", (&backups[i], &targets[i]))
            } else {
                os.call_method1("remove", (&targets[i],))
            };
            if let Err(error) = rolled_back {
                errors.push(error.to_string());
            }
        }
        if !errors.is_empty() {
            staging.retain = true;
            let error = pyo3::exceptions::PyOSError::new_err(format!(
                "Could not restore the sounding output; recovery files retained in {}: {}",
                staging.path.display(),
                errors.join(", ")
            ));
            error.set_cause(py, Some(failure));
            return Err(error);
        }
        return Err(failure);
    }
    Ok(())
}
pub(super) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(write_portable_pair, module)?)
}

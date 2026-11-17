//! Bounded opened-profile registry; a request keeps its source files alive.
use pyo3::{
    exceptions::{PyKeyError, PyValueError},
    prelude::*,
};
use std::{
    collections::{HashSet, VecDeque},
    fs, io,
    path::{Path, PathBuf},
    sync::{Mutex, MutexGuard},
};

fn ordinary(path: &Path) -> io::Result<bool> {
    let metadata = fs::symlink_metadata(path)?;
    #[cfg(windows)]
    let link = {
        use std::os::windows::fs::MetadataExt;
        metadata.file_attributes() & 0x400 != 0
    };
    #[cfg(not(windows))]
    let link = metadata.file_type().is_symlink();
    if link {
        return Err(io::Error::other(
            "sounding cleanup refuses links and junctions",
        ));
    }
    if !metadata.is_file() && !metadata.is_dir() {
        return Err(io::Error::other("sounding cleanup refuses special files"));
    }
    Ok(metadata.is_dir())
}

fn remove(path: &Path) -> io::Result<()> {
    if ordinary(path)? {
        // Validate the whole disposable tree before changing any descendant.
        let mut pending = vec![path.to_owned()];
        while let Some(directory) = pending.pop() {
            for child in fs::read_dir(directory)? {
                let child = child?.path();
                if ordinary(&child)? {
                    pending.push(child);
                }
            }
        }
        fs::remove_dir_all(path)
    } else {
        fs::remove_file(path)
    }
}

fn valid_id(id: &str) -> PyResult<()> {
    if id.is_empty()
        || id.len() > 64
        || !id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
    {
        return Err(PyValueError::new_err("invalid cache-owned sounding ID"));
    }
    Ok(())
}

fn cleanup(root: &Path, renders: &Path, id: &str) -> io::Result<()> {
    for (directory, rendering) in [(root, false), (renders, true)] {
        if !ordinary(directory)? {
            return Err(io::Error::other("sounding cache root is not a directory"));
        }
        let paths = fs::read_dir(directory)?
            .filter_map(|item| {
                let item = match item {
                    Ok(item) => item,
                    Err(error) => return Some(Err(error)),
                };
                let name = item.file_name();
                let name = name.to_string_lossy();
                let suffix = name.strip_prefix(id)?;
                let owned = if rendering {
                    suffix.starts_with('-')
                } else {
                    suffix.is_empty() || suffix.starts_with('.') || suffix.starts_with('-')
                };
                owned.then(|| Ok(item.path()))
            })
            .collect::<io::Result<Vec<_>>>()?;
        for path in paths {
            match remove(&path) {
                Err(error) if error.kind() == io::ErrorKind::NotFound => {}
                other => other?,
            }
        }
    }
    Ok(())
}

#[pyclass(module = "sharpmod_rs")]
struct SoundingFiles {
    root: PathBuf,
    renders: PathBuf,
    id: String,
}

impl Drop for SoundingFiles {
    fn drop(&mut self) {
        if let Err(error) = cleanup(&self.root, &self.renders, &self.id) {
            if error.kind() != io::ErrorKind::NotFound {
                eprintln!("sounding file cleanup failed for {}: {error}", self.id);
            }
        }
    }
}

#[derive(Default)]
struct Entries {
    profiles: VecDeque<(String, Py<PyAny>)>,
    retained: HashSet<String>,
}

#[pyclass(module = "sharpmod_rs")]
struct SoundingEntries {
    root: PathBuf,
    renders: PathBuf,
    maximum: usize,
    entries: Mutex<Entries>,
}

impl SoundingEntries {
    fn entries(&self) -> MutexGuard<'_, Entries> {
        self.entries
            .lock()
            .unwrap_or_else(|error| error.into_inner())
    }
}

#[pymethods]
impl SoundingEntries {
    #[new]
    fn new(root: PathBuf, renders: PathBuf, maximum: usize) -> PyResult<Self> {
        if maximum == 0 {
            return Err(PyValueError::new_err(
                "sounding cache capacity must be positive",
            ));
        }
        Ok(Self {
            root: fs::canonicalize(root)?,
            renders: fs::canonicalize(renders)?,
            maximum,
            entries: Mutex::new(Entries::default()),
        })
    }

    fn add(&self, py: Python<'_>, entry: &Bound<'_, PyAny>) -> PyResult<()> {
        let id: String = entry.getattr("sounding_id")?.extract()?;
        valid_id(&id)?;
        {
            let mut entries = self.entries();
            if let Some(position) = entries.profiles.iter().position(|(key, _)| key == &id) {
                if !entries.profiles[position].1.bind(py).is(entry) {
                    return Err(PyValueError::new_err(
                        "a different sounding already owns this ID",
                    ));
                }
                let same = entries.profiles.remove(position).unwrap();
                entries.profiles.push_back(same);
                return Ok(());
            }
            if entries.profiles.len() >= self.maximum
                && entries
                    .profiles
                    .iter()
                    .all(|(id, _)| entries.retained.contains(id))
            {
                return Err(PyValueError::new_err(
                    "sounding cache is full of retained profiles",
                ));
            }
        }
        match entry.getattr("_cache_files") {
            Ok(owner) => {
                let owner = owner.extract::<PyRef<'_, SoundingFiles>>()?;
                if owner.id != id || owner.root != self.root || owner.renders != self.renders {
                    return Err(PyValueError::new_err(
                        "sounding already belongs to another cache",
                    ));
                }
            }
            Err(error) if error.is_instance_of::<pyo3::exceptions::PyAttributeError>(py) => {
                entry.setattr(
                    "_cache_files",
                    Py::new(
                        py,
                        SoundingFiles {
                            root: self.root.clone(),
                            renders: self.renders.clone(),
                            id: id.clone(),
                        },
                    )?,
                )?;
            }
            Err(error) => return Err(error),
        }
        let evicted = {
            let mut entries = self.entries();
            entries.profiles.push_back((id, entry.clone().unbind()));
            if entries.profiles.len() > self.maximum {
                let position = entries
                    .profiles
                    .iter()
                    .position(|(id, _)| !entries.retained.contains(id))
                    .unwrap();
                entries.profiles.remove(position)
            } else {
                None
            }
        };
        // A renderer/snapshot request holds the Entry itself. Only releasing
        // the final owner drops SoundingFiles and removes this profile's data.
        drop(evicted);
        Ok(())
    }

    fn get(&self, py: Python<'_>, id: &str) -> PyResult<Py<PyAny>> {
        let mut entries = self.entries();
        let position = entries
            .profiles
            .iter()
            .position(|(key, _)| key == id)
            .ok_or_else(|| PyKeyError::new_err(id.to_owned()))?;
        let entry = entries.profiles.remove(position).unwrap();
        let result = entry.1.clone_ref(py);
        entries.profiles.push_back(entry);
        Ok(result)
    }

    fn ids(&self) -> Vec<String> {
        self.entries()
            .profiles
            .iter()
            .map(|(key, _)| key.clone())
            .collect()
    }

    fn retain(&self, ids: Vec<String>) -> PyResult<()> {
        if ids.len() > self.maximum.min(24) {
            return Err(PyValueError::new_err("too many retained sounding IDs"));
        }
        for id in &ids {
            valid_id(id)?;
        }
        let mut entries = self.entries();
        entries.retained = ids
            .into_iter()
            .filter(|id| entries.profiles.iter().any(|(key, _)| key == id))
            .collect();
        Ok(())
    }

    fn remove_files(&self, py: Python<'_>, id: &str) -> PyResult<()> {
        valid_id(id)?;
        py.detach(|| cleanup(&self.root, &self.renders, id))?;
        Ok(())
    }
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<SoundingEntries>()?;
    module.add_function(wrap_pyfunction!(retain_soundings, module)?)
}

#[pyfunction]
fn retain_soundings<'py>(
    py: Python<'py>,
    params: &Bound<'py, pyo3::types::PyDict>,
) -> PyResult<Bound<'py, pyo3::types::PyDict>> {
    let validators = PyModule::import(py, "sharpmod.bridge.params")?;
    validators.call_method1("keys", (params, ("sounding_ids",)))?;
    let cache = PyModule::import(py, "sharpmod.bridge.sounding_cache")?.call_method0("cache")?;
    cache
        .getattr("_entries")?
        .call_method1("retain", (params.get_item("sounding_ids")?.unwrap(),))
        .map_err(|error| {
            if error.is_instance_of::<PyValueError>(py)
                || error.is_instance_of::<pyo3::exceptions::PyTypeError>(py)
            {
                validators
                    .call_method1(
                        "invalid",
                        ("sounding_ids must contain up to 24 valid sounding IDs",),
                    )
                    .map(PyErr::from_value)
                    .unwrap_or(error)
            } else {
                error
            }
        })?;
    let result = pyo3::types::PyDict::new(py);
    result.set_item("retained", true)?;
    Ok(result)
}

#[cfg(test)]
mod tests;

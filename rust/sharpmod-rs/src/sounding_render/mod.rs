//! Sounding views and PNG cache transactions through the existing Qt worker.
mod files;
use files::{Image, ReadError, Staged};
use pyo3::{
    prelude::*,
    types::{PyDict, PyList, PyTuple},
};
use std::{
    collections::HashSet,
    fs, io,
    path::{Path, PathBuf},
};

const MAX_IMAGE_BYTES: usize = 6 * 1024 * 1024 - 4096;

fn failure(py: Python<'_>, message: impl ToString) -> PyErr {
    let message = message.to_string();
    match PyModule::import(py, "sharpmod.bridge.errors")
        .and_then(|module| module.getattr("BridgeError"))
        .and_then(|class| class.call1(("RENDER_FAILED", &message, false)))
    {
        Ok(error) => PyErr::from_value(error),
        Err(_) => pyo3::exceptions::PyValueError::new_err(message),
    }
}

fn read_error(py: Python<'_>, error: ReadError) -> PyErr {
    match error {
        ReadError::TooLarge => failure(py, "The sounding image is too large to send."),
        ReadError::Invalid(error) => failure(py, error),
        ReadError::Io(error) => {
            failure(py, format!("The sounding image could not be read: {error}"))
        }
    }
}

fn remove_file(py: Python<'_>, path: &Path) -> PyResult<()> {
    py.detach(|| match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    })
    .map_err(|error| {
        failure(
            py,
            format!("The cached sounding image could not be removed: {error}"),
        )
    })
}

fn cached_image(py: Python<'_>, path: &Path, limit: usize) -> PyResult<Option<Image>> {
    match py.detach(|| Image::read(path, limit)) {
        Ok(image) => Ok(Some(image)),
        Err(ReadError::Io(error)) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(ReadError::Invalid(_) | ReadError::TooLarge) => {
            remove_file(py, path)?;
            Ok(None)
        }
        Err(error) => Err(read_error(py, error)),
    }
}

struct Request<'py> {
    cache: Bound<'py, PyAny>,
    entry: Bound<'py, PyAny>,
    source: PathBuf,
    root: PathBuf,
    parcel: String,
    mode: String,
    style: String,
}

impl<'py> Request<'py> {
    fn new(py: Python<'py>, params: &Bound<'py, PyDict>, interactive: bool) -> PyResult<Self> {
        let validators = PyModule::import(py, "sharpmod.bridge.params")?;
        let required: &[&str] = if interactive {
            &["sounding_id", "view"]
        } else {
            &["sounding_id"]
        };
        validators.call_method1(
            "keys",
            (
                params,
                PyTuple::new(py, required)?,
                ("parcel", "image_mode", "color_style"),
            ),
        )?;
        let cache =
            PyModule::import(py, "sharpmod.bridge.sounding_cache")?.call_method0("cache")?;
        let entry = cache.call_method1("get", (params.get_item("sounding_id")?.unwrap(),))?;
        let api = PyModule::import(py, "sharpmod.bridge.soundings")?;
        let parcel = validators
            .call_method1(
                "choice",
                (params, "parcel", api.getattr("RENDER_PARCELS")?, "MU"),
            )?
            .extract()?;
        let mode = validators
            .call_method1(
                "choice",
                (
                    params,
                    "image_mode",
                    api.getattr("IMAGE_MODES")?,
                    if interactive { "uhd" } else { "hd" },
                ),
            )?
            .extract()?;
        let style = validators
            .call_method1(
                "choice",
                (
                    params,
                    "color_style",
                    api.getattr("COLOR_STYLES")?,
                    "standard",
                ),
            )?
            .extract()?;
        Ok(Self {
            source: entry.getattr("path")?.extract()?,
            root: cache.getattr("renders")?.extract()?,
            cache,
            entry,
            parcel,
            mode,
            style,
        })
    }

    fn render_in_worker(
        &self,
        py: Python<'py>,
        staged: &Staged,
        view: Option<&Bound<'py, PyDict>>,
        mode: &str,
    ) -> PyResult<Bound<'py, PyAny>> {
        let kwargs = PyDict::new(py);
        kwargs.set_item("color_style", &self.style)?;
        if let Some(view) = view {
            kwargs.set_item("view", view)?;
        }
        let client =
            PyModule::import(py, "sharpmod.bridge.render_client")?.call_method0("client")?;
        client.call_method(
            "render",
            (&self.source, &staged.image, &self.parcel, mode),
            Some(&kwargs),
        )
    }

    fn render(
        &self,
        py: Python<'py>,
        staged: &Staged,
        view: Option<&Bound<'py, PyDict>>,
        limit: usize,
    ) -> PyResult<(Image, Bound<'py, PyAny>)> {
        let metadata = self.render_in_worker(py, staged, view, &self.mode)?;
        Ok((
            py.detach(|| Image::read(&staged.image, limit))
                .map_err(|error| read_error(py, error))?,
            metadata,
        ))
    }

    fn output(&self, parcel: &str) -> PyResult<PathBuf> {
        self.cache
            .call_method1(
                "render_path",
                (self.entry.getattr("sounding_id")?, parcel, &self.mode),
            )?
            .extract()
    }
}

fn locked<'py, T>(
    entry: &Bound<'py, PyAny>,
    operation: impl FnOnce() -> PyResult<T>,
) -> PyResult<T> {
    let py = entry.py();
    let lock = entry.getattr("render_lock")?;
    lock.call_method0("__enter__")?;
    let result = operation();
    let released = lock.call_method1("__exit__", (py.None(), py.None(), py.None()));
    result.and_then(|value| {
        released?;
        Ok(value)
    })
}

fn publish(py: Python<'_>, staged: &Staged, output: &Path) -> PyResult<()> {
    py.detach(|| fs::rename(&staged.image, output))
        .map_err(|error| {
            failure(
                py,
                format!("The sounding image could not be cached: {error}"),
            )
        })
}

#[pyfunction]
#[pyo3(signature = (params, max_bytes=MAX_IMAGE_BYTES))]
fn render_sounding_png<'py>(
    py: Python<'py>,
    params: &Bound<'py, PyDict>,
    max_bytes: usize,
) -> PyResult<Bound<'py, PyDict>> {
    let request = Request::new(py, params, false)?;
    let limit = max_bytes.min(MAX_IMAGE_BYTES);
    let output = request.output(&format!("{}-{}", request.parcel, request.style))?;
    let image = locked(&request.entry, || {
        if let Some(image) = cached_image(py, &output, limit)? {
            return Ok(image);
        }
        let staged = py.detach(|| Staged::new(&request.root))?;
        let (image, _) = request.render(py, &staged, None, limit)?;
        publish(py, &staged, &output)?;
        Ok(image)
    })?;
    let (encoded, width, height, length) = py.detach(|| image.encoded());
    let result = PyDict::new(py);
    result.set_item("sounding_id", request.entry.getattr("sounding_id")?)?;
    result.set_item("parcel", request.parcel)?;
    result.set_item("image_mode", request.mode)?;
    result.set_item("width", width)?;
    result.set_item("height", height)?;
    result.set_item("byte_length", length)?;
    result.set_item("png_base64", encoded)?;
    Ok(result)
}

fn image_keys<'py>(renders: &Bound<'py, PyAny>, path: &Path) -> PyResult<Vec<Bound<'py, PyAny>>> {
    let mut keys = Vec::new();
    for item in renders.call_method0("items")?.try_iter()? {
        let item = item?;
        if item.get_item(1)?.get_item(0)?.extract::<PathBuf>()? == path {
            keys.push(item.get_item(0)?);
        }
    }
    Ok(keys)
}

fn prune(renders: &Bound<'_, PyAny>, max_images: usize) -> PyResult<()> {
    let py = renders.py();
    let mut kept = HashSet::new();
    let mut evicted = HashSet::new();
    let mut expired = Vec::new();
    let mut aliases_left = max_images - 1;
    // The newest key for each file is its resolved view. Extra request keys
    // have their own budget so they can never displace a resolved view.
    let items = renders
        .call_method0("items")?
        .call_method0("__reversed__")?;
    for item in items.try_iter()? {
        let item = item?;
        let path: PathBuf = item.get_item(1)?.get_item(0)?.extract()?;
        if kept.contains(&path) {
            if aliases_left > 0 {
                aliases_left -= 1;
                continue;
            }
        } else if kept.len() < max_images {
            kept.insert(path);
            continue;
        } else {
            evicted.insert(path);
        }
        expired.push(item.get_item(0)?);
    }
    for key in expired {
        renders.del_item(key)?;
    }
    for path in evicted {
        if let Err(error) = remove_file(py, &path) {
            PyModule::import(py, "logging")?
                .call_method1("getLogger", ("sharpmod.bridge",))?
                .call_method1(
                    "warning",
                    (
                        "could not remove cached sounding image %s",
                        error.to_string(),
                    ),
                )?;
        }
    }
    Ok(())
}

fn maximum_visible_wind(metadata: &Bound<'_, PyAny>) -> PyResult<f64> {
    let levels = metadata
        .get_item("inspection")?
        .get_item("levels")?
        .cast_into::<PyList>()?;
    let mut maximum: f64 = 0.0;
    for level in levels {
        if level.get_item("hodograph")?.is_none() {
            continue;
        }
        if let Some(speed) = level.get_item("windSpeed")?.extract::<Option<f64>>()? {
            maximum = maximum.max(speed);
        }
    }
    Ok(maximum)
}

#[pyfunction]
#[pyo3(signature = (params, max_bytes=MAX_IMAGE_BYTES, max_cached=4))]
fn render_sounding_view<'py>(
    py: Python<'py>,
    params: &Bound<'py, PyDict>,
    max_bytes: usize,
    max_cached: usize,
) -> PyResult<Bound<'py, PyDict>> {
    let request = Request::new(py, params, true)?;
    let view =
        crate::sounding_view::validate_sounding_view(py, &params.get_item("view")?.unwrap())?;
    let time: usize = view.get_item("timeIndex")?.unwrap().extract()?;
    let count: usize = request
        .entry
        .getattr("summary")?
        .get_item("time_count")?
        .extract()?;
    if time >= count.max(1) {
        return Err(PyErr::from_value(
            PyModule::import(py, "sharpmod.bridge.params")?
                .call_method1("invalid", ("timeIndex is outside this sounding's times",))?,
        ));
    }
    if view.contains("measureWind")? {
        return locked(&request.entry, || {
            // Use the same fresh configuration directory as an HD image render.
            let staged = py.detach(|| Staged::new(&request.root))?;
            let metadata = request.render_in_worker(py, &staged, Some(&view), "hd")?;
            let result = PyDict::new(py);
            result.set_item("maximumWindKt", maximum_visible_wind(&metadata)?)?;
            Ok(result)
        });
    }
    let limit = max_bytes.min(MAX_IMAGE_BYTES);
    let api = PyModule::import(py, "sharpmod.bridge.sounding_view")?;
    let key: String = api
        .call_method1(
            "_render_key",
            (&request.parcel, &request.mode, &view, &request.style),
        )?
        .extract()?;
    let (image, metadata) = locked(&request.entry, || {
        let renders = request.entry.getattr("view_renders")?;
        let cached = renders.call_method1("get", (&key,))?;
        if !cached.is_none() {
            let output: PathBuf = cached.get_item(0)?.extract()?;
            if let Some(image) = cached_image(py, &output, limit)? {
                let keys = image_keys(&renders, &output)?;
                let resolved = keys.last().expect("cache hit is indexed");
                renders.call_method1("move_to_end", (&key,))?;
                if !resolved.eq(&key)? {
                    renders.call_method1("move_to_end", (resolved,))?;
                }
                return Ok((image, cached.get_item(1)?));
            }
            for key in image_keys(&renders, &output)? {
                renders.del_item(key)?;
            }
        }
        let staged = py.detach(|| Staged::new(&request.root))?;
        let (image, metadata) = request.render(py, &staged, Some(&view), limit)?;
        let resolved = view.copy()?;
        resolved.set_item("edits", metadata.get_item("edits")?)?;
        if resolved.contains("drag")? {
            resolved.del_item("drag")?;
        }
        let resolved_key: String = api
            .call_method1(
                "_render_key",
                (&request.parcel, &request.mode, &resolved, &request.style),
            )?
            .extract()?;
        let output = request.output(&format!("{}-{resolved_key}", request.parcel))?;
        publish(py, &staged, &output)?;
        if key != resolved_key {
            renders.set_item(&key, (&output, &metadata))?;
            renders.call_method1("move_to_end", (&key,))?;
        }
        renders.set_item(&resolved_key, (&output, &metadata))?;
        renders.call_method1("move_to_end", (&resolved_key,))?;
        prune(&renders, max_cached.max(1))?;
        Ok((image, metadata))
    })?;
    let (encoded, width, height, _) = py.detach(|| image.encoded());
    let result = PyDict::new(py);
    result.set_item("image", encoded)?;
    result.set_item("width", width)?;
    result.set_item("height", height)?;
    result.update(metadata.cast::<PyDict>()?.as_mapping())?;
    Ok(result)
}

pub(crate) fn register(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(render_sounding_png, module)?)?;
    module.add_function(wrap_pyfunction!(render_sounding_view, module)?)
}

#[cfg(test)]
mod tests;

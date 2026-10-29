use std::{
    collections::HashMap,
    ffi::OsString,
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicU64, Ordering},
        Mutex,
    },
};

use serde_json::{json, Value};

use super::{ByteRange, Error};

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(super) struct Identity {
    size: Option<u64>,
    etag: Option<String>,
    last_modified: Option<String>,
}

impl Identity {
    pub fn response(headers: &HashMap<String, String>, size: u64) -> Self {
        Self {
            size: Some(size),
            etag: headers.get("etag").filter(|s| !s.is_empty()).cloned(),
            last_modified: headers
                .get("last-modified")
                .filter(|s| !s.is_empty())
                .cloned(),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.size.is_none() && self.etag.is_none() && self.last_modified.is_none()
    }

    pub fn matches(&self, actual: &Self) -> bool {
        !self.is_empty()
            && self.size.is_none_or(|value| Some(value) == actual.size)
            && self
                .etag
                .as_ref()
                .is_none_or(|value| Some(value) == actual.etag.as_ref())
            && self
                .last_modified
                .as_ref()
                .is_none_or(|value| Some(value) == actual.last_modified.as_ref())
    }

    pub fn if_range(&self) -> Option<&str> {
        self.etag
            .as_deref()
            .filter(|tag| !tag.starts_with("W/"))
            .or(self.last_modified.as_deref())
    }

    fn parse(manifest: &Value) -> Self {
        let Some(value) = manifest.get("identity").filter(|v| v.is_object()) else {
            return Self {
                etag: manifest
                    .get("etag")
                    .and_then(Value::as_str)
                    .filter(|s| !s.is_empty())
                    .map(str::to_owned),
                ..Self::default()
            };
        };
        Self {
            size: value
                .get("size")
                .and_then(|v| v.as_u64().or_else(|| v.as_str()?.parse().ok())),
            etag: value
                .get("etag")
                .and_then(Value::as_str)
                .filter(|s| !s.is_empty())
                .map(str::to_owned),
            last_modified: value
                .get("last_modified")
                .and_then(Value::as_str)
                .filter(|s| !s.is_empty())
                .map(str::to_owned),
        }
    }

    fn json(&self) -> Value {
        let mut value = serde_json::Map::new();
        if let Some(size) = self.size {
            value.insert("size".into(), json!(size));
        }
        if let Some(etag) = &self.etag {
            value.insert("etag".into(), json!(etag));
        }
        if let Some(modified) = &self.last_modified {
            value.insert("last_modified".into(), json!(modified));
        }
        Value::Object(value)
    }
}

pub(super) struct Store {
    pub directory: PathBuf,
    manifest_path: PathBuf,
    manifest: Mutex<Value>,
}

impl Store {
    pub fn open(output: &Path, url: &str, plan: &[ByteRange]) -> Result<Self, Error> {
        let parent = output
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        fs::create_dir_all(parent).map_err(io_error)?;
        let mut directory = OsString::from(".");
        directory.push(
            output
                .file_name()
                .ok_or_else(|| Error::Unavailable("output has no file name".into()))?,
        );
        directory.push(".ranges");
        let directory = parent.join(directory);
        let manifest_path = directory.join("manifest.json");
        let mut manifest = fs::read(&manifest_path)
            .ok()
            .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
            .filter(Value::is_object)
            .unwrap_or_else(|| json!({}));
        let ranges: Vec<_> = plan.iter().map(|r| [r.start, r.end]).collect();
        if manifest.as_object().is_some_and(|m| !m.is_empty())
            && (manifest.get("url") != Some(&json!(url))
                || manifest.get("ranges") != Some(&json!(ranges)))
        {
            remove_directory(&directory)?;
            manifest = json!({});
        }
        manifest["version"] = json!(2);
        manifest["url"] = json!(url);
        manifest["ranges"] = json!(ranges);
        fs::create_dir_all(&directory).map_err(io_error)?;
        Ok(Self {
            directory,
            manifest_path,
            manifest: Mutex::new(manifest),
        })
    }

    pub fn identity(&self) -> Identity {
        Identity::parse(&self.manifest.lock().unwrap())
    }

    pub fn has_parts(&self) -> Result<bool, Error> {
        for entry in fs::read_dir(&self.directory).map_err(io_error)? {
            if entry
                .map_err(io_error)?
                .path()
                .extension()
                .is_some_and(|v| v == "part")
            {
                return Ok(true);
            }
        }
        Ok(false)
    }

    pub fn reset(&self) -> Result<(), Error> {
        self.remove()?;
        fs::create_dir_all(&self.directory).map_err(io_error)?;
        let mut manifest = self.manifest.lock().unwrap();
        manifest.as_object_mut().unwrap().remove("identity");
        manifest.as_object_mut().unwrap().remove("etag");
        Ok(())
    }

    pub fn save(&self) -> Result<(), Error> {
        write_manifest(&self.manifest_path, &self.manifest.lock().unwrap())
    }

    pub fn set_identity(&self, identity: Identity) -> Result<(), Error> {
        let mut manifest = self.manifest.lock().unwrap();
        manifest["identity"] = identity.json();
        if let Some(etag) = &identity.etag {
            manifest["etag"] = json!(etag);
        } else {
            manifest.as_object_mut().unwrap().remove("etag");
        }
        write_manifest(&self.manifest_path, &manifest)
    }

    pub fn pin(&self, headers: &HashMap<String, String>, size: u64) -> Result<(), Error> {
        let candidate = Identity::response(headers, size);
        let mut manifest = self.manifest.lock().unwrap();
        let expected = Identity::parse(&manifest);
        if !expected.is_empty() && !expected.matches(&candidate) {
            return Err(Error::ObjectChanged(
                "source changed during the HTTP range transfer".into(),
            ));
        }
        if candidate != expected {
            manifest["identity"] = candidate.json();
            if let Some(etag) = &candidate.etag {
                manifest["etag"] = json!(etag);
            }
            write_manifest(&self.manifest_path, &manifest)?;
        }
        Ok(())
    }

    pub fn part(&self, item: ByteRange) -> PathBuf {
        self.directory
            .join(format!("{}-{}.part", item.start, item.end))
    }

    pub fn size(&self, item: ByteRange) -> Result<u64, Error> {
        let path = self.part(item);
        let existing = match fs::metadata(&path) {
            Ok(metadata) => metadata.len(),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => 0,
            Err(error) => return Err(io_error(error)),
        };
        if existing > item.size() {
            fs::remove_file(path).map_err(io_error)?;
            return Ok(0);
        }
        Ok(existing)
    }

    pub fn sizes(&self, plan: &[ByteRange]) -> Result<Vec<u64>, Error> {
        plan.iter().map(|&item| self.size(item)).collect()
    }

    pub fn assemble(
        &self,
        plan: &[ByteRange],
        output: &Path,
        check: impl Fn() -> Result<(), Error>,
    ) -> Result<(), Error> {
        let (temporary, mut file) = temporary(output)?;
        let mut buffer = vec![0; 1024 * 1024];
        for &item in plan {
            if self.size(item)? != item.size() {
                return Err(Error::Unavailable(
                    "source returned an incomplete HTTP range".into(),
                ));
            }
            let mut source = File::open(self.part(item)).map_err(io_error)?;
            loop {
                check()?;
                let size = source.read(&mut buffer).map_err(io_error)?;
                if size == 0 {
                    break;
                }
                file.write_all(&buffer[..size]).map_err(io_error)?;
            }
        }
        file.flush().map_err(io_error)?;
        drop(file);
        check()?;
        if !valid_grib(&temporary.0) {
            return Err(Error::Unavailable(
                "assembled byte ranges are not a valid GRIB stream".into(),
            ));
        }
        fs::rename(&temporary.0, output).map_err(io_error)?;
        Ok(())
    }

    pub fn remove(&self) -> Result<(), Error> {
        remove_directory(&self.directory)
    }
}

pub(super) fn valid_grib(path: &Path) -> bool {
    let read = || -> std::io::Result<bool> {
        let mut file = File::open(path)?;
        if file.metadata()?.len() < 8 {
            return Ok(false);
        }
        let mut bytes = [0; 4];
        file.read_exact(&mut bytes)?;
        if &bytes != b"GRIB" {
            return Ok(false);
        }
        file.seek(SeekFrom::End(-4))?;
        file.read_exact(&mut bytes)?;
        Ok(&bytes == b"7777")
    };
    read().unwrap_or(false)
}

pub(super) fn io_error(error: std::io::Error) -> Error {
    Error::Unavailable(format!("HTTP range file operation failed: {error}"))
}

fn remove_directory(path: &Path) -> Result<(), Error> {
    match fs::remove_dir_all(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(io_error(error)),
    }
}

struct Temporary(PathBuf);
impl Drop for Temporary {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

fn temporary(target: &Path) -> Result<(Temporary, File), Error> {
    static NEXT: AtomicU64 = AtomicU64::new(0);
    loop {
        let mut name = target.as_os_str().to_os_string();
        name.push(format!(
            ".{}-{}.tmp",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        let path = PathBuf::from(name);
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(file) => return Ok((Temporary(path), file)),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(io_error(error)),
        }
    }
}

fn write_manifest(path: &Path, value: &Value) -> Result<(), Error> {
    let (temporary, mut file) = temporary(path)?;
    serde_json::to_writer(&mut file, value).map_err(|error| {
        Error::Unavailable(format!("HTTP range manifest write failed: {error}"))
    })?;
    file.flush().map_err(io_error)?;
    drop(file);
    fs::rename(&temporary.0, path).map_err(io_error)
}

//! Bounded reads and ownership of render output until validation succeeds.
use crate::sounding_image::dimensions;
use base64::{engine::general_purpose::STANDARD, Engine as _};
use std::{
    fs::{self, File},
    io::{self, Read},
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};

pub(super) enum ReadError {
    Io(io::Error),
    Invalid(io::Error),
    TooLarge,
}

pub(super) struct Image {
    pub(super) data: Vec<u8>,
    pub(super) width: u32,
    pub(super) height: u32,
}

impl Image {
    pub(super) fn read(path: &Path, limit: usize) -> Result<Self, ReadError> {
        // A bounded read also catches a file growing after its metadata was read.
        let file = File::open(path).map_err(ReadError::Io)?;
        let mut data = Vec::new();
        file.take(limit as u64 + 1)
            .read_to_end(&mut data)
            .map_err(ReadError::Io)?;
        if data.len() > limit {
            return Err(ReadError::TooLarge);
        }
        let (width, height) = dimensions(&data).map_err(ReadError::Invalid)?;
        Ok(Self {
            data,
            width,
            height,
        })
    }

    pub(super) fn encoded(self) -> (String, u32, u32, usize) {
        (
            STANDARD.encode(&self.data),
            self.width,
            self.height,
            self.data.len(),
        )
    }
}

pub(super) struct Staged {
    directory: PathBuf,
    pub(super) image: PathBuf,
}

impl Staged {
    pub(super) fn new(root: &Path) -> io::Result<Self> {
        static SEQUENCE: AtomicU64 = AtomicU64::new(0);
        loop {
            let directory = root.join(format!(
                ".sounding-render-{}-{}",
                std::process::id(),
                SEQUENCE.fetch_add(1, Ordering::Relaxed)
            ));
            match fs::create_dir(&directory) {
                Ok(()) => {
                    return Ok(Self {
                        image: directory.join("image.png"),
                        directory,
                    })
                }
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(error),
            }
        }
    }
}

impl Drop for Staged {
    fn drop(&mut self) {
        // Only the directory created by this transaction is owned by the guard.
        let _ = fs::remove_dir_all(&self.directory);
    }
}

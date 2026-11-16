//! Ownership of newly created sounding-cache files until registration succeeds.
use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::PathBuf,
};

#[derive(Default)]
pub(crate) struct Pending {
    paths: Vec<PathBuf>,
    pub(crate) committed: bool,
}

impl Pending {
    pub(crate) fn write(&mut self, path: PathBuf, bytes: &[u8]) -> std::io::Result<()> {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&path)?;
        self.paths.push(path);
        file.write_all(bytes)?;
        file.sync_all()
    }
}

impl Drop for Pending {
    fn drop(&mut self) {
        if !self.committed {
            for path in &self.paths {
                let _ = fs::remove_file(path);
            }
        }
    }
}

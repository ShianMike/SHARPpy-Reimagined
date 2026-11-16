//! Stream into a same-directory staging file, then replace only on success.

use ring::rand::{SecureRandom, SystemRandom};
use std::{
    fs::{self, File, OpenOptions},
    io,
    path::{Path, PathBuf},
};

struct Pending {
    path: PathBuf,
}

impl Drop for Pending {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.path);
    }
}

pub(super) struct Export {
    // File closes before Pending cleans up on an early error, also on Windows.
    writer: Option<csv::Writer<File>>,
    pending: Pending,
    destination: PathBuf,
}

impl Export {
    pub(super) fn new(path: PathBuf, fields: &[String], create_parents: bool) -> io::Result<Self> {
        let parent = path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        if create_parents {
            fs::create_dir_all(parent)?;
        }
        if path.file_name().is_none() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "CSV output needs a filename",
            ));
        }
        let permissions = match fs::metadata(&path) {
            Ok(metadata) => Some(metadata.permissions()),
            Err(error) if error.kind() == io::ErrorKind::NotFound => None,
            Err(error) => return Err(error),
        };
        let mut opened = None;
        for _ in 0..16 {
            let mut random = [0_u8; 8];
            SystemRandom::new()
                .fill(&mut random)
                .map_err(|_| io::Error::other("could not choose a temporary CSV filename"))?;
            let temporary = parent.join(format!(
                ".sharpmod-csv-{:016x}.tmp",
                u64::from_ne_bytes(random)
            ));
            match OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&temporary)
            {
                Ok(file) => {
                    opened = Some((Pending { path: temporary }, file));
                    break;
                }
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => (),
                Err(error) => return Err(error),
            }
        }
        let (pending, file) = opened.ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::AlreadyExists,
                "could not create a unique temporary CSV",
            )
        })?;
        let writer = csv::WriterBuilder::new()
            .terminator(csv::Terminator::CRLF)
            .from_writer(file);
        let mut export = Self {
            writer: Some(writer),
            pending,
            destination: path,
        };
        if let Some(permissions) = permissions {
            export
                .writer
                .as_ref()
                .expect("new CSV writer")
                .get_ref()
                .set_permissions(permissions)?;
        }
        export
            .writer
            .as_mut()
            .expect("new CSV writer")
            .write_record(fields)
            .map_err(io::Error::other)?;
        Ok(export)
    }

    pub(super) fn append(&mut self, rows: Vec<Vec<String>>) -> io::Result<()> {
        let writer = self.writer.as_mut().expect("unfinished CSV writer");
        for row in rows {
            writer.write_record(row).map_err(io::Error::other)?;
        }
        Ok(())
    }

    pub(super) fn finish(mut self) -> io::Result<()> {
        let writer = self.writer.as_mut().expect("unfinished CSV writer");
        writer.flush()?;
        writer.get_ref().sync_all()?;
        // Close the file before replacing an existing Windows destination.
        drop(self.writer.take());
        fs::rename(&self.pending.path, &self.destination)
    }
}

#[cfg(test)]
pub(super) struct Table {
    pub fields: Vec<String>,
    pub rows: Vec<Vec<String>>,
}

#[cfg(test)]
pub(super) fn write(path: &Path, table: &Table, create_parents: bool) -> io::Result<()> {
    let mut export = Export::new(path.to_path_buf(), &table.fields, create_parents)?;
    export.append(table.rows.clone())?;
    export.finish()
}

//! Reuse parsed SARS tables while their source file is unchanged.

use super::{parse, Record};
use std::{
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::SystemTime,
};

struct CachedTable<const N: usize> {
    key: (PathBuf, u64, Option<SystemTime>),
    records: Arc<Vec<Record<N>>>,
}

type Cache<const N: usize> = Mutex<Option<CachedTable<N>>>;

fn load<const N: usize>(
    cache: &Cache<N>,
    path: &Path,
    columns: [usize; N],
    outcome: usize,
    comment: &str,
) -> Result<Arc<Vec<Record<N>>>, String> {
    let metadata = std::fs::metadata(path).map_err(|error| error.to_string())?;
    let key = (path.to_path_buf(), metadata.len(), metadata.modified().ok());
    let mut cached = cache.lock().map_err(|error| error.to_string())?;
    if let Some(table) = cached
        .as_ref()
        .filter(|table| table.key == key && key.2.is_some())
    {
        return Ok(Arc::clone(&table.records));
    }
    let text = std::fs::read_to_string(path).map_err(|error| error.to_string())?;
    let records = Arc::new(parse(&text, columns, outcome, comment)?);
    *cached = Some(CachedTable {
        key,
        records: Arc::clone(&records),
    });
    Ok(records)
}

pub(crate) fn hail(path: &Path) -> Result<Arc<Vec<Record<8>>>, String> {
    static HAIL: Cache<8> = Mutex::new(None);
    load(&HAIL, path, [4, 3, 5, 7, 10, 11, 9, 12], 2, "#")
}

pub(crate) fn supercell(path: &Path) -> Result<Arc<Vec<Record<9>>>, String> {
    static SUPERCELL: Cache<9> = Mutex::new(None);
    load(&SUPERCELL, path, [3, 5, 9, 11, 7, 6, 12, 13, 14], 1, "%%%%")
}

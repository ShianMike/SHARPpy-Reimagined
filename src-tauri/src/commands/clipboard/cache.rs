//! App-owned clipboard files. A file currently referenced by CF_HDROP survives
//! application exit and startup cleanup, until the clipboard changes.

use std::fs::{self, Metadata, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

const PREFIX: &str = "forecast-gif-v1-";
const RECENT_GRACE: Duration = Duration::from_secs(10 * 60);
const MAX_RETAINED: usize = 2;
static NEXT_ID: AtomicU64 = AtomicU64::new(0);

pub(super) struct PendingFile {
    path: PathBuf,
    published: bool,
}

impl PendingFile {
    pub(super) fn create(directory: &Path, bytes: &[u8]) -> io::Result<Self> {
        fs::create_dir_all(directory)?;
        verify_directory(directory)?;
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        loop {
            let seq = NEXT_ID.fetch_add(1, Ordering::Relaxed);
            let path = directory.join(format!("{PREFIX}{}-{now}-{seq}.gif", std::process::id()));
            match OpenOptions::new().write(true).create_new(true).open(&path) {
                Ok(mut file) => {
                    let pending = Self {
                        path,
                        published: false,
                    };
                    file.write_all(bytes)?;
                    file.sync_all()?;
                    return Ok(pending);
                }
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(error),
            }
        }
    }

    pub(super) fn path(&self) -> &Path {
        &self.path
    }

    pub(super) fn publish(&mut self) {
        self.published = true;
    }
}

impl Drop for PendingFile {
    fn drop(&mut self) {
        if !self.published {
            let _ = fs::remove_file(&self.path);
        }
    }
}

fn owned_name(name: &str) -> bool {
    let Some(rest) = name
        .strip_prefix(PREFIX)
        .and_then(|name| name.strip_suffix(".gif"))
    else {
        return false;
    };
    let mut parts = rest.split('-');
    matches!((parts.next(), parts.next(), parts.next(), parts.next()),
        (Some(pid), Some(time), Some(seq), None)
            if pid.parse::<u32>().is_ok_and(|pid| pid != 0)
                && time.parse::<u128>().is_ok()
                && seq.parse::<u64>().is_ok())
}

fn reparse(metadata: &Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        metadata.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))]
    {
        metadata.file_type().is_symlink()
    }
}

fn verify_directory(directory: &Path) -> io::Result<()> {
    let metadata = fs::symlink_metadata(directory)?;
    if reparse(&metadata) || !metadata.is_dir() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "GIF clipboard cache is not a normal directory",
        ));
    }
    Ok(())
}

/// Call while the clipboard is open and the cross-process cache mutex is held.
/// Entries other than the exact names created above are never removed.
pub(super) fn cleanup(directory: &Path, protected: &[PathBuf], now: SystemTime) -> io::Result<()> {
    match verify_directory(directory) {
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        other => other?,
    }
    let mut candidates = Vec::new();
    for entry in fs::read_dir(directory)? {
        let entry = entry?;
        let name = entry.file_name();
        if !name.to_str().is_some_and(owned_name) {
            continue;
        }
        let metadata = fs::symlink_metadata(entry.path())?;
        if reparse(&metadata) || !metadata.is_file() {
            continue;
        }
        let path = entry.path();
        let keep = protected.iter().any(|item| same_path(item, &path));
        candidates.push((path, metadata.modified()?, keep));
    }
    candidates.sort_by_key(|candidate| std::cmp::Reverse(candidate.1));
    let mut retained = candidates.iter().filter(|candidate| candidate.2).count();
    let mut first_error = None;
    for (path, modified, keep) in candidates {
        if keep {
            continue;
        }
        let recent = now.duration_since(modified).unwrap_or_default() <= RECENT_GRACE;
        if recent && retained < MAX_RETAINED {
            retained += 1;
        } else if let Err(error) = fs::remove_file(path) {
            if error.kind() != io::ErrorKind::NotFound && first_error.is_none() {
                first_error = Some(error);
            }
        }
    }
    first_error.map_or(Ok(()), Err)
}

fn same_path(a: &Path, b: &Path) -> bool {
    if a == b {
        return true;
    }
    let (Ok(a), Ok(b)) = (fs::canonicalize(a), fs::canonicalize(b)) else {
        return false;
    };
    #[cfg(windows)]
    {
        a.to_string_lossy()
            .eq_ignore_ascii_case(&b.to_string_lossy())
    }
    #[cfg(not(windows))]
    {
        a == b
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn directory() -> PathBuf {
        let sequence = NEXT_ID.fetch_add(1, Ordering::Relaxed);
        std::env::temp_dir().join(format!(
            "sharppy-gif-test-{}-{sequence}",
            std::process::id()
        ))
    }

    #[test]
    fn cleanup_keeps_current_and_one_recent_removes_stale_and_ignores_other_files() {
        let dir = directory();
        let mut current = PendingFile::create(&dir, b"current").unwrap();
        current.publish();
        let mut old = PendingFile::create(&dir, b"old").unwrap();
        old.publish();
        let mut recent = PendingFile::create(&dir, b"recent").unwrap();
        recent.publish();
        let previous = SystemTime::now() - RECENT_GRACE - Duration::from_secs(5);
        OpenOptions::new()
            .write(true)
            .open(old.path())
            .unwrap()
            .set_modified(previous)
            .unwrap();
        fs::write(dir.join("user.gif"), b"keep").unwrap();
        fs::create_dir(dir.join("forecast-gif-v1-1-2-3.gif")).unwrap();
        cleanup(&dir, &[current.path().to_owned()], SystemTime::now()).unwrap();
        assert!(current.path().exists());
        assert!(recent.path().exists());
        assert!(!old.path().exists());
        assert!(dir.join("user.gif").exists());
        fs::remove_file(current.path()).unwrap();
        fs::remove_file(recent.path()).unwrap();
        fs::remove_file(dir.join("user.gif")).unwrap();
        fs::remove_dir(dir.join("forecast-gif-v1-1-2-3.gif")).unwrap();
        fs::remove_dir(dir).unwrap();
    }

    #[test]
    fn pending_files_are_removed_unless_they_were_published() {
        let dir = directory();
        let pending = PendingFile::create(&dir, b"not published").unwrap();
        let path = pending.path().to_owned();
        drop(pending);
        assert!(!path.exists());
        fs::remove_dir(dir).unwrap();
    }

    #[test]
    fn recognizes_only_our_exact_filename_format() {
        assert!(owned_name("forecast-gif-v1-1-2-3.gif"));
        for name in [
            "forecast-gif-v1-0-2-3.gif",
            "forecast-gif-v1-1-2-x.gif",
            "forecast-gif-v1-1-2-3.gif.bak",
            "user.gif",
        ] {
            assert!(!owned_name(name));
        }
    }
}

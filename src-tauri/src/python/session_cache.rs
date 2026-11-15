//! Disposable bridge files and conservative recovery after desktop process death.
//!
//! Only directories created by this launcher, with a complete ownership
//! marker and a process group that no longer exists, are eligible. Older
//! unmarked directories are deliberately left alone: their writers cannot be
//! identified reliably.

use std::fs::{self, Metadata};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

const PREFIX: &str = "bridge-cache-v2-";
const MARKER_SUFFIX: &str = ".owner";
const MAGIC: &str = "sharppy-bridge-session-v2";
static NEXT_ID: AtomicU64 = AtomicU64::new(0);

pub struct SessionCache {
    path: PathBuf,
}

impl SessionCache {
    pub fn create(parent: &Path) -> io::Result<Self> {
        fs::create_dir_all(parent)?;
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        loop {
            let sequence = NEXT_ID.fetch_add(1, Ordering::Relaxed);
            let name = format!("{PREFIX}{}-{now}-{sequence}", std::process::id());
            let path = parent.join(name);
            match fs::create_dir(&path) {
                Ok(()) => return Ok(Self { path }),
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(error),
            }
        }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    #[cfg(windows)]
    pub fn job_name(&self) -> String {
        job_name(self.path.file_name().expect("session directory name"))
    }

    /// Called only after the child has joined its own process group or job.
    /// A crash before this marker is complete leaves the directory untouched.
    pub fn mark_managed(&self, child_pid: u32) -> io::Result<()> {
        #[cfg(unix)]
        let owner = format!("{MAGIC}\ngroup\n{child_pid}\n");
        #[cfg(windows)]
        let owner = format!("{MAGIC}\njob\n{child_pid}\n");
        #[cfg(not(any(unix, windows)))]
        let owner = {
            let _ = child_pid;
            return Err(io::Error::new(
                io::ErrorKind::Unsupported,
                "unknown process owner",
            ));
        };
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(marker_path(&self.path))?;
        file.write_all(owner.as_bytes())?;
        file.sync_all()
    }

    /// Remove only a completed session whose entire process group has exited.
    pub fn cleanup(&self) -> io::Result<bool> {
        clean_candidate(&self.path)
    }
}

fn valid_name(name: &str) -> bool {
    let Some(rest) = name.strip_prefix(PREFIX) else {
        return false;
    };
    let mut parts = rest.split('-');
    matches!((parts.next(), parts.next(), parts.next(), parts.next()),
        (Some(pid), Some(time), Some(sequence), None)
            if pid.parse::<u32>().is_ok_and(|pid| pid > 0)
                && time.parse::<u128>().is_ok()
                && sequence.parse::<u64>().is_ok())
}

fn marker_path(path: &Path) -> PathBuf {
    let mut name = path
        .file_name()
        .expect("session directory name")
        .to_os_string();
    name.push(MARKER_SUFFIX);
    path.with_file_name(name)
}

#[cfg(windows)]
fn job_name(name: &std::ffi::OsStr) -> String {
    format!("Local\\SHARPpy-{}", name.to_string_lossy())
}

fn is_reparse(metadata: &Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
        metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
    }
    #[cfg(not(windows))]
    {
        metadata.file_type().is_symlink()
    }
}

fn no_reparse_points(path: &Path) -> io::Result<bool> {
    let metadata = fs::symlink_metadata(path)?;
    if is_reparse(&metadata) {
        return Ok(false);
    }
    if metadata.is_dir() {
        for entry in fs::read_dir(path)? {
            if !no_reparse_points(&entry?.path())? {
                return Ok(false);
            }
        }
        Ok(true)
    } else {
        Ok(metadata.is_file())
    }
}

#[cfg(unix)]
fn group_gone(pid: u32) -> bool {
    use std::os::raw::c_int;
    unsafe extern "C" {
        fn kill(pid: c_int, sig: c_int) -> c_int;
    }
    let Ok(pid) = i32::try_from(pid) else {
        return false;
    };
    if pid <= 1 {
        return false;
    }
    // SAFETY: signal 0 only checks the group; it never signals a process.
    let result = unsafe { kill(-pid, 0) };
    result != 0 && io::Error::last_os_error().raw_os_error() == Some(3) // ESRCH
}

fn owner_gone(name: &str, marker: &str) -> bool {
    #[cfg(unix)]
    {
        let _ = name;
        let Some(pid) = marker
            .strip_prefix(&format!("{MAGIC}\ngroup\n"))
            .and_then(|text| text.strip_suffix('\n'))
            .and_then(|text| text.parse::<u32>().ok())
        else {
            return false;
        };
        group_gone(pid)
    }
    #[cfg(windows)]
    {
        let Some(pid) = marker
            .strip_prefix(&format!("{MAGIC}\njob\n"))
            .and_then(|text| text.strip_suffix('\n'))
            .and_then(|text| text.parse::<u32>().ok())
        else {
            return false;
        };
        super::job_windows::is_gone(&job_name(std::ffi::OsStr::new(name)))
            && super::job_windows::process_is_gone(pid)
    }
    #[cfg(not(any(unix, windows)))]
    {
        let _ = (name, marker);
        false
    }
}

fn marker_owner_gone(name: &str, marker_path: &Path) -> io::Result<bool> {
    let marker_metadata = match fs::symlink_metadata(marker_path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error),
    };
    if !marker_metadata.is_file() || is_reparse(&marker_metadata) || marker_metadata.len() > 128 {
        return Ok(false);
    }
    let bytes = fs::read(marker_path)?;
    let Ok(marker) = std::str::from_utf8(&bytes) else {
        return Ok(false);
    };
    Ok(owner_gone(name, marker))
}

fn candidate_owner_gone(path: &Path) -> io::Result<bool> {
    let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
        return Ok(false);
    };
    if !valid_name(name) {
        return Ok(false);
    }
    let metadata = fs::symlink_metadata(path)?;
    if !metadata.is_dir() || is_reparse(&metadata) {
        return Ok(false);
    }
    marker_owner_gone(name, &marker_path(path))
}

fn clean_candidate(path: &Path) -> io::Result<bool> {
    if !candidate_owner_gone(path)? || !no_reparse_points(path)? {
        return Ok(false);
    }
    fs::remove_dir_all(path)?;
    fs::remove_file(marker_path(path))?;
    Ok(true)
}

/// A successful directory removal followed by a failed marker removal is
/// retried too; the owner record is never discarded before the directory.
fn orphan_marker_owner_gone(path: &Path) -> io::Result<bool> {
    let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
        return Ok(false);
    };
    let Some(session_name) = name.strip_suffix(MARKER_SUFFIX) else {
        return Ok(false);
    };
    if !valid_name(session_name) {
        return Ok(false);
    }
    let session = path.with_file_name(session_name);
    match fs::symlink_metadata(session) {
        Ok(_) => return Ok(false),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(error),
    }
    marker_owner_gone(session_name, path)
}

/// Scan only the application's direct session directories, never other caches.
pub fn recover(parent: &Path) -> io::Result<usize> {
    recover_after(parent, std::time::Duration::from_secs(2))
}

fn recover_after(parent: &Path, grace: std::time::Duration) -> io::Result<usize> {
    match fs::symlink_metadata(parent) {
        Ok(metadata) if is_reparse(&metadata) || !metadata.is_dir() => {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid cache parent",
            ));
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(0),
        Err(error) => return Err(error),
        _ => {}
    }
    let entries = match fs::read_dir(parent) {
        Ok(entries) => entries,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(0),
        Err(error) => return Err(error),
    };
    let mut candidates = Vec::new();
    let mut orphan_markers = Vec::new();
    let mut first_error = None;
    for entry in entries {
        let entry = match entry {
            Ok(entry) => entry,
            Err(error) => {
                first_error.get_or_insert(error);
                continue;
            }
        };
        let path = entry.path();
        match candidate_owner_gone(&path) {
            Ok(true) => candidates.push(path.clone()),
            Ok(false) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => {
                first_error.get_or_insert(error);
            }
        }
        match orphan_marker_owner_gone(&path) {
            Ok(true) => orphan_markers.push(path),
            Ok(false) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => {
                first_error.get_or_insert(error);
            }
        }
    }
    // Confirm absence again after the kernel has had time to finish tearing
    // down a killed job and its processes. This runs on the recovery thread.
    if (!candidates.is_empty() || !orphan_markers.is_empty()) && !grace.is_zero() {
        std::thread::sleep(grace);
    }
    let mut removed = 0;
    for candidate in candidates {
        let parent_metadata = fs::symlink_metadata(parent)?;
        if !parent_metadata.is_dir() || is_reparse(&parent_metadata) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid cache parent",
            ));
        }
        match clean_candidate(&candidate) {
            Ok(true) => removed += 1,
            Ok(false) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => {
                first_error.get_or_insert(error);
            }
        }
    }
    for marker in orphan_markers {
        let parent_metadata = fs::symlink_metadata(parent)?;
        if !parent_metadata.is_dir() || is_reparse(&parent_metadata) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid cache parent",
            ));
        }
        match orphan_marker_owner_gone(&marker) {
            Ok(true) => match fs::remove_file(marker) {
                Ok(()) => {}
                Err(error) if error.kind() == io::ErrorKind::NotFound => {}
                Err(error) => {
                    first_error.get_or_insert(error);
                }
            },
            Ok(false) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => {
                first_error.get_or_insert(error);
            }
        }
    }
    match first_error {
        Some(error) => Err(error),
        None => Ok(removed),
    }
}

#[cfg(test)]
#[path = "session_cache_tests.rs"]
mod tests;

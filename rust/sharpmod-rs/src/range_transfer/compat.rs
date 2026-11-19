//! Sequential compatibility transfer for weak range servers and geographic CGI.
use super::{disk, ByteRange, Error, Hooks, Transport};
use std::{
    fs::{File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicU64, Ordering},
        Condvar, Mutex,
    },
    time::Duration,
};
struct Temporary(PathBuf);
impl Drop for Temporary {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}
pub(crate) fn download(
    transport: &dyn Transport,
    hooks: &dyn Hooks,
    url: &str,
    plan: &[ByteRange],
    output: &Path,
    chunk_size: usize,
    full: bool,
) -> Result<u64, Error> {
    let check = || {
        if hooks.cancelled() {
            Err(Error::Cancelled)
        } else {
            Ok(())
        }
    };
    check()?;
    if disk::valid_grib(output) {
        let size = std::fs::metadata(output).map_err(disk::io_error)?.len();
        hooks.progress(size, size)?;
        return Ok(size);
    }
    if !full && plan.is_empty() {
        return Err(Error::Unavailable(
            "compatibility byte-range plan is empty".into(),
        ));
    }
    let total = plan
        .iter()
        .try_fold(0_u64, |sum, r| {
            r.end
                .checked_sub(r.start)
                .and_then(|n| n.checked_add(1))
                .and_then(|n| sum.checked_add(n))
        })
        .ok_or_else(|| Error::Unavailable("inventory contains an invalid byte range".into()))?;
    let parent = output
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    std::fs::create_dir_all(parent).map_err(disk::io_error)?;
    static NEXT: AtomicU64 = AtomicU64::new(0);
    let (temporary, mut file) = loop {
        let mut name = output.as_os_str().to_os_string();
        name.push(format!(
            ".{}-{}.part",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        let path = PathBuf::from(name);
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(file) => break (Temporary(path), file),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(disk::io_error(e)),
        }
    };
    let done = (Mutex::new(false), Condvar::new());
    let result = std::thread::scope(|scope| {
        scope.spawn(|| loop {
            let finished = done
                .1
                .wait_timeout_while(done.0.lock().unwrap(), Duration::from_millis(50), |v| !*v)
                .unwrap();
            if *finished.0 {
                break;
            }
            drop(finished);
            if hooks.cancelled() {
                transport.close_active();
                break;
            }
        });
        let result = (|| {
            let mut completed = 0_u64;
            if url.starts_with("https://") || url.starts_with("http://") {
                let groups: Vec<_> = if full {
                    vec![None]
                } else {
                    plan.iter().copied().map(Some).collect()
                };
                for range in groups {
                    check()?;
                    let headers = range
                        .map(|r| vec![("Range".into(), format!("bytes={}-{}", r.start, r.end))])
                        .unwrap_or_default();
                    let mut response = transport.get(0, url, &headers, chunk_size.max(1))?;
                    if (full && response.status != 200)
                        || (!full && ![200, 206].contains(&response.status))
                    {
                        return Err(Error::Unavailable(format!(
                            "compatibility subset returned HTTP {}",
                            response.status
                        )));
                    }
                    if response.status == 200 && completed != 0 {
                        file.seek(SeekFrom::Start(0)).map_err(disk::io_error)?;
                        file.set_len(0).map_err(disk::io_error)?;
                        completed = 0;
                    }
                    let expected = if full {
                        response
                            .headers
                            .get("content-length")
                            .and_then(|v| v.parse().ok())
                            .unwrap_or(0)
                    } else {
                        total
                    };
                    let mut received = 0_u64;
                    while let Some(bytes) = response.body.next()? {
                        check()?;
                        if bytes.is_empty() {
                            continue;
                        }
                        file.write_all(&bytes).map_err(disk::io_error)?;
                        received += bytes.len() as u64;
                        completed += bytes.len() as u64;
                        hooks.bytes(bytes.len() as u64)?;
                        hooks.progress(completed, expected)?;
                        check()?;
                    }
                    response.body.close();
                    if response.status == 206
                        && range.is_some_and(|r| received != r.end - r.start + 1)
                    {
                        return Err(Error::Unavailable(
                            "compatibility subset returned an incomplete byte range".into(),
                        ));
                    }
                    if response.status == 200 {
                        break;
                    }
                }
            } else {
                let mut source = File::open(url).map_err(disk::io_error)?;
                let mut buffer = vec![0; chunk_size.clamp(1, 16 * 1024 * 1024)];
                for range in plan {
                    check()?;
                    source
                        .seek(SeekFrom::Start(range.start))
                        .map_err(disk::io_error)?;
                    let mut remaining = range.end - range.start + 1;
                    while remaining != 0 {
                        check()?;
                        let size = buffer.len().min(remaining as usize);
                        let n = source.read(&mut buffer[..size]).map_err(disk::io_error)?;
                        if n == 0 {
                            return Err(Error::Unavailable(
                                "local compatibility source ended early".into(),
                            ));
                        }
                        file.write_all(&buffer[..n]).map_err(disk::io_error)?;
                        remaining -= n as u64;
                        completed += n as u64;
                        hooks.progress(completed, total)?;
                    }
                }
            }
            check()?;
            file.sync_all().map_err(disk::io_error)?;
            if !disk::valid_grib(&temporary.0) {
                return Err(Error::Unavailable(
                    "compatibility subset did not produce a valid GRIB".into(),
                ));
            }
            Ok(completed)
        })();
        *done.0.lock().unwrap() = true;
        done.1.notify_one();
        if hooks.cancelled() {
            Err(Error::Cancelled)
        } else {
            result
        }
    });
    let completed = result?;
    drop(file);
    std::fs::rename(&temporary.0, output).map_err(disk::io_error)?;
    Ok(completed)
}
#[cfg(test)]
mod tests;

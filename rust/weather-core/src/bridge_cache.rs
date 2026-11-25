use flate2::{read::GzDecoder, write::GzEncoder, Compression, GzBuilder};
use serde_json::Value;
use std::fs::{self, File, Metadata, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Debug)]
pub enum BridgeCacheError {
    TooLarge,
    Io(io::Error),
    Json(serde_json::Error),
}

impl From<io::Error> for BridgeCacheError {
    fn from(value: io::Error) -> Self {
        Self::Io(value)
    }
}

impl From<serde_json::Error> for BridgeCacheError {
    fn from(value: serde_json::Error) -> Self {
        Self::Json(value)
    }
}

pub fn is_regular_entry(meta: &Metadata) -> bool {
    let regular = !meta.file_type().is_symlink();
    #[cfg(windows)]
    let regular = {
        use std::os::windows::fs::MetadataExt;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0400;
        regular && (meta.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT) == 0
    };
    regular
}

pub fn mtime_epoch_seconds(meta: &Metadata) -> f64 {
    let Ok(modified) = meta.modified() else {
        return 0.0;
    };
    match modified.duration_since(UNIX_EPOCH) {
        Ok(d) => d.as_secs_f64(),
        Err(err) => -err.duration().as_secs_f64(),
    }
}

pub fn now_epoch_seconds() -> f64 {
    match SystemTime::now().duration_since(UNIX_EPOCH) {
        Ok(d) => d.as_secs_f64(),
        Err(err) => -err.duration().as_secs_f64(),
    }
}

pub fn decompress_gzip_bounded<R: Read>(
    reader: R,
    max_bytes: usize,
) -> Result<Vec<u8>, BridgeCacheError> {
    let decoder = GzDecoder::new(reader);
    let mut limited = decoder.take(max_bytes as u64 + 1);
    let mut out = Vec::new();
    limited.read_to_end(&mut out)?;
    if out.len() > max_bytes {
        return Err(BridgeCacheError::TooLarge);
    }
    Ok(out)
}

pub fn compress_gzip_level1(data: &[u8]) -> io::Result<Vec<u8>> {
    let mut encoder: GzEncoder<Vec<u8>> = GzBuilder::new()
        .mtime(0)
        .write(Vec::new(), Compression::fast());
    encoder.write_all(data)?;
    encoder.finish()
}

pub fn read_gzip_json_object_from_bytes(
    compressed: &[u8],
    max_bytes: usize,
) -> Option<serde_json::Map<String, Value>> {
    let decoded = decompress_gzip_bounded(compressed, max_bytes).ok()?;
    let value: Value = serde_json::from_slice(&decoded).ok()?;
    match value {
        Value::Object(map) => Some(map),
        _ => None,
    }
}

pub fn read_live_cache_file(
    path: &Path,
    ttl_seconds: f64,
    now_secs: f64,
    max_bytes: usize,
) -> Option<serde_json::Map<String, Value>> {
    let lmeta = fs::symlink_metadata(path).ok()?;
    if !is_regular_entry(&lmeta) || !lmeta.is_file() {
        return None;
    }
    if ttl_seconds.is_nan() {
        return None;
    }
    if now_secs - mtime_epoch_seconds(&lmeta) >= ttl_seconds {
        return None;
    }
    let file = File::open(path).ok()?;
    let decoded = decompress_gzip_bounded(file, max_bytes).ok()?;
    let value: Value = serde_json::from_slice(&decoded).ok()?;
    match value {
        Value::Object(map) => Some(map),
        _ => None,
    }
}

#[derive(Clone, Debug)]
pub struct CachedFileEntry {
    pub path: PathBuf,
    pub size: u64,
    pub mtime_secs: f64,
}

pub fn collect_live_cache_entries(
    search_root: &Path,
    exclude: &Path,
) -> io::Result<Vec<CachedFileEntry>> {
    let mut entries = Vec::new();
    let mut pending = vec![search_root.to_path_buf()];
    while let Some(dir) = pending.pop() {
        let Ok(children) = fs::read_dir(&dir) else {
            continue;
        };
        for entry_res in children {
            let Ok(entry) = entry_res else {
                continue;
            };
            let path = entry.path();
            if path == exclude {
                continue;
            }
            let Ok(meta) = fs::metadata(&path) else {
                continue;
            };
            if meta.is_dir() {
                pending.push(path);
            } else if meta.is_file() {
                let name = entry.file_name();
                if name.to_string_lossy().ends_with(".json.gz") {
                    entries.push(CachedFileEntry {
                        path,
                        size: meta.len(),
                        mtime_secs: mtime_epoch_seconds(&meta),
                    });
                }
            }
        }
    }
    entries.sort_by(|a, b| {
        a.mtime_secs
            .total_cmp(&b.mtime_secs)
            .then_with(|| a.size.cmp(&b.size))
            .then_with(|| a.path.cmp(&b.path))
    });
    Ok(entries)
}

pub fn write_bytes_atomically(path: &Path, payload: &[u8], unique_suffix: &str) -> io::Result<()> {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent)?;
        }
    }
    let temp = path.with_file_name(format!(
        "{}.{}.part",
        path.file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| "cache".to_string()),
        unique_suffix
    ));
    let mut handle = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(&temp)?;
    if let Err(err) = handle.write_all(payload) {
        drop(handle);
        let _ = fs::remove_file(&temp);
        return Err(err);
    }
    drop(handle);
    if let Err(err) = fs::rename(&temp, path) {
        let _ = fs::remove_file(&temp);
        return Err(err);
    }
    Ok(())
}

pub fn glob_matches(pattern: &str, text: &str) -> bool {
    #[cfg(windows)]
    let (p_owned, t_owned) = (pattern.to_lowercase(), text.to_lowercase());
    #[cfg(not(windows))]
    let (p_owned, t_owned) = (pattern.to_string(), text.to_string());

    let p: Vec<char> = p_owned.chars().collect();
    let t: Vec<char> = t_owned.chars().collect();
    glob_match_chars(&p, &t)
}

fn glob_match_chars(p: &[char], t: &[char]) -> bool {
    if p.is_empty() {
        return t.is_empty();
    }
    match p[0] {
        '*' => {
            let mut rest = 1;
            while rest < p.len() && p[rest] == '*' {
                rest += 1;
            }
            for i in 0..=t.len() {
                if glob_match_chars(&p[rest..], &t[i..]) {
                    return true;
                }
            }
            false
        }
        '?' => !t.is_empty() && glob_match_chars(&p[1..], &t[1..]),
        '[' => {
            if t.is_empty() {
                return false;
            }
            if let Some(end_offset) = p[1..].iter().position(|&c| c == ']') {
                let class_slice = &p[1..1 + end_offset];
                if !class_slice.is_empty() {
                    let (negate, items) = if class_slice[0] == '!' || class_slice[0] == '^' {
                        (true, &class_slice[1..])
                    } else {
                        (false, class_slice)
                    };
                    let ch = t[0];
                    let mut matched = false;
                    let mut i = 0;
                    while i < items.len() {
                        if i + 2 < items.len() && items[i + 1] == '-' {
                            if items[i] <= ch && ch <= items[i + 2] {
                                matched = true;
                            }
                            i += 3;
                        } else {
                            if items[i] == ch {
                                matched = true;
                            }
                            i += 1;
                        }
                    }
                    let pass = if negate { !matched } else { matched };
                    return pass && glob_match_chars(&p[2 + end_offset..], &t[1..]);
                }
            }
            t[0] == '[' && glob_match_chars(&p[1..], &t[1..])
        }
        ch => !t.is_empty() && t[0] == ch && glob_match_chars(&p[1..], &t[1..]),
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ScannedStorageFile {
    pub path: PathBuf,
    pub size: u64,
    pub ready: bool,
}

pub fn scan_storage_files(
    root: &Path,
    patterns: &[String],
    partial_cutoff_epoch_secs: f64,
) -> io::Result<Vec<ScannedStorageFile>> {
    let root_meta = match fs::symlink_metadata(root) {
        Ok(m) => m,
        Err(err) if err.kind() == io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(err) => return Err(err),
    };
    if !is_regular_entry(&root_meta) {
        return Ok(Vec::new());
    }
    let boundary = match fs::canonicalize(root) {
        Ok(b) => b,
        Err(err) if err.kind() == io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(err) => return Err(err),
    };

    let mut result = Vec::new();
    let mut pending = vec![root.to_path_buf()];
    while let Some(parent) = pending.pop() {
        let Ok(resolved_parent) = fs::canonicalize(&parent) else {
            continue;
        };
        if !resolved_parent.starts_with(&boundary) {
            continue;
        }
        let children = match fs::read_dir(&parent) {
            Ok(rd) => rd,
            Err(err) if err.kind() == io::ErrorKind::NotFound => continue,
            Err(err) => return Err(err),
        };
        for child_res in children {
            let child = match child_res {
                Ok(c) => c,
                Err(err) if err.kind() == io::ErrorKind::NotFound => continue,
                Err(err) => return Err(err),
            };
            let child_path = child.path();
            let info = match fs::symlink_metadata(&child_path) {
                Ok(m) => m,
                Err(err) if err.kind() == io::ErrorKind::NotFound => continue,
                Err(err) => return Err(err),
            };
            if !is_regular_entry(&info) {
                continue;
            }
            if info.is_dir() {
                pending.push(child_path);
            } else if info.is_file() {
                let name = child.file_name().to_string_lossy().into_owned();
                if patterns.iter().any(|pat| glob_matches(pat, &name)) {
                    let ready = !name.ends_with(".part")
                        || mtime_epoch_seconds(&info) < partial_cutoff_epoch_secs;
                    result.push(ScannedStorageFile {
                        path: child_path,
                        size: info.len(),
                        ready,
                    });
                }
            }
        }
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gzip_round_trip_and_bomb_limit() {
        let payload = br#"{"status":"ok","items":[1,2,3]}"#;
        let gz = compress_gzip_level1(payload).expect("compress");
        let parsed = read_gzip_json_object_from_bytes(&gz, 1024).expect("parse");
        assert_eq!(parsed.get("status").and_then(|v| v.as_str()), Some("ok"));
        assert!(matches!(
            decompress_gzip_bounded(&gz[..], 8),
            Err(BridgeCacheError::TooLarge)
        ));
        let arr_gz = compress_gzip_level1(b"[1,2]").expect("compress arr");
        assert!(read_gzip_json_object_from_bytes(&arr_gz, 1024).is_none());
    }

    #[test]
    fn cached_contour_coordinates_round_trip_exactly() {
        let position = [-126.88231999999999_f64, 35.602684999999994_f64];
        let payload = serde_json::to_vec(&serde_json::json!({"position": position}))
            .expect("serialize coordinates");
        let compressed = compress_gzip_level1(&payload).expect("compress");
        let cached = read_gzip_json_object_from_bytes(&compressed, 1024).expect("parse");
        let coordinates = cached["position"].as_array().expect("coordinates");
        assert_eq!(coordinates.len(), position.len());
        for (value, expected) in coordinates.iter().zip(position) {
            assert_eq!(
                value.as_f64().expect("coordinate").to_bits(),
                expected.to_bits()
            );
        }
    }

    #[test]
    fn glob_and_storage_scanner_filter_symlinks_and_parts() {
        assert!(glob_matches("*.json.gz", "frame.json.gz"));
        assert!(!glob_matches("*.json.gz", "frame.png"));
        assert!(glob_matches("a?c", "abc"));

        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let root = std::env::temp_dir().join(format!("sharpmod_scan_test_{nanos}"));
        let sub = root.join("sub");
        fs::create_dir_all(&sub).expect("mkdir");
        fs::write(sub.join("a.png"), b"12345").expect("write png");
        fs::write(sub.join("b.part"), b"12").expect("write part");

        let scanned = scan_storage_files(&root, &["*.png".to_string(), "*.part".to_string()], 0.0)
            .expect("scan");
        assert_eq!(scanned.len(), 2);
        let png = scanned.iter().find(|f| f.path.ends_with("a.png")).unwrap();
        let part = scanned.iter().find(|f| f.path.ends_with("b.part")).unwrap();
        assert!(png.ready);
        assert_eq!(png.size, 5);
        assert!(!part.ready);

        #[cfg(unix)]
        {
            let link_path = root.join("sym.png");
            std::os::unix::fs::symlink(sub.join("a.png"), &link_path).expect("symlink");
            let after = scan_storage_files(&root, &["*.png".to_string()], 0.0).expect("scan2");
            assert_eq!(after.len(), 1);
        }
        let _ = fs::remove_dir_all(&root);
    }
}

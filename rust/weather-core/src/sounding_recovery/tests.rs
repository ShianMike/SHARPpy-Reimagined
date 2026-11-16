use super::*;
use std::{
    path::PathBuf,
    sync::atomic::{AtomicU64, Ordering},
};
static SEQUENCE: AtomicU64 = AtomicU64::new(0);
struct Directory(PathBuf);
impl Directory {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "sharpmod-recovery-{}-{}",
            std::process::id(),
            SEQUENCE.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }
}
impl Drop for Directory {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}
fn summary() -> Value {
    serde_json::from_str(include_str!(
        "../../../../src-tauri/contracts/bridge/soundings/open.result.example.json"
    ))
    .unwrap()
}

#[test]
fn source_and_sidecar_survive_loss_of_the_process_cache_without_rounding() {
    let directory = Directory::new();
    let path = directory.0.join("profile.npz");
    let bytes = [0, 255, 18, 99, 128, 0];
    let metadata = br#"{"model":"HRRR","run":"2026-11-16T12:00:00Z"}"#;
    fs::write(&path, bytes).unwrap();
    fs::write(path.with_extension("json"), metadata).unwrap();
    let payload = pack(&path, summary()).unwrap();
    fs::remove_file(&path).unwrap();
    fs::remove_file(path.with_extension("json")).unwrap();
    let restored = unpack(&payload).unwrap();
    assert_eq!(restored.bytes, bytes);
    assert_eq!(restored.sidecar.unwrap(), metadata);
    assert_eq!(restored.summary, summary());
    assert_eq!(restored.suffix, ".npz");
}

#[test]
fn bad_versions_paths_sizes_and_damaged_sources_are_rejected() {
    let original = serde_json::json!({ "version":1, "suffix":".buf", "source":STANDARD.encode(b"profile"), "sidecar":null, "summary":summary() });
    for (key, value) in [
        ("version", serde_json::json!(2)),
        ("suffix", serde_json::json!("../outside")),
        ("suffix", serde_json::json!(".a/b")),
        ("suffix", serde_json::json!(".é")),
        ("source", serde_json::json!("invalid!")),
        ("source", serde_json::json!("")),
        (
            "sidecar",
            serde_json::json!(STANDARD.encode(vec![1; MAX_SIDECAR_BYTES + 1])),
        ),
    ] {
        let mut input = original.clone();
        input[key] = value;
        assert!(unpack(&input.to_string()).is_err(), "{key}");
    }
    assert!(unpack(&"x".repeat(MAX_PAYLOAD_BYTES + 1)).is_err());
}

#[test]
fn invalid_sounding_metadata_does_not_enter_the_backend_cache() {
    let directory = Directory::new();
    let path = directory.0.join("profile.txt");
    fs::write(&path, b"profile").unwrap();
    for (field, value) in [
        ("kind", serde_json::json!("wrf")),
        ("title", serde_json::json!("")),
        ("latitude", serde_json::json!(91)),
        ("time_count", serde_json::json!(0)),
        ("level_count", serde_json::json!(30000)),
        ("times", serde_json::json!([])),
    ] {
        let mut metadata = summary();
        metadata[field] = value;
        assert!(pack(&path, metadata).is_err(), "{field}");
    }
    let file = fs::File::create(&path).unwrap();
    file.set_len(MAX_SOURCE_BYTES as u64 + 1).unwrap();
    assert!(pack(&path, summary()).is_err());
}

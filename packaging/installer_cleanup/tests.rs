use super::*;
use std::sync::atomic::{AtomicU64, Ordering};

struct Directory(PathBuf);
impl Directory {
    fn new() -> Self {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        let path = std::env::temp_dir().join(format!(
            "sharppy-installer-test-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        Self(path)
    }
    fn backend(&self) -> PathBuf {
        self.0.join("backend")
    }
    fn file(&self, name: &str) -> PathBuf {
        let path = self.0.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, b"MZ runtime fixture").unwrap();
        path
    }
    fn install(&self) {
        self.file("backend/SHARPpy-Reimagined.exe");
        self.file("backend/_internal/current.dll");
        write_inventory(&self.backend(), "3.8.4").unwrap();
    }
}
impl Drop for Directory {
    fn drop(&mut self) {
        fs::remove_dir_all(&self.0).unwrap();
    }
}

#[test]
fn legacy_upgrade_removes_old_code_wheels_and_metadata_but_preserves_current_and_user_data() {
    let directory = Directory::new();
    directory.install();
    let stale = [
        directory.file("backend/_internal/obsolete.pyc"),
        directory.file("backend/_internal/old-native.pyd"),
        directory.file("backend/sharpmod_rs-3.8.2.whl"),
        directory.file("backend/_internal/sharpmod_rs-3.8.2.dist-info/METADATA"),
    ];
    let kept = [
        directory.file("saved-sounding.spc"),
        directory.file("backend/user-sounding.spc"),
        directory.file("backend/_internal/user-notes.txt"),
    ];
    fs::create_dir(directory.backend().join("user-empty-folder")).unwrap();
    prune(&directory.0, &directory.0.join("absent-previous")).unwrap();
    assert!(stale.iter().all(|path| !path.exists()));
    assert!(kept.iter().all(|path| path.exists()));
    assert!(directory.backend().join("_internal/current.dll").exists());
    assert!(directory.backend().join("user-empty-folder").exists());
    assert!(
        fs::read_to_string(directory.0.join("installer-cleanup.json"))
            .unwrap()
            .contains("\"removed_files\":4")
    );
}

#[test]
fn previous_inventory_prunes_retired_resources_while_preserving_unknown_documents() {
    let directory = Directory::new();
    directory.install();
    directory.file("backend/_internal/old-package/obsolete.dat");
    write_inventory(&directory.backend(), "3.8.3").unwrap();
    let previous = directory.0.join("previous-runtime-files");
    prepare(&directory.0, &previous).unwrap();
    fs::remove_file(
        directory
            .backend()
            .join("_internal/old-package/obsolete.dat"),
    )
    .unwrap();
    write_inventory(&directory.backend(), "3.8.4").unwrap();
    let retired = directory.file("backend/_internal/old-package/obsolete.dat");
    let user = directory.file("backend/_internal/user-notes.dat");
    prune(&directory.0, &previous).unwrap();
    assert!(!retired.exists());
    assert!(!directory.backend().join("_internal/old-package").exists());
    assert!(user.exists());
}

#[test]
fn missing_new_file_and_malformed_inventory_stop_before_deletion() {
    let directory = Directory::new();
    directory.install();
    let stale = directory.file("backend/_internal/old.dll");
    fs::remove_file(directory.backend().join("_internal/current.dll")).unwrap();
    assert!(prune(&directory.0, &directory.0.join("none")).is_err());
    assert!(stale.exists());
    directory.file("backend/_internal/current.dll");
    let previous = directory.file("previous-runtime-files");
    fs::write(&previous, format!("{HEADER}\n3.8.3\n../outside.dll\n")).unwrap();
    assert!(prune(&directory.0, &previous).is_err());
    assert!(stale.exists());
    for name in [
        "/absolute",
        "../outside",
        "C:/Windows/file",
        "nested/../file",
        "nested\\file",
        "a//b",
    ] {
        assert!(relative(name).is_err());
    }
    assert!(root(Path::new("relative")).is_err());
}

#[test]
fn fresh_install_is_idempotent_and_a_corrupt_previous_inventory_is_refused() {
    let directory = Directory::new();
    let fresh = directory.0.join("new-install");
    let previous = directory.0.join("previous-inventory");
    prepare(&fresh, &previous).unwrap();
    assert!(fresh.is_dir() && !previous.exists());
    directory.install();
    prune(&directory.0, &previous).unwrap();
    prune(&directory.0, &previous).unwrap();
    assert!(
        fs::read_to_string(directory.0.join("installer-cleanup.json"))
            .unwrap()
            .contains("\"removed_files\":0")
    );
    fs::write(directory.backend().join(INVENTORY), "untrusted").unwrap();
    assert!(prepare(&directory.0, &previous).is_err());
    assert!(!previous.exists());
}

#[test]
fn case_changes_do_not_delete_a_current_windows_runtime_file() {
    let directory = Directory::new();
    directory.install();
    let path = directory.backend().join(INVENTORY);
    let content = fs::read_to_string(&path)
        .unwrap()
        .replace("_internal/current.dll", "_INTERNAL/CURRENT.DLL");
    fs::write(path, content).unwrap();
    prune(&directory.0, &directory.0.join("none")).unwrap();
    assert!(directory.backend().join("_internal/current.dll").exists());
}

#[cfg(any(unix, windows))]
#[test]
fn directory_links_and_report_links_are_rejected_without_touching_their_targets() {
    let directory = Directory::new();
    let outside = Directory::new();
    directory.install();
    let stale = directory.file("backend/_internal/old.dll");
    let preserved = outside.file("preserved.dll");
    let link = directory.backend().join("_internal/linked");
    #[cfg(unix)]
    std::os::unix::fs::symlink(&outside.0, &link).unwrap();
    #[cfg(windows)]
    {
        let status = std::process::Command::new("cmd")
            .args(["/C", "mklink", "/J"])
            .arg(&link)
            .arg(&outside.0)
            .status()
            .unwrap();
        assert!(status.success());
    }
    assert!(prune(&directory.0, &directory.0.join("none")).is_err());
    assert!(prepare(&directory.0, &directory.0.join("previous")).is_err());
    assert!(preserved.exists() && stale.exists());
    #[cfg(unix)]
    fs::remove_file(&link).unwrap();
    #[cfg(windows)]
    fs::remove_dir(&link).unwrap();
    #[cfg(unix)]
    {
        std::os::unix::fs::symlink(&preserved, directory.0.join("installer-cleanup.json")).unwrap();
        assert!(prune(&directory.0, &directory.0.join("none")).is_err());
        assert_eq!(fs::read(preserved).unwrap(), b"MZ runtime fixture");
    }
}

#[cfg(windows)]
#[test]
fn a_locked_obsolete_dll_reports_failure_instead_of_claiming_cleanup() {
    use std::os::windows::fs::OpenOptionsExt;
    let directory = Directory::new();
    directory.install();
    let stale = directory.file("backend/_internal/old.dll");
    let lock = fs::OpenOptions::new()
        .read(true)
        .share_mode(0)
        .open(&stale)
        .unwrap();
    assert!(prune(&directory.0, &directory.0.join("none")).is_err());
    assert!(stale.exists());
    drop(lock);
    prune(&directory.0, &directory.0.join("none")).unwrap();
    assert!(!stale.exists());
}

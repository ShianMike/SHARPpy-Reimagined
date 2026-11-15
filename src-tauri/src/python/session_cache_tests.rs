use super::*;
#[cfg(unix)]
use std::os::unix::fs::symlink;
#[cfg(unix)]
use std::os::unix::process::CommandExt;
use std::process::Command;

fn scratch() -> PathBuf {
    let path = std::env::temp_dir().join(format!(
        "sharppy-session-cache-test-{}-{}",
        std::process::id(),
        NEXT_ID.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir(&path).unwrap();
    path
}

#[cfg(unix)]
fn live_group() -> (std::process::Child, ()) {
    let child = Command::new("sleep")
        .arg("60")
        .process_group(0)
        .spawn()
        .unwrap();
    (child, ())
}

#[cfg(windows)]
fn live_group(cache: &SessionCache) -> (std::process::Child, super::super::job_windows::Job) {
    use std::os::windows::io::AsRawHandle;
    use std::os::windows::process::CommandExt;
    let child = Command::new("cmd.exe")
        .args(["/C", "ping -n 60 127.0.0.1 >nul"])
        .creation_flags(0x0800_0000)
        .spawn()
        .unwrap();
    let job =
        super::super::job_windows::Job::assign(child.as_raw_handle(), &cache.job_name()).unwrap();
    (child, job)
}

#[test]
fn recovery_waits_for_the_writer_and_preserves_other_files() {
    let root = scratch();
    let cache = SessionCache::create(&root).unwrap();
    #[cfg(unix)]
    let (mut child, _) = live_group();
    #[cfg(windows)]
    let (mut child, job) = live_group(&cache);
    cache.mark_managed(child.id()).unwrap();
    fs::write(cache.path().join("cached.npz"), b"temporary").unwrap();
    let saved = root.join("saved.npz");
    fs::write(&saved, b"user data").unwrap();
    assert_eq!(recover_after(&root, std::time::Duration::ZERO).unwrap(), 0);
    assert!(cache.path().exists());
    child.kill().unwrap();
    child.wait().unwrap();
    #[cfg(windows)]
    job.terminate_remaining().unwrap();
    #[cfg(windows)]
    drop(job);
    #[cfg(windows)]
    for _ in 0..100 {
        if super::super::job_windows::is_gone(&cache.job_name()) {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
    assert_eq!(recover_after(&root, std::time::Duration::ZERO).unwrap(), 1);
    assert!(!cache.path().exists());
    assert_eq!(fs::read(&saved).unwrap(), b"user data");
    fs::remove_dir_all(root).unwrap();
}

#[cfg(unix)]
#[test]
fn recovery_waits_for_worker_after_bridge_exits() {
    let root = scratch();
    let cache = SessionCache::create(&root).unwrap();
    let (mut bridge, _) = live_group();
    let mut worker = Command::new("sleep")
        .arg("60")
        .process_group(i32::try_from(bridge.id()).unwrap())
        .spawn()
        .unwrap();
    cache.mark_managed(bridge.id()).unwrap();
    fs::write(cache.path().join("render.png"), b"rendering").unwrap();
    bridge.kill().unwrap();
    bridge.wait().unwrap();
    assert_eq!(recover_after(&root, std::time::Duration::ZERO).unwrap(), 0);
    assert!(cache.path().exists());
    worker.kill().unwrap();
    worker.wait().unwrap();
    assert_eq!(recover_after(&root, std::time::Duration::ZERO).unwrap(), 1);
    fs::remove_dir_all(root).unwrap();
}

#[cfg(windows)]
#[test]
fn locked_file_leaves_ownership_record_for_retry() {
    use std::os::windows::fs::OpenOptionsExt;

    let root = scratch();
    let cache = SessionCache::create(&root).unwrap();
    let other_cache = SessionCache::create(&root).unwrap();
    let (mut child, job) = live_group(&cache);
    let (mut other_child, other_job) = live_group(&other_cache);
    cache.mark_managed(child.id()).unwrap();
    other_cache.mark_managed(other_child.id()).unwrap();
    child.kill().unwrap();
    child.wait().unwrap();
    other_child.kill().unwrap();
    other_child.wait().unwrap();
    job.terminate_remaining().unwrap();
    other_job.terminate_remaining().unwrap();
    drop(job);
    drop(other_job);
    for _ in 0..100 {
        if super::super::job_windows::is_gone(&cache.job_name())
            && super::super::job_windows::is_gone(&other_cache.job_name())
        {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
    let file_path = cache.path().join("locked.png");
    fs::write(&file_path, b"temporary").unwrap();
    let locked = fs::OpenOptions::new()
        .read(true)
        .share_mode(0x0000_0001 | 0x0000_0002) // read/write sharing, no delete sharing
        .open(file_path)
        .unwrap();
    assert!(no_reparse_points(cache.path()).unwrap());
    assert!(recover_after(&root, std::time::Duration::ZERO).is_err());
    assert!(marker_path(cache.path()).exists());
    assert!(!other_cache.path().exists());
    assert!(!marker_path(other_cache.path()).exists());
    drop(locked);
    assert_eq!(recover_after(&root, std::time::Duration::ZERO).unwrap(), 1);
    assert!(!cache.path().exists());
    assert!(!marker_path(cache.path()).exists());
    fs::remove_dir(root).unwrap();
}

#[test]
fn orphaned_marker_is_removed_after_its_directory() {
    let root = scratch();
    let cache = SessionCache::create(&root).unwrap();
    #[cfg(unix)]
    let (mut child, _) = live_group();
    #[cfg(windows)]
    let (mut child, job) = live_group(&cache);
    cache.mark_managed(child.id()).unwrap();
    child.kill().unwrap();
    child.wait().unwrap();
    #[cfg(windows)]
    job.terminate_remaining().unwrap();
    #[cfg(windows)]
    drop(job);
    fs::remove_dir(cache.path()).unwrap();
    #[cfg(windows)]
    for _ in 0..100 {
        if super::super::job_windows::is_gone(&cache.job_name()) {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
    assert_eq!(recover_after(&root, std::time::Duration::ZERO).unwrap(), 0);
    assert!(!marker_path(cache.path()).exists());
    fs::remove_dir(root).unwrap();
}

#[cfg(windows)]
#[test]
fn crash_owner_helper() {
    let Some(root) = std::env::var_os("SHARPPY_CACHE_CRASH_TEST_ROOT") else {
        return;
    };
    let root = PathBuf::from(root);
    let cache = SessionCache::create(&root).unwrap();
    let (mut child, job) = live_group(&cache);
    cache.mark_managed(child.id()).unwrap();
    fs::write(cache.path().join("render.png"), b"temporary").unwrap();
    fs::write(
        root.join("ready"),
        cache.path().file_name().unwrap().as_encoded_bytes(),
    )
    .unwrap();
    std::thread::sleep(std::time::Duration::from_secs(60));
    drop(job);
    let _ = child.wait();
}

#[cfg(windows)]
#[test]
fn recovery_after_owner_process_is_killed() {
    use std::os::windows::process::CommandExt;
    use std::process::Stdio;
    use std::time::{Duration, Instant};

    let root = scratch();
    let saved = root.join("saved.npz");
    fs::write(&saved, b"user data").unwrap();
    let mut owner = Command::new(std::env::current_exe().unwrap())
        .args(["crash_owner_helper", "--nocapture"])
        .env("SHARPPY_CACHE_CRASH_TEST_ROOT", &root)
        .creation_flags(0x0800_0000)
        .stdout(Stdio::null())
        .spawn()
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(10);
    while !root.join("ready").exists() && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(20));
    }
    let name = fs::read_to_string(root.join("ready")).expect("owner became ready");
    let cache = root.join(name);
    assert_eq!(recover_after(&root, Duration::ZERO).unwrap(), 0);
    assert!(cache.exists());
    owner.kill().unwrap();
    owner.wait().unwrap();
    let deadline = Instant::now() + Duration::from_secs(10);
    while cache.exists() && Instant::now() < deadline {
        recover_after(&root, Duration::from_millis(100)).unwrap();
    }
    assert!(!cache.exists(), "cache survived after the job exited");
    assert_eq!(fs::read(saved).unwrap(), b"user data");
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn unknown_old_and_linked_directories_are_preserved() {
    let root = scratch();
    let unmarked = SessionCache::create(&root).unwrap();
    let malformed = SessionCache::create(&root).unwrap();
    fs::write(marker_path(malformed.path()), b"not an owner").unwrap();
    let legacy = root.join("bridge-cache-123-456");
    fs::create_dir(&legacy).unwrap();
    let saved = root.join("saved.npz");
    fs::write(&saved, b"keep").unwrap();
    #[cfg(unix)]
    let linked = {
        let linked = SessionCache::create(&root).unwrap();
        let (mut child, _) = live_group();
        linked.mark_managed(child.id()).unwrap();
        child.kill().unwrap();
        child.wait().unwrap();
        symlink(&saved, linked.path().join("link")).unwrap();
        linked
    };
    #[cfg(unix)]
    assert!(linked.path().exists());
    assert_eq!(recover_after(&root, std::time::Duration::ZERO).unwrap(), 0);
    for path in [unmarked.path(), malformed.path(), &legacy] {
        assert!(path.exists(), "{}", path.display());
    }
    assert_eq!(fs::read(saved).unwrap(), b"keep");
    fs::remove_dir_all(root).unwrap();
}

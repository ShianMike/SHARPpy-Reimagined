use serde_json::{json, Value};
use std::{io, path::Path, process::Command};

pub fn output(command: &str, args: &[&str], cwd: &Path) -> io::Result<String> {
    let result = Command::new(command).args(args).current_dir(cwd).output()?;
    if !result.status.success() {
        return Err(io::Error::other(format!(
            "{command} failed: {}",
            String::from_utf8_lossy(&result.stderr)
        )));
    }
    Ok(String::from_utf8_lossy(&result.stdout).trim().to_owned())
}

pub fn hash(bytes: &[u8]) -> String {
    ring::digest::digest(&ring::digest::SHA256, bytes)
        .as_ref()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

pub fn source(root: &Path) -> io::Result<Value> {
    let status = output(
        "git",
        &["status", "--porcelain", "--untracked-files=normal"],
        root,
    )?;
    Ok(
        json!({"revision": output("git", &["rev-parse", "HEAD"], root)?,
        "tree": output("git", &["rev-parse", "HEAD^{tree}"], root)?,
        "dirty": !status.is_empty(), "status": status.lines().collect::<Vec<_>>()}),
    )
}

pub fn machine(root: &Path, power_mode: &str) -> Value {
    let cpu = std::fs::read_to_string("/proc/cpuinfo")
        .ok()
        .and_then(|text| {
            text.lines()
                .find_map(|line| line.strip_prefix("model name\t: ").map(str::to_owned))
        });
    json!({"os": std::env::consts::OS, "arch": std::env::consts::ARCH,
        "cpu": cpu.or_else(|| std::env::var("PROCESSOR_IDENTIFIER").ok()),
        "available_parallelism": std::thread::available_parallelism().ok().map(usize::from),
        "power_mode": power_mode,
        "rustc": output("rustc", &["--version", "--verbose"], root).ok(),
        "cgroup_cpu_max": std::fs::read_to_string("/sys/fs/cgroup/cpu.max").ok().map(|s| s.trim().to_owned()),
        "cgroup_memory_max": std::fs::read_to_string("/sys/fs/cgroup/memory.max").ok().map(|s| s.trim().to_owned()),
        "build": if cfg!(debug_assertions) { "debug" } else { "release" },
    })
}

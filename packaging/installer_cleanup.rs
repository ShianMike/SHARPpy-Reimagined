//! Small, dependency-free installer helper; never starts the desktop or Python.
#![cfg_attr(all(windows, not(test)), windows_subsystem = "windows")]

#[path = "installer_cleanup/mod.rs"]
mod runtime;

fn main() {
    let args: Vec<_> = std::env::args_os().collect();
    let result = match args.get(1).and_then(|arg| arg.to_str()) {
        Some("--manifest") if args.len() == 4 => {
            runtime::write_inventory(std::path::Path::new(&args[2]), &args[3].to_string_lossy())
        }
        Some("--prune") if args.len() == 4 => runtime::prune(
            std::path::Path::new(&args[2]),
            std::path::Path::new(&args[3]),
        ),
        Some("--prepare") if args.len() == 5 => runtime::prepare(
            std::path::Path::new(&args[2]),
            std::path::Path::new(&args[3]),
            std::path::Path::new(&args[4]),
        ),
        _ => Err(std::io::Error::other("invalid installer cleanup arguments")),
    };
    if let Err(error) = result {
        eprintln!("Installer runtime cleanup failed: {error}");
        std::process::exit(2);
    }
}

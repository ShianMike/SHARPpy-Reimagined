//! The Python sidecar: interpreter discovery, process launch, the wire
//! protocol, and the supervisor that owns the child.

pub mod interpreter;
#[cfg(windows)]
mod job_windows;
pub mod launcher;
pub mod protocol;
mod session_cache;
pub mod supervisor;

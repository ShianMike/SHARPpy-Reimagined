//! Launching the bridge process. The [`ProcessLauncher`] trait is the test
//! seam: `tests/supervisor.rs` drives the supervisor with a fake launcher built
//! on in-memory pipes.

use std::future::Future;
use std::path::PathBuf;
use std::pin::Pin;
use std::process::Stdio;

use tokio::io::{AsyncRead, AsyncWrite};

use crate::error::BridgeFailure;
use crate::python::interpreter;

/// The fixed bridge arguments. Nothing from the webview reaches them.
pub const BRIDGE_ARGS: [&str; 6] = ["-I", "-u", "-X", "utf8", "-m", "sharpmod.bridge"];

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

pub type ExitFuture<'a> = Pin<Box<dyn Future<Output = Option<i32>> + Send + 'a>>;

/// Control of a launched child: wait for its exit code, or start killing it.
pub trait ChildControl: Send {
    /// Resolves with the exit code once the process has exited (`None` when it
    /// was killed by a signal or the code is unknown).
    fn wait(&mut self) -> ExitFuture<'_>;
    /// Starts killing the process; [`ChildControl::wait`] then resolves.
    fn start_kill(&mut self);
}

/// A launched bridge process with all three pipes.
pub struct LaunchedProcess {
    /// Shown in logs and error messages (the interpreter path).
    pub program: String,
    pub pid: Option<u32>,
    pub stdin: Box<dyn AsyncWrite + Send + Unpin>,
    pub stdout: Box<dyn AsyncRead + Send + Unpin>,
    pub stderr: Box<dyn AsyncRead + Send + Unpin>,
    pub control: Box<dyn ChildControl>,
}

pub trait ProcessLauncher: Send + Sync {
    /// Starts one bridge process. Fails with `NotConfigured` when no
    /// interpreter is configured, or `Spawn` when the process cannot start.
    fn launch(&self) -> Result<LaunchedProcess, BridgeFailure>;
}

/// The real launcher: `<python> -I -u -X utf8 -m sharpmod.bridge`, no shell,
/// no console window, and a kill-on-close Job Object on Windows.
pub struct TokioLauncher {
    working_dir: PathBuf,
    resources: PathBuf,
}

impl TokioLauncher {
    /// `working_dir` is the app local data directory; it is created if missing.
    pub fn new(working_dir: PathBuf, resources: PathBuf) -> Self {
        Self {
            working_dir,
            resources,
        }
    }
}

struct TokioChild {
    child: tokio::process::Child,
    #[cfg(windows)]
    _job: Option<super::job_windows::Job>,
}

impl ChildControl for TokioChild {
    fn wait(&mut self) -> ExitFuture<'_> {
        Box::pin(async move {
            self.child
                .wait()
                .await
                .ok()
                .and_then(|status| status.code())
        })
    }

    fn start_kill(&mut self) {
        if let Err(err) = self.child.start_kill() {
            log::debug!("bridge child kill: {err}");
        }
    }
}

impl ProcessLauncher for TokioLauncher {
    fn launch(&self) -> Result<LaunchedProcess, BridgeFailure> {
        let interpreter = interpreter::discover_from_environment(&self.resources)?;
        let args: &[&str] = if interpreter == interpreter::bundled_backend(&self.resources) {
            &["--desktop-bridge"]
        } else {
            &BRIDGE_ARGS
        };
        let program = interpreter.display().to_string();
        if let Err(err) = std::fs::create_dir_all(&self.working_dir) {
            log::warn!("could not create {}: {err}", self.working_dir.display());
        }
        let mut command = tokio::process::Command::new(&interpreter);
        command
            .args(args)
            .current_dir(&self.working_dir)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);
        let mut child = command.spawn().map_err(|err| BridgeFailure::Spawn {
            reason: format!("{program} could not be started ({err})"),
            details: None,
        })?;
        let pid = child.id();
        #[cfg(windows)]
        let job = match child.raw_handle().map(super::job_windows::Job::assign) {
            Some(Ok(job)) => Some(job),
            Some(Err(err)) => {
                log::warn!("could not assign the bridge to a Job Object: {err}");
                None
            }
            None => None,
        };
        let (Some(stdin), Some(stdout), Some(stderr)) =
            (child.stdin.take(), child.stdout.take(), child.stderr.take())
        else {
            return Err(BridgeFailure::Spawn {
                reason: format!("{program} started without pipes"),
                details: None,
            });
        };
        log::info!(
            "spawned bridge: {program} {} (spawned pid {pid:?})",
            args.join(" ")
        );
        Ok(LaunchedProcess {
            program,
            pid,
            stdin: Box::new(stdin),
            stdout: Box::new(stdout),
            stderr: Box::new(stderr),
            control: Box::new(TokioChild {
                child,
                #[cfg(windows)]
                _job: job,
            }),
        })
    }
}

//! Launching the bridge process. The [`ProcessLauncher`] trait is the test
//! seam: `tests/supervisor.rs` drives the supervisor with a fake launcher built
//! on in-memory pipes.

use std::future::Future;
use std::path::PathBuf;
use std::pin::Pin;
use std::process::Stdio;
use std::sync::Arc;

use tokio::io::{AsyncRead, AsyncWrite};

use crate::error::BridgeFailure;
use crate::python::interpreter;
use crate::python::session_cache::{self, SessionCache};

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
    _recovery_lifetime: Arc<()>,
}

impl TokioLauncher {
    /// `working_dir` is the app local data directory; it is created if missing.
    pub fn new(working_dir: PathBuf, resources: PathBuf) -> Self {
        let lifetime = Arc::new(());
        let owner = Arc::downgrade(&lifetime);
        let cache_parent = working_dir.clone();
        if let Err(error) = std::thread::Builder::new()
            .name("bridge-cache-recovery".into())
            .spawn(move || {
                while owner.strong_count() > 0 {
                    match session_cache::recover(&cache_parent) {
                        Ok(removed) if removed > 0 => {
                            log::info!("removed {removed} abandoned bridge sessions");
                        }
                        Err(error) => {
                            log::warn!("could not recover some bridge sessions: {error}");
                        }
                        _ => {}
                    }
                    std::thread::sleep(std::time::Duration::from_secs(30));
                }
            })
        {
            log::warn!("could not start bridge session recovery: {error}");
        }
        Self {
            working_dir,
            resources,
            _recovery_lifetime: lifetime,
        }
    }
}

struct TokioChild {
    child: tokio::process::Child,
    cache: SessionCache,
    #[cfg(windows)]
    job: Option<super::job_windows::Job>,
}

impl ChildControl for TokioChild {
    fn wait(&mut self) -> ExitFuture<'_> {
        Box::pin(async move {
            let status = self.child.wait().await.ok()?;
            // On Windows, forced bridge exit can leave its renderer in the job.
            #[cfg(windows)]
            if let Some(job) = &self.job {
                if let Err(error) = job.terminate_remaining() {
                    log::warn!("could not stop the bridge process group: {error}");
                }
            }
            #[cfg(windows)]
            for _ in 0..20 {
                match self.job.as_ref().map(|job| job.active_processes()) {
                    Some(Ok(0)) | None => break,
                    Some(Err(_)) => break,
                    Some(Ok(_)) => tokio::time::sleep(std::time::Duration::from_millis(50)).await,
                }
            }
            #[cfg(windows)]
            drop(self.job.take());
            // Workers exit when their pipes close. Never remove files while a
            // worker still owns them; next launch retries any deferred cleanup.
            let mut last_error = None;
            for attempt in 0..20 {
                match self.cache.cleanup() {
                    Ok(true) => {
                        last_error = None;
                        break;
                    }
                    Ok(false) => {}
                    Err(err) if err.kind() == std::io::ErrorKind::NotFound => {
                        last_error = None;
                        break;
                    }
                    Err(err) => last_error = Some(err),
                }
                if attempt < 19 {
                    tokio::time::sleep(std::time::Duration::from_millis(50)).await;
                }
            }
            if let Some(error) = last_error {
                log::warn!(
                    "could not remove {} after retrying: {error}",
                    self.cache.path().display()
                );
            }
            status.code()
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
        let cache =
            SessionCache::create(&self.working_dir).map_err(|err| BridgeFailure::Spawn {
                reason: format!("Could not create a bridge session cache ({err})"),
                details: None,
            })?;
        let mut command = tokio::process::Command::new(&interpreter);
        command
            .args(args)
            .current_dir(&self.working_dir)
            .env("SHARPMOD_BRIDGE_CACHE", cache.path())
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);
        #[cfg(unix)]
        command.process_group(0);
        let mut child = command.spawn().map_err(|err| {
            // No child was started and this fresh directory cannot contain data.
            let _ = std::fs::remove_dir(cache.path());
            BridgeFailure::Spawn {
                reason: format!("{program} could not be started ({err})"),
                details: None,
            }
        })?;
        let pid = child.id();
        #[cfg(windows)]
        let job = match child
            .raw_handle()
            .map(|handle| super::job_windows::Job::assign(handle, &cache.job_name()))
        {
            Some(Ok(job)) => Some(job),
            Some(Err(err)) => {
                log::warn!("could not assign the bridge to a Job Object: {err}");
                None
            }
            None => None,
        };
        #[cfg(windows)]
        let managed = job.is_some();
        #[cfg(unix)]
        let managed = true;
        #[cfg(not(any(unix, windows)))]
        let managed = false;
        if managed {
            if let Some(pid) = pid {
                if let Err(error) = cache.mark_managed(pid) {
                    log::warn!("could not record bridge session ownership: {error}");
                }
            }
        }
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
                cache,
                #[cfg(windows)]
                job,
            }),
        })
    }
}

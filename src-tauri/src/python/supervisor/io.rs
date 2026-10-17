//! The four tasks of one child: frame writer (stdin), frame reader (stdout),
//! stderr drain into the developer log, and the exit watcher.

use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncWrite, AsyncWriteExt, BufReader};

use super::*;
use crate::python::launcher::{ChildControl, LaunchedProcess};
use crate::python::protocol::{decode_envelope, read_line, CorruptFrame, LineRead};

/// Stderr lines kept for the exit-before-handshake log entry.
const STDERR_TAIL: usize = 20;

pub(super) type StderrTail = Arc<Mutex<VecDeque<String>>>;

impl Supervisor {
    /// Makes `process` the current child and starts its tasks. Returns `None`
    /// (and kills the process) when the lifecycle was cancelled meanwhile.
    pub(super) fn attach(
        &self,
        epoch: u64,
        mut process: LaunchedProcess,
    ) -> Option<(u64, mpsc::UnboundedReceiver<ChildEvent>, StderrTail)> {
        let (writer_tx, writer_rx) = mpsc::unbounded_channel();
        let (events_tx, events_rx) = mpsc::unbounded_channel();
        let (exit_tx, exit_rx) = watch::channel(None);
        let kill = Arc::new(Notify::new());
        let tail: StderrTail = Arc::new(Mutex::new(VecDeque::with_capacity(STDERR_TAIL)));
        let generation = {
            let mut inner = self.lock();
            if inner.epoch != epoch || inner.shutting_down {
                process.control.start_kill();
                self.shared.rt.spawn(async move {
                    process.control.wait().await;
                });
                return None;
            }
            inner.generation += 1;
            inner.child = Some(ChildSlot {
                generation: inner.generation,
                program: process.program.clone(),
                writer: writer_tx,
                events: events_tx.clone(),
                kill: Arc::clone(&kill),
                exit: exit_rx,
                up_since: None,
                ping_in_flight: false,
            });
            inner.generation
        };
        log::info!(
            "bridge child {generation} attached (spawned pid {:?})",
            process.pid
        );
        let rt = &self.shared.rt;
        rt.spawn(write_frames(process.stdin, writer_rx));
        rt.spawn(read_frames(
            self.clone(),
            generation,
            process.stdout,
            events_tx.clone(),
        ));
        rt.spawn(drain_stderr(process.stderr, Arc::clone(&tail)));
        rt.spawn(watch_exit(process.control, kill, exit_tx, events_tx));
        Some((generation, events_rx, tail))
    }
}

async fn write_frames(
    mut stdin: Box<dyn AsyncWrite + Send + Unpin>,
    mut frames: mpsc::UnboundedReceiver<Vec<u8>>,
) {
    while let Some(frame) = frames.recv().await {
        if let Err(err) = async {
            stdin.write_all(&frame).await?;
            stdin.flush().await
        }
        .await
        {
            log::warn!("bridge stdin closed: {err}");
            return;
        }
    }
    // The slot was dropped: closing stdin tells the bridge to exit.
    let _ = stdin.shutdown().await;
}

async fn read_frames(
    supervisor: Supervisor,
    generation: u64,
    stdout: Box<dyn AsyncRead + Send + Unpin>,
    events: mpsc::UnboundedSender<ChildEvent>,
) {
    let mut reader = BufReader::with_capacity(64 * 1024, stdout);
    loop {
        match read_line(&mut reader).await {
            Ok(LineRead::Line(line)) => match decode_envelope(&line) {
                Ok(envelope) => supervisor.route(generation, envelope),
                Err(CorruptFrame(reason)) => {
                    log::error!("bridge stdout corrupt: {reason}");
                    let _ = events.send(ChildEvent::Corrupt(reason));
                    return;
                }
            },
            Ok(LineRead::Oversized) => {
                log::error!("bridge stdout corrupt: a frame exceeds the 8 MiB cap");
                let _ = events.send(ChildEvent::Corrupt(
                    "a frame exceeds the 8 MiB cap".to_owned(),
                ));
                return;
            }
            Ok(LineRead::Eof) => {
                let _ = events.send(ChildEvent::Eof);
                return;
            }
            Err(err) => {
                log::warn!("bridge stdout read failed: {err}");
                let _ = events.send(ChildEvent::Eof);
                return;
            }
        }
    }
}

async fn drain_stderr(stderr: Box<dyn AsyncRead + Send + Unpin>, tail: StderrTail) {
    let mut reader = BufReader::new(stderr);
    let mut buffer = Vec::new();
    loop {
        buffer.clear();
        match reader.read_until(b'\n', &mut buffer).await {
            Ok(0) | Err(_) => return,
            Ok(_) => {
                let line = String::from_utf8_lossy(&buffer).trim_end().to_owned();
                log::info!(target: "python", "{line}");
                let mut tail = tail.lock().unwrap_or_else(PoisonError::into_inner);
                if tail.len() == STDERR_TAIL {
                    tail.pop_front();
                }
                tail.push_back(line);
            }
        }
    }
}

async fn watch_exit(
    mut control: Box<dyn ChildControl>,
    kill: Arc<Notify>,
    exit: watch::Sender<Option<Option<i32>>>,
    events: mpsc::UnboundedSender<ChildEvent>,
) {
    let exited = tokio::select! {
        code = control.wait() => Some(code),
        () = kill.notified() => None,
    };
    let code = match exited {
        Some(code) => code,
        None => {
            control.start_kill();
            control.wait().await
        }
    };
    exit.send_replace(Some(code));
    let _ = events.send(ChildEvent::Exited(code));
}

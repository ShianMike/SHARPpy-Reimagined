//! Read already-written fragments in plan order while the bounded download runs.
use super::Transfer;
use std::fs::File;
use std::io::{self, Read};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Condvar;
use std::time::Duration;

#[derive(Default)]
pub(super) struct Feed {
    pub ready: AtomicBool,
    pub done: AtomicBool,
    pub changed: Condvar,
}

impl Feed {
    pub fn finish(&self) {
        self.done.store(true, Ordering::Release);
        self.changed.notify_all();
    }
}

pub(super) struct Fragments<'a, 'b> {
    transfer: &'a Transfer<'b>,
    feed: &'a Feed,
    index: usize,
    consumed: u64,
    file: Option<File>,
}

impl<'a, 'b> Fragments<'a, 'b> {
    pub fn new(transfer: &'a Transfer<'b>, feed: &'a Feed) -> Self {
        Self {
            transfer,
            feed,
            index: 0,
            consumed: 0,
            file: None,
        }
    }
}

impl Read for Fragments<'_, '_> {
    fn read(&mut self, target: &mut [u8]) -> io::Result<usize> {
        if target.is_empty() {
            return Ok(0);
        }
        loop {
            self.transfer.check().map_err(|_| {
                io::Error::new(io::ErrorKind::ConnectionAborted, "model transfer stopped")
            })?;
            let Some(&range) = self.transfer.plan.get(self.index) else {
                return Ok(0);
            };
            let state = self.transfer.state.lock().unwrap();
            if self.feed.ready.load(Ordering::Acquire) {
                let available = state.sizes[self.index];
                if available < self.consumed {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        "HTTP fragment changed while decoding",
                    ));
                }
                if available > self.consumed {
                    let length = (available - self.consumed).min(target.len() as u64) as usize;
                    drop(state);
                    let file = match &mut self.file {
                        Some(file) => file,
                        empty => empty.insert(File::open(self.transfer.store.part(range))?),
                    };
                    let read = file.read(&mut target[..length])?;
                    if read == 0 {
                        return Err(io::Error::new(
                            io::ErrorKind::UnexpectedEof,
                            "written HTTP fragment is truncated",
                        ));
                    }
                    self.consumed += read as u64;
                    return Ok(read);
                }
                if self.consumed == range.size() {
                    drop(state);
                    self.index += 1;
                    self.consumed = 0;
                    self.file = None;
                    continue;
                }
            }
            if self.feed.done.load(Ordering::Acquire) {
                return Err(io::Error::new(
                    io::ErrorKind::UnexpectedEof,
                    "HTTP transfer ended before the fragment",
                ));
            }
            drop(
                self.feed
                    .changed
                    .wait_timeout(state, Duration::from_millis(50))
                    .unwrap(),
            );
        }
    }
}

"""Per-request model download counters; only active requests occupy memory."""

from collections import deque
from contextlib import contextmanager
from threading import RLock
from time import monotonic

from sharpmod.bridge import params as p

_lock = RLock()
_active = {}


def _id(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 64 \
            or not value.isascii() or not value.isalnum():
        raise p.invalid("progress_id must be 1 to 64 letters or digits")
    return value


class DownloadProgress:
    def __init__(self):
        self.phase = "waiting"
        self.downloaded = 0
        self.samples = deque([(monotonic(), 0)])

    def stage(self, stage, _planned_bytes=0):
        with _lock:
            self.phase = (
                "waiting" if stage in ("locating", "queued", "validating") else
                "downloading" if stage in ("downloading", "retrieving") else
                "processing"
            )

    def receive(self, size):
        with _lock:
            self.phase = "downloading"
            self.downloaded += size
            self._sample()

    def _sample(self):
        now = monotonic()
        # Coalesce fast parallel chunks; retain three seconds plus a baseline.
        if now - self.samples[-1][0] >= 0.1:
            self.samples.append((now, self.downloaded))
        while len(self.samples) > 1 and self.samples[1][0] < now - 3:
            self.samples.popleft()
        return now

    def snapshot(self):
        with _lock:
            now = self._sample()
            start, count = self.samples[0]
            speed = (self.downloaded - count) / max(now - start, 0.1)
            return {
                "stage": self.phase,
                "downloaded_bytes": self.downloaded,
                "bytes_per_second": speed if self.phase == "downloading" else 0,
            }


@contextmanager
def track(progress_id):
    progress = DownloadProgress()
    if progress_id is not None:
        _id(progress_id)
        with _lock:
            if progress_id in _active:
                raise p.invalid("progress_id is already in use")
            _active[progress_id] = progress
    try:
        yield progress
    finally:
        with _lock:
            _active.pop(progress_id, None)


def get_progress(params):
    p.keys(params, ("progress_id",))
    progress_id = _id(params["progress_id"])
    with _lock:
        progress = _active.get(progress_id)
        return (progress or DownloadProgress()).snapshot()

"""Killable process boundary for long-running forecast batch work.

Qt threads keep the interface responsive, but they cannot safely interrupt a
native decoder or a network library blocked below Python.  This module runs the
existing resumable batch CLI in a child process, streams structured progress
back to the owning ``QThread``, and makes cancellation a process operation.
Completed NPZ/JSON pairs remain safe because the extractor writes them
atomically and checkpoints its manifest after every transition.
"""

from __future__ import annotations

from collections import deque
from contextlib import suppress
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

from sharpmod.batch_extract import (
    BATCH_SPEC_VERSION,
    BatchRequest,
    BatchRunResult,
    load_batch_result,
)
from sharpmod.tools.era5_extract import _atomic_write_json


_EVENT_PREFIX = "SHARPMOD_EVENT "
_CANCEL_GRACE_SECONDS = 2.0
_OUTPUT_TAIL_LINES = 30


class IsolatedBatchError(RuntimeError):
    """The child process failed before producing a valid batch result."""


class IsolatedBatchCancelled(IsolatedBatchError):
    """The caller stopped the child process intentionally."""


def _canonical_time(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _request_record(request: BatchRequest) -> dict[str, object]:
    return {
        "id": request.id,
        "model": request.model,
        "lat": request.lat,
        "lon": request.lon,
        "run": _canonical_time(request.run_time),
        "fxx": request.fxx,
        "output": request.output,
        "loc": request.loc,
        "member": request.member,
    }


def _worker_command(
    spec_path: Path,
    output_root: Path,
    manifest_path: Path,
    workers: int,
    disk_cache,
) -> list[str]:
    args = [
        str(spec_path),
        "--output-dir",
        str(output_root),
        "--manifest",
        str(manifest_path),
        "--workers",
        str(workers),
        "--json-progress",
    ]
    if disk_cache is not None:
        args.extend(
            [
                "--cache-root",
                str(Path(disk_cache.root).expanduser().resolve()),
                "--cache-max-bytes",
                str(int(disk_cache.max_bytes)),
                "--cache-max-age-hours",
                str(float(disk_cache.max_age_hours)),
            ]
        )
    if getattr(sys, "frozen", False):
        return [sys.executable, "--model-batch-worker", *args]
    return [sys.executable, "-m", "sharpmod.tools.batch_extract", *args]


class IsolatedBatchRunner:
    """Run one batch in a child process and expose prompt, safe cancellation."""

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None

    def cancel(self) -> None:
        """Request cancellation without waiting on the calling (usually UI) thread."""
        self._cancelled.set()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            with suppress(OSError, ProcessLookupError):
                process.terminate()

    @staticmethod
    def _start_reader(process, lines: queue.Queue) -> threading.Thread:
        def read_output() -> None:
            try:
                if process.stdout is not None:
                    for line in process.stdout:
                        lines.put(line)
            finally:
                lines.put(None)

        thread = threading.Thread(
            target=read_output,
            name="sharpmod-batch-output",
            daemon=True,
        )
        thread.start()
        return thread

    @staticmethod
    def _emit_line(line: str, progress_callback, output_tail) -> None:
        line = line.rstrip("\r\n")
        if line.startswith(_EVENT_PREFIX):
            try:
                event = json.loads(line[len(_EVENT_PREFIX):])
            except json.JSONDecodeError:
                output_tail.append(line)
                return
            if isinstance(event, dict) and progress_callback is not None:
                progress_callback(event)
            return
        if line:
            output_tail.append(line)

    def run(
        self,
        requests,
        *,
        output_dir,
        max_workers: int,
        progress_callback=None,
        disk_cache=None,
    ) -> BatchRunResult:
        """Execute ``requests`` and load the child-owned result manifest."""
        request_list = tuple(requests)
        if not request_list:
            raise IsolatedBatchError("an isolated batch needs at least one request")
        workers = max(1, min(4, int(max_workers)))
        output_root = Path(output_dir).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        nonce = uuid.uuid4().hex
        spec_path = output_root / f".gui-batch-{nonce}.json"
        manifest_path = output_root / "batch-manifest.json"
        _atomic_write_json(
            spec_path,
            {
                "version": BATCH_SPEC_VERSION,
                "requests": [_request_record(request) for request in request_list],
            },
        )
        command = _worker_command(
            spec_path, output_root, manifest_path, workers, disk_cache
        )
        popen_kwargs = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "text": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NO_WINDOW", 0
            )

        process = None
        output_tail = deque(maxlen=_OUTPUT_TAIL_LINES)
        try:
            process = subprocess.Popen(command, **popen_kwargs)
            with self._lock:
                self._process = process
            if self._cancelled.is_set() and process.poll() is None:
                process.terminate()

            lines: queue.Queue[str | None] = queue.Queue()
            reader = self._start_reader(process, lines)
            cancel_started = None
            output_closed = False
            while process.poll() is None or not output_closed:
                try:
                    line = lines.get(timeout=0.1)
                except queue.Empty:
                    line = ...
                if line is None:
                    output_closed = True
                elif line is not ...:
                    self._emit_line(line, progress_callback, output_tail)

                if self._cancelled.is_set() and process.poll() is None:
                    if cancel_started is None:
                        cancel_started = time.monotonic()
                        with suppress(OSError, ProcessLookupError):
                            process.terminate()
                    elif time.monotonic() - cancel_started >= _CANCEL_GRACE_SECONDS:
                        with suppress(OSError, ProcessLookupError):
                            process.kill()
            reader.join(timeout=1.0)
            return_code = process.wait(timeout=1.0)
            if self._cancelled.is_set():
                raise IsolatedBatchCancelled("forecast batch was cancelled")
            if return_code not in (0, 1):
                details = "\n".join(output_tail).strip()
                suffix = f": {details}" if details else ""
                raise IsolatedBatchError(
                    f"forecast batch process exited with code {return_code}{suffix}"
                )
            try:
                return load_batch_result(manifest_path, output_dir=output_root)
            except Exception as exc:
                details = "\n".join(output_tail).strip()
                suffix = f"; child output: {details}" if details else ""
                raise IsolatedBatchError(
                    f"forecast batch produced no valid result: {exc}{suffix}"
                ) from exc
        finally:
            with self._lock:
                if self._process is process:
                    self._process = None
            with suppress(OSError):
                spec_path.unlink()


__all__ = [
    "IsolatedBatchCancelled",
    "IsolatedBatchError",
    "IsolatedBatchRunner",
]

"""Starts and talks to the Qt render worker (``render_worker.py``).

The worker starts on the first render and stays up, so only the first image
pays for importing Qt and SHARPpy. Renders are serialized. A render that runs
past its deadline, or a worker that exits, is reported as a retryable
``RENDER_FAILED`` and the next render starts a fresh worker. The worker is a
child of the bridge, so the Windows job object that holds the bridge holds it
too, and it is stopped when the bridge exits.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from sharpmod.bridge.errors import BridgeError

log = logging.getLogger("sharpmod.bridge")

RENDER_TIMEOUT_S = 120.0
#: Added to the first render of a worker: importing Qt and SHARPpy.
STARTUP_ALLOWANCE_S = 60.0
#: Read at every spawn, so tests can substitute a stand-in worker.
WORKER_ARGS: tuple[str, ...] = (
    "-I",
    "-u",
    "-X",
    "utf8",
    "-m",
    "sharpmod.bridge.render_worker",
)
_STOP_WAIT_S = 2.0


def _crashed() -> BridgeError:
    return BridgeError(
        "RENDER_FAILED", "The sounding renderer stopped unexpectedly.", True
    )


class RenderClient:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen[bytes] | None = None
        self._replies: queue.Queue[dict[str, Any] | None] | None = None
        self._reader: threading.Thread | None = None
        self._next_id = 0
        self._fresh = True

    def _spawn(self) -> None:
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        env["QT_API"] = "pyside6"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        proc = subprocess.Popen(  # noqa: S603 - fixed interpreter and arguments
            [sys.executable, *(["--sounding-render-worker"] if getattr(sys, "frozen", False) else WORKER_ARGS)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            env=env,
            creationflags=flags,
        )
        replies: queue.Queue[dict[str, Any] | None] = queue.Queue()
        stdout = proc.stdout
        assert stdout is not None

        def read() -> None:
            try:
                for raw in stdout:
                    try:
                        replies.put(json.loads(raw.decode("utf-8")))
                    except ValueError:
                        log.warning("render worker wrote an unreadable line")
            finally:
                stdout.close()
                replies.put(None)

        reader = threading.Thread(target=read, name="render-reader", daemon=True)
        reader.start()
        self._proc, self._replies, self._reader, self._fresh = (
            proc,
            replies,
            reader,
            True,
        )
        log.info("render worker started (pid %d)", proc.pid)

    def _stop(self) -> None:
        proc, reader = self._proc, self._reader
        self._proc, self._replies, self._reader = None, None, None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
            proc.wait(timeout=_STOP_WAIT_S)
        except Exception:  # noqa: BLE001 - make sure it is gone
            proc.kill()
            proc.wait()
        if reader is not None:
            reader.join(timeout=_STOP_WAIT_S)

    def close(self) -> None:
        with self._lock:
            self._stop()

    def _await(
        self,
        replies: queue.Queue[dict[str, Any] | None],
        request_id: int,
        timeout: float,
        infile: Path,
    ) -> dict[str, Any]:
        while True:
            try:
                reply = replies.get(timeout=timeout)
            except queue.Empty:
                log.warning(
                    "render of %s passed %.0f s; stopping the worker", infile, timeout
                )
                self._stop()
                raise BridgeError(
                    "RENDER_FAILED",
                    "Drawing the sounding took too long, so it was stopped.",
                    True,
                    {"timeout_s": timeout},
                ) from None
            if reply is None:
                self._stop()
                raise _crashed()
            if reply.get("id") == request_id:
                return reply

    def render(self, infile: Path, outfile: Path, parcel: str, image_mode: str,
               *, view: dict | None = None) -> dict:
        """Draw ``infile`` into ``outfile``; raise ``BridgeError`` on failure."""
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._stop()
                self._spawn()
            proc, replies = self._proc, self._replies
            assert proc is not None and proc.stdin is not None and replies is not None
            self._next_id += 1
            request_id = self._next_id
            frame = {
                "id": request_id,
                "infile": str(infile),
                "outfile": str(outfile),
                "parcel": parcel,
                "image_mode": image_mode,
            }
            if view is not None:
                frame["view"] = view
            timeout = RENDER_TIMEOUT_S + (STARTUP_ALLOWANCE_S if self._fresh else 0.0)
            self._fresh = False
            try:
                proc.stdin.write(json.dumps(frame).encode("utf-8") + b"\n")
                proc.stdin.flush()
            except OSError:
                self._stop()
                raise _crashed() from None
            reply = self._await(replies, request_id, timeout, infile)
            if not reply.get("ok"):
                message = str(reply.get("message") or "unknown error")
                raise BridgeError(
                    "RENDER_FAILED",
                    f"The sounding image could not be drawn: {message}",
                    False,
                )
            return reply.get("view", {})


_CLIENT: RenderClient | None = None
_CLIENT_LOCK = threading.Lock()


def client() -> RenderClient:
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = RenderClient()
            atexit.register(_CLIENT.close)
        return _CLIENT

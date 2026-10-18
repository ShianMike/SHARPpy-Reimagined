"""Read loop, worker pool, and frame writer of the bridge process.

In process mode (``main()`` with no arguments, as ``__main__`` calls it) the
protocol moves onto private duplicates of descriptors 0 and 1 before any
request is read. Descriptor 1 then points at stderr and descriptor 0 at the
null device, so stray prints, native output, and child processes can neither
corrupt the protocol stream nor read protocol input.

In in-process mode (``main(stdin, stdout)``, used by tests) the given binary
streams are used as they are, and no descriptor, ``sys`` stream, or logging
handler is touched.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, BinaryIO

from sharpmod.bridge import handlers, live_cache
from sharpmod.bridge.errors import BridgeError, error_frame, internal_error
from sharpmod.bridge.protocol import (
    MAX_FRAME_BYTES,
    ProtocolError,
    Request,
    check_params,
    decode_request,
    encode_frame,
)

log = logging.getLogger("sharpmod.bridge")

#: Sounding downloads and renders can hold a worker for minutes.
WORKERS = 8
_DRAIN_CHUNK = 64 * 1024
_STD_INPUT_HANDLE = -10 & 0xFFFFFFFF
_STD_OUTPUT_HANDLE = -11 & 0xFFFFFFFF


def _point_std_handles_at_descriptors() -> str | None:
    """Re-point the Windows standard handles at descriptors 0 and 1.

    Returns a warning to log, or ``None`` on success.
    """
    try:
        import ctypes
        import msvcrt

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        set_std_handle = kernel32.SetStdHandle
        set_std_handle.argtypes = (ctypes.c_uint32, ctypes.c_void_p)
        set_std_handle.restype = ctypes.c_int
        for handle_id, fd in ((_STD_OUTPUT_HANDLE, 1), (_STD_INPUT_HANDLE, 0)):
            if not set_std_handle(handle_id, msvcrt.get_osfhandle(fd)):
                return f"SetStdHandle failed (error {ctypes.get_last_error()})"
    except Exception as exc:  # descriptor-level protection still holds
        return f"SetStdHandle failed ({type(exc).__name__}: {exc})"
    return None


def _claim_process_streams() -> tuple[BinaryIO, BinaryIO, str | None]:
    """Move the protocol onto private descriptors (process mode only)."""
    proto_out = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    proto_in = os.fdopen(os.dup(0), "rb")
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    warning = _point_std_handles_at_descriptors() if os.name == "nt" else None
    return proto_in, proto_out, warning


class _Server:
    def __init__(self, proto_in: BinaryIO, proto_out: BinaryIO) -> None:
        self._in = proto_in
        self._out = proto_out
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(
            max_workers=WORKERS, thread_name_prefix="bridge"
        )

    def serve(self) -> int:
        try:
            self._read_loop()
        finally:
            self._pool.shutdown(wait=True, cancel_futures=True)
        return 0

    def _read_loop(self) -> None:
        while True:
            line = self._in.readline(MAX_FRAME_BYTES + 2)
            if not line:
                return  # EOF: the parent is gone
            if not line.endswith(b"\n"):
                if len(line) < MAX_FRAME_BYTES + 2:
                    log.warning("request stream ended inside a frame; exiting")
                    return
                self._drain_oversized()
                self._reply_error(None, _oversized())
                continue
            if self._dispatch(line) == "shutdown":
                return

    def _drain_oversized(self) -> None:
        while True:
            chunk = self._in.readline(_DRAIN_CHUNK)
            if not chunk or chunk.endswith(b"\n"):
                return

    def _dispatch(self, line: bytes) -> str | None:
        try:
            request = decode_request(line)
            if request.method != "shutdown" and request.method not in handlers.HANDLERS:
                raise ProtocolError(
                    BridgeError(
                        "METHOD_NOT_FOUND",
                        f"The installed sharpmod does not support {request.method!r}. "
                        "Update sharpmod or the app so their versions match.",
                        False,
                        {"method": request.method},
                    ),
                    request.id,
                )
            check_params(request)
        except ProtocolError as exc:
            log.warning("rejected request frame: %s", exc.error.message)
            self._reply_error(exc.request_id, exc.error)
            return None
        if request.method == "shutdown":
            self._write({"id": request.id, "result": {"ok": True}})
            return "shutdown"
        if request.method == "hello":
            # The liveness ping is answered on the read loop, so a pool busy with
            # long downloads or renders never looks like a hung bridge.
            self._run(request)
            return None
        self._pool.submit(self._run, request)
        return None

    def _run(self, request: Request) -> None:
        handler = handlers.HANDLERS[request.method]
        try:
            result = live_cache.call(request.method, request.params, handler)
            frame: dict[str, Any] = {"id": request.id, "result": result}
        except BridgeError as err:
            frame = error_frame(request.id, err)
        except Exception:
            log.exception("unexpected error in %s", request.method)
            frame = error_frame(request.id, internal_error(request.method))
        try:
            data = encode_frame(frame)
        except (TypeError, ValueError):
            log.exception("result of %s could not be encoded", request.method)
            data = encode_frame(error_frame(request.id, internal_error(request.method)))
        self._write_bytes(data)

    def _reply_error(self, request_id: int | None, err: BridgeError) -> None:
        self._write(error_frame(request_id, err))

    def _write(self, frame: dict[str, Any]) -> None:
        self._write_bytes(encode_frame(frame))

    def _write_bytes(self, data: bytes) -> None:
        with self._lock:
            try:
                view = memoryview(data)
                while view:
                    view = view[self._out.write(view):]
            except (BrokenPipeError, OSError, ValueError):
                log.warning(
                    "protocol stream closed; dropped a %d-byte frame", len(data)
                )


def _oversized() -> BridgeError:
    return BridgeError(
        "BRIDGE_PROTOCOL", "Request frame exceeds the 8 MiB limit.", False
    )


def main(stdin: BinaryIO | None = None, stdout: BinaryIO | None = None) -> int:
    """Serve requests until EOF or ``shutdown``; return the exit code."""
    if (stdin is None) != (stdout is None):
        raise TypeError("pass both stdin and stdout, or neither")
    if stdin is None or stdout is None:
        proto_in, proto_out, warning = _claim_process_streams()
        logging.basicConfig(
            stream=sys.stderr,
            level=logging.INFO,
            format="%(levelname)s %(name)s: %(message)s",
            force=True,
        )
        if warning:
            log.warning("%s; descriptor-level protection still holds", warning)
        log.info("bridge ready (pid %d)", os.getpid())
    else:
        proto_in, proto_out = stdin, stdout
    return _Server(proto_in, proto_out).serve()

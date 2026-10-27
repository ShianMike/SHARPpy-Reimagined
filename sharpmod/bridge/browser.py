"""Loopback-only development connection for the React browser preview."""

from __future__ import annotations

import argparse
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from sharpmod.bridge import params as p
from sharpmod.bridge import live_cache
from sharpmod.bridge.errors import BridgeError, error_frame, internal_error
from sharpmod.bridge.handlers import HANDLERS, health, hello
from sharpmod.bridge import cancellation
import threading
from sharpmod.bridge.protocol import (
    MAX_FRAME_BYTES,
    ProtocolError,
    Request,
    check_params,
    decode_request,
    encode_frame,
)
from sharpmod.bridge.public import COMMANDS, bridge_request, camel, public_result

log = logging.getLogger(__name__)


def backend_health(since: str) -> dict[str, Any]:
    facts = hello({"protocol_version": 1})
    return {
        "app": {
            "name": "SHARPpy Reimagined",
            "version": facts["sharpmod_version"],
            "tauriVersion": "browser",
            "buildProfile": "debug",
            "logFile": "stderr",
        },
        "bridge": {
            "status": "up",
            "protocolVersion": facts["protocol_version"],
            "pid": facts["pid"],
            "since": since,
            "restarts": 0,
        },
        "python": {
            "version": facts["python_version"],
            "executable": facts["python_executable"],
            "sharpmodVersion": facts["sharpmod_version"],
        },
        "scientificBackend": camel(health({})["scientific_backend"]),
        "checkedAt": p.iso(p.now()),
    }


class BrowserServer(ThreadingHTTPServer):
    # shortcut: loopback development only; use a managed API server before hosting.
    def __init__(self, address: tuple[str, int], origin: str) -> None:
        super().__init__(address, BrowserHandler)
        self.origins = {origin, origin.replace("127.0.0.1", "localhost")}
        self.since = p.iso(p.now())
        self.admission = threading.BoundedSemaphore(24)


class BrowserHandler(BaseHTTPRequestHandler):
    server: BrowserServer

    def do_POST(self) -> None:
        command = "browser request"
        request_id = None
        try:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                raise p.invalid("Content-Length must be an integer") from None
            if not 0 < length <= MAX_FRAME_BYTES:
                raise p.invalid("request body must be between 1 byte and 8 MiB")
            self.connection.settimeout(15)
            # Unread POST bytes can reset the socket before Windows receives an error.
            body = self.rfile.read(length)
            host = f"127.0.0.1:{self.server.server_port}"
            if self.path != "/__backend" or self.headers.get("Host") != host:
                self.send_error(403)
                return
            if self.headers.get("Origin") not in (None, *self.server.origins):
                self.send_error(403)
                return
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise p.invalid("Content-Type must be application/json")
            request = decode_request(body)
            command = request.method
            if "requestId" in request.params:
                request_id = cancellation.token(request.params.pop("requestId"))
            if command == "cancel_request":
                p.keys(request.params, ())
                result = cancellation.cancel({"request_id": request_id})
            if command == "get_backend_health":
                p.keys(request.params, ())
                result = backend_health(self.server.since)
            elif command in COMMANDS or command == "open_sounding":
                method, params = bridge_request(command, request.params)
                check_params(Request(request.id, method, params))
                if method == "soundings.progress":
                    result = public_result(command, HANDLERS[method](params))
                else:
                    if not self.server.admission.acquire(blocking=False):
                        raise BridgeError("INTERNAL", "The backend is busy. Try again shortly.", True)
                    registered = False
                    try:
                        event = cancellation.register(request_id) if request_id is not None else threading.Event()
                        registered = request_id is not None
                        with cancellation.scope(event):
                            result = public_result(command, live_cache.call(method, params, HANDLERS[method]))
                            cancellation.check()
                    finally:
                        if registered:
                            cancellation.release(request_id)
                        self.server.admission.release()
            elif command != "cancel_request":
                raise BridgeError(
                    "METHOD_NOT_FOUND",
                    f"{command} is unavailable in browser development.",
                    False,
                )
            binary = isinstance(result, bytes)
            self._reply(
                200,
                result if binary else encode_frame(result),
                "application/octet-stream" if binary else "application/json",
            )
            if command != "get_backend_health":
                log.info("live %s completed", command)
        except ProtocolError as exc:
            self._error(command, exc.error, request_id)
        except BridgeError as exc:
            self._error(command, exc, request_id)
        except TimeoutError as exc:
            self._error(command, p.invalid(str(exc)), request_id)
        except Exception:
            log.exception("live %s failed", command)
            self._error(command, internal_error(command), request_id)

    def _error(self, command: str, error: BridgeError, request_id=None) -> None:
        cancelled = isinstance(error, cancellation.Cancelled)
        log.log(logging.INFO if cancelled else logging.WARNING,
                "live command=%s request_id=%s code=%s cancelled=%s: %s",
                command, request_id, error.code, cancelled, error.message)
        status = (
            400
            if error.code in ("INVALID_ARGUMENT", "BRIDGE_PROTOCOL", "METHOD_NOT_FOUND")
            else 502
        )
        payload = error_frame(None, error)["error"]
        payload["context"] = {"operation": command, "provider": "python-bridge"}
        if request_id is not None:
            payload.setdefault("details", {})["request_id"] = request_id
        self._reply(status, encode_frame(payload), "application/json")

    def _reply(self, status: int, data: bytes, content_type: str) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # The browser may close while a provider finishes.

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Log the operation once, without a health-poll access log.


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    with BrowserServer(("127.0.0.1", 0), args.origin) as server:
        print(f"SHARPPY_BROWSER_READY {server.server_port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()

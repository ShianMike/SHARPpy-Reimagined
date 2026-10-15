"""The Qt render worker: sounding images drawn by sharpmod's own renderer.

``python -m sharpmod.bridge.render_worker`` reads one JSON request per line and
answers each with one JSON line::

    {"id": 1, "infile": "...", "outfile": "...", "parcel": "MU", "image_mode": "hd"}
    {"id": 1, "ok": true}        or        {"id": 1, "ok": false, "message": "..."}

Qt widgets must live on a process's main thread, so rendering happens here,
one sounding at a time, and not in the bridge's worker threads. It uses
:func:`sharpmod.rendering.cli.render` with the legacy locator map omitted.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any

MAX_MESSAGE_CHARS = 240


def _short(exc: BaseException) -> str:
    cause = getattr(exc, "cause", None) or exc
    text = (
        str(cause).strip().splitlines()[0]
        if str(cause).strip()
        else type(cause).__name__
    )
    return text[:MAX_MESSAGE_CHARS]


def _reply(stream, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
    view = memoryview(data)
    while view:
        view = view[stream.write(view) :]


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QT_API", "pyside6")
    from sharpmod.bridge.server import _claim_process_streams

    proto_in, proto_out, warning = _claim_process_streams()
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )
    log = logging.getLogger("sharpmod.render_worker")
    if warning:
        log.warning("%s; descriptor-level protection still holds", warning)
    from sharpmod.rendering import cli  # Qt, fonts, and the SHARPpy widgets, once

    log.info("render worker ready (pid %d)", os.getpid())
    for line in proto_in:
        try:
            request = json.loads(line.decode("utf-8"))
            request_id = request["id"]
            infile, outfile = str(request["infile"]), str(request["outfile"])
            parcel, mode = str(request["parcel"]), str(request["image_mode"])
        except Exception:  # noqa: BLE001 - a bad frame gets an error, never a crash
            log.warning("render worker: unreadable request %r", line[:200])
            _reply(
                proto_out, {"id": None, "ok": False, "message": "unreadable request"}
            )
            continue
        try:
            view_result: dict[str, Any] = {}
            cli.render(infile, outfile, parcel=parcel, image_mode=mode,
                       view=request.get("view"), view_result=view_result,
                       show_locator=False)
            reply: dict[str, Any] = {"id": request_id, "ok": True, "view": view_result}
        except Exception as exc:  # noqa: BLE001 - every failure is reported
            log.exception("render of %s failed", infile)
            reply = {"id": request_id, "ok": False, "message": _short(exc)}
        _reply(proto_out, reply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

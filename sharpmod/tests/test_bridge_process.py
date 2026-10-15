"""The sharpmod bridge as a real process: framing, Qt isolation, stream protection.

Each test starts a fresh interpreter with the argv the Rust supervisor uses.
Pipes are closed explicitly (``Popen`` as a context manager), because the
suite turns ``ResourceWarning`` into a failure.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLAGS = ["-I", "-u", "-X", "utf8"]
BRIDGE_ARGV = [sys.executable, *FLAGS, "-m", "sharpmod.bridge"]
TIMEOUT = 120
SHUTDOWN = b'{"id":99,"method":"shutdown","params":{}}\n'


def _strict_frame(line: bytes) -> dict:
    def reject(token):
        raise ValueError(token)

    frame = json.loads(line.decode("utf-8"), parse_constant=reject)
    assert isinstance(frame, dict) and set(frame) in ({"id", "result"}, {"id", "error"})
    return frame


def _session(argv: list[str], requests: list[bytes]) -> tuple[list[dict], str, int]:
    """Send ``requests``, read one reply each, then shut the bridge down."""
    with subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, cwd=ROOT) as proc:
        try:
            for request in requests:
                proc.stdin.write(request)
            proc.stdin.flush()
            frames = [_strict_frame(proc.stdout.readline()) for _ in requests]
            out, err = proc.communicate(SHUTDOWN, timeout=TIMEOUT)
        except BaseException:
            proc.kill()
            proc.communicate()
            raise
        frames.extend(_strict_frame(line) for line in out.splitlines())
        return frames, err.decode("utf-8", "replace"), proc.returncode


def test_bridge_process_serves_every_method_and_exits_cleanly():
    frames, stderr, code = _session(BRIDGE_ARGV, [
        b'{"id":1,"method":"hello","params":{"protocol_version":1}}\n',
        b'{"id":2,"method":"health","params":{}}\n',
        b'{"id":3,"method":"stations.catalog","params":{}}\n',
    ])
    assert code == 0, stderr
    by_id = {frame["id"]: frame for frame in frames}
    assert sorted(by_id) == [1, 2, 3, 99]
    assert by_id[1]["result"]["protocol_version"] == 1
    assert by_id[2]["result"]["scientific_backend"]["status"] in {"ok", "unavailable"}
    catalog = by_id[3]["result"]
    ids = [station["id"] for station in catalog["stations"]]
    assert len(ids) == len(set(ids)) == 933
    assert catalog["origin"] == "bundled" and catalog["dropped_count"] == 0
    assert by_id[99] == {"id": 99, "result": {"ok": True}}


def test_handlers_load_no_qt_module():
    code = textwrap.dedent("""
        import json, sys
        from sharpmod.bridge import handlers, server
        handlers.hello({"protocol_version": 1})
        handlers.health({})
        handlers.stations_catalog({})
        qt = sorted(m for m in sys.modules
                    if m.split(".")[0] in ("PySide6", "shiboken6", "qtpy"))
        print(json.dumps(qt))
    """)
    result = subprocess.run([sys.executable, *FLAGS, "-c", code], cwd=ROOT,
                            capture_output=True, timeout=TIMEOUT, check=False)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert json.loads(result.stdout.decode().strip().splitlines()[-1]) == []


HARNESS = textwrap.dedent("""
    import os, subprocess, sys
    from sharpmod.bridge import handlers, server

    def noisy(params):
        print("print-noise")
        os.write(1, b"fd-noise\\n")
        subprocess.run([sys.executable, "-c", "print('child-noise')"],
                       stdin=subprocess.DEVNULL, check=True)
        return {"quiet": True}

    handlers.HANDLERS["test.noise"] = noisy
    raise SystemExit(server.main())
""")


def test_stray_output_goes_to_stderr_not_the_protocol_stream():
    frames, stderr, code = _session(
        [sys.executable, *FLAGS, "-c", HARNESS],
        [b'{"id":1,"method":"test.noise","params":{}}\n'],
    )
    assert code == 0, stderr
    assert frames == [{"id": 1, "result": {"quiet": True}},
                      {"id": 99, "result": {"ok": True}}]
    for noise in ("print-noise", "fd-noise", "child-noise"):
        assert noise in stderr

"""Fast process-boundary contracts with no network or real child process."""

from __future__ import annotations

from datetime import datetime, timezone
import io
import json
from pathlib import Path

import pytest

from sharpmod.batch_extract import MANIFEST_SCHEMA, MANIFEST_VERSION, BatchRequest
from sharpmod import gui_batch_process as isolated


RUN = datetime(2026, 9, 10, 0, tzinfo=timezone.utc)


class _CompletedProcess:
    def __init__(self, output=""):
        self.stdout = io.StringIO(output)
        self.returncode = 0
        self.terminated = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.returncode = -9


def _request():
    return BatchRequest(
        "oun",
        "gfs",
        35.22,
        -97.44,
        RUN,
        fxx=3,
        output="oun.npz",
        loc="KOUN",
    )


def test_runner_streams_framed_json_and_loads_child_manifest(
    tmp_path, monkeypatch
):
    launched = {}

    def fake_popen(command, **kwargs):
        launched["command"] = command
        launched["kwargs"] = kwargs
        spec = json.loads(Path(command[3]).read_text(encoding="utf-8"))
        manifest_path = Path(command[command.index("--manifest") + 1])
        records = [
            {**record, "status": "completed", "resumed": False}
            for record in spec["requests"]
        ]
        manifest_path.write_text(
            json.dumps(
                {
                    "schema": MANIFEST_SCHEMA,
                    "version": MANIFEST_VERSION,
                    "job_id": "child-job",
                    "requests": records,
                    "summary": {},
                }
            ),
            encoding="utf-8",
        )
        event = 'SHARPMOD_EVENT {"event":"completed","request_id":"oun"}\n'
        return _CompletedProcess(event)

    monkeypatch.setattr(isolated.subprocess, "Popen", fake_popen)
    events = []

    result = isolated.IsolatedBatchRunner().run(
        [_request()],
        output_dir=tmp_path,
        max_workers=1,
        progress_callback=events.append,
    )

    assert result.completed == 1
    assert result.items[0].output_path == tmp_path / "oun.npz"
    assert events == [{"event": "completed", "request_id": "oun"}]
    assert launched["command"][:3] == [
        isolated.sys.executable,
        "-m",
        "sharpmod.tools.batch_extract",
    ]
    assert not list(tmp_path.glob(".gui-batch-*.json"))


def test_cancel_before_spawn_immediately_stops_child(tmp_path, monkeypatch):
    process = _CompletedProcess()
    process.returncode = None
    monkeypatch.setattr(isolated.subprocess, "Popen", lambda *_a, **_kw: process)
    runner = isolated.IsolatedBatchRunner()
    runner.cancel()

    with pytest.raises(isolated.IsolatedBatchCancelled):
        runner.run([_request()], output_dir=tmp_path, max_workers=1)

    assert process.terminated is True
    assert runner._process is None


def test_frozen_worker_command_routes_back_through_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(isolated.sys, "frozen", True, raising=False)

    command = isolated._worker_command(
        tmp_path / "spec.json",
        tmp_path,
        tmp_path / "manifest.json",
        2,
        None,
    )

    assert command[:2] == [isolated.sys.executable, "--model-batch-worker"]

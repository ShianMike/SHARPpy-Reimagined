"""Focused GUI workflow tests for portable case building and exact replay."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import numpy.ma as ma
from qtpy.QtWidgets import QMainWindow, QPlainTextEdit, QWidget
from sharppy.sharptab import prof_collection, profile

from sharpmod.case_replay import create_case_package, load_case_package
from sharpmod.gui_case_replay import CaseReplayWorkspace


UTC = timezone.utc
T0 = datetime(2024, 5, 20, 18, tzinfo=UTC)
T1 = T0 + timedelta(minutes=10)


def _collection():
    raw = profile.create_profile(
        profile="raw",
        pres=ma.array([1000.0, 900.0, 800.0]),
        hght=ma.array([100.0, 1000.0, 2000.0]),
        tmpc=ma.array([24.0, 16.0, 8.0]),
        dwpc=ma.array([18.0, 10.0, 1.0]),
        wdir=ma.array([170.0, 210.0, 240.0]),
        wspd=ma.array([10.0, 25.0, 40.0]),
        omeg=ma.masked_all(3),
        location="KOUN",
        date=T0,
        latitude=35.22,
        missing=-9999.0,
    )
    result = prof_collection.ProfCollection({"control": [raw]}, [T0])
    result.setMeta("loc", "KOUN")
    result.setMeta("model", "HRRR")
    result.setMeta("run", T0 - timedelta(hours=1))
    result.setMeta("observed", False)
    return result


class _Viewer(QWidget):
    def __init__(self, collections):
        super().__init__()
        self.prof_collections = list(collections)
        self.prof_ids = [f"profile-{index}" for index in range(len(collections))]
        self.pc_idx = 0
        self.deviant = "right"

    def setProfileCollection(self, profile_id):  # noqa: N802 - upstream API
        self.pc_idx = self.prof_ids.index(profile_id)

    def updateProfs(self):  # noqa: N802 - upstream API
        self.update()


class _Window(QMainWindow):
    def __init__(self, collections):
        super().__init__()
        self.spc_widget = _Viewer(collections)
        self.setCentralWidget(self.spc_widget)

    def addProfileCollection(self, collection, **_kwargs):  # noqa: N802
        self.spc_widget.prof_collections.append(collection)
        self.spc_widget.prof_ids.append(
            f"profile-{len(self.spc_widget.prof_ids)}"
        )


class _Host:
    def __init__(self, win, collections):
        self.win = win
        self.collections = collections
        self.notes = QPlainTextEdit()
        self.notes.setPlainText("Archive review notes")
        self.scenario_workspace = SimpleNamespace(book=None)
        self.verification_workspace = SimpleNamespace(pairs=())
        self.wind_workspace = SimpleNamespace(profiles=())
        self.populate_calls = 0

    def _collections(self):
        return list(self.collections)

    @staticmethod
    def _focused_index():
        return 0

    def _window(self):
        return self.win

    def _populate_controls(self):
        self.populate_calls += 1


def test_current_case_build_reopens_offline_and_replays_only_exact_times(
    qt_app, tmp_path
):
    original = _collection()
    win = _Window([original])
    host = _Host(win, [original])
    workspace = CaseReplayWorkspace(host)
    archive = tmp_path / "radar.png"
    archive.write_bytes(b"archived radar bytes")
    root = None
    try:
        workspace.refresh()
        workspace.kind.setCurrentIndex(workspace.kind.findData("radar-image"))
        workspace.event_time.setText(T1.isoformat().replace("+00:00", "Z"))
        workspace.available_time.setText(T1.isoformat().replace("+00:00", "Z"))
        workspace._new_asset(
            source_url=archive.resolve().as_uri(), path=archive, status="complete"
        )

        root, manifest, files = workspace._prepare_case()
        by_id = {item.asset_id: item for item in manifest.assets}
        assert by_id["sounding-session"].status == "complete"
        assert by_id["sounding-session"].available_time is None
        assert "notes" in by_id
        assert any(item.kind == "radar-image" for item in manifest.assets)

        package = create_case_package(tmp_path / "case.sharpmod-case", manifest, files)
        loaded = load_case_package(package)
        assert loaded.asset_bytes("sounding-session").startswith(b"{")
        assert loaded.asset_bytes("notes") == b"Archive review notes"

        workspace._loaded_ready(workspace._token, loaded)
        assert workspace.replay_time.count() == 2
        assert workspace._clock.current == T0
        workspace._open_soundings()
        assert len(workspace._case_collections) == 1
        assert host.populate_calls == 1
        assert workspace._case_collections[0].getCurrentDate() == T0

        workspace.replay_time.setCurrentIndex(1)
        assert "No packaged sounding exists at exact replay time" in workspace.status.text()
        assert workspace._case_collections[0].getCurrentDate() == T0

        workspace.replay_time.setCurrentIndex(0)
        workspace.training.setChecked(True)
        assert "sounding-session" not in workspace._visible_ids()
        assert not workspace.open_soundings.isEnabled()
        assert "availability time is unknown" in workspace.training_limitations.text()
    finally:
        workspace.shutdown()
        workspace.close()
        win.close()
        if root is not None:
            shutil.rmtree(root, ignore_errors=True)


def test_ordinary_session_state_excludes_loaded_bytes_and_local_paths(qt_app, tmp_path):
    original = _collection()
    win = _Window([original])
    host = _Host(win, [original])
    workspace = CaseReplayWorkspace(host)
    private_path = Path(tmp_path / "private-radar.bin")
    private_path.write_bytes(b"private payload")
    try:
        workspace.refresh()
        workspace._new_asset(
            source_url=private_path.resolve().as_uri(),
            path=private_path,
            status="complete",
        )
        encoded = json.dumps(workspace.session_state())

        assert str(private_path) not in encoded
        assert "private payload" not in encoded
        assert "_external" not in encoded
        assert set(json.loads(encoded)) == {
            "name",
            "include_current",
            "training",
            "speed_ms",
            "mode",
            "replay_split",
        }
    finally:
        workspace.shutdown()
        workspace.close()
        win.close()

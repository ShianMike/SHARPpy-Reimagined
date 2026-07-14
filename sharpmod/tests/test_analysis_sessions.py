"""Portable analysis-session files and bounded undo/redo history."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import numpy.ma as ma
import pytest
from qtpy.QtWidgets import QApplication, QMainWindow, QMenu, QStatusBar

from sharppy.sharptab import prof_collection, profile

from sharpmod.sessions import (
    AnalysisHistory,
    SessionFormatError,
    build_session,
    install_history_hooks,
    read_session,
    restore_collection,
    snapshot_collection,
    write_session,
)
from sharpmod import gui, gui_picker


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _collection(*, temperature=20.0):
    valid = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
    raw = profile.create_profile(
        profile="raw",
        pres=ma.array([1000.0, 900.0, 800.0], mask=[0, 0, 0]),
        hght=ma.array([100.0, 1000.0, 2000.0], mask=[0, 0, 0]),
        tmpc=ma.array([temperature, 12.0, 5.0], mask=[0, 0, 0]),
        dwpc=ma.array([15.0, 8.0, -1.0], mask=[0, 1, 0]),
        wdir=ma.array([180.0, 210.0, 240.0], mask=[0, 0, 0]),
        wspd=ma.array([10.0, 25.0, 40.0], mask=[0, 0, 0]),
        omeg=ma.array([-0.1, -0.2, -0.3], mask=[0, 0, 1]),
        location="TEST",
        date=valid,
        latitude=35.2,
        missing=-9999.0,
    )
    raw.surface_relative_vorticity = 0.002
    collection = prof_collection.ProfCollection({"member": [raw]}, [valid])
    collection.setMeta("loc", "TEST")
    collection.setMeta("observed", False)
    collection.setMeta("run", valid)
    collection.setMeta("nested", {"times": [valid], "pair": (1, 2)})
    return collection


def _active_profile(collection):
    return collection._profs[collection._highlight][collection._prof_idx]


def test_collection_snapshot_round_trip_preserves_portable_analysis_state():
    original = _collection()
    original.modify(1, tmpc=10.5, dwpc=7.0)
    _active_profile(original).surface_relative_vorticity = 0.002
    _active_profile(original).srwind = (20.0, 5.0, -10.0, 8.0)
    payload = snapshot_collection(original)

    restored = restore_collection(payload)
    prof = _active_profile(restored)

    np.testing.assert_allclose(prof.tmpc.filled(np.nan), [20.0, 10.5, 5.0])
    assert ma.getmaskarray(prof.dwpc).tolist() == [False, False, False]
    assert ma.getmaskarray(prof.omeg).tolist() == [False, False, True]
    assert prof.surface_relative_vorticity == pytest.approx(0.002)
    assert tuple(prof.srwind) == pytest.approx((20.0, 5.0, -10.0, 8.0))
    assert restored.getMeta("run") == original.getMeta("run")
    assert restored.getMeta("nested")["pair"] == (1, 2)
    assert restored._mod_therm == original._mod_therm
    assert 0 in restored._orig_profs


def test_session_file_round_trip_is_versioned_and_atomic(tmp_path):
    path = tmp_path / "analysis.sharpmod-session"
    document = build_session(
        [_collection(), _collection(temperature=25.0)],
        active_collection=1,
        ui_state={"deviant": "left", "parcel_types": ["MU", "ML"]},
    )

    written = write_session(path, document)
    loaded = read_session(path)

    assert written == str(path)
    assert loaded["version"] == 1
    assert loaded["active_collection"] == 1
    assert loaded["ui_state"]["parcel_types"] == ["MU", "ML"]
    assert len(loaded["collections"]) == 2
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        json.dumps({"format": "sharpmod-analysis-session", "version": 999,
                    "collections": []}),
        json.dumps({"format": "wrong", "version": 1, "collections": []}),
    ],
)
def test_session_reader_rejects_malformed_or_unsupported_documents(
        tmp_path, payload):
    path = tmp_path / "bad.sharpmod-session"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(SessionFormatError):
        read_session(path)


class _FakeWidget:
    def __init__(self, collection):
        self.prof_collections = [collection]
        self.pc_idx = 0
        self.updated = 0
        self.focused = 0

    def updateProfs(self):  # noqa: N802 - vendored API shape
        self.updated += 1

    def setFocus(self):  # noqa: N802 - vendored API shape
        self.focused += 1


def test_history_undo_redo_restores_exact_collection_and_notifies():
    widget = _FakeWidget(_collection())
    history = AnalysisHistory(widget, limit=5)
    events = []
    history.add_listener(lambda: events.append(
        (history.can_undo(), history.can_redo())))
    before = snapshot_collection(widget.prof_collections[0])
    widget.prof_collections[0].modify(0, tmpc=30.0)
    after = snapshot_collection(widget.prof_collections[0])
    history.record("Edit sounding level", 0, before, after)

    assert history.can_undo() and not history.can_redo()
    assert history.undo() == "Edit sounding level"
    assert float(_active_profile(widget.prof_collections[0]).tmpc[0]) == 20.0
    assert history.can_redo()
    assert history.redo() == "Edit sounding level"
    assert float(_active_profile(widget.prof_collections[0]).tmpc[0]) == 30.0
    assert widget.updated == 2
    assert widget.focused == 2
    assert events


def test_history_new_edit_clears_redo_and_limit_is_bounded():
    widget = _FakeWidget(_collection())
    history = AnalysisHistory(widget, limit=2)
    for value in (21.0, 22.0, 23.0):
        before = snapshot_collection(widget.prof_collections[0])
        widget.prof_collections[0].modify(0, tmpc=value)
        after = snapshot_collection(widget.prof_collections[0])
        history.record("Edit", 0, before, after)
    assert history.undo_depth == 2
    history.undo()
    assert history.can_redo()

    before = snapshot_collection(widget.prof_collections[0])
    widget.prof_collections[0].modify(0, tmpc=24.0)
    history.record(
        "Different edit", 0, before,
        snapshot_collection(widget.prof_collections[0]))
    assert not history.can_redo()


def test_history_ignores_unchanged_operations():
    widget = _FakeWidget(_collection())
    history = AnalysisHistory(widget)
    state = snapshot_collection(widget.prof_collections[0])
    history.record("No-op", 0, state, state)
    assert not history.can_undo()


def test_installed_hooks_capture_existing_vendored_mutation_path():
    class FakeSPCWidget(_FakeWidget):
        def modifyProf(self, idx, kwargs):  # noqa: N802
            self.prof_collections[self.pc_idx].modify(idx, **kwargs)
            self.updateProfs()
            self.setFocus()

    install_history_hooks(FakeSPCWidget)
    widget = FakeSPCWidget(_collection())
    widget._sharpmod_history = AnalysisHistory(widget)

    widget.modifyProf(0, {"tmpc": 31.0})

    assert widget._sharpmod_history.can_undo()
    widget._sharpmod_history.undo()
    assert float(_active_profile(widget.prof_collections[0]).tmpc[0]) == 20.0


def test_analysis_actions_install_history_and_standard_shortcuts(qt_app):
    win = QMainWindow()
    win.setStatusBar(QStatusBar(win))
    win._test_filemenu = QMenu("File", win)
    win.menuBar().addMenu(win._test_filemenu)
    win.spc_widget = _FakeWidget(_collection())

    class Controller:
        def _open_analysis_session(self):
            pass

    gui._install_analysis_actions(win, Controller())

    assert win.spc_widget._sharpmod_history is win._sharpmod_history
    assert win._sharpmod_undo_action.shortcut().toString() == "Ctrl+Z"
    assert win._sharpmod_redo_action.shortcut().toString() == "Ctrl+Y"
    assert not win._sharpmod_undo_action.isEnabled()

    history = win._sharpmod_history
    before = snapshot_collection(win.spc_widget.prof_collections[0])
    win.spc_widget.prof_collections[0].modify(0, tmpc=28.0)
    history.record(
        "Edit sounding level", 0, before,
        snapshot_collection(win.spc_widget.prof_collections[0]))
    assert win._sharpmod_undo_action.isEnabled()
    assert "Edit sounding level" in win._sharpmod_undo_action.text()


def test_find_window_menu_returns_a_stable_qt_owned_menu(qt_app):
    """Menu discovery must not return a deleted temporary PySide wrapper."""
    win = QMainWindow()
    win.menuBar().addMenu("&File")

    menu = gui._find_window_menu(win, "File")

    assert menu is not None
    menu.addSeparator()
    assert len(menu.actions()) == 1


def test_picker_opens_all_session_collections_in_one_new_viewer(
        qt_app, tmp_path, monkeypatch):
    path = tmp_path / "two.sharpmod-session"
    write_session(path, build_session(
        [_collection(), _collection(temperature=26.0)],
        active_collection=0,
        ui_state={"window_title": "Restored Analysis", "deviant": "right"},
    ))

    class FakeHistory:
        cleared = False

        def clear(self):
            self.cleared = True

    class FakeSPC(_FakeWidget):
        def __init__(self, first):
            super().__init__(first)
            self.prof_ids = ["first"]
            self.deviant = "right"
            self.convective = None
            self.index_board = None

        def setProfileCollection(self, prof_id):  # noqa: N802
            self.pc_idx = self.prof_ids.index(prof_id)
            self.updateProfs()

        def toggleVector(self, deviant):  # noqa: N802
            self.deviant = deviant

    class FakeWindow(QMainWindow):
        def __init__(self, first):
            super().__init__()
            self.spc_widget = FakeSPC(first)
            self._sharpmod_history = FakeHistory()

        def addProfileCollection(self, collection, **_kwargs):  # noqa: N802
            self.spc_widget.prof_collections.append(collection)
            self.spc_widget.prof_ids.append(
                f"collection-{len(self.spc_widget.prof_ids)}")

    created = []

    def fake_compose(_config, first, _controller, **_kwargs):
        win = FakeWindow(first)
        created.append(win)
        return win

    monkeypatch.setattr(gui_picker, "compose_interactive", fake_compose)

    class Status:
        def showMessage(self, *_args):
            pass

    class Owner:
        def __init__(self):
            self._viewers = []
            self._status = Status()

        def _config(self):
            return object()

        def statusBar(self):
            return self._status

    owner = Owner()
    gui.PickerWindow._open_analysis_session(owner, str(path))

    assert len(created) == 1
    assert len(created[0].spc_widget.prof_collections) == 2
    assert created[0].windowTitle() == "Restored Analysis"
    assert created[0]._sharpmod_history.cleared
    assert owner._viewers == created

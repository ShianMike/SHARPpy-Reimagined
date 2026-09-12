"""Portable analysis-session files and bounded undo/redo history."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import numpy.ma as ma
import pytest
from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QDockWidget,
    QMainWindow,
    QMenu,
    QStatusBar,
    QTabWidget,
    QWidget,
)

from sharppy.sharptab import prof_collection, profile

from sharpmod.sessions import (
    AnalysisHistory,
    SESSION_VERSION,
    SessionFormatError,
    build_session,
    install_history_hooks,
    read_session,
    restore_collection,
    snapshot_collection,
    write_session,
)
from sharpmod import export_paths, gui, gui_picker, gui_sessions, gui_viewer
from sharpmod.profile_timeline import append_collection, combine_collections


def _collection(*, temperature=20.0, valid=None):
    valid = valid or datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
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
    assert loaded["version"] == SESSION_VERSION == 2
    assert loaded["active_collection"] == 1
    assert loaded["ui_state"]["parcel_types"] == ["MU", "ML"]
    assert len(loaded["collections"]) == 2
    assert not list(tmp_path.glob("*.tmp"))


def test_session_reader_migrates_v1_and_writer_never_emits_it(tmp_path):
    old_document = build_session(
        [_collection()],
        ui_state={"deviant": "left", "legacy_choice": (1, 2)},
    )
    old_document["version"] = 1
    # Version 1 predates collection-level locator descriptors.
    for collection in old_document["collections"]:
        collection.pop("locator_overlays", None)
    source = tmp_path / "legacy.sharpmod-session"
    source.write_text(json.dumps(old_document), encoding="utf-8")

    migrated = read_session(source)

    assert migrated["version"] == SESSION_VERSION
    assert migrated["ui_state"]["legacy_choice"] == (1, 2)

    target = tmp_path / "rewritten.sharpmod-session"
    write_session(target, old_document)
    assert json.loads(target.read_text(encoding="utf-8"))["version"] == 2


def test_collection_snapshot_keeps_overlay_provenance_not_heavy_payload():
    collection = _collection()

    class FakeOverlayRaster:
        key = "radar"
        title = "Radar reflectivity"
        short_name = "Radar"
        valid_time = datetime(2026, 7, 14, 12, tzinfo=timezone.utc)
        retrieved_at = datetime(2026, 7, 14, 12, 1, tzinfo=timezone.utc)
        bounds = (-105.0, -90.0, 30.0, 42.0)
        update_interval_s = 120.0
        opacity = 0.7
        byte_count = 25_000_000
        image_bytes = b"SECRET_RASTER_BYTES_MUST_NOT_ENTER_SESSION"

    collection.setMeta("sharpmod_locator_overlays", [FakeOverlayRaster()])

    payload = snapshot_collection(collection)
    encoded = json.dumps(payload)

    assert "SECRET_RASTER_BYTES_MUST_NOT_ENTER_SESSION" not in encoded
    assert "image_bytes" not in encoded
    assert payload["locator_overlays"][0]["key"] == "radar"
    assert payload["locator_overlays"][0]["byte_count"] == 25_000_000

    restored = restore_collection(payload)
    descriptors = restored.getMeta("sharpmod_locator_overlay_descriptors")
    assert descriptors[0]["valid_time"] == FakeOverlayRaster.valid_time


class _SessionView(QWidget):
    def __init__(self, *, scale=1.0, fit=False, parent=None):
        super().__init__(parent)
        self.scale = scale
        self.fit = fit

    def current_scale(self):
        return self.scale

    def is_fit_mode(self):
        return self.fit

    def zoom_to(self, scale):
        self.fit = False
        self.scale = float(scale)

    def fit_to_window(self):
        self.fit = True


class _SessionMap:
    def __init__(self, bounds):
        self.bounds = tuple(bounds)
        self.restored = None

    def view_bounds(self):
        return self.bounds

    def set_extent(self, *bounds, **kwargs):
        self.restored = (tuple(bounds), dict(kwargs))


class _SessionController(QWidget):
    def __init__(self, bounds):
        super().__init__()
        self._map = _SessionMap(bounds)
        self.restored_hook = None

    def session_map_state(self):
        return {"active_map": "station"}

    def restore_session_map_state(self, state):
        self.restored_hook = dict(state)


class _SessionWorkspace:
    def __init__(self, state=None):
        self.state = dict(state or {})
        self.restored = None

    def session_state(self):
        return dict(self.state)

    def restore_session_state(self, state):
        self.restored = dict(state)


class _SessionSPC:
    def __init__(self, collection):
        self.prof_collections = [collection]
        self.pc_idx = 0
        self.updated = 0
        self.prof_ids = ["only"]
        self.deviant = "right"
        self.convective = None
        self.index_board = None
        self.right_inset = "STP STATS"

    def setProfileCollection(self, prof_id):  # noqa: N802
        self.pc_idx = self.prof_ids.index(prof_id)

    def toggleVector(self, deviant):  # noqa: N802
        self.deviant = deviant

    def updateProfs(self):  # noqa: N802
        self.updated += 1


def _session_window(controller, *, scale, panel_index, dock_visible):
    win = QMainWindow(controller)
    win.spc_widget = _SessionSPC(_collection())
    win.setCentralWidget(_SessionView(scale=scale, fit=False, parent=win))
    tabs = QTabWidget()
    tabs.addTab(QWidget(), "Trends")
    tabs.addTab(QWidget(), "Notes")
    tabs.setCurrentIndex(panel_index)
    dock = QDockWidget("Analysis Workspace", win)
    dock.setObjectName("analysisWorkspace")
    dock.setWidget(tabs)
    win.addDockWidget(Qt.RightDockWidgetArea, dock)
    dock.setVisible(dock_visible)
    win._test_dock = dock
    win._test_tabs = tabs
    return win


def test_viewer_workspace_state_round_trip_is_defensive_and_complete(
    qt_app, monkeypatch
):
    source_controller = _SessionController((-105.0, -90.0, 30.0, 42.0))
    source = _session_window(
        source_controller, scale=1.75, panel_index=1, dock_visible=False
    )
    source.setWindowTitle("Operational comparison")
    source.spc_widget.deviant = "left"
    source.spc_widget.right_inset = "SHIP"
    source._sharpmod_analysis_workspace = _SessionWorkspace(
        {
            "current_tab": "Notes",
            "notes": "Watch the cap near 700 hPa.",
        }
    )
    source.spc_widget.prof_collections[0].setMeta(
        "sharpmod_locator_overlay_descriptors",
        [{"key": "risk", "valid_from": "2026-07-14T12:00:00+00:00"}],
    )

    state = gui._viewer_session_ui_state(source)

    target_controller = _SessionController((-80.0, -70.0, 20.0, 30.0))
    target = _session_window(
        target_controller, scale=1.0, panel_index=0, dock_visible=True
    )
    target._sharpmod_analysis_workspace = _SessionWorkspace()

    def show_panel(win, key):
        win.spc_widget.right_inset = key
        return True

    monkeypatch.setattr(gui_viewer, "show_sounding_panel", show_panel)
    gui._apply_viewer_session_state(target, 0, state)

    assert target.windowTitle() == "Operational comparison"
    assert target.spc_widget.deviant == "left"
    assert target.spc_widget.right_inset == "SHIP"
    assert target.centralWidget().scale == pytest.approx(1.75)
    assert not target.centralWidget().fit
    assert target._test_dock.isHidden()
    assert target._test_tabs.currentIndex() == 1
    assert target._sharpmod_analysis_workspace.restored["notes"].startswith(
        "Watch the cap"
    )
    assert target_controller.restored_hook == {"active_map": "station"}
    assert target_controller._map.restored == (
        (-105.0, -90.0, 30.0, 42.0),
        {"pad": 0.0},
    )
    # Collection payloads are now the one descriptor source of truth. Early-v2
    # UI copies remain readable, but current UI state does not duplicate them.
    assert "locator_overlays" not in state


def test_station_map_session_restore_keeps_exact_bounds(qt_app):
    from sharpmod.gui_maps import StationMapWidget

    widget = StationMapWidget([])
    expected = (-105.25, -90.75, 30.5, 42.25)
    widget.restore_view_bounds(expected)
    assert widget.view_bounds() == pytest.approx(expected)

    widget.restore_view_bounds(widget.view_bounds())
    assert widget.view_bounds() == pytest.approx(expected)


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        json.dumps(
            {"format": "sharpmod-analysis-session", "version": 999, "collections": []}
        ),
        json.dumps({"format": "wrong", "version": 1, "collections": []}),
    ],
)
def test_session_reader_rejects_malformed_or_unsupported_documents(tmp_path, payload):
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
    history.add_listener(
        lambda: events.append((history.can_undo(), history.can_redo()))
    )
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


def test_history_undo_redo_keeps_timeline_hours_streamed_after_edit():
    first_valid = datetime(2026, 7, 14, 15, tzinfo=timezone.utc)
    second_valid = first_valid - timedelta(hours=3)
    timeline = combine_collections((_collection(valid=first_valid),))
    widget = _FakeWidget(timeline)
    history = AnalysisHistory(widget, limit=5)

    before = snapshot_collection(timeline)
    timeline.modify(0, tmpc=31.0)
    history.record("Edit sounding level", 0, before, snapshot_collection(timeline))

    append_collection(
        timeline,
        _collection(temperature=17.0, valid=second_valid),
    )
    assert timeline._dates == [second_valid, first_valid]

    assert history.undo() == "Edit sounding level"
    restored = widget.prof_collections[0]
    assert restored is timeline
    assert restored._dates == [second_valid, first_valid]
    assert restored.getCurrentDate() == first_valid
    assert restored.getMeta("timeline_count") == 2
    assert float(restored._profs[restored._highlight][0].tmpc[0]) == 17.0
    assert float(restored._profs[restored._highlight][1].tmpc[0]) == 20.0

    assert history.redo() == "Edit sounding level"
    restored = widget.prof_collections[0]
    assert restored is timeline
    assert restored._dates == [second_valid, first_valid]
    assert restored.getCurrentDate() == first_valid
    assert restored.getMeta("timeline_count") == 2
    assert float(restored._profs[restored._highlight][0].tmpc[0]) == 17.0
    assert float(restored._profs[restored._highlight][1].tmpc[0]) == 31.0


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
        "Different edit", 0, before, snapshot_collection(widget.prof_collections[0])
    )
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
        "Edit sounding level",
        0,
        before,
        snapshot_collection(win.spc_widget.prof_collections[0]),
    )
    assert win._sharpmod_undo_action.isEnabled()
    assert "Edit sounding level" in win._sharpmod_undo_action.text()


def test_session_save_default_and_output_use_dedicated_export_folder(
        qt_app, tmp_path, monkeypatch):
    application = tmp_path / "application"
    application.mkdir()
    monkeypatch.setattr(export_paths, "application_root", lambda: application)
    chosen = []

    def accept_default(_parent, _caption, start, _filter):
        chosen.append(Path(start))
        return start, "SHARPpy Analysis Session (*.sharpmod-session)"

    monkeypatch.setattr(
        gui_sessions.QFileDialog, "getSaveFileName", accept_default
    )
    win = QMainWindow()
    win.setStatusBar(QStatusBar(win))
    win._test_filemenu = QMenu("File", win)
    win.menuBar().addMenu(win._test_filemenu)
    win.spc_widget = _FakeWidget(_collection())
    monkeypatch.setattr(
        gui_sessions,
        "_find_window_menu",
        lambda _window, title: win._test_filemenu if title == "File" else None,
    )

    class Controller:
        _settings = None

        def _open_analysis_session(self):
            pass

    gui_sessions._install_analysis_actions(win, Controller())
    win._sharpmod_save_session_action.trigger()
    qt_app.processEvents()

    assert len(chosen) == 1
    assert chosen[0].parent == application / "rendered_soundings"
    assert chosen[0].is_file()

    # A prior one-off destination contributes only its basename.  Its parent
    # must not become the next dialog's default.
    one_off = tmp_path / "user" / "Desktop" / "chosen.sharpmod-session"
    win._sharpmod_session_path = str(one_off)
    win._sharpmod_save_session_action.trigger()
    qt_app.processEvents()

    assert chosen[1] == application / "rendered_soundings" / one_off.name
    assert chosen[1].is_file()
    win.close()


def test_find_window_menu_returns_a_stable_qt_owned_menu(qt_app):
    """Menu discovery must not return a deleted temporary PySide wrapper."""
    win = QMainWindow()
    win.menuBar().addMenu("&File")

    menu = gui._find_window_menu(win, "File")

    assert menu is not None
    menu.addSeparator()
    assert len(menu.actions()) == 1


def test_picker_opens_all_session_collections_in_one_new_viewer(
    qt_app, tmp_path, monkeypatch
):
    path = tmp_path / "two.sharpmod-session"
    first_collection = _collection()
    second_collection = _collection(temperature=26.0)
    first_collection.setMeta("sharpmod_locator_overlay_spec", "risk:torn")
    second_collection.setMeta("sharpmod_locator_overlay_spec", "hrrr:refc")
    write_session(
        path,
        build_session(
            [first_collection, second_collection],
            active_collection=0,
            ui_state={"window_title": "Restored Analysis", "deviant": "right"},
        ),
    )

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
                f"collection-{len(self.spc_widget.prof_ids)}"
            )

    created = []
    composed_specs = []

    def fake_compose(_config, first, _controller, **_kwargs):
        composed_specs.append(first.getMeta("sharpmod_locator_overlay_spec"))
        win = FakeWindow(first)
        created.append(win)
        return win

    monkeypatch.setattr(gui_picker, "compose_interactive", fake_compose)
    fetched_specs = []
    monkeypatch.setattr(
        gui_picker,
        "_start_locator_overlay_fetch",
        lambda _win, _collection, **kwargs: fetched_specs.append(kwargs["spec"]),
    )

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
    assert composed_specs == ["risk:torn"]
    assert fetched_specs == ["hrrr:refc"]

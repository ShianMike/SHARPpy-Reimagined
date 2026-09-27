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
from sharpmod import (
    export_paths,
    gui,
    gui_picker,
    gui_sessions,
    gui_viewer,
    locator_presentation,
)
from sharpmod.ensemble_members import EnsembleAcquisition, MemberFailure
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


@pytest.mark.parametrize("link_mode", (
    locator_presentation.LINK_PINNED,
    locator_presentation.LINK_FOLLOW,
))
def test_collection_snapshot_preserves_locator_presentation(link_mode):
    collection = _collection()
    state = locator_presentation.LocatorPresentation(
        link_mode=link_mode,
        extent_mode=locator_presentation.EXTENT_CUSTOM,
        custom_bounds=(-99.0, 33.0, -95.0, 37.0),
        main_bounds=(-110.0, 25.0, -80.0, 48.0),
        selected_area=(-101.0, 31.0, -91.0, 39.0),
        scale_units="imperial",
    )
    locator_presentation.set_collection_presentation(collection, state)

    restored = restore_collection(snapshot_collection(collection))

    assert locator_presentation.collection_presentation(restored) == state


def test_collection_snapshot_preserves_locator_overlay_failure_status():
    collection = _collection()
    locator_presentation.set_overlay_status(
        collection, "spc_outlook", "unavailable",
        family="risk", product="torn", detail="Provider unavailable",
    )

    restored = restore_collection(snapshot_collection(collection))
    status = locator_presentation.overlay_statuses(restored)["spc_outlook"]

    assert status["state"] == "unavailable"
    assert status["detail"] == "Provider unavailable"
    assert status["updated_at"].tzinfo is not None


def test_collection_snapshot_round_trip_preserves_partial_ensemble_ledger():
    collection = _collection()
    collection._profs = {
        "c00": collection._profs["member"],
        "p01": collection._profs["member"],
    }
    collection._highlight = "c00"
    acquisition = EnsembleAcquisition(
        ("c00", "p01", "p02"),
        ("c00", "p01"),
        (MemberFailure("p02", "failed", "provider timeout"),),
        (
            {"request_id": "gefs-c00", "member": "c00"},
            {"request_id": "gefs-p01", "member": "p01"},
            {"request_id": "gefs-p02", "member": "p02"},
        ),
    )
    acquisition.attach(collection)

    restored = restore_collection(snapshot_collection(collection))
    ledger = EnsembleAcquisition.from_collection(restored)

    assert ledger.requested_members == ("c00", "p01", "p02")
    assert ledger.loaded_members == ("c00", "p01")
    assert ledger.failed_members == ("p02",)
    assert [item["member"] for item in ledger.specs_for_retry()] == ["p02"]


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


def test_floatable_analysis_window_placement_round_trips(qt_app):
    """A separate analysis window remains separate when a session reopens."""
    source_controller = _SessionController((-105.0, -90.0, 30.0, 42.0))
    target_controller = _SessionController((-105.0, -90.0, 30.0, 42.0))
    source = _session_window(
        source_controller,
        scale=1.0,
        panel_index=1,
        dock_visible=True,
    )
    target = _session_window(
        target_controller,
        scale=1.0,
        panel_index=0,
        dock_visible=False,
    )
    features = (
        QDockWidget.DockWidgetClosable
        | QDockWidget.DockWidgetMovable
        | QDockWidget.DockWidgetFloatable
    )
    source._test_dock.setFeatures(features)
    target._test_dock.setFeatures(features)
    try:
        source._test_dock.setFloating(True)
        source._test_dock.setGeometry(120, 90, 900, 650)
        qt_app.processEvents()
        states = gui_sessions._dock_session_state(source)
        dock_state = states["analysisWorkspace"]
        assert dock_state["floating_geometry"] == [120, 90, 900, 650]
        assert "floating_screen" in dock_state

        # Pretend the session came from a removed second screen. Restore must
        # map it into the current primary work area rather than honoring an
        # unreachable native position.
        dock_state["floating_geometry"] = [4200, 200, 1200, 800]
        dock_state["floating_screen"] = {
            "name": "Removed monitor",
            "available": [3840, 0, 1920, 1080],
            "device_pixel_ratio": 1.0,
        }

        gui_sessions._restore_dock_session_state(target, states)
        qt_app.processEvents()

        assert target._test_dock.isFloating()
        assert not target._test_dock.isHidden()
        assert target._test_tabs.currentIndex() == 1
        from sharpmod.gui_workspace_layouts import (
            current_screen_specs,
            safe_floating_geometry,
        )

        expected = safe_floating_geometry(
            dock_state["floating_geometry"],
            saved_screen=dock_state["floating_screen"],
            current_screens=current_screen_specs(),
        )
        actual = target._test_dock.geometry()
        assert [actual.x(), actual.y(), actual.width(), actual.height()] == expected
    finally:
        source.close()
        target.close()


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


def test_successful_session_save_records_history_only_after_the_actual_write(
        qt_app, tmp_path, monkeypatch):
    from qtpy.QtCore import QSettings
    from sharpmod.recent_destinations import RecentDestinationStore

    saved = tmp_path / "analysis.sharpmod-session"
    window = QMainWindow()
    window.setStatusBar(QStatusBar(window))
    menu = window.menuBar().addMenu("File")
    window.spc_widget = _FakeWidget(_collection())

    class Controller:
        _settings = QSettings(str(tmp_path / "history.ini"), QSettings.IniFormat)

        def _open_analysis_session(self):
            pass

        def _record_recent_destination(self, kind, target, **metadata):
            assert Path(target).is_file(), "history must follow the successful session write"
            RecentDestinationStore(self._settings).remember(kind, target, **metadata)

    controller = Controller()
    monkeypatch.setattr(gui_sessions, "_find_window_menu", lambda _window, title: menu if title == "File" else None)
    monkeypatch.setattr(gui_sessions.QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(saved), ""))
    try:
        gui_sessions._install_analysis_actions(window, controller)
        window._sharpmod_save_session_action.trigger()
        entries = RecentDestinationStore(controller._settings).load()
        assert saved.is_file() and len(entries) == 1
        assert entries[0].kind == "session" and entries[0].last_used is not None
        assert "1 sounding ·" in entries[0].details
    finally:
        window.close()
        window.deleteLater()


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

def test_recovery_snapshots_are_bounded_and_listable(tmp_path, monkeypatch):
    from sharpmod import export_paths
    from sharpmod.sessions import list_recovery_snapshots, write_recovery_snapshot

    app_root = tmp_path / "approot"
    monkeypatch.setattr(export_paths, "application_root", lambda: app_root)
    document = build_session([_collection()])
    for _ in range(7):
        write_recovery_snapshot(document, limit=5)
    snapshots = list_recovery_snapshots()
    assert len(snapshots) == 5
    assert all(s.path.is_file() for s in snapshots)
    created = [s.created for s in snapshots]
    assert created == sorted(created, reverse=True)
    assert all(s.soundings == 1 for s in snapshots)
    assert len({s.path.name for s in snapshots}) == 5
    assert snapshots[0].identities == ("TEST · 2026-07-14 12:00Z",)
    assert "Decoded profile data is embedded" in snapshots[0].summary()


def test_recovery_survives_an_interrupted_write(tmp_path, monkeypatch):
    from sharpmod import export_paths, sessions
    from sharpmod.sessions import list_recovery_snapshots, write_recovery_snapshot

    app_root = tmp_path / "approot"
    monkeypatch.setattr(export_paths, "application_root", lambda: app_root)
    document = build_session([_collection()])
    first = write_recovery_snapshot(document)
    assert read_session(first).get("collections")

    def interrupted_replace(_source, _target):
        raise OSError("simulated interruption")

    monkeypatch.setattr(sessions.os, "replace", interrupted_replace)
    with pytest.raises(OSError, match="simulated interruption"):
        write_recovery_snapshot(document)

    snapshots = list_recovery_snapshots()
    assert [s.path for s in snapshots] == [first]
    assert not list(first.parent.glob("*.tmp"))


def test_session_status_shows_dirty_path_and_save_time(qt_app, tmp_path, monkeypatch):
    from qtpy.QtCore import QSettings
    window = QMainWindow()
    window.setStatusBar(QStatusBar(window))
    menu = window.menuBar().addMenu("File")
    window.spc_widget = _FakeWidget(_collection())

    class Controller:
        _settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)

        def _open_analysis_session(self):
            pass

    monkeypatch.setattr(gui_sessions, "_find_window_menu", lambda _window, title: menu if title == "File" else None)
    saved = tmp_path / "analysis.sharpmod-session"
    monkeypatch.setattr(gui_sessions.QFileDialog, "getSaveFileName", lambda *_a, **_k: (str(saved), ""))
    try:
        gui_sessions._install_analysis_actions(window, Controller())
        label = window._sharpmod_session_label
        assert "Unsaved" in label.text()
        window._sharpmod_save_session_action.trigger()
        qt_app.processEvents()
        assert "analysis.sharpmod-session" in label.text()
        assert "Unsaved" not in label.text()
        history = window._sharpmod_history
        before = snapshot_collection(window.spc_widget.prof_collections[0])
        window.spc_widget.prof_collections[0].modify(0, tmpc=28.0)
        history.record("Edit sounding level", 0, before, snapshot_collection(window.spc_widget.prof_collections[0]))
        assert "Unsaved changes" in label.text()
    finally:
        window.close()
        window.deleteLater()


def test_automatic_recovery_uses_current_workspace_and_explicit_save_cleans_it(
    qt_app, tmp_path, monkeypatch
):
    from qtpy.QtCore import QSettings

    app_root = tmp_path / "approot"
    monkeypatch.setattr(export_paths, "application_root", lambda: app_root)
    window = QMainWindow()
    window.setStatusBar(QStatusBar(window))
    menu = window.menuBar().addMenu("File")
    window.spc_widget = _FakeWidget(_collection())
    saved = tmp_path / "analysis.sharpmod-session"

    class Controller:
        _settings = QSettings(str(tmp_path / "recovery.ini"), QSettings.IniFormat)

        def _open_analysis_session(self, _path=None, *, recovered=False):
            return recovered

    monkeypatch.setattr(
        gui_sessions, "_find_window_menu",
        lambda _window, title: menu if title == "File" else None,
    )
    monkeypatch.setattr(
        gui_sessions.QFileDialog, "getSaveFileName",
        lambda *_args, **_kwargs: (str(saved), ""),
    )
    try:
        gui_sessions._install_analysis_actions(window, Controller())
        window._sharpmod_recovery_timer.stop()
        recovery = window._sharpmod_write_recovery(force=True)
        assert recovery.is_file()
        assert read_session(recovery)["collections"]
        assert "Latest recovery snapshot" in window._sharpmod_session_label.toolTip()

        window._sharpmod_save_session_action.trigger()
        qt_app.processEvents()
        assert saved.is_file()
        assert not recovery.exists()
        assert window._sharpmod_recovery_paths == []
    finally:
        window.close()
        window.deleteLater()


def test_recovery_choice_summarizes_context_before_replacing_workspace(
    qt_app, tmp_path, monkeypatch
):
    from qtpy.QtCore import QSettings
    from sharpmod.sessions import write_recovery_snapshot

    app_root = tmp_path / "approot"
    monkeypatch.setattr(export_paths, "application_root", lambda: app_root)
    settings = QSettings(str(tmp_path / "restore.ini"), QSettings.IniFormat)
    recovery = write_recovery_snapshot(
        build_session([_collection()]), settings=settings
    )
    opened = []
    questions = []
    window = QMainWindow()
    window.setStatusBar(QStatusBar(window))
    menu = window.menuBar().addMenu("File")
    window.spc_widget = _FakeWidget(_collection(temperature=25.0))

    class Controller:
        _settings = settings

        def _open_analysis_session(self, path=None, *, recovered=False):
            opened.append((path, recovered))
            return True

    monkeypatch.setattr(
        gui_sessions, "_find_window_menu",
        lambda _window, title: menu if title == "File" else None,
    )
    monkeypatch.setattr(
        gui_sessions.QInputDialog, "getItem",
        lambda _parent, _title, _label, choices, *_args: (choices[0], True),
    )

    def confirm(_parent, _title, text, *_args):
        questions.append(text)
        return gui_sessions.QMessageBox.Yes

    monkeypatch.setattr(gui_sessions.QMessageBox, "question", confirm)
    try:
        window.show()
        gui_sessions._install_analysis_actions(window, Controller())
        window._sharpmod_recovery_timer.stop()
        window._sharpmod_recover_session_action.trigger()
        qt_app.processEvents()

        assert opened == [(str(recovery), True)]
        assert questions and "TEST · 2026-07-14 12:00Z" in questions[0]
        assert "Decoded profile data is embedded" in questions[0]
        assert "replace the current workspace" in questions[0]
        assert not window.isVisible()
    finally:
        window.close()
        window.deleteLater()

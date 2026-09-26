"""Recent discovery delegates to existing actions and explains missing paths."""

import gc
from datetime import datetime, timezone
import weakref
from types import SimpleNamespace

import pytest
from qtpy.QtCore import QCoreApplication, QEvent, QSettings, Qt
from qtpy.QtTest import QTest
from qtpy.QtWidgets import QListWidget, QMainWindow

from sharpmod.gui_recents import RecentDestinationsDialog, install_recent_destinations
from sharpmod.recent_destinations import RecentDestinationStore, SETTINGS_KEY
from sharpmod.saved_locations import SavedLocationStore


class _Owner(QMainWindow):
    def __init__(self, tmp_path):
        super().__init__()
        self.resize(1000, 650)
        self._settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
        self._recent_location_store = SavedLocationStore(self._settings, key="locations/recent")
        self.calls = []
        self.open_success = True

    def _safe_locations(self, store):
        return store.load()

    def _open_file(self, path):
        self.calls.append(("file", path))
        return self.open_success

    def _open_analysis_session(self, path):
        self.calls.append(("session", path))
        return self.open_success

    def _apply_recent_location(self, point):
        self.calls.append(("location", point))


@pytest.fixture
def recent_owner(qt_app, tmp_path):
    owner = _Owner(tmp_path)
    yield owner
    owner.close()
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qt_app.processEvents()


def test_metadata_search_details_times_and_native_exact_once_open(recent_owner, tmp_path, qt_app):
    owner = recent_owner
    path = tmp_path / "available.spc"
    path.touch()
    RecentDestinationStore(owner._settings).remember("file", path, details="Location: OAX · Model: Archive",
                                                    now=datetime(2026, 9, 16, 8, tzinfo=timezone.utc))
    dialog = RecentDestinationsDialog(owner)
    dialog.show()
    dialog.activateWindow()
    dialog.search.setFocus()
    dialog.search.setText("OAX archive")
    qt_app.processEvents()
    assert dialog.results.count() == 1
    assert str(path) in dialog.results.item(0).text()
    assert "2026-09-16 08:00:00 UTC" in dialog.results.item(0).text()
    QTest.keyClick(dialog.search, Qt.Key_Return)
    assert owner.calls == [("file", str(path))]
    assert not dialog.isVisible()


def test_missing_file_is_retained_with_explicit_replacement_and_deletion_race(
        recent_owner, tmp_path, monkeypatch):
    owner = recent_owner
    missing, replacement = tmp_path / "moved.spc", tmp_path / "replacement.spc"
    replacement.touch()
    owner._settings.setValue("recent_files", [str(missing)])
    dialog = RecentDestinationsDialog(owner)
    dialog.show()
    assert dialog.results.count() == 1
    assert "missing" in dialog.results.item(0).text()
    assert "not recorded" in dialog.results.item(0).text()
    assert "Choose replacement" in dialog.status.text()
    assert not dialog.open_button.isEnabled() and dialog.recover.isVisibleTo(dialog)
    dialog.open_selected()
    assert owner.calls == []
    from sharpmod import gui_recents

    monkeypatch.setattr(gui_recents.QFileDialog, "getOpenFileName", lambda *_a, **_k: (str(replacement), ""))
    dialog.choose_replacement()
    assert owner.calls == [("file", str(replacement))]
    assert owner._settings.value("recent_files", [], list) == [str(missing)]
    # Merely choosing a replacement does not discard the old path; the actual
    # successful reader owns remembering the new one.
    timed = RecentDestinationStore(owner._settings)
    timed.remember("file", replacement)
    race = RecentDestinationsDialog(owner, query="replacement")
    replacement.unlink()
    race.open_selected()
    assert owner.calls == [("file", str(replacement))]
    assert "missing" in race.status.text()


def test_session_and_point_dispatch_and_failed_open_remain_recoverable(recent_owner, tmp_path):
    owner = recent_owner
    session = tmp_path / "analysis.sharpmod-session"
    session.touch()
    store = RecentDestinationStore(owner._settings)
    store.remember("session", session, details="2 soundings")
    store.remember("location", label="Manila", lat=14.6, lon=120.98)
    dialog = RecentDestinationsDialog(owner, kind="session")
    dialog.show()
    owner.open_success = False
    dialog.open_selected()
    assert owner.calls == [("session", str(session))]
    assert dialog.isVisible() and "could not be opened" in dialog.status.text()
    point = RecentDestinationsDialog(owner, kind="location", query="Manila")
    assert point.results.count() == 1 and point.open_button.text() == "Use point"
    point.open_selected()
    assert owner.calls[-1][0] == "location"
    assert owner.calls[-1][1].lat == 14.6 and owner.calls[-1][1].lon == 120.98


def test_corrupt_history_keeps_legacy_recovery_and_does_not_overwrite(recent_owner, tmp_path):
    owner = recent_owner
    owner._settings.setValue(SETTINGS_KEY, "broken-json")
    owner._settings.setValue("recent_files", [str(tmp_path / "missing.spc")])
    dialog = RecentDestinationsDialog(owner)
    assert dialog.results.count() == 1
    assert "could not be read" in dialog.status.text()
    assert owner._settings.value(SETTINGS_KEY, "", str) == "broken-json"


def test_missing_recent_file_is_not_silently_hidden(qt_app, tmp_path):
    from sharpmod.gui_picker import PickerWindow

    settings = QSettings(str(tmp_path / "missing-recents.ini"), QSettings.IniFormat)
    missing = str(tmp_path / "missing.spc")
    settings.setValue("recent_files", [missing])
    owner = SimpleNamespace(_settings=settings, _recent_list=QListWidget())
    try:
        PickerWindow._load_recent_files(owner)
        assert owner._recent_list.count() == 1
        item = owner._recent_list.item(0)
        assert item.data(Qt.UserRole) == missing
        assert "missing" in item.text()
    finally:
        owner._recent_list.deleteLater()


def test_shortcut_idempotence_dialog_bound_and_owner_cleanup(qt_app, tmp_path):
    owner = _Owner(tmp_path)
    menu = owner.menuBar().addMenu("File")
    action = install_recent_destinations(owner, menu)
    assert install_recent_destinations(owner, menu) is action
    assert action.shortcut().toString() == "Ctrl+Shift+R"
    owner.show()
    owner.activateWindow()
    qt_app.processEvents()
    QTest.keyClick(owner, Qt.Key_R, Qt.ControlModifier | Qt.ShiftModifier)
    qt_app.processEvents()
    assert owner._recent_destinations_controller.dialog.isVisible()
    owner._recent_destinations_controller.dialog.reject()
    for _ in range(3):
        action.trigger()
        owner._recent_destinations_controller.dialog.reject()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert len(owner.findChildren(RecentDestinationsDialog)) == 1
    owner_ref = weakref.ref(owner)
    dialog_ref = weakref.ref(owner._recent_destinations_controller.dialog)
    owner.close()
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qt_app.processEvents()
    del menu, action, owner
    gc.collect()
    assert owner_ref() is None and dialog_ref() is None


def test_one_large_recent_row_can_scroll_to_its_last_used_time(recent_owner, qt_app, tmp_path):
    from sharpmod import gui_theme

    try:
        gui_theme.apply_theme(qt_app, color_style="standard", text_scale=200)
        path = tmp_path / ("long-missing-filename-" * 6 + ".spc")
        RecentDestinationStore(recent_owner._settings).remember("file", path,
                                                               details="Actual recorded identifying details " * 8)
        dialog = RecentDestinationsDialog(recent_owner)
        dialog.show()
        qt_app.processEvents()
        assert dialog.results.count() == 1
        assert dialog.results.visualItemRect(dialog.results.item(0)).height() > dialog.results.viewport().height()
        scrollbar = dialog.results.verticalScrollBar()
        assert scrollbar.maximum() > 0, "a single wrapped row must not trap its last-used line below the fold"
        scrollbar.setValue(scrollbar.maximum())
        qt_app.processEvents()
        assert dialog.results.visualItemRect(dialog.results.item(0)).top() < 0
        assert dialog.recover.isVisibleTo(dialog) and not dialog.open_button.isEnabled()
    finally:
        gui_theme.apply_theme(qt_app, color_style="standard")

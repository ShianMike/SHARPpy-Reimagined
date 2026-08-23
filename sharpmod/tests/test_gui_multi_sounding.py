"""Multiple-sounding viewer regressions."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sharpmod import gui, gui_picker, render
from sharpmod.tests._examples import examples_dir


class _Viewer:
    def __init__(self):
        self.added = []
        self.titles = []
        self.shown = 0
        self.raised = 0
        self.activated = 0
        self.spc_widget = SimpleNamespace(prof_collections=["collection-1"])

    def isVisible(self):
        return True

    def addProfileCollection(self, collection, **kwargs):
        self.added.append((collection, kwargs))
        self.spc_widget.prof_collections.append(collection)

    def setWindowTitle(self, title):
        self.titles.append(title)

    def showNormal(self):
        self.shown += 1

    def raise_(self):
        self.raised += 1

    def activateWindow(self):
        self.activated += 1


def test_show_sounding_adds_to_active_viewer(monkeypatch):
    viewer = _Viewer()
    picker = SimpleNamespace(
        _viewers=[viewer],
        _combine_soundings_enabled=lambda: True,
        _prune_closed_viewers=lambda: None,
    )
    monkeypatch.setattr(
        gui_picker,
        "compose_interactive",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("must reuse the active viewer")),
    )
    monkeypatch.setattr(
        gui_picker, "_fill_profile_metadata", lambda *_args, **_kwargs: None)

    result = gui.PickerWindow._show_sounding(
        picker, "collection-2", "KOUN", title="Second")

    assert result is viewer
    assert viewer.added == [(
        "collection-2",
        {"focus": True, "check_integrity": False},
    )]
    assert picker._viewers == [viewer]
    assert viewer.titles[-1] == "SHARPpy Reimagined — 2 Soundings"
    assert (viewer.shown, viewer.raised, viewer.activated) == (1, 1, 1)


def test_disabled_combine_mode_composes_a_new_viewer(monkeypatch):
    old_viewer = _Viewer()
    new_viewer = SimpleNamespace()
    picker = SimpleNamespace(
        _viewers=[old_viewer],
        _combine_soundings_enabled=lambda: False,
        _prune_closed_viewers=lambda: None,
        _config=lambda: "config",
    )
    monkeypatch.setattr(
        gui_picker,
        "compose_interactive",
        lambda config, collection, controller, **kwargs: new_viewer,
    )

    # Stop after composition; the rest of the method requires a real QObject.
    new_viewer.setWindowTitle = lambda _title: None
    new_viewer.destroyed = SimpleNamespace(connect=lambda _callback: None)
    result = gui.PickerWindow._show_sounding(
        picker, "collection-2", "KOUN", title="Second")

    assert result is new_viewer
    assert picker._viewers == [old_viewer, new_viewer]


def test_bundled_oax_is_normalized_before_combined_viewer_add():
    collection, station_id = render.decode(
        str(examples_dir() / "14061619.OAX")
    )
    with pytest.raises(KeyError):
        collection.getMeta("run")
    with pytest.raises(KeyError):
        collection.getMeta("model")

    class MetadataCheckingViewer(_Viewer):
        def addProfileCollection(self, added, **kwargs):
            self.menu_name = (
                f"{added.getMeta('model')} "
                f"{added.getMeta('run'):%d/%H%MZ}"
            )
            super().addProfileCollection(added, **kwargs)

    viewer = MetadataCheckingViewer()
    picker = SimpleNamespace(
        _viewers=[viewer],
        _combine_soundings_enabled=lambda: True,
        _prune_closed_viewers=lambda: None,
    )

    result = gui.PickerWindow._show_sounding(
        picker, collection, station_id, title="Bundled OAX"
    )

    assert result is viewer
    assert collection.getMeta("run") == collection.getMeta("base_time")
    assert collection.getMeta("model") == "Archive"
    assert viewer.menu_name == "Archive 16/1900Z"


def test_local_display_failure_replaces_decoding_status(
        qt_app, tmp_path, monkeypatch):
    sounding = tmp_path / "decoded.spc"
    sounding.write_text("decoded input", encoding="utf-8")
    statuses = []
    criticals = []
    critical_cursors = []
    status_bar = SimpleNamespace(
        showMessage=lambda message, *_args: statuses.append(message)
    )
    picker = SimpleNamespace(
        statusBar=lambda: status_bar,
        _show_sounding=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("viewer rejected collection")
        ),
        _settings=SimpleNamespace(setValue=lambda *_args: None),
        _remember_recent_file=lambda _path: None,
    )
    monkeypatch.setattr(
        gui_picker,
        "_render",
        lambda: SimpleNamespace(decode=lambda _path: (object(), "TEST")),
    )
    monkeypatch.setattr(
        gui_picker.QMessageBox,
        "critical",
        lambda *args: (
            criticals.append(args),
            critical_cursors.append(qt_app.overrideCursor()),
        ),
    )

    gui.PickerWindow._open_file(picker, str(sounding))

    assert statuses[0].startswith("Decoding decoded.spc")
    assert statuses[-1] == "Display failed"
    assert len(criticals) == 1
    assert "Decoded, but could not display" in criticals[0][2]
    assert critical_cursors == [None]
    assert qt_app.overrideCursor() is None


def test_browse_filter_exposes_all_bundled_sounding_formats(monkeypatch):
    filters = []
    picker = SimpleNamespace(
        _settings=SimpleNamespace(value=lambda *_args: "")
    )
    monkeypatch.setattr(
        gui_picker.QFileDialog,
        "getOpenFileName",
        lambda _parent, _title, _start, file_filter: (
            filters.append(file_filter) or ("", "")
        ),
    )

    gui.PickerWindow._browse_file(picker)

    assert len(filters) == 1
    for pattern in ("*.npz", "*.spc", "*.OAX", "*.buf", "*.pecan"):
        assert pattern in filters[0]

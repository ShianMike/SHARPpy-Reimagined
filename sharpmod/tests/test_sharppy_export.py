"""Tests for explicit SHARPpy text sounding export helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from datetime import datetime

import pytest
from qtpy.QtWidgets import QMainWindow

from sharpmod import export_paths, gui_viewer
from sharpmod.io.sharppy_export import (
    export_collection_to_sharppy,
    export_profile_to_sharppy,
    highlighted_profile,
)


class _ExportableProfile:
    def toFile(self, file_name):
        Path(file_name).write_text(
            "%TITLE%\nTEST   260705/0000\n%RAW%\n%END%\n",
            encoding="utf-8",
        )


def test_export_profile_to_sharppy_writes_canonical_text(tmp_path):
    out = tmp_path / "shared_sounding.txt"

    written = export_profile_to_sharppy(_ExportableProfile(), out)

    assert written == str(out)
    text = out.read_text(encoding="utf-8")
    assert "%TITLE%" in text
    assert "%RAW%" in text
    assert "%END%" in text


def test_export_collection_to_sharppy_uses_highlighted_profile(tmp_path):
    prof = _ExportableProfile()
    prof_col = SimpleNamespace(getHighlightedProf=lambda: prof)
    out = tmp_path / "highlighted.txt"

    assert highlighted_profile(prof_col) is prof
    export_collection_to_sharppy(prof_col, out)

    assert out.read_text(encoding="utf-8").startswith("%TITLE%")


def test_export_profile_to_sharppy_rejects_unexportable_profile(tmp_path):
    with pytest.raises(TypeError):
        export_profile_to_sharppy(SimpleNamespace(), tmp_path / "bad.txt")


def test_export_menu_resolves_basename_from_focused_collection(
        qt_app, monkeypatch):
    class Collection:
        def __init__(self, loc, run):
            self.metadata = {"loc": loc, "run": run}

        def getMeta(self, key):
            return self.metadata[key]

    first = Collection("FIRST", datetime(2026, 1, 1, 0))
    focused = Collection("OAX", datetime(2014, 6, 16, 19))
    win = QMainWindow()
    win.spc_widget = SimpleNamespace(
        prof_collections=[first, focused],
        pc_idx=0,
        default_prof=SimpleNamespace(),
    )
    renderer = SimpleNamespace(
        PNG_IMAGE_HD="hd",
        PNG_IMAGE_UHD="uhd",
        PNG_IMAGE_LOSSLESS="lossless",
    )
    starts = []
    monkeypatch.setattr(gui_viewer, "_render", lambda: renderer)
    monkeypatch.setattr(
        gui_viewer.QFileDialog,
        "getSaveFileName",
        lambda _parent, title, start, _filter: (
            starts.append((title, Path(start))) or ("", "")
        ),
    )

    gui_viewer._install_export_menu(
        win, first, SimpleNamespace(_settings=None)
    )
    win.spc_widget.pc_idx = 1
    export_menu = next(
        action.menu()
        for action in win.menuBar().actions()
        if action.text() == "Export"
    )
    actions = {action.text(): action for action in export_menu.actions()}
    actions["Export Image (HD PNG)\u2026"].trigger()
    actions["Export Text (SHARPpy)\u2026"].trigger()
    qt_app.processEvents()

    assert [(title, path.name) for title, path in starts] == [
        ("Export Sounding HD Image", "OAX_2014061619Z_hd.png"),
        ("Export Sounding Text (SHARPpy)", "OAX_2014061619Z.txt"),
    ]
    assert {path.parent for _title, path in starts} == {
        export_paths.export_directory()
    }
    win.close()


def test_export_dialog_accepts_one_off_destination_without_remembering_it(
        qt_app, tmp_path, monkeypatch):
    application = tmp_path / "application"
    application.mkdir()
    monkeypatch.setattr(export_paths, "application_root", lambda: application)
    selected = tmp_path / "one-off" / "chosen.png"
    selected.parent.mkdir()
    starts = []

    def choose(_parent, _title, start, _filter):
        starts.append(Path(start))
        return (str(selected), "PNG image (*.png)") if len(starts) == 1 else ("", "")

    def save(_widget, path, *, image_mode):
        assert image_mode == "hd"
        Path(path).write_bytes(b"png")
        return True

    renderer = SimpleNamespace(
        PNG_IMAGE_HD="hd",
        PNG_IMAGE_UHD="uhd",
        PNG_IMAGE_LOSSLESS="lossless",
        save_widget_png=save,
    )
    monkeypatch.setattr(gui_viewer, "_render", lambda: renderer)
    monkeypatch.setattr(gui_viewer.QFileDialog, "getSaveFileName", choose)
    win = QMainWindow()
    win.spc_widget = SimpleNamespace(
        prof_collections=[], pc_idx=0, default_prof=SimpleNamespace()
    )
    gui_viewer._install_export_menu(win, SimpleNamespace(), SimpleNamespace(_settings=None))
    export_menu = next(
        action.menu() for action in win.menuBar().actions() if action.text() == "Export"
    )
    action = next(
        item for item in export_menu.actions()
        if item.text() == "Export Image (HD PNG)\u2026"
    )

    action.trigger()
    action.trigger()
    qt_app.processEvents()

    dedicated = application / "rendered_soundings"
    assert selected.read_bytes() == b"png"
    assert [path.parent for path in starts] == [dedicated, dedicated]
    win.close()


def test_open_export_folder_action_uses_the_dialog_directory(
        qt_app, tmp_path, monkeypatch):
    application = tmp_path / "application"
    application.mkdir()
    monkeypatch.setattr(export_paths, "application_root", lambda: application)
    opened = []
    monkeypatch.setattr(
        gui_viewer.QDesktopServices,
        "openUrl",
        lambda url: opened.append(Path(url.toLocalFile())) or True,
    )
    monkeypatch.setattr(
        gui_viewer,
        "_render",
        lambda: SimpleNamespace(
            PNG_IMAGE_HD="hd",
            PNG_IMAGE_UHD="uhd",
            PNG_IMAGE_LOSSLESS="lossless",
        ),
    )
    win = QMainWindow()
    win.spc_widget = SimpleNamespace(
        prof_collections=[], pc_idx=0, default_prof=SimpleNamespace()
    )
    gui_viewer._install_export_menu(win, SimpleNamespace(), SimpleNamespace(_settings=None))
    export_menu = next(
        action.menu() for action in win.menuBar().actions() if action.text() == "Export"
    )

    next(
        item for item in export_menu.actions() if item.text() == "Open Export Folder"
    ).trigger()
    qt_app.processEvents()

    assert opened == [application / "rendered_soundings"]
    win.close()


def test_unusable_export_folder_is_reported_before_opening_dialog(
        qt_app, tmp_path, monkeypatch):
    application = tmp_path / "application"
    application.mkdir()
    blocked = application / "rendered_soundings"
    blocked.write_text("blocked", encoding="utf-8")
    monkeypatch.setattr(export_paths, "application_root", lambda: application)
    messages = []
    monkeypatch.setattr(
        gui_viewer.QMessageBox,
        "critical",
        lambda _parent, _title, message: messages.append(message),
    )
    monkeypatch.setattr(
        gui_viewer.QFileDialog,
        "getSaveFileName",
        lambda *_args: pytest.fail("save dialog opened with an unusable export folder"),
    )
    monkeypatch.setattr(
        gui_viewer,
        "_render",
        lambda: SimpleNamespace(
            PNG_IMAGE_HD="hd",
            PNG_IMAGE_UHD="uhd",
            PNG_IMAGE_LOSSLESS="lossless",
        ),
    )
    win = QMainWindow()
    win.spc_widget = SimpleNamespace(
        prof_collections=[], pc_idx=0, default_prof=SimpleNamespace()
    )
    gui_viewer._install_export_menu(win, SimpleNamespace(), SimpleNamespace(_settings=None))
    export_menu = next(
        action.menu() for action in win.menuBar().actions() if action.text() == "Export"
    )

    next(
        item for item in export_menu.actions()
        if item.text() == "Export Image (HD PNG)\u2026"
    ).trigger()
    qt_app.processEvents()

    assert len(messages) == 1
    assert str(blocked) in messages[0]
    win.close()

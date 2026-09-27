"""Tests for explicit SHARPpy text sounding export helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from datetime import datetime

import pytest
from qtpy.QtCore import QSettings
from qtpy.QtGui import QImage, QPixmap
from qtpy.QtWidgets import QDialog, QMainWindow, QWidget

from sharpmod import export_paths, gui_export, gui_viewer, render
from sharpmod.export_presentation import ExportPresentation, recent_exports
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


def _accept_image_dialog(monkeypatch, *, dimensions=(640, 480)):
    class Dialog:
        def __init__(self, _renderer, **_kwargs):
            self.presentation = ExportPresentation(
                *dimensions, preset="custom", theme="dark"
            )
            self.output_pixmap = QPixmap(*dimensions)
            self.output_pixmap.fill()

        def exec(self):
            return QDialog.Accepted

        def release(self):
            pass

    monkeypatch.setattr(gui_export, "ExportImageDialog", Dialog)


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
    _accept_image_dialog(monkeypatch)
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
    actions["Export Sounding Image (PNG)\u2026"].trigger()
    actions["Export Text (SHARPpy)\u2026"].trigger()
    qt_app.processEvents()

    assert [(title, path.name) for title, path in starts] == [
        ("Export Sounding Image", "OAX-init-20140616T1900Z-sounding-image.png"),
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

    renderer = SimpleNamespace(
        PNG_IMAGE_HD="hd",
        PNG_IMAGE_UHD="uhd",
        PNG_IMAGE_LOSSLESS="lossless",
        save_pixmap_png_atomic=render.save_pixmap_png_atomic,
    )
    monkeypatch.setattr(gui_viewer, "_render", lambda: renderer)
    _accept_image_dialog(monkeypatch)
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
        if item.text() == "Export Sounding Image (PNG)\u2026"
    )

    action.trigger()
    action.trigger()
    qt_app.processEvents()

    dedicated = application / "rendered_soundings"
    assert selected.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
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
        if item.text() == "Export Sounding Image (PNG)\u2026"
    ).trigger()
    qt_app.processEvents()

    assert len(messages) == 1
    assert str(blocked) in messages[0]
    win.close()


def test_real_menu_preview_one_off_default_collision_and_completion_actions(
    qt_app, tmp_path, monkeypatch
):
    application = tmp_path / "application"
    application.mkdir()
    monkeypatch.setattr(export_paths, "application_root", lambda: application)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    collection = SimpleNamespace(
        _dates=[datetime(2026, 9, 22, 3)], _prof_idx=0,
        getMeta=lambda key: {
            "loc": "KOUN", "model": "HRRR", "run": datetime(2026, 9, 22),
        }[key],
    )
    win = QMainWindow()
    canvas = QWidget()
    canvas.setStyleSheet("background: #153c61;")
    canvas.prof_collections = [collection]
    canvas.pc_idx = 0
    canvas.default_prof = SimpleNamespace()
    win.setCentralWidget(canvas)
    win.spc_widget = canvas
    win.statusBar()
    win.resize(720, 530)
    win.show()
    qt_app.processEvents()
    snapshots = []

    def accept_preview(dialog):
        dialog.sizes.set_presentation(
            ExportPresentation(800, 600, "custom", True, "light")
        )
        dialog.capture_preview()
        snapshots.append(dialog.output_pixmap.toImage())
        return QDialog.Accepted

    monkeypatch.setattr(gui_export.ExportImageDialog, "exec", accept_preview)
    starts = []
    one_off = tmp_path / "one-off" / "custom.png"
    one_off.parent.mkdir()

    def choose(_parent, _title, start, _filter):
        starts.append(Path(start))
        return (str(one_off), "") if len(starts) == 1 else (start, "")

    monkeypatch.setattr(gui_viewer.QFileDialog, "getSaveFileName", choose)
    opened = []
    monkeypatch.setattr(
        gui_viewer.QDesktopServices, "openUrl",
        lambda url: opened.append(Path(url.toLocalFile())) or True,
    )
    gui_viewer._install_export_menu(win, collection, SimpleNamespace(_settings=settings))
    qt_app.processEvents()
    before = canvas.size()
    menu = next(
        action.menu() for action in win.menuBar().actions() if action.text() == "Export"
    )
    actions = {action.text(): action for action in menu.actions()}
    try:
        image_action = actions["Export Sounding Image (PNG)\u2026"]
        image_action.trigger()
        starts[0].write_bytes(b"prior complete artifact")
        image_action.trigger()
        qt_app.processEvents()
        dedicated = application / "rendered_soundings"
        assert [path.parent for path in starts] == [dedicated, dedicated]
        assert "KOUN-HRRR-init-20260922T0000Z-valid-20260922T0300Z" in starts[0].name
        assert starts[0] != starts[1]
        assert starts[1].name.endswith("-2.png")
        assert starts[0].read_bytes() == b"prior complete artifact"
        assert QImage(str(one_off)) == snapshots[0]
        assert QImage(str(starts[1])) == snapshots[1]
        assert canvas.size() == before
        history = recent_exports(settings)
        assert [Path(item.path) for item in history] == [starts[1], one_off]
        assert all(item.width == 800 and item.height == 600 for item in history)
        actions["Copy Last Export Path"].trigger()
        assert qt_app.clipboard().text() == str(starts[1])
        actions["Open Last Completed Export"].trigger()
        assert opened[-1] == starts[1]
        actions["Open Export Folder"].trigger()
        assert opened[-1] == dedicated
    finally:
        win.close()


def test_menu_png_failure_keeps_previous_file_out_of_completed_history(
    qt_app, tmp_path, monkeypatch
):
    application = tmp_path / "application"
    application.mkdir()
    monkeypatch.setattr(export_paths, "application_root", lambda: application)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    destination = tmp_path / "prior.png"
    destination.write_bytes(b"prior complete output")
    monkeypatch.setattr(
        gui_viewer.QFileDialog, "getSaveFileName",
        lambda *_args: (str(destination), ""),
    )
    _accept_image_dialog(monkeypatch)
    monkeypatch.setattr(
        gui_viewer, "_render",
        lambda: SimpleNamespace(save_pixmap_png_atomic=lambda *_a: False),
    )
    messages = []
    monkeypatch.setattr(
        gui_viewer.QMessageBox, "warning",
        lambda _parent, _title, message: messages.append(message),
    )
    win = QMainWindow()
    win.spc_widget = SimpleNamespace(prof_collections=[], pc_idx=0)
    gui_viewer._install_export_menu(win, SimpleNamespace(), SimpleNamespace(_settings=settings))
    menu = next(
        action.menu() for action in win.menuBar().actions() if action.text() == "Export"
    )
    try:
        next(
            action for action in menu.actions()
            if action.text() == "Export Sounding Image (PNG)\u2026"
        ).trigger()
        assert destination.read_bytes() == b"prior complete output"
        assert recent_exports(settings) == ()
        assert len(messages) == 1
        assert "No partial output was published" in messages[0]
    finally:
        win.close()

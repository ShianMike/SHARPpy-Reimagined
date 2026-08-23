"""Tests for explicit SHARPpy text sounding export helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from datetime import datetime

import pytest
from qtpy.QtWidgets import QMainWindow

from sharpmod import gui_viewer
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
            starts.append((title, Path(start).name)) or ("", "")
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

    assert starts == [
        ("Export Sounding HD Image", "OAX_2014061619Z_hd.png"),
        ("Export Sounding Text (SHARPpy)", "OAX_2014061619Z.txt"),
    ]
    win.close()

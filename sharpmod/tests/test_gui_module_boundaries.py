"""The desktop GUI is split into explicit responsibility modules."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from sharpmod import gui


def test_gui_responsibility_modules_import_independently():
    names = (
        "sharpmod.gui_common",
        "sharpmod.gui_settings",
        "sharpmod.gui_workers",
        "sharpmod.gui_maps",
        "sharpmod.gui_sessions",
        "sharpmod.gui_viewer",
        "sharpmod.gui_picker",
    )

    modules = [importlib.import_module(name) for name in names]

    assert [module.__name__ for module in modules] == list(names)


def test_gui_facade_reexports_supported_entrypoints():
    from sharpmod import gui_maps, gui_picker, gui_viewer

    assert gui.PickerWindow is gui_picker.PickerWindow
    assert gui.compose_interactive is gui_viewer.compose_interactive
    assert gui.StationMapWidget is gui_maps.StationMapWidget
    assert gui.PointMapWidget is gui_maps.PointMapWidget


def test_gui_facade_is_only_bootstrap_and_compatibility():
    source = Path(gui.__file__).read_text(encoding="utf-8")

    assert len(source.splitlines()) < 250
    assert "class PickerWindow" not in source
    assert "class StationMapWidget" not in source


def test_lower_gui_layers_do_not_import_picker_controller():
    root = Path(gui.__file__).resolve().parent
    lower_layers = (
        "gui_common.py",
        "gui_settings.py",
        "gui_workers.py",
        "gui_maps.py",
        "gui_sessions.py",
        "gui_viewer.py",
    )

    for filename in lower_layers:
        source = (root / filename).read_text(encoding="utf-8")
        assert "import gui_picker" not in source
        assert "from sharpmod.gui_picker" not in source


def test_classes_live_in_their_responsibility_modules():
    assert gui.PickerWindow.__module__ == "sharpmod.gui_picker"
    assert gui.StationMapWidget.__module__ == "sharpmod.gui_maps"
    assert gui._ModelFetchWorker.__module__ == "sharpmod.gui_workers"


def test_windows_python314_relaunches_gui_with_project_runtime(
        monkeypatch, tmp_path):
    from sharpmod import gui_picker

    runtime = tmp_path / ".gribenv" / "Scripts" / "pythonw.exe"
    calls = []

    monkeypatch.setattr(gui_picker.sys, "platform", "win32")
    monkeypatch.setattr(gui_picker.sys, "version_info", (3, 14, 0))
    monkeypatch.setattr(
        gui_picker.sys, "executable", str(tmp_path / "python314.exe"))
    monkeypatch.delattr(gui_picker.sys, "frozen", raising=False)
    monkeypatch.delenv("SHARPMOD_GUI_STABLE_RUNTIME", raising=False)
    monkeypatch.setattr(
        gui_picker, "_project_gui_runtime",
        lambda: (runtime, tmp_path))
    monkeypatch.setattr(
        gui_picker.subprocess, "Popen",
        lambda command, **kwargs: calls.append((command, kwargs)))

    assert gui_picker._relaunch_stable_windows_gui(["--sample"])

    command, kwargs = calls[0]
    assert command == [
        str(runtime), "-m", "sharpmod.gui", "--sample"]
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["env"]["SHARPMOD_GUI_STABLE_RUNTIME"] == "1"
    assert kwargs["close_fds"] is True


def test_stable_gui_runtime_does_not_relaunch(monkeypatch):
    from sharpmod import gui_picker

    monkeypatch.setattr(gui_picker.sys, "platform", "win32")
    monkeypatch.setattr(gui_picker.sys, "version_info", (3, 13, 0))
    monkeypatch.setattr(
        gui_picker, "_project_gui_runtime",
        lambda: pytest.fail("stable Python should not search for a fallback"))

    assert not gui_picker._relaunch_stable_windows_gui([])


def test_windows_python314_without_project_runtime_stops_before_qt(
        monkeypatch):
    from sharpmod import gui_picker

    messages = []
    monkeypatch.setattr(gui_picker.sys, "platform", "win32")
    monkeypatch.setattr(gui_picker.sys, "version_info", (3, 14, 0))
    monkeypatch.delattr(gui_picker.sys, "frozen", raising=False)
    monkeypatch.delenv("SHARPMOD_GUI_STABLE_RUNTIME", raising=False)
    monkeypatch.setattr(gui_picker, "_project_gui_runtime", lambda: None)
    monkeypatch.setattr(
        gui_picker, "_show_stable_gui_runtime_required",
        lambda: messages.append("shown"))

    assert gui_picker._relaunch_stable_windows_gui([])
    assert messages == ["shown"]


def test_main_relaunches_before_qapplication(monkeypatch):
    from sharpmod import gui_picker

    calls = []
    monkeypatch.setattr(gui_picker, "_configure_debug_logging", lambda: None)
    monkeypatch.setattr(
        gui_picker, "_relaunch_stable_windows_gui",
        lambda args: calls.append(args) or True)
    monkeypatch.setattr(
        gui_picker, "QApplication",
        lambda *_args, **_kwargs: pytest.fail(
            "QApplication started before stable-runtime relaunch"))

    assert gui_picker.main(["sharpmod-gui", "--sample"]) == 0
    assert calls == [["--sample"]]

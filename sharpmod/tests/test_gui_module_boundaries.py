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


def test_a_leaked_stable_runtime_flag_cannot_disable_the_guard(
        monkeypatch, tmp_path):
    """An environment variable must not overrule ``sys.version_info``.

    The flag exists so a relaunched child does not relaunch again. A copy left
    in the environment by an earlier run used to suppress the guard outright,
    which let Python 3.14 reach QApplication and access-violate with no
    catchable traceback.
    """
    from sharpmod import gui_picker

    runtime = tmp_path / ".gribenv" / "Scripts" / "pythonw.exe"
    calls = []

    monkeypatch.setattr(gui_picker.sys, "platform", "win32")
    monkeypatch.setattr(gui_picker.sys, "version_info", (3, 14, 0))
    monkeypatch.setattr(
        gui_picker.sys, "executable", str(tmp_path / "python314.exe"))
    monkeypatch.delattr(gui_picker.sys, "frozen", raising=False)
    monkeypatch.setenv("SHARPMOD_GUI_STABLE_RUNTIME", "1")
    monkeypatch.setattr(
        gui_picker, "_project_gui_runtime", lambda: (runtime, tmp_path))
    monkeypatch.setattr(
        gui_picker.subprocess, "Popen",
        lambda command, **kwargs: calls.append((command, kwargs)))

    assert gui_picker._relaunch_stable_windows_gui([])
    assert calls and calls[0][0][0] == str(runtime)


def test_a_relaunched_supported_child_does_not_relaunch_again(monkeypatch):
    """The single-shot property comes from the child's own version."""
    from sharpmod import gui_picker

    monkeypatch.setattr(gui_picker.sys, "platform", "win32")
    monkeypatch.setattr(gui_picker.sys, "version_info", (3, 11, 14))
    monkeypatch.setenv("SHARPMOD_GUI_STABLE_RUNTIME", "1")
    monkeypatch.setattr(
        gui_picker, "_project_gui_runtime",
        lambda: pytest.fail("a supported child must not look for a fallback"))

    assert not gui_picker._relaunch_stable_windows_gui([])


@pytest.mark.parametrize(
    ("contents", "expected"),
    (
        ("home = C:\\py\nversion = 3.11.14\n", (3, 11, 14)),
        ("version_info = 3.13.2.final.0\n", (3, 13, 2)),
        ("version = 3.14.0\n", (3, 14, 0)),
        ("home = C:\\py\n", None),
        ("", None),
    ),
)
def test_venv_python_version_is_read_not_executed(tmp_path, contents, expected):
    from sharpmod import gui_picker

    (tmp_path / "pyvenv.cfg").write_text(contents, encoding="utf-8")

    assert gui_picker._venv_python_version(tmp_path) == expected


def test_a_missing_pyvenv_config_reports_no_version(tmp_path):
    from sharpmod import gui_picker

    assert gui_picker._venv_python_version(tmp_path / "absent") is None


@pytest.mark.parametrize("version", ("3.14.0", "3.15.1"))
def test_an_equally_unsupported_venv_is_not_offered_as_a_rescue(
        monkeypatch, tmp_path, version):
    """Relaunching into another 3.14 would reach the same crash."""
    from sharpmod import gui_picker

    environment = tmp_path / ".gribenv"
    scripts = environment / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "pythonw.exe").write_bytes(b"")
    (environment / "pyvenv.cfg").write_text(
        "version = %s\n" % version, encoding="utf-8")
    monkeypatch.setattr(
        gui_picker, "__file__", str(tmp_path / "sharpmod" / "gui_picker.py"))
    monkeypatch.setattr(
        gui_picker.sys, "executable", str(tmp_path / "python314.exe"))

    assert gui_picker._project_gui_runtime() is None


def test_a_supported_venv_is_offered(monkeypatch, tmp_path):
    from sharpmod import gui_picker

    environment = tmp_path / ".gribenv"
    scripts = environment / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "pythonw.exe").write_bytes(b"")
    (environment / "pyvenv.cfg").write_text(
        "version = 3.11.14\n", encoding="utf-8")
    monkeypatch.setattr(
        gui_picker, "__file__", str(tmp_path / "sharpmod" / "gui_picker.py"))
    monkeypatch.setattr(
        gui_picker.sys, "executable", str(tmp_path / "python314.exe"))

    runtime = gui_picker._project_gui_runtime()

    assert runtime is not None
    assert runtime[0] == scripts / "pythonw.exe"
    assert runtime[1] == tmp_path


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

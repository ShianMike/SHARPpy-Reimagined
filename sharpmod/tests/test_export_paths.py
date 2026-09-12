"""Regression coverage for the centralized interactive export destination."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest
from qtpy.QtCore import QSettings

from sharpmod import export_paths
from sharpmod.export_paths import ExportDirectoryError
from sharpmod.gui_settings import _build_settings


def test_source_export_directory_is_project_relative_outside_cwd(
    tmp_path, monkeypatch
):
    expected_root = Path(export_paths.__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)

    assert export_paths.application_root() == expected_root
    assert export_paths.export_directory() == expected_root / "rendered_soundings"


def test_missing_export_directory_is_created_and_writable(tmp_path, monkeypatch):
    application = tmp_path / "installed-app"
    application.mkdir()
    monkeypatch.setattr(export_paths, "application_root", lambda: application)

    directory = export_paths.export_directory()

    assert directory == application / "rendered_soundings"
    assert directory.is_dir()
    assert not tuple(directory.glob(".sharpmod-export-write-test-*"))


def test_export_directory_never_falls_back_when_target_is_invalid(
    tmp_path, monkeypatch
):
    application = tmp_path / "installed-app"
    application.mkdir()
    blocked = application / "rendered_soundings"
    blocked.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(export_paths, "application_root", lambda: application)

    with pytest.raises(ExportDirectoryError, match="not a directory") as error:
        export_paths.export_directory()

    assert str(blocked) in str(error.value)


def test_export_directory_reports_creation_failure_without_fallback(
    tmp_path, monkeypatch
):
    application = tmp_path / "installed-app"
    application.write_text("not a directory", encoding="utf-8")
    target = application / "rendered_soundings"
    monkeypatch.setattr(export_paths, "application_root", lambda: application)

    with pytest.raises(ExportDirectoryError, match="could not be created") as error:
        export_paths.export_directory()

    assert str(target) in str(error.value)
    assert not (tmp_path / "Desktop").exists()
    assert not (tmp_path / "Documents").exists()


def test_export_directory_reports_failed_write_probe(tmp_path, monkeypatch):
    application = tmp_path / "installed-app"
    application.mkdir()
    target = application / "rendered_soundings"
    monkeypatch.setattr(export_paths, "application_root", lambda: application)

    def deny_probe(*_args, **_kwargs):
        raise PermissionError("write denied by test")

    monkeypatch.setattr(export_paths.tempfile, "NamedTemporaryFile", deny_probe)

    with pytest.raises(ExportDirectoryError, match="not writable") as error:
        export_paths.export_directory()

    assert str(target) in str(error.value)
    assert "write denied by test" in str(error.value)


def test_frozen_build_uses_installed_executable_directory(tmp_path, monkeypatch):
    executable = tmp_path / "portable" / "SHARPpy Reimagined.exe"
    monkeypatch.setattr(export_paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(export_paths.sys, "executable", str(executable))

    assert export_paths.application_root() == executable.parent.resolve()


def test_stale_export_setting_is_removed_across_settings_restart(
    tmp_path, monkeypatch
):
    settings_path = tmp_path / "settings.ini"
    legacy_path = tmp_path / "legacy.ini"
    stale = tmp_path / "user" / "Desktop"
    application = tmp_path / "installed-app"
    application.mkdir()
    monkeypatch.setattr(export_paths, "application_root", lambda: application)
    legacy = QSettings(str(legacy_path), QSettings.IniFormat)
    legacy.setValue("export_dir", str(stale))
    legacy.sync()

    settings = _build_settings(settings_path, legacy)
    assert not settings.contains("export_dir")
    assert export_paths.export_file_path(
        "first.png", settings=settings
    ) == application / "rendered_soundings" / "first.png"
    settings.sync()
    del settings

    restarted = QSettings(str(settings_path), QSettings.IniFormat)
    assert not restarted.contains("export_dir")
    assert export_paths.export_file_path(
        "second.png", settings=restarted
    ) == application / "rendered_soundings" / "second.png"


def test_process_restart_uses_project_directory_and_ignores_stale_setting(
    tmp_path,
):
    project_root = Path(export_paths.__file__).resolve().parents[1]
    settings_path = tmp_path / "settings.ini"
    settings = QSettings(str(settings_path), QSettings.IniFormat)
    settings.setValue("meta/native_settings_migrated", True)
    settings.setValue("export_dir", str(tmp_path / "user" / "Desktop"))
    settings.sync()
    del settings
    script = (
        "import json; "
        "from sharpmod.export_paths import application_root, export_directory; "
        "from sharpmod.gui_settings import _build_settings; "
        "settings = _build_settings(); "
        "print(json.dumps({'root': str(application_root()), "
        "'directory': str(export_directory(settings=settings)), "
        "'has_stale': settings.contains('export_dir')}))"
    )
    environment = os.environ.copy()
    environment["SHARPMOD_SETTINGS_PATH"] = str(settings_path)
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(project_root), environment.get("PYTHONPATH", "")))
    )

    payloads = tuple(
        json.loads(
            subprocess.run(
                [sys.executable, "-c", script],
                cwd=tmp_path,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        for _restart in range(2)
    )

    assert {Path(payload["root"]) for payload in payloads} == {project_root}
    assert {Path(payload["directory"]) for payload in payloads} == {
        project_root / "rendered_soundings"
    }
    assert not any(payload["has_stale"] for payload in payloads)


def test_export_file_path_discards_parent_components(tmp_path, monkeypatch):
    application = tmp_path / "installed-app"
    application.mkdir()
    monkeypatch.setattr(export_paths, "application_root", lambda: application)

    result = export_paths.export_file_path(tmp_path / "elsewhere" / "result.csv")

    assert result == application / "rendered_soundings" / "result.csv"


def test_render_npz_defaults_to_export_folder_and_honors_explicit_path(
    tmp_path, monkeypatch
):
    import importlib

    from sharpmod import tools

    application = tmp_path / "installed-app"
    application.mkdir()
    monkeypatch.setattr(export_paths, "application_root", lambda: application)
    render_module = importlib.import_module("sharpmod.render")
    calls = []

    def fake_render(infile, outfile, **kwargs):
        calls.append((Path(infile), Path(outfile), kwargs))
        return str(outfile)

    monkeypatch.setattr(render_module, "render", fake_render)
    source = tmp_path / "working-directory" / "named-sounding.npz"
    explicit = tmp_path / "one-off" / "chosen.png"

    default_result = tools.render_npz(str(source), image_mode="hd")
    explicit_result = tools.render_npz(str(source), str(explicit))

    dedicated = application / "rendered_soundings" / "named-sounding.png"
    assert Path(default_result) == dedicated
    assert Path(explicit_result) == explicit
    assert calls == [
        (source, dedicated, {"image_mode": "hd"}),
        (source, explicit, {}),
    ]


@pytest.mark.parametrize(
    ("module_name", "argv"),
    (
        ("sharpmod.tools.era5_extract", ["2024-05-20 00", "35", "-97"]),
        ("sharpmod.tools.ifs_extract", ["35", "-97", "2024-05-20 00"]),
        ("sharpmod.tools.wrf_extract", ["wrfout", "35", "-97"]),
    ),
)
def test_extractor_cli_default_sounding_uses_export_folder(
    module_name, argv, tmp_path, monkeypatch
):
    import importlib

    application = tmp_path / "installed-app"
    application.mkdir()
    monkeypatch.setattr(export_paths, "application_root", lambda: application)
    module = importlib.import_module(module_name)
    captured = []

    def fake_extract(*args, **_kwargs):
        captured.append(Path(args[3]))
        return str(args[3])

    monkeypatch.setattr(module, "extract", fake_extract)

    assert module.main(argv) == 0
    assert len(captured) == 1
    assert captured[0].parent == application / "rendered_soundings"
    assert captured[0].suffix == ".npz"


def test_uwyo_cli_default_sounding_uses_export_folder(tmp_path, monkeypatch):
    from sharpmod.tools import uwyo_sounding

    application = tmp_path / "installed-app"
    application.mkdir()
    monkeypatch.setattr(export_paths, "application_root", lambda: application)
    metadata = SimpleNamespace(
        id="72357", name="Norman Oklahoma", lat=35.18, lon=-97.44
    )
    profile = SimpleNamespace(meta={}, pres=np.asarray([1000.0, 900.0]))
    decoder = SimpleNamespace(
        resolve_station=lambda _station: metadata,
        fetch=lambda _station, _when: profile,
    )
    monkeypatch.setattr(uwyo_sounding, "UWyo_Decoder", lambda **_kwargs: decoder)
    written = []
    monkeypatch.setattr(
        uwyo_sounding,
        "_write_npz",
        lambda _profile, path, _meta, _loc: written.append(Path(path)),
    )
    args = SimpleNamespace(
        station="72357",
        time="2024-05-20 00",
        out=None,
        loc=None,
        render=None,
    )

    assert uwyo_sounding._cmd_fetch(args) == 0
    assert written == [
        application / "rendered_soundings" / "uwyo_72357_2024052000.npz"
    ]


def test_observed_cli_default_sounding_uses_export_folder(tmp_path, monkeypatch):
    from sharpmod.tools import observed_sounding

    application = tmp_path / "installed-app"
    application.mkdir()
    monkeypatch.setattr(export_paths, "application_root", lambda: application)
    valid = observed_sounding._parse_when("2024-05-20 00")
    result = SimpleNamespace(
        metadata={},
        provider="uwyo",
        provider_name="University of Wyoming",
        station_id="72357",
        valid=valid,
        profile=SimpleNamespace(pres=np.asarray([1000.0, 900.0])),
    )
    monkeypatch.setattr(
        observed_sounding, "fetch_observed", lambda *_args, **_kwargs: result
    )
    written = []
    monkeypatch.setattr(
        observed_sounding,
        "write_observed_npz",
        lambda _result, path, **_kwargs: written.append(Path(path)),
    )
    args = SimpleNamespace(
        station="72357",
        time=valid,
        provider="auto",
        out=None,
        loc=None,
        render=None,
    )

    assert observed_sounding._cmd_fetch(args) == 0
    assert written == [
        application
        / "rendered_soundings"
        / "observed_uwyo_72357_2024052000.npz"
    ]


def test_saved_locations_dialog_accepts_default_in_export_folder(
    qt_app, tmp_path, monkeypatch
):
    from sharpmod import gui_locations

    application = tmp_path / "installed-app"
    application.mkdir()
    monkeypatch.setattr(export_paths, "application_root", lambda: application)
    chosen = []

    class Store:
        @staticmethod
        def load():
            return []

        @staticmethod
        def export_file(path):
            Path(path).write_text("{}", encoding="utf-8")

    def accept_default(_parent, _caption, start, _filter):
        chosen.append(Path(start))
        return start, "SHARPpy Locations (*.json)"

    monkeypatch.setattr(
        gui_locations.QFileDialog, "getSaveFileName", accept_default
    )
    dialog = gui_locations.SavedLocationsDialog(Store())
    try:
        dialog._export()
        expected = application / "rendered_soundings" / "sharpmod-locations.json"
        assert chosen == [expected]
        assert expected.read_text(encoding="utf-8") == "{}"
    finally:
        dialog.close()


@pytest.mark.parametrize(
    ("method_name", "config_key"),
    (("saveimage", "save_img"), ("savetext", "save_txt")),
)
def test_vendored_file_save_actions_reset_default_after_one_off_choice(
    qt_app, tmp_path, monkeypatch, method_name, config_key
):
    import importlib

    application = tmp_path / "installed-app"
    application.mkdir()
    monkeypatch.setattr(export_paths, "application_root", lambda: application)
    dedicated = application / "rendered_soundings"
    selected = tmp_path / f"one-off-{method_name}.dat"
    starts = []
    upstream = importlib.import_module("sharppy.viz.SPCWindow")
    spc_window = importlib.import_module("sharpmod.viz.SPCWindow")

    def choose(_parent, _caption, start, _filter):
        starts.append(Path(start))
        return str(selected), "selected filter"

    monkeypatch.setattr(upstream.QFileDialog, "getSaveFileName", choose)
    spc_window._install_export_directory_hooks()
    config = {("paths", config_key): str(tmp_path / "user" / "Desktop")}
    dummy = SimpleNamespace(config=config)
    if method_name == "saveimage":
        dummy.pixmapToFile = lambda path: Path(path).write_bytes(b"png")
    else:
        dummy.default_prof = SimpleNamespace(
            toFile=lambda path: Path(path).write_text("sounding", encoding="utf-8")
        )

    getattr(spc_window._VendoredSPCWidget, method_name)(dummy)

    assert starts == [dedicated]
    assert selected.is_file()
    assert config["paths", config_key] == str(dedicated)

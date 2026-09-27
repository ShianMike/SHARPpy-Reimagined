"""T27 pixel, identity, path, preference, and completed-history contracts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import pytest
from qtpy.QtCore import QSettings

from sharpmod import export_paths
from sharpmod.export_presentation import (
    MAX_EXPORT_FILENAME,
    ExportIdentity,
    ExportPresentation,
    available_export_path,
    export_filename,
    export_identity,
    load_presentation,
    recent_exports,
    remember_export,
    sanitize_filename,
    save_presentation,
)


RUN = datetime(2026, 9, 22, 0, tzinfo=timezone.utc)


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)


def test_dimension_aspect_presets_and_malformed_settings_are_bounded(settings):
    selected = ExportPresentation(2048, 1536, "custom", False, "light", False)
    save_presentation(settings, "Sounding Image", selected)
    assert load_presentation(settings, "Sounding Image") == selected
    assert selected.aspect_label == "4:3"
    assert settings.allKeys() == ["exports/presentation/sounding-image"]
    assert not any("destination" in key for key in settings.allKeys())
    settings.setValue("exports/presentation/sounding-image", "{broken")
    assert load_presentation(settings, "Sounding Image") == ExportPresentation()
    with pytest.raises(ValueError, match="safety limit"):
        ExportPresentation(4096, 4096)
    with pytest.raises(ValueError, match="pixel count"):
        ExportPresentation(True, 1080)
    with pytest.raises(ValueError, match="boolean"):
        ExportPresentation(1920, 1080, include_caption="false")


def test_identity_naming_unsafe_collision_and_long_components(tmp_path, monkeypatch):
    class Collection:
        _dates = [RUN + timedelta(hours=3)]
        _prof_idx = 0

        @staticmethod
        def getMeta(key):  # noqa: N802 - upstream API
            return {
                "loc": "CON:/Atwood?* Oklahoma",
                "model": "HRRR/experimental",
                "provider": "NCEP",
                "run": RUN,
            }[key]

    identity = export_identity(Collection())
    name = export_filename(identity, kind="sounding-image", extension="png")
    assert "Atwood" in name and "HRRR" in name and "NCEP" in name
    assert "init-20260922T0000Z" in name
    assert "valid-20260922T0300Z" in name
    assert len(name) <= MAX_EXPORT_FILENAME
    assert not any(char in name for char in '<>:"/\\|?*')
    assert sanitize_filename("CON") == "_CON"
    assert sanitize_filename("../../") == "export"

    very_long = ExportIdentity(
        location="Location" * 50,
        source="Source" * 50,
        initialization=RUN,
        initialization_end=RUN + timedelta(hours=1),
        valid_start=RUN + timedelta(hours=1),
        valid_end=RUN + timedelta(hours=36),
        detail="Field" * 50,
    )
    long_name = export_filename(very_long, kind="forecast-timeline", extension="gif")
    assert len(long_name) <= MAX_EXPORT_FILENAME
    assert "valid-20260922T0100Z-to-20260923T1200Z" in long_name
    assert "detail-" in long_name
    application = tmp_path / "application"
    application.mkdir()
    monkeypatch.setattr(export_paths, "application_root", lambda: application)
    first = available_export_path(name)
    first.write_bytes(b"complete")
    second = available_export_path(name)
    assert second.parent == first.parent == application / "rendered_soundings"
    assert second.name.endswith("-2.png") and second != first
    explicit = tmp_path / "one-off" / "chosen.png"
    explicit.parent.mkdir()
    assert available_export_path(explicit.name, directory=explicit.parent) == explicit
    assert available_export_path(name).parent == first.parent


def test_recent_exports_only_follow_complete_files_and_include_one_off_paths(
    settings, tmp_path
):
    path = tmp_path / "one-off" / "output.png"
    path.parent.mkdir()
    with pytest.raises(ValueError, match="does not exist"):
        remember_export(settings, path, kind="PNG")
    assert recent_exports(settings) == ()
    path.write_bytes(b"\x89PNG\r\n\x1a\ncomplete")
    first = remember_export(
        settings, path, kind="PNG", summary="KOUN HRRR valid 2026-09-22",
        dimensions=(1920, 1080), now=RUN,
    )
    assert first.exists and first.path == str(path.resolve())
    assert recent_exports(settings) == (first,)
    for index in range(20):
        extra = tmp_path / f"{index}.gif"
        extra.write_bytes(b"GIF89a complete")
        remember_export(settings, extra, kind="GIF", now=RUN + timedelta(minutes=index + 1))
    assert len(recent_exports(settings)) == 12
    assert all(entry.exists for entry in recent_exports(settings))
    settings.sync()
    loaded = QSettings(settings.fileName(), QSettings.IniFormat)
    assert len(recent_exports(loaded)) == 12
    payload = json.loads(loaded.value("exports/recent", "", str))
    assert payload["format"] == "sharpmod-recent-exports"
    assert "export_dir" not in loaded.allKeys()
    loaded.setValue("exports/recent", '{"format":"wrong"}')
    assert recent_exports(loaded) == ()

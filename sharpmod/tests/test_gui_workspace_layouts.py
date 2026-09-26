"""Portable named-layout storage and monitor-safe geometry regressions."""

from __future__ import annotations

import json

import pytest
from qtpy.QtCore import QSettings

from sharpmod.gui_workspace_layouts import (
    LAYOUT_FORMAT,
    LAYOUT_SETTINGS_KEY,
    LayoutFormatError,
    WorkspaceLayoutStore,
    safe_floating_geometry,
)


def _state(mode="docked"):
    return {
        "mode": mode,
        "analysis_visible": True,
        "analysis_area": "right",
        "analysis_width": 640,
        "sidebar_visible": False,
        "sidebar_width": 280,
        "tabified": False,
        "floating_geometry": [2300, 100, 1000, 700],
        "floating_screen": {
            "name": "Secondary",
            "available": [1920, 0, 1920, 1080],
            "device_pixel_ratio": 1.0,
        },
    }


def test_named_layout_store_round_trips_updates_and_deletes(tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    store = WorkspaceLayoutStore(settings)

    saved = store.save("  Severe Ops  ", _state("separate"))

    assert saved == "Severe Ops"
    assert store.names() == ("Severe Ops",)
    assert store.get("severe ops")["mode"] == "separate"

    # Names are case-insensitive identities, so saving the same name updates
    # one layout rather than creating a visually indistinguishable duplicate.
    store.save("SEVERE OPS", _state("expanded"))
    assert store.names() == ("Severe Ops",)
    assert WorkspaceLayoutStore(settings).get("Severe Ops")["mode"] == "expanded"

    document = json.loads(settings.value(LAYOUT_SETTINGS_KEY, "", str))
    assert document["format"] == LAYOUT_FORMAT
    assert document["version"] == 1
    assert len(document["layouts"]) == 1

    assert store.delete("severe ops")
    assert store.names() == ()
    assert not store.delete("missing")


@pytest.mark.parametrize("name", ("", "   ", "bad\nname", "x" * 65))
def test_named_layout_store_rejects_ambiguous_names(tmp_path, name):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)

    with pytest.raises(ValueError):
        WorkspaceLayoutStore(settings).save(name, _state())


def test_unknown_future_layout_document_is_preserved(tmp_path):
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    future = json.dumps(
        {"format": LAYOUT_FORMAT, "version": 99, "layouts": []},
        separators=(",", ":"),
    )
    settings.setValue(LAYOUT_SETTINGS_KEY, future)
    settings.sync()
    store = WorkspaceLayoutStore(settings)

    assert store.names() == ()
    with pytest.raises(LayoutFormatError, match="newer version"):
        store.save("Do not overwrite", _state())
    assert settings.value(LAYOUT_SETTINGS_KEY, "", str) == future


def test_geometry_maps_missing_scaled_monitor_into_current_primary():
    restored = safe_floating_geometry(
        [2300, 100, 1000, 700],
        saved_screen={
            "name": "Secondary",
            "available": [1920, 0, 1920, 1080],
            "device_pixel_ratio": 1.0,
        },
        current_screens=(
            {
                "name": "Primary",
                "available": [0, 0, 1280, 720],
                "device_pixel_ratio": 2.0,
            },
        ),
    )

    x, y, width, height = restored
    assert (width, height) == (667, 467)
    assert 0 <= x and x + width <= 1280
    assert 0 <= y and y + height <= 720


def test_geometry_keeps_a_window_fully_on_negative_origin_screen():
    screens = (
        {
            "name": "Left",
            "available": [-1280, 0, 1280, 720],
            "device_pixel_ratio": 1.0,
        },
        {
            "name": "Primary",
            "available": [0, 0, 1920, 1040],
            "device_pixel_ratio": 1.0,
        },
    )
    restored = safe_floating_geometry(
        [-1500, -200, 1600, 1200],
        saved_screen={
            "name": "Left",
            "available": [-1280, 0, 1280, 720],
            "device_pixel_ratio": 1.0,
        },
        current_screens=screens,
    )

    x, y, width, height = restored
    assert -1280 <= x and x + width <= 0
    assert 0 <= y and y + height <= 720
    assert width <= 1280 and height <= 720


def test_malformed_or_wholly_offscreen_geometry_uses_safe_defaults():
    screens = (
        {
            "name": "Primary",
            "available": [20, 40, 1000, 600],
            "device_pixel_ratio": 1.0,
        },
    )

    for broken in (None, [], [1, 2, 3], [float("nan"), 2, 3, 4]):
        x, y, width, height = safe_floating_geometry(
            broken, saved_screen=None, current_screens=screens
        )
        assert 20 <= x and x + width <= 1020
        assert 40 <= y and y + height <= 640
        assert width >= 480 and height >= 320

    x, y, width, height = safe_floating_geometry(
        [9000, 9000, 900, 700],
        saved_screen=None,
        current_screens=screens,
    )
    assert 20 <= x and x + width <= 1020
    assert 40 <= y and y + height <= 640

"""The browser editor reuses native transforms and leaves source files intact."""
import base64
import hashlib
from pathlib import Path

import pytest

from sharpmod.bridge.errors import BridgeError
from sharpmod.bridge.sounding_view import validate_view

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("view", [
    {"edits": [{"index": 0, "values": {"tmpc": float("nan")}}]},
    {"edits": [{"index": 0, "values": {"unknown": 1}}]},
    {"edits": [{"index": 0, "values": {"u": 1}}] * 2},
    {"edits": [], "chart": "not-a-chart"},
    {"edits": [], "drag": {"target": "wind:0", "x": 1.5, "y": 0.3}},
])
def test_rejects_unsafe_edits(view):
    with pytest.raises(BridgeError):
        validate_view(view)


@pytest.mark.parametrize("show_locator", [True, False])
def test_render_releases_its_windows_and_preserves_existing_ones(tmp_path, qt_app, monkeypatch, show_locator):
    from qtpy.QtCore import QRectF
    from qtpy.QtWidgets import QWidget
    from qtpy import QtCore
    from sharpmod.rendering import cli
    from sharpmod.viz import hodo_locator
    from sharpmod.viz.hodo_levels import _height_level_exclusions

    save = cli.save_widget_png
    def checked_save(widget, *args, **kwargs):
        hodo = widget.hodo
        rect = hodo_locator._inset_rect(hodo, QtCore)
        assert (hodo_locator.locator_rect_for_widget(hodo, QtCore) is not None) == show_locator
        assert any(QRectF(item) == rect for item in _height_level_exclusions(hodo, QtCore)) == show_locator
        if not show_locator:
            before = hodo.plotBitMap.toImage()
            assert not hodo_locator.draw_hodo_locator(hodo)
            assert hodo.plotBitMap.toImage() == before
        return save(widget, *args, **kwargs)

    monkeypatch.setattr(cli, "save_widget_png", checked_save)

    existing = QWidget()
    before = set(qt_app.topLevelWidgets())
    try:
        cli.render(str(ROOT / "examples/soundings/hrrr_point_36.68N_95.66W_f018.npz"),
                   str(tmp_path / "sounding.png"), image_mode="hd", show_locator=show_locator,
                   view=validate_view({"edits": [], "panel": "COND STP"}))
        assert set(qt_app.topLevelWidgets()) == before
    finally:
        existing.deleteLater()


def test_native_edits_panels_reset_and_full_resolution(tmp_path):
    from sharpmod.bridge import render_client, sounding_cache, soundings
    source = ROOT / "examples/soundings/hrrr_point_36.68N_95.66W_f018.npz"
    original_hash = hashlib.sha256(source.read_bytes()).digest()
    sounding_cache.reset_cache(tmp_path)
    try:
        summary = soundings.open_file({"file_name": source.name,
            "content_base64": base64.b64encode(source.read_bytes()).decode("ascii")})
        request = {"sounding_id": summary["sounding_id"], "image_mode": "hd",
                   "view": {"edits": []}}
        original = soundings.render_view(request)
        assert original["width"] > 3000 and original["height"] > 2000
        assert base64.b64decode(original["image"]).startswith(b"\x89PNG")
        find = lambda result, key: next(p for p in result["points"] if p["id"] == key)
        temperature = find(original, "tmpc:0")
        dragged = soundings.render_view({**request, "view": {
            "edits": [], "chart": "theta", "panel": "COND STP",
            "drag": {"target": "tmpc:0", "x": temperature["x"] + 0.01, "y": temperature["y"]}}})
        assert find(dragged, "tmpc:0")["values"][0]["value"] > temperature["values"][0]["value"]
        assert dragged["selection"] == {"chart": "theta", "panel": "COND STP"}
        wind = find(dragged, "wind:0")
        changed = soundings.render_view({**request, "view": {
            "edits": dragged["edits"], "drag": {"target": "wind:0", "x": wind["x"] + 0.015, "y": wind["y"] - 0.01}}})
        assert find(changed, "wind:0")["values"] != wind["values"]
        assert "tmpc" in changed["edits"][0]["values"]
        motion = find(changed, "storm:-1")
        moved = soundings.render_view({**request, "view": {
            "edits": changed["edits"], "drag": {"target": "storm:-1", "x": motion["x"] + 0.01, "y": motion["y"]}}})
        assert find(moved, "storm:-1")["values"] != motion["values"]
        reset = soundings.render_view(request)
        assert reset["edits"] == []
        assert reset["points"] == original["points"]
        if reset["image"] != original["image"]:
            for name, result in (("original", original), ("reset", reset)):
                (tmp_path / f"{name}.png").write_bytes(base64.b64decode(result["image"]))
            pytest.fail(f"Reset image changed; comparison images saved in {tmp_path}")
        full = soundings.render_view({**request, "image_mode": "uhd"})
        assert full["width"] > 4000 and full["height"] > 3000
        assert len(full["points"]) == len(original["points"])
        assert hashlib.sha256(source.read_bytes()).digest() == original_hash
    finally:
        render_client.client().close()
        sounding_cache.reset_cache()

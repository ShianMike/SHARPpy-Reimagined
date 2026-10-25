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
    {"edits": [], "timeIndex": -1},
    {"edits": [], "timeIndex": True},
    {"edits": [], "timeIndex": 1.5},
])
def test_rejects_unsafe_edits(view):
    with pytest.raises(BridgeError):
        validate_view(view)


def test_inspection_tracks_undrawn_winds_and_preserves_missing_values():
    from types import SimpleNamespace

    import numpy as np
    from qtpy.QtCore import QPoint

    from sharpmod.rendering.interactive import inspection

    prof = SimpleNamespace(pres=[1000, 850, 500, np.nan], hght=[100, 13000, 5600, 0],
                           tmpc=[20, 10, -10, 0], dwpc=np.ma.array([15, 5, -20, 0], mask=[0, 1, 0, 0]),
                           wdir=[180, 220, 270, 0], wspd=[0, 20, np.nan, 0],
                           u=[0, 20, np.nan, 0], v=[0, 20, 10, 0])
    sound = SimpleNamespace(lpad=10, tpad=20, brx=800, bry=450, pmin=100, pmax=1050,
                            originy=0, scale=1, pres_to_pix=lambda pressure: (1050 - pressure) / 2,
                            mapTo=lambda _root, _point: QPoint(100, 50))
    hodo = SimpleNamespace(uv_to_pix=lambda u, v: (u * 5, v * 5), width=lambda: 400,
                           height=lambda: 400, mapTo=lambda _root, _point: QPoint(1300, 100))
    sw = SimpleNamespace(default_prof=prof, sound=sound, hodo=hodo, width=lambda: 2000, height=lambda: 1000)
    data = inspection(sw)
    assert data["bounds"] == {"left": 0.055, "top": 0.07, "right": 0.45, "bottom": 0.5}
    levels = {level["index"]: level for level in data["levels"]}
    assert len(levels) == 3
    assert levels[1] == {"index": 1, "pressure": 850.0, "y": 0.15, "height": 13000.0,
                         "tmpc": 10.0, "dwpc": None, "windDirection": 220.0,
                         "windSpeed": 20.0, "hodograph": {"x": 0.7, "y": 0.2}}
    assert levels[0]["windSpeed"] == 0 and levels[0]["hodograph"] == {"x": 0.65, "y": 0.1}
    assert levels[2]["windSpeed"] is None and levels[2]["hodograph"] is None
    prof.hght[1] = np.nan
    assert next(level for level in inspection(sw)["levels"] if level["index"] == 1)["hodograph"] == levels[1]["hodograph"]
    prof.u[1] = 100
    assert next(level for level in inspection(sw)["levels"] if level["index"] == 1)["hodograph"] is None


def test_all_height_charts_replace_only_the_diagnostic_panel(tmp_path, qt_app, monkeypatch):
    from sharpmod.rendering import cli

    save = cli.save_widget_png
    seen = []

    def checked_save(widget, *args, **kwargs):
        slot = widget.streamwiseness
        group = slot._companions
        seen.append(group is not None)
        assert slot.currentChart() == "theta"
        assert widget.grid3.getItemPosition(widget.grid3.indexOf(slot)) == (0, 3, 1, 1)
        if group is not None:
            assert widget.right_inset_ob.isHidden()
            assert widget.grid3.getItemPosition(widget.grid3.indexOf(group)) == (0, 4, 1, 1)
            assert group.layout().count() == 3
            charts = [slot.chart] + [group.layout().itemAt(i).widget() for i in range(3)]
            assert all(chart.data is not None and chart.prof is widget.default_prof for chart in charts)
            assert all(not chart.isHidden() and chart.width() > 0 for chart in charts)
        else:
            assert not widget.right_inset_ob.isHidden()
        return save(widget, *args, **kwargs)

    monkeypatch.setattr(cli, "save_widget_png", checked_save)
    source = ROOT / "examples/soundings/hrrr_point_36.68N_95.66W_f018.npz"
    for panel in ("HEIGHT CHARTS", "STP STATS"):
        result = {}
        cli.render(str(source), str(tmp_path / f"{panel}.png"), image_mode="hd",
                   view=validate_view({"edits": [{"index": 0, "values": {"tmpc": 31}}],
                                       "chart": "theta", "panel": panel}), view_result=result)
        assert result["selection"] == {"chart": "theta", "panel": panel}
        assert {"key": "HEIGHT CHARTS", "label": "All height charts"} in result["options"][1]["choices"]
    assert seen == [True, False]


def test_all_file_times_are_selectable_without_mutating_other_profiles(tmp_path):
    from sharpmod.bridge import render_client, sounding_cache, soundings

    source = ROOT / "examples/soundings/hrrr_kbvo_20260625_06z.buf"
    original_hash = hashlib.sha256(source.read_bytes()).digest()
    sounding_cache.reset_cache(tmp_path)
    try:
        summary = soundings.open_file({"file_name": source.name,
            "content_base64": base64.b64encode(source.read_bytes()).decode("ascii")})
        assert len(summary["times"]) == summary["time_count"] == 49
        request = {"sounding_id": summary["sounding_id"], "image_mode": "hd", "view": {"edits": []}}
        with pytest.raises(BridgeError, match="outside"):
            soundings.render_view({**request, "view": {"edits": [], "timeIndex": 49}})
        original = soundings.render_view(request)
        final = soundings.render_view({**request, "view": {"edits": [], "timeIndex": 48}})
        collection, _ = sounding_cache.decode_collection(source)
        collection._prof_idx = 48
        profile = collection.getHighlightedProf()
        temperatures = {point["id"]: point["values"][0]["value"] for point in final["points"] if point["id"].startswith("tmpc:")}
        assert temperatures and temperatures == {f"tmpc:{index}": float(value) for index, value in enumerate(profile.tmpc)
                                                if f"tmpc:{index}" in temperatures}
        assert all(field["original"] == field["value"] for point in final["points"] for field in point["values"])
        assert final["points"] != original["points"]
        assert final["inspection"] != original["inspection"]
        assert soundings.render_view(request)["points"] == original["points"]
        assert sounding_cache.cache().get(summary["sounding_id"]).collection._prof_idx == 0
        assert hashlib.sha256(source.read_bytes()).digest() == original_hash
    finally:
        render_client.client().close()
        sounding_cache.reset_cache()


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
        assert all(field["original"] == field["value"] for point in original["points"] for field in point["values"])
        collection, _ = sounding_cache.decode_collection(source)
        profile = collection.getHighlightedProf()
        level = original["inspection"]["levels"][0]
        assert level["pressure"] == float(profile.pres[0])
        assert level["height"] == float(profile.hght[0])
        assert level["tmpc"] == float(profile.tmpc[0])
        assert level["dwpc"] == float(profile.dwpc[0])
        assert level["windDirection"] == float(profile.wdir[0])
        assert level["windSpeed"] == float(profile.wspd[0])
        assert level["hodograph"] == {key: find(original, "wind:0")[key] for key in ("x", "y")}
        upper = next(level for level in original["inspection"]["levels"] if level["pressure"] == 175)
        assert upper["height"] - float(profile.hght[profile.sfc]) > 12000
        assert upper["hodograph"] is not None
        assert not any(point["id"] == f"wind:{upper['index']}" for point in original["points"])
        dragged = soundings.render_view({**request, "view": {
            "edits": [], "chart": "theta", "panel": "COND STP",
            "drag": {"target": "tmpc:0", "x": temperature["x"] + 0.01, "y": temperature["y"]}}})
        assert find(dragged, "tmpc:0")["values"][0]["value"] > temperature["values"][0]["value"]
        assert dragged["inspection"]["levels"][0]["tmpc"] == find(dragged, "tmpc:0")["values"][0]["value"]
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
        for target in ("tmpc:0", "wind:0", "storm:-1"):
            assert [field["original"] for field in find(moved, target)["values"]] == [
                field["value"] for field in find(original, target)["values"]]
        repeated = soundings.render_view({**request, "view": {"edits": moved["edits"]}})
        assert repeated["points"] == moved["points"]
        reset = soundings.render_view(request)
        assert reset["edits"] == []
        assert reset["points"] == original["points"]
        assert reset["inspection"] == original["inspection"]
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

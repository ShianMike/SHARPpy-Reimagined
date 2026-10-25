"""Completed interactive PNGs are reused without retaining failed or stale views."""

import base64
import struct
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest

from sharpmod.bridge import render_client, sounding_cache, sounding_view
from sharpmod.bridge.errors import BridgeError

PNG = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR" + struct.pack(">II", 4564, 3077)


@pytest.fixture
def renderer(tmp_path, monkeypatch):
    cache = sounding_cache.reset_cache(tmp_path)
    source = tmp_path / "source.npz"
    source.write_bytes(b"original profile")
    entry = sounding_cache.Entry("example", source, {"time_count": 2})
    cache.add(entry)
    calls = []

    def render(infile, outfile, parcel, mode, *, view):
        calls.append((parcel, mode, view))
        outfile.write_bytes(PNG)
        edits = [{"index": 0, "values": {"tmpc": 30}}] if "drag" in view else view["edits"]
        return {"edits": edits, "selection": {"chart": view["chart"], "panel": view["panel"]}}

    worker = SimpleNamespace(render=render)
    monkeypatch.setattr(render_client, "client", lambda: worker)
    yield cache, entry, calls, worker
    sounding_cache.reset_cache(None)


def test_concurrent_unchanged_requests_share_one_completed_render(renderer):
    cache, entry, calls, worker = renderer
    original = worker.render
    entered, release = Event(), Event()

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    worker.render = slow
    request = {"sounding_id": entry.sounding_id, "view": {"edits": []}}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(sounding_view.render_view, request)
        assert entered.wait(5)
        second = pool.submit(sounding_view.render_view, request)
        release.set()
        assert first.result() == second.result()
    same = sounding_view.render_view({**request, "image_mode": "uhd", "view": {
        "edits": [], "chart": "streamwiseness", "panel": "STP STATS", "timeIndex": 0,
    }})
    assert len(calls) == 1
    assert base64.b64decode(same["image"]) == PNG
    assert (same["width"], same["height"]) == (4564, 3077)
    assert entry.path.read_bytes() == b"original profile"
    assert len(list(cache.renders.glob("*.png"))) == 1


def test_quality_parcel_edits_panels_and_forecast_times_have_distinct_cache_keys(renderer):
    cache, entry, calls, _ = renderer
    request = {"sounding_id": entry.sounding_id, "view": {"edits": []}}
    sounding_view.render_view(request)
    for change in [
        {"image_mode": "hd"}, {"parcel": "SFC"},
        {"view": {"edits": [{"index": 0, "values": {"tmpc": 30}}]}},
        {"view": {"edits": [], "panel": "WINTER"}},
        {"view": {"edits": [], "timeIndex": 1}},
    ]:
        sounding_view.render_view({**request, **change})
        sounding_view.render_view({**request, **change})
    assert len(calls) == 6
    assert len(entry.view_renders) == sounding_view.MAX_CACHED_VIEWS
    assert len(list(cache.renders.glob("*.png"))) <= sounding_view.MAX_CACHED_VIEWS
    sounding_view.render_view(request)
    assert len(calls) == 7


def test_drag_result_is_reused_by_its_resolved_edits_and_cache_files_stay_bounded(renderer):
    cache, entry, calls, _ = renderer
    request = {"sounding_id": entry.sounding_id, "view": {"edits": [], "drag": {
        "target": "tmpc:0", "x": 0.5, "y": 0.5,
    }}}
    result = sounding_view.render_view(request)
    assert sounding_view.render_view({**request, "view": {"edits": result["edits"]}}) == result
    assert len(calls) == 1
    for x in [0.51, 0.52, 0.53]:
        sounding_view.render_view({**request, "view": {**request["view"], "drag": {
            **request["view"]["drag"], "x": x,
        }}})
    assert len(entry.view_renders) == sounding_view.MAX_CACHED_VIEWS
    assert len(list(cache.renders.glob("*.png"))) == 1


def test_invalid_output_is_never_marked_cached_and_can_be_retried(renderer):
    _, entry, calls, worker = renderer
    original = worker.render

    def invalid(*args, **kwargs):
        result = original(*args, **kwargs)
        args[1].write_bytes(b"not a PNG")
        return result

    worker.render = invalid
    request = {"sounding_id": entry.sounding_id, "view": {"edits": []}}
    with pytest.raises(BridgeError, match="did not produce a PNG"):
        sounding_view.render_view(request)
    assert not entry.view_renders
    worker.render = original
    assert base64.b64decode(sounding_view.render_view(request)["image"]) == PNG
    assert len(calls) == 2

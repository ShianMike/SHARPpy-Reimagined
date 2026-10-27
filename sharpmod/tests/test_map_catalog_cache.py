"""Saved map frames remain selectable without the provider's inventory cache."""

import base64
import json
import os
import time
from types import SimpleNamespace

import numpy as np
import pytest

from sharpmod.bridge import fields, frame_cache
from sharpmod.bridge.errors import BridgeError
from sharpmod.providers import hrrr_field as hf, hrrr_products as hp

REQUEST = {"model": "gfs", "run_time": "2026-10-27T00:00:00Z", "forecast_hour": 6}


@pytest.fixture
def saved_frames(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARPMOD_HRRR_FIELD_CACHE", str(tmp_path))
    monkeypatch.setattr(fields, "FRAME", (4, 2))
    monkeypatch.setattr(hf, "field_grid", lambda _model: SimpleNamespace(shape=(2, 4)))
    monkeypatch.setattr(hf, "index_map", lambda size, bounds, **_: SimpleNamespace(
        flat=np.arange(size[0] * size[1]) % 8,
        inside=np.ones(size[0] * size[1], dtype=bool),
        width=size[0], height=size[1], bounds=bounds,
    ))
    monkeypatch.setattr(hf, "_download_fields", lambda product, *_args, **_kwargs: {
        spec.name: np.full((2, 4), 10, dtype=np.float32) for spec in product.all_fields
    })

    def inventory(url, *_args):
        if url.endswith(".index"):
            return json.dumps({"param": "2t", "levtype": "sfc", "step": "6",
                               "_offset": 0, "_length": 100}).encode()
        return b"1:0:d=2026102700:TMP:2 m above ground:6 hour fcst:\n"

    monkeypatch.setattr(hf, "_default_opener", inventory)
    hf.clear_cache()
    try:
        yield
    finally:
        hf.clear_cache()


def _offline(monkeypatch, error=hf.HrrrFieldOffline):
    hf.clear_cache()

    def unavailable(*_args, **_kwargs):
        raise error("inventory is offline")

    monkeypatch.setattr(hf, "_default_opener", unavailable)
    monkeypatch.setattr(hf, "_download_fields", lambda *_args, **_kwargs: pytest.fail(
        "A saved frame must not be downloaded again"))


@pytest.mark.parametrize("model", (*hp.GLOBAL_MODELS, *hp.HRW_MODELS))
@pytest.mark.parametrize("error", [hf.HrrrFieldOffline, hf.HrrrFieldUnavailable, hf.HrrrFieldError])
def test_saved_fields_remain_selectable_after_offline_restart(saved_frames, monkeypatch, model, error):
    request = {**REQUEST, "model": model}
    assert [p["key"] for p in fields.catalog(request)["products"]] == ["tmp-2m"]
    saved = fields.grid({**request, "product": "tmp-2m"})
    assert hf._INVENTORY_CACHE
    _offline(monkeypatch, error)
    assert not hf._INVENTORY_CACHE

    catalog = fields.catalog(request)
    assert [p["key"] for p in catalog["products"]] == ["tmp-2m"]
    assert catalog["default_product"] == "tmp-2m"
    assert fields.grid({**request, "product": "tmp-2m"}) == saved


@pytest.mark.parametrize("damage", [
    "corrupt", "missing", "old-version", "other-model", "other-run", "other-hour", "other-size",
    "wrong-product", "wrong-run", "wrong-hour", "wrong-size", "missing-data", "invalid-data", "short-data",
])
def test_fallback_requires_the_exact_readable_saved_frame(saved_frames, monkeypatch, damage):
    saved = fields.grid({**REQUEST, "product": "tmp-2m"})
    model, product, run, hour = fields._request({**REQUEST, "product": "tmp-2m"})
    target = frame_cache.path("grid", model, product.key, run, hour, fields.FRAME)
    request = dict(REQUEST)
    if damage == "corrupt":
        target.write_bytes(b"interrupted frame")
    elif damage == "missing":
        target.unlink()
    elif damage == "old-version":
        target.rename(target.with_name(target.name.replace("grid-v1_", "grid-v0_")))
    elif damage == "other-model":
        request["model"] = "aigfs"
    elif damage == "other-run":
        request["run_time"] = "2026-10-26T00:00:00Z"
    elif damage == "other-hour":
        request["forecast_hour"] = 12
    elif damage == "other-size":
        monkeypatch.setattr(fields, "FRAME", (8, 4))
    else:
        changes = {
            "wrong-product": {"product": "wind-10m"},
            "wrong-run": {"run_time": "2026-10-26T00:00:00Z"},
            "wrong-hour": {"forecast_hour": 12},
            "wrong-size": {"width": 8},
            "missing-data": {"data_base64": None},
            "invalid-data": {"data_base64": "!" * len(saved["data_base64"])},
            "short-data": {"data_base64": base64.b64encode(b"\0\0").decode()},
        }
        frame_cache.write(target, {**saved, **changes[damage]})
    _offline(monkeypatch)

    with pytest.raises(BridgeError) as error:
        fields.catalog(request)
    assert error.value.code == "FIELD_FETCH_FAILED" and error.value.retryable


def test_cached_products_supplement_a_partial_inventory_without_requiring_contours(saved_frames, monkeypatch):
    saved = fields.grid({**REQUEST, "product": "hgt-wind-500"})
    model, product, run, hour = fields._request({**REQUEST, "product": "hgt-wind-500"})
    frame_cache.path("contours", model, product.key, run, hour, fields.FRAME).unlink()
    hf.clear_cache()

    catalog = fields.catalog(REQUEST)
    assert {p["key"] for p in catalog["products"]} == {"tmp-2m", "hgt-wind-500"}
    _offline(monkeypatch)
    assert [p["key"] for p in fields.catalog(REQUEST)["products"]] == ["hgt-wind-500"]
    assert fields.grid({**REQUEST, "product": "hgt-wind-500"}) == saved


def test_expired_frames_do_not_bypass_existing_retention(saved_frames, monkeypatch):
    fields.grid({**REQUEST, "product": "tmp-2m"})
    model, product, run, hour = fields._request({**REQUEST, "product": "tmp-2m"})
    target = frame_cache.path("grid", model, product.key, run, hour, fields.FRAME)
    expired = time.time() - hf.MAX_MAP_DISK_AGE_SECONDS - 1
    os.utime(target, (expired, expired))
    monkeypatch.setattr(hf, "_NEXT_DISK_PRUNE", 0)
    _offline(monkeypatch)

    with pytest.raises(BridgeError) as error:
        fields.catalog(REQUEST)
    assert error.value.code == "FIELD_FETCH_FAILED"
    assert not target.exists()


@pytest.mark.parametrize("model", (*hp.GLOBAL_MODELS, *hp.HRW_MODELS))
@pytest.mark.parametrize(("failure", "code", "retryable"), [
    (hf.HrrrFieldOffline, "FIELD_FETCH_FAILED", True),
    (hf.HrrrFieldUnavailable, "FIELD_UNAVAILABLE", False),
])
def test_unsaved_products_preserve_provider_errors(saved_frames, monkeypatch, model, failure, code, retryable):
    _offline(monkeypatch, failure)
    with pytest.raises(BridgeError) as error:
        fields.catalog({**REQUEST, "model": model})
    assert error.value.code == code and error.value.retryable is retryable

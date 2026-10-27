"""Bounded, offline regression checks for the T26 field hot paths."""

from __future__ import annotations

import threading
import time
import tracemalloc
from collections import OrderedDict
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from sharpmod import hrrr_field
from sharpmod.providers import hrrr_field_fetch


RUN = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)


def test_nam_multipart_winds_share_a_range_but_decode_distinct_components(monkeypatch, tmp_path):
    from dataclasses import replace
    from sharpmod.providers import hrrr_products as hp
    from sharpmod.tests.test_grib_backend_equivalence import _write_multifield_wind_message

    api = pytest.importorskip("eccodes")
    path = tmp_path / "winds.grib2"
    with path.open("wb") as output:
        _write_multifield_wind_message(api, output, 300, 3, 4)
    payload = path.read_bytes()
    inventory = ("1.1:0:d=2026090412:UGRD:300 mb:1 hour fcst:\n"
                 "1.2:0:d=2026090412:VGRD:300 mb:1 hour fcst:\n"
                 f"2:{len(payload)}:d=2026090412:HGT:300 mb:1 hour fcst:\n")
    rows = hrrr_field.parse_inventory(inventory)
    assert [(row.length, row.field_index) for row in rows[:2]] == [(len(payload), 0), (len(payload), 1)]
    monkeypatch.setattr(hrrr_field, "HRRR_GRID", replace(hrrr_field.HRRR_GRID, shape=(2, 3)))
    monkeypatch.setattr(hrrr_field, "_INVENTORY_CACHE", {})
    requests = []

    def opener(url, _timeout, _limit, byte_range=None):
        requests.append((url, byte_range))
        return inventory.encode() if url.endswith(".idx") else payload

    product = replace(next(item for item in hp.available_products("nam-3km-conus") if item.key == "hgt-wind-300"), contour_fields=())
    for _repeat in range(3):
        decoded = hrrr_field._download_fields(product, RUN, 1, opener=opener, timeout=1, should_cancel=None, model="nam-3km-conus")
        assert decoded["ugrd300"][1, 1] == 3
        assert decoded["vgrd300"][1, 1] == 4
        assert product.derive(decoded)[1, 1] == pytest.approx(5 * hp.MS_TO_KT)
    assert [byte_range for _, byte_range in requests if byte_range is not None] == [(0, len(payload) - 1)] * 3
    with pytest.raises(hrrr_field.HrrrFieldError, match="field 2"):
        hrrr_field.decode_message(payload, api, field_index=2)


def test_parallel_field_requests_do_not_overlap_native_decoding(monkeypatch):
    """Independent browser tabs must not enter ecCodes' global parser together."""
    monkeypatch.setattr(hrrr_field, "HRRR_GRID", replace(hrrr_field.HRRR_GRID, shape=(1, 1)))
    start = threading.Barrier(2)
    active = 0
    peak = 0

    def open_message(_payload):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        time.sleep(0.04)
        return 1

    def release(_handle):
        nonlocal active
        active -= 1

    api = SimpleNamespace(
        codes_new_from_message=open_message,
        codes_get=lambda _handle, key: 9999 if key == "missingValue" else 1,
        codes_get_values=lambda _handle: [42.0],
        codes_release=release,
    )

    def decode(_index):
        start.wait(timeout=5)
        return hrrr_field.decode_message(b"GRIB", api)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(decode, range(2)))
    assert all(result.item() == 42 for result in results)
    assert peak == 1
    assert active == 0


def test_numeric_hover_does_not_copy_the_entire_remembered_frame():
    """A cell read cannot allocate another full-resolution field per hover."""
    frame = np.full((1536, 3072), 42.0, dtype=np.float32)
    hrrr_field.remember_derived("refc", RUN, 6, (3072, 1536), frame)
    try:
        # Warm imports and lookup helpers outside the measured allocation.
        assert hrrr_field.sample_derived("refc", RUN, 6, 35.0, -97.0)[0] == 42.0
        tracemalloc.start()
        try:
            values = [hrrr_field.sample_derived(
                "refc", RUN, 6, 35.0, -97.0)[0] for _ in range(3)]
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert values == [42.0] * 3
        assert peak < 2 * 1024 * 1024, (
            f"three cell reads allocated {peak / 1024 / 1024:.1f} MiB")
    finally:
        hrrr_field.clear_derived_cache()


def test_projection_index_cache_has_a_small_geometry_bound(monkeypatch):
    """Custom export sizes cannot retain unlimited multi-megabyte mappings."""
    monkeypatch.setattr(hrrr_field, "_INDEX_CACHE", OrderedDict())
    for width in range(16, 21):
        hrrr_field.index_map((width, 16), hrrr_field.COVERAGE_BOUNDS)
    assert len(hrrr_field._INDEX_CACHE) <= 2


def test_numeric_and_finished_frame_caches_stay_bounded(monkeypatch):
    """Run/hour churn cannot retain every large grid or rendered frame."""
    hrrr_field.clear_cache()
    monkeypatch.setattr(hrrr_field, "_disk_read", lambda _path: None)
    monkeypatch.setattr(hrrr_field, "_disk_write", lambda *_args: None)
    monkeypatch.setattr(hrrr_field, "_download_fields",
                        lambda *_args, **_kwargs: {"fake": True})
    monkeypatch.setattr(hrrr_field, "render_field",
                        lambda *_args, **_kwargs: b"fake-png")
    monkeypatch.setattr(hrrr_field_fetch, "OverlayRaster", lambda **kwargs:
                        SimpleNamespace(**kwargs))
    try:
        for hour in range(hrrr_field._CACHE_MAX_ENTRIES + 7):
            hrrr_field.fetch_field("refc", run=RUN, fxx=hour)
            hrrr_field.remember_derived(
                "refc", RUN, hour, (2, 2),
                np.full((2, 2), hour, dtype=np.float32))
        assert len(hrrr_field._CACHE) <= hrrr_field._CACHE_MAX_ENTRIES
        assert len(hrrr_field._DERIVED_CACHE) <= \
            hrrr_field._DERIVED_CACHE_MAX_ENTRIES
    finally:
        hrrr_field.clear_cache()


def test_simultaneous_panels_share_one_cold_projection(monkeypatch):
    """Four fields of the same geometry must not project four times."""
    from pyproj import Transformer

    calls = []
    lock = threading.Lock()

    class CountingTransformer:
        def transform(self, lon, lat):
            with lock:
                calls.append(1)
            time.sleep(0.04)  # make concurrent cold misses deterministic
            return (np.asarray(lon) * 0 + hrrr_field.HRRR_X0,
                    np.asarray(lat) * 0 + hrrr_field.HRRR_Y0)

    monkeypatch.setattr(Transformer, "from_crs", lambda *_args, **_kwargs:
                        CountingTransformer())
    monkeypatch.setattr(hrrr_field, "_INDEX_CACHE", OrderedDict())
    start = threading.Barrier(4)

    def projection(_index):
        start.wait(timeout=5)
        return hrrr_field.index_map((32, 16), hrrr_field.COVERAGE_BOUNDS)

    with ThreadPoolExecutor(max_workers=4) as pool:
        maps = list(pool.map(projection, range(4)))
    assert all(item is maps[0] for item in maps)
    assert len(calls) == 1


def test_simultaneous_panels_share_one_field_download_and_render(monkeypatch):
    """Identical panel requests share a cold frame, not merely warm cache hits."""
    downloads = []
    renders = []
    lock = threading.Lock()
    start = threading.Barrier(4)
    hrrr_field.clear_cache()
    monkeypatch.setattr(hrrr_field, "_disk_read", lambda _path: None)
    monkeypatch.setattr(hrrr_field, "_disk_write", lambda *_args: None)

    def download(*_args, **_kwargs):
        with lock:
            downloads.append(1)
        time.sleep(0.05)
        return {"fake": True}

    def render(*_args, **_kwargs):
        with lock:
            renders.append(1)
        return b"fake-png"

    monkeypatch.setattr(hrrr_field, "_download_fields", download)
    monkeypatch.setattr(hrrr_field, "render_field", render)
    monkeypatch.setattr(hrrr_field_fetch, "OverlayRaster", lambda **kwargs:
                        SimpleNamespace(**kwargs))

    def field(_index):
        start.wait(timeout=5)
        return hrrr_field.fetch_field("refc", run=RUN, fxx=6)

    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(field, range(4)))
        assert len(downloads) == 1
        assert len(renders) == 1
        assert all(item is results[0] for item in results)
    finally:
        hrrr_field.clear_cache()

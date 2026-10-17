"""T26 offline/empty/cancelled field states and retained-map recovery."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime, timedelta, timezone

import pytest
from qtpy.QtCore import QBuffer, QByteArray
from qtpy.QtGui import QColor, QImage

from sharpmod import hrrr_field
from sharpmod.map_overlays import OverlayRaster


RUN = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)


def _raster(product="refc", run=RUN, hour=6):
    image = QImage(8, 8, QImage.Format_RGBA8888)
    image.fill(QColor(80, 100, 130, 255))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.WriteOnly)
    assert image.save(buffer, "PNG")
    return OverlayRaster(
        key="hrrr_field", title="HRRR field", image_bytes=bytes(data),
        bounds=hrrr_field.COVERAGE_BOUNDS, short_name=product,
        subtitle=hrrr_field.field_valid_label(run, hour),
        valid_time=run + timedelta(hours=hour),
        source_url=hrrr_field.grib_url(
            run, hour, hrrr_field.get_product(product).sources[0]),
        retrieved_at=RUN + timedelta(hours=hour + 1))


def test_transport_failure_is_distinct_from_unpublished_data(monkeypatch):
    class BrokenSession:
        def get(self, *_args, **_kwargs):
            raise TimeoutError("connection timed out")

    monkeypatch.setattr(hrrr_field, "_session", lambda: BrokenSession())
    with pytest.raises(hrrr_field.HrrrFieldOffline):
        hrrr_field._default_opener("https://example.invalid", 0.1, 100)


def test_unpublished_negative_cache_keeps_its_no_data_type(monkeypatch):
    calls = []
    hrrr_field.clear_cache()
    monkeypatch.setattr(hrrr_field, "_disk_read", lambda _path: None)

    def unpublished(*_args, **_kwargs):
        calls.append(1)
        raise hrrr_field.HrrrFieldUnavailable("hour not published")

    monkeypatch.setattr(hrrr_field, "_download_fields", unpublished)
    try:
        for _ in range(2):
            with pytest.raises(hrrr_field.HrrrFieldUnavailable,
                               match="not published"):
                hrrr_field.fetch_field("refc", run=RUN, fxx=6)
        assert calls == [1]
    finally:
        hrrr_field.clear_cache()


def test_offline_failure_is_retryable_and_does_not_evict_other_panels(monkeypatch):
    """Only unpublished frames get a negative entry; peers stay cached."""
    hrrr_field.clear_cache()
    peer_key = ("sbcape", RUN.isoformat(), 6,
                hrrr_field.DEFAULT_FRAME_SIZE)
    retained = _raster(product="sbcape")
    with hrrr_field._CACHE_LOCK:
        hrrr_field._CACHE[peer_key] = (float("inf"), retained)
    monkeypatch.setattr(hrrr_field, "_disk_read", lambda _path: None)
    calls = []

    def offline(*_args, **_kwargs):
        calls.append(1)
        raise hrrr_field.HrrrFieldOffline("network disconnected")

    monkeypatch.setattr(hrrr_field, "_download_fields", offline)
    try:
        for _ in range(2):
            with pytest.raises(hrrr_field.HrrrFieldOffline):
                hrrr_field.fetch_field("refc", run=RUN, fxx=6)
        assert calls == [1, 1]
        assert hrrr_field.invalidate_frame("sbcape", run=RUN, fxx=6,
                                           failed_only=True) is False
        with hrrr_field._CACHE_LOCK:
            assert hrrr_field._CACHE[peer_key][1] is retained
            assert all(key[0] != "refc" for key in hrrr_field._CACHE)
    finally:
        hrrr_field.clear_cache()


def test_targeted_retry_forgets_only_the_selected_negative_frame(monkeypatch):
    hrrr_field.clear_cache()
    monkeypatch.setattr(hrrr_field, "_disk_read", lambda _path: None)

    def unpublished(*_args, **_kwargs):
        raise hrrr_field.HrrrFieldUnavailable("not yet published")

    monkeypatch.setattr(hrrr_field, "_download_fields", unpublished)
    try:
        for product in ("refc", "sbcape"):
            with pytest.raises(hrrr_field.HrrrFieldUnavailable):
                hrrr_field.fetch_field(product, run=RUN, fxx=6)
        assert hrrr_field.invalidate_frame("refc", run=RUN, fxx=6,
                                           failed_only=True)
        with hrrr_field._CACHE_LOCK:
            assert {key[0] for key in hrrr_field._CACHE} == {"sbcape"}
    finally:
        hrrr_field.clear_cache()


def test_failed_refresh_keeps_the_last_successful_memory_frame(monkeypatch):
    hrrr_field.clear_cache()
    # A fresh hosted runner may have monotonic uptime below the normal TTL.
    # Refresh must expire the frame even with a longer-than-uptime TTL.
    monkeypatch.setattr(hrrr_field, "FRAME_CACHE_TTL_S", float("inf"))
    key = ("refc", RUN.isoformat(), 6, hrrr_field.DEFAULT_FRAME_SIZE)
    retained = _raster()
    with hrrr_field._CACHE_LOCK:
        hrrr_field._CACHE[key] = (float("inf"), retained)
    monkeypatch.setattr(hrrr_field, "_disk_read", lambda _path: None)

    def unpublished(*_args, **_kwargs):
        raise hrrr_field.HrrrFieldUnavailable("not yet published")

    monkeypatch.setattr(hrrr_field, "_download_fields", unpublished)
    try:
        assert hrrr_field.invalidate_frame("refc", run=RUN, fxx=6)
        with pytest.raises(hrrr_field.HrrrFieldUnavailable):
            hrrr_field.fetch_field("refc", run=RUN, fxx=6)
        with hrrr_field._CACHE_LOCK:
            assert hrrr_field._CACHE[key][1] is retained
    finally:
        hrrr_field.clear_cache()

"""A failed byte range must release siblings and keep the original error."""

from datetime import datetime, timezone
from threading import Event, Lock
import time
from types import SimpleNamespace

import pytest

from sharpmod.providers import hrrr_field as fields
from sharpmod.providers.hrrr_products import FieldSpec
from sharpmod.models.model_transport import DownloadCancelled


def test_cancelled_field_opener_does_not_start_a_connection(monkeypatch):
    monkeypatch.setattr(fields, "_session", lambda: pytest.fail("cancelled request opened an HTTP session"))
    with pytest.raises(DownloadCancelled):
        fields._default_opener("https://example.invalid/field", 1, 100, should_cancel=lambda: True)


def test_failed_range_stops_siblings_and_preserves_the_provider_error(monkeypatch):
    specs = tuple(FieldSpec(f"t{i}", "prs", "TMP", f"{300 + i} mb") for i in range(8))
    rows = tuple(fields.InventoryRow(i + 1, i * 1048576, 16, "TMP", spec.level, "1 hour fcst") for i, spec in enumerate(specs))
    product = SimpleNamespace(key="test", all_fields=specs)
    monkeypatch.setattr(fields, "fetch_inventory", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(fields, "_load_eccodes", lambda: object())
    monkeypatch.setattr(fields, "_RANGE_WORKERS", 2)
    started, stopped = Event(), Event()
    lock = Lock()
    calls = []
    failure = fields.HrrrFieldOffline("provider failed")

    def opener(_url, _timeout, _limit, byte_range=None, *, should_cancel=None):
        with lock:
            calls.append(byte_range)
        if byte_range[0] == 1048576:
            assert started.wait(2)
            raise failure
        started.set()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if should_cancel is not None and should_cancel():
                stopped.set()
                raise DownloadCancelled("sibling stopped")
            time.sleep(.005)
        raise fields.HrrrFieldOffline("sibling did not stop after provider failure")

    monkeypatch.setattr(fields, "_default_opener", opener)
    with pytest.raises(fields.HrrrFieldOffline, match="^provider failed$") as raised:
        fields._download_fields(product, datetime(2026, 10, 19, tzinfo=timezone.utc), 1,
                                opener=opener, timeout=1, should_cancel=lambda: False)
    assert raised.value is failure
    assert stopped.is_set()
    assert len(calls) < len(rows), "queued ranges still started after the request failed"

"""Saturated work queues must retain a fast cancellation and progress path."""

from concurrent.futures import ThreadPoolExecutor
import io
from threading import Event, Thread
import time

import pytest

from sharpmod.bridge import cancellation, fields, handlers, server
from sharpmod.bridge.protocol import encode_frame
from sharpmod.tests.test_bridge_protocol import Feed, Sink


def test_full_queue_keeps_cancel_progress_and_hello_responsive(monkeypatch):
    started = Event()
    def busy(_params):
        started.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            cancellation.check()
            time.sleep(0.005)
        pytest.fail("cancel never reached active work")
    monkeypatch.setitem(handlers.HANDLERS, "test.busy", busy)
    monkeypatch.setattr(server, "WORKERS", 1)
    monkeypatch.setattr(server, "MAX_ADMITTED", 2)
    feed, sink = Feed(), Sink()
    thread = Thread(target=server.main, args=(feed, sink))
    thread.start()
    def send(request_id, method, params):
        feed.push(encode_frame({"id": request_id, "method": method, "params": params}))
    try:
        send(1, "test.busy", {"request_id": "first"})
        assert started.wait(5)
        send(2, "test.busy", {"request_id": "queued"})
        send(3, "test.busy", {})
        send(4, "hello", {"protocol_version": 1})
        send(5, "soundings.progress", {"progress_id": "missing"})
        sink.wait_frames(3)
        replies = {frame["id"]: frame for frame in sink.frames()}
        assert replies[3]["error"]["retryable"]
        assert replies[4]["result"]["protocol_version"] == 1
        assert replies[5]["result"]["downloaded_bytes"] == 0
        send(6, "cancel", {"request_id": "queued"})
        send(7, "cancel", {"id": 1})
        sink.wait_frames(7)
        replies = {frame["id"]: frame for frame in sink.frames()}
        assert replies[1]["error"]["details"]["cancelled"]
        assert replies[2]["error"]["details"]["cancelled"]
    finally:
        send(8, "shutdown", {})
        thread.join(10)
    assert not thread.is_alive()
    assert not cancellation._ACTIVE


def test_cancelling_one_shared_acquisition_preserves_another_consumer(monkeypatch):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    entered, finish = Event(), Event()
    first, second = Event(), Event()
    product = SimpleNamespace(key="test", contour=None, wind=False)
    calls = []
    def produce(_model, _product, _run, _hour, _target, stopped):
        calls.append(1)
        entered.set()
        assert finish.wait(5)
        assert not stopped()
        return {"shared": True}, None
    monkeypatch.setattr(fields, "_produce", produce)
    from sharpmod.bridge import frame_cache
    monkeypatch.setattr(frame_cache, "read", lambda _target: None)
    def acquire(event):
        with cancellation.scope(event):
            return fields._acquire("hrrr", product, datetime(2026, 10, 18, tzinfo=UTC), 0)
    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(acquire, first)
        assert entered.wait(5)
        b = pool.submit(acquire, second)
        deadline = time.monotonic() + 5
        while len(next(iter(fields._FLIGHTS.values()))[1]) < 2:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        second.set()
        with pytest.raises(cancellation.Cancelled):
            b.result(timeout=5)
        finish.set()
        assert a.result(timeout=5) == ({"shared": True}, None)
    assert calls == [1] and not fields._FLIGHTS


def test_stream_cancellation_stops_before_reading_the_remaining_bytes():
    from sharpmod.models.model_transport import DownloadCancelled
    from sharpmod.providers.hrrr_field import _read_body

    stream = io.BytesIO(b"x" * 200000)
    with pytest.raises(DownloadCancelled):
        _read_body(stream, 200000, lambda: stream.tell() >= 65536)
    assert stream.tell() == 65536

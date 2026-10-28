"""Saturated work queues must retain a fast cancellation and progress path."""

from concurrent.futures import ThreadPoolExecutor
import io
from threading import Event, Thread
import time

import pytest

from sharpmod.bridge import cancellation, fields, handlers, server
from sharpmod.bridge.protocol import encode_frame
from sharpmod.tests.test_bridge_protocol import Feed, Sink


@pytest.mark.parametrize("control", ["wire", "token", "early-token"])
def test_cancelled_queue_releases_admission_before_running_work_finishes(monkeypatch, control):
    started, finish = Event(), Event()
    queued_calls = []
    def busy(_params):
        started.set()
        assert finish.wait(5)
        return {"ok": True}
    def queued(_params):
        queued_calls.append(1)
        return {"ok": True}
    monkeypatch.setitem(handlers.HANDLERS, "test.busy", busy)
    monkeypatch.setitem(handlers.HANDLERS, "test.queued", queued)
    monkeypatch.setitem(handlers.HANDLERS, "test.fresh", lambda _params: {"fresh": True})
    monkeypatch.setattr(server, "WORKERS", 1)
    monkeypatch.setattr(server, "MAX_ADMITTED", 2)
    sink = Sink()
    bridge = server._Server(io.BytesIO(), sink)
    def send(request_id, method, params):
        bridge._dispatch(encode_frame({"id": request_id, "method": method, "params": params}))
    try:
        send(1, "test.busy", {"request_id": "running"})
        assert started.wait(5)
        if control == "early-token":
            send(3, "cancel", {"request_id": "queued"})
        send(2, "test.queued", {"request_id": "queued"})
        if control != "early-token":
            send(3, "cancel", {"id": 2} if control == "wire" else {"request_id": "queued"})
        send(4, "test.fresh", {})
        replies = {frame["id"]: frame for frame in sink.frames()}
        assert 4 not in replies, "cancelled queued work still blocks new admission"
        assert replies[2]["error"]["details"]["cancelled"]
        assert replies[3]["result"]["ok"]
    finally:
        finish.set()
        bridge._pool.shutdown(wait=True)
    replies = {frame["id"]: frame for frame in sink.frames()}
    assert replies[4]["result"] == {"fresh": True}
    assert queued_calls == []
    assert not bridge._requests and not bridge._futures and not cancellation._ACTIVE


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


@pytest.mark.parametrize("model", ["hrrr", "nam-3km-conus", "rrfs-a", "refs-m1"])
def test_fresh_consumers_replace_an_abandoned_field_without_losing_their_flight(monkeypatch, model):
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from sharpmod.bridge import frame_cache

    abandoned, old_finish, fresh_started, fresh_finish = (Event() for _ in range(4))
    old_stop = Event()
    product = SimpleNamespace(key="cancel-rejoin", contour=None, wind=False)
    calls = []

    def produce(_model, _product, _run, _hour, _target, stopped):
        calls.append(1)
        if len(calls) == 1:
            abandoned.set()
            assert old_finish.wait(5)
            assert stopped()
            raise cancellation.Cancelled()
        fresh_started.set()
        assert fresh_finish.wait(5)
        assert not stopped()
        return {"fresh": True}, None

    monkeypatch.setattr(fields, "_produce", produce)
    monkeypatch.setattr(frame_cache, "read", lambda _target: None)

    def acquire(event):
        with cancellation.scope(event):
            return fields._acquire(model, product, datetime(2026, 10, 19, tzinfo=UTC), 0)

    with ThreadPoolExecutor(3) as pool:
        first = pool.submit(acquire, old_stop)
        try:
            assert abandoned.wait(5)
            old_stop.set()
            second = pool.submit(acquire, Event())
            assert fresh_started.wait(1), "fresh consumer joined an already abandoned producer"
            old_finish.set()
            with pytest.raises(cancellation.Cancelled):
                first.result(timeout=5)
            assert len(fields._FLIGHTS) == 1, "old cleanup removed the replacement producer"
            third = pool.submit(acquire, Event())
            deadline = time.monotonic() + 5
            while len(next(iter(fields._FLIGHTS.values()))[1]) < 2:
                assert time.monotonic() < deadline
                time.sleep(0.005)
            fresh_finish.set()
            assert second.result(timeout=5) == third.result(timeout=5) == ({"fresh": True}, None)
        finally:
            old_finish.set()
            fresh_finish.set()
    assert len(calls) == 2 and not fields._FLIGHTS

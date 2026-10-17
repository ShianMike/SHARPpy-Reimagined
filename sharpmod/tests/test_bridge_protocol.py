"""In-process tests of the sharpmod bridge wire protocol and read loop.

``server.main(stdin, stdout)`` runs against in-memory streams; in that mode it
must not touch any process-wide stream or logging handler.
"""

from __future__ import annotations

import io
import json
import logging
import sys
import threading

import pytest

from sharpmod.bridge import handlers, server
from sharpmod.bridge.errors import ERROR_CODES, BridgeError, error_frame
from sharpmod.bridge.protocol import (
    MAX_FRAME_BYTES,
    MAX_REQUEST_ID,
    decode_request,
    encode_frame,
)

HELLO = b'{"id":1,"method":"hello","params":{"protocol_version":1}}\n'


class Feed:
    """Blocking binary input: queued bytes, then EOF once closed."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._closed = False
        self._cond = threading.Condition()

    def push(self, data: bytes) -> None:
        with self._cond:
            self._buf += data
            self._cond.notify_all()

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def readline(self, limit: int = -1) -> bytes:
        def ready() -> bool:
            return (b"\n" in self._buf or self._closed
                    or 0 <= limit <= len(self._buf))

        with self._cond:
            assert self._cond.wait_for(ready, timeout=10), "read loop starved"
            end = self._buf.find(b"\n")
            end = len(self._buf) if end < 0 else end + 1
            if limit >= 0:
                end = min(end, limit)
            line = bytes(self._buf[:end])
            del self._buf[:end]
            return line


class Sink:
    """Thread-safe binary output that can wait for complete frames."""

    def __init__(self, max_write: int | None = None) -> None:
        self.data = bytearray()
        self.writes = 0
        self._max = max_write
        self._cond = threading.Condition()

    def write(self, chunk) -> int:
        with self._cond:
            taken = bytes(chunk[: self._max] if self._max else chunk)
            self.data += taken
            self.writes += 1
            self._cond.notify_all()
            return len(taken)

    def wait_frames(self, count: int) -> None:
        with self._cond:
            assert self._cond.wait_for(
                lambda: self.data.count(b"\n") >= count, timeout=10
            ), f"expected {count} frames, got {bytes(self.data)!r}"

    def frames(self) -> list[dict]:
        return [json.loads(line) for line in bytes(self.data).splitlines()]


def _process_state():
    return sys.stdout, sys.stdin, list(logging.getLogger().handlers)


def _assert_state_unchanged(before) -> None:
    after = _process_state()
    assert after[0] is before[0] and after[1] is before[1]
    assert len(after[2]) == len(before[2])
    assert all(a is b for a, b in zip(after[2], before[2], strict=True))


def exchange(*frames: bytes, expect: int, sink: Sink | None = None) -> list[dict]:
    """Send ``frames``, wait for ``expect`` replies, then close the input."""
    feed, out = Feed(), sink or Sink()
    before = _process_state()
    codes: list[int] = []
    thread = threading.Thread(target=lambda: codes.append(server.main(feed, out)))
    thread.start()
    for frame in frames:
        feed.push(frame)
    out.wait_frames(expect)
    feed.close()
    thread.join(10)
    assert not thread.is_alive() and codes == [0]
    _assert_state_unchanged(before)
    return out.frames()


def run_sync(data: bytes) -> list[dict]:
    """Run frames that are answered without the worker pool, over BytesIO."""
    out = io.BytesIO()
    before = _process_state()
    assert server.main(io.BytesIO(data), out) == 0
    _assert_state_unchanged(before)
    return [json.loads(line) for line in out.getvalue().splitlines()]


def test_encode_frame_is_compact_utf8_and_rejects_non_finite():
    assert encode_frame({"a": [1, "\u00df"]}) == '{"a":[1,"\u00df"]}\n'.encode()
    with pytest.raises(ValueError):
        encode_frame({"lat": float("nan")})
    with pytest.raises(ValueError):
        encode_frame({"x": float("inf")})
    with pytest.raises(ValueError):
        encode_frame({"pad": "x" * MAX_FRAME_BYTES})


def test_hello_round_trip_and_crlf_input():
    (reply,) = exchange(HELLO.replace(b"\n", b"\r\n"), expect=1)
    assert reply["id"] == 1
    assert reply["result"]["protocol_version"] == 1
    assert set(reply["result"]) == {
        "protocol_version", "sharpmod_version", "python_version",
        "python_executable", "pid",
    }


@pytest.mark.parametrize("line", [
    b"not json\n",
    b'{"id": 1, "method": "hello", "params": {"protocol_version": NaN}}\n',
    b"[1, 2]\n",
    b"\xff\xfe\n",
    b'{"method":"hello","params":{}}\n',
    b'{"id":0,"method":"hello","params":{}}\n',
    b'{"id":true,"method":"hello","params":{}}\n',
    b'{"id":"1","method":"hello","params":{}}\n',
    b'{"id":1.5,"method":"hello","params":{}}\n',
    ('{"id":%d,"method":"hello","params":{}}\n' % (MAX_REQUEST_ID + 1)).encode(),
])
def test_unparsable_frames_get_a_null_id_reply(line):
    replies = run_sync(line + b'{"id":9,"method":"shutdown","params":{}}\n')
    assert replies[0]["id"] is None
    assert replies[0]["error"]["code"] == "BRIDGE_PROTOCOL"
    assert replies[0]["error"]["retryable"] is False
    assert replies[1] == {"id": 9, "result": {"ok": True}}


@pytest.mark.parametrize(("line", "code"), [
    (b'{"id":4,"method":"hello"}\n', "BRIDGE_PROTOCOL"),
    (b'{"id":4,"method":"hello","params":{},"extra":1}\n', "BRIDGE_PROTOCOL"),
    (b'{"id":4,"method":7,"params":{}}\n', "BRIDGE_PROTOCOL"),
    (b'{"id":4,"method":"health","params":[]}\n', "INVALID_ARGUMENT"),
    (b'{"id":4,"method":"health","params":{"x":1}}\n', "INVALID_ARGUMENT"),
    (b'{"id":4,"method":"hello","params":{}}\n', "INVALID_ARGUMENT"),
    (b'{"id":4,"method":"hello","params":{"protocol_version":2}}\n', "INVALID_ARGUMENT"),
    (b'{"id":4,"method":"hello","params":{"protocol_version":true}}\n',
     "INVALID_ARGUMENT"),
    (b'{"id":4,"method":"hello","params":{"protocol_version":1,"x":0}}\n',
     "INVALID_ARGUMENT"),
    (b'{"id":4,"method":"stations.search","params":{}}\n', "METHOD_NOT_FOUND"),
    (b'{"id":4,"method":"ensemble.summary","params":{}}\n', "METHOD_NOT_FOUND"),
])
def test_bad_requests_are_answered_under_their_id(line, code):
    (reply,) = run_sync(line)
    assert reply["id"] == 4
    assert reply["error"]["code"] == code
    assert reply["error"]["retryable"] is False
    if code == "METHOD_NOT_FOUND":
        assert reply["error"]["details"] == {"method": json.loads(line)["method"]}


def test_oversized_line_is_drained_and_rejected():
    big = b'{"id":2,"pad":"' + b"x" * MAX_FRAME_BYTES + b'"}\n'
    replies = exchange(big, HELLO, expect=2)
    assert replies[0]["id"] is None
    assert replies[0]["error"]["code"] == "BRIDGE_PROTOCOL"
    assert replies[1]["id"] == 1 and "result" in replies[1]


def test_frame_of_exactly_the_cap_is_accepted_with_crlf():
    head = b'{"id":3,"method":"hello","params":{"protocol_version":1}'
    frame = head + b" " * (MAX_FRAME_BYTES - len(head) - 1) + b"}"
    assert len(frame) == MAX_FRAME_BYTES
    assert decode_request(frame + b"\r\n").id == 3
    (reply,) = exchange(frame + b"\r\n", expect=1)
    assert reply["id"] == 3 and "result" in reply
    with pytest.raises(Exception, match="8 MiB"):
        decode_request(frame + b" \n")


def test_handler_errors_map_to_error_frames(monkeypatch):
    def fails(params):
        raise BridgeError("CATALOG_UNAVAILABLE", "Nope.", True, {"k": 1})

    def explodes(params):
        raise RuntimeError("boom")

    def non_finite(params):
        return {"x": float("nan")}

    monkeypatch.setitem(handlers.HANDLERS, "test.fails", fails)
    monkeypatch.setitem(handlers.HANDLERS, "test.explodes", explodes)
    monkeypatch.setitem(handlers.HANDLERS, "test.nan", non_finite)
    replies = exchange(
        b'{"id":1,"method":"test.fails","params":{}}\n',
        b'{"id":2,"method":"test.explodes","params":{}}\n',
        b'{"id":3,"method":"test.nan","params":{}}\n',
        expect=3,
    )
    by_id = {r["id"]: r["error"] for r in replies}
    assert by_id[1] == {"code": "CATALOG_UNAVAILABLE", "message": "Nope.",
                        "retryable": True, "details": {"k": 1}}
    for request_id, method in ((2, "test.explodes"), (3, "test.nan")):
        assert by_id[request_id] == {
            "code": "INTERNAL",
            "message": f"Unexpected error in {method}; see the log.",
            "retryable": False,
            "details": {"method": method},
        }
    assert all("Traceback" not in json.dumps(e) for e in by_id.values())


def test_responses_arrive_out_of_order(monkeypatch):
    released = threading.Event()

    def slow(params):
        assert released.wait(10)
        return {"which": "slow"}

    def fast(params):
        released.set()
        return {"which": "fast"}

    monkeypatch.setitem(handlers.HANDLERS, "test.slow", slow)
    monkeypatch.setitem(handlers.HANDLERS, "test.fast", fast)
    replies = exchange(
        b'{"id":1,"method":"test.slow","params":{}}\n',
        b'{"id":2,"method":"test.fast","params":{}}\n',
        expect=2,
    )
    assert [r["id"] for r in replies] == [2, 1]


def test_short_writes_never_splice_frames(monkeypatch):
    monkeypatch.setitem(handlers.HANDLERS, "test.big", lambda p: {"s": "y" * 500})
    sink = Sink(max_write=7)
    frames = [b'{"id":%d,"method":"test.big","params":{}}\n' % i for i in range(1, 9)]
    replies = exchange(*frames, expect=8, sink=sink)
    assert sorted(r["id"] for r in replies) == list(range(1, 9))
    assert all(r["result"] == {"s": "y" * 500} for r in replies)
    assert sink.writes > 8 * 70


def test_shutdown_replies_and_stops_reading():
    replies = run_sync(b'{"id":5,"method":"shutdown","params":{}}\n' + HELLO)
    assert replies == [{"id": 5, "result": {"ok": True}}]


def test_shutdown_waits_for_work_already_running(monkeypatch):
    started, release = threading.Event(), threading.Event()

    def busy(params):
        started.set()
        assert release.wait(10)
        return {"done": True}

    monkeypatch.setitem(handlers.HANDLERS, "test.busy", busy)
    feed, sink = Feed(), Sink()
    codes: list[int] = []
    thread = threading.Thread(target=lambda: codes.append(server.main(feed, sink)))
    thread.start()
    feed.push(b'{"id":1,"method":"test.busy","params":{}}\n')
    assert started.wait(10)
    feed.push(b'{"id":2,"method":"shutdown","params":{}}\n')
    sink.wait_frames(1)
    assert thread.is_alive()
    release.set()
    thread.join(10)
    assert not thread.is_alive() and codes == [0]
    assert sink.frames() == [{"id": 2, "result": {"ok": True}},
                             {"id": 1, "result": {"done": True}}]


def test_eof_and_partial_final_line_end_the_loop():
    assert run_sync(b"") == []
    assert run_sync(b'{"id":1,"method":"hel') == []


def test_main_requires_both_streams_or_neither():
    with pytest.raises(TypeError):
        server.main(io.BytesIO(), None)


def test_error_frames_and_closed_code_set():
    err = BridgeError("INTERNAL", "Something.", False)
    assert error_frame(None, err) == {
        "id": None, "error": {"code": "INTERNAL", "message": "Something.",
                              "retryable": False}}
    assert len(ERROR_CODES) == 14
    with pytest.raises(ValueError):
        BridgeError("BACKEND_UNAVAILABLE", "Health-only code.", False)

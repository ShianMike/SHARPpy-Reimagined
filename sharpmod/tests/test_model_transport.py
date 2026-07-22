"""Optimized forecast-model field and byte-range transport regressions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import time

import pandas as pd
import pytest

from sharpmod.model_fields import (
    build_noaa_search,
    choose_noaa_fields,
)
from sharpmod.model_transport import (
    ByteRange,
    DownloadCancelled,
    OptimizedTransportUnavailable,
    RangeTransferMetrics,
    download_herbie_subset,
    download_ranges,
    parallelize_range_plan,
    plan_ranges,
    range_worker_count,
)


def _inventory(*variables):
    return pd.DataFrame({"variable": list(variables)})


def test_noaa_search_keeps_levels_but_prunes_equivalent_fields():
    inventory = _inventory(
        "HGT", "TMP", "RH", "SPFH", "UGRD", "VGRD",
        "VVEL", "DZDT", "ABSV",
    )

    fields = choose_noaa_fields(inventory)

    assert fields == (
        "HGT", "TMP", "UGRD", "VGRD", "RH", "VVEL", "ABSV"
    )
    search = build_noaa_search(fields)
    assert ":SPFH:" not in search
    assert ":DZDT:" not in search
    assert "\\d+(?:\\.\\d+)? mb:" in search


def test_noaa_search_falls_back_to_specific_humidity_and_dzdt():
    fields = choose_noaa_fields(
        _inventory("HGT", "TMP", "SPFH", "UGRD", "VGRD", "DZDT")
    )

    assert fields == ("HGT", "TMP", "UGRD", "VGRD", "SPFH", "DZDT")


def test_noaa_search_requires_sounding_core_fields():
    with pytest.raises(ValueError, match="missing required pressure fields"):
        choose_noaa_fields(_inventory("HGT", "TMP", "RH", "UGRD"))


def test_range_plan_merges_small_gaps_under_global_overhead_budget():
    rows = [(0, 99), (120, 199), (5000, 5099)]

    assert plan_ranges(
        rows, max_gap=64, max_overhead_ratio=0.25
    ) == [ByteRange(0, 199), ByteRange(5000, 5099)]


def test_range_plan_does_not_spend_more_than_global_overhead_budget():
    rows = [(0, 99), (120, 219), (240, 339), (360, 459)]

    planned = plan_ranges(rows, max_gap=64, max_overhead_ratio=0.10)

    assert planned == [ByteRange(0, 339), ByteRange(360, 459)]


def test_range_plan_rejects_open_or_invalid_ranges():
    with pytest.raises(OptimizedTransportUnavailable, match="ending byte"):
        plan_ranges([(0, None)])


def test_parallel_plan_splits_one_large_contiguous_span_without_byte_changes():
    original = [ByteRange(100, 10 * 1024 * 1024 + 99)]

    planned = parallelize_range_plan(original, 4)

    assert len(planned) == 4
    assert planned[0].start == original[0].start
    assert planned[-1].end == original[0].end
    assert sum(item.size for item in planned) == original[0].size
    assert all(
        left.end + 1 == right.start
        for left, right in zip(planned, planned[1:])
    )


def test_parallel_plan_keeps_small_spans_intact():
    original = [ByteRange(0, 1024 * 1024 - 1)]

    assert parallelize_range_plan(original, 4) == original
    with pytest.raises(OptimizedTransportUnavailable, match="invalid byte range"):
        plan_ranges([(100, 99)])


def test_range_plan_consolidates_shared_vector_message_offsets():
    assert plan_ranges(
        [(100, 99), (100, 199), (200, 249)]
    ) == [ByteRange(100, 249)]


@dataclass
class _Response:
    status_code: int
    content: bytes
    headers: dict[str, str]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("HTTP failure")

    def iter_content(self, chunk_size=64 * 1024):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset:offset + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _RangeSession:
    def __init__(self, payload, ignore_ranges=False):
        self.payload = payload
        self.ignore_ranges = ignore_ranges
        self.requests = []

    def get(self, _url, headers, stream, timeout):
        assert stream is True
        assert timeout
        value = headers["Range"].removeprefix("bytes=")
        start, end = (int(part) for part in value.split("-"))
        self.requests.append((start, end))
        if self.ignore_ranges:
            return _Response(200, self.payload, {})
        data = self.payload[start:end + 1]
        return _Response(
            206,
            data,
            {
                "Content-Range": f"bytes {start}-{end}/{len(self.payload)}",
                "ETag": '"fixture"',
            },
        )


class _ConcurrentResponse(_Response):
    def __init__(self, *args, gate=None, delay=0.0, **kwargs):
        super().__init__(*args, **kwargs)
        self.gate = gate
        self.delay = float(delay)

    def iter_content(self, chunk_size=64 * 1024):
        if self.gate is not None:
            self.gate.wait(timeout=2.0)
        if self.delay:
            time.sleep(self.delay)
        yield from super().iter_content(chunk_size=chunk_size)


class _ConcurrentServer:
    def __init__(self, payload, *, changed_start=None):
        self.payload = payload
        self.changed_start = changed_start
        self.lock = threading.Lock()
        self.next_session = 0
        self.requests = []
        self.gate = threading.Barrier(2)

    def session(self):
        with self.lock:
            session_id = self.next_session
            self.next_session += 1
        server = self

        class Session:
            def get(self, _url, headers, stream, timeout):
                assert stream is True
                assert timeout
                start, end = (
                    int(part) for part in
                    headers["Range"].removeprefix("bytes=").split("-")
                )
                with server.lock:
                    server.requests.append((session_id, start, end, headers))
                etag = '"v2"' if server.changed_start == start else '"v1"'
                is_probe = start == 0 and end == 0 and session_id == 0
                return _ConcurrentResponse(
                    206,
                    server.payload[start:end + 1],
                    {
                        "Content-Range": (
                            f"bytes {start}-{end}/{len(server.payload)}"
                        ),
                        "ETag": etag,
                        "Last-Modified": "Wed, 22 Jul 2026 00:00:00 GMT",
                    },
                    gate=(
                        None if is_probe or server.changed_start is not None
                        else server.gate
                    ),
                    delay=0.03 if start == 0 and not is_probe else 0.0,
                )

            def close(self):
                pass

        return Session()


def test_completed_fragment_is_reused_and_output_is_atomic(tmp_path):
    payload = b"GRIB" + (b"x" * 24) + b"7777"
    session = _RangeSession(payload)
    ranges = [ByteRange(0, len(payload) - 1)]
    output = tmp_path / "subset.grib2"

    download_ranges(session, "https://data/file", ranges, output)
    download_ranges(session, "https://data/file", ranges, output)

    assert output.read_bytes() == payload
    assert session.requests == [(0, len(payload) - 1)]
    assert not list(tmp_path.glob("*.tmp"))


def test_partial_fragment_resumes_from_existing_size(tmp_path):
    payload = b"GRIB" + (b"x" * 24) + b"7777"
    output = tmp_path / "subset.grib2"
    fragments = tmp_path / ".subset.grib2.ranges"
    fragments.mkdir()
    (fragments / f"0-{len(payload) - 1}.part").write_bytes(payload[:9])
    session = _RangeSession(payload)

    download_ranges(
        session,
        "https://data/file",
        [ByteRange(0, len(payload) - 1)],
        output,
    )

    assert output.read_bytes() == payload
    assert session.requests == [(9, len(payload) - 1)]


def test_server_that_ignores_ranges_uses_fallback_exception(tmp_path):
    payload = b"GRIB" + (b"x" * 24) + b"7777"
    session = _RangeSession(payload, ignore_ranges=True)

    with pytest.raises(OptimizedTransportUnavailable, match="HTTP range"):
        download_ranges(
            session,
            "https://data/file",
            [ByteRange(0, len(payload) - 1)],
            tmp_path / "subset.grib2",
        )


def test_cancellation_preserves_resumable_fragment(tmp_path):
    payload = b"GRIB" + (b"x" * 100_000) + b"7777"
    session = _RangeSession(payload)
    calls = 0

    def cancelled():
        nonlocal calls
        calls += 1
        return calls > 1

    with pytest.raises(DownloadCancelled):
        download_ranges(
            session,
            "https://data/file",
            [ByteRange(0, len(payload) - 1)],
            tmp_path / "subset.grib2",
            cancelled=cancelled,
            chunk_size=4096,
        )

    fragments = tmp_path / ".subset.grib2.ranges"
    assert any(path.stat().st_size > 0 for path in fragments.glob("*.part"))


def test_herbie_subset_uses_planned_local_filename(tmp_path):
    payload = b"GRIB" + (b"x" * 24) + b"7777"
    session = _RangeSession(payload)

    class _Herbie:
        grib = "https://data/file"
        save_dir = tmp_path

        def inventory(self, search):
            assert search == ":chosen:"
            return pd.DataFrame({
                "start_byte": [0],
                "end_byte": [len(payload) - 1],
            })

        def get_localFilePath(self, search):
            assert search == ":chosen:"
            return Path(self.save_dir) / "subset.grib2"

    result, total = download_herbie_subset(
        _Herbie(), ":chosen:", save_dir=tmp_path, session=session
    )

    assert result == tmp_path / "subset.grib2"
    assert result.read_bytes() == payload
    assert total == len(payload)


def test_range_worker_count_is_independent_and_bounded(monkeypatch):
    monkeypatch.setenv("SHARPMOD_RANGE_WORKERS", "6")
    assert range_worker_count() == 6
    assert range_worker_count(0) == 1
    assert range_worker_count(999) == 8
    assert range_worker_count("bad", default=3) == 3


def test_parallel_ranges_use_per_worker_sessions_and_ordered_assembly(tmp_path):
    payload = b"GRIB" + (b"a" * 56) + b"7777"
    split = len(payload) // 2
    server = _ConcurrentServer(payload)
    coordinator = server.session()
    progress = []

    result = download_ranges(
        coordinator,
        "https://data/file",
        [ByteRange(0, split - 1), ByteRange(split, len(payload) - 1)],
        tmp_path / "parallel.grib2",
        workers=2,
        session_factory=server.session,
        progress=lambda completed, total: progress.append((completed, total)),
        retry_backoff=0,
    )

    worker_requests = [row for row in server.requests if row[1:3] != (0, 0)]
    assert result.read_bytes() == payload
    assert len({row[0] for row in worker_requests}) == 2
    assert [value for value, _total in progress] == sorted(
        value for value, _total in progress
    )
    assert progress[-1] == (len(payload), len(payload))


def test_parallel_identity_change_discards_all_fragments(tmp_path):
    payload = b"GRIB" + (b"b" * 56) + b"7777"
    split = len(payload) // 2
    server = _ConcurrentServer(payload, changed_start=split)
    output = tmp_path / "changed.grib2"

    with pytest.raises(OptimizedTransportUnavailable, match="changed"):
        download_ranges(
            server.session(),
            "https://data/file",
            [ByteRange(0, split - 1), ByteRange(split, len(payload) - 1)],
            output,
            workers=2,
            session_factory=server.session,
            retry_backoff=0,
        )

    assert not output.exists()
    assert not (tmp_path / ".changed.grib2.ranges").exists()


def test_transient_status_and_truncation_retry_only_failed_range(tmp_path):
    payload = b"GRIB" + (b"c" * 24) + b"7777"

    class RetrySession(_RangeSession):
        def __init__(self, value):
            super().__init__(value)
            self.calls = 0

        def get(self, url, headers, stream, timeout):
            self.calls += 1
            if self.calls == 1:
                value = headers["Range"].removeprefix("bytes=")
                start, end = (int(part) for part in value.split("-"))
                self.requests.append((start, end))
                return _Response(503, b"", {"Retry-After": "0"})
            response = super().get(url, headers, stream, timeout)
            if self.calls == 2:
                response.content = response.content[:7]
            return response

    session = RetrySession(payload)
    output = download_ranges(
        session,
        "https://data/file",
        [ByteRange(0, len(payload) - 1)],
        tmp_path / "retry.grib2",
        retries=2,
        retry_backoff=0,
    )

    assert output.read_bytes() == payload
    assert session.requests == [
        (0, len(payload) - 1),
        (0, len(payload) - 1),
        (7, len(payload) - 1),
    ]


def test_parallel_cancellation_preserves_fragments_and_resumes(tmp_path):
    payload = b"GRIB" + (b"d" * 120_000) + b"7777"
    quarter = len(payload) // 4
    ranges = [
        ByteRange(0, quarter - 1),
        ByteRange(quarter, quarter * 2 - 1),
        ByteRange(quarter * 2, quarter * 3 - 1),
        ByteRange(quarter * 3, len(payload) - 1),
    ]
    server = _ConcurrentServer(payload)
    cancel_event = threading.Event()
    output = tmp_path / "resumable.grib2"

    def progress(completed, _total):
        if completed:
            cancel_event.set()

    with pytest.raises(DownloadCancelled):
        download_ranges(
            server.session(),
            "https://data/file",
            ranges,
            output,
            workers=2,
            session_factory=server.session,
            cancelled=cancel_event.is_set,
            progress=progress,
            chunk_size=4096,
            retry_backoff=0,
        )

    fragments = tmp_path / ".resumable.grib2.ranges"
    assert fragments.exists()
    assert any(path.stat().st_size for path in fragments.glob("*.part"))
    initial_worker_requests = [
        row for row in server.requests if row[1:3] != (0, 0)
    ]
    assert len(initial_worker_requests) <= 2

    resumed_server = _ConcurrentServer(payload)
    result = download_ranges(
        resumed_server.session(),
        "https://data/file",
        ranges,
        output,
        workers=2,
        session_factory=resumed_server.session,
        retry_backoff=0,
    )

    assert result.read_bytes() == payload
    resumed_requests = [
        row for row in resumed_server.requests if row[1:3] != (0, 0)
    ]
    assert any(
        start not in {item.start for item in ranges}
        for _session_id, start, _end, _headers in resumed_requests
    )


def test_parallel_rejection_falls_back_to_sequential_ranges(
        tmp_path, monkeypatch):
    payload = bytearray(b"x" * 2_100_032)
    payload[:16] = b"GRIB" + (b"a" * 12)
    payload[-16:] = (b"b" * 12) + b"7777"
    payload = bytes(payload)
    starts = (0, len(payload) - 16)
    session_number = 0
    requests_seen = []
    lock = threading.Lock()

    class Session:
        def __init__(self):
            nonlocal session_number
            with lock:
                self.number = session_number
                session_number += 1

        def get(self, _url, headers, stream, timeout):
            start, end = (
                int(part) for part in
                headers["Range"].removeprefix("bytes=").split("-")
            )
            requests_seen.append((self.number, start, end))
            # Session zero coordinates the transfer and becomes the sequential
            # fallback. Worker-owned sessions emulate a server that rejects
            # concurrent range traffic.
            if self.number != 0:
                return _Response(429, b"", {"Retry-After": "0"})
            return _Response(
                206,
                payload[start:end + 1],
                {
                    "Content-Range": f"bytes {start}-{end}/{len(payload)}",
                    "ETag": '"stable"',
                },
            )

        def close(self):
            pass

    import requests
    monkeypatch.setattr(requests, "Session", Session)

    class Herbie:
        grib = "https://data/file"
        save_dir = tmp_path

        def inventory(self, _search):
            return pd.DataFrame({
                "start_byte": starts,
                "end_byte": (15, len(payload) - 1),
            })

        def get_localFilePath(self, _search):
            return tmp_path / "fallback.grib2"

    metrics = RangeTransferMetrics()
    result, total = download_herbie_subset(
        Herbie(), ":chosen:", workers=2, retries=0, metrics=metrics
    )

    assert result.read_bytes() == payload[:16] + payload[-16:]
    assert total == 32
    assert any(session_id != 0 for session_id, _start, _end in requests_seen)
    assert (0, starts[0], 15) in requests_seen
    assert (0, starts[1], len(payload) - 1) in requests_seen
    assert metrics.planned_bytes == 32
    assert metrics.transferred_bytes == 33
    assert metrics.fallback_used is True


def test_parallel_without_validator_downgrades_to_coordinator_session(tmp_path):
    payload = b"GRIB" + (b"n" * 56) + b"7777"
    split = len(payload) // 2
    requests_seen = []
    factory_calls = 0

    class Session:
        def get(self, _url, headers, stream, timeout):
            start, end = (
                int(part) for part in
                headers["Range"].removeprefix("bytes=").split("-")
            )
            requests_seen.append((start, end, dict(headers)))
            return _Response(
                206,
                payload[start:end + 1],
                {"Content-Range": f"bytes {start}-{end}/{len(payload)}"},
            )

        def close(self):
            pass

    def session_factory():
        nonlocal factory_calls
        factory_calls += 1
        return Session()

    metrics = RangeTransferMetrics()
    result = download_ranges(
        Session(),
        "https://data/file",
        [ByteRange(0, split - 1), ByteRange(split, len(payload) - 1)],
        tmp_path / "no-validator.grib2",
        workers=2,
        session_factory=session_factory,
        retry_backoff=0,
        metrics=metrics,
    )

    assert result.read_bytes() == payload
    assert factory_calls == 0
    assert metrics.worker_count == 1
    assert metrics.planned_bytes == len(payload)
    assert metrics.transferred_bytes == len(payload) + 1
    assert requests_seen[0][:2] == (0, 0)
    assert all("If-Range" not in headers for _start, _end, headers in requests_seen)


def test_cancellation_closes_a_response_blocked_in_iter_content(tmp_path):
    payload = b"GRIB" + (b"s" * 24) + b"7777"
    entered = threading.Event()
    released = threading.Event()
    cancel_event = threading.Event()

    class BlockingResponse(_Response):
        closed = False

        def iter_content(self, chunk_size=64 * 1024):
            entered.set()
            if not released.wait(timeout=5.0):
                raise TimeoutError("test response was not closed")
            if self.closed:
                raise OSError("response closed by cancellation")
            yield self.content

        def close(self):
            self.closed = True
            released.set()

    response = BlockingResponse(
        206,
        payload,
        {
            "Content-Range": f"bytes 0-{len(payload) - 1}/{len(payload)}",
            "ETag": '"blocking"',
        },
    )

    class Session:
        closed = False

        def get(self, *_args, **_kwargs):
            return response

        def close(self):
            self.closed = True
            response.close()

    session = Session()

    def request_cancel():
        assert entered.wait(timeout=1.0)
        cancel_event.set()

    trigger = threading.Thread(target=request_cancel, daemon=True)
    trigger.start()
    started = time.monotonic()
    with pytest.raises(DownloadCancelled):
        download_ranges(
            session,
            "https://data/file",
            [ByteRange(0, len(payload) - 1)],
            tmp_path / "blocked.grib2",
            cancelled=cancel_event.is_set,
            retry_backoff=0,
        )
    elapsed = time.monotonic() - started
    trigger.join(timeout=1.0)

    assert elapsed < 1.0
    assert response.closed is True
    assert session.closed is True


@pytest.mark.parametrize("workers", [1, 2, 4, 6])
def test_worker_matrix_reports_planned_and_actual_wire_bytes(tmp_path, workers):
    payload = b"GRIB" + (b"m" * 112) + b"7777"
    edges = [round(index * len(payload) / 6) for index in range(7)]
    ranges = [
        ByteRange(edges[index], edges[index + 1] - 1)
        for index in range(6)
    ]
    lock = threading.Lock()

    class Session:
        def get(self, _url, headers, stream, timeout):
            start, end = (
                int(part) for part in
                headers["Range"].removeprefix("bytes=").split("-")
            )
            with lock:
                data = payload[start:end + 1]
            return _Response(
                206,
                data,
                {
                    "Content-Range": f"bytes {start}-{end}/{len(payload)}",
                    "ETag": '"metrics"',
                },
            )

        def close(self):
            pass

    metrics = RangeTransferMetrics()
    output = download_ranges(
        Session(),
        "https://data/file",
        ranges,
        tmp_path / f"workers-{workers}.grib2",
        workers=workers,
        session_factory=Session,
        retry_backoff=0,
        metrics=metrics,
    )

    assert output.read_bytes() == payload
    assert metrics.planned_bytes == len(payload)
    assert metrics.transferred_bytes == len(payload) + (workers > 1)
    assert metrics.reused_bytes == 0
    assert metrics.request_count == len(ranges) + (workers > 1)
    assert metrics.retry_count == 0
    assert metrics.worker_count == workers

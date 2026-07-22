"""Resumable adaptive HTTP byte-range transport for GRIB subsets."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
import json
import logging
import math
import os
from pathlib import Path
import random
import shutil
import tempfile
import threading
import time
from typing import Callable, Iterable


_LOGGER = logging.getLogger(__name__)
_MAX_RANGE_WORKERS = 8
_MIN_PARALLEL_PART_BYTES = 2 * 1024 * 1024


class OptimizedTransportUnavailable(RuntimeError):
    """The optimized path is incompatible; callers should use a fallback."""


class DownloadCancelled(RuntimeError):
    """A cooperative model download cancellation was requested."""


class _ObjectChanged(OptimizedTransportUnavailable):
    """The remote object no longer matches the pinned transfer identity."""


class _TransientRangeFailure(RuntimeError):
    """One range attempt may succeed when retried."""

    def __init__(self, message: str, retry_after: float = 0.0):
        super().__init__(message)
        self.retry_after = max(0.0, float(retry_after or 0.0))


@dataclass(frozen=True, order=True)
class ByteRange:
    """One inclusive HTTP byte range."""

    start: int
    end: int

    @property
    def size(self) -> int:
        return self.end - self.start + 1


@dataclass
class RangeTransferMetrics:
    """Observable byte/request accounting for one range-transfer operation.

    ``planned_bytes`` is the assembled subset size. ``transferred_bytes`` is
    what this process actually consumed from HTTP response bodies, including
    identity probes and retry payloads. ``reused_bytes`` came from resumable
    fragments or an already complete output and therefore used no network.
    """

    planned_bytes: int = 0
    transferred_bytes: int = 0
    reused_bytes: int = 0
    request_count: int = 0
    retry_count: int = 0
    worker_count: int = 1
    fallback_used: bool = False


def _coerce_range(row) -> ByteRange:
    try:
        start, end = row
        if end is None or (isinstance(end, float) and math.isnan(end)):
            raise OptimizedTransportUnavailable(
                "inventory has no ending byte for a selected GRIB message"
            )
        item = ByteRange(int(start), int(end))
    except OptimizedTransportUnavailable:
        raise
    except (TypeError, ValueError, OverflowError) as exc:
        raise OptimizedTransportUnavailable(
            "inventory contains an invalid byte range"
        ) from exc
    if item.start < 0 or item.end < item.start:
        raise OptimizedTransportUnavailable(
            "inventory contains an invalid byte range"
        )
    return item


def _collapse_contiguous(rows: Iterable[tuple[int, int]]) -> list[ByteRange]:
    # Some wgrib2 inventories expose one physical vector message twice (for
    # example UGRD and VGRD) with the same start offset. Herbie calculates the
    # first duplicate's end as ``start - 1`` and the second duplicate carries
    # the real message end. Consolidate equal starts before validating so the
    # shared GRIB message remains downloadable without accepting a genuinely
    # malformed singleton range.
    grouped: dict[int, int] = {}
    for row in rows:
        try:
            start, end = row
            if end is None or (isinstance(end, float) and math.isnan(end)):
                raise OptimizedTransportUnavailable(
                    "inventory has no ending byte for a selected GRIB message"
                )
            start = int(start)
            end = int(end)
        except OptimizedTransportUnavailable:
            raise
        except (TypeError, ValueError, OverflowError) as exc:
            raise OptimizedTransportUnavailable(
                "inventory contains an invalid byte range"
            ) from exc
        if start < 0:
            raise OptimizedTransportUnavailable(
                "inventory contains an invalid byte range"
            )
        grouped[start] = max(grouped.get(start, end), end)

    exact: list[ByteRange] = []
    for item in sorted(_coerce_range(row) for row in grouped.items()):
        if exact and item.start <= exact[-1].end + 1:
            exact[-1] = ByteRange(exact[-1].start, max(exact[-1].end, item.end))
        else:
            exact.append(item)
    if not exact:
        raise OptimizedTransportUnavailable(
            "inventory returned no byte ranges for the selected fields"
        )
    return exact


def plan_ranges(
    rows: Iterable[tuple[int, int]],
    *,
    max_gap: int = 2 * 1024 * 1024,
    max_overhead_ratio: float = 0.25,
) -> list[ByteRange]:
    """Merge nearby message spans without exceeding a global byte budget."""
    exact = _collapse_contiguous(rows)
    exact_bytes = sum(item.size for item in exact)
    overhead_limit = max(0, int(exact_bytes * float(max_overhead_ratio)))
    overhead = 0
    planned: list[ByteRange] = []
    for item in exact:
        if not planned:
            planned.append(item)
            continue
        gap = item.start - planned[-1].end - 1
        if gap <= int(max_gap) and overhead + gap <= overhead_limit:
            overhead += gap
            planned[-1] = ByteRange(planned[-1].start, item.end)
        else:
            planned.append(item)
    return planned


def ranges_from_inventory(
    inventory,
    *,
    max_gap: int = 2 * 1024 * 1024,
    max_overhead_ratio: float = 0.25,
) -> list[ByteRange]:
    """Create an adaptive range plan from a Herbie inventory DataFrame."""
    try:
        rows = zip(inventory["start_byte"], inventory["end_byte"])
    except (KeyError, TypeError) as exc:
        raise OptimizedTransportUnavailable(
            "inventory does not expose HTTP byte ranges"
        ) from exc
    return plan_ranges(
        rows, max_gap=max_gap, max_overhead_ratio=max_overhead_ratio
    )


def parallelize_range_plan(
    ranges: Iterable[ByteRange],
    workers: int,
    *,
    min_part_bytes: int = _MIN_PARALLEL_PART_BYTES,
) -> list[ByteRange]:
    """Split large contiguous spans so bounded workers have useful work.

    Inventory coalescing often turns all sounding fields into one large byte
    span.  Splitting that span does not split or rewrite GRIB messages in the
    final file: the fragments are reassembled in byte order before validation.
    Small spans stay intact so request latency cannot dominate useful data.
    """
    plan = sorted(_coerce_range((item.start, item.end)) for item in ranges)
    if not plan:
        return []
    target = range_worker_count(workers)
    minimum = max(1, int(min_part_bytes))
    while len(plan) < target:
        candidates = [
            (item.size, index)
            for index, item in enumerate(plan)
            if item.size >= minimum * 2
        ]
        if not candidates:
            break
        _size, index = max(candidates)
        item = plan[index]
        left_size = item.size // 2
        split_at = item.start + left_size - 1
        plan[index:index + 1] = [
            ByteRange(item.start, split_at),
            ByteRange(split_at + 1, item.end),
        ]
    return plan


def _valid_grib(path: Path) -> bool:
    try:
        if path.stat().st_size < 8:
            return False
        with path.open("rb") as handle:
            if handle.read(4) != b"GRIB":
                return False
            handle.seek(-4, os.SEEK_END)
            return handle.read(4) == b"7777"
    except OSError:
        return False


def _load_manifest(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _write_manifest(path: Path, payload: dict) -> None:
    fd, temporary = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise


def _parse_content_range_details(value: str) -> tuple[int, int, int] | None:
    try:
        unit, remainder = value.split(" ", 1)
        span, total = remainder.split("/", 1)
        start, end = span.split("-", 1)
        if unit.lower() != "bytes" or total == "*":
            return None
        start = int(start)
        end = int(end)
        total = int(total)
        if start < 0 or end < start or total <= end:
            return None
        return start, end, total
    except (AttributeError, TypeError, ValueError):
        return None


def _parse_content_range(value: str) -> tuple[int, int] | None:
    details = _parse_content_range_details(value)
    return details[:2] if details is not None else None


def range_worker_count(value=None, *, default: int = 1) -> int:
    """Return a validated HTTP-range worker count, bounded independently.

    This setting controls network requests only.  It deliberately has no
    relationship to either the Python or Rust GRIB decoder concurrency.
    """
    if value is None:
        value = os.environ.get("SHARPMOD_RANGE_WORKERS", default)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = int(default)
    return min(_MAX_RANGE_WORKERS, max(1, parsed))


def _header(headers, name: str):
    try:
        value = headers.get(name)
    except AttributeError:
        return None
    if value is not None:
        return value
    lowered = name.lower()
    try:
        return next(
            value for key, value in headers.items()
            if str(key).lower() == lowered
        )
    except (AttributeError, StopIteration):
        return None


def _response_identity(headers, total_size: int) -> dict:
    identity = {"size": int(total_size)}
    etag = _header(headers, "ETag")
    last_modified = _header(headers, "Last-Modified")
    if etag:
        identity["etag"] = str(etag)
    if last_modified:
        identity["last_modified"] = str(last_modified)
    return identity


def _manifest_identity(manifest: dict) -> dict:
    value = manifest.get("identity")
    if isinstance(value, dict):
        result = {}
        try:
            if value.get("size") is not None:
                result["size"] = int(value["size"])
        except (TypeError, ValueError):
            pass
        for key in ("etag", "last_modified"):
            if value.get(key):
                result[key] = str(value[key])
        return result
    # Migrate resumable manifests written by the original sequential transport.
    return {"etag": str(manifest["etag"])} if manifest.get("etag") else {}


def _identity_matches(expected: dict, actual: dict) -> bool:
    if not expected:
        return False
    for key, value in expected.items():
        if key not in actual or actual[key] != value:
            return False
    return True


def _if_range_value(identity: dict) -> str | None:
    etag = str(identity.get("etag") or "")
    # RFC 9110 requires a strong entity tag for If-Range.
    if etag and not etag.startswith("W/"):
        return etag
    modified = identity.get("last_modified")
    return str(modified) if modified else None


def _retry_after_seconds(headers) -> float:
    value = _header(headers, "Retry-After")
    try:
        return min(30.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _check_cancelled(cancelled, stop_event=None) -> None:
    if cancelled is not None and cancelled():
        raise DownloadCancelled("forecast-model download cancelled")
    if stop_event is not None and stop_event.is_set():
        raise DownloadCancelled("forecast-model range workers stopped")


def _retry_wait(
    attempt: int,
    retry_after: float,
    retry_backoff: float,
    cancelled,
    stop_event=None,
) -> None:
    base = max(0.0, float(retry_backoff)) * (2 ** max(0, int(attempt)))
    jittered = base * (0.75 + random.random() * 0.5)
    remaining = min(30.0, max(float(retry_after or 0.0), jittered))
    while remaining > 0.0:
        _check_cancelled(cancelled, stop_event)
        interval = min(0.1, remaining)
        time.sleep(interval)
        remaining -= interval


def _probe_source_identity(
    session,
    url: str,
    *,
    timeout,
    retries: int,
    retry_backoff: float,
    cancelled,
    request_started: Callable[[], None] | None = None,
    bytes_received: Callable[[int], None] | None = None,
    retrying: Callable[[], None] | None = None,
) -> dict:
    """Pin an object identity with a one-byte conditional-range precursor."""
    for attempt in range(max(0, int(retries)) + 1):
        _check_cancelled(cancelled)
        probe_done = threading.Event()
        probe_response = []
        probe_monitor = None
        if cancelled is not None:
            def monitor_probe():
                while not probe_done.wait(0.05):
                    try:
                        requested = bool(cancelled())
                    except Exception:
                        return
                    if not requested:
                        continue
                    for value in list(probe_response):
                        close = getattr(value, "close", None)
                        if callable(close):
                            try:
                                close()
                            except Exception:
                                pass
                    close = getattr(session, "close", None)
                    if callable(close):
                        try:
                            close()
                        except Exception:
                            pass
                    # Keep watching until the attempt exits: ``session.get``
                    # may return a response just after the session was closed.

            probe_monitor = threading.Thread(
                target=monitor_probe,
                name="sharpmod-range-probe-cancel",
                daemon=True,
            )
            probe_monitor.start()
        try:
            if request_started is not None:
                request_started()
            response_context = session.get(
                str(url), headers={"Range": "bytes=0-0"}, stream=True,
                timeout=timeout,
            )
            with response_context as response:
                probe_response.append(response)
                status = int(getattr(response, "status_code", 0))
                if status == 429 or 500 <= status <= 599:
                    raise _TransientRangeFailure(
                        "source identity probe returned HTTP %d" % status,
                        _retry_after_seconds(getattr(response, "headers", {})),
                    )
                if status != 206:
                    raise OptimizedTransportUnavailable(
                        "source did not honor the HTTP identity range request"
                    )
                headers = getattr(response, "headers", {})
                details = _parse_content_range_details(
                    _header(headers, "Content-Range") or ""
                )
                if details is None or details[:2] != (0, 0):
                    raise OptimizedTransportUnavailable(
                        "source returned a mismatched identity range"
                    )
                received = 0
                for chunk in response.iter_content(chunk_size=1):
                    if not chunk:
                        continue
                    received += len(chunk)
                    if bytes_received is not None:
                        bytes_received(len(chunk))
                if received != 1:
                    raise OptimizedTransportUnavailable(
                        "source returned an incomplete identity range"
                    )
                return _response_identity(headers, details[2])
        except (DownloadCancelled, OptimizedTransportUnavailable):
            raise
        except _TransientRangeFailure as exc:
            failure = exc
        except Exception as exc:
            failure = _TransientRangeFailure(str(exc))
        finally:
            probe_done.set()
            if probe_monitor is not None:
                probe_monitor.join(timeout=0.2)
        if attempt >= max(0, int(retries)):
            raise OptimizedTransportUnavailable(
                "optimized HTTP identity probe failed: %s" % failure
            ) from failure
        if retrying is not None:
            retrying()
        _retry_wait(
            attempt, failure.retry_after, retry_backoff, cancelled
        )


def download_ranges(
    session,
    url: str,
    ranges: Iterable[ByteRange],
    output_path,
    *,
    timeout=(10, 90),
    chunk_size: int = 256 * 1024,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
    workers: int = 1,
    session_factory: Callable[[], object] | None = None,
    retries: int = 2,
    retry_backoff: float = 0.25,
    metrics: RangeTransferMetrics | None = None,
) -> Path:
    """Download, resume, validate, and atomically assemble GRIB ranges.

    Network concurrency is bounded by ``workers``.  Parallel mode requires a
    ``session_factory`` so mutable HTTP sessions are never shared by workers;
    callers that provide only one session retain the established sequential
    behavior.
    """
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    plan = list(ranges)
    if not plan:
        raise OptimizedTransportUnavailable("byte-range plan is empty")
    total = sum(item.size for item in plan)
    worker_count = min(range_worker_count(workers), len(plan))
    if worker_count > 1 and session_factory is None:
        worker_count = 1
    metrics_lock = threading.Lock()
    if metrics is not None:
        with metrics_lock:
            metrics.planned_bytes = total
            metrics.transferred_bytes = 0
            metrics.reused_bytes = 0
            metrics.request_count = 0
            metrics.retry_count = 0
            metrics.worker_count = worker_count
            metrics.fallback_used = False

    def add_metric(name, amount=1):
        if metrics is None:
            return
        with metrics_lock:
            setattr(metrics, name, int(getattr(metrics, name)) + int(amount))

    def set_metric(name, value):
        if metrics is None:
            return
        with metrics_lock:
            setattr(metrics, name, value)

    if output.exists() and _valid_grib(output):
        set_metric("worker_count", 0)
        set_metric("reused_bytes", total)
        return output

    fragments = output.parent / f".{output.name}.ranges"
    manifest_path = fragments / "manifest.json"
    fragments.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(manifest_path)
    signature = [[item.start, item.end] for item in plan]
    if manifest and (
        manifest.get("url") != str(url) or manifest.get("ranges") != signature
    ):
        shutil.rmtree(fragments, ignore_errors=True)
        fragments.mkdir(parents=True, exist_ok=True)
        manifest = {}
    manifest.update({"version": 2, "url": str(url), "ranges": signature})

    # Parallel workers and resumptions pin the source before trusting fragments.
    existing_parts = any(fragments.glob("*.part"))
    stored_identity = _manifest_identity(manifest)
    # Old sequential callers may have manually seeded a fragment without a
    # manifest. Preserve that compatibility route. Parallel mode never trusts
    # such an anonymous fragment, and all fragments created by this module now
    # carry a pinned identity before bytes are written.
    if worker_count > 1 or (existing_parts and stored_identity):
        fresh_identity = _probe_source_identity(
            session,
            str(url),
            timeout=timeout,
            retries=retries,
            retry_backoff=retry_backoff,
            cancelled=cancelled,
            request_started=lambda: add_metric("request_count"),
            bytes_received=lambda size: add_metric(
                "transferred_bytes", size
            ),
            retrying=lambda: add_metric("retry_count"),
        )
        if worker_count > 1 and not (
                fresh_identity.get("etag")
                or fresh_identity.get("last_modified")):
            # A byte count alone cannot prevent fragments from two same-sized
            # object versions being mixed. Keep the established sequential
            # route when the server publishes no conditional validator.
            _LOGGER.info(
                "model_transport.parallel_downgrade reason=no-validator"
            )
            worker_count = 1
            set_metric("worker_count", 1)
        if existing_parts and not _identity_matches(
                stored_identity, fresh_identity):
            shutil.rmtree(fragments, ignore_errors=True)
            fragments.mkdir(parents=True, exist_ok=True)
            manifest = {
                "version": 2, "url": str(url), "ranges": signature,
            }
        manifest["identity"] = fresh_identity
        if fresh_identity.get("etag"):
            manifest["etag"] = fresh_identity["etag"]
    _write_manifest(manifest_path, manifest)

    state_lock = threading.RLock()
    stop_event = threading.Event()
    progress_sizes = {}
    for item in plan:
        fragment = fragments / f"{item.start}-{item.end}.part"
        existing = fragment.stat().st_size if fragment.exists() else 0
        if existing > item.size:
            fragment.unlink()
            existing = 0
        progress_sizes[item] = existing

    initial_progress = sum(progress_sizes.values())
    set_metric("reused_bytes", initial_progress)
    if progress is not None and initial_progress:
        progress(initial_progress, total)

    def report_progress(item, size):
        with state_lock:
            progress_sizes[item] = int(size)
            completed = sum(progress_sizes.values())
            if progress is not None:
                # Keep callbacks serialized; GUI signals and tests need not be
                # made thread-safe merely because the transport is parallel.
                progress(completed, total)

    def pin_or_validate_identity(headers, content_total):
        candidate = _response_identity(headers, content_total)
        with state_lock:
            expected = _manifest_identity(manifest)
            if expected and not _identity_matches(expected, candidate):
                raise _ObjectChanged(
                    "source changed during the HTTP range transfer"
                )
            merged = dict(expected)
            merged.update(candidate)
            if merged != expected:
                manifest["identity"] = merged
                if merged.get("etag"):
                    manifest["etag"] = merged["etag"]
                _write_manifest(manifest_path, manifest)
            return merged

    thread_local = threading.local()
    worker_sessions = []
    session_list_lock = threading.Lock()
    active_io_lock = threading.Lock()
    active_responses = {}
    inflight_sessions = {}

    def close_active_io():
        with active_io_lock:
            responses = list(active_responses.values())
            sessions = list(inflight_sessions.values())
        for response, _http_session in responses:
            close = getattr(response, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        unique_sessions = {
            id(http_session): http_session
            for _response, http_session in responses
        }
        unique_sessions.update({
            id(http_session): http_session for http_session in sessions
        })
        for http_session in unique_sessions.values():
            close = getattr(http_session, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

    def request_session():
        if worker_count <= 1 or session_factory is None:
            return session
        value = getattr(thread_local, "session", None)
        if value is None:
            value = session_factory()
            thread_local.session = value
            with session_list_lock:
                worker_sessions.append(value)
        return value

    def download_fragment(item):
        fragment = fragments / f"{item.start}-{item.end}.part"
        max_attempts = max(0, int(retries)) + 1
        for attempt in range(max_attempts):
            _check_cancelled(cancelled, stop_event)
            existing = fragment.stat().st_size if fragment.exists() else 0
            if existing > item.size:
                fragment.unlink(missing_ok=True)
                existing = 0
                report_progress(item, 0)
            if existing == item.size:
                report_progress(item, existing)
                return
            request_start = item.start + existing
            headers = {"Range": f"bytes={request_start}-{item.end}"}
            with state_lock:
                if_range = _if_range_value(_manifest_identity(manifest))
            if if_range:
                headers["If-Range"] = if_range
            try:
                http_session = request_session()
                with active_io_lock:
                    inflight_sessions[id(http_session)] = http_session
                try:
                    add_metric("request_count")
                    response_context = http_session.get(
                        str(url), headers=headers, stream=True, timeout=timeout
                    )
                    with response_context as response:
                        with active_io_lock:
                            active_responses[id(response)] = (
                                response, http_session
                            )
                        try:
                            status = int(getattr(response, "status_code", 0))
                            response_headers = getattr(response, "headers", {})
                            if status == 429 or 500 <= status <= 599:
                                raise _TransientRangeFailure(
                                    "range request returned HTTP %d" % status,
                                    _retry_after_seconds(response_headers),
                                )
                            if status != 206:
                                if headers.get("If-Range") and status == 200:
                                    raise _ObjectChanged(
                                        "source no longer matches the pinned "
                                        "object"
                                    )
                                raise OptimizedTransportUnavailable(
                                    "source did not honor the HTTP range request"
                                )
                            details = _parse_content_range_details(
                                _header(
                                    response_headers, "Content-Range"
                                ) or ""
                            )
                            if details is None or details[:2] != (
                                    request_start, item.end):
                                raise OptimizedTransportUnavailable(
                                    "source returned a mismatched HTTP range"
                                )
                            pin_or_validate_identity(
                                response_headers, details[2]
                            )
                            mode = "ab" if existing else "wb"
                            with fragment.open(mode) as handle:
                                for chunk in response.iter_content(
                                        chunk_size=chunk_size):
                                    if not chunk:
                                        continue
                                    handle.write(chunk)
                                    existing += len(chunk)
                                    add_metric(
                                        "transferred_bytes", len(chunk)
                                    )
                                    if existing > item.size:
                                        fragment.unlink(missing_ok=True)
                                        report_progress(item, 0)
                                        raise OptimizedTransportUnavailable(
                                            "source returned more bytes than "
                                            "requested"
                                        )
                                    report_progress(item, existing)
                                    _check_cancelled(cancelled, stop_event)
                        finally:
                            with active_io_lock:
                                active_responses.pop(id(response), None)
                finally:
                    with active_io_lock:
                        inflight_sessions.pop(id(http_session), None)
                if existing == item.size:
                    return
                raise _TransientRangeFailure(
                    "source returned an incomplete HTTP range"
                )
            except (DownloadCancelled, OptimizedTransportUnavailable):
                raise
            except _TransientRangeFailure as exc:
                failure = exc
            except Exception as exc:
                failure = _TransientRangeFailure(str(exc))
            if attempt >= max_attempts - 1:
                raise OptimizedTransportUnavailable(
                    "optimized HTTP range transfer failed: %s" % failure
                ) from failure
            add_metric("retry_count")
            _retry_wait(
                attempt,
                failure.retry_after,
                retry_backoff,
                cancelled,
                stop_event,
            )

    incomplete = [
        item for item in plan if progress_sizes.get(item, 0) != item.size
    ]
    monitor_done = threading.Event()
    monitor_thread = None
    if cancelled is not None and incomplete:
        def monitor_cancellation():
            # Fast responses retain the cheap in-loop checks. This monitor is
            # specifically for a response stalled inside ``iter_content``.
            while not monitor_done.wait(0.05):
                try:
                    requested = bool(cancelled())
                except Exception:
                    return
                if requested:
                    stop_event.set()
                    close_active_io()
                    return

        monitor_thread = threading.Thread(
            target=monitor_cancellation,
            name="sharpmod-range-cancel",
            daemon=True,
        )
        monitor_thread.start()
    try:
        if worker_count <= 1:
            for item in incomplete:
                download_fragment(item)
        else:
            executor = ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix="sharpmod-range",
            )
            pending = set()
            iterator = iter(incomplete)
            try:
                for _ in range(worker_count):
                    item = next(iterator, None)
                    if item is None:
                        break
                    pending.add(executor.submit(download_fragment, item))
                while pending:
                    _check_cancelled(cancelled)
                    done, pending = wait(
                        pending, timeout=0.1,
                        return_when=FIRST_COMPLETED,
                    )
                    for future in done:
                        future.result()
                    # Only replenish the bounded queue after every completion
                    # in this batch has validated successfully.
                    for _future in done:
                        item = next(iterator, None)
                        if item is not None:
                            pending.add(
                                executor.submit(download_fragment, item)
                            )
            except BaseException:
                stop_event.set()
                for future in pending:
                    future.cancel()
                raise
            finally:
                executor.shutdown(wait=True, cancel_futures=True)
    except _ObjectChanged:
        # No fragment may survive an object-identity change.  A later
        # sequential or Herbie fallback must begin against the new object.
        shutil.rmtree(fragments, ignore_errors=True)
        raise
    finally:
        monitor_done.set()
        if monitor_thread is not None:
            monitor_thread.join(timeout=0.2)
        for worker_session in worker_sessions:
            close = getattr(worker_session, "close", None)
            if callable(close):
                close()

    _check_cancelled(cancelled)
    for item in plan:
        fragment = fragments / f"{item.start}-{item.end}.part"
        if not fragment.exists() or fragment.stat().st_size != item.size:
            raise OptimizedTransportUnavailable(
                "source returned an incomplete HTTP range"
            )

    fd, temporary = tempfile.mkstemp(
        prefix=output.name + ".", suffix=".tmp", dir=output.parent
    )
    try:
        with os.fdopen(fd, "wb") as destination:
            for item in plan:
                fragment = fragments / f"{item.start}-{item.end}.part"
                with fragment.open("rb") as source:
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
        temporary_path = Path(temporary)
        if not _valid_grib(temporary_path):
            raise OptimizedTransportUnavailable(
                "assembled byte ranges are not a valid GRIB stream"
            )
        os.replace(temporary_path, output)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise
    shutil.rmtree(fragments, ignore_errors=True)
    return output


def download_herbie_subset(
    herbie,
    search: str,
    *,
    inventory=None,
    save_dir=None,
    session=None,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
    max_gap: int = 2 * 1024 * 1024,
    max_overhead_ratio: float = 0.25,
    workers: int | None = None,
    retries: int = 2,
    metrics: RangeTransferMetrics | None = None,
) -> tuple[Path, int]:
    """Download a Herbie inventory subset through the optimized transport."""
    source = str(getattr(herbie, "grib", "") or "")
    if not source.startswith(("http://", "https://")):
        raise OptimizedTransportUnavailable(
            "Herbie source is not an HTTP byte-range endpoint"
        )
    if save_dir is not None:
        herbie.save_dir = Path(save_dir).expanduser()
    try:
        if inventory is None:
            inventory = herbie.inventory(search).copy()
        else:
            inventory = inventory.copy()
        output = Path(herbie.get_localFilePath(search))
    except Exception as exc:
        raise OptimizedTransportUnavailable(
            "Herbie could not provide a subset inventory: %s" % exc
        ) from exc
    ranges = ranges_from_inventory(
        inventory,
        max_gap=max_gap,
        max_overhead_ratio=max_overhead_ratio,
    )
    total = sum(item.size for item in ranges)
    worker_count = range_worker_count(workers)
    owned_session = session is None
    session_factory = None
    if owned_session:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - required dependency
            raise OptimizedTransportUnavailable(
                "requests is unavailable for optimized model downloads"
            ) from exc
        session = requests.Session()
        if worker_count > 1:
            session_factory = requests.Session
            ranges = parallelize_range_plan(ranges, worker_count)
    attempt_metrics = []
    used_fallback = False
    try:
        try:
            parallel_metrics = RangeTransferMetrics()
            attempt_metrics.append(parallel_metrics)
            result = download_ranges(
                session,
                source,
                ranges,
                output,
                cancelled=cancelled,
                progress=progress,
                workers=worker_count,
                session_factory=session_factory,
                retries=retries,
                metrics=parallel_metrics,
            )
        except OptimizedTransportUnavailable as parallel_error:
            if worker_count <= 1:
                raise
            _LOGGER.info(
                "model_transport.parallel_fallback workers=%d reason=%s",
                worker_count,
                parallel_error,
            )
            used_fallback = True
            # Preserve validated fragments and retry through the established
            # single-session path before asking callers to use a full Herbie
            # fallback.  DownloadCancelled intentionally bypasses this branch.
            sequential_metrics = RangeTransferMetrics()
            attempt_metrics.append(sequential_metrics)
            result = download_ranges(
                session,
                source,
                ranges,
                output,
                cancelled=cancelled,
                progress=progress,
                workers=1,
                retries=retries,
                metrics=sequential_metrics,
            )
        return result, total
    finally:
        if metrics is not None and attempt_metrics:
            metrics.planned_bytes = total
            metrics.transferred_bytes = sum(
                item.transferred_bytes for item in attempt_metrics
            )
            metrics.reused_bytes = max(
                item.reused_bytes for item in attempt_metrics
            )
            metrics.request_count = sum(
                item.request_count for item in attempt_metrics
            )
            metrics.retry_count = sum(
                item.retry_count for item in attempt_metrics
            )
            metrics.worker_count = max(
                item.worker_count for item in attempt_metrics
            )
            metrics.fallback_used = used_fallback
        if owned_session:
            close = getattr(session, "close", None)
            if callable(close):
                close()

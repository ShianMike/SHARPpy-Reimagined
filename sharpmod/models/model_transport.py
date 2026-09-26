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


def inclusive_end_bytes(herbie, inventory):
    """Normalize eccodes-style end offsets to inclusive HTTP range bounds.

    Herbie's wgrib2 inventories set ``end_byte`` to the last byte of a message
    (the next message's start minus one), which is what an inclusive HTTP
    ``Range`` header needs. Its eccodes inventories (ECMWF open data) instead
    set ``end_byte`` to ``_offset + _length``, the *first byte of the next
    message*, so requesting that range over-reads by one byte and the
    assembled stream no longer ends at the GRIB ``7777`` trailer. That only
    stayed invisible while a selection happened to include the file's final
    message, where the server clamps the range at EOF.
    """
    if str(getattr(herbie, "IDX_STYLE", "wgrib2")).lower() != "eccodes":
        return inventory
    columns = getattr(inventory, "columns", ())
    if "end_byte" not in tuple(columns):
        return inventory
    adjusted = inventory.copy()
    adjusted["end_byte"] = adjusted["end_byte"] - 1
    return adjusted


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


from sharpmod.models.model_transfer import (  # noqa: E402
    download_ranges,
    download_herbie_subset,
    download_herbie_subset_fallback
)

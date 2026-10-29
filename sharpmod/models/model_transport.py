"""Resumable adaptive HTTP byte-range transport for GRIB subsets."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import math
import os
from pathlib import Path
from typing import Iterable


_LOGGER = logging.getLogger(__name__)
_MAX_RANGE_WORKERS = 8
_MIN_PARALLEL_PART_BYTES = 2 * 1024 * 1024


class OptimizedTransportUnavailable(RuntimeError):
    """The optimized path is incompatible; callers should use a fallback."""


class DownloadCancelled(RuntimeError):
    """A cooperative model download cancellation was requested."""


class _ObjectChanged(OptimizedTransportUnavailable):
    """The remote object no longer matches the pinned transfer identity."""


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


def _check_cancelled(cancelled, stop_event=None) -> None:
    if cancelled is not None and cancelled():
        raise DownloadCancelled("forecast-model download cancelled")
    if stop_event is not None and stop_event.is_set():
        raise DownloadCancelled("forecast-model range workers stopped")


from sharpmod.models.model_transfer import (  # noqa: E402
    download_ranges,
    download_herbie_subset,
    download_herbie_subset_fallback
)

"""Resumable adaptive HTTP byte-range transport for GRIB subsets."""

from __future__ import annotations

from dataclasses import dataclass
import logging
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


def plan_ranges(
    rows: Iterable[tuple[int, int]],
    *,
    max_gap: int = 2 * 1024 * 1024,
    max_overhead_ratio: float = 0.25,
) -> list[ByteRange]:
    """Merge nearby message spans without exceeding a global byte budget."""
    import sharpmod_rs

    return [
        ByteRange(start, end)
        for start, end in sharpmod_rs.plan_model_ranges(
            rows, max_gap=int(max_gap), max_overhead_ratio=float(max_overhead_ratio)
        )
    ]


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
    import sharpmod_rs

    target = range_worker_count(workers)
    minimum = max(1, int(min_part_bytes))
    return [
        ByteRange(start, end)
        for start, end in sharpmod_rs.parallelize_model_range_plan(
            ranges, workers=target, min_part_bytes=minimum
        )
    ]


def _valid_grib(path: Path) -> bool:
    import sharpmod_rs

    return sharpmod_rs.valid_grib_file(path)


def _parse_content_range_details(value: str) -> tuple[int, int, int] | None:
    import sharpmod_rs

    return sharpmod_rs.parse_content_range_details(value)


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

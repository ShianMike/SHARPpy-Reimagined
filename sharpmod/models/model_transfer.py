"""HTTP range and Herbie subset download implementations for forecast-model data.

The ``model_transport`` facade selects optimized transport, cancellation, and fallback
behavior so callers share one transport contract."""

from __future__ import annotations

from pathlib import Path
from typing import Callable
from typing import Iterable
import os
import shutil
import tempfile
import threading
from sharpmod.models import model_transport as _api


def download_ranges(
    session,
    url: str,
    ranges: Iterable[_api.ByteRange],
    output_path,
    *,
    timeout=(10, 90),
    chunk_size: int = 256 * 1024,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
    on_bytes: Callable[[int], None] | None = None,
    workers: int = 1,
    session_factory: Callable[[], object] | None = None,
    retries: int = 2,
    retry_backoff: float = 0.25,
    metrics: _api.RangeTransferMetrics | None = None,
    decode_point=None,
) -> Path:
    """Download, resume, validate, and atomically assemble GRIB ranges in Rust."""
    from sharpmod_rs import download_model_ranges

    return Path(download_model_ranges(
        session, str(url), [(item.start, item.end) for item in ranges],
        os.fspath(output_path), timeout, chunk_size, cancelled, progress,
        on_bytes, _api.range_worker_count(workers), session_factory,
        retries, retry_backoff, metrics,
        **({"decode_point": decode_point} if decode_point is not None else {}),
    ))


def download_herbie_subset(
    herbie,
    search: str,
    *,
    inventory=None,
    save_dir=None,
    session=None,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
    on_bytes: Callable[[int], None] | None = None,
    max_gap: int = 2 * 1024 * 1024,
    max_overhead_ratio: float = 0.25,
    workers: int | None = None,
    retries: int = 2,
    metrics: _api.RangeTransferMetrics | None = None,
    decode_point=None,
) -> tuple[Path, int]:
    """Download a Herbie inventory subset through the optimized transport."""
    source = str(getattr(herbie, "grib", "") or "")
    if not source.startswith(("http://", "https://")):
        raise _api.OptimizedTransportUnavailable(
            "Herbie source is not an HTTP byte-range endpoint"
        )
    if save_dir is not None:
        herbie.save_dir = Path(save_dir).expanduser()
    try:
        if inventory is None:
            inventory = herbie.inventory(search).copy()
        else:
            inventory = inventory.copy()
        inventory = _api.inclusive_end_bytes(herbie, inventory)
        output = Path(herbie.get_localFilePath(search))
    except Exception as exc:
        raise _api.OptimizedTransportUnavailable(
            "Herbie could not provide a subset inventory: %s" % exc
        ) from exc
    from sharpmod_rs import adaptive_model_range_gap

    ranges = _api.ranges_from_inventory(
        inventory,
        max_gap=adaptive_model_range_gap(source, max_gap),
        max_overhead_ratio=max_overhead_ratio,
    )
    total = sum(item.size for item in ranges)
    worker_count = _api.range_worker_count(workers)
    session_factory = None
    owned_session = session is None
    if owned_session:
        from sharpmod_rs import model_range_session

        session = model_range_session((10, 90))
        if worker_count > 1:
            ranges = _api.parallelize_range_plan(ranges, worker_count)
    attempt_metrics = []
    used_fallback = False
    try:
        try:
            parallel_metrics = _api.RangeTransferMetrics()
            attempt_metrics.append(parallel_metrics)
            result = _api.download_ranges(
                session,
                source,
                ranges,
                output,
                cancelled=cancelled,
                progress=progress,
                on_bytes=on_bytes,
                workers=worker_count,
                session_factory=session_factory,
                retries=retries,
                metrics=parallel_metrics,
                decode_point=decode_point,
            )
        except _api.OptimizedTransportUnavailable as parallel_error:
            if worker_count <= 1:
                raise
            _api._LOGGER.info(
                "model_transport.parallel_fallback workers=%d reason=%s",
                worker_count,
                parallel_error,
            )
            used_fallback = True
            # Preserve validated fragments and retry through the established
            # single-session path before asking callers to use a full Herbie
            # fallback.  DownloadCancelled intentionally bypasses this branch.
            sequential_metrics = _api.RangeTransferMetrics()
            attempt_metrics.append(sequential_metrics)
            result = _api.download_ranges(
                session,
                source,
                ranges,
                output,
                cancelled=cancelled,
                progress=progress,
                on_bytes=on_bytes,
                workers=1,
                retries=retries,
                metrics=sequential_metrics,
                decode_point=decode_point,
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
            session.close()


def download_herbie_subset_fallback(
    herbie,
    search: str,
    *,
    inventory=None,
    save_dir=None,
    session=None,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
    on_bytes: Callable[[int], None] | None = None,
    timeout=(10, 30),
    chunk_size: int = 256 * 1024,
) -> tuple[Path, int]:
    from sharpmod_rs import download_herbie_compat

    return download_herbie_compat(herbie, search, locals())

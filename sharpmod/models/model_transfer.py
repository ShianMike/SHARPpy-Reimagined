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
) -> Path:
    """Download, resume, validate, and atomically assemble GRIB ranges in Rust."""
    from sharpmod_rs import download_model_ranges

    return Path(download_model_ranges(
        session, str(url), [(item.start, item.end) for item in ranges],
        os.fspath(output_path), timeout, chunk_size, cancelled, progress,
        on_bytes, _api.range_worker_count(workers), session_factory,
        retries, retry_backoff, metrics,
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
    ranges = _api.ranges_from_inventory(
        inventory,
        max_gap=max_gap,
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
    """Compatibility subset transfer with bounded, cancellable I/O.

    This is the final fallback for servers whose range metadata is too weak
    for the resumable transport.  It deliberately mirrors Herbie's permissive
    sequential behavior, including accepting a full HTTP 200 payload when a
    server ignores ``Range``, while retaining SHARPmod's cancellation,
    timeout, atomic-write, and GRIB-integrity guarantees.
    """

    source_value = getattr(herbie, "grib", None)
    source = os.fspath(source_value) if source_value is not None else ""
    if not source:
        raise _api.OptimizedTransportUnavailable(
            "Herbie compatibility fallback has no source payload"
        )
    if save_dir is not None:
        herbie.save_dir = Path(save_dir).expanduser()
    try:
        selected = (
            herbie.inventory(search).copy()
            if inventory is None
            else inventory.copy()
        )
        selected = _api.inclusive_end_bytes(herbie, selected)
        ranges = _api.ranges_from_inventory(
            selected, max_gap=0, max_overhead_ratio=0.0
        )
        output = Path(herbie.get_localFilePath(search))
    except Exception as exc:
        raise _api.OptimizedTransportUnavailable(
            "Herbie compatibility fallback could not plan the subset: %s"
            % exc
        ) from exc

    _api._check_cancelled(cancelled)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and _api._valid_grib(output):
        size = output.stat().st_size
        if progress is not None:
            progress(size, size)
        return output.resolve(), size

    planned_total = sum(item.size for item in ranges)
    owned_session = session is None
    if owned_session and source.startswith(("http://", "https://")):
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - required dependency
            raise _api.OptimizedTransportUnavailable(
                "requests is unavailable for model download fallback"
            ) from exc
        session = requests.Session()

    fd, temporary = tempfile.mkstemp(
        prefix=output.name + ".", suffix=".part", dir=output.parent
    )
    temporary_path = Path(temporary)
    monitor_done = threading.Event()
    active_lock = threading.Lock()
    active_responses = []

    def close_active_io():
        with active_lock:
            responses = list(active_responses)
        for response in responses:
            close = getattr(response, "close", None)
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

    monitor_thread = None
    if cancelled is not None:
        def monitor_cancellation():
            while not monitor_done.wait(0.05):
                try:
                    requested = bool(cancelled())
                except Exception:
                    return
                if requested:
                    close_active_io()
                    return

        monitor_thread = threading.Thread(
            target=monitor_cancellation,
            name="sharpmod-herbie-fallback-cancel",
            daemon=True,
        )
        monitor_thread.start()

    completed = 0
    try:
        with os.fdopen(fd, "wb") as destination:
            fd = -1
            if source.startswith(("http://", "https://")):
                for item in ranges:
                    _api._check_cancelled(cancelled)
                    response_context = session.get(
                        source,
                        headers={"Range": f"bytes={item.start}-{item.end}"},
                        stream=True,
                        timeout=timeout,
                    )
                    with response_context as response:
                        with active_lock:
                            active_responses.append(response)
                        try:
                            status = int(getattr(response, "status_code", 0))
                            if status not in {200, 206}:
                                raise _api.OptimizedTransportUnavailable(
                                    "Herbie compatibility fallback returned "
                                    f"HTTP {status}"
                                )
                            if status == 200 and completed:
                                destination.seek(0)
                                destination.truncate()
                                completed = 0
                            received = 0
                            for chunk in response.iter_content(
                                chunk_size=max(1, int(chunk_size))
                            ):
                                if not chunk:
                                    continue
                                destination.write(chunk)
                                received += len(chunk)
                                completed += len(chunk)
                                if on_bytes is not None:
                                    on_bytes(len(chunk))
                                if progress is not None:
                                    progress(completed, planned_total)
                                _api._check_cancelled(cancelled)
                            if status == 206 and received != item.size:
                                raise _api.OptimizedTransportUnavailable(
                                    "Herbie compatibility fallback returned "
                                    "an incomplete byte range"
                                )
                            if status == 200:
                                # The server ignored Range and returned the
                                # complete source; it already contains every
                                # selected message, so no later group is needed.
                                break
                        finally:
                            with active_lock:
                                try:
                                    active_responses.remove(response)
                                except ValueError:
                                    pass
            else:
                source_path = Path(source).expanduser().resolve(strict=True)
                with source_path.open("rb") as source_handle:
                    for item in ranges:
                        _api._check_cancelled(cancelled)
                        source_handle.seek(item.start)
                        remaining = item.size
                        while remaining:
                            chunk = source_handle.read(
                                min(max(1, int(chunk_size)), remaining)
                            )
                            if not chunk:
                                raise _api.OptimizedTransportUnavailable(
                                    "local compatibility source ended early"
                                )
                            destination.write(chunk)
                            completed += len(chunk)
                            remaining -= len(chunk)
                            if progress is not None:
                                progress(completed, planned_total)
                            _api._check_cancelled(cancelled)
        _api._check_cancelled(cancelled)
        if not _api._valid_grib(temporary_path):
            raise _api.OptimizedTransportUnavailable(
                "Herbie compatibility fallback did not produce a valid GRIB"
            )
        os.replace(temporary_path, output)
        return output.resolve(), completed
    except _api.DownloadCancelled:
        raise
    except _api.OptimizedTransportUnavailable:
        _api._check_cancelled(cancelled)
        raise
    except Exception as exc:
        _api._check_cancelled(cancelled)
        raise _api.OptimizedTransportUnavailable(
            "Herbie compatibility fallback failed: %s" % exc
        ) from exc
    finally:
        monitor_done.set()
        if monitor_thread is not None:
            monitor_thread.join(timeout=0.2)
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        if owned_session and session is not None:
            close = getattr(session, "close", None)
            if callable(close):
                close()

"""HTTP range and Herbie subset download implementations for forecast-model data.

The ``model_transport`` facade selects optimized transport, cancellation, and fallback
behavior so callers share one transport contract."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait
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
    workers: int = 1,
    session_factory: Callable[[], object] | None = None,
    retries: int = 2,
    retry_backoff: float = 0.25,
    metrics: _api.RangeTransferMetrics | None = None,
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
        raise _api.OptimizedTransportUnavailable("byte-range plan is empty")
    total = sum(item.size for item in plan)
    worker_count = min(_api.range_worker_count(workers), len(plan))
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

    if output.exists() and _api._valid_grib(output):
        set_metric("worker_count", 0)
        set_metric("reused_bytes", total)
        return output

    fragments = output.parent / f".{output.name}.ranges"
    manifest_path = fragments / "manifest.json"
    fragments.mkdir(parents=True, exist_ok=True)
    manifest = _api._load_manifest(manifest_path)
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
    stored_identity = _api._manifest_identity(manifest)
    # Old sequential callers may have manually seeded a fragment without a
    # manifest. Preserve that compatibility route. Parallel mode never trusts
    # such an anonymous fragment, and all fragments created by this module now
    # carry a pinned identity before bytes are written.
    if worker_count > 1 or (existing_parts and stored_identity):
        fresh_identity = _api._probe_source_identity(
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
            _api._LOGGER.info(
                "model_transport.parallel_downgrade reason=no-validator"
            )
            worker_count = 1
            set_metric("worker_count", 1)
        if existing_parts and not _api._identity_matches(
                stored_identity, fresh_identity):
            shutil.rmtree(fragments, ignore_errors=True)
            fragments.mkdir(parents=True, exist_ok=True)
            manifest = {
                "version": 2, "url": str(url), "ranges": signature,
            }
        manifest["identity"] = fresh_identity
        if fresh_identity.get("etag"):
            manifest["etag"] = fresh_identity["etag"]
    _api._write_manifest(manifest_path, manifest)

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
        candidate = _api._response_identity(headers, content_total)
        with state_lock:
            expected = _api._manifest_identity(manifest)
            if expected and not _api._identity_matches(expected, candidate):
                raise _api._ObjectChanged(
                    "source changed during the HTTP range transfer"
                )
            merged = dict(expected)
            merged.update(candidate)
            if merged != expected:
                manifest["identity"] = merged
                if merged.get("etag"):
                    manifest["etag"] = merged["etag"]
                _api._write_manifest(manifest_path, manifest)
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
            _api._check_cancelled(cancelled, stop_event)
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
                if_range = _api._if_range_value(_api._manifest_identity(manifest))
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
                                raise _api._TransientRangeFailure(
                                    "range request returned HTTP %d" % status,
                                    _api._retry_after_seconds(response_headers),
                                )
                            if status != 206:
                                if headers.get("If-Range") and status == 200:
                                    raise _api._ObjectChanged(
                                        "source no longer matches the pinned "
                                        "object"
                                    )
                                raise _api.OptimizedTransportUnavailable(
                                    "source did not honor the HTTP range request"
                                )
                            details = _api._parse_content_range_details(
                                _api._header(
                                    response_headers, "Content-Range"
                                ) or ""
                            )
                            if details is None or details[:2] != (
                                    request_start, item.end):
                                raise _api.OptimizedTransportUnavailable(
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
                                        raise _api.OptimizedTransportUnavailable(
                                            "source returned more bytes than "
                                            "requested"
                                        )
                                    report_progress(item, existing)
                                    _api._check_cancelled(cancelled, stop_event)
                        finally:
                            with active_io_lock:
                                active_responses.pop(id(response), None)
                finally:
                    with active_io_lock:
                        inflight_sessions.pop(id(http_session), None)
                if existing == item.size:
                    return
                raise _api._TransientRangeFailure(
                    "source returned an incomplete HTTP range"
                )
            except (_api.DownloadCancelled, _api.OptimizedTransportUnavailable):
                raise
            except _api._TransientRangeFailure as exc:
                failure = exc
            except Exception as exc:
                failure = _api._TransientRangeFailure(str(exc))
            if attempt >= max_attempts - 1:
                raise _api.OptimizedTransportUnavailable(
                    "optimized HTTP range transfer failed: %s" % failure
                ) from failure
            add_metric("retry_count")
            _api._retry_wait(
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
                    _api._check_cancelled(cancelled)
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
    except _api._ObjectChanged:
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

    _api._check_cancelled(cancelled)
    for item in plan:
        fragment = fragments / f"{item.start}-{item.end}.part"
        if not fragment.exists() or fragment.stat().st_size != item.size:
            raise _api.OptimizedTransportUnavailable(
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
        if not _api._valid_grib(temporary_path):
            raise _api.OptimizedTransportUnavailable(
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
    owned_session = session is None
    session_factory = None
    if owned_session:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - required dependency
            raise _api.OptimizedTransportUnavailable(
                "requests is unavailable for optimized model downloads"
            ) from exc
        session = requests.Session()
        if worker_count > 1:
            session_factory = requests.Session
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


def download_herbie_subset_fallback(
    herbie,
    search: str,
    *,
    inventory=None,
    save_dir=None,
    session=None,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
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

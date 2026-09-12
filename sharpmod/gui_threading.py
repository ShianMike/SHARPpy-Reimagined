"""Safe lifetime management for Qt workers that outlive their owner.

Qt's interruption API is cooperative.  A worker inside a socket read or native
decoder may therefore still be running when a window's bounded shutdown grace
period expires.  Destroying that worker's ``QThread`` object aborts the process,
while ``QThread.terminate()`` can leave Python and native libraries in an
undefined state.

This module provides the third option: detach the worker from the closing
window and retain it until it finishes naturally.  Normal window closes remain
bounded; final application shutdown waits for the small retained set so Python
never tears down a live Qt thread.
"""

from __future__ import annotations

import logging
import shutil
import time

from qtpy.QtCore import QCoreApplication, QObject, Slot


_LOGGER = logging.getLogger(__name__)


class _WorkerRetainer(QObject):
    """Own detached workers until their cooperative operation returns."""

    def __init__(self) -> None:
        super().__init__()
        self._workers: dict[int, object] = {}
        self._hooked_app = None

    def retain(self, worker) -> bool:
        """Detach and retain a running worker, returning whether it was kept."""
        if worker is None:
            return False
        try:
            if not worker.isRunning():
                return False
        except (AttributeError, RuntimeError):
            return False

        identity = id(worker)
        if identity in self._workers:
            return True
        try:
            # QThread objects live in the GUI thread even though ``run`` does
            # not, so changing their QObject parent here is safe.
            worker.setParent(None)
            worker.finished.connect(self._retire_sender)
        except (AttributeError, RuntimeError) as exc:
            _LOGGER.error(
                "application.worker_retain_failed worker=%s error=%s",
                type(worker).__name__,
                exc,
            )
            return False

        self._workers[identity] = worker
        self._hook_application_shutdown()
        _LOGGER.warning(
            "application.worker_retained_until_finished worker=%s",
            type(worker).__name__,
        )
        return True

    def _hook_application_shutdown(self) -> None:
        app = QCoreApplication.instance()
        if app is None or app is self._hooked_app:
            return
        app.aboutToQuit.connect(self.wait_for_all)
        self._hooked_app = app

    @Slot()
    def _retire_sender(self) -> None:
        worker = self.sender()
        if worker is None:
            return
        retained = self._workers.pop(id(worker), None)
        if retained is not None:
            try:
                retained.deleteLater()
            except RuntimeError:
                pass

    @Slot()
    def wait_for_all(self) -> None:
        """Finish retained work before Qt and Python tear down their state."""
        workers = tuple(self._workers.values())
        for worker in workers:
            try:
                if worker.isRunning():
                    worker.requestInterruption()
            except (AttributeError, RuntimeError):
                continue
        for worker in workers:
            try:
                if worker.isRunning():
                    _LOGGER.info(
                        "application.worker_waiting_at_exit worker=%s",
                        type(worker).__name__,
                    )
                    # Final process shutdown is deliberately not forceful. All
                    # network workers have transport timeouts, and native work
                    # must finish before its libraries can be unloaded safely.
                    worker.wait()
            except (AttributeError, RuntimeError):
                continue
        self._workers.clear()


_RETAINER: _WorkerRetainer | None = None


def _retainer() -> _WorkerRetainer:
    global _RETAINER
    if _RETAINER is None:
        _RETAINER = _WorkerRetainer()
    return _RETAINER


def retain_worker_until_finished(worker) -> bool:
    """Keep a running Qt worker alive after its owning window is gone."""
    return _retainer().retain(worker)


def shutdown_picker_workers(owner, *, retain=None, logger=None) -> None:
    """Cooperatively stop and release every worker owned by a picker window."""
    if getattr(owner, "_shutdown_started", False):
        return
    owner._shutdown_started = True
    retain = retain or retain_worker_until_finished
    logger = logger or _LOGGER

    for name in (
        "_avail_timer",
        "_catalog_timer",
        "_utc_timer",
        "_model_availability_timer",
        "_model_progress_timer",
    ):
        timer = getattr(owner, name, None)
        if timer is not None:
            timer.stop()
    owner._avail_request = None
    owner._catalog_request = None
    owner._model_availability_request = None
    owner._model_availability_waiting_for_worker = False
    owner._avail_token += 1
    owner._catalog_token += 1
    owner._model_availability_token += 1

    advisory_workers = [
        getattr(owner, "_catalog_worker", None),
        *list(getattr(owner, "_avail_workers", ())),
        *list(getattr(owner, "_model_availability_workers", ())),
    ]
    active_workers = [
        getattr(owner, "_worker", None),
        getattr(owner, "_model_worker", None),
        getattr(owner, "_model_timeline_worker", None),
        getattr(owner, "_model_compare_worker", None),
        getattr(owner, "_box_extract_worker", None),
        getattr(owner, "_box_analysis_worker", None),
        getattr(owner, "_box_mean_worker", None),
        getattr(owner, "_model_prefetch_worker", None),
        getattr(owner, "_era5_worker", None),
        getattr(owner, "_wrf_inspect_worker", None),
        getattr(owner, "_wrf_extract_worker", None),
        getattr(owner, "_model_cache_prune_worker", None),
    ]
    workers = []
    advisory_ids = {id(worker) for worker in advisory_workers if worker is not None}
    seen = set()
    for worker in (*advisory_workers, *active_workers):
        if worker is None or id(worker) in seen:
            continue
        seen.add(id(worker))
        workers.append(worker)

    # Signal all workers before waiting on any one of them, allowing
    # cooperative downloads and extractors to wind down concurrently.
    for worker in workers:
        try:
            worker.requestInterruption()
        except (AttributeError, RuntimeError):
            continue

    started = time.monotonic()
    advisory_deadline = started + 1.0
    model_availability_deadline = started + 5.0
    active_deadline = started + 5.0
    model_availability_ids = {
        id(worker)
        for worker in getattr(owner, "_model_availability_workers", ())
        if worker is not None
    }
    for worker in workers:
        try:
            if not worker.isRunning():
                continue
            if id(worker) in model_availability_ids:
                deadline = model_availability_deadline
            elif id(worker) in advisory_ids:
                deadline = advisory_deadline
            else:
                deadline = active_deadline
            remaining = max(0, int((deadline - time.monotonic()) * 1000))
            if remaining > 0 and worker.wait(remaining):
                continue
            # A native or network library may be below Python and unable to
            # observe interruption immediately. Detach and retain it rather
            # than corrupting its state with QThread.terminate().
            if not retain(worker):
                logger.error(
                    "application.worker_retain_failed worker=%s",
                    type(worker).__name__,
                )
        except RuntimeError:
            continue

    owner._avail_workers.clear()
    owner._model_availability_workers.clear()
    for name in (
        "_catalog_worker",
        "_worker",
        "_model_worker",
        "_model_timeline_worker",
        "_model_compare_worker",
        "_box_extract_worker",
        "_box_analysis_worker",
        "_box_mean_worker",
        "_model_prefetch_worker",
        "_era5_worker",
        "_wrf_inspect_worker",
        "_wrf_extract_worker",
        "_model_cache_prune_worker",
    ):
        setattr(owner, name, None)

    # Box soundings live in a temporary directory only for the window's life.
    box_output_dir = getattr(owner, "_box_output_dir", None)
    if box_output_dir:
        owner._box_output_dir = None
        shutil.rmtree(box_output_dir, ignore_errors=True)

    hour_cache = getattr(owner, "_model_hour_cache", None)
    if hour_cache is not None:
        hour_cache.clear()


__all__ = ["retain_worker_until_finished", "shutdown_picker_workers"]

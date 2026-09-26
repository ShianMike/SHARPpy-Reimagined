"""_ModelAvailabilityWorker, _ModelFetchWorker, _ModelPrefetchWorker, _StationListWorker, _SpcOutlookWorker, _StormReportsWorker, _RadarMosaicWorker, _HrrrFieldWorker, _RadarSiteWorker worker implementations."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from qtpy.QtCore import QThread
from qtpy.QtCore import Signal
from sharpmod.ui.features.gui_common import _LOGGER
import os
import time
from sharpmod.ui.features.gui_workers import (
    AVAIL_AVAILABLE,
    AVAIL_FALLBACK,
    AVAIL_UNKNOWN,
    _cleanup_model_data,
    _model_probe_candidates,
    _portable_pair_valid,
    _update_sounding_sidecar
)


class _ModelAvailabilityWorker(QThread):
    """Check the selected model run and offer the nearest earlier live cycle."""

    # token, model, selected run, fxx, member, status, message, available run
    checked = Signal(int, str, object, int, object, str, str, object)

    def __init__(self, model: str, run_time: datetime, fxx: int,
                 member: str | None, token: int, parent=None):
        super().__init__(parent)
        self._model = model
        self._run_time = run_time
        self._fxx = int(fxx)
        self._member = member or None
        self.token = int(token)

    def run(self):  # noqa: D401 - QThread entry point
        from sharpmod.tools import model_extract

        errors = []
        cache_hit = False
        try:
            candidates = _model_probe_candidates(
                self._model, self._run_time, limit=4)
        except Exception as exc:  # noqa: BLE001 - a probe must not break UI
            errors.append(str(exc))
            candidates = [self._run_time]

        for index, run_time in enumerate(candidates):
            if self.isInterruptionRequested():
                return
            try:
                result = model_extract.probe(
                    self._model, run_time=run_time, fxx=self._fxx,
                    member=self._member,
                    open_subset=False,
                    cancelled=self.isInterruptionRequested,
                    request_timeout=2.0,
                    deadline_seconds=8.0,
                )
            except model_extract.DownloadCancelled:
                return
            except Exception as exc:  # noqa: BLE001 - network/catalog failure
                result = {"available": False, "error": str(exc)}
            if result.get("available"):
                if self.isInterruptionRequested():
                    return
                if index == 0:
                    status = AVAIL_AVAILABLE
                    message = f"Selected cycle {run_time:%Y-%m-%d %H}Z is available"
                else:
                    status = AVAIL_FALLBACK
                    message = (
                        f"Earlier cycle {run_time:%Y-%m-%d %H}Z is available")
                self.checked.emit(
                    self.token, self._model, self._run_time, self._fxx,
                    self._member, status, message, run_time)
                return
            error = str(result.get("error") or "").strip()
            if error:
                errors.append(error)

        message = "Availability could not be confirmed; Fetch remains available"
        if errors:
            _LOGGER.debug(
                "model_availability.probe_failed model=%s run=%s errors=%s",
                self._model, self._run_time, errors)
        self.checked.emit(
            self.token, self._model, self._run_time, self._fxx,
            self._member, AVAIL_UNKNOWN, message, None)


class _ModelFetchWorker(QThread):
    """Extract a forecast-model point sounding off the UI thread."""

    finished_ok = Signal(str, str, object, int)
    failed = Signal(str)
    cancelled = Signal()
    # stage, expected bytes, bytes already in the isolated tree, transfer start
    progress = Signal(str, int, int, float)

    def __init__(self, model: str, lat: float, lon: float, run_time: datetime,
                 fxx: int, out_path: str, loc: str | None = None,
                 resolve_place: bool = False,
                 member: str | None = None, download_dir: str | None = None,
                 model_hour_cache=None, cached_grib=None,
                 cached_source_fields=(), cached_cache=None,
                 cached_directory=None, cached_contract_version=None,
                 completed_pair=None,
                 parent=None):
        super().__init__(parent)
        self._model = model
        self._lat = float(lat)
        self._lon = float(lon)
        self._run_time = run_time
        self._fxx = int(fxx)
        self._out_path = out_path
        self._loc = loc
        self._resolve_place = bool(resolve_place) and not loc
        self._member = member or None
        self._output_dir = download_dir or os.path.dirname(out_path)
        # The picker polls this path while a GRIB download is active.  A cache
        # miss replaces it with the cache-owned directory before progress is
        # emitted; point-output cleanup always uses ``_output_dir``.
        self._download_dir = self._output_dir
        self._model_hour_cache = model_hour_cache
        self._cached_grib = (
            os.fspath(cached_grib) if cached_grib is not None else None
        )
        self._cached_source_fields = tuple(cached_source_fields or ())
        self._cached_cache = cached_cache
        self._cached_directory = cached_directory
        self._cached_contract_version = cached_contract_version
        self._completed_pair = completed_pair
        self.result = None
        self._cancel_requested = False
        self._progress_download_baseline = 0
        self._progress_transfer_started = 0.0

    def requestInterruption(self):  # noqa: N802 - Qt API override
        self._cancel_requested = True
        super().requestInterruption()

    def cancellation_requested(self) -> bool:
        return self._cancel_requested or self.isInterruptionRequested()

    def run(self):  # noqa: D401 - QThread entry point
        if self.cancellation_requested():
            self.cancelled.emit()
            return
        _LOGGER.info(
            "model_fetch.worker_start model=%s run=%s fxx=%03d lat=%.4f "
            "lon=%.4f download_dir=%s",
            self._model, self._run_time, self._fxx, self._lat, self._lon,
            self._download_dir)
        try:
            from sharpmod.tools import model_extract
            cfg = model_extract.get_config(self._model)
            if self._completed_pair and _portable_pair_valid(self._out_path):
                from sharpmod.analysis.batch_extract import _sha256

                path = Path(self._out_path)
                hashes = _sha256(path), _sha256(path.with_suffix(".json"))
                if hashes == self._completed_pair:
                    self._report_progress("saved")
                    self.result = (str(path), cfg.label, self._run_time, self._fxx)
                    if self.cancellation_requested():
                        self.cancelled.emit()
                    else:
                        self.finished_ok.emit(*self.result)
                    return
            if self._cached_grib is not None and not (
                model_extract.cached_source_fields_compatible(
                    cfg,
                    self._cached_source_fields,
                    self._cached_contract_version,
                )
            ):
                _LOGGER.info(
                    "model_fetch.cache_contract_miss model=%s path=%s "
                    "fields=%s",
                    cfg.key,
                    self._cached_grib,
                    self._cached_source_fields,
                )
                self._cached_grib = None
                self._cached_source_fields = ()
            if self._resolve_place and not self._loc:
                self._report_progress("town")
                from sharpmod.maps.place_names import reverse_town_name
                self._loc = reverse_town_name(
                    self._lat, self._lon) or cfg.label
            cache_hit = False
            if self._cached_grib is not None:
                if self._cached_directory is not None:
                    self._download_dir = os.fspath(self._cached_directory)
                protection = (
                    self._cached_cache.protect(self._cached_directory)
                    if self._cached_cache is not None
                    and self._cached_directory is not None
                    else nullcontext()
                )
                with protection:
                    self._report_progress("cached")
                    dataset = model_extract._LocalGribDataset(
                        self._cached_grib
                    )
                    try:
                        path = model_extract.extract(
                            self._model,
                            self._lat,
                            self._lon,
                            run_time=self._run_time,
                            fxx=self._fxx,
                            out_path=self._out_path,
                            loc=self._loc,
                            member=self._member,
                            dataset=dataset,
                            download_dir=self._cached_directory,
                            source_grib=self._cached_grib,
                            source_fields=self._cached_source_fields,
                            source_transport="offline-cache",
                            progress_callback=self._report_progress,
                            cancelled=self.cancellation_requested,
                        )
                    finally:
                        dataset.close()
                cache_hit = True
            elif self._model_hour_cache is None:
                path = model_extract.extract(
                    self._model,
                    self._lat,
                    self._lon,
                    run_time=self._run_time,
                    fxx=self._fxx,
                    out_path=self._out_path,
                    loc=self._loc,
                    member=self._member,
                    download_dir=self._download_dir,
                    progress_callback=self._report_progress,
                    cancelled=self.cancellation_requested,
                )
            else:
                from sharpmod.models.model_hour_cache import ModelHourKey

                run_dt = model_extract._run_datetime(self._run_time, cfg)
                key = ModelHourKey.create(
                    cfg.key,
                    run_dt,
                    self._fxx,
                    self._member,
                    spatial=model_extract.spatial_cache_key(
                        cfg, self._lat, self._lon
                    ),
                )

                def _load_hour(cache_dir):
                    self._download_dir = cache_dir
                    return model_extract._retrieve_dataset(
                        cfg,
                        run_dt,
                        self._fxx,
                        member=self._member,
                        download_dir=cache_dir,
                        progress_callback=self._report_progress,
                        cancelled=self.cancellation_requested,
                        lat=self._lat,
                        lon=self._lon,
                    )

                with self._model_hour_cache.lease(key, _load_hour) as (
                        entry, cache_hit):
                    self._download_dir = entry.download_dir
                    if cache_hit:
                        self._report_progress("cached")

                    def _cached_progress(stage, total=0):
                        if cache_hit and stage == "extracting":
                            stage = "cached"
                        self._report_progress(stage, total)

                    path = model_extract.extract(
                        cfg.key,
                        self._lat,
                        self._lon,
                        run_time=run_dt,
                        fxx=self._fxx,
                        out_path=self._out_path,
                        loc=self._loc,
                        member=self._member,
                        dataset=entry.dataset,
                        download_dir=entry.download_dir,
                        source_grib=entry.source_grib,
                        source_fields=entry.source_fields,
                        source_transport=entry.source_transport,
                        progress_callback=_cached_progress,
                        cancelled=self.cancellation_requested,
                    )
            _update_sounding_sidecar(path, cache_hit=bool(cache_hit))
        except model_extract.DownloadCancelled:
            _LOGGER.info(
                "model_fetch.worker_cancelled model=%s run=%s fxx=%03d",
                self._model, self._run_time, self._fxx)
            _cleanup_model_data(self._out_path, self._output_dir)
            self.cancelled.emit()
            return
        except Exception as exc:  # noqa: BLE001 - surface any model error to UI
            _LOGGER.exception(
                "model_fetch.worker_failed model=%s run=%s fxx=%03d",
                self._model, self._run_time, self._fxx)
            _cleanup_model_data(self._out_path, self._output_dir)
            self.failed.emit(f"Forecast model fetch failed: {exc}")
            return
        _LOGGER.info(
            "model_fetch.worker_ok model=%s run=%s fxx=%03d path=%s",
            self._model, self._run_time, self._fxx, path)
        self.result = (path, cfg.label, self._run_time, self._fxx)
        if self.cancellation_requested():
            self.cancelled.emit()
        else:
            self.finished_ok.emit(*self.result)

    def _report_progress(self, stage: str, total_bytes: int = 0) -> None:
        """Forward extractor progress safely across the Qt thread boundary."""
        stage = str(stage)
        if stage == "downloading":
            baseline = 0
            try:
                for root, _dirs, files in os.walk(self._download_dir):
                    for filename in files:
                        if filename.lower().endswith(
                            (".grib2", ".grib", ".grb2", ".grb", ".part")
                        ):
                            try:
                                baseline += os.path.getsize(
                                    os.path.join(root, filename)
                                )
                            except OSError:
                                pass
            except OSError:
                pass
            # Capture on the worker thread before the transfer begins. The Qt
            # signal is queued, so sampling this baseline in the UI slot can
            # otherwise count bytes that arrived between emit and delivery.
            self._progress_download_baseline = baseline
            self._progress_transfer_started = time.monotonic()
        self.progress.emit(
            stage,
            max(0, int(total_bytes or 0)),
            int(self._progress_download_baseline),
            float(self._progress_transfer_started),
        )


class _ModelPrefetchWorker(QThread):
    """Warm one next forecast hour without creating or displaying a sounding."""

    ready = Signal(str, object, int)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(
        self, model, lat, lon, run_time, fxx, member, model_hour_cache,
        parent=None,
    ):
        super().__init__(parent)
        self._model = str(model)
        self._lat = float(lat)
        self._lon = float(lon)
        self._run_time = run_time
        self._fxx = int(fxx)
        self._member = member or None
        self._model_hour_cache = model_hour_cache
        self._cancel_requested = False

    def requestInterruption(self):  # noqa: N802 - Qt API override
        self._cancel_requested = True
        super().requestInterruption()

    def cancellation_requested(self) -> bool:
        return self._cancel_requested or self.isInterruptionRequested()

    def run(self):
        if self.cancellation_requested():
            self.cancelled.emit()
            return
        try:
            from sharpmod.tools import model_extract
            from sharpmod.models.model_hour_cache import ModelHourKey

            cfg = model_extract.get_config(self._model)
            run_dt = model_extract._run_datetime(self._run_time, cfg)
            key = ModelHourKey.create(
                cfg.key,
                run_dt,
                self._fxx,
                self._member,
                spatial=model_extract.spatial_cache_key(
                    cfg, self._lat, self._lon
                ),
            )

            def load(cache_dir):
                return model_extract._retrieve_dataset(
                    cfg,
                    run_dt,
                    self._fxx,
                    member=self._member,
                    download_dir=cache_dir,
                    cancelled=self.cancellation_requested,
                    lat=self._lat,
                    lon=self._lon,
                )

            with self._model_hour_cache.lease(key, load):
                pass
        except model_extract.DownloadCancelled:
            self.cancelled.emit()
            return
        except Exception as exc:  # noqa: BLE001 - background best-effort path
            _LOGGER.exception(
                "model_prefetch.failed model=%s run=%s fxx=%03d",
                self._model, self._run_time, self._fxx,
            )
            self.failed.emit(str(exc))
            return
        if self.cancellation_requested():
            self.cancelled.emit()
        else:
            self.ready.emit(cfg.label, self._run_time, self._fxx)


class _StationListWorker(QThread):
    """Fetch the stations UWyo reported at a given time, off the UI thread.

    The bundled catalogue is fixed in time, so it misses stations that were
    relocated (and had their WMO index change). This worker queries the live
    ``/wsgi/sounding_json`` endpoint for the requested observation time and
    emits the normalized station records so the picker can show exactly what is
    choosable for that datetime.
    """

    #: (when_utc, list_of_station_records)
    loaded = Signal(object, object)
    #: (when_utc, human-readable message)
    failed = Signal(object, str)

    def __init__(self, when_utc: datetime, token: int, parent=None):
        super().__init__(parent)
        self._when = when_utc
        self.token = token

    def run(self):  # noqa: D401 - QThread entry point
        if self.isInterruptionRequested():
            return
        try:
            from sharpmod.io import uwyo_catalog as catalog
            stations = catalog.fetch_stations_for_datetime(self._when)
        except Exception as exc:  # noqa: BLE001 - never crash the UI thread
            self.failed.emit(self._when, str(exc))
            return
        if self.isInterruptionRequested():
            return
        self.loaded.emit(self._when, stations)


class _SpcOutlookWorker(QThread):
    """Resolve and decode the SPC outlook covering one valid time.

    Kept off the GUI thread for the same reason the hodograph locator refuses
    to touch the network while painting: the overlay is an embellishment, and a
    slow or unreachable SPC must never stall the picker.

    ``token`` lets the caller discard a result that arrived after the user moved
    on, matching the availability probes' staleness handling. "No outlook
    exists" is reported through :attr:`loaded` with a ``None`` layer rather than
    :attr:`failed`, because it is the correct answer for a date outside the
    2020+ archive or beyond Day 3, not an error worth alarming the user about.
    """

    #: (token, valid_time, layer_or_None)
    loaded = Signal(object, object, object)
    #: (token, valid_time, human-readable message)
    failed = Signal(object, object, str)

    def __init__(self, valid_time: datetime, token: int, parent=None,
                 product: str = "cat"):
        super().__init__(parent)
        self._valid_time = valid_time
        self._product = product
        self.token = token

    def run(self):  # noqa: D401 - QThread entry point
        if self.isInterruptionRequested():
            return
        try:
            from sharpmod.providers import spc_outlook
            layer = spc_outlook.fetch_layer(
                self._valid_time,
                product=self._product,
                should_cancel=self.isInterruptionRequested,
            )
        except Exception as exc:  # noqa: BLE001 - never crash the UI thread
            _LOGGER.debug("spc_outlook.worker_failed valid=%s err=%s",
                          self._valid_time, exc)
            self.failed.emit(self.token, self._valid_time, str(exc))
            return
        if self.isInterruptionRequested():
            return
        self.loaded.emit(self.token, self._valid_time, layer)


class _StormReportsWorker(QThread):
    """Fetch the storm reports around one valid time, off the GUI thread.

    Shaped like :class:`_SpcOutlookWorker`, because it answers the same kind of
    question -- what applies at this moment -- and is read against that outlook.
    Two differences follow from what reports are:

    * A window rather than an instant. Reports are discrete events, so a moment
      alone would almost always be empty; the provider is asked for a span
      centred on the selection.
    * A view, when one is offered. Reports are points and the feed is national,
      so passing the map's extent asks the service to send back only what can
      actually be drawn instead of the continent.

    "No reports in that window" is reported through :attr:`loaded` with a
    ``None`` layer, not :attr:`failed`: a quiet day is the correct answer, not an
    error worth alarming anyone about.
    """

    #: (token, valid_time, layer_or_None)
    loaded = Signal(object, object, object)
    #: (token, valid_time, human-readable message)
    failed = Signal(object, object, str)

    def __init__(self, valid_time, token: int, parent=None, view=None,
                 window=None, span_deg=None):
        super().__init__(parent)
        self._valid_time = valid_time
        self._view = view
        self._window = window
        self._span_deg = span_deg
        self.token = token

    def run(self):  # noqa: D401 - QThread entry point
        if self.isInterruptionRequested():
            return
        try:
            from sharpmod.providers import storm_reports
            window = self._window or storm_reports.DEFAULT_WINDOW
            layer = storm_reports.fetch_layer(
                window=window,
                view=self._view,
                around=self._valid_time,
                span_deg=self._span_deg,
                should_cancel=self.isInterruptionRequested,
            )
        except Exception as exc:  # noqa: BLE001 - never crash the UI thread
            _LOGGER.debug("storm_reports.worker_failed valid=%s err=%s",
                          self._valid_time, exc)
            self.failed.emit(self.token, self._valid_time, str(exc))
            return
        if self.isInterruptionRequested():
            return
        self.loaded.emit(self.token, self._valid_time, layer)


class _RadarMosaicWorker(QThread):
    """Fetch one live radar frame off the GUI thread.

    Shaped like :class:`_SpcOutlookWorker`, with one difference worth naming:
    there is no valid time. A radar frame is always "the newest one", so a
    result cannot be stale with respect to a selection -- only with respect to a
    later fetch, which ``token`` already handles.

    A cancelled fetch emits nothing at all. :func:`radar_mosaic.fetch_frame`
    answers ``None`` for cancellation and raises for every real failure, so the
    two are never confused into reporting an error the user caused by switching
    the overlay off.
    """

    #: (token, raster_or_None)
    loaded = Signal(object, object)
    #: (token, human-readable message)
    failed = Signal(object, str)

    def __init__(self, token: int, parent=None, product: str | None = None,
                 opacity: float = 1.0):
        super().__init__(parent)
        self._product = product
        self._opacity = float(opacity)
        self.token = token

    def run(self):  # noqa: D401 - QThread entry point
        if self.isInterruptionRequested():
            return
        try:
            from sharpmod.providers import radar_mosaic
            raster = radar_mosaic.fetch_frame(
                self._product,
                opacity=self._opacity,
                should_cancel=self.isInterruptionRequested,
            )
        except Exception as exc:  # noqa: BLE001 - never crash the UI thread
            _LOGGER.debug("radar_mosaic.worker_failed product=%s err=%s",
                          self._product, exc)
            self.failed.emit(self.token, str(exc))
            return
        if self.isInterruptionRequested() or raster is None:
            return
        self.loaded.emit(self.token, raster)


class _HrrrFieldWorker(QThread):
    """Fetch, derive and render one HRRR product off the GUI thread.

    Time-addressed like :class:`_SpcOutlookWorker` rather than always-newest
    like :class:`_RadarMosaicWorker`: a model field belongs to a run and a
    forecast hour, so the requested valid time travels with the result and is
    re-emitted for the controller to check against the selection it has by then.

    This worker does more than fetch. :func:`hrrr_field.fetch_field` also
    decodes GRIB, reprojects a 1059x1799 grid, and encodes a PNG -- hundreds of
    milliseconds of NumPy and eccodes work that would visibly stall the window if
    it ran on the GUI thread. The import stays inside :meth:`run` for the same
    reason the other workers' do: it pulls in eccodes and pyproj, which are
    optional extras, and a source checkout without them should fail this one
    overlay rather than the whole application.
    """

    #: (token, valid_time, raster_or_None)
    loaded = Signal(object, object, object)
    #: (token, human-readable message, no-data/offline/failed)
    failed = Signal(object, str, str)

    def __init__(self, token: int, parent=None, product: str | None = None,
                 valid_time=None, run=None, fxx=None, opacity: float = 1.0):
        super().__init__(parent)
        self._product = product
        self._valid_time = valid_time
        # A pinned cycle, when the caller selected one. ``run`` is what makes the
        # field the same forecast as the sounding rather than merely the same
        # hour; ``valid_time`` remains the fallback for the observed tab, which
        # has a moment but no cycle to pin to.
        self._run = run
        self._fxx = fxx
        self._opacity = float(opacity)
        self.token = token

    def run(self):  # noqa: D401 - QThread entry point
        if self.isInterruptionRequested():
            return
        hrrr_field = None
        try:
            from sharpmod.providers import hrrr_field
            raster = hrrr_field.fetch_field(
                self._product,
                valid_time=self._valid_time,
                run=self._run,
                fxx=self._fxx,
                opacity=self._opacity,
                should_cancel=self.isInterruptionRequested,
            )
        except Exception as exc:  # noqa: BLE001 - never crash the UI thread
            _LOGGER.debug("hrrr_field.worker_failed product=%s err=%s",
                          self._product, exc)
            if hrrr_field is not None and isinstance(
                    exc, hrrr_field.HrrrFieldUnavailable):
                kind = "no-data"
            elif hrrr_field is not None and isinstance(
                    exc, hrrr_field.HrrrFieldOffline):
                kind = "offline"
            else:
                kind = "failed"
            self.failed.emit(self.token, str(exc), kind)
            return
        if self.isInterruptionRequested() or raster is None:
            return
        self.loaded.emit(self.token, self._valid_time, raster)


class _RadarSiteWorker(QThread):
    """Fetch one single-site radar frame off the GUI thread.

    Same always-newest shape as :class:`_RadarMosaicWorker`, with one addition:
    which radar to use is resolved from the map's view, so the worker carries the
    view it was started with. The chosen site travels back on the signal because
    the caller needs it for the status line, and by the time the frame lands the
    user may have panned somewhere a different antenna would serve.
    """

    #: (token, raster_or_None)
    loaded = Signal(object, object)
    #: (token, human-readable message)
    failed = Signal(object, str)

    def __init__(self, token: int, parent=None, product: str | None = None,
                 view=None, site_id: str | None = None,
                 opacity: float = 1.0):
        super().__init__(parent)
        self._product = product
        self._view = view
        self._site_id = site_id
        self._opacity = float(opacity)
        self.token = token

    def run(self):  # noqa: D401 - QThread entry point
        if self.isInterruptionRequested():
            return
        try:
            from sharpmod.providers import radar_site
            raster = radar_site.fetch_frame(
                self._product,
                site_id=self._site_id,
                view=self._view,
                opacity=self._opacity,
                should_cancel=self.isInterruptionRequested,
            )
        except Exception as exc:  # noqa: BLE001 - never crash the UI thread
            _LOGGER.debug("radar_site.worker_failed product=%s err=%s",
                          self._product, exc)
            self.failed.emit(self.token, str(exc))
            return
        if self.isInterruptionRequested() or raster is None:
            return
        self.loaded.emit(self.token, raster)

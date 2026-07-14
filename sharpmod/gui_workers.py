from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import numpy as np

"""Observed and forecast fetch, availability, and catalog workers."""

from sharpmod.gui_common import _LOGGER, _uwyo_decoder_classes
from sharpmod.model_hour_cache import ModelHourKey

from qtpy.QtCore import (
    Qt, QThread, QTimer, Signal, QDate, QSettings, QPointF, QRectF, QSize, QUrl,
)
from qtpy.QtGui import (
    QAction, QPainter, QColor, QPen, QBrush, QPolygonF, QFont, QPixmap, QIcon,
    QTransform, QDesktopServices,
)
from qtpy.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QPushButton,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QLabel,
    QDateEdit,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QMessageBox,
    QTabWidget,
    QGroupBox,
    QStatusBar,
    QToolButton,
    QScrollArea,
    QFrame,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QCheckBox,
    QSizePolicy,
    QGraphicsView,
    QGraphicsScene,
    QProgressBar,
    QMenu,
)

# ===========================================================================
# Sounding availability pre-flight check (green / red / gray status)
# ===========================================================================
#: Availability states surfaced by :class:`_AvailabilityIndicator`.
AVAIL_UNKNOWN = "unknown"          # not checked yet
AVAIL_CHECKING = "checking"        # network probe in flight
AVAIL_AVAILABLE = "available"      # green  -- a usable sounding exists
AVAIL_FALLBACK = "fallback"        # amber -- an earlier model cycle exists
AVAIL_INSUFFICIENT = "insufficient"  # gray -- present but corrupt/too sparse
AVAIL_UNAVAILABLE = "unavailable"  # red   -- nothing archived / unreachable

#: Dot color per state (green available, red unavailable, gray insufficient).
_AVAIL_COLORS = {
    AVAIL_UNKNOWN: "#6f7d8f",
    AVAIL_CHECKING: "#e0a030",
    AVAIL_AVAILABLE: "#3fbf5f",
    AVAIL_FALLBACK: "#e0a030",
    AVAIL_INSUFFICIENT: "#9aa4b0",
    AVAIL_UNAVAILABLE: "#e0433a",
}

#: Default label per state (overridable with a specific message).
_AVAIL_LABELS = {
    AVAIL_UNKNOWN: "Not checked",
    AVAIL_CHECKING: "Checking...",
    AVAIL_AVAILABLE: "Available",
    AVAIL_FALLBACK: "Earlier cycle available",
    AVAIL_INSUFFICIENT: "Limited",
    AVAIL_UNAVAILABLE: "Unavailable",
}

#: Minimum decoded levels / moisture levels for a "best analysis" sounding.
_AVAIL_MIN_THERMO_LEVELS = 6
_AVAIL_MIN_MOISTURE_LEVELS = 3
_AVAIL_MIN_PRESSURE_SPAN_HPA = 150.0


def _station_label(station_id: str, name: str) -> str:
    """Format a station's index + city, e.g. ``"72357 \u2014 OUN Norman, OK"``.

    UWyo catalogue names are ``"<callsign> <city>, <state>"``; the id is the
    WMO station index. Both are shown so the observation is unambiguous.
    """
    sid = str(station_id or "").strip()
    city = str(name or "").strip()
    if sid and city:
        return f"{sid} \u2014 {city}"
    return sid or city


def _decoder_for_station(station: dict | None):
    """Build a UWyo decoder, seeding it with ``station`` when provided.

    When ``station`` (a ``{"id","name","lat","lon","src"}`` record from the
    live datetime-aware list) is given, the decoder resolves that id against a
    one-entry catalogue carrying its real UWyo ``src`` (``BUFR`` / ``FM35`` /
    ...). This lets the picker fetch stations that are *not* in the bundled
    catalogue -- e.g. relocated stations whose WMO index changed over time --
    and always requests the correct data source. Otherwise it falls back to the
    full bundled catalogue.

    Returns ``(decoder, resolve_query)`` where ``resolve_query`` is the string
    to hand :meth:`UWyo_Decoder.resolve_station`.
    """
    _StationLookupError, UWyo_Decoder, _UWyoError = _uwyo_decoder_classes()
    if station and station.get("id"):
        sid = str(station["id"])
        catalog = {
            sid: (
                station.get("name", ""),
                station.get("lat", float("nan")),
                station.get("lon", float("nan")),
                float("nan"),
                station.get("src") or UWyo_Decoder.DEFAULT_SRC,
            )
        }
        return UWyo_Decoder(station_catalog=catalog), sid
    return UWyo_Decoder(full_catalog=True), None


def _classify_availability(prof) -> tuple[str, str]:
    """Grade a successfully fetched profile as green vs. gray.

    A sounding that downloads and parses cleanly is still only useful for SPC
    analysis if it has a reasonable vertical extent with temperature, moisture,
    and wind. Returns ``(AVAIL_AVAILABLE | AVAIL_INSUFFICIENT, message)``.
    """
    import numpy as np

    try:
        def _valid(name):
            arr = np.ma.masked_invalid(np.ma.asarray(getattr(prof, name),
                                                      dtype=float))
            return arr, np.ma.getmaskarray(arr)

        pres, pmask = _valid("pres")
        _tmpc, tmask = _valid("tmpc")
        _dwpc, dmask = _valid("dwpc")
        _wspd, wmask = _valid("wspd")
    except Exception:  # noqa: BLE001 - malformed profile => gray
        return AVAIL_INSUFFICIENT, "Limited (data unreadable)"

    n_thermo = int(np.count_nonzero(~(pmask | tmask)))
    n_moist = int(np.count_nonzero(~(pmask | dmask)))
    n_wind = int(np.count_nonzero(~(pmask | wmask)))

    if n_thermo < _AVAIL_MIN_THERMO_LEVELS:
        return AVAIL_INSUFFICIENT, f"Limited ({n_thermo} levels)"

    valid_pres = pres.compressed()
    if valid_pres.size >= 2:
        span = float(np.nanmax(valid_pres) - np.nanmin(valid_pres))
        if span < _AVAIL_MIN_PRESSURE_SPAN_HPA:
            return AVAIL_INSUFFICIENT, "Limited (shallow profile)"

    notes = []
    if n_moist < _AVAIL_MIN_MOISTURE_LEVELS:
        notes.append("missing moisture")
    if n_wind < _AVAIL_MIN_MOISTURE_LEVELS:
        notes.append("missing wind")
    if notes:
        return AVAIL_INSUFFICIENT, "Limited (" + ", ".join(notes) + ")"

    return AVAIL_AVAILABLE, f"Available ({n_thermo} levels)"


class _AvailabilityWorker(QThread):
    """Probe UWyo for a station/time and classify the result off the UI thread.

    Emits :attr:`checked` with ``(station_id, when, status, message)`` where
    ``status`` is one of the ``AVAIL_*`` constants. The full fetch is performed
    (the availability of a *usable* sounding cannot be known without decoding
    it), so this shares the exact retrieval/decode path as a real fetch.
    """

    #: (query, when, status, message, station_label). ``station_label`` is a
    #: "index \u2014 city" string once the station resolves, else "".
    checked = Signal(str, object, str, str, str)

    def __init__(self, station_query: str, when_utc: datetime, token: int,
                 parent=None, station: dict | None = None):
        super().__init__(parent)
        self._query = station_query
        self._when = when_utc
        self.token = token
        self._station = station

    def run(self):  # noqa: D401 - QThread entry point
        try:
            StationLookupError, UWyo_Decoder, UWyoError = _uwyo_decoder_classes()
        except Exception:  # noqa: BLE001
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (decoder)", "")
            return

        # Typed errors let us distinguish "nothing archived" (red) from
        # "corrupt/unparseable" (gray). Missing imports degrade gracefully.
        try:
            from sharpmod.io.uwyo_decoder import (
                RetrievalError,
                SoundingParseError,
                StationTimeUnavailableError,
            )
        except Exception:  # noqa: BLE001 - fall back to base-class handling
            RetrievalError = SoundingParseError = StationTimeUnavailableError = ()

        # Resolve the station first so its index + city can be reported in
        # every outcome (available, unavailable, or insufficient). A live
        # station record (with its real UWyo ``src``) is used when available so
        # relocated / re-indexed stations resolve and fetch correctly.
        try:
            decoder, seeded_query = _decoder_for_station(self._station)
            meta = decoder.resolve_station(seeded_query or self._query)
        except StationLookupError:
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (station lookup)", "")
            return
        except Exception:  # noqa: BLE001
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (station lookup)", "")
            return

        label = _station_label(meta.id, meta.name)

        try:
            prof = decoder.fetch(meta.id, self._when)
        except SoundingParseError:
            self.checked.emit(self._query, self._when, AVAIL_INSUFFICIENT,
                              "Limited (data unreadable)", label)
            return
        except StationTimeUnavailableError:
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (no sounding)", label)
            return
        except RetrievalError:
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (service unreachable)", label)
            return
        except UWyoError:
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (fetch failed)", label)
            return
        except Exception:  # noqa: BLE001 - never crash the UI thread
            self.checked.emit(self._query, self._when, AVAIL_UNAVAILABLE,
                              "Unavailable (unexpected error)", label)
            return

        status, message = _classify_availability(prof)
        self.checked.emit(self._query, self._when, status, message, label)


class _AvailabilityIndicator(QWidget):
    """A colored dot + text reporting a station's sounding availability.

    Shows the resolved station index and city on a header line (when known)
    above the color-coded availability status.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        self.setMinimumHeight(50)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(8)
        self._dot = QLabel()
        self._dot.setFixedSize(14, 14)
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        self._station = QLabel()
        self._station.setWordWrap(True)
        self._station.setMinimumHeight(18)
        self._station.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        self._station.setStyleSheet("color:#d5e0ef; font-weight:bold;")
        self._station.setVisible(False)
        self._text = QLabel()
        self._text.setWordWrap(True)
        self._text.setMinimumHeight(24)
        self._text.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._text.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        text_col.addWidget(self._station)
        text_col.addWidget(self._text)
        lay.addWidget(self._dot, 0, Qt.AlignTop)
        lay.addLayout(text_col, 1)
        self.set_status(AVAIL_UNKNOWN)

    def set_status(self, status: str, message: str | None = None,
                   station_label: str | None = None) -> None:
        color = _AVAIL_COLORS.get(status, _AVAIL_COLORS[AVAIL_UNKNOWN])
        self._dot.setStyleSheet(
            f"background:{color}; border-radius:7px; border:1px solid #0c1118;")
        label = (station_label or "").strip()
        if " \u2014 " in label:
            display_label = label.split(" \u2014 ", 1)[0]
        elif len(label) > 48:
            display_label = label[:45].rstrip() + "\u2026"
        else:
            display_label = label
        self._station.setText(display_label)
        self._station.setVisible(bool(label))
        text = message or _AVAIL_LABELS.get(status, "")
        self._text.setText(text)
        self._text.setStyleSheet(f"color:{color}; font-weight:bold;")
        self.setToolTip(f"{label}\n{text}".strip() if label else text)


# ===========================================================================
# UWyo fetch worker (keeps the picker UI responsive during the network call)
# ===========================================================================
class _FetchWorker(QThread):
    """Fetch a UWyo sounding off the UI thread and write a temp ``.npz``.

    Emits :attr:`finished_ok` with ``(npz_path, station_meta, when)`` on
    success or :attr:`failed` with a human-readable message on any error.
    """

    finished_ok = Signal(str, object, object)
    failed = Signal(str)

    def __init__(self, station_query: str, when_utc: datetime, parent=None,
                 station: dict | None = None):
        super().__init__(parent)
        self._query = station_query
        self._when = when_utc
        self._station = station

    def run(self):  # noqa: D401 - QThread entry point
        try:
            StationLookupError, UWyo_Decoder, UWyoError = _uwyo_decoder_classes()
        except Exception as exc:  # noqa: BLE001 - surface import/freezer issues
            self.failed.emit(f"UWyo decoder is unavailable: {exc}")
            return
        try:
            decoder, seeded_query = _decoder_for_station(self._station)
            meta = decoder.resolve_station(seeded_query or self._query)
            prof = decoder.fetch(meta.id, self._when)
        except StationLookupError as exc:
            self.failed.emit(f"Station lookup failed: {exc}")
            return
        except UWyoError as exc:
            self.failed.emit(f"UWyo fetch failed: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 - surface any error to the UI
            self.failed.emit(f"Unexpected error: {exc}")
            return

        try:
            # Reuse the tested UWyo -> .npz writer so the interactive path and
            # the CLI/PNG path share one output format + metadata.
            from sharpmod.tools.uwyo_sounding import _write_npz

            prof_meta = dict(getattr(prof, "meta", {}) or {})
            if prof_meta.get("lat") != prof_meta.get("lat") or "lat" not in prof_meta:
                prof_meta["lat"] = meta.lat
            if prof_meta.get("lon") != prof_meta.get("lon") or "lon" not in prof_meta:
                prof_meta["lon"] = meta.lon
            prof_meta.setdefault("valid", self._when)

            loc = meta.name.split(",")[0].split()[0]
            fd, npz_path = tempfile.mkstemp(
                prefix=f"uwyo_{meta.id}_{self._when:%Y%m%d%H}_", suffix=".npz")
            os.close(fd)
            _write_npz(prof, npz_path, prof_meta, loc)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Could not save fetched sounding: {exc}")
            return

        self.finished_ok.emit(npz_path, meta, self._when)


def _cleanup_model_data(npz_path: str, download_dir: str) -> None:
    """Remove one isolated forecast-model fetch tree."""
    _LOGGER.info(
        "model_data.cleanup npz=%s download_dir=%s", npz_path, download_dir)
    from sharpmod.tools import model_extract
    model_extract.cleanup_transient_data(npz_path, download_dir)


def _retain_model_data_until_close(viewer, npz_path: str,
                                   download_dir: str) -> None:
    """Keep model data alive until ``viewer`` is actually closed."""
    _LOGGER.info(
        "model_data.retain viewer=%s npz=%s download_dir=%s",
        id(viewer), npz_path, download_dir)
    viewer.setAttribute(Qt.WA_DeleteOnClose, True)
    viewer.destroyed.connect(
        lambda *_args: _cleanup_model_data(npz_path, download_dir))


def _model_probe_candidates(model: str, requested_run: datetime,
                            limit: int = 4) -> list[datetime]:
    """Return the selected run followed by recent configured model cycles."""
    from sharpmod.tools import model_extract

    cfg = model_extract.get_config(model)
    requested = requested_run.replace(tzinfo=timezone.utc) \
        if requested_run.tzinfo is None \
        else requested_run.astimezone(timezone.utc)
    requested = requested.replace(minute=0, second=0, microsecond=0)
    count = max(1, int(limit))
    candidates = [requested]
    configured_hours = set(int(hour) for hour in cfg.cycles)
    cursor = requested
    # All supported cycle schedules repeat daily. Walking by hours also handles
    # the midnight boundary without special date arithmetic.
    while len(candidates) < count:
        cursor -= timedelta(hours=1)
        if cursor.hour in configured_hours:
            candidates.append(cursor)
    return candidates


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
        try:
            candidates = _model_probe_candidates(
                self._model, self._run_time, limit=4)
        except Exception as exc:  # noqa: BLE001 - a probe must not break UI
            errors.append(str(exc))
            candidates = [self._run_time]

        for index, run_time in enumerate(candidates):
            try:
                result = model_extract.probe(
                    self._model, run_time=run_time, fxx=self._fxx,
                    member=self._member, open_subset=False)
            except Exception as exc:  # noqa: BLE001 - network/catalog failure
                result = {"available": False, "error": str(exc)}
            if result.get("available"):
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
    progress = Signal(str, int)

    def __init__(self, model: str, lat: float, lon: float, run_time: datetime,
                 fxx: int, out_path: str, loc: str | None = None,
                 member: str | None = None, download_dir: str | None = None,
                 model_hour_cache=None, parent=None):
        super().__init__(parent)
        self._model = model
        self._lat = float(lat)
        self._lon = float(lon)
        self._run_time = run_time
        self._fxx = int(fxx)
        self._out_path = out_path
        self._loc = loc
        self._member = member or None
        self._output_dir = download_dir or os.path.dirname(out_path)
        # The picker polls this path while a GRIB download is active.  A cache
        # miss replaces it with the cache-owned directory before progress is
        # emitted; point-output cleanup always uses ``_output_dir``.
        self._download_dir = self._output_dir
        self._model_hour_cache = model_hour_cache
        self._cancel_requested = False

    def requestInterruption(self):  # noqa: N802 - Qt API override
        self._cancel_requested = True
        super().requestInterruption()

    def cancellation_requested(self) -> bool:
        return self._cancel_requested or self.isInterruptionRequested()

    def run(self):  # noqa: D401 - QThread entry point
        _LOGGER.info(
            "model_fetch.worker_start model=%s run=%s fxx=%03d lat=%.4f "
            "lon=%.4f download_dir=%s",
            self._model, self._run_time, self._fxx, self._lat, self._lon,
            self._download_dir)
        try:
            from sharpmod.tools import model_extract
            cfg = model_extract.get_config(self._model)
            if self._model_hour_cache is None:
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
                        source_grib=entry.source_grib,
                        source_fields=entry.source_fields,
                        source_transport=entry.source_transport,
                        progress_callback=_cached_progress,
                        cancelled=self.cancellation_requested,
                    )
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
        self.finished_ok.emit(path, cfg.label, self._run_time, self._fxx)

    def _report_progress(self, stage: str, total_bytes: int = 0) -> None:
        """Forward extractor progress safely across the Qt thread boundary."""
        self.progress.emit(str(stage), max(0, int(total_bytes or 0)))


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


# ===========================================================================
# Datetime-aware station-list worker (relocated / re-indexed station support)
# ===========================================================================
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
        try:
            from sharpmod.io import uwyo_catalog as catalog
            stations = catalog.fetch_stations_for_datetime(self._when)
        except Exception as exc:  # noqa: BLE001 - never crash the UI thread
            self.failed.emit(self._when, str(exc))
            return
        self.loaded.emit(self._when, stations)

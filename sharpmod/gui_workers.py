from __future__ import annotations

from contextlib import nullcontext
import logging
import os
import re
import json
import shutil
import sys
import tempfile
import time
from types import SimpleNamespace
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
    """Fetch one observed sounding with explicit UWyo → IEM fallback.

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
            from sharpmod.observations import (
                IEMObservedProvider,
                ObservedFallbackError,
                UWyoObservedProvider,
                fetch_observed,
                write_observed_npz,
            )
        except Exception as exc:  # noqa: BLE001 - import/freezer boundary
            self.failed.emit(
                f"Observed-sounding providers are unavailable: {exc}"
            )
            return
        try:
            decoder, seeded_query = _decoder_for_station(self._station)
            result = fetch_observed(
                seeded_query or self._query,
                self._when,
                providers=(
                    UWyoObservedProvider(decoder=decoder),
                    IEMObservedProvider(),
                ),
            )
        except ObservedFallbackError as exc:
            self.failed.emit(f"Observed sounding fetch failed: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 - surface any error to the UI
            self.failed.emit(f"Observed-sounding providers are unavailable: {exc}")
            return

        npz_path = None
        try:
            metadata = dict(result.metadata)
            station_name = str(
                metadata.get("station_name") or result.station_id
            )
            meta = SimpleNamespace(
                id=result.station_id,
                name=station_name,
                lat=float(metadata.get("lat", float("nan"))),
                lon=float(metadata.get("lon", float("nan"))),
                provider=result.provider,
            )
            loc = station_name.split(",")[0].split()[0]
            fd, npz_path = tempfile.mkstemp(
                prefix=(
                    f"observed_{result.provider}_{meta.id}_"
                    f"{self._when:%Y%m%d%H}_"
                ),
                suffix=".npz",
            )
            os.close(fd)
            write_observed_npz(result, npz_path, loc=loc)
        except Exception as exc:  # noqa: BLE001
            _cleanup_point_data(npz_path, None)
            self.failed.emit(f"Could not save fetched sounding: {exc}")
            return

        self.finished_ok.emit(npz_path, meta, self._when)


def _cleanup_model_data(npz_path: str, download_dir: str) -> None:
    """Remove one isolated forecast-model fetch tree."""
    _LOGGER.info(
        "model_data.cleanup npz=%s download_dir=%s", npz_path, download_dir)
    _cleanup_point_data(npz_path, download_dir)


def _cleanup_point_data(npz_path: str | None,
                        output_dir: str | None) -> None:
    """Remove one viewer-owned portable sounding and its isolated directory."""
    if npz_path:
        try:
            os.remove(os.fspath(npz_path))
        except OSError:
            pass
        try:
            os.remove(os.path.splitext(os.fspath(npz_path))[0] + ".json")
        except OSError:
            pass
    if output_dir:
        shutil.rmtree(os.fspath(output_dir), ignore_errors=True)


def _retain_point_data_until_close(viewer, npz_path: str,
                                   output_dir: str) -> None:
    """Keep one GUI-produced point sounding alive until its viewer closes."""
    _LOGGER.info(
        "point_data.retain viewer=%s npz=%s output_dir=%s",
        id(viewer), npz_path, output_dir)
    viewer.setAttribute(Qt.WA_DeleteOnClose, True)
    viewer.destroyed.connect(
        lambda *_args: _cleanup_point_data(npz_path, output_dir))


def _retain_model_data_until_close(viewer, npz_path: str,
                                   download_dir: str) -> None:
    """Keep model data alive until ``viewer`` is actually closed."""
    _LOGGER.info(
        "model_data.retain viewer=%s npz=%s download_dir=%s",
        id(viewer), npz_path, download_dir)
    _retain_point_data_until_close(viewer, npz_path, download_dir)


def _portable_pair_valid(npz_path) -> bool:
    """Return whether a cached portable sounding and sidecar are complete."""
    npz_path = os.fspath(npz_path)
    json_path = os.path.splitext(npz_path)[0] + ".json"
    if not os.path.isfile(npz_path) or not os.path.isfile(json_path):
        return False
    try:
        with np.load(npz_path, allow_pickle=True) as data:
            required = {"pres", "hght", "tmpc", "dwpc", "wdir", "wspd"}
            if not required.issubset(data.files) or np.asarray(data["pres"]).size < 2:
                return False
        with open(json_path, encoding="utf-8") as handle:
            return isinstance(json.load(handle), dict)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _atomic_npz(path, arrays) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(suffix=".npz", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            np.savez(handle, **arrays)
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


def _atomic_json(path, payload) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
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


def _update_sounding_sidecar(npz_path, **values) -> None:
    """Atomically add GUI/cache provenance to an extractor sidecar."""
    path = os.path.splitext(os.path.abspath(os.fspath(npz_path)))[0] + ".json"
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("sounding sidecar must contain a JSON object")
    payload.update(values)
    _atomic_json(path, payload)


def _materialize_cached_sounding(source_npz, out_path, *, loc,
                                 requested_lat, requested_lon,
                                 cache_hit=False) -> None:
    """Create a viewer-owned copy while retaining current request metadata."""
    source_npz = os.fspath(source_npz)
    source_json = os.path.splitext(source_npz)[0] + ".json"
    out_path = os.fspath(out_path)
    out_json = os.path.splitext(out_path)[0] + ".json"
    with np.load(source_npz, allow_pickle=True) as source:
        arrays = {name: source[name] for name in source.files}
    arrays["loc"] = str(loc)
    with open(source_json, encoding="utf-8") as handle:
        metadata = json.load(handle)
    metadata.update({
        "loc": str(loc),
        "requested_lat": float(requested_lat),
        "requested_lon": float(requested_lon),
        "cache_hit": bool(cache_hit),
        "npz": os.path.abspath(out_path),
    })
    _atomic_npz(out_path, arrays)
    try:
        _atomic_json(out_json, metadata)
    except BaseException:
        try:
            os.remove(out_path)
        except OSError:
            pass
        raise


class _ERA5FetchWorker(QThread):
    """Retrieve/cache one ERA5 analysis and create a viewer-owned output."""

    finished_ok = Signal(str, object, float, float, bool)
    failed = Signal(str)
    cancelled = Signal()
    progress = Signal(str)

    def __init__(self, lat, lon, valid_time, out_path, *, loc="ERA5pt",
                 disk_cache=None, parent=None):
        super().__init__(parent)
        self._lat = float(lat)
        self._lon = float(lon)
        self._valid_time = valid_time
        self._out_path = os.fspath(out_path)
        self._output_dir = os.path.dirname(self._out_path)
        self._loc = str(loc or "ERA5pt")
        self._disk_cache = disk_cache
        self._cancel_requested = False

    def requestInterruption(self):  # noqa: N802 - Qt API override
        self._cancel_requested = True
        super().requestInterruption()

    def cancellation_requested(self) -> bool:
        return self._cancel_requested or self.isInterruptionRequested()

    def _report_progress(self, stage) -> None:
        self.progress.emit(str(stage))

    def run(self):
        from sharpmod.tools import era5_extract

        cache_hit = False
        try:
            if self.cancellation_requested():
                raise era5_extract.ExtractionCancelled("ERA5 fetch cancelled")
            snapped_lat, snapped_lon = era5_extract._nearest_era5_grid_point(
                self._lat, self._lon)
            if self._disk_cache is None:
                path = era5_extract.extract(
                    self._lat, self._lon, self._valid_time, self._out_path,
                    loc=self._loc,
                    progress_callback=self._report_progress,
                    cancelled=self.cancellation_requested,
                )
            else:
                valid = era5_extract._as_datetime(self._valid_time)
                if valid.tzinfo is None:
                    valid = valid.replace(tzinfo=timezone.utc)
                else:
                    valid = valid.astimezone(timezone.utc)
                valid = valid.replace(minute=0, second=0, microsecond=0)
                key = ModelHourKey.create(
                    "era5", valid, 0,
                    spatial=f"{snapped_lat:.2f},{snapped_lon:.2f}",
                )
                cache_dir = self._disk_cache.directory_for(key)
                cache_npz = os.path.join(cache_dir, "era5-point.npz")
                with self._disk_cache.protect(cache_dir):
                    cache_hit = _portable_pair_valid(cache_npz)
                    if not cache_hit:
                        try:
                            os.remove(cache_npz)
                        except OSError:
                            pass
                        try:
                            os.remove(os.path.splitext(cache_npz)[0] + ".json")
                        except OSError:
                            pass
                        era5_extract.extract(
                            snapped_lat, snapped_lon, valid, cache_npz,
                            loc="ERA5",
                            progress_callback=self._report_progress,
                            cancelled=self.cancellation_requested,
                        )
                    else:
                        self._report_progress("cached")
                    self._disk_cache.annotate(
                        cache_dir,
                        source_url=f"cds://{era5_extract.ERA5_CDS_DATASET}",
                        source_transport="cdsapi",
                        source_provider="Copernicus Climate Data Store",
                        source_fields=era5_extract.ERA5_CDS_VARIABLES,
                    )
                    if self.cancellation_requested():
                        raise era5_extract.ExtractionCancelled(
                            "ERA5 fetch cancelled")
                    self._report_progress("writing")
                    _materialize_cached_sounding(
                        cache_npz, self._out_path, loc=self._loc,
                        requested_lat=self._lat, requested_lon=self._lon,
                        cache_hit=cache_hit,
                    )
                    path = self._out_path
                    self._report_progress("complete")
        except era5_extract.ExtractionCancelled:
            _cleanup_point_data(self._out_path, self._output_dir)
            self.cancelled.emit()
            return
        except Exception as exc:  # noqa: BLE001 - surface optional/network errors
            _LOGGER.exception("era5_fetch.worker_failed")
            _cleanup_point_data(self._out_path, self._output_dir)
            self.failed.emit(f"ERA5 fetch failed: {exc}")
            return
        self.finished_ok.emit(
            path, self._valid_time, snapped_lat, snapped_lon, cache_hit)


class _WRFInspectWorker(QThread):
    """Inspect WRF coordinates and times without blocking Qt."""

    inspected = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self._path = os.fspath(path)
        self._cancel_requested = False

    def requestInterruption(self):  # noqa: N802 - Qt API override
        self._cancel_requested = True
        super().requestInterruption()

    def cancellation_requested(self) -> bool:
        return self._cancel_requested or self.isInterruptionRequested()

    def run(self):
        from sharpmod.tools import wrf_extract
        try:
            result = wrf_extract.inspect_file(
                self._path, cancelled=self.cancellation_requested)
        except wrf_extract.ExtractionCancelled:
            self.cancelled.emit()
            return
        except Exception as exc:  # noqa: BLE001 - file/backend errors to UI
            _LOGGER.exception("wrf_inspect.worker_failed path=%s", self._path)
            self.failed.emit(f"Could not inspect raw WRF output: {exc}")
            return
        self.inspected.emit(result)


class _WRFExtractWorker(QThread):
    """Extract one raw wrfout point sounding without blocking Qt."""

    finished_ok = Signal(str, object)
    failed = Signal(str)
    cancelled = Signal()
    progress = Signal(str)

    def __init__(self, path, lat, lon, out_path, *, valid_time=None,
                 loc="WRFpt", parent=None):
        super().__init__(parent)
        self._path = os.fspath(path)
        self._lat = float(lat)
        self._lon = float(lon)
        self._out_path = os.fspath(out_path)
        self._output_dir = os.path.dirname(self._out_path)
        self._valid_time = valid_time
        self._loc = str(loc or "WRFpt")
        self._cancel_requested = False

    def requestInterruption(self):  # noqa: N802 - Qt API override
        self._cancel_requested = True
        super().requestInterruption()

    def cancellation_requested(self) -> bool:
        return self._cancel_requested or self.isInterruptionRequested()

    def run(self):
        from sharpmod.tools import wrf_extract
        try:
            path = wrf_extract.extract(
                self._path, self._lat, self._lon, self._out_path,
                valid_time=self._valid_time, loc=self._loc,
                progress_callback=lambda stage: self.progress.emit(str(stage)),
                cancelled=self.cancellation_requested,
            )
        except wrf_extract.ExtractionCancelled:
            _cleanup_point_data(self._out_path, self._output_dir)
            self.cancelled.emit()
            return
        except Exception as exc:  # noqa: BLE001 - file/backend errors to UI
            _LOGGER.exception("wrf_extract.worker_failed path=%s", self._path)
            _cleanup_point_data(self._out_path, self._output_dir)
            self.failed.emit(f"WRF extraction failed: {exc}")
            return
        self.finished_ok.emit(path, self._valid_time)


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
        cache_hit = False
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
                 model_hour_cache=None, cached_grib=None,
                 cached_source_fields=(), cached_cache=None,
                 cached_directory=None, parent=None):
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
        self._cached_grib = (
            os.fspath(cached_grib) if cached_grib is not None else None
        )
        self._cached_source_fields = tuple(cached_source_fields or ())
        self._cached_cache = cached_cache
        self._cached_directory = cached_directory
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
            cache_hit = False
            if self._cached_grib is not None:
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

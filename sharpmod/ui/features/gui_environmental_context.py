"""Qt controller for optional GOES and surface-observation map context."""

from __future__ import annotations

from datetime import datetime
import math
from time import monotonic

from qtpy.QtCore import QObject, QThread, QTimer, Qt, Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from sharpmod.providers.satellite_context import (
    CHANNELS,
    GOES_OVERLAY_KEY,
    SatelliteCancelled,
    fetch_satellite_overlay,
)
from sharpmod.providers.surface_observations import (
    SURFACE_OVERLAY_KEY,
    SurfaceObservationCancelled,
    fetch_surface_observations,
    surface_overlay_layer,
)
from sharpmod.ui.picker.layout import dependent_panel
from sharpmod.ui.styles.theme import OBJ_HINT, OBJ_PLAIN, SPACE


_DEBOUNCE_MS = 450
_SHUTDOWN_WAIT_MS = 2500


class _WorkerFleet:
    def __init__(self):
        self._live = []

    def track(self, worker):
        self._live.append(worker)
        worker.finished.connect(lambda: self.retire(worker))

    def retire(self, worker):
        self._live = [item for item in self._live if item is not worker]

    def interrupt(self):
        for worker in tuple(self._live):
            try:
                worker.requestInterruption()
            except RuntimeError:
                self.retire(worker)

    def drain(self, budget_ms):
        self.interrupt()
        deadline = monotonic() + max(0, int(budget_ms)) / 1000.0
        for worker in tuple(self._live):
            remaining = int((deadline - monotonic()) * 1000.0)
            if remaining <= 0:
                break
            try:
                if worker.isRunning():
                    worker.wait(remaining)
            except RuntimeError:
                self.retire(worker)
        # Retain any socket-blocked workers at process scope, matching the other
        # picker overlay controllers' bounded close behaviour.
        from sharpmod.ui.features.gui_threading import retain_worker_until_finished

        for worker in tuple(self._live):
            try:
                running = worker.isRunning()
            except RuntimeError:
                self.retire(worker)
                continue
            if running and retain_worker_until_finished(worker):
                self.retire(worker)


class _SatelliteWorker(QThread):
    loaded = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, token, when, bounds, channel, parent=None):
        super().__init__(parent)
        self.token = int(token)
        self.when = when
        self.bounds = tuple(bounds)
        self.channel = str(channel)

    def run(self):
        try:
            result = fetch_satellite_overlay(
                self.when,
                bounds=self.bounds,
                channel=self.channel,
                cancel=self.isInterruptionRequested,
            )
        except SatelliteCancelled:
            return
        except Exception as exc:  # noqa: BLE001 - thread boundary
            self.failed.emit(self.token, str(exc))
            return
        if not self.isInterruptionRequested():
            self.loaded.emit(self.token, result)


class _SurfaceWorker(QThread):
    loaded = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, token, latitude, longitude, when, parent=None):
        super().__init__(parent)
        self.token = int(token)
        self.latitude = float(latitude)
        self.longitude = float(longitude)
        self.when = when

    def run(self):
        try:
            result = fetch_surface_observations(
                self.latitude,
                self.longitude,
                self.when,
                cancel=self.isInterruptionRequested,
            )
        except SurfaceObservationCancelled:
            return
        except Exception as exc:  # noqa: BLE001 - thread boundary
            self.failed.emit(self.token, str(exc))
            return
        if not self.isInterruptionRequested():
            self.loaded.emit(self.token, result)


class EnvironmentalContextController(QObject):
    """Keep one picker map synchronized with optional environmental context."""

    contextChanged = Signal()

    #: Emitted whenever the layer's list rows may have changed (T19.1).
    layerChanged = Signal()

    _DENSITIES = {
        "sparse": (64, 14),
        "normal": (46, 24),
        "dense": (34, 32),
    }

    def __init__(
        self,
        map_widget,
        *,
        parent=None,
        channel="infrared",
        opacity=0.72,
        density="normal",
    ):
        super().__init__(parent)
        self._map = map_widget
        self._valid_time: datetime | None = None
        self._satellite = None
        self._surface = None
        self._sat_token = 0
        self._surface_token = 0
        self._sat_workers = _WorkerFleet()
        self._surface_workers = _WorkerFleet()

        self._sat_timer = QTimer(self)
        self._sat_timer.setSingleShot(True)
        self._sat_timer.setInterval(_DEBOUNCE_MS)
        self._sat_timer.timeout.connect(self._start_satellite)
        self._surface_timer = QTimer(self)
        self._surface_timer.setSingleShot(True)
        self._surface_timer.setInterval(_DEBOUNCE_MS)
        self._surface_timer.timeout.connect(self._start_surface)

        self._content = QWidget()
        self._content.setObjectName(OBJ_PLAIN)
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["xs"])

        self._sat_check = QCheckBox("GOES satellite")
        self._sat_check.setObjectName("environmentSatelliteToggle")
        self._sat_check.setToolTip(
            "Time-match public NOAA GOES imagery to the selected analysis hour"
        )
        self._sat_check.toggled.connect(self._on_satellite_toggled)
        layout.addWidget(self._sat_check)

        # Was a hardcoded 18px indent. ``dependent_panel`` derives the same idea
        # from the check-box indicator column, so these settings line up with
        # their switch's label -- and with every other overlay's settings, which
        # previously were not indented at all.
        self._sat_details, sat_layout = dependent_panel()
        self._channel = QComboBox()
        self._channel.setObjectName("environmentSatelliteChannel")
        self._channel.setAccessibleName("Satellite imagery channel")
        for key, (_number, title, resolution) in CHANNELS.items():
            self._channel.addItem(f"{title} · {resolution:g} km native", key)
        index = self._channel.findData(channel)
        self._channel.setCurrentIndex(index if index >= 0 else 0)
        self._channel.currentIndexChanged.connect(self._queue_satellite)
        sat_layout.addWidget(self._channel)
        opacity_row = QHBoxLayout()
        opacity_label = QLabel("Opacity")
        opacity_label.setObjectName(OBJ_HINT)
        opacity_row.addWidget(opacity_label)
        self._opacity = QSlider(Qt.Horizontal)
        self._opacity.setObjectName("environmentSatelliteOpacity")
        self._opacity.setRange(20, 90)
        self._opacity.setValue(int(round(max(0.2, min(0.9, opacity)) * 100)))
        self._opacity.valueChanged.connect(self._on_opacity)
        opacity_row.addWidget(self._opacity, 1)
        # T19.2: numeric readout plus reset, matching the radar/field rows.
        from qtpy.QtWidgets import QSpinBox as _SatSpin
        from qtpy.QtWidgets import QToolButton as _SatReset

        self._opacity_spin = _SatSpin()
        self._opacity_spin.setRange(20, 90)
        self._opacity_spin.setSuffix("%")
        self._opacity_spin.setValue(self._opacity.value())
        self._opacity_spin.setToolTip("Satellite opacity, in percent")
        self._opacity_spin.setAccessibleName("Satellite opacity percent")
        self._opacity_spin.valueChanged.connect(self._on_opacity_spin)
        self._opacity.valueChanged.connect(self._sync_opacity_spin)
        opacity_row.addWidget(self._opacity_spin)
        self._opacity_reset = _SatReset()
        self._opacity_reset.setText("Reset")
        self._opacity_reset.setToolTip("Return satellite opacity to 75%")
        self._opacity_reset.setAccessibleName("Reset satellite opacity")
        self._opacity_reset.clicked.connect(self.reset_opacity)
        opacity_row.addWidget(self._opacity_reset)
        sat_layout.addLayout(opacity_row)
        self._sat_status = QLabel("")
        self._sat_status.setObjectName(OBJ_HINT)
        self._sat_status.setWordWrap(True)
        sat_layout.addWidget(self._sat_status)
        layout.addWidget(self._sat_details)

        self._surface_check = QCheckBox("Surface station observations")
        self._surface_check.setObjectName("environmentSurfaceToggle")
        self._surface_check.setToolTip(
            "Show nearby NWS API station observations matched to the selected time"
        )
        self._surface_check.toggled.connect(self._on_surface_toggled)
        layout.addWidget(self._surface_check)

        self._surface_details, surface_layout = dependent_panel()
        density_row = QHBoxLayout()
        density_label = QLabel("Plot density")
        density_label.setObjectName(OBJ_HINT)
        density_row.addWidget(density_label)
        self._density = QComboBox()
        self._density.setObjectName("environmentSurfaceDensity")
        for key in self._DENSITIES:
            self._density.addItem(key.title(), key)
        density_index = self._density.findData(density)
        self._density.setCurrentIndex(density_index if density_index >= 0 else 1)
        self._density.currentIndexChanged.connect(self._rerender_surface)
        density_row.addWidget(self._density, 1)
        surface_layout.addLayout(density_row)
        self._surface_status = QLabel("")
        self._surface_status.setObjectName(OBJ_HINT)
        self._surface_status.setWordWrap(True)
        surface_layout.addWidget(self._surface_status)
        layout.addWidget(self._surface_details)

        self._sat_details.hide()
        self._surface_details.hide()

    def controls_widget(self):
        return self._content

    def channel(self) -> str:
        return str(self._channel.currentData() or "infrared")

    def set_channel(self, channel: str) -> None:
        index = self._channel.findData(str(channel))
        if index >= 0:
            self._channel.setCurrentIndex(index)

    def opacity(self) -> float:
        return self._opacity.value() / 100.0

    def density(self) -> str:
        return str(self._density.currentData() or "normal")

    def is_satellite_enabled(self) -> bool:
        return self._sat_check.isChecked()

    def is_surface_enabled(self) -> bool:
        return self._surface_check.isChecked()

    # -- T19 layer contracts -------------------------------------------------- #
    def layer_state_text(self, facet: str | None = None) -> str:
        """Return one context status, or both for legacy callers."""
        try:
            sat = str(self._sat_status.text() or "")
            surface = str(self._surface_status.text() or "")
        except (AttributeError, RuntimeError):
            return ""
        if facet in ("satellite", "goes_context"):
            return sat
        if facet in ("surface", "surface_observations"):
            return surface
        return "\n".join(part for part in (sat, surface) if part)

    def layer_time_text(self, facet: str | None = None) -> str:
        """Return actual time text for one context row (T19/T21).

        The active-layer list has separate GOES and surface rows.  Returning the
        GOES stamp for both whenever both were enabled made the surface row look
        time-matched to a scan it did not use, so callers may name the facet.
        The no-argument form remains the compact combined-controller fallback.
        """
        key = str(facet or "").strip().lower()
        wants_satellite = key in (
            "", "satellite", "goes_context", "goes_satellite")
        wants_surface = key in ("", "surface", "surface_observations")
        if wants_satellite and self._satellite is not None:
            try:
                selected = self._satellite.selected_time
                return selected.strftime("%d %b %HZ") if selected is not None else ""
            except Exception:  # noqa: BLE001 - advisory text only
                return ""
        if wants_surface and self._surface is not None:
            try:
                return (
                    f"requested {self._surface.requested_time:%d %b %HZ} · "
                    f"{len(self._surface.observations)} matched"
                )
            except (AttributeError, TypeError):
                return ""
        return ""

    def layer_error_text(self, facet: str | None = None) -> str:
        """Return the failure text while a failure is showing, else ``""``."""
        text = self.layer_state_text(facet)
        lowered = text.casefold()
        if "unavailable" in lowered or "failed" in lowered or "error" in lowered:
            return text
        return ""

    def retry(self) -> None:
        """Retry enabled context layers alone (T19.5)."""
        if self.is_satellite_enabled():
            self._queue_satellite()
        if self.is_surface_enabled():
            self._queue_surface()

    def retry_scoped(self) -> None:
        """Alias spelling the T19.5 scoped-retry contract by name."""
        self.retry()

    def retry_layer(self, key: str) -> None:
        """Retry only the satellite or surface row the user selected."""
        if key == "goes_context":
            self._queue_satellite()
        elif key == "surface_observations":
            self._queue_surface()

    def set_opacity_value(self, value: float) -> None:
        """Set satellite opacity through the slider band (T19.2).

        Re-wraps the attached frame at the new value, matching what a slider
        drag does, so session restores and presets depict the remembered
        opacity instead of leaving a stale frame at the old one.
        """
        from sharpmod.maps.map_layers import normalise_opacity

        self._opacity.setValue(int(round(normalise_opacity(value) * 100.0)))
        if self._satellite is not None and self.is_satellite_enabled():
            self._map.set_overlay(
                GOES_OVERLAY_KEY,
                self._satellite.raster.at_opacity(self.opacity()),
                visible=True,
            )

    def reset_opacity(self) -> None:
        """Return satellite opacity to the reset value (T19.2)."""
        from sharpmod.maps.map_layers import opacity_reset_value

        self._opacity.setValue(int(round(opacity_reset_value() * 100.0)))

    def set_valid_time(self, when) -> None:
        if when == self._valid_time:
            return
        self._valid_time = when
        if self.is_satellite_enabled():
            self._queue_satellite()
        if self.is_surface_enabled():
            self._queue_surface()

    def refresh(self) -> None:
        if self.is_satellite_enabled():
            self._queue_satellite()
        if self.is_surface_enabled():
            self._queue_surface()

    def on_location_changed(self, *_args) -> None:
        if self.is_surface_enabled():
            self._queue_surface()

    def on_view_settled(self) -> None:
        if self.is_satellite_enabled():
            self._queue_satellite()
        if self.is_surface_enabled():
            point = self._context_point()
            if self._surface is None or self._surface_distance_km(point) > 80.0:
                self._queue_surface()
            else:
                self._rerender_surface()

    def satellite_overlay(self):
        return self._satellite

    def surface_observations(self):
        return self._surface

    def context_rows(self):
        rows = []
        if self._satellite is not None:
            rows.append(
                {
                    "layer": "GOES satellite",
                    "source": self._satellite.satellite,
                    "choice": self._satellite.channel,
                    "requested_utc": self._satellite.requested_time,
                    "selected_utc": self._satellite.selected_time,
                    "coverage": f"{self._satellite.coverage_fraction:.0%}",
                    "resolution_km": self._satellite.native_resolution_km,
                }
            )
        if self._surface is not None:
            rows.append(
                {
                    "layer": "Surface observations",
                    "source": "NOAA/NWS API",
                    "requested_utc": self._surface.requested_time,
                    "matched": len(self._surface.observations),
                    "discovered": self._surface.discovered_station_count,
                    "stale_or_mismatched": self._surface.stale_count,
                }
            )
        return tuple(rows)

    def shutdown(self) -> None:
        self._sat_timer.stop()
        self._surface_timer.stop()
        self._sat_workers.drain(_SHUTDOWN_WAIT_MS)
        self._surface_workers.drain(_SHUTDOWN_WAIT_MS)

    def _context_point(self):
        try:
            lat, lon = self._map.context_point()
            return float(lat), float(lon)
        except (AttributeError, TypeError, ValueError):
            lon0, lon1, lat0, lat1 = self._map.view_bounds()
            return (lat0 + lat1) / 2.0, (lon0 + lon1) / 2.0

    def _surface_distance_km(self, point):
        lat, lon = point
        old_lat = self._surface.centre_latitude
        old_lon = self._surface.centre_longitude
        mean_lat = math.radians((lat + old_lat) / 2.0)
        return math.hypot(
            (lat - old_lat) * 111.2,
            (lon - old_lon) * 111.2 * max(0.1, math.cos(mean_lat)),
        )

    def _emit_layer_changed(self) -> None:
        try:
            self.layerChanged.emit()
        except (AttributeError, RuntimeError):
            pass

    def _on_satellite_toggled(self, checked):
        self._sat_details.setVisible(
            getattr(self, "_picker_open_key", None) == "goes_context"
            if getattr(self, "_picker_compact", False) else bool(checked)
        )
        if not checked:
            self._sat_timer.stop()
            self._sat_token += 1
            self._sat_workers.interrupt()
            self._map.remove_overlay(GOES_OVERLAY_KEY)
            self._sat_status.clear()
            self.contextChanged.emit()
            self._emit_layer_changed()
            return
        self._queue_satellite()

    def _on_surface_toggled(self, checked):
        self._surface_details.setVisible(
            getattr(self, "_picker_open_key", None) == "surface_observations"
            if getattr(self, "_picker_compact", False) else bool(checked)
        )
        if not checked:
            self._surface_timer.stop()
            self._surface_token += 1
            self._surface_workers.interrupt()
            self._map.remove_overlay(SURFACE_OVERLAY_KEY)
            self._surface_status.clear()
            self.contextChanged.emit()
            self._emit_layer_changed()
            return
        self._queue_surface()

    def _queue_satellite(self, *_args):
        if self.is_satellite_enabled():
            # Orphan the old request at selection time, not only when the
            # debounce expires.  A response landing inside that interval must
            # not briefly overwrite the newly selected time/channel.
            self._sat_token += 1
            self._sat_workers.interrupt()
            self._sat_timer.start()

    def _queue_surface(self, *_args):
        if self.is_surface_enabled():
            self._surface_token += 1
            self._surface_workers.interrupt()
            self._surface_timer.start()

    def _start_satellite(self):
        if not self.is_satellite_enabled():
            return
        if self._valid_time is None:
            self._sat_status.setText("Choose an analysis/valid time first")
            return
        bounds = tuple(float(value) for value in self._map.view_bounds())
        if bounds[0] < -180 or bounds[1] > 180:
            self._map.remove_overlay(GOES_OVERLAY_KEY)
            self._sat_status.setText(
                "GOES context is unavailable for a map view crossing the dateline"
            )
            return
        self._sat_workers.interrupt()
        self._sat_token += 1
        token = self._sat_token
        worker = _SatelliteWorker(
            token, self._valid_time, bounds, self.channel(), parent=self
        )
        worker.loaded.connect(self._satellite_loaded)
        worker.failed.connect(self._satellite_failed)
        self._sat_workers.track(worker)
        worker.finished.connect(worker.deleteLater)
        self._sat_status.setText("Loading and geolocating the nearest GOES frame…")
        worker.start()

    def _satellite_loaded(self, token, result):
        # T21.4: a late response never overwrites a newer selection. The
        # result carries the requested time it was fetched for.
        if getattr(result, "requested_time", None) != self._valid_time:
            return
        if token != self._sat_token or not self.is_satellite_enabled():
            return
        self._satellite = result
        self._map.set_overlay(
            GOES_OVERLAY_KEY,
            result.raster.at_opacity(self.opacity()),
            visible=True,
        )
        offset = result.time_offset_seconds / 60.0
        self._sat_status.setText(
            f"{result.satellite} {CHANNELS[result.channel][1]} · actual "
            f"{result.selected_time:%Y-%m-%d %H:%M:%SZ} · {offset:+.0f} min · "
            f"{result.coverage_fraction:.0%} view coverage"
        )
        self.contextChanged.emit()
        self._emit_layer_changed()

    def _satellite_failed(self, token, message):
        if token != self._sat_token:
            return
        # Keep the previous successful scan on screen with its actual timestamp
        # and T21 offset verdict.  The status line makes the failed refresh
        # explicit; removing the scan would lose correctly labelled context.
        self._sat_status.setText(f"Satellite unavailable: {message}")
        self.contextChanged.emit()
        self._emit_layer_changed()

    def _on_opacity(self, _value):
        self._sync_opacity_spin(int(self._opacity.value()))
        if self._satellite is not None and self.is_satellite_enabled():
            self._map.set_overlay(
                GOES_OVERLAY_KEY,
                self._satellite.raster.at_opacity(self.opacity()),
                visible=True,
            )

    def _sync_opacity_spin(self, value: int) -> None:
        """Mirror the slider into the numeric readout (T19.2)."""
        spin = getattr(self, "_opacity_spin", None)
        if spin is not None and spin.value() != int(value):
            spin.blockSignals(True)
            try:
                spin.setValue(int(value))
            finally:
                spin.blockSignals(False)

    def _on_opacity_spin(self, value: int) -> None:
        """Drive the slider from the numeric readout (T19.2)."""
        if self._opacity.value() != int(value):
            self._opacity.setValue(int(value))

    def _start_surface(self):
        if not self.is_surface_enabled():
            return
        if self._valid_time is None:
            self._surface_status.setText("Choose an analysis/valid time first")
            return
        lat, lon = self._context_point()
        self._surface_workers.interrupt()
        self._surface_token += 1
        token = self._surface_token
        worker = _SurfaceWorker(token, lat, lon, self._valid_time, parent=self)
        worker.loaded.connect(self._surface_loaded)
        worker.failed.connect(self._surface_failed)
        self._surface_workers.track(worker)
        worker.finished.connect(worker.deleteLater)
        self._surface_status.setText("Matching nearby station observations…")
        worker.start()

    def _surface_loaded(self, token, dataset):
        # T21.4: a late response never overwrites a newer selection.
        if getattr(dataset, "requested_time", None) != self._valid_time:
            return
        if token != self._surface_token or not self.is_surface_enabled():
            return
        self._surface = dataset
        self._rerender_surface()
        self.contextChanged.emit()

    def _rerender_surface(self, *_args):
        if self._surface is None or not self.is_surface_enabled():
            return
        spacing, limit = self._DENSITIES[self.density()]
        bounds = self._map.view_bounds()
        layer = surface_overlay_layer(
            self._surface,
            bounds,
            pixel_size=(max(1, self._map.width()), max(1, self._map.height())),
            minimum_spacing_px=spacing,
            max_stations=limit,
        )
        self._map.set_overlay(SURFACE_OVERLAY_KEY, layer, visible=True)
        displayed = len(layer.shapes)
        matched_displayed = sum(
            str(getattr(shape, "availability_state", "") or "") == "available"
            for shape in layer.shapes
        )
        status_displayed = displayed - matched_displayed
        displayed_text = f"{matched_displayed} displayed"
        if status_displayed:
            displayed_text = (
                f"{matched_displayed} observations + {status_displayed} "
                "status markers displayed"
            )
        text = (
            f"{displayed_text} / {len(self._surface.observations)} matched / "
            f"{self._surface.queried_station_count} queried / "
            f"{self._surface.discovered_station_count} nearby · matched to "
            "selected time · Source: NOAA/NWS API"
        )
        if self._surface.stale_count:
            text += f" · {self._surface.stale_count} stale/mismatched"
        if self._surface.unmatched_station_ids:
            failed = set(self._surface.failed_station_ids)
            no_match = len(set(self._surface.unmatched_station_ids) - failed)
            if no_match:
                text += f" · {no_match} checked with no observation"
        if self._surface.failed_station_ids:
            text += f" · {len(self._surface.failed_station_ids)} query failed"
        if self._surface.unqueried_station_count:
            text += f" · {self._surface.unqueried_station_count} not queried"
        self._surface_status.setText(text)
        self._emit_layer_changed()

    def _surface_failed(self, token, message):
        if token != self._surface_token:
            return
        # As with GOES, retain the previous time-window layer while the scoped
        # retry is failing.  Its validity range keeps it from masquerading as
        # an exact match to the new request.
        self._surface_status.setText(f"Surface observations unavailable: {message}")
        self.contextChanged.emit()
        self._emit_layer_changed()


__all__ = ["EnvironmentalContextController"]

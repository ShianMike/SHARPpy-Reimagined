"""StormReportsOverlayController overlay controls."""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timedelta
from time import monotonic

from qtpy.QtCore import QObject, Qt, QTimer, Signal
from qtpy.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sharpmod.providers import radar_mosaic, radar_site, spc_outlook
from sharpmod.ui.features.gui_workers import (
    _HrrrFieldWorker,
    _RadarMosaicWorker,
    _RadarSiteWorker,
    _SpcOutlookWorker,
    _StormReportsWorker,
)
from sharpmod.ui.picker.layout import dependent_panel
from sharpmod.ui.features.gui_threading import retain_worker_until_finished
from sharpmod.maps.map_overlays import format_age
from sharpmod.ui.styles.theme import OBJ_HINT, OBJ_PLAIN, OBJ_SECTION_LABEL, SPACE
from sharpmod.ui.maps.overlays.controllers import (
    _REFRESH_DEBOUNCE_MS,
    _SHUTDOWN_WAIT_MS,
    _WorkerFleet
)
from sharpmod.ui.maps.overlays import controllers as _controls


class StormReportsOverlayController(QObject):
    """Draw local storm reports on one picker map, beneath the outlook.

    Reports are read *against* the outlook that anticipated them -- the question
    is whether what was forecast is what happened -- so the switch stays disabled
    until the outlook on the same map is showing, and it turns itself off if the
    outlook is switched off underneath it. That mirrors the rule
    :mod:`sharpmod.maps.locator_overlay` applies to the sounding's inset, so the map
    and the inset cannot disagree about what may be shown alone.

    Otherwise shaped like :class:`OutlookOverlayController`: a debounced request
    so scrubbing a date does not become one fetch per intermediate value, a token
    to discard a result the user has moved past, and a bounded drain on close.
    """

    #: Emitted whenever the layer's list row may have changed (T19.1).
    layerChanged = Signal()

    def __init__(
        self, map_widget, *, parent=None, label="Storm reports", enabled=False
    ):
        super().__init__(parent)
        from sharpmod.providers import storm_reports

        self._map = map_widget
        self._reports = storm_reports
        self._valid_time = None
        self._token = 0
        self._workers = _WorkerFleet()
        self._outlook = None
        self._raw_layer = None

        # A plain widget for the same reason as the outlook controller.
        self._content = QWidget()
        self._content.setObjectName(OBJ_PLAIN)
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACE["xs"])

        self._check = QCheckBox(label)
        self._check.setChecked(bool(enabled))
        self._check.toggled.connect(self._on_toggled)
        layout.addWidget(self._check)

        # The reason this switch is unavailable, stated where it is needed rather
        # than only in a tooltip. This is the one disabled control in the card, and
        # a greyed row with no visible explanation reads as broken -- a tooltip
        # cannot be discovered by someone who has not already decided to hover the
        # thing that looks broken. The tooltip stays as well, for the pointer.
        self._reason = QLabel("")
        self._reason.setWordWrap(True)
        self._reason.setObjectName(OBJ_HINT)
        self._reason.setVisible(False)
        self._reason_panel, reason_layout = dependent_panel(self._content)
        reason_layout.addWidget(self._reason)
        layout.addWidget(self._reason_panel)

        self._detail, detail = dependent_panel(self._content)
        layout.addWidget(self._detail)

        filter_row = QHBoxLayout()
        filter_label = QLabel("Hazard")
        filter_label.setObjectName(OBJ_HINT)
        filter_row.addWidget(filter_label)
        self._hazard_filter = QComboBox()
        self._hazard_filter.setObjectName("stormReportsHazardFilter")
        self._hazard_filter.setAccessibleName("Storm report hazard filter")
        for key, title in self._reports.REPORT_FILTER_LABELS.items():
            self._hazard_filter.addItem(title, key)
        self._hazard_filter.setToolTip(
            "Filter the fetched Local Storm Reports by hazard. This is applied "
            "locally because the provider's type query can return false-empty data."
        )
        self._hazard_filter.currentIndexChanged.connect(
            self._on_hazard_filter_changed
        )
        filter_row.addWidget(self._hazard_filter, 1)
        detail.addLayout(filter_row)

        window_row = QHBoxLayout()
        window_label = QLabel("Event window")
        window_label.setObjectName(OBJ_HINT)
        window_row.addWidget(window_label)
        self._window = QComboBox()
        self._window.setObjectName("stormReportsTimeFilter")
        self._window.setAccessibleName("Storm report event time window")
        for title, span in self._reports.REPORT_WINDOWS:
            self._window.addItem(title, int(span.total_seconds()))
        default_seconds = int(self._reports.DEFAULT_WINDOW.total_seconds())
        default_index = self._window.findData(default_seconds)
        self._window.setCurrentIndex(default_index if default_index >= 0 else 0)
        self._window.setToolTip(
            "Show report event times within this symmetric window around the "
            "selected map time."
        )
        self._window.currentIndexChanged.connect(self._on_window_changed)
        window_row.addWidget(self._window, 1)
        detail.addLayout(window_row)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setObjectName(OBJ_HINT)
        # Empty text means hidden -- the rule ``_set_status`` applies on every
        # later update. It was not applied to the initial state, so a blank label
        # held a 15px row open under a switch that was showing nothing.
        self._status.setVisible(False)
        detail.addWidget(self._status)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_REFRESH_DEBOUNCE_MS)
        self._timer.timeout.connect(self._start_fetch)

        self._sync_available()
        self._detail.setVisible(self._check.isChecked())

    # -- public API ---------------------------------------------------------- #
    def controls_widget(self) -> QWidget:
        return self._content

    def is_enabled(self) -> bool:
        return self._check.isChecked()

    def set_enabled(self, enabled: bool) -> None:
        self._check.setChecked(bool(enabled))

    def hazard_filter(self) -> str:
        """Return the local hazard-filter key (T22.2)."""

        return self._reports.normalise_report_filter(
            self._hazard_filter.currentData()
        )

    def set_hazard_filter(self, value: str | None) -> None:
        key = self._reports.normalise_report_filter(value)
        index = self._hazard_filter.findData(key)
        self._hazard_filter.setCurrentIndex(index if index >= 0 else 0)

    def report_window(self) -> timedelta:
        """Return the total event-time span selected by the user (T22.2)."""

        try:
            seconds = int(self._window.currentData())
        except (TypeError, ValueError, OverflowError):
            return self._reports.DEFAULT_WINDOW
        return timedelta(seconds=max(15 * 60, seconds))

    def set_report_window(self, value) -> None:
        try:
            seconds = (
                int(value.total_seconds())
                if hasattr(value, "total_seconds")
                else int(value)
            )
        except (TypeError, ValueError, OverflowError):
            seconds = int(self._reports.DEFAULT_WINDOW.total_seconds())
        index = self._window.findData(seconds)
        if index >= 0:
            self._window.setCurrentIndex(index)

    # -- T19 layer contracts -------------------------------------------------- #
    def layer_state_text(self) -> str:
        """Return the status-line text behind the active-layer row (T19.1)."""
        try:
            status = str(self._status.text() or "")
            reason = str(self._reason.text() or "")
        except (AttributeError, RuntimeError):
            return ""
        return status or reason

    def layer_time_text(self) -> str:
        """Return this layer's coverage text for the list (T19.1)."""
        try:
            layer = self._map.overlay(self._reports.OVERLAY_KEY)
        except (AttributeError, RuntimeError):
            return ""
        return str(getattr(layer, "subtitle", "") or "")

    def layer_error_text(self) -> str:
        """Return the failure text while a failure is showing, else ``""``."""
        text = self.layer_state_text()
        lowered = text.casefold()
        if "unavailable" in lowered or "failed" in lowered or "error" in lowered:
            return text
        return ""

    def retry(self) -> None:
        """Retry this layer alone (T19.5): scoped, others untouched."""
        self._token += 1
        if not self._check.isChecked():
            return
        self._timer.start()

    def retry_scoped(self) -> None:
        """Alias spelling the T19.5 scoped-retry contract by name."""
        self.retry()

    def bind_outlook(self, outlook) -> None:
        """Follow ``outlook``'s switch, since reports are read against it.

        Bound rather than constructed together so the owning tab keeps deciding
        the order its cards appear in.
        """
        self._outlook = outlook
        try:
            outlook._check.toggled.connect(lambda _on: self._sync_available())
        except AttributeError:  # pragma: no cover - a stand-in outlook
            pass
        self._sync_available()

    def set_valid_time(self, when) -> None:
        """Point the overlay at ``when``, refetching once the value settles."""
        if when == self._valid_time:
            return
        self._valid_time = when
        if self._check.isChecked():
            self._timer.start()

    def refresh(self) -> None:
        if self._check.isChecked():
            self._timer.start()
        try:
            self.layerChanged.emit()
        except (AttributeError, RuntimeError):
            pass

    def on_view_settled(self) -> None:
        """Re-ask once panning stops, so the box matches what is on screen."""
        if self._check.isChecked():
            self._timer.start()

    def attached_layer(self):
        """Return the reports drawn on the map, or ``None`` while switched off."""
        if not self._check.isChecked():
            return None
        try:
            return self._map.overlay(self._reports.OVERLAY_KEY)
        except AttributeError:
            return None

    def shutdown(self) -> None:
        self._timer.stop()
        self._workers.drain(_SHUTDOWN_WAIT_MS)

    # -- internals ----------------------------------------------------------- #
    def _outlook_showing(self) -> bool:
        if self._outlook is None:
            return True  # unbound: nothing to depend on, so nothing to block
        try:
            return bool(self._outlook.is_enabled())
        except Exception:  # noqa: BLE001 - a stand-in outlook is not fatal
            return True

    def _sync_available(self) -> None:
        """Enable the switch only while the outlook it is read against is on."""
        available = self._outlook_showing()
        self._check.setEnabled(available)
        self._check.setToolTip(
            "Triangle: tornado · Diamond: hail · Circle: wind gust · "
            "Square: wind damage · White: significant report. "
            "Zoom in for values; click a marker for the full report."
            if available
            else "Switch on the SPC convective outlook first; storm reports are "
            "read against the outlook that anticipated them."
        )
        # Shorter than the tooltip on purpose: this sits permanently under the
        # greyed row, so it has to say what unblocks it and stop.
        self._reason.setText("" if available else "Needs the outlook switched on")
        self._reason.setVisible(not available)
        self._reason_panel.setVisible(not available)
        if not available and self._check.isChecked():
            # Turning itself off rather than drawing on regardless: reports with
            # no risk areas behind them cannot answer the question they exist
            # for, and leaving the box ticked would misreport what is on screen.
            self._check.setChecked(False)
        self._status.setVisible(self._check.isChecked())

    def _emit_layer_changed(self) -> None:
        try:
            self.layerChanged.emit()
        except (AttributeError, RuntimeError):
            pass

    def _on_toggled(self, checked: bool) -> None:
        self._detail.setVisible(
            getattr(self, "_picker_open_key", None) == "storm_reports"
            if getattr(self, "_picker_compact", False) else bool(checked)
        )
        self._status.setVisible(bool(checked))
        if not checked:
            self._timer.stop()
            self._token += 1
            self._workers.interrupt()
            self._map.remove_overlay(self._reports.OVERLAY_KEY)
            self._raw_layer = None
            self._status.setText("")
            self._emit_layer_changed()
            return
        self._timer.start()

    def _view_bounds(self):
        try:
            return self._map.view_bounds()
        except AttributeError:
            return None

    def _start_fetch(self) -> None:
        if not self._check.isChecked():
            return
        self._token += 1
        token = self._token
        view = self._view_bounds()
        span = None
        if view is not None:
            span = abs(float(view[1]) - float(view[0]))
        worker = _controls._StormReportsWorker(
            self._valid_time,
            token,
            parent=self,
            view=view,
            window=self.report_window(),
            span_deg=span,
        )
        worker.loaded.connect(self._on_loaded)
        worker.failed.connect(self._on_failed)
        self._workers.track(worker)
        worker.finished.connect(worker.deleteLater)
        self._status.setText("Loading storm reports\u2026")
        worker.start()

    def _on_loaded(self, token, _valid_time, layer) -> None:
        # T21.4: a late response never overwrites a newer selection.
        if _valid_time != self._valid_time:
            return
        if token != self._token or not self._check.isChecked():
            return
        self._raw_layer = layer
        if not layer:
            self._map.remove_overlay(self._reports.OVERLAY_KEY)
            self._status.setText(
                "No storm reports in "
                + self._reports.report_window_label(self.report_window())
                + " of the selected time"
            )
            self._emit_layer_changed()
            return
        self._apply_report_filter()

    def _on_failed(self, token, _valid_time, message) -> None:
        # T21.4: a late failure for an older time must not clear the frame a
        # newer selection has since attached.
        if _valid_time != self._valid_time:
            return
        if token != self._token:
            return
        # Preserve the last successful report window, correctly labelled by
        # its own validity range, while exposing the failed refresh.  A valid
        # empty response still removes the old markers in ``_on_loaded``.
        self._status.setText(f"Storm reports unavailable: {message}")
        self._emit_layer_changed()

    def _on_hazard_filter_changed(self, *_args) -> None:
        """Re-filter the retained response without another network request."""

        if self._check.isChecked() and self._raw_layer is not None:
            self._apply_report_filter()

    def _on_window_changed(self, *_args) -> None:
        """A time-window change changes the provider query and refetches."""

        if self._check.isChecked():
            self._token += 1
            self._workers.interrupt()
            self._timer.start()

    def _apply_report_filter(self) -> None:
        raw = self._raw_layer
        filtered = self._reports.filter_report_layer(raw, self.hazard_filter())
        raw_count = len(getattr(raw, "shapes", ())) if raw is not None else 0
        label = self._reports.REPORT_FILTER_LABELS[self.hazard_filter()]
        window = self._reports.report_window_label(self.report_window())
        if filtered is None:
            self._map.remove_overlay(self._reports.OVERLAY_KEY)
            self._status.setText(
                f"No {label.lower()} reports in {window} of the selected time "
                f"({raw_count} total before filter)"
            )
            self._emit_layer_changed()
            return
        self._map.set_overlay(self._reports.OVERLAY_KEY, filtered, visible=True)
        count = len(filtered.shapes)
        count_text = f"{count} storm report{'s' if count != 1 else ''}"
        if count != raw_count:
            count_text += f" of {raw_count}"
        self._status.setText(
            f"{count_text} · {label} · {window} · click a marker for details"
        )
        self._emit_layer_changed()

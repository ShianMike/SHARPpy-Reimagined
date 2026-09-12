"""Cooperative background loading for sounding-locator overlay selections."""

from __future__ import annotations

import logging

from qtpy.QtCore import QThread, Signal

from sharpmod import locator_overlay


_LOGGER = logging.getLogger(__name__)


class LocatorOverlayWorker(QThread):
    """Fetch a resolved locator selection without blocking Qt's GUI thread."""

    loaded = Signal(str, object)

    def __init__(
        self,
        selections,
        *,
        lat,
        lon,
        valid_time=None,
        run=None,
        fxx=None,
        span_deg=None,
        half_span_deg=None,
        parent=None,
    ):
        super().__init__(parent)
        self.selections = tuple(selections)
        self.lat = float(lat)
        self.lon = float(lon)
        self.valid_time = valid_time
        self.run_time = run
        self.fxx = fxx
        self.span_deg = span_deg
        self.half_span_deg = half_span_deg

    def run(self) -> None:
        resolved = locator_overlay.resolve(
            self.selections,
            lat=self.lat,
            lon=self.lon,
            valid_time=self.valid_time,
            half_span_deg=self.half_span_deg,
        )
        for selection in resolved:
            if self.isInterruptionRequested():
                return
            try:
                layer = locator_overlay.fetch(
                    selection,
                    lat=self.lat,
                    lon=self.lon,
                    valid_time=self.valid_time,
                    run=self.run_time,
                    fxx=self.fxx,
                    span_deg=self.span_deg,
                    should_cancel=self.isInterruptionRequested,
                )
            except Exception as exc:  # noqa: BLE001 - optional visual context
                _LOGGER.debug(
                    "locator_overlay.fetch_failed family=%s error=%s",
                    selection.family,
                    exc,
                )
                layer = None
            if self.isInterruptionRequested():
                return
            self.loaded.emit(selection.key, layer)


__all__ = ["LocatorOverlayWorker"]

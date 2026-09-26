"""Picker-side control binding one map to the SPC convective outlook overlay.

The picker owns four maps across four source tabs, each with its own valid-time
widgets, and this application has no shared time model to subscribe to. Rather
than repeat the toggle, debounce, worker, and staleness handling in every tab,
one controller owns all of it and a tab supplies just two things: the map to
draw on, and a call to :meth:`OutlookOverlayController.set_valid_time` from
whatever method it already uses to refresh its valid-time label.

The overlay defaults to off. It is an embellishment on a location picker, so it
should not issue network requests to SPC before the user asks for it.
"""

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

#: Matches the station-catalogue probe, so dragging a date spinner settles once
#: instead of firing a request per intermediate value.
_REFRESH_DEBOUNCE_MS = 300

#: How often to ask whether a newer outlook has been issued for the selection
#: already on screen. SPC updates Day 1 five times a day, so this is far more
#: responsive than it needs to be; it is affordable because the check itself is
#: pure arithmetic and only reaches the network when the answer has changed.
_SUPERSEDE_CHECK_MS = 5 * 60 * 1000

#: Bounded grace period for an interrupted fetch to unwind on window close.
_SHUTDOWN_WAIT_MS = 2000


class _WorkerFleet:
    """Keeps every started worker reachable until it has actually finished.

    ``QThread.requestInterruption`` is advisory: it is observed at checkpoints,
    so a worker blocked in a socket read keeps running after a newer request
    replaces it. A controller therefore owns several live threads at once
    whenever the user outruns the network.

    Holding only the newest reference left the older ones unreferenced while
    they were still running and still parented to the controller, so closing
    the picker could destroy a running ``QThread`` -- which aborts the process
    rather than raising. Draining the whole fleet on close is what rules that
    out; tracking exists to make the drain possible.
    """

    def __init__(self) -> None:
        self._live: list = []

    def track(self, worker) -> None:
        """Adopt ``worker``, releasing it again once it reports finished.

        Connect this before ``deleteLater`` so the bookkeeping runs while the
        object is still valid.
        """
        self._live.append(worker)
        worker.finished.connect(lambda: self.retire(worker))

    def retire(self, worker) -> None:
        """Forget ``worker``, comparing by identity.

        Equality would touch a C++ object ``deleteLater`` may already have
        taken away.
        """
        self._live = [live for live in self._live if live is not worker]

    def live_count(self) -> int:
        """Number of started workers still owned by this controller."""
        return len(self._live)

    def interrupt(self) -> None:
        """Ask every live worker to stop at its next checkpoint."""
        for worker in tuple(self._live):
            try:
                if worker.isRunning():
                    worker.requestInterruption()
            except RuntimeError:  # already gone; nothing left to interrupt
                self.retire(worker)

    def drain(self, budget_ms: int) -> None:
        """Interrupt everything, then wait out one shared deadline.

        The budget is shared rather than per worker, so closing the window
        cannot take N times longer because N requests happened to be in flight.
        """
        self.interrupt()
        deadline = monotonic() + budget_ms / 1000.0
        for worker in tuple(self._live):
            remaining_ms = int((deadline - monotonic()) * 1000.0)
            if remaining_ms <= 0:
                break
            try:
                if worker.isRunning():
                    worker.wait(remaining_ms)
            except RuntimeError:
                self.retire(worker)

        # Anything still blocked has outlived this controller's grace period.
        # Detach it before the controller is destroyed; the process-level
        # retainer releases it when the operation returns naturally.
        for worker in tuple(self._live):
            try:
                running = worker.isRunning()
            except RuntimeError:
                self.retire(worker)
                continue
            if running and retain_worker_until_finished(worker):
                self.retire(worker)


def _resolved_day(valid_time: datetime | None, product: str) -> int | None:
    """Return the outlook day ``product`` would resolve to for ``valid_time``.

    Asked of the resolver rather than derived from the date, because
    availability depends on which issuances exist yet, not on the date alone: a
    hazard with no Day 3 product becomes reachable the moment that convective
    day's Day 2 outlook is published, which happens partway through the span the
    arithmetic still calls Day 3. Computing the number independently let an
    entry name a day the fetch would not have used.

    Pure arithmetic over candidate URLs, so this touches no network.
    """
    if valid_time is None:
        return None
    try:
        candidates = spc_outlook.candidates_for(valid_time, product=product)
    except Exception:  # noqa: BLE001 - a bad time must not break the label
        return None
    return candidates[0].day if candidates else None


def _product_item_text(spec, day: int | None) -> str:
    """Return the combo entry for ``spec`` at resolved outlook ``day``.

    The day range is a property of the product, not of the selection, so
    showing it unconditionally made every entry read as a statement about the
    day on screen: a Day 2 or Day 3 selection still said "(Day 1-2)". The entry
    now names the day the product actually resolves to, and states which days
    publish it only when the selection reaches none of them.
    """
    if day is not None:
        return f"{spec.label}  (Day {day})"
    if len(spec.days) >= len(spc_outlook.SUPPORTED_DAYS):
        return spec.label
    return f"{spec.label}  ({spc_outlook.format_product_days(spec)} only)"




#: How often a live radar frame is re-fetched while the overlay is on.
#:
#: Unlike :data:`_SUPERSEDE_CHECK_MS`, whose cost argument is that it usually
#: short-circuits on pure arithmetic, this timer genuinely reaches the network
#: most times it fires -- new imagery is the whole point. It is therefore set
#: from the product's own publication cadence rather than being made
#: "responsive", because polling faster than the source publishes buys nothing
#: and every extra request lands on a shared public service.
_RADAR_REFRESH_FLOOR_MS = 60 * 1000

#: Coalesces a dragged opacity slider or a quick run through the product list.
_RADAR_DEBOUNCE_MS = 250

#: Which radar the controller draws.
#:
#: ``site`` is one antenna's own imagery over its 10-degree extent, chosen from
#: the map centre and followed as the user pans -- roughly half a kilometre per
#: pixel, so storm structure survives. ``mosaic`` is the national composite over
#: 70 degrees at about 1.9 km a pixel: the right answer for "where is the
#: convection today", too coarse for "what is this storm doing".
#:
#: They are separate overlay keys, so they are separate slots on the map. The
#: control offers one or the other because two reflectivity ramps over the same
#: storm is not twice the information.
SCOPE_SITE = "site"
SCOPE_MOSAIC = "mosaic"

#: Sentinel for "let the map decide which antenna", which is the default and the
#: behaviour the single-site scope shipped with. Anything else is a WSR-88D
#: identifier the user has named, and a named radar is honoured wherever the map
#: happens to be looking -- asking for KTLX means KTLX, not "whatever is nearest".
SITE_AUTO = ""




#: Coalesces a run through the product list or a dragged opacity slider. Longer
#: than the radar debounce because a field costs a GRIB subset, a reprojection
#: and a PNG encode rather than one WMS request, so a discarded one is dearer.
_FIELD_DEBOUNCE_MS = 350

#: HRRR publishes hourly. Re-checking a little more often than that picks up a
#: late run without polling a public bucket for no reason.
_FIELD_REFRESH_MS = 10 * 60 * 1000

# A socket read is not interruptible until its timeout. Keep the newest field
# request queued rather than starting an arbitrary number of older QThreads.
_FIELD_MAX_LIVE_WORKERS = 2




def _product_tooltip(spec) -> str:
    """Build the hover text for one product entry."""
    parts = [spec.description or spec.label]
    if spec.palette.units:
        parts.append("Units: %s" % spec.palette.units)
    if spec.caveat:
        parts.append(spec.caveat)
    return "\n\n".join(parts)


def _same_forecast_hour(first: datetime | None, second: datetime | None) -> bool:
    """Whether two times resolve to the same HRRR forecast hour."""
    if first is None or second is None:
        return first is None and second is None
    return first.replace(minute=0, second=0, microsecond=0) == second.replace(
        minute=0, second=0, microsecond=0
    )


# --------------------------------------------------------------------------- #
# Choosing what the sounding's locator inset carries
# --------------------------------------------------------------------------- #

#: Where the locator selection is remembered, beside the other overlay choices.
LOCATOR_SETTINGS_KEY = "overlays/locator_selection"






from sharpmod.ui.maps.overlays.outlook_controller import OutlookOverlayController  # noqa: E402
from sharpmod.ui.maps.overlays.radar_controller import RadarOverlayController  # noqa: E402
from sharpmod.ui.maps.overlays.hrrr_controller import HrrrFieldController  # noqa: E402
from sharpmod.ui.maps.overlays.locator_selector import LocatorOverlaySelector  # noqa: E402
from sharpmod.ui.maps.overlays.reports_controller import StormReportsOverlayController  # noqa: E402

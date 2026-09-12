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
    QVBoxLayout,
    QWidget,
)

from sharpmod import radar_mosaic, radar_site, spc_outlook
from sharpmod.gui_workers import (
    _HrrrFieldWorker,
    _RadarMosaicWorker,
    _RadarSiteWorker,
    _SpcOutlookWorker,
    _StormReportsWorker,
)
from sharpmod.gui_threading import retain_worker_until_finished
from sharpmod.map_overlays import format_age
from sharpmod.theme import OBJ_HINT, OBJ_PLAIN

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


class OutlookOverlayController(QObject):
    """Keep one map's SPC outlook overlay in step with a selected valid time."""

    #: Emitted with a short human-readable state for the owning tab to show.
    statusChanged = Signal(str)

    def __init__(
        self,
        map_widget,
        *,
        parent=None,
        label: str = "SPC convective outlook",
        enabled: bool = False,
    ) -> None:
        super().__init__(parent)
        self._map = map_widget
        self._valid_time: datetime | None = None
        self._token = 0
        self._workers = _WorkerFleet()
        #: The candidate set the attached result was resolved from. Compared
        #: against the current one to notice that SPC has since issued a newer
        #: or lower-numbered outlook for the same target day.
        self._signature: tuple[str, ...] | None = None
        self._pending_signature: tuple[str, ...] | None = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_REFRESH_DEBOUNCE_MS)
        self._timer.timeout.connect(self._start_fetch)

        self._supersede_timer = QTimer(self)
        self._supersede_timer.setInterval(_SUPERSEDE_CHECK_MS)
        self._supersede_timer.timeout.connect(self._check_superseded)

        # A plain widget, not a card: the owning rail groups this with the other
        # overlay controllers under one heading, because a card per switch read
        # as two mostly empty panels.
        self._content = QWidget()
        self._content.setObjectName(OBJ_PLAIN)
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(0, 0, 0, 0)
        self._check = QCheckBox(label)
        self._check.setToolTip(
            "Draw the SPC convective outlook covering the selected valid time "
            "(2020 onward)"
        )
        self._check.setChecked(bool(enabled))
        self._check.toggled.connect(self._on_toggled)
        layout.addWidget(self._check)

        # One product at a time rather than several checkboxes: the
        # probabilistic areas nest the same way the categorical ones do, so
        # drawing two hazards together produces overlapping translucent bands
        # that cannot be read. SPC's own graphics show one hazard per view.
        self._product = QComboBox()
        for spec in spc_outlook.PRODUCTS.values():
            self._product.addItem(_product_item_text(spec, None), spec.key)
        self._product.setCurrentIndex(
            max(0, self._product.findData(spc_outlook.DEFAULT_PRODUCT))
        )
        self._product.setToolTip(
            "Hazard probabilities are only issued for Days 1 and 2; the "
            "categorical outlook covers Days 1 to 3"
        )
        self._product.currentIndexChanged.connect(self._on_product_changed)
        layout.addWidget(self._product)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setObjectName(OBJ_HINT)
        layout.addWidget(self._status)

        # Which product to draw only means something once something is being
        # drawn, so the card collapses to its switch while the overlay is off.
        # It also keeps this card from spending rail height on a control that
        # cannot affect anything, which is what pushed the forecast panel past
        # a maximized window.
        self._product.setVisible(self._check.isChecked())

        # Deliberately no initial fetch: there is no valid time yet, and
        # scheduling one here left a timer pending that later fired against
        # whatever time happened to arrive first, skipping the check that avoids
        # refetching a window already on screen. The owning tab's first
        # ``set_valid_time`` starts the work.
        if self._check.isChecked():
            self._supersede_timer.start()

    # -- public API ---------------------------------------------------------- #
    def controls_widget(self) -> QWidget:
        """Return the widget the owning tab should add to its overlay card."""
        return self._content

    def is_enabled(self) -> bool:
        return self._check.isChecked()

    def set_enabled(self, enabled: bool) -> None:
        self._check.setChecked(bool(enabled))

    def product(self) -> str:
        return str(self._product.currentData() or spc_outlook.DEFAULT_PRODUCT)

    def set_product(self, product: str) -> None:
        index = self._product.findData(product)
        if index >= 0:
            self._product.setCurrentIndex(index)

    def set_valid_time(self, when: datetime | None) -> None:
        """Point the overlay at ``when``, refetching if the outlook changes.

        The map is told the new time immediately, even while a fetch is in
        flight, so its legend can flag an overlay that no longer covers the
        selection instead of leaving a stale one looking authoritative.
        """
        if when == self._valid_time:
            return
        self._valid_time = when
        self._map.set_valid_time(when)
        self._sync_product_labels()
        if not self._check.isChecked():
            return
        layer = self._map.overlay(spc_outlook.OVERLAY_KEY)
        if (
            layer is not None
            and layer.covers(when)
            and self._current_signature() == self._signature
        ):
            # The outlook on screen covers the new time and is still the one
            # that would be resolved for it, so stepping between forecast hours
            # inside a single issuance costs nothing. Coverage alone is not
            # enough: every issuance of a convective day shares one expiry, so a
            # 1630Z outlook still covers 00Z even though the 2000Z update has
            # superseded it for that hour.
            self._set_status(self._describe(layer))
            return
        self._request()

    def refresh(self) -> None:
        """Force a refetch for the current valid time."""
        if self._check.isChecked():
            self._signature = None
            self._request()

    def _sync_product_labels(self) -> None:
        """Restate each product entry against the currently resolved day.

        Only the visible text changes, so no selection signal fires and no
        fetch is triggered.
        """
        for index in range(self._product.count()):
            spec = spc_outlook.resolve_product(self._product.itemData(index))
            self._product.setItemText(
                index,
                _product_item_text(spec, _resolved_day(self._valid_time, spec.key)),
            )

    def _current_signature(self) -> tuple[str, ...] | None:
        """Return what could answer the current selection, or ``None``."""
        if self._valid_time is None:
            return None
        return spc_outlook.resolution_signature(
            self._valid_time, product=self.product()
        )

    def _check_superseded(self) -> None:
        """Refetch when SPC has issued something better since we last resolved.

        Runs on a timer because the trigger is the passage of time rather than
        anything the user does: a target day moves from Day 3 to Day 2 to Day 1
        while the window sits open, and each step brings a more skilful outlook
        and, past Day 3, the hazard probabilities as well.
        """
        if not self._check.isChecked() or self._valid_time is None:
            return
        # The resolved day is measured from now, so it can advance without the
        # selection changing. Restate the entries before deciding on a refetch.
        self._sync_product_labels()
        if self._current_signature() == self._signature:
            return
        self._request()

    def shutdown(self) -> None:
        """Interrupt any in-flight fetch and wait briefly, for window close."""
        self._timer.stop()
        self._supersede_timer.stop()
        # Every worker, not just the newest. Interruption is only observed
        # between candidates, so one inside a socket read keeps going until its
        # own timeout, and a user who outran the network leaves more than one
        # of those behind. The wait is bounded and shared, so a hung request
        # cannot delay the window closing.
        self._workers.drain(_SHUTDOWN_WAIT_MS)

    # -- internals ----------------------------------------------------------- #
    def _on_product_changed(self, *_args) -> None:
        """Switch hazard, discarding the layer the previous one produced.

        The attached layer belongs to the old product, so its window covering
        the current time says nothing about the new one; it has to be dropped
        before refetching or the map would keep showing the wrong hazard while
        the request is in flight.
        """
        # Detach unconditionally, including while switched off. Leaving the old
        # hazard's layer attached meant re-enabling later found something that
        # covered the current time and reused it, showing the previous hazard
        # under the new hazard's name.
        self._map.remove_overlay(spc_outlook.OVERLAY_KEY)
        self._token += 1  # orphan any in-flight result for the old product
        self._signature = None  # the old basis says nothing about the new one
        if not self._check.isChecked():
            return
        self._request()

    def _on_toggled(self, checked: bool) -> None:
        self._product.setVisible(checked)
        if not checked:
            # Hide rather than detach. The geometry and its legend both go away,
            # but keeping the layer means re-enabling costs no request and no
            # worker thread at all -- detaching it forced a round trip back
            # through the fetch path just to recover something already in hand.
            self._timer.stop()
            self._supersede_timer.stop()
            self._token += 1  # orphan any in-flight result
            self._map.set_overlay_visible(spc_outlook.OVERLAY_KEY, False)
            self._set_status("")
            return
        self._supersede_timer.start()
        layer = self._map.overlay(spc_outlook.OVERLAY_KEY)
        if (
            layer is not None
            and layer.covers(self._valid_time)
            and self._current_signature() == self._signature
        ):
            # Same test as set_valid_time, and for the same reason. The valid
            # time can move while the overlay is off -- set_valid_time records
            # it and returns without resolving anything -- so coverage alone
            # would restore a superseded issuance here too: a 1630Z outlook
            # covers 00Z long after the 2000Z update replaced it for that hour.
            # Requiring the basis to match means a time that moved across an
            # issuance while hidden refetches on the way back on, instead of
            # showing a stale hazard until the supersede timer next fires.
            self._map.set_overlay_visible(spc_outlook.OVERLAY_KEY, True)
            self._set_status(self._describe(layer))
            return
        self._request()

    def _request(self) -> None:
        self._timer.stop()
        if self._valid_time is None:
            return
        self._set_status("Loading SPC outlook\u2026")
        self._timer.start()

    def _start_fetch(self) -> None:
        if not self._check.isChecked() or self._valid_time is None:
            return
        # Record what this attempt is based on before starting it, so a result
        # is never credited to a candidate set that has since moved on.
        self._pending_signature = self._current_signature()
        self._token += 1
        token = self._token

        # Ask anything still running to stop, but keep it tracked: the token
        # bump above has already orphaned its result, and the thread itself has
        # to be waited on at close whether or not we still want the answer.
        self._workers.interrupt()

        worker = _SpcOutlookWorker(
            self._valid_time, token, parent=self, product=self.product()
        )
        worker.loaded.connect(self._on_loaded)
        worker.failed.connect(self._on_failed)
        self._workers.track(worker)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_loaded(self, token, valid_time, layer) -> None:
        if token != self._token or not self._check.isChecked():
            return  # superseded, or switched off while in flight
        # Remember the basis of this answer, including when the answer was
        # "nothing": a hazard that publishes no Day 3 product must be retried
        # once the target becomes Day 2 and one exists.
        self._signature = self._pending_signature
        if layer is None or not layer:
            self._map.remove_overlay(spc_outlook.OVERLAY_KEY)
            spec = spc_outlook.resolve_product(self.product())
            self._set_status(
                f"No {spec.label.lower()} covers {valid_time:%Y-%m-%d %H}Z "
                f"({spc_outlook.format_product_days(spec)}, 2020 onward)"
            )
            return
        self._map.set_overlay(spc_outlook.OVERLAY_KEY, layer, visible=True)
        self._set_status(self._describe(layer))

    def _on_failed(self, token, valid_time, message) -> None:
        if token != self._token:
            return
        # Leave the signature unset so the next opportunity retries. A failure
        # is not an answer about what SPC holds.
        self._signature = None
        self._map.remove_overlay(spc_outlook.OVERLAY_KEY)
        self._set_status(f"SPC outlook unavailable: {message}")

    @staticmethod
    def _describe(layer) -> str:
        parts = [layer.title]
        if layer.subtitle:
            parts.append(layer.subtitle)
        return "\n".join(parts)

    def _set_status(self, text: str) -> None:
        self._status.setText(text)
        self._status.setVisible(bool(text))
        self.statusChanged.emit(text)


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


class RadarOverlayController(QObject):
    """Keep one map's live radar overlay current.

    The same toggle, debounce, token, and bounded-shutdown structure as
    :class:`OutlookOverlayController`, with three differences that follow from
    radar being live rather than time-addressed:

    * **There is no valid time.** A frame is always the newest one, so nothing
      here consults the tab's selected time and no "does this cover the
      selection" test exists. Freshness is reported as an age instead.
    * **Refresh is driven by the clock, not by the user.** A repeating timer
      re-fetches at the product's publication cadence while the overlay is on,
      and stops the moment it is switched off.
    * **Coverage is checked before spending a request.** MRMS is CONUS only and
      this application picks points worldwide, so a map looking at Europe is
      told why there is no radar rather than fetching a frame it cannot draw.

    Opacity is handled entirely locally: the attached frame is re-wrapped at the
    new value, which costs neither a request nor an image decode.
    """

    #: Emitted with a short human-readable state for the owning tab to show.
    statusChanged = Signal(str)

    def __init__(
        self,
        map_widget,
        *,
        parent=None,
        label: str = "Show radar",
        enabled: bool = False,
        opacity: float = 0.85,
        scope: str = SCOPE_SITE,
        site: str = SITE_AUTO,
    ) -> None:
        super().__init__(parent)
        self._map = map_widget
        self._token = 0
        self._workers = _WorkerFleet()
        #: Which radar the last successful single-site fetch used, for the status
        #: line. Cleared whenever the scope or product changes.
        self._site_id: str | None = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_RADAR_DEBOUNCE_MS)
        self._timer.timeout.connect(self._start_fetch)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._on_refresh_tick)

        # A plain widget for the same reason as the outlook controller.
        self._content = QWidget()
        self._content.setObjectName(OBJ_PLAIN)
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(0, 0, 0, 0)

        self._check = QCheckBox(label)
        self._check.setToolTip(
            "Draw live radar over the map. Refreshes while it is switched on."
        )
        self._check.setChecked(bool(enabled))
        self._check.toggled.connect(self._on_toggled)
        layout.addWidget(self._check)

        # Which radar, before which product. A single site follows the map and
        # resolves a storm; the mosaic covers the country at about 1.9 km a
        # pixel, which is the right answer zoomed out and far too coarse zoomed
        # in. Single site is the default because this application's maps exist to
        # pick a point, and the point is usually the thing being examined.
        self._scope = QComboBox()
        self._scope.addItem("Nearest single site", SCOPE_SITE)
        self._scope.addItem("CONUS mosaic", SCOPE_MOSAIC)
        self._scope.setCurrentIndex(
            max(
                0,
                self._scope.findData(
                    scope if scope in (SCOPE_SITE, SCOPE_MOSAIC) else SCOPE_SITE
                ),
            )
        )
        self._scope.setToolTip(
            "A single radar near the map centre, at its own resolution, or the "
            "national mosaic"
        )
        self._scope.currentIndexChanged.connect(self._on_scope_changed)
        layout.addWidget(self._scope)

        # Which antenna, when the scope is a single site. "Nearest to the map"
        # answers the common question -- what is happening where I am looking --
        # but not the other one: a forecaster interrogating a storm knows the
        # radar by name and wants that radar, even when a closer one exists or
        # the map has been panned away. Editable with a completer because typing
        # four letters is how a radar is addressed; 155 identifiers is too many
        # to scan and they have no plainer names to sort by.
        self._site = QComboBox()
        self._site.setEditable(True)
        self._site.setInsertPolicy(QComboBox.NoInsert)
        self._site.setToolTip(
            "Which WSR-88D to draw. Type an identifier to jump to it."
        )
        self._site.setMaxVisibleItems(16)
        self._reload_sites(prefer=site)
        completer = self._site.completer()
        if completer is not None:
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setFilterMode(Qt.MatchContains)
        line_edit = self._site.lineEdit()
        if line_edit is not None:
            line_edit.setPlaceholderText("Nearest to map centre")
        self._site.currentIndexChanged.connect(self._on_site_changed)
        layout.addWidget(self._site)

        # One product at a time, for the same reason the outlook controller
        # offers one hazard: these are opaque colour ramps over the same pixels,
        # so two of them stacked is unreadable rather than twice as informative.
        self._product = QComboBox()
        self._product.currentIndexChanged.connect(self._on_product_changed)
        layout.addWidget(self._product)
        self._reload_products()

        opacity_row = QHBoxLayout()
        self._opacity_label = opacity_label = QLabel("Opacity")
        opacity_label.setObjectName(OBJ_HINT)
        opacity_row.addWidget(opacity_label)
        self._opacity = QSlider(Qt.Horizontal)
        # Never fully transparent and never fully opaque: 0 would be an overlay
        # the user has turned on and cannot see, and 100 hides the coastlines
        # and state borders that make the image locatable at all.
        self._opacity.setRange(20, 95)
        self._opacity.setValue(int(round(min(0.95, max(0.20, float(opacity))) * 100.0)))
        self._opacity.setToolTip("How strongly the radar image covers the map")
        self._opacity.valueChanged.connect(self._on_opacity_changed)
        opacity_row.addWidget(self._opacity, 1)
        layout.addLayout(opacity_row)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setObjectName(OBJ_HINT)
        layout.addWidget(self._status)

        # Collapse to the switch while the overlay is off, matching the outlook
        # controller: neither the product nor the opacity of an image that is
        # not being drawn is a meaningful thing to set.
        self._set_detail_visible(self._check.isChecked())

        self._sync_refresh_interval()
        if self._check.isChecked():
            self._request()

    # -- public API ---------------------------------------------------------- #
    def controls_widget(self) -> QWidget:
        """Return the widget the owning tab should add to its overlay card."""
        return self._content

    def is_enabled(self) -> bool:
        return self._check.isChecked()

    def set_enabled(self, enabled: bool) -> None:
        self._check.setChecked(bool(enabled))

    def product(self) -> str:
        return str(self._product.currentData() or self._source().DEFAULT_PRODUCT)

    def set_product(self, product: str) -> None:
        index = self._product.findData(product)
        if index >= 0:
            self._product.setCurrentIndex(index)

    def opacity(self) -> float:
        return self._opacity.value() / 100.0

    def refresh(self) -> None:
        """Force a refetch, ignoring the cached frame."""
        if not self._check.isChecked():
            return
        self._source().clear_cache()
        self._request()

    def on_view_settled(self) -> None:
        """Re-aim a nearest-to-map antenna after the view moves.

        Only the automatic choice moves with the map: a named antenna is the
        user's answer to "which radar", and panning is not a retraction of it.
        The mosaic is national, so its frame does not depend on where the map is
        looking either -- ``_request`` still gets called for it by the cadence,
        and re-fetching a composite on every pan would be pure waste.

        Without this the layer went quietly blank. Panning from Oklahoma to New
        England left KINX's frame attached and off-screen, with the marker and
        the status line both still naming KINX, until the 150-second cadence came
        round and resolved KBOX. Nothing said the map had outrun the data.
        """
        if not self._check.isChecked() or self.scope() != SCOPE_SITE:
            return
        if self.site():
            return  # pinned by name; the map does not get to change it
        if not self._in_coverage():
            # Out of range now: say so at once instead of leaving the previous
            # antenna's frame sitting there looking current.
            self._request()
            return
        serving = self._serving_site_id()
        if serving is None or serving == (self._site_id or None):
            return  # the same antenna still covers this view
        self._request()

    def _serving_site_id(self) -> str | None:
        """Which antenna would serve the current view, by id."""
        view = self._view_bounds()
        if view is None:
            return None
        resolved = radar_site.site_for_view(view)
        if not resolved:
            return None
        return resolved[0].id

    def shutdown(self) -> None:
        """Interrupt any in-flight fetch and wait briefly, for window close."""
        self._timer.stop()
        self._refresh_timer.stop()
        # Drain the whole fleet, not just the newest worker. Interruption is
        # only observed around the request, so one inside a socket read runs to
        # its own timeout; the refresh timer can have left several of those
        # behind. Bounded and shared, so a hung request cannot hold the window
        # open.
        self._workers.drain(_SHUTDOWN_WAIT_MS)

    def attached_raster(self):
        """Return the radar frame currently drawn on the map, or ``None``.

        The sibling of :meth:`HrrrFieldController.attached_raster`, and read for
        the same reason: a profile opened while radar is showing can carry that
        frame onto its locator inset without fetching a second one. ``None``
        while switched off, because a frame the user cannot see on the map should
        not appear beside the sounding either.

        Only the scope in force is offered. The two scopes own separate map
        slots, so reading both could hand back a frame from the scope the user
        switched away from.
        """
        if not self._check.isChecked():
            return None
        try:
            return self._map.overlay(self._key())
        except AttributeError:
            return None

    # -- internals ----------------------------------------------------------- #
    def scope(self) -> str:
        """Return ``"site"`` or ``"mosaic"``."""
        value = str(self._scope.currentData() or SCOPE_SITE)
        return value if value in (SCOPE_SITE, SCOPE_MOSAIC) else SCOPE_SITE

    def set_scope(self, scope: str) -> None:
        index = self._scope.findData(scope)
        if index >= 0:
            self._scope.setCurrentIndex(index)

    def site(self) -> str:
        """Return the pinned WSR-88D identifier, or :data:`SITE_AUTO`."""
        data = self._site.currentData()
        return SITE_AUTO if data is None else str(data)

    def set_site(self, site_id: str | None) -> None:
        """Pin a radar by identifier, or pass ``None`` to follow the map."""
        index = self._site.findData(
            SITE_AUTO if not site_id else str(site_id).strip().upper()
        )
        if index >= 0:
            self._site.setCurrentIndex(index)

    def _reload_sites(self, prefer: str | None = None) -> None:
        """Fill the antenna list from the bundled catalogue.

        Signals are blocked while it is built for the same reason the product
        list blocks them: Qt reports every insertion as a selection change.
        """
        wanted = SITE_AUTO if not prefer else str(prefer).strip().upper()
        blocked = self._site.blockSignals(True)
        try:
            self._site.clear()
            self._site.addItem("Nearest to map centre", SITE_AUTO)
            for entry in radar_site.sites():
                self._site.addItem(entry.id, entry.id)
                # The catalogue carries no place names, so the position is what
                # confirms a half-remembered identifier is the right one.
                self._site.setItemData(
                    self._site.count() - 1,
                    "%s at %.2f%s %.2f%s"
                    % (
                        entry.id,
                        abs(entry.lat),
                        "N" if entry.lat >= 0 else "S",
                        abs(entry.lon),
                        "E" if entry.lon >= 0 else "W",
                    ),
                    Qt.ToolTipRole,
                )
            self._site.setCurrentIndex(max(0, self._site.findData(wanted)))
        finally:
            self._site.blockSignals(blocked)

    def _pinned_site(self):
        """Return the selected :class:`~sharpmod.radar_site.RadarSite`, or None."""
        if self.scope() != SCOPE_SITE:
            return None
        return radar_site.site_by_id(self.site())

    def _on_site_changed(self, *_args) -> None:
        """Switch antenna, discarding the frame the previous one produced.

        Detached unconditionally, for the reason the product change is: a frame
        left attached would be re-shown on the next enable under the new site's
        name while actually being the old site's image.
        """
        self._map.remove_overlay(radar_site.OVERLAY_KEY)
        self._site_id = None
        self._token += 1
        # A pinned radar may not publish every product the scope offers, so the
        # list is rebuilt against what this antenna actually has.
        self._reload_products(prefer=self.product())
        self._sync_refresh_interval()
        self._sync_site_markers()
        if not self._check.isChecked():
            return
        self._request()

    def _source(self):
        """Return the module backing the current scope.

        The two modules present the same surface on purpose -- ``get_product``,
        ``available_products``, ``covers``, ``clear_cache``, ``OVERLAY_KEY`` and a
        ``fetch`` that answers ``None`` only for cancellation -- so everything
        below is written once against whichever is selected.
        """
        return radar_site if self.scope() == SCOPE_SITE else radar_mosaic

    def _key(self) -> str:
        return self._source().OVERLAY_KEY

    def _other_key(self) -> str:
        """The key belonging to the scope that is *not* selected."""
        return (
            radar_mosaic.OVERLAY_KEY
            if self.scope() == SCOPE_SITE
            else radar_site.OVERLAY_KEY
        )

    def _spec(self):
        return self._source().get_product(self.product())

    def _reload_products(self, prefer: str | None = None) -> None:
        """Refill the product list for the selected scope.

        The two scopes publish different products -- the mosaic has echo tops,
        a site has radial velocity and accumulations -- so the list is rebuilt
        rather than filtered. Signals are blocked while it is, because Qt emits
        ``currentIndexChanged`` as items come and go and each would look like a
        user selection.
        """
        source = self._source()
        # A named antenna is filtered to what it actually publishes. Offering a
        # product the site does not carry would fail the fetch with a message the
        # user cannot act on, when the list could simply not have offered it.
        pinned = self._pinned_site()
        offered = [
            spec
            for spec in source.available_products()
            if pinned is None or pinned.supports(spec)
        ] or list(source.available_products())
        blocked = self._product.blockSignals(True)
        try:
            self._product.clear()
            for spec in offered:
                self._product.addItem(spec.label, spec.key)
                if spec.description:
                    self._product.setItemData(
                        self._product.count() - 1, spec.description, Qt.ToolTipRole
                    )
            index = self._product.findData(prefer or source.DEFAULT_PRODUCT)
            self._product.setCurrentIndex(max(0, index))
        finally:
            self._product.blockSignals(blocked)

    def _sync_refresh_interval(self) -> None:
        """Point the refresh timer at the selected product's cadence."""
        cadence_ms = int(self._spec().update_interval_s * 1000.0)
        self._refresh_timer.setInterval(max(_RADAR_REFRESH_FLOOR_MS, cadence_ms))

    def _view_bounds(self):
        """Return the map's lon/lat view, or ``None`` if it cannot report one."""
        try:
            return self._map.view_bounds()
        except AttributeError:
            return None

    def _in_coverage(self) -> bool:
        """Report whether the map could display this product at all."""
        if self._pinned_site() is not None:
            # A named radar is honoured wherever the map is looking. The
            # proximity test exists to choose an antenna, and there is nothing
            # left to choose -- refusing here would mean a user could select a
            # site and be told no radar is in range of it.
            return True
        view = self._view_bounds()
        if view is None:
            # A map without the accessor cannot be interrogated; assume it can
            # see the product rather than silently refusing to ever draw.
            return True
        return self._source().covers(view)

    def _on_scope_changed(self, *_args) -> None:
        """Switch between a single site and the mosaic.

        Both slots are detached, not just the one being left: they are separate
        overlay keys, so leaving the previous frame attached would draw two radar
        images at once — which is exactly what choosing a scope is meant to avoid.
        """
        self._map.remove_overlay(radar_site.OVERLAY_KEY)
        self._map.remove_overlay(radar_mosaic.OVERLAY_KEY)
        self._site_id = None
        self._token += 1
        self._reload_products()
        self._sync_refresh_interval()
        self._set_detail_visible(self._check.isChecked())
        if not self._check.isChecked():
            return
        self._request()

    def _on_toggled(self, checked: bool) -> None:
        self._set_detail_visible(checked)
        if not checked:
            # Hide rather than detach, matching the outlook controller: the
            # frame and its decoded pixmap both stay, so switching back on is
            # instant and costs no request.
            self._timer.stop()
            self._refresh_timer.stop()
            self._token += 1  # orphan any in-flight result
            self._map.set_overlay_visible(self._key(), False)
            self._set_status("")
            return
        self._refresh_timer.start()
        raster = self._map.overlay(self._key())
        if raster is not None and not raster.is_stale():
            self._map.set_overlay_visible(self._key(), True)
            self._set_status(self._describe(raster))
            return
        self._request()

    def _set_detail_visible(self, visible: bool) -> None:
        """Show or hide the controls that only apply to a drawn overlay."""
        for widget in (self._product, self._opacity_label, self._opacity):
            widget.setVisible(bool(visible))
        # The antenna list belongs to the single-site scope alone: the mosaic is
        # one national composite and has no site to choose.
        self._site.setVisible(bool(visible) and self.scope() == SCOPE_SITE)
        self._sync_site_markers()

    def _sync_site_markers(self) -> None:
        """Put the antennas on the map, or take them off.

        Shown while a single site is being drawn, because that is when "which
        radar" is a live question and clicking one on the map is a far more
        direct answer than finding it in a list of 155. Cleared for the mosaic
        and whenever the overlay is off, so the markers never outlive the reason
        to show them.

        Duck-typed against the map for the same reason ``view_bounds`` is: not
        every map this controller can be attached to has to carry the layer.
        """
        wanted = self._check.isChecked() and self.scope() == SCOPE_SITE
        try:
            self._map.set_radar_sites(radar_site.sites() if wanted else ())
            self._map.set_radar_site_selected(self._site_id or self.site() or None)
        except AttributeError:
            return

    def _on_product_changed(self, *_args) -> None:
        """Switch product, discarding the frame the previous one produced.

        Detached unconditionally, including while switched off: leaving it
        attached meant re-enabling later found a frame that looked fresh and
        showed the previous product under the new product's name.
        """
        self._map.remove_overlay(self._key())
        self._token += 1  # orphan any in-flight result for the old product
        self._sync_refresh_interval()
        if not self._check.isChecked():
            return
        self._request()

    def _on_opacity_changed(self, *_args) -> None:
        """Re-wrap the attached frame at the new opacity, with no request.

        Applied even while switched off so the value is already correct when the
        overlay is switched back on.
        """
        raster = self._map.overlay(self._key())
        if raster is None:
            return
        self._map.set_overlay(self._key(), raster.at_opacity(self.opacity()))

    def _on_refresh_tick(self) -> None:
        """Fetch the next frame, or explain why one is not being fetched."""
        if not self._check.isChecked():
            self._refresh_timer.stop()
            return
        self._request()

    def _request(self) -> None:
        self._timer.stop()
        if not self._in_coverage():
            # Report and spend nothing. The timer keeps running, so panning back
            # into coverage recovers on its own within one cadence.
            self._map.set_overlay_visible(self._key(), False)
            # Two different reasons for two different scopes, because "no radar
            # here" means different things: no antenna within range, versus a
            # view outside the national composite entirely.
            self._set_status(
                "No NEXRAD site is within range of this view"
                if self.scope() == SCOPE_SITE
                else "The mosaic covers the contiguous United States; the map is "
                "currently outside it"
            )
            return
        self._map.set_overlay_visible(self._key(), True)
        self._set_status("Loading radar\u2026")
        self._timer.start()

    def _start_fetch(self) -> None:
        if not self._check.isChecked() or not self._in_coverage():
            return
        self._token += 1
        token = self._token

        # Interrupt but keep tracking: the token bump already orphaned the
        # result, yet the thread still has to be waited on at close.
        self._workers.interrupt()

        if self.scope() == SCOPE_SITE:
            # The view travels with the request: which antenna serves it is
            # resolved from the map centre, and the user may pan before the
            # frame lands.
            worker = _RadarSiteWorker(
                token,
                parent=self,
                product=self.product(),
                view=self._view_bounds(),
                site_id=self.site() or None,
                opacity=self.opacity(),
            )
        else:
            worker = _RadarMosaicWorker(
                token, parent=self, product=self.product(), opacity=self.opacity()
            )
        worker.loaded.connect(self._on_loaded)
        worker.failed.connect(self._on_failed)
        self._workers.track(worker)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_loaded(self, token, raster) -> None:
        if token != self._token or not self._check.isChecked():
            return  # superseded, or switched off while in flight
        if raster is None:
            self._set_status("No radar frame was returned")
            return
        # A site frame names the antenna it came from, which is the one thing the
        # user cannot infer from the image itself.
        if self.scope() == SCOPE_SITE:
            self._site_id = raster.short_name or None
            # Highlight whichever antenna actually served the frame, which for
            # "nearest to map" is the one the map resolved rather than anything
            # the user picked.
            self._sync_site_markers()
        self._map.set_overlay(self._key(), raster, visible=True)
        self._set_status(self._describe(raster) + self._offscreen_note(raster))

    def _offscreen_note(self, raster) -> str:
        """Warn when a pinned radar's frame is not on screen.

        Choosing a distant antenna is legitimate -- it is how you keep watching a
        storm after panning away -- but an overlay that draws nothing looks
        broken. Saying so is the difference between "not fetched" and "fetched,
        just not here".
        """
        if self._pinned_site() is None:
            return ""
        view = self._view_bounds()
        if view is None:
            return ""
        lon0, lon1, lat0, lat1 = view
        west, east, south, north = raster.bounds
        if east >= lon0 and west <= lon1 and north >= lat0 and south <= lat1:
            return ""
        return "\n\u26a0 This radar is outside the current view"

    def _on_failed(self, token, message) -> None:
        if token != self._token:
            return
        # The previous frame is left attached on purpose. It is labelled with
        # its own age, so an ageing image plus a stated failure is more useful
        # than a blank map, and the legend marks it stale once it is too old.
        self._set_status("Radar unavailable: %s" % message)

    @staticmethod
    def _describe(raster) -> str:
        parts = [raster.title]
        age = format_age(raster.age_seconds())
        if age:
            parts.append("Frame is %s" % age)
        return "\n".join(parts)

    def _set_status(self, text: str) -> None:
        self._status.setText(text)
        self._status.setVisible(bool(text))
        self.statusChanged.emit(text)


#: Coalesces a run through the product list or a dragged opacity slider. Longer
#: than the radar debounce because a field costs a GRIB subset, a reprojection
#: and a PNG encode rather than one WMS request, so a discarded one is dearer.
_FIELD_DEBOUNCE_MS = 350

#: HRRR publishes hourly. Re-checking a little more often than that picks up a
#: late run without polling a public bucket for no reason.
_FIELD_REFRESH_MS = 10 * 60 * 1000


class HrrrFieldController(QObject):
    """Keep one map's HRRR model-field overlay current.

    Same toggle, debounce, token and bounded-shutdown structure as the radar and
    outlook controllers. Three things are particular to it:

    * **Two combo boxes, not one.** Twenty-three products in a single flat list
      is a scrolling exercise; grouping them by category the way a forecaster
      works -- pattern, then surface, then instability, then shear, then
      composites -- makes the one they want two short choices away. The category
      box is a filter over the product box, not a separate setting.
    * **One slot for every product.** They all share
      :data:`self._fields.OVERLAY_KEY`, so selecting a different field replaces the
      one on the map instead of stacking. That is deliberate: these are opaque
      colour ramps over the same pixels, and two at once is unreadable. The SPC
      outlook and the radar frame use their own keys and so keep composing with
      whichever field is showing.
    * **It is time-addressed.** A field belongs to a run and a forecast hour, so
      the tab's selected valid time drives which one is fetched, and a result
      that arrives after the selection moved on is discarded by token.
    """

    #: Emitted with a short human-readable state for the owning tab to show.
    statusChanged = Signal(str)

    def __init__(
        self,
        map_widget,
        *,
        parent=None,
        label: str = "Show HRRR model field",
        enabled: bool = False,
        product: str | None = None,
        opacity: float = 0.75,
    ) -> None:
        super().__init__(parent)
        # Imported here rather than at module scope, and held as attributes so
        # the rest of the class can use them normally. Both modules pull in
        # NumPy, and the picker is required to reach first paint without it --
        # ``test_picker_import_does_not_load_heavy_analysis_modules`` enforces
        # that, because importing NumPy on the startup path is measurable delay
        # for a window whose first job is to draw a map and a station list.
        from sharpmod import hrrr_field, hrrr_products

        self._fields = hrrr_field
        self._catalogue = hrrr_products

        self._map = map_widget
        self._token = 0
        self._workers = _WorkerFleet()
        self._valid_time: datetime | None = None
        # A pinned cycle, set by a tab that has one. While these are None the
        # overlay follows ``_valid_time`` instead, which is the observed tab's
        # only handle on time.
        self._run: datetime | None = None
        self._fxx: int | None = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_FIELD_DEBOUNCE_MS)
        self._timer.timeout.connect(self._start_fetch)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(_FIELD_REFRESH_MS)
        self._refresh_timer.timeout.connect(self._on_refresh_tick)

        self._content = QWidget()
        self._content.setObjectName(OBJ_PLAIN)
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(0, 0, 0, 0)

        self._check = QCheckBox(label)
        self._check.setToolTip(
            "Draw an HRRR forecast field over the contiguous United States, "
            "beneath the SPC outlook and any radar. One field at a time."
        )
        self._check.setChecked(bool(enabled))
        self._check.toggled.connect(self._on_toggled)
        layout.addWidget(self._check)

        wanted = self._catalogue.get_product(product)

        self._category = QComboBox()
        for name, _members in self._catalogue.products_by_category():
            self._category.addItem(name, name)
        self._category.setToolTip("Which group of fields to choose from")
        layout.addWidget(self._category)

        self._product = QComboBox()
        self._product.setToolTip("The field to draw")
        layout.addWidget(self._product)

        # Populate before connecting, so building the initial list cannot look
        # like a user selection and fire a fetch for the wrong product.
        self._category.setCurrentIndex(max(0, self._category.findData(wanted.category)))
        self._reload_products(wanted.key)
        self._category.currentIndexChanged.connect(self._on_category_changed)
        self._product.currentIndexChanged.connect(self._on_product_changed)

        opacity_row = QHBoxLayout()
        self._opacity_label = opacity_label = QLabel("Opacity")
        opacity_label.setObjectName(OBJ_HINT)
        opacity_row.addWidget(opacity_label)
        self._opacity = QSlider(Qt.Horizontal)
        # Same reasoning as radar: never invisible, never fully opaque. A field
        # covers the whole country, so the floor matters more here -- the
        # coastline and state borders underneath are what locate it.
        self._opacity.setRange(20, 95)
        self._opacity.setValue(int(round(min(0.95, max(0.20, float(opacity))) * 100.0)))
        self._opacity.setToolTip("How strongly the field covers the map")
        self._opacity.valueChanged.connect(self._on_opacity_changed)
        opacity_row.addWidget(self._opacity, 1)
        layout.addLayout(opacity_row)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setObjectName(OBJ_HINT)
        layout.addWidget(self._status)

        self._set_detail_visible(self._check.isChecked())
        if self._check.isChecked():
            self._refresh_timer.start()
            self._request()

    # -- public API ---------------------------------------------------------- #
    def controls_widget(self) -> QWidget:
        """Return the widget the owning tab should add to its overlay card."""
        return self._content

    def is_enabled(self) -> bool:
        return self._check.isChecked()

    def set_enabled(self, enabled: bool) -> None:
        self._check.setChecked(bool(enabled))

    def product(self) -> str:
        return str(self._product.currentData() or self._catalogue.DEFAULT_PRODUCT)

    def set_product(self, product: str) -> None:
        """Select a product, moving the category box to match if needed."""
        spec = self._catalogue.get_product(product)
        category = self._category.findData(spec.category)
        if category >= 0 and category != self._category.currentIndex():
            self._category.setCurrentIndex(category)
        index = self._product.findData(spec.key)
        if index >= 0:
            self._product.setCurrentIndex(index)

    def opacity(self) -> float:
        return self._opacity.value() / 100.0

    def attached_raster(self):
        """Return the field image currently drawn on the map, or ``None``.

        Read by the sounding window so a profile opened while a field is showing
        carries that same field onto its locator inset. ``None`` while the overlay
        is switched off, because a field the user cannot see on the map should not
        appear beside the sounding either.
        """
        if not self._check.isChecked():
            return None
        try:
            return self._map.overlay(self._fields.OVERLAY_KEY)
        except AttributeError:
            return None

    def set_valid_time(self, when: datetime | None) -> None:
        """Point the overlay at a forecast time, refetching if it moved.

        Compared at hour resolution because that is the resolution HRRR
        publishes: stepping a minute spinner inside the same hour resolves to the
        same forecast field, and refetching it would spend a GRIB subset to
        redraw an identical image.
        """
        previous = self._valid_time
        previous_run, previous_fxx = self._run, self._fxx
        self._valid_time = when
        # A moment supersedes a pinned cycle: the caller is telling us it no
        # longer has one, and leaving the pin in place would silently keep
        # serving the old cycle while the label said otherwise.
        self._run = None
        self._fxx = None
        if not self._check.isChecked():
            return
        if (
            previous_run is None
            and previous_fxx is None
            and _same_forecast_hour(previous, when)
        ):
            return
        self._request()

    def set_forecast_reference(self, run: datetime | None, fxx: int | None) -> None:
        """Pin the overlay to an exact model cycle run and forecast hour.

        This is the difference between "a field valid at the same hour" and "the
        field the sounding came from". Selecting HRRR 12Z F18 and being shown the
        18Z run's F12 would be two different forecasts presented as one, so the
        tab that owns a cycle selection pins it here. Passing ``None`` releases
        the pin and returns the overlay to following the valid time.
        """
        if run is None:
            if self._run is None and self._fxx is None:
                return
            self._run = None
            self._fxx = None
            if self._check.isChecked():
                self._request()
            return
        anchored, hour = self._fields.pin_request(run, fxx)
        if self._run == anchored and self._fxx == hour:
            return
        self._run = anchored
        self._fxx = hour
        # Keep the moment in step so a later release of the pin does not fall
        # back to a stale valid time from a different selection.
        self._valid_time = anchored + timedelta(hours=hour)
        if self._check.isChecked():
            self._request()

    def refresh(self) -> None:
        """Force a refetch, ignoring the cached frame."""
        if not self._check.isChecked():
            return
        self._fields.clear_cache()
        self._request()

    def on_view_settled(self) -> None:
        """Recover promptly when an enabled field re-enters HRRR coverage."""
        if not self._check.isChecked():
            return
        if not self._in_coverage():
            self._request()
            return
        raster = self._map.overlay(self._fields.OVERLAY_KEY)
        if (
            raster is not None
            and raster.short_name == self.product()
            and not raster.is_stale()
        ):
            self._map.set_overlay_visible(self._fields.OVERLAY_KEY, True)
            self._set_status(self._describe(raster))
            return
        self._request()

    def shutdown(self) -> None:
        """Interrupt any in-flight fetch and wait briefly, for window close."""
        self._timer.stop()
        self._refresh_timer.stop()
        self._workers.drain(_SHUTDOWN_WAIT_MS)

    # -- internals ----------------------------------------------------------- #
    def _reload_products(self, prefer: str | None = None) -> None:
        """Refill the product box for the selected category.

        Signals are blocked while the list is rebuilt: Qt emits
        ``currentIndexChanged`` as items come and go, and each of those would
        otherwise look like the user picking a product.
        """
        category = str(self._category.currentData() or "")
        members = [
            product
            for product in self._catalogue.available_products()
            if product.category == category
        ]
        blocked = self._product.blockSignals(True)
        try:
            self._product.clear()
            for spec in members:
                self._product.addItem(spec.label, spec.key)
                self._product.setItemData(
                    self._product.count() - 1, _product_tooltip(spec), Qt.ToolTipRole
                )
            index = self._product.findData(prefer) if prefer else -1
            self._product.setCurrentIndex(max(0, index))
        finally:
            self._product.blockSignals(blocked)

    def _in_coverage(self) -> bool:
        try:
            view = self._map.view_bounds()
        except AttributeError:
            return True
        return self._fields.covers(view)

    def _set_detail_visible(self, visible: bool) -> None:
        for widget in (
            self._category,
            self._product,
            self._opacity_label,
            self._opacity,
        ):
            widget.setVisible(bool(visible))

    def _on_toggled(self, checked: bool) -> None:
        self._set_detail_visible(checked)
        if not checked:
            # Hide rather than detach, so switching back on is instant.
            self._timer.stop()
            self._refresh_timer.stop()
            self._token += 1
            self._map.set_overlay_visible(self._fields.OVERLAY_KEY, False)
            self._set_status("")
            return
        self._refresh_timer.start()
        raster = self._map.overlay(self._fields.OVERLAY_KEY)
        if (
            raster is not None
            and raster.short_name == self.product()
            and not raster.is_stale()
        ):
            self._map.set_overlay_visible(self._fields.OVERLAY_KEY, True)
            self._set_status(self._describe(raster))
            return
        self._request()

    def _on_category_changed(self, *_args) -> None:
        """Move to another group, selecting its first field."""
        self._reload_products()
        self._on_product_changed()

    def _on_product_changed(self, *_args) -> None:
        """Switch field, discarding the image the previous one produced.

        Detached unconditionally, including while switched off, for the same
        reason as radar: leaving it attached means re-enabling later finds an
        image that looks fresh and shows the previous field under the new
        field's name.
        """
        self._map.remove_overlay(self._fields.OVERLAY_KEY)
        self._token += 1
        if not self._check.isChecked():
            return
        self._request()

    def _on_opacity_changed(self, *_args) -> None:
        """Re-wrap the attached image at the new opacity, with no request."""
        raster = self._map.overlay(self._fields.OVERLAY_KEY)
        if raster is None:
            return
        self._map.set_overlay(
            self._fields.OVERLAY_KEY, raster.at_opacity(self.opacity())
        )

    def _on_refresh_tick(self) -> None:
        if not self._check.isChecked():
            self._refresh_timer.stop()
            return
        self._request()

    def _request(self) -> None:
        self._timer.stop()
        if not self._in_coverage():
            self._map.set_overlay_visible(self._fields.OVERLAY_KEY, False)
            self._set_status(
                "HRRR covers the contiguous United States; the map is "
                "currently outside it"
            )
            return
        self._map.set_overlay_visible(self._fields.OVERLAY_KEY, True)
        self._set_status(
            "Loading %s\u2026" % self._catalogue.get_product(self.product()).label
        )
        self._timer.start()

    def _start_fetch(self) -> None:
        if not self._check.isChecked() or not self._in_coverage():
            return
        self._token += 1
        token = self._token
        self._workers.interrupt()

        worker = _HrrrFieldWorker(
            token,
            parent=self,
            product=self.product(),
            valid_time=self._valid_time,
            run=self._run,
            fxx=self._fxx,
            opacity=self.opacity(),
        )
        worker.loaded.connect(self._on_loaded)
        worker.failed.connect(self._on_failed)
        self._workers.track(worker)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_loaded(self, token, _valid_time, raster) -> None:
        if token != self._token or not self._check.isChecked():
            return  # superseded, or switched off while in flight
        if raster is None:
            self._set_status("No field was returned")
            return
        self._map.set_overlay(self._fields.OVERLAY_KEY, raster, visible=True)
        self._set_status(self._describe(raster))

    def _on_failed(self, token, message) -> None:
        if token != self._token:
            return
        # The previous image is left attached for the same reason radar's is: it
        # carries its own age, and an ageing field plus a stated failure beats a
        # blank map.
        self._set_status("Field unavailable: %s" % message)

    def _describe(self, raster) -> str:
        spec = self._catalogue.get_product(raster.short_name or self.product())
        parts = [raster.title]
        if raster.subtitle:
            parts.append(raster.subtitle)
        if spec.caveat:
            parts.append(spec.caveat)
        return "\n".join(parts)

    def _set_status(self, text: str) -> None:
        self._status.setText(text)
        self._status.setVisible(bool(text))
        self.statusChanged.emit(text)


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


class LocatorOverlaySelector(QObject):
    """Pick which overlays travel onto a sounding's locator inset.

    A thin control over :mod:`sharpmod.locator_overlay`, which owns the rules.
    Nothing here decides what may coexist; it presents the families, asks that
    module to reduce the selection, and then makes the boxes agree with the
    answer. Keeping the judgement in one place is what stops this control and
    the ``--locator-overlay`` command line drifting apart.

    Two behaviours are worth stating, because both are the control telling the
    truth rather than quietly disagreeing with what will be drawn:

    * Choosing radar clears the outlook and the field, since radar displaces
      them. The boxes move, so the control never claims to be showing something
      that has been displaced.
    * Storm reports stay disabled, with a tooltip saying why, until the outlook
      is selected. They are read against it -- the question is whether what was
      forecast happened -- and a scatter of markers with nothing behind them
      cannot answer that.
    """

    #: Emitted whenever the effective selection changes.
    selectionChanged = Signal()

    #: Human labels, in the order :data:`locator_overlay.FAMILIES` gives.
    LABELS = {
        "risk": "SPC risk areas",
        "reports": "Storm reports",
        "hrrr": "HRRR model field",
        "radar-site": "Radar (nearest site)",
        "radar-mosaic": "Radar (national mosaic)",
    }

    #: Combo entry meaning "whatever hazard the map is showing". Kept as the
    #: default because the inset is context for the map the sounding came from,
    #: so following it is the answer that needs no decision.
    FOLLOW_MAP_LABEL = "Match the map"

    def __init__(self, *, parent=None, settings=None):
        super().__init__(parent)
        from sharpmod import locator_overlay

        self._rules = locator_overlay
        self._settings = settings
        self._applying = False
        self._boxes: dict = {}

        self._widget = QWidget()
        layout = QVBoxLayout(self._widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        caption = QLabel("Show on the sounding's locator")
        caption.setToolTip(
            "Overlays drawn on the small map beside the hodograph. The risk "
            "areas and a model field can be shown together; radar replaces "
            "them."
        )
        layout.addWidget(caption)

        # Built before the loop so ``_sync`` and :meth:`hazard` never depend on
        # the risk family's position in FAMILIES; the loop only places it.
        hazard_combo = self._build_hazard_combo()
        for family in self._rules.FAMILIES:
            box = QCheckBox(self.LABELS.get(family, family))
            box.setProperty("locator_family", family)
            box.toggled.connect(self._on_toggled)
            layout.addWidget(box)
            self._boxes[family] = box
            if family == self._rules.FAMILY_RISK:
                layout.addWidget(hazard_combo)

        self._restore()
        self._sync()

    def _build_hazard_combo(self):
        """Choose which outlook the inset draws, independently of the map.

        The inset had no hazard control at all: the selection it produced named
        only the family, so it could only ever draw whichever outlook something
        else had chosen. Naming a hazard here makes it an explicit request, which
        outranks the map for exactly that reason.
        """
        self._hazard = QComboBox()
        self._hazard.addItem(self.FOLLOW_MAP_LABEL, None)
        for spec in spc_outlook.PRODUCTS.values():
            self._hazard.addItem(_product_item_text(spec, None), spec.key)
        self._hazard.setCurrentIndex(0)
        self._hazard.setToolTip(
            "Which outlook the locator inset draws. \u201c"
            f"{self.FOLLOW_MAP_LABEL}\u201d follows the hazard selected for the "
            "map; naming one here pins the inset to it instead. Hazard "
            "probabilities are issued for Days 1 and 2, so a sounding outside "
            "those days falls back to the categorical outlook."
        )
        self._hazard.currentIndexChanged.connect(self._on_hazard_changed)
        return self._hazard

    def hazard(self):
        """Return the pinned outlook product, or ``None`` to follow the map."""
        return self._hazard.currentData()

    # -- public API ---------------------------------------------------------- #
    def controls_widget(self):
        """Return the widget a tab should mount."""
        return self._widget

    def selection(self):
        """Return the reduced selection, as the rules module resolves it."""
        hazard = self.hazard()
        chosen = [
            self._rules.Selection(
                family,
                hazard if family == self._rules.FAMILY_RISK else None,
            )
            for family in self._rules.FAMILIES
            if self._boxes[family].isChecked()
        ]
        return self._rules.enforce_exclusivity(tuple(chosen))

    def spec(self) -> str:
        """Return the selection in the form the command line accepts."""
        return ",".join(item.spec() for item in self.selection()) or "none"

    def set_spec(self, text: str | None) -> None:
        """Adopt a specification string, ignoring anything unrecognised."""
        try:
            selections = self._rules.parse(text)
        except Exception:  # noqa: BLE001 - a stored string is not trusted
            selections = ()
        wanted = {item.family for item in selections}
        hazard = next(
            (
                item.product
                for item in selections
                if item.family == self._rules.FAMILY_RISK
            ),
            None,
        )
        self._applying = True
        try:
            for family, box in self._boxes.items():
                box.setChecked(family in wanted)
            # An unknown product falls back to following the map rather than
            # silently pinning the inset to something that cannot be drawn.
            index = self._hazard.findData(hazard)
            self._hazard.setCurrentIndex(max(0, index))
        finally:
            self._applying = False
        self._sync()

    def remember(self) -> None:
        """Persist the *choice*, never whether it was switched on.

        The same policy the field and radar choices follow: a launch should still
        reach for no network until asked, while a user who always wants the risk
        areas finds them already selected.
        """
        if self._settings is None:
            return
        with suppress(Exception):
            self._settings.setValue(LOCATOR_SETTINGS_KEY, self.spec())

    # -- internals ----------------------------------------------------------- #
    def _restore(self) -> None:
        if self._settings is None:
            return
        try:
            stored = self._settings.value(LOCATOR_SETTINGS_KEY, "")
        except Exception:  # noqa: BLE001 - an unreadable INI is not fatal
            return
        self.set_spec(str(stored or "") or None)

    def _on_toggled(self, _checked) -> None:
        if self._applying:
            return
        self._sync()
        self.selectionChanged.emit()

    def _on_hazard_changed(self, _index) -> None:
        if self._applying:
            return
        self._sync()
        self.selectionChanged.emit()

    def _sync(self) -> None:
        """Make the boxes agree with what the rules will actually draw."""
        effective = {item.family for item in self.selection()}
        # Which outlook to draw only means something once one is being drawn, the
        # same collapse the map's own outlook card uses.
        self._hazard.setVisible(self._rules.FAMILY_RISK in effective)
        self._applying = True
        try:
            for family, box in self._boxes.items():
                if box.isChecked() and family not in effective:
                    # Displaced by an exclusive choice, or its prerequisite is
                    # not selected. Either way it will not be drawn, so the box
                    # must not go on claiming otherwise.
                    box.setChecked(False)
                missing = self._rules.missing_prerequisite(family, self.selection())
                box.setEnabled(missing is None)
                box.setToolTip(
                    ""
                    if missing is None
                    else f"Select {self.LABELS.get(missing, missing)} first; "
                    "storm reports are read against the outlook that "
                    "anticipated them."
                )
        finally:
            self._applying = False


class StormReportsOverlayController(QObject):
    """Draw local storm reports on one picker map, beneath the outlook.

    Reports are read *against* the outlook that anticipated them -- the question
    is whether what was forecast is what happened -- so the switch stays disabled
    until the outlook on the same map is showing, and it turns itself off if the
    outlook is switched off underneath it. That mirrors the rule
    :mod:`sharpmod.locator_overlay` applies to the sounding's inset, so the map
    and the inset cannot disagree about what may be shown alone.

    Otherwise shaped like :class:`OutlookOverlayController`: a debounced request
    so scrubbing a date does not become one fetch per intermediate value, a token
    to discard a result the user has moved past, and a bounded drain on close.
    """

    def __init__(
        self, map_widget, *, parent=None, label="Show storm reports", enabled=False
    ):
        super().__init__(parent)
        from sharpmod import storm_reports

        self._map = map_widget
        self._reports = storm_reports
        self._valid_time = None
        self._token = 0
        self._workers = _WorkerFleet()
        self._outlook = None

        self._content = QWidget()
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(0, 0, 0, 0)

        self._check = QCheckBox(label)
        self._check.setChecked(bool(enabled))
        self._check.toggled.connect(self._on_toggled)
        layout.addWidget(self._check)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setObjectName(OBJ_HINT)
        layout.addWidget(self._status)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_REFRESH_DEBOUNCE_MS)
        self._timer.timeout.connect(self._start_fetch)

        self._sync_available()

    # -- public API ---------------------------------------------------------- #
    def controls_widget(self) -> QWidget:
        return self._content

    def is_enabled(self) -> bool:
        return self._check.isChecked()

    def set_enabled(self, enabled: bool) -> None:
        self._check.setChecked(bool(enabled))

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
            ""
            if available
            else "Switch on the SPC convective outlook first; storm reports are "
            "read against the outlook that anticipated them."
        )
        if not available and self._check.isChecked():
            # Turning itself off rather than drawing on regardless: reports with
            # no risk areas behind them cannot answer the question they exist
            # for, and leaving the box ticked would misreport what is on screen.
            self._check.setChecked(False)
        self._status.setVisible(self._check.isChecked())

    def _on_toggled(self, checked: bool) -> None:
        self._status.setVisible(bool(checked))
        if not checked:
            self._timer.stop()
            self._token += 1
            self._workers.interrupt()
            self._map.remove_overlay(self._reports.OVERLAY_KEY)
            self._status.setText("")
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
        worker = _StormReportsWorker(
            self._valid_time, token, parent=self, view=view, span_deg=span
        )
        worker.loaded.connect(self._on_loaded)
        worker.failed.connect(self._on_failed)
        self._workers.track(worker)
        worker.finished.connect(worker.deleteLater)
        self._status.setText("Loading storm reports\u2026")
        worker.start()

    def _on_loaded(self, token, _valid_time, layer) -> None:
        if token != self._token or not self._check.isChecked():
            return
        if not layer:
            self._map.remove_overlay(self._reports.OVERLAY_KEY)
            self._status.setText("No storm reports in this window")
            return
        self._map.set_overlay(self._reports.OVERLAY_KEY, layer, visible=True)
        count = len(getattr(layer, "shapes", ()))
        self._status.setText(
            f"{count} storm report{'s' if count != 1 else ''} \u00b7 "
            f"{layer.subtitle or 'click one for detail'}"
        )

    def _on_failed(self, token, _valid_time, message) -> None:
        if token != self._token:
            return
        self._map.remove_overlay(self._reports.OVERLAY_KEY)
        self._status.setText(f"Storm reports unavailable: {message}")

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

from datetime import datetime

from qtpy.QtCore import QObject, Qt, QTimer, Signal
from qtpy.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget,
)

from sharpmod import radar_mosaic, spc_outlook
from sharpmod.gui_workers import _RadarMosaicWorker, _SpcOutlookWorker
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
        self._worker: _SpcOutlookWorker | None = None
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
            "(2020 onward)")
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
            max(0, self._product.findData(spc_outlook.DEFAULT_PRODUCT)))
        self._product.setToolTip(
            "Hazard probabilities are only issued for Days 1 and 2; the "
            "categorical outlook covers Days 1 to 3")
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
        return str(self._product.currentData()
                   or spc_outlook.DEFAULT_PRODUCT)

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
        if layer is not None and layer.covers(when) \
                and self._current_signature() == self._signature:
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
            self._product.setItemText(index, _product_item_text(
                spec, _resolved_day(self._valid_time, spec.key)))

    def _current_signature(self) -> tuple[str, ...] | None:
        """Return what could answer the current selection, or ``None``."""
        if self._valid_time is None:
            return None
        return spc_outlook.resolution_signature(
            self._valid_time, product=self.product())

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
        worker = self._worker
        self._worker = None
        if worker is None:
            return
        try:
            if not worker.isRunning():
                return
            worker.requestInterruption()
            # Interruption is only observed between candidates, so a worker
            # inside a socket read keeps going until its own timeout. Give it a
            # moment rather than letting Qt warn that a running thread was
            # destroyed; the wait is bounded so a hung request cannot delay the
            # window closing for longer than this.
            worker.wait(_SHUTDOWN_WAIT_MS)
        except RuntimeError:
            pass

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
        if layer is not None and layer.covers(self._valid_time):
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

        previous = self._worker
        self._worker = None
        if previous is not None:
            try:
                if previous.isRunning():
                    previous.requestInterruption()
            except RuntimeError:
                pass

        worker = _SpcOutlookWorker(self._valid_time, token, parent=self,
                                   product=self.product())
        worker.loaded.connect(self._on_loaded)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_loaded(self, token, valid_time, layer) -> None:
        if token != self._token or not self._check.isChecked():
            return  # superseded, or switched off while in flight
        self._worker = None
        # Remember the basis of this answer, including when the answer was
        # "nothing": a hazard that publishes no Day 3 product must be retried
        # once the target becomes Day 2 and one exists.
        self._signature = self._pending_signature
        if layer is None or not layer:
            self._map.remove_overlay(spc_outlook.OVERLAY_KEY)
            spec = spc_outlook.resolve_product(self.product())
            self._set_status(
                f"No {spec.label.lower()} covers {valid_time:%Y-%m-%d %H}Z "
                f"({spc_outlook.format_product_days(spec)}, 2020 onward)")
            return
        self._map.set_overlay(spc_outlook.OVERLAY_KEY, layer, visible=True)
        self._set_status(self._describe(layer))

    def _on_failed(self, token, valid_time, message) -> None:
        if token != self._token:
            return
        self._worker = None
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
            label: str = "Show radar mosaic",
            enabled: bool = False,
            opacity: float = 0.85,
    ) -> None:
        super().__init__(parent)
        self._map = map_widget
        self._token = 0
        self._worker: _RadarMosaicWorker | None = None

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
            "Draw the latest MRMS radar mosaic over the contiguous United "
            "States. Refreshes while it is switched on.")
        self._check.setChecked(bool(enabled))
        self._check.toggled.connect(self._on_toggled)
        layout.addWidget(self._check)

        # One product at a time, for the same reason the outlook controller
        # offers one hazard: these are opaque colour ramps over the same pixels,
        # so two of them stacked is unreadable rather than twice as informative.
        self._product = QComboBox()
        for spec in radar_mosaic.available_products():
            self._product.addItem(spec.label, spec.key)
        self._product.setCurrentIndex(
            max(0, self._product.findData(radar_mosaic.DEFAULT_PRODUCT)))
        self._product.currentIndexChanged.connect(self._on_product_changed)
        layout.addWidget(self._product)

        opacity_row = QHBoxLayout()
        self._opacity_label = opacity_label = QLabel("Opacity")
        opacity_label.setObjectName(OBJ_HINT)
        opacity_row.addWidget(opacity_label)
        self._opacity = QSlider(Qt.Horizontal)
        # Never fully transparent and never fully opaque: 0 would be an overlay
        # the user has turned on and cannot see, and 100 hides the coastlines
        # and state borders that make the image locatable at all.
        self._opacity.setRange(20, 95)
        self._opacity.setValue(
            int(round(min(0.95, max(0.20, float(opacity))) * 100.0)))
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
        return str(self._product.currentData()
                   or radar_mosaic.DEFAULT_PRODUCT)

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
        radar_mosaic.clear_cache()
        self._request()

    def shutdown(self) -> None:
        """Interrupt any in-flight fetch and wait briefly, for window close."""
        self._timer.stop()
        self._refresh_timer.stop()
        worker = self._worker
        self._worker = None
        if worker is None:
            return
        try:
            if not worker.isRunning():
                return
            worker.requestInterruption()
            # Interruption is only observed around the request, so a worker
            # inside a socket read runs to its own timeout. Bounded so a hung
            # request cannot hold the window open.
            worker.wait(_SHUTDOWN_WAIT_MS)
        except RuntimeError:
            pass

    # -- internals ----------------------------------------------------------- #
    def _spec(self):
        return radar_mosaic.get_product(self.product())

    def _sync_refresh_interval(self) -> None:
        """Point the refresh timer at the selected product's cadence."""
        cadence_ms = int(self._spec().update_interval_s * 1000.0)
        self._refresh_timer.setInterval(
            max(_RADAR_REFRESH_FLOOR_MS, cadence_ms))

    def _in_coverage(self) -> bool:
        """Report whether the map could display this product at all."""
        try:
            view = self._map.view_bounds()
        except AttributeError:
            # A map without the accessor cannot be interrogated; assume it can
            # see the product rather than silently refusing to ever draw.
            return True
        return radar_mosaic.covers(view)

    def _on_toggled(self, checked: bool) -> None:
        self._set_detail_visible(checked)
        if not checked:
            # Hide rather than detach, matching the outlook controller: the
            # frame and its decoded pixmap both stay, so switching back on is
            # instant and costs no request.
            self._timer.stop()
            self._refresh_timer.stop()
            self._token += 1  # orphan any in-flight result
            self._map.set_overlay_visible(radar_mosaic.OVERLAY_KEY, False)
            self._set_status("")
            return
        self._refresh_timer.start()
        raster = self._map.overlay(radar_mosaic.OVERLAY_KEY)
        if raster is not None and not raster.is_stale():
            self._map.set_overlay_visible(radar_mosaic.OVERLAY_KEY, True)
            self._set_status(self._describe(raster))
            return
        self._request()

    def _set_detail_visible(self, visible: bool) -> None:
        """Show or hide the controls that only apply to a drawn overlay."""
        for widget in (self._product, self._opacity_label, self._opacity):
            widget.setVisible(bool(visible))

    def _on_product_changed(self, *_args) -> None:
        """Switch product, discarding the frame the previous one produced.

        Detached unconditionally, including while switched off: leaving it
        attached meant re-enabling later found a frame that looked fresh and
        showed the previous product under the new product's name.
        """
        self._map.remove_overlay(radar_mosaic.OVERLAY_KEY)
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
        raster = self._map.overlay(radar_mosaic.OVERLAY_KEY)
        if raster is None:
            return
        self._map.set_overlay(
            radar_mosaic.OVERLAY_KEY, raster.at_opacity(self.opacity()))

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
            self._map.set_overlay_visible(radar_mosaic.OVERLAY_KEY, False)
            self._set_status(
                "Radar covers the contiguous United States; the map is "
                "currently outside it")
            return
        self._map.set_overlay_visible(radar_mosaic.OVERLAY_KEY, True)
        self._set_status("Loading radar\u2026")
        self._timer.start()

    def _start_fetch(self) -> None:
        if not self._check.isChecked() or not self._in_coverage():
            return
        self._token += 1
        token = self._token

        previous = self._worker
        self._worker = None
        if previous is not None:
            try:
                if previous.isRunning():
                    previous.requestInterruption()
            except RuntimeError:
                pass

        worker = _RadarMosaicWorker(token, parent=self, product=self.product(),
                                    opacity=self.opacity())
        worker.loaded.connect(self._on_loaded)
        worker.failed.connect(self._on_failed)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _on_loaded(self, token, raster) -> None:
        if token != self._token or not self._check.isChecked():
            return  # superseded, or switched off while in flight
        self._worker = None
        if raster is None:
            self._set_status("No radar frame was returned")
            return
        self._map.set_overlay(radar_mosaic.OVERLAY_KEY, raster, visible=True)
        self._set_status(self._describe(raster))

    def _on_failed(self, token, message) -> None:
        if token != self._token:
            return
        self._worker = None
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

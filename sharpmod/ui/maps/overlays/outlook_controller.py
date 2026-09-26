"""OutlookOverlayController overlay controls."""

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
    _SUPERSEDE_CHECK_MS,
    _SHUTDOWN_WAIT_MS,
    _WorkerFleet,
    _resolved_day,
    _product_item_text
)
from sharpmod.ui.maps.overlays import controllers as _controls


class OutlookOverlayController(QObject):
    """Keep one map's SPC outlook overlay in step with a selected valid time."""

    #: Emitted with a short human-readable state for the owning tab to show.
    statusChanged = Signal(str)

    #: Emitted whenever the layer's list row may have changed (T19.1).
    layerChanged = Signal()

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
        layout.setSpacing(SPACE["xs"])
        self._check = QCheckBox(label)
        self._check.setToolTip(
            "Draw the SPC convective outlook covering the selected valid time "
            "(2020 onward)"
        )
        self._check.setChecked(bool(enabled))
        self._check.toggled.connect(self._on_toggled)
        layout.addWidget(self._check)

        # Which hazard and how it went are this switch's settings, not further
        # overlays, so they are indented under its label. See ``dependent_panel``.
        # The list row refreshes through ``_on_toggled``'s own status update,
        # which keeps one emission path rather than two that could disagree.
        self._detail, detail = dependent_panel(self._content)
        layout.addWidget(self._detail)

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

        detail.addWidget(self._product)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setObjectName(OBJ_HINT)
        # Empty text means hidden -- the rule ``_set_status`` applies on every
        # later update. It was not applied to the initial state, so a blank label
        # held a 15px row open under a switch that was showing nothing.
        self._status.setVisible(False)
        detail.addWidget(self._status)

        # Which product to draw only means something once something is being
        # drawn, so the card collapses to its switch while the overlay is off.
        # It also keeps this card from spending rail height on a control that
        # cannot affect anything, which is what pushed the forecast panel past
        # a maximized window.
        #
        # The *panel* is what hides, not the combo inside it. Hiding the combo
        # individually here while ``_on_toggled`` reveals only the panel left the
        # product permanently hidden: switching the outlook on showed its status
        # line but no way to choose the day or hazard, so the categorical outlook
        # was the only one reachable.
        self._detail.setVisible(self._check.isChecked())

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

    # -- T19 layer contracts -------------------------------------------------- #
    def layer_state_text(self) -> str:
        """Return the status-line text behind the active-layer row (T19.1)."""
        try:
            return str(self._status.text() or "")
        except (AttributeError, RuntimeError):
            return ""

    def layer_time_text(self) -> str:
        """Return this layer's actual time text for the list (T19.1)."""
        try:
            layer = self._map.overlay(spc_outlook.OVERLAY_KEY)
        except (AttributeError, RuntimeError):
            return ""
        valid_from = getattr(layer, "valid_from", None)
        valid_to = getattr(layer, "valid_to", None)
        if valid_from is not None or valid_to is not None:
            start = valid_from.strftime("%d %b %HZ") if valid_from else "…"
            end = valid_to.strftime("%d %b %HZ") if valid_to else "…"
            return f"{start}–{end}"
        return str(getattr(layer, "subtitle", "") or "")

    def layer_error_text(self) -> str:
        """Return the failure text while a failure is showing, else ``""``."""
        text = self.layer_state_text()
        lowered = text.casefold()
        if "unavailable" in lowered or "failed" in lowered or "error" in lowered:
            return text
        return ""

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
        """Force a refetch, ignoring the cached frame."""
        if self._check.isChecked():
            self._signature = None
            self._request()

    def retry(self) -> None:
        """Retry this layer alone (T19.5): scoped, others untouched."""
        if not self._check.isChecked():
            return
        self._signature = None
        self._request()

    def retry_scoped(self) -> None:
        """Alias spelling the T19.5 scoped-retry contract by name."""
        self.retry()

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
        self._detail.setVisible(
            getattr(self, "_picker_open_key", None) == "spc_outlook"
            if getattr(self, "_picker_compact", False) else checked
        )
        if not checked:
            # Hide rather than detach. The geometry and its legend both go away,
            # but keeping the layer means re-enabling costs no request and no
            # worker thread at all -- detaching it forced a round trip back
            # through the fetch path just to recover something already in hand.
            # The cleared status still emits, so the active-layer list row
            # refreshes through the same path as every other state change.
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

        worker = _controls._SpcOutlookWorker(
            self._valid_time, token, parent=self, product=self.product()
        )
        worker.loaded.connect(self._on_loaded)
        worker.failed.connect(self._on_failed)
        self._workers.track(worker)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_loaded(self, token, valid_time, layer) -> None:
        # T21.4: a late response never overwrites a newer selection. The worker
        # carries the valid time it was asked for; anything that is not the
        # current one is dropped even when the token still matches.
        if valid_time != self._valid_time:
            return
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
        # T21.4: a late failure for an older time must not clear (or relabel)
        # the frame a newer selection has since attached.
        if valid_time != self._valid_time:
            return
        if token != self._token:
            return
        # Leave the signature unset so the next opportunity retries. A failure
        # is not an answer about what SPC holds.
        self._signature = None
        # Keep the previous successfully decoded layer.  Its own validity
        # window and the map's T21 match verdict label it as old/outside while
        # the explicit failure remains visible; blanking it would discard useful
        # context without making the failed request any more truthful.
        self._set_status(f"SPC outlook unavailable: {message}")

    @staticmethod
    def _describe(layer) -> str:
        parts = [layer.title]
        if layer.subtitle:
            parts.append(layer.subtitle)
        return "\n".join(parts)

    def _emit_layer_changed(self) -> None:
        try:
            self.layerChanged.emit()
        except (AttributeError, RuntimeError):
            pass

    def _set_status(self, text: str) -> None:
        self._status.setText(text)
        self._status.setVisible(bool(text))
        self.statusChanged.emit(text)
        self._emit_layer_changed()

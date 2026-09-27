"""HrrrFieldController overlay controls."""

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
    _FIELD_DEBOUNCE_MS,
    _FIELD_REFRESH_MS,
    _FIELD_MAX_LIVE_WORKERS,
    _SHUTDOWN_WAIT_MS,
    _WorkerFleet,
    _product_tooltip,
    _same_forecast_hour
)
from sharpmod.ui.maps.overlays import controllers as _controls


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

    #: Emitted whenever the layer's list row may have changed (T19.1).
    layerChanged = Signal()

    def __init__(
        self,
        map_widget,
        *,
        parent=None,
        label: str = "HRRR model field",
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
        from sharpmod.providers import hrrr_field, hrrr_products

        self._fields = hrrr_field
        self._catalogue = hrrr_products

        self._map = map_widget
        self._token = 0
        self._workers = _WorkerFleet()
        self._queued_fetch = False
        self._shutting_down = False
        self._state = "off"
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
        layout.setSpacing(SPACE["xs"])

        self._check = QCheckBox(label)
        self._check.setToolTip(
            "Draw an HRRR forecast field over the contiguous United States, "
            "beneath the SPC outlook and any radar. One field at a time."
        )
        self._check.setChecked(bool(enabled))
        self._check.toggled.connect(self._on_toggled)
        layout.addWidget(self._check)

        # Category, field, and opacity are this switch's settings. See
        # ``dependent_panel``.
        self._detail, detail = dependent_panel(self._content)
        layout.addWidget(self._detail)

        wanted = self._catalogue.get_product(product)

        self._category = QComboBox()
        for name, _members in self._catalogue.products_by_category():
            self._category.addItem(name, name)
        self._category.setToolTip("Which group of fields to choose from")
        detail.addWidget(self._category)

        self._product = QComboBox()
        self._product.setToolTip("The field to draw")
        detail.addWidget(self._product)

        # Populate before connecting, so building the initial list cannot look
        # like a user selection and fire a fetch for the wrong product.
        self._category.setCurrentIndex(max(0, self._category.findData(wanted.category)))
        self._reload_products(wanted.key)
        self._category.currentIndexChanged.connect(self._on_category_changed)
        self._product.currentIndexChanged.connect(self._on_product_changed)

        from sharpmod.ui.features.gui_selectors import bind_product_search

        detail.addWidget(bind_product_search(self._product, self._choose_search_product))

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
        # T19.2: numeric readout plus reset, matching the radar row -- an exact
        # value by keyboard, and a way back after experimenting.
        from qtpy.QtWidgets import QSpinBox as _FieldSpin
        from qtpy.QtWidgets import QToolButton as _FieldReset

        self._opacity_spin = _FieldSpin()
        self._opacity_spin.setRange(20, 95)
        self._opacity_spin.setSuffix("%")
        self._opacity_spin.setValue(self._opacity.value())
        self._opacity_spin.setToolTip("Model-field opacity, in percent")
        self._opacity_spin.setAccessibleName("Model field opacity percent")
        self._opacity_spin.valueChanged.connect(self._on_opacity_spin_changed)
        self._opacity.valueChanged.connect(self._sync_opacity_spin)
        opacity_row.addWidget(self._opacity_spin)
        self._opacity_reset = _FieldReset()
        self._opacity_reset.setText("Reset")
        self._opacity_reset.setToolTip("Return model-field opacity to 75%")
        self._opacity_reset.setAccessibleName("Reset model field opacity")
        self._opacity_reset.clicked.connect(self.reset_opacity)
        opacity_row.addWidget(self._opacity_reset)
        detail.addLayout(opacity_row)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setObjectName(OBJ_HINT)
        # Empty text means hidden -- the rule ``_set_status`` applies on every
        # later update. It was not applied to the initial state, so a blank label
        # held a 15px row open under a switch that was showing nothing.
        self._status.setVisible(False)
        detail.addWidget(self._status)
        self._cancel_button = QToolButton()
        self._cancel_button.setText("Cancel field request")
        self._cancel_button.setAccessibleName("Cancel HRRR field request")
        self._cancel_button.setToolTip(
            "Stop this field request; keep any previously drawn layers")
        self._cancel_button.setVisible(False)
        self._cancel_button.clicked.connect(self.cancel)
        detail.addWidget(self._cancel_button)

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

    # -- T19 layer contracts -------------------------------------------------- #
    def layer_state_text(self) -> str:
        """Return the status-line text behind the active-layer row (T19.1)."""
        try:
            return str(self._status.text() or "")
        except (AttributeError, RuntimeError):
            return ""

    def layer_time_text(self) -> str:
        """Return this layer's run/valid text for the list (T19.1)."""
        try:
            raster = self._map.overlay(self._fields.OVERLAY_KEY)
        except (AttributeError, RuntimeError):
            return ""
        return str(getattr(raster, "subtitle", "") or "")

    def layer_state(self) -> str:
        """Exact field state for map rows and four-panel recovery controls."""
        return self._state

    def layer_error_text(self) -> str:
        """Return the failure text while a failure is showing, else ``""``."""
        return self.layer_state_text() if self._state in (
            "offline", "failed", "no-data") else ""

    def set_opacity_value(self, value: float) -> None:
        """Set field opacity through the slider band (T19.2).

        Re-wraps the attached frame at the new value, matching what a slider
        drag does, so session restores and presets depict the remembered
        opacity instead of leaving a stale frame at the old one.
        """
        from sharpmod.maps.map_layers import normalise_opacity

        self._opacity.setValue(int(round(normalise_opacity(value) * 100.0)))
        try:
            raster = self._map.overlay(self._fields.OVERLAY_KEY)
        except (AttributeError, RuntimeError):
            return
        if raster is not None:
            self._map.set_overlay(
                self._fields.OVERLAY_KEY, raster.at_opacity(self.opacity()))

    def reset_opacity(self) -> None:
        """Return field opacity to the reset value (T19.2)."""
        from sharpmod.maps.map_layers import opacity_reset_value

        self._opacity.setValue(int(round(opacity_reset_value() * 100.0)))

    def retry(self) -> None:
        """Retry this layer alone (T19.5): scoped, others untouched."""
        if not self._check.isChecked():
            return
        self._fields.invalidate_frame(
            self.product(), valid_time=self._valid_time,
            run=self._run, fxx=self._fxx, failed_only=True)
        self._request()

    def cancel(self) -> None:
        """Cancel only the current request, leaving cached imagery and peers."""
        if not self._check.isChecked() or self._state != "loading":
            return
        self._timer.stop()
        self._queued_fetch = False
        self._token += 1
        self._workers.interrupt()
        self._set_status("Field request canceled; Retry to request it again",
                         state="canceled")

    def retry_scoped(self) -> None:
        """Alias spelling the T19.5 scoped-retry contract by name."""
        self.retry()

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

    def _choose_search_product(self, key: str) -> None:
        """Commit one confirmed catalogue choice, not a category's interim field."""
        if key not in self._catalogue.PRODUCTS or key == self.product():
            return
        spec = self._catalogue.PRODUCTS[key]
        blocked = self._category.blockSignals(True)
        try:
            self._category.setCurrentIndex(self._category.findData(spec.category))
        finally:
            self._category.blockSignals(blocked)
        self._reload_products(key)
        self._on_product_changed()

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
        """Refresh this selected frame, not every panel's field/cache."""
        if not self._check.isChecked():
            return
        self._fields.invalidate_frame(
            self.product(), valid_time=self._valid_time,
            run=self._run, fxx=self._fxx)
        self._request()

    def on_view_settled(self) -> None:
        """Recover promptly when an enabled field re-enters HRRR coverage."""
        if not self._check.isChecked():
            return
        if not self._in_coverage():
            self._request()
            return
        raster = self._map.overlay(self._fields.OVERLAY_KEY)
        if self._raster_matches_request(raster):
            self._map.set_overlay_visible(self._fields.OVERLAY_KEY, True)
            if self._state not in (
                    "loading", "offline", "failed", "no-data", "canceled"):
                self._set_status(self._describe(raster), state="available")
            return
        self._request()

    def shutdown(self) -> None:
        """Interrupt any in-flight fetch and wait briefly, for window close."""
        self._shutting_down = True
        self._timer.stop()
        self._refresh_timer.stop()
        self._queued_fetch = False
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

    def _raster_matches_request(self, raster) -> bool:
        """Whether ``raster`` is the exact immutable grid currently selected.

        ``is_stale`` is deliberately not used: its age rule is right for radar
        but makes every archived HRRR frame stale merely because its valid time
        is old.  A model grid never changes after publication; product and
        run/hour identity are the correct reuse keys.
        """
        if raster is None or raster.short_name != self.product():
            return False
        expected = None
        run = None
        fxx = None
        try:
            if self._run is not None and self._fxx is not None:
                run, fxx = self._run, int(self._fxx)
                expected = run + timedelta(hours=fxx)
            elif self._valid_time is not None:
                run, fxx = self._fields.resolve_request(self._valid_time)
                expected = run + timedelta(hours=int(fxx))
        except (AttributeError, OverflowError, TypeError, ValueError):
            return False
        if expected is None or getattr(raster, "valid_time", None) != expected:
            return False
        # Valid time alone cannot distinguish two cycles that reach the same
        # hour. Older/test rasters without provenance keep the legacy match;
        # fetched frames always carry their exact GRIB source URL.
        source = str(getattr(raster, "source_url", "") or "")
        if source:
            spec = self._catalogue.get_product(self.product())
            return source == self._fields.grib_url(run, fxx, spec.sources[0])
        return True

    def _set_detail_visible(self, visible: bool) -> None:
        # The panel goes too, not just its contents. A visible container holding
        # only hidden children is zero-height but still takes the layout's spacing
        # above it, so an off switch left a gap the size of a control it was not
        # showing -- which is what made the card's row rhythm uneven.
        if getattr(self, "_picker_compact", False):
            visible = getattr(self, "_picker_open_key", None) == "hrrr_field"
        self._detail.setVisible(bool(visible))
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
            self._queued_fetch = False
            self._workers.interrupt()
            self._map.set_overlay_visible(self._fields.OVERLAY_KEY, False)
            self._set_status("", state="off")
            return
        self._refresh_timer.start()
        raster = self._map.overlay(self._fields.OVERLAY_KEY)
        if self._raster_matches_request(raster):
            self._map.set_overlay_visible(self._fields.OVERLAY_KEY, True)
            self._set_status(self._describe(raster), state="available")
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
        self._notify_inspection_source()
        if not self._check.isChecked():
            return
        self._request()

    def _notify_inspection_source(self) -> None:
        """Tell the owning tab the depicted field changed (T18.4)."""
        owner = self.parent()
        sync = getattr(owner, "_sync_inspection_source", None)
        if not callable(sync):
            return
        try:
            if getattr(owner, "_model_field", None) is self:
                sync("_model_map")
            elif getattr(owner, "_map_field", None) is self:
                sync("_map")
            else:
                sync()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass

    def _on_opacity_changed(self, *_args) -> None:
        """Re-wrap the attached image at the new opacity, with no request."""
        raster = self._map.overlay(self._fields.OVERLAY_KEY)
        if raster is None:
            return
        self._map.set_overlay(
            self._fields.OVERLAY_KEY, raster.at_opacity(self.opacity())
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

    def _on_opacity_spin_changed(self, value: int) -> None:
        """Drive the slider from the numeric readout (T19.2)."""
        if self._opacity.value() != int(value):
            self._opacity.setValue(int(value))

    def _on_refresh_tick(self) -> None:
        if not self._check.isChecked():
            self._refresh_timer.stop()
            return
        self._request()

    def _request(self) -> None:
        if self._shutting_down:
            return
        self._timer.stop()
        # Supersede the old run/product *now*, not when the debounce fires.
        # Two cycles can share a valid hour, so checking only the valid-time
        # carried by an old result leaves a window for the wrong run to land.
        self._token += 1
        self._queued_fetch = False
        self._workers.interrupt()
        if not self._in_coverage():
            self._map.set_overlay_visible(self._fields.OVERLAY_KEY, False)
            self._set_status(
                "HRRR covers the contiguous United States; the map is "
                "currently outside it", state="outside-domain"
            )
            return
        self._map.set_overlay_visible(self._fields.OVERLAY_KEY, True)
        self._set_status(
            "Loading %s\u2026" % self._catalogue.get_product(self.product()).label,
            state="loading"
        )
        self._timer.start()

    def _start_fetch(self) -> None:
        if self._shutting_down or not self._check.isChecked() \
                or not self._in_coverage():
            return
        self._token += 1
        token = self._token
        self._workers.interrupt()

        if self._workers.live_count() >= _FIELD_MAX_LIVE_WORKERS:
            # The next finished worker releases one slot. Only the most recent
            # request is reconstructed then; no obsolete run keeps a worker.
            self._queued_fetch = True
            self._set_status("Waiting for the previous field request to stop…",
                             state="loading")
            return
        self._queued_fetch = False

        worker = _controls._HrrrFieldWorker(
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
        worker.finished.connect(self._resume_queued_fetch)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _resume_queued_fetch(self) -> None:
        """Use one released worker slot for the latest requested field only."""
        if self._shutting_down or not self._queued_fetch \
                or not self._check.isChecked():
            return
        if self._workers.live_count() >= _FIELD_MAX_LIVE_WORKERS:
            return
        self._queued_fetch = False
        QTimer.singleShot(0, self._start_fetch)

    def _on_loaded(self, token, _valid_time, raster) -> None:
        # T21.4: a late response never overwrites a newer selection. The
        # worker carries the valid time it was asked for; anything that is
        # not the current one is dropped even when the token still matches.
        if _valid_time is not None and self._valid_time is not None \
                and _valid_time != self._valid_time:
            return
        if token != self._token or not self._check.isChecked():
            return  # superseded, or switched off while in flight
        if raster is None:
            self._set_status("No field was returned", state="no-data")
            return
        self._map.set_overlay(self._fields.OVERLAY_KEY, raster, visible=True)
        self._set_status(self._describe(raster), state="available")
        self._notify_inspection_source()

    def _on_failed(self, token, message, kind="failed") -> None:
        if token != self._token:
            return
        # The previous image is left attached for the same reason radar's is: it
        # carries its own age, and an ageing field plus a stated failure beats a
        # blank map.
        kind = kind if kind in ("offline", "no-data") else "failed"
        prefix = {
            "offline": "Field offline or provider unreachable",
            "no-data": "Field unavailable for this run/hour (no data)",
            "failed": "Field failed",
        }[kind]
        self._set_status(f"{prefix}: {message}", state=kind)

    def _describe(self, raster) -> str:
        spec = self._catalogue.get_product(raster.short_name or self.product())
        parts = [raster.title]
        if raster.subtitle:
            parts.append(raster.subtitle)
        if spec.caveat:
            parts.append(spec.caveat)
        return "\n".join(parts)

    def _set_status(self, text: str, *, state: str | None = None) -> None:
        if state is not None:
            self._state = state
        self._cancel_button.setVisible(self._state == "loading"
                                       and self._check.isChecked())
        self._status.setText(text)
        self._status.setVisible(bool(text))
        self.statusChanged.emit(text)
        try:
            self.layerChanged.emit()
        except (AttributeError, RuntimeError):
            pass

"""RadarOverlayController overlay controls."""

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
    _RADAR_REFRESH_FLOOR_MS,
    _RADAR_DEBOUNCE_MS,
    SCOPE_SITE,
    SCOPE_MOSAIC,
    SITE_AUTO,
    _SHUTDOWN_WAIT_MS,
    _WorkerFleet
)
from sharpmod.ui.maps.overlays import controllers as _controls


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

    #: Emitted whenever the layer's list row may have changed (T19.1).
    layerChanged = Signal()

    def __init__(
        self,
        map_widget,
        *,
        parent=None,
        label: str = "Radar",
        enabled: bool = False,
        opacity: float = 0.85,
        scope: str = SCOPE_SITE,
        site: str = SITE_AUTO,
    ) -> None:
        super().__init__(parent)
        self._map = map_widget
        self._token = 0
        self._map_mode = "live"
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
        layout.setSpacing(SPACE["xs"])

        self._check = QCheckBox(label)
        self._check.setToolTip(
            "Draw live radar over the map. Refreshes while it is switched on."
        )
        self._check.setChecked(bool(enabled))
        self._check.toggled.connect(self._on_toggled)
        layout.addWidget(self._check)

        # Scope, antenna, product, and opacity are all settings of this one
        # switch, so they are indented under its label rather than sitting flush
        # as four more peers in the card. See ``dependent_panel``.
        self._detail, detail = dependent_panel(self._content)
        layout.addWidget(self._detail)

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
        detail.addWidget(self._scope)

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

        self._reload_sites(prefer=site)
        completer = self._site.completer()
        if completer is not None:
            completer.setCaseSensitivity(Qt.CaseInsensitive)
            completer.setFilterMode(Qt.MatchContains)
        line_edit = self._site.lineEdit()
        if line_edit is not None:
            line_edit.setPlaceholderText("Nearest to map centre")
        self._site.currentIndexChanged.connect(self._on_site_changed)
        detail.addWidget(self._site)

        # One product at a time, for the same reason the outlook controller
        # offers one hazard: these are opaque colour ramps over the same pixels,
        # so two of them stacked is unreadable rather than twice as informative.
        # The T19 active-layer list restates this as an occlusion note rather
        # than leaving the top frame to win silently.
        self._product = QComboBox()
        self._product.currentIndexChanged.connect(self._on_product_changed)
        detail.addWidget(self._product)
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
        # T19.2: a numeric readout plus reset beside the slider, so an exact
        # value is reachable by keyboard and recoverable after experimenting.
        from qtpy.QtWidgets import QSpinBox as _SpinBox
        from qtpy.QtWidgets import QToolButton as _ToolButton

        self._opacity_spin = _SpinBox()
        self._opacity_spin.setRange(20, 95)
        self._opacity_spin.setSuffix("%")
        self._opacity_spin.setValue(self._opacity.value())
        self._opacity_spin.setToolTip("Radar opacity, in percent")
        self._opacity_spin.setAccessibleName("Radar opacity percent")
        self._opacity_spin.valueChanged.connect(self._on_opacity_spin_changed)
        self._opacity.valueChanged.connect(self._sync_opacity_spin)
        opacity_row.addWidget(self._opacity_spin)
        self._opacity_reset = _ToolButton()
        self._opacity_reset.setText("Reset")
        self._opacity_reset.setToolTip("Return radar opacity to 75%")
        self._opacity_reset.setAccessibleName("Reset radar opacity")
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
            raster = self._map.overlay(self._key())
        except (AttributeError, RuntimeError):
            return ""
        if raster is None:
            return ""
        try:
            from sharpmod.maps.map_overlays import format_age

            age = format_age(raster.age_seconds())
        except Exception:  # noqa: BLE001 - age text is advisory
            age = ""
        valid = getattr(raster, "valid_time", None)
        try:
            stamp = valid.strftime("%d %b %HZ") if valid is not None else ""
        except Exception:  # noqa: BLE001 - a bad stamp is not fatal
            stamp = ""
        return stamp or (f"live · {age}" if age else "live")

    def layer_error_text(self) -> str:
        """Return the failure text while a failure is showing, else ``""``."""
        text = self.layer_state_text()
        lowered = text.casefold()
        if "unavailable" in lowered or "failed" in lowered or "error" in lowered:
            return text
        return ""

    def set_opacity_value(self, value: float) -> None:
        """Set radar opacity through the slider band (T19.2).

        Re-wraps the attached frame at the new value, matching what a slider
        drag does, so session restores and presets depict the remembered
        opacity instead of leaving a stale frame at the old one.
        """
        from sharpmod.maps.map_layers import normalise_opacity

        self._opacity.setValue(int(round(normalise_opacity(value) * 100.0)))
        try:
            raster = self._map.overlay(self._key())
        except (AttributeError, RuntimeError):
            return
        if raster is not None:
            self._map.set_overlay(self._key(), raster.at_opacity(self.opacity()))

    def reset_opacity(self) -> None:
        """Return radar opacity to the reset value (T19.2)."""
        from sharpmod.maps.map_layers import opacity_reset_value

        self._opacity.setValue(int(round(opacity_reset_value() * 100.0)))

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
        if not self._check.isChecked() or self._map_mode == "history":
            return
        self._source().clear_cache()
        self._request()

    def retry(self) -> None:
        """Retry this layer alone (T19.5): scoped, others untouched."""
        if not self._check.isChecked() or self._map_mode == "history":
            return
        self._request()

    def retry_scoped(self) -> None:
        """Alias spelling the T19.5 scoped-retry contract by name."""
        self.retry()

    def set_map_mode(self, mode) -> None:
        """Suspend latest-only radar while a map is pinned to history (T21.4).

        The map owns the actual frame shelf; the controller owns network work.
        Stopping both timers and orphaning in-flight workers prevents a current
        response from continually refilling that shelf while the user examines
        a past hour.  Returning to Live resumes the normal publication cadence.
        """
        from sharpmod.maps.map_time import normalise_mode

        wanted = normalise_mode(mode)
        if wanted == self._map_mode:
            return
        self._map_mode = wanted
        if wanted == "history":
            self._timer.stop()
            self._refresh_timer.stop()
            self._token += 1
            self._workers.interrupt()
            if self._check.isChecked():
                self._set_status(
                    "Live radar hidden in historical view; return to Live to "
                    "resume updates"
                )
            return
        if not self._check.isChecked():
            return
        self._refresh_timer.start()
        raster = self._map.overlay(self._key())
        if raster is not None and not raster.is_stale():
            self._map.set_overlay_visible(self._key(), True)
            self._set_status(self._describe(raster))
            return
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
        if not self._check.isChecked() or self._map_mode == "history" \
                or self.scope() != SCOPE_SITE:
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
        """Return the selected :class:`~sharpmod.providers.radar_site.RadarSite`, or None."""
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
        if self._map_mode == "history":
            self._timer.stop()
            self._refresh_timer.stop()
            self._set_status(
                "Live radar hidden in historical view; return to Live to "
                "resume updates"
            )
            return
        self._refresh_timer.start()
        raster = self._map.overlay(self._key())
        if raster is not None and not raster.is_stale():
            self._map.set_overlay_visible(self._key(), True)
            self._set_status(self._describe(raster))
            return
        self._request()

    def _set_detail_visible(self, visible: bool) -> None:
        """Show or hide the controls that only apply to a drawn overlay.

        The scope belongs in this set for the same reason the product and opacity
        do: which radar to composite is not a meaningful thing to choose for an
        image that is not being drawn. It was the one detail left permanently
        visible, so an unchecked "Radar" still carried a full-width "Nearest
        single site" combo underneath it -- the one control in the card that
        looked like it belonged to nothing.

        The panel is hidden with them: a visible container of hidden children is
        zero-height but still takes the layout's spacing above it.
        """
        if getattr(self, "_picker_compact", False):
            visible = getattr(self, "_picker_open_key", None) == "radar_site"
        self._detail.setVisible(bool(visible))
        for widget in (
            self._scope,
            self._product,
            self._opacity_label,
            self._opacity,
        ):
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
        """Fetch the next frame, or explain why one is not being fetched."""
        if not self._check.isChecked() or self._map_mode == "history":
            self._refresh_timer.stop()
            return
        self._request()

    def _request(self) -> None:
        self._timer.stop()
        if self._map_mode == "history":
            return
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
        if not self._check.isChecked() or self._map_mode == "history" \
                or not self._in_coverage():
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
            worker = _controls._RadarSiteWorker(
                token,
                parent=self,
                product=self.product(),
                view=self._view_bounds(),
                site_id=self.site() or None,
                opacity=self.opacity(),
            )
        else:
            worker = _controls._RadarMosaicWorker(
                token, parent=self, product=self.product(), opacity=self.opacity()
            )
        worker.loaded.connect(self._on_loaded)
        worker.failed.connect(self._on_failed)
        self._workers.track(worker)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_loaded(self, token, raster) -> None:
        if token != self._token or not self._check.isChecked() \
                or self._map_mode == "history":
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
        try:
            self.layerChanged.emit()
        except (AttributeError, RuntimeError):
            pass

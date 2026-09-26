"""Station map selection and shared map/overlay coordination."""

from __future__ import annotations

from datetime import datetime

from qtpy.QtWidgets import QMessageBox

from sharpmod.ui.features.gui_common import APP_NAME, _most_recent_synoptic, as_utc
from sharpmod.ui.picker.cycles import _select_cycle
from sharpmod.ui.picker.selection import refresh_selection_feedback


class MapSourceMixin:
    """Coordinate map selections, inspection, and overlay times."""

    def _map_on_radar_site(self, site_id: str) -> None:
        self._select_radar_site("_map_radar", site_id)

    def _model_on_radar_site(self, site_id: str) -> None:
        self._select_radar_site("_model_radar", site_id)

    def _select_radar_site(self, attribute: str, site_id: str) -> None:
        """Point a tab's radar controller at the antenna clicked on its map.

        A map never reaches into a controller and a controller never listens to a
        map; both talk to this window instead, which is the arrangement every
        other overlay already uses.
        """
        controller = getattr(self, attribute, None)
        if controller is None or not site_id:
            return
        controller.set_site(site_id)

    def _map_on_view_settled(self) -> None:
        self._view_settled(
            "_map_radar", "_map_field", "_map_reports", "_map_context"
        )

    def _model_on_view_settled(self) -> None:
        self._view_settled(
            "_model_radar", "_model_field", "_model_reports", "_model_context"
        )

    def _sync_overlay_times(self, when, *attributes: str) -> None:
        """Point a tab's time-aware overlays at one valid time.

        Every one of these controllers refuses to fetch while its valid time is
        ``None`` -- an outlook, a storm report, and a satellite image are all
        answers to "at what time", so there is nothing to ask for without one.
        That makes this call the difference between an overlay that draws and an
        overlay whose switch does nothing, which is exactly what the field-panels
        tab shipped with: its overlays were wired to the maps and to the rail, and
        never told what hour to depict.

        Collected here, and used by both map tabs, so a third tab cannot repeat
        that by leaving one of the three controllers out of its own copy.
        """
        for attribute in attributes:
            controller = getattr(self, attribute, None)
            setter = getattr(controller, "set_valid_time", None)
            if setter is not None:
                setter(when)

    def _view_settled(self, *attributes: str) -> None:
        """Let a tab's view-dependent overlays catch up with a moved map.

        Relayed through this window for the same reason the site click is: the
        map does not know the controllers exist. Single-site radar chooses its
        antenna from the view, while HRRR fields use the same event to retry as
        soon as a view crosses back into their domain.
        """
        for attribute in attributes:
            controller = getattr(self, attribute, None)
            handler = getattr(controller, "on_view_settled", None)
            if handler is not None:
                handler()
        self._refresh_navigation_actions()

    def _inspection_map(self, attribute: str | None = None):
        """Return the point map inspection reads, preferring the active tab."""
        if attribute is not None:
            widget = getattr(self, attribute, None)
            if widget is not None and hasattr(widget, "sample_field_at"):
                return widget
        tabs = getattr(self, "_tabs", None)
        title = ""
        if tabs is not None:
            try:
                title = str(tabs.tabText(tabs.currentIndex()))
            except (AttributeError, RuntimeError, TypeError):
                title = ""
        order = ("_model_map", "_map") if title != "Station Map" else (
            "_map", "_model_map")
        for name in order:
            widget = getattr(self, name, None)
            if widget is not None and hasattr(widget, "sample_field_at"):
                return widget
        return None

    def _inspection_reference(self, attribute: str | None = None):
        """Return ``(product_key, run, fxx)`` for the map's field (T18.4)."""
        controller = None
        widget = None
        if attribute == "_model_map" or attribute is None:
            controller = getattr(self, "_model_field", None)
            widget = getattr(self, "_model_map", None)
        if controller is None and attribute in (None, "_map"):
            controller = getattr(self, "_map_field", None)
            widget = getattr(self, "_map", None)
        if controller is None or widget is None:
            return None
        try:
            product = controller.product()
            run = controller._run
            fxx = controller._fxx
            valid = controller._valid_time
        except AttributeError:
            return None
        if run is None:
            if valid is None:
                return None
            try:
                from sharpmod.providers import hrrr_field

                run, fxx = hrrr_field.resolve_request(valid)
            except Exception:  # noqa: BLE001 - inspection stays unset
                return None
        if fxx is None:
            return None
        return (str(product), run, int(fxx))

    def _sync_inspection_source(self, attribute: str | None = None) -> None:
        """Point map inspection at the field the map depicts (T18.4)."""
        widget = self._inspection_map(attribute)
        if widget is None:
            return
        reference = self._inspection_reference(
            "_model_map" if widget is getattr(self, "_model_map", None)
            else "_map")
        if reference is None:
            try:
                widget.set_inspection_product(None)
            except (AttributeError, RuntimeError):
                pass
            return
        try:
            widget.set_inspection_product(reference[0], run=reference[1],
                                          fxx=reference[2])
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass

    def _refresh_inspection_ui(self, attribute: str | None = None) -> None:
        """Refresh inspection-dependent rail state after a point move (T18.3)."""
        if attribute in (None, "_model_map"):
            self._model_update_fetch_state()

    def _map_sync_overlay_times(self) -> None:
        """Point the observed tab's overlays at the selected sounding hour.

        There is no cycle selection here, only the hour of the observation, so
        the HRRR field follows the valid time: for a past hour that resolves to
        that hour's own analysis, which is the model's depiction of the same
        atmosphere the RAOB sampled. Inspection follows the same reference so
        the card always names the field the map depicts.
        """
        when = as_utc(self._map_when())
        self._sync_inspection_source("_map")
        if hasattr(self, "_map_outlook"):
            self._map_outlook.set_valid_time(when)
        field = getattr(self, "_map_field", None)
        if field is not None:
            field.set_valid_time(when)
        reports = getattr(self, "_map_reports", None)
        if reports is not None:
            reports.set_valid_time(when)
        context = getattr(self, "_map_context", None)
        if context is not None:
            context.set_valid_time(when)
        self._refresh_map_time_header("map")
        self._refresh_layer_list("map")

    def _map_set_recent(self) -> None:
        d, h = _most_recent_synoptic()
        self._map_date.setDate(d)
        _select_cycle(self._map_cycle, h)

    def _map_when(self) -> datetime:
        d = self._map_date.date()
        h = int(self._map_cycle.currentData())
        return datetime(d.year(), d.month(), d.day(), h, 0)

    def _map_on_select(self, sid: str, *, check_availability=True) -> None:
        self._map_selected_id = sid
        st = next((s for s in self._all_stations if s["id"] == sid), None)
        if st is not None:
            self._map_sel_lbl.setText(
                f"{st['id']} \u2014 {st['name']}\n({st['lat']:.2f}, {st['lon']:.2f})"
            )
        else:
            self._map_sel_lbl.setText(sid)
        self._map_gen_btn.setEnabled(
            not (self._worker is not None and self._worker.isRunning())
        )
        if check_availability:
            self._queue_availability(sid, self._map_when(), self._map_avail)
        refresh_selection_feedback(self, "map")

    def _map_recheck_availability(self) -> None:
        # Refresh the datetime-aware station set for the newly chosen cycle,
        # then re-probe availability for the current selection.
        self._refresh_station_catalog(self._map_when())
        self._queue_availability(
            self._map_selected_id, self._map_when(), self._map_avail
        )
        # The chosen cycle is also the overlays' valid time.
        self._map_sync_overlay_times()
        refresh_selection_feedback(self, "map")

    def _map_on_activate(self, sid: str) -> None:
        self._map_on_select(sid)
        self._map_generate()

    def _map_generate(self) -> None:
        if not self._map_selected_id:
            QMessageBox.warning(self, APP_NAME, "Click a station on the map first.")
            return
        self._start_fetch(self._map_selected_id, self._map_when())

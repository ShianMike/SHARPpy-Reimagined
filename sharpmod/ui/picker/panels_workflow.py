"""Field panel cycle, point, and sounding controls."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from qtpy.QtCore import Qt, QDate
from qtpy.QtWidgets import QMessageBox

from sharpmod.ui.features.gui_common import APP_NAME, SYNOPTIC_HOURS, _LOGGER, as_utc
from sharpmod.ui.styles.theme import OBJ_HINT, OBJ_STATUS
from sharpmod.ui.picker.cycles import (
    _annotate_cycle_combo, _fill_cycle_combo, _fxx_item_text,
    _newest_cycle_not_after,
)
from sharpmod.ui.picker.selection import refresh_selection_feedback

HRRR_FIELD_MODEL_KEY = "hrrr"


class PanelsWorkflowMixin:
    """Coordinate field panel controls and map selection."""

    def _panels_config(self):
        """Return the HRRR model config the panels tab works against."""
        from sharpmod.tools import model_extract

        try:
            return model_extract.get_config(HRRR_FIELD_MODEL_KEY)
        except Exception:  # noqa: BLE001 - an absent catalogue is not fatal here
            _LOGGER.debug("panels.config_unavailable", exc_info=True)
            return None

    def _panels_on_view_settled(self) -> None:
        """Let the shared overlays catch up once the grid has settled."""
        self._view_settled("_panels_radar", "_panels_reports", "_panels_context")

    def _panels_on_radar_site(self, site_id: str) -> None:
        self._select_radar_site("_panels_radar", site_id)

    def _panels_count_changed(self, _index: int) -> None:
        count = self._panels_count_combo.currentData()
        view = getattr(self, "_panels_view", None)
        if view is None or count is None:
            return
        view.set_panel_count(int(count))
        self._rebuild_panels_tool_rows()
        self._remember_panel_layout()

    def _panels_preset_changed(self) -> None:
        """Apply the chosen named four-field combination (T24.4)."""
        combo = getattr(self, "_panels_preset_combo", None)
        view = getattr(self, "_panels_view", None)
        if combo is None or view is None:
            return
        key = combo.currentData()
        if not key:
            return
        try:
            resolved = view.apply_preset(str(key))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return
        try:
            self._settings.setValue("panels/preset", resolved)
            self._settings.sync()
        except Exception:  # noqa: BLE001 - a preference must not break the UI
            _LOGGER.debug("map_panels.preset_unsaved", exc_info=True)
        self._remember_panel_layout()
        self.statusBar().showMessage(
            f"Applied field preset {combo.currentText()}.", 5000)

    def _panels_area_changed(self, name: str) -> None:
        view = getattr(self, "_panels_view", None)
        if view is not None:
            view.set_area(name)

    def _rebuild_panels_tool_rows(self) -> None:
        """Point the Region tool/zoom rows at the last-clicked panel (T24.3)."""
        host = getattr(self, "_panels_tool_row_host", None)
        layout = getattr(self, "_panels_tool_layout", None)
        zoom_layout = getattr(self, "_panels_zoom_layout", None)
        view = getattr(self, "_panels_view", None)
        if host is None or layout is None or zoom_layout is None or view is None:
            return

        def clear_rows(row_layout) -> None:
            while row_layout.count():
                item = row_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
                nested = item.layout()
                if nested is not None:
                    clear_rows(nested)
                    nested.deleteLater()

        clear_rows(layout)
        clear_rows(zoom_layout)
        try:
            panels = [panel for panel in view._panels if panel.is_active()]
        except (AttributeError, RuntimeError):
            panels = []
        try:
            last = int(getattr(view, "_last_clicked", 0) or 0)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            last = 0
        active = None
        for panel in panels:
            if int(getattr(panel, "index", -1)) == last:
                active = panel
                break
        if active is None and panels:
            active = panels[0]
        if active is None:
            return
        from sharpmod.ui.picker.layout import map_tool_row, rail_zoom_row

        try:
            layout.addLayout(map_tool_row(active.map, allow_box=False))
            zoom_layout.addLayout(rail_zoom_row(active.map))
        except (AttributeError, RuntimeError, TypeError):
            pass

    def _panels_update_cycles(self) -> None:
        if not hasattr(self, "_panels_cycle"):
            return
        cfg = self._panels_config()
        cycles = cfg.cycles if cfg is not None else SYNOPTIC_HOURS
        now = datetime.now(timezone.utc)
        blocked = self._panels_cycle.blockSignals(True)
        try:
            _fill_cycle_combo(
                self._panels_cycle,
                cycles,
                _newest_cycle_not_after(cycles, now.hour),
                run_date=self._panels_date.date(),
            )
        finally:
            self._panels_cycle.blockSignals(blocked)
        view = getattr(self, "_panels_view", None)
        if view is not None and cfg is not None:
            # Outline but no caption. The Forecast Model tab labels its domain
            # because that map is where a point is judged in or out of the grid;
            # here the same words would be painted over two to four panels at once
            # and the rail already says when the point falls outside.
            view.set_domain(cfg.domain_bounds, "", outline=cfg.domain_outline)
        self._panels_update_fxx()

    def _panels_update_fxx(self) -> None:
        if not hasattr(self, "_panels_fxx_combo"):
            return
        cfg = self._panels_config()
        current = self._panels_fxx_combo.currentData()
        blocked = self._panels_fxx_combo.blockSignals(True)
        try:
            self._panels_fxx_combo.clear()
            if cfg is not None:
                from sharpmod.tools import model_extract

                cycle = int(self._panels_cycle.currentData() or 0)
                run = self._panels_run_time()
                for hour in model_extract.forecast_hours(cfg, cycle_hour=cycle):
                    hour = int(hour)
                    valid = run + timedelta(hours=hour)
                    self._panels_fxx_combo.addItem(_fxx_item_text(hour, valid), hour)
                    self._panels_fxx_combo.setItemData(
                        self._panels_fxx_combo.count() - 1,
                        f"Forecast hour {hour}\nValid {valid:%a %d %b %Y %H}Z",
                        Qt.ToolTipRole,
                    )
                index = self._panels_fxx_combo.findData(current)
                if index < 0:
                    index = self._panels_fxx_combo.findData(cfg.default_fxx)
                self._panels_fxx_combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self._panels_fxx_combo.blockSignals(blocked)
        self._panels_update_valid_label()
        self._refresh_frame_strip("panels")

    def _panels_run_time(self) -> datetime:
        d = self._panels_date.date()
        h = int(self._panels_cycle.currentData() or 0)
        return datetime(d.year(), d.month(), d.day(), h, 0, tzinfo=timezone.utc)

    def _panels_selected_fxx(self) -> int:
        return int(self._panels_fxx_combo.currentData() or 0)

    def _panels_set_recent(self) -> None:
        cfg = self._panels_config()
        cycles = tuple(sorted(cfg.cycles if cfg is not None else SYNOPTIC_HOURS))
        now = datetime.now(timezone.utc)
        day = now
        eligible = [hour for hour in cycles if hour <= now.hour]
        if eligible:
            hour = eligible[-1]
        else:
            day = now - timedelta(days=1)
            hour = cycles[-1] if cycles else 0
        self._panels_date.setDate(QDate(day.year, day.month, day.day))
        index = self._panels_cycle.findData(hour)
        if index >= 0:
            self._panels_cycle.setCurrentIndex(index)
        self._panels_update_valid_label()

    def _panels_update_valid_label(self) -> None:
        """Restate the run and valid time, and push both onto every panel."""
        if not hasattr(self, "_panels_valid_lbl"):
            return
        run = self._panels_run_time()
        fxx = self._panels_selected_fxx()
        valid = run + timedelta(hours=fxx)
        _annotate_cycle_combo(self._panels_cycle, self._panels_date.date())
        combo = self._panels_fxx_combo
        for index in range(combo.count()):
            hour = combo.itemData(index)
            if hour is None:
                continue
            entry_valid = run + timedelta(hours=int(hour))
            combo.setItemText(index, _fxx_item_text(int(hour), entry_valid))
        self._panels_valid_lbl.setText(
            f"Valid {valid:%a %d %b %H}Z · +{fxx}h"
        )
        self._panels_valid_lbl.setToolTip(
            f"Run {run:%a %d %b %H}Z → Valid {valid:%a %d %b %H}Z"
        )
        # The shared overlays follow the hour the panels depict, exactly as the
        # forecast-model tab's do. Without this the outlook switch turned on and
        # then drew nothing, because an outlook is a question about a time.
        self._sync_overlay_times(
            as_utc(valid), "_panels_outlook", "_panels_reports", "_panels_context"
        )
        view = getattr(self, "_panels_view", None)
        if view is not None:
            view.set_forecast_reference(as_utc(run), int(fxx))
        for panel in getattr(view, "_panels", ()):  # T18.4 per-panel provenance
            try:
                panel.map.set_inspection_product(
                    panel.product(), run=as_utc(run), fxx=int(fxx))
            except (AttributeError, RuntimeError, TypeError, ValueError):
                pass
        self._refresh_map_time_header("panels")
        self._refresh_frame_strip("panels")
        self._refresh_layer_list("panels")
        refresh_selection_feedback(self, "panels")

    def _panels_point_from_spins(self, center: bool = False) -> None:
        """Move the marker to the spin boxes' coordinates on every panel."""
        view = getattr(self, "_panels_view", None)
        if view is None or self._panels_syncing_point:
            return
        self._panels_syncing_point = True
        try:
            view.set_point(
                float(self._panels_lat.value()),
                float(self._panels_lon.value()),
                center=bool(center),
            )
        finally:
            self._panels_syncing_point = False
        # A map click reaches the context overlay through the view's own
        # ``pointSelected``; a typed coordinate has no such path, and without this
        # the nearby-observations area stayed where it was last clicked.
        context = getattr(self, "_panels_context", None)
        handler = getattr(context, "on_location_changed", None)
        if handler is not None:
            handler(float(self._panels_lat.value()), float(self._panels_lon.value()))
        self._panels_describe_point()

    def _panels_on_point(self, lat: float, lon: float) -> None:
        """Take a clicked point into the spin boxes without echoing it back."""
        if self._panels_syncing_point:
            return
        # Whose field travels onto a sounding taken here. Rebound per click
        # because this tab shows several at once; see ``TAB_FIELD_CONTROLLERS``.
        view = getattr(self, "_panels_view", None)
        if view is not None:
            self._panels_field = view.active_field_controller()
            self._rebuild_panels_tool_rows()
        self._panels_syncing_point = True
        try:
            for spin, value in (
                (self._panels_lat, float(lat)),
                (self._panels_lon, float(lon)),
            ):
                blocked = spin.blockSignals(True)
                try:
                    spin.setValue(value)
                finally:
                    spin.blockSignals(blocked)
        finally:
            self._panels_syncing_point = False
        self._panels_describe_point()

    def _panels_describe_point(self) -> None:
        """Say whether the marked point is somewhere HRRR can be sampled."""
        if not hasattr(self, "_panels_point_status"):
            return
        lat = float(self._panels_lat.value())
        lon = float(self._panels_lon.value())
        cfg = self._panels_config()
        inside = True
        if cfg is not None:
            from sharpmod.tools import model_extract

            try:
                inside = bool(model_extract.point_in_domain(cfg, lat, lon))
            except Exception:  # noqa: BLE001 - a coverage check is advisory
                inside = True
        label = self._panels_point_status
        if inside:
            label.setText(f"{lat:.4f}, {lon:.4f}")
            label.setObjectName(OBJ_HINT)
        else:
            label.setText(f"{lat:.4f}, {lon:.4f} \u2014 outside the HRRR domain")
            label.setObjectName(OBJ_STATUS)
        # Qt does not re-evaluate style-sheet selectors when an object name
        # changes, so the pair is what actually moves the label between the two
        # rules rather than leaving it on whichever matched at construction.
        style = label.style()
        style.unpolish(label)
        style.polish(label)
        if hasattr(self, "_panels_fetch_btn"):
            busy = any(getattr(self, name, None) is not None for name in (
                "_model_worker", "_model_timeline_worker", "_model_compare_worker",
                "_box_extract_worker", "_box_analysis_worker", "_box_mean_worker",
            ))
            self._panels_fetch_btn.setEnabled(inside and not busy)
        view = getattr(self, "_panels_view", None)
        if view is not None:
            try:
                locked = bool(view.is_point_locked())
            except (AttributeError, RuntimeError):
                locked = False
            lock_btn = getattr(self, "_panels_lock_btn", None)
            if lock_btn is not None:
                if lock_btn.isChecked() != locked:
                    lock_btn.blockSignals(True)
                    try:
                        lock_btn.setChecked(locked)
                    finally:
                        lock_btn.blockSignals(False)
                lock_btn.setText("Unlock point" if locked else "Lock point")
            recent_btn = getattr(self, "_panels_recent_btn", None)
            if recent_btn is not None:
                try:
                    recent_btn.setEnabled(bool(view.has_recent_point()))
                except (AttributeError, RuntimeError):
                    recent_btn.setEnabled(False)
        refresh_selection_feedback(self, "panels")

    def _panels_lock_toggled(self, checked: bool) -> None:
        """Lock or unlock the sounding point on every panel (T24.3)."""
        view = getattr(self, "_panels_view", None)
        if view is not None:
            try:
                view.set_point_locked(bool(checked))
            except (AttributeError, RuntimeError):
                pass
        lock_btn = getattr(self, "_panels_lock_btn", None)
        if lock_btn is not None:
            lock_btn.setText("Unlock point" if checked else "Lock point")
        self._panels_describe_point()

    def _panels_restore_recent_point(self) -> None:
        """Return every panel to its reversible previous point (T24.3)."""
        view = getattr(self, "_panels_view", None)
        if view is None:
            self.statusBar().showMessage("No previous sounding point.", 4000)
            return
        try:
            restored = bool(view.restore_recent_point())
        except (AttributeError, RuntimeError):
            restored = False
        if not restored:
            self.statusBar().showMessage("No previous sounding point.", 4000)
            return
        try:
            lat, lon = view.maps()[0].context_point()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            self._panels_describe_point()
            return
        self._panels_syncing_point = True
        try:
            self._panels_lat.setValue(float(lat))
            self._panels_lon.setValue(float(lon))
        finally:
            self._panels_syncing_point = False
        self._panels_describe_point()

    def _panels_fetch_sounding(self) -> None:
        """Extract a sounding at the marked point, through the model tab's path.

        Delegated rather than reimplemented. ``_model_fetch`` already owns the
        runtime check, the in-flight guard, the disk cache, progress reporting,
        cancellation, and opening the result in a viewer; a second copy of that
        here would be a second set of those decisions to keep in agreement. So
        this tab's choices are written into the Forecast Model tab's controls and
        the one fetch is called.
        """
        if not hasattr(self, "_panels_lat"):
            return
        self._ensure_tab("Forecast Model")
        if not hasattr(self, "_model_lat"):
            QMessageBox.warning(
                self, APP_NAME, "The forecast model tab is unavailable."
            )
            return
        index = self._model_combo.findData(HRRR_FIELD_MODEL_KEY)
        if index < 0:
            QMessageBox.warning(
                self,
                APP_NAME,
                "HRRR is not selectable for this region, so a field-panel "
                "sounding cannot be extracted.",
            )
            return
        # Setting the model rebuilds the cycle and forecast lists, so it has to
        # happen before the run is written or the write would be discarded.
        if self._model_combo.currentIndex() != index:
            self._model_combo.setCurrentIndex(index)
        run = self._panels_run_time()
        self._model_date.setDate(QDate(run.year, run.month, run.day))
        cycle = self._model_cycle.findData(run.hour)
        if cycle >= 0:
            self._model_cycle.setCurrentIndex(cycle)
        fxx = self._model_fxx_combo.findData(self._panels_selected_fxx())
        if fxx >= 0:
            self._model_fxx_combo.setCurrentIndex(fxx)
        self._model_lat.setValue(float(self._panels_lat.value()))
        self._model_lon.setValue(float(self._panels_lon.value()))
        self._model_fetch()

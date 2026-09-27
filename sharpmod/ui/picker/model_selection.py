"""Forecast model, cycle, frame, point, and availability selection."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from qtpy.QtCore import Qt, QDate, QTimer

from sharpmod.ui.features.gui_common import SYNOPTIC_HOURS, _LOGGER, as_utc
from sharpmod.ui.features.gui_maps import MAP_AREAS
from sharpmod.ui.features.gui_workers import AVAIL_CHECKING, AVAIL_FALLBACK, AVAIL_UNKNOWN
from sharpmod.ui.styles.theme import OBJ_STATUS, OBJ_WARNING_TEXT
from sharpmod.ui.picker.cycles import (
    _annotate_cycle_combo, _fill_cycle_combo, _fxx_item_text,
    _newest_cycle_not_after,
)
from sharpmod.ui.picker.panels_workflow import HRRR_FIELD_MODEL_KEY
from sharpmod.ui.picker.selection import refresh_selection_feedback


class ModelSelectionMixin:
    """Maintain the selected forecast run, frame, point, and availability."""

    def _model_unsupported_text(self) -> str:
        try:
            from sharpmod.tools import model_extract

            unsupported = model_extract.unsupported_models()
        except Exception:
            return ""
        names = ", ".join(sorted(unsupported))
        return "Known but not selectable yet: " + names

    def _model_area_changed(self, name: str) -> None:
        if hasattr(self, "_model_map"):
            self._model_map.set_area(name)
        self._model_populate_models()

    def _model_populate_models(self) -> None:
        if not hasattr(self, "_model_combo"):
            return
        previous = self._model_combo.currentData()
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        try:
            from sharpmod.tools import model_extract

            area = (
                self._model_area_combo.currentText()
                if hasattr(self, "_model_area_combo")
                else "United States (CONUS)"
            )
            area_bounds = MAP_AREAS.get(area, MAP_AREAS["United States (CONUS)"])
            configs = []
            for cfg in model_extract.available_models():
                allowed = model_extract.domain_intersects_bounds(cfg, area_bounds)
                if allowed:
                    configs.append(cfg)
            for cfg in configs:
                self._model_combo.addItem(cfg.label, cfg.key)
            if not configs:
                self._model_combo.addItem("No public models for this region", "")
                self._model_combo.setEnabled(False)
            else:
                self._model_combo.setEnabled(True)
                idx = self._model_combo.findData(previous)
                self._model_combo.setCurrentIndex(idx if idx >= 0 else 0)
        except Exception:
            self._model_combo.addItem("Forecast models unavailable", "")
            self._model_combo.setEnabled(False)
        finally:
            self._model_combo.blockSignals(False)
        self._model_update_cycles()

    def _model_config(self):
        key = self._model_combo.currentData()
        if not key:
            return None
        from sharpmod.tools import model_extract

        return model_extract.get_config(key)

    def _set_model_summary(self, cfg) -> None:
        """Show the selected model's coverage and description, or neither.

        Each label is hidden when it has nothing to say, rather than being set to
        an empty string. An empty ``QLabel`` still occupies a full row, so the
        no-selection state used to hold blank space open under the combo.
        """
        domain = "" if cfg is None else f"Domain: {cfg.domain}"
        notes = "" if cfg is None else str(cfg.notes or "")
        for label, text in ((self._model_domain, domain), (self._model_notes, notes)):
            label.setText(text)
            label.setVisible(bool(text))

    def _model_update_cycles(self) -> None:
        if not hasattr(self, "_model_cycle") or not hasattr(self, "_model_notes"):
            return
        cfg = self._model_config()
        self._model_cycle.blockSignals(True)
        self._model_cycle.clear()
        if cfg is None:
            self._set_model_summary(None)
            self._model_cycle.blockSignals(False)
            self._model_update_fxx()
            self._model_update_fetch_state()
            return
        # Newest first, and the default is still the newest cycle that has come
        # round today -- unchanged behaviour, just chosen by hour instead of by
        # position now that position no longer tracks the clock.
        now = datetime.now(timezone.utc)
        _fill_cycle_combo(
            self._model_cycle,
            cfg.cycles,
            _newest_cycle_not_after(cfg.cycles, now.hour),
            run_date=self._model_date.date(),
        )
        self._model_cycle.blockSignals(False)

        self._set_model_summary(cfg)
        if hasattr(self, "_model_map"):
            self._model_map.set_domain(
                cfg.domain_bounds,
                f"{cfg.label} domain: {cfg.domain}",
                outline=cfg.domain_outline,
            )
        ensemble = cfg.key in {"gefs", "cfs"}
        self._model_member.setEnabled(ensemble)
        # Hidden rather than shown-but-disabled: a deterministic model has no
        # member, and an always-present dead field cost a card of rail height.
        if hasattr(self, "_model_member_box"):
            self._model_member_box.setVisible(ensemble)
        if ensemble:
            if cfg.key == "gefs":
                self._model_member.setPlaceholderText("default c00; e.g. p01")
            else:
                self._model_member.setPlaceholderText("default 1")
        else:
            self._model_member.clear()
            self._model_member.setPlaceholderText("deterministic")
        self._model_update_fxx()
        self._model_update_fetch_state()

    def _model_update_fxx(self) -> None:
        if not hasattr(self, "_model_fxx_combo"):
            return
        cfg = self._model_config()
        current = self._model_fxx_combo.currentData()
        self._model_fxx_combo.blockSignals(True)
        self._model_fxx_combo.clear()
        if cfg is not None:
            from sharpmod.tools import model_extract

            cycle = int(self._model_cycle.currentData() or 0)
            run = self._model_run_time()
            for hour in model_extract.forecast_hours(cfg, cycle_hour=cycle):
                hour = int(hour)
                valid = run + timedelta(hours=hour)
                self._model_fxx_combo.addItem(_fxx_item_text(hour, valid), hour)
                self._model_fxx_combo.setItemData(
                    self._model_fxx_combo.count() - 1,
                    f"Forecast hour {hour}\nValid {valid:%a %d %b %Y %H}Z",
                    Qt.ToolTipRole,
                )
            idx = self._model_fxx_combo.findData(current)
            if idx < 0:
                idx = self._model_fxx_combo.findData(cfg.default_fxx)
            self._model_fxx_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._model_fxx_combo.blockSignals(False)
        self._model_update_valid_label()
        self._refresh_frame_strip("model")

    def _frame_strip(self, tab: str):
        """Return the tab's frame scrubber strip, or ``None`` (T21.3)."""
        prefixes = {"model": "_model", "panels": "_panels"}
        prefix = prefixes.get(tab)
        if prefix is None:
            return None
        return getattr(self, f"{prefix}_frame_strip", None)

    def _refresh_frame_strip(self, tab: str) -> None:
        """Restate one tab's frame coverage strip (T21.3).

        The strip reuses the T09 :class:`FrameStrip` (same widget, same
        state colours and patterns, same click-to-jump), fed by a
        :class:`map_time.FrameCoverage` over the catalogue's offered hours
        for the current run -- so the scrubber and the sounding timeline
        never speak two clocks. Gridded fields have no per-hour fetch
        ledger: a catalogue hour is a depictable hour, and the strip's own
        tooltips name the valid time per cell. Never blocks the rail.
        """
        from sharpmod.ui.features.gui_timeline_playback import MapFramePlayback
        from sharpmod.analysis.timeline_frames import TimelineCoverage

        strip = self._frame_strip(tab)
        if strip is None or not isinstance(strip, MapFramePlayback):
            return
        config = (self._model_config() if tab == "model"
                  else self._panels_config())
        if config is None:
            return
        try:
            from sharpmod.tools import model_extract

            cycle = int((self._model_cycle.currentData()
                         if tab == "model"
                         else self._panels_cycle.currentData()) or 0)
            hours = [int(hour) for hour in model_extract.forecast_hours(
                config, cycle_hour=cycle)]
            run = self._model_run_time() if tab == "model" \
                else self._panels_run_time()
            current = self._model_selected_fxx() if tab == "model" \
                else self._panels_selected_fxx()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return
        try:
            # Start with catalogue-ready map hours.  An active/completed T09
            # request then overrides the hours it actually asked for, so the
            # map retains the full selectable playback range while speaking
            # exactly the same loading/missing/failed outcomes as the sounding
            # timeline for their shared subset.
            timeline = getattr(self, f"_{tab}_frame_coverage", None)
            ledger = TimelineCoverage.requested_range(hours, run_time=run)
            for hour in hours:
                valid = None
                try:
                    from datetime import timedelta as _delta

                    valid = run + _delta(hours=int(hour))
                except (AttributeError, RuntimeError, TypeError, ValueError):
                    valid = None
                ledger = ledger.with_state(hour, "loaded",
                                           valid_time=valid)
            if isinstance(timeline, TimelineCoverage) \
                    and timeline.run_time == run:
                offered = set(hours)
                for frame in timeline.frames:
                    if frame.forecast_hour not in offered:
                        continue
                    ledger = ledger.with_state(
                        frame.forecast_hour, frame.state,
                        valid_time=frame.valid_time, reason=frame.reason)
            strip.set_coverage(ledger, active_hour=int(current))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass

    def _set_map_frame_coverage(self, coverage) -> None:
        """Publish T09 acquisition states into the model map rail (T21.3)."""
        from sharpmod.analysis.timeline_frames import TimelineCoverage

        if not isinstance(coverage, TimelineCoverage):
            return
        self._model_frame_coverage = coverage
        strip = self._frame_strip("model")
        if strip is None:
            return
        try:
            run = self._model_run_time()
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return
        if coverage.run_time != run:
            return
        self._refresh_frame_strip("model")

    def _on_frame_chosen(self, tab: str, hour: int) -> None:
        """Step one tab to the scrubber's chosen forecast hour (T21.3)."""
        prefixes = {"model": ("_model_fxx_combo", "_model_update_valid_label"),
                    "panels": ("_panels_fxx_combo",
                               "_panels_update_valid_label")}
        combo_attr, updater = prefixes.get(tab, (None, None))
        if combo_attr is None:
            return
        combo = getattr(self, combo_attr, None)
        if combo is None:
            return
        try:
            index = combo.findData(int(hour))
        except (TypeError, ValueError, OverflowError):
            return
        if index is not None and index >= 0:
            combo.setCurrentIndex(index)
        else:
            try:
                getattr(self, updater)()
            except (AttributeError, RuntimeError, TypeError):
                pass

    def _on_model_frame_chosen(self, hour: int) -> None:
        self._on_frame_chosen("model", hour)

    def _on_panels_frame_chosen(self, hour: int) -> None:
        self._on_frame_chosen("panels", hour)

    def _model_run_time(self) -> datetime:
        d = self._model_date.date()
        h = int(self._model_cycle.currentData() or 0)
        return datetime(d.year(), d.month(), d.day(), h, 0, tzinfo=timezone.utc)

    def _model_selected_fxx(self) -> int:
        return int(self._model_fxx_combo.currentData() or 0)

    def _model_set_recent(self) -> None:
        cfg = self._model_config()
        cycles = tuple(sorted(cfg.cycles if cfg is not None else SYNOPTIC_HOURS))
        now = datetime.now(timezone.utc)
        day = now
        eligible = [h for h in cycles if h <= now.hour]
        if eligible:
            hour = eligible[-1]
        else:
            day = now - timedelta(days=1)
            hour = cycles[-1]
        self._model_date.setDate(QDate(day.year, day.month, day.day))
        idx = self._model_cycle.findData(hour)
        if idx >= 0:
            self._model_cycle.setCurrentIndex(idx)
        self._model_update_valid_label()

    def _model_relabel_time_items(self) -> None:
        """Re-derive what the cycle and forecast entries say about the run date.

        Both lists describe themselves in terms of a run the *date* control owns,
        so editing the date without this left every forecast entry advertising a
        valid time for the previous date -- the entries would have been confidently
        wrong rather than merely unhelpful.

        Text only: the data and the current index are untouched, so nothing here
        emits ``currentIndexChanged`` and this cannot re-enter its own caller.
        """
        _annotate_cycle_combo(self._model_cycle, self._model_date.date())
        combo = getattr(self, "_model_fxx_combo", None)
        if combo is None:
            return
        run = self._model_run_time()
        for index in range(combo.count()):
            hour = combo.itemData(index)
            if hour is None:
                continue
            valid = run + timedelta(hours=int(hour))
            combo.setItemText(index, _fxx_item_text(int(hour), valid))
            combo.setItemData(
                index,
                f"Forecast hour {int(hour)}\nValid {valid:%a %d %b %Y %H}Z",
                Qt.ToolTipRole,
            )

    def _model_update_valid_label(self) -> None:
        if not hasattr(self, "_model_valid_lbl"):
            return
        run = self._model_run_time()
        fxx = self._model_selected_fxx()
        valid = run + timedelta(hours=fxx)
        self._model_relabel_time_items()
        # The lead time is spelled out rather than left to be inferred from the
        # two timestamps, because that subtraction is the whole reason the pair is
        # shown together.
        self._model_valid_lbl.setText(
            f"Run {run:%a %d %b %H}Z  \u2192  Valid {valid:%a %d %b %H}Z"
            f"   (+{fxx} h)"
        )
        # The overlays track the forecast *valid* time, not the run time: a
        # sounding is compared against the outlook covering the hour it depicts.
        self._sync_overlay_times(
            as_utc(valid), "_model_outlook", "_model_reports", "_model_context"
        )
        self._model_sync_field_reference(run, fxx, valid)
        # T21: the map records the same run/hour for its painted header, so
        # the legend, the rail, and the export agree without re-deriving it.
        try:
            self._model_map.set_forecast_reference(as_utc(run), int(fxx))
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        self._refresh_map_time_header("model")
        self._refresh_frame_strip("model")
        self._refresh_layer_list("model")
        # The field-panels tab is deliberately *not* driven from here. It owns its
        # own date, cycle, and forecast hour so a comparison can be held on one
        # run while this tab is moved to another; pushing this tab's cycle across
        # would overwrite that choice every time either control was touched.
        if hasattr(self, "_model_availability"):
            self._queue_model_availability()
        if hasattr(self, "_model_point_status"):
            self._model_update_fetch_state()
        if hasattr(self, "_model_box_editor") and self._model_map.box() is not None:
            # Time/model changes retain the region. Re-planning is local and
            # keeps the selected source's preview current (T23.4).
            self._refresh_model_box_preview()
        refresh_selection_feedback(self, "model")

    def _model_sync_field_reference(
        self, run: datetime, fxx: int, valid: datetime
    ) -> None:
        """Point the HRRR field overlay at the selected cycle.

        The gridded overlay is HRRR, so it can be pinned to the very run and
        forecast hour on screen whenever HRRR is the selected product -- the map
        then shows the field the sounding will be cut from rather than a
        different run that happens to be valid at the same hour.

        For any other model the run has no meaning to HRRR (a 0.25-degree GFS
        F120 has no HRRR counterpart), so the overlay falls back to matching the
        valid time with the freshest HRRR run that reaches it. Both cases are
        stated in the overlay's own subtitle, so the map never implies the field
        and the sounding share a cycle when they do not.
        """
        field = getattr(self, "_model_field", None)
        if field is None:
            return
        cfg = self._model_config()
        if cfg is not None and cfg.key == HRRR_FIELD_MODEL_KEY:
            field.set_forecast_reference(as_utc(run), int(fxx))
        else:
            field.set_forecast_reference(None, None)
            field.set_valid_time(as_utc(valid))
        self._sync_inspection_source("_model_map")

    def _model_member_value(self) -> str | None:
        if not hasattr(self, "_model_member") or not self._model_member.isEnabled():
            return None
        return self._model_member.text().strip() or None

    def _queue_model_availability(self, *_args) -> None:
        """Debounce a catalog probe for the exact current picker selection."""
        if not hasattr(self, "_model_availability_timer") or not hasattr(
            self, "_model_availability"
        ):
            return
        self._model_availability_token += 1
        self._model_availability_timer.stop()
        self._model_availability_waiting_for_worker = False
        for worker in list(self._model_availability_workers):
            try:
                if worker.isRunning():
                    worker.requestInterruption()
            except RuntimeError:
                continue
        self._model_available_run = None
        self._model_use_available_btn.hide()
        cfg = self._model_config()
        if cfg is None or not hasattr(self, "_model_fxx_combo"):
            self._model_availability_request = None
            self._model_availability.set_status(AVAIL_UNKNOWN)
            return
        request = (
            cfg.key,
            self._model_run_time(),
            self._model_selected_fxx(),
            self._model_member_value(),
        )
        self._model_availability_request = request
        self._model_availability.set_status(
            AVAIL_CHECKING, "Checking selected and recent cycles\u2026"
        )
        self._model_availability_timer.start()

    def _on_model_availability_checked(
        self,
        token: int,
        model: str,
        run_time: datetime,
        fxx: int,
        member: str | None,
        status: str,
        message: str,
        available_run: datetime | None,
    ) -> None:
        request = (model, run_time, int(fxx), member or None)
        if (
            token != self._model_availability_token
            or request != self._model_availability_request
        ):
            _LOGGER.debug(
                "model_availability.stale token=%s current=%s request=%s",
                token,
                self._model_availability_token,
                request,
            )
            return
        self._model_availability.set_status(status, message)
        if status == AVAIL_FALLBACK and available_run is not None:
            self._model_available_run = available_run
            self._model_use_available_btn.setText(
                f"Use available cycle ({available_run:%Y-%m-%d %H}Z)"
            )
            self._model_use_available_btn.show()
        else:
            self._model_available_run = None
            self._model_use_available_btn.hide()

    def _on_model_availability_finished(self) -> None:
        worker = self.sender()
        try:
            self._model_availability_workers.remove(worker)
        except ValueError:
            pass
        worker.deleteLater()
        if (
            getattr(self, "_model_availability_waiting_for_worker", False)
            and self._model_availability_request is not None
            and not getattr(self, "_shutdown_started", False)
        ):
            self._model_availability_waiting_for_worker = False
            QTimer.singleShot(0, self._run_model_availability)

    def _use_model_available_run(self) -> None:
        """Apply the offered fallback only after the user explicitly opts in."""
        run_time = self._model_available_run
        if run_time is None:
            return
        self._model_date.setDate(QDate(run_time.year, run_time.month, run_time.day))
        index = self._model_cycle.findData(run_time.hour)
        if index >= 0:
            self._model_cycle.setCurrentIndex(index)

    def _model_point_from_spins(self, center: bool = False) -> None:
        if getattr(self, "_model_syncing_point", False):
            return
        if hasattr(self, "_model_map"):
            self._model_map.set_point(
                float(self._model_lat.value()),
                float(self._model_lon.value()),
                center=center,
            )
        self._model_update_fetch_state()

    def _model_on_map_point(self, lat: float, lon: float) -> None:
        self._model_syncing_point = True
        try:
            self._model_lat.setValue(float(lat))
            self._model_lon.setValue(float(lon))
        finally:
            self._model_syncing_point = False
        self._sync_inspection_source("_model_map")
        self._refresh_inspection_ui("_model_map")
        self._model_update_fetch_state()

    def _model_lock_toggled(self, checked: bool) -> None:
        """Lock or unlock the forecast sounding point (T18.1)."""
        if hasattr(self, "_model_map"):
            self._model_map.set_point_locked(bool(checked))
        action = "Unlock" if checked else "Lock"
        self._model_lock_btn.setText(f"{action} point")
        self._model_lock_btn.setAccessibleName(
            f"{action} sounding point")
        self._model_lock_btn.setToolTip(
            "The sounding point is locked; map clicks cannot move it. "
            "Inspect, pan, zoom, history, and box drawing still work. "
            "Click to unlock the point."
            if checked else
            "Prevent map clicks from moving the sounding point. "
            "Inspect, pan, zoom, history, and box drawing remain available."
        )
        self._model_update_fetch_state()

    def _model_restore_recent_point(self) -> None:
        """Return the forecast map to its reversible previous point (T18.1)."""
        widget = getattr(self, "_model_map", None)
        if widget is None or not widget.restore_recent_point():
            self.statusBar().showMessage("No previous sounding point.", 4000)
            return
        recent = widget.recent_point()
        if recent is None:
            self._refresh_inspection_ui("_model_map")
            self._model_update_fetch_state()
            return
        self._model_syncing_point = True
        try:
            self._model_lat.setValue(float(recent[0]))
            self._model_lon.setValue(float(recent[1]))
        finally:
            self._model_syncing_point = False
        self._model_update_fetch_state()

    def _model_point_ok(self) -> bool:
        cfg = self._model_config()
        if cfg is None:
            return False
        from sharpmod.tools import model_extract

        return model_extract.point_in_domain(
            cfg, self._model_lat.value(), self._model_lon.value()
        )

    def _model_update_fetch_state(self) -> None:
        if not hasattr(self, "_model_fetch_btn") or not hasattr(
            self, "_model_point_status"
        ):
            return
        cfg = self._model_config()
        busy = (
            self._model_worker is not None
            or getattr(self, "_model_timeline_worker", None) is not None
            or getattr(self, "_model_compare_worker", None) is not None
            or getattr(self, "_box_extract_worker", None) is not None
            or getattr(self, "_box_analysis_worker", None) is not None
            or getattr(self, "_box_mean_worker", None) is not None
        )
        if cfg is None:
            self._model_point_status.setText("")
            self._model_point_status.hide()
            self._model_fetch_btn.setEnabled(False)
            if hasattr(self, "_model_new_request_btn"):
                self._model_new_request_btn.setEnabled(False)
            if hasattr(self, "_model_box_extract_btn"):
                self._model_box_extract_btn.setEnabled(False)
            if hasattr(self, "_model_box_extract_action"):
                self._model_box_extract_action.setEnabled(False)
            refresh_selection_feedback(self, "model")
            return
        lat = float(self._model_lat.value())
        lon = float(self._model_lon.value())
        ok = self._model_point_ok()
        _LOGGER.debug(
            "model_fetch.ui_state model=%s point_ok=%s busy=%s lat=%.4f lon=%.4f",
            cfg.key,
            ok,
            busy,
            lat,
            lon,
        )
        if ok:
            self._model_point_status.setText(
                f"Selected {lat:.4f}, {lon:.4f} inside {cfg.domain}"
            )
            status_role = OBJ_STATUS
        else:
            self._model_point_status.setText(
                f"Selected {lat:.4f}, {lon:.4f} is outside {cfg.label} "
                f"{cfg.domain} coverage"
            )
            status_role = OBJ_WARNING_TEXT
        if self._model_point_status.objectName() != status_role:
            self._model_point_status.setObjectName(status_role)
            style = self._model_point_status.style()
            style.unpolish(self._model_point_status)
            style.polish(self._model_point_status)
        # The selection feedback in the Model card already explains an invalid
        # point, so a second coverage warning beside the coordinates adds noise.
        self._model_point_status.setVisible(ok)
        time_ready = (self._model_cycle.currentData() is not None
                      and self._model_fxx_combo.currentData() is not None)
        self._model_fetch_btn.setEnabled(ok and time_ready and not busy)
        if hasattr(self, "_model_new_request_btn"):
            self._model_new_request_btn.setEnabled(ok and time_ready and not busy)
        if hasattr(self, "_model_timeline_btn"):
            self._model_timeline_btn.setEnabled(ok and not busy)
        if hasattr(self, "_model_compare_btn"):
            self._model_compare_btn.setEnabled(ok and not busy)
        if hasattr(self, "_model_box_extract_btn"):
            self._model_box_extract_btn.setEnabled(
                getattr(self, "_box_preview_plan", None) is not None and not busy
            )
        if hasattr(self, "_model_box_extract_action"):
            self._model_box_extract_action.setEnabled(
                getattr(self, "_box_preview_plan", None) is not None and not busy
            )
        if hasattr(self, "_model_lock_btn") and hasattr(self, "_model_map"):
            locked = self._model_map.is_point_locked()
            if self._model_lock_btn.isChecked() != locked:
                self._model_lock_btn.blockSignals(True)
                try:
                    self._model_lock_btn.setChecked(locked)
                finally:
                    self._model_lock_btn.blockSignals(False)
            self._model_lock_btn.setText("Unlock point" if locked else "Lock point")
        if hasattr(self, "_model_recent_btn") and hasattr(self, "_model_map"):
            try:
                has_recent = bool(self._model_map.has_recent_point())
            except (AttributeError, RuntimeError):
                has_recent = False
            self._model_recent_btn.setEnabled(has_recent)
        refresh_selection_feedback(self, "model")

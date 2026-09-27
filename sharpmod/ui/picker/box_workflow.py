"""Model box editor, extraction, analysis, and worker lifecycle."""

from __future__ import annotations

import os
import shutil
import tempfile

from qtpy.QtCore import Qt, QTimer
from qtpy.QtWidgets import (
    QComboBox, QDialog, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QWidget,
)

from sharpmod.ui.features.gui_common import APP_NAME, _LOGGER, _render
from sharpmod.ui.features.gui_jobs import JobCounts
from sharpmod.ui.styles.theme import (
    CONTROL_H, OBJ_GHOST, OBJ_HINT, OBJ_NUMERIC, OBJ_PRIMARY, OBJ_STATUS, SPACE,
)
from sharpmod.ui.picker.layout import rail_card as _rail_card


class BoxWorkflowMixin:
    """Manage forecast area selection and box result windows."""

    def _build_model_box_editor(self) -> QWidget:
        """Build the persistent geographic-area editor (T23.1/T23.5)."""
        card, layout = _rail_card("Selected area")

        hint = QLabel(
            "Drag a handle to resize or the interior to move. Bounds and "
            "preview are planning-only; data is fetched only after Review & extract."
        )
        hint.setWordWrap(True)
        hint.setObjectName(OBJ_HINT)
        layout.addWidget(hint)

        self._model_box_saved_combo = QComboBox()
        self._model_box_saved_combo.setAccessibleName("Saved geographic areas")
        self._model_box_saved_combo.setToolTip(
            "Restore geographic bounds only. The selected model, run, hour, "
            "member, and output mode do not change."
        )
        self._model_box_saved_combo.activated.connect(
            self._model_restore_saved_box)
        layout.addWidget(self._model_box_saved_combo)

        self._model_box_name = QLineEdit()
        self._model_box_name.setPlaceholderText("area name for Save")
        self._model_box_name.setAccessibleName("Geographic area name")
        layout.addWidget(self._model_box_name)

        bounds = QGridLayout()
        bounds.setHorizontalSpacing(SPACE["sm"])
        bounds.setVerticalSpacing(SPACE["xs"])
        specs = (
            ("South", "_model_box_south", -90.0, 90.0),
            ("North", "_model_box_north", -90.0, 90.0),
            ("West", "_model_box_west", -180.0, 180.0),
            ("East", "_model_box_east", -180.0, 180.0),
        )
        for row, (label, attribute, minimum, maximum) in enumerate(specs):
            spin = QDoubleSpinBox()
            spin.setRange(minimum, maximum)
            spin.setDecimals(4)
            spin.setSingleStep(0.25)
            spin.setObjectName(OBJ_NUMERIC)
            spin.setAccessibleName(f"Area {label.lower()} bound")
            spin.setSuffix("°")
            setattr(self, attribute, spin)
            bounds.addWidget(QLabel(label), row, 0)
            bounds.addWidget(spin, row, 1)
        layout.addLayout(bounds)

        apply_row = QHBoxLayout()
        self._model_box_apply_btn = QPushButton("Apply bounds")
        self._model_box_apply_btn.clicked.connect(self._model_apply_box_bounds)
        apply_row.addWidget(self._model_box_apply_btn, 1)
        self._model_box_reset_btn = QPushButton("Reset")
        self._model_box_reset_btn.setObjectName(OBJ_GHOST)
        self._model_box_reset_btn.setToolTip(
            "Clear the selected area and its preview; saved areas are retained."
        )
        self._model_box_reset_btn.clicked.connect(self._model_reset_box)
        apply_row.addWidget(self._model_box_reset_btn, 1)
        layout.addLayout(apply_row)

        saved_row = QHBoxLayout()
        self._model_box_save_btn = QPushButton("Save")
        self._model_box_save_btn.clicked.connect(self._model_save_box)
        saved_row.addWidget(self._model_box_save_btn, 1)
        self._model_box_duplicate_btn = QPushButton("Duplicate")
        self._model_box_duplicate_btn.clicked.connect(self._model_duplicate_box)
        saved_row.addWidget(self._model_box_duplicate_btn, 1)
        self._model_box_remove_btn = QPushButton("Remove")
        self._model_box_remove_btn.setObjectName(OBJ_GHOST)
        self._model_box_remove_btn.clicked.connect(self._model_remove_saved_box)
        saved_row.addWidget(self._model_box_remove_btn, 1)
        layout.addLayout(saved_row)

        self._model_box_preview = QLabel("Enter bounds or drag on the map.")
        self._model_box_preview.setWordWrap(True)
        self._model_box_preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._model_box_preview.setObjectName(OBJ_STATUS)
        self._model_box_preview.setAccessibleName(
            "Geographic area sample and work estimate")
        layout.addWidget(self._model_box_preview)

        self._model_box_extract_btn = QPushButton("Review & extract…")
        self._model_box_extract_btn.setObjectName(OBJ_PRIMARY)
        self._model_box_extract_btn.setMinimumHeight(CONTROL_H["lg"])
        self._model_box_extract_btn.setToolTip(
            "Review the planned grid, forecast hour(s), and estimated work. "
            "Extraction starts only after confirming that review."
        )
        self._model_box_extract_btn.clicked.connect(self._model_extract_box)
        self._model_box_extract_btn.setEnabled(False)
        layout.addWidget(self._model_box_extract_btn)

        from sharpmod.state.saved_box_regions import SavedBoxRegionStore

        self._box_region_store = SavedBoxRegionStore(self._settings)
        self._saved_box_regions = {}
        self._box_preview_plan = None
        self._reload_model_box_regions()
        return card

    def _reload_model_box_regions(self, *, select: str = "") -> None:
        """Reload the source-neutral saved-area list into the editor."""
        combo = getattr(self, "_model_box_saved_combo", None)
        if combo is None:
            return
        try:
            regions = self._box_region_store.load()
        except Exception as exc:  # noqa: BLE001 - malformed preferences stay local
            regions = []
            if hasattr(self, "_model_box_preview"):
                self._model_box_preview.setText(
                    f"Saved areas could not be read: {exc}")
        self._saved_box_regions = {
            region.name.casefold(): region for region in regions
        }
        combo.blockSignals(True)
        try:
            combo.clear()
            combo.addItem("Unsaved area", "")
            for region in regions:
                combo.addItem(region.name, region.name)
            index = combo.findData(select) if select else 0
            combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            combo.blockSignals(False)

    def _current_model_box_region(self):
        """Return the map's selected geographic region, including wrap."""
        corners = self._model_map.box()
        if corners is None:
            return None
        from sharpmod.analysis.box_sounding import BoxRegion

        return BoxRegion.from_corners(*corners)

    def _sync_model_box_bounds(self, region) -> None:
        """Mirror one BoxRegion into the numeric controls without side effects."""
        raw_east = float(region.lon0 + region.lon_span)
        # Keep +180 as +180 in the numeric west/east convention. BoxRegion's
        # normalized lon1 is -180 there, which would turn a valid edge (or a
        # full-world -180..+180 box) into a different wrapped span on Apply.
        east = raw_east if raw_east <= 180.0 else raw_east - 360.0
        for widget, value in (
            (self._model_box_south, region.lat0),
            (self._model_box_north, region.lat1),
            (self._model_box_west, region.lon0),
            (self._model_box_east, east),
        ):
            widget.blockSignals(True)
            try:
                widget.setValue(float(value))
            finally:
                widget.blockSignals(False)

    def _seed_model_box_bounds(self) -> None:
        """Give numeric entry a useful point-centred starting rectangle."""
        from sharpmod.analysis.box_sounding import BoxRegion

        lat = float(self._model_lat.value())
        lon = float(self._model_lon.value())
        south, north = max(-90.0, lat - 1.0), min(90.0, lat + 1.0)
        region = BoxRegion.from_corners(south, lon - 1.5, north, lon + 1.5)
        self._sync_model_box_bounds(region)

    def _model_apply_box_bounds(self) -> None:
        """Commit the numeric south/west/north/east controls to the map."""
        from sharpmod.analysis.box_sounding import BoxSoundingError, region_from_bounds

        try:
            # ``west > east`` is the explicit antimeridian-crossing convention.
            region = region_from_bounds((
                self._model_box_west.value(),
                self._model_box_east.value(),
                self._model_box_south.value(),
                self._model_box_north.value(),
            ))
        except BoxSoundingError as exc:
            self._model_box_preview.setText(f"Invalid bounds: {exc}")
            return
        corners = (
            region.lat0,
            region.lon0,
            region.lat1,
            region.lon0 + region.lon_span,
        )
        self._model_map.set_box(corners)
        self._model_on_box_selected(*corners)

    def _model_reset_box(self) -> None:
        """Explicitly clear the active area while retaining saved regions."""
        self._model_map.clear_box()
        self._box_preview_plan = None
        self._seed_model_box_bounds()
        self._model_box_preview.setText(
            "No area selected. Bounds above are a draft; edit them and Apply "
            "or choose Draw area to drag on the map."
        )
        self._model_box_extract_btn.setEnabled(False)
        self._model_box_extract_action.hide()
        self._model_box_saved_combo.setCurrentIndex(0)
        self._model_box_name.clear()
        self._model_box_editor.show()
        self._reveal_model_box_editor()

    def _model_save_box(self) -> None:
        region = self._current_model_box_region()
        name = self._model_box_name.text().strip()
        if region is None:
            self._model_box_preview.setText("Draw or apply an area before saving it.")
            return
        if not name:
            self._model_box_preview.setText("Enter a name before saving this area.")
            self._model_box_name.setFocus()
            return
        try:
            saved = self._box_region_store.upsert(name, region)
        except Exception as exc:  # noqa: BLE001 - validation is reader-facing
            self._model_box_preview.setText(f"Area could not be saved: {exc}")
            return
        self._reload_model_box_regions(select=saved.name)
        self._model_box_name.setText(saved.name)
        self._refresh_model_box_preview()
        self.statusBar().showMessage(f"Saved area “{saved.name}”.", 5000)

    def _model_duplicate_box(self) -> None:
        region = self._current_model_box_region()
        if region is None:
            self._model_box_preview.setText(
                "Draw, apply, or restore an area before duplicating it.")
            return
        base = str(self._model_box_saved_combo.currentData() or "").strip()
        if not base:
            base = self._model_box_name.text().strip() or "Area"
        try:
            duplicate = self._box_region_store.duplicate(base, region=region)
        except Exception as exc:  # noqa: BLE001 - validation is reader-facing
            self._model_box_preview.setText(f"Area could not be duplicated: {exc}")
            return
        self._reload_model_box_regions(select=duplicate.name)
        self._model_box_name.setText(duplicate.name)
        self.statusBar().showMessage(
            f"Duplicated area as “{duplicate.name}”.", 5000)

    def _model_remove_saved_box(self) -> None:
        name = str(self._model_box_saved_combo.currentData() or "").strip()
        if not name:
            self._model_box_preview.setText("Choose a saved area to remove.")
            return
        try:
            removed = self._box_region_store.remove(name)
        except Exception as exc:  # noqa: BLE001 - preference errors stay local
            self._model_box_preview.setText(f"Saved area could not be removed: {exc}")
            return
        self._reload_model_box_regions()
        if removed:
            self.statusBar().showMessage(f"Removed saved area “{name}”.", 5000)

    def _model_restore_saved_box(self, index: int) -> None:
        """Restore bounds only; never mutate model/run/hour/member (T23.5)."""
        name = str(self._model_box_saved_combo.itemData(index) or "").strip()
        saved = self._saved_box_regions.get(name.casefold())
        if saved is None:
            return
        self._model_map.set_box(saved.map_corners)
        self._model_box_name.setText(saved.name)
        self._sync_model_box_bounds(saved.region)
        self._model_box_editor.show()
        self._refresh_model_box_preview()

    def _refresh_model_box_preview(self) -> None:
        """Plan coverage/work locally; never start a fetch (T23.3/T23.4)."""
        editor = getattr(self, "_model_box_editor", None)
        if editor is None:
            return
        try:
            region = self._current_model_box_region()
        except Exception as exc:  # noqa: BLE001 - map state remains recoverable
            self._box_preview_plan = None
            self._model_box_preview.setText(f"Invalid area: {exc}")
            self._model_box_extract_btn.setEnabled(False)
            return
        if region is None:
            self._box_preview_plan = None
            self._model_box_extract_btn.setEnabled(False)
            self._model_box_extract_action.hide()
            return
        self._model_box_extract_action.show()
        config = self._model_config()
        if config is None:
            self._box_preview_plan = None
            self._model_map.set_box_nodes((), "No model selected")
            self._model_box_preview.setText(
                "Area retained. Choose a model to preview its grid coverage.")
            self._model_box_extract_btn.setEnabled(False)
            return
        try:
            from sharpmod.analysis.box_sounding import plan_box_samples

            plan = plan_box_samples(config, region, target_points=64)
        except Exception as exc:  # noqa: BLE001 - preview reports domain issues
            self._box_preview_plan = None
            self._model_map.set_box_nodes((), "Outside selected model coverage")
            self._model_box_preview.setText(
                f"Preview unavailable for {config.label}: {exc}\n"
                "The area is retained; change its bounds or choose another model."
            )
            self._model_box_extract_btn.setEnabled(False)
            return
        self._box_preview_plan = plan
        inside = len(plan.requestable_points)
        coverage = 100.0 * inside / max(1, plan.count)
        transfers = plan.estimated_downloads
        self._model_map.set_box_nodes(
            plan.points,
            f"{inside}/{plan.count} in domain · {plan.spacing_km:.0f} km estimate",
        )
        self._model_box_preview.setText(
            f"Preview: {plan.rows} × {plan.cols} planned nodes; "
            f"{inside}/{plan.count} ({coverage:.0f}%) in {config.label} coverage.\n"
            f"Grid spacing {plan.spacing_km:.1f} km (native "
            f"{plan.native_spacing_km:.1f} km). Estimated work: {inside} sounding "
            f"decodes and about {transfers} model-hour data transfer"
            f"{'s' if transfers != 1 else ''}.\n"
            "Estimate only — availability, cache hits, retries, and duration "
            "are not guaranteed. Crosses mark planned nodes outside the domain."
        )
        busy = any(worker is not None for worker in (
            self._model_worker,
            self._model_timeline_worker,
            self._box_extract_worker,
            self._box_analysis_worker,
            self._box_mean_worker,
        ))
        self._model_box_extract_btn.setEnabled(not busy)
        self._model_box_extract_action.setEnabled(not busy)

    def _model_box_mode_toggled(self, checked: bool) -> None:
        """Turn sticky drawing/editing on without deleting committed bounds."""
        self._model_map.set_box_mode(bool(checked))
        self._model_sync_box_mode(bool(checked))

    def _model_sync_box_mode(self, checked: bool) -> None:
        """Keep the bottom action and map's B/V/I tool choices synchronized."""
        button = getattr(self, "_model_box_btn", None)
        if button is None:
            return
        if button.isChecked() != bool(checked):
            button.blockSignals(True)
            try:
                button.setChecked(bool(checked))
            finally:
                button.blockSignals(False)
        if checked:
            self._model_box_editor.show()
            if self._model_map.box() is None:
                self._seed_model_box_bounds()
            self._reveal_model_box_editor()
        elif self._model_map.box() is None:
            self._model_box_editor.hide()
        self.statusBar().showMessage(
            "Drag to draw; drag a handle to resize or the interior to move. "
            "Edits only update the preview. Middle/right-drag still pans."
            if checked
            else "",
            6000,
        )

    def _reveal_model_box_editor(self) -> None:
        """Bring the controls into the rail after Qt has laid out the card."""
        def reveal() -> None:
            try:
                if self._model_box_editor.isVisible():
                    self._model_controls_scroll.ensureWidgetVisible(
                        self._model_box_editor, 0, 12)
            except RuntimeError:  # pragma: no cover - window closed meanwhile
                pass

        QTimer.singleShot(0, reveal)

    def _disarm_box_mode(self) -> None:
        """Release box mode for pan/navigation, keeping the selected object.

        Called after extraction is explicitly confirmed and when Escape leaves
        the tool.  Tool state is deliberately independent from bounds state.
        """
        if not self._model_box_btn.isChecked():
            return
        self._model_box_btn.setChecked(False)

    def _model_on_box_cleared(self) -> None:
        """Keep the toolbar/editor in step when Escape or Reset clears bounds."""
        self._disarm_box_mode()
        if self._model_map.box() is not None:
            # Escape may cancel a replacement drag while leaving the previously
            # committed object intact. In that case only the tool is disarmed.
            self._model_box_editor.show()
            return
        self._box_preview_plan = None
        if hasattr(self, "_model_box_extract_btn"):
            self._model_box_extract_btn.setEnabled(False)
        if hasattr(self, "_model_box_extract_action"):
            self._model_box_extract_action.hide()
        if hasattr(self, "_model_box_preview"):
            self._model_box_preview.setText("Enter bounds or drag on the map.")
        if hasattr(self, "_model_box_editor"):
            self._model_box_editor.hide()

    def _model_on_box_selected(self, lat0, lon0, lat1, lon1) -> None:
        """Commit map bounds and refresh preview without fetching (T23.4)."""
        from sharpmod.analysis.box_sounding import BoxRegion, BoxSoundingError
        try:
            region = BoxRegion.from_corners(lat0, lon0, lat1, lon1)
        except BoxSoundingError as exc:
            self.statusBar().showMessage(str(exc), 6000)
            self._model_map.set_box(None)
            return
        if self._model_map.box() is None:
            self._model_map.set_box((
                region.lat0, region.lon0,
                region.lat1, region.lon0 + region.lon_span,
            ))
        self._sync_model_box_bounds(region)
        self._model_box_editor.show()
        self._refresh_model_box_preview()
        self._reveal_model_box_editor()

    def _model_extract_box(self) -> None:
        """Open the review gate and fetch only after its explicit confirmation."""
        from sharpmod.ui.features.gui_box import BoxPlanDialog

        config = self._model_config()
        try:
            region = self._current_model_box_region()
        except Exception as exc:  # noqa: BLE001 - invalid edits remain fixable
            self._model_box_preview.setText(f"Invalid area: {exc}")
            return
        if config is None or region is None:
            self._model_box_preview.setText(
                "Choose a model and draw or apply an area before extraction.")
            return
        if (
            self._box_extract_worker is not None
            or self._box_analysis_worker is not None
            or self._box_mean_worker is not None
        ):
            QMessageBox.information(
                self, APP_NAME, "A box sounding is already in progress.")
            return
        if self._model_worker is not None or self._model_timeline_worker is not None:
            QMessageBox.information(
                self, APP_NAME, "A model fetch is already in progress.")
            return

        available_hours = [
            self._model_fxx_combo.itemData(index)
            for index in range(self._model_fxx_combo.count())
        ]
        dialog = BoxPlanDialog(
            config.key,
            region,
            parent=self,
            available_hours=[int(hour) for hour in available_hours if hour is not None],
            start_hour=self._model_selected_fxx(),
        )
        if dialog.exec() != QDialog.Accepted:
            return
        plan = dialog.plan()
        if plan is None:
            return
        # Show exactly which points will be sampled before anything downloads.
        self._model_map.set_box_nodes(
            plan.points,
            f"{plan.rows} x {plan.cols} at {plan.spacing_km:.0f} km",
        )
        self._box_mode = dialog.mode()
        # Return left-drag to pan while work runs. The committed area, numeric
        # bounds, and preview remain available for the next hour/settings change.
        self._disarm_box_mode()
        self._start_box_extraction(
            plan, hours=dialog.hours(), mode=self._box_mode, fxx=dialog.fxx()
        )

    def _start_box_extraction(
        self,
        plan,
        *,
        hours=None,
        mode=None,
        fxx=None,
    ) -> None:
        from sharpmod.ui.features.gui_box import BoxExtractWorker

        if mode is not None:
            self._box_mode = str(mode)
        run_time = self._model_run_time()
        # The dialog's hour wins when it supplied one; it starts on the sidebar's
        # selection, so the sidebar is still the default rather than being
        # overridden by it.
        fxx = self._model_selected_fxx() if fxx is None else int(fxx)
        output_dir = tempfile.mkdtemp(prefix="sharpmod-box-")
        self._box_window_closed = False
        self._box_output_dir = output_dir
        disk_cache, _hour_cache = self._ensure_model_cache()
        worker = BoxExtractWorker(
            plan,
            run_time,
            fxx,
            output_dir,
            member=self._model_member_value(),
            loc=self._model_loc.text().strip() or None,
            disk_cache=disk_cache,
            hours=hours,
            parent=self,
        )
        self._box_sequence = int(getattr(self, "_box_sequence", 0)) + 1
        self._box_token = (id(self), self._box_sequence, run_time, fxx, output_dir)
        worker._sharpmod_box_token = self._box_token
        self._box_extract_worker = worker
        worker.point_failed.connect(self._on_box_point_failed)
        worker.progress.connect(self._on_box_progress)
        worker.result_ready.connect(self._on_box_extract_result)
        worker.failed.connect(self._on_box_extract_failed)
        worker.finished.connect(self._on_box_extract_finished)
        points = len(plan.requestable_points)
        total = points * (len(hours) if hours else 1)
        self._set_model_busy(True)
        self._model_job_scroll.show()
        self._model_job.begin(
            self._box_token, "Box sounding extraction",
            f"{plan.model_label} initialization {run_time:%Y-%m-%d %H}Z F{fxx:03d} · "
            f"{len(plan.requestable_points)} grid point(s)",
            "Extracting the saved box plan", total=total,
            unit="box soundings", cancellable=True,
        )
        self._model_job_retry = None
        self._model_progress.show()
        # Indeterminate until the first sounding lands: the download that comes
        # first has no per-point milestones to count.
        self._model_progress.setRange(0, 0)
        self._model_progress_detail.setText(
            f"downloading one {plan.model_label} model hour for {total} soundings\u2026"
        )
        self._model_progress_detail.show()
        if hours:
            self.statusBar().showMessage(
                f"Extracting {total} soundings across {len(hours)} "
                f"{plan.model_label} forecast hours "
                f"(F{hours[0]:03d}\u2013F{hours[-1]:03d})…"
            )
        elif self._box_mode == "mean":
            self.statusBar().showMessage(
                f"Averaging {points} {plan.model_label} F{fxx:03d} soundings "
                f"from one model hour into one sounding…"
            )
        else:
            self.statusBar().showMessage(
                f"Extracting {points} soundings from one "
                f"{plan.model_label} F{fxx:03d} model hour…"
            )
        worker.start()

    def _on_box_point_failed(self, row, col, message) -> None:
        if self.sender() is not self._box_extract_worker:
            return
        _LOGGER.info("box.point_failed row=%d col=%d error=%s", row, col, message)

    def _box_extract_current(self, worker):
        token = getattr(self, "_box_token", None)
        if worker is not self._box_extract_worker or token is None:
            return False
        if getattr(worker, "_sharpmod_box_token", None) != token:
            return False
        # Cancel must win over a queued terminal report: a snapshot for this
        # token that left running (cancelled, finished) rejects late reports.
        # No snapshot, or one for another token, keeps the legacy worker-
        # identity verdict already established above.
        snapshot = self._model_job.snapshot
        return not (
            snapshot is not None
            and snapshot.token == token
            and snapshot.state != "running"
        )

    def _on_box_progress(self, stage, done, total) -> None:
        worker = self.sender()
        token = getattr(self, "_box_token", None)
        if worker is not self._box_extract_worker or token is None:
            return
        if (
            getattr(worker, "_sharpmod_box_token", None) is not None
            and getattr(worker, "_sharpmod_box_token", None) != token
        ):
            return
        total = max(1, int(total))
        done = max(0, int(done))
        label = str(stage or "working").replace("_", " ")
        self._model_job.update(
            token, stage=f"{label} box soundings", done=min(done, total),
            total=total,
        )
        if done <= 0:
            # The transfer is a single bulk download with no per-point
            # milestones, so a 0-of-N bar sits perfectly still and reads as
            # nothing happening at all. An indeterminate bar says "working"
            # without claiming progress it cannot measure.
            self._model_progress.setRange(0, 0)
            self._model_progress_detail.setText(
                f"{label} one model hour for {total} soundings\u2026"
            )
        else:
            self._model_progress.setRange(0, total)
            self._model_progress.setValue(done)
            self._model_progress_detail.setText(
                f"{label} \u2014 {done}/{total} soundings"
            )
        self._model_progress.show()
        self._model_progress_detail.show()

    def _on_box_extract_failed(self, message) -> None:
        worker = self.sender()
        if not self._box_extract_current(worker):
            return
        token = self._box_token
        self._model_job.finish(
            token, counts=JobCounts(failed=1, requested=1), outcome="failed",
            message=str(message), retryable=False,
        )
        QMessageBox.critical(self, APP_NAME, str(message))

    def _on_box_extract_result(self, extraction) -> None:
        worker = self.sender()
        if not self._box_extract_current(worker):
            return
        token = self._box_token
        if not extraction.ok:
            self._model_job.finish(
                token, counts=JobCounts(failed=1, requested=1), outcome="failed",
                message="No sounding in that box could be extracted.",
                retryable=False,
            )
            QMessageBox.warning(
                self,
                APP_NAME,
                "No sounding in that box could be extracted. The run may not "
                "be published yet, or the area may fall outside the model's "
                "usable grid.",
            )
            return
        self._model_job.finish(
            token,
            counts=JobCounts(completed=extraction.completed, requested=max(
                extraction.completed + extraction.failed + extraction.cancelled, 1)),
            message=(
                f"Extracted {extraction.completed} box sounding(s); "
                f"{extraction.failed} failed, {extraction.cancelled} cancelled."
            ),
        )
        self._box_extraction = extraction
        if self._box_mode == "mean" and not extraction.sequence:
            self._start_box_mean(extraction)
            return
        window = self._ensure_box_window()
        window.set_extraction(extraction)
        if extraction.sequence:
            window.set_status(
                f"{extraction.completed} soundings across "
                f"{len(extraction.hours)} {extraction.plan.model_label} "
                f"forecast hours "
                f"(F{extraction.hours[0]:03d}\u2013"
                f"F{extraction.hours[-1]:03d})"
            )
        else:
            skipped = extraction.plan.count - extraction.completed
            window.set_status(
                f"{extraction.completed} soundings from one "
                f"{extraction.plan.model_label} download"
                + (f", {skipped} unavailable" if skipped > 0 else "")
            )
        window.show()
        window.raise_()
        from sharpmod.analysis.box_analysis import FAST_TIER

        self._start_box_analysis(extraction, (FAST_TIER,))

    def _on_box_extract_finished(self) -> None:
        worker = self.sender()
        if self._box_extract_worker is worker:
            self._box_extract_worker = None
            if self._box_analysis_worker is None and self._box_mean_worker is None:
                self._set_model_busy(False)
            self._cleanup_closed_box_output()
        try:
            worker.deleteLater()
        except RuntimeError:
            pass

    def _start_box_mean(self, extraction) -> None:
        """Average an extracted box and open the result as one sounding."""
        from sharpmod.ui.features.gui_box import BoxMeanWorker

        worker = BoxMeanWorker(
            extraction,
            loc=self._model_loc.text().strip() or None,
            parent=self,
        )
        self._box_mean_worker = worker
        worker.ready.connect(self._on_box_mean_ready)
        worker.failed.connect(self._on_box_mean_failed)
        worker.finished.connect(self._on_box_mean_finished)
        # Averaging and then building the parcel surface is one opaque stretch of
        # work, so the bar stays indeterminate rather than parking at 100% and
        # looking finished while the window has not opened yet.
        self._model_progress.show()
        self._model_progress.setRange(0, 0)
        self._model_progress_detail.setText(
            f"averaging {extraction.completed} soundings into one\u2026"
        )
        self._model_progress_detail.show()
        self.statusBar().showMessage(
            f"Averaging {extraction.completed} soundings into one sounding…"
        )
        worker.start()

    def _on_box_mean_failed(self, message) -> None:
        if self.sender() is not self._box_mean_worker:
            return
        _LOGGER.error("box.mean_failed message=%s", message)
        self.statusBar().showMessage("Box mean failed")
        QMessageBox.critical(self, APP_NAME, str(message))

    def _on_box_mean_finished(self) -> None:
        worker = self.sender()
        if self._box_mean_worker is worker:
            self._box_mean_worker = None
            if self._box_extract_worker is None and self._box_analysis_worker is None:
                self._set_model_busy(False)
                # Back to a determinate bar so the next fetch does not inherit
                # this one's indeterminate sweep.
                self._model_progress.setRange(0, 1)
                self._model_progress.reset()
                self._model_progress.hide()
                self._model_progress_detail.hide()
            self._cleanup_closed_box_output()
        worker.deleteLater()

    def _ensure_box_window(self):
        from sharpmod.ui.features.gui_box import BoxAnalysisWindow

        window = self._box_window
        if window is not None:
            try:
                window.objectName()
                return window
            except RuntimeError:
                self._box_window = None
        window = BoxAnalysisWindow()
        window.setAttribute(Qt.WA_DeleteOnClose, True)
        window.soundingRequested.connect(self._on_box_sounding_requested)
        window.compositesRequested.connect(self._on_box_composites_requested)
        window.destroyed.connect(self._on_box_window_destroyed)
        self._box_window = window
        self._box_window_closed = False
        # The workspace builds its field map with the window, so it adopts the
        # chosen view here the way every other lazily created map does. Routed
        # through the shared applier rather than the map directly, so the box
        # workspace cannot drift from the rest of the picker.
        self._apply_map_projection()
        return window

    def _on_box_window_destroyed(self, *_args) -> None:
        self._box_window = None
        self._box_window_closed = True
        # The extracted soundings only exist for this window, so they go with
        # it rather than accumulating in the temporary directory. An active
        # worker may still be reading them, so retain the directory until its
        # finished handler can remove it.
        self._box_extraction = None
        worker = self._box_analysis_worker
        if worker is not None:
            worker.requestInterruption()
        self._cleanup_closed_box_output()

    def _cleanup_closed_box_output(self) -> None:
        """Remove deferred box scratch data after a dismissed run is idle."""
        if (
            self._box_window is not None
            or self._box_extract_worker is not None
            or self._box_analysis_worker is not None
            or self._box_mean_worker is not None
        ):
            return
        output_dir = self._box_output_dir
        self._box_output_dir = None
        self._box_extraction = None
        if output_dir:
            shutil.rmtree(output_dir, ignore_errors=True)

    def _start_box_analysis(self, extraction, tiers) -> None:
        from sharpmod.ui.features.gui_box import BoxAnalysisWorker

        if self._box_analysis_worker is not None:
            return
        worker = BoxAnalysisWorker(extraction, tiers=tiers, parent=self)
        worker._sharpmod_box_token = getattr(self, "_box_token", None)
        self._box_analysis_worker = worker
        worker.progress.connect(self._on_box_analysis_progress)
        worker.ready.connect(self._on_box_analysis_ready)
        worker.failed.connect(self._on_box_analysis_failed)
        worker.finished.connect(self._on_box_analysis_finished)
        worker.start()

    def _on_box_analysis_progress(self, done, total) -> None:
        if self.sender() is not self._box_analysis_worker or self._box_window is None:
            return
        self._box_window.set_progress(int(done), int(total))

    def _on_box_analysis_ready(self, analysis) -> None:
        worker = self.sender()
        if worker is not self._box_analysis_worker:
            return
        if getattr(worker, "_sharpmod_box_token", None) != getattr(self, "_box_token", None):
            return
        if self._box_window_closed:
            # The user dismissed this run while analysis was active. Do not
            # resurrect its workspace when a pending worker result arrives.
            return
        window = self._ensure_box_window()
        # A multi-hour run yields a BoxSequence; the window owns the hour
        # controls, so it only needs to be handed the right kind of object.
        if hasattr(analysis, "analyses"):
            window.set_sequence(analysis)
        else:
            window.set_analysis(analysis)
        window.clear_progress()
        window.show()
        window.raise_()

    def _on_box_analysis_failed(self, message) -> None:
        worker = self.sender()
        if worker is not self._box_analysis_worker:
            return
        if getattr(worker, "_sharpmod_box_token", None) != getattr(self, "_box_token", None):
            return
        if self._box_window is not None:
            self._box_window.clear_progress()
            self._box_window.set_status(str(message))
        elif not self._box_window_closed:
            QMessageBox.critical(self, APP_NAME, str(message))

    def _on_box_analysis_finished(self) -> None:
        worker = self.sender()
        if self._box_analysis_worker is worker:
            self._box_analysis_worker = None
            if self._box_extract_worker is None and self._box_mean_worker is None:
                self._set_model_busy(False)
            self._cleanup_closed_box_output()
        worker.deleteLater()

    def _on_box_composites_requested(self) -> None:
        if self._box_extraction is None:
            return
        if self._box_analysis_worker is not None:
            return
        count = sum(
            len(outputs) for outputs in self._box_extraction.outputs_by_hour.values()
        )
        # About 0.4 s per sounding, measured. Say so rather than letting the
        # window appear to hang.
        answer = QMessageBox.question(
            self,
            APP_NAME,
            f"Computing the SPC composite indices for {count} soundings takes "
            f"roughly {max(1, round(count * 0.42))} seconds. Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer != QMessageBox.Yes:
            return
        if self._box_window is not None:
            self._box_window.set_status("Computing SPC composites…")
        from sharpmod.analysis.box_analysis import COMPOSITE_TIER, FAST_TIER

        self._start_box_analysis(self._box_extraction, (FAST_TIER, COMPOSITE_TIER))

    def _on_box_sounding_requested(self, npz_path: str, label: str) -> None:
        """Open one box grid point in the ordinary sounding workspace."""
        try:
            # The same decode path every other sounding in the application
            # takes, so a box cell opens into an identical workspace.
            prof_col, stn_id = _render().decode(npz_path)
        except Exception as exc:  # noqa: BLE001 - report, do not crash
            QMessageBox.warning(
                self, APP_NAME, f"That grid point could not be opened: {exc}"
            )
            return
        self._show_sounding(prof_col, stn_id or label, title=f"{APP_NAME} — {label}")

    def _cancel_box_operation(self) -> None:
        workers = (
            self._box_extract_worker,
            self._box_analysis_worker,
            self._box_mean_worker,
        )
        for worker in workers:
            if worker is None:
                continue
            box_token = getattr(self, "_box_token", None)
            if worker is self._box_extract_worker and box_token is not None:
                self._model_job.request_cancel(box_token)
            try:
                worker.requestInterruption()
            except (AttributeError, RuntimeError):
                pass

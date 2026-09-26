"""ERA5 source workflow for the sounding picker."""

from __future__ import annotations

from sharpmod.ui.features.gui_jobs import JobCounts

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from qtpy.QtCore import QDate, Qt
from qtpy.QtWidgets import (
    QApplication,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sharpmod.ui.features.gui_common import APP_NAME, _LOGGER, _render, action_label, install_month_calendar
from sharpmod.ui.features.gui_maps import MAP_AREAS, PointMapWidget
from sharpmod.ui.picker.layout import (
    DATE_DISPLAY_FORMAT,
    TOWN_LOOKUP_TOOLTIP,
    map_tool_row,
    rail_card,
    rail_row,
    rail_zoom_row,
    scrolling_control_rail,
    selection_pane,
    set_button_busy,
    town_lookup_attribution_label,
)
from sharpmod.ui.features.gui_workers import (
    _ERA5FetchWorker,
    _cleanup_point_data,
    _retain_point_data_until_close,
)
from sharpmod.ui.picker.selection import install_selection_feedback, refresh_selection_feedback
from sharpmod.ui.styles.theme import (
    CONTROL_H,
    FIELD_W,
    OBJ_CARD_RULE,
    OBJ_CARD_TOGGLE,
    OBJ_GHOST,
    OBJ_HINT,
    OBJ_NUMERIC,
    OBJ_PLAIN,
    OBJ_PRIMARY,
    OBJ_PROGRESS_DETAIL,
    OBJ_SECTION_LABEL,
    OBJ_STATUS,
    SPACE,
)


class Era5PickerMixin:
    """Build and operate the ERA5 reanalysis picker source."""

    def _build_era5_tab(self) -> QWidget:
        w = QWidget()
        outer = QHBoxLayout(w)
        outer.setContentsMargins(SPACE["md"], SPACE["sm"], SPACE["md"], SPACE["sm"])
        outer.setSpacing(SPACE["md"])

        self._era5_syncing_point = False
        self._era5_map = PointMapWidget()
        self._era5_map.set_projection(self.map_projection())
        self._apply_map_presentation()
        self._refresh_loaded_profile_markers()
        self._era5_map.pointSelected.connect(self._era5_on_map_point)
        self._era5_map.pointActivated.connect(lambda _lat, _lon: self._era5_fetch())
        self._era5_map.set_domain(
            (-180.0, 180.0, -90.0, 90.0), "ERA5 global 0.25° grid"
        )

        left = QVBoxLayout()
        left.setSpacing(SPACE["sm"])
        left.setContentsMargins(0, 0, 0, 0)

        setup_box, setup_layout = rail_card("ERA5 setup")
        self._era5_setup_card = setup_box

        def section(title: str) -> None:
            label = QLabel(title, setup_box)
            label.setObjectName(OBJ_SECTION_LABEL)
            setup_layout.addWidget(label)

        def rule() -> None:
            line = QFrame(setup_box)
            line.setFrameShape(QFrame.NoFrame)
            line.setObjectName(OBJ_CARD_RULE)
            setup_layout.addWidget(line)

        section("Map location")
        from sharpmod.ui.maps.regions import region_combo

        self._era5_area_combo = region_combo(self._era5_map, parent=setup_box)
        self._era5_area_combo.setAccessibleName("ERA5 map region")
        setup_layout.addWidget(self._era5_area_combo)
        setup_layout.addLayout(map_tool_row(self._era5_map, allow_box=False))

        rule()
        section("Analysis time (UTC)")
        time_grid = QGridLayout()
        time_grid.setContentsMargins(0, 0, 0, 0)
        time_grid.setHorizontalSpacing(SPACE["xs"])
        time_grid.setVerticalSpacing(SPACE["xs"])
        time_grid.setColumnMinimumWidth(0, FIELD_W["label"])
        time_grid.setColumnStretch(1, 1)
        setup_layout.addLayout(time_grid)
        self._era5_date = QDateEdit()
        self._era5_date.setDisplayFormat(DATE_DISPLAY_FORMAT)
        self._era5_date.setCalendarPopup(True)
        install_month_calendar(self._era5_date)
        self._era5_date.setMinimumDate(QDate(1940, 1, 1))
        self._era5_date.setMaximumDate(QDate.currentDate())
        self._era5_date.dateChanged.connect(self._era5_update_state)
        rail_row(time_grid, 0, "Date:", self._era5_date, width="timestamp")
        self._era5_hour = QComboBox()
        self._era5_hour.setObjectName(OBJ_NUMERIC)
        for hour in range(24):
            self._era5_hour.addItem(f"{hour:02d}Z", hour)
        self._era5_hour.currentIndexChanged.connect(self._era5_update_state)
        recent = QToolButton()
        recent.setText("Most recent")
        recent.setToolTip(
            "Latest analysis likely to be published. ERA5 normally appears "
            "several days after real time."
        )
        recent.clicked.connect(self._era5_set_recent)
        rail_row(time_grid, 1, "Hour:", self._era5_hour,
                 trailing=recent, width="compact")

        rule()
        section("Sounding point")
        point_hint = QLabel("Click the map or enter coordinates.", setup_box)
        point_hint.setObjectName(OBJ_HINT)
        setup_layout.addWidget(point_hint)
        point_grid = QGridLayout()
        point_grid.setContentsMargins(0, 0, 0, 0)
        point_grid.setHorizontalSpacing(SPACE["xs"])
        point_grid.setVerticalSpacing(SPACE["xs"])
        point_grid.setColumnStretch(0, 1)
        point_grid.setColumnStretch(1, 1)
        setup_layout.addLayout(point_grid)
        self._era5_lat = QDoubleSpinBox()
        self._era5_lat.setRange(-90.0, 90.0)
        self._era5_lat.setDecimals(4)
        self._era5_lat.setSingleStep(0.25)
        self._era5_lat.setValue(35.18)
        self._era5_lat.valueChanged.connect(self._era5_point_from_spins)
        self._era5_lon = QDoubleSpinBox()
        self._era5_lon.setRange(-180.0, 180.0)
        self._era5_lon.setDecimals(4)
        self._era5_lon.setSingleStep(0.25)
        self._era5_lon.setValue(-97.44)
        self._era5_lon.valueChanged.connect(self._era5_point_from_spins)
        for column, title, field in (
                (0, "Latitude", self._era5_lat),
                (1, "Longitude", self._era5_lon)):
            label = QLabel(title, setup_box)
            label.setBuddy(field)
            field.setAccessibleName(f"ERA5 {title.lower()}")
            field.setMinimumHeight(CONTROL_H["md"])
            point_grid.addWidget(label, 0, column)
            point_grid.addWidget(field, 1, column)
        self._era5_loc = QLineEdit()
        self._era5_loc.setPlaceholderText("Auto-detect from coordinates")
        self._era5_loc.setToolTip(TOWN_LOOKUP_TOOLTIP)
        place_label = QLabel("Place name (optional)", setup_box)
        place_label.setBuddy(self._era5_loc)
        point_grid.addWidget(place_label, 2, 0, 1, 2)
        point_grid.addWidget(self._era5_loc, 3, 0, 1, 2)
        self._era5_snapped = QLabel("")
        self._era5_snapped.setWordWrap(True)
        self._era5_snapped.setObjectName(OBJ_HINT)
        setup_layout.addWidget(self._era5_snapped)
        from sharpmod.ui.picker.coordinates import coordinate_paste_button

        self._era5_paste_coordinates = coordinate_paste_button(
            self, self._era5_lat, self._era5_lon, self._era5_point_from_spins,
            source="Reanalysis (ERA5)",
        )
        center = QToolButton(setup_box)
        center.setText("Center map")
        center.setToolTip("Center the map on this sounding point")
        center.clicked.connect(
            lambda: self._era5_map.set_point(
                self._era5_lat.value(), self._era5_lon.value(), center=True
            )
        )
        point_actions = QHBoxLayout()
        point_actions.setContentsMargins(0, 0, 0, 0)
        point_actions.setSpacing(SPACE["xs"])
        point_actions.addWidget(self._era5_paste_coordinates, 1)
        point_actions.addWidget(center, 1)
        setup_layout.addLayout(point_actions)
        setup_layout.addWidget(town_lookup_attribution_label(setup_box))
        self._era5_lock_btn = QPushButton("Lock point")
        self._era5_lock_btn.setCheckable(True)
        self._era5_lock_btn.setToolTip(
            "Lock the sounding point so clicks cannot move it. "
            "Inspect, pan, zoom, and history keep working.")
        self._era5_lock_btn.toggled.connect(self._era5_lock_toggled)
        self._era5_recent_btn = QPushButton("Recent point")
        self._era5_recent_btn.setToolTip(
            "Return to the previous sounding point (U with map focus).")
        self._era5_recent_btn.clicked.connect(self._era5_restore_recent_point)
        self._era5_recent_btn.setEnabled(False)
        era5_lock_row = QHBoxLayout()
        era5_lock_row.setContentsMargins(0, 0, 0, 0)
        era5_lock_row.setSpacing(SPACE["xs"])
        era5_lock_row.addWidget(self._era5_lock_btn, 1)
        era5_lock_row.addWidget(self._era5_recent_btn, 1)
        setup_layout.addLayout(era5_lock_row)

        self._era5_readiness = QLabel("")
        self._era5_readiness.setWordWrap(True)
        self._era5_readiness.setObjectName(OBJ_STATUS)
        setup_layout.addWidget(self._era5_readiness)

        fetch_row = QHBoxLayout()
        self._era5_fetch_btn = QPushButton(action_label("load_sounding"))
        self._era5_fetch_btn.setObjectName(OBJ_PRIMARY)
        self._era5_fetch_btn.setMinimumHeight(CONTROL_H["md"])
        self._era5_fetch_btn.clicked.connect(self._era5_fetch)
        fetch_row.addWidget(self._era5_fetch_btn, 1)
        self._era5_cancel_btn = QPushButton(action_label("cancel"))
        self._era5_cancel_btn.setObjectName(OBJ_GHOST)
        self._era5_cancel_btn.setMinimumHeight(CONTROL_H["md"])
        self._era5_cancel_btn.clicked.connect(self._cancel_era5_fetch)
        self._era5_cancel_btn.hide()
        fetch_row.addWidget(self._era5_cancel_btn)
        self._era5_multi_sounding = self._make_multi_sounding_checkbox("ERA5")
        setup_layout.addWidget(self._era5_multi_sounding)

        rule()
        navigation_toggle = QToolButton(setup_box)
        navigation_toggle.setObjectName(OBJ_CARD_TOGGLE)
        navigation_toggle.setText("Map navigation")
        navigation_toggle.setCheckable(True)
        navigation_toggle.setArrowType(Qt.RightArrow)
        navigation_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        navigation_toggle.setAutoRaise(True)
        navigation_toggle.setMinimumHeight(CONTROL_H["md"])
        navigation_toggle.setToolTip("Show zoom, fit, center, and map history controls")
        navigation = QWidget(setup_box)
        navigation.setObjectName(OBJ_PLAIN)
        navigation_layout = QVBoxLayout(navigation)
        navigation_layout.setContentsMargins(0, 0, 0, 0)
        navigation_layout.addLayout(rail_zoom_row(self._era5_map))
        navigation.hide()

        def set_navigation_visible(visible: bool) -> None:
            navigation.setVisible(visible)
            navigation_toggle.setArrowType(Qt.DownArrow if visible else Qt.RightArrow)
            navigation_toggle.setAccessibleName(
                f"{'Hide' if visible else 'Show'} map navigation controls"
            )

        navigation_toggle.toggled.connect(set_navigation_visible)
        set_navigation_visible(False)
        setup_layout.addWidget(navigation_toggle)
        setup_layout.addWidget(navigation)
        left.addWidget(setup_box)

        self._era5_progress = QProgressBar()
        self._era5_progress.setRange(0, 0)
        self._era5_progress.hide()
        self._era5_progress_detail = QLabel("")
        self._era5_progress_detail.setWordWrap(True)
        self._era5_progress_detail.setObjectName(OBJ_PROGRESS_DETAIL)
        self._era5_progress_detail.hide()
        left.addStretch(1)

        self._era5_controls_scroll = scrolling_control_rail(left)
        self._era5_selection_pane = selection_pane(
            self._era5_controls_scroll, self._era5_map, settings=self._settings,
            key="era5", title="ERA5",
        )
        outer.addWidget(self._era5_selection_pane)
        self._era5_selection_pane.actions.addLayout(fetch_row)
        self._era5_selection_pane.embed_guidance(setup_layout, index=0)
        self._era5_selection_pane.actions.addWidget(self._era5_progress)
        self._era5_selection_pane.actions.addWidget(self._era5_progress_detail)
        from sharpmod.ui.features.gui_jobs import JobStatus
        from sharpmod.ui.features.gui_common import scrollable_page

        self._era5_job = JobStatus(self)
        self._era5_job_retry = None
        self._era5_job.cancelRequested.connect(self._cancel_era5_fetch)
        self._era5_job.retryRequested.connect(self._retry_era5_job)
        self._era5_job_scroll = scrollable_page(
            self._era5_job, parent=self,
            accessible_name="ERA5 acquisition progress and recovery details",
        )
        self._era5_job_scroll.hide()
        self._era5_selection_pane.actions.addWidget(self._era5_job_scroll)

        self._era5_set_recent()
        self._era5_point_from_spins(center=True)
        self._era5_update_readiness()
        install_selection_feedback(self, self._era5_selection_pane, "era5", self._era5_fetch_btn)
        return w

    def _era5_update_readiness(self) -> None:
        try:
            from importlib.util import find_spec

            missing = [
                name
                for name in ("cdsapi", "cfgrib", "xarray")
                if find_spec(name) is None
            ]
        except (ImportError, ValueError):
            missing = []
        if missing:
            message = (
                "Missing ERA5 packages: "
                + ", ".join(missing)
                + '. Install with pip install -e ".[era5]".'
            )
            detail = message
        else:
            rc_path = Path(
                os.environ.get("CDSAPI_RC", str(Path.home() / ".cdsapirc"))
            ).expanduser()
            env_profile = bool(
                os.environ.get("CDSAPI_URL") and os.environ.get("CDSAPI_KEY")
            )
            if env_profile or rc_path.is_file():
                message = "CDS profile detected"
                detail = (
                    "Dataset terms are checked when loading. Secret values are never displayed."
                )
            else:
                message = (
                    "CDS credentials missing. Accept ERA5 dataset terms and "
                    "save .cdsapirc in your user folder."
                )
                detail = (
                    "Accept the ERA5 pressure-level and single-level terms, then "
                    "save the API profile as .cdsapirc in your user folder."
                )
        self._era5_readiness.setText(message)
        self._era5_readiness.setToolTip(detail)
        self._era5_readiness.setVisible(
            not message.startswith(("Missing ERA5", "CDS credentials"))
        )
        refresh_selection_feedback(self, "era5")

    def _era5_set_recent(self) -> None:
        recent = datetime.now(timezone.utc) - timedelta(days=6)
        self._era5_date.setDate(QDate(recent.year, recent.month, recent.day))
        index = self._era5_hour.findData(recent.hour)
        if index >= 0:
            self._era5_hour.setCurrentIndex(index)
        self._era5_update_state()

    def _era5_valid_time(self) -> datetime:
        day = self._era5_date.date()
        hour = int(self._era5_hour.currentData() or 0)
        return datetime(day.year(), day.month(), day.day(), hour, tzinfo=timezone.utc)

    def _era5_point_from_spins(self, *_args, center=False) -> None:
        if getattr(self, "_era5_syncing_point", False):
            return
        self._era5_map.set_point(
            self._era5_lat.value(), self._era5_lon.value(), center=center
        )
        self._era5_update_state()

    def _era5_on_map_point(self, lat, lon) -> None:
        self._era5_syncing_point = True
        try:
            self._era5_lat.setValue(float(lat))
            self._era5_lon.setValue(float(lon))
        finally:
            self._era5_syncing_point = False
        self._era5_update_state()

    def _era5_lock_toggled(self, checked: bool) -> None:
        """Lock or unlock the ERA5 sounding point (T18.1)."""
        if hasattr(self, "_era5_map"):
            self._era5_map.set_point_locked(bool(checked))
        self._era5_lock_btn.setText("Unlock point" if checked else "Lock point")
        self._era5_update_state()

    def _era5_restore_recent_point(self) -> None:
        """Return the ERA5 map to its reversible previous point (T18.1)."""
        widget = getattr(self, "_era5_map", None)
        if widget is None or not widget.restore_recent_point():
            self.statusBar().showMessage("No previous sounding point.", 4000)
            return
        recent = widget.recent_point()
        if recent is None:
            self._era5_update_state()
            return
        self._era5_syncing_point = True
        try:
            self._era5_lat.setValue(float(recent[0]))
            self._era5_lon.setValue(float(recent[1]))
        finally:
            self._era5_syncing_point = False
        self._era5_update_state()

    def _era5_update_state(self, *_args) -> None:
        if not hasattr(self, "_era5_snapped"):
            return
        from sharpmod.tools import era5_extract

        lat = float(self._era5_lat.value())
        lon = float(self._era5_lon.value())
        snapped_lat, snapped_lon = era5_extract._nearest_era5_grid_point(lat, lon)
        self._era5_snapped.setText(
            f"ERA5 grid point {snapped_lat:.2f}°, {snapped_lon:.2f}°"
        )
        self._era5_snapped.setToolTip(
            f"Requested {lat:.4f}°, {lon:.4f}° → "
            f"ERA5 grid {snapped_lat:.2f}°, {snapped_lon:.2f}°"
        )
        valid = self._era5_valid_time()
        self._era5_fetch_btn.setEnabled(
            valid <= datetime.now(timezone.utc) and self._era5_worker is None
        )
        if hasattr(self, "_era5_lock_btn") and hasattr(self, "_era5_map"):
            locked = self._era5_map.is_point_locked()
            if self._era5_lock_btn.isChecked() != locked:
                self._era5_lock_btn.blockSignals(True)
                try:
                    self._era5_lock_btn.setChecked(locked)
                finally:
                    self._era5_lock_btn.blockSignals(False)
            self._era5_lock_btn.setText("Unlock point" if locked else "Lock point")
        if hasattr(self, "_era5_recent_btn") and hasattr(self, "_era5_map"):
            try:
                has_recent = bool(self._era5_map.has_recent_point())
            except (AttributeError, RuntimeError):
                has_recent = False
            self._era5_recent_btn.setEnabled(has_recent)
        refresh_selection_feedback(self, "era5")

    def _era5_fetch(self) -> None:
        if self._era5_valid_time() > datetime.now(timezone.utc):
            self._era5_update_state()
            QMessageBox.warning(self, APP_NAME, "Choose an earlier ERA5 analysis time first.")
            return
        if self._era5_worker is not None:
            QMessageBox.information(
                self, APP_NAME, "An ERA5 fetch is already in progress."
            )
            return
        self._ensure_model_cache()
        from sharpmod.tools import era5_extract

        try:
            era5_extract.require_runtime_dependencies()
        except era5_extract.RetrievalError as exc:
            self._era5_update_readiness()
            self.statusBar().showMessage("ERA5 setup is incomplete")
            QMessageBox.critical(self, APP_NAME, str(exc))
            return

        valid = self._era5_valid_time()
        lat = float(self._era5_lat.value())
        lon = float(self._era5_lon.value())
        loc = self._era5_loc.text().strip() or None
        self._remember_point(lat, lon, loc)
        output_dir = tempfile.mkdtemp(
            prefix=f"era5_{valid:%Y%m%d%H}_{lat:+07.2f}_{lon:+08.2f}_"
        )
        out_path = os.path.join(output_dir, "sounding.npz")
        worker = _ERA5FetchWorker(
            lat,
            lon,
            valid,
            out_path,
            loc=loc,
            resolve_place=not bool(loc),
            disk_cache=self._model_disk_cache,
            parent=self,
        )
        self._era5_sequence = int(getattr(self, "_era5_sequence", 0)) + 1
        self._era5_token = (id(self), self._era5_sequence, valid, lat, lon, loc)
        self._era5_request = (valid, lat, lon, loc)
        worker._sharpmod_era5_token = self._era5_token
        self._era5_worker = worker
        worker.finished_ok.connect(self._on_era5_fetch_ok)
        worker.failed.connect(self._on_era5_fetch_failed)
        worker.cancelled.connect(self._on_era5_fetch_cancelled)
        worker.progress.connect(self._on_era5_progress)
        worker.finished.connect(self._on_era5_fetch_finished)
        self._set_era5_busy(True)
        self._era5_job_scroll.show()
        self._era5_job.begin(
            self._era5_token, "ERA5 reanalysis",
            f"ERA5 · analysis {valid:%Y-%m-%d %H}Z · {lat:.4f}, {lon:.4f} · {loc or 'ERA5'}",
            "Requesting the saved ERA5 analysis", total=1,
            unit="reanalysis soundings", cancellable=True,
        )
        self._era5_job_retry = None
        worker.start()

    def _set_era5_busy(self, busy) -> None:
        if busy:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self._era5_fetch_btn.setEnabled(False)
            set_button_busy(self._era5_fetch_btn, True, "Fetching ERA5…")
            self._era5_cancel_btn.setEnabled(True)
            set_button_busy(self._era5_cancel_btn, False, "")
            self._era5_cancel_btn.show()
            self._era5_progress.show()
            self._era5_progress_detail.setText("Validating the ERA5 request…")
            self._era5_progress_detail.show()
        else:
            QApplication.restoreOverrideCursor()
            set_button_busy(self._era5_fetch_btn, False, "")
            self._era5_cancel_btn.hide()
            self._era5_progress.hide()
            self._era5_progress_detail.hide()
            self._era5_update_state()
        refresh_selection_feedback(self, "era5")

    def _era5_current(self, worker):
        token = getattr(self, "_era5_token", None)
        if worker is not self._era5_worker or token is None:
            return False
        if getattr(worker, "_sharpmod_era5_token", None) != token:
            return False
        # Cancel must win over a queued terminal report: a snapshot for this
        # token that left running (cancelled, finished) rejects late reports.
        # No snapshot, or one for another token, keeps the legacy worker-
        # identity verdict already established above.
        snapshot = self._era5_job.snapshot
        return not (
            snapshot is not None
            and snapshot.token == token
            and snapshot.state != "running"
        )

    def _on_era5_progress(self, stage) -> None:
        worker = self.sender()
        if worker is not None and not self._era5_current(worker):
            return
        messages = {
            "town": "Resolving the selected town name…",
            "validating": "Validating the ERA5 request…",
            "queued": "Submitting the request to the Copernicus CDS queue…",
            "retrieving": "Waiting for and downloading the CDS result…",
            "decoding": "Decoding all 37 ERA5 pressure levels…",
            "extracting": "Extracting the nearest ERA5 grid column…",
            "cached": "Using the cached ERA5 point/hour…",
            "writing": "Writing the viewer-owned sounding…",
            "complete": "Preparing the ERA5 sounding display…",
            "rendering": "Rendering the ERA5 sounding window…",
        }
        message = messages.get(str(stage), "Processing ERA5 data…")
        self._era5_progress_detail.setText(message)
        self.statusBar().showMessage(message)
        if worker is not None and str(stage) not in {"rendering"}:
            self._era5_job.update(self._era5_token, stage=message.rstrip("…"))

    def _cancel_era5_fetch(self) -> None:
        worker = self._era5_worker
        if worker is None:
            return
        if self._era5_token is not None:
            self._era5_job.request_cancel(self._era5_token)
        worker.requestInterruption()
        self._era5_cancel_btn.setEnabled(False)
        set_button_busy(self._era5_cancel_btn, True, "Cancellation requested")
        self._era5_progress_detail.setText(
            "Cancellation requested. A synchronous CDS request already in "
            "flight must return before local cleanup can finish."
        )

    def _on_era5_fetch_cancelled(self) -> None:
        worker = self.sender()
        if not self._era5_current(worker):
            return
        token = self._era5_token
        self._era5_job.finish(
            token, counts=JobCounts(cancelled=1, requested=1), outcome="cancelled",
            message="ERA5 fetch cancelled; no new sounding was opened.", retryable=True,
        )
        self._era5_job_retry = token
        self.statusBar().showMessage("ERA5 fetch cancelled", 5000)

    def _on_era5_fetch_failed(self, message) -> None:
        worker = self.sender()
        if not self._era5_current(worker):
            return
        token = self._era5_token
        self._era5_job.finish(
            token, counts=JobCounts(failed=1, requested=1), outcome="failed",
            message=str(message), retryable=True,
        )
        self._era5_job_retry = token
        self.statusBar().showMessage("ERA5 fetch failed")
        QMessageBox.critical(self, APP_NAME, str(message))

    def _on_era5_fetch_finished(self) -> None:
        worker = self.sender()
        if self._era5_worker is not worker:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass
            return
        token = self._era5_token
        self._era5_worker = None
        try:
            self._set_era5_busy(False)
        finally:
            snapshot = self._era5_job.snapshot
            if (
                token is not None and snapshot is not None
                and snapshot.token == token
                and snapshot.state in {"running", "cancelling"}
            ):
                cancelled = snapshot.state == "cancelling"
                self._era5_job.finish(
                    token,
                    counts=JobCounts(
                        cancelled=1 if cancelled else 0,
                        unknown=0 if cancelled else 1,
                        requested=1,
                    ),
                    outcome="cancelled" if cancelled else "partial",
                    message=(
                        "ERA5 fetch cancelled; no new sounding was opened."
                        if cancelled
                        else "ERA5 worker stopped without a terminal report; no new sounding was opened."
                    ),
                    retryable=True,
                )
                self._era5_job_retry = token
            worker.deleteLater()

    def _retry_era5_job(self) -> None:
        token = getattr(self, "_era5_job_retry", None)
        snapshot = self._era5_job.snapshot
        if token is None or snapshot is None or snapshot.token != token:
            return
        if not snapshot.retryable or self._era5_worker is not None:
            return
        self._era5_job_retry = None
        self._era5_fetch()

    def _on_era5_fetch_ok(
        self, npz_path, valid_time, snapped_lat, snapped_lon, cache_hit
    ) -> None:
        worker = self.sender()
        if not self._era5_current(worker):
            return
        token = self._era5_token
        self._on_era5_progress("rendering")
        QApplication.processEvents()
        if not self._era5_current(worker):
            return
        try:
            renderer = _render()
            prof_col, stn_id = renderer.decode(npz_path)
            title = (
                f"{APP_NAME} — ERA5 {valid_time:%Y-%m-%d %H}Z "
                f"({snapped_lat:.2f}, {snapped_lon:.2f})"
            )
            win = self._show_sounding(prof_col, stn_id, title=title)
            _retain_point_data_until_close(win, npz_path, os.path.dirname(npz_path))
        except Exception as exc:  # noqa: BLE001 - GUI/render boundary
            _LOGGER.exception("era5_fetch.display_failed")
            _cleanup_point_data(npz_path, os.path.dirname(npz_path))
            self._era5_job.finish(
                token, counts=JobCounts(failed=1, requested=1), outcome="failed",
                message=f"Fetched, but could not display: {exc}", retryable=True,
            )
            self._era5_job_retry = token
            QMessageBox.critical(
                self, APP_NAME, f"Fetched, but could not display:\n{exc}"
            )
            return
        suffix = " (cache hit)" if cache_hit else ""
        self._era5_job.finish(
            token, counts=JobCounts(completed=1, requested=1),
            message=f"Opened ERA5 {valid_time:%Y-%m-%d %H}Z{suffix}.",
        )
        self.statusBar().showMessage(
            f"Opened ERA5 {valid_time:%Y-%m-%d %H}Z{suffix}", 5000
        )


__all__ = ["Era5PickerMixin"]

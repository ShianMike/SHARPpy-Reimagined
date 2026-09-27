"""Raw WRF source workflow for the sounding picker."""

from __future__ import annotations

from sharpmod.ui.features.gui_jobs import JobCounts

import os
import tempfile
from datetime import datetime

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sharpmod.ui.features.gui_common import APP_NAME, _LOGGER, _render, action_label
from sharpmod.ui.picker.selection import install_selection_feedback, refresh_selection_feedback
from sharpmod.ui.features.gui_maps import MAP_AREAS, PointMapWidget
from sharpmod.ui.picker.layout import (
    map_tool_row,
    rail_card,
    rail_zoom_row,
    scrolling_control_rail,
    selection_pane,
    set_button_busy,
    town_lookup_attribution_label,
)
from sharpmod.ui.features.gui_workers import (
    _WRFExtractWorker,
    _WRFInspectWorker,
    _cleanup_point_data,
    _retain_point_data_until_close,
)
from sharpmod.ui.styles.theme import (
    CONTROL_H,
    OBJ_CARD_RULE,
    OBJ_CARD_TOGGLE,
    OBJ_GHOST,
    OBJ_HINT,
    OBJ_PLAIN,
    OBJ_PRIMARY,
    OBJ_PROGRESS_DETAIL,
    OBJ_SECTION_LABEL,
    OBJ_STATUS,
    SPACE,
)


class WrfPickerMixin:
    """Build and operate the raw-WRF picker source."""

    def _build_wrf_file_panel(self) -> QWidget:
        panel = QWidget()
        outer = QHBoxLayout(panel)
        outer.setContentsMargins(SPACE["md"], SPACE["sm"], SPACE["md"], SPACE["sm"])
        outer.setSpacing(SPACE["md"])

        self._wrf_syncing_point = False
        self._wrf_map = PointMapWidget()
        self._wrf_map.set_projection(self.map_projection())
        self._apply_map_presentation()
        self._refresh_loaded_profile_markers()
        self._wrf_map.set_area("World")
        self._wrf_map.pointSelected.connect(self._wrf_on_map_point)
        self._wrf_map.pointActivated.connect(lambda _lat, _lon: self._wrf_extract())

        left = QVBoxLayout()
        left.setSpacing(SPACE["sm"])
        left.setContentsMargins(0, 0, 0, 0)

        setup_box, setup_layout = rail_card("WRF setup")
        self._wrf_setup_card = setup_box

        def section(title: str) -> None:
            label = QLabel(title, setup_box)
            label.setObjectName(OBJ_SECTION_LABEL)
            setup_layout.addWidget(label)

        def rule() -> None:
            line = QFrame(setup_box)
            line.setFrameShape(QFrame.NoFrame)
            line.setObjectName(OBJ_CARD_RULE)
            setup_layout.addWidget(line)

        section("WRF output file")
        self._wrf_path_edit = QLineEdit()
        self._wrf_path_edit.setClearButtonEnabled(True)
        self._wrf_path_edit.setPlaceholderText("Choose a wrfout* NetCDF file")
        self._wrf_path_edit.setAccessibleName("WRF output file path")
        self._wrf_path_edit.setToolTip(
            "Select a native wrfout* NetCDF file. Inspect it to read its grid and times."
        )
        self._wrf_path_edit.textChanged.connect(self._wrf_path_changed)
        self._wrf_path_edit.returnPressed.connect(self._wrf_start_inspection)
        setup_layout.addWidget(self._wrf_path_edit)
        file_actions = QHBoxLayout()
        file_actions.setContentsMargins(0, 0, 0, 0)
        file_actions.setSpacing(SPACE["xs"])
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_wrf_file)
        file_actions.addWidget(browse, 1)
        self._wrf_inspect_btn = QPushButton("Inspect file")
        self._wrf_inspect_btn.setToolTip("Read the WRF grid and available times")
        self._wrf_inspect_btn.clicked.connect(self._wrf_start_inspection)
        file_actions.addWidget(self._wrf_inspect_btn, 1)
        setup_layout.addLayout(file_actions)
        self._wrf_domain_status = QLabel("")
        self._wrf_domain_status.setWordWrap(True)
        self._wrf_domain_status.setObjectName(OBJ_STATUS)
        self._wrf_domain_status.hide()
        setup_layout.addWidget(self._wrf_domain_status)

        rule()
        section("Available time (UTC)")
        self._wrf_time_combo = QComboBox()
        self._wrf_time_combo.setEnabled(False)
        self._wrf_time_combo.setAccessibleName("WRF valid time")
        setup_layout.addWidget(self._wrf_time_combo)

        rule()
        section("Sounding point")
        setup_layout.addLayout(map_tool_row(self._wrf_map, allow_box=False))
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
        self._wrf_lat = QDoubleSpinBox()
        self._wrf_lat.setRange(-90.0, 90.0)
        self._wrf_lat.setDecimals(4)
        self._wrf_lat.setValue(35.18)
        self._wrf_lat.valueChanged.connect(self._wrf_point_from_spins)
        self._wrf_lon = QDoubleSpinBox()
        self._wrf_lon.setRange(-180.0, 180.0)
        self._wrf_lon.setDecimals(4)
        self._wrf_lon.setValue(-97.44)
        self._wrf_lon.valueChanged.connect(self._wrf_point_from_spins)
        for column, title, field in (
            (0, "Latitude", self._wrf_lat),
            (1, "Longitude", self._wrf_lon),
        ):
            label = QLabel(title, setup_box)
            label.setBuddy(field)
            field.setAccessibleName(f"WRF {title.lower()}")
            field.setMinimumHeight(CONTROL_H["md"])
            point_grid.addWidget(label, 0, column)
            point_grid.addWidget(field, 1, column)
        self._wrf_loc = QLineEdit()
        self._wrf_loc.setPlaceholderText("Auto-detect from coordinates")
        place_label = QLabel("Place name (optional)", setup_box)
        place_label.setBuddy(self._wrf_loc)
        point_grid.addWidget(place_label, 2, 0, 1, 2)
        point_grid.addWidget(self._wrf_loc, 3, 0, 1, 2)
        self._wrf_point_status = QLabel("")
        self._wrf_point_status.setWordWrap(True)
        self._wrf_point_status.setObjectName(OBJ_HINT)
        self._wrf_point_status.hide()
        setup_layout.addWidget(self._wrf_point_status)
        from sharpmod.ui.picker.coordinates import coordinate_paste_button

        self._wrf_paste_coordinates = coordinate_paste_button(
            self, self._wrf_lat, self._wrf_lon, self._wrf_point_from_spins,
            source="WRF file",
        )
        self._wrf_paste_coordinates.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed
        )
        center = QToolButton()
        center.setText("Center map")
        center.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        center.setToolTip("Center the map on this sounding point")
        center.clicked.connect(
            lambda: self._wrf_map.set_point(
                self._wrf_lat.value(), self._wrf_lon.value(), center=True
            )
        )
        point_actions = QHBoxLayout()
        point_actions.setContentsMargins(0, 0, 0, 0)
        point_actions.setSpacing(SPACE["xs"])
        point_actions.addWidget(self._wrf_paste_coordinates, 1)
        point_actions.addWidget(center, 1)
        setup_layout.addLayout(point_actions)
        setup_layout.addWidget(town_lookup_attribution_label(setup_box))
        self._wrf_lock_btn = QPushButton("Lock point")
        self._wrf_lock_btn.setCheckable(True)
        self._wrf_lock_btn.setToolTip(
            "Lock the sounding point so clicks cannot move it. "
            "Inspect, pan, zoom, and history keep working.")
        self._wrf_lock_btn.toggled.connect(self._wrf_lock_toggled)
        self._wrf_recent_btn = QPushButton("Recent point")
        self._wrf_recent_btn.setToolTip(
            "Return to the previous sounding point (U with map focus).")
        self._wrf_recent_btn.clicked.connect(self._wrf_restore_recent_point)
        self._wrf_recent_btn.setEnabled(False)
        wrf_lock_row = QHBoxLayout()
        wrf_lock_row.setContentsMargins(0, 0, 0, 0)
        wrf_lock_row.setSpacing(SPACE["xs"])
        wrf_lock_row.addWidget(self._wrf_lock_btn, 1)
        wrf_lock_row.addWidget(self._wrf_recent_btn, 1)
        setup_layout.addLayout(wrf_lock_row)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(SPACE["xs"])
        self._wrf_extract_btn = QPushButton(action_label("load_sounding"))
        self._wrf_extract_btn.setObjectName(OBJ_PRIMARY)
        self._wrf_extract_btn.setMinimumHeight(CONTROL_H["md"])
        self._wrf_extract_btn.setEnabled(False)
        self._wrf_extract_btn.clicked.connect(self._wrf_extract)
        action_row.addWidget(self._wrf_extract_btn, 1)
        self._wrf_cancel_btn = QPushButton(action_label("cancel"))
        self._wrf_cancel_btn.setObjectName(OBJ_GHOST)
        self._wrf_cancel_btn.setMinimumHeight(CONTROL_H["md"])
        self._wrf_cancel_btn.clicked.connect(self._cancel_wrf_operation)
        self._wrf_cancel_btn.hide()
        action_row.addWidget(self._wrf_cancel_btn)
        self._wrf_multi_sounding = self._make_multi_sounding_checkbox("WRF")
        setup_layout.addWidget(self._wrf_multi_sounding)

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
        navigation_layout.addLayout(rail_zoom_row(self._wrf_map))
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

        self._wrf_progress = QProgressBar()
        self._wrf_progress.setRange(0, 0)
        self._wrf_progress.hide()
        self._wrf_progress_detail = QLabel("")
        self._wrf_progress_detail.setWordWrap(True)
        self._wrf_progress_detail.setObjectName(OBJ_PROGRESS_DETAIL)
        self._wrf_progress_detail.hide()
        left.addStretch(1)

        self._wrf_controls_scroll = scrolling_control_rail(left)
        self._wrf_selection_pane = selection_pane(
            self._wrf_controls_scroll, self._wrf_map, settings=self._settings,
            key="wrf", title="Raw WRF",
        )
        outer.addWidget(self._wrf_selection_pane)
        self._wrf_selection_pane.actions.addLayout(action_row)
        self._wrf_selection_pane.embed_guidance(setup_layout, index=0)
        self._wrf_selection_pane.actions.addWidget(self._wrf_progress)
        self._wrf_selection_pane.actions.addWidget(self._wrf_progress_detail)
        from sharpmod.ui.features.gui_jobs import JobStatus
        from sharpmod.ui.features.gui_common import scrollable_page

        self._wrf_job = JobStatus(self)
        self._wrf_job_retry = None
        self._wrf_job.cancelRequested.connect(self._cancel_wrf_operation)
        self._wrf_job.retryRequested.connect(self._retry_wrf_job)
        self._wrf_job_scroll = scrollable_page(
            self._wrf_job, parent=self,
            accessible_name="WRF acquisition progress and recovery details",
        )
        self._wrf_job_scroll.hide()
        self._wrf_selection_pane.actions.addWidget(self._wrf_job_scroll)
        self._wrf_point_from_spins(center=True)
        install_selection_feedback(self, self._wrf_selection_pane, "wrf", self._wrf_extract_btn)
        return panel

    def _browse_wrf_file(self) -> None:
        start = self._settings.value(
            "wrf/last_dir", self._settings.value("last_dir", "", str), str
        )
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Raw WRF Output",
            start,
            "WRF output (wrfout* *.nc *.nc4);;All files (*.*)",
        )
        if path:
            self._wrf_path_edit.setText(path)
            self._wrf_start_inspection()

    def _wrf_path(self) -> str:
        return self._wrf_path_edit.text().strip().strip('"')

    def _wrf_path_changed(self, *_args) -> None:
        path = self._wrf_path()
        inspected = (self._wrf_domain or {}).get("source_file")
        if inspected and os.path.abspath(path) == os.path.abspath(inspected):
            return
        self._wrf_domain = None
        self._wrf_time_combo.clear()
        self._wrf_time_combo.setEnabled(False)
        self._wrf_map.set_domain(None)
        self._wrf_domain_status.clear()
        self._wrf_domain_status.hide()
        self._wrf_update_fetch_state()

    def _wrf_start_inspection(self) -> None:
        if self._wrf_inspect_worker is not None:
            QMessageBox.information(
                self, APP_NAME, "WRF inspection is already in progress."
            )
            return
        if self._wrf_extract_worker is not None:
            QMessageBox.information(
                self, APP_NAME, "Wait for the active WRF extraction first."
            )
            return
        path = self._wrf_path()
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, APP_NAME, f"WRF output file not found:\n{path}")
            return
        from sharpmod.tools import wrf_extract

        try:
            wrf_extract.require_runtime_dependencies()
        except wrf_extract.RetrievalError as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))
            return
        worker = _WRFInspectWorker(path, parent=self)
        self._wrf_inspect_worker = worker
        worker.inspected.connect(self._on_wrf_inspected)
        worker.failed.connect(self._on_wrf_inspect_failed)
        worker.cancelled.connect(self._on_wrf_cancelled)
        worker.finished.connect(self._on_wrf_inspect_finished)
        self._set_wrf_busy(True, "Inspecting WRF coordinates and times…")
        worker.start()

    @staticmethod
    def _wrf_map_area(bounds) -> str:
        lon0, lon1, lat0, lat1 = bounds
        candidates = []
        for name, (area_lon0, area_lon1, area_lat0, area_lat1) in MAP_AREAS.items():
            if (
                area_lon0 <= lon0
                and lon1 <= area_lon1
                and area_lat0 <= lat0
                and lat1 <= area_lat1
            ):
                size = (area_lon1 - area_lon0) * (area_lat1 - area_lat0)
                candidates.append((size, name))
        return min(candidates)[1] if candidates else "World"

    def _on_wrf_inspected(self, domain) -> None:
        worker = self.sender()
        if os.path.abspath(self._wrf_path()) != os.path.abspath(worker._path):
            _LOGGER.info("wrf_inspect.stale path=%s", worker._path)
            return
        self._wrf_domain = dict(domain)
        self._wrf_time_combo.clear()
        times = tuple(domain.get("times") or (None,))
        for index, value in enumerate(times):
            label = (
                value.strftime("%Y-%m-%d %H:%MZ")
                if isinstance(value, datetime)
                else f"File time {index + 1}"
            )
            self._wrf_time_combo.addItem(label, value)
        self._wrf_time_combo.setEnabled(bool(times))
        ny, nx = domain["shape"]
        lon0, lon1, lat0, lat1 = domain["bounds"]
        self._wrf_domain_status.setText(
            f"Grid {ny} × {nx} · {len(times)} available "
            f"time{'s' if len(times) != 1 else ''}"
        )
        self._wrf_domain_status.setToolTip(
            f"{lat0:.3f}–{lat1:.3f}° latitude · "
            f"{lon0:.3f}–{lon1:.3f}° longitude"
        )
        self._wrf_domain_status.show()
        self._wrf_map.set_area(self._wrf_map_area(domain["bounds"]))
        self._wrf_map.set_domain(domain["bounds"], f"WRF grid {ny} × {nx}")
        center_lat, center_lon = domain["center"]
        self._wrf_syncing_point = True
        try:
            self._wrf_lat.setValue(float(center_lat))
            self._wrf_lon.setValue(float(center_lon))
        finally:
            self._wrf_syncing_point = False
        self._wrf_map.set_point(center_lat, center_lon, center=True)
        self._settings.setValue("wrf/last_dir", os.path.dirname(worker._path))
        self._wrf_update_fetch_state()

    def _on_wrf_inspect_failed(self, message) -> None:
        self._wrf_domain = None
        self._wrf_time_combo.clear()
        self._wrf_time_combo.setEnabled(False)
        self._wrf_map.set_domain(None)
        self._wrf_domain_status.setText("WRF inspection failed.")
        self._wrf_domain_status.setToolTip(str(message))
        self._wrf_domain_status.show()
        self._wrf_update_fetch_state()
        self.statusBar().showMessage("WRF inspection failed")
        QMessageBox.critical(self, APP_NAME, str(message))

    def _on_wrf_inspect_finished(self) -> None:
        worker = self.sender()
        if self._wrf_inspect_worker is worker:
            self._wrf_inspect_worker = None
            self._set_wrf_busy(False)
        worker.deleteLater()

    def _wrf_point_from_spins(self, *_args, center=False) -> None:
        if getattr(self, "_wrf_syncing_point", False):
            return
        self._wrf_map.set_point(
            self._wrf_lat.value(), self._wrf_lon.value(), center=center
        )
        self._wrf_update_fetch_state()

    def _wrf_on_map_point(self, lat, lon) -> None:
        self._wrf_syncing_point = True
        try:
            self._wrf_lat.setValue(float(lat))
            self._wrf_lon.setValue(float(lon))
        finally:
            self._wrf_syncing_point = False
        self._wrf_update_fetch_state()

    def _wrf_lock_toggled(self, checked: bool) -> None:
        """Lock or unlock the WRF sounding point (T18.1)."""
        if hasattr(self, "_wrf_map"):
            self._wrf_map.set_point_locked(bool(checked))
        self._wrf_lock_btn.setText("Unlock point" if checked else "Lock point")
        self._wrf_update_fetch_state()

    def _wrf_restore_recent_point(self) -> None:
        """Return the WRF map to its reversible previous point (T18.1)."""
        widget = getattr(self, "_wrf_map", None)
        if widget is None or not widget.restore_recent_point():
            self.statusBar().showMessage("No previous sounding point.", 4000)
            return
        recent = widget.recent_point()
        if recent is None:
            self._wrf_update_fetch_state()
            return
        self._wrf_syncing_point = True
        try:
            self._wrf_lat.setValue(float(recent[0]))
            self._wrf_lon.setValue(float(recent[1]))
        finally:
            self._wrf_syncing_point = False
        self._wrf_update_fetch_state()

    def _wrf_update_fetch_state(self) -> None:
        if not hasattr(self, "_wrf_extract_btn"):
            return
        from sharpmod.tools import wrf_extract

        lat = float(self._wrf_lat.value())
        lon = float(self._wrf_lon.value())
        ok = wrf_extract.point_in_domain(self._wrf_domain, lat, lon)
        if self._wrf_domain is None:
            self._wrf_point_status.hide()
        elif ok:
            self._wrf_point_status.setText("Inside WRF grid")
            self._wrf_point_status.show()
        else:
            self._wrf_point_status.setText("Outside WRF grid")
            self._wrf_point_status.show()
        busy = (
            self._wrf_inspect_worker is not None or self._wrf_extract_worker is not None
        )
        self._wrf_extract_btn.setEnabled(ok and not busy)
        if hasattr(self, "_wrf_lock_btn") and hasattr(self, "_wrf_map"):
            locked = self._wrf_map.is_point_locked()
            if self._wrf_lock_btn.isChecked() != locked:
                self._wrf_lock_btn.blockSignals(True)
                try:
                    self._wrf_lock_btn.setChecked(locked)
                finally:
                    self._wrf_lock_btn.blockSignals(False)
            self._wrf_lock_btn.setText("Unlock point" if locked else "Lock point")
        if hasattr(self, "_wrf_recent_btn") and hasattr(self, "_wrf_map"):
            try:
                has_recent = bool(self._wrf_map.has_recent_point())
            except (AttributeError, RuntimeError):
                has_recent = False
            self._wrf_recent_btn.setEnabled(has_recent)
        refresh_selection_feedback(self, "wrf")

    def _wrf_selected_time(self):
        if self._wrf_time_combo.count() == 0:
            return None
        return self._wrf_time_combo.currentData()

    def _wrf_extract(self) -> None:
        if self._wrf_extract_worker is not None:
            QMessageBox.information(
                self, APP_NAME, "A WRF extraction is already in progress."
            )
            return
        if self._wrf_inspect_worker is not None:
            QMessageBox.information(
                self, APP_NAME, "Wait for WRF inspection to finish first."
            )
            return
        from sharpmod.tools import wrf_extract

        lat = float(self._wrf_lat.value())
        lon = float(self._wrf_lon.value())
        if not wrf_extract.point_in_domain(self._wrf_domain, lat, lon):
            QMessageBox.warning(
                self, APP_NAME, "Choose a point inside the inspected WRF grid."
            )
            return
        path = self._wrf_path()
        if not os.path.isfile(path):
            QMessageBox.warning(self, APP_NAME, f"File not found:\n{path}")
            return
        valid = self._wrf_selected_time()
        loc = self._wrf_loc.text().strip() or None
        output_dir = tempfile.mkdtemp(prefix="wrf_gui_")
        out_path = os.path.join(output_dir, "sounding.npz")
        worker = _WRFExtractWorker(
            path,
            lat,
            lon,
            out_path,
            valid_time=valid,
            loc=loc,
            resolve_place=not bool(loc),
            parent=self,
        )
        self._wrf_sequence = int(getattr(self, "_wrf_sequence", 0)) + 1
        self._wrf_token = (id(self), self._wrf_sequence, path, valid, lat, lon, loc)
        self._wrf_request = (path, valid, lat, lon, loc)
        worker._sharpmod_wrf_token = self._wrf_token
        self._wrf_extract_worker = worker
        worker.finished_ok.connect(self._on_wrf_extract_ok)
        worker.failed.connect(self._on_wrf_extract_failed)
        worker.cancelled.connect(self._on_wrf_cancelled)
        worker.progress.connect(self._on_wrf_progress)
        worker.finished.connect(self._on_wrf_extract_finished)
        self._set_wrf_busy(True, "Opening raw WRF output…")
        self._wrf_job_scroll.show()
        self._wrf_job.begin(
            self._wrf_token, "Raw WRF extraction",
            f"WRF-ARW · {os.path.basename(path)} · {lat:.4f}, {lon:.4f} · {loc or 'WRF'}",
            "Extracting the saved WRF column", total=1,
            unit="WRF soundings", cancellable=True,
        )
        self._wrf_job_retry = None
        worker.start()

    def _set_wrf_busy(self, busy, message="") -> None:
        if busy:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            self._wrf_inspect_btn.setEnabled(False)
            self._wrf_extract_btn.setEnabled(False)
            self._wrf_cancel_btn.setEnabled(True)
            set_button_busy(self._wrf_cancel_btn, False, "")
            self._wrf_cancel_btn.show()
            self._wrf_progress.show()
            self._wrf_progress_detail.setText(message or "Processing WRF data…")
            self._wrf_progress_detail.show()
        else:
            QApplication.restoreOverrideCursor()
            self._wrf_inspect_btn.setEnabled(True)
            self._wrf_cancel_btn.hide()
            self._wrf_progress.hide()
            self._wrf_progress_detail.hide()
            set_button_busy(self._wrf_extract_btn, False, "")
            self._wrf_update_fetch_state()
        refresh_selection_feedback(self, "wrf")

    def _wrf_extract_current(self, worker):
        token = getattr(self, "_wrf_token", None)
        if worker is not self._wrf_extract_worker or token is None:
            return False
        if getattr(worker, "_sharpmod_wrf_token", None) != token:
            return False
        # Cancel must win over a queued terminal report: a snapshot for this
        # token that left running (cancelled, finished) rejects late reports.
        # No snapshot, or one for another token, keeps the legacy worker-
        # identity verdict already established above.
        snapshot = self._wrf_job.snapshot
        return not (
            snapshot is not None
            and snapshot.token == token
            and snapshot.state != "running"
        )

    def _on_wrf_progress(self, stage) -> None:
        worker = self.sender()
        if worker is not None and not self._wrf_extract_current(worker):
            return
        messages = {
            "town": "Resolving the selected town name…",
            "validating": "Validating the WRF request…",
            "opening": "Opening raw WRF NetCDF output…",
            "extracting": "Destaggering and extracting the WRF column…",
            "writing": "Writing the viewer-owned sounding…",
            "complete": "Preparing the WRF sounding display…",
            "rendering": "Rendering the WRF sounding window…",
        }
        message = messages.get(str(stage), "Processing WRF output…")
        self._wrf_progress_detail.setText(message)
        self.statusBar().showMessage(message)
        if worker is not None and str(stage) != "rendering":
            self._wrf_job.update(self._wrf_token, stage=message.rstrip("…"))

    def _cancel_wrf_operation(self) -> None:
        worker = self._wrf_extract_worker or self._wrf_inspect_worker
        if worker is None:
            return
        if worker is self._wrf_extract_worker and self._wrf_token is not None:
            self._wrf_job.request_cancel(self._wrf_token)
        worker.requestInterruption()
        self._wrf_cancel_btn.setEnabled(False)
        set_button_busy(self._wrf_cancel_btn, True, "Cancelling…")
        self.statusBar().showMessage("Cancelling WRF operation…")

    def _on_wrf_cancelled(self) -> None:
        worker = self.sender()
        is_extract = worker is self._wrf_extract_worker
        if is_extract and not self._wrf_extract_current(worker):
            return
        if is_extract:
            token = self._wrf_token
            self._wrf_job.finish(
                token, counts=JobCounts(cancelled=1, requested=1), outcome="cancelled",
                message="WRF extraction cancelled; no new sounding was opened.",
                retryable=True,
            )
            self._wrf_job_retry = token
        self.statusBar().showMessage("WRF operation cancelled", 5000)

    def _on_wrf_extract_failed(self, message) -> None:
        worker = self.sender()
        if not self._wrf_extract_current(worker):
            return
        token = self._wrf_token
        self._wrf_job.finish(
            token, counts=JobCounts(failed=1, requested=1), outcome="failed",
            message=str(message), retryable=True,
        )
        self._wrf_job_retry = token
        self.statusBar().showMessage("WRF extraction failed")
        QMessageBox.critical(self, APP_NAME, str(message))

    def _on_wrf_extract_finished(self) -> None:
        worker = self.sender()
        if self._wrf_extract_worker is not worker:
            try:
                worker.deleteLater()
            except RuntimeError:
                pass
            return
        token = self._wrf_token
        self._wrf_extract_worker = None
        try:
            self._set_wrf_busy(False)
        finally:
            snapshot = self._wrf_job.snapshot
            if (
                token is not None and snapshot is not None
                and snapshot.token == token
                and snapshot.state in {"running", "cancelling"}
            ):
                cancelled = snapshot.state == "cancelling"
                self._wrf_job.finish(
                    token,
                    counts=JobCounts(
                        cancelled=1 if cancelled else 0,
                        unknown=0 if cancelled else 1,
                        requested=1,
                    ),
                    outcome="cancelled" if cancelled else "partial",
                    message=(
                        "WRF extraction cancelled; no new sounding was opened."
                        if cancelled
                        else "WRF worker stopped without a terminal report; no new sounding was opened."
                    ),
                    retryable=True,
                )
                self._wrf_job_retry = token
            worker.deleteLater()

    def _retry_wrf_job(self) -> None:
        token = getattr(self, "_wrf_job_retry", None)
        snapshot = self._wrf_job.snapshot
        if token is None or snapshot is None or snapshot.token != token:
            return
        if not snapshot.retryable or self._wrf_extract_worker is not None:
            return
        self._wrf_job_retry = None
        self._wrf_extract()

    def _on_wrf_extract_ok(self, npz_path, valid_time) -> None:
        worker = self.sender()
        if not self._wrf_extract_current(worker):
            return
        token = self._wrf_token
        self._on_wrf_progress("rendering")
        QApplication.processEvents()
        if not self._wrf_extract_current(worker):
            return
        try:
            renderer = _render()
            prof_col, stn_id = renderer.decode(npz_path)
            suffix = (
                valid_time.strftime(" %Y-%m-%d %H:%MZ")
                if isinstance(valid_time, datetime)
                else ""
            )
            title = f"{APP_NAME} — WRF-ARW{suffix}"
            win = self._show_sounding(prof_col, stn_id, title=title)
            _retain_point_data_until_close(win, npz_path, os.path.dirname(npz_path))
        except Exception as exc:  # noqa: BLE001 - GUI/render boundary
            _LOGGER.exception("wrf_extract.display_failed")
            _cleanup_point_data(npz_path, os.path.dirname(npz_path))
            self._wrf_job.finish(
                token, counts=JobCounts(failed=1, requested=1), outcome="failed",
                message=f"Extracted, but could not display: {exc}", retryable=True,
            )
            self._wrf_job_retry = token
            QMessageBox.critical(
                self, APP_NAME, f"Extracted, but could not display:\n{exc}"
            )
            return
        self._wrf_job.finish(
            token, counts=JobCounts(completed=1, requested=1),
            message="Opened raw WRF point sounding.",
        )
        self.statusBar().showMessage("Opened raw WRF point sounding", 5000)


__all__ = ["WrfPickerMixin"]

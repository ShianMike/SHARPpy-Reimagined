"""Raw WRF source workflow for the sounding picker."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime

from qtpy.QtCore import Qt
from qtpy.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
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

from sharpmod.gui_common import APP_NAME, _LOGGER, _render
from sharpmod.gui_maps import MAP_AREAS, PointMapWidget
from sharpmod.gui_picker_layout import (
    scrolling_control_rail,
    set_button_busy,
    town_lookup_attribution_label,
)
from sharpmod.gui_workers import (
    _WRFExtractWorker,
    _WRFInspectWorker,
    _cleanup_point_data,
    _retain_point_data_until_close,
)
from sharpmod.theme import (
    CONTROL_H,
    OBJ_GHOST,
    OBJ_HINT,
    OBJ_PRIMARY,
    OBJ_PROGRESS_DETAIL,
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
        self._wrf_map.set_area("World")
        self._wrf_map.pointSelected.connect(self._wrf_on_map_point)
        self._wrf_map.pointActivated.connect(lambda _lat, _lon: self._wrf_extract())

        left = QVBoxLayout()
        left.setSpacing(SPACE["md"])

        file_box = QGroupBox("Raw WRF-ARW output")
        file_layout = QVBoxLayout(file_box)
        explanation = QLabel(
            "Choose a native wrfout* NetCDF file. Domain and time inspection "
            "runs in the background before extraction is enabled."
        )
        explanation.setWordWrap(True)
        file_layout.addWidget(explanation)
        file_row = QHBoxLayout()
        self._wrf_path_edit = QLineEdit()
        self._wrf_path_edit.setClearButtonEnabled(True)
        self._wrf_path_edit.setPlaceholderText("Path to wrfout_d01_…")
        self._wrf_path_edit.textChanged.connect(self._wrf_path_changed)
        self._wrf_path_edit.returnPressed.connect(self._wrf_start_inspection)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_wrf_file)
        file_row.addWidget(self._wrf_path_edit, 1)
        file_row.addWidget(browse)
        file_layout.addLayout(file_row)
        self._wrf_inspect_btn = QPushButton("Inspect Domain && Times")
        self._wrf_inspect_btn.clicked.connect(self._wrf_start_inspection)
        file_layout.addWidget(self._wrf_inspect_btn)
        self._wrf_domain_status = QLabel("Choose a file to inspect.")
        self._wrf_domain_status.setWordWrap(True)
        self._wrf_domain_status.setObjectName(OBJ_STATUS)
        file_layout.addWidget(self._wrf_domain_status)
        left.addWidget(file_box)

        time_box = QGroupBox("Available time")
        time_layout = QVBoxLayout(time_box)
        self._wrf_time_combo = QComboBox()
        self._wrf_time_combo.setEnabled(False)
        time_layout.addWidget(self._wrf_time_combo)
        left.addWidget(time_box)

        point_box = QGroupBox("Point inside WRF domain")
        point_grid = QGridLayout(point_box)
        point_grid.addWidget(QLabel("Latitude:"), 0, 0)
        self._wrf_lat = QDoubleSpinBox()
        self._wrf_lat.setRange(-90.0, 90.0)
        self._wrf_lat.setDecimals(4)
        self._wrf_lat.setValue(35.18)
        self._wrf_lat.valueChanged.connect(self._wrf_point_from_spins)
        point_grid.addWidget(self._wrf_lat, 0, 1)
        point_grid.addWidget(QLabel("Longitude:"), 1, 0)
        self._wrf_lon = QDoubleSpinBox()
        self._wrf_lon.setRange(-180.0, 180.0)
        self._wrf_lon.setDecimals(4)
        self._wrf_lon.setValue(-97.44)
        self._wrf_lon.valueChanged.connect(self._wrf_point_from_spins)
        point_grid.addWidget(self._wrf_lon, 1, 1)
        center = QToolButton()
        center.setText("Center")
        center.clicked.connect(
            lambda: self._wrf_map.set_point(
                self._wrf_lat.value(), self._wrf_lon.value(), center=True
            )
        )
        point_grid.addWidget(center, 0, 2, 2, 1)
        self._wrf_point_status = QLabel("Inspect a WRF domain first.")
        self._wrf_point_status.setWordWrap(True)
        self._wrf_point_status.setObjectName(OBJ_HINT)
        point_grid.addWidget(self._wrf_point_status, 2, 0, 1, 3)
        point_grid.addWidget(QLabel("Label:"), 3, 0)
        self._wrf_loc = QLineEdit()
        self._wrf_loc.setPlaceholderText("automatic town name")
        point_grid.addWidget(self._wrf_loc, 3, 1, 1, 2)
        point_grid.addWidget(town_lookup_attribution_label(), 4, 0, 1, 3)
        left.addWidget(point_box)

        action_row = QHBoxLayout()
        self._wrf_extract_btn = QPushButton("Extract && Display WRF Sounding")
        self._wrf_extract_btn.setObjectName(OBJ_PRIMARY)
        self._wrf_extract_btn.setMinimumHeight(CONTROL_H["lg"])
        self._wrf_extract_btn.setEnabled(False)
        self._wrf_extract_btn.clicked.connect(self._wrf_extract)
        action_row.addWidget(self._wrf_extract_btn, 1)
        self._wrf_cancel_btn = QPushButton("Cancel")
        self._wrf_cancel_btn.setObjectName(OBJ_GHOST)
        self._wrf_cancel_btn.setMinimumHeight(CONTROL_H["lg"])
        self._wrf_cancel_btn.clicked.connect(self._cancel_wrf_operation)
        self._wrf_cancel_btn.hide()
        action_row.addWidget(self._wrf_cancel_btn)
        left.addLayout(action_row)
        self._wrf_multi_sounding = self._make_multi_sounding_checkbox("WRF")
        left.addWidget(self._wrf_multi_sounding)
        self._wrf_progress = QProgressBar()
        self._wrf_progress.setRange(0, 0)
        self._wrf_progress.hide()
        left.addWidget(self._wrf_progress)
        self._wrf_progress_detail = QLabel("")
        self._wrf_progress_detail.setWordWrap(True)
        self._wrf_progress_detail.setObjectName(OBJ_PROGRESS_DETAIL)
        self._wrf_progress_detail.hide()
        left.addWidget(self._wrf_progress_detail)
        left.addStretch(1)

        self._wrf_controls_scroll = scrolling_control_rail(left)
        outer.addWidget(self._wrf_controls_scroll)
        outer.addWidget(self._wrf_map, 1)
        self._wrf_point_from_spins(center=True)
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
        self._wrf_domain_status.setText("Inspect this file before extraction.")
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
            f"Grid {ny} × {nx}; {lat0:.3f}–{lat1:.3f}° latitude, "
            f"{lon0:.3f}–{lon1:.3f}° longitude; {len(times)} time(s)."
        )
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
        self._wrf_domain_status.setText("WRF inspection failed.")
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

    def _wrf_update_fetch_state(self) -> None:
        if not hasattr(self, "_wrf_extract_btn"):
            return
        from sharpmod.tools import wrf_extract

        lat = float(self._wrf_lat.value())
        lon = float(self._wrf_lon.value())
        ok = wrf_extract.point_in_domain(self._wrf_domain, lat, lon)
        if self._wrf_domain is None:
            self._wrf_point_status.setText("Inspect a WRF domain first.")
        elif ok:
            self._wrf_point_status.setText(
                f"Selected {lat:.4f}, {lon:.4f} inside the WRF grid."
            )
        else:
            self._wrf_point_status.setText(
                f"Selected {lat:.4f}, {lon:.4f} is outside the WRF grid."
            )
        busy = (
            self._wrf_inspect_worker is not None or self._wrf_extract_worker is not None
        )
        self._wrf_extract_btn.setEnabled(ok and not busy)

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
        self._wrf_extract_worker = worker
        worker.finished_ok.connect(self._on_wrf_extract_ok)
        worker.failed.connect(self._on_wrf_extract_failed)
        worker.cancelled.connect(self._on_wrf_cancelled)
        worker.progress.connect(self._on_wrf_progress)
        worker.finished.connect(self._on_wrf_extract_finished)
        self._set_wrf_busy(True, "Opening raw WRF output…")
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

    def _on_wrf_progress(self, stage) -> None:
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

    def _cancel_wrf_operation(self) -> None:
        worker = self._wrf_extract_worker or self._wrf_inspect_worker
        if worker is None:
            return
        worker.requestInterruption()
        self._wrf_cancel_btn.setEnabled(False)
        set_button_busy(self._wrf_cancel_btn, True, "Cancelling…")
        self.statusBar().showMessage("Cancelling WRF operation…")

    def _on_wrf_cancelled(self) -> None:
        self.statusBar().showMessage("WRF operation cancelled", 5000)

    def _on_wrf_extract_failed(self, message) -> None:
        self.statusBar().showMessage("WRF extraction failed")
        QMessageBox.critical(self, APP_NAME, str(message))

    def _on_wrf_extract_finished(self) -> None:
        worker = self.sender()
        if self._wrf_extract_worker is worker:
            self._wrf_extract_worker = None
            self._set_wrf_busy(False)
        worker.deleteLater()

    def _on_wrf_extract_ok(self, npz_path, valid_time) -> None:
        self._on_wrf_progress("rendering")
        QApplication.processEvents()
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
            QMessageBox.critical(
                self, APP_NAME, f"Extracted, but could not display:\n{exc}"
            )
            return
        self.statusBar().showMessage("Opened raw WRF point sounding", 5000)


__all__ = ["WrfPickerMixin"]

"""ERA5 source workflow for the sounding picker."""

from __future__ import annotations

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

from sharpmod.gui_common import APP_NAME, _LOGGER, _render, install_month_calendar
from sharpmod.gui_maps import MAP_AREAS, PointMapWidget
from sharpmod.gui_picker_layout import (
    TOWN_LOOKUP_TOOLTIP,
    rail_card,
    rail_form,
    rail_row,
    rail_zoom_row,
    scrolling_control_rail,
    set_button_busy,
    town_lookup_attribution_label,
)
from sharpmod.gui_workers import (
    _ERA5FetchWorker,
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
        self._era5_map.pointSelected.connect(self._era5_on_map_point)
        self._era5_map.pointActivated.connect(lambda _lat, _lon: self._era5_fetch())
        self._era5_map.set_domain(
            (-180.0, 180.0, -90.0, 90.0), "ERA5 global 0.25° grid"
        )

        left = QVBoxLayout()
        left.setSpacing(SPACE["md"])
        left.setContentsMargins(0, 0, 0, 0)

        area_box, area_layout = rail_card("Region")
        self._era5_area_combo = QComboBox()
        for name in MAP_AREAS:
            self._era5_area_combo.addItem(name)
        self._era5_area_combo.setMinimumHeight(CONTROL_H["md"])
        self._era5_area_combo.currentTextChanged.connect(self._era5_map.set_area)
        area_layout.addWidget(self._era5_area_combo)
        area_layout.addLayout(rail_zoom_row(self._era5_map))
        left.addWidget(area_box)

        time_box, time_grid = rail_form("Analysis time (UTC)")
        self._era5_date = QDateEdit()
        self._era5_date.setDisplayFormat("yyyy-MM-dd")
        self._era5_date.setCalendarPopup(True)
        install_month_calendar(self._era5_date)
        self._era5_date.setMinimumDate(QDate(1940, 1, 1))
        self._era5_date.setMaximumDate(QDate.currentDate())
        self._era5_date.dateChanged.connect(self._era5_update_state)
        rail_row(time_grid, 0, "Date:", self._era5_date)
        self._era5_hour = QComboBox()
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
        rail_row(time_grid, 1, "Hour:", self._era5_hour, trailing=recent)
        left.addWidget(time_box)

        point_box, point_grid = rail_form("Point")
        self._era5_lat = QDoubleSpinBox()
        self._era5_lat.setRange(-90.0, 90.0)
        self._era5_lat.setDecimals(4)
        self._era5_lat.setSingleStep(0.25)
        self._era5_lat.setValue(35.18)
        self._era5_lat.valueChanged.connect(self._era5_point_from_spins)
        center = QToolButton()
        center.setText("Center")
        center.setToolTip("Center the map on this point")
        center.clicked.connect(
            lambda: self._era5_map.set_point(
                self._era5_lat.value(), self._era5_lon.value(), center=True
            )
        )
        rail_row(point_grid, 0, "Latitude:", self._era5_lat, trailing=center)
        self._era5_lon = QDoubleSpinBox()
        self._era5_lon.setRange(-180.0, 180.0)
        self._era5_lon.setDecimals(4)
        self._era5_lon.setSingleStep(0.25)
        self._era5_lon.setValue(-97.44)
        self._era5_lon.valueChanged.connect(self._era5_point_from_spins)
        rail_row(point_grid, 1, "Longitude:", self._era5_lon)
        self._era5_loc = QLineEdit()
        self._era5_loc.setPlaceholderText("automatic town name")
        self._era5_loc.setToolTip(TOWN_LOOKUP_TOOLTIP)
        rail_row(point_grid, 2, "Town:", self._era5_loc)
        self._era5_snapped = QLabel("")
        self._era5_snapped.setWordWrap(True)
        self._era5_snapped.setObjectName(OBJ_HINT)
        point_grid.addWidget(self._era5_snapped, 3, 0, 1, 3)
        point_grid.addWidget(town_lookup_attribution_label(), 4, 0, 1, 3)
        left.addWidget(point_box)

        self._era5_readiness = QLabel("")
        self._era5_readiness.setWordWrap(True)
        self._era5_readiness.setObjectName(OBJ_STATUS)
        left.addWidget(self._era5_readiness)

        fetch_row = QHBoxLayout()
        self._era5_fetch_btn = QPushButton("Fetch && Display ERA5 Sounding")
        self._era5_fetch_btn.setObjectName(OBJ_PRIMARY)
        self._era5_fetch_btn.setMinimumHeight(CONTROL_H["lg"])
        self._era5_fetch_btn.clicked.connect(self._era5_fetch)
        fetch_row.addWidget(self._era5_fetch_btn, 1)
        self._era5_cancel_btn = QPushButton("Cancel")
        self._era5_cancel_btn.setObjectName(OBJ_GHOST)
        self._era5_cancel_btn.setMinimumHeight(CONTROL_H["lg"])
        self._era5_cancel_btn.clicked.connect(self._cancel_era5_fetch)
        self._era5_cancel_btn.hide()
        fetch_row.addWidget(self._era5_cancel_btn)
        left.addLayout(fetch_row)
        self._era5_multi_sounding = self._make_multi_sounding_checkbox("ERA5")
        left.addWidget(self._era5_multi_sounding)

        self._era5_progress = QProgressBar()
        self._era5_progress.setRange(0, 0)
        self._era5_progress.hide()
        left.addWidget(self._era5_progress)
        self._era5_progress_detail = QLabel("")
        self._era5_progress_detail.setWordWrap(True)
        self._era5_progress_detail.setObjectName(OBJ_PROGRESS_DETAIL)
        self._era5_progress_detail.hide()
        left.addWidget(self._era5_progress_detail)
        left.addStretch(1)

        self._era5_controls_scroll = scrolling_control_rail(left)
        outer.addWidget(self._era5_controls_scroll)
        outer.addWidget(self._era5_map, 1)

        self._era5_set_recent()
        self._era5_point_from_spins(center=True)
        self._era5_update_readiness()
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
            self._era5_readiness.setText(
                "Missing ERA5 packages: "
                + ", ".join(missing)
                + '. Install with pip install -e ".[era5]".'
            )
            return
        rc_path = Path(
            os.environ.get("CDSAPI_RC", str(Path.home() / ".cdsapirc"))
        ).expanduser()
        env_profile = bool(
            os.environ.get("CDSAPI_URL") and os.environ.get("CDSAPI_KEY")
        )
        if not env_profile and not rc_path.is_file():
            self._era5_readiness.setText(
                "CDS credentials are not configured. Accept the ERA5 "
                "pressure-level and single-level terms, then save the API "
                "profile as $HOME/.cdsapirc."
            )
        else:
            self._era5_readiness.setText(
                "CDS profile detected. Dataset terms are verified when the "
                "request is submitted; secret values are never displayed."
            )

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

    def _era5_update_state(self, *_args) -> None:
        if not hasattr(self, "_era5_snapped"):
            return
        from sharpmod.tools import era5_extract

        lat = float(self._era5_lat.value())
        lon = float(self._era5_lon.value())
        snapped_lat, snapped_lon = era5_extract._nearest_era5_grid_point(lat, lon)
        self._era5_snapped.setText(
            f"Requested {lat:.4f}, {lon:.4f} → ERA5 grid "
            f"{snapped_lat:.2f}, {snapped_lon:.2f}"
        )
        valid = self._era5_valid_time()
        self._era5_fetch_btn.setEnabled(
            valid <= datetime.now(timezone.utc) and self._era5_worker is None
        )

    def _era5_fetch(self) -> None:
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
        self._era5_worker = worker
        worker.finished_ok.connect(self._on_era5_fetch_ok)
        worker.failed.connect(self._on_era5_fetch_failed)
        worker.cancelled.connect(self._on_era5_fetch_cancelled)
        worker.progress.connect(self._on_era5_progress)
        worker.finished.connect(self._on_era5_fetch_finished)
        self._set_era5_busy(True)
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

    def _on_era5_progress(self, stage) -> None:
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

    def _cancel_era5_fetch(self) -> None:
        worker = self._era5_worker
        if worker is None:
            return
        worker.requestInterruption()
        self._era5_cancel_btn.setEnabled(False)
        set_button_busy(self._era5_cancel_btn, True, "Cancellation requested")
        self._era5_progress_detail.setText(
            "Cancellation requested. A synchronous CDS request already in "
            "flight must return before local cleanup can finish."
        )

    def _on_era5_fetch_cancelled(self) -> None:
        self.statusBar().showMessage("ERA5 fetch cancelled", 5000)

    def _on_era5_fetch_failed(self, message) -> None:
        self.statusBar().showMessage("ERA5 fetch failed")
        QMessageBox.critical(self, APP_NAME, str(message))

    def _on_era5_fetch_finished(self) -> None:
        worker = self.sender()
        if self._era5_worker is worker:
            self._era5_worker = None
            self._set_era5_busy(False)
        worker.deleteLater()

    def _on_era5_fetch_ok(
        self, npz_path, valid_time, snapped_lat, snapped_lon, cache_hit
    ) -> None:
        self._on_era5_progress("rendering")
        QApplication.processEvents()
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
            QMessageBox.critical(
                self, APP_NAME, f"Fetched, but could not display:\n{exc}"
            )
            return
        suffix = " (cache hit)" if cache_hit else ""
        self.statusBar().showMessage(
            f"Opened ERA5 {valid_time:%Y-%m-%d %H}Z{suffix}", 5000
        )


__all__ = ["Era5PickerMixin"]

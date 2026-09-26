"""Forecast source tab builders for model and field views.

The picker controller still owns source state and callbacks. These builders only
assemble each tab's controls and bind them to that controller.
"""

from __future__ import annotations

from qtpy.QtCore import QDate, QEvent, Qt
from qtpy.QtWidgets import (
    QComboBox, QDateEdit, QDoubleSpinBox, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QProgressBar, QPushButton,
    QSizePolicy, QStyle, QToolButton, QVBoxLayout, QWidget,
)

from sharpmod.ui.features.gui_common import (
    SYNOPTIC_HOURS, _most_recent_synoptic, action_label,
    install_month_calendar, scrollable_page,
)
from sharpmod.ui.features.gui_jobs import JobStatus
from sharpmod.ui.features.gui_maps import PointMapWidget, StationMapWidget
from sharpmod.ui.features.gui_workers import _AvailabilityIndicator
from sharpmod.ui.styles.theme import (
    CONTROL_H, FIELD_W, OBJ_CARD, OBJ_CARD_RULE, OBJ_CARD_TOGGLE, OBJ_EMPHASIS,
    OBJ_ERROR_TEXT, OBJ_GHOST, OBJ_HINT, OBJ_NUMERIC, OBJ_PLAIN,
    OBJ_POINT_LOCK, OBJ_PRIMARY, OBJ_PROGRESS_DETAIL, OBJ_SECTION_LABEL,
    OBJ_STATUS, OBJ_WARNING_TEXT, PROGRESS_H, SPACE,
)
from sharpmod.ui.maps.overlays.controllers import (
    HrrrFieldController, OutlookOverlayController, RadarOverlayController,
    StormReportsOverlayController,
)
from sharpmod.ui.picker.cycles import _annotate_cycle_combo, _fill_cycle_combo
from sharpmod.ui.picker.observed_sources import OBSERVED_SOURCES
from sharpmod.ui.picker.layout import (
    ActiveLayerList as _ActiveLayerList,
    DATE_DISPLAY_FORMAT as _DATE_DISPLAY_FORMAT,
    TOWN_LOOKUP_TOOLTIP as _TOWN_LOOKUP_TOOLTIP,
    map_tool_row as _map_tool_row,
    order_rail_cards as _order_rail_cards,
    rail_card as _rail_card,
    rail_form as _rail_form,
    rail_row as _rail_row,
    rail_zoom_row as _rail_zoom_row,
    scrolling_control_rail as _scrolling_control_rail,
    selection_pane as _selection_pane,
    town_lookup_attribution_label as _town_lookup_attribution_label,
)
from sharpmod.ui.picker.selection import install_selection_feedback


class _DownloadSummary(QLabel):
    """Shorten the visible line while retaining its full accessible detail."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._full_text = ""
        self.setTextFormat(Qt.PlainText)

    def set_summary(self, text):
        self._full_text = str(text or "")
        self.setToolTip(self._full_text)
        self.setAccessibleDescription(self._full_text)
        self._sync_text()

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._sync_text()

    def changeEvent(self, event):  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() == QEvent.FontChange:
            self._sync_text()

    def _sync_text(self):
        self.setText(self.fontMetrics().elidedText(
            self._full_text, Qt.ElideRight, max(1, self.width())))


class ForecastPagesMixin:
    """Build model and field panel tabs."""

    def _update_forecast_map_details(self, tab: str) -> None:
        """Show the displayed map's source and scale beside its controls."""
        if tab == "model":
            label = getattr(self, "_model_map_details", None)
            widget = getattr(self, "_model_map", None)
            if label is not None and widget is not None:
                text = widget.legend_details()
                label.setText(text)
                label.setVisible(bool(text))
            return
        if tab == "panels":
            rows = [(panel, label, panel.map.legend_details().splitlines())
                    for panel, label in getattr(self, "_panels_detail_rows", ())
                    if panel.is_active()]
            common = []
            for prefix in ("≈", "Source: "):
                values = [[line for line in lines if line.startswith(prefix)]
                          for _panel, _label, lines in rows]
                if values and all(len(value) == 1 for value in values) \
                        and all(value == values[0] for value in values[1:]):
                    common.append(values[0][0])
            shared = getattr(self, "_panels_shared_details", None)
            if shared is not None:
                shared.setText("\n".join(common))
                shared.setVisible(bool(common))
            for panel, label, lines in rows:
                text = "\n".join(line for line in lines if line not in common)
                label.setText(text)
                label.setVisible(bool(text))
            for panel, label in getattr(self, "_panels_detail_rows", ()):
                if not panel.is_active():
                    label.hide()

    def _sync_panel_activity_rows(self, _count=None) -> None:
        for panel, host in getattr(self, "_panels_activity_rows", ()):
            host.setVisible(panel.is_active())
        self._update_forecast_map_details("panels")

    def _build_model_tab(self) -> QWidget:
        w = QWidget()
        outer = QHBoxLayout(w)
        outer.setContentsMargins(SPACE["md"], SPACE["sm"], SPACE["md"], SPACE["sm"])
        outer.setSpacing(SPACE["md"])

        self._model_syncing_point = False
        self._model_map = PointMapWidget()
        self._model_map._legend_metadata_in_rail = True
        self._model_map.set_projection(self.map_projection())
        self._apply_map_presentation()
        self._refresh_loaded_profile_markers()
        self._model_map.radarSiteSelected.connect(self._model_on_radar_site)
        self._model_map.viewSettled.connect(self._model_on_view_settled)
        self._model_map.pointSelected.connect(self._model_on_map_point)
        self._model_map.pointActivated.connect(lambda _lat, _lon: self._model_fetch())
        self._model_map.boxSelected.connect(self._model_on_box_selected)
        self._model_map.boxCleared.connect(self._model_on_box_cleared)
        self._model_map.boxModeChanged.connect(self._model_sync_box_mode)

        left = QVBoxLayout()
        left.setSpacing(SPACE["md"])
        left.setContentsMargins(0, 0, 0, 0)

        location_box, location_layout = _rail_card("Map location")

        def location_section(title: str) -> QLabel:
            heading = QLabel(title, location_box)
            heading.setObjectName(OBJ_SECTION_LABEL)
            return heading

        def location_rule() -> None:
            rule = QFrame(location_box)
            rule.setFrameShape(QFrame.Shape.NoFrame)
            rule.setObjectName(OBJ_CARD_RULE)
            location_layout.addWidget(rule)

        location_layout.addWidget(location_section("Region"))
        from sharpmod.ui.maps.regions import region_combo as _region_combo

        self._model_area_combo = _region_combo(
            self._model_map, parent=location_box,
            on_changed=self._model_area_changed)
        location_layout.addWidget(self._model_area_combo)
        location_layout.addWidget(location_section("Map tool"))
        location_layout.addLayout(_map_tool_row(self._model_map, allow_box=True))
        location_rule()
        location_layout.addWidget(location_section("Sounding point"))
        point_hint = QLabel("Click the map or enter coordinates below.")
        point_hint.setObjectName(OBJ_HINT)
        point_hint.setWordWrap(True)
        location_layout.addWidget(point_hint)
        point_form = QVBoxLayout()
        point_form.setContentsMargins(0, 0, 0, 0)
        point_form.setSpacing(SPACE["sm"])
        location_layout.addLayout(point_form)
        location_rule()
        location_layout.addWidget(location_section("Map navigation"))
        location_layout.addLayout(_rail_zoom_row(self._model_map))
        location_rule()

        self._model_appearance_toggle = QToolButton(location_box)
        self._model_appearance_toggle.setObjectName(OBJ_CARD_TOGGLE)
        self._model_appearance_toggle.setText("Map appearance")
        self._model_appearance_toggle.setAccessibleName(
            "Show map appearance settings")
        self._model_appearance_toggle.setToolTip(
            "Show legend placement and color scale options")
        self._model_appearance_toggle.setCheckable(True)
        self._model_appearance_toggle.setChecked(False)
        self._model_appearance_toggle.setArrowType(Qt.RightArrow)
        self._model_appearance_toggle.setToolButtonStyle(
            Qt.ToolButtonTextBesideIcon)
        self._model_appearance_toggle.setAutoRaise(True)
        self._model_appearance_toggle.setMinimumHeight(CONTROL_H["md"])
        appearance_content = QWidget(location_box)
        appearance_content.setObjectName(OBJ_PLAIN)
        appearance_layout = QVBoxLayout(appearance_content)
        appearance_layout.setContentsMargins(0, 0, 0, 0)
        appearance_layout.setSpacing(SPACE["xs"])
        # These persisted presentation controls apply to the map and its legend;
        # keep them available without competing with point selection.
        appearance_layout.addWidget(self._legend_corner_combo())
        appearance_layout.addWidget(self._legend_collapsed_check())
        appearance_layout.addWidget(self._scale_lock_check())
        appearance_content.hide()

        def set_model_appearance_visible(visible: bool) -> None:
            visible = bool(visible)
            appearance_content.setVisible(visible)
            self._model_appearance_toggle.setArrowType(
                Qt.DownArrow if visible else Qt.RightArrow)
            action = "Hide" if visible else "Show"
            self._model_appearance_toggle.setAccessibleName(
                f"{action} map appearance settings")
            self._model_appearance_toggle.setToolTip(
                f"{action} legend placement and color scale options")

        self._model_appearance_toggle.toggled.connect(
            set_model_appearance_visible)
        location_layout.addWidget(self._model_appearance_toggle)
        location_layout.addWidget(appearance_content)

        (layer_box, layer_choices, layer_actions,
         self._model_layer_overview) = self._layer_manager_card("On this map")
        self._model_layers_card = layer_box
        self._model_outlook = OutlookOverlayController(self._model_map, parent=self)
        layer_choices.addWidget(self._model_outlook.controls_widget())

        self._model_reports = StormReportsOverlayController(
            self._model_map, parent=self
        )
        self._model_reports.bind_outlook(self._model_outlook)
        layer_choices.addWidget(self._model_reports.controls_widget())
        self._model_radar = RadarOverlayController(
            self._model_map,
            parent=self,
            scope=self._startup_radar_scope(),
            site=self._startup_radar_site(),
            opacity=self._startup_radar_opacity(),
        )
        layer_choices.addWidget(self._model_radar.controls_widget())
        self._model_field = HrrrFieldController(
            self._model_map, parent=self, product=self._startup_field_product(),
            opacity=self._startup_field_opacity(),
        )
        layer_choices.addWidget(self._model_field.controls_widget())
        # Keep optional satellite/observation dependencies out of module import.
        from sharpmod.ui.features.gui_environmental_context import EnvironmentalContextController

        self._model_context = EnvironmentalContextController(
            self._model_map,
            parent=self,
            channel=self._startup_satellite_channel(),
            density=self._startup_surface_density(),
            opacity=self._startup_satellite_opacity(),
        )
        self._model_map.pointSelected.connect(
            self._model_context.on_location_changed
        )
        layer_choices.addWidget(self._model_context.controls_widget())
        self._model_locator = self._add_locator_selector(layer_choices, "model")
        self._install_inline_layer_statuses("model")
        self._model_layers = _ActiveLayerList(parent=layer_box, toolbar_only=True)
        self._model_layers.set_presets(
            self._layer_presets(), current=self._startup_layer_preset())
        self._model_layers.visibilityToggled.connect(self._on_model_layer_toggled)
        self._model_layers.retryRequested.connect(self._on_model_layer_retry)
        self._model_layers.apply_preset_btn.clicked.connect(
            self._on_model_layer_preset)
        layer_actions.addWidget(self._model_layers)
        left.addWidget(layer_box)
        left.addWidget(location_box)
        self._wire_layer_list_refresh("model")
        self._refresh_layer_list("model")

        model_box, model_layout = _rail_card("Model")
        self._model_box = model_box
        self._model_combo = QComboBox()
        self._model_combo.setMinimumHeight(CONTROL_H["md"])
        self._model_combo.currentIndexChanged.connect(self._model_update_cycles)
        from sharpmod.ui.features.gui_selectors import selector_row

        model_layout.addLayout(selector_row(self._model_combo, name="forecast model"))
        # Coverage above description, and at a stronger weight. The domain decides
        # whether the point about to be picked is inside the grid at all, which is
        # actionable; the blurb is background. Both shared one tertiary label
        # before, so the line that can invalidate a selection read as a footnote
        # to the one that cannot.
        self._model_domain = QLabel("")
        self._model_domain.setObjectName(OBJ_STATUS)
        self._model_domain.setVisible(False)
        model_layout.addWidget(self._model_domain)
        self._model_notes = QLabel("")
        self._model_notes.setWordWrap(True)
        self._model_notes.setObjectName(OBJ_HINT)
        self._model_notes.setVisible(False)
        model_layout.addWidget(self._model_notes)
        self._model_map_details = QLabel("")
        self._model_map_details.setObjectName(OBJ_HINT)
        self._model_map_details.setTextFormat(Qt.PlainText)
        self._model_map_details.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self._model_map_details.setWordWrap(True)
        self._model_map_details.setAccessibleName("Forecast map source and scale")
        self._model_map_details.hide()
        model_layout.addWidget(self._model_map_details)
        self._model_field.statusChanged.connect(
            lambda _text: self._update_forecast_map_details("model"))
        left.addWidget(model_box)

        # No minimum height: the card sizes to its own rows. A fixed 210 px
        # floor padded it well past its content and was the largest single
        # reason this rail scrolled on a maximized window.
        time_box, time_grid = _rail_form("Run / valid time (UTC)")
        self._model_date = QDateEdit()
        self._model_date.setDisplayFormat(_DATE_DISPLAY_FORMAT)
        self._model_date.setCalendarPopup(True)
        install_month_calendar(self._model_date)
        self._model_date.setDate(QDate.currentDate())
        self._model_date.setMaximumDate(QDate.currentDate().addDays(1))
        self._model_date.dateChanged.connect(self._model_update_valid_label)
        _rail_row(time_grid, 0, "Date:", self._model_date, width="timestamp")
        self._model_cycle = QComboBox()
        self._model_cycle.setObjectName(OBJ_NUMERIC)
        self._model_cycle.currentIndexChanged.connect(self._model_update_fxx)
        recent = QToolButton()
        recent.setText("Most recent")
        recent.setToolTip("Jump to the most recent published cycle")
        recent.clicked.connect(self._model_set_recent)
        _rail_row(time_grid, 1, "Cycle:", self._model_cycle, trailing=recent)
        self._model_fxx_combo = QComboBox()
        self._model_fxx_combo.setObjectName(OBJ_NUMERIC)
        self._model_fxx_combo.currentIndexChanged.connect(
            self._model_update_valid_label
        )
        _rail_row(
            time_grid, 2, "Forecast:", self._model_fxx_combo, width="timestamp"
        )
        self._model_valid_lbl = QLabel("")
        self._model_valid_lbl.setObjectName(OBJ_EMPHASIS)
        self._model_valid_lbl.setWordWrap(True)
        time_grid.addWidget(self._model_valid_lbl, 3, 0, 1, 3)
        # T21.1: the persistent time header, naming requested vs actual, run
        # and lead, and the live/history mode -- the same line the map paints
        # in its legend, so the rail and the pixels cannot disagree.
        self._model_time_lbl = QLabel("")
        self._model_time_lbl.setObjectName(OBJ_HINT)
        self._model_time_lbl.setWordWrap(True)
        self._model_time_lbl.setAccessibleName("Forecast Model map time")
        time_grid.addWidget(self._model_time_lbl, 4, 0, 1, 3)
        # T21.4: live follows new runs; history pins the chosen run/hour even
        # as newer cycles publish. Historical refreshes keep the labelled
        # frame; the current overlay never swaps in unasked.
        self._model_mode_combo = QComboBox()
        self._model_mode_combo.setAccessibleName("Forecast Model map mode")
        self._model_mode_combo.setToolTip(
            "Live follows the newest published run. Historical pins this "
            "run and hour: refreshes keep the labelled frame.")
        self._model_mode_combo.addItem("Live — follow new runs", "live")
        self._model_mode_combo.addItem("Historical — pin this run/hour",
                                       "history")
        self._model_mode_combo.currentIndexChanged.connect(
            self._on_model_map_mode_changed)
        time_grid.addWidget(self._model_mode_combo, 5, 0, 1, 3)
        self._model_availability = _AvailabilityIndicator()
        self._model_availability.setToolTip(
            "Catalog check only; Fetch remains available if this is uncertain"
        )
        time_grid.addWidget(self._model_availability, 6, 0, 1, 3)
        self._model_use_available_btn = QPushButton("Use available cycle")
        self._model_use_available_btn.clicked.connect(self._use_model_available_run)
        self._model_use_available_btn.hide()
        time_grid.addWidget(self._model_use_available_btn, 7, 0, 1, 3)
        # T21.3: the frame scrubber -- the T09 strip over this run's offered
        # hours, click-to-jump, so the rail and the sounding timeline speak
        # one clock.
        from sharpmod.ui.features.gui_timeline_playback import MapFramePlayback

        self._model_frame_strip = MapFramePlayback(time_box)
        self._model_frame_strip.setAccessibleName(
            "Forecast Model frame scrubber")
        self._model_frame_strip.setToolTip(
            "Offered forecast hours for this run. Choose a cell to depict "
            "that hour; only offered hours are shown, so no hour here is "
            "interpolated.")
        self._model_frame_strip.frameChosen.connect(
            self._on_model_frame_chosen)
        time_grid.addWidget(self._model_frame_strip, 8, 0, 1, 3)
        left.addWidget(time_box)

        self._model_lat = QDoubleSpinBox()
        self._model_lat.setRange(-90.0, 90.0)
        self._model_lat.setDecimals(4)
        self._model_lat.setSuffix("\u00b0")
        self._model_lat.setSingleStep(0.25)
        self._model_lat.setAccessibleName("Forecast sounding latitude")
        self._model_lat.setValue(35.6300)
        self._model_lat.setMinimumWidth(FIELD_W["wide"])
        self._model_lat.setMinimumHeight(CONTROL_H["md"])
        self._model_lat.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._model_lat.valueChanged.connect(
            lambda _value: self._model_point_from_spins()
        )
        self._model_lon = QDoubleSpinBox()
        self._model_lon.setRange(-180.0, 180.0)
        self._model_lon.setDecimals(4)
        self._model_lon.setSuffix("\u00b0")
        self._model_lon.setSingleStep(0.25)
        self._model_lon.setAccessibleName("Forecast sounding longitude")
        self._model_lon.setValue(-97.4400)
        self._model_lon.setMinimumWidth(FIELD_W["wide"])
        self._model_lon.setMinimumHeight(CONTROL_H["md"])
        self._model_lon.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._model_lon.valueChanged.connect(
            lambda _value: self._model_point_from_spins()
        )
        coordinate_grid = QGridLayout()
        coordinate_grid.setContentsMargins(0, 0, 0, 0)
        coordinate_grid.setHorizontalSpacing(SPACE["sm"])
        coordinate_grid.setVerticalSpacing(SPACE["xxs"])
        coordinate_grid.setColumnStretch(0, 1)
        coordinate_grid.setColumnStretch(1, 1)
        latitude_label = QLabel("Latitude")
        latitude_label.setObjectName(OBJ_HINT)
        latitude_label.setBuddy(self._model_lat)
        longitude_label = QLabel("Longitude")
        longitude_label.setObjectName(OBJ_HINT)
        longitude_label.setBuddy(self._model_lon)
        coordinate_grid.addWidget(latitude_label, 0, 0)
        coordinate_grid.addWidget(longitude_label, 0, 1)
        coordinate_grid.addWidget(self._model_lat, 1, 0)
        coordinate_grid.addWidget(self._model_lon, 1, 1)
        point_form.addLayout(coordinate_grid)

        place_name_label = QLabel("Place name (optional)")
        place_name_label.setObjectName(OBJ_HINT)
        point_form.addWidget(place_name_label)
        self._model_loc = QLineEdit()
        self._model_loc.setMinimumHeight(CONTROL_H["md"])
        self._model_loc.setPlaceholderText("Auto-detect from coordinates")
        self._model_loc.setToolTip(_TOWN_LOOKUP_TOOLTIP)
        self._model_loc.setAccessibleName("Optional sounding place name")
        place_name_label.setBuddy(self._model_loc)
        point_form.addWidget(self._model_loc)
        point_form.addWidget(_town_lookup_attribution_label())
        self._model_point_status = QLabel("")
        self._model_point_status.setWordWrap(True)
        self._model_point_status.setObjectName(OBJ_STATUS)
        self._model_point_status.setAccessibleName(
            "Forecast point and model coverage status")
        point_form.addWidget(self._model_point_status)
        from sharpmod.ui.picker.coordinates import coordinate_paste_button

        self._model_paste_coordinates = coordinate_paste_button(
            self, self._model_lat, self._model_lon, self._model_point_from_spins,
            source="Forecast Model",
        )
        self._model_paste_coordinates.setText("Paste coordinates")
        self._model_paste_coordinates.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed)
        point_form.addWidget(self._model_paste_coordinates)
        self._model_lock_btn = QPushButton("Lock point")
        self._model_lock_btn.setObjectName(OBJ_POINT_LOCK)
        self._model_lock_btn.setAccessibleName("Lock sounding point")
        self._model_lock_btn.setCheckable(True)
        self._model_lock_btn.setToolTip(
            "Prevent map clicks from moving the sounding point. "
            "Inspect, pan, zoom, history, and box drawing remain available.")
        self._model_lock_btn.toggled.connect(self._model_lock_toggled)
        self._model_recent_btn = QPushButton("Previous point")
        self._model_recent_btn.setAccessibleName(
            "Restore previous sounding point")
        self._model_recent_btn.setToolTip(
            "Return to the previous sounding point (U with map focus). "
            "History and session restores never move the point.")
        self._model_recent_btn.clicked.connect(self._model_restore_recent_point)
        self._model_recent_btn.setEnabled(False)
        point_lock_row = QHBoxLayout()
        point_lock_row.setSpacing(SPACE["sm"])
        point_lock_row.addWidget(self._model_lock_btn, 1)
        point_lock_row.addWidget(self._model_recent_btn, 1)
        lock_host = QWidget(location_box)
        lock_host.setObjectName(OBJ_PLAIN)
        lock_host.setLayout(point_lock_row)
        point_form.addWidget(lock_host)
        # Only the ensemble products take a member, so the card is hidden for
        # the deterministic ones rather than shown permanently disabled. It is
        # meaningless for HRRR or RRFS and cost a full card of height on every
        # model.
        self._model_member_box, member_layout = _rail_card("Ensemble member")
        self._model_member = QLineEdit()
        self._model_member.setMinimumHeight(CONTROL_H["md"])
        self._model_member.textChanged.connect(self._queue_model_availability)
        member_layout.addWidget(self._model_member)
        self._model_member_box.hide()
        left.addWidget(self._model_member_box)

        # T23: a completed geographic box remains a first-class editable
        # selection.  This card is hidden until Draw area is armed or bounds
        # exist, so the ordinary point workflow pays no permanent rail space.
        self._model_box_editor = self._build_model_box_editor()
        self._model_box_editor.hide()
        left.addWidget(self._model_box_editor)

        # Keep the primary action reachable below the map. During a request the
        # job strip takes its place; a disabled full-width button adds no useful
        # information and crowds the transfer status.
        self._model_fetch_btn = QPushButton(action_label("load_sounding"))
        self._model_fetch_btn.setObjectName(OBJ_PRIMARY)
        self._model_fetch_btn.setMinimumHeight(CONTROL_H["lg"])
        self._model_fetch_btn.setToolTip(
            "Load a sounding for the currently selected model, run, and point")
        self._model_fetch_btn.clicked.connect(self._model_fetch)

        # Secondary actions share the rail width and remain reachable by its
        # native vertical scroll when several settings cards are expanded.
        action_row = QHBoxLayout()
        self._model_timeline_btn = QPushButton("Timeline…")
        self._model_timeline_btn.setMinimumHeight(CONTROL_H["lg"])
        self._model_timeline_btn.setToolTip(
            "Fetch several forecast hours into an animated timeline"
        )
        self._model_timeline_btn.clicked.connect(self._model_fetch_timeline)
        action_row.addWidget(self._model_timeline_btn, 1)
        self._model_box_btn = QPushButton("Draw area")
        self._model_box_btn.setCheckable(True)
        self._model_box_btn.setMinimumHeight(CONTROL_H["lg"])
        self._model_box_btn.setToolTip(
            "Draw or edit a geographic rectangle. Drag a corner handle to "
            "resize it or drag its interior to move it. Drawing and editing "
            "only refresh the sample/coverage estimate; they never download.\n"
            "Use Review & extract in Selected area when the bounds are ready. "
            "Middle-drag or right-drag still pans, and Shift-drag works in any tool."
        )
        self._model_box_btn.toggled.connect(self._model_box_mode_toggled)
        action_row.addWidget(self._model_box_btn, 1)
        self._model_cancel_btn = QPushButton("Cancel")
        self._model_cancel_btn.setObjectName(OBJ_GHOST)
        self._model_cancel_btn.setMinimumHeight(CONTROL_H["md"])
        self._model_cancel_btn.clicked.connect(self._cancel_model_fetch)
        self._model_cancel_btn.hide()
        self._model_retry_btn = QPushButton("Retry")
        self._model_retry_btn.setObjectName(OBJ_PRIMARY)
        self._model_retry_btn.setMinimumHeight(CONTROL_H["md"])
        self._model_retry_btn.setAccessibleName(
            "Retry the failed forecast download")
        self._model_retry_btn.setToolTip(
            "Retry the saved request without changing its model, run, or point")
        self._model_retry_btn.clicked.connect(self._retry_model_job)
        self._model_retry_btn.hide()
        self._model_new_request_btn = QPushButton("Load selected")
        self._model_new_request_btn.setMinimumHeight(CONTROL_H["md"])
        self._model_new_request_btn.setAccessibleName(
            "Load a new sounding from the current selection")
        self._model_new_request_btn.setToolTip(
            "Start a new request with the model, run, and point selected now")
        self._model_new_request_btn.clicked.connect(self._model_fetch)
        self._model_new_request_btn.hide()
        # Lazy import keeps the request planner and comparison dialog out of
        # first-paint startup.  The coordinator owns the bounded batch worker;
        # this file only contributes its slot in the existing action row.
        from sharpmod.ui.features.gui_model_compare import install_model_compare_control

        install_model_compare_control(self, action_row)

        self._model_progress = QProgressBar()
        self._model_progress.setMinimumHeight(PROGRESS_H)
        self._model_progress.setTextVisible(False)
        self._model_progress.hide()
        self._model_progress.setObjectName("forecastDownloadProgress")
        self._model_progress.setAccessibleName("Forecast download progress")
        self._model_progress.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._model_job_progress = QProgressBar()
        self._model_job_progress.setMinimumHeight(PROGRESS_H)
        self._model_job_progress.setTextVisible(False)
        self._model_job_progress.hide()
        self._model_job_progress.setObjectName("forecastJobProgress")
        self._model_job_progress.setAccessibleName("Forecast work progress")
        self._model_job_progress.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._model_progress_detail = QLabel("")
        self._model_progress_detail.setWordWrap(True)
        self._model_progress_detail.setObjectName(OBJ_PROGRESS_DETAIL)
        self._model_progress_detail.setAccessibleName(
            "Forecast download stage and transfer details")
        self._model_progress_detail.hide()
        self._model_job_summary = _DownloadSummary()
        self._model_job_summary.setObjectName(OBJ_PROGRESS_DETAIL)
        self._model_job_summary.setAccessibleName("Forecast download status")
        self._model_job_summary.setWordWrap(False)
        self._model_job_summary.setMinimumWidth(0)
        self._model_job_summary.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._model_job_summary.hide()
        self._model_job = JobStatus(self)
        self._model_job_retry = None
        self._model_job.cancelRequested.connect(self._cancel_model_fetch)
        self._model_job.retryRequested.connect(self._retry_model_job)
        self._model_job.snapshotChanged.connect(self._model_job_presentation_changed)
        self._model_job_scroll = scrollable_page(
            self._model_job, parent=self,
            accessible_name="Forecast acquisition progress and recovery details",
        )
        self._model_job_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarAlwaysOff)
        self._model_job_scroll.setMaximumHeight(self.fontMetrics().height() * 8)
        self._model_job_scroll.setMinimumHeight(self.fontMetrics().height() * 3)
        self._model_job_scroll.hide()
        self._model_job_details = QWidget()
        self._model_job_details.setObjectName(OBJ_PLAIN)
        details_layout = QVBoxLayout(self._model_job_details)
        details_layout.setContentsMargins(0, SPACE["xs"], 0, 0)
        details_layout.setSpacing(SPACE["xs"])
        detail_rule = QFrame(self._model_job_details)
        detail_rule.setFrameShape(QFrame.Shape.HLine)
        detail_rule.setObjectName(OBJ_CARD_RULE)
        details_layout.addWidget(detail_rule)
        detail_row = QHBoxLayout()
        detail_row.setSpacing(SPACE["sm"])
        detail_heading = QLabel("Request", self._model_job_details)
        detail_heading.setObjectName(OBJ_SECTION_LABEL)
        detail_row.addWidget(detail_heading)
        self._model_detail_request = _DownloadSummary(self._model_job_details)
        self._model_detail_request.setObjectName(OBJ_HINT)
        self._model_detail_request.setAccessibleName("Forecast request details")
        self._model_detail_request.setMinimumWidth(0)
        self._model_detail_request.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Fixed)
        detail_row.addWidget(self._model_detail_request, 1)
        self._model_detail_outcome = _DownloadSummary(self._model_job_details)
        self._model_detail_outcome.setObjectName(OBJ_PROGRESS_DETAIL)
        self._model_detail_outcome.setAccessibleName("Forecast request outcome")
        self._model_detail_outcome.hide()
        detail_row.addWidget(self._model_detail_outcome)
        details_layout.addLayout(detail_row)
        self._model_detail_issue = QLabel(self._model_job_details)
        self._model_detail_issue.setObjectName(OBJ_ERROR_TEXT)
        self._model_detail_issue.setAccessibleName("Full forecast failure reason")
        self._model_detail_issue.setTextFormat(Qt.PlainText)
        self._model_detail_issue.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self._model_detail_issue.setWordWrap(True)
        self._model_detail_issue.setMinimumWidth(0)
        self._model_detail_issue.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._model_detail_issue.hide()
        details_layout.addWidget(self._model_detail_issue)
        self._model_detail_retained = _DownloadSummary(self._model_job_details)
        self._model_detail_retained.setObjectName(OBJ_HINT)
        self._model_detail_retained.setAccessibleName("Retained forecast result")
        self._model_detail_retained.setMinimumWidth(0)
        self._model_detail_retained.setSizePolicy(
            QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._model_detail_retained.hide()
        details_layout.addWidget(self._model_detail_retained)
        # Existing workers still update these diagnostic widgets. Keep their
        # data and accessible descriptions without repeating the whole shared
        # JobStatus panel beneath the same error and progress strip.
        legacy_details = QWidget(self._model_job_details)
        legacy_details.setObjectName(OBJ_PLAIN)
        legacy_layout = QVBoxLayout(legacy_details)
        legacy_layout.setContentsMargins(0, 0, 0, 0)
        legacy_layout.addWidget(self._model_progress_detail)
        legacy_layout.addWidget(self._model_job_scroll)
        legacy_details.hide()
        self._model_job_details.hide()
        self._model_job_details_btn = QPushButton("Details ▾")
        self._model_job_details_btn.setObjectName(OBJ_GHOST)
        self._model_job_details_btn.setCheckable(True)
        self._model_job_details_btn.setMinimumHeight(CONTROL_H["md"])
        self._model_job_details_btn.setAccessibleName(
            "Show forecast download and retry details")
        self._model_job_details_btn.toggled.connect(
            self._model_job_details.setVisible)
        self._model_job_details_btn.toggled.connect(
            lambda expanded: self._model_job_details_btn.setText(
                "Details ▴" if expanded else "Details ▾"))
        self._model_job_details_btn.toggled.connect(
            lambda expanded: self._model_job_details_btn.setAccessibleName(
                ("Hide" if expanded else "Show")
                + " forecast download and retry details"))
        self._model_job_details_btn.hide()
        self._model_job_strip = QFrame()
        self._model_job_strip.setObjectName(OBJ_CARD)
        strip_layout = QVBoxLayout(self._model_job_strip)
        strip_layout.setContentsMargins(
            SPACE["sm"], SPACE["xs"], SPACE["sm"], SPACE["xs"])
        strip_layout.setSpacing(SPACE["xs"])
        strip_header = QHBoxLayout()
        strip_header.setSpacing(SPACE["sm"])
        strip_header.addWidget(self._model_job_summary, 1)
        strip_header.addWidget(self._model_cancel_btn)
        strip_header.addWidget(self._model_retry_btn)
        strip_header.addWidget(self._model_new_request_btn)
        strip_header.addWidget(self._model_job_details_btn)
        strip_layout.addLayout(strip_header)
        strip_layout.addWidget(self._model_progress)
        strip_layout.addWidget(self._model_job_progress)
        strip_layout.addWidget(self._model_job_details)
        self._model_job_strip.hide()

        left.addLayout(action_row)

        # The withheld-model list is reference detail about the choice above,
        # not a control, so it rides on the model combo's tooltip instead of
        # holding three word-wrapped lines open at the bottom of the rail.
        unsupported_text = self._model_unsupported_text()
        if unsupported_text:
            self._model_combo.setToolTip(unsupported_text)
        left.addStretch(1)

        _order_rail_cards(left)
        self._model_controls_scroll = _scrolling_control_rail(left)
        self._model_selection_pane = _selection_pane(
            self._model_controls_scroll, self._model_map, settings=self._settings,
            key="forecast-model", title="Forecast model",
        )
        coverage_guidance = QHBoxLayout()
        coverage_guidance.setContentsMargins(0, 0, 0, 0)
        coverage_guidance.setSpacing(SPACE["xs"])
        model_layout.insertLayout(1, coverage_guidance)
        self._model_selection_pane.embed_guidance(coverage_guidance)
        self._model_selection_pane.feedback.setMinimumWidth(0)
        self._model_selection_pane.feedback.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._model_selection_pane.corrective.setSizePolicy(
            QSizePolicy.Fixed, QSizePolicy.Fixed)
        outer.addWidget(self._model_selection_pane)
        self._model_selection_pane.actions.addWidget(self._model_job_strip)
        self._model_selection_pane.actions.addWidget(self._model_fetch_btn)
        # This explicit gate remains available when the configuration rail is
        # collapsed. It appears only after a geographic area is selected.
        self._model_box_extract_action = QPushButton("Review selected area…")
        self._model_box_extract_action.setObjectName(OBJ_PRIMARY)
        self._model_box_extract_action.setMinimumHeight(CONTROL_H["lg"])
        self._model_box_extract_action.clicked.connect(self._model_extract_box)
        self._model_box_extract_action.hide()
        self._model_selection_pane.actions.addWidget(self._model_box_extract_action)
        QWidget.setTabOrder(self._model_cancel_btn, self._model_retry_btn)
        QWidget.setTabOrder(self._model_retry_btn, self._model_new_request_btn)
        QWidget.setTabOrder(self._model_new_request_btn, self._model_job_details_btn)
        QWidget.setTabOrder(self._model_job_details_btn, self._model_fetch_btn)

        self._model_area_changed(self._model_area_combo.currentText())
        self._model_mode_combo.setCurrentIndex(max(0, self._model_mode_combo.findData(
            self._startup_map_mode("model"))))
        self._apply_map_mode("model", self._startup_map_mode("model"))
        self._model_set_recent()
        self._model_point_from_spins(center=True)
        self._sync_inspection_source("_model_map")
        self._apply_stored_map_tool("Forecast Model")
        install_selection_feedback(self, self._model_selection_pane, "model", self._model_fetch_btn)
        return w

    def _model_job_presentation_changed(self, snapshot) -> None:
        """Present the current job without compressing its status into a button."""
        details = self._model_job_details_btn
        summary = self._model_job_summary
        if snapshot.token != getattr(self, "_model_presented_token", None):
            self._model_presented_token = snapshot.token
            details.setChecked(False)
        # Recovery stays beside the status, even with diagnostics collapsed.
        self._model_job.cancel_button.hide()
        self._model_job.retry_button.hide()
        retryable = (
            snapshot.state in {"failed", "partial", "cancelled"}
            and snapshot.retryable
        )
        self._model_retry_btn.setVisible(retryable)
        self._model_new_request_btn.setVisible(retryable)
        self._model_new_request_btn.setEnabled(self._model_fetch_btn.isEnabled())
        if snapshot.state in {"superseded", "completed"}:
            details.setChecked(False)
            self._model_job_strip.hide()
            self._model_fetch_btn.show()
            self._model_progress.hide()
            self._model_job_progress.hide()
            return

        active = snapshot.state in {"running", "cancelling"}
        self._model_job_strip.show()
        self._model_fetch_btn.setVisible(not active and not retryable)

        if snapshot.state == "running":
            message = snapshot.stage
        elif snapshot.state == "cancelling":
            message = "Cancellation requested; waiting for the current step"
        else:
            message = snapshot.message or snapshot.state.capitalize()
        title = (
            "Sounding download" if snapshot.operation == "Point-model acquisition"
            else snapshot.operation
        )
        summary_style = (
            OBJ_ERROR_TEXT if snapshot.state == "failed" else
            OBJ_WARNING_TEXT if snapshot.state in {
                "partial", "cancelled", "cancelling"
            } else
            OBJ_PROGRESS_DETAIL
        )
        if summary.objectName() != summary_style:
            summary.setObjectName(summary_style)
            summary.style().unpolish(summary)
            summary.style().polish(summary)
        summary_text = (
            message if snapshot.state == "running" and message.startswith("Downloading ")
            else "Download failed" if snapshot.state == "failed"
            else "Download partly completed" if snapshot.state == "partial"
            else "Download cancelled" if snapshot.state == "cancelled"
            else f"{title} · {message}"
        )
        summary.set_summary(summary_text)
        summary.setToolTip(message)
        summary.setAccessibleDescription(message)
        summary.show()
        details.show()
        self._model_detail_request.set_summary(snapshot.affected_input)
        counts = snapshot.counts
        outcome = ", ".join(
            f"{value} {name}" for name, value in (
                ("completed", counts.completed), ("failed", counts.failed),
                ("cancelled", counts.cancelled), ("unavailable", counts.unavailable),
                ("unknown", counts.unknown), ("not attempted", counts.unattempted),
            ) if value
        )
        self._model_detail_outcome.set_summary(outcome)
        self._model_detail_outcome.setToolTip(counts.summary)
        self._model_detail_outcome.setAccessibleDescription(counts.summary)
        self._model_detail_outcome.setVisible(bool(outcome))
        show_issue = snapshot.state in {"failed", "partial", "cancelled"}
        self._model_detail_issue.setText(message if show_issue else "")
        self._model_detail_issue.setVisible(show_issue and bool(message))
        self._model_detail_retained.set_summary(
            f"Saved result · {snapshot.retained}" if snapshot.retained else "")
        self._model_detail_retained.setVisible(bool(snapshot.retained))
        if snapshot.state in {"failed", "partial", "cancelled"}:
            self._model_progress.hide()
            self._model_job_progress.hide()
            return

        # Point and box work have true byte/work progress in the existing bar.
        # Other jobs use the honest item counts in their shared snapshots.
        if snapshot.operation in {"Point-model acquisition", "Box sounding extraction"}:
            self._model_job_progress.hide()
            return
        if snapshot.total is None:
            self._model_job_progress.setRange(0, 0)
        else:
            self._model_job_progress.setRange(0, max(1, snapshot.total))
            self._model_job_progress.setValue(snapshot.done)
        self._model_job_progress.show()

    # Panels
    def _build_panels_tab(self) -> QWidget:
        """Build the synchronized HRRR field comparison tab.

        Deliberately HRRR-only, so this rail carries no model chooser. Every
        field in the catalogue is an HRRR product, and offering a model list that
        had one entry would imply a choice that does not exist.
        """
        from sharpmod.ui.maps.panels import PANEL_COUNTS, MapPanelsView

        w = QWidget()
        outer = QHBoxLayout(w)
        outer.setContentsMargins(SPACE["md"], SPACE["sm"], SPACE["md"], SPACE["sm"])
        outer.setSpacing(SPACE["md"])

        self._panels_syncing_point = False
        count, products, _preset, memory = self._stored_panel_layout()
        self._panels_view = MapPanelsView(panel_count=count, products=products)
        try:
            self._panels_view.set_category_memory(memory)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            pass
        self._refresh_loaded_profile_markers()
        self._panels_view.layoutChanged.connect(self._remember_panel_layout)
        self._panels_view.productChanged.connect(self._remember_panel_layout)
        self._panels_view.pointSelected.connect(self._panels_on_point)
        self._panels_view.pointActivated.connect(
            lambda _lat, _lon: self._panels_fetch_sounding()
        )
        self._panels_view.viewSettled.connect(self._panels_on_view_settled)
        self._panels_view.radarSiteSelected.connect(self._panels_on_radar_site)
        # Every panel at once, through one controller and one fetch. See
        # ``MapPanelsView.shared_map``.
        shared = self._panels_view.shared_map()

        left = QVBoxLayout()
        left.setSpacing(SPACE["md"])
        left.setContentsMargins(0, 0, 0, 0)

        def section_heading(title: str, parent: QWidget) -> QLabel:
            label = QLabel(title, parent)
            label.setObjectName(OBJ_SECTION_LABEL)
            return label

        def section_rule(parent: QWidget) -> QFrame:
            rule = QFrame(parent)
            rule.setFrameShape(QFrame.Shape.NoFrame)
            rule.setObjectName(OBJ_CARD_RULE)
            return rule

        setup_box, setup_layout = _rail_card("Field setup")
        setup_layout.addWidget(section_heading("Comparison", setup_box))
        layout_grid = QGridLayout()
        layout_grid.setHorizontalSpacing(SPACE["sm"])
        layout_grid.setVerticalSpacing(SPACE["sm"])
        layout_grid.setColumnMinimumWidth(0, FIELD_W["label"])
        layout_grid.setColumnStretch(1, 1)
        setup_layout.addLayout(layout_grid)
        self._panels_count_combo = QComboBox()
        for value in PANEL_COUNTS:
            self._panels_count_combo.addItem(f"{value} panels", value)
        index = self._panels_count_combo.findData(self._panels_view.panel_count())
        self._panels_count_combo.setCurrentIndex(max(0, index))
        self._panels_count_combo.setMinimumHeight(CONTROL_H["md"])
        self._panels_count_combo.setAccessibleName("Field panel layout")
        self._panels_count_combo.setToolTip(
            "Show two or four field panels. Hidden panels keep their "
            "fields without fetching.")
        self._panels_count_combo.currentIndexChanged.connect(
            self._panels_count_changed
        )
        _rail_row(layout_grid, 0, "Layout:", self._panels_count_combo)
        from sharpmod.ui.maps.panels import PANEL_PRESETS

        self._panels_preset_combo = QComboBox()
        for key, entry in PANEL_PRESETS.items():
            self._panels_preset_combo.addItem(str(entry.get("label", key)), key)
            self._panels_preset_combo.setItemData(
                self._panels_preset_combo.count() - 1,
                str(entry.get("why", "")),
                Qt.ToolTipRole,
            )
        self._panels_preset_combo.setMinimumHeight(CONTROL_H["md"])
        self._panels_preset_combo.setAccessibleName("Field panel preset")
        self._panels_preset_combo.setToolTip(
            "Choosing a preset applies its four fields immediately. Select the "
            "same preset again to restore it after editing a panel. Preset "
            "names describe ingredients, never an official forecast or warning.")
        _rail_row(layout_grid, 1, "Preset:", self._panels_preset_combo)
        self._panels_preset_combo.activated.connect(
            lambda _index: self._panels_preset_changed())
        try:
            stored_preset = str(
                self._settings.value("panels/preset", "") or "").strip()
        except Exception:  # noqa: BLE001 - an unreadable INI is not fatal
            stored_preset = ""
        if stored_preset:
            from sharpmod.ui.maps.panels import migrate_panel_preset_choice

            preset_index = self._panels_preset_combo.findData(
                migrate_panel_preset_choice(stored_preset))
            if preset_index >= 0:
                self._panels_preset_combo.blockSignals(True)
                try:
                    self._panels_preset_combo.setCurrentIndex(preset_index)
                finally:
                    self._panels_preset_combo.blockSignals(False)
        panels_hint = QLabel("Scroll: zoom · drag: pan · click: point")
        panels_hint.setObjectName(OBJ_HINT)
        panels_hint.setToolTip(
            "Every panel follows the same view. Middle/right-drag pans; "
            "right-click offers zoom, fit, centre, and view history. "
            "Click to place the sounding point, double-click to fetch it, "
            "or hover to compare one location across all panels."
        )
        panels_hint.setWordWrap(False)
        layout_grid.addWidget(panels_hint, 2, 0, 1, 3)
        setup_layout.addWidget(section_rule(setup_box))
        setup_layout.addWidget(section_heading("Run / valid time (UTC)", setup_box))

        location_box, location_layout = _rail_card("Map location")
        location_layout.addWidget(section_heading("Region", location_box))
        from sharpmod.ui.maps.regions import region_combo as _panels_region_combo

        self._panels_area_combo = _panels_region_combo(
            self._panels_view._panels[0].map, parent=location_box,
            on_changed=self._panels_area_changed)
        location_layout.addWidget(self._panels_area_combo)
        location_layout.addWidget(section_heading("Map tool", location_box))
        # The tool/zoom rows follow the last-clicked panel (T24.3): the maps
        # share one view and one sounding point, so the active panel is the
        # one whose tool, lock, and navigation the rows describe.
        self._panels_tool_row_host = QWidget(location_box)
        self._panels_tool_row_host.setObjectName(OBJ_PLAIN)
        self._panels_tool_layout = QVBoxLayout(self._panels_tool_row_host)
        self._panels_tool_layout.setContentsMargins(0, 0, 0, 0)
        self._panels_tool_layout.setSpacing(SPACE["xs"])
        location_layout.addWidget(self._panels_tool_row_host)
        self._panels_zoom_row_host = QWidget(location_box)
        self._panels_zoom_row_host.setObjectName(OBJ_PLAIN)
        self._panels_zoom_layout = QVBoxLayout(self._panels_zoom_row_host)
        self._panels_zoom_layout.setContentsMargins(0, 0, 0, 0)
        self._rebuild_panels_tool_rows()
        location_layout.addWidget(section_rule(location_box))
        location_layout.addWidget(section_heading("Sounding point", location_box))

        # The same unified layer manager the two map tabs carry, minus the model field:
        # each panel chooses that for itself, and the card would be offering a
        # fifth answer to a question already asked four times above the maps.
        (layer_box, layer_choices, layer_actions,
         self._panels_layer_overview) = self._layer_manager_card(
             "On every panel")
        self._panels_layers_card = layer_box
        self._panels_outlook = OutlookOverlayController(shared, parent=self)
        layer_choices.addWidget(self._panels_outlook.controls_widget())
        self._panels_reports = StormReportsOverlayController(shared, parent=self)
        self._panels_reports.bind_outlook(self._panels_outlook)
        layer_choices.addWidget(self._panels_reports.controls_widget())
        self._panels_radar = RadarOverlayController(
            shared,
            parent=self,
            scope=self._startup_radar_scope(),
            site=self._startup_radar_site(),
        )
        layer_choices.addWidget(self._panels_radar.controls_widget())
        from sharpmod.ui.features.gui_environmental_context import EnvironmentalContextController

        self._panels_context = EnvironmentalContextController(
            shared,
            parent=self,
            channel=self._startup_satellite_channel(),
            density=self._startup_surface_density(),
        )
        self._panels_view.pointSelected.connect(
            self._panels_context.on_location_changed
        )
        layer_choices.addWidget(self._panels_context.controls_widget())
        self._panels_locator = self._add_locator_selector(layer_choices, "panels")
        self._install_inline_layer_statuses("panels")
        self._panels_layers = _ActiveLayerList(parent=layer_box, toolbar_only=True)
        self._panels_layers.set_presets(
            self._layer_presets(), current=self._startup_layer_preset())
        self._panels_layers.visibilityToggled.connect(self._on_panels_layer_toggled)
        self._panels_layers.retryRequested.connect(self._on_panels_layer_retry)
        self._panels_layers.apply_preset_btn.clicked.connect(
            self._on_panels_layer_preset)
        layer_actions.addWidget(self._panels_layers)
        left.addWidget(layer_box)
        self._wire_layer_list_refresh("panels")
        self._refresh_layer_list("panels")

        time_grid = QGridLayout()
        time_grid.setHorizontalSpacing(SPACE["sm"])
        time_grid.setVerticalSpacing(SPACE["sm"])
        time_grid.setColumnMinimumWidth(0, FIELD_W["label"])
        time_grid.setColumnStretch(1, 1)
        setup_layout.addLayout(time_grid)
        self._panels_date = QDateEdit()
        self._panels_date.setDisplayFormat(_DATE_DISPLAY_FORMAT)
        self._panels_date.setCalendarPopup(True)
        install_month_calendar(self._panels_date)
        self._panels_date.setDate(QDate.currentDate())
        self._panels_date.setMaximumDate(QDate.currentDate().addDays(1))
        self._panels_date.dateChanged.connect(self._panels_update_valid_label)
        _rail_row(time_grid, 0, "Date:", self._panels_date, width="timestamp")
        self._panels_cycle = QComboBox()
        self._panels_cycle.setObjectName(OBJ_NUMERIC)
        self._panels_cycle.currentIndexChanged.connect(self._panels_update_fxx)
        recent = QToolButton()
        recent.setIcon(recent.style().standardIcon(QStyle.SP_BrowserReload))
        recent.setAccessibleName("Most recent published run")
        recent.setToolTip("Jump to the most recent published cycle")
        recent.clicked.connect(self._panels_set_recent)
        time_grid.addWidget(QLabel("Cycle:"), 1, 0)
        self._panels_cycle.setMinimumHeight(CONTROL_H["md"])
        time_grid.addWidget(self._panels_cycle, 1, 1)
        recent.setFixedSize(CONTROL_H["md"], CONTROL_H["md"])
        time_grid.addWidget(recent, 1, 2)
        self._panels_fxx_combo = QComboBox()
        self._panels_fxx_combo.setObjectName(OBJ_NUMERIC)
        self._panels_fxx_combo.currentIndexChanged.connect(
            self._panels_update_valid_label
        )
        _rail_row(
            time_grid, 2, "Forecast:", self._panels_fxx_combo, width="timestamp"
        )
        self._panels_valid_lbl = QLabel("")
        self._panels_valid_lbl.setObjectName(OBJ_EMPHASIS)
        self._panels_valid_lbl.setWordWrap(True)
        time_grid.addWidget(self._panels_valid_lbl, 3, 0, 1, 3)
        # T21.1/T21.4: same persistent header + live/history choice as the
        # Forecast Model tab, so all three rails answer time identically.
        self._panels_time_lbl = QLabel("")
        self._panels_time_lbl.setObjectName(OBJ_HINT)
        self._panels_time_lbl.setWordWrap(True)
        self._panels_time_lbl.setAccessibleName("Field Panels map time")
        time_grid.addWidget(self._panels_time_lbl, 4, 0, 1, 3)
        self._panels_time_lbl.hide()
        self._panels_mode_combo = QComboBox()
        self._panels_mode_combo.setAccessibleName("Field Panels map mode")
        self._panels_mode_combo.setToolTip(
            "Live follows the newest published run. Historical pins this "
            "run and hour: refreshes keep the labelled frame.")
        self._panels_mode_combo.addItem("Live — follow new runs", "live")
        self._panels_mode_combo.addItem("Historical — pin this run/hour",
                                        "history")
        self._panels_mode_combo.currentIndexChanged.connect(
            self._on_panels_map_mode_changed)
        time_grid.addWidget(self._panels_mode_combo, 5, 0, 1, 3)
        # T21.3: same frame scrubber as the Forecast Model tab, over this
        # tab's own run -- one clock in both places.
        from sharpmod.ui.features.gui_timeline_playback import MapFramePlayback as _PanelsStrip

        self._panels_frame_strip = _PanelsStrip(setup_box)
        self._panels_frame_strip.setAccessibleName(
            "Field Panels frame scrubber")
        self._panels_frame_strip.setToolTip(
            "Offered forecast hours for this run. Choose a cell to depict "
            "that hour; only offered hours are shown, so no hour here is "
            "interpolated.")
        self._panels_frame_strip.frameChosen.connect(
            self._on_panels_frame_chosen)
        time_grid.addWidget(self._panels_frame_strip, 6, 0, 1, 3)
        setup_layout.addWidget(section_rule(setup_box))
        setup_layout.addWidget(section_heading("Field activity and sources", setup_box))
        self._panels_shared_details = QLabel("", setup_box)
        self._panels_shared_details.setObjectName(OBJ_HINT)
        self._panels_shared_details.setTextFormat(Qt.PlainText)
        self._panels_shared_details.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self._panels_shared_details.setWordWrap(True)
        self._panels_shared_details.setAccessibleName(
            "Shared displayed field resolution and source")
        self._panels_shared_details.hide()
        setup_layout.addWidget(self._panels_shared_details)
        self._panels_activity_rows = []
        self._panels_detail_rows = []
        for panel in self._panels_view._panels:
            host = QWidget(setup_box)
            host.setObjectName(OBJ_PLAIN)
            host_layout = QVBoxLayout(host)
            host_layout.setContentsMargins(0, 0, 0, 0)
            host_layout.setSpacing(SPACE["xs"])
            activity = QHBoxLayout()
            activity.setContentsMargins(0, 0, 0, 0)
            activity.setSpacing(SPACE["xs"])
            number = QLabel(f"{panel.index + 1:02d}", host)
            number.setObjectName(OBJ_SECTION_LABEL)
            activity.addWidget(number)
            activity.addWidget(panel.activity_row, 1)
            host_layout.addLayout(activity)
            detail = QLabel("", host)
            detail.setObjectName(OBJ_HINT)
            detail.setTextFormat(Qt.PlainText)
            detail.setTextInteractionFlags(
                Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
            detail.setWordWrap(True)
            detail.setAccessibleName(
                f"Field panel {panel.index + 1} source and scale")
            detail.hide()
            host_layout.addWidget(detail)
            setup_layout.addWidget(host)
            self._panels_activity_rows.append((panel, host))
            self._panels_detail_rows.append((panel, detail))
            panel.statusUpdated.connect(
                lambda p=panel: self._update_forecast_map_details("panels"))
        self._panels_view.layoutChanged.connect(self._sync_panel_activity_rows)
        self._sync_panel_activity_rows()
        # Keep the sounding action in Field setup with the panel activity. It
        # no longer reserves a full-width footer beneath the comparison maps.
        self._panels_fetch_btn = QPushButton(action_label("load_sounding"))
        self._panels_fetch_btn.setObjectName(OBJ_PRIMARY)
        self._panels_fetch_btn.setMinimumHeight(CONTROL_H["md"])
        self._panels_fetch_btn.setToolTip(
            "Extract an HRRR sounding at the marked point, for this run and "
            "forecast hour"
        )
        self._panels_fetch_btn.clicked.connect(self._panels_fetch_sounding)
        self._panels_cancel_btn = QPushButton(action_label("cancel"))
        self._panels_cancel_btn.setObjectName(OBJ_GHOST)
        self._panels_cancel_btn.setMinimumHeight(CONTROL_H["md"])
        self._panels_cancel_btn.clicked.connect(self._cancel_model_fetch)
        self._panels_cancel_btn.hide()
        sounding_actions = QHBoxLayout()
        sounding_actions.setContentsMargins(0, 0, 0, 0)
        sounding_actions.setSpacing(SPACE["xs"])
        sounding_actions.addWidget(self._panels_fetch_btn, 1)
        sounding_actions.addWidget(self._panels_cancel_btn)
        setup_layout.addLayout(sounding_actions)
        left.addWidget(setup_box)

        point_grid = QGridLayout()
        point_grid.setHorizontalSpacing(SPACE["sm"])
        point_grid.setVerticalSpacing(SPACE["sm"])
        point_grid.setColumnMinimumWidth(0, FIELD_W["label"])
        point_grid.setColumnStretch(1, 1)
        location_layout.addLayout(point_grid)
        self._panels_lat = QDoubleSpinBox()
        self._panels_lat.setRange(-90.0, 90.0)
        self._panels_lat.setDecimals(4)
        self._panels_lat.setSingleStep(0.25)
        self._panels_lat.setValue(35.6300)
        self._panels_lat.valueChanged.connect(
            lambda _value: self._panels_point_from_spins()
        )
        center = QToolButton()
        center.setText("Center")
        center.setToolTip("Center every panel on this point")
        center.clicked.connect(lambda: self._panels_point_from_spins(center=True))
        _rail_row(point_grid, 0, "Latitude:", self._panels_lat, trailing=center)
        self._panels_lon = QDoubleSpinBox()
        self._panels_lon.setRange(-180.0, 180.0)
        self._panels_lon.setDecimals(4)
        self._panels_lon.setSingleStep(0.25)
        self._panels_lon.setValue(-97.4400)
        self._panels_lon.valueChanged.connect(
            lambda _value: self._panels_point_from_spins()
        )
        _rail_row(point_grid, 1, "Longitude:", self._panels_lon)
        self._panels_point_status = QLabel("")
        self._panels_point_status.setObjectName(OBJ_HINT)
        self._panels_point_status.setWordWrap(True)
        point_grid.addWidget(self._panels_point_status, 2, 0, 1, 3)
        from sharpmod.ui.picker.coordinates import coordinate_paste_button

        self._panels_paste_coordinates = coordinate_paste_button(
            self, self._panels_lat, self._panels_lon, self._panels_point_from_spins,
            source="Field Panels",
        )
        point_grid.addWidget(self._panels_paste_coordinates, 3, 0, 1, 3)
        self._panels_lock_btn = QPushButton("Lock point")
        self._panels_lock_btn.setCheckable(True)
        self._panels_lock_btn.setToolTip(
            "Lock the sounding point so clicks cannot move it on any panel. "
            "Inspect, pan, zoom, history, and box drawing keep working.")
        self._panels_lock_btn.toggled.connect(self._panels_lock_toggled)
        self._panels_recent_btn = QPushButton("Recent point")
        self._panels_recent_btn.setToolTip(
            "Return to the previous sounding point (U with map focus). "
            "History and session restores never move the point.")
        self._panels_recent_btn.clicked.connect(
            self._panels_restore_recent_point)
        self._panels_recent_btn.setEnabled(False)
        point_lock_row = QHBoxLayout()
        point_lock_row.addWidget(self._panels_lock_btn, 1)
        point_lock_row.addWidget(self._panels_recent_btn, 1)
        point_lock_host = QWidget()
        point_lock_host.setLayout(point_lock_row)
        point_grid.addWidget(point_lock_host, 4, 0, 1, 3)
        location_layout.addWidget(section_rule(location_box))
        location_layout.addWidget(section_heading("Map navigation", location_box))
        location_layout.addWidget(self._panels_zoom_row_host)
        location_layout.addWidget(section_rule(location_box))
        self._panels_appearance_toggle = QToolButton(location_box)
        self._panels_appearance_toggle.setObjectName(OBJ_CARD_TOGGLE)
        self._panels_appearance_toggle.setText("Map appearance")
        self._panels_appearance_toggle.setAccessibleName(
            "Show map appearance settings")
        self._panels_appearance_toggle.setToolTip(
            "Show legend placement and color scale options")
        self._panels_appearance_toggle.setCheckable(True)
        self._panels_appearance_toggle.setArrowType(Qt.RightArrow)
        self._panels_appearance_toggle.setToolButtonStyle(
            Qt.ToolButtonTextBesideIcon)
        self._panels_appearance_toggle.setAutoRaise(True)
        self._panels_appearance_toggle.setMinimumHeight(CONTROL_H["md"])
        appearance_content = QWidget(location_box)
        appearance_content.setObjectName(OBJ_PLAIN)
        appearance_layout = QVBoxLayout(appearance_content)
        appearance_layout.setContentsMargins(0, 0, 0, 0)
        appearance_layout.setSpacing(SPACE["xs"])
        appearance_layout.addWidget(self._legend_corner_combo())
        appearance_layout.addWidget(self._legend_collapsed_check())
        appearance_layout.addWidget(self._scale_lock_check())
        appearance_content.hide()

        def set_panels_appearance_visible(visible: bool) -> None:
            appearance_content.setVisible(bool(visible))
            self._panels_appearance_toggle.setArrowType(
                Qt.DownArrow if visible else Qt.RightArrow)
            action = "Hide" if visible else "Show"
            self._panels_appearance_toggle.setAccessibleName(
                f"{action} map appearance settings")
            self._panels_appearance_toggle.setToolTip(
                f"{action} legend placement and color scale options")

        self._panels_appearance_toggle.toggled.connect(
            set_panels_appearance_visible)
        location_layout.addWidget(self._panels_appearance_toggle)
        location_layout.addWidget(appearance_content)
        left.addWidget(location_box)

        left.addStretch(1)

        _order_rail_cards(left)
        location_box.set_collapsed(True)
        self._panels_controls_scroll = _scrolling_control_rail(left)
        self._panels_selection_pane = _selection_pane(
            self._panels_controls_scroll, self._panels_view, settings=self._settings,
            key="field-panels", title="Field panels",
        )
        outer.addWidget(self._panels_selection_pane)
        # The sounding actions live outside the collapsible rail, with every
        # other source's primary action: hiding the configuration must not
        # hide the request or its cancel control. They are built in the rail
        # above so the construction order matches the other tabs, then moved
        # here with their row layout intact.
        setup_layout.removeItem(sounding_actions)
        self._panels_selection_pane.actions.addLayout(sounding_actions)
        # Which panel's field answers for a sounding before anything is clicked.
        # Panel one is always in the layout, so it is the only safe default.
        self._panels_field = self._panels_view.active_field_controller()

        self._panels_area_changed(self._panels_area_combo.currentText())
        self._panels_update_cycles()
        self._panels_mode_combo.setCurrentIndex(max(0, self._panels_mode_combo.findData(
            self._startup_map_mode("panels"))))
        self._apply_map_mode("panels", self._startup_map_mode("panels"))
        self._panels_set_recent()
        self._panels_point_from_spins(center=True)
        install_selection_feedback(self, self._panels_selection_pane, "panels", self._panels_fetch_btn)
        return w

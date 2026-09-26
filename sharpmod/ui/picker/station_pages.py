"""Observed source tab builders for station and archive views.

The picker controller still owns source state and callbacks. These builders only
assemble each tab's controls and bind them to that controller.
"""

from __future__ import annotations

from qtpy.QtCore import QDate, Qt
from qtpy.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QDoubleSpinBox, QFrame,
    QGridLayout, QHeaderView, QHBoxLayout, QLabel, QLineEdit, QProgressBar,
    QPushButton, QSizePolicy, QStackedWidget, QToolButton, QVBoxLayout, QWidget,
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
    OBJ_GHOST, OBJ_HINT, OBJ_NUMERIC, OBJ_PLAIN, OBJ_POINT_LOCK,
    OBJ_PRIMARY, OBJ_PROGRESS_DETAIL, OBJ_SECTION_LABEL, OBJ_STATUS,
    PROGRESS_H, SPACE,
)
from sharpmod.ui.maps.overlays.controllers import (
    HrrrFieldController, OutlookOverlayController, RadarOverlayController,
    StormReportsOverlayController,
)
from sharpmod.ui.picker.cycles import _annotate_cycle_combo, _fill_cycle_combo
from sharpmod.ui.picker.observed_sources import OBSERVED_SOURCES
from sharpmod.ui.picker.station_catalogue import StationCatalogueView
from sharpmod.ui.picker.layout import (
    ActiveLayerList as _ActiveLayerList,
    DATE_DISPLAY_FORMAT as _DATE_DISPLAY_FORMAT,
    TOWN_LOOKUP_TOOLTIP as _TOWN_LOOKUP_TOOLTIP,
    map_tool_row as _map_tool_row,
    order_rail_cards as _order_rail_cards,
    rail_card as _rail_card,
    rail_row as _rail_row,
    rail_zoom_row as _rail_zoom_row,
    scrolling_control_rail as _scrolling_control_rail,
    selection_pane as _selection_pane,
    town_lookup_attribution_label as _town_lookup_attribution_label,
)
from sharpmod.ui.picker.selection import install_selection_feedback




class StationPagesMixin:
    """Build station and observed archive tabs."""

    # Map
    def _build_map_tab(self) -> QWidget:
        w = QWidget()
        outer = QHBoxLayout(w)
        outer.setContentsMargins(SPACE["md"], SPACE["sm"], SPACE["md"], SPACE["sm"])
        outer.setSpacing(SPACE["md"])

        # The map is built before the control rail because the overlay toggle
        # binds directly to it, and the rail is sealed into a scroll area as
        # soon as it is complete. Its placement in ``outer`` is unchanged.
        self._map = StationMapWidget(self._all_stations)
        self._map.set_projection(self.map_projection())
        self._apply_map_presentation()
        self._refresh_loaded_profile_markers()
        self._map.radarSiteSelected.connect(self._map_on_radar_site)
        self._map.viewSettled.connect(self._map_on_view_settled)
        self._map.stationSelected.connect(self._map_on_select)
        self._map.stationActivated.connect(self._map_on_activate)

        # --- left control column ---
        left = QVBoxLayout()
        left.setSpacing(SPACE["md"])
        left.setContentsMargins(0, 0, 0, 0)

        setup_box, setup = _rail_card("Sounding setup")
        setup.setSpacing(SPACE["xs"])
        self._map_setup_card = setup_box

        def setup_heading(title: str) -> None:
            heading = QLabel(title, setup_box)
            heading.setObjectName(OBJ_SECTION_LABEL)
            setup.addWidget(heading)

        def setup_rule() -> None:
            setup.addSpacing(SPACE["xs"])
            rule = QFrame(setup_box)
            rule.setFrameShape(QFrame.Shape.NoFrame)
            rule.setObjectName(OBJ_CARD_RULE)
            setup.addWidget(rule)
            setup.addSpacing(SPACE["xs"])

        setup_heading("Sounding Source")
        self._map_source = QComboBox()
        for key, label, tooltip in OBSERVED_SOURCES:
            self._map_source.addItem(label, key)
            self._map_source.setItemData(
                self._map_source.count() - 1, tooltip, Qt.ToolTipRole
            )
        self._map_source.setMinimumHeight(CONTROL_H["md"])
        stored_source = self._startup_observed_source()
        stored_index = self._map_source.findData(stored_source)
        if stored_index >= 0:
            self._map_source.setCurrentIndex(stored_index)
        self._map_source.setToolTip("Which archive to fetch observed soundings from.")
        self._map_source.setAccessibleName("Sounding source")
        self._map_source.currentIndexChanged.connect(self._map_on_source_changed)
        setup.addWidget(self._map_source)

        setup_rule()
        setup_heading("Selected Station")
        selb = QVBoxLayout()
        selb.setContentsMargins(0, 0, 0, 0)
        selb.setSpacing(SPACE["xs"])
        self._map_sel_lbl = QLabel("No station selected")
        self._map_sel_lbl.setWordWrap(True)
        self._map_sel_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._map_sel_lbl.setObjectName(OBJ_EMPHASIS)
        selb.addWidget(self._map_sel_lbl)
        self._map_avail = _AvailabilityIndicator()
        selb.addWidget(self._map_avail)
        setup.addLayout(selb)

        setup_rule()
        setup_heading("Runtime (UTC)")
        cg = QGridLayout()
        cg.setContentsMargins(0, 0, 0, 0)
        cg.setHorizontalSpacing(SPACE["xs"])
        cg.setVerticalSpacing(SPACE["xs"])
        cg.setColumnStretch(1, 1)
        default_date, default_hour = _most_recent_synoptic()
        self._map_date = QDateEdit()
        self._map_date.setDisplayFormat(_DATE_DISPLAY_FORMAT)
        self._map_date.setCalendarPopup(True)
        install_month_calendar(self._map_date)
        self._map_date.setDate(default_date)
        self._map_date.setMaximumDate(QDate.currentDate().addDays(1))
        self._map_date.setAccessibleName("Run date UTC")
        _rail_row(cg, 0, "Date:", self._map_date, width="timestamp")
        self._map_cycle = QComboBox()
        self._map_cycle.setObjectName(OBJ_NUMERIC)
        self._map_cycle.setAccessibleName("Run cycle UTC")
        _fill_cycle_combo(
            self._map_cycle, SYNOPTIC_HOURS, default_hour, run_date=default_date
        )
        # The "not yet run" mark is relative to the date beside it, so it has to be
        # re-derived whenever that date moves.
        self._map_date.dateChanged.connect(
            lambda date: _annotate_cycle_combo(self._map_cycle, date)
        )
        recent = QToolButton()
        recent.setText("Most recent")
        recent.setToolTip("Jump to the most recent synoptic cycle")
        recent.clicked.connect(self._map_set_recent)
        _rail_row(cg, 1, "Cycle:", self._map_cycle, trailing=recent,
                  width="compact")
        # T21.1: the persistent time header for the observed tab. No run or
        # lead here -- only the hour of the observation -- but the same
        # requested-vs-actual wording as every other rail.
        self._map_time_lbl = QLabel("")
        self._map_time_lbl.setObjectName(OBJ_HINT)
        self._map_time_lbl.setWordWrap(True)
        self._map_time_lbl.setAccessibleName("Station Map time")
        cg.addWidget(self._map_time_lbl, 2, 0, 1, 3)
        self._map_mode_combo = QComboBox()
        self._map_mode_combo.setAccessibleName("Station Map mode")
        self._map_mode_combo.setToolTip(
            "Live permits latest-only layers such as radar. Historical pins "
            "the selected archive hour and hides live-only layers.")
        self._map_mode_combo.addItem("Live — show current layers", "live")
        self._map_mode_combo.addItem("Historical — pin selected hour", "history")
        self._map_mode_combo.currentIndexChanged.connect(
            self._on_station_map_mode_changed)
        _rail_row(cg, 3, "Map mode:", self._map_mode_combo)
        setup.addLayout(cg)
        left.addWidget(setup_box)

        area_box, ab = _rail_card("Region")
        from sharpmod.ui.maps.regions import region_combo

        self._area_combo = region_combo(self._map, parent=area_box)
        ab.addWidget(self._area_combo)
        ab.addLayout(_map_tool_row(self._map, allow_box=False))
        ab.addLayout(_rail_zoom_row(self._map))
        from sharpmod.maps.map_geography import LABEL_DENSITY_LABELS

        remembered = self._startup_map_presentation()
        self._map_label_density_combo = self._map_presentation_combo(
            tuple(sorted(LABEL_DENSITY_LABELS.items(),
                         key=lambda item: (
                             "off", "sparse", "standard",
                             "dense").index(item[0]))),
            remembered["label_density"],
            "How many place names the map draws. Sounding selection is "
            "unchanged; this only declutters labels.",
            "Place label density",
            lambda value: self._on_map_presentation_chosen(
                "label_density", value),
        )
        self._map_scale_units_combo = self._map_presentation_combo(
            (("metric", "Scale: kilometres"), ("imperial", "Scale: miles")),
            remembered["scale_units"],
            "Scale-bar units. The bar measures the same ground distance "
            "either way.",
            "Scale bar units",
            lambda value: self._on_map_presentation_chosen(
                "scale_units", value),
        )
        ab.addWidget(self._map_label_density_combo)
        ab.addWidget(self._map_scale_units_combo)
        # T20 legend controls beside the other persisted Region presentation.
        ab.addWidget(self._legend_corner_combo())
        ab.addWidget(self._legend_collapsed_check())
        ab.addWidget(self._scale_lock_check())
        left.addWidget(area_box)

        # One surface keeps each switch, its state, and bulk actions together.
        (layer_box, layer_choices, layer_actions,
         self._map_layer_overview) = self._layer_manager_card("On this map")
        self._map_layers_card = layer_box  # compatibility for integrations
        self._map_outlook = OutlookOverlayController(self._map, parent=self)
        layer_choices.addWidget(self._map_outlook.controls_widget())

        # Directly under the outlook, because it is read against it: the switch
        # stays disabled until that outlook is showing.
        self._map_reports = StormReportsOverlayController(self._map, parent=self)
        self._map_reports.bind_outlook(self._map_outlook)
        layer_choices.addWidget(self._map_reports.controls_widget())
        self._map_radar = RadarOverlayController(
            self._map,
            parent=self,
            scope=self._startup_radar_scope(),
            site=self._startup_radar_site(),
            opacity=self._startup_radar_opacity(),
        )
        layer_choices.addWidget(self._map_radar.controls_widget())
        # Listed after radar but drawn beneath it: see RASTER_DRAW_ORDER. The
        # control order follows what the user reaches for most often, the paint
        # order follows what has to stay readable.
        self._map_field = HrrrFieldController(
            self._map, parent=self, product=self._startup_field_product(),
            opacity=self._startup_field_opacity(),
        )
        layer_choices.addWidget(self._map_field.controls_widget())
        # GOES decoding imports NumPy; defer it until the picker actually builds
        # a map so importing the lightweight picker facade stays fast.
        from sharpmod.ui.features.gui_environmental_context import EnvironmentalContextController

        self._map_context = EnvironmentalContextController(
            self._map,
            parent=self,
            channel=self._startup_satellite_channel(),
            density=self._startup_surface_density(),
            opacity=self._startup_satellite_opacity(),
        )
        self._map.stationSelected.connect(self._map_context.on_location_changed)
        layer_choices.addWidget(self._map_context.controls_widget())
        # What travels onto the sounding's own locator inset. It belongs in this
        # card because it is the same subject -- what to draw over a map -- and is
        # separated by a rule so it still reads as being about the sounding
        # rather than about the map on screen.
        self._map_locator = self._add_locator_selector(layer_choices, "map")
        self._install_inline_layer_statuses("map")
        self._map_layers = _ActiveLayerList(parent=layer_box, toolbar_only=True)
        self._map_layers.set_presets(
            self._layer_presets(), current=self._startup_layer_preset())
        self._map_layers.visibilityToggled.connect(self._on_map_layer_toggled)
        self._map_layers.retryRequested.connect(self._on_map_layer_retry)
        self._map_layers.apply_preset_btn.clicked.connect(
            self._on_map_layer_preset)
        layer_actions.addWidget(self._map_layers)
        left.addWidget(layer_box)
        self._wire_layer_list_refresh("map")
        self._refresh_layer_list("map")

        # Re-probe when the requested cycle changes for the current selection.
        self._map_date.dateChanged.connect(self._map_recheck_availability)
        self._map_cycle.currentIndexChanged.connect(self._map_recheck_availability)

        self._map_gen_btn = QPushButton(action_label("load_sounding"))
        self._map_gen_btn.setObjectName(OBJ_PRIMARY)
        self._map_gen_btn.setMinimumHeight(CONTROL_H["lg"])
        self._map_gen_btn.setEnabled(False)
        self._map_gen_btn.clicked.connect(self._map_generate)
        # Extra air above the action, on top of the rail's own `md` gap. The
        # cards above are all configuration; this is the one thing that acts on
        # them, and at a uniform gap it read as a sixth card in the stack.
        left.addSpacing(SPACE["sm"])

        hint = QLabel(
            "Select places a station \u2014 Inspect pins a card without "
            "selecting. Scroll to zoom, drag to pan (middle/right-drag "
            "always pans), right-click to pin, copy, centre, and navigate. "
            "Map keys: V/I tools, U recent point, +/\u2212 zoom, 0 fit, "
            "L centre, Alt+Left/Right history. "
            "Type in Region to jump to a named area; find sounding sites by "
            "id or name in the Station List tab, by Town on gridded tabs, "
            "or by pasting coordinates."
        )
        hint.setWordWrap(True)
        hint.setObjectName(OBJ_HINT)
        left.addWidget(hint)

        # The stretch goes *last*, so the controls and their primary action form
        # one top-aligned group and any spare height collects below. It used to
        # sit above the button, which pushed the action to the bottom of the rail
        # and opened a large empty gap in the middle.
        left.addStretch(1)

        _order_rail_cards(left)
        self._map_controls_scroll = _scrolling_control_rail(left)
        self._map_selection_pane = _selection_pane(
            self._map_controls_scroll, self._map, settings=self._settings,
            key="station-map", title="Station map",
        )
        outer.addWidget(self._map_selection_pane)
        # Keep corrective guidance beside the station it explains.
        self._map_selection_pane.embed_guidance(selb, index=1)
        self._map_selection_pane.actions.addWidget(self._map_gen_btn)
        if getattr(self, "_observed_job", None) is None:
            self._observed_job = JobStatus(self)
            self._observed_job_retry = None
            self._observed_job.cancelRequested.connect(self._cancel_observed_fetch)
            self._observed_job.retryRequested.connect(self._retry_observed_job)
            self._observed_job_scroll = scrollable_page(
                self._observed_job, parent=self,
                accessible_name="Observed acquisition progress and recovery details",
            )
            # This lives across the full action row. On overlay-scrollbar
            # platforms an as-needed horizontal bar appeared as a stray white
            # pill below Load sounding after a job; the status text already
            # wraps, so only vertical recovery scrolling is useful here.
            self._observed_job_scroll.setHorizontalScrollBarPolicy(
                Qt.ScrollBarAlwaysOff)
            self._observed_job_scroll.hide()
        self._map_selection_pane.actions.addWidget(self._observed_job_scroll)

        self._map_selected_id: str | None = None
        self._map_mode_combo.setCurrentIndex(max(
            0, self._map_mode_combo.findData(self._startup_map_mode("map"))))
        self._apply_map_mode("map", self._startup_map_mode("map"))
        self._map_sync_overlay_times()
        self._sync_inspection_source("_map")
        self._apply_stored_map_tool("Station Map")
        install_selection_feedback(self, self._map_selection_pane, "map", self._map_gen_btn)
        return w

    # Uwyo
    def _build_uwyo_tab(self) -> QWidget:
        w = QWidget()
        outer = QHBoxLayout(w)
        outer.setContentsMargins(SPACE["md"], SPACE["sm"], SPACE["md"], SPACE["sm"])
        outer.setSpacing(SPACE["md"])

        # Selection context and time stay visible while the catalogue scrolls.
        left = QVBoxLayout()
        left.setSpacing(SPACE["md"])
        left.setContentsMargins(0, 0, 0, 0)

        selected_box, selected = _rail_card("Selected station")
        self._uwyo_selected_lbl = QLabel("Choose a station from the catalogue")
        self._uwyo_selected_lbl.setObjectName(OBJ_EMPHASIS)
        self._uwyo_selected_lbl.setWordWrap(True)
        self._uwyo_selected_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        selected.addWidget(self._uwyo_selected_lbl)
        self._uwyo_avail = _AvailabilityIndicator(show_station_label=False)
        self._uwyo_avail.hide()
        selected.addWidget(self._uwyo_avail)
        left.addWidget(selected_box)

        time_box, time_body = _rail_card("Observation time (UTC)")
        tg = QGridLayout()
        tg.setContentsMargins(0, 0, 0, 0)
        tg.setHorizontalSpacing(SPACE["xs"])
        tg.setVerticalSpacing(SPACE["xs"])
        tg.setColumnStretch(1, 1)
        default_date, default_hour = _most_recent_synoptic()
        self._date_edit = QDateEdit()
        self._date_edit.setDisplayFormat(_DATE_DISPLAY_FORMAT)
        self._date_edit.setCalendarPopup(True)
        install_month_calendar(self._date_edit)
        self._date_edit.setDate(default_date)
        self._date_edit.setMaximumDate(QDate.currentDate().addDays(1))
        self._date_edit.setAccessibleName("Observation date UTC")
        self._date_edit.dateChanged.connect(self._update_valid_label)
        _rail_row(tg, 0, "Date:", self._date_edit, width="timestamp")
        self._cycle_combo = QComboBox()
        self._cycle_combo.setObjectName(OBJ_NUMERIC)
        self._cycle_combo.setAccessibleName("Observation cycle UTC")
        _fill_cycle_combo(
            self._cycle_combo, SYNOPTIC_HOURS, default_hour, run_date=default_date
        )
        self._date_edit.dateChanged.connect(
            lambda date: _annotate_cycle_combo(self._cycle_combo, date)
        )
        self._cycle_combo.currentIndexChanged.connect(self._update_valid_label)
        recent_btn = QToolButton()
        recent_btn.setText("Most recent")
        recent_btn.setToolTip("Jump to the most recent synoptic cycle")
        recent_btn.clicked.connect(self._set_most_recent)
        _rail_row(tg, 1, "Cycle:", self._cycle_combo, trailing=recent_btn,
                  width="compact")
        self._valid_lbl = QLabel("")
        self._valid_lbl.setObjectName(OBJ_HINT)
        self._valid_lbl.setWordWrap(True)
        tg.addWidget(self._valid_lbl, 2, 0, 1, 3)
        time_body.addLayout(tg)
        left.addWidget(time_box)
        left.addStretch(1)

        # A bounded search and a columned catalogue use wide screens without
        # making one station row a long, unstructured sentence.
        content = QVBoxLayout()
        content.setSpacing(SPACE["sm"])
        content.setContentsMargins(SPACE["lg"], 0, 0, 0)
        heading = QHBoxLayout()
        heading.setSpacing(SPACE["sm"])
        title = QLabel("Station catalogue")
        title.setObjectName(OBJ_EMPHASIS)
        heading.addWidget(title)
        self._count_lbl = QLabel("")
        self._count_lbl.setObjectName(OBJ_HINT)
        heading.addWidget(self._count_lbl)
        heading.addStretch(1)
        self._fetch_btn = QPushButton(action_label("load_sounding"))
        self._fetch_btn.setObjectName(OBJ_PRIMARY)
        self._fetch_btn.setMinimumHeight(CONTROL_H["lg"])
        self._fetch_btn.setMinimumWidth(FIELD_W["timestamp"])
        self._fetch_btn.setEnabled(False)
        self._fetch_btn.clicked.connect(self._fetch_selected)
        heading.addWidget(self._fetch_btn)
        content.addLayout(heading)

        self._uwyo_search = QLineEdit()
        self._uwyo_search.setClearButtonEnabled(True)
        self._uwyo_search.setPlaceholderText("Search station ID or name")
        self._uwyo_search.setAccessibleName("Search stations")
        self._uwyo_search.setToolTip(
            "Results update as you type. Press Enter to focus the first match.")
        self._uwyo_search.setMinimumHeight(CONTROL_H["md"])
        self._uwyo_search.setMaximumWidth(600)
        self._uwyo_search.textChanged.connect(self._filter_stations)
        self._uwyo_search.returnPressed.connect(self._focus_first_station)
        content.addWidget(self._uwyo_search)

        self._station_list = StationCatalogueView()
        self._station_list.setColumnCount(4)
        self._station_list.setHeaderLabels(
            ("ID", "Station", "Latitude", "Longitude"))
        self._station_list.setRootIsDecorated(False)
        self._station_list.setItemsExpandable(False)
        self._station_list.setUniformRowHeights(True)
        self._station_list.setAlternatingRowColors(True)
        self._station_list.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._station_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._station_list.setTextElideMode(Qt.ElideRight)
        self._station_list.setAccessibleName("Station catalogue results")
        columns = self._station_list.header()
        columns.setStretchLastSection(False)
        columns.setSectionResizeMode(0, QHeaderView.Fixed)
        columns.setSectionResizeMode(1, QHeaderView.Stretch)
        columns.setSectionResizeMode(2, QHeaderView.Fixed)
        columns.setSectionResizeMode(3, QHeaderView.Fixed)
        self._station_list.setColumnWidth(0, 84)
        self._station_list.setColumnWidth(2, 96)
        self._station_list.setColumnWidth(3, 104)
        self._station_list.setSortingEnabled(True)
        self._station_list.sortByColumn(0, Qt.AscendingOrder)
        self._station_list.itemSelectionChanged.connect(self._sync_fetch_enabled)
        self._station_list.itemActivated.connect(
            lambda _item, _column: self._fetch_selected())

        self._uwyo_results_stack = QStackedWidget()
        self._uwyo_results_stack.addWidget(self._station_list)
        empty_panel = QFrame()
        empty_panel.setObjectName(OBJ_CARD)
        empty_body = QVBoxLayout(empty_panel)
        empty_body.setContentsMargins(SPACE["lg"], SPACE["lg"],
                                      SPACE["lg"], SPACE["lg"])
        empty_body.setSpacing(SPACE["sm"])
        empty_body.addStretch(1)
        self._uwyo_empty_title = QLabel("")
        self._uwyo_empty_title.setObjectName(OBJ_EMPHASIS)
        self._uwyo_empty_title.setAlignment(Qt.AlignCenter)
        empty_body.addWidget(self._uwyo_empty_title)
        self._uwyo_empty_lbl = QLabel("")
        self._uwyo_empty_lbl.setObjectName(OBJ_HINT)
        self._uwyo_empty_lbl.setWordWrap(True)
        self._uwyo_empty_lbl.setAlignment(Qt.AlignCenter)
        empty_body.addWidget(self._uwyo_empty_lbl)
        self._uwyo_clear_search = QPushButton("Clear search")
        self._uwyo_clear_search.clicked.connect(self._uwyo_search.clear)
        empty_action = QHBoxLayout()
        empty_action.addStretch(1)
        empty_action.addWidget(self._uwyo_clear_search)
        empty_action.addStretch(1)
        empty_body.addLayout(empty_action)
        empty_body.addStretch(2)
        self._uwyo_results_stack.addWidget(empty_panel)
        content.addWidget(self._uwyo_results_stack, stretch=1)

        self._uwyo_help_lbl = QLabel(
            "Select a row to check availability. Double-click or press Enter to load.")
        self._uwyo_help_lbl.setObjectName(OBJ_HINT)
        self._uwyo_help_lbl.setWordWrap(True)
        content.addWidget(self._uwyo_help_lbl)

        self._uwyo_controls_scroll = _scrolling_control_rail(left)
        catalogue = QWidget()
        catalogue.setLayout(content)
        self._uwyo_selection_pane = _selection_pane(
            self._uwyo_controls_scroll, catalogue, settings=self._settings,
            key="station-list", title="Station list",
        )
        outer.addWidget(self._uwyo_selection_pane)
        self._uwyo_selection_pane.embed_guidance(selected, index=1)
        self._uwyo_selection_pane.actions.addWidget(self._observed_job_scroll)

        # Re-probe availability when the selection or requested cycle changes.
        self._station_list.itemSelectionChanged.connect(self._uwyo_recheck_availability)
        self._date_edit.dateChanged.connect(self._uwyo_recheck_availability)
        self._cycle_combo.currentIndexChanged.connect(self._uwyo_recheck_availability)
        # Reload the datetime-aware station set when the cycle changes.
        self._date_edit.dateChanged.connect(self._uwyo_refresh_catalog)
        self._cycle_combo.currentIndexChanged.connect(self._uwyo_refresh_catalog)

        # Populate the full catalogue now; live-filter narrows it.
        self._filter_stations("")
        self._update_valid_label()
        self._sync_fetch_enabled()
        install_selection_feedback(self, self._uwyo_selection_pane, "uwyo", self._fetch_btn)
        return w

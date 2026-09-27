"""Source tab builders for station, observed, forecast, and field maps.

The picker controller still owns source state and callbacks. These builders only
assemble each tab's controls and bind them to that controller.
"""

from __future__ import annotations

from qtpy.QtCore import QDate, Qt
from qtpy.QtWidgets import (
    QComboBox, QDateEdit, QDoubleSpinBox, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QProgressBar, QPushButton,
    QSizePolicy, QToolButton, QVBoxLayout, QWidget,
)

from sharpmod.ui.features.gui_common import (
    SYNOPTIC_HOURS, _most_recent_synoptic, action_label,
    install_month_calendar, scrollable_page,
)
from sharpmod.ui.features.gui_jobs import JobStatus
from sharpmod.ui.features.gui_maps import PointMapWidget, StationMapWidget
from sharpmod.ui.features.gui_workers import _AvailabilityIndicator
from sharpmod.ui.styles.theme import (
    CONTROL_H, FIELD_W, OBJ_CARD_RULE, OBJ_CARD_TOGGLE, OBJ_EMPHASIS,
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



from sharpmod.ui.picker.station_pages import StationPagesMixin
from sharpmod.ui.picker.forecast_pages import ForecastPagesMixin


class SourcePagesMixin(StationPagesMixin, ForecastPagesMixin):
    """Build the picker source tabs."""

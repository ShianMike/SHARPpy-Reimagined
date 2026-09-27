"""Feature-facing map widgets and public map constants.

``StationMapWidget``, ``PointMapWidget``, and ``BoxFieldMapWidget`` compose the focused
geometry, projection, layer, interaction, and drawing mixins in ``sharpmod.ui.maps``;
keep this module as the stable construction/import surface for picker and viewer
workflows."""

from __future__ import annotations

import logging
import math
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import MappingProxyType

"""Interactive station and point-selection map widgets."""

# Importing common first applies the native Qt platform policy.
from sharpmod.ui.features import gui_common as _gui_common
from sharpmod.ui.features.gui_theme import current_theme, mono_font, ui_font
from sharpmod.maps.map_geography import (
    boundary_draw_order,
    boundary_width,
    declutter_labels,
    format_scale_length,
    km_per_pixel_at_latitude,
    labels_visible_for_span,
    normalise_label_density,
    pick_scale_length,
)
from sharpmod.maps.map_overlays import (
    OverlayRaster,
    describe_at,
    format_age,
    marker_hits_at,
)
from sharpmod.ui.maps.hatching import hatch_brush
from sharpmod.ui.styles.theme import MapPalette, map_palette
from sharpmod.ui.maps.layers import (
    LIVE_ONLY_RASTER_KEYS, RASTER_DRAW_ORDER, RASTER_ORDER_DEFAULT,
    VECTOR_DRAW_ORDER, VECTOR_ORDER_DEFAULT, MapLayerStateMixin,
)
from sharpmod.ui.maps.time_state import MapTimeMixin
from sharpmod.ui.maps.basemap import MapBasemapMixin
from sharpmod.ui.maps.basemap_data import (
    BASEMAP_LAYER_NAMES, _load_basemap, _prepare_basemap_layers,
    _shared_basemap_layers as _build_shared_basemap_layers,
)
from sharpmod.ui.maps.raster import (
    MapRasterMixin, _WARP_MAX_CELLS, _WARP_MIN_CELLS, _WARP_TARGET_CELL_DEG,
)
from sharpmod.ui.maps.vector import (
    MapOverlayDrawingMixin, OVERLAY_FILL_ALPHA, OVERLAY_HATCH_ALPHA,
    OVERLAY_MARKER_FILL_ALPHA, OVERLAY_STROKE_WIDTH,
)
from sharpmod.ui.maps.station_labels import (
    STATION_LABEL_ID_LON_SPAN, STATION_LABEL_NAME_LON_SPAN,
    StationLabelsMixin,
)
from sharpmod.ui.maps.profile_markers import ProfileMarkersMixin
from sharpmod.ui.maps.station_interactions import StationInteractionsMixin
from sharpmod.ui.maps.point import (
    MAP_TOOLS, MIN_BOX_DRAG_PX, PointMapMixin, _inspection_run_iso,
    _inspection_run_value, _inspection_valid_iso,
)
from sharpmod.ui.maps.box import (
    BOX_EDIT_DRAG_PX, BOX_HANDLE_HIT_RADIUS_PX, BOX_HANDLE_RADIUS_PX,
    _DOMAIN_EDGE_STEP_DEG, _DOMAIN_MAX_STEPS, _DOMAIN_MIN_STEPS, MapBoxMixin,
)
from sharpmod.ui.maps.legend import (
    COLOUR_BAR_BLOCK_H, COLOUR_BAR_H, COLOUR_BAR_HEADER_H, COLOUR_BAR_TICK_H,
    LEGEND_BLOCK_GAP, LEGEND_LINE_H, LEGEND_MARGIN, LEGEND_SWATCH_H,
    OVERLAY_LEGEND_FILL_ALPHA, MapLegendMixin, _format_tick,
    _interpolate_stops,
)
from sharpmod.ui.maps.presentation import (
    MapPresentationMixin, _map, _estimate_label_widths, _estimated_label_height,
)
from sharpmod.ui.maps.projection import (
    _CONIC_LAT_LIMIT, _CONIC_MAX_LAT_SPAN, _CONIC_MIN_ABS_MID_LAT,
    _Projection, _conic_constants,
)
from sharpmod.ui.maps.navigation import (
    MAP_AREAS, MAP_PROJECTIONS, NAV_HISTORY_LIMIT, NAV_MAX_LAT_SPAN, NAV_MAX_LON_SPAN,
    NAV_MIN_LAT_SPAN, NAV_MIN_LON_SPAN, PAN_BUTTONS, SECONDARY_PAN_BUTTONS,
    WHEEL_NOTCH_FACTOR,
    WHEEL_NOTCH_UNITS, MapNavigationMixin,
)


from qtpy import QtCore, QtGui
from qtpy.QtCore import (
    Qt,
    QThread,
    QTimer,
    Signal,
    QDate,
    QSettings,
    QPointF,
    QRectF,
    QSize,
    QUrl,
)
from qtpy.QtGui import (
    QAction,
    QPainter,
    QColor,
    QPen,
    QBrush,
    QPolygonF,
    QPainterPath,
    QFont,
    QPixmap,
    QImage,
    QIcon,
    QTransform,
    QDesktopServices,
)
from qtpy.QtWidgets import (
    QApplication,
    QMainWindow,
    QToolTip,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QPushButton,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QLabel,
    QDateEdit,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QMessageBox,
    QTabWidget,
    QGroupBox,
    QStatusBar,
    QToolButton,
    QScrollArea,
    QFrame,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QCheckBox,
    QSizePolicy,
    QGraphicsView,
    QGraphicsScene,
    QProgressBar,
    QMenu,
)



#: Explicit map tools (T18.1). ``select`` places the sounding point,
#: ``inspect`` reads values without moving anything, ``box`` draws an area.
#: The station map has no box of its own, so it accepts only the first two.
MAP_POINT_TOOLS = ("select", "inspect")



#: How much closer a radar antenna has to be than a competing sounding station
#: before a click counts as aimed at the antenna, in pixels.
#:
#: Not a tie-break but a margin, because the two are routinely not merely close
#: but *co-located*: 48 station/antenna pairs sit under two pixels apart at
#: normal zooms and the nearest of them, KILX with Lincoln 74560, is six
#: thousandths of a pixel away. Deciding those by "whichever is nearer" hands the
#: click to whichever rounding error came out ahead, so clicking the one visible
#: dot on the Station Map re-aimed the radar instead of picking the sounding
#: about half the time, differently on each attempt. With a margin the station
#: keeps every shared mast, which is the right default on a map whose purpose is
#: choosing a sounding site; an antenna that stands on its own is still one
#: click away, and every antenna remains reachable from the site list.
RADAR_PICK_MARGIN_PX = 3.0


def presentation_preferences(widget) -> dict:
    """Return a widget's persisted map presentation (T16.4, T20 legend)."""
    get = getattr(widget, "presentation_state", None)
    if callable(get):
        try:
            state = get()
        except Exception:  # noqa: BLE001 - presentation never breaks callers
            return {"label_density": "standard", "scale_units": "metric"}
        if isinstance(state, dict):
            prefs = {
                "label_density": normalise_label_density(
                    state.get("label_density")),
                "scale_units": "imperial"
                if str(state.get("scale_units") or "").strip().lower() in {
                    "imperial", "mi", "miles"} else "metric",
            }
            if isinstance(state.get("legend"), dict):
                from sharpmod.maps.map_legends import restore_legend_state

                prefs["legend"] = restore_legend_state(state.get("legend"))
            if isinstance(state.get("mode"), str):
                from sharpmod.maps.map_time import normalise_mode

                prefs["mode"] = normalise_mode(state.get("mode"))
            return prefs
    return {"label_density": "standard", "scale_units": "metric"}


@lru_cache(maxsize=1)
def _shared_basemap_layers() -> MappingProxyType:
    """Cache one immutable basemap while retaining public loading hooks."""
    return _build_shared_basemap_layers(
        loader=_load_basemap, preparer=_prepare_basemap_layers,
    )


from sharpmod.ui.maps.station_state import MapStationStateMixin
from sharpmod.ui.maps.chrome_drawing import MapChromeDrawingMixin


class StationMapWidget(MapStationStateMixin, MapChromeDrawingMixin, MapNavigationMixin, MapPresentationMixin, MapLegendMixin, MapLayerStateMixin, MapTimeMixin, MapBasemapMixin, MapRasterMixin, MapOverlayDrawingMixin, StationLabelsMixin, ProfileMarkersMixin, StationInteractionsMixin, QWidget):
    """A clickable map of sounding stations (the legacy SHARPpy picker map).

    Plots every station as a dot over an HD coastline + border basemap. The
    projection is an equirectangular with a cosine-of-latitude longitude
    correction and a single uniform scale (letterbox fit), so land shapes keep
    their real proportions and never stretch as the window is resized.

    Hovering shows the cursor lat/lon and the nearest station; clicking selects
    the nearest station; double-clicking activates it (generate). The mouse
    wheel zooms about the cursor and dragging pans. The visible extent can be
    set to a named region via :meth:`set_area`. The basemap is rasterized once
    per extent/size into a cached pixmap so hover and selection stay smooth.
    """

    stationSelected = Signal(str)  # station id (single click / hover-pick)
    stationActivated = Signal(str)  # station id (double click -> generate)
    #: WSR-88D identifier, when the user clicks one of the radar site markers.
    #: Emitted by the map and consumed by the owning tab, which is the only thing
    #: that knows about the radar controller -- a map never reaches into one.
    radarSiteSelected = Signal(str)

    #: The view has come to rest somewhere new, after a pan, a zoom, a region
    #: change or a resize. Overlays whose *content depends on where the map is
    #: looking* need this: a single-site radar left on "nearest to map centre"
    #: otherwise kept drawing the antenna that served the previous view until its
    #: own refresh cadence came round, which is two and a half minutes for
    #: reflectivity, and in the meantime the frame was off-screen and the layer
    #: looked broken. Emitted once per settled view rather than per mouse-move,
    #: so a drag costs nothing.
    viewSettled = Signal()

    def __init__(self, stations, parent=None):
        super().__init__(parent)
        # Point-map panels keep a rasterized frame for crosshair-only repaints.
        # The frame is discarded by ``update`` whenever actual map content
        # changes, so the next paint rebuilds from current state.
        self._map_frame_cache = None
        self._stations = list(stations)
        # Monotonic station-data identity for the station-label cache. Building
        # a tuple of every id/name/coordinate inside every mouse-move paint was
        # O(stations) even on a cache hit.
        self._stations_revision = 0
        self._layers = _shared_basemap_layers()
        self._area_name = "United States (CONUS)"
        #: Flat by default: it is what every previous version drew, and it is the
        #: only view that stays correct at every extent this map offers.
        self._projection = "flat"
        #: ``(key, projection)`` memo for :meth:`_proj`. Keyed on every input the
        #: transform is derived from, so a stale entry cannot be returned and no
        #: caller has to remember to invalidate it.
        self._proj_cache: tuple[tuple, "_Projection"] | None = None
        #: ``(key, pixmap)`` composite of every image overlay warped into the
        #: curved projection, and the transform it was built under. Only the
        #: curved path uses these; the flat path blits straight from the source.
        self._warp_cache: tuple[tuple, "QPixmap"] | None = None
        self._warp_proj: "_Projection" | None = None
        self._outline_raster_cache = None
        #: Cone held across pan and wheel gestures, or ``None`` when the cone is
        #: free to follow the view. Set on the first gesture after the view is
        #: chosen and cleared by :meth:`_invalidate`. See
        #: :meth:`_queue_map_preview`.
        self._conic_freeze: tuple | None = None
        self._lon0, self._lon1, self._lat0, self._lat1 = MAP_AREAS[self._area_name]
        self._selected_id: str | None = None
        self._hover_id: str | None = None
        self._hover_lonlat: tuple[float, float] | None = None
        self._drag_last: QPointF | None = None
        self._dragged = False
        # Radar antennas, as ``(id, lon, lat)``. Lives on the base widget rather
        # than on PointMapWidget beside the saved-location pins, because the
        # radar overlay is offered on both the station map and the forecast-model
        # map and the markers have to be pickable on either. Empty means the
        # layer is off, which is the only "hidden" state it needs: the controller
        # pushes the list when the single-site scope is showing and clears it
        # otherwise.
        self._radar_sites: tuple[tuple[str, float, float], ...] = ()
        self._radar_site_id: str | None = None
        self._radar_hover_id: str | None = None
        # Overlays are drawn live rather than baked into the basemap raster:
        # they depend on the selected valid time, which is not part of the
        # raster's cache key, and they are small enough (hundreds of points)
        # that redrawing them on hover costs nothing.
        self._overlays: dict[str, object] = {}
        # Raster overlays live in their own registry rather than beside the
        # vector layers. Every consumer of ``_overlays`` reaches for
        # ``layer.shapes``, so a raster mixed in there would have to be filtered
        # out at each of those sites; keeping them apart means the vector paint
        # path and legend need no type checks at all. The two share
        # ``_overlay_visible``, which is keyed only by string, and one key may
        # not name both.
        self._rasters: dict[str, OverlayRaster] = {}
        #: ``{key: (image_bytes, QPixmap | None)}``. Decoding a full-extent
        #: radar frame costs milliseconds and ``paintEvent`` runs on every mouse
        #: move, so the decode is cached. The key half is the *bytes object*, so
        #: re-wrapping the same payload at a new opacity reuses the pixmap while
        #: a genuinely new frame replaces it. ``None`` records a failed decode so
        #: a corrupt payload is not re-attempted on every repaint.
        self._raster_pixmaps: dict[str, tuple[bytes, QPixmap | None]] = {}
        self._overlay_visible: dict[str, bool] = {}
        self._valid_time: datetime | None = None
        self._basemap_cache = None
        self._cache_key = None
        self._cache_proj = None
        self._basemap_refresh_timer = QTimer(self)
        self._basemap_refresh_timer.setSingleShot(True)
        # Keep the lightweight preview alive across ordinary physical-wheel
        # notches (often 80-120 ms apart).  A shorter delay rerasterizes the
        # full vector map between notches and reintroduces visible stutter.
        self._basemap_refresh_timer.setInterval(240)
        self._basemap_refresh_timer.timeout.connect(self._finish_map_preview)
        #: Place-label density (T16.2): ``off``/``sparse``/``standard``/``dense``.
        #: Never a data selection -- it only decides which bundled place names
        #: are drawn, so a sounding can always be picked by click or search.
        self._label_density = "standard"
        #: Scale-bar units (T16.3): ``metric`` (km) or ``imperial`` (mi). The
        #: bar always measures the same ground distance; only the label changes.
        self._scale_units = "metric"
        #: Pinned place label ``(name, lon, lat)`` or ``None``. Drawn with the
        #: selection marker semantics (diamond + shadowed text) so it survives
        #: the declutter that hides ordinary town labels.
        self._pinned_place: tuple[str, float, float] | None = None
        #: Previous/next view history (T17.2): bounded stacks of
        #: ``(lon0, lon1, lat0, lat1)`` extents plus a re-entrancy guard. Only
        #: user navigation pushes (zoom, pan end, region fit); restores and
        #: session restores never push, so going back and restoring cannot
        #: strand the user in a loop of their own history.
        self._view_back: list[tuple[float, float, float, float]] = []
        self._view_forward: list[tuple[float, float, float, float]] = []
        self._navigating_history = False
        self._pan_push_armed = False
        #: T17.4 click-versus-pan state. ``_press_pos``/``_press_button``
        #: remember a pan-button press; ``_pan_consumed_press`` suppresses the
        #: context menu after a drag that panned. All default to ``None``/False
        #: so attribute access never depends on which gesture ran first.
        self._press_pos: QPointF | None = None
        self._press_button = Qt.NoButton
        self._pan_consumed_press = False
        #: Explicit tool (T18.1): ``select`` places, ``inspect`` reads without
        #: moving anything. The box tool lives on PointMapWidget, which owns
        #: the rectangle; the base class accepts only the point tools.
        self._map_tool = "select"
        #: Sounding-selection lock (T18.1): while set, clicks and keyboard
        #: recent-point moves cannot move the selection, but inspection,
        #: navigation, history, and box gestures keep working.
        self._point_locked = False
        #: Pinned inspection snapshot (T18.2): a plain-data dict from
        #: :mod:`sharpmod.maps.map_field_inspection`, or ``None``. Survives hover
        #: and pan; cleared by Escape, unpin, or a fresh pin elsewhere.
        self._inspection: dict | None = None
        #: Legend choices (T20): preferred corner, collapsed flag, and
        #: fixed-scale locks keyed by product. Geometry re-derives at paint
        #: time from the live widget size; only the choices persist.
        self._legend_corner = ""
        self._legend_collapsed = False
        self._legend_locks: dict[str, dict] = {}
        #: Time/mode choices (T21): the pinned run/hour when a forecast tab
        #: owns one, ``None`` when the map follows a valid moment instead;
        #: live/history mode naming whether the view follows new runs.
        #: ``_held_frames`` shelves newer live rasters that arrive under a
        #: historical view, so a refresh keeps the labelled frame.
        self._forecast_run = None
        self._forecast_fxx: int | None = None
        self._map_mode = "live"
        self._held_frames: dict[str, object] = {}
        #: Last painted legend layout (T20.4): the bar rectangle and scale the
        #: hover marker reads, or ``None`` when no continuous bar was drawn.
        #: ``_time_header_rect`` is the painted time chip (T21.1), or ``None``.
        self._legend_bar: tuple[float, float, float, float] | None = None
        self._legend_bar_range: tuple[float, float] | None = None
        self._readout_rect: tuple[float, float, float, float] | None = None
        self._time_header_rect: tuple[float, float, float, float] | None = None
        self.setMinimumSize(QSize(520, 380))
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName("Sounding location map")
        self._refresh_tool_chrome()
        self._refresh_map_accessible_description()
        #: Per-view labels and their projection. During a gesture, the last
        #: settled layout is transformed with the map; decluttering runs again
        #: once the view stops moving. ``None`` means stale.
        self._label_layout_cache: tuple[tuple, tuple, "_Projection"] | None = None
        #: Sounding-station text has its own progressive declutter. It cannot
        #: share the place-name cache because protected station labels must win,
        #: but its key is O(1) and an ordinary hover never invalidates towns.
        self._station_label_layout_cache: tuple[tuple, tuple] | None = None
        #: Loaded profile points supplied by the picker (T22.4).  Stored as
        #: plain mappings so maps remain independent of ProfileCollection.
        self._profile_markers: tuple[dict, ...] = ()
        #: Per-view boundary outline cache: ``(key, QPolygonF-tuple)`` of
        #: projected boundary polylines in widget pixels. Same staleness
        #: contract as the label layout -- reprojecting ~113k basemap vertices
        #: per paint frame is what made pans stutter with the added text.
        self._outline_cache: tuple[tuple, tuple] | None = None

    def update(self, *args) -> None:
        """Invalidate a cached point-map frame before scheduling a repaint."""
        self._map_frame_cache = None
        super().update(*args)

    # -- data prep ----------------------------------------------------------- #
    def _refresh_map_accessible_description(self) -> None:
        """Describe the view for assistive technology (T16.2/T16.3).

        Names the projection, the visible extent, the scale-bar length, and
        the selected/hovered/pinned location -- the same facts a sighted
        reader gets from the scale bar, orientation line, and markers. Plain
        text, refreshed on every view or selection change, so a chart reader
        never needs the pixels.
        """
        try:
            info = self.scale_bar_info()
            scale = info.get("label", "")
        except Exception:  # noqa: BLE001 - description never breaks the map
            scale = ""
        tool = getattr(self, "_map_tool", "select")
        tool_text = {"select": "Select tool", "inspect": "Inspect tool",
                     "box": "Draw box tool"}.get(tool, "Select tool")
        lock_text = "; selection locked" if self.is_point_locked() else ""
        effective = self.effective_projection()
        chosen = self._projection if self._projection in MAP_PROJECTIONS else "flat"
        if effective == chosen:
            projection = "curved" if chosen == "curved" else "flat"
        else:
            projection = f"{chosen} (shown flat)"
        try:
            clock = self.time_header_text()
        except Exception:  # noqa: BLE001 - description never breaks the map
            clock = ""
        try:
            from sharpmod.maps.map_time import mode_label

            mode = mode_label(self.map_mode())
        except Exception:  # noqa: BLE001 - description never breaks the map
            mode = ""
        area = self._area_name or "custom view"
        lon0, lon1 = sorted((self._lon0, self._lon1))
        extent = f"{lon0:.1f} to {lon1:.1f} longitude, {self._lat0:.1f} to " \
            f"{self._lat1:.1f} latitude"
        selected = self._selected_id or "no station selected"
        pinned = getattr(self, "_pinned_place", None)
        pin = f"; pinned place {pinned[0]}" if pinned else ""
        profile_count = len(getattr(self, "_profile_markers", ()))
        profiles = (
            f"; {profile_count} loaded profile point"
            f"{'s' if profile_count != 1 else ''}"
            if profile_count
            else ""
        )
        back = len(getattr(self, "_view_back", ()))
        forward = len(getattr(self, "_view_forward", ()))
        history = ""
        if back or forward:
            history = f" View history: {back} previous, {forward} next."
        clock_text = f" {clock}." if clock else ""
        mode_text = f" {mode}." if mode else ""
        self.setAccessibleDescription(
            f"Sounding location map, {tool_text}{lock_text}, {projection} "
            f"projection, {area}, showing {extent}. Scale "
            f"{scale or 'unavailable'}. {selected}{pin}{profiles}.{history}"
            f"{clock_text}{mode_text}")

    @staticmethod
    def _prep_layers(basemap: dict) -> MappingProxyType:
        """Precompute each polyline's lon/lat bounding box for fast clipping."""
        return _prepare_basemap_layers(basemap)

    # -- navigation state (T17) ------------------------------------------------ #


    def effective_projection(self) -> str:
        """Return the projection actually drawn: ``"flat"`` or ``"curved"``.

        The chosen projection may fall back to flat for extents a cone cannot
        represent; the choice is remembered so returning to a regional extent
        restores the conic, while this reports what the pixels show.
        """
        if self._projection == "curved" and _conic_constants(
                self._lat0, self._lat1,
                (self._lon0 + self._lon1) / 2.0) is None:
            return "flat"
        return self._projection if self._projection in MAP_PROJECTIONS else "flat"


    # -- explicit tools, lock, inspection state (T18) ------------------------ #
    def map_tool(self) -> str:
        """Return the active explicit tool: ``select`` or ``inspect``."""
        return getattr(self, "_map_tool", "select")

    def _allowed_tools(self) -> tuple[str, ...]:
        return MAP_POINT_TOOLS

    def set_map_tool(self, tool) -> None:
        """Choose the active tool, falling back to ``select`` (T18.1)."""
        from sharpmod.maps.map_field_inspection import normalise_tool

        wanted = normalise_tool(tool, allow_box=("box" in self._allowed_tools()))
        if wanted == getattr(self, "_map_tool", "select"):
            return
        self._map_tool = wanted
        self._refresh_tool_chrome()
        self.update()

    def _refresh_tool_chrome(self) -> None:
        """Sync cursor and tooltip with the active tool (T18.1)."""
        tool = getattr(self, "_map_tool", "select")
        cursors = {
            "select": Qt.CrossCursor,
            "inspect": Qt.WhatsThisCursor,
            "box": Qt.CrossCursor,
        }
        try:
            self.setCursor(cursors.get(tool, Qt.CrossCursor))
        except (AttributeError, RuntimeError, TypeError):
            pass
        self.setToolTip(self.tool_hint_text())

    def tool_hint_text(self) -> str:
        """Return the one-line hint describing the active tool (T18.1)."""
        gestures = ("middle/right-drag always pans), right-click for zoom, "
                    "fit, centre, and view history.")
        if getattr(self, "_map_tool", "select") == "inspect":
            return ("Inspect: hover to read values, click to pin a card, "
                    "double-click does nothing — inspecting never moves the "
                    "sounding selection. Scroll to zoom at the cursor, drag "
                    "to pan (" + gestures)
        return ("Select: click to place the sounding point, double-click to "
                "fetch. Scroll to zoom at the cursor, drag to pan (" + gestures)

    def is_point_locked(self) -> bool:
        """Whether the sounding selection is locked (T18.1)."""
        return bool(getattr(self, "_point_locked", False))

    def set_point_locked(self, locked: bool) -> None:
        """Lock or unlock the sounding selection (T18.1)."""
        locked = bool(locked)
        if locked == getattr(self, "_point_locked", False):
            return
        self._point_locked = locked
        self._refresh_map_accessible_description()
        self.update()

    def inspection_snapshot(self) -> dict | None:
        """Return the pinned inspection snapshot, or ``None`` (T18.2)."""
        snapshot = getattr(self, "_inspection", None)
        return dict(snapshot) if isinstance(snapshot, dict) else None

    def clear_inspection(self) -> None:
        """Drop the pinned inspection card, if any (T18.2)."""
        if getattr(self, "_inspection", None) is not None:
            self._inspection = None
            self.update()

    def hover_inspection_text(self) -> str:
        """Return the current hover readout text (T18.2).

        Falls back to the live cursor coordinate so headless and synthetic
        events -- which never ran the hover path -- still report honestly.
        """
        text = str(getattr(self, "_hover_inspection_text", "") or "")
        if text:
            return text
        hover = getattr(self, "_hover_lonlat", None)
        try:
            lon, lat = hover
            return f"{float(lat):.3f}, {float(lon):.3f}"
        except (TypeError, ValueError, OverflowError):
            return ""

    def inspection_card_lines(self) -> list[str]:
        """Return the pinned-card body lines, or ``[]`` (T18.2)."""
        from sharpmod.maps.map_field_inspection import card_lines

        snapshot = self.inspection_snapshot()
        if snapshot is None:
            return []
        try:
            return list(card_lines(snapshot))
        except Exception:  # noqa: BLE001 - a card never breaks the map
            return []

    def copy_inspection_coordinates(self) -> str:
        """Return clipboard text for the pinned card's coordinates (T18.3)."""
        from sharpmod.maps.map_field_inspection import coordinates_text

        snapshot = self.inspection_snapshot()
        if snapshot is None:
            return "No pinned inspection to copy."
        for keys in (("sampled_lat", "sampled_lon"), ("lat", "lon")):
            try:
                return coordinates_text(snapshot[keys[0]], snapshot[keys[1]])
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
        return "No pinned inspection to copy."

    def copy_inspection_value(self) -> str:
        """Return clipboard text for the pinned card's value (T18.3/4)."""
        from sharpmod.maps.map_field_inspection import value_copy_text

        snapshot = self.inspection_snapshot()
        if snapshot is None:
            return "No pinned inspection to copy."
        try:
            return str(value_copy_text(snapshot))
        except Exception:  # noqa: BLE001 - copy text is advisory
            return "No pinned inspection to copy."

    def inspection_save_payload(self, name: str) -> dict:
        """Return ``(name, lat, lon)`` for the pinned card (T18.3).

        Uses the *sampled* grid point for field cards and the card's own
        coordinate for station/point cards, so a saved location re-opens
        where the value was actually read.
        """
        label = str(name or "").strip()
        if not label:
            raise ValueError("location name cannot be empty")
        snapshot = self.inspection_snapshot()
        if snapshot is None:
            raise ValueError("no pinned inspection to save")
        kind = str(snapshot.get("kind") or "point")
        try:
            if kind == "field":
                lat = float(snapshot["sampled_lat"])
                lon = float(snapshot["sampled_lon"])
            elif kind == "station":
                station = self._station(str(snapshot.get("station_id") or ""))
                if station is None:
                    raise ValueError("pinned station is no longer available")
                lat, lon = float(station["lat"]), float(station["lon"])
            else:
                lat = float(snapshot["lat"])
                lon = float(snapshot["lon"])
        except (KeyError, TypeError, ValueError, OverflowError) as error:
            raise ValueError("pinned inspection has no usable coordinate") from error
        if not (-90.0 <= lat <= 90.0) or not -180.0 <= lon <= 180.0:
            raise ValueError("pinned inspection coordinate is out of range")
        return {"name": label, "lat": float(lat), "lon": float(lon)}

    # -- public API ---------------------------------------------------------- #








    # -- overlays ------------------------------------------------------------ #


    # -- time header, forecast reference, live/history mode (T21) ------------- #






    # -- projection (aspect-correct, letterboxed) ---------------------------- #








    # -- radar site markers -------------------------------------------------- #







    # -- offscreen buffers at real screen resolution ------------------------- #



    # -- basemap raster (cached per extent + size) --------------------------- #


    # -- place labels (T16.2) ------------------------------------------------ #


    # -- presentation preferences (T16.4) -------------------------------------- #


    # -- scale bar and orientation (T16.3) ----------------------------------- #


    # -- raster overlay painting --------------------------------------------- #


    # -- overlay painting ---------------------------------------------------- #



    # -- legend state, placement, scales, hover link (T20) ------------------- #


    # -- painting ------------------------------------------------------------ #



    # -- loaded-profile points (T22.4) -------------------------------------- #


    #: Longitude span, in degrees, below which every antenna gets a label. At a
    #: continental extent 155 four-letter identifiers overlap into a grey mat, so
    #: the ids appear as the view closes in and the hovered and selected ones are
    #: always named regardless.
    RADAR_LABEL_LON_SPAN = 26.0








    # -- interaction --------------------------------------------------------- #


class PointMapWidget(PointMapMixin, MapBoxMixin, StationMapWidget):
    """Clickable lat/lon picker map for forecast-model point soundings.

    Also draws and returns a rectangular *box* selection, used by the
    box-sounding feature to sample many points at once. A box is drawn either
    with Shift held or after :meth:`set_box_mode` is turned on, so the existing
    click-to-select and drag-to-pan gestures keep working unchanged.
    """

    pointSelected = Signal(float, float)  # lat, lon
    pointActivated = Signal(float, float)  # lat, lon
    #: Shared field-panel crosshair (T24.1): the hovered geographic location,
    #: as ``(lat, lon)``. Emitted on mouse moves that are not panning or
    #: drawing, so a host can mirror one geographic cursor across panels while
    #: each panel keeps sampling its own field. Plain hover state, not a fetch.
    crosshairHover = Signal(float, float)  # lat, lon
    #: lat0, lon0, lat1, lon1. Longitudes are deliberately **not** wrapped into
    #: [-180, 180): the raw projected values carry the span the user actually
    #: dragged, which is the only way a box across the antimeridian can be told
    #: apart from its 340-degree complement. ``BoxRegion.from_corners`` expects
    #: exactly this.
    boxSelected = Signal(float, float, float, float)
    boxCleared = Signal()
    boxModeChanged = Signal(bool)

    def __init__(self, parent=None):
        super().__init__([], parent=parent)
        self._point_lonlat = (-97.44, 35.63)
        #: Time/mode choices (T21): forecast tabs pin run/hour here so the
        #: header paints without reaching into a controller; the base widget
        #: owns the same names so one paint path serves every map subclass.
        #: ``_held_frames`` shelves newer live rasters under a historical view.
        self._forecast_run = None
        self._forecast_fxx: int | None = None
        self._map_mode = "live"
        self._held_frames: dict[str, object] = {}
        #: Reversible recent point (T18.1): the previous committed selection
        #: as ``(lon, lat)``, or ``None``. Written only by user commits
        #: (clicks/double-clicks), never by programmatic ``set_point``,
        #: session restores, or history steps.
        self._recent_point: tuple[float, float] | None = None
        #: Active numeric inspection source (T18.4): ``(product_key, run, fxx)``
        #: or ``None``. The run may be a datetime or an ISO string when it
        #: arrives from a cached render.
        self._inspection_source: tuple | None = None
        self._saved_points: tuple[tuple[str, float, float], ...] = ()
        self._domain_bounds: tuple[float, float, float, float] | None = None
        self._domain_outline: tuple[tuple[float, float], ...] = ()
        self._domain_label = ""
        #: Shared field-panel crosshair (T24.1): the hovered location from a
        #: sibling panel, as ``(lat, lon)``, or ``None``. Painted as a
        #: dashed full-extent guide plus a readout line naming this panel's
        #: own sampled value, so one geographic location is read against
        #: every panel's own field, units, valid time, and availability.
        self._crosshair_lonlat: tuple[float, float] | None = None
        self._crosshair_suppressed = False
        # Box selection state. ``_box_corners`` is the committed rectangle;
        # ``_box_anchor``/``_box_drag`` are live only while dragging. Pixel
        # copies are kept so the click-versus-drag test stays zoom-independent.
        self._box_mode = False
        self._box_corners: tuple[float, float, float, float] | None = None
        self._box_anchor: tuple[float, float] | None = None
        self._box_drag: tuple[float, float] | None = None
        self._box_anchor_px: QPointF | None = None
        self._box_drag_px: QPointF | None = None
        #: Post-creation editing (T23.1): ``_box_edit_kind`` is one of the four
        #: geographic corners or ``move``.  The origin is stored in the same
        #: unwrapped longitude copy the user grabbed, so dragging a box through
        #: the antimeridian never turns it into its 340-degree complement.
        self._box_edit_kind: str | None = None
        self._box_edit_origin: tuple[float, float, float, float] | None = None
        self._box_edit_anchor: tuple[float, float] | None = None
        self._box_edit_anchor_px: QPointF | None = None
        self._box_edit_moved = False
        #: ``(lon, lat)`` lattice preview, so the user can see exactly which
        #: points a box will sample before paying to extract them.
        self._box_nodes: tuple[tuple[float, float], ...] = ()
        self._box_node_coverage: tuple[bool, ...] = ()
        self._box_note = ""

    # -- explicit tool, recent point, numeric inspection (T18) ---------------- #


    # -- shared field-panel crosshair (T24.1) ------------------------------- #


    # -- box selection ------------------------------------------------------ #


    def keyPressEvent(self, event) -> None:  # noqa: N802
        # Escape clears the pinned card first, then the rectangle handling
        # below runs: one keypress clears one thing (T18.2).
        if event.key() == Qt.Key_Escape and getattr(
                self, "_inspection", None) is not None:
            self._inspection = None
            self.update()
            event.accept()
            return
        # Escape abandons an in-progress rectangle, and clears a committed one.
        if event.key() == Qt.Key_Escape:
            if self._box_edit_kind is not None:
                self._cancel_box_edit(restore=True)
                self.update()
                return
            if self._box_anchor is not None:
                self._cancel_box_drag()
                self.set_box_mode(False)
                self.update()
                self.boxCleared.emit()
                return
            if self._box_corners is not None:
                self.set_box_mode(False)
                self.clear_box()
                return
            if self._box_mode:
                self.set_box_mode(False)
                self.boxCleared.emit()
                return
        # U restores the reversible recent point (T18.1): same focus rules as
        # every other bare key, never from a text field, never while locked.
        if event.key() == Qt.Key_U:
            try:
                modifiers = event.modifiers()
            except AttributeError:
                modifiers = Qt.NoModifier
            if not (modifiers & (Qt.ControlModifier | Qt.AltModifier
                                 | Qt.ShiftModifier)):
                if self.restore_recent_point():
                    event.accept()
                    return
        handler = getattr(super(), "keyPressEvent", None)
        if handler is None:
            handler = StationMapWidget.keyPressEvent.__get__(self, type(self))
        handler(event)


from sharpmod.ui.maps.box_field import BoxFieldMixin  # noqa: E402


class BoxFieldMapWidget(BoxFieldMixin, PointMapWidget):
    """Box-analysis field map with the public historical class name."""

    cellSelected = Signal(int, int)  # row, col
    cellActivated = Signal(int, int)  # row, col (double click)

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
from sharpmod import gui_common as _gui_common
from sharpmod.gui_theme import current_theme, mono_font, ui_font
from sharpmod.map_overlays import OverlayRaster, describe_at, format_age
from sharpmod.overlay_hatch import hatch_brush as _overlay_hatch_brush
from sharpmod.theme import MapPalette, map_palette


def _map() -> MapPalette:
    """Return the map palette paired with the active chrome theme.

    Resolved on each call rather than cached on the widget, because the theme
    can change at runtime (File -> Preferences) and the maps repaint in response
    without being rebuilt.
    """
    return map_palette(current_theme())


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

#: Basemap layers the station map prepares and draws, in one place so a new
#: layer cannot be loaded but silently dropped during preparation.
BASEMAP_LAYER_NAMES = ("coastline", "lakes", "countries", "states")

#: Map views the user can choose between.
#:
#: ``flat`` is an equirectangular with a cosine-of-latitude longitude squeeze --
#: straight meridians and parallels, and the projection every version of this map
#: has used.
#:
#: ``curved`` is a Lambert conformal conic, the projection operational forecast
#: charts are drawn on: meridians converge toward the pole and parallels bow, so
#: a continent-wide view keeps its shape instead of stretching east-west with
#: latitude.
MAP_PROJECTIONS = ("flat", "curved")

#: Widest latitude span a cone can still represent sensibly. Past this a conic
#: either wraps past the pole or degenerates toward a cylinder, so the curved
#: view falls back to flat rather than drawing something wrong.
_CONIC_MAX_LAT_SPAN = 75.0

#: A cone standing on the equator is a cylinder, and its apex runs to infinity.
#: Views centred inside this band fall back to flat.
_CONIC_MIN_ABS_MID_LAT = 4.0

#: Latitudes are clamped inside the poles before projecting: the conic's radius
#: term is undefined at exactly +/-90.
_CONIC_LAT_LIMIT = 89.0


def _conic_constants(lat0: float, lat1: float, lon_ref: float = 0.0):
    """Return ``(n, F, rho0, lon_ref)`` for a Lambert conformal conic.

    ``lon_ref`` is the central meridian the cone is cut along -- the view's own
    centre longitude -- and every longitude is measured as an offset from it.

    Standard parallels are placed a sixth of the way in from each edge of the
    view, which is the usual choice: it splits the scale error across the map
    instead of piling it at one edge.

    Returns ``None`` when the view is one a cone cannot represent -- too tall, or
    centred too near the equator -- so the caller can fall back to a flat view
    rather than draw a distorted one.
    """
    south = max(-_CONIC_LAT_LIMIT, min(_CONIC_LAT_LIMIT, float(lat0)))
    north = max(-_CONIC_LAT_LIMIT, min(_CONIC_LAT_LIMIT, float(lat1)))
    if north <= south:
        return None
    span = north - south
    mid = (south + north) / 2.0
    if span > _CONIC_MAX_LAT_SPAN or abs(mid) < _CONIC_MIN_ABS_MID_LAT:
        return None
    phi1 = math.radians(south + span / 6.0)
    phi2 = math.radians(north - span / 6.0)
    try:
        t1 = math.tan(math.pi / 4.0 + phi1 / 2.0)
        t2 = math.tan(math.pi / 4.0 + phi2 / 2.0)
        if t1 <= 0.0 or t2 <= 0.0:
            return None
        if abs(phi1 - phi2) < 1e-9:
            n = math.sin(phi1)
        else:
            n = math.log(math.cos(phi1) / math.cos(phi2)) / math.log(t2 / t1)
        if not math.isfinite(n) or abs(n) < 1e-6:
            return None
        f = math.cos(phi1) * (t1**n) / n
        if not math.isfinite(f):
            return None
        rho0 = f / (math.tan(math.pi / 4.0 + math.radians(mid) / 2.0) ** n)
        if not math.isfinite(rho0):
            return None
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    return n, f, rho0, float(lon_ref)


class _Projection:
    """One resolved map transform: geographic degrees to widget pixels.

    Built once per paint and handed to every draw helper, so a widget's geometry
    is converted through exactly one object and a new projection cannot reach
    half the layers.

    ``affine`` says whether the transform is a pure scale-and-translate from
    geographic degrees.

    ``cone`` is the resolved conic constants, or ``None`` for a flat view. It
    exists so two projections can be compared: the plane-to-pixel step is a
    scale-and-translate for *both* views, so the basemap's gesture preview can
    re-blit a cached raster whenever two transforms share the same cone --
    ``None == None`` for flat, identical constants for a conic. Only the
    degrees-to-plane step differs, and that is what the cone pins down.
    """

    __slots__ = (
        "kind",
        "affine",
        "cone",
        "scale",
        "offx",
        "offy",
        "k",
        "x0",
        "y1",
        "_n",
        "_f",
        "_rho0",
        "_lon_ref",
    )

    def __init__(self, kind, *, scale, offx, offy, k=1.0, x0=0.0, y1=0.0, conic=None):
        self.kind = kind
        self.affine = conic is None
        self.cone = None if conic is None else tuple(conic)
        self.scale = scale
        self.offx = offx
        self.offy = offy
        self.k = k
        self.x0 = x0
        self.y1 = y1
        if conic is None:
            self._n = self._f = self._rho0 = self._lon_ref = 0.0
        else:
            self._n, self._f, self._rho0, self._lon_ref = conic

    # -- flat ------------------------------------------------------------- #
    def _flat_forward(self, lon, lat):
        return (
            self.offx + (lon * self.k - self.x0) * self.scale,
            self.offy + (self.y1 - lat) * self.scale,
        )

    def _flat_inverse(self, x, y):
        lon = ((x - self.offx) / self.scale + self.x0) / self.k
        lat = self.y1 - (y - self.offy) / self.scale
        return lon, lat

    # -- conic ------------------------------------------------------------ #
    def _conic_plane(self, lon, lat):
        n, f = self._n, self._f
        phi = math.radians(max(-_CONIC_LAT_LIMIT, min(_CONIC_LAT_LIMIT, lat)))
        # Longitudes are unwrapped onto the shortest way round from the
        # reference meridian, so a view crossing the antimeridian does not fling
        # its geometry the long way about the cone.
        delta = math.radians(((lon - self._lon_ref + 180.0) % 360.0) - 180.0)
        t = math.tan(math.pi / 4.0 + phi / 2.0)
        if t <= 0.0:
            return 0.0, 0.0
        rho = f / (t**n)
        theta = n * delta
        return rho * math.sin(theta), self._rho0 - rho * math.cos(theta)

    def _conic_forward(self, lon, lat):
        px, py = self._conic_plane(lon, lat)
        return (
            self.offx + (px - self.x0) * self.scale,
            self.offy + (self.y1 - py) * self.scale,
        )

    def _conic_inverse(self, x, y):
        n, f = self._n, self._f
        px = (x - self.offx) / self.scale + self.x0
        py = self.y1 - (y - self.offy) / self.scale
        dy = self._rho0 - py
        rho = math.hypot(px, dy)
        if n < 0.0:
            rho = -rho
        if abs(rho) < 1e-12:
            return self._lon_ref, math.copysign(_CONIC_LAT_LIMIT, n)
        theta = math.atan2(px, dy) if n > 0.0 else math.atan2(-px, -dy)
        lon = self._lon_ref + math.degrees(theta / n)
        try:
            phi = 2.0 * math.atan((f / rho) ** (1.0 / n)) - math.pi / 2.0
        except (ValueError, ZeroDivisionError, OverflowError):
            return lon, 0.0
        return lon, math.degrees(phi)

    # -- public ----------------------------------------------------------- #
    def forward(self, lon, lat):
        if self.affine:
            return self._flat_forward(lon, lat)
        return self._conic_forward(lon, lat)

    def inverse(self, x, y):
        if self.affine:
            return self._flat_inverse(x, y)
        return self._conic_inverse(x, y)


def _load_basemap() -> dict:
    """Load the bundled HD basemap layers for the station map.

    Returns a dict ``{"coastline": [...], "countries": [...], "states": [...]}``
    where each value is a list of ``[lon, lat]`` polylines. Resolved
    package-relative via :mod:`importlib.resources`; prefers the multi-layer
    ``basemap.json`` and falls back to the older single-layer
    ``coastlines.json`` (or empty layers) so the map always renders.
    """
    import json

    try:
        from importlib.resources import files

        pkg = files("sharpmod.resources")
        res = pkg.joinpath("basemap.json")
        data = json.loads(res.read_text(encoding="utf-8"))
        return {
            "coastline": data.get("coastline", []),
            # Absent from an older bundled basemap, which simply means no lake
            # outlines rather than a failure to load the map at all.
            "lakes": data.get("lakes", []),
            "countries": data.get("countries", []),
            "states": data.get("states", []),
        }
    except Exception:
        pass
    try:
        from importlib.resources import files

        res = files("sharpmod.resources").joinpath("coastlines.json")
        data = json.loads(res.read_text(encoding="utf-8"))
        return {
            "coastline": data.get("polylines", []),
            "lakes": [],
            "countries": [],
            "states": [],
        }
    except Exception:
        return {"coastline": [], "lakes": [], "countries": [], "states": []}


def _prepare_basemap_layers(basemap: dict) -> MappingProxyType:
    """Freeze basemap polylines and precompute their clipping bounds.

    Map widgets only read this geometry.  Freezing every container makes it
    safe for all picker tabs to share one prepared copy while their view,
    raster, timer, station, and selection state remains widget-local.
    """
    prepared = {}
    for name in BASEMAP_LAYER_NAMES:
        lines = []
        for points in basemap.get(name, ()):
            if len(points) < 2:
                continue

            frozen_points = []
            min_lon = min_lat = float("inf")
            max_lon = max_lat = float("-inf")
            for point in points:
                lon, lat = point[0], point[1]
                frozen_points.append((lon, lat))
                min_lon = min(min_lon, lon)
                max_lon = max(max_lon, lon)
                min_lat = min(min_lat, lat)
                max_lat = max(max_lat, lat)

            lines.append(
                (
                    (min_lon, max_lon, min_lat, max_lat),
                    tuple(frozen_points),
                )
            )
        prepared[name] = tuple(lines)
    return MappingProxyType(prepared)


@lru_cache(maxsize=1)
def _shared_basemap_layers() -> MappingProxyType:
    """Return the process-wide immutable, clipping-ready basemap geometry."""
    return _prepare_basemap_layers(_load_basemap())


#: Alpha applied to an overlay's published fill colour. SPC's palette is built
#: for opaque printing on white, so it is drawn translucent here to keep the
#: coastline, borders, and station dots readable underneath it.
OVERLAY_FILL_ALPHA = 68
#: The legend swatch is small, so it needs more opacity than the map area to
#: read as the same colour.
OVERLAY_LEGEND_FILL_ALPHA = 150

#: A point marker is opaque. It covers a handful of pixels, so it has nothing to
#: hide, and a translucent dot reads as a smudge rather than a mark.
OVERLAY_MARKER_FILL_ALPHA = 255
#: Hatched areas annotate the band beneath them, so the strokes stay legible.
OVERLAY_HATCH_ALPHA = 190
OVERLAY_STROKE_WIDTH = 1.8

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

# --------------------------------------------------------------------------- #
# Legend geometry
#
# The legend stacks up to three blocks in the bottom-left corner: a colour bar
# for a continuous field, a row of categorical swatches, and the prose captions.
# Each block's height is declared here and nowhere else, because the bug these
# constants replace was a reserved height that had drifted from the height the
# drawing actually used -- the colour bar needs a caption line, the bar, and a
# row of tick numbers, and reserving only part of that drew the swatches and the
# prose straight over the numbers.
# --------------------------------------------------------------------------- #

#: Left inset shared by every legend block, so their left edges line up.
LEGEND_MARGIN = 10.0
#: One line of caption prose.
LEGEND_LINE_H = 15.0
#: The categorical swatch row.
LEGEND_SWATCH_H = 14.0
#: Separation between two adjacent blocks. Without it the tick numbers and the
#: swatch boxes touch, which reads as one crowded row rather than two things.
LEGEND_BLOCK_GAP = 6.0

#: The colour bar's own three pieces.
COLOUR_BAR_HEADER_H = 13.0
COLOUR_BAR_H = 9.0
COLOUR_BAR_TICK_H = 12.0
#: What a colour bar costs in total. This is the number the legend reserves.
COLOUR_BAR_BLOCK_H = COLOUR_BAR_HEADER_H + COLOUR_BAR_H + COLOUR_BAR_TICK_H


def _interpolate_stops(stops, value: float) -> str:
    """Return the blended ``#rrggbb`` for ``value`` on a continuous scale."""
    if value <= stops[0][0]:
        return stops[0][1]
    if value >= stops[-1][0]:
        return stops[-1][1]
    for index in range(len(stops) - 1):
        low, low_colour = stops[index]
        high, high_colour = stops[index + 1]
        if low <= value <= high:
            fraction = (value - low) / ((high - low) or 1.0)
            channels = []
            for offset in (1, 3, 5):
                start = int(low_colour[offset : offset + 2], 16)
                end = int(high_colour[offset : offset + 2], 16)
                channels.append(int(round(start + (end - start) * fraction)))
            return "#%02x%02x%02x" % tuple(channels)
    return stops[-1][1]


def _format_tick(value: float) -> str:
    """Format a colour-bar tick without trailing noise.

    Scales in this application span whole thousands of joules and tenths of a
    composite index, so a single format string would print either ``6000.0`` or
    ``1`` where ``1.5`` was meant.
    """
    if value == int(value):
        return "%d" % int(value)
    return ("%.1f" % value).rstrip("0").rstrip(".")


#: Target cell size, in degrees, for warping imagery into a curved projection.
#:
#: The piecewise transform treats each cell as affine, so the error inside one
#: grows with how much the projection curves across it. Two degrees keeps that
#: under a pixel at every extent the picker offers, measured against projecting
#: each pixel exactly, while staying coarse enough that a full-CONUS frame is a
#: few hundred blits rather than a few thousand.
_WARP_TARGET_CELL_DEG = 2.0
#: Bounds on the lattice. The floor keeps a tiny extent from being warped by a
#: single cell, which would be the rectangle this code exists to avoid; the
#: ceiling bounds one frame's cost.
_WARP_MIN_CELLS = 2
_WARP_MAX_CELLS = 48

#: Target segment length, in degrees, when walking a model-domain edge.
#:
#: A domain given as a lon/lat box used to be drawn from its four corners alone.
#: That is exact on the flat view, where a box really is an axis-aligned
#: rectangle, but on the conic view a parallel projects to an arc and joining the
#: corners cuts the chord across it: measured on a CONUS view at 1024x640, the
#: southern edge was drawn 118 px from where the model actually ends and the
#: northern edge 73 px, both worst at the central meridian. The dashed outline is
#: what tells a forecaster where a sounding can be pulled from, so it has to
#: follow the projection. One degree keeps the residual under a pixel at
#: continental extents and costs a polygon of a couple of hundred points.
_DOMAIN_EDGE_STEP_DEG = 1.0

#: Bounds on the walk, for the same reasons as the lattice above.
_DOMAIN_MIN_STEPS = 2
_DOMAIN_MAX_STEPS = 180


#: Bottom-to-top paint order for image overlays that share the map.
#:
#: A model field is an environment: a broad, smooth area telling the user what
#: the atmosphere is like. Radar is a location: small, bright, and the thing
#: being located within that environment. So the field goes down first and radar
#: lands on top of it, which is the order every mesoanalysis page uses and the
#: only one where both remain readable -- radar echoes are far too small to
#: survive being covered by a continent-wide wash of colour.
#:
#: Keys not listed here take :data:`RASTER_ORDER_DEFAULT`, so an overlay added
#: later draws above the model field and below nothing, rather than disappearing.
RASTER_DRAW_ORDER = {
    "hrrr_field": 10,
    "radar_mosaic": 40,
    "radar_site": 50,
}
RASTER_ORDER_DEFAULT = 30


def hatch_brush(colour: QColor, level: int) -> QBrush:
    """Return the brush for an overlay hatch qualifier at ``level``.

    Delegates so the picker maps and the hodograph's locator inset cannot drift
    apart on a pattern that is the only thing distinguishing one SPC intensity
    group from the next. Bound at import rather than per call, because this runs
    once per hatched shape inside a repaint.
    """
    return _overlay_hatch_brush(QtCore, QtGui, colour, level)


#: Named map extents for the "Map Area" selector: (lon0, lon1, lat0, lat1).
MAP_AREAS: dict[str, tuple[float, float, float, float]] = {
    "United States (CONUS)": (-125.0, -66.0, 23.0, 50.0),
    "Alaska": (-180.0, -125.0, 48.0, 73.0),
    "Hawaii": (-162.5, -152.0, 15.0, 24.5),
    "Puerto Rico": (-69.5, -64.0, 16.5, 20.0),
    "North America": (-170.0, -50.0, 8.0, 75.0),
    "Caribbean / Gulf": (-100.0, -50.0, 5.0, 35.0),
    "Western Pacific": (115.0, 170.0, -5.0, 30.0),
    "Northern Hemisphere": (-180.0, 180.0, 0.0, 88.0),
    "Southern Hemisphere": (-180.0, 180.0, -88.0, 0.0),
    "Europe": (-15.0, 45.0, 34.0, 72.0),
    "Australia / Oceania": (110.0, 180.0, -50.0, 5.0),
    "Tropics": (-180.0, 180.0, -30.0, 30.0),
    "World": (-180.0, 180.0, -85.0, 85.0),
}

#: Buttons that pan the map but never select anything. Keeping a pan available on
#: a button the box gesture does not use is what lets the map still be moved
#: while box mode is armed -- the same arrangement selection tools elsewhere use.
SECONDARY_PAN_BUTTONS = Qt.MiddleButton | Qt.RightButton

#: Every button that can pan. The left button pans only when no other gesture
#: (a box drag) has claimed the press.
PAN_BUTTONS = Qt.LeftButton | SECONDARY_PAN_BUTTONS


class StationMapWidget(QWidget):
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
        self._stations = list(stations)
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
        self.setMinimumSize(QSize(520, 380))
        self.setMouseTracking(True)
        self.setCursor(Qt.CrossCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    # -- data prep ----------------------------------------------------------- #
    @staticmethod
    def _prep_layers(basemap: dict) -> MappingProxyType:
        """Precompute each polyline's lon/lat bounding box for fast clipping."""
        return _prepare_basemap_layers(basemap)

    # -- public API ---------------------------------------------------------- #
    def set_area(self, name: str) -> None:
        if name in MAP_AREAS:
            self._area_name = name
            self._lon0, self._lon1, self._lat0, self._lat1 = MAP_AREAS[name]
            self._invalidate()

    def set_extent(self, lon0, lon1, lat0, lat1, *, pad=0.2) -> None:
        """Frame an arbitrary lon/lat extent, with proportional padding.

        Complements :meth:`set_area`, which is limited to the named regions. A
        wrapped extent (``lon0 > lon1``) is unrolled eastward so the view keeps
        a single increasing longitude range, which is what the projection needs.
        """
        lon0 = float(lon0)
        lon1 = float(lon1)
        lat0, lat1 = sorted((float(lat0), float(lat1)))
        if lon1 <= lon0:
            lon1 += 360.0
        lon_pad = max(0.05, (lon1 - lon0) * float(pad))
        lat_pad = max(0.05, (lat1 - lat0) * float(pad))
        self._area_name = ""
        self._lon0 = lon0 - lon_pad
        self._lon1 = lon1 + lon_pad
        self._lat0 = max(-89.99, lat0 - lat_pad)
        self._lat1 = min(89.99, lat1 + lat_pad)
        self._invalidate()

    def restore_view_bounds(self, bounds) -> None:
        """Restore a previously captured viewport without adding padding.

        ``set_extent`` intentionally frames new data with a minimum border.
        Reusing it for session restoration compounds that border on every
        save/open cycle, so persisted view bounds have a distinct exact path.
        """
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
            raise ValueError("view bounds must contain four values")
        lon0, lon1, lat0, lat1 = (float(value) for value in bounds)
        if not all(math.isfinite(value) for value in (lon0, lon1, lat0, lat1)):
            raise ValueError("view bounds must be finite")
        if lon1 <= lon0 or lat1 <= lat0:
            raise ValueError("view bounds must be increasing")
        if lat0 < -89.99 or lat1 > 89.99:
            raise ValueError("view latitude bounds are out of range")
        self._area_name = ""
        self._lon0, self._lon1 = lon0, lon1
        self._lat0, self._lat1 = lat0, lat1
        self._invalidate()

    def reset_view(self) -> None:
        """Snap back to the current named region's default extent."""
        self.set_area(self._area_name)

    def zoom(self, factor: float) -> None:
        """Zoom about the map center (``factor`` < 1 zooms in)."""
        clon = (self._lon0 + self._lon1) / 2.0
        clat = (self._lat0 + self._lat1) / 2.0
        self._lon0 = clon + (self._lon0 - clon) * factor
        self._lon1 = clon + (self._lon1 - clon) * factor
        self._lat0 = clat + (self._lat0 - clat) * factor
        self._lat1 = clat + (self._lat1 - clat) * factor
        self._invalidate()

    def set_stations(self, stations) -> None:
        """Replace the plotted station set (keeps a valid selection).

        Used when the datetime-aware station list is refreshed from UWyo: the
        map redraws with exactly the stations available at that time. The
        current selection/hover is cleared if it no longer exists.
        """
        self._stations = list(stations)
        ids = {s["id"] for s in self._stations}
        if self._selected_id not in ids:
            self._selected_id = None
        if self._hover_id not in ids:
            self._hover_id = None
            self._hover_lonlat = None
        self.update()

    def set_selected(self, sid: str | None) -> None:
        self._selected_id = sid
        self.update()

    def center_on(self, sid: str) -> None:
        """Pan the view so ``sid`` is centred (keeps the current zoom span)."""
        st = self._station(sid)
        if st is None:
            return
        span_lon = (self._lon1 - self._lon0) / 2.0
        span_lat = (self._lat1 - self._lat0) / 2.0
        self._lon0, self._lon1 = st["lon"] - span_lon, st["lon"] + span_lon
        self._lat0, self._lat1 = st["lat"] - span_lat, st["lat"] + span_lat
        self._invalidate()

    def view_bounds(self) -> tuple[float, float, float, float]:
        """Return the visible extent as ``(lon0, lon1, lat0, lat1)``.

        Exists so a controller can decide whether a regional product could be
        seen at all before spending a request on it, without reaching into the
        widget's private viewport fields.
        """
        return (self._lon0, self._lon1, self._lat0, self._lat1)

    # -- overlays ------------------------------------------------------------ #
    def set_overlay(self, key: str, layer, *, visible: bool | None = None) -> None:
        """Attach or replace the overlay stored under ``key``.

        Passing ``None`` for ``layer`` removes it. Visibility is remembered
        across replacements so refreshing an overlay for a new valid time does
        not silently re-enable one the user turned off; pass ``visible`` to set
        it explicitly.
        """
        if layer is None:
            self.remove_overlay(key)
            return
        # Routed on type so callers attach either kind through one method. A key
        # is claimed by whichever kind arrives, and the other registry is cleared
        # of it so a product that changes representation cannot leave a stale
        # twin drawing underneath.
        if isinstance(layer, OverlayRaster):
            self._overlays.pop(key, None)
            self._rasters[key] = layer
        else:
            self._rasters.pop(key, None)
            self._raster_pixmaps.pop(key, None)
            self._overlays[key] = layer
        if visible is not None:
            self._overlay_visible[key] = bool(visible)
        else:
            self._overlay_visible.setdefault(key, True)
        self.update()

    def remove_overlay(self, key: str) -> None:
        """Detach the overlay stored under ``key``, keeping its toggle state."""
        removed = self._overlays.pop(key, None) is not None
        removed |= self._rasters.pop(key, None) is not None
        self._raster_pixmaps.pop(key, None)
        if removed:
            self.update()

    def overlay(self, key: str):
        """Return the overlay stored under ``key``, vector or raster, or ``None``."""
        layer = self._overlays.get(key)
        if layer is not None:
            return layer
        return self._rasters.get(key)

    def overlay_keys(self) -> tuple[str, ...]:
        return tuple(self._overlays) + tuple(self._rasters)

    def set_overlay_visible(self, key: str, visible: bool) -> None:
        """Show or hide one overlay without discarding its geometry.

        The layer is kept so toggling back on is instant and needs no refetch.
        """
        visible = bool(visible)
        if self._overlay_visible.get(key) == visible:
            return
        self._overlay_visible[key] = visible
        self.update()

    def is_overlay_visible(self, key: str) -> bool:
        return bool(self._overlay_visible.get(key, True))

    def set_valid_time(self, when: datetime | None) -> None:
        """Record the valid time the map's overlays should describe.

        Only used for display: the map reports when an attached overlay does
        not cover this time so a mismatched product cannot pass for a current
        one. Fetching the right overlay stays the caller's job.
        """
        if self._valid_time == when:
            return
        self._valid_time = when
        if self._overlays:
            self.update()

    def valid_time(self) -> datetime | None:
        return self._valid_time

    def _visible_overlays(self) -> list:
        return [
            layer
            for key, layer in self._overlays.items()
            if self._overlay_visible.get(key, True) and layer
        ]

    def _raster_decode_failed(self, key: str, raster: OverlayRaster) -> bool:
        """Report whether this exact payload has already failed to decode."""
        cached = self._raster_pixmaps.get(key)
        return (
            cached is not None and cached[1] is None and cached[0] is raster.image_bytes
        )

    def _visible_rasters(self) -> list[tuple[str, OverlayRaster]]:
        """Return visible raster overlays that overlap the current view.

        The view test happens here rather than in the paint loop so a product
        covering somewhere the map is not looking never has its payload decoded.

        A payload already known not to decode is dropped too. That is what keeps
        the legend honest: it is drawn from this same list, so without the filter
        a corrupt frame would be captioned and credited on screen while nothing
        at all had been painted. The entry clears itself when a new frame
        arrives, because the decode cache is keyed on the payload object.
        """
        view = (self._lon0, self._lon1, self._lat0, self._lat1)
        visible = [
            (key, raster)
            for key, raster in self._rasters.items()
            if self._overlay_visible.get(key, True)
            and raster
            and raster.intersects(view)
            and not self._raster_decode_failed(key, raster)
        ]
        # Sorted, because these images stack and dict insertion order is not a
        # z-order: it depends on which overlay the user happened to switch on
        # first, and a remove/set cycle silently reshuffles it. A model field and
        # a radar frame are routinely shown together, and which one ends up on
        # top has to be a decision rather than an accident.
        visible.sort(
            key=lambda entry: RASTER_DRAW_ORDER.get(entry[0], RASTER_ORDER_DEFAULT)
        )
        return visible

    def _invalidate(self) -> None:
        self._basemap_refresh_timer.stop()
        self._basemap_cache = None
        self._cache_key = None
        self._cache_proj = None
        # The warped imagery is built under the same transform as the
        # basemap, so it goes stale at exactly the same moments.
        self._warp_cache = None
        self._warp_proj = None
        self._conic_freeze = None
        self.update()
        # An extent set outright -- a region change, a reset, a resize -- is a
        # settled view straight away; there is no gesture to wait out.
        self._announce_view_settled()

    def _queue_map_preview(self) -> None:
        """Reuse the current raster during rapid pan or wheel input.

        Re-rasterizing the vector basemap takes tens of milliseconds, so doing
        it for every input event makes input queue up.  Keep the last crisp
        frame as a transformed preview and rebuild it once input pauses.

        On the curved view this also pins the cone, which is what makes the
        preview possible at all. Re-blitting the cached raster needs the two
        transforms to differ by a scale-and-translate, and refitting the
        standard parallels to the moving extent breaks that: every mouse-move
        then re-projects all ~113k basemap vertices, which measured 101 ms a
        frame against 3 ms with the cone pinned.

        The pin lasts for the gesture and no longer. It used to be held until a
        region was picked, on the reasoning that a projection is a property of a
        chart and panning only moves the viewport through it. That is true of a
        GIS layer in a fixed coordinate system, and wrong here: this cone is
        fitted to the view, so holding it across a session's worth of pans left
        the map leaning further and further from north with no way back. See
        :meth:`_finish_map_preview`, which releases it once the view settles.

        Held, not frozen: :meth:`_build_proj` still fits a fresh cone each time
        to decide whether the view is one a cone can represent at all, so
        zooming out past the conic limit falls back to flat as it always did.
        """
        if self._basemap_cache is None or self._cache_proj is None:
            self._invalidate()
            return
        if self._conic_freeze is None:
            # The cone of the raster being previewed, so the transform stays a
            # scale-and-translate away from what is already on screen. ``None``
            # on a flat view, which leaves that path exactly as it was.
            self._conic_freeze = self._cache_proj.cone
        self._basemap_refresh_timer.start()
        self.update()

    def _finish_map_preview(self) -> None:
        """Discard the temporary preview and request one crisp vector frame.

        The cone is released here, so the settled view is drawn on a cone fitted
        to *it* rather than to wherever the view happened to be when the region
        was chosen.

        It used to survive, to avoid the map re-bowing a fifth of a second after
        the drag ended. The cost of that turned out to be far worse than the
        settle it avoided: a conic's central meridian is the longitude it was cut
        along, and screen-up only points at true north there. Every degree the
        view travels east or west leaves the map leaning by ``n`` degrees -- about
        0.6 at CONUS latitudes -- and because the cone was only released when a
        region was picked, the lean accumulated across every pan of the session
        and never came back. Measured from a CONUS fit: 12 degrees of lean after
        panning 20 degrees west, 16.9 by the west coast, 13.3 over New England.
        A map that leans by sixteen degrees is not a map anybody asked for.

        So the settle is accepted and the lean is not. Refitting restores true
        north exactly, and the content shift it brings arrives while the basemap
        is being rebuilt anyway, at rest, after the user has let go -- which reads
        as the map finishing rather than as the map moving.

        The cone is still held *during* the gesture by :meth:`_queue_map_preview`,
        which is what keeps a drag re-blittable instead of re-projecting 113k
        vertices per mouse-move. Only the settled frame refits.
        """
        self._basemap_cache = None
        self._cache_key = None
        self._cache_proj = None
        # Released so the settled frame refits to the view it actually shows.
        self._conic_freeze = None
        # The warped imagery is built under the same transform as the
        # basemap, so it goes stale at exactly the same moments.
        self._warp_cache = None
        self._warp_proj = None
        self.update()
        self._announce_view_settled()

    def _announce_view_settled(self) -> None:
        """Tell listeners the view has come to rest. See :attr:`viewSettled`.

        Guarded because this runs from a timer and during teardown: a listener
        raising here would otherwise escape into the Qt event loop, where an
        exception has nowhere useful to go.
        """
        try:
            self.viewSettled.emit()
        except RuntimeError:  # pragma: no cover - widget already destroyed
            pass

    # -- projection (aspect-correct, letterboxed) ---------------------------- #
    def projection(self) -> str:
        """Return the active map view, ``"flat"`` or ``"curved"``."""
        return self._projection

    def set_projection(self, name) -> None:
        """Choose the map view. Unknown names fall back to ``"flat"``."""
        wanted = str(name or "flat").strip().lower()
        if wanted not in MAP_PROJECTIONS:
            wanted = "flat"
        if wanted == self._projection:
            return
        self._projection = wanted
        # The cached basemap raster bakes the transform in, so it cannot be
        # reused across a projection change.
        self._basemap_cache = None
        self._cache_key = None
        self._cache_proj = None
        # The warped imagery is built under the same transform as the
        # basemap, so it goes stale at exactly the same moments.
        self._warp_cache = None
        self._warp_proj = None
        self._conic_freeze = None
        self.update()

    def _proj(self) -> "_Projection":
        """Return the resolved transform for the current view and size.

        A curved view that a cone cannot represent -- too tall, or centred on the
        equator -- resolves to the flat transform instead of a distorted conic.
        That fallback is silent by design: the alternative is refusing to draw a
        map the user asked for.

        The result is memoized on the view, the widget size, and any pinned
        cone. It is a pure function of those, and a conic one costs 68 boundary
        samples to fit, which is not something to repeat for every mouse-move
        readout.
        """
        w = max(1, self.width())
        h = max(1, self.height())
        key = (
            self._projection,
            self._lon0,
            self._lon1,
            self._lat0,
            self._lat1,
            w,
            h,
            self._conic_freeze,
        )
        cached = self._proj_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        projection = self._build_proj(w, h)
        self._proj_cache = (key, projection)
        return projection

    def _build_proj(self, w: int, h: int) -> "_Projection":
        """Fit the current view into ``w`` x ``h``. See :meth:`_proj`."""
        conic = None
        if self._projection == "curved":
            conic = _conic_constants(
                self._lat0, self._lat1, (self._lon0 + self._lon1) / 2.0
            )
            # Across pans and zooms the cone is the one pinned to the chosen
            # view, so the transform stays re-blittable and the map does not
            # re-bow under the cursor. The fresh fit above is still what decides
            # *whether* there is a cone at all, so zooming out past the conic
            # limit falls back to flat instead of drawing a cone wrapped past
            # the pole. See :meth:`_queue_map_preview`.
            if conic is not None and self._conic_freeze is not None:
                conic = self._conic_freeze
        if conic is not None:
            probe = _Projection("curved", scale=1.0, offx=0.0, offy=0.0, conic=conic)
            # The edges of a conic view bow, so the projected bounds come from
            # sampling the boundary rather than from the four corners alone --
            # corners alone would clip the bulge off the top and bottom edges.
            xs, ys = [], []
            steps = 16
            for index in range(steps + 1):
                frac = index / steps
                lon = self._lon0 + (self._lon1 - self._lon0) * frac
                lat = self._lat0 + (self._lat1 - self._lat0) * frac
                for point in (
                    probe._conic_plane(lon, self._lat0),
                    probe._conic_plane(lon, self._lat1),
                    probe._conic_plane(self._lon0, lat),
                    probe._conic_plane(self._lon1, lat),
                ):
                    xs.append(point[0])
                    ys.append(point[1])
            box_w = max(1e-9, max(xs) - min(xs))
            box_h = max(1e-9, max(ys) - min(ys))
            scale = min(w / box_w, h / box_h)
            return _Projection(
                "curved",
                scale=scale,
                offx=(w - box_w * scale) / 2.0,
                offy=(h - box_h * scale) / 2.0,
                x0=min(xs),
                y1=max(ys),
                conic=conic,
            )

        lat_ref = math.radians((self._lat0 + self._lat1) / 2.0)
        k = max(0.05, math.cos(lat_ref))
        x0 = self._lon0 * k
        x1 = self._lon1 * k
        box_w = max(1e-6, x1 - x0)
        box_h = max(1e-6, self._lat1 - self._lat0)
        scale = min(w / box_w, h / box_h)
        return _Projection(
            "flat",
            scale=scale,
            offx=(w - box_w * scale) / 2.0,
            offy=(h - box_h * scale) / 2.0,
            k=k,
            x0=x0,
            y1=self._lat1,
        )

    def _to_px(self, lon: float, lat: float, p=None) -> QPointF:
        x, y = (self._proj() if p is None else p).forward(lon, lat)
        return QPointF(x, y)

    def _to_lonlat(self, x: float, y: float) -> tuple[float, float]:
        return self._proj().inverse(x, y)

    def _station(self, sid):
        for s in self._stations:
            if s["id"] == sid:
                return s
        return None

    def _nearest(self, x: float, y: float, max_px: float = 12.0):
        p = self._proj()
        w, h = self.width(), self.height()
        best, best_d2 = None, max_px * max_px
        for s in self._stations:
            pt = self._to_px(s["lon"], s["lat"], p)
            if pt.x() < -5 or pt.y() < -5 or pt.x() > w + 5 or pt.y() > h + 5:
                continue
            d2 = (pt.x() - x) ** 2 + (pt.y() - y) ** 2
            if d2 <= best_d2:
                best, best_d2 = s, d2
        return best

    # -- radar site markers -------------------------------------------------- #
    def set_radar_sites(self, sites) -> None:
        """Show a set of radar antennas as pickable markers.

        Accepts anything with ``id``/``lat``/``lon``, as attributes or as
        mapping keys, so the catalogue's own records can be passed straight in.
        An empty sequence turns the layer off, which is how the radar controller
        hides it when the mosaic scope or the whole overlay is deselected.
        """
        resolved: list[tuple[str, float, float]] = []
        for entry in sites or ():
            try:
                if isinstance(entry, dict):
                    site_id = str(entry["id"])
                    lat = float(entry["lat"])
                    lon = float(entry["lon"])
                else:
                    site_id = str(entry.id)
                    lat = float(entry.lat)
                    lon = float(entry.lon)
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if not (-90.0 <= lat <= 90.0):
                continue
            resolved.append((site_id.upper(), ((lon + 180.0) % 360.0) - 180.0, lat))
        markers = tuple(resolved)
        if markers == self._radar_sites:
            return
        self._radar_sites = markers
        if not markers:
            self._radar_hover_id = None
        self.update()

    def radar_sites_visible(self) -> bool:
        return bool(self._radar_sites)

    def set_radar_site_selected(self, site_id: str | None) -> None:
        """Mark one antenna as the chosen one, or ``None`` for none."""
        wanted = None if not site_id else str(site_id).strip().upper()
        if wanted == self._radar_site_id:
            return
        self._radar_site_id = wanted
        self.update()

    def _radar_site_hit(self, x: float, y: float, max_px: float = 11.0):
        """Return ``((id, lon, lat), distance_squared)``, or ``None``."""
        if not self._radar_sites:
            return None
        p = self._proj()
        w, h = self.width(), self.height()
        best, best_d2 = None, max_px * max_px
        for marker in self._radar_sites:
            _site_id, lon, lat = marker
            pt = self._to_px(lon, lat, p)
            if pt.x() < -5 or pt.y() < -5 or pt.x() > w + 5 or pt.y() > h + 5:
                continue
            d2 = (pt.x() - x) ** 2 + (pt.y() - y) ** 2
            if d2 <= best_d2:
                best, best_d2 = marker, d2
        return None if best is None else (best, best_d2)

    def _radar_site_at(self, x: float, y: float, max_px: float = 11.0):
        """Return ``(id, lon, lat)`` for the antenna under a point, or ``None``."""
        hit = self._radar_site_hit(x, y, max_px)
        return None if hit is None else hit[0]

    def _distance2(self, pos, lon: float, lat: float) -> float:
        """Squared pixel distance from ``pos`` to a geographic point."""
        pt = self._to_px(lon, lat, self._proj())
        return (pt.x() - pos.x()) ** 2 + (pt.y() - pos.y()) ** 2

    def _pick_radar_site(self, pos, rival_d2: float | None = None) -> bool:
        """Select the antenna under ``pos``. Returns whether one was taken.

        ``rival_d2`` is the squared distance to whatever else is competing for
        the click. Many sounding sites share a mast with a WSR-88D -- Dodge City
        with KDDC, Lincoln with KILX -- so giving the radar an unconditional veto
        would make those stations unselectable for as long as the layer was
        showing, and giving it the click whenever it is merely nearer decides
        co-located pairs by rounding error. The antenna therefore has to be
        :data:`RADAR_PICK_MARGIN_PX` clearly closer than the rival to take the
        click, and the station keeps every shared mast.

        The caller stops when this returns ``True``: a click resolved to a radar
        must not also move the sounding point or select a station under it.
        """
        hit = self._radar_site_hit(pos.x(), pos.y())
        if hit is None:
            return False
        marker, distance2 = hit
        if rival_d2 is not None:
            # Compared as distances, not squares: the margin is a pixel count,
            # and squared units would scale it with how far out the click landed.
            if math.sqrt(distance2) + RADAR_PICK_MARGIN_PX >= math.sqrt(rival_d2):
                return False
        self._radar_site_id = marker[0]
        self.update()
        self.radarSiteSelected.emit(marker[0])
        return True

    # -- offscreen buffers at real screen resolution ------------------------- #
    def _device_pixmap(self):
        """Return an offscreen pixmap matching the widget's *physical* pixels.

        ``QPixmap(self.size())`` allocates a **logical**-size buffer. On a 1.5x
        display the widget's backing store is 1.5x larger, so blitting such a
        buffer 1:1 makes Qt bilinearly upscale it, and every raster routed
        through an offscreen cache -- the vector basemap and the warped imagery
        composite -- arrived on screen softened. Nothing downstream needs to
        change: a painter opened on a pixmap that carries a device pixel ratio
        applies that ratio to its transform, so the drawing code keeps working in
        the same logical coordinates and simply resolves finer.

        The ratio is deliberately not rounded. The picker asks for
        ``PassThrough`` scale-factor rounding, so 1.25 and 1.75 are ordinary.
        """
        ratio = self.devicePixelRatioF()
        pm = QPixmap(
            max(1, round(self.width() * ratio)), max(1, round(self.height() * ratio))
        )
        pm.setDevicePixelRatio(ratio)
        return pm

    @staticmethod
    def _independent_size(pixmap) -> tuple[float, float]:
        """Return ``pixmap``'s size in the logical units a painter addresses.

        Spelled out rather than using ``deviceIndependentSize`` so the call still
        works under the Qt5 bindings qtpy may supply.
        """
        ratio = pixmap.devicePixelRatio() or 1.0
        return pixmap.width() / ratio, pixmap.height() / ratio

    @staticmethod
    def _device_source(pixmap, source):
        """Convert a logical source rectangle into the device pixels Qt expects.

        ``drawPixmap``'s destination is in logical coordinates but its *source*
        rectangle is measured in the pixmap's own device pixels, so a
        high-resolution cache needs the two expressed in different units.
        """
        ratio = pixmap.devicePixelRatio() or 1.0
        if ratio == 1.0:
            return source
        return QRectF(
            source.x() * ratio,
            source.y() * ratio,
            source.width() * ratio,
            source.height() * ratio,
        )

    # -- basemap raster (cached per extent + size) --------------------------- #
    def _basemap_key(self) -> tuple:
        """Everything the baked basemap raster depends on.

        The palette is part of it because the pixmap bakes in the background,
        graticule, and border colours, so switching theme has to invalidate it or
        the previous theme's basemap stays on screen; it is a frozen dataclass of
        strings, hence hashable. The projection is part of it because the
        transform is painted in just as permanently.

        Shared with :meth:`_draw_basemap` so the preview shortcut compares like
        with like. Two keys built to different recipes never compare equal, which
        silently turns "has the view moved?" into a constant.
        """
        return (
            self.width(),
            self.height(),
            # Dragging the window to a monitor of a different density has to
            # rebuild the raster, or a cache baked for the old ratio is
            # rescaled onto the new backing store.
            round(self.devicePixelRatioF(), 4),
            round(self._lon0, 4),
            round(self._lon1, 4),
            round(self._lat0, 4),
            round(self._lat1, 4),
            self._projection,
            _map(),
        )

    def _basemap_pixmap(self):
        key = self._basemap_key()
        if self._basemap_cache is not None and self._cache_key == key:
            return self._basemap_cache

        pm = self._device_pixmap()
        pm.fill(QColor(_map().background))
        qp = QPainter(pm)
        qp.setRenderHint(QPainter.Antialiasing, True)
        p = self._proj()
        self._draw_graticule(qp, p)
        # Draw borders first (dim), coastline last (bright) so it reads on top.
        self._draw_layer(qp, self._layers.get("states", []), _map().states, 1.0, p)
        self._draw_layer(
            qp, self._layers.get("countries", []), _map().countries, 1.0, p
        )
        # Lake shores share the coastline colour and sit just under it: they are
        # the same kind of boundary, so giving them their own hue would imply a
        # distinction that does not exist. Slightly finer, because a lake outline
        # enclosing a small area reads heavier than an open coast of the same
        # weight.
        self._draw_layer(qp, self._layers.get("lakes", []), _map().coastline, 1.1, p)
        self._draw_layer(
            qp, self._layers.get("coastline", []), _map().coastline, 1.4, p
        )
        qp.end()

        self._basemap_cache = pm
        self._cache_key = key
        self._cache_proj = p
        return pm

    def _draw_basemap(self, qp: QPainter) -> None:
        """Draw either the exact basemap or a fast transformed wheel preview."""
        key = self._basemap_key()
        new_projection = self._proj()
        previewing = (
            self._basemap_cache is not None
            and self._cache_proj is not None
            and self._cache_key != key
            and self._basemap_refresh_timer.isActive()
            # The shortcut below re-blits the cached raster under a changed
            # view, which holds exactly while the two transforms differ by a
            # scale-and-translate. Both views reach pixels that way -- flat from
            # squeezed degrees, curved from the cone's plane -- so what has to
            # match is the step in front of it: the cone. ``None == None`` is
            # the flat case; identical constants is a curved view whose cone is
            # pinned for the gesture. A *different* cone bows its parallels
            # differently and genuinely has to be redrawn.
            and self._cache_proj.cone == new_projection.cone
        )
        if not previewing:
            qp.drawPixmap(0, 0, self._basemap_pixmap())
            return

        source_w, source_h = self._independent_size(self._basemap_cache)
        destination, source = self._reblit_geometry(
            self._cache_proj, new_projection, source_w, source_h
        )
        qp.save()
        qp.setRenderHint(QPainter.SmoothPixmapTransform, True)
        qp.drawPixmap(
            destination,
            self._basemap_cache,
            self._device_source(self._basemap_cache, source),
        )
        qp.restore()

    @staticmethod
    def _reblit_geometry(old, new, source_w: float, source_h: float):
        """Map a raster drawn under ``old`` into the view ``new``.

        Returns ``(destination, source)`` for a single ``drawPixmap``. Valid only
        while ``old.cone == new.cone``: the two transforms then differ by how the
        shared intermediate plane is fitted to the widget, which is a
        scale-and-translate, and that is what this expresses. A different cone
        bows its parallels differently and genuinely has to be redrawn.

        Shared by the basemap preview and the warped-imagery preview so the two
        cannot drift apart on arithmetic they both depend on.
        """
        old_k, old_scale, old_offx, old_offy, old_x0, old_y1 = (
            old.k,
            old.scale,
            old.offx,
            old.offy,
            old.x0,
            old.y1,
        )
        new_k, new_scale, new_offx, new_offy, new_x0, new_y1 = (
            new.k,
            new.scale,
            new.offx,
            new.offy,
            new.x0,
            new.y1,
        )
        scale_x = new_k * new_scale / (old_k * old_scale)
        dest_x = (
            new_offx
            + ((old_x0 - old_offx / old_scale) * (new_k / old_k) - new_x0) * new_scale
        )
        scale_y = new_scale / old_scale
        dest_y = new_offy + (new_y1 - old_y1) * new_scale - old_offy * scale_y
        source = QRectF(0.0, 0.0, source_w, source_h)
        destination = QRectF(dest_x, dest_y, source_w * scale_x, source_h * scale_y)
        return destination, source

    def _draw_graticule(self, qp, p) -> None:
        span = self._lon1 - self._lon0
        step = 5 if span <= 40 else (10 if span <= 90 else (20 if span <= 200 else 30))
        grid = QPen(QColor(_map().graticule), 1)
        label = QColor(_map().graticule_label)
        # Graticule labels are figures, so the tabular family keeps the degree
        # values aligned. "Helvetica" here resolved to a silent substitution on
        # Windows, and changed again once render.install_font replaced QFont.
        qp.setFont(mono_font("caption"))
        # A straight line between two endpoints is only a meridian or a parallel
        # while the transform is affine. On the conic both bow, so they are
        # sampled and drawn as polylines.
        samples = 1 if p.affine else 24

        def graticule_line(lon_a, lat_a, lon_b, lat_b):
            if samples == 1:
                qp.drawLine(self._to_px(lon_a, lat_a, p), self._to_px(lon_b, lat_b, p))
                return
            poly = QPolygonF()
            for index in range(samples + 1):
                frac = index / samples
                poly.append(
                    self._to_px(
                        lon_a + (lon_b - lon_a) * frac,
                        lat_a + (lat_b - lat_a) * frac,
                        p,
                    )
                )
            qp.drawPolyline(poly)

        lon = int(self._lon0 // step * step)
        while lon <= self._lon1:
            qp.setPen(grid)
            graticule_line(lon, self._lat0, lon, self._lat1)
            qp.setPen(QPen(label))
            top = self._to_px(lon, self._lat1, p)
            qp.drawText(
                QRectF(top.x() - 24, 2, 48, 12), Qt.AlignCenter, self._fmt_lon(lon)
            )
            lon += step
        lat = int(self._lat0 // step * step)
        while lat <= self._lat1:
            qp.setPen(grid)
            graticule_line(self._lon0, lat, self._lon1, lat)
            qp.setPen(QPen(label))
            left = self._to_px(self._lon0, lat, p)
            qp.drawText(
                QRectF(3, left.y() - 7, 34, 12),
                Qt.AlignLeft | Qt.AlignVCenter,
                self._fmt_lat(lat),
            )
            lat += step

    @staticmethod
    def _fmt_lat(lat: int) -> str:
        return f"{abs(lat)}\u00b0{'N' if lat >= 0 else 'S'}"

    @staticmethod
    def _fmt_lon(lon: int) -> str:
        lon = ((lon + 180) % 360) - 180  # normalize to [-180, 180)
        return f"{abs(lon)}\u00b0{'E' if lon >= 0 else 'W'}"

    def _draw_layer(self, qp, prepped, color, width, p) -> None:
        if not prepped:
            return
        qp.setPen(QPen(QColor(color), width))
        # Pad the clip window by one extent-span so partially visible lines draw.
        lon_pad = (self._lon1 - self._lon0) * 0.15
        lat_pad = (self._lat1 - self._lat0) * 0.15
        vlon0, vlon1 = self._lon0 - lon_pad, self._lon1 + lon_pad
        vlat0, vlat1 = self._lat0 - lat_pad, self._lat1 + lat_pad
        for (blo0, blo1, bla0, bla1), pts in prepped:
            if blo1 < vlon0 or blo0 > vlon1 or bla1 < vlat0 or bla0 > vlat1:
                continue  # bbox entirely outside the view
            poly = QPolygonF()
            for lon, lat in pts:
                poly.append(self._to_px(lon, lat, p))
            qp.drawPolyline(poly)

    # -- raster overlay painting --------------------------------------------- #
    def _raster_pixmap(self, key: str, raster: OverlayRaster):
        """Return the decoded pixmap for ``raster``, decoding at most once.

        The no-data pixels in a WMS radar frame are white with zero alpha, so the
        image is converted to a premultiplied format before it is ever scaled.
        Scaling straight ARGB32 interpolates those white pixels into the edge of
        every echo and rings each storm with a pale halo.
        """
        cached = self._raster_pixmaps.get(key)
        if cached is not None and cached[0] is raster.image_bytes:
            return cached[1]

        image = QImage()
        if not image.loadFromData(raster.image_bytes):
            # Remember the failure. Retrying a corrupt payload on every repaint
            # would burn the decode cost dozens of times a second while hovering.
            self._raster_pixmaps[key] = (raster.image_bytes, None)
            return None
        image = image.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            self._raster_pixmaps[key] = (raster.image_bytes, None)
            return None
        self._raster_pixmaps[key] = (raster.image_bytes, pixmap)
        return pixmap

    def _visible_lonlat_bounds(self, p) -> tuple[float, float, float, float]:
        """Return the lon/lat envelope the widget can actually show.

        Not the same thing as the requested extent, and that difference is a bug
        magnet. Both transforms *letterbox*: the extent is fitted into the widget
        at one uniform scale, so whichever axis is not the limiting one leaves
        margin, and the conic additionally fits a **bowed** outline into a
        rectangle, which leaves a crescent of margin along every edge. All of
        that margin is real map -- the basemap draws coastlines into it -- so
        anything that clamps itself to the requested extent instead stops short
        of the window and appears to be cut off along a curve.

        Derived by inverse-projecting a ring of points around the widget border
        rather than by padding a guess, because the margin's width depends on the
        aspect ratio and on how hard the cone bows, and a fixed pad would be too
        small at some extents and wasteful at others. The corners are included:
        on the conic they reach furthest outside the extent.
        """
        width = max(1, self.width())
        height = max(1, self.height())
        inverse = p.inverse
        lons: list[float] = []
        lats: list[float] = []
        steps = 8
        for index in range(steps + 1):
            fx = width * index / steps
            fy = height * index / steps
            for x, y in ((fx, 0.0), (fx, float(height)), (0.0, fy), (float(width), fy)):
                try:
                    lon, lat = inverse(x, y)
                except Exception:  # noqa: BLE001 - a limit point is not fatal
                    continue
                if math.isfinite(lon) and math.isfinite(lat):
                    lons.append(lon)
                    lats.append(lat)
        if not lons:
            # Nothing invertible: fall back to the requested extent, which is at
            # worst the behaviour this replaced.
            return (
                min(self._lon0, self._lon1),
                max(self._lon0, self._lon1),
                min(self._lat0, self._lat1),
                max(self._lat0, self._lat1),
            )
        return (min(lons), max(lons), min(lats), max(lats))

    def _draw_raster_overlays(self, qp, p) -> None:
        """Blit every visible georeferenced image into the current projection.

        Only the visible sub-rectangle of the source is drawn. Zoomed in far
        enough, the full destination rectangle would be orders of magnitude
        larger than the widget, and letting Qt scale the whole frame and then
        clip it wastes that work on pixels nobody sees.

        On the flat view a lon/lat box maps to an axis-aligned pixel box, so a
        plate-carree source needs no resampling beyond the scale Qt is already
        doing, and the whole visible window goes down in one call.

        The curved view has no such rectangle, so it takes
        :meth:`_draw_raster_warped` instead. Imagery used to be withheld there
        entirely -- correct at the time, since blitting it as a rectangle would
        have put radar echoes a hundred kilometres from the storm, and a
        misplaced echo is worse than an absent one. It is drawn now because the
        transform is applied properly rather than ignored.
        """
        if not p.affine:
            self._draw_raster_warped(qp, p)
            return
        view = self._visible_lonlat_bounds(p)
        for key, raster in self._visible_rasters():
            pixmap = self._raster_pixmap(key, raster)
            if pixmap is None:
                continue

            min_lon, max_lon, min_lat, max_lat = raster.bounds
            lon_span = max_lon - min_lon
            lat_span = max_lat - min_lat
            if lon_span <= 0.0 or lat_span <= 0.0:
                continue

            # Visible window, clamped to what the image actually covers. The
            # window is what the widget can *show*, not what was requested, so
            # the image fills the letterbox margin instead of stopping at the
            # extent edge with basemap still drawn beyond it.
            vlon0 = max(min_lon, view[0])
            vlon1 = min(max_lon, view[1])
            vlat0 = max(min_lat, view[2])
            vlat1 = min(max_lat, view[3])
            if vlon1 <= vlon0 or vlat1 <= vlat0:
                continue

            img_w = float(pixmap.width())
            img_h = float(pixmap.height())
            # Source pixels: x grows east, y grows south from the north edge.
            sx0 = (vlon0 - min_lon) / lon_span * img_w
            sx1 = (vlon1 - min_lon) / lon_span * img_w
            sy0 = (max_lat - vlat1) / lat_span * img_h
            sy1 = (max_lat - vlat0) / lat_span * img_h
            source = QRectF(sx0, sy0, sx1 - sx0, sy1 - sy0)
            if source.width() <= 0.0 or source.height() <= 0.0:
                continue

            destination = QRectF(
                self._to_px(vlon0, vlat1, p),
                self._to_px(vlon1, vlat0, p),
            )

            # Smooth only when shrinking the image. Magnifying a reflectivity
            # field bilinearly invents gradients between data cells, which is
            # what makes a zoomed-in radar overlay look blurred rather than
            # coarse; nearest-neighbour keeps the cells the source actually
            # published, the way dedicated radar displays present them. When
            # minifying, smoothing is still wanted -- it stops isolated cells
            # aliasing in and out as the view moves.
            # The destination is logical and the source is in image pixels, so
            # the comparison has to be brought into one unit first. Left in
            # logical terms it understates the destination by exactly the device
            # pixel ratio, concluding "minifying" while genuinely enlarging, and
            # smoothing the cells it meant to preserve.
            ratio = self.devicePixelRatioF()
            magnifying = (
                destination.width() * ratio > source.width()
                or destination.height() * ratio > source.height()
            )
            qp.save()
            qp.setRenderHint(QPainter.SmoothPixmapTransform, not magnifying)
            qp.setOpacity(raster.opacity)
            qp.drawPixmap(destination, pixmap, source)
            qp.restore()

    def _draw_raster_warped(self, qp, p) -> None:
        """Blit imagery through a non-affine transform, one small cell at a time.

        A conic has no rectangle to blit into, but it is smooth: over a small
        enough patch of the map it is indistinguishable from a scale, rotation and
        shear, which is exactly what a ``QTransform`` expresses. So the visible
        window is cut into a grid of cells, each cell's four corners are projected
        properly, and each is drawn through the transform that carries its source
        rectangle onto the resulting quadrilateral. The seams line up because
        adjacent cells are built from the same projected corners.

        Cell count follows the view span rather than being fixed: the error inside
        a cell grows with how much the projection curves across it, so a
        continent-wide view needs a fine grid and a county-wide view needs almost
        none. :data:`_WARP_TARGET_CELL_DEG` sets the target cell size, and the
        bounds keep a single frame's cost bounded either way.

        The result is cached and, during a pan or zoom, re-blitted rather than
        rebuilt -- the same treatment the vector basemap gets, and for the same
        reason. A full-CONUS lattice is a few hundred transformed blits, which
        measured 28 ms a frame with one field attached and 53 ms with two; that is
        a fine cost once per settled view and far too much per mouse-move. The
        cone is pinned for the duration of a gesture, so the cached frame differs
        from the new view by a scale-and-translate and :meth:`_reblit_geometry`
        maps it exactly.
        """
        rasters = self._visible_rasters()
        if not rasters:
            return
        key = self._warp_cache_key(rasters, p)
        cached = self._warp_cache
        if cached is not None and cached[0] == key:
            qp.drawPixmap(0, 0, cached[1])
            return

        if (
            cached is not None
            and self._warp_proj is not None
            and self._basemap_refresh_timer.isActive()
            and self._warp_proj.cone == p.cone
        ):
            source_w, source_h = self._independent_size(cached[1])
            destination, source = self._reblit_geometry(
                self._warp_proj, p, source_w, source_h
            )
            qp.save()
            qp.setRenderHint(QPainter.SmoothPixmapTransform, True)
            qp.drawPixmap(
                destination, cached[1], self._device_source(cached[1], source)
            )
            qp.restore()
            return

        composed = self._compose_warped(rasters, p)
        if composed is None:
            return
        self._warp_cache = (key, composed)
        self._warp_proj = p
        qp.drawPixmap(0, 0, composed)

    def _warp_cache_key(self, rasters, p) -> tuple:
        """Everything the warped composite depends on.

        Payload identity rather than equality, matching the decode cache: a new
        frame is a new ``bytes`` object, and comparing megabytes of image on every
        repaint would cost more than the warp it is meant to avoid.
        """
        return (
            self.width(),
            self.height(),
            round(self.devicePixelRatioF(), 4),
            round(self._lon0, 4),
            round(self._lon1, 4),
            round(self._lat0, 4),
            round(self._lat1, 4),
            p.cone,
            tuple(
                (key, id(raster.image_bytes), round(raster.opacity, 3))
                for key, raster in rasters
            ),
        )

    def _compose_warped(self, rasters, p):
        """Warp every visible image into one widget-sized transparent layer.

        Composited rather than drawn straight to the widget so the result can be
        cached and re-blitted as a unit. Each image contributes at its own
        opacity here, so the composite blits at full strength and looks identical
        to drawing them one at a time.
        """
        if self.width() <= 0 or self.height() <= 0:
            return None
        layer = self._device_pixmap()
        layer.fill(Qt.transparent)
        painter = QPainter(layer)
        try:
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            self._warp_rasters_onto(painter, rasters, p)
        finally:
            painter.end()
        return layer

    def _warp_rasters_onto(self, qp, rasters, p) -> None:
        """Warp each image in ``rasters`` onto ``qp``. See above."""
        view = self._visible_lonlat_bounds(p)
        for key, raster in rasters:
            pixmap = self._raster_pixmap(key, raster)
            if pixmap is None:
                continue

            min_lon, max_lon, min_lat, max_lat = raster.bounds
            lon_span = max_lon - min_lon
            lat_span = max_lat - min_lat
            if lon_span <= 0.0 or lat_span <= 0.0:
                continue

            # What the widget can show, clamped to what the image covers. Using
            # the requested extent here is what made the field stop along a bowed
            # arc with the basemap still drawn past it: the conic fits that bowed
            # outline into a rectangle, and everything between the two is real map.
            vlon0 = max(min_lon, view[0])
            vlon1 = min(max_lon, view[1])
            vlat0 = max(min_lat, view[2])
            vlat1 = min(max_lat, view[3])
            if vlon1 <= vlon0 or vlat1 <= vlat0:
                continue

            cols = max(
                _WARP_MIN_CELLS,
                min(
                    _WARP_MAX_CELLS,
                    int(math.ceil((vlon1 - vlon0) / _WARP_TARGET_CELL_DEG)),
                ),
            )
            rows = max(
                _WARP_MIN_CELLS,
                min(
                    _WARP_MAX_CELLS,
                    int(math.ceil((vlat1 - vlat0) / _WARP_TARGET_CELL_DEG)),
                ),
            )

            img_w = float(pixmap.width())
            img_h = float(pixmap.height())
            forward = p.forward

            # Corner lattice, projected once and shared by the four cells that
            # meet at it: projecting per cell would trace every interior corner
            # four times and let rounding open seams between neighbours.
            lons = [vlon0 + (vlon1 - vlon0) * index / cols for index in range(cols + 1)]
            lats = [vlat1 + (vlat0 - vlat1) * index / rows for index in range(rows + 1)]
            projected = [[forward(lon, lat) for lon in lons] for lat in lats]

            qp.save()
            qp.setOpacity(raster.opacity)
            qp.setRenderHint(QPainter.SmoothPixmapTransform, True)
            for row in range(rows):
                for col in range(cols):
                    self._warp_cell(
                        qp,
                        pixmap,
                        projected,
                        lons,
                        lats,
                        row,
                        col,
                        min_lon,
                        max_lat,
                        lon_span,
                        lat_span,
                        img_w,
                        img_h,
                    )
            qp.restore()

    @staticmethod
    def _warp_cell(
        qp,
        pixmap,
        projected,
        lons,
        lats,
        row,
        col,
        min_lon,
        max_lat,
        lon_span,
        lat_span,
        img_w,
        img_h,
    ) -> None:
        """Draw one cell of a warped raster. See :meth:`_draw_raster_warped`."""
        # Two rectangles, and the distinction matters. ``base`` is the cell's
        # true source extent, and it is what the projected corners correspond to,
        # so it is what the transform must be derived from. ``bleed`` is that
        # grown half a pixel on every side and is what gets drawn, so adjacent
        # cells overlap by a pixel instead of leaving a hairline of background
        # showing through every seam.
        #
        # Deriving the transform from ``bleed`` instead would scale every cell
        # down by the bleed, opening exactly the seams the bleed exists to close.
        sx0 = (lons[col] - min_lon) / lon_span * img_w
        sx1 = (lons[col + 1] - min_lon) / lon_span * img_w
        sy0 = (max_lat - lats[row]) / lat_span * img_h
        sy1 = (max_lat - lats[row + 1]) / lat_span * img_h
        if sx1 <= sx0 or sy1 <= sy0:
            return
        base = QRectF(sx0, sy0, sx1 - sx0, sy1 - sy0)
        bleed = QRectF(sx0 - 0.5, sy0 - 0.5, (sx1 - sx0) + 1.0, (sy1 - sy0) + 1.0)

        top_left = projected[row][col]
        top_right = projected[row][col + 1]
        bottom_right = projected[row + 1][col + 1]
        bottom_left = projected[row + 1][col]

        # Off-screen cells still cost a transform and a clip, so reject them on
        # the projected corners first. A continent-wide conic view puts most of
        # the lattice outside the widget.
        xs = (top_left[0], top_right[0], bottom_right[0], bottom_left[0])
        ys = (top_left[1], top_right[1], bottom_right[1], bottom_left[1])
        if max(xs) < -2.0 or min(xs) > qp.device().width() + 2.0:
            return
        if max(ys) < -2.0 or min(ys) > qp.device().height() + 2.0:
            return

        # An *affine* fit to the four corners, not the exact projective map
        # through them. Two reasons, and the first is the important one: Qt
        # rasterizes a perspective transform on a far slower path than an affine
        # one, and measured over a full-CONUS lattice that difference was 44 ms a
        # frame against 7 ms. The second is that it costs nothing here -- the
        # cells are small enough that the projection is already affine across one
        # to well under a pixel, which is what
        # ``test_the_warp_places_geometry_where_the_projection_says`` holds.
        #
        # The fit averages both pairs of opposite edges and pins the cell centre,
        # so the residual is split across the cell instead of piling up at the one
        # corner a three-point fit would leave out.
        width = base.width()
        height = base.height()
        if width <= 0.0 or height <= 0.0:
            return
        m11 = ((top_right[0] - top_left[0]) + (bottom_right[0] - bottom_left[0])) / (
            2.0 * width
        )
        m12 = ((top_right[1] - top_left[1]) + (bottom_right[1] - bottom_left[1])) / (
            2.0 * width
        )
        m21 = ((bottom_left[0] - top_left[0]) + (bottom_right[0] - top_right[0])) / (
            2.0 * height
        )
        m22 = ((bottom_left[1] - top_left[1]) + (bottom_right[1] - top_right[1])) / (
            2.0 * height
        )
        centre_x = (top_left[0] + top_right[0] + bottom_right[0] + bottom_left[0]) / 4.0
        centre_y = (top_left[1] + top_right[1] + bottom_right[1] + bottom_left[1]) / 4.0
        source_cx = base.left() + width / 2.0
        source_cy = base.top() + height / 2.0
        dx = centre_x - m11 * source_cx - m21 * source_cy
        dy = centre_y - m12 * source_cx - m22 * source_cy
        if not (m11 * m22 - m12 * m21):
            # Degenerate: a cell collapsed to a line at the projection's limit.
            # Skipping it loses one cell, not the frame.
            return

        qp.save()
        qp.setTransform(QTransform(m11, m12, m21, m22, dx, dy), True)
        qp.drawPixmap(bleed, pixmap, bleed)
        qp.restore()

    # -- overlay painting ---------------------------------------------------- #
    def _draw_overlays(self, qp, p) -> None:
        """Fill and outline every visible overlay shape inside the view."""
        layers = self._visible_overlays()
        if not layers:
            return
        # Same padded clip window as the basemap layers, so a shape that only
        # partly intersects the view still draws its visible portion.
        lon_pad = (self._lon1 - self._lon0) * 0.15
        lat_pad = (self._lat1 - self._lat0) * 0.15
        vlon0, vlon1 = self._lon0 - lon_pad, self._lon1 + lon_pad
        vlat0, vlat1 = self._lat0 - lat_pad, self._lat1 + lat_pad

        qp.save()
        for layer in layers:
            for shape in layer.shapes:
                blo0, blo1, bla0, bla1 = shape.bounds
                if blo1 < vlon0 or blo0 > vlon1 or bla1 < vlat0 or bla0 > vlat1:
                    continue
                path = QPainterPath()
                # Odd-even filling makes an interior ring a hole regardless of
                # its winding direction. The source data's ring order is not
                # guaranteed, and with the winding rule a hole wound the same
                # way as its exterior fills solid instead.
                path.setFillRule(Qt.OddEvenFill)
                for ring in shape.rings:
                    poly = QPolygonF()
                    for lon, lat in ring:
                        poly.append(self._to_px(lon, lat, p))
                    path.addPolygon(poly)
                    path.closeSubpath()
                if shape.fill:
                    fill = QColor(shape.fill)
                    if shape.hatch:
                        # A hatch qualifies the band it sits on, so it keeps
                        # more opacity than a wash and lets the colour beneath
                        # show through the gaps rather than replacing it.
                        fill.setAlpha(OVERLAY_HATCH_ALPHA)
                        qp.fillPath(
                            path, hatch_brush(fill, getattr(shape, "hatch_level", 0))
                        )
                    else:
                        # A marker stands in for a point, so it is drawn as a
                        # symbol at full strength. The wash exists to keep the
                        # coastline and station dots readable under a
                        # continent-sized polygon; a five-pixel dot hides
                        # nothing, and washing it turns a cluster of storm
                        # reports into one soft bruise.
                        fill.setAlpha(
                            OVERLAY_MARKER_FILL_ALPHA
                            if getattr(shape, "marker", False)
                            else OVERLAY_FILL_ALPHA
                        )
                        qp.fillPath(path, QBrush(fill))
                if shape.stroke:
                    qp.strokePath(
                        path, QPen(QColor(shape.stroke), OVERLAY_STROKE_WIDTH)
                    )
        qp.restore()

    def _overlay_legend_rows(self) -> list[tuple[str, str, str, int]]:
        """Return ``(label, stroke, fill, hatch_level)`` per distinct category.

        A category arrives as several shapes when its area is a multi-polygon,
        so the legend would otherwise repeat "MRGL" three times.

        The hatch level travels with the row because SPC's intensity groups all
        publish the same grey: without it the CIG1, CIG2, and CIG3 swatches
        would be three identical squares.
        """
        rows: list[tuple[str, str, str, int]] = []
        seen: set[tuple[str, str, str, int]] = set()
        for layer in self._visible_overlays():
            # A layer whose shapes are individual observations rather than a
            # fixed set of categories has no key to offer: storm reports would
            # spread one swatch per report across the bottom of the map. Its
            # title and attribution are still captioned below.
            if not getattr(layer, "legend", True):
                continue
            # Prefix the hazard on the first swatch of a probability product.
            # "5% 15% 30%" alone does not say what is being measured, and
            # repeating the hazard on every swatch would not fit the row.
            prefix = getattr(layer, "short_name", "")
            for shape in layer.shapes:
                if not shape.label:
                    continue
                label = shape.label
                if prefix:
                    label = f"{prefix} {label}"
                    prefix = ""
                row = (
                    label,
                    shape.stroke,
                    shape.fill or "",
                    getattr(shape, "hatch_level", 0) if shape.hatch else 0,
                )
                if row in seen:
                    continue
                seen.add(row)
                rows.append(row)
        return rows

    @staticmethod
    def _field_palette(rasters):
        """Return the colour scale of a visible model field, or ``None``.

        Only the model field has one: a WMS radar frame arrives already coloured
        by the server and carries no scale this code could read. Imported lazily
        so a map never pulls the product catalogue in just to draw a legend
        without one.
        """
        for key, raster in rasters:
            if key != "hrrr_field":
                continue
            try:
                from sharpmod.hrrr_products import get_product

                return get_product(raster.short_name).palette
            except Exception:  # noqa: BLE001 - a legend is not worth a failure
                return None
        return None

    def _colour_bar_width(self) -> float:
        """Return the bar's drawn width for this widget."""
        return min(240.0, max(120.0, self.width() * 0.28))

    def _colour_bar_tick_layout(
        self, metrics, palette, x: float, width: float
    ) -> list[tuple[float, str]]:
        """Return ``(left, label)`` for every tick that fits under the bar.

        Two rules, both about not lying to the reader. A label stays centred on
        the value it names and is nudged only as far as keeping it on screen
        requires -- clamping it into the bar's own span instead would park the
        last tick at the right-hand end, where it would appear to label a value
        the scale does not stop at. And the two ends are placed before the middle
        ticks and never yield to them: they carry the range, so dropping the top
        label to fit an intermediate one would leave no way to tell where the bar
        stops.
        """
        low = palette.minimum
        span = (palette.maximum - low) or 1.0
        ticks = list(palette.tick_values()) or [low, palette.maximum]
        # Only the widget edge constrains a label's position. A few pixels of
        # overhang past the bar is honest; several past the window is clipped.
        left_limit = 2.0
        right_limit = max(left_limit + 1.0, self.width() - 2.0)

        def placement(value):
            label = _format_tick(value)
            label_w = metrics.horizontalAdvance(label) + 6.0
            centre = x + width * (value - low) / span
            left = min(max(centre - label_w / 2.0, left_limit), right_limit - label_w)
            return left, label, label_w

        placed: list[tuple[float, str]] = []
        taken: list[tuple[float, float]] = []
        ends = [ticks[0]] if len(ticks) == 1 else [ticks[0], ticks[-1]]
        for value in ends + ticks[1:-1]:
            left, label, label_w = placement(value)
            right = left + label_w
            if any(left < end and right > begin for begin, end in taken):
                continue
            placed.append((left, label))
            taken.append((left, right))
        return placed

    def _draw_field_colour_bar(self, qp, palette, x: float, y: float) -> None:
        """Draw a labelled colour bar occupying exactly ``COLOUR_BAR_BLOCK_H``.

        Sampled from the palette itself rather than redrawn from its stops, so a
        banded scale shows bands and a continuous one shows a ramp -- the bar and
        the map cannot disagree about what a colour means.
        """
        width = self._colour_bar_width()
        low = palette.minimum
        span = (palette.maximum - low) or 1.0
        bar_y = y + COLOUR_BAR_HEADER_H

        stops = palette.stops
        steps = max(2, int(width))
        qp.save()

        qp.setFont(mono_font("caption"))
        units = palette.units
        header = f"Scale ({units})" if units else "Scale"
        for pen, offset in (
            (QPen(QColor(_map().readout_shadow)), 1.0),
            (QPen(QColor(_map().readout_text)), 0.0),
        ):
            qp.setPen(pen)
            qp.drawText(
                QRectF(x + offset, y + offset, width, COLOUR_BAR_HEADER_H),
                Qt.AlignLeft | Qt.AlignVCenter,
                header,
            )

        qp.setPen(Qt.NoPen)
        for step in range(steps):
            value = low + span * (step + 0.5) / steps
            if palette.stepped:
                colour = stops[0][1]
                for stop_value, stop_colour in stops:
                    if value >= stop_value:
                        colour = stop_colour
                    else:
                        break
            else:
                colour = _interpolate_stops(stops, value)
            qp.setBrush(QColor(colour))
            qp.drawRect(
                QRectF(
                    x + width * step / steps, bar_y, width / steps + 1.0, COLOUR_BAR_H
                )
            )
        qp.setBrush(Qt.NoBrush)
        qp.setPen(QPen(QColor(_map().graticule), 1))
        qp.drawRect(QRectF(x, bar_y, width, COLOUR_BAR_H))

        # Ticks: the values a forecaster reasons about, placed where their colour
        # actually changes. Labelling every stop on a thirty-class scale would
        # overlap into unreadability, so the palette nominates its own.
        tick_y = bar_y + COLOUR_BAR_H
        for left, label in self._colour_bar_tick_layout(
            qp.fontMetrics(), palette, x, width
        ):
            rect = QRectF(
                left,
                tick_y,
                qp.fontMetrics().horizontalAdvance(label) + 6.0,
                COLOUR_BAR_TICK_H,
            )
            qp.setPen(QPen(QColor(_map().readout_shadow)))
            qp.drawText(rect.translated(1, 1), Qt.AlignCenter, label)
            qp.setPen(QPen(QColor(_map().readout_text)))
            qp.drawText(rect, Qt.AlignCenter, label)
        qp.restore()

    def _draw_overlay_legend(self, qp) -> None:
        """Draw the overlay title, validity, and category swatches.

        Bottom-left, because the coordinate readout owns the top-left corner.
        The validity line is the visible half of "time aware": it states the
        window the product covers, and says so plainly when that window does
        not contain the map's selected valid time.
        """
        layers = self._visible_overlays()
        rasters = self._visible_rasters()
        if not layers and not rasters:
            return

        captions: list[str] = []
        credits: list[str] = []
        for layer in layers:
            captions.append(layer.title)
            if layer.subtitle:
                captions.append(layer.subtitle)
            # State the relationship either way rather than only warning on a
            # mismatch. An SPC convective day runs 12Z to 12Z, so a sounding
            # valid 00Z belongs to the previous calendar day's outlook; seeing
            # the two dates differ with nothing to explain it reads as a fault.
            if self._valid_time is not None:
                if layer.covers(self._valid_time):
                    captions.append(
                        f"Selected {self._valid_time:%d %b %H%M}Z is within "
                        "this outlook"
                    )
                else:
                    captions.append(
                        f"\u26a0 Selected {self._valid_time:%d %b %H%M}Z is "
                        "outside this outlook"
                    )
            credit = getattr(layer, "attribution", "")
            if credit and credit not in credits:
                credits.append(credit)

        for _key, raster in rasters:
            # Age belongs on the same line as the title. A live image with no
            # time on it invites the assumption that it is current, which is
            # exactly the assumption that misleads once a fetch starts failing.
            age = format_age(raster.age_seconds())
            title = raster.title
            if age:
                marker = "\u26a0 " if raster.is_stale() else ""
                title = f"{marker}{title} \u00b7 {age}"
            captions.append(title)
            credit = getattr(raster, "attribution", "")
            if credit and credit not in credits:
                credits.append(credit)

        # Attribution is drawn here because nothing else draws it. Every remote
        # overlay records a credit, and until now the map showed none of them.
        if credits:
            captions.append("Source: " + ", ".join(credits))

        rows = self._overlay_legend_rows()
        palette = self._field_palette(rasters)
        if not captions and not rows and palette is None:
            return

        # Reserve exactly what each block draws, gaps included. The blocks are
        # then laid out top-down from that total, so the bottom caption lands on
        # the bottom margin and nothing is drawn over anything else.
        line_h = LEGEND_LINE_H
        block_h = len(captions) * line_h
        if rows:
            block_h += LEGEND_SWATCH_H + LEGEND_BLOCK_GAP
        if palette is not None:
            block_h += COLOUR_BAR_BLOCK_H + LEGEND_BLOCK_GAP
        y = self.height() - 8 - block_h

        if palette is not None:
            # A continuous field cannot be read from a list of swatches, so it
            # gets a bar. Above the categorical swatches because it belongs to
            # the image underneath everything, and the reading order then matches
            # the paint order.
            self._draw_field_colour_bar(qp, palette, LEGEND_MARGIN, y)
            y += COLOUR_BAR_BLOCK_H + LEGEND_BLOCK_GAP

        if rows:
            qp.setFont(mono_font("caption"))
            x = LEGEND_MARGIN
            swatch_y = y + 1.0
            for label, stroke, fill, hatch_level in rows:
                box = QRectF(x, swatch_y, 11.0, 11.0)
                if fill:
                    patch = QColor(fill)
                    patch.setAlpha(OVERLAY_LEGEND_FILL_ALPHA)
                    # Hatched categories show their pattern here too, since it
                    # is the only thing separating one intensity group from the
                    # next on the map.
                    qp.setBrush(
                        hatch_brush(patch, hatch_level)
                        if hatch_level
                        else QBrush(patch)
                    )
                else:
                    qp.setBrush(Qt.NoBrush)
                qp.setPen(QPen(QColor(stroke), 1.4))
                qp.drawRect(box)
                text_w = qp.fontMetrics().horizontalAdvance(label) + 6.0
                qp.setPen(QPen(QColor(_map().readout_shadow)))
                qp.drawText(
                    QRectF(x + 14.0, swatch_y - 1.0, text_w, 13.0).translated(1, 1),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    label,
                )
                qp.setPen(QPen(QColor(_map().readout_text)))
                qp.drawText(
                    QRectF(x + 14.0, swatch_y - 1.0, text_w, 13.0),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    label,
                )
                x += 14.0 + text_w + 6.0
            y += LEGEND_SWATCH_H + LEGEND_BLOCK_GAP

        # Product name and validity window: prose, so the UI family reads best.
        # Left-aligned on the same margin as the bar and the swatches above it,
        # which were inset by two more pixels than this text was.
        qp.setFont(ui_font("caption"))
        for text in captions:
            rect = QRectF(LEGEND_MARGIN, y, self.width() - LEGEND_MARGIN * 2.0, line_h)
            qp.setPen(QPen(QColor(_map().readout_shadow)))
            qp.drawText(rect.translated(1, 1), Qt.AlignLeft | Qt.AlignVCenter, text)
            qp.setPen(QPen(QColor(_map().readout_text)))
            qp.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)
            y += line_h

    # -- painting ------------------------------------------------------------ #
    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        qp = QPainter(self)
        self._draw_basemap(qp)
        qp.setRenderHint(QPainter.Antialiasing, True)
        p = self._proj()
        # Rasters go down first, directly on the basemap: a radar mosaic is
        # imagery to sit behind everything, and drawn later it would bury the
        # vector outlines and the stations.
        self._draw_raster_overlays(qp, p)
        # Overlays sit above the basemap but below the markers, so a risk area
        # never hides the station the user is trying to click.
        self._draw_overlays(qp, p)
        self._draw_stations(qp, p)
        # Above the stations: the antennas are what the user is aiming at while
        # the layer is showing, and they are the smaller target of the two.
        self._draw_radar_sites(qp, p)
        self._draw_readout(qp)
        self._draw_overlay_legend(qp)
        qp.end()

    def _draw_stations(self, qp, p) -> None:
        r = 3.0
        w, h = self.width(), self.height()
        for s in self._stations:
            pt = self._to_px(s["lon"], s["lat"], p)
            if pt.x() < 0 or pt.y() < 0 or pt.x() > w or pt.y() > h:
                continue
            sid = s["id"]
            if sid == self._selected_id:
                qp.setBrush(QBrush(QColor(_map().selected)))
                qp.setPen(QPen(QColor(_map().selected_edge), 1.5))
                qp.drawEllipse(pt, r + 2.5, r + 2.5)
            elif sid == self._hover_id:
                qp.setBrush(QBrush(QColor(_map().station_hover)))
                qp.setPen(QPen(QColor(_map().station_hover_edge), 1))
                qp.drawEllipse(pt, r + 1.5, r + 1.5)
            else:
                qp.setBrush(QBrush(QColor(_map().station)))
                qp.setPen(QPen(QColor(_map().station_edge), 1))
                qp.drawEllipse(pt, r, r)

    #: Longitude span, in degrees, below which every antenna gets a label. At a
    #: continental extent 155 four-letter identifiers overlap into a grey mat, so
    #: the ids appear as the view closes in and the hovered and selected ones are
    #: always named regardless.
    RADAR_LABEL_LON_SPAN = 26.0

    def _draw_radar_sites(self, qp, p) -> None:
        """Draw the radar antennas as pickable diamonds.

        Diamonds rather than dots so they cannot be mistaken for the round
        sounding stations they sit beside on the station map: on that tab both
        layers are drawn at once, and two circle families would read as one
        network.
        """
        if not self._radar_sites:
            return
        w, h = self.width(), self.height()
        label_all = abs(self._lon1 - self._lon0) <= self.RADAR_LABEL_LON_SPAN
        theme = _map()
        qp.save()
        qp.setFont(mono_font("caption"))
        for site_id, lon, lat in self._radar_sites:
            pt = self._to_px(lon, lat, p)
            if pt.x() < -8 or pt.y() < -8 or pt.x() > w + 8 or pt.y() > h + 8:
                continue
            chosen = site_id == self._radar_site_id
            hovered = site_id == self._radar_hover_id
            size = 6.0 if chosen else (5.0 if hovered else 3.6)
            # Cyan, not the stations' red: on the station map both layers draw at
            # once and one hue across two networks would read as one. Yellow when
            # chosen, which is the selection colour the station dots already use,
            # so "this is the selected one" means the same thing on both layers.
            if chosen:
                fill, edge = theme.selected, theme.selected_edge
            elif hovered:
                fill, edge = theme.saved, theme.selected_edge
            else:
                fill, edge = theme.saved, theme.saved_edge
            qp.setBrush(QBrush(QColor(fill)))
            qp.setPen(QPen(QColor(edge), 1.5 if chosen else 1.0))
            qp.drawPolygon(
                QPolygonF(
                    [
                        QPointF(pt.x(), pt.y() - size),
                        QPointF(pt.x() + size, pt.y()),
                        QPointF(pt.x(), pt.y() + size),
                        QPointF(pt.x() - size, pt.y()),
                    ]
                )
            )
            if not (label_all or chosen or hovered):
                continue
            for pen, offset in (
                (QPen(QColor(theme.readout_shadow)), 1.0),
                (QPen(QColor(theme.readout_text)), 0.0),
            ):
                qp.setPen(pen)
                qp.drawText(
                    QPointF(pt.x() + size + 3.0 + offset, pt.y() - size + 1.0 + offset),
                    site_id,
                )
        qp.restore()

    def _draw_readout(self, qp) -> None:
        lines = []
        if self._hover_lonlat is not None:
            lon, lat = self._hover_lonlat
            lines.append(f"{lat:.3f}, {lon:.3f}")
        if self._hover_id is not None:
            st = self._station(self._hover_id)
            if st is not None:
                lines.append(f"{st['id']}  {st['name']}")
        if not lines:
            return
        # Station id plus place name: mixed text, so the UI family reads better.
        qp.setFont(ui_font("body"))
        # Shadowed text for legibility over any basemap color.
        y = 18
        for text in lines:
            rect = QRectF(8, y - 14, self.width() - 16, 18)
            qp.setPen(QPen(QColor(_map().readout_shadow)))
            qp.drawText(rect.translated(1, 1), Qt.AlignLeft | Qt.AlignVCenter, text)
            qp.setPen(QPen(QColor(_map().readout_text)))
            qp.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)
            y += 18

    # -- interaction --------------------------------------------------------- #
    @staticmethod
    def _pos(event) -> QPointF:
        return (
            event.position()
            if hasattr(event, "position")
            else QPointF(event.x(), event.y())
        )

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = self._pos(event)
        if self._drag_last is not None and (event.buttons() & PAN_BUTTONS):
            # Pan by inverse-projecting both cursor positions and shifting the
            # view by the difference between them. A pixel delta divided by the
            # scale only works for an affine transform; this holds for the conic
            # too, and gives the same answer for the flat one.
            p = self._proj()
            dx = pos.x() - self._drag_last.x()
            dy = pos.y() - self._drag_last.y()
            self._dragged = self._dragged or abs(dx) + abs(dy) > 3
            was_lon, was_lat = p.inverse(self._drag_last.x(), self._drag_last.y())
            now_lon, now_lat = p.inverse(pos.x(), pos.y())
            dlon = was_lon - now_lon
            dlat = was_lat - now_lat
            if not (math.isfinite(dlon) and math.isfinite(dlat)):
                self._drag_last = pos
                return
            self._lon0 += dlon
            self._lon1 += dlon
            self._lat0 += dlat
            self._lat1 += dlat
            self._drag_last = pos
            self._queue_map_preview()
            return
        self._hover_lonlat = self._to_lonlat(pos.x(), pos.y())
        near = self._nearest(pos.x(), pos.y())
        self._hover_id = near["id"] if near else None
        # Resolved exactly as a click is, so what lights up under the cursor is
        # what pressing there would select.
        hit = self._radar_site_hit(pos.x(), pos.y())
        rival = self._station_rival(pos, near)
        if hit is not None and (rival is None or rival >= hit[1]):
            self._radar_hover_id = hit[0][0]
            self.setToolTip("%s radar" % hit[0][0])
        else:
            self._radar_hover_id = None
            self.setToolTip(f"{near['id']}  {near['name']}" if near else "")
        self.update()  # cheap: basemap is cached, only overlay repaints

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() & PAN_BUTTONS:
            self._drag_last = self._pos(event)
            self._dragged = False

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() & SECONDARY_PAN_BUTTONS:
            # A middle or right drag only ever pans, so it ends here rather than
            # falling through to a selection.
            self._drag_last = None
            self._dragged = False
            return
        if event.button() != Qt.LeftButton:
            return
        pos = self._pos(event)
        was_drag = self._dragged
        self._drag_last = None
        self._dragged = False
        if was_drag:
            return
        near = self._nearest(pos.x(), pos.y())
        if self._pick_radar_site(pos, self._station_rival(pos, near)):
            return
        if near is not None:
            self._selected_id = near["id"]
            self.stationSelected.emit(near["id"])
            self.update()
            return
        # Nothing else wanted this click, so answer the other question a click
        # on a coloured area is asking: what is this? Placed last on purpose --
        # describing an overlay must never cost a station or radar selection,
        # and the areas are wide enough that there is always bare ground nearby
        # to ask from.
        self._describe_overlay_at(pos, event)

    @staticmethod
    def _global_point(event):
        """Screen position of ``event``, across the two Qt bindings' spellings."""
        if hasattr(event, "globalPosition"):
            return event.globalPosition().toPoint()
        return event.globalPos()  # pragma: no cover - Qt5 naming

    def _describe_overlay_at(self, pos, event) -> bool:
        """Show what the overlay under ``pos`` is, if anything is there."""
        layers = self._visible_overlays()
        if not layers:
            return False
        try:
            lon, lat = self._proj().inverse(pos.x(), pos.y())
        except Exception:  # noqa: BLE001 - a limit point is not worth a crash
            return False
        if not (math.isfinite(lon) and math.isfinite(lat)):
            return False
        text = describe_at(layers, lon, lat)
        if not text:
            return False
        QToolTip.showText(self._global_point(event), text, self)
        return True

    def _station_rival(self, pos, near) -> float | None:
        """Squared distance to the station competing for a click, if any."""
        if near is None:
            return None
        return self._distance2(pos, near["lon"], near["lat"])

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        pos = self._pos(event)
        near = self._nearest(pos.x(), pos.y())
        if self._pick_radar_site(pos, self._station_rival(pos, near)):
            return
        if near is not None:
            self._selected_id = near["id"]
            self.stationSelected.emit(near["id"])
            self.stationActivated.emit(near["id"])
            self.update()

    def wheelEvent(self, event) -> None:  # noqa: N802
        delta = event.angleDelta().y()
        if delta == 0:
            return
        factor = 0.83 if delta > 0 else 1.20  # wheel up = zoom in
        pos = self._pos(event)
        clon, clat = self._to_lonlat(pos.x(), pos.y())
        self._lon0 = clon + (self._lon0 - clon) * factor
        self._lon1 = clon + (self._lon1 - clon) * factor
        self._lat0 = clat + (self._lat0 - clat) * factor
        self._lat1 = clat + (self._lat1 - clat) * factor
        self._queue_map_preview()

    def resizeEvent(self, event) -> None:  # noqa: N802
        self._invalidate()
        super().resizeEvent(event)


#: Smallest drag, in pixels, that counts as drawing a box rather than clicking.
#: Applied in screen space rather than degrees so the threshold means the same
#: thing at every zoom level.
MIN_BOX_DRAG_PX = 6.0


class PointMapWidget(StationMapWidget):
    """Clickable lat/lon picker map for forecast-model point soundings.

    Also draws and returns a rectangular *box* selection, used by the
    box-sounding feature to sample many points at once. A box is drawn either
    with Shift held or after :meth:`set_box_mode` is turned on, so the existing
    click-to-select and drag-to-pan gestures keep working unchanged.
    """

    pointSelected = Signal(float, float)  # lat, lon
    pointActivated = Signal(float, float)  # lat, lon
    #: lat0, lon0, lat1, lon1. Longitudes are deliberately **not** wrapped into
    #: [-180, 180): the raw projected values carry the span the user actually
    #: dragged, which is the only way a box across the antimeridian can be told
    #: apart from its 340-degree complement. ``BoxRegion.from_corners`` expects
    #: exactly this.
    boxSelected = Signal(float, float, float, float)
    boxCleared = Signal()

    def __init__(self, parent=None):
        super().__init__([], parent=parent)
        self._point_lonlat = (-97.44, 35.63)
        self._saved_points: tuple[tuple[str, float, float], ...] = ()
        self._domain_bounds: tuple[float, float, float, float] | None = None
        self._domain_outline: tuple[tuple[float, float], ...] = ()
        self._domain_label = ""
        # Box selection state. ``_box_corners`` is the committed rectangle;
        # ``_box_anchor``/``_box_drag`` are live only while dragging. Pixel
        # copies are kept so the click-versus-drag test stays zoom-independent.
        self._box_mode = False
        self._box_corners: tuple[float, float, float, float] | None = None
        self._box_anchor: tuple[float, float] | None = None
        self._box_drag: tuple[float, float] | None = None
        self._box_anchor_px: QPointF | None = None
        self._box_drag_px: QPointF | None = None
        #: ``(lon, lat)`` lattice preview, so the user can see exactly which
        #: points a box will sample before paying to extract them.
        self._box_nodes: tuple[tuple[float, float], ...] = ()
        self._box_note = ""

    # -- box selection ------------------------------------------------------ #
    def box_mode(self) -> bool:
        """Whether a plain left-drag draws a box instead of panning."""
        return self._box_mode

    def set_box_mode(self, enabled: bool) -> None:
        """Turn sticky box drawing on or off (Shift-drag works regardless)."""
        enabled = bool(enabled)
        if enabled == self._box_mode:
            return
        self._box_mode = enabled
        if not enabled:
            self._cancel_box_drag()
        self.update()

    def box(self) -> tuple[float, float, float, float] | None:
        """Return the committed box as ``(lat0, lon0, lat1, lon1)``."""
        return self._box_corners

    def set_box(self, corners) -> None:
        """Show a committed box, or clear it when given ``None``.

        Does not emit :attr:`boxSelected`: this is how a controller reflects
        state back onto the map without re-triggering its own handler.
        """
        if corners is None:
            self._box_corners = None
            self._box_nodes = ()
            self._box_note = ""
        else:
            lat0, lon0, lat1, lon1 = (float(value) for value in corners)
            self._box_corners = (
                min(lat0, lat1),
                min(lon0, lon1),
                max(lat0, lat1),
                max(lon0, lon1),
            )
        self._cancel_box_drag()
        self.update()

    def clear_box(self) -> None:
        """Clear any box selection and announce it."""
        had_box = self._box_corners is not None
        self.set_box(None)
        if had_box:
            self.boxCleared.emit()

    def set_box_nodes(self, nodes, note: str = "") -> None:
        """Preview the sample lattice a box resolves to.

        ``nodes`` is an iterable of objects with ``lat``/``lon`` attributes (a
        :class:`~sharpmod.box_sounding.BoxSamplePoint` works directly) or
        ``(lat, lon)`` pairs.
        """
        points = []
        for node in nodes or ():
            try:
                if hasattr(node, "lat"):
                    lat = float(node.lat)
                    lon = float(node.lon)
                else:
                    lat, lon = (float(value) for value in node)
            except (AttributeError, TypeError, ValueError):
                continue
            points.append((lon, lat))
        self._box_nodes = tuple(points)
        self._box_note = str(note or "")
        self.update()

    def _cancel_box_drag(self) -> None:
        self._box_anchor = None
        self._box_drag = None
        self._box_anchor_px = None
        self._box_drag_px = None

    def _box_gesture(self, event) -> bool:
        """Whether this press should start a box rather than a pan."""
        return self._box_mode or bool(event.modifiers() & Qt.ShiftModifier)

    def set_saved_points(self, locations) -> None:
        """Show user-named locations as passive map markers."""
        points = []
        for location in locations or ():
            try:
                if isinstance(location, dict):
                    name = location["name"]
                    lat = location["lat"]
                    lon = location["lon"]
                else:
                    name = location.name
                    lat = location.lat
                    lon = location.lon
                lat = float(lat)
                lon = ((float(lon) + 180.0) % 360.0) - 180.0
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            if -90.0 <= lat <= 90.0:
                points.append((str(name), lon, lat))
        self._saved_points = tuple(points)
        self.update()

    def set_point(self, lat: float, lon: float, center: bool = False) -> None:
        lon = ((float(lon) + 180.0) % 360.0) - 180.0
        lat = max(-89.99, min(89.99, float(lat)))
        self._point_lonlat = (lon, lat)
        if center:
            span_lon = (self._lon1 - self._lon0) / 2.0
            span_lat = (self._lat1 - self._lat0) / 2.0
            self._lon0, self._lon1 = lon - span_lon, lon + span_lon
            self._lat0, self._lat1 = lat - span_lat, lat + span_lat
            self._invalidate()
        else:
            self.update()

    def set_domain(self, bounds, label: str = "", outline=None) -> None:
        self._domain_bounds = tuple(bounds) if bounds is not None else None
        self._domain_outline = tuple(outline or ())
        self._domain_label = label or ""
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        qp = QPainter(self)
        self._draw_basemap(qp)
        qp.setRenderHint(QPainter.Antialiasing, True)
        p = self._proj()
        # Imagery first, beneath the domain outline and the picked point. See
        # StationMapWidget.paintEvent -- both orders are hardcoded, so a new
        # layer has to be added to each.
        self._draw_raster_overlays(qp, p)
        self._draw_overlays(qp, p)
        self._draw_domain(qp, p)
        # Above the domain outline: a box is the active selection, and it has to
        # read clearly against the dashed domain perimeter it usually sits
        # inside. Below the markers, which are small enough not to hide it.
        self._draw_box(qp, p)
        self._draw_saved_points(qp, p)
        self._draw_radar_sites(qp, p)
        self._draw_point(qp, p)
        self._draw_readout(qp)
        self._draw_overlay_legend(qp)
        qp.end()

    def _box_rect(self, p, corners) -> QRectF:
        """Return the screen rectangle for ``(lat0, lon0, lat1, lon1)``.

        Only a faithful outline while ``p.affine``; see :meth:`_box_polygon`.
        """
        lat0, lon0, lat1, lon1 = corners
        top_left = self._to_px(lon0, lat1, p)
        bottom_right = self._to_px(lon1, lat0, p)
        return QRectF(top_left, bottom_right).normalized()

    def _box_corner_points(self, p, corners) -> tuple:
        """The four projected geographic corners, in NW, NE, SW, SE order.

        Not the corners of :meth:`_box_rect`: that rectangle derives its other
        two corners from the x of one and the y of the other, which lands on the
        real corner only while the transform is affine.
        """
        lat0, lon0, lat1, lon1 = corners
        return (
            self._to_px(lon0, lat1, p),
            self._to_px(lon1, lat1, p),
            self._to_px(lon0, lat0, p),
            self._to_px(lon1, lat0, p),
        )

    def _box_polygon(self, p, corners) -> QPolygonF:
        """Return the box outline, following the projection along every edge.

        A lat/lon box is a screen rectangle only while the transform is affine.
        On the conic its parallels bow and its meridians tilt, so each edge is
        sampled. Drawing the straight chords instead would leave an outline that
        does not enclose its own lattice points, which are projected one by one.
        """
        lat0, lon0, lat1, lon1 = corners
        return self._lonlat_box_polygon(p, lon0, lon1, lat0, lat1)

    def _lonlat_box_polygon(self, p, lon0, lon1, lat0, lat1) -> QPolygonF:
        """Walk a lon/lat box perimeter, following the projection along each edge.

        Shared by the box selection and the model-domain outline. Both are lon/lat
        boxes, and a lon/lat box is a screen rectangle only while the transform is
        affine: under the conic a parallel projects to an arc, so joining the
        corners cuts the chord across it. The domain outline used to do exactly
        that and was drawn up to 118 px from where the model really ends.

        The step count follows the span rather than being fixed, because the error
        inside a segment grows with how much the projection curves across it: a
        continent-wide domain needs many segments and a county-wide box needs
        almost none. See :data:`_DOMAIN_EDGE_STEP_DEG`.

        On the flat view the extra samples are collinear, so the result is the
        same rectangle as before. One path that is always right beats two that
        have to agree.
        """

        def steps(span):
            if p.affine:
                return 1  # collinear anyway; do not pay for the samples
            return max(
                _DOMAIN_MIN_STEPS,
                min(
                    _DOMAIN_MAX_STEPS, int(math.ceil(abs(span) / _DOMAIN_EDGE_STEP_DEG))
                ),
            )

        across = steps(lon1 - lon0)
        upward = steps(lat1 - lat0)
        poly = QPolygonF()
        # Each edge stops short of its far corner, which is the next edge's
        # opening point, so no vertex is emitted twice and ``drawPolygon`` closes
        # the ring itself.
        for lon_a, lat_a, lon_b, lat_b, count in (
            (lon0, lat1, lon1, lat1, across),  # north, along a parallel
            (lon1, lat1, lon1, lat0, upward),  # east, along a meridian
            (lon1, lat0, lon0, lat0, across),  # south, along a parallel
            (lon0, lat0, lon0, lat1, upward),  # west, along a meridian
        ):
            for index in range(count):
                frac = index / count
                poly.append(
                    self._to_px(
                        lon_a + (lon_b - lon_a) * frac,
                        lat_a + (lat_b - lat_a) * frac,
                        p,
                    )
                )
        return poly

    def _draw_box(self, qp, p) -> None:
        """Draw the committed box, the live rubber band, and the lattice."""
        live = self._box_anchor is not None and self._box_drag is not None
        if live:
            anchor_lon, anchor_lat = self._box_anchor
            drag_lon, drag_lat = self._box_drag
            corners = (
                min(anchor_lat, drag_lat),
                min(anchor_lon, drag_lon),
                max(anchor_lat, drag_lat),
                max(anchor_lon, drag_lon),
            )
        elif self._box_corners is not None:
            corners = self._box_corners
        else:
            return

        # The box uses the `selected` role rather than `domain_edge`: it *is* the
        # current selection, and reusing the domain's blue would make the two
        # rectangles hard to tell apart when a box is drawn inside a domain.
        edge = QColor(_map().selected)
        fill = QColor(edge)
        fill.setAlpha(30)
        qp.setBrush(QBrush(fill))
        # Dashed while dragging, solid once committed: the reader can tell a
        # provisional rectangle from one that has been sampled.
        qp.setPen(QPen(edge, 1.8, Qt.DashLine if live else Qt.SolidLine))
        if p.affine:
            qp.drawRect(self._box_rect(p, corners))
        else:
            qp.drawPolygon(self._box_polygon(p, corners))

        if not live:
            # Corner ticks give the committed rectangle a deliberate,
            # handle-like read without implying it can be dragged.
            qp.setPen(QPen(QColor(_map().selected_edge), 2.4))
            arm = 9.0
            north_west, north_east, south_west, south_east = self._box_corner_points(
                p, corners
            )
            for corner, dx, dy in (
                (north_west, 1.0, 1.0),
                (north_east, -1.0, 1.0),
                (south_west, 1.0, -1.0),
                (south_east, -1.0, -1.0),
            ):
                qp.drawLine(corner, QPointF(corner.x() + dx * arm, corner.y()))
                qp.drawLine(corner, QPointF(corner.x(), corner.y() + dy * arm))

        if self._box_nodes and not live:
            qp.setBrush(QBrush(edge))
            qp.setPen(Qt.NoPen)
            width = self.width()
            height = self.height()
            for lon, lat in self._box_nodes:
                point = self._to_px(lon, lat, p)
                if (
                    point.x() < -6
                    or point.y() < -6
                    or point.x() > width + 6
                    or point.y() > height + 6
                ):
                    continue
                qp.drawEllipse(point, 1.9, 1.9)

    def _box_size_text(self, corners) -> str:
        """Return a ``W x H km`` description of a box."""
        from sharpmod.box_sounding import KM_PER_DEG_LAT, km_per_deg_lon

        lat0, lon0, lat1, lon1 = corners
        center_lat = (lat0 + lat1) / 2.0
        width = abs(lon1 - lon0) * km_per_deg_lon(center_lat)
        height = abs(lat1 - lat0) * KM_PER_DEG_LAT
        return f"{width:.0f} x {height:.0f} km"

    def _draw_domain(self, qp, p) -> None:
        if self._domain_bounds is None:
            return
        lon0, lon1, lat0, lat1 = self._domain_bounds
        if lon0 <= -179.0 and lon1 >= 179.0 and lat0 <= -85.0 and lat1 >= 85.0:
            return
        fill = QColor(80, 140, 220, 34)
        edge = QColor(_map().domain_edge)
        qp.setBrush(QBrush(fill))
        qp.setPen(QPen(edge, 1.4, Qt.DashLine))
        if self._domain_outline:
            # A rotated grid can wrap around a pole. Filling its geographic
            # polygon with a planar Qt winding rule shades the complement near
            # the antimeridian, so render the precise perimeter only.
            qp.setBrush(Qt.NoBrush)
            unwrapped = []
            previous_lon = None
            for lon, lat in self._domain_outline:
                lon = float(lon)
                if previous_lon is not None:
                    lon = previous_lon + ((lon - previous_lon + 180.0) % 360.0) - 180.0
                unwrapped.append((lon, float(lat)))
                previous_lon = lon
            # Draw adjacent longitude copies so an antimeridian-crossing
            # rotated grid remains visible in both world and regional views.
            for shift in (-360.0, 0.0, 360.0):
                poly = QPolygonF(
                    [self._to_px(lon + shift, lat, p) for lon, lat in unwrapped]
                )
                qp.drawPolygon(poly)
            return
        spans = ((lon0, lon1),) if lon0 <= lon1 else ((lon0, 180.0), (-180.0, lon1))
        for start, end in spans:
            qp.drawPolygon(self._lonlat_box_polygon(p, start, end, lat0, lat1))

    def _draw_saved_points(self, qp, p) -> None:
        # User-supplied place labels: prose, not figures.
        qp.setFont(ui_font("caption"))
        for name, lon, lat in self._saved_points:
            pt = self._to_px(lon, lat, p)
            if (
                pt.x() < -20
                or pt.y() < -20
                or pt.x() > self.width() + 20
                or pt.y() > self.height() + 20
            ):
                continue
            qp.setBrush(QBrush(QColor(_map().saved)))
            qp.setPen(QPen(QColor(_map().saved_edge), 1.3))
            qp.drawEllipse(pt, 4.0, 4.0)
            qp.setPen(QPen(QColor(_map().readout_text), 1.0))
            qp.drawText(QPointF(pt.x() + 6, pt.y() - 5), name)

    def _draw_point(self, qp, p) -> None:
        lon, lat = self._point_lonlat
        pt = self._to_px(lon, lat, p)
        if (
            pt.x() < -20
            or pt.y() < -20
            or pt.x() > self.width() + 20
            or pt.y() > self.height() + 20
        ):
            return
        qp.setBrush(QBrush(QColor(_map().selected)))
        qp.setPen(QPen(QColor(_map().selected_edge), 2.0))
        qp.drawEllipse(pt, 7.0, 7.0)
        # Drawn across the marker itself, so it contrasts with `selected`
        # rather than with the basemap.
        qp.setPen(QPen(QColor(_map().selected_crosshair), 1.4))
        qp.drawLine(QPointF(pt.x() - 10, pt.y()), QPointF(pt.x() + 10, pt.y()))
        qp.drawLine(QPointF(pt.x(), pt.y() - 10), QPointF(pt.x(), pt.y() + 10))

    def _draw_readout(self, qp) -> None:
        lines = []
        if self._hover_lonlat is not None:
            lon, lat = self._hover_lonlat
            lines.append(f"Cursor  {lat:.3f}, {lon:.3f}")
        lon, lat = self._point_lonlat
        lines.append(f"Point   {lat:.3f}, {lon:.3f}")
        # While dragging, the size is the number the user is actually steering
        # by, so it replaces the committed box line rather than joining it.
        if self._box_anchor is not None and self._box_drag is not None:
            anchor_lon, anchor_lat = self._box_anchor
            drag_lon, drag_lat = self._box_drag
            lines.append(
                "Box     "
                + self._box_size_text(
                    (
                        min(anchor_lat, drag_lat),
                        min(anchor_lon, drag_lon),
                        max(anchor_lat, drag_lat),
                        max(anchor_lon, drag_lon),
                    )
                )
            )
        elif self._box_corners is not None:
            lines.append("Box     " + self._box_size_text(self._box_corners))
            if self._box_note:
                lines.append("        " + self._box_note)
        elif self._box_mode:
            lines.append("Box     drag to select an area")
        if self._domain_label:
            lines.append(self._domain_label)
        # Coordinate readout: monospace so the digits do not shift as the
        # pointer moves across the map.
        qp.setFont(mono_font("body"))
        y = 18
        for text in lines:
            rect = QRectF(8, y - 14, self.width() - 16, 18)
            qp.setPen(QPen(QColor(_map().readout_shadow)))
            qp.drawText(rect.translated(1, 1), Qt.AlignLeft | Qt.AlignVCenter, text)
            qp.setPen(QPen(QColor(_map().readout_text)))
            qp.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)
            y += 18

    def _select_from_pos(self, pos: QPointF, activate: bool = False) -> None:
        lon, lat = self._to_lonlat(pos.x(), pos.y())
        lon = ((lon + 180.0) % 360.0) - 180.0
        lat = max(-89.99, min(89.99, lat))
        self.set_point(lat, lon)
        self.pointSelected.emit(float(lat), float(lon))
        if activate:
            self.pointActivated.emit(float(lat), float(lon))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self._box_gesture(event):
            pos = self._pos(event)
            # Deliberately unwrapped: the inverse projection can return values
            # beyond +/-180 when the view is panned across the dateline, and that
            # is what preserves the dragged span.
            self._box_anchor = self._to_lonlat(pos.x(), pos.y())
            self._box_drag = self._box_anchor
            self._box_anchor_px = pos
            self._box_drag_px = pos
            # Suppress the inherited pan for this gesture.
            self._drag_last = None
            self._dragged = False
            self.update()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._box_anchor is not None and (event.buttons() & Qt.LeftButton):
            pos = self._pos(event)
            self._box_drag = self._to_lonlat(pos.x(), pos.y())
            self._box_drag_px = pos
            # Keep the cursor readout live without letting the base class pan.
            lon, lat = self._box_drag
            self._hover_lonlat = (((lon + 180.0) % 360.0) - 180.0, lat)
            self.setToolTip(
                self._box_size_text(
                    (
                        min(self._box_anchor[1], lat),
                        min(self._box_anchor[0], lon),
                        max(self._box_anchor[1], lat),
                        max(self._box_anchor[0], lon),
                    )
                )
            )
            self.update()
            return
        super().mouseMoveEvent(event)
        # The base class already set a radar tooltip if one is under the cursor;
        # overwriting it with bare coordinates would hide the only label the
        # antenna markers have at a continental extent.
        if self._radar_hover_id is None and self._hover_lonlat is not None:
            lon, lat = self._hover_lonlat
            self.setToolTip(f"{lat:.3f}, {lon:.3f}")

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() & SECONDARY_PAN_BUTTONS:
            # Let the base class end its pan; a secondary button never draws a
            # box or picks a point.
            super().mouseReleaseEvent(event)
            return
        if event.button() != Qt.LeftButton:
            return
        pos = self._pos(event)
        if self._box_anchor is not None:
            anchor = self._box_anchor
            anchor_px = self._box_anchor_px or pos
            drag = self._box_drag or anchor
            self._cancel_box_drag()
            moved_x = abs(pos.x() - anchor_px.x())
            moved_y = abs(pos.y() - anchor_px.y())
            if moved_x < MIN_BOX_DRAG_PX or moved_y < MIN_BOX_DRAG_PX:
                # Too small to be a rectangle. Treat it as an ordinary pick so a
                # stray Shift-click still does something useful instead of
                # silently doing nothing.
                self.update()
                self._select_from_pos(pos)
                return
            corners = (
                min(anchor[1], drag[1]),
                min(anchor[0], drag[0]),
                max(anchor[1], drag[1]),
                max(anchor[0], drag[0]),
            )
            self._box_corners = corners
            self._box_nodes = ()
            self._box_note = ""
            self.update()
            self.boxSelected.emit(*(float(value) for value in corners))
            return
        was_drag = self._dragged
        self._drag_last = None
        self._dragged = False
        if was_drag:
            return
        # An antenna under the cursor claims the click. Falling through would
        # move the sounding point as well, so one click would both retarget the
        # radar and move the profile location -- two answers to one gesture.
        if self._pick_radar_site(pos):
            return
        self._select_from_pos(pos)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        # A double click while drawing a box would otherwise leave a stale
        # anchor behind and pick a point at the same time.
        if self._box_anchor is not None or self._box_mode:
            return
        pos = self._pos(event)
        if self._pick_radar_site(pos):
            return
        self._select_from_pos(pos, activate=True)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        # Escape abandons an in-progress rectangle, and clears a committed one.
        if event.key() == Qt.Key_Escape:
            if self._box_anchor is not None:
                self._cancel_box_drag()
                self.update()
                return
            if self._box_corners is not None:
                self.clear_box()
                return
        super().keyPressEvent(event)


class BoxFieldMapWidget(PointMapWidget):
    """A picker map that also paints a box analysis as a parameter field.

    Kept separate from :class:`PointMapWidget` so the ordinary picker never
    imports the analysis or colour-scale machinery, while the box workspace
    still inherits the real basemap, projection, pan, and zoom rather than
    growing a second map implementation.
    """

    cellSelected = Signal(int, int)  # row, col
    cellActivated = Signal(int, int)  # row, col (double click)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._analysis = None
        self._field_key = None
        self._field_scale = None
        self._show_values = True
        self._selected_cell: tuple[int, int] | None = None
        # Ingredient screen: the criteria, the resolved tri-state mask, and the
        # coverage result. Held together so the map and the readout beside it
        # cannot describe different things.
        self._screen = None
        self._screen_mask = None
        self._screen_coverage = None

    def set_analysis(self, analysis, key=None) -> None:
        """Show ``analysis`` coloured by ``key``, rescaling to the field."""
        self._analysis = analysis
        if key is not None:
            self._field_key = str(key)
        self._selected_cell = None
        self._rescale()
        self._recompute_screen()
        self.update()

    def set_screen(self, criteria) -> None:
        """Hatch the cells satisfying ``criteria``, or clear when ``None``.

        ``criteria`` is anything :meth:`BoxAnalysis.mask` accepts: a screen name,
        one ``Criterion``, or an iterable of them.
        """
        self._screen = criteria
        self._recompute_screen()
        self.update()

    def screen(self):
        return self._screen

    def coverage(self):
        """Return the current screen's :class:`CoverageResult`, if any."""
        return self._screen_coverage

    def _recompute_screen(self) -> None:
        self._screen_mask = None
        self._screen_coverage = None
        if self._analysis is None or self._screen is None:
            return
        try:
            self._screen_mask = self._analysis.mask(self._screen)
            self._screen_coverage = self._analysis.coverage(self._screen)
        except Exception:
            # An unusable screen clears the overlay rather than breaking the
            # repaint that would otherwise show the field.
            self._screen_mask = None
            self._screen_coverage = None

    def set_field(self, key) -> None:
        """Switch which parameter is coloured without re-analyzing."""
        self._field_key = None if key is None else str(key)
        self._rescale()
        self.update()

    def field(self):
        return self._field_key

    def analysis(self):
        return self._analysis

    def set_show_values(self, enabled: bool) -> None:
        self._show_values = bool(enabled)
        self.update()

    def scale(self):
        return self._field_scale

    def selected_cell(self):
        return self._selected_cell

    def set_selected_cell(self, row, col) -> None:
        """Highlight a cell without announcing it.

        Used when the selection arrives from elsewhere -- a ranked list, say --
        so reflecting it back onto the map cannot re-enter the handler that set
        it.
        """
        if row is None or col is None:
            self._selected_cell = None
        else:
            self._selected_cell = (int(row), int(col))
        self.update()

    def set_view(self, region, *, pad=0.2) -> None:
        """Frame a :class:`~sharpmod.box_sounding.BoxRegion`."""
        self.set_extent(
            region.lon0,
            region.lon0 + region.lon_span,
            region.lat0,
            region.lat1,
            pad=pad,
        )

    def _rescale(self) -> None:
        self._field_scale = None
        if self._analysis is None or not self._field_key:
            return
        from sharpmod.viz.box_field import scale_for

        stats = self._analysis.statistics(self._field_key)
        if stats is not None:
            self._field_scale = scale_for(stats)

    def _cell_at(self, pos: QPointF):
        """Return the ``(row, col)`` under a screen position, if any."""
        if self._analysis is None:
            return None
        lon, lat = self._to_lonlat(pos.x(), pos.y())
        lon = ((lon + 180.0) % 360.0) - 180.0
        best = None
        best_distance = None
        for point in self._analysis.points:
            # Compare in degrees with a wrapped longitude delta so a box across
            # the dateline still resolves to the nearest node.
            dlon = ((point.lon - lon + 180.0) % 360.0) - 180.0
            dlat = point.lat - lat
            distance = dlon * dlon + dlat * dlat
            if best_distance is None or distance < best_distance:
                best = (point.row, point.col)
                best_distance = distance
        return best

    def paintEvent(self, _event) -> None:  # noqa: N802
        qp = QPainter(self)
        self._draw_basemap(qp)
        qp.setRenderHint(QPainter.Antialiasing, True)
        p = self._proj()
        self._draw_raster_overlays(qp, p)
        self._draw_overlays(qp, p)
        self._draw_domain(qp, p)
        # The field goes under the box outline and markers so the rectangle and
        # the selected cell stay legible over it.
        self._draw_field(qp, p)
        # Over the field, under the box outline: the hatch qualifies the cells
        # it covers, so it must not be hidden by them, but it must not hide the
        # rectangle either.
        self._draw_screen(qp, p)
        self._draw_box(qp, p)
        self._draw_selected_cell(qp, p)
        self._draw_saved_points(qp, p)
        self._draw_readout(qp)
        self._draw_field_legend(qp)
        qp.end()

    def _draw_screen(self, qp, p) -> None:
        if self._analysis is None or self._screen_mask is None:
            return
        from sharpmod.viz.box_field import draw_mask_overlay

        draw_mask_overlay(
            qp,
            lambda lon, lat: self._to_px(lon, lat, p),
            self._analysis,
            self._screen_mask,
        )

    def _draw_field(self, qp, p) -> None:
        if self._analysis is None or not self._field_key or self._field_scale is None:
            return
        from sharpmod.viz.box_field import draw_cell_values, draw_field_cells

        def to_px(lon, lat):
            return self._to_px(lon, lat, p)

        draw_field_cells(qp, to_px, self._analysis, self._field_key, self._field_scale)
        if self._show_values:
            draw_cell_values(
                qp, to_px, self._analysis, self._field_key, font=mono_font("caption")
            )

    def _draw_selected_cell(self, qp, p) -> None:
        if self._selected_cell is None or self._analysis is None:
            return
        point = self._analysis.point_at(*self._selected_cell)
        if point is None:
            return
        center = self._to_px(point.lon, point.lat, p)
        qp.setBrush(Qt.NoBrush)
        qp.setPen(QPen(QColor(_map().selected_edge), 2.6))
        qp.drawEllipse(center, 8.0, 8.0)
        qp.setPen(QPen(QColor(_map().selected), 1.6))
        qp.drawEllipse(center, 8.0, 8.0)

    def _draw_field_legend(self, qp) -> None:
        if self._field_scale is None or not self._field_key:
            return
        from sharpmod.viz.box_field import draw_color_bar

        width = min(260.0, max(140.0, self.width() * 0.45))
        rect = QRectF(self.width() - width - 12.0, self.height() - 48.0, width, 40.0)
        draw_color_bar(
            qp,
            rect,
            self._field_scale,
            self._field_key,
            text_color=_map().readout_text,
            shadow_color=_map().readout_shadow,
            font=mono_font("caption"),
        )

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        # Let the box gesture and pan resolve first; only a plain click on an
        # existing field picks a cell.
        drawing = self._box_anchor is not None
        panned = self._dragged
        super().mouseReleaseEvent(event)
        if drawing or panned or event.button() != Qt.LeftButton:
            return
        cell = self._cell_at(self._pos(event))
        if cell is not None:
            self._selected_cell = cell
            self.update()
            self.cellSelected.emit(int(cell[0]), int(cell[1]))

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self._box_anchor is not None or self._box_mode:
            return
        cell = self._cell_at(self._pos(event))
        if cell is None:
            super().mouseDoubleClickEvent(event)
            return
        self._selected_cell = cell
        self.update()
        self.cellSelected.emit(int(cell[0]), int(cell[1]))
        self.cellActivated.emit(int(cell[0]), int(cell[1]))

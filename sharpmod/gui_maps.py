from __future__ import annotations

import logging
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
from sharpmod.map_overlays import OverlayRaster, format_age
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
    Qt, QThread, QTimer, Signal, QDate, QSettings, QPointF, QRectF, QSize, QUrl,
)
from qtpy.QtGui import (
    QAction, QPainter, QColor, QPen, QBrush, QPolygonF, QPainterPath, QFont,
    QPixmap, QImage, QIcon, QTransform, QDesktopServices,
)
from qtpy.QtWidgets import (
    QApplication,
    QMainWindow,
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
            "countries": data.get("countries", []),
            "states": data.get("states", []),
        }
    except Exception:
        pass
    try:
        from importlib.resources import files
        res = files("sharpmod.resources").joinpath("coastlines.json")
        data = json.loads(res.read_text(encoding="utf-8"))
        return {"coastline": data.get("polylines", []),
                "countries": [], "states": []}
    except Exception:
        return {"coastline": [], "countries": [], "states": []}


def _prepare_basemap_layers(basemap: dict) -> MappingProxyType:
    """Freeze basemap polylines and precompute their clipping bounds.

    Map widgets only read this geometry.  Freezing every container makes it
    safe for all picker tabs to share one prepared copy while their view,
    raster, timer, station, and selection state remains widget-local.
    """
    prepared = {}
    for name in ("coastline", "countries", "states"):
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

            lines.append((
                (min_lon, max_lon, min_lat, max_lat),
                tuple(frozen_points),
            ))
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
#: Hatched areas annotate the band beneath them, so the strokes stay legible.
OVERLAY_HATCH_ALPHA = 190
OVERLAY_STROKE_WIDTH = 1.8

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

    stationSelected = Signal(str)   # station id (single click / hover-pick)
    stationActivated = Signal(str)  # station id (double click -> generate)

    def __init__(self, stations, parent=None):
        super().__init__(parent)
        self._stations = list(stations)
        self._layers = _shared_basemap_layers()
        self._area_name = "United States (CONUS)"
        self._lon0, self._lon1, self._lat0, self._lat1 = MAP_AREAS[
            self._area_name]
        self._selected_id: str | None = None
        self._hover_id: str | None = None
        self._hover_lonlat: tuple[float, float] | None = None
        self._drag_last: QPointF | None = None
        self._dragged = False
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
    def set_overlay(self, key: str, layer, *, visible: bool | None = None
                    ) -> None:
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
        return [layer for key, layer in self._overlays.items()
                if self._overlay_visible.get(key, True) and layer]

    def _raster_decode_failed(self, key: str, raster: OverlayRaster) -> bool:
        """Report whether this exact payload has already failed to decode."""
        cached = self._raster_pixmaps.get(key)
        return (cached is not None and cached[1] is None
                and cached[0] is raster.image_bytes)

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
        return [(key, raster) for key, raster in self._rasters.items()
                if self._overlay_visible.get(key, True) and raster
                and raster.intersects(view)
                and not self._raster_decode_failed(key, raster)]

    def _invalidate(self) -> None:
        self._basemap_refresh_timer.stop()
        self._basemap_cache = None
        self._cache_key = None
        self._cache_proj = None
        self.update()

    def _queue_map_preview(self) -> None:
        """Reuse the current raster during rapid wheel input.

        Re-rasterizing the vector basemap takes tens of milliseconds, so doing
        it for every wheel event makes input queue up.  Keep the last crisp
        frame as a transformed preview and rebuild it once input pauses.
        """
        if self._basemap_cache is None or self._cache_proj is None:
            self._invalidate()
            return
        self._basemap_refresh_timer.start()
        self.update()

    def _finish_map_preview(self) -> None:
        """Discard the temporary preview and request one crisp vector frame."""
        self._basemap_cache = None
        self._cache_key = None
        self._cache_proj = None
        self.update()

    # -- projection (aspect-correct, letterboxed) ---------------------------- #
    def _proj(self) -> tuple:
        """Return the projection params ``(k, scale, offx, offy, X0, Y1)``."""
        import math
        w = max(1, self.width())
        h = max(1, self.height())
        lat_ref = math.radians((self._lat0 + self._lat1) / 2.0)
        k = max(0.05, math.cos(lat_ref))
        x0 = self._lon0 * k
        x1 = self._lon1 * k
        box_w = max(1e-6, x1 - x0)
        box_h = max(1e-6, self._lat1 - self._lat0)
        scale = min(w / box_w, h / box_h)
        offx = (w - box_w * scale) / 2.0
        offy = (h - box_h * scale) / 2.0
        return k, scale, offx, offy, x0, self._lat1

    def _to_px(self, lon: float, lat: float, p=None) -> QPointF:
        k, scale, offx, offy, x0, y1 = p or self._proj()
        return QPointF(offx + (lon * k - x0) * scale,
                       offy + (y1 - lat) * scale)

    def _to_lonlat(self, x: float, y: float) -> tuple[float, float]:
        k, scale, offx, offy, x0, y1 = self._proj()
        lon = ((x - offx) / scale + x0) / k
        lat = y1 - (y - offy) / scale
        return lon, lat

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

    # -- basemap raster (cached per extent + size) --------------------------- #
    def _basemap_pixmap(self):
        # The palette is part of the key: the rendered pixmap bakes in the
        # background, graticule, and border colours, so switching theme has to
        # invalidate it or the previous theme's basemap stays on screen. The
        # palette is a frozen dataclass of strings, hence hashable.
        key = (self.width(), self.height(),
               round(self._lon0, 4), round(self._lon1, 4),
               round(self._lat0, 4), round(self._lat1, 4),
               _map())
        if self._basemap_cache is not None and self._cache_key == key:
            return self._basemap_cache

        pm = QPixmap(self.size())
        pm.fill(QColor(_map().background))
        qp = QPainter(pm)
        qp.setRenderHint(QPainter.Antialiasing, True)
        p = self._proj()
        self._draw_graticule(qp, p)
        # Draw borders first (dim), coastline last (bright) so it reads on top.
        self._draw_layer(qp, self._layers.get("states", []),
                         _map().states, 1.0, p)
        self._draw_layer(qp, self._layers.get("countries", []),
                         _map().countries, 1.0, p)
        self._draw_layer(qp, self._layers.get("coastline", []),
                         _map().coastline, 1.4, p)
        qp.end()

        self._basemap_cache = pm
        self._cache_key = key
        self._cache_proj = p
        return pm

    def _draw_basemap(self, qp: QPainter) -> None:
        """Draw either the exact basemap or a fast transformed wheel preview."""
        key = (self.width(), self.height(),
               round(self._lon0, 4), round(self._lon1, 4),
               round(self._lat0, 4), round(self._lat1, 4))
        previewing = (
            self._basemap_cache is not None
            and self._cache_proj is not None
            and self._cache_key != key
            and self._basemap_refresh_timer.isActive()
        )
        if not previewing:
            qp.drawPixmap(0, 0, self._basemap_pixmap())
            return

        old_k, old_scale, old_offx, old_offy, old_x0, old_y1 = \
            self._cache_proj
        new_k, new_scale, new_offx, new_offy, new_x0, new_y1 = \
            self._proj()

        # Map the complete cached raster from its old projection into the new
        # projection.  Both projections are affine, so this is a single fast
        # scale/translate instead of another ~96k-point vector traversal.
        scale_x = new_k * new_scale / (old_k * old_scale)
        dest_x = new_offx + (
            (old_x0 - old_offx / old_scale) * (new_k / old_k) - new_x0
        ) * new_scale
        scale_y = new_scale / old_scale
        dest_y = new_offy + (new_y1 - old_y1) * new_scale \
            - old_offy * scale_y
        source = QRectF(
            0.0, 0.0,
            float(self._basemap_cache.width()),
            float(self._basemap_cache.height()),
        )
        destination = QRectF(
            dest_x,
            dest_y,
            source.width() * scale_x,
            source.height() * scale_y,
        )
        qp.save()
        qp.setRenderHint(QPainter.SmoothPixmapTransform, True)
        qp.drawPixmap(destination, self._basemap_cache, source)
        qp.restore()

    def _draw_graticule(self, qp, p) -> None:
        span = self._lon1 - self._lon0
        step = 5 if span <= 40 else (10 if span <= 90 else
                                     (20 if span <= 200 else 30))
        grid = QPen(QColor(_map().graticule), 1)
        label = QColor(_map().graticule_label)
        # Graticule labels are figures, so the tabular family keeps the degree
        # values aligned. "Helvetica" here resolved to a silent substitution on
        # Windows, and changed again once render.install_font replaced QFont.
        qp.setFont(mono_font("caption"))
        lon = int(self._lon0 // step * step)
        while lon <= self._lon1:
            qp.setPen(grid)
            qp.drawLine(self._to_px(lon, self._lat0, p),
                        self._to_px(lon, self._lat1, p))
            qp.setPen(QPen(label))
            top = self._to_px(lon, self._lat1, p)
            qp.drawText(QRectF(top.x() - 24, 2, 48, 12),
                        Qt.AlignCenter, self._fmt_lon(lon))
            lon += step
        lat = int(self._lat0 // step * step)
        while lat <= self._lat1:
            qp.setPen(grid)
            qp.drawLine(self._to_px(self._lon0, lat, p),
                        self._to_px(self._lon1, lat, p))
            qp.setPen(QPen(label))
            left = self._to_px(self._lon0, lat, p)
            qp.drawText(QRectF(3, left.y() - 7, 34, 12),
                        Qt.AlignLeft | Qt.AlignVCenter, self._fmt_lat(lat))
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

    def _draw_raster_overlays(self, qp, p) -> None:
        """Blit every visible georeferenced image into the current projection.

        Only the visible sub-rectangle of the source is drawn. Zoomed in far
        enough, the full destination rectangle would be orders of magnitude
        larger than the widget, and letting Qt scale the whole frame and then
        clip it wastes that work on pixels nobody sees.

        This projection is equirectangular and axis-aligned, so a lon/lat box
        maps to an axis-aligned pixel box and a plate-carree source needs no
        resampling beyond the scale Qt is already doing.
        """
        for key, raster in self._visible_rasters():
            pixmap = self._raster_pixmap(key, raster)
            if pixmap is None:
                continue

            min_lon, max_lon, min_lat, max_lat = raster.bounds
            lon_span = max_lon - min_lon
            lat_span = max_lat - min_lat
            if lon_span <= 0.0 or lat_span <= 0.0:
                continue

            # Visible window, clamped to what the image actually covers.
            vlon0 = max(min_lon, min(self._lon0, self._lon1))
            vlon1 = min(max_lon, max(self._lon0, self._lon1))
            vlat0 = max(min_lat, min(self._lat0, self._lat1))
            vlat1 = min(max_lat, max(self._lat0, self._lat1))
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
            magnifying = (destination.width() > source.width()
                          or destination.height() > source.height())
            qp.save()
            qp.setRenderHint(QPainter.SmoothPixmapTransform, not magnifying)
            qp.setOpacity(raster.opacity)
            qp.drawPixmap(destination, pixmap, source)
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
                if blo1 < vlon0 or blo0 > vlon1 \
                        or bla1 < vlat0 or bla0 > vlat1:
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
                        qp.fillPath(path, hatch_brush(
                            fill, getattr(shape, "hatch_level", 0)))
                    else:
                        fill.setAlpha(OVERLAY_FILL_ALPHA)
                        qp.fillPath(path, QBrush(fill))
                if shape.stroke:
                    qp.strokePath(path, QPen(
                        QColor(shape.stroke), OVERLAY_STROKE_WIDTH))
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
                row = (label, shape.stroke, shape.fill or "",
                       getattr(shape, "hatch_level", 0) if shape.hatch else 0)
                if row in seen:
                    continue
                seen.add(row)
                rows.append(row)
        return rows

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
                        "this outlook")
                else:
                    captions.append(
                        f"\u26a0 Selected {self._valid_time:%d %b %H%M}Z is "
                        "outside this outlook")
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
        if not captions and not rows:
            return

        line_h = 15
        swatch_h = 13 if rows else 0
        block_h = len(captions) * line_h + swatch_h
        y = self.height() - 8 - block_h

        if rows:
            qp.setFont(mono_font("caption"))
            x = 10.0
            swatch_y = y + 1.0
            for label, stroke, fill, hatch_level in rows:
                box = QRectF(x, swatch_y, 11.0, 11.0)
                if fill:
                    patch = QColor(fill)
                    patch.setAlpha(OVERLAY_LEGEND_FILL_ALPHA)
                    # Hatched categories show their pattern here too, since it
                    # is the only thing separating one intensity group from the
                    # next on the map.
                    qp.setBrush(hatch_brush(patch, hatch_level)
                                if hatch_level else QBrush(patch))
                else:
                    qp.setBrush(Qt.NoBrush)
                qp.setPen(QPen(QColor(stroke), 1.4))
                qp.drawRect(box)
                text_w = qp.fontMetrics().horizontalAdvance(label) + 6.0
                qp.setPen(QPen(QColor(_map().readout_shadow)))
                qp.drawText(QRectF(x + 14.0, swatch_y - 1.0, text_w, 13.0)
                            .translated(1, 1),
                            Qt.AlignLeft | Qt.AlignVCenter, label)
                qp.setPen(QPen(QColor(_map().readout_text)))
                qp.drawText(QRectF(x + 14.0, swatch_y - 1.0, text_w, 13.0),
                            Qt.AlignLeft | Qt.AlignVCenter, label)
                x += 14.0 + text_w + 6.0
            y += swatch_h

        # Product name and validity window: prose, so the UI family reads best.
        qp.setFont(ui_font("caption"))
        for text in captions:
            rect = QRectF(8, y, self.width() - 16, line_h)
            qp.setPen(QPen(QColor(_map().readout_shadow)))
            qp.drawText(rect.translated(1, 1),
                        Qt.AlignLeft | Qt.AlignVCenter, text)
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
            qp.drawText(rect.translated(1, 1),
                        Qt.AlignLeft | Qt.AlignVCenter, text)
            qp.setPen(QPen(QColor(_map().readout_text)))
            qp.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)
            y += 18

    # -- interaction --------------------------------------------------------- #
    @staticmethod
    def _pos(event) -> QPointF:
        return event.position() if hasattr(event, "position") \
            else QPointF(event.x(), event.y())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        pos = self._pos(event)
        if self._drag_last is not None and (event.buttons() & Qt.LeftButton):
            # Pan: convert the pixel delta to a lon/lat shift via the transform.
            k, scale, _ox, _oy, _x0, _y1 = self._proj()
            dx = pos.x() - self._drag_last.x()
            dy = pos.y() - self._drag_last.y()
            self._dragged = self._dragged or abs(dx) + abs(dy) > 3
            dlon = -dx / scale / k
            dlat = dy / scale
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
        self.setToolTip(f"{near['id']}  {near['name']}" if near else "")
        self.update()  # cheap: basemap is cached, only overlay repaints

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_last = self._pos(event)
            self._dragged = False

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        pos = self._pos(event)
        was_drag = self._dragged
        self._drag_last = None
        self._dragged = False
        if was_drag:
            return
        near = self._nearest(pos.x(), pos.y())
        if near is not None:
            self._selected_id = near["id"]
            self.stationSelected.emit(near["id"])
            self.update()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        pos = self._pos(event)
        near = self._nearest(pos.x(), pos.y())
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


class PointMapWidget(StationMapWidget):
    """Clickable lat/lon picker map for forecast-model point soundings."""

    pointSelected = Signal(float, float)   # lat, lon
    pointActivated = Signal(float, float)  # lat, lon

    def __init__(self, parent=None):
        super().__init__([], parent=parent)
        self._point_lonlat = (-97.44, 35.63)
        self._saved_points: tuple[tuple[str, float, float], ...] = ()
        self._domain_bounds: tuple[float, float, float, float] | None = None
        self._domain_outline: tuple[tuple[float, float], ...] = ()
        self._domain_label = ""

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
        self._draw_saved_points(qp, p)
        self._draw_point(qp, p)
        self._draw_readout(qp)
        self._draw_overlay_legend(qp)
        qp.end()

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
                    lon = previous_lon + (
                        (lon - previous_lon + 180.0) % 360.0
                    ) - 180.0
                unwrapped.append((lon, float(lat)))
                previous_lon = lon
            # Draw adjacent longitude copies so an antimeridian-crossing
            # rotated grid remains visible in both world and regional views.
            for shift in (-360.0, 0.0, 360.0):
                poly = QPolygonF([
                    self._to_px(lon + shift, lat, p)
                    for lon, lat in unwrapped
                ])
                qp.drawPolygon(poly)
            return
        spans = ((lon0, lon1),) if lon0 <= lon1 else (
            (lon0, 180.0), (-180.0, lon1)
        )
        for start, end in spans:
            poly = QPolygonF([
                self._to_px(start, lat0, p),
                self._to_px(end, lat0, p),
                self._to_px(end, lat1, p),
                self._to_px(start, lat1, p),
                self._to_px(start, lat0, p),
            ])
            qp.drawPolygon(poly)

    def _draw_saved_points(self, qp, p) -> None:
        # User-supplied place labels: prose, not figures.
        qp.setFont(ui_font("caption"))
        for name, lon, lat in self._saved_points:
            pt = self._to_px(lon, lat, p)
            if pt.x() < -20 or pt.y() < -20 \
                    or pt.x() > self.width() + 20 \
                    or pt.y() > self.height() + 20:
                continue
            qp.setBrush(QBrush(QColor(_map().saved)))
            qp.setPen(QPen(QColor(_map().saved_edge), 1.3))
            qp.drawEllipse(pt, 4.0, 4.0)
            qp.setPen(QPen(QColor(_map().readout_text), 1.0))
            qp.drawText(QPointF(pt.x() + 6, pt.y() - 5), name)

    def _draw_point(self, qp, p) -> None:
        lon, lat = self._point_lonlat
        pt = self._to_px(lon, lat, p)
        if pt.x() < -20 or pt.y() < -20 \
                or pt.x() > self.width() + 20 or pt.y() > self.height() + 20:
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
        if self._domain_label:
            lines.append(self._domain_label)
        # Coordinate readout: monospace so the digits do not shift as the
        # pointer moves across the map.
        qp.setFont(mono_font("body"))
        y = 18
        for text in lines:
            rect = QRectF(8, y - 14, self.width() - 16, 18)
            qp.setPen(QPen(QColor(_map().readout_shadow)))
            qp.drawText(rect.translated(1, 1),
                        Qt.AlignLeft | Qt.AlignVCenter, text)
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

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        super().mouseMoveEvent(event)
        if self._hover_lonlat is not None:
            lon, lat = self._hover_lonlat
            self.setToolTip(f"{lat:.3f}, {lon:.3f}")

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton:
            return
        pos = self._pos(event)
        was_drag = self._dragged
        self._drag_last = None
        self._dragged = False
        if was_drag:
            return
        self._select_from_pos(pos)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self._select_from_pos(self._pos(event), activate=True)

from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import numpy as np

"""Interactive station and point-selection map widgets."""

# Importing common first applies the native Qt platform policy.
from sharpmod import gui_common as _gui_common

from qtpy.QtCore import (
    Qt, QThread, QTimer, Signal, QDate, QSettings, QPointF, QRectF, QSize, QUrl,
)
from qtpy.QtGui import (
    QAction, QPainter, QColor, QPen, QBrush, QPolygonF, QFont, QPixmap, QIcon,
    QTransform, QDesktopServices,
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


#: Named map extents for the "Map Area" selector: (lon0, lon1, lat0, lat1).
MAP_AREAS: dict[str, tuple[float, float, float, float]] = {
    "United States (CONUS)": (-125.0, -66.0, 23.0, 50.0),
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
        self._layers = self._prep_layers(_load_basemap())
        self._area_name = "United States (CONUS)"
        self._lon0, self._lon1, self._lat0, self._lat1 = MAP_AREAS[
            self._area_name]
        self._selected_id: str | None = None
        self._hover_id: str | None = None
        self._hover_lonlat: tuple[float, float] | None = None
        self._drag_last: QPointF | None = None
        self._dragged = False
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
    def _prep_layers(basemap: dict) -> dict:
        """Precompute each polyline's lon/lat bounding box for fast clipping."""
        out = {}
        for name, lines in basemap.items():
            prepped = []
            for pts in lines:
                if len(pts) < 2:
                    continue
                lons = [p[0] for p in pts]
                lats = [p[1] for p in pts]
                bbox = (min(lons), max(lons), min(lats), max(lats))
                prepped.append((bbox, pts))
            out[name] = prepped
        return out

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
        key = (self.width(), self.height(),
               round(self._lon0, 4), round(self._lon1, 4),
               round(self._lat0, 4), round(self._lat1, 4))
        if self._basemap_cache is not None and self._cache_key == key:
            return self._basemap_cache

        pm = QPixmap(self.size())
        pm.fill(QColor("#05070d"))
        qp = QPainter(pm)
        qp.setRenderHint(QPainter.Antialiasing, True)
        p = self._proj()
        self._draw_graticule(qp, p)
        # Draw borders first (dim), coastline last (bright) so it reads on top.
        self._draw_layer(qp, self._layers.get("states", []), "#2c3e55", 1.0, p)
        self._draw_layer(qp, self._layers.get("countries", []), "#54697f", 1.0, p)
        self._draw_layer(qp, self._layers.get("coastline", []), "#a9c0dc", 1.4, p)
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
        grid = QPen(QColor("#141d2e"), 1)
        label = QColor("#3a4a63")
        qp.setFont(QFont("Helvetica", 8))
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

    # -- painting ------------------------------------------------------------ #
    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        qp = QPainter(self)
        self._draw_basemap(qp)
        qp.setRenderHint(QPainter.Antialiasing, True)
        self._draw_stations(qp, self._proj())
        self._draw_readout(qp)
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
                qp.setBrush(QBrush(QColor("#ffd000")))
                qp.setPen(QPen(QColor("#ffffff"), 1.5))
                qp.drawEllipse(pt, r + 2.5, r + 2.5)
            elif sid == self._hover_id:
                qp.setBrush(QBrush(QColor("#ff8a8a")))
                qp.setPen(QPen(QColor("#ffffff"), 1))
                qp.drawEllipse(pt, r + 1.5, r + 1.5)
            else:
                qp.setBrush(QBrush(QColor("#e03030")))
                qp.setPen(QPen(QColor("#7a1414"), 1))
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
        qp.setFont(QFont("Helvetica", 10))
        # Shadowed text for legibility over any basemap color.
        y = 18
        for text in lines:
            rect = QRectF(8, y - 14, self.width() - 16, 18)
            qp.setPen(QPen(QColor("#000000")))
            qp.drawText(rect.translated(1, 1),
                        Qt.AlignLeft | Qt.AlignVCenter, text)
            qp.setPen(QPen(QColor("#eef2f8")))
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
        self._domain_bounds: tuple[float, float, float, float] | None = None
        self._domain_label = ""

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

    def set_domain(self, bounds, label: str = "") -> None:
        self._domain_bounds = tuple(bounds) if bounds is not None else None
        self._domain_label = label or ""
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        qp = QPainter(self)
        self._draw_basemap(qp)
        qp.setRenderHint(QPainter.Antialiasing, True)
        p = self._proj()
        self._draw_domain(qp, p)
        self._draw_point(qp, p)
        self._draw_readout(qp)
        qp.end()

    def _draw_domain(self, qp, p) -> None:
        if self._domain_bounds is None:
            return
        lon0, lon1, lat0, lat1 = self._domain_bounds
        if lon0 <= -179.0 and lon1 >= 179.0 and lat0 <= -85.0 and lat1 >= 85.0:
            return
        poly = QPolygonF([
            self._to_px(lon0, lat0, p),
            self._to_px(lon1, lat0, p),
            self._to_px(lon1, lat1, p),
            self._to_px(lon0, lat1, p),
            self._to_px(lon0, lat0, p),
        ])
        fill = QColor(80, 140, 220, 34)
        edge = QColor("#79b8ff")
        qp.setBrush(QBrush(fill))
        qp.setPen(QPen(edge, 1.4, Qt.DashLine))
        qp.drawPolygon(poly)

    def _draw_point(self, qp, p) -> None:
        lon, lat = self._point_lonlat
        pt = self._to_px(lon, lat, p)
        if pt.x() < -20 or pt.y() < -20 \
                or pt.x() > self.width() + 20 or pt.y() > self.height() + 20:
            return
        qp.setBrush(QBrush(QColor("#ffd000")))
        qp.setPen(QPen(QColor("#ffffff"), 2.0))
        qp.drawEllipse(pt, 7.0, 7.0)
        qp.setPen(QPen(QColor("#0a0d13"), 1.4))
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
        qp.setFont(QFont("Helvetica", 10))
        y = 18
        for text in lines:
            rect = QRectF(8, y - 14, self.width() - 16, 18)
            qp.setPen(QPen(QColor("#000000")))
            qp.drawText(rect.translated(1, 1),
                        Qt.AlignLeft | Qt.AlignVCenter, text)
            qp.setPen(QPen(QColor("#eef2f8")))
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

"""MapChromeDrawing behavior for station maps."""

from __future__ import annotations

from qtpy.QtCore import QPointF
from qtpy.QtCore import QRectF
from qtpy.QtCore import Qt
from qtpy.QtGui import QBrush
from qtpy.QtGui import QColor
from qtpy.QtGui import QPainter
from qtpy.QtGui import QPen
from qtpy.QtGui import QPolygonF
from sharpmod.ui.features.gui_theme import mono_font
from sharpmod.ui.features.gui_theme import ui_font
from sharpmod.ui.maps.presentation import _map
import math


class MapChromeDrawingMixin:
    """StationMapWidget methods grouped by responsibility."""

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt override)
        self._clear_empty_legend_layout()
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
        # T16.1: essential geography stays legible above filled weather layers.
        self._draw_boundary_outlines(qp, p)
        self._draw_place_labels(qp, p)
        self._draw_stations(qp, p)
        self._draw_station_labels(qp, p)
        self._draw_loaded_profile_markers(qp, p)
        # Above the stations: the antennas are what the user is aiming at while
        # the layer is showing, and they are the smaller target of the two.
        self._draw_radar_sites(qp, p)
        self._draw_scale_bar(qp, p)
        self._draw_readout(qp)
        # T20: the legend draws after (and avoids) the readout and inspection
        # card, so the key and the card stop overprinting each other.
        # T21: the time header draws last, above the legend's own time lines,
        # so requested-vs-actual survives even a collapsed legend.
        self._draw_overlay_legend(qp)
        self._draw_time_header(qp)
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
        if self._point_locked:
            lines.append("Selection locked")
        if not lines:
            self._readout_rect = None
            self._draw_inspection_card(qp)
            return
        # Station id plus place name: mixed text, so the UI family reads better.
        qp.setFont(ui_font("body"))
        metrics = qp.fontMetrics()
        line_height = max(18.0, math.ceil(metrics.height() + 2.0))
        readout_width = min(
            max(0.0, float(self.width() - 16)),
            max(float(metrics.horizontalAdvance(text)) for text in lines) + 3.0,
        )
        self._readout_rect = (
            8.0, 8.0, readout_width, len(lines) * line_height)
        # Shadowed text for legibility over any basemap color.
        y = 8.0
        for text in lines:
            rect = QRectF(8, y, self.width() - 16, line_height)
            qp.setPen(QPen(QColor(_map().readout_shadow)))
            qp.drawText(rect.translated(1, 1), Qt.AlignLeft | Qt.AlignVCenter, text)
            qp.setPen(QPen(QColor(_map().readout_text)))
            qp.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)
            y += line_height
        self._draw_inspection_card(qp)

    def _draw_time_header(self, qp) -> None:
        """Draw the persistent time header, top-centre (T21.1).

        Requested vs actual, run and lead when pinned, live observation time
        where one exists, retrieval age secondary and labelled, and the
        live/history mode beside it -- one line every screenshot carries, even
        with the rail cropped out. Drawn last so it survives the legend, the
        readout, and the inspection card; centred so it clears both corners'
        chrome. Nothing when the map depicts nothing visible: a hidden
        overlay's clock must not linger on the bare frame (the paint tests
        hold hiding to restoring the bare pixels exactly).
        """
        if getattr(self, "_legend_metadata_in_rail", False):
            self._time_header_rect = None
            return
        if not self._visible_overlays() and not self._visible_rasters():
            self._time_header_rect = None
            return
        try:
            header = self.time_header_text()
        except (AttributeError, RuntimeError):
            header = ""
        if not header:
            self._time_header_rect = None
            return
        try:
            from sharpmod.maps.map_time import mode_label

            second = mode_label(self.map_mode())
        except (AttributeError, RuntimeError):
            second = ""
        qp.save()
        qp.setFont(ui_font("caption"))
        metrics = qp.fontMetrics()
        lines = [header] + ([second] if second else [])
        line_height = max(17.0, math.ceil(metrics.height() + 2.0))
        width = max(metrics.horizontalAdvance(text) for text in lines) + 20.0
        width = min(max(width, 180.0), max(180.0, self.width() - 24.0))
        height = len(lines) * line_height + 10.0
        readout = self.readout_rect()
        left_reserved = 8.0
        if readout is not None:
            left_reserved = readout.x() + readout.width() + 12.0
        right_reserved = 8.0
        inspection = self.inspection_card_rect()
        if inspection is not None:
            right_reserved = inspection.width() + 20.0
        available = self.width() - left_reserved - right_reserved
        y = 8.0
        if available >= 180.0:
            width = min(width, available)
            x = left_reserved + max(0.0, (available - width) / 2.0)
        else:
            x = max(8.0, (self.width() - width) / 2.0)
            proposed = QRectF(x, y, width, height)
            occupied = [rect for rect in (readout, inspection) if rect is not None]
            if any(proposed.intersects(rect) for rect in occupied):
                y = max(rect.bottom() for rect in occupied) + 8.0
        rect = QRectF(x, y, width, height)
        qp.setBrush(QBrush(QColor(_map().readout_shadow)))
        qp.setPen(Qt.NoPen)
        qp.drawRoundedRect(rect.translated(1.5, 1.5), 6.0, 6.0)
        background = QColor(_map().background)
        background.setAlpha(232)
        qp.setBrush(QBrush(background))
        qp.setPen(QPen(QColor(_map().graticule), 1.0))
        qp.drawRoundedRect(rect, 6.0, 6.0)
        y = rect.top() + 5.0
        for text in lines:
            row = QRectF(rect.left() + 10.0, y, rect.width() - 20.0,
                         line_height)
            shown = text
            if metrics.horizontalAdvance(shown) > rect.width() - 20.0:
                shown = metrics.elidedText(shown, Qt.ElideRight,
                                           int(rect.width() - 20.0))
            qp.setPen(QPen(QColor(_map().readout_text)))
            qp.drawText(row, Qt.AlignLeft | Qt.AlignVCenter, shown)
            y += line_height
        qp.restore()
        self._time_header_rect = (rect.x(), rect.y(), rect.width(),
                                  rect.height())

    def time_header_rect(self):
        """Return the last painted time-header rect, or ``None`` (T21.1)."""
        box = getattr(self, "_time_header_rect", None)
        try:
            x, y, w, h = (float(value) for value in box)
        except (TypeError, ValueError, OverflowError):
            return None
        return (x, y, w, h)

    def readout_rect(self):
        """Return the last painted coordinate/readout bounds, if any."""
        box = getattr(self, "_readout_rect", None)
        try:
            x, y, w, h = (float(value) for value in box)
        except (TypeError, ValueError, OverflowError):
            return None
        return QRectF(x, y, w, h)

    def inspection_card_rect(self):
        """Return the pinned-card ``QRectF``, or ``None`` when absent (T20.1).

        The legend avoids this rectangle when choosing a corner, so the key
        and the card stop overprinting each other.
        """
        lines = self.inspection_card_lines()
        if not lines:
            return None
        try:
            from qtpy.QtGui import QFontMetrics
        except ImportError:  # pragma: no cover - Qt is present here
            return None
        metrics = QFontMetrics(ui_font("caption"))
        line_height = max(17.0, math.ceil(metrics.height() + 2.0))
        width = max(metrics.horizontalAdvance(text) for text in lines) + 20.0
        width = min(max(width, 150.0), max(150.0, self.width() - 24.0))
        height = len(lines) * line_height + 14.0
        x = max(8.0, self.width() - width - 12.0)
        return QRectF(x, 8.0, width, height)

    def _draw_inspection_card(self, qp) -> None:
        """Draw the pinned inspection card, top-right (T18.2)."""
        lines = self.inspection_card_lines()
        rect = self.inspection_card_rect()
        if not lines or rect is None:
            return
        qp.save()
        qp.setFont(ui_font("caption"))
        line_height = max(17.0, math.ceil(qp.fontMetrics().height() + 2.0))
        qp.setBrush(QBrush(QColor(_map().readout_shadow)))
        qp.setPen(Qt.NoPen)
        qp.drawRoundedRect(rect.translated(1.5, 1.5), 6.0, 6.0)
        background = QColor(_map().background)
        background.setAlpha(232)
        qp.setBrush(QBrush(background))
        qp.setPen(QPen(QColor(_map().graticule), 1.0))
        qp.drawRoundedRect(rect, 6.0, 6.0)
        y = rect.top() + 7.0
        for text in lines:
            row = QRectF(rect.left() + 10.0, y, rect.width() - 20.0,
                         line_height)
            qp.setPen(QPen(QColor(_map().readout_text)))
            qp.drawText(row, Qt.AlignLeft | Qt.AlignVCenter, text)
            y += line_height
        qp.restore()

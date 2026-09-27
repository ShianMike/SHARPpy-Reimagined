"""Surface station plots and vector overlay drawing."""

from __future__ import annotations

import math

from qtpy import QtCore, QtGui
from qtpy.QtCore import Qt, QPointF, QRectF
from qtpy.QtGui import QBrush, QColor, QPainterPath, QPen, QPolygonF

from sharpmod.ui.features.gui_theme import current_theme, mono_font
from sharpmod.ui.maps.hatching import hatch_brush
from sharpmod.ui.maps.presentation import _map
from sharpmod.rendering.map_point_symbols import draw_map_point_symbol

#: Alpha applied to an overlay's published fill colour. SPC's palette is built
#: for opaque printing on white, so it is drawn translucent here to keep the
#: coastline, borders, and station dots readable underneath it.
OVERLAY_FILL_ALPHA = 68

#: A point marker is opaque. It covers a handful of pixels, so it has nothing to
#: hide, and a translucent dot reads as a smudge rather than a mark.
OVERLAY_MARKER_FILL_ALPHA = 255
#: Hatched areas annotate the band beneath them, so the strokes stay legible.
OVERLAY_HATCH_ALPHA = 190
OVERLAY_STROKE_WIDTH = 1.8

# Report symbols always use screen pixels. Value labels appear only at a local
# zoom, with a small cap so dense outbreaks do not cover the map.
REPORT_CAPTION_MAX_SPAN_DEG = 6.0
REPORT_CAPTION_MAX_COUNT = 8


class MapOverlayDrawingMixin:
    """Draw station observations and vector weather layers."""

    def _draw_surface_station_plot(self, qp, shape, p) -> None:
        """Draw one fixed-size station model: barb, temperature, dewpoint, id.

        The containing :class:`OverlayShape` supplies a geographic dot and hit
        target.  This adornment deliberately lives in screen pixels: zooming a
        map should separate station models, not enlarge their typography or
        turn a 25 kt staff into a county-wide line.
        """

        station_id = str(getattr(shape, "station_id", "") or "")
        lon = getattr(shape, "station_longitude", None)
        lat = getattr(shape, "station_latitude", None)
        if not station_id or lon is None or lat is None:
            return
        try:
            centre = self._to_px(float(lon), float(lat), p)
        except (TypeError, ValueError):
            return
        theme = current_theme()
        availability = str(
            getattr(shape, "availability_state", "available") or "available"
        )
        ink = QColor(theme.warning if getattr(shape, "stale", False) else theme.text_primary)
        qp.save()
        qp.setPen(QPen(ink, 1.35))
        qp.setBrush(QBrush(ink))
        qp.setFont(mono_font("small"))

        if availability in {"unavailable", "unknown"}:
            # Queried-no-match and query-failed are both distinct from a calm
            # observation and from an unqueried station (which has no status
            # symbol at all).  Shape, not hue, carries the distinction.
            status_ink = QColor(
                theme.warning if availability == "unknown" else theme.text_secondary
            )
            qp.setBrush(Qt.NoBrush)
            qp.setPen(
                QPen(
                    status_ink,
                    1.7,
                    Qt.DashLine if availability == "unknown" else Qt.SolidLine,
                )
            )
            qp.drawEllipse(centre, 6.0, 6.0)
            if availability == "unavailable":
                qp.drawLine(
                    QPointF(centre.x() - 4.5, centre.y() - 4.5),
                    QPointF(centre.x() + 4.5, centre.y() + 4.5),
                )
                qp.drawLine(
                    QPointF(centre.x() - 4.5, centre.y() + 4.5),
                    QPointF(centre.x() + 4.5, centre.y() - 4.5),
                )
            else:
                qp.drawText(QPointF(centre.x() - 2.5, centre.y() + 3.5), "?")
            qp.drawText(QPointF(centre.x() + 8.0, centre.y() + 4.0), station_id)
            qp.restore()
            return

        if getattr(shape, "stale", False):
            qp.setBrush(Qt.NoBrush)
            qp.setPen(QPen(QColor(theme.warning), 1.3, Qt.DashLine))
            qp.drawEllipse(centre, 27.0, 27.0)
            qp.setPen(QPen(ink, 1.35))
            qp.setBrush(QBrush(ink))

        u = getattr(shape, "wind_u_kt", None)
        v = getattr(shape, "wind_v_kt", None)
        wind_state = str(getattr(shape, "wind_state", "") or "")
        try:
            speed = math.hypot(float(u), float(v))
        except (TypeError, ValueError):
            speed = None
        if not wind_state:
            wind_state = (
                "missing"
                if speed is None
                else ("calm" if speed < 0.5 else "available")
            )
        if wind_state == "available" and speed is not None and speed >= 0.5:
            # Meteorological barbs point toward the direction the wind comes
            # from. Stored u/v components point toward motion, hence -u/-v;
            # screen y grows south, turning the north component into +v here.
            dx = -float(u) / speed * 24.0
            dy = float(v) / speed * 24.0
            end = QPointF(centre.x() + dx, centre.y() + dy)
            qp.drawLine(centre, end)
            along_x, along_y = dx / 24.0, dy / 24.0
            side_x, side_y = -along_y, along_x
            remaining = int(round(speed / 5.0) * 5)
            offset = 0.0
            while remaining >= 50:
                base = QPointF(end.x() - along_x * offset, end.y() - along_y * offset)
                next_base = QPointF(
                    base.x() - along_x * 5.0, base.y() - along_y * 5.0
                )
                flag = QPainterPath(base)
                flag.lineTo(base.x() + side_x * 10.0, base.y() + side_y * 10.0)
                flag.lineTo(next_base)
                flag.closeSubpath()
                qp.fillPath(flag, QBrush(ink))
                remaining -= 50
                offset += 6.0
            while remaining >= 10:
                base = QPointF(end.x() - along_x * offset, end.y() - along_y * offset)
                qp.drawLine(
                    base,
                    QPointF(base.x() + side_x * 9.0, base.y() + side_y * 9.0),
                )
                remaining -= 10
                offset += 4.5
            if remaining >= 5:
                base = QPointF(end.x() - along_x * offset, end.y() - along_y * offset)
                qp.drawLine(
                    base,
                    QPointF(base.x() + side_x * 5.0, base.y() + side_y * 5.0),
                )
        elif wind_state == "calm":
            qp.setBrush(Qt.NoBrush)
            qp.drawEllipse(centre, 4.0, 4.0)
        elif wind_state == "direction-missing":
            qp.setBrush(Qt.NoBrush)
            qp.drawRect(QRectF(centre.x() - 4.0, centre.y() - 4.0, 8.0, 8.0))
            qp.drawText(QPointF(centre.x() + 5.0, centre.y() - 5.0), "?")
        else:
            # A missing wind is an X, never the open calm-wind circle.
            qp.setBrush(Qt.NoBrush)
            qp.drawLine(
                QPointF(centre.x() - 3.5, centre.y() - 3.5),
                QPointF(centre.x() + 3.5, centre.y() + 3.5),
            )
            qp.drawLine(
                QPointF(centre.x() - 3.5, centre.y() + 3.5),
                QPointF(centre.x() + 3.5, centre.y() - 3.5),
            )

        # The station model: temperature above dewpoint, left of the staff, with
        # the identifier lower right. That is the arrangement a surface chart has
        # always used, and it is what makes the colours a reinforcement rather
        # than the only thing telling the two readings apart -- position alone
        # still distinguishes them for a reader who cannot separate the hues.
        #
        # Only the dewpoint was drawn before, so the plot showed half of the pair
        # that matters: dewpoint alone gives moisture but not the spread, and the
        # temperature was already in hand.
        palette = _map()
        stale = bool(getattr(shape, "stale", False))
        # Both readings sit inside the 24px the wind staff already claims around
        # the centre, so the station model's footprint is unchanged and the
        # declutter spacings in ``_DENSITIES`` stay valid. A row above that -- a
        # gust, say -- would push the model past its own spacing and collide with
        # its neighbour at the dense setting, which is why the gust stays in the
        # hover description instead.
        readings = (
            (getattr(shape, "temperature_c", None), palette.obs_temperature, -4.0),
            (getattr(shape, "dewpoint_c", None), palette.obs_dewpoint, 15.0),
        )
        for value, colour, dy in readings:
            text = "\u2014"
            try:
                if value is not None and math.isfinite(float(value)):
                    text = f"{float(value):.0f}"
            except (TypeError, ValueError):
                pass
            # A stale plot keeps one warning colour throughout. Colouring its
            # readings semantically would dress a reading the overlay is telling
            # you not to trust as though it were current.
            qp.setPen(QPen(ink if stale else QColor(colour), 1.35))
            qp.drawText(QPointF(centre.x() - 23.0, centre.y() + dy), text)

        # Dimmer than the readings: it names the plot, it is not a measurement.
        qp.setPen(QPen(ink if stale else QColor(theme.text_secondary), 1.35))
        qp.drawText(QPointF(centre.x() + 7.0, centre.y() + 15.0), station_id)
        qp.restore()

    def _draw_overlays(self, qp, p) -> None:
        """Fill and outline every visible overlay shape inside the view.

        Painted bottom-to-top in :data:`VECTOR_DRAW_ORDER`, so the order is a
        decision rather than dict-insertion luck: a remove/set cycle elsewhere
        must not silently reshuffle which boundary reads on top.
        """
        layers = self._ordered_vector_layers()
        self._report_caption_hit_cache = (p, frozenset(
            id(layer) for layer in layers), ())
        if not layers:
            return
        # Same padded clip window as the basemap layers, so a shape that only
        # partly intersects the view still draws its visible portion.
        lon_pad = (self._lon1 - self._lon0) * 0.15
        lat_pad = (self._lat1 - self._lat0) * 0.15
        vlon0, vlon1 = self._lon0 - lon_pad, self._lon1 + lon_pad
        vlat0, vlat1 = self._lat0 - lat_pad, self._lat1 + lat_pad

        qp.save()
        point_symbols = []
        report_captions = []
        show_captions = self._lon1 - self._lon0 <= REPORT_CAPTION_MAX_SPAN_DEG
        for layer in layers:
            for shape in layer.shapes:
                radius = getattr(shape, "marker_radius_px", 0.0) or 0.0
                lon = getattr(shape, "marker_lon", None)
                lat = getattr(shape, "marker_lat", None)
                if radius > 0.0 and lon is not None and lat is not None:
                    if not (vlon0 <= lon <= vlon1 and vlat0 <= lat <= vlat1):
                        continue
                    centre = self._to_px(lon, lat, p)
                    if (-radius <= centre.x() <= self.width() + radius
                            and -radius <= centre.y() <= self.height() + radius):
                        point_symbols.append((centre, radius, shape))
                        caption = str(getattr(shape, "marker_caption", "") or "")
                        if show_captions and caption:
                            report_captions.append((shape.rank, centre, radius,
                                                    caption, shape.fill, layer,
                                                    shape))
                    continue
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
                self._draw_surface_station_plot(qp, shape, p)
        # Paint these after the translucent outlook wash, keeping their hazard
        # colours crisp even when the reports layer sits below the outlook.
        # A higher-priority report stays visible at a shared location.
        for centre, radius, shape in sorted(
            point_symbols, key=lambda entry: entry[2].rank
        ):
            draw_map_point_symbol(
                qp, centre.x(), centre.y(), radius,
                getattr(shape, "marker_symbol", "circle"),
                shape.fill, shape.stroke, QtCore, QtGui,
            )
        if report_captions:
            self._report_caption_hit_cache = (
                p, frozenset(id(layer) for layer in layers),
                self._draw_report_captions(qp, report_captions),
            )
        qp.restore()

    def _draw_report_captions(self, qp, captions) -> tuple:
        """Show a few close-zoom report values without blanketing the map."""
        qp.setFont(mono_font("caption"))
        metrics = qp.fontMetrics()
        height = float(metrics.height() + 2)
        occupied = []
        hit_boxes = []
        marker_bounds = [
            (id(centre), QRectF(
                centre.x() - radius - 1, centre.y() - radius - 1,
                radius * 2 + 2, radius * 2 + 2,
            ))
            for _, centre, radius, _, _, _, _ in captions
        ]
        limit = max(2, min(REPORT_CAPTION_MAX_COUNT,
                           self.width() * self.height() // 55000))
        for index, item in enumerate(sorted(captions, key=lambda entry: -entry[0])):
            if index >= limit * 12:
                break
            if len(occupied) >= limit:
                break
            _rank, centre, radius, caption, _colour, layer, shape = item
            width = float(metrics.horizontalAdvance(caption) + 6)
            y = centre.y() - height / 2.0
            if y < 2.0 or y + height > self.height() - 2.0:
                continue
            for x in (centre.x() + radius + 5.0,
                      centre.x() - radius - width - 5.0):
                rect = QRectF(x, y, width, height)
                padded = rect.adjusted(-3, -3, 3, 3)
                if (rect.left() < 2.0 or rect.right() > self.width() - 2.0
                        or any(padded.intersects(other) for other in occupied)
                        or any(padded.intersects(bounds)
                               for source_id, bounds in marker_bounds
                               if source_id != id(centre))):
                    continue
                background = QColor("#101820")
                background.setAlpha(190)
                qp.setBrush(QBrush(background))
                qp.setPen(Qt.NoPen)
                qp.drawRoundedRect(rect, 2.0, 2.0)
                qp.setPen(QPen(QColor("#F5F7FA")))
                qp.drawText(rect, Qt.AlignCenter, caption)
                occupied.append(rect)
                hit_boxes.append((rect, layer, shape))
                break
        return tuple(hit_boxes)

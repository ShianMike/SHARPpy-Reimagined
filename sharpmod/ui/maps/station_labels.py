"""Station label placement and drawing."""

from __future__ import annotations

import math

from qtpy.QtCore import Qt, QPointF, QRectF
from qtpy.QtGui import QColor, QPen

from sharpmod.ui.features.gui_theme import mono_font
from sharpmod.maps.map_geography import declutter_labels
from sharpmod.ui.maps.presentation import (
    _estimate_label_widths, _estimated_label_height, _map,
)

#: Progressive sounding-station label thresholds (T22.1).  Markers remain
#: selectable at every extent; only their text density changes.  Wide views
#: reserve text for selected/hovered/pinned stations and regional views add
#: compact IDs. Local views add names only where the shared rectangle-aware
#: declutter finds room beside towns.
STATION_LABEL_ID_LON_SPAN = 55.0
STATION_LABEL_NAME_LON_SPAN = 16.0


class StationLabelsMixin:
    """Keep station labels legible across map scales."""

    def _pinned_station_id(self) -> str | None:
        snapshot = getattr(self, "_inspection", None)
        if not isinstance(snapshot, dict) or snapshot.get("kind") != "station":
            return None
        station_id = str(snapshot.get("station_id") or "").strip()
        return station_id or None

    def _station_label_layout_key(self) -> tuple:
        return (
            self._projection,
            round(self._lon0, 4),
            round(self._lon1, 4),
            round(self._lat0, 4),
            round(self._lat1, 4),
            self.width(),
            self.height(),
            self._conic_freeze,
            self._selected_id,
            self._hover_id,
            self._pinned_station_id(),
            self._stations_revision,
        )

    def _position_protected_station_labels(self, records, reserved_rects):
        """Place selected/hovered/pinned labels without stacking their text.

        The marker remains at its geographic point. When the normal east/above
        slot is occupied, the label walks around that marker through nearby
        east/west and above/below slots; the painter adds a short leader for a
        displaced label. This keeps all three protected identities available
        without exempting their text rectangles from collision handling.
        """
        widths = _estimate_label_widths(record[3] for record in records)
        line_height = _estimated_label_height(15.0)
        occupied = [tuple(float(value) for value in rect[:4])
                    for rect in reserved_rects]
        positioned = []

        def intersects(rect, other, pad=3.0):
            x, y, width, height = rect
            ox, oy, ow, oh = other
            return (
                x < ox + ow + pad
                and x + width + pad > ox
                and y < oy + oh + pad
                and y + height + pad > oy
            )

        for priority, marker_x, marker_y, label, station_id in sorted(records):
            extent = max(1.0, float(widths.get(label, 0.0)))
            rect_width = extent + 6.0
            step = line_height + 5.0
            slots = []
            for level in range(5):
                distance = level * step
                top_above = marker_y - line_height + 1.0 - distance
                top_below = marker_y + 6.0 + distance
                slots.extend((
                    (marker_x + 6.0, top_above),
                    (marker_x + 6.0, top_below),
                    (marker_x - rect_width - 6.0, top_above),
                    (marker_x - rect_width - 6.0, top_below),
                ))
            candidates = []
            seen = set()
            for index, (left, top) in enumerate(slots):
                left = max(2.0, min(left, self.width() - rect_width - 2.0))
                top = max(2.0, min(top, self.height() - line_height - 2.0))
                rounded = (round(left, 3), round(top, 3))
                if rounded in seen:
                    continue
                seen.add(rounded)
                rect = (left, top, rect_width, line_height)
                hits = sum(intersects(rect, other) for other in occupied)
                distance = math.hypot(
                    (left - 6.0) - marker_x,
                    (top + line_height - 1.0) - marker_y,
                )
                candidates.append((hits, distance, index, rect))
            _hits, _distance, _index, rect = min(candidates)
            left, top, _width, _height = rect
            anchor_x = left - 6.0
            anchor_y = top + line_height - 1.0
            candidate = (0, anchor_x, anchor_y, label)
            metadata = (
                station_id, True, float(marker_x), float(marker_y))
            positioned.append((candidate, metadata))
            occupied.append(rect)
        return positioned

    def station_labels(self) -> tuple:
        """Return progressive, decluttered station labels (T22.1).

        Every sounding marker remains pickable. Text progresses from protected
        items only to compact IDs. Selected/hovered/pinned stations keep their
        full names; repeating every long station name beside the town layer was
        the overlap visible in regional zooms.
        """

        key = self._station_label_layout_key()
        cached = self._station_label_layout_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        p = self._proj()
        span = abs(float(self._lon1) - float(self._lon0))
        protected_order = []
        for item in (
                self._selected_id, self._hover_id, self._pinned_station_id()):
            if item and item not in protected_order:
                protected_order.append(item)
        protected_ids = set(protected_order)
        protected_rank = {
            station_id: rank
            for rank, station_id in enumerate(protected_order)
        }
        show_regular = span <= STATION_LABEL_ID_LON_SPAN
        show_names = span <= STATION_LABEL_NAME_LON_SPAN
        candidates = []
        protected_records = []
        metadata = {}
        for station in self._stations:
            try:
                station_id = str(station["id"])
                protected = station_id in protected_ids
                if not protected and not show_regular:
                    continue
                point = self._to_px(float(station["lon"]), float(station["lat"]), p)
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if not (-8.0 <= point.x() <= self.width() + 8.0):
                continue
            if not (-8.0 <= point.y() <= self.height() + 8.0):
                continue
            name = str(station.get("name") or "").strip()
            label = station_id
            if (show_names or protected) and name:
                label = f"{station_id} {name}"
            if protected:
                protected_records.append((
                    protected_rank[station_id], point.x(), point.y(), label,
                    station_id))
                continue
            item = (2, point.x(), point.y(), label)
            candidates.append(item)
            metadata[(point.x(), point.y(), label)] = (
                station_id, False, point.x(), point.y())

        if span > STATION_LABEL_ID_LON_SPAN:
            limit, separation = 6, 28.0
        elif span > 25.0:
            limit, separation = 22, 40.0
        elif span > 10.0:
            limit, separation = 40, 32.0
        else:
            limit, separation = 64, 25.0
        try:
            from sharpmod.ui.features.gui_theme import current_font_text_scale

            text_scale = max(1.0, int(current_font_text_scale()) / 100.0)
        except (ImportError, TypeError, ValueError, OverflowError):
            text_scale = 1.0
        separation *= text_scale
        limit = max(len(protected_ids), int(limit / max(1.0, text_scale * 0.8)))
        place_labels = self.cached_place_labels()
        for item, item_metadata in self._position_protected_station_labels(
                protected_records, self._label_rectangles(place_labels)):
            candidates.append(item)
            metadata[(item[1], item[2], item[3])] = item_metadata
        place_points = tuple((x, y) for x, y, _label in place_labels)
        kept = declutter_labels(
            candidates,
            protected=place_points + tuple(self._label_protected_points(p)),
            protected_rects=self._label_rectangles(place_labels),
            min_separation_px=separation,
            limit=limit,
            label_widths=_estimate_label_widths(
                tuple(str(item[3]) for item in candidates)
            ),
            always_keep_importance=0,
            viewport_size=(self.width(), self.height()),
        )
        labels = tuple(
            (
                x,
                y,
                label,
                metadata.get(
                    (x, y, label),
                    ("", importance == 0, x, y),
                )[0],
                metadata.get(
                    (x, y, label),
                    ("", importance == 0, x, y),
                )[1],
                metadata.get(
                    (x, y, label),
                    ("", importance == 0, x, y),
                )[2],
                metadata.get(
                    (x, y, label),
                    ("", importance == 0, x, y),
                )[3],
            )
            for importance, x, y, label in kept
        )
        self._station_label_layout_cache = (key, labels)
        return labels

    def _draw_station_labels(self, qp, _p) -> None:
        labels = self.station_labels()
        if not labels:
            return
        palette = _map()
        qp.save()
        qp.setFont(mono_font("caption"))
        line_height = max(
            15.0, math.ceil(qp.fontMetrics().height() + 1.0))
        for x, y, label, _station_id, protected, marker_x, marker_y in labels:
            text_w = qp.fontMetrics().horizontalAdvance(label)
            draw_x = min(x + 6.0, max(2.0, self.width() - text_w - 4.0))
            rect = QRectF(
                draw_x, y - line_height + 1.0, text_w + 6.0, line_height)
            if protected and math.hypot(x - marker_x, y - marker_y) > 2.0:
                target_x = max(rect.left(), min(marker_x, rect.right()))
                target_y = max(rect.top(), min(marker_y, rect.bottom()))
                leader = QColor(palette.selected)
                leader.setAlpha(180)
                qp.setPen(QPen(leader, 1.0))
                qp.drawLine(
                    QPointF(marker_x, marker_y), QPointF(target_x, target_y))
            qp.setPen(QPen(QColor(palette.readout_shadow), 2.0))
            qp.drawText(rect.translated(1.0, 1.0), Qt.AlignLeft | Qt.AlignVCenter, label)
            colour = palette.selected if protected else palette.readout_text
            qp.setPen(QPen(QColor(colour), 1.0))
            qp.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, label)
        qp.restore()

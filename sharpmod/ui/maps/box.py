"""Point-map box selection, editing, and drawing."""

from __future__ import annotations

import math

from qtpy.QtCore import Qt, QPointF, QRectF
from qtpy.QtGui import QBrush, QColor, QPainterPath, QPen, QPolygonF

from sharpmod.ui.maps.presentation import _map

#: Geographic-box editing affordances (T23.1).  The painted handle is large
#: enough to read, while the hit target is deliberately larger so a user does
#: not have to land on a four-pixel square on a high-DPI display.
BOX_HANDLE_RADIUS_PX = 4.5
BOX_HANDLE_HIT_RADIUS_PX = 11.0
BOX_EDIT_DRAG_PX = 3.0


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


class MapBoxMixin:
    """Box region gestures and display for point maps."""

    def box_mode(self) -> bool:
        """Whether a plain left-drag draws a box instead of panning."""
        return self._box_mode

    def set_box_mode(self, enabled: bool) -> None:
        """Turn sticky box drawing on or off (Shift-drag works regardless)."""
        enabled = bool(enabled)
        if enabled == self._box_mode:
            if enabled and getattr(self, "_map_tool", "select") != "box":
                self._map_tool = "box"
                self._refresh_tool_chrome()
            return
        self._box_mode = enabled
        if not enabled:
            self._cancel_box_drag()
            self._cancel_box_edit(restore=True)
        # Sticky box mode and the explicit box tool are one state (T18.1):
        # they can only disagree transiently while the picker catches up.
        if enabled:
            self._map_tool = "box"
        elif getattr(self, "_map_tool", "select") == "box":
            self._map_tool = "select"
        self._refresh_tool_chrome()
        self.update()
        self.boxModeChanged.emit(enabled)

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
            self._box_node_coverage = ()
            self._box_note = ""
        else:
            lat0, lon0, lat1, lon1 = (float(value) for value in corners)
            self._box_corners = (
                min(lat0, lat1),
                min(lon0, lon1),
                max(lat0, lat1),
                max(lon0, lon1),
            )
            self._box_nodes = ()
            self._box_node_coverage = ()
            self._box_note = ""
        self._cancel_box_drag()
        self._cancel_box_edit()
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
        :class:`~sharpmod.analysis.box_sounding.BoxSamplePoint` works directly) or
        ``(lat, lon)`` pairs.
        """
        points = []
        coverage = []
        for node in nodes or ():
            try:
                if hasattr(node, "lat"):
                    lat = float(node.lat)
                    lon = float(node.lon)
                    in_domain = bool(getattr(node, "in_domain", True))
                else:
                    lat, lon = (float(value) for value in node)
                    in_domain = True
            except (AttributeError, TypeError, ValueError):
                continue
            points.append((lon, lat))
            coverage.append(in_domain)
        self._box_nodes = tuple(points)
        self._box_node_coverage = tuple(coverage)
        self._box_note = str(note or "")
        self.update()

    def _cancel_box_drag(self) -> None:
        self._box_anchor = None
        self._box_drag = None
        self._box_anchor_px = None
        self._box_drag_px = None

    def _cancel_box_edit(self, *, restore: bool = False) -> None:
        """End a handle/move gesture, optionally restoring its first bounds."""
        if restore and self._box_edit_origin is not None:
            self._box_corners = self._box_edit_origin
        self._box_edit_kind = None
        self._box_edit_origin = None
        self._box_edit_anchor = None
        self._box_edit_anchor_px = None
        self._box_edit_moved = False

    def _box_longitude_copies(self, corners) -> tuple:
        """Return longitude copies of *corners* that meet the visible view.

        A short box crossing the antimeridian is stored as, for example,
        ``170..190``.  A world view has a seam at both -180 and +180, so it
        needs both ``170..190`` and ``-190..-170``.  Keeping this choice in one
        helper makes paint, hit testing, and resize handles agree (T23.2).
        """
        lat0, lon0, lat1, lon1 = (float(value) for value in corners)
        view0, view1 = sorted((float(self._lon0), float(self._lon1)))
        copies = []
        first = int(math.ceil((view0 - lon1) / 360.0))
        last = int(math.floor((view1 - lon0) / 360.0))
        for multiple in range(first, last + 1):
            shift = 360.0 * multiple
            west, east = lon0 + shift, lon1 + shift
            copies.append((lat0, west, lat1, east))
        if not copies:
            copies.append((lat0, lon0, lat1, lon1))
        return tuple(copies)

    def _box_hit_target(self, pos: QPointF):
        """Return ``(kind, displayed_corners)`` under *pos*, if any.

        Hit testing uses the same projection-following polygon that is painted,
        so a curved edge never leaves an invisible rectangular grab region.
        """
        if self._box_corners is None:
            return None
        p = self._proj()
        names = ("nw", "ne", "sw", "se")
        for corners in self._box_longitude_copies(self._box_corners):
            for name, point in zip(names, self._box_corner_points(p, corners)):
                if math.hypot(point.x() - pos.x(), point.y() - pos.y()) \
                        <= BOX_HANDLE_HIT_RADIUS_PX:
                    return name, corners
        for corners in self._box_longitude_copies(self._box_corners):
            polygon = self._box_polygon(p, corners)
            path = QPainterPath()
            path.addPolygon(polygon)
            path.closeSubpath()
            if path.contains(pos):
                return "move", corners
        return None

    def _begin_box_edit(self, pos: QPointF) -> bool:
        target = self._box_hit_target(pos)
        if target is None:
            return False
        kind, corners = target
        self._box_edit_kind = kind
        self._box_edit_origin = tuple(float(value) for value in corners)
        self._box_edit_anchor = self._to_lonlat(pos.x(), pos.y())
        self._box_edit_anchor_px = pos
        self._box_edit_moved = False
        self._drag_last = None
        self._dragged = False
        return True

    def _update_box_edit_cursor(self, pos: QPointF) -> None:
        """Expose resize/move affordances before the user presses (T23.1)."""
        if self._box_corners is None:
            self._refresh_tool_chrome()
            return
        target = self._box_hit_target(pos)
        kind = target[0] if target is not None else ""
        cursor = {
            "nw": Qt.SizeFDiagCursor,
            "se": Qt.SizeFDiagCursor,
            "ne": Qt.SizeBDiagCursor,
            "sw": Qt.SizeBDiagCursor,
            "move": Qt.SizeAllCursor,
        }.get(kind, Qt.CrossCursor)
        self.setCursor(cursor)

    def _update_box_edit(self, pos: QPointF) -> None:
        """Apply the current projected handle/move gesture to geographic bounds."""
        kind = self._box_edit_kind
        origin = self._box_edit_origin
        anchor = self._box_edit_anchor
        if kind is None or origin is None or anchor is None:
            return
        anchor_px = self._box_edit_anchor_px or pos
        if not self._box_edit_moved and math.hypot(
            pos.x() - anchor_px.x(), pos.y() - anchor_px.y()
        ) < BOX_EDIT_DRAG_PX:
            return
        lon, lat = self._to_lonlat(pos.x(), pos.y())
        if not self._box_edit_moved and math.isclose(
                lon, anchor[0], abs_tol=1.0e-10) and math.isclose(
                lat, anchor[1], abs_tol=1.0e-10):
            return
        lat0, lon0, lat1, lon1 = origin
        if kind == "move":
            delta_lon = lon - anchor[0]
            delta_lat = lat - anchor[1]
            # Preserve size at a pole rather than flattening the rectangle.
            delta_lat = max(-90.0 - lat0, min(90.0 - lat1, delta_lat))
            lat0, lat1 = lat0 + delta_lat, lat1 + delta_lat
            lon0, lon1 = lon0 + delta_lon, lon1 + delta_lon
        else:
            lat = max(-90.0, min(90.0, float(lat)))
            if "n" in kind:
                lat1 = lat
            else:
                lat0 = lat
            if "w" in kind:
                lon0 = lon
            else:
                lon1 = lon
            lat0, lat1 = sorted((lat0, lat1))
            lon0, lon1 = sorted((lon0, lon1))
            if lon1 - lon0 > 360.0:
                if "w" in kind:
                    lon0 = lon1 - 360.0
                else:
                    lon1 = lon0 + 360.0
        self._box_corners = (lat0, lon0, lat1, lon1)
        self._box_edit_moved = True
        self.setToolTip(self._box_size_text(self._box_corners))
        self.update()

    def _finish_box_edit(self) -> bool:
        """Commit an edit and emit it, restoring invalid collapsed bounds."""
        changed = bool(self._box_edit_moved)
        origin = self._box_edit_origin
        corners = self._box_corners
        if not changed or corners is None:
            self._cancel_box_edit()
            return False
        try:
            from sharpmod.analysis.box_sounding import BoxRegion

            BoxRegion.from_corners(*corners)
        except Exception:  # noqa: BLE001 - a collapsed handle drag is undoable
            if origin is not None:
                self._box_corners = origin
            self._cancel_box_edit()
            self.update()
            return False
        self._cancel_box_edit()
        self._box_nodes = ()
        self._box_node_coverage = ()
        self._box_note = ""
        self.update()
        self.boxSelected.emit(*(float(value) for value in corners))
        return True

    def _box_gesture(self, event) -> bool:
        """Whether this press should start a box rather than a pan."""
        return self._box_mode or bool(event.modifiers() & Qt.ShiftModifier)

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
        editing = self._box_edit_kind is not None
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
        qp.setPen(QPen(edge, 1.8, Qt.DashLine if live or editing else Qt.SolidLine))
        copies = (corners,) if live else self._box_longitude_copies(corners)
        for visible_corners in copies:
            if p.affine:
                qp.drawRect(self._box_rect(p, visible_corners))
            else:
                qp.drawPolygon(self._box_polygon(p, visible_corners))

        if not live:
            # T23.1: these are real resize handles, not decorative ticks. Their
            # larger invisible hit area is defined by BOX_HANDLE_HIT_RADIUS_PX.
            qp.setPen(QPen(QColor(_map().selected_edge), 2.4))
            qp.setBrush(QBrush(QColor(_map().readout_text)))
            radius = BOX_HANDLE_RADIUS_PX
            for visible_corners in copies:
                for corner in self._box_corner_points(p, visible_corners):
                    qp.drawRect(QRectF(
                        corner.x() - radius,
                        corner.y() - radius,
                        radius * 2.0,
                        radius * 2.0,
                    ))

        if self._box_nodes and not live and not editing:
            width = self.width()
            height = self.height()
            for index, (lon, lat) in enumerate(self._box_nodes):
                in_domain = (
                    self._box_node_coverage[index]
                    if index < len(self._box_node_coverage)
                    else True
                )
                base_lon = lon + 360.0 * round(
                    ((corners[1] + corners[3]) / 2.0 - lon) / 360.0
                )
                for visible_corners in copies:
                    shifted_lon = base_lon + visible_corners[1] - corners[1]
                    point = self._to_px(shifted_lon, lat, p)
                    if (
                        point.x() < -6
                        or point.y() < -6
                        or point.x() > width + 6
                        or point.y() > height + 6
                    ):
                        continue
                    if in_domain:
                        qp.setBrush(QBrush(edge))
                        qp.setPen(Qt.NoPen)
                        qp.drawEllipse(point, 2.2, 2.2)
                    else:
                        # A hollow cross marks a planned node outside the
                        # selected model's sampleable domain (T23.3).
                        qp.setBrush(Qt.NoBrush)
                        qp.setPen(QPen(QColor(_map().selected_edge), 1.1))
                        qp.drawLine(QPointF(point.x() - 2.5, point.y() - 2.5),
                                    QPointF(point.x() + 2.5, point.y() + 2.5))
                        qp.drawLine(QPointF(point.x() - 2.5, point.y() + 2.5),
                                    QPointF(point.x() + 2.5, point.y() - 2.5))

    def _box_size_text(self, corners) -> str:
        """Return a ``W x H km`` description of a box."""
        from sharpmod.analysis.box_sounding import KM_PER_DEG_LAT, km_per_deg_lon

        lat0, lon0, lat1, lon1 = corners
        center_lat = (lat0 + lat1) / 2.0
        width = abs(lon1 - lon0) * km_per_deg_lon(center_lat)
        height = abs(lat1 - lat0) * KM_PER_DEG_LAT
        return f"{width:.0f} x {height:.0f} km"

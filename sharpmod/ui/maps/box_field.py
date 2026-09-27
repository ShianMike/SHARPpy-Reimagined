"""Box-analysis field map widget."""

from __future__ import annotations

from qtpy.QtCore import Qt, QPointF, QRectF
from qtpy.QtGui import QColor, QPainter, QPen

from sharpmod.ui.features.gui_theme import mono_font
from sharpmod.ui.maps.presentation import _estimate_label_widths, _map


class BoxFieldMixin:
    """A picker map that also paints a box analysis as a parameter field.

    Kept separate from :class:`PointMapWidget` so the ordinary picker never
    imports the analysis or colour-scale machinery, while the box workspace
    still inherits the real basemap, projection, pan, and zoom rather than
    growing a second map implementation.
    """

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

    def _label_protected_points(self, projection) -> list:
        """Keep geographic names off cell values and the selected marker."""

        protected = list(super()._label_protected_points(projection))
        if self._analysis is None:
            return protected
        points = self._analysis.points if self._show_values else ()
        if not points and self._selected_cell is not None:
            selected = self._analysis.point_at(*self._selected_cell)
            points = (selected,) if selected is not None else ()
        for point in points:
            try:
                pixel = self._to_px(float(point.lon), float(point.lat), projection)
                protected.append((float(pixel.x()), float(pixel.y())))
            except (AttributeError, TypeError, ValueError, OverflowError):
                continue
        return protected

    def place_labels(self) -> tuple:
        """Keep place names clear of values, selection, and the colour key.

        The base declutter protects label *anchors*. A long place name whose
        anchor is clear can still cross a cell value or the field key, so the
        field map performs one final rectangle intersection pass using the
        same draw geometry as :meth:`_draw_place_labels`.
        """

        labels = super().place_labels()
        if not labels:
            return labels
        estimates = _estimate_label_widths(tuple(item[2] for item in labels))
        protected = []
        if self._analysis is not None:
            points = self._analysis.points if self._show_values else ()
            if not points and self._selected_cell is not None:
                selected = self._analysis.point_at(*self._selected_cell)
                points = (selected,) if selected is not None else ()
            projection = self._proj()
            for point in points:
                try:
                    pixel = self._to_px(
                        float(point.lon), float(point.lat), projection)
                    protected.append((
                        float(pixel.x()) - 32.0,
                        float(pixel.y()) - 14.0,
                        64.0,
                        28.0,
                    ))
                except (AttributeError, TypeError, ValueError, OverflowError):
                    continue

        if self._field_scale is not None and self._field_key:
            legend_width = min(260.0, max(140.0, self.width() * 0.45))
            protected.append((
                self.width() - legend_width - 18.0,
                self.height() - 66.0,
                legend_width + 12.0,
                62.0,
            ))

        def intersects(left, top, width, height, other) -> bool:
            o_left, o_top, o_width, o_height = other
            return not (
                left + width < o_left
                or o_left + o_width < left
                or top + height < o_top
                or o_top + o_height < top
            )

        visible = []
        for x, y, label in labels:
            text_width = estimates.get(label, 0.0)
            draw_x = min(x + 5.0, max(0.0, self.width() - text_width - 4.0))
            label_rect = (draw_x, y - 13.0, text_width + 8.0, 14.0)
            if any(intersects(*label_rect, target) for target in protected):
                continue
            visible.append((x, y, label))
        return tuple(visible)

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
        """Frame a :class:`~sharpmod.analysis.box_sounding.BoxRegion`."""
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
        # T16.1: geography above filled analysis, like the picker maps.
        self._draw_boundary_outlines(qp, p)
        self._draw_place_labels(qp, p)
        self._draw_box(qp, p)
        self._draw_selected_cell(qp, p)
        self._draw_saved_points(qp, p)
        self._draw_scale_bar(qp, p)
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

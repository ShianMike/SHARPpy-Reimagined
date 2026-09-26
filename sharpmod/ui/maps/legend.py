"""Map legend geometry, color bars, and accessible state."""

from __future__ import annotations

import math

from qtpy import QtGui
from qtpy.QtCore import Qt, QPointF, QRectF
from qtpy.QtGui import QBrush, QColor, QPen, QPolygonF

from sharpmod.ui.features.gui_theme import mono_font, ui_font
from sharpmod.maps.map_overlays import format_age
from sharpmod.ui.maps.hatching import hatch_brush
from sharpmod.ui.maps.presentation import _map


#: The legend swatch is small, so it needs more opacity than the map area to
#: read as the same colour.
OVERLAY_LEGEND_FILL_ALPHA = 150

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




class MapLegendMixin:
    """Legend display and state shared by station and point maps."""

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
                from sharpmod.providers.hrrr_products import get_product

                return get_product(raster.short_name).palette
            except Exception:  # noqa: BLE001 - a legend is not worth a failure
                return None
        return None

    def _colour_bar_width(self) -> float:
        """Give tick labels room while keeping the key within the map."""
        available = max(1.0, float(self.width()) - 2.0 * LEGEND_MARGIN)
        return min(available, 360.0, max(180.0, self.width() * 0.42))

    @staticmethod
    def _category_row_layout(rows, hidden_count: int, width: float,
                             metrics, row_height: float):
        """Pack swatches by measured width and reserve a line for overflow."""
        box_size = max(11.0, min(18.0, float(row_height) - 3.0))
        chunks: list[list] = []
        current: list = []
        used = 0.0
        for row in rows:
            label_width = min(
                float(metrics.horizontalAdvance(str(row[0]))) + 6.0,
                max(20.0, float(width) - box_size - 3.0),
            )
            entry_width = box_size + 3.0 + label_width + 6.0
            if current and used + entry_width > width:
                chunks.append(current)
                current = []
                used = 0.0
            if len(chunks) == 2:
                hidden_count += 1
                continue
            current.append(row)
            used += entry_width
        if current:
            chunks.append(current)
        return chunks, hidden_count

    def _colour_bar_tick_layout(
        self, metrics, palette, x: float, width: float, *, bar_range=None
    ) -> list[tuple[float, str]]:
        """Return ``(left, label)`` for every tick that fits under the bar.

        Two rules, both about not lying to the reader. A label stays centred on
        the value it names and is nudged only as far as keeping it on screen
        requires -- clamping it into the bar's own span instead would park the
        last tick at the right-hand end, where it would appear to label a value
        the scale does not stop at. And the two ends are placed before the middle
        ticks and never yield to them: they carry the range, so dropping the top
        label to fit an intermediate one would leave no way to tell where the bar
        stops. Ticks outside a locked ``bar_range`` are dropped rather than
        clamped: a locked bar that still labels values it no longer spans would
        imply comparability the scale does not offer.
        """
        if bar_range is not None:
            try:
                low, high = float(bar_range[0]), float(bar_range[1])
            except (TypeError, ValueError, OverflowError, IndexError):
                low, high = palette.minimum, palette.maximum
        else:
            low, high = palette.minimum, palette.maximum
        span = (high - low) or 1.0
        ticks = sorted({low, high, *(value for value in palette.tick_values()
                                     if low <= value <= high)})
        # Labels belong to this bar, not the widget edge. Never let an end
        # value drift into the next overlay when the map is narrow.
        left_limit = x
        right_limit = x + width

        def placement(value):
            label = _format_tick(value)
            label_w = metrics.horizontalAdvance(label) + 6.0
            centre = x + width * (value - low) / span
            left = min(max(centre - label_w / 2.0, left_limit),
                       max(left_limit, right_limit - label_w))
            return left, label, label_w

        placed: list[tuple[float, str]] = []
        taken: list[tuple[float, float]] = []
        ends = [ticks[0]] if len(ticks) == 1 else [ticks[0], ticks[-1]]
        for value in ends + ticks[1:-1]:
            left, label, label_w = placement(value)
            right = left + label_w
            gap = max(6.0, metrics.height() * 0.35)
            if any(left < end + gap and right + gap > begin
                   for begin, end in taken):
                continue
            placed.append((left, label))
            taken.append((left, right))
        return placed

    def legend_details(self) -> str:
        """Source and scale context for a control rail beside the map."""
        from sharpmod.maps.map_legends import encoding_tag, resolution_context

        rasters = self._visible_rasters()
        palette = self._field_palette(rasters)
        field = next((raster for key, raster in rasters
                      if key == "hrrr_field"), None)
        lines = []
        if palette is not None:
            key = str(getattr(field, "short_name", "") or "")
            _palette, lock = self._effective_palette(key, field)
            low, high = float(palette.minimum), float(palette.maximum)
            if lock is not None:
                try:
                    low, high = float(lock["minimum"]), float(lock["maximum"])
                except (KeyError, TypeError, ValueError, OverflowError):
                    pass
            units = str(getattr(palette, "units", "") or "")
            mode = encoding_tag(continuous=True,
                                stepped=bool(getattr(palette, "stepped", False)))
            suffix = f" {units}" if units else ""
            lines.append(f"Color scale: {low:g}–{high:g}{suffix} · {mode}")
            lines.append(resolution_context(layer_key="hrrr_field"))
        sources = []
        for item in [*self._visible_overlays(),
                     *(raster for _key, raster in rasters)]:
            source = str(getattr(item, "attribution", "") or "").strip()
            if source and source not in sources:
                sources.append(source)
        if sources:
            lines.append("Source: " + ", ".join(sources))
        for state, detail in self.time_match_states().values():
            if state != "exact" and detail:
                note = f"Time match: {detail}"
                if note not in lines:
                    lines.append(note)
        return "\n".join(lines)

    def _draw_field_colour_bar(self, qp, palette, x: float, y: float, *,
                                 bar_range=None, hover_value=None,
                                 header_h=COLOUR_BAR_HEADER_H,
                                 bar_h=COLOUR_BAR_H,
                                 tick_h=COLOUR_BAR_TICK_H) -> None:
        """Draw a labelled colour bar using the supplied measured geometry.

        Sampled from the palette itself rather than redrawn from its stops, so a
        banded scale shows bands and a continuous one shows a ramp -- the bar and
        the map cannot disagree about what a colour means. ``bar_range``
        overrides the drawn span while locked (T20.3); ``hover_value`` drops a
        marker where the hovered number falls on the bar (T20.4).
        """
        width = self._colour_bar_width()
        if bar_range is not None:
            try:
                low, high = float(bar_range[0]), float(bar_range[1])
            except (TypeError, ValueError, OverflowError, IndexError):
                low, high = palette.minimum, palette.maximum
        else:
            low, high = palette.minimum, palette.maximum
        span = (high - low) or 1.0
        header_h = float(header_h)
        bar_h = float(bar_h)
        tick_h = float(tick_h)
        bar_y = y + header_h

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
                QRectF(x + offset, y + offset, width, header_h),
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
                    x + width * step / steps, bar_y, width / steps + 1.0, bar_h
                )
            )
        qp.setBrush(Qt.NoBrush)
        qp.setPen(QPen(QColor(_map().graticule), 1))
        qp.drawRect(QRectF(x, bar_y, width, bar_h))

        # Ticks: the values a forecaster reasons about, placed where their colour
        # actually changes. Labelling every stop on a thirty-class scale would
        # overlap into unreadability, so the palette nominates its own. Ticks
        # outside a locked span are dropped rather than clamped into it: parking
        # a tick at the bar's end would label a value the scale does not stop
        # at, which is the same lie the tick layout already refuses.
        tick_y = bar_y + bar_h
        for left, label in self._colour_bar_tick_layout(
            qp.fontMetrics(), palette, x, width, bar_range=(low, high)
        ):
            rect = QRectF(
                left,
                tick_y,
                qp.fontMetrics().horizontalAdvance(label) + 6.0,
                tick_h,
            )
            qp.setPen(QPen(QColor(_map().readout_shadow)))
            qp.drawText(rect.translated(1, 1), Qt.AlignCenter, label)
            qp.setPen(QPen(QColor(_map().readout_text)))
            qp.drawText(rect, Qt.AlignCenter, label)

        # T20.4: the hover marker. A vertical line through the bar at the hovered
        # value's fractional position, drawn in the selection colour with a
        # shadow pass like every other legend glyph.
        if hover_value is not None:
            from sharpmod.maps.map_legends import colorbar_fraction

            fraction = colorbar_fraction(hover_value, low, high)
            if fraction is not None:
                marker_x = x + width * float(fraction)
                for pen in (QPen(QColor(_map().readout_shadow), 1.6),
                            QPen(QColor(_map().selected_edge), 1.4)):
                    qp.setPen(pen)
                    offset = 1.0 if pen.widthF() > 1.5 else 0.0
                    qp.drawLine(QPointF(marker_x + offset, bar_y - 2.0),
                                QPointF(marker_x + offset,
                                        bar_y + bar_h + 2.0))
        qp.restore()

    def _legend_origin(self, corner: str, box_width: float,
                       box_height: float) -> tuple[float, float]:
        """Return the top-left origin for a legend box at ``corner`` (T20.1)."""
        margin = LEGEND_MARGIN
        width = max(1.0, float(self.width()))
        height = max(1.0, float(self.height()))
        if corner == "top-right":
            return (width - margin - box_width, 8.0)
        if corner == "bottom-right":
            return (width - margin - box_width, height - 8.0 - box_height)
        return (margin, height - 8.0 - box_height)

    def _draw_legend_text_row(self, qp, text: str, x: float, y: float,
                              width: float, line_h: float) -> None:
        """Draw one shadowed caption line at an explicit origin (T20)."""
        rect = QRectF(x, y, width, line_h)
        qp.setPen(QPen(QColor(_map().readout_shadow)))
        qp.drawText(rect.translated(1, 1), Qt.AlignLeft | Qt.AlignVCenter, text)
        qp.setPen(QPen(QColor(_map().readout_text)))
        qp.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)

    def _draw_legend_panel(self, qp, x: float, y: float, width: float,
                           height: float) -> tuple[float, float, float, float]:
        """Draw one restrained backing card and return its occupied rectangle."""
        panel = QRectF(x - 6.0, y - 5.0, width + 12.0, height + 10.0)
        qp.save()
        qp.setPen(Qt.NoPen)
        qp.setBrush(QBrush(QColor(_map().readout_shadow)))
        qp.drawRoundedRect(panel.translated(1.5, 1.5), 6.0, 6.0)
        background = QColor(_map().background)
        background.setAlpha(224)
        qp.setBrush(QBrush(background))
        qp.setPen(QPen(QColor(_map().graticule), 1.0))
        qp.drawRoundedRect(panel, 6.0, 6.0)
        qp.restore()
        return (panel.x(), panel.y(), panel.width(), panel.height())

    def _draw_category_rows(self, qp, rows, hidden_count: int, x: float,
                            y: float, width: float, *,
                            row_height=LEGEND_SWATCH_H,
                            line_height=LEGEND_LINE_H,
                            layout=None) -> None:
        """Draw measured categorical rows and their explicit overflow count."""
        if not rows and not hidden_count:
            return
        row_height = float(row_height)
        line_height = float(line_height)
        qp.setFont(mono_font("caption"))
        if layout is None:
            layout = self._category_row_layout(
                rows, hidden_count, width, qp.fontMetrics(), row_height)
        chunks, hidden_count = layout
        for line_index, chunk in enumerate(chunks):
            box_size = max(11.0, min(18.0, row_height - 3.0))
            swatch_y = (
                y + line_index * row_height + (row_height - box_size) / 2.0
            )
            cursor = x
            for row in chunk:
                label, stroke, fill, hatch_level = row[:4]
                marker_shape = str(row[4]) if len(row) > 4 else ""
                box = QRectF(cursor, swatch_y, box_size, box_size)
                if fill:
                    patch = QColor(fill)
                    patch.setAlpha(
                        255 if marker_shape else OVERLAY_LEGEND_FILL_ALPHA
                    )
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
                if marker_shape == "triangle":
                    qp.drawPolygon(
                        QPolygonF(
                            [
                                QPointF(box.center().x(), box.top()),
                                QPointF(box.right(), box.bottom()),
                                QPointF(box.left(), box.bottom()),
                            ]
                        )
                    )
                elif marker_shape == "diamond":
                    qp.drawPolygon(
                        QPolygonF(
                            [
                                QPointF(box.center().x(), box.top()),
                                QPointF(box.right(), box.center().y()),
                                QPointF(box.center().x(), box.bottom()),
                                QPointF(box.left(), box.center().y()),
                            ]
                        )
                    )
                else:
                    qp.drawRect(box)
                text_w = qp.fontMetrics().horizontalAdvance(label) + 6.0
                # Elide rather than overrun: a wrapped row that still exceeds
                # the legend width would paint over the map it explains.
                text_x = cursor + box_size + 3.0
                room = max(20.0, x + width - text_x)
                shown = label
                if text_w > room:
                    shown = qp.fontMetrics().elidedText(
                        label, Qt.ElideRight, int(room))
                    text_w = qp.fontMetrics().horizontalAdvance(shown) + 6.0
                qp.setPen(QPen(QColor(_map().readout_shadow)))
                qp.drawText(
                    QRectF(text_x, y + line_index * row_height,
                           text_w, row_height
                           ).translated(1, 1),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    shown,
                )
                qp.setPen(QPen(QColor(_map().readout_text)))
                qp.drawText(
                    QRectF(text_x, y + line_index * row_height,
                           text_w, row_height),
                    Qt.AlignLeft | Qt.AlignVCenter,
                    shown,
                )
                cursor += box_size + 3.0 + text_w + 6.0
        if hidden_count:
            qp.setFont(ui_font("caption"))
            self._draw_legend_text_row(
                qp, f"+{hidden_count} more categories", x,
                y + len(chunks) * row_height, width,
                line_height)

    def _legend_hover_value(self):
        """Return the hovered numeric value for the bar marker (T20.4)."""
        try:
            hover = getattr(self, "_hover_lonlat", None)
            if hover is None:
                return None
            sample = self.sample_field_at(float(hover[1]), float(hover[0]))
        except Exception:  # noqa: BLE001 - hover state is advisory
            return None
        value = getattr(sample, "value", None)
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError, OverflowError):
            return None

    def legend_state(self) -> dict:
        """Return the portable legend-choice snapshot (T20/session).

        Choices only: preferred corner, collapsed flag, and fixed-scale locks
        keyed by product. Geometry re-derives from the live widget size, so a
        restore never replays pixels.
        """
        from sharpmod.maps.map_legends import legend_state

        return legend_state(corner=getattr(self, "_legend_corner", ""),
                            collapsed=bool(getattr(self, "_legend_collapsed",
                                                   False)),
                            locks=dict(getattr(self, "_legend_locks", {}) or {}))

    def restore_legend_state(self, payload) -> None:
        """Restore legend choices, ignoring unknown values (T20)."""
        from sharpmod.maps.map_legends import restore_legend_state

        state = restore_legend_state(payload)
        self._legend_corner = str(state.get("corner") or "")
        self._legend_collapsed = bool(state.get("collapsed", False))
        self._legend_locks = dict(state.get("locks") or {})
        self.update()

    def set_legend_corner(self, corner: str = "") -> None:
        """Prefer a legend corner (T20.1); ``""`` follows the auto order."""
        from sharpmod.maps.map_legends import LEGEND_CORNERS

        wanted = str(corner or "").strip().lower().replace("_", "-")
        self._legend_corner = wanted if wanted in LEGEND_CORNERS else ""
        self.update()

    def legend_corner(self) -> str:
        """Return the preferred corner, or ``""`` for automatic (T20.1)."""
        return str(getattr(self, "_legend_corner", "") or "")

    def set_legend_collapsed(self, collapsed: bool) -> None:
        """Collapse the legend to its one-line identity (T20.1)."""
        collapsed = bool(collapsed)
        if collapsed == getattr(self, "_legend_collapsed", False):
            return
        self._legend_collapsed = collapsed
        self.update()

    def is_legend_collapsed(self) -> bool:
        """Whether the legend is collapsed to one line (T20.1)."""
        return bool(getattr(self, "_legend_collapsed", False))

    def set_scale_locked(self, product: str, locked: bool = True) -> None:
        """Lock or release one product's colour scale at its range (T20.3).

        A lock names the comparator contract: while locked, the bar keeps the
        stored range even when the depicted product changes underneath, and any
        change is labelled explicitly rather than silently rescaling.
        """
        key = str(product or "").strip()
        if not key:
            return
        locks = getattr(self, "_legend_locks", None)
        if locks is None:
            locks = {}
            self._legend_locks = locks
        if bool(locked):
            palette = self._palette_for_product(key)
            if palette is None:
                return
            locks[key] = {
                "minimum": float(palette.minimum),
                "maximum": float(palette.maximum),
                "units": str(getattr(palette, "units", "") or ""),
                "label": self._product_label_for(key),
            }
        else:
            locks.pop(key, None)
        self.update()

    def is_scale_locked(self, product: str = "") -> bool:
        """Report whether a product's scale is locked (T20.3)."""
        locks = getattr(self, "_legend_locks", {}) or {}
        if product:
            return str(product) in locks
        return bool(locks)

    def scale_lock_info(self, product: str = "") -> dict | None:
        """Return the stored lock for ``product``, or ``None`` (T20.3)."""
        locks = getattr(self, "_legend_locks", {}) or {}
        lock = locks.get(str(product or ""))
        return dict(lock) if isinstance(lock, dict) else None

    def _palette_for_product(self, key: str):
        """Return the catalogue palette for a product key, or ``None``."""
        try:
            from sharpmod.providers.hrrr_products import get_product

            return get_product(key).palette
        except Exception:  # noqa: BLE001 - a legend is not worth a failure
            return None

    def _product_label_for(self, key: str) -> str:
        """Return the display name for a product key (T20.2)."""
        try:
            from sharpmod.providers.hrrr_products import get_product

            return str(get_product(key).label or key)
        except Exception:  # noqa: BLE001 - fall back to the key
            return str(key)

    def _effective_palette(self, key: str, raster):
        """Return ``(palette, locked, lock)`` for the depicted field (T20.3).

        The depicted palette always draws; the lock only decides the range it
        draws against. A locked bar keeps the stored range and labels any
        change explicitly, so a comparison across panels or times reads one
        stated scale instead of silently rescaling under it.
        """
        palette = self._field_palette([("hrrr_field", raster)]) \
            if raster is not None \
            else self._field_palette(self._visible_rasters())
        lock = self.scale_lock_info(key) if key else None
        if lock is None and key:
            # One lock is the comparator contract: when any product is locked,
            # the bar keeps that range so panels and times read one scale.
            locks = getattr(self, "_legend_locks", {}) or {}
            for _other, candidate in locks.items():
                if isinstance(candidate, dict):
                    lock = dict(candidate)
                    break
        return palette, lock

    def _legend_clip_box(self):
        """Return the last painted legend ``(x, y, w, h)``, or ``None``."""
        box = getattr(self, "_legend_clip", None)
        try:
            x, y, w, h = (float(value) for value in box)
        except (TypeError, ValueError, OverflowError):
            return None
        return (x, y, w, h)

    def _clear_empty_legend_layout(self) -> None:
        """Drop the prior frame's layout when no legend content remains.

        The scale bar and place-label declutter run before the legend paints,
        so they intentionally consume the previous frame's measured box.  If
        the final layer is hidden, however, that box must be cleared before
        the first layer-free frame; otherwise the scale bar visibly jumps for
        one paint and only returns on the next repaint.
        """

        if (
            self._visible_overlays()
            or self._visible_rasters()
            or self.profile_marker_key()
        ):
            return
        self._legend_bar = None
        self._legend_bar_range = None
        self._legend_clip = None
        self._legend_layout_corner = "bottom-left"
        self._legend_layout_collapsed = False

    def legend_layout_info(self) -> dict:
        """Return the last painted legend layout for tests (T20.1/T20.4).

        ``corner``/``collapsed`` describe what was drawn; ``bar`` carries the
        colour-bar ``(x, y, width, height)`` and ``range`` its
        ``(minimum, maximum)`` when a continuous bar was painted.
        """
        info = {
            "corner": str(getattr(self, "_legend_layout_corner", "bottom-left")),
            "collapsed": bool(getattr(self, "_legend_layout_collapsed", False)),
            "bar": None, "range": None,
        }
        bar = getattr(self, "_legend_bar", None)
        if bar is not None:
            info["bar"] = tuple(float(value) for value in bar)
        bar_range = getattr(self, "_legend_bar_range", None)
        if bar_range is not None:
            info["range"] = tuple(float(value) for value in bar_range)
        return info

    def hover_colorbar_fraction(self):
        """Return the hover value's 0-1 position on the legend bar (T20.4).

        ``None`` when no continuous bar is drawn or no numeric hover value
        exists: the marker only points where a real value meets a real scale.
        """
        from sharpmod.maps.map_legends import colorbar_fraction

        bar_range = getattr(self, "_legend_bar_range", None)
        bar = getattr(self, "_legend_bar", None)
        if bar is None or bar_range is None:
            return None
        try:
            sample = self.sample_field_at(*self._hover_lonlat[::-1]) \
                if getattr(self, "_hover_lonlat", None) else None
        except Exception:  # noqa: BLE001 - hover state is advisory
            return None
        value = getattr(sample, "value", None)
        if value is None:
            return None
        try:
            return colorbar_fraction(float(value), float(bar_range[0]),
                                     float(bar_range[1]))
        except (TypeError, ValueError, OverflowError):
            return None

    def legend_accessible_text(self) -> str:
        """Return the spoken legend equivalent (T20.2, T21.2).

        Names the product, units, scale mode and range, the encoding, the
        visible categories, and -- since T21 -- the time header plus each
        row's time-match state, so the spoken legend answers "when" with the
        same words the painted one shows.
        """
        from sharpmod.maps.map_legends import (
            compact_identity,
            encoding_tag,
            scale_lock_label,
        )
        from sharpmod.maps.map_time import layer_time_match

        try:
            header = self.time_header_text()
        except (AttributeError, RuntimeError):
            header = ""
        rasters = self._visible_rasters()
        palette = self._field_palette(rasters)
        rows = self._overlay_legend_rows()
        parts: list[str] = []
        if header:
            parts.append(header)
            try:
                from sharpmod.maps.map_time import mode_label

                parts.append(mode_label(self.map_mode()))
            except (AttributeError, RuntimeError):
                pass
        key = ""
        for raster_key, raster in rasters:
            if raster_key == "hrrr_field":
                key = str(getattr(raster, "short_name", "") or "")
                break
        if palette is not None:
            _draw_palette, lock = self._effective_palette(
                key, dict(rasters).get("hrrr_field"))
            low, high = float(palette.minimum), float(palette.maximum)
            if lock is not None:
                try:
                    low, high = float(lock["minimum"]), float(lock["maximum"])
                except (TypeError, ValueError, OverflowError, KeyError):
                    pass
            label = self._product_label_for(key) if key else "Model field"
            units = str(getattr(palette, "units", "") or "")
            locked = lock is not None
            if getattr(self, "_legend_metadata_in_rail", False):
                suffix = f" {units}" if units else ""
                parts.append(f"{label} · Color scale {low:g}–{high:g}{suffix}")
            else:
                parts.append(scale_lock_label(product=label, minimum=low,
                                              maximum=high, units=units,
                                              locked=locked))
            parts.append(encoding_tag(continuous=True,
                                      stepped=bool(getattr(palette, "stepped",
                                                           False))))
        if rows:
            parts.append(encoding_tag(continuous=False))
            shown = [label for label, _stroke, _fill, _hatch in rows[:8]]
            parts.append("Categories: " + ", ".join(shown))
            if len(rows) > 8:
                parts.append(f"+{len(rows) - 8} more")
        # T21.2: exceptions retain the same words as the painted captions.
        # An exact match is already stated by the header, so repeating it here
        # adds noise without adding a fact.
        try:
            for state, detail in self.time_match_states().values():
                if state != "exact" and detail:
                    marker = "⚠ " if state in ("outside", "unavailable") \
                        else ""
                    parts.append(f"{marker}Time match: {detail}")
        except (AttributeError, RuntimeError):
            pass
        if not parts:
            for layer in self._visible_overlays():
                parts.append(str(getattr(layer, "title", "") or "Overlay"))
        return "; ".join(part for part in parts if part)

    def _legend_avoid_points(self, p) -> list[tuple[float, float]]:
        """Return widget points the legend must avoid (T20.1, T21.1, T24.1)."""
        # Permanent ownership: coordinate/selection readout is painted at the
        # upper-left. Reserving a stable point here prevents the exact overlap
        # that used to occur when a persisted top-left preference was restored.
        avoid: list[tuple[float, float]] = [(14.0, 14.0)]
        try:
            chip = self.time_header_rect()
            if chip is not None:
                x, y, w, h = chip
                avoid.extend([(x, y), (x + w, y), (x, y + h),
                              (x + w, y + h)])
        except (TypeError, ValueError, OverflowError, AttributeError,
                RuntimeError):
            pass
        try:
            if getattr(self, "_point_lonlat", None) is not None:
                lon, lat = self._point_lonlat
                avoid.append((float(self._to_px(lon, lat, p).x()),
                              float(self._to_px(lon, lat, p).y())))
        except (TypeError, ValueError, OverflowError, AttributeError,
                RuntimeError):
            pass
        # Cursor hover is intentionally not an avoidance point. It changes on
        # every pixel and formerly made the legend flee from bottom-right to
        # top-left, then snap back when the reader chased it with the pointer.
        try:
            extra = self._legend_avoid_points_extra(p)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            extra = []
        for point in extra or ():
            try:
                avoid.append((float(point[0]), float(point[1])))
            except (TypeError, ValueError, OverflowError, IndexError):
                continue
        try:
            rect = self.inspection_card_rect()
            if rect is not None:
                avoid.extend([(rect.left(), rect.top()),
                              (rect.right(), rect.top()),
                              (rect.left(), rect.bottom()),
                              (rect.right(), rect.bottom())])
        except (TypeError, ValueError, OverflowError, AttributeError,
                RuntimeError):
            pass
        return avoid

    def _draw_overlay_legend(self, qp) -> None:
        draw_overlay_legend(self, qp)


from sharpmod.ui.maps.legend_drawing import draw_overlay_legend  # noqa: E402

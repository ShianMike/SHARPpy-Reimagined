"""Small, fixed-pixel weather symbols shared by picker and sounding maps."""

from __future__ import annotations

import math


def draw_map_point_symbol(
    painter, x, y, radius, symbol, fill, stroke, qtcore, qtgui
) -> None:
    """Draw a report at a projected point without scaling its size with zoom."""
    if not all(math.isfinite(value) for value in (x, y, radius)) or radius <= 0:
        return
    centre = qtcore.QPointF(x, y)
    painter.setBrush(qtgui.QBrush(qtgui.QColor(fill)))
    painter.setPen(qtgui.QPen(qtgui.QColor(stroke), 1.25))
    if symbol == "triangle":
        painter.drawPolygon(qtgui.QPolygonF([
            qtcore.QPointF(x, y - radius),
            qtcore.QPointF(x + radius, y + radius),
            qtcore.QPointF(x - radius, y + radius),
        ]))
    elif symbol == "square":
        painter.drawRect(qtcore.QRectF(
            x - radius, y - radius, radius * 2, radius * 2
        ))
    elif symbol == "diamond":
        painter.drawPolygon(qtgui.QPolygonF([
            qtcore.QPointF(x, y - radius),
            qtcore.QPointF(x + radius, y),
            qtcore.QPointF(x, y + radius),
            qtcore.QPointF(x - radius, y),
        ]))
    else:
        painter.drawEllipse(centre, radius, radius)

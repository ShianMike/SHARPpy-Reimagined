"""Shared compact rendering for unit suffixes in sounding value rows."""

from __future__ import annotations

from qtpy import QtCore, QtGui


UNIT_FONT_SCALE = 0.78
_UNIT_SUFFIXES = tuple(sorted((
    " degrees C/km",
    " degrees C",
    " m\u00b3/s\u00b3",
    " J/kg/m",
    " m AGL",
    " m2/s2",
    " C/km",
    " g/kg",
    " J/kg",
    " m/s",
    " kt",
    " cm",
    " in",
    " m",
), key=len, reverse=True))
_DEGREE_SUFFIXES = ("\u00b0F", "\u00b0C")


def split_value_unit(text: str) -> tuple[str, str] | None:
    """Split a sounding value from a recognized trailing unit suffix."""
    if not isinstance(text, str) or not text:
        return None
    for suffix in _UNIT_SUFFIXES:
        if text.endswith(suffix):
            value = text[:-len(suffix)]
            if value and value.strip():
                return value, suffix
    for suffix in _DEGREE_SUFFIXES:
        if text.endswith(suffix):
            value = text[:-len(suffix)]
            if value and value.strip():
                return value, suffix
    return None


def small_unit_font(font: QtGui.QFont) -> QtGui.QFont:
    """Return a legible smaller variant of ``font`` for a value's unit."""
    compact = QtGui.QFont(font)
    pixel_size = compact.pixelSize()
    if pixel_size > 0:
        compact.setPixelSize(max(8, int(round(pixel_size * UNIT_FONT_SCALE))))
    else:
        compact.setPointSizeF(max(6.0, compact.pointSizeF() * UNIT_FONT_SCALE))
    return compact


def value_unit_width(font: QtGui.QFont, text: str) -> int:
    """Return the width of ``text`` with any recognized unit compacted."""
    parts = split_value_unit(text)
    metrics = QtGui.QFontMetrics(font)
    if parts is None:
        return metrics.horizontalAdvance(text)
    value, unit = parts
    return (metrics.horizontalAdvance(value)
            + QtGui.QFontMetrics(small_unit_font(font)).horizontalAdvance(unit))


def draw_text_with_smaller_unit(qp: QtGui.QPainter, rect, text: str,
                                align: QtCore.Qt.AlignmentFlag) -> bool:
    """Draw a recognized unit suffix smaller than its numeric value."""
    parts = split_value_unit(text)
    if parts is None:
        return False

    value, unit = parts
    value_font = QtGui.QFont(qp.font())
    unit_font = small_unit_font(value_font)
    value_width = QtGui.QFontMetrics(value_font).horizontalAdvance(value)
    unit_width = QtGui.QFontMetrics(unit_font).horizontalAdvance(unit)
    group_width = value_width + unit_width
    if group_width > rect.width():
        return False

    if align & QtCore.Qt.AlignRight:
        left = rect.x() + rect.width() - group_width
    elif align & QtCore.Qt.AlignHCenter:
        left = rect.x() + (rect.width() - group_width) // 2
    else:
        left = rect.x()

    flags = int(QtCore.Qt.TextSingleLine | QtCore.Qt.AlignLeft
                | QtCore.Qt.AlignVCenter)
    qp.setFont(value_font)
    qp.drawText(QtCore.QRect(left, rect.y(), value_width, rect.height()),
                flags, value)
    qp.setFont(unit_font)
    qp.drawText(QtCore.QRect(left + value_width, rect.y(), unit_width,
                             rect.height()), flags, unit)
    qp.setFont(value_font)
    return True

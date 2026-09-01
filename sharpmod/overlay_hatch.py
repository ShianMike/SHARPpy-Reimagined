"""Brushes for graded overlay hatching.

SPC separates its Conditional Intensity Groups by pattern alone -- every level
publishes the same grey fill -- so for these areas the pattern carries the
data. The two places that draw overlays, the picker maps and the locator inset
on the hodograph, must therefore agree on it exactly, which is why the family
is defined here once rather than at each call site.

Qt is passed in rather than imported, matching :mod:`sharpmod.map_overlays` in
keeping this module import-safe for callers that have no display.
"""

from __future__ import annotations

from typing import Any

#: Side of the tiled texture, in pixels. A 45-degree line across it leaves the
#: diagonals about 6 px apart, which reads as hatching at both map and inset
#: scale without closing up into a solid block.
HATCH_TILE_PX = 8

#: Highest level the pattern family expresses. Tornado and wind publish three
#: groups, hail and the Day 3 total-severe product two.
MAX_HATCH_LEVEL = 3

#: Textures are tiny and there are at most a handful of colour/level pairs in
#: play, but they are rebuilt on every repaint without this.
_TEXTURE_CACHE: dict[tuple[int, int], Any] = {}


def clear_texture_cache() -> None:
    """Drop cached textures. Used by tests and on theme changes."""
    _TEXTURE_CACHE.clear()


def hatch_texture(qtcore: Any, qtgui: Any, colour_rgba: int, level: int) -> Any:
    """Return a tiling ``QImage`` for a graded hatch at ``level``.

    CIG1 is a broken diagonal, CIG2 the same diagonal unbroken, and CIG3 a
    diagonal cross, so the three read as one family of increasing density. The
    tile wraps, so strokes join across tile edges into continuous diagonals.
    """
    level = max(1, min(int(level), MAX_HATCH_LEVEL))
    key = (int(colour_rgba), level)
    cached = _TEXTURE_CACHE.get(key)
    if cached is not None:
        return cached

    size = HATCH_TILE_PX
    image = qtgui.QImage(size, size, qtgui.QImage.Format_ARGB32_Premultiplied)
    image.fill(qtcore.Qt.transparent)
    painter = qtgui.QPainter(image)
    try:
        # Aliased deliberately: a 1 px stroke smeared over two rows halves its
        # contrast, and a tile this small cannot absorb that.
        painter.setRenderHint(qtgui.QPainter.Antialiasing, False)
        painter.setPen(qtgui.QPen(qtgui.QColor.fromRgba(colour_rgba), 1))
        # Bottom-left to top-right, the direction SPC draws.
        if level >= 2:
            painter.drawLine(0, size, size, 0)
        else:
            # Half the run, so tiling alternates stroke and gap along one
            # diagonal instead of producing a continuous line.
            painter.drawLine(0, size, size // 2, size // 2)
        if level >= 3:
            painter.drawLine(0, 0, size, size)
    finally:
        painter.end()

    _TEXTURE_CACHE[key] = image
    return image


def hatch_brush(qtcore: Any, qtgui: Any, colour: Any, level: int) -> Any:
    """Return the brush for a hatch qualifier at ``level``.

    Level ``0`` is the ungraded "significant severe" area SPC published before
    2026-03-03, which keeps the single diagonal it was always drawn with.
    """
    try:
        level = int(level)
    except (TypeError, ValueError):
        level = 0
    if level <= 0:
        return qtgui.QBrush(colour, qtcore.Qt.BDiagPattern)
    return qtgui.QBrush(hatch_texture(qtcore, qtgui, colour.rgba(), level))

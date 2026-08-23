"""Shared compact rendering for unit suffixes in sounding value rows."""

from __future__ import annotations

from qtpy import QtCore, QtGui


UNIT_FONT_SCALE = 0.78

#: Glyph rasterisation settings for crisp text in both the GUI and the PNG
#: export path.  Lives here because this module depends only on ``qtpy``, so both
#: ``sharpmod.render`` and the ``sharpmod.viz`` widgets can use it without an
#: import cycle.
#:
#: MEASURED on a 13px sample string as the share of inked pixels left at mid-tone
#: (a soft edge ramps over several pixels, so lower is crisper):
#:
#:   mode        default   vertical hinting
#:   lossless      0.649     0.547   (-15.7%, solid ink 0.251 -> 0.370)
#:   hd 2x         0.398     0.367   ( -7.8%)
#:   uhd 2.8x      0.362     0.343   ( -5.3%)
#:
#: ``PreferVerticalHinting`` keeps baselines and x-heights snapped to the pixel
#: grid, which is what makes text look sharp, while dropping *horizontal*
#: hinting, which distorts stem positions and advance widths.  That distortion is
#: worst in the export path because the painter is scaled: glyphs are hinted in
#: unscaled design space and the snapped positions then land between physical
#: pixels.  ``PreferNoHinting`` measured identically at 2x/2.8x but clearly worse
#: at 1x, so vertical hinting is used for being the only setting that never loses.
RENDER_FONT_STYLE_STRATEGY = (
    QtGui.QFont.StyleStrategy.PreferAntialias
    | QtGui.QFont.StyleStrategy.PreferQuality
)

#: Hinting used only when painting into a density-scaled export surface.
RENDER_FONT_SCALED_HINTING = QtGui.QFont.HintingPreference.PreferVerticalHinting

#: Hinting used everywhere else.  Set explicitly rather than left alone so the
#: function is deterministic: a font built while a scaled export was active must
#: be returnable to unscaled behaviour instead of silently keeping the scaled
#: hinting it was constructed with.
RENDER_FONT_DEFAULT_HINTING = QtGui.QFont.HintingPreference.PreferDefaultHinting

#: At 1x, full hinting is what snaps stems onto whole pixels, so the default is
#: left alone.  MEASURED on real renders (share of inked pixels fully on, higher
#: is crisper), which is why this is scale-dependent rather than global:
#:
#:   mode        default   vertical hinting
#:   lossless 1x   0.226     0.202   <- WORSE, so 1x keeps the default
#:   hd 2x         0.357     0.374   <- better
#:   uhd 2.8x      0.527     0.570   <- better
#:
#: For comparison, rasterising the same face natively at the matching physical
#: size scores 0.377 at 18px and 0.553 at 25px, so the scaled modes now land at
#: or above native quality and the export path is not the limiting factor.
_scaled_export = False


def set_scaled_export(enabled):
    """Declare whether painting targets a density-scaled export surface.

    Returns the previous value so a caller can restore it.
    """
    global _scaled_export
    previous = _scaled_export
    _scaled_export = bool(enabled)
    return previous


def scaled_export_active():
    """Is density-scaled export painting currently declared?"""
    return _scaled_export


def apply_render_font_quality(font, scaled=None):
    """Apply the measured crisp-text rasterisation settings to ``font``.

    ``scaled`` defaults to the current export state.  Returns the same object so
    it can be used inline.
    """
    use_scaled = _scaled_export if scaled is None else bool(scaled)
    try:
        font.setStyleStrategy(RENDER_FONT_STYLE_STRATEGY)
        font.setHintingPreference(
            RENDER_FONT_SCALED_HINTING
            if use_scaled
            else RENDER_FONT_DEFAULT_HINTING
        )
    except (AttributeError, TypeError):  # pragma: no cover - binding guard
        pass
    return font


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

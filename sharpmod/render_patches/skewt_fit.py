"""Skewt Fit adjustments for headless sounding renders."""

from __future__ import annotations

from sharpmod.render_patches.frames import PANEL_FRAME_WIDTH
from sharpmod.render_patches.geometry import _fit_rect_to_skewt_plot
from sharpmod.rendering import cli as _render_api


def _install_skewt_effective_layer_label_fit():
    """Keep effective-layer labels, including bottom ``SFC``, in the plot box."""
    try:
        import sharppy.viz.skew as _skew
        _cls = _skew.plotSkewT
        if getattr(_cls, "_sharpmod_effective_layer_fit", False):
            return
        _tab = _skew.tab
        _QtGui = _skew.QtGui
        _QtCore = _skew.QtCore
        _orig = _cls.draw_effective_layer

        def draw_effective_layer(self, qp):
            try:
                qp.setClipping(True)
                line_len = 15
                layout = _render_api._skewt_effective_layer_labels(
                    self, _QtCore, _QtGui, _tab)
                if layout is None:
                    return

                x1 = layout["x1"]
                y1 = layout["y1"]
                y2 = layout["y2"]
                text_bot = layout["text_bot"]
                text_top = layout["text_top"]
                text_esrh = layout["text_esrh"]
                rect1 = layout["rect_bot"]
                rect2 = layout["rect_top"]
                rect3 = layout["rect_esrh"]

                qp.setFont(self.esrh_font)

                # No background plate behind these three labels. The vendored
                # method fills each rect with ``bg_color`` first, but that is
                # the plot's own background colour, so the plate adds no
                # contrast the plot did not already give -- it only punches a
                # hole in the isotherms, dry adiabats, and mixing-ratio lines
                # passing behind the text. The rects are still needed, as they
                # position the text below.

                qp.setPen(_QtGui.QPen(self.eff_layer_color, 2,
                                      _QtCore.Qt.SolidLine))
                qp.drawLine(x1 - line_len, y1, x1 + line_len, y1)
                qp.drawLine(x1 - line_len, y2, x1 + line_len, y2)
                qp.drawLine(x1, y1, x1, y2)

                # ``TextDontClip`` on all three, and clipping lifted for the
                # bottom one, exactly as upstream does. That bottom label sits
                # *below* the inflow layer's lower line, which for a
                # surface-based layer is at or under the plot's bottom edge, so
                # with clipping left on it is discarded -- which is how the
                # ``SFC`` label went missing.
                flags = (_QtCore.Qt.TextDontClip | _QtCore.Qt.AlignLeft
                         | _QtCore.Qt.AlignVCenter)
                qp.setClipping(False)
                qp.drawText(rect1, flags, text_bot)
                qp.setClipping(True)
                qp.drawText(rect2, flags, text_top)
                qp.drawText(rect3, flags, text_esrh)
            except Exception:
                _orig(self, qp)

        _cls.draw_effective_layer = draw_effective_layer
        _cls._sharpmod_effective_layer_fit = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


class _RectSuppressingPainter:
    """Forward every painter call except ``drawRect``.

    Lets a vendored draw method run untouched while the opaque background plate
    it paints first is dropped, so its label sits directly on the plot and the
    linework behind stays continuous.

    Used instead of restating the method so the colours, geometry, and text
    remain upstream and cannot drift from it.
    """

    __slots__ = ("_qp",)

    def __init__(self, qp):
        self._qp = qp

    def drawRect(self, *_args, **_kwargs):  # noqa: N802 - Qt API name
        return None

    def __getattr__(self, name):
        return getattr(self._qp, name)


def _install_skewt_lapse_rate_label_placement():
    """Use the Rust painter's fixed minus-30-degree display anchor."""
    import sharppy.viz.skew as _skew
    from sharpmod_rs import draw_fixed_lapse_layer

    _cls = _skew.plotSkewT
    if getattr(_cls, "_sharpmod_lapse_rate_label_placed", False):
        return

    def draw_max_lapse_rate_layer(self, qp, bound=4.5):
        return draw_fixed_lapse_layer(self, qp, bound)

    _cls.draw_max_lapse_rate_layer = draw_max_lapse_rate_layer
    _cls._sharpmod_lapse_rate_label_placed = True


def _install_skewt_lapse_rate_label_transparency():
    """Drop the opaque plate behind the skew-T max-lapse-rate label.

    Same reasoning as the effective-inflow labels: the plate is filled with
    ``bg_color``, which is already the colour behind it, so it contributes no
    legibility and instead cuts a rectangular gap out of the background
    isopleths crossing the label.
    """
    try:
        import sharppy.viz.skew as _skew

        _cls = _skew.plotSkewT
        if getattr(_cls, "_sharpmod_lapse_rate_label_transparent", False):
            return
        _orig = _cls.draw_max_lapse_rate_layer

        def draw_max_lapse_rate_layer(self, qp, *args, **kwargs):
            try:
                _orig(self, _RectSuppressingPainter(qp), *args, **kwargs)
            except Exception:  # pragma: no cover - vendored failure path
                _orig(self, qp, *args, **kwargs)

        _cls.draw_max_lapse_rate_layer = draw_max_lapse_rate_layer
        _cls._sharpmod_lapse_rate_label_transparent = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


def _install_hodo_storm_motion_label_transparency():
    """Drop the plate behind the hodograph's ``RM``/``LM`` labels.

    ``drawSMV`` positions those two labels with rectangles it intends to be
    invisible -- its own comment calls them "the invisible rectangles" -- and it
    tries to achieve that by setting an alpha-zero *pen*. It never sets a
    *brush*, so the rectangles are filled with whatever brush the previous draw
    call happened to leave active, which paints a solid plate over the hodograph
    rings and traces behind each label. Suppressing the two ``drawRect`` calls
    honours the upstream intent; the text is drawn from the same rectangles and
    is unaffected.

    The same block also does ``color = self.bg_color`` followed by
    ``color.setAlpha(0)``. That is not a copy -- it mutates the widget's own
    background colour in place and leaves its alpha at zero for everything drawn
    afterwards, so the alpha is restored once the call returns.
    """
    try:
        import sharppy.viz.hodo as _hodo

        _cls = _hodo.plotHodo
        if getattr(_cls, "_sharpmod_smv_label_transparent", False):
            return
        _orig = _cls.drawSMV

        def drawSMV(self, qp, *args, **kwargs):  # noqa: N802 - upstream Qt API
            background = getattr(self, "bg_color", None)
            alpha = background.alpha() if background is not None else None
            try:
                _orig(self, _RectSuppressingPainter(qp), *args, **kwargs)
            except Exception:  # pragma: no cover - vendored failure path
                _orig(self, qp, *args, **kwargs)
            finally:
                if background is not None and alpha is not None:
                    background.setAlpha(alpha)

        _cls.drawSMV = drawSMV
        _cls._sharpmod_smv_label_transparent = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


def _install_skewt_frame_ontop():
    """Redraw the white skew-T frame outline on top of the plotted data.

    The surface value labels (and other in-plot labels) paint an opaque
    background mask to stay legible; where a label sits against the skew-T's
    white border that mask punches a black gap in the outline. This wraps
    ``plotSkewT.plotData`` to redraw ONLY the four white frame lines after the
    vendored data pass, so the outline is always intact. It redraws just the
    border strokes (never the vendored black clearing rects), so it restores
    the outline without erasing any plotted content. Idempotent + guarded.
    """
    try:
        import sharppy.viz.skew as _skew
        _cls = _skew.plotSkewT
        if getattr(_cls, "_sharpmod_frame_ontop", False):
            return
        _QtGui = _skew.QtGui
        _QtCore = _skew.QtCore
        _orig = _cls.plotData

        def plotData(self):
            _orig(self)
            try:
                qp = _QtGui.QPainter()
                qp.begin(self.plotBitMap)
                qp.setClipping(False)
                # Use the same shared width as every auxiliary plot box.  This
                # redraw goes directly onto the raw painter, so it must not
                # drift from the frame-normalisation patch installed below.
                pen = _QtGui.QPen(
                    self.fg_color, PANEL_FRAME_WIDTH, _QtCore.Qt.SolidLine)
                qp.setPen(pen)
                lpad = int(self.lpad)
                tpad = int(self.tpad)
                bry = int(self.bry)
                rx = int(self.brx + self.rpad)
                qp.drawLine(lpad, tpad, rx, tpad)
                qp.drawLine(rx, tpad, rx, bry)
                qp.drawLine(rx, bry, lpad, bry)
                qp.drawLine(lpad, bry, lpad, tpad)
                qp.end()
            except Exception:
                pass

        _cls.plotData = plotData
        _cls._sharpmod_frame_ontop = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


#: Fill for the box-mean callout. Amber matches the rectangle the picker draws
#: for the box on the map, and it is neither the plot's foreground nor its
#: background in any bundled theme, so the chip reads as a callout about the
#: sounding rather than as another piece of plotted data.
BOX_MEAN_BADGE_FILL = "#FFD000"

#: Smallest point size the callout will shrink to before it gives up. Below this
#: it stops being a warning and becomes a smudge over the isotherms.
BOX_MEAN_BADGE_MIN_PT = 6

#: Most of the plot's width the callout may occupy. Past this it is covering
#: sounding rather than annotating it.
BOX_MEAN_BADGE_MAX_WIDTH_FRAC = 0.42

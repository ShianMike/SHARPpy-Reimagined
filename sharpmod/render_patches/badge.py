"""Badge adjustments for headless sounding renders."""

from __future__ import annotations

import os
from sharpmod.rendering import cli as _render_api


def collection_meta(prof_coll, key):
    """Read one metadata value off a profile collection, tolerantly.

    Mirrors :func:`sharpmod.viz.hodo_locator._collection_meta`: the vendored
    collection exposes ``getMeta``, while the lightweight stand-ins used in tests
    carry a plain ``_meta`` dict.
    """
    try:
        getter = getattr(prof_coll, "getMeta", None)
        if callable(getter):
            return getter(key)
    except Exception:
        pass
    meta = getattr(prof_coll, "_meta", None)
    if isinstance(meta, dict):
        return meta.get(key)
    return None


def box_mean_badge_lines_for(prof_coll) -> tuple[str, ...]:
    """Return the box-mean callout lines for a collection, or ``()``."""
    from sharpmod.analysis.box_mean import box_mean_badge_lines

    return box_mean_badge_lines(
        collection_meta(prof_coll, "box_mean"),
        collection_meta(prof_coll, "box_mean_members"))


def draw_box_mean_badge(widget, lines, qtcore, qtgui, painter):
    """Draw the box-mean callout inside the top-right of the plot.

    Top-right because on a skew-T that corner is warm air at low pressure: no
    real sounding reaches it, so the callout cannot bury a trace, a parcel path,
    the level labels down the left edge, or the isotherm labels along the bottom.

    Returns the rect used, or ``None`` when it did not fit. Not fitting is a
    real outcome worth handling rather than forcing: the title still names the
    average, and a chip crushed over the data would cost more than it says.
    """
    if not lines:
        return None
    left = float(widget.lpad)
    right = float(widget.brx) + float(getattr(widget, "rpad", 0) or 0)
    top = float(widget.tpad)
    bottom = float(widget.bry)
    plot_w = right - left
    plot_h = bottom - top
    if plot_w <= 0.0 or plot_h <= 0.0:
        return None

    base = getattr(widget, "label_font", None)
    margin = 6.0
    padding = 5.0
    budget = plot_w * _render_api.BOX_MEAN_BADGE_MAX_WIDTH_FRAC
    # Scaled off the plot so the chip keeps its weight on a large export and
    # does not swamp a small GUI pane.
    start_pt = max(_render_api.BOX_MEAN_BADGE_MIN_PT, min(13, int(round(plot_h * 0.026))))

    def measure(head_pt, texts):
        head = qtgui.QFont(base) if base is not None else qtgui.QFont()
        head.setPointSize(head_pt)
        head.setBold(True)
        sub = qtgui.QFont(head)
        sub.setPointSize(max(_render_api.BOX_MEAN_BADGE_MIN_PT, head_pt - 2))
        sub.setBold(False)
        fonts = [head] + [sub] * (len(texts) - 1)
        metrics = [qtgui.QFontMetricsF(font) for font in fonts]
        width = max(m.horizontalAdvance(t) for m, t in zip(metrics, texts))
        height = sum(m.height() for m in metrics)
        return fonts, metrics, width, height

    texts = list(lines)
    chosen = None
    while texts:
        for point in range(start_pt, _render_api.BOX_MEAN_BADGE_MIN_PT - 1, -1):
            fonts, metrics, width, height = measure(point, texts)
            if (width + padding * 2.0 <= budget
                    and height + padding * 2.0 <= plot_h * 0.30):
                chosen = (fonts, metrics, width, height)
                break
        if chosen is not None:
            break
        # Drop the explanatory line before giving up entirely: naming the average
        # is the part that must survive.
        texts = texts[:-1]
    if chosen is None:
        return None

    fonts, metrics, width, height = chosen
    chip_w = width + padding * 2.0
    chip_h = height + padding * 2.0
    chip = qtcore.QRectF(
        right - margin - chip_w, top + margin, chip_w, chip_h)

    fill = qtgui.QColor(_render_api.BOX_MEAN_BADGE_FILL)
    if not fill.isValid():
        return None
    painter.setBrush(qtgui.QBrush(fill))
    edge = qtgui.QColor(getattr(widget, "bg_color", None) or "#000000")
    painter.setPen(qtgui.QPen(edge if edge.isValid() else fill, 1.0))
    painter.drawRect(chip)

    # Chosen against the chip's own fill rather than the plot background, since
    # the chip is opaque and amber is a light colour on a dark plot.
    ink = (qtgui.QColor("#000000") if fill.lightnessF() >= 0.5
           else qtgui.QColor("#FFFFFF"))
    painter.setPen(qtgui.QPen(ink))
    y = chip.top() + padding
    for font, metric, text in zip(fonts, metrics, texts):
        painter.setFont(font)
        row = qtcore.QRectF(
            chip.left() + padding, y, width, metric.height())
        painter.drawText(
            row,
            int(qtcore.Qt.AlignHCenter | qtcore.Qt.AlignVCenter
                | qtcore.Qt.TextDontClip),
            text)
        y += metric.height()
    return chip


def _install_skewt_box_mean_badge():
    """Say on the plot itself that a box-mean sounding is an average.

    The title already carries ``box mean of N``, and that has proved too quiet:
    it sits in a line of run and valid times that reads as boilerplate, so the
    page still looks like an ordinary point sounding. Every parcel, index, and
    hodograph on it belongs to an average, which is exactly the thing
    :func:`sharpmod.analysis.box_mean.mean_model_label` set out to make visible.

    Wraps ``plotSkewT.plotData`` so the chip composites over the finished data
    pass, the same hook the frame redraw uses. A point sounding is untouched --
    the callout only appears when the collection says ``box_mean``. Idempotent
    and guarded: a failure here must never cost the render.
    """
    try:
        import sharppy.viz.skew as _skew
        _cls = _skew.plotSkewT
        if getattr(_cls, "_sharpmod_box_mean_badge", False):
            return
        _QtGui = _skew.QtGui
        _QtCore = _skew.QtCore
        _orig = _cls.plotData

        def plotData(self):
            _orig(self)
            try:
                collections = getattr(self, "prof_collections", None) or []
                index = int(getattr(self, "pc_idx", 0) or 0)
                if not 0 <= index < len(collections):
                    return
                lines = box_mean_badge_lines_for(collections[index])
                if not lines:
                    return
                qp = _QtGui.QPainter()
                qp.begin(self.plotBitMap)
                try:
                    qp.setClipping(False)
                    qp.setRenderHint(_QtGui.QPainter.Antialiasing, True)
                    draw_box_mean_badge(self, lines, _QtCore, _QtGui, qp)
                finally:
                    qp.end()
            except Exception:
                pass

        _cls.plotData = plotData
        _cls._sharpmod_box_mean_badge = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


def _install_skewt_isotherm_label_fit():
    """Keep the skew-T bottom isotherm labels inside the widget's bottom pad.

    The vendored ``draw_isotherm_labels`` draws each label ``AlignTop`` from
    ``bry+2``; the taller bold font on the enlarged canvas spills past the
    widget's bottom edge and is clipped. This replaces it with a version that
    centers the label vertically within the bottom-pad slot (``bry`` .. widget
    bottom) and shrinks the font only if it would not fit that slot, so the
    labels are never clipped. Idempotent + guarded (per-call fallback).
    """
    try:
        import sharppy.viz.skew as _skew
        _cls = _skew.backgroundSkewT
        if getattr(_cls, "_sharpmod_isotherm_fit", False):
            return
        _tab = _skew.tab
        _QtGui = _skew.QtGui
        _QtCore = _skew.QtCore
        _orig = _cls.draw_isotherm_labels

        def draw_isotherm_labels(self, t, qp):
            try:
                x1 = (self.originx
                      + self.tmpc_to_pix(t, self.pmax) / self.scale)
                if not (x1 >= self.lpad and x1 <= self.wid):
                    return
                f = _QtGui.QFont(self.label_font)
                f.setBold(True)
                fm = _QtGui.QFontMetrics(f)
                # Shrink only if the glyphs would not fit the bottom pad slot.
                while fm.height() > self.bpad and f.pointSizeF() > 5:
                    f.setPointSizeF(f.pointSizeF() - 1)
                    fm = _QtGui.QFontMetrics(f)
                qp.setFont(f)
                qp.setPen(_QtGui.QPen(self.fg_color))
                qp.setClipping(False)
                rect = _QtCore.QRectF(x1 - 20, self.bry, 40, self.bpad)
                qp.drawText(rect,
                            _QtCore.Qt.TextDontClip | _QtCore.Qt.AlignCenter,
                            _tab.utils.INT2STR(t))
            except Exception:
                _orig(self, t, qp)

        _cls.draw_isotherm_labels = draw_isotherm_labels
        _cls._sharpmod_isotherm_fit = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


def _install_slinky_title_fit():
    """Keep the Storm Slinky title fully inside the widget (no descender clip).

    The vendored ``draw_frame`` places the ``'Storm Slinky'`` title in a slot
    only ``xHeight + fpad`` tall and (on Windows) nudges it down by the font
    descent, so the taller bundled font's descenders (the ``y`` in ``Slinky``)
    spill across the bottom border and are clipped. This replaces
    ``draw_frame`` with a faithful port that draws the same border lines, then
    positions the title in a rect sized to the font's *full* height and clamped
    so the glyphs (descenders included) stay above the bottom border.
    Idempotent + guarded (per-call fallback to the vendored method).
    """
    try:
        import sharppy.viz.slinky as _slinky
        _cls = _slinky.backgroundSlinky
        if getattr(_cls, "_sharpmod_title_fit", False):
            return
        _QtGui = _slinky.QtGui
        _QtCore = _slinky.QtCore
        _orig = _cls.draw_frame

        def draw_frame(self, qp):
            try:
                pen = _QtGui.QPen(self.fg_color, 2, _QtCore.Qt.SolidLine)
                qp.setPen(pen)
                qp.setFont(self.title_font)
                # Border lines (unchanged from the vendored method).
                qp.drawLine(self.tlx, self.tly, self.brx, self.tly)
                qp.drawLine(self.brx, self.tly, self.brx, self.bry)
                qp.drawLine(self.brx, self.bry, self.tlx, self.bry)
                qp.drawLine(self.tlx, self.bry, self.tlx, self.tly)
                # Title: size the slot to the font's full height and clamp so
                # the descenders stay above the bottom border (no clip).
                fm = _QtGui.QFontMetrics(self.title_font)
                h = fm.height()
                yval = self.bry - h - 2
                if yval < self.tly:
                    yval = self.tly
                rect0 = _QtCore.QRect(self.lpad, yval,
                                      max(self.brx - self.lpad, 20), h)
                qp.setClipping(False)
                qp.drawText(rect0,
                            _QtCore.Qt.TextDontClip | _QtCore.Qt.AlignLeft,
                            'Storm Slinky')
            except Exception:
                _orig(self, qp)

        _cls.draw_frame = draw_frame
        _cls._sharpmod_title_fit = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


# --- Chart enlargement (skew-T + hodograph) --------------------------------
# The vendored SPCWidget lays the skew-T out at grid cell (0,0,3,1) beside the
# upper-right panel (0,1,3,1) with the index-table band below at (3,0,1,2), and
# never sets stretch factors -- so Qt sizes the panels from content hints and
# the table band claims more vertical room than the charts need. Setting
# stretch factors on the OUTER ``grid`` alone gives the skew-T and the entire
# upper-right (hodograph) panel the majority of the window: the chart rows
# dominate the table band vertically, and the two chart columns split the
# width. The inner ``grid2`` is deliberately left untouched -- adding stretch
# there starves the left wind-speed / temperature-advection strips (columns)
# and the bottom storm-slinky / theta-e / SR-wind insets (rows). The hodograph
# already occupies 24/29 columns and 8/11 rows of ``grid2``, so it scales up
# proportionally as the enlarged panel grows, with its neighbors kept legible.
SKEWT_COL_STRETCH = int(os.environ.get("SKEWT_COL_STRETCH", "6"))
URPANEL_COL_STRETCH = int(os.environ.get("URPANEL_COL_STRETCH", "6"))
CHART_ROW_STRETCH = int(os.environ.get("CHART_ROW_STRETCH", "10"))
TEXT_ROW_STRETCH = int(os.environ.get("TEXT_ROW_STRETCH", "3"))
# Column stretch WITHIN the upper-right ``grid2`` (columns only -- never rows,
# which would squeeze the bottom storm-slinky / theta-e / SR-wind insets). The
# left wind-speed strip spans cols 0-2, the inferred-temperature-advection
# strip spans cols 3-4, and the hodograph + bottom insets span cols 5-28.
# These widen the two narrow left strips (the vendored content-hint widths left
# the temp-advection strip only ~50 px, cramping its title) while keeping the
# hodograph dominant.
SPEED_STRIP_COL_STRETCH = int(os.environ.get("SPEED_STRIP_COL_STRETCH", "2"))
ADV_STRIP_COL_STRETCH = int(os.environ.get("ADV_STRIP_COL_STRETCH", "3"))
HODO_COL_STRETCH = int(os.environ.get("HODO_COL_STRETCH", "2"))
# Absolute canvas growth (px) so the stretch-enlarged skew-T and hodograph have
# real room to grow without squeezing their neighbor strips/insets.
CANVAS_GROW_W = int(os.environ.get("CANVAS_GROW_W", "280"))
CANVAS_GROW_H = int(os.environ.get("CANVAS_GROW_H", "200"))

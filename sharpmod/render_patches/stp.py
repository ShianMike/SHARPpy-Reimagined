"""Stp adjustments for headless sounding renders."""

from __future__ import annotations

from sharpmod.viz import colors
from sharpmod.render_patches.palette import _semantic_qcolor
from sharpmod.viz.unit_text import apply_render_font_quality
import os
from sharpmod.rendering import cli as _render_api


_stp_condense_installed = False


def _install_title_top():
    """Nudge the skew-T title up by drawing it at ``TITLE_TOP`` instead of y=2.

    Overrides ``plotSkewT.drawTitles`` with a copy that uses the configurable
    top offset; on any error it falls back to the vendored method so the render
    never breaks. Idempotent.
    """
    if _render_api.TITLE_TOP == 2:
        return
    try:
        import sharppy.viz.skew as _skew_mod
        _cls = _skew_mod.plotSkewT
        if getattr(_cls, "_sharpmod_title_top", False):
            return
        _orig = _cls.drawTitles
        _QtCore = _skew_mod.QtCore
        _QtGui = _skew_mod.QtGui
        _top = _render_api.TITLE_TOP

        def drawTitles(self, qp):
            try:
                cur_dt = self.prof_collections[self.pc_idx].getCurrentDate()
                idxs, titles = list(zip(*[
                    (idx, self.getPlotTitle(pc))
                    for idx, pc in enumerate(self.prof_collections)
                    if pc.getCurrentDate() == cur_dt or self.all_observed]))
                titles = list(titles)
                main_title = titles.pop(idxs.index(self.pc_idx))
                qp.setClipping(False)
                qp.setFont(self.title_font)
                metrics = _QtGui.QFontMetrics(self.title_font)
                # The whole width between the pads. The vendored layout reserved
                # a 150 px stub and then drew with TextDontClip, so a title long
                # enough to matter always spilled out of it.
                right_pad = getattr(self, "rpad", self.lpad)
                usable = max(50, self.width() - self.lpad - right_pad)
                # The vendored title_height can be shorter than the bundled
                # font needs, and the rect is what bounds the glyphs, so the
                # font's own height is the floor. Without this the ascenders and
                # the degree signs get sliced off.
                line = max(int(self.title_height), int(metrics.height()))

                def draw(row, text, align, color):
                    qp.setPen(_QtGui.QPen(color, 1, _QtCore.Qt.SolidLine))
                    rect = _QtCore.QRect(
                        self.lpad, _top + row * line, usable, line)
                    # Elided so two titles cannot overwrite each other, and
                    # TextDontClip so the rect positions the text without ever
                    # cropping it. Horizontal fit is the elide's job; vertical
                    # fit is nobody's business to crop.
                    qp.drawText(
                        rect,
                        align | _QtCore.Qt.AlignVCenter
                        | _QtCore.Qt.TextDontClip,
                        metrics.elidedText(
                            text, _QtCore.Qt.ElideRight, usable))

                draw(0, main_title, _QtCore.Qt.AlignLeft, self.fg_color)
                bg = 0
                for row, title in enumerate(titles, start=1):
                    # Every additional sounding gets its own line. Starting this
                    # enumeration at 0 put the second sounding's title on the
                    # focused one's baseline, which is what drew them on top of
                    # each other whenever two soundings shared a valid time.
                    draw(row, title, _QtCore.Qt.AlignRight,
                         _QtGui.QColor(self.background_colors[bg]))
                    bg = (bg + 1) % len(self.background_colors)
            except Exception:
                _orig(self, qp)

        _cls.drawTitles = drawTitles
        _cls._sharpmod_title_top = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


def _install_stp_condense():
    """Condense every font built inside the vendored Effective Layer STP
    widget so the wider bundled font stops overflowing its Helvetica-tuned
    fixed layout.

    ``sharppy.viz.stp`` builds ALL of its fonts inline via ``QtGui.QFont(...)``
    -- the y-axis ticks, the title, the box text and the plot text -- so a
    per-attribute tweak would miss the axis/title fonts. Instead we swap the
    module's ``QtGui`` reference for a thin proxy whose ``QFont`` applies a
    condensing ``setStretch(STP_FONT_STRETCH)`` and delegates everything else
    to the real ``QtGui``. Installed before the STP widget is constructed, so
    both its stored and inline fonts are condensed. Guarded + at most once.
    """
    global _stp_condense_installed
    if _stp_condense_installed:
        return
    stretch = _render_api.STP_FONT_STRETCH
    if not stretch or stretch == 100:
        return
    try:
        import sharppy.viz.stp as _stp_mod
        _real = _stp_mod.QtGui
        _BaseFont = _real.QFont  # already the forced-family QFont subclass

        class _CondensedFont(_BaseFont):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                try:
                    self.setStretch(stretch)
                except Exception:
                    pass

        class _QtGuiProxy:
            QFont = _CondensedFont

            def __getattr__(self, name):
                return getattr(_real, name)

        _stp_mod.QtGui = _QtGuiProxy()
        _stp_condense_installed = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


def _install_stp_bottom_margin():
    """Give the Effective Layer STP graphic a small bottom margin.

    The vendored widget centres its x-axis labels at ``bry + bpad`` which
    equals ``height - bpad`` -- nearly flush with the bottom edge. Wrapping
    ``backgroundSTP.initUI`` to enlarge ``bpad`` (and recompute ``hgt`` /
    ``bry``) shifts the plot and its labels up, opening a margin below.
    ``plotSTP`` inherits ``initUI``, so both the background and data layers
    use the new geometry. Idempotent + guarded.
    """
    if _render_api.STP_BOTTOM_MARGIN <= 0:
        return
    try:
        import sharppy.viz.stp as _stp_mod
        _cls = _stp_mod.backgroundSTP
        if getattr(_cls, "_sharpmod_margin", False):
            return
        _orig_initUI = _cls.initUI
        _extra = _render_api.STP_BOTTOM_MARGIN

        def initUI(self):
            _orig_initUI(self)
            try:
                self.bpad = self.bpad + _extra
                self.hgt = self.size().height() - self.bpad
                self.bry = self.hgt - self.bpad
                # Shrink the inline draw-time fonts (y-ticks + EF x-axis
                # labels) without touching the stored title/box fonts.
                if _render_api.STP_LABEL_SCALE and _render_api.STP_LABEL_SCALE != 1.0:
                    self.font_ratio = self.font_ratio * _render_api.STP_LABEL_SCALE
                # Clear first: the original initUI already drew the frame at
                # the old geometry; redraw on a blank bitmap to avoid a
                # doubled (ghosted) render.
                self.plotBitMap.fill(self.bg_color)
                self.plotBackground()
            except Exception:
                pass

        _cls.initUI = initUI
        _cls._sharpmod_margin = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


# Scale factor for the Effective Layer STP "Prob EF2+ torn with supercell"
# box font + row height. The vendored box sizes its font to the widget height
# (``height * 0.0512``), so on the enlarged canvas the box grows large; this
# shrinks the box text and its per-row height so the box is more compact.
STP_BOX_SCALE = float(os.environ.get("STP_BOX_SCALE", "0.8"))


def _install_stp_box_shrink():
    """Shrink the Effective Layer STP prob box's font + row height.

    Wraps ``backgroundSTP.initUI`` to rebuild ``box_font`` at
    :data:`STP_BOX_SCALE` of its computed size and recompute ``box_metrics`` /
    ``box_height`` (which drives the per-row spacing and thus the overall box
    height), then repaints. Copying the existing font preserves the condense
    stretch applied by :func:`_install_stp_condense`. Idempotent + guarded.
    """
    if STP_BOX_SCALE <= 0 or STP_BOX_SCALE >= 1.0:
        return
    try:
        import sharppy.viz.stp as _stp_mod
        _cls = _stp_mod.backgroundSTP
        if getattr(_cls, "_sharpmod_box_shrink", False):
            return
        _orig = _cls.initUI
        scale = STP_BOX_SCALE

        def initUI(self):
            _orig(self)
            try:
                _QtGui = _stp_mod.QtGui
                f = _QtGui.QFont(self.box_font)
                ps = f.pointSizeF()
                if ps > 0:
                    f.setPointSizeF(ps * scale)
                    self.box_font = f
                    self.box_metrics = _QtGui.QFontMetrics(f)
                    self.box_height = self.box_metrics.xHeight() + self.textpad
                    # Redraw on a blank bitmap so the smaller box replaces the
                    # larger one the vendored initUI already drew.
                    self.plotBitMap.fill(self.bg_color)
                    self.plotBackground()
            except Exception:
                pass

        _cls.initUI = initUI
        _cls._sharpmod_box_shrink = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


def _install_stp_label_rename():
    """Shorten the Effective Layer STP graphic's ``NONTOR`` x-axis label to
    ``NON``.

    The vendored STP widget reads its x-tick labels from
    ``sharppy.databases.inset_data.stpData()`` at draw time, so wrapping that
    function rewrites the label without touching the read-only vendored file.
    Idempotent + guarded.
    """
    try:
        import sharppy.databases.inset_data as _ins
        _orig = _ins.stpData
        if getattr(_orig, "_sharpmod_wrapped", False):
            return

        def stpData(*a, **k):
            d = _orig(*a, **k)
            try:
                xt = d.get("stp_xtexts")
                if xt:
                    d["stp_xtexts"] = ["NON" if t == "NONTOR" else t
                                       for t in xt]
            except Exception:
                pass
            return d

        stpData._sharpmod_wrapped = True
        _ins.stpData = stpData
    except Exception:  # pragma: no cover - registry reports install failure
        raise


# Per-EF-category colors for the Effective Layer STP graphic's x-axis labels.
# EF4+ -> pink, EF3 -> red, EF2 -> yellow, EF1 -> cyan; EF0 and NON keep the
# default foreground. Keyed on the (possibly renamed) x-tick label text.
STP_XLABEL_COLORS = {
    "EF4+": "#FF00FF",   # pink
    "EF3": "#FF0000",    # red
    "EF2": "#FFA500",    # orange
    "EF1": "#FFFF00",    # yellow
    "EF0": "#3399FF",    # blue
}

STP_XLABEL_ROLES = {
    "EF4+": "tornado_ef4",
    "EF3": "tornado_ef3",
    "EF2": "orange",
    "EF1": "yellow",
    "EF0": "blue",
}


def _install_stp_xlabel_colors():
    """Color the Effective Layer STP graphic's EF-category labels + boxes.

    The vendored ``backgroundSTP.draw_frame`` draws every x-tick label
    (``EF4+ .. NONTOR``) in the plain foreground color and every per-category
    box-and-whisker in a single green (``box_color``). This *fully replaces*
    ``draw_frame`` with a faithful port that instead draws each EF category's
    box-and-whisker AND its x-axis label directly in the category's documented
    color (:data:`STP_XLABEL_COLORS`): EF4+ pink, EF3 red, EF2 orange, EF1
    yellow, EF0 blue. ``NONTOR`` (renamed ``NON``) has no scale color, so its
    box keeps the vendored green and its label the foreground.

    Drawing the boxes in their color from the start -- rather than repainting
    over the vendored green -- means no green ever shows through behind the
    recolored whiskers. Label text is read from the (wrapped) ``stpData`` so the
    ``NONTOR -> NON`` rename still applies. Idempotent + fully guarded so a
    failure leaves the vendored render untouched.
    """
    try:
        import numpy as _np
        import sharppy.viz.stp as _stp_mod
        import sharppy.databases.inset_data as _ins
        _cls = _stp_mod.backgroundSTP
        if getattr(_cls, "_sharpmod_xcolors", False):
            return
        _QtGui = _stp_mod.QtGui
        _QtCore = _stp_mod.QtCore
        _orig = _cls.draw_frame

        def _draw_box(qp, cx, width, row):
            # Vendored box-and-whisker geometry: lower whisker, box
            # top/bottom/sides, median, upper whisker.
            wl, bb, med, bt, wh = (float(row[0]), float(row[1]),
                                   float(row[2]), float(row[3]), float(row[4]))
            hw = width / 2.
            qp.drawLine(_QtCore.QPointF(cx, wl), _QtCore.QPointF(cx, bb))
            qp.drawLine(_QtCore.QPointF(cx - hw, bt), _QtCore.QPointF(cx + hw, bt))
            qp.drawLine(_QtCore.QPointF(cx - hw, bb), _QtCore.QPointF(cx + hw, bb))
            qp.drawLine(_QtCore.QPointF(cx - hw, bb), _QtCore.QPointF(cx - hw, bt))
            qp.drawLine(_QtCore.QPointF(cx + hw, bb), _QtCore.QPointF(cx + hw, bt))
            qp.drawLine(_QtCore.QPointF(cx - hw, med), _QtCore.QPointF(cx + hw, med))
            qp.drawLine(_QtCore.QPointF(cx, bt), _QtCore.QPointF(cx, wh))

        def draw_frame(self, qp):
            try:
                data = _ins.stpData()

                # Title.
                qp.setPen(_QtGui.QPen(self.fg_color, 2, _QtCore.Qt.SolidLine))
                qp.setFont(self.plot_font)
                qp.drawText(
                    _QtCore.QRectF(0, 5, self.brx, self.plot_height),
                    _QtCore.Qt.TextDontClip | _QtCore.Qt.AlignCenter,
                    'Effective Layer STP (with CIN)')

                # Y-axis gridlines + tick labels.
                ytick_fontsize = round(self.font_ratio * self.hgt) + 1
                qp.setFont(_QtGui.QFont('Helvetica', ytick_fontsize))
                ytexts = data['stp_ytexts']
                for yt in ytexts:
                    tick_pxl = self.stp_to_pix(int(yt))
                    qp.setPen(_QtGui.QPen(self.line_color, 1, _QtCore.Qt.DashLine))
                    qp.drawLine(_QtCore.QPointF(self.tlx, tick_pxl),
                                _QtCore.QPointF(self.brx, tick_pxl))
                    qp.setPen(_QtGui.QPen(self.fg_color, 1, _QtCore.Qt.SolidLine))
                    qp.drawText(
                        _QtCore.QRectF(self.tlx, tick_pxl - ytick_fontsize / 2.,
                                       20, ytick_fontsize),
                        _QtCore.Qt.TextDontClip | _QtCore.Qt.AlignCenter, yt)

                # Per-category box-and-whisker + x label, colored by EF scale.
                ef = self.stp_to_pix(data['ef'])
                xtexts = data['stp_xtexts']
                width = self.brx / 14
                spacing = self.brx / 7
                center = _np.arange(spacing, self.brx, spacing)
                qp.setFont(_QtGui.QFont(
                    'Helvetica', round(self.font_ratio * self.hgt)))
                for i in range(ef.shape[0]):
                    if i >= len(center):
                        break
                    text = xtexts[i] if i < len(xtexts) else ""
                    hexc = STP_XLABEL_COLORS.get(text)
                    role = STP_XLABEL_ROLES.get(text)
                    themed = (
                        _semantic_qcolor(self, role, override=hexc)
                        if role is not None else None
                    )
                    box_col = themed if themed is not None else self.box_color
                    lbl_col = themed if themed is not None else self.fg_color
                    cx = float(center[i])
                    qp.setPen(_QtGui.QPen(box_col, 2, _QtCore.Qt.SolidLine))
                    _draw_box(qp, cx, width, ef[i])
                    qp.setPen(_QtGui.QPen(lbl_col, 1, _QtCore.Qt.SolidLine))
                    qp.drawText(
                        _QtCore.QRectF(cx - width / 2.,
                                       self.bry + round(self.bpad / 2),
                                       width, self.bpad),
                        _QtCore.Qt.TextDontClip | _QtCore.Qt.AlignCenter, text)
            except Exception:
                # Fall back to the vendored frame on any failure.
                _orig(self, qp)

        _cls.draw_frame = draw_frame
        _cls._sharpmod_xcolors = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


def _install_stp_prob_box_spacing():
    """Fix the vertical spacing in the Effective Layer STP conditional-prob box.

    The vendored ``plotSTP.draw_box`` advances the two header rows by
    ``box_height + 1`` but then draws the divider rule at ``y1 - 1`` -- flush
    against the first data row ("based on CAPE") -- and advances the six data
    rows by only ``box_height`` (one pixel tighter than the headers). The
    result is an unbalanced layout: a full line-gap above the divider and none
    below it, with the data rows packed slightly closer than the header.

    This replaces ``draw_box`` with a port that (1) uses one consistent row
    height everywhere, (2) opens a symmetric gap around the divider so the
    first data row is no longer cramped against it, and (3) sizes the box from
    the actual laid-out content so the bottom border still hugs the last row.
    Idempotent + fully guarded: any failure leaves the vendored method intact.
    """
    try:
        import platform as _platform
        import sharppy.viz.stp as _stp_mod
        _cls = _stp_mod.plotSTP
        if getattr(_cls, "_sharpmod_box_spacing", False):
            return
        _QtGui = _stp_mod.QtGui
        _QtCore = _stp_mod.QtCore
        _tab = _stp_mod.tab
        _orig = _cls.draw_box

        def draw_box(self, qp):
            qp.begin(self.plotBitMap)
            try:
                width = self.brx / 14.
                top_y = self.stp_to_pix(11.)

                # Size the box from its actual content rather than stretching to
                # the inset's right edge, so the wide right-hand whitespace is
                # removed. The value column sits just past the longest label.
                _box_font = _QtGui.QFont(self.box_font)
                if _render_api.USE_CUSTOM_FONT:
                    _box_font.setFamily(_render_api.FONT_FAMILY)
                apply_render_font_quality(_box_font)
                _fm = _QtGui.QFontMetrics(_box_font)
                _adv = getattr(_fm, "horizontalAdvance", None) or _fm.width
                _labels = ['based on CAPE:', 'based on LCL:', 'based on ESRH:',
                           'based on EBWD:', 'based on STPC:',
                           'based on STP_fixed:']
                _headers = ['Prob EF2+ torn with supercell',
                            'Sample CLIMO = .15 sigtor']
                label_w = max(_adv(t) for t in _labels)
                col_gap = max(12, _adv('  '))
                val_w = _adv('0.00')
                content_w = max(label_w + col_gap + val_w,
                                max(_adv(t) for t in _headers))
                # The Streamwiseness inset now shares the former STP column.
                # Fit this long-label box to the available right half instead
                # of letting its height-derived font clip at the new width.
                available_w = max(40., self.brx - width * 7 - 10.)
                if content_w + 8 > available_w:
                    point_size = _box_font.pointSizeF()
                    if point_size > 0:
                        scale = available_w / float(content_w + 8)
                        _box_font.setPointSizeF(max(5.0, point_size * scale * .96))
                        _fm = _QtGui.QFontMetrics(_box_font)
                        _adv = (getattr(_fm, "horizontalAdvance", None)
                                or _fm.width)
                        label_w = max(_adv(t) for t in _labels)
                        col_gap = max(8, _adv('  '))
                        val_w = _adv('0.00')
                        content_w = max(
                            label_w + col_gap + val_w,
                            max(_adv(t) for t in _headers),
                        )
                # Anchor the box against the inset's right edge (but never left
                # of the mid-line, so it can't overlap the EF box-and-whisker
                # plot on the left half).
                right_x = self.brx - 5.
                left_x = max(width * 7, right_x - (content_w + 8))

                # One consistent row height for both header and data rows.
                box_height = _fm.xHeight() + self.textpad
                row_h = box_height + 1
                if _platform.system() == "Windows":
                    row_h += _fm.descent()

                # Symmetric breathing room around the divider rule so the first
                # data row is no longer flush against it.
                div_gap = max(3, int(round(row_h * 0.4)))

                # 2 header rows + divider gap + 6 data rows, plus top/bottom pad.
                bot_y = top_y + 2 + 8 * row_h + div_gap + 2

                ## fill the box with a black background
                brush = _QtGui.QBrush(self.bg_color, _QtCore.Qt.SolidPattern)
                pen = _QtGui.QPen(self.bg_color, 0, _QtCore.Qt.SolidLine)
                qp.setPen(pen)
                qp.setBrush(brush)
                qp.drawRect(left_x, top_y, right_x - left_x, bot_y - top_y)
                ## draw the borders of the box
                pen = _QtGui.QPen(self.fg_color, 2, _QtCore.Qt.SolidLine)
                qp.setPen(pen)
                qp.setBrush(_QtGui.QBrush(_QtCore.Qt.NoBrush))
                qp.drawLine(left_x, top_y, right_x, top_y)
                qp.drawLine(left_x, bot_y, right_x, bot_y)
                qp.drawLine(left_x, top_y, left_x, bot_y)
                qp.drawLine(right_x, top_y, right_x, bot_y)

                qp.setFont(_box_font)
                text_w = right_x - left_x - 3
                x1 = left_x + 3
                x2 = x1 + label_w + col_gap
                y1 = top_y + 2

                ## header/title rows
                pen = _QtGui.QPen(self.fg_color, 1, _QtCore.Qt.SolidLine)
                qp.setPen(pen)
                _header_font = _render_api._custom_emphasis_font(_box_font)
                self._sharpmod_box_header_font = _header_font
                qp.setFont(_header_font)
                for text in ['Prob EF2+ torn with supercell',
                             'Sample CLIMO = .15 sigtor']:
                    rect = _QtCore.QRectF(x1, y1, text_w, box_height)
                    qp.drawText(
                        rect, _QtCore.Qt.TextDontClip | _QtCore.Qt.AlignLeft, text)
                    y1 += row_h

                ## divider rule, centred in its gap
                div_y = y1 + div_gap / 2.
                qp.drawLine(left_x, div_y, right_x, div_y)
                y1 += div_gap

                ## variable rows
                qp.setFont(_box_font)
                texts = ['based on CAPE:', 'based on LCL:', 'based on ESRH:',
                         'based on EBWD:', 'based on STPC:', 'based on STP_fixed:']
                probs = [self.cape_p, self.lcl_p, self.esrh_p,
                         self.ebwd_p, self.stpc_p, self.stpf_p]
                colors = [self.cape_c, self.lcl_c, self.esrh_c,
                          self.ebwd_c, self.stpc_c, self.stpf_c]
                for text, p, c in zip(texts, probs, colors):
                    qp.setPen(_QtGui.QPen(c, 1, _QtCore.Qt.SolidLine))
                    rect = _QtCore.QRectF(x1, y1, text_w, box_height)
                    rect2 = _QtCore.QRectF(x2, y1, text_w, box_height)
                    qp.drawText(
                        rect, _QtCore.Qt.TextDontClip | _QtCore.Qt.AlignLeft, text)
                    qp.drawText(
                        rect2, _QtCore.Qt.TextDontClip | _QtCore.Qt.AlignLeft,
                        _tab.utils.FLOAT2STR(p, 2))
                    y1 += row_h
            except Exception:
                # Fall back to the vendored box on any failure.
                if qp.isActive():
                    qp.end()
                _orig(self, qp)
                return
            qp.end()

        _cls.draw_box = draw_box
        _cls._sharpmod_box_spacing = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise

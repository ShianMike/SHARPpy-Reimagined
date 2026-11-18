"""Skewt Labels adjustments for headless sounding renders."""

from __future__ import annotations

from sharpmod.render_patches.geometry import _fit_rect_to_skewt_plot
from sharpmod.render_patches.geometry import _layout_skewt_surface_labels
from sharpmod.render_patches.geometry import _skewt_surface_label_rect
from sharpmod.render_patches.geometry import _upsert_skewt_surface_label
from sharpmod.rendering import cli as _render_api


def _install_skewt_mixratio_mask():
    """Draw green mixing-ratio labels directly on the Skew-T grid.

    Size the text rectangles from the active font so each label stays centered
    at the end of its mixing-ratio line.
    """
    try:
        import sharppy.viz.skew as _skew
        _cls = _skew.backgroundSkewT
        if getattr(_cls, "_sharpmod_mixr_mask", False):
            return
        _tab = _skew.tab
        _QtGui = _skew.QtGui
        _QtCore = _skew.QtCore
        _orig = _cls.draw_mixing_ratios

        def draw_mixing_ratios(self, w, pmin, qp):
            try:
                qp.setClipping(True)
                t = _tab.thermo.temp_at_mixrat(w, self.pmax)
                x1 = self.originx + self.tmpc_to_pix(t, self.pmax) / self.scale
                y1 = self.originy + self.pres_to_pix(self.pmax) / self.scale
                t = _tab.thermo.temp_at_mixrat(w, pmin)
                x2 = self.originx + self.tmpc_to_pix(t, pmin) / self.scale
                y2 = self.originy + self.pres_to_pix(pmin) / self.scale
                label = _tab.utils.INT2STR(w)
                qp.setFont(self.in_plot_font)
                fm = _QtGui.QFontMetrics(self.in_plot_font)
                tw = fm.horizontalAdvance(label)
                th = fm.height()
                pad = 1
                rectF = _QtCore.QRectF(
                    x2 - tw / 2.0 - pad, y2 - th - pad,
                    tw + 2 * pad, th + 2 * pad)
                pen = _QtGui.QPen(self.mixr_color, 1, _QtCore.Qt.SolidLine)
                qp.setPen(pen)
                qp.drawLine(int(x1), int(y1), int(x2), int(y2))
                qp.drawText(rectF,
                            _QtCore.Qt.AlignBottom | _QtCore.Qt.AlignCenter,
                            label)
            except Exception:
                _orig(self, w, pmin, _render_api._RectSuppressingPainter(qp))

        _cls.draw_mixing_ratios = draw_mixing_ratios
        _cls._sharpmod_mixr_mask = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


def _install_skewt_sfc_label_mask():
    """Size and collision-pack the skew-T surface trace-value labels.

    The vendored ``plotSkewT.drawTrace`` masks each surface temperature /
    dewpoint / wet-bulb value with a fixed 16x12 px background rect. The wider
    bundled font overflows it, so the surface isobar behind the number bleeds
    through the digit gaps. Independently centering the three corrected masks
    also makes them erase one another when their surface values are close.
    This replaces ``drawTrace`` with a faithful port that sizes masks from font
    metrics, then queues every displayed surface label so ``plotData`` can
    deduplicate repeated profile passes and pack the full set with a real gap
    before drawing every mask and glyph. The trace paths are unchanged.
    Idempotent + guarded (per-call fallback).
    """
    cap = _render_api.SFC_LABEL_MAX_PT
    try:
        import sharppy.viz.skew as _skew
        _cls = _skew.plotSkewT
        if getattr(_cls, "_sharpmod_sfc_mask", False):
            return
        _tab = _skew.tab
        _QtGui = _skew.QtGui
        _QtCore = _skew.QtCore
        _np = _skew.np
        _QPainterPath = _skew.QPainterPath
        _orig = _cls.drawTrace
        _orig_plot_data = _cls.plotData

        def _paint_surface_label(qp, entry, rect):
            qp.setFont(entry["font"])
            qp.setPen(_QtGui.QPen(
                entry["background"], 0, _QtCore.Qt.SolidLine))
            qp.setBrush(_QtGui.QBrush(
                entry["background"], _QtCore.Qt.SolidPattern))
            qp.drawRect(rect)
            qp.setPen(_QtGui.QPen(
                entry["color"], 3, _QtCore.Qt.SolidLine))
            qp.drawText(rect, _QtCore.Qt.AlignCenter, entry["text"])

        def drawTrace(self, data, color, qp, width=3,
                      style=_QtCore.Qt.SolidLine, p=None, stdev=None,
                      label=True):
            try:
                source_data = data
                qp.setClipping(True)
                pen = _QtGui.QPen(_QtGui.QColor(color), width, style)
                qp.setPen(pen)
                qp.setBrush(_QtGui.QBrush(_QtCore.Qt.NoBrush))

                mask1 = data.mask
                if p is not None:
                    mask2 = p.mask
                    pres = p
                else:
                    mask2 = self.pres.mask
                    pres = self.pres
                mask = _np.maximum(mask1, mask2)
                data = data[~mask]
                pres = pres[~mask]
                if stdev is not None:
                    stdev = stdev[~mask]

                path = _QPainterPath()
                x = self.originx + self.tmpc_to_pix(data, pres) / self.scale
                y = self.originy + self.pres_to_pix(pres) / self.scale
                path.moveTo(x[0], y[0])
                for i in range(1, x.shape[0]):
                    path.lineTo(x[i], y[i])
                    if stdev is not None:
                        self.drawSTDEV(pres[i], data[i], stdev[i], color, qp)
                qp.drawPath(path)

                if label is True:
                    qp.setClipping(False)
                    if self.sfc_units == 'Celsius':
                        lbl_val = data[0]
                    else:
                        lbl_val = _tab.thermo.ctof(data[0])
                    lbl_str = _tab.utils.INT2STR(lbl_val)
                    # The three surface values (temp / dewpoint / wet-bulb) sit
                    # close together; at the tall bundled font (~15 pt) their
                    # masks overlap and a later label erases its neighbor. Cap
                    # the point size so all three fit side-by-side.
                    tf = _QtGui.QFont(self.environment_trace_font)
                    if cap > 0 and tf.pointSize() > cap:
                        tf.setPointSize(cap)
                    qp.setFont(tf)
                    fm = _QtGui.QFontMetrics(tf)
                    tw = fm.horizontalAdvance(lbl_str)
                    th = fm.height()
                    pad_x = 1
                    pad_y = 1
                    rect = _skewt_surface_label_rect(
                        self, _QtCore, x[0], y[0],
                        tw + 2 * pad_x, th + 2 * pad_y)
                    entry = {
                        "center_x": float(x[0]),
                        "line_y": float(y[0]),
                        "width": float(rect.width()),
                        "height": float(rect.height()),
                        "text": lbl_str,
                        "font": _QtGui.QFont(tf),
                        "color": _QtGui.QColor(color),
                        "background": _QtGui.QColor(self.bg_color),
                    }
                    primary_sources = (
                        ("wetbulb", getattr(self, "wetbulb", None)),
                        ("temperature", getattr(self, "tmpc", None)),
                        ("dewpoint", getattr(self, "dwpc", None)),
                    )
                    collect = bool(getattr(
                        self, "_sharpmod_collect_sfc_labels", False))
                    primary_key = next((
                        key for key, candidate in primary_sources
                        if candidate is not None
                        and source_data is candidate
                    ), None)
                    if collect:
                        if primary_key is not None:
                            entry["primary_key"] = primary_key
                        # The focused profile is also present in SHARPpy's
                        # ensemble/background pass, and other highlighted
                        # collections are repeated by SHARPpy too. Deduplicate
                        # by the source array identity while keeping the last
                        # occurrence, whose color/width reflects the final
                        # primary or highlighted-profile draw pass.
                        _upsert_skewt_surface_label(
                            self._sharpmod_sfc_label_queue,
                            ("source", id(source_data)),
                            entry,
                        )
                    else:
                        _paint_surface_label(qp, entry, rect)
                    qp.setClipping(True)
            except Exception:
                _orig(self, data, color, qp, width=width, style=style, p=p,
                      stdev=stdev, label=label)

        _missing = object()

        def plotData(self):
            previous_collect = getattr(
                self, "_sharpmod_collect_sfc_labels", _missing)
            previous_queue = getattr(
                self, "_sharpmod_sfc_label_queue", _missing)
            self._sharpmod_collect_sfc_labels = True
            self._sharpmod_sfc_label_queue = []
            try:
                result = _orig_plot_data(self)
                queued = list(self._sharpmod_sfc_label_queue)
            finally:
                if previous_collect is _missing:
                    try:
                        del self._sharpmod_collect_sfc_labels
                    except AttributeError:
                        pass
                else:
                    self._sharpmod_collect_sfc_labels = previous_collect
                if previous_queue is _missing:
                    try:
                        del self._sharpmod_sfc_label_queue
                    except AttributeError:
                        pass
                else:
                    self._sharpmod_sfc_label_queue = previous_queue

            if not queued:
                return result

            rects = _layout_skewt_surface_labels(
                self, _QtCore, queued, gap=4)
            painter = _QtGui.QPainter()
            try:
                if not painter.begin(self.plotBitMap):
                    return result
                painter.setClipping(False)
                painter.setRenderHint(
                    _QtGui.QPainter.Antialiasing, True)
                painter.setRenderHint(
                    _QtGui.QPainter.TextAntialiasing, True)

                # Paint every opaque mask first. Drawing mask/text pairs in
                # sequence lets a later mask erase a neighboring earlier
                # glyph even after their rectangles have been separated.
                for entry, rect in zip(queued, rects):
                    painter.setPen(_QtGui.QPen(
                        entry["background"], 0,
                        _QtCore.Qt.SolidLine))
                    painter.setBrush(_QtGui.QBrush(
                        entry["background"], _QtCore.Qt.SolidPattern))
                    painter.drawRect(rect)
                for entry, rect in zip(queued, rects):
                    painter.setFont(entry["font"])
                    painter.setPen(_QtGui.QPen(
                        entry["color"], 3, _QtCore.Qt.SolidLine))
                    painter.drawText(
                        rect, _QtCore.Qt.AlignCenter, entry["text"])
            except Exception:
                _render_api._LOGGER.exception("skewt.surface_labels.draw_failed")
            finally:
                if painter.isActive():
                    painter.end()
            return result

        _cls.drawTrace = drawTrace
        _cls.plotData = plotData
        _cls._sharpmod_sfc_mask = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


#: Clear space kept between two left-hand skew-T annotations, in pixels.
SKEWT_ANNOTATION_CLEARANCE = 6.0

#: Temperature bounds of the omega meter at 1000 mb. Taken from
#: ``plotSkewT.draw_omega_profile``, which hard-codes -49 and -41.
SKEWT_OMEGA_BOUNDS_C = (-49.0, -41.0)

#: Widest scale label the omega meter prints beside those bounds. The meter
#: positions ``-10`` and ``+10`` with 5 px rects, so both overhang their end.
SKEWT_OMEGA_SCALE_LABEL = "-10"

#: Pixels from ``lpad`` at which ``plotSkewT.draw_height`` starts each height
#: label, after a tick that runs from ``lpad`` itself.
SKEWT_HEIGHT_LABEL_OFFSET = 15.0

#: The height markers ``plotData`` draws on every repaint, from its own list of
#: 0, 1, 3, 6, 9, 12 and 15 km.
SKEWT_HEIGHT_LABELS = ("0 km", "1 km", "3 km", "6 km", "9 km", "12 km",
                       "15 km")

#: Fallback width for the height-label column when no font is available to
#: measure. Wide enough to clear the longest of them at any usual size.
SKEWT_HEIGHT_LABEL_FALLBACK_PX = 40.0


def _skewt_height_label_band(widget, qtgui=None):
    """Return the ``(left, right)`` pixels the height markers occupy.

    Unlike the omega meter this band is never absent: ``plotData`` calls
    ``draw_height`` for seven levels on every repaint, whatever the sounding is.
    Each marker is a tick starting at ``lpad`` with its label 15 px further in,
    and together they form a full-height column against the left edge -- the
    column an annotation placed at ``lpad`` lands on top of.
    """
    left = float(getattr(widget, "lpad", 0))
    width = SKEWT_HEIGHT_LABEL_FALLBACK_PX
    font = getattr(widget, "hght_font", None)
    if qtgui is not None and font is not None:
        try:
            metrics = qtgui.QFontMetrics(font)
            width = max(float(metrics.horizontalAdvance(text))
                        for text in SKEWT_HEIGHT_LABELS)
        except Exception:  # noqa: BLE001 - geometry is advisory
            width = SKEWT_HEIGHT_LABEL_FALLBACK_PX
    return left, left + SKEWT_HEIGHT_LABEL_OFFSET + width


#: Lowest pressure the omega meter draws a bar for, from
#: ``plotSkewT.draw_omega_profile``.
SKEWT_OMEGA_TOP_HPA = 111.0


def _skewt_omega_bar_extent(widget):
    """Return the ``(left, right)`` pixels the omega *bars* actually reach.

    ``draw_omega_profile`` scales each bar by the reported value, so a vertical
    velocity beyond the meter's own +/-10 scale is drawn past the bound rather
    than clipped to it. On a real profile the strongest ascent reaches several
    degrees of skew-T past -41 C -- which is precisely where a neighbouring
    annotation would otherwise be placed, and the bars and a warm-tier lapse
    rate are both red, so the two merge into one another when they meet.
    """
    import numpy as np

    prof = getattr(widget, "prof", None)
    omeg = getattr(prof, "omeg", None)
    pres = getattr(prof, "pres", None)
    if omeg is None or pres is None:
        return None
    try:
        values = np.ma.masked_invalid(np.ma.asarray(omeg, dtype=float))
        levels = np.ma.masked_invalid(np.ma.asarray(pres, dtype=float))
        drawn = (~np.ma.getmaskarray(values)
                 & ~np.ma.getmaskarray(levels)
                 & (levels.filled(-1.0) >= SKEWT_OMEGA_TOP_HPA))
        usable = np.asarray(values.filled(0.0))[drawn]
        if usable.size == 0:
            return None
        edges = [float(widget.omeg_to_pix(float(value) * 10.0))
                 for value in (usable.min(), usable.max())]
    except Exception:  # noqa: BLE001 - geometry is advisory
        return None
    edges = [edge for edge in edges if edge == edge]  # NaN guard
    if not edges:
        return None
    return min(edges), max(edges)


def _skewt_omega_band(widget, qtgui=None):
    """Return the ``(left, right)`` pixels the omega meter occupies, or ``None``.

    ``None`` means the meter is not drawn, which is the case for observed
    soundings -- ``plotSkewT`` sets ``plot_omega`` from whether the collection is
    observed, and an observed sounding carries no vertical velocity.

    Winter markers use this span to avoid the omega meter. The span is wider than the meter's own -49 to -41 bounds. Its
    ``+10``/``-10`` scale labels are drawn from 5 px rectangles and so overhang
    both ends, and the bars themselves can reach further still.
    """
    if not getattr(widget, "plot_omega", False):
        return None
    try:
        left = float(widget.tmpc_to_pix(SKEWT_OMEGA_BOUNDS_C[0], 1000))
        right = float(widget.tmpc_to_pix(SKEWT_OMEGA_BOUNDS_C[1], 1000))
    except Exception:  # noqa: BLE001 - geometry is advisory
        return None
    if left != left or right != right:  # NaN guard
        return None
    overhang = 0.0
    font = getattr(widget, "esrh_font", None)
    if qtgui is not None and font is not None:
        try:
            overhang = float(qtgui.QFontMetrics(font).horizontalAdvance(
                SKEWT_OMEGA_SCALE_LABEL))
        except Exception:  # noqa: BLE001
            overhang = 0.0
    low = min(left, right) - overhang
    high = max(left, right) + overhang
    bars = _skewt_omega_bar_extent(widget)
    if bars is not None:
        low = min(low, bars[0])
        high = max(high, bars[1])
    return low, high


def _skewt_left_gutter(widget, qtgui=None):
    """Leave room for the height markers; omega bars may cross annotations."""
    _, right = _skewt_height_label_band(widget, qtgui)
    return right + SKEWT_ANNOTATION_CLEARANCE


def _skewt_clear_of(rect, blockers, qtcore, right_limit=None,
                    margin=SKEWT_ANNOTATION_CLEARANCE):
    """Return ``rect`` moved right until it clears every blocker by ``margin``.

    Only x moves. For these annotations the vertical position carries the
    meaning -- it is the pressure the value belongs to -- while the column is
    arbitrary, so a collision is resolved by sliding sideways and never by
    sliding up or down.

    Each blocker is grown by ``margin`` before being tested, because a strict
    rect intersection is not the thing that matters visually: two labels one
    pixel apart do not overlap and still read as a single smear.
    """
    result = qtcore.QRectF(rect)
    padded = [
        None if blocker is None
        else qtcore.QRectF(blocker).adjusted(-margin, -margin, margin, margin)
        for blocker in blockers
    ]
    for _ in range(len(padded) + 2):
        moved = False
        for blocker in padded:
            if blocker is None or not result.intersects(blocker):
                continue
            candidate = float(blocker.right())
            if right_limit is not None and \
                    candidate + result.width() > right_limit:
                continue
            result.moveLeft(candidate)
            moved = True
        if not moved:
            break
    return result


#: Slack required beyond the label's own width before the column to the right of
#: the effective-inflow annotation is considered usable.
SKEWT_LAPSE_COLUMN_SLACK = 4.0


def _skewt_trace_left_edge(widget, tab, pbot, ptop, step=10.0):
    """Leftmost temperature/dewpoint pixel across a pressure layer.

    The cold region is only empty up to whichever trace comes first, and that
    varies with the sounding -- a dry profile puts its dewpoint much further
    left than a saturated one. Sampled across the layer rather than at its top
    alone, because the bracket spans the whole depth.

    Returns ``None`` when it cannot be determined, which callers treat as
    "unknown" rather than as "no limit".
    """
    import numpy as np

    prof = getattr(widget, "prof", None)
    if prof is None:
        return None
    try:
        low, high = float(min(pbot, ptop)), float(max(pbot, ptop))
        levels = np.arange(low, high + 1.0, float(step))
        if levels.size == 0:
            return None
        edges = []
        for reader in (tab.interp.temp, tab.interp.dwpt):
            values = np.ma.masked_invalid(
                np.ma.asarray(reader(prof, levels), dtype=float))
            pixels = np.ma.masked_invalid(np.ma.asarray(
                widget.tmpc_to_pix(values, levels), dtype=float))
            usable = np.asarray(pixels.compressed(), dtype=float)
            if usable.size:
                edges.append(float(usable.min()))
    except Exception:  # noqa: BLE001 - geometry is advisory
        return None
    if not edges:
        return None
    return min(edges)


def _skewt_effective_layer_labels(widget, qtcore, qtgui, tab):
    """Return the effective-inflow-layer bracket geometry and label rects.

    Shared by the effective-layer drawing and the max-lapse-rate placement, so
    the lapse-rate label can avoid these without depending on which annotation
    is drawn first. The order genuinely differs between panels: every panel but
    winter draws the lapse rate from inside ``plotData``, while the winter panel
    draws it from a later pass, so a first-come reservation would place the same
    label differently depending on which panel happened to be open.

    Returns ``None`` when there is no effective inflow layer to draw.
    """
    prof = getattr(widget, "prof", None)
    if prof is None:
        return None
    ptop = getattr(prof, "etop", None)
    pbot = getattr(prof, "ebottom", None)
    if not (tab.utils.QC(ptop) and tab.utils.QC(pbot)):
        return None

    x1 = widget.tmpc_to_pix(-20, 1000)
    x2 = widget.tmpc_to_pix(-33, 1000)
    scale = float(getattr(widget, "scale", 1.0)) or 1.0
    originy = float(getattr(widget, "originy", 0.0))
    y1 = originy + widget.pres_to_pix(pbot) / scale
    y2 = originy + widget.pres_to_pix(ptop) / scale

    surface = tab.interp.hght(prof, prof.pres[prof.sfc])
    surface_based = prof.pres[prof.sfc] == pbot
    if surface_based:
        text_bot = "SFC"
    else:
        text_bot = tab.utils.INT2STR(
            tab.interp.hght(prof, pbot) - surface) + "m"
    text_top = tab.utils.INT2STR(
        tab.interp.hght(prof, ptop) - surface) + "m"
    esrh = (prof.left_esrh[0] if getattr(widget, "use_left", False)
            else prof.right_esrh[0])
    text_esrh = tab.utils.INT2STR(esrh) + " m2s2"

    metrics = qtgui.QFontMetrics(widget.esrh_font)
    rect_h = max(float(getattr(widget, "esrh_height", 0)),
                 float(metrics.height()) + 2.0)
    gutter = _skewt_left_gutter(widget, qtgui)

    def _rect(left, top, text):
        """A rect sized to the text, not to an arbitrary floor.

        The vendored 25/50/50 pixel minimums served no purpose: the text is
        left-aligned with ``TextDontClip``, so the rect's width never affected
        where a glyph landed. Carrying them made every collision test pessimistic
        by up to 30 px, which would have spread these three labels much further
        apart than the ink actually needs.
        """
        width = max(float(metrics.horizontalAdvance(str(text))) + 4.0, 12.0)
        # Keep SFC at its usual column even beside the height markers.
        left = float(left) if text == "SFC" else max(float(left), gutter)
        return _fit_rect_to_skewt_plot(
            widget, qtcore, qtcore.QRectF(left, float(top), width, rect_h))

    # The bottom label sits *below* the layer's lower line, which for a
    # surface-based layer is at or under the plot's bottom edge; flip it above
    # the line rather than let it fall out of the box.
    bottom_top = float(y1) + 4.0
    bottom_limit = float(getattr(
        widget, "bry", getattr(widget, "hgt", y1))) - 2.0
    if bottom_top + rect_h > bottom_limit:
        bottom_top = float(y1) - 4.0 - rect_h

    # Preserve the surface label's column; the other values yield if a
    # shallow layer puts them on the same row. An elevated base still yields.
    right_limit = float(getattr(widget, "brx", 0))
    rect_bot = _rect(x2, bottom_top, text_bot)
    rect_top = _rect(x2, y2 - rect_h, text_top)
    if surface_based:
        rect_top = _skewt_clear_of(
            rect_top, (rect_bot,), qtcore, right_limit=right_limit)
    rect_esrh = _skewt_clear_of(
        _rect(x1 - 15.0, y2 - rect_h, text_esrh),
        (rect_top, rect_bot) if surface_based else (rect_top,), qtcore,
        right_limit=right_limit)
    if not surface_based:
        rect_bot = _skewt_clear_of(
            rect_bot, (rect_top, rect_esrh), qtcore, right_limit=right_limit)

    return {
        "x1": x1,
        "y1": y1,
        "y2": y2,
        "rect_bot": rect_bot,
        "rect_top": rect_top,
        "rect_esrh": rect_esrh,
        "text_bot": text_bot,
        "text_top": text_top,
        "text_esrh": text_esrh,
        "rect_h": rect_h,
    }

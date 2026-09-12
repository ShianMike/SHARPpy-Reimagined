"""Hodograph render patch implementations."""

from __future__ import annotations


def install_hodo_label_fit(
    *,
    HODO_LABEL_MAX_PT,
    HODO_READOUT_MAX_PT,
    _LOGGER,
    _fit_font_to_rect,
    _fit_rect_to_hodo,
    _place_hodo_annotation_rect,
    _semantic_qcolor,
):
    """Cap hodo fonts and place vector labels from their measured bounds.

    The vendored ``backgroundHodo.initUI`` sizes ``label_font`` and
    ``readout_font`` with a height-proportional term (``self.hgt * 0.0045``)
    that grows too large on bigger displays. Meanwhile the ``drawSMV`` and
    ``paintEvent`` readout rects are a fixed 55x12 / 55x16 px regardless of
    actual font size, so the text clips or overflows.

    This patch:
    1. Caps ``label_font`` to :data:`HODO_LABEL_MAX_PT` and ``readout_font``
       to :data:`HODO_READOUT_MAX_PT` after ``initUI`` runs.
    2. Overrides ring labels so three-digit rings (``100``) use font-metrics
       width and clamp inside the hodograph frame.
    3. Overrides ``drawSMV`` to size the RM/LM label rects from font metrics.
    4. Overrides ``paintEvent`` readout to size the cursor rect from metrics.
    5. Sizes the Corfidi labels from their text.
    6. Centers the LCL-EL mean-wind square and places its measured value label
       on a free side with a visible marker gap.

    Idempotent + fully guarded.
    """
    try:
        import sharppy.viz.hodo as _hodo_mod
        import sharppy.sharptab as _tab
        import numpy as _np

        _bg = _hodo_mod.backgroundHodo
        _plot = _hodo_mod.plotHodo
        if getattr(_plot, "_sharpmod_label_fit", False):
            return
        _QtGui = _hodo_mod.QtGui
        _QtCore = _hodo_mod.QtCore
        try:
            from qtpy.QtCore import QPointF as _QPointF
            from qtpy.QtCore import QPoint as _QPoint
        except Exception:
            _QPointF = _QtCore.QPointF
            _QPoint = _QtCore.QPoint

        label_cap = int(HODO_LABEL_MAX_PT)
        readout_cap = int(HODO_READOUT_MAX_PT)

        # --- 1. Cap fonts after initUI (called at construction AND on resize) ---
        _orig_initUI = _bg.initUI

        def initUI(self):
            _orig_initUI(self)
            try:
                if label_cap > 0:
                    pt = self.label_font.pointSize()
                    if pt < 0:
                        pt = self.label_font.pixelSize()
                    if pt > label_cap:
                        self.label_font = _QtGui.QFont(
                            self.label_font.family(), label_cap
                        )
                        self.label_font.setBold(True)
                        self.label_metrics = _QtGui.QFontMetrics(self.label_font)
                        self.label_height = self.label_metrics.xHeight() + 5
                if readout_cap > 0:
                    pt = self.readout_font.pointSize()
                    if pt < 0:
                        pt = self.readout_font.pixelSize()
                    if pt > readout_cap:
                        self.readout_font = _QtGui.QFont(
                            self.readout_font.family(), readout_cap
                        )
                        self.readout_font.setBold(True)
            except Exception:
                pass

        _bg.initUI = initUI

        # Reset annotation occupancy once per data pass.  The dynamic label
        # placers below append their final text/marker rectangles so later
        # annotations can choose a free side instead of painting on top.
        _orig_plot_data = _plot.plotData

        def plotData(self):
            self._sharpmod_hodo_annotation_rects = []
            return _orig_plot_data(self)

        _plot.plotData = plotData

        # --- 2. Override ring labels to use font-metrics-sized rects ---
        _orig_ring = _bg.draw_ring

        def draw_ring(self, spd, qp):
            try:
                color = self.isotach_color
                _uu, vv = _tab.utils.vec2comp(0, spd)
                radius = abs(float(vv) * float(self.scale))
                center = _QtCore.QPointF(self.centerx, self.centery)

                pen = _QtGui.QPen(_QtGui.QColor(color), 1)
                pen.setStyle(_QtCore.Qt.DashLine)
                qp.setPen(pen)
                qp.drawEllipse(center, radius, radius)

                text = _tab.utils.INT2STR(spd)
                avail_w = max(1, int(float(self.brx) - float(self.tlx) - 4))
                avail_h = max(1, int(float(self.bry) - float(self.tly) - 4))
                font = _fit_font_to_rect(
                    _QtGui,
                    self.label_font,
                    text,
                    avail_w,
                    18,
                    max_pt=label_cap,
                    min_pt=5,
                )
                qp.setFont(font)
                fm = _QtGui.QFontMetrics(font)
                width = min(avail_w, max(15, fm.horizontalAdvance(text) + 6))
                height = min(avail_h, max(15, fm.height() + 2))

                offset = 5
                pad = 2.0
                left_limit = float(getattr(self, "tlx", 0)) + pad
                right_limit = float(getattr(self, "brx", self.wid)) - pad
                top_limit = float(getattr(self, "tly", 0)) + pad
                bottom_limit = float(getattr(self, "bry", self.hgt)) - pad

                # Emit each of the four axis labels ONLY when its natural
                # position lies fully inside the frame. Clamping an out-of-range
                # label back onto the frame edge (the previous behavior) piled
                # every too-large ring's number onto the same spot, so the
                # numbers merged -- worst on the shorter vertical axis after the
                # zoom-out. Skipping them instead keeps the labels clean.
                rects = []
                top_y = self.centery - radius - offset
                if top_y >= top_limit:  # above origin
                    rects.append(
                        _QtCore.QRectF(self.centerx + offset, top_y, width, height)
                    )
                bot_y = self.centery + radius - offset
                if bot_y + height <= bottom_limit:  # below origin
                    rects.append(
                        _QtCore.QRectF(self.centerx + offset, bot_y, width, height)
                    )
                right_x = self.centerx + radius - offset
                if right_x + width <= right_limit:  # right of origin
                    rects.append(
                        _QtCore.QRectF(right_x, self.centery + offset, width, height)
                    )
                left_x = self.centerx - radius - offset
                if left_x >= left_limit:  # left of origin
                    rects.append(
                        _QtCore.QRectF(left_x, self.centery + offset, width, height)
                    )

                for rect in rects:
                    try:
                        qp.fillRect(rect, self.bg_color)
                    except Exception:
                        pass
                qp.setPen(_QtGui.QPen(self.fg_color))
                for rect in rects:
                    qp.drawText(rect, _QtCore.Qt.AlignCenter, text)
            except Exception:
                _orig_ring(self, spd, qp)

        _bg.draw_ring = draw_ring

        # --- 3. Override drawSMV to use font-metrics-sized rects ---
        _orig_smv = _plot.drawSMV

        def drawSMV(self, qp):
            try:
                # Duplicates vendored logic but with dynamic rect sizing.
                penwidth = 1
                pen = _QtGui.QPen(self.fg_color, penwidth)
                pen.setStyle(_QtCore.Qt.SolidLine)
                qp.setPen(pen)

                rstu, rstv, lstu, lstv = self.srwind
                bkru, bkrv, bklu, bklv = self.prof.bunkers
                if not _tab.utils.QC(rstu) or not _tab.utils.QC(lstu):
                    return

                # Bunkers location markers (+)
                ruu_b, rvv_b = self.uv_to_pix(bkru, bkrv)
                luu_b, lvv_b = self.uv_to_pix(bklu, bklv)
                center_rm_b = _QPointF(ruu_b, rvv_b)
                center_lm_b = _QPointF(luu_b, lvv_b)
                qp.drawLine(center_rm_b - _QPoint(2, 0), center_rm_b + _QPoint(2, 0))
                qp.drawLine(center_rm_b - _QPoint(0, 2), center_rm_b + _QPoint(0, 2))
                qp.drawLine(center_lm_b - _QPoint(2, 0), center_lm_b + _QPoint(2, 0))
                qp.drawLine(center_lm_b - _QPoint(0, 2), center_lm_b + _QPoint(0, 2))

                # User storm motion vectors (circles)
                ruu, rvv = self.uv_to_pix(rstu, rstv)
                luu, lvv = self.uv_to_pix(lstu, lstv)
                center_rm = _QPointF(ruu, rvv)
                center_lm = _QPointF(luu, lvv)
                qp.drawEllipse(center_rm, 5, 5)
                qp.drawEllipse(center_lm, 5, 5)

                # Effective inflow layer lines
                ptop, pbottom = self.ptop, self.pbottom
                if _tab.utils.QC(ptop) and _tab.utils.QC(pbottom):
                    utop, vtop = _tab.interp.components(self.prof, ptop)
                    ubot, vbot = _tab.interp.components(self.prof, pbottom)
                    uutop, vvtop = self.uv_to_pix(utop, vtop)
                    uubot, vvbot = self.uv_to_pix(ubot, vbot)
                    pen = _QtGui.QPen(self.eff_inflow_color, penwidth)
                    pen.setStyle(_QtCore.Qt.SolidLine)
                    qp.setPen(pen)
                    if self.use_left:
                        qp.drawLine(center_lm.x(), center_lm.y(), uubot, vvbot)
                        qp.drawLine(center_lm.x(), center_lm.y(), uutop, vvtop)
                    else:
                        qp.drawLine(center_rm.x(), center_rm.y(), uubot, vvbot)
                        qp.drawLine(center_rm.x(), center_rm.y(), uutop, vvtop)

                # RM / LM text labels -- sized to font metrics
                qp.setFont(self.label_font)
                fm = _QtGui.QFontMetrics(self.label_font)

                rm_spd = self.bunkers_right_vec[1]
                lm_spd = self.bunkers_left_vec[1]
                if self.wind_units == "m/s":
                    rm_spd = _tab.utils.KTS2MS(rm_spd)
                    lm_spd = _tab.utils.KTS2MS(lm_spd)

                rm_text = (
                    _tab.utils.INT2STR(_np.float64(self.bunkers_right_vec[0]))
                    + "/"
                    + _tab.utils.INT2STR(rm_spd)
                    + " RM"
                )
                lm_text = (
                    _tab.utils.INT2STR(_np.float64(self.bunkers_left_vec[0]))
                    + "/"
                    + _tab.utils.INT2STR(lm_spd)
                    + " LM"
                )

                h_offset = 2
                v_offset = 5
                pad = 2
                rm_tw = fm.horizontalAdvance(rm_text) + pad * 2
                lm_tw = fm.horizontalAdvance(lm_text) + pad * 2
                th = fm.height() + pad

                rm_rect = _QtCore.QRectF(ruu + h_offset, rvv + v_offset, rm_tw, th)
                lm_rect = _QtCore.QRectF(luu + h_offset, lvv + v_offset, lm_tw, th)

                # A range-ring speed label sits at each marker's on-axis
                # position, so when a storm-motion vector lands on (or near) an
                # axis its label crowds that ring number. Mask a region that
                # extends up-and-left of the label -- toward the marker/axis --
                # so the colliding ring number is painted over, not just the
                # text's own footprint.
                mask_pad_x = 14
                mask_pad_y = 12

                def _mask_rect(rect):
                    return _QtCore.QRectF(
                        rect.x() - mask_pad_x,
                        rect.y() - mask_pad_y,
                        rect.width() + mask_pad_x,
                        rect.height() + mask_pad_y,
                    )

                # Background fill so labels are readable over the hodo traces
                # and any range-ring number beneath / beside them.
                qp.fillRect(_mask_rect(rm_rect), self.bg_color)
                qp.fillRect(_mask_rect(lm_rect), self.bg_color)

                pen = _QtGui.QPen(self.fg_color)
                qp.setPen(pen)
                qp.drawText(rm_rect, _QtCore.Qt.AlignCenter, rm_text)
                qp.drawText(lm_rect, _QtCore.Qt.AlignCenter, lm_text)

                # The widened mask can paint over the storm-motion marker
                # circles (drawn earlier); redraw them on top so they survive.
                qp.drawEllipse(center_rm, 5, 5)
                qp.drawEllipse(center_lm, 5, 5)
                occupied = getattr(self, "_sharpmod_hodo_annotation_rects", None)
                if isinstance(occupied, list):
                    occupied.extend(
                        (
                            _QtCore.QRectF(rm_rect),
                            _QtCore.QRectF(lm_rect),
                            _QtCore.QRectF(ruu - 5, rvv - 5, 10, 10),
                            _QtCore.QRectF(luu - 5, lvv - 5, 10, 10),
                        )
                    )
            except Exception:
                _orig_smv(self, qp)

        _plot.drawSMV = drawSMV

        # --- 4. Override paintEvent readout to use font-metrics-sized rect ---
        _orig_paint = _plot.paintEvent

        def paintEvent(self, e):
            # The vendored paintEvent draws the cursor readout with a fixed
            # 55x16 rect. We override it to use font metrics for sizing and
            # the capped readout_font.
            try:
                draw_readout = False
                u_interp = 0
                xx, yy = 0, 0
                readout = ""

                if self.prof:
                    vis = getattr(self, "readout_visible", False)
                    rh = getattr(self, "readout_hght", -999.0)
                    draw_readout = vis and rh >= 0 and rh <= 12000.0
                else:
                    draw_readout = False

                if draw_readout:
                    hght_agl = _tab.interp.to_agl(self.prof, self.hght)
                    u_interp = _tab.interp.generic_interp_hght(
                        self.readout_hght, hght_agl, self.u
                    )
                    v_interp = _tab.interp.generic_interp_hght(
                        self.readout_hght, hght_agl, self.v
                    )
                    if _tab.utils.QC(u_interp):
                        wd_interp, ws_interp = _tab.utils.comp2vec(u_interp, v_interp)
                        if self.wind_units == "m/s":
                            ws_interp = _tab.utils.KTS2MS(ws_interp)
                            units = "m/s"
                        else:
                            units = "kts"
                        xx, yy = self.uv_to_pix(u_interp, v_interp)
                        readout = "%03d/%02d %s" % (wd_interp, ws_interp, units)
                    else:
                        readout = "--/-- %s" % self.wind_units
                        draw_readout = False

                # Blit the background pixmap (same as vendored)
                _bg.paintEvent(self, e)
                qp = _QtGui.QPainter()
                qp.begin(self)
                qp.drawPixmap(0, 0, self.plotBitMap)

                # Scale-aware overlays are painted with this live widget
                # painter so HD/UHD capture rasterizes their small text at
                # the final target density. Keep them below the transient
                # cursor readout, which must remain the topmost annotation.
                for overlay in tuple(
                    getattr(type(self), "_sharpmod_live_overlays", ())
                ):
                    try:
                        overlay(self, qp)
                    except Exception:
                        _LOGGER.exception(
                            "hodo_live_overlay.draw_failed",
                            extra={
                                "overlay": getattr(overlay, "__name__", repr(overlay))
                            },
                        )

                if draw_readout and _tab.utils.QC(u_interp):
                    # Use a fixed small font for the readout, bypassing
                    # self.readout_font which may be rebuilt by other code.
                    _readout_f = _QtGui.QFont("Helvetica", readout_cap)
                    _readout_f.setBold(True)
                    qp.setFont(_readout_f)
                    fm = _QtGui.QFontMetrics(_readout_f)
                    pad = 3
                    tw = fm.horizontalAdvance(readout) + pad * 2
                    th = fm.height() + pad
                    text_rect = _fit_rect_to_hodo(
                        self, _QtCore, _QtCore.QRectF(xx + 2, yy + 5, tw, th)
                    )
                    qp.fillRect(text_rect, self.bg_color)
                    qp.setPen(_QtGui.QPen(self.fg_color, 1))
                    qp.drawEllipse(_QPointF(xx, yy), 4, 4)
                    qp.drawText(text_rect, _QtCore.Qt.AlignCenter, readout)

                qp.end()
            except Exception:
                _orig_paint(self, e)

        _plot.paintEvent = paintEvent
        _plot._sharpmod_live_overlay_host = True

        # --- 5. Override drawCorfidi to use font-metrics-sized rects ---
        _orig_corfidi = _plot.drawCorfidi

        def drawCorfidi(self, qp):
            try:
                penwidth = 1
                corfidi_color = _semantic_qcolor(self, "corfidi")
                pen = _QtGui.QPen(corfidi_color, penwidth)
                pen.setStyle(_QtCore.Qt.SolidLine)
                qp.setPen(pen)

                if (
                    not _np.isfinite(self.corfidi_up_u)
                    or not _np.isfinite(self.corfidi_up_v)
                    or not _np.isfinite(self.corfidi_dn_u)
                    or not _np.isfinite(self.corfidi_dn_v)
                ):
                    return

                up_u, up_v = self.uv_to_pix(self.corfidi_up_u, self.corfidi_up_v)
                dn_u, dn_v = self.uv_to_pix(self.corfidi_dn_u, self.corfidi_dn_v)
                center_up = _QPointF(up_u, up_v)
                center_dn = _QPointF(dn_u, dn_v)
                qp.drawEllipse(center_up, 3, 3)
                qp.drawEllipse(center_dn, 3, 3)

                # Labels sized to font metrics
                qp.setFont(self.label_font)
                fm = _QtGui.QFontMetrics(self.label_font)

                up_spd = self.upshear[1]
                dn_spd = self.downshear[1]
                if self.wind_units == "m/s":
                    up_spd = _tab.utils.KTS2MS(up_spd)
                    dn_spd = _tab.utils.KTS2MS(dn_spd)

                up_text = (
                    "UP="
                    + _tab.utils.INT2STR(_np.float64(self.upshear[0]))
                    + "/"
                    + _tab.utils.INT2STR(up_spd)
                )
                dn_text = (
                    "DN="
                    + _tab.utils.INT2STR(_np.float64(self.downshear[0]))
                    + "/"
                    + _tab.utils.INT2STR(dn_spd)
                )

                h_offset = 1
                v_offset = 3
                pad = 2
                up_tw = fm.horizontalAdvance(up_text) + pad * 2
                dn_tw = fm.horizontalAdvance(dn_text) + pad * 2
                th = fm.height() + pad

                up_rect = _QtCore.QRectF(up_u + h_offset, up_v + v_offset, up_tw, th)
                dn_rect = _QtCore.QRectF(dn_u + h_offset, dn_v + v_offset, dn_tw, th)

                qp.fillRect(up_rect, self.bg_color)
                qp.fillRect(dn_rect, self.bg_color)

                pen = _QtGui.QPen(corfidi_color)
                qp.setPen(pen)
                qp.drawText(up_rect, _QtCore.Qt.AlignCenter, up_text)
                qp.drawText(dn_rect, _QtCore.Qt.AlignCenter, dn_text)
                occupied = getattr(self, "_sharpmod_hodo_annotation_rects", None)
                if isinstance(occupied, list):
                    occupied.extend(
                        (
                            _QtCore.QRectF(up_rect),
                            _QtCore.QRectF(dn_rect),
                            _QtCore.QRectF(up_u - 3, up_v - 3, 6, 6),
                            _QtCore.QRectF(dn_u - 3, dn_v - 3, 6, 6),
                        )
                    )
            except Exception:
                _orig_corfidi(self, qp)

        _plot.drawCorfidi = drawCorfidi

        # --- 6. Keep the LCL-EL mean-wind value clear of its square marker ---
        _orig_mean_wind = _plot.drawLCLtoEL_MW

        def drawLCLtoEL_MW(self, qp):
            try:
                if not _tab.utils.QC(self.mean_lcl_el[0]):
                    return
                mean_u, mean_v = self.uv_to_pix(
                    self.mean_lcl_el[0], self.mean_lcl_el[1]
                )
                marker_size = 8.0
                marker_rect = _QtCore.QRectF(
                    mean_u - marker_size / 2.0,
                    mean_v - marker_size / 2.0,
                    marker_size,
                    marker_size,
                )

                speed = self.mean_lcl_el_vec[1]
                if self.wind_units == "m/s":
                    speed = _tab.utils.KTS2MS(speed)
                text = (
                    _tab.utils.INT2STR(_np.float64(self.mean_lcl_el_vec[0]))
                    + "/"
                    + _tab.utils.INT2STR(speed)
                )

                qp.setFont(self.label_font)
                fm = _QtGui.QFontMetrics(self.label_font)
                text_rect = _place_hodo_annotation_rect(
                    self,
                    _QtCore,
                    marker_rect,
                    fm.horizontalAdvance(text) + 6,
                    fm.height() + 2,
                    occupied=getattr(self, "_sharpmod_hodo_annotation_rects", ()),
                    gap=5,
                )
                mean_color = _semantic_qcolor(self, "orange", override="#B8860B")

                qp.fillRect(text_rect, self.bg_color)
                qp.setBrush(_QtGui.QBrush(_QtCore.Qt.NoBrush))
                qp.setPen(_QtGui.QPen(mean_color, 2, _QtCore.Qt.SolidLine))
                qp.drawRect(marker_rect)
                qp.setPen(_QtGui.QPen(mean_color, 1, _QtCore.Qt.SolidLine))
                qp.drawText(text_rect, _QtCore.Qt.AlignCenter, text)

                occupied = getattr(self, "_sharpmod_hodo_annotation_rects", None)
                if isinstance(occupied, list):
                    occupied.extend(
                        (
                            _QtCore.QRectF(marker_rect),
                            _QtCore.QRectF(text_rect),
                        )
                    )
            except Exception:
                _orig_mean_wind(self, qp)

        _plot.drawLCLtoEL_MW = drawLCLtoEL_MW

        _plot._sharpmod_label_fit = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


__all__ = ["install_hodo_label_fit"]

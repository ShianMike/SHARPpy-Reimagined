"""Panel Fonts adjustments for headless sounding renders."""

from __future__ import annotations

from sharpmod.render_patches.geometry import COND_PROB_LABEL_MAX_PT
from sharpmod.render_patches.geometry import _centered_rect_in_bounds
from sharpmod.render_patches.geometry import _draw_fitted_text
from sharpmod.render_patches.geometry import _fit_font_to_rect
from sharpmod.render_patches.hodo_legacy import HODO_0_500_COLOR
from sharpmod.render_patches.palette import _semantic_qcolor
import os
from sharpmod.rendering import cli as _render_api


def enlarge_charts(spc_widget):
    """Give the skew-T and hodograph the majority of the window real estate.

    Sets stretch factors on the vendored ``SPCWidget``'s OUTER ``grid`` only
    (it ships with none): the three chart rows dominate the bottom table band
    and the skew-T column is balanced against the upper-right hodograph panel.
    The inner ``grid2`` is intentionally NOT touched, so the hodograph's
    neighbor strips (wind speed, temperature advection) and bottom insets
    (storm slinky, theta-e, SR winds) keep their vendored proportions and stay
    legible while the whole panel -- hodograph included -- grows. Stretch
    factors govern how *surplus* space is distributed, so this is applied once
    and survives the later canvas grow. Fully guarded: a missing layout or
    renamed attribute never aborts a render.
    """
    grid = getattr(spc_widget, "grid", None)
    if grid is not None:
        try:
            # Chart rows (skew-T + upper-right panel span rows 0-2) dominate the
            # index-table band (row 3).
            grid.setRowStretch(0, _render_api.CHART_ROW_STRETCH)
            grid.setRowStretch(1, _render_api.CHART_ROW_STRETCH)
            grid.setRowStretch(2, _render_api.CHART_ROW_STRETCH)
            grid.setRowStretch(3, _render_api.TEXT_ROW_STRETCH)
            # Balance the skew-T column (0) against the hodo/insets panel (1).
            grid.setColumnStretch(0, _render_api.SKEWT_COL_STRETCH)
            grid.setColumnStretch(1, _render_api.URPANEL_COL_STRETCH)
        except Exception:
            pass

    grid2 = getattr(spc_widget, "grid2", None)
    if grid2 is not None:
        try:
            # COLUMN stretch only: widen the two cramped left strips (wind speed
            # cols 0-2, inferred temp advection cols 3-4) while the hodograph and
            # bottom insets (cols 5-28) stay dominant. Row stretch is left alone
            # so the storm-slinky / theta-e / SR-wind insets keep their height.
            for _c in range(0, 3):
                grid2.setColumnStretch(_c, _render_api.SPEED_STRIP_COL_STRETCH)
            for _c in range(3, 5):
                grid2.setColumnStretch(_c, _render_api.ADV_STRIP_COL_STRETCH)
            for _c in range(5, 29):
                grid2.setColumnStretch(_c, _render_api.HODO_COL_STRETCH)
        except Exception:
            pass


# Maximum point size for the "Wind Speed (knots)" strip title. The vendored
# ``backgroundSpeed.plotBackground`` recomputes the title font at draw time as
# ``width * font_ratio`` (font_ratio = 0.12), so widening the strip (e.g. via
# the enlarged canvas) balloons the title until it overflows its box. Capping
# it keeps the two-line title inside its 30 px header rect at any strip width.
SPEED_TITLE_MAX_PT = int(os.environ.get("SPEED_TITLE_MAX_PT", "9"))
# Maximum point size for the wind-speed strip's numeric axis labels ("40 80
# 120"). They are drawn into a short fixed-height slot at the strip's bottom,
# so the taller bundled font spills past the widget edge and gets clipped.
SPEED_LABEL_MAX_PT = int(os.environ.get("SPEED_LABEL_MAX_PT", "9"))
# Maximum point size for the "Inf. Temp. Adv. (C/hr)" strip. The vendored
# ``backgroundAdvection.initUI`` sizes its ``label_font`` (used for BOTH the
# title and the strip's numeric axis labels) as ``width * font_ratio + 3``
# (font_ratio = 0.12), so widening the strip balloons the title. Capping keeps
# the title + axis labels small and tidy at any strip width.
ADV_TITLE_MAX_PT = int(os.environ.get("ADV_TITLE_MAX_PT", "9"))
# Maximum point size for the three skew-T surface value labels (temperature /
# dewpoint / wet-bulb). They sit close together, so the tall bundled font makes
# their background masks overlap and erase each other; capping keeps all three
# legible side-by-side.
SFC_LABEL_MAX_PT = int(os.environ.get("SFC_LABEL_MAX_PT", "10"))

_speed_title_cap_installed = False
_speed_0500_installed = False
_adv_font_cap_installed = False


def _speed_axis_label_font(base_font, qtgui, text, max_width, max_height,
                           max_pt=SPEED_LABEL_MAX_PT, min_pt=6):
    """Return a copy of ``base_font`` small enough for a speed-axis tick."""
    font = qtgui.QFont(base_font)
    pt = font.pointSize()
    if pt < 0:
        pt = font.pixelSize()
    if pt <= 0:
        pt = max_pt
    pt = min(int(pt), int(max_pt))

    while pt > int(min_pt):
        font.setPointSize(pt)
        metrics = qtgui.QFontMetrics(font)
        if (metrics.height() <= max_height and
                metrics.horizontalAdvance(str(text)) <= max_width):
            return font
        pt -= 1

    font.setPointSize(max(int(min_pt), 1))
    return font


def _install_advection_font_cap():
    """Cap the inferred-temperature-advection strip font so it stays tidy.

    Wraps ``backgroundAdvection.initUI`` to rebuild ``label_font`` at a point
    size no larger than :data:`ADV_TITLE_MAX_PT` after the vendored ``initUI``
    runs, then repaints the background so the capped font takes effect. That
    font drives both the "Inf. Temp. Adv. (C/hr)" title and the strip's numeric
    axis labels, so both stay small and readable however wide the strip is.
    Idempotent + fully guarded so a failure leaves the vendored render intact.
    """
    global _adv_font_cap_installed
    if _adv_font_cap_installed:
        return
    cap = ADV_TITLE_MAX_PT
    if cap <= 0:
        return
    try:
        import sharppy.viz.advection as _adv_mod
        _cls = _adv_mod.backgroundAdvection
        if getattr(_cls, "_sharpmod_font_cap", False):
            return
        _QtGui = _adv_mod.QtGui
        _orig = _cls.initUI

        def initUI(self):
            _orig(self)
            try:
                f = self.label_font
                if f.pointSize() > cap:
                    f = _QtGui.QFont(f.family(), cap)
                    self.label_font = f
                    self.label_metrics = _QtGui.QFontMetrics(f)
                    # Repaint the background on a blank bitmap so the capped
                    # title/axis font replaces the oversized one already drawn.
                    self.plotBitMap.fill(self.bg_color)
                    self.plotBackground()
            except Exception:
                pass

        _cls.initUI = initUI
        _cls._sharpmod_font_cap = True
        _adv_font_cap_installed = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


def _install_speed_title_cap():
    """Cap the wind-speed strip title font so it stops overflowing when wide.

    Wraps ``backgroundSpeed.plotBackground`` to clamp ``font_ratio`` for the
    duration of the draw so the title point size never exceeds
    :data:`SPEED_TITLE_MAX_PT`. ``plotBackground`` uses ``font_ratio`` only for
    the title (the axis tick labels use ``label_font`` built in ``initUI``), so
    this shrinks nothing but the oversized title. Idempotent + fully guarded so
    a failure leaves the vendored render untouched.
    """
    global _speed_title_cap_installed
    if _speed_title_cap_installed:
        return
    cap = SPEED_TITLE_MAX_PT
    if cap <= 0:
        return
    try:
        import sharppy.viz.speed as _speed_mod
        _cls = _speed_mod.backgroundSpeed
        if getattr(_cls, "_sharpmod_title_cap", False):
            return
        _orig = _cls.plotBackground

        def plotBackground(self):
            saved = getattr(self, "font_ratio", 0.12)
            try:
                w = max(1, self.size().width())
                if round(w * saved) > cap:
                    self.font_ratio = cap / float(w)
            except Exception:
                pass
            try:
                _orig(self)
            finally:
                self.font_ratio = saved

        _cls.plotBackground = plotBackground

        # Also cap the numeric axis-label font (``label_font``, set in
        # ``initUI`` and used by ``draw_speed``) so the "40 80 120" labels fit
        # the short bottom slot instead of being clipped by the widget edge.
        lbl_cap = SPEED_LABEL_MAX_PT
        _orig_init = _cls.initUI
        _QtGui_s = _speed_mod.QtGui

        def initUI(self):
            _orig_init(self)
            try:
                f = self.label_font
                if lbl_cap > 0 and f.pointSize() > lbl_cap:
                    self.label_font = _QtGui_s.QFont(f.family(), lbl_cap)
                    # Repaint on a blank bitmap so the capped axis font (and the
                    # capped title, via the wrapped plotBackground) replace the
                    # oversized ones the vendored initUI already drew.
                    self.plotBitMap.fill(_QtGui_s.QColor(self.bg_color))
                    self.plotBackground()
            except Exception:
                pass

        _cls.initUI = initUI

        # The vendored ``draw_speed`` draws the "40 80 120" axis labels into a
        # fixed 10 px-tall rect at ``bry+5``; larger fonts spill past the widget
        # bottom and the 120-kt label can exceed its horizontal slot. Fit the
        # local tick font to the actual slot before drawing.
        _Qt = _speed_mod.QtCore.Qt
        _orig_draw = _cls.draw_speed

        def draw_speed(self, s, qp, delta=0, drawlabel=True):
            try:
                pen = _QtGui_s.QPen(self.isotach_color, 1, _Qt.DashLine)
                qp.setPen(pen)
                qp.setFont(self.label_font)
                x1 = self.speed_to_pix(s)
                labelx1 = self.speed_to_pix(s - delta)
                label_width = (self.speed_to_pix(s + delta)
                               - self.speed_to_pix(s - delta))
                qp.drawLine(int(x1), int(self.bry), int(x1), int(self.tly))
                if drawlabel is True and s > 0:
                    text = str(int(s))
                    label_height = max(1, int(self.bpad) - 4)
                    label_width = max(1, int(label_width))
                    label_font = _speed_axis_label_font(
                        self.label_font, _QtGui_s, text,
                        max(1, label_width - 2), label_height)
                    qp.setFont(label_font)
                    pen = _QtGui_s.QPen(_QtGui_s.QColor(self.fg_color), 1,
                                        _Qt.DashLine)
                    qp.setPen(pen)
                    qp.drawText(int(labelx1), int(self.bry + 1),
                                label_width, label_height,
                                _Qt.AlignVCenter | _Qt.AlignHCenter, text)
            except Exception:
                _orig_draw(self, s, qp, delta=delta, drawlabel=drawlabel)

        _cls.draw_speed = draw_speed
        _cls._sharpmod_title_cap = True
        _speed_title_cap_installed = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


def _install_speed_0500():
    """Color the wind-speed strip's SFC-500 m layer like the hodograph.

    Upstream ``sharppy.viz.speed.plotSpeed`` colors every height below 3 km
    with the low-level hodograph color. The hodograph patch splits out the
    first 500 m as a pink band; this draw-profile wrapper applies the same
    split to the speed-vs-height strip while leaving the rest of the vendored
    profile drawing unchanged.
    """
    global _speed_0500_installed
    if _speed_0500_installed:
        return
    try:
        import numpy as _np
        import sharppy.sharptab as _tab
        import sharppy.viz.speed as _speed_mod

        _cls = _speed_mod.plotSpeed
        if getattr(_cls, "_sharpmod_0500", False):
            return
        _QtGui = _speed_mod.QtGui
        _QtCore = _speed_mod.QtCore
        _orig = _cls.draw_profile

        def draw_profile(self, qp):
            try:
                if self.prof is None:
                    return
                try:
                    mask = _np.maximum(
                        _np.maximum(self.u.mask, self.v.mask),
                        self.hght.mask)
                    hgt = _tab.interp.to_agl(self.prof, self.hght[~mask])
                    pres = self.pres[~mask]
                    u = self.u[~mask]
                    v = self.v[~mask]
                    spd = _np.sqrt(u ** 2 + v ** 2)
                except Exception:
                    hgt = _tab.interp.to_agl(self.prof, self.hght)
                    pres = self.pres
                    u = self.u
                    v = self.v
                    spd = _np.sqrt(u ** 2 + v ** 2)

                if self.wind_units == "m/s":
                    spd = _tab.utils.KTS2MS(spd)

                sfc_500_color = _semantic_qcolor(
                    self, "hodo_0500", override=HODO_0_500_COLOR)
                for i in range(pres.shape[0]):
                    hgt1 = float(hgt[i])
                    x1 = self.speed_to_pix(spd[i])
                    y1 = self.pres_to_pix(pres[i])
                    if hgt1 < 500.0:
                        pen = _QtGui.QPen(sfc_500_color, 2)
                    elif hgt1 < 3000.0:
                        pen = _QtGui.QPen(self.low_level_color, 2)
                    elif hgt1 < 6000.0:
                        pen = _QtGui.QPen(self.mid_level_color, 2)
                    elif hgt1 < 9000.0:
                        pen = _QtGui.QPen(self.upper_level_color, 2)
                    else:
                        pen = _QtGui.QPen(self.trop_level_color, 2)
                    pen.setStyle(_QtCore.Qt.SolidLine)
                    qp.setPen(pen)
                    qp.drawLine(0, int(y1), int(x1), int(y1))
            except Exception:
                _orig(self, qp)

        _cls.draw_profile = draw_profile
        _cls._sharpmod_0500 = True
        _speed_0500_installed = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


def _install_conditional_prob_panel_fit():
    """Keep conditional STPC/VROT probability-panel text inside its frame.

    The vendored ``stpef`` and ``vrot`` panels draw title, legend, y-tick, and
    x-tick labels into very small fixed rectangles with ``TextDontClip``. On the
    enlarged GUI/render canvas and bundled font, labels either crowd the plot
    area or clip against panel edges. These replacements keep the same plotted
    curves/data but fit every text label to its available slot.
    """
    try:
        import numpy as _np
        import sharppy.databases.inset_data as _ins
        import sharppy.viz.stpef as _stpef_mod
        import sharppy.viz.vrot as _vrot_mod

        def _draw_header(qp, self, qtcore, qtgui, title, legend):
            label_h = float(getattr(
                self, "label_height", getattr(self, "box_height",
                                              getattr(self, "plot_height", 12))))
            title_rect = qtcore.QRectF(4, 2, max(1.0, self.brx - 8),
                                       max(1.0, self.plot_height))
            _draw_fitted_text(
                qp, qtcore, qtgui, title_rect, title, self.plot_font,
                self.fg_color, qtcore.Qt.AlignCenter,
                max_pt=COND_PROB_LABEL_MAX_PT)

            top = 3 + self.plot_height
            height = max(1.0, label_h - 1)
            for center_x, label, color, width in legend:
                rect = _centered_rect_in_bounds(
                    qtcore, center_x, top, width, height,
                    max(1.0, self.tlx + 24), max(2.0, self.brx - 2))
                _draw_fitted_text(
                    qp, qtcore, qtgui, rect, label,
                    qtgui.QFont("Helvetica", COND_PROB_LABEL_MAX_PT),
                    qtgui.QColor(color), qtcore.Qt.AlignCenter,
                    max_pt=COND_PROB_LABEL_MAX_PT)

        def _draw_y_grid(qp, self, qtcore, qtgui, texts):
            label_h = float(getattr(
                self, "label_height", getattr(self, "box_height",
                                              getattr(self, "plot_height", 12))))
            base_font = qtgui.QFont("Helvetica", min(
                COND_PROB_LABEL_MAX_PT, max(6, int(getattr(self, "fsize", 8)))))
            for text in texts:
                try:
                    tick = self.prob_to_pix(int(text))
                except Exception:
                    continue
                qp.setPen(qtgui.QPen(_semantic_qcolor(
                                      self, "conditional_grid"), 1,
                                      qtcore.Qt.DashLine))
                qp.drawLine(self.tlx, tick, self.brx, tick)
                rect = qtcore.QRectF(self.tlx, tick - label_h / 2.0,
                                     24, max(1.0, label_h))
                _draw_fitted_text(
                    qp, qtcore, qtgui, rect, text, base_font, self.fg_color,
                    qtcore.Qt.AlignCenter, max_pt=COND_PROB_LABEL_MAX_PT)

        def _draw_x_label(qp, self, qtcore, qtgui, center_x, width, text):
            rect = _centered_rect_in_bounds(
                qtcore, center_x, self.bry + 1,
                max(1.0, width), max(1.0, self.bpad - 2),
                max(0.0, self.tlx), max(1.0, self.brx - 1))
            _draw_fitted_text(
                qp, qtcore, qtgui, rect, text,
                qtgui.QFont("Helvetica", COND_PROB_LABEL_MAX_PT),
                self.fg_color, qtcore.Qt.AlignCenter,
                max_pt=COND_PROB_LABEL_MAX_PT, min_pt=5)

        _stpef_cls = _stpef_mod.backgroundSTPEF
        if not getattr(_stpef_cls, "_sharpmod_text_fit", False):
            _stpef_orig = _stpef_cls.draw_frame
            _QtGuiS = _stpef_mod.QtGui
            _QtCoreS = _stpef_mod.QtCore

            def stpef_draw_frame(self, qp):
                try:
                    data = _ins.condSTPData()
                    legend_colors = {
                        "EF1+": _semantic_qcolor(self, "tornado_ef1"),
                        "EF2+": _semantic_qcolor(self, "tornado_ef2"),
                        "EF3+": _semantic_qcolor(self, "tornado_ef3"),
                        "EF4+": _semantic_qcolor(self, "tornado_ef4"),
                    }
                    _draw_header(qp, self, _QtCoreS, _QtGuiS,
                                 "Conditional Tornado Probs based on STPC",
                                 [
                                     (self.stpc_to_pix(.2), "EF1+",
                                      legend_colors["EF1+"], 48),
                                     (self.stpc_to_pix(1.1), "EF2+",
                                      legend_colors["EF2+"], 48),
                                     (self.stpc_to_pix(3.1), "EF3+",
                                      legend_colors["EF3+"], 48),
                                     (self.stpc_to_pix(6.1), "EF4+",
                                      legend_colors["EF4+"], 48),
                                 ])

                    _draw_y_grid(qp, self, _QtCoreS, _QtGuiS, data["ytexts"])

                    width = self.brx / 12.0
                    spacing = self.brx / 12.0
                    centers = _np.arange(spacing, self.brx, spacing)
                    xtexts = data["xticks"]
                    for i, text in enumerate(xtexts):
                        if i >= len(centers):
                            break
                        _draw_x_label(qp, self, _QtCoreS, _QtGuiS,
                                      centers[i], width, text)

                    series = [
                        ("EF1+", legend_colors["EF1+"]),
                        ("EF2+", legend_colors["EF2+"]),
                        ("EF3+", legend_colors["EF3+"]),
                        ("EF4+", legend_colors["EF4+"]),
                    ]
                    for key, color in series:
                        vals = data[key]
                        qp.setPen(_QtGuiS.QPen(_QtGuiS.QColor(color), 3,
                                               _QtCoreS.Qt.SolidLine))
                        for i in range(1, _np.asarray(xtexts).shape[0], 1):
                            qp.drawLine(
                                centers[i - 1], self.prob_to_pix(vals[i - 1]),
                                centers[i], self.prob_to_pix(vals[i]))
                except Exception:
                    _stpef_orig(self, qp)

            _stpef_cls.draw_frame = stpef_draw_frame
            _stpef_cls._sharpmod_text_fit = True

        _vrot_cls = _vrot_mod.backgroundVROT
        if not getattr(_vrot_cls, "_sharpmod_text_fit", False):
            _vrot_orig = _vrot_cls.draw_frame
            _QtGuiV = _vrot_mod.QtGui
            _QtCoreV = _vrot_mod.QtCore

            def vrot_draw_frame(self, qp):
                try:
                    data = self.vrot_inset_data
                    _draw_header(qp, self, _QtCoreV, _QtGuiV,
                                 "Conditional EF-scale Probs based on Vrot",
                                 [
                                     (self.vrot_to_pix(25), "EF0-EF1",
                                      self.EF01_color, 74),
                                     (self.vrot_to_pix(50), "EF2-EF3",
                                      self.EF23_color, 74),
                                     (self.vrot_to_pix(75), "EF4-EF5",
                                      self.EF45_color, 74),
                                 ])

                    _draw_y_grid(qp, self, _QtCoreV, _QtGuiV, data["ytexts"])

                    width = self.brx / 12.0
                    for text in _np.arange(10, 110, 10):
                        _draw_x_label(qp, self, _QtCoreV, _QtGuiV,
                                      self.vrot_to_pix(text), width, str(text))

                    xpts = data["xpts"]
                    series = [
                        ("EF0-EF1", self.EF01_color),
                        ("EF2-EF3", self.EF23_color),
                        ("EF4-EF5", self.EF45_color),
                    ]
                    for key, color in series:
                        vals = data[key]
                        lastprob = min(vals[0], 70)
                        for i in range(1, _np.asarray(xpts).shape[0], 1):
                            prob = min(vals[i], 70)
                            style = (_QtCoreV.Qt.DotLine if vals[i] > 70
                                     else _QtCoreV.Qt.SolidLine)
                            qp.setPen(_QtGuiV.QPen(
                                _QtGuiV.QColor(color), 2.5, style))
                            qp.drawLine(
                                self.vrot_to_pix(xpts[i - 1]),
                                self.prob_to_pix(lastprob),
                                self.vrot_to_pix(xpts[i]),
                                self.prob_to_pix(prob))
                            lastprob = prob
                except Exception:
                    _vrot_orig(self, qp)

            _vrot_cls.draw_frame = vrot_draw_frame
            _vrot_cls._sharpmod_text_fit = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


def _install_winter_text_fit():
    """Install the winter/DGZ panel patch from its focused owner module."""
    from sharpmod.render_patches.text_panels import install_winter_text_fit

    return install_winter_text_fit(
        _fit_font_to_rect=_fit_font_to_rect,
        WINTER_LABEL_MAX_PT=_render_api.WINTER_LABEL_MAX_PT,
        WINTER_MIN_ROW_PX=_render_api.WINTER_MIN_ROW_PX,
        WINTER_DGZ_ROWS=_render_api.WINTER_DGZ_ROWS,
        WINTER_ENERGY_ROWS=_render_api.WINTER_ENERGY_ROWS,
    )


def _install_fire_text_fit():
    """Install the fire-weather panel patch from its focused owner module."""
    from sharpmod.render_patches.text_panels import install_fire_text_fit

    return install_fire_text_fit(
        _fit_font_to_rect=_fit_font_to_rect,
        FIRE_LABEL_MAX_PT=_render_api.FIRE_LABEL_MAX_PT,
    )

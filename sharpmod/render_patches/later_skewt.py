"""Later Skewt adjustments for headless sounding renders."""

from __future__ import annotations

from qtpy import QtGui
from sharpmod.viz import colors
from sharpmod.render_patches.geometry import _fit_font_to_rect
from sharpmod.render_patches.hodo_legacy import _fit_rect_to_hodo
from sharpmod.render_patches.hodo_legacy import _place_hodo_annotation_rect
from sharpmod.render_patches.palette import _semantic_qcolor
import os
from sharpmod.rendering import cli as _render_api


def _apply_sars_match_color():
    """Substitute the dim legacy SARS non-tornadic match color for a readable
    tan (Requirement 22.2).

    The SARS analogues widget colors matches via a module-level constant pulled
    in with ``from constants import *``; rebinding it applies the documented
    substitution consistently.
    """
    try:
        import sharppy.viz.analogues as analogues_mod
        analogues_mod.LBROWN = colors.SARS_NONTOR_MATCH
    except Exception:  # pragma: no cover - analogues optional at import time
        pass




def _install_skewt_title_shrink():
    """Shrink + condense the skew-T title font on every geometry rebuild.

    ``backgroundSkewT.initUI`` recomputes ``title_font`` from scratch on each
    resize, so a one-time shrink is undone by the later window grow. Wrapping
    ``initUI`` re-applies ``TITLE_FONT_SCALE`` / ``TITLE_STRETCH`` every time,
    so the title stays small. ``plotSkewT`` inherits ``initUI``. Idempotent.
    """
    try:
        import sharppy.viz.skew as _skew_mod
        _cls = _skew_mod.backgroundSkewT
        if getattr(_cls, "_sharpmod_title", False):
            return
        _orig = _cls.initUI

        def initUI(self):
            _orig(self)
            try:
                f = self.title_font
                ps = f.pointSizeF()
                if ps > 0 and _render_api.TITLE_FONT_SCALE != 1.0:
                    f.setPointSizeF(ps * _render_api.TITLE_FONT_SCALE)
                if _render_api.TITLE_STRETCH and _render_api.TITLE_STRETCH != 100:
                    f.setStretch(_render_api.TITLE_STRETCH)
                self.title_font = f
                self.title_metrics = QtGui.QFontMetrics(f)
            except Exception:
                pass
            # Widen the left pad so the 4-digit "1000" mb pressure label isn't
            # clipped at the widget's left edge (its label rect is lpad-4 wide).
            try:
                if _render_api.SKEWT_LPAD and self.lpad < _render_api.SKEWT_LPAD:
                    self.lpad = _render_api.SKEWT_LPAD
                    self.clip = _skew_mod.QRect(
                        _skew_mod.QPoint(self.lpad, self.tly),
                        _skew_mod.QPoint(self.brx + self.rpad, self.bry))
            except Exception:
                pass
            # Keep the pressure labels condensed so "1000" fits its label box (a
            # plain resize rebuilds label_font at full width, undoing the
            # one-time tighten_pressure_labels pass).
            try:
                lf = self.label_font
                if _render_api.PLABEL_STRETCH and _render_api.PLABEL_STRETCH != 100:
                    lf.setStretch(_render_api.PLABEL_STRETCH)
                    self.label_font = lf
                    self.label_metrics = QtGui.QFontMetrics(lf)
            except Exception:
                pass
            # ``_orig`` already painted the background using the vendored
            # ``lpad`` (30) and full-width label font, so the "1000" mb label
            # was drawn clipped in its narrow box. Now that ``lpad``/``clip``
            # are widened and the label font condensed, repaint the background
            # so the persisted bitmap shows the full label. Without this, every
            # resize (incl. the final window grow) re-clips "1000".
            try:
                if hasattr(self, "plotBitMap"):
                    self.plotBitMap.fill(self.bg_color)
                if hasattr(self, "plotBackground"):
                    self.plotBackground()
            except Exception:
                pass

        _cls.initUI = initUI
        _cls._sharpmod_title = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise



# Default hodograph viewport, expressed as the knot span across the full widget
# width. A 160-kt span is 20% tighter than the previous 200-kt view, so wind
# vectors and annotations render 25% larger while retaining the same values.
# ``HODO_ZOOM_KTS`` remains an environment override for specialized displays.


# Maximum point size for the hodograph label font (RM/LM storm motion labels).
HODO_LABEL_MAX_PT = int(os.environ.get("HODO_LABEL_MAX_PT", "8"))
# Maximum point size for the hodograph readout font (cursor wind readout).
HODO_READOUT_MAX_PT = int(os.environ.get("HODO_READOUT_MAX_PT", "7"))


def _install_hodo_label_fit():
    """Install the hodograph label patch from its focused owner module."""
    from sharpmod.render_patches.hodograph import install_hodo_label_fit

    return install_hodo_label_fit(
        HODO_LABEL_MAX_PT=HODO_LABEL_MAX_PT,
        HODO_READOUT_MAX_PT=HODO_READOUT_MAX_PT,
        _LOGGER=_render_api._LOGGER,
        _fit_font_to_rect=_fit_font_to_rect,
        _fit_rect_to_hodo=_fit_rect_to_hodo,
        _place_hodo_annotation_rect=_place_hodo_annotation_rect,
        _semantic_qcolor=_semantic_qcolor,
    )


def _install_hodo_locator():
    """Overlay the bundled offline locator after hodograph data is drawn."""
    try:
        import sharppy.viz.hodo as _hodo_mod
        from sharpmod.viz.hodo_locator import draw_hodo_locator

        _plot = _hodo_mod.plotHodo
        if getattr(_plot, "_sharpmod_locator", False):
            return
        _orig_plot_data = _plot.plotData

        def plotData(self):
            _orig_plot_data(self)
            try:
                draw_hodo_locator(self)
            except Exception:
                _render_api._LOGGER.exception("hodo_locator.draw_failed")

        _plot.plotData = plotData
        _plot._sharpmod_locator = True
    except Exception:  # pragma: no cover - vendored module always present
        _render_api._LOGGER.exception("hodo_locator.install_failed")
        raise


def _install_hodo_height_levels():
    """Annotate standard AGL levels on the live hodograph paint layer.

    The hodograph's ``plotBitMap`` is a logical-resolution raster cache. HD
    and UHD output scale that cache, so baking seven/eight-pixel numerals into
    it makes them visibly softer than text painted by the widget itself.
    Drawing after the normal ``paintEvent`` lets Qt rasterize the overlay
    directly for the active screen or export resolution.
    """
    try:
        import sharppy.viz.hodo as _hodo_mod
        from sharpmod.viz.hodo_levels import draw_hodo_height_levels

        _plot = _hodo_mod.plotHodo
        if getattr(_plot, "_sharpmod_height_levels", False):
            return

        def height_level_overlay(widget, painter):
            draw_hodo_height_levels(widget, painter=painter)

        if getattr(_plot, "_sharpmod_live_overlay_host", False):
            overlays = tuple(getattr(
                _plot, "_sharpmod_live_overlays", ()))
            _plot._sharpmod_live_overlays = overlays + (
                height_level_overlay,)
        else:
            # Defensive fallback for an unexpected upstream widget where the
            # label-fit paint host could not be installed.
            _orig_paint_event = _plot.paintEvent

            def paintEvent(self, event):
                _orig_paint_event(self, event)
                painter = _hodo_mod.QtGui.QPainter(self)
                try:
                    height_level_overlay(self, painter)
                except Exception:
                    _render_api._LOGGER.exception("hodo_height_levels.draw_failed")
                finally:
                    if painter.isActive():
                        painter.end()

            _plot.paintEvent = paintEvent
        _plot._sharpmod_height_levels = True
    except Exception:  # pragma: no cover - vendored module always present
        _render_api._LOGGER.exception("hodo_height_levels.install_failed")
        raise


def _install_skewt_level_labels_fit():
    """Keep the skew-T right-side level labels inside the plot frame.

    The vendored ``plotSkewT`` anchors the LCL/LFC/EL markers and the
    0 / -20 / -30 C height labels at the 37-41 C isotherm position, which sits
    right against the right plot border (``brx``). With the bundled (wider)
    font and the reimagined canvas sizing, the longer height labels
    (e.g. ``-30 C=30670'``) left-align *past* ``brx`` and spill into the
    wind-barb margin, so their trailing digits collide with / are cut off at
    the frame. These overrides clamp every such label so its right edge stays
    inside ``brx``, shifting it left only when it would otherwise overflow --
    labels that already fit keep their upstream placement. Marker tick lines
    are unchanged. Falls back to the vendored method on any error. Idempotent.
    """
    try:
        import sharppy.viz.skew as _skew_mod
        import sharppy.sharptab as _tab
        from sharpmod.viz.skew import parcel_level_markers
        _cls = _skew_mod.plotSkewT
        if getattr(_cls, "_sharpmod_level_fit", False):
            return
        _QtGui = _skew_mod.QtGui
        _QtCore = _skew_mod.QtCore
        _pad = 3

        _orig_parcel = _cls.draw_parcel_levels
        _orig_temp = _cls.draw_temp_levels

        def _fit_left(self, left, w):
            """Clamp a label's left x so [left, left+w] stays within the frame."""
            right_limit = self.brx - _pad
            if left + w > right_limit:
                left = right_limit - w
            if left < self.tlx + _pad:
                left = self.tlx + _pad
            return left

        def draw_parcel_levels(self, qp):
            try:
                qp.setClipping(True)
                x = self.tmpc_to_pix([37, 41], [1000., 1000.])
                qp.setFont(self.hght_font)
                fm = _QtGui.QFontMetrics(self.hght_font)
                cx = (float(x[0]) + float(x[1])) / 2.0
                flags = int(_QtCore.Qt.TextDontClip | _QtCore.Qt.AlignLeft)

                fh = fm.height()

                def _marker(p, color, above, text):
                    y = self.originy + self.pres_to_pix(p) / self.scale
                    qp.setPen(_QtGui.QPen(color, 2, _QtCore.Qt.SolidLine))
                    qp.drawLine(x[0], y, x[1], y)
                    w = fm.horizontalAdvance(text)
                    left = _fit_left(self, cx - w / 2.0, w)
                    # Place the label fully clear of the marker line: its bottom
                    # edge sits just above the line (above=True) or its top edge
                    # just below it (above=False), using the real font height so
                    # the (taller bundled) glyphs never straddle the tick.
                    if above:
                        top = y - _pad - fh
                    else:
                        top = y + _pad
                    qp.drawText(_QtCore.QRectF(left, top, w, fh), flags, text)

                colors = {
                    "LCL": self.lcl_mkr_color,
                    "LFC": self.lfc_mkr_color,
                    "EL": self.el_mkr_color,
                    "MPL": _semantic_qcolor(self, "mpl"),
                }
                for label, pressure in parcel_level_markers(self.pcl):
                    if not _tab.utils.QC(pressure):
                        continue
                    if label == "EL" and pressure == self.pcl.lclpres:
                        continue
                    _marker(
                        pressure,
                        colors[label],
                        label != "LCL",
                        label,
                    )
            except Exception:
                _orig_parcel(self, qp)

        def draw_temp_levels(self, qp):
            try:
                if self.pcl is None:
                    return
                x = self.tmpc_to_pix([37, 41], [1000., 1000.])
                lvls = [[self.pcl.p0c, self.pcl.hght0c, '0 C'],
                        [self.pcl.pm20c, self.pcl.hghtm20c, '-20 C'],
                        [self.pcl.pm30c, self.pcl.hghtm30c, '-30 C']]
                qp.setClipping(True)
                qp.setFont(self.hght_font)
                fm = _QtGui.QFontMetrics(self.hght_font)
                flags = int(_QtCore.Qt.TextDontClip | _QtCore.Qt.AlignLeft)
                fh = fm.height()
                for p, h, t in lvls:
                    try:
                        if not _tab.utils.QC(p):
                            continue
                        y = self.originy + self.pres_to_pix(p) / self.scale
                        qp.setPen(_QtGui.QPen(self.sig_temp_level_color, 2,
                                              _QtCore.Qt.SolidLine))
                        qp.drawLine(x[0], y, x[1], y)
                        text = t + '=' + _tab.utils.INT2STR(
                            _tab.utils.M2FT(h)) + '\''
                        w = fm.horizontalAdvance(text)
                        left = _fit_left(self, float(x[0]), w)
                        # Seat the label fully above the marker line using the
                        # real font height so the (taller bundled) glyphs never
                        # straddle / get clipped into the tick.
                        top = y - _pad - fh
                        qp.drawText(_QtCore.QRectF(left, top, w, fh),
                                    flags, text)
                    except Exception:
                        continue
            except Exception:
                _orig_temp(self, qp)

        _cls.draw_parcel_levels = draw_parcel_levels
        _cls.draw_temp_levels = draw_temp_levels
        _cls._sharpmod_level_fit = True
    except Exception:  # pragma: no cover - registry reports install failure
        raise


def _install_custom_barbs():
    """Use the speed-based wind-barb color table on the skew-T.

    The vendored ``sharppy.viz.skew`` does ``from sharppy.viz.barbs import
    drawBarb``, so it holds its own ``drawBarb`` reference. Rebinding
    ``skew.drawBarb`` (and the ``barbs`` module's) to the SHARPpy Reimagined custom
    version colors every wind barb by speed. Guarded + idempotent.
    """
    try:
        from sharpmod.viz import custom_barbs as _cb
        import sharppy.viz.skew as _skew_mod
        _skew_mod.drawBarb = _cb.drawBarb
        try:
            import sharppy.viz.barbs as _barbs_mod
            _barbs_mod.drawBarb = _cb.drawBarb
        except Exception:
            pass
    except Exception:  # pragma: no cover - registry reports install failure
        raise

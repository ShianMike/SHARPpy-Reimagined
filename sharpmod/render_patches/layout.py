"""Layout adjustments for headless sounding renders."""

from __future__ import annotations

from qtpy import QtGui
from sharpmod.viz.unit_text import apply_render_font_quality
from sharpmod.rendering import cli as _render_api


def tighten_pressure_labels(spc_widget):
    """Condense the skew-T axis labels so '1000' fits its box, then redraw."""
    s = getattr(spc_widget, "sound", None)
    if s is None or not hasattr(s, "label_font"):
        return
    try:
        f = s.label_font
        f.setStretch(_render_api.PLABEL_STRETCH)
        s.label_font = f
        # plotBackground() paints onto the existing bitmap WITHOUT clearing, so
        # blank it first or the original (clipped) labels remain underneath.
        s.plotBitMap.fill(s.bg_color)
        if hasattr(s, "plotBackground"):
            s.plotBackground()
        if hasattr(s, "clearData"):
            s.clearData()
        if hasattr(s, "plotData"):
            s.plotData()
        s.update()
    except Exception:
        pass


def shrink_title(spc_widget):
    """Shrink/condense the skew-T sounding title so it fits above the plot."""
    s = getattr(spc_widget, "sound", None)
    if s is None or not hasattr(s, "title_font"):
        return
    try:
        f = s.title_font
        ps = f.pointSizeF()
        if ps > 0 and _render_api.TITLE_FONT_SCALE != 1.0:
            f.setPointSizeF(ps * _render_api.TITLE_FONT_SCALE)
        if _render_api.TITLE_STRETCH and _render_api.TITLE_STRETCH != 100:
            f.setStretch(_render_api.TITLE_STRETCH)
        s.title_font = f
        if hasattr(s, "title_metrics"):
            s.title_metrics = QtGui.QFontMetrics(f)
        # Blank + redraw the skew-T bitmap so the title re-renders at new size.
        s.plotBitMap.fill(s.bg_color)
        if hasattr(s, "plotBackground"):
            s.plotBackground()
        if hasattr(s, "clearData"):
            s.clearData()
        if hasattr(s, "plotData"):
            s.plotData()
        s.update()
    except Exception:
        pass


def fill_table_panels(spc_widget):
    """Tighten the thermo/kinematics row pitch to reserve a bottom band.

    The SHARPpy Reimagined SFC-500 m kinematics and layer-thermodynamics rows are appended
    *into* the vendored ``kinematic`` / ``convective`` panels below their
    existing rows (see :func:`sharpmod.viz.SPCWindow.mount_products`). Those
    panels are fixed-height and already full, so this compresses the vendored
    row pitch (``label_height``) by :data:`TABLE_COMPRESS` and re-drives the
    vendored redraw. That lifts the vendored content up, opening a band at the
    bottom for the appended rows to fit without clipping the existing rows.

    The redraw calls the (wrapped) ``plotData``, so the appended SHARPpy Reimagined rows
    are drawn into the freed band as part of this pass. Guarded so a missing or
    renamed widget never crashes the render.
    """
    for name in ("convective", "kinematic"):
        wd = getattr(spc_widget, name, None)
        if wd is None or not hasattr(wd, "label_height"):
            continue
        try:
            # Spread the vendored rows to comfortably fill the panel (the
            # legacy roomy look); no band is reserved because the SHARPpy Reimagined
            # parameters now live in their own panels below (grid3 row 1).
            wd.label_height = int(round(wd.label_height * _render_api.TABLE_FILL))
            # Mirror SHARPpy's own setProf() redraw sequence with the new pitch.
            wd.ylast = wd.label_height
            if hasattr(wd, "clearData"):
                wd.clearData()
            if hasattr(wd, "plotBackground"):
                wd.plotBackground()
            if hasattr(wd, "plotData"):
                wd.plotData()
            wd.update()
        except Exception:
            continue


# font attr -> its font-metrics attr, per panel type.
_STP_FONTS = {"box_font": "box_metrics", "plot_font": "plot_metrics"}
_SARS_FONTS = {"title_font": "title_metrics", "plot_font": "plot_metrics",
               "match_font": "match_metrics"}


def enlarge_panel_fonts(spc_widget):
    """Enlarge the SARS and Effective Layer STP panel fonts for readability."""
    if _render_api.PANEL_FONT_BOOST == 1.0:
        return
    try:
        from sharppy.viz.stp import plotSTP
        from sharppy.viz.analogues import plotAnalogues
    except Exception:
        return

    def bump(wd, fontmap):
        try:
            for fattr, mattr in fontmap.items():
                f = getattr(wd, fattr, None)
                if f is None:
                    continue
                ps = f.pointSizeF()
                if ps > 0:
                    f.setPointSizeF(ps * _render_api.PANEL_FONT_BOOST)
                setattr(wd, fattr, f)
                # keep the matching metrics in sync so row spacing scales too
                if hasattr(wd, mattr):
                    setattr(wd, mattr, QtGui.QFontMetrics(f))
            for m in ("clearData", "plotBackground", "plotData"):
                if hasattr(wd, m):
                    getattr(wd, m)()
            wd.update()
        except Exception:
            pass

    try:
        # The STP panel is condensed (not boosted) by _install_stp_condense;
        # enlarging it here only worsened the Helvetica-layout overflow.
        for wd in spc_widget.findChildren(plotAnalogues):
            bump(wd, _SARS_FONTS)
    except Exception:
        pass


def _grow_for_family_panels(win):
    """Preserve the ``grid3`` panel height and make room for its wind barbs.

    The established lower scientific band needs :data:`CHART_HEIGHT_GROW` even
    without a footer; removing it compresses the parcel/index tables and the
    Streamwiseness/STP charts. Width growth keeps the storm-motion vectors and
    1/6-km wind barbs from clipping.

    It also grows the window/canvas *width* by :data:`CHART_WIDTH_GROW` so the
    widened bottom index board has room for the storm-motion vectors AND the
    1 km / 6 km AGL wind barbs beside them (see ``IndexBoard._col_kin``) without
    clipping either. Fully guarded: a missing widget or geometry hook never
    aborts the render.
    """
    grow_h = _render_api.CHART_HEIGHT_GROW
    grow_w = _render_api.CHART_WIDTH_GROW
    if grow_h <= 0 and grow_w <= 0:
        return
    try:
        sw = getattr(win, "spc_widget", None)
        if sw is None:
            return
        # Preserve the established text-band height and give the widened index
        # board enough width for the barbs beside the storm-motion vectors.
        text = getattr(sw, "text", None)
        if text is not None:
            try:
                if grow_h > 0:
                    # Immediately after mount, ``text.height()`` can still be
                    # the pre-layout value from the original four-column
                    # bottom band.  Adding the fifth Streamwiseness/STP column
                    # made that stale live value much taller than the settled
                    # one.  Grow from Qt's layout hints instead so the result
                    # is deterministic whether or not an event pass happened
                    # between mount and this function.
                    base_height = max(
                        1,
                        text.minimumHeight(),
                        text.minimumSizeHint().height(),
                        text.sizeHint().height(),
                    )
                    text.setMinimumHeight(base_height + grow_h)
                if grow_w > 0:
                    text.setMinimumWidth(max(text.width(), 1) + grow_w)
            except Exception:
                pass
        # Grow the top-level window and the grabbed canvas to match.
        try:
            win.resize(win.width() + grow_w, win.height() + grow_h)
        except Exception:
            pass
        try:
            sw.resize(sw.width() + grow_w, sw.height() + grow_h)
        except Exception:
            pass
    except Exception:
        # Geometry growth is best-effort; never break the base render.
        pass

def enlarge_canvas(win):
    """Grow the window + grabbed canvas so the charts gain absolute size.

    The outer-``grid`` stretch factors make the skew-T and hodograph *relatively*
    larger; this grows the overall canvas by :data:`CANVAS_GROW_W` /
    :data:`CANVAS_GROW_H` so that relative gain translates into an absolute size
    increase for the charts while the neighbor strips/insets -- sized from their
    content hints -- stay readable. Fully guarded so a missing geometry hook
    never aborts a render.
    """
    if _render_api.CANVAS_GROW_W <= 0 and _render_api.CANVAS_GROW_H <= 0:
        return
    try:
        win.resize(win.width() + _render_api.CANVAS_GROW_W, win.height() + _render_api.CANVAS_GROW_H)
    except Exception:
        pass
    try:
        sw = getattr(win, "spc_widget", None)
        if sw is not None:
            sw.resize(sw.width() + _render_api.CANVAS_GROW_W, sw.height() + _render_api.CANVAS_GROW_H)
    except Exception:
        pass


def application_label() -> str:
    """Return the branded label using the package's canonical version."""
    from sharpmod._version import __version__

    return f"SHARPpy Reimagined v{__version__}"


def _custom_emphasis_font(font):
    """Return ``font`` in the configured chart face with visible emphasis."""
    emphasized = QtGui.QFont(font)
    if _render_api.USE_CUSTOM_FONT:
        emphasized.setFamily(_render_api.FONT_FAMILY)
    emphasized.setWeight(QtGui.QFont.Weight.DemiBold)
    return apply_render_font_quality(emphasized)


def rebrand_version_label(win, text=None):
    """Rename the vendored top-right ``SHARPpy v...`` label to the fork's brand.

    Returns the label widget (or ``None``) so callers can align it. Guarded so a
    missing label never aborts a render.
    """
    text = application_label() if text is None else str(text)
    try:
        from qtpy.QtWidgets import QLabel
        for lbl in win.findChildren(QLabel):
            if lbl.text().startswith("SHARPpy"):
                lbl.setText(text)
                # This label is created by the vendored window before it is
                # rebranded.  Set the face explicitly instead of relying on
                # inherited application styling, which can be lost when the
                # widget's theme stylesheet is reapplied.
                lbl.setFont(_custom_emphasis_font(lbl.font()))
                return lbl
    except Exception:
        pass
    return None


def align_top_row(win):
    """Level the top frame: line the upper-right panel band up with the skew-T.

    The vendored :class:`~sharppy.viz.SPCWindow.SPCWidget` stacks the brand
    label in its own header row (``urparent_grid`` row 0) above the upper-right
    panel column (row 1), but the skew-T column has no equivalent header band --
    so the right-side panels' top border sits a few px below the skew-T plot
    border, stepping the top frame at the skew-T/hodograph seam. Re-styling the
    brand label's vertical padding to :data:`BRAND_PAD_TOP` /
    :data:`BRAND_PAD_BOTTOM` trims that header row so the panel band rises to
    meet the skew-T top border, giving a level top frame across the window.
    Fully guarded + idempotent (only rewrites the two padding declarations).
    """
    try:
        sw = getattr(win, "spc_widget", None)
        brand = getattr(sw, "brand", None) if sw is not None else None
        if brand is None:
            return
        ss = brand.styleSheet()
        for prop, val in (("padding-top", _render_api.BRAND_PAD_TOP),
                          ("padding-bottom", _render_api.BRAND_PAD_BOTTOM)):
            # Rewrite the existing "prop: Npx;" declaration (any current value).
            start = ss.find(prop + ":")
            if start != -1:
                end = ss.find(";", start)
                if end != -1:
                    ss = ss[:start] + f"{prop}: {val}px" + ss[end:]
        brand.setStyleSheet(ss)
    except Exception:
        pass


def apply_layout_compensation(spc_widget):
    """Apply the legacy layout-compensation passes, in the legacy order.

    The first three only compensate for a wider/taller custom font, so they run
    only when a custom font is in use; the panel-font enlargement always runs
    (it is a readability boost independent of the font choice). Each pass is
    individually guarded so a missing widget never crashes the render.

    The legacy ``tighten_haz_title`` pass is intentionally omitted: the Possible
    Hazard Type box it condensed is removed from the layout (Step 3), so there
    is no hazard-title panel left to compensate for.
    """
    if _render_api.USE_CUSTOM_FONT:
        tighten_pressure_labels(spc_widget)
        # Title shrink is owned by _install_skewt_title_shrink (survives
        # the later window resize); no one-time shrink_title here.
    # Always reserve a bottom band in the thermo/kinematics panels for the
    # appended SHARPpy Reimagined family rows, regardless of the font choice.
    fill_table_panels(spc_widget)
    enlarge_panel_fonts(spc_widget)
    # Enlarge the skew-T and hodograph relative to the tables/insets.
    _render_api.enlarge_charts(spc_widget)

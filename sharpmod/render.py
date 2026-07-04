"""Headless SHARPpy Reimagined renderer -- a de-shimmed port of ``render_sounding.py``.

This module renders the SPC-style sounding window (skew-T, hodograph, insets,
index tables) to a PNG headlessly, using the Qt ``offscreen`` platform so no
window ever appears on screen. The window itself is composed in
:mod:`sharpmod.viz.SPCWindow`.

It is the modernized successor to the legacy ``render_sounding.py``. All four
legacy compatibility shims carried by that script are **removed** here, each by
fixing the root cause rather than shimming (Requirement 11):

* **The ``imp`` module stub** (a Python 3.12+ workaround) is gone. Decoders are
  loaded through :mod:`sharpmod.io.decoder`, which already discovers custom
  decoders via :mod:`importlib` -- there is no ``imp`` reference anywhere
  (Requirement 11.1, 11.2).
* **The ``urlopen(cafile=...)`` wrapper** is gone. Remote inputs are fetched by
  the decoder's HTTPS path, which uses :func:`ssl.create_default_context`
  (server-certificate verification on) and passes it as ``context=`` to
  :func:`urllib.request.urlopen` -- no removed ``cafile`` keyword
  (Requirement 11.6). :func:`fetch_url` exposes the same verified transport for
  callers that need to pull a remote sounding to a local path first.
* **The ``StubParent`` fake Picker window** is gone. The renderer composes
  :class:`~sharppy.viz.SPCWindow.SPCWindow` with a real, minimal
  :class:`~sharpmod.viz.SPCWindow.RenderController` -- a purpose-built
  controller that owns the render :class:`~sutils.config.Config` and provides
  exactly the hooks ``SPCWindow`` connects to (a ``config_changed`` signal and
  a ``preferencesbox`` slot). It is never shown, so no fake parent window is
  instantiated (Requirement 11.4).
* **The PySide2 / Qt5 pin** is gone. Qt is imported through :mod:`qtpy` bound
  to PySide6 (Qt6); ``QT_API`` is pinned to ``pyside6`` before the first Qt
  import (Requirement 11.3).

On a per-input render failure the renderer raises :class:`RenderError` naming
the failing input and writes **no** partial PNG: output is written to a
temporary file and atomically renamed onto the destination only after a
non-empty image has been produced, so a failure never leaves a partial or
corrupt PNG and never disturbs a pre-existing output file
(Requirements 11.7, 15.5).
"""

from __future__ import annotations

import os
import ssl
import sys
import tempfile
from datetime import datetime

# --- Qt platform / binding setup (must precede the first Qt import) --------
# Render without a physical display. ``setdefault`` lets a caller override
# (e.g. to "xcb"/"windows" for an interactive debug run).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Pin the qtpy binding to PySide6 (Qt6); no PySide2/Qt5 fallback.
os.environ.setdefault("QT_API", "pyside6")

import certifi  # noqa: E402
from urllib.error import URLError  # noqa: E402
from urllib.request import urlopen  # noqa: E402

from qtpy import QtGui  # noqa: E402
from qtpy.QtWidgets import QApplication  # noqa: E402

# Restore Qt5-style unscoped enum access for the vendored ``sharppy.viz`` stack
# before importing any vendored widget, so they paint under Qt6/PySide6.
from sharpmod.viz import _qt6_compat  # noqa: E402

_qt6_compat.apply()

from sharppy.viz.preferences import PrefDialog  # noqa: E402
from sutils.config import Config  # noqa: E402

from sharpmod import colors  # noqa: E402
from sharpmod.io import decoder as decoder_mod  # noqa: E402
from sharpmod.resources import font_resolver  # noqa: E402
# Window composition (real minimal controller; no fake parent window). Importing
# it also performs the Qt ``offscreen``/PySide6 + bundled-font environment
# setup before the first Qt widget import.
from sharpmod.viz.SPCWindow import RenderController, compose_window  # noqa: E402

__all__ = ["RenderError", "RenderController", "fetch_url", "build_config",
           "decode", "render", "main", "install_font"]


# ===========================================================================
# Font install + layout compensation (faithful port of ``render_sounding.py``)
# ===========================================================================
#
# The legacy renderer installs the bundled TTF fonts, forces every ``QFont`` to
# the custom family, tightens the thermo/kinematics table row spacing, and then
# applies five layout-compensation passes to the composed window so a taller /
# wider custom font (Space Grotesk) still fits the vendored fixed-size panels.
# The modernized port keeps this behavior verbatim, except fonts are resolved
# *package-relative* through :mod:`sharpmod.resources.font_resolver` rather than
# an absolute development path (Requirement 15.2).

# Empty ``CHART_FONT`` keeps SHARPpy's own font; the default "Space Grotesk"
# matches the known-good reference. Only when a custom font is in use are the
# font-compensation layout tweaks applied.
FONT_FAMILY = os.environ.get("CHART_FONT", "Space Grotesk").strip()
USE_CUSTOM_FONT = bool(FONT_FAMILY)
FONT_STRETCH = int(os.environ.get("CHART_FONT_STRETCH", "100"))
FONT_SCALE = float(os.environ.get("CHART_FONT_SCALE", "1.0"))
TABLE_FONT_SCALE = float(os.environ.get("TABLE_FONT_SCALE", "1.0"))

# Layout-compensation tunables (env-overridable), values matching the legacy.
# NOTE: the legacy ``HAZ_TITLE_STRETCH`` / ``tighten_haz_title`` pass is
# intentionally NOT ported: the Possible Hazard Type box (the vendored
# ``watch_type`` widget) is removed from the layout in
# :func:`sharpmod.viz.SPCWindow.mount_products`, so there is no hazard-title
# panel left to condense (Step 3 -- feature placement rework).
PLABEL_STRETCH = int(os.environ.get("PLABEL_STRETCH", "100"))
TITLE_FONT_SCALE = float(os.environ.get("TITLE_FONT_SCALE", "0.90"))
# Left pad (px) for the skew-T so the 4-digit "1000" mb pressure label has room
# and is not clipped at the widget's left edge (vendored default is 30). The
# label is drawn right-aligned in a box of width ``lpad-4``, so this must be
# wide enough for the bold 4-digit "1000" -- widening it also thins the plot.
SKEWT_LPAD = int(os.environ.get("SKEWT_LPAD", "46"))
# Top y (px) for the skew-T title. The vendored default is 2. The title sits in
# the ~20 px band above the skew-T plot border, so it must be high enough to
# clear that border (a lower value clipped the title's descenders against it)
# while still lining up with the top-right brand label (see BRAND_PAD_*). This
# value keeps a small gap between the title and the border.
TITLE_TOP = int(os.environ.get("TITLE_TOP", "0"))
TITLE_STRETCH = int(os.environ.get("TITLE_STRETCH", "80"))
# Vertical padding (px) for the top-right brand label. The vendored SPCWidget
# stacks this label in its own header row above the upper-right panel column,
# while the skew-T column has no equivalent header band. These two values are
# tuned together so that (a) the brand text lines up with the skew-T title near
# the top, and (b) the label's total height keeps the upper-right panel band's
# top border level with the skew-T plot border (no step at the seam). Their sum
# sets the panel-band top; the split sets the brand's vertical position.
BRAND_PAD_TOP = int(os.environ.get("BRAND_PAD_TOP", "1"))
BRAND_PAD_BOTTOM = int(os.environ.get("BRAND_PAD_BOTTOM", "4"))
TABLE_FILL = float(os.environ.get("TABLE_FILL", "1.10"))
# Row-pitch compression factor for the vendored thermo/kinematics panels, so a
# bottom band opens up for the appended SHARPpy Reimagined family rows (mount_products).
TABLE_COMPRESS = float(os.environ.get("TABLE_COMPRESS", "0.80"))
TABLE_MIN_LABEL_HEIGHT = int(os.environ.get("TABLE_MIN_LABEL_HEIGHT", "7"))
PANEL_FONT_BOOST = float(os.environ.get("PANEL_FONT_BOOST", "1.18"))
# Horizontal condense (stretch %) for the vendored Effective Layer STP
# graphic. Its layout hard-codes Helvetica x-positions; the wider bundled
# Space Grotesk overflows them, so every font it builds is condensed to fit.
STP_FONT_STRETCH = int(os.environ.get("STP_FONT_STRETCH", "82"))
# Extra bottom padding (px) for the Effective Layer STP graphic so its
# x-axis labels (EF4+ ... NON) sit above the bottom edge rather than flush.
STP_BOTTOM_MARGIN = int(os.environ.get("STP_BOTTOM_MARGIN", "16"))
# Scale applied to the Effective Layer STP widget font_ratio so its axis-tick
# and EF x-axis labels render smaller (<1 shrinks them).
STP_LABEL_SCALE = float(os.environ.get("STP_LABEL_SCALE", "0.72"))
# Extra vertical space (px) added to the window/canvas so the SHARPpy Reimagined family
# panels -- placed as a second row in the vendored bottom table band -- render
# fully below the index tables without overlap (Step 4 -- in-grid placement).
CHART_HEIGHT_GROW = int(os.environ.get("CHART_HEIGHT_GROW", "120"))
# Extra horizontal space (px) added to the window/canvas so the widened bottom
# index board has room for the storm-motion vectors AND the 1 km / 6 km AGL
# wind barbs beside them without clipping either.
CHART_WIDTH_GROW = int(os.environ.get("CHART_WIDTH_GROW", "170"))

# Guards so per-process monkeypatches are installed at most once even across
# repeated ``render()`` calls (the test suite renders several inputs in-process).
_font_installed = False
_table_spacing_patched = False


def install_font(app):
    """Register the bundled TTFs and force every ``QFont`` to ``FONT_FAMILY``.

    A faithful port of the legacy ``install_font``: the vendored SHARPpy widgets
    hard-code 'Helvetica', so overriding the font means subclassing ``QFont`` to
    rewrite the family on construction and to apply ``TABLE_FONT_SCALE`` in
    ``setPixelSize`` (the lower table panels size their font in pixels after
    construction). Fonts resolve **package-relative** via
    :mod:`sharpmod.resources.font_resolver` -- never an absolute dev path
    (Requirement 15.2). When ``CHART_FONT`` is empty this is a no-op and SHARPpy
    uses its default font. Installed at most once per process.
    """
    global _font_installed
    if not USE_CUSTOM_FONT or _font_installed:
        return

    loaded = False
    try:
        for name in font_resolver.font_names():
            # Skip the variable-weight files ("[wght]") to avoid odd family
            # registrations; the static instances cover all weights/styles.
            if "[" in name:
                continue
            try:
                path = str(font_resolver.font_path(name))
            except Exception:
                continue
            if QtGui.QFontDatabase.addApplicationFont(path) != -1:
                loaded = True
    except Exception:
        loaded = False

    if not loaded:
        # No bundled fonts could be registered; leave SHARPpy on system fonts.
        return

    _OrigQFont = QtGui.QFont

    class _ForcedFont(_OrigQFont):
        def __init__(self, *args, **kwargs):
            if args and isinstance(args[0], str):
                # QFont(family, [pointSize, weight, italic, ...])
                super().__init__(FONT_FAMILY, *args[1:], **kwargs)
            else:
                # QFont(), QFont(other_font), QFont(variant)
                super().__init__(*args, **kwargs)
                self.setFamily(FONT_FAMILY)
            if FONT_STRETCH and FONT_STRETCH != 100:
                self.setStretch(FONT_STRETCH)
            if FONT_SCALE != 1.0:
                ps = self.pointSizeF()
                if ps > 0:
                    self.setPointSizeF(ps * FONT_SCALE)

        def setPixelSize(self, px):
            # The lower table panels (thermo/kinematics) size their font in
            # PIXELS after construction. A taller custom font overflows those
            # fixed-height panels, clipping the last rows, so scale pixel sizes
            # down by TABLE_FONT_SCALE here too.
            super().setPixelSize(max(1, int(round(px * TABLE_FONT_SCALE))))

    QtGui.QFont = _ForcedFont
    app.setFont(_ForcedFont(FONT_FAMILY, 9))
    _font_installed = True


def _apply_table_spacing_patch():
    """Loosen the thermo/kinematics table row spacing for the custom font.

    Those two vendored panels add the font's descent to each row's spacing ONLY
    on Windows (a workaround tuned for the original Helvetica). A taller custom
    font overflows the fixed-height panels and clips the last rows; SHARPpy's
    non-Windows spacing (no per-row descent) fits every row. Mirrors the legacy
    ``platform`` monkeypatch, applied only when a custom font is in use and at
    most once per process.
    """
    global _table_spacing_patched
    if not USE_CUSTOM_FONT or _table_spacing_patched:
        return

    class _NonWinPlatform:
        @staticmethod
        def system():
            return "Linux"

    try:
        import sharppy.viz.thermo as _thermo_mod
        import sharppy.viz.kinematics as _kinematics_mod
        _thermo_mod.platform = _NonWinPlatform
        _kinematics_mod.platform = _NonWinPlatform
        _table_spacing_patched = True
    except Exception:  # pragma: no cover - vendored modules always present
        pass


# --- Five layout-compensation passes (applied after compose + updateConfig) --
# Each is a faithful port of the legacy helper and is fully guarded so a missing
# or renamed widget never crashes the render.


def tighten_pressure_labels(spc_widget):
    """Condense the skew-T axis labels so '1000' fits its box, then redraw."""
    s = getattr(spc_widget, "sound", None)
    if s is None or not hasattr(s, "label_font"):
        return
    try:
        f = s.label_font
        f.setStretch(PLABEL_STRETCH)
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
        if ps > 0 and TITLE_FONT_SCALE != 1.0:
            f.setPointSizeF(ps * TITLE_FONT_SCALE)
        if TITLE_STRETCH and TITLE_STRETCH != 100:
            f.setStretch(TITLE_STRETCH)
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
            wd.label_height = int(round(wd.label_height * TABLE_FILL))
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
    if PANEL_FONT_BOOST == 1.0:
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
                    f.setPointSizeF(ps * PANEL_FONT_BOOST)
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
    """Grow the window/canvas to fit the ``grid3`` family-panel row + barbs.

    The SHARPpy Reimagined family panels are mounted as a second row inside the vendored
    bottom table band (``grid3``, owned by ``spc_widget.text``). To keep them
    from overlapping the index tables above, this grows the window height by
    :data:`CHART_HEIGHT_GROW`, resizes the grabbed ``spc_widget`` to match, and
    raises the ``text`` frame's minimum height so the layout allocates the extra
    space to the table band (rather than the expandable skew-T).

    It also grows the window/canvas *width* by :data:`CHART_WIDTH_GROW` so the
    widened bottom index board has room for the storm-motion vectors AND the
    1 km / 6 km AGL wind barbs beside them (see ``IndexBoard._col_kin``) without
    clipping either. Fully guarded: a missing widget or geometry hook never
    aborts the render.
    """
    grow_h = CHART_HEIGHT_GROW
    grow_w = CHART_WIDTH_GROW
    if grow_h <= 0 and grow_w <= 0:
        return
    try:
        sw = getattr(win, "spc_widget", None)
        if sw is None:
            return
        # Give the vendored text band (grid3 owner) room for the second row so
        # the added panels do not overlap the tables above them, and extra
        # width so the widened index board fits the barbs beside the vectors.
        text = getattr(sw, "text", None)
        if text is not None:
            try:
                if grow_h > 0:
                    text.setMinimumHeight(max(text.height(), 1) + grow_h)
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


def _install_skewt_mixratio_mask():
    """Size the skew-T mixing-ratio label's background mask to its text.

    The vendored ``backgroundSkewT.draw_mixing_ratios`` masks each green
    mixing-ratio value with a fixed 10x10 px background rect before drawing it.
    The wider/taller bundled font overflows that box, so the dry-adiabat and
    isotherm lines behind bleed through the digits. This replaces the method
    with a faithful port whose mask rect is sized from the label's font metrics
    (so it always fully covers the text), leaving everything else identical.
    Idempotent + fully guarded (per-call fallback to the vendored method).
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
                pen = _QtGui.QPen(self.bg_color, 1, _QtCore.Qt.SolidLine)
                brush = _QtGui.QBrush(self.bg_color, _QtCore.Qt.SolidPattern)
                qp.setPen(pen)
                qp.setBrush(brush)
                qp.drawRect(rectF)
                pen = _QtGui.QPen(self.mixr_color, 1, _QtCore.Qt.SolidLine)
                qp.setPen(pen)
                qp.drawLine(int(x1), int(y1), int(x2), int(y2))
                qp.drawText(rectF,
                            _QtCore.Qt.AlignBottom | _QtCore.Qt.AlignCenter,
                            label)
            except Exception:
                _orig(self, w, pmin, qp)

        _cls.draw_mixing_ratios = draw_mixing_ratios
        _cls._sharpmod_mixr_mask = True
    except Exception:  # pragma: no cover - vendored module always present
        pass


def _install_skewt_sfc_label_mask():
    """Size the skew-T surface trace-value label's background mask to its text.

    The vendored ``plotSkewT.drawTrace`` masks each surface temperature /
    dewpoint / wet-bulb value with a fixed 16x12 px background rect. The wider
    bundled font overflows it, so the surface isobar behind the number bleeds
    through the digit gaps. This replaces ``drawTrace`` with a faithful port
    that sizes the label's mask rect from the font metrics; the trace path and
    everything else are unchanged. Idempotent + guarded (per-call fallback).
    """
    cap = SFC_LABEL_MAX_PT
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

        def drawTrace(self, data, color, qp, width=3,
                      style=_QtCore.Qt.SolidLine, p=None, stdev=None,
                      label=True):
            try:
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
                    rect = _QtCore.QRectF(
                        x[0] - tw / 2.0 - pad_x, y[0] + 4 - pad_y,
                        tw + 2 * pad_x, th + 2 * pad_y)
                    qp.setPen(_QtGui.QPen(self.bg_color, 0,
                                          _QtCore.Qt.SolidLine))
                    qp.setBrush(_QtGui.QBrush(self.bg_color,
                                             _QtCore.Qt.SolidPattern))
                    qp.drawRect(rect)
                    qp.setPen(_QtGui.QPen(_QtGui.QColor(color), 3,
                                          _QtCore.Qt.SolidLine))
                    qp.drawText(rect, _QtCore.Qt.AlignCenter, lbl_str)
                    qp.setClipping(True)
            except Exception:
                _orig(self, data, color, qp, width=width, style=style, p=p,
                      stdev=stdev, label=label)

        _cls.drawTrace = drawTrace
        _cls._sharpmod_sfc_mask = True
    except Exception:  # pragma: no cover - vendored module always present
        pass


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
                pen = _QtGui.QPen(self.fg_color, 2, _QtCore.Qt.SolidLine)
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
    except Exception:  # pragma: no cover - vendored module always present
        pass


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
    except Exception:  # pragma: no cover - vendored module always present
        pass


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
    except Exception:  # pragma: no cover - vendored module always present
        pass


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
            grid.setRowStretch(0, CHART_ROW_STRETCH)
            grid.setRowStretch(1, CHART_ROW_STRETCH)
            grid.setRowStretch(2, CHART_ROW_STRETCH)
            grid.setRowStretch(3, TEXT_ROW_STRETCH)
            # Balance the skew-T column (0) against the hodo/insets panel (1).
            grid.setColumnStretch(0, SKEWT_COL_STRETCH)
            grid.setColumnStretch(1, URPANEL_COL_STRETCH)
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
                grid2.setColumnStretch(_c, SPEED_STRIP_COL_STRETCH)
            for _c in range(3, 5):
                grid2.setColumnStretch(_c, ADV_STRIP_COL_STRETCH)
            for _c in range(5, 29):
                grid2.setColumnStretch(_c, HODO_COL_STRETCH)
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
# so the taller bundled font spills past the widget edge and gets clipped;
# capping keeps them inside the slot.
SPEED_LABEL_MAX_PT = int(os.environ.get("SPEED_LABEL_MAX_PT", "12"))
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
_adv_font_cap_installed = False


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
    except Exception:  # pragma: no cover - vendored module always present
        pass


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
        # fixed 10 px-tall rect at ``bry+5``; the ~12 pt font (kept consistent
        # with the skew-T isotherm labels) spills past the widget's bottom edge
        # and is clipped. Redraw them in the full bottom-pad slot (``bry+2`` ..
        # widget bottom) so the larger, consistent font fits. Each label already
        # owns a wide 40-kt slot, so there is no horizontal crowding.
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
                    pen = _QtGui_s.QPen(_QtGui_s.QColor(self.fg_color), 1,
                                        _Qt.DashLine)
                    qp.setPen(pen)
                    qp.drawText(int(labelx1), int(self.bry + 2),
                                int(label_width), int(self.bpad - 2),
                                _Qt.AlignTop | _Qt.AlignCenter, str(int(s)))
            except Exception:
                _orig_draw(self, s, qp, delta=delta, drawlabel=drawlabel)

        _cls.draw_speed = draw_speed
        _cls._sharpmod_title_cap = True
        _speed_title_cap_installed = True
    except Exception:  # pragma: no cover - vendored module always present
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
    if CANVAS_GROW_W <= 0 and CANVAS_GROW_H <= 0:
        return
    try:
        win.resize(win.width() + CANVAS_GROW_W, win.height() + CANVAS_GROW_H)
    except Exception:
        pass
    try:
        sw = getattr(win, "spc_widget", None)
        if sw is not None:
            sw.resize(sw.width() + CANVAS_GROW_W, sw.height() + CANVAS_GROW_H)
    except Exception:
        pass


def rebrand_version_label(win, text="SHARPpy Reimagined v0.1"):
    """Rename the vendored top-right ``SHARPpy v...`` label to the fork's brand.

    Returns the label widget (or ``None``) so callers can align it. Guarded so a
    missing label never aborts a render.
    """
    try:
        from qtpy.QtWidgets import QLabel
        for lbl in win.findChildren(QLabel):
            if lbl.text().startswith("SHARPpy"):
                lbl.setText(text)
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
        for prop, val in (("padding-top", BRAND_PAD_TOP),
                          ("padding-bottom", BRAND_PAD_BOTTOM)):
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
    if USE_CUSTOM_FONT:
        tighten_pressure_labels(spc_widget)
        # Title shrink is owned by _install_skewt_title_shrink (survives
        # the later window resize); no one-time shrink_title here.
    # Always reserve a bottom band in the thermo/kinematics panels for the
    # appended SHARPpy Reimagined family rows, regardless of the font choice.
    fill_table_panels(spc_widget)
    enlarge_panel_fonts(spc_widget)
    # Enlarge the skew-T and hodograph relative to the tables/insets.
    enlarge_charts(spc_widget)


class RenderError(RuntimeError):
    """Raised when rendering a single input fails.

    The failing input is always named so a caller can report exactly which
    sounding could not be rendered (Requirements 11.7, 15.5). No partial PNG is
    written when this is raised.
    """

    def __init__(self, infile: str, message: str,
                 cause: BaseException | None = None):
        self.infile = infile
        self.cause = cause
        super().__init__(f"failed to render {infile!r}: {message}")


# ---------------------------------------------------------------------------
# Secure remote fetch (replaces the legacy ``urlopen(cafile=...)`` wrapper)
# ---------------------------------------------------------------------------


def fetch_url(url: str, timeout: float = 30.0) -> bytes:
    """Fetch ``url`` over HTTPS with server-certificate verification enabled.

    Uses :func:`ssl.create_default_context` (verification on) passed as
    ``context=`` to :func:`urllib.request.urlopen`, with the ``certifi`` CA
    bundle. This is the modern replacement for the removed
    ``urlopen(cafile=...)`` shim (Requirement 11.6).
    """
    context = ssl.create_default_context(cafile=certifi.where())
    try:
        with urlopen(url, timeout=timeout, context=context) as response:
            return response.read()
    except URLError as exc:  # surface TLS/network failures descriptively
        raise RenderError(url, f"remote fetch failed: {exc}", cause=exc)


# ---------------------------------------------------------------------------
# Config construction
# ---------------------------------------------------------------------------


def build_config(out_dir: str) -> Config:
    """Build the render :class:`Config`, applying the documented Color Scheme.

    Mirrors the launcher's config bootstrap (``PrefDialog.initConfig``) and
    then applies the modernized alert-tier substitutions from
    :mod:`sharpmod.colors` (Requirement 22.3), so the two lowest amber tiers
    are legible against the black background instead of the near-unreadable
    legacy dark browns.
    """
    cfg_path = os.path.join(out_dir, "sharpmod_render.ini")
    config = Config(cfg_path)
    PrefDialog.initConfig(config)

    # Brighten the two lowest alert tiers (Requirement 22.3). Values are sourced
    # from the documented Color Scheme rather than hard-coded here.
    config["preferences", "alert_l1_color"] = colors.ALERT_L1_COLOR
    config["preferences", "alert_l2_color"] = colors.ALERT_L2_COLOR

    config.initialize({("paths", "save_img"): out_dir,
                       ("paths", "save_txt"): out_dir,
                       ("paths", "load_txt"): out_dir})
    return config


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


_stp_condense_installed = False


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
                if ps > 0 and TITLE_FONT_SCALE != 1.0:
                    f.setPointSizeF(ps * TITLE_FONT_SCALE)
                if TITLE_STRETCH and TITLE_STRETCH != 100:
                    f.setStretch(TITLE_STRETCH)
                self.title_font = f
                self.title_metrics = QtGui.QFontMetrics(f)
            except Exception:
                pass
            # Widen the left pad so the 4-digit "1000" mb pressure label isn't
            # clipped at the widget's left edge (its label rect is lpad-4 wide).
            try:
                if SKEWT_LPAD and self.lpad < SKEWT_LPAD:
                    self.lpad = SKEWT_LPAD
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
                if PLABEL_STRETCH and PLABEL_STRETCH != 100:
                    lf.setStretch(PLABEL_STRETCH)
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
    except Exception:  # pragma: no cover - vendored module always present
        pass


HODO_0_500_COLOR = os.environ.get("HODO_0_500_COLOR", "#FF00FF")

# Default hodograph zoom, expressed as the knots magnitude spanning the full
# widget width. The x-axis reaches +/- HODO_ZOOM_KTS / 2, so 200 kt -> x up to
# 100 kt (and y up to ~70 kt at the widget's ~0.7 aspect ratio).
HODO_ZOOM_KTS = float(os.environ.get("HODO_ZOOM_KTS", "200"))


def _install_hodo_0500():
    """Add a distinct 0-500 m band to the hodograph height coloring.

    The vendored ``plotHodo.draw_hodo`` colors segments at 0-1/1-3/3-6/6-9/9-12
    km. This overrides it to insert a 500 m boundary so the innermost 0-500 m of
    the hodograph is drawn in :data:`HODO_0_500_COLOR`, then the usual config
    band colors. Falls back to the original on any error. Idempotent.
    """
    try:
        import sharppy.viz.hodo as _hodo_mod
        import sharppy.sharptab as _tab
        import numpy as _np
        _cls = _hodo_mod.plotHodo
        if getattr(_cls, "_sharpmod_0500", False):
            return
        _QtGui = _hodo_mod.QtGui
        _QtCore = _hodo_mod.QtCore
        try:
            from qtpy.QtGui import QPainterPath as _QPP
        except Exception:
            _QPP = _QtGui.QPainterPath
        _c0500 = _QtGui.QColor(HODO_0_500_COLOR)
        _orig = _cls.draw_hodo

        def draw_hodo(self, qp, prof, colors, width=2):
            try:
                try:
                    mask = _np.maximum(_np.maximum(prof.u.mask, prof.v.mask),
                                       prof.hght.mask)
                    z = _tab.interp.to_agl(prof, prof.hght)[~mask]
                    u = prof.u[~mask]; v = prof.v[~mask]
                except Exception:
                    z = _tab.interp.to_agl(prof, prof.hght)
                    u = prof.u; v = prof.v
                xx, yy = self.uv_to_pix(u, v)
                # Insert a single 500 m boundary into the vendored band edges
                # (0-3/3-6/6-9/9-12 km). This yields five segments whose colors
                # line up 1:1 with ``[0-500 m] + colors`` -- the 0-500 m band is
                # magenta and the remaining bands keep their configured colors
                # (500 m-3 km, 3-6, 6-9, 9-12). The previous version inserted a
                # 1000 m edge too, which shifted every band's color and ran the
                # colors list out of range.
                seg_bnds = _np.maximum(
                    [0., 500., 3000., 6000., 9000., 12000.], z.min())
                hcolors = [_c0500] + list(colors)   # 0-500 m + the 4 bands
                seg_x = [_tab.interp.generic_interp_hght(b, z, xx)
                         for b in seg_bnds if b <= z.max()]
                seg_y = [_tab.interp.generic_interp_hght(b, z, yy)
                         for b in seg_bnds if b <= z.max()]
                seg_idxs = _np.searchsorted(z, seg_bnds)
                for idx in range(len(seg_x) - 1):
                    pen = _QtGui.QPen(hcolors[idx], width)
                    pen.setStyle(_QtCore.Qt.SolidLine)
                    qp.setPen(pen)
                    path = _QPP()
                    path.moveTo(seg_x[idx], seg_y[idx])
                    for z_idx in range(seg_idxs[idx], seg_idxs[idx + 1]):
                        path.lineTo(xx[z_idx], yy[z_idx])
                    path.lineTo(seg_x[idx + 1], seg_y[idx + 1])
                    qp.drawPath(path)
                if z.max() < max(seg_bnds):
                    idx = len(seg_x) - 1
                    pen = _QtGui.QPen(hcolors[idx], width)
                    pen.setStyle(_QtCore.Qt.SolidLine)
                    qp.setPen(pen)
                    path = _QPP()
                    path.moveTo(seg_x[idx], seg_y[idx])
                    for z_idx in range(seg_idxs[idx], len(xx)):
                        path.lineTo(xx[z_idx], yy[z_idx])
                    qp.drawPath(path)
            except Exception:
                _orig(self, qp, prof, colors, width=width)

        _cls.draw_hodo = draw_hodo
        _cls._sharpmod_0500 = True
    except Exception:  # pragma: no cover - vendored module always present
        pass


def _install_hodo_zoom():
    """Zoom the hodograph out so the x-axis reaches ~100 kt (y ~70 kt).

    The vendored ``backgroundHodo`` scales uniformly from ``hodomag`` -- the
    wind magnitude (in the active units) that spans the full widget width --
    centered on the origin, so the x-axis extends +/- ``hodomag`` / 2. Upstream
    defaults to ``hodomag = 160`` kt (x reaches +/- 80 kt), with the y-axis
    following the widget's ~0.7 aspect ratio (~+/- 56 kt). Bumping the knots
    default to :data:`HODO_ZOOM_KTS` (200 kt) makes the x-axis reach +/- 100 kt
    and, at that aspect ratio, the y-axis reach ~+/- 70 kt. The metric default
    is scaled proportionally (kept within the vendored ``max_zoom``). Applied by
    wrapping ``backgroundHodo.__init__`` (initial draw) and
    ``plotHodo.setPreferences`` (units / preference changes) so the zoom
    survives both. Falls back to the vendored behavior on any error. Idempotent.
    """
    try:
        import sharppy.viz.hodo as _hodo_mod
        import sharppy.sharptab as _tab
        import numpy as _np

        _bg = _hodo_mod.backgroundHodo
        _plot = _hodo_mod.plotHodo
        if getattr(_bg, "_sharpmod_zoom", False):
            return

        _kts = float(HODO_ZOOM_KTS)
        # Metric equivalent, rounded to the vendored 5 m/s ring increment and
        # clamped to the vendored metric max_zoom (100 m/s) so zoom-out stays
        # in bounds.
        _ms = min(round(_tab.utils.KTS2MS(_kts) / 5.0) * 5.0, 100.0)

        def _apply_zoom(self):
            """Override hodomag for the active units and recompute scale/rings."""
            try:
                if getattr(self, "wind_units", "knots") == "m/s":
                    self.hodomag = _ms
                    self.max_zoom = max(getattr(self, "max_zoom", 0.0), _ms)
                    conv = _tab.utils.KTS2MS
                else:
                    self.hodomag = _kts
                    self.max_zoom = max(getattr(self, "max_zoom", 0.0), _kts)
                    conv = lambda s: s
                self.scale = (self.brx - self.tlx) / self.hodomag
                max_uv = int(conv(_np.hypot(*self.pix_to_uv(self.brx, self.bry))))
                self.rings = range(self.ring_increment,
                                   max_uv + self.ring_increment,
                                   self.ring_increment)
            except Exception:
                pass

        _orig_init = _bg.__init__

        def __init__(self, **kwargs):
            _orig_init(self, **kwargs)
            _apply_zoom(self)
            # Rebuild the background pixmap with the zoomed-out scale.
            try:
                self.plotBitMap.fill(self.bg_color)
                self.plotBackground()
                self.backgroundBitMap = self.plotBitMap.copy()
            except Exception:
                pass

        _orig_prefs = _plot.setPreferences

        def setPreferences(self, update_gui=True, **kwargs):
            _orig_prefs(self, update_gui=False, **kwargs)
            _apply_zoom(self)
            try:
                self.plotBitMap.fill(self.bg_color)
                self.plotBackground()
                self.backgroundBitMap = self.plotBitMap.copy()
            except Exception:
                pass
            if update_gui:
                try:
                    self.clearData()
                    self.plotData()
                    self.update()
                    self.parentWidget().setFocus()
                except Exception:
                    pass

        _bg.__init__ = __init__
        _plot.setPreferences = setPreferences
        _bg._sharpmod_zoom = True
    except Exception:  # pragma: no cover - vendored module always present
        pass


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

                if _tab.utils.QC(self.pcl.lclpres):
                    _marker(self.pcl.lclpres, self.lcl_mkr_color, False, "LCL")
                if _tab.utils.QC(self.pcl.lfcpres):
                    _marker(self.pcl.lfcpres, self.lfc_mkr_color, True, "LFC")
                if _tab.utils.QC(self.pcl.elpres) and \
                        self.pcl.elpres != self.pcl.lclpres:
                    _marker(self.pcl.elpres, self.el_mkr_color, True, "EL")
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
    except Exception:  # pragma: no cover - vendored module always present
        pass


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
    except Exception:  # pragma: no cover - vendored module always present
        pass


def _install_title_top():
    """Nudge the skew-T title up by drawing it at ``TITLE_TOP`` instead of y=2.

    Overrides ``plotSkewT.drawTitles`` with a copy that uses the configurable
    top offset; on any error it falls back to the vendored method so the render
    never breaks. Idempotent.
    """
    if TITLE_TOP == 2:
        return
    try:
        import sharppy.viz.skew as _skew_mod
        _cls = _skew_mod.plotSkewT
        if getattr(_cls, "_sharpmod_title_top", False):
            return
        _orig = _cls.drawTitles
        _QtCore = _skew_mod.QtCore
        _QtGui = _skew_mod.QtGui
        _top = TITLE_TOP

        def drawTitles(self, qp):
            try:
                box_width = 150
                cur_dt = self.prof_collections[self.pc_idx].getCurrentDate()
                idxs, titles = list(zip(*[
                    (idx, self.getPlotTitle(pc))
                    for idx, pc in enumerate(self.prof_collections)
                    if pc.getCurrentDate() == cur_dt or self.all_observed]))
                titles = list(titles)
                main_title = titles.pop(idxs.index(self.pc_idx))
                qp.setClipping(False)
                qp.setFont(self.title_font)
                qp.setPen(_QtGui.QPen(self.fg_color, 1, _QtCore.Qt.SolidLine))
                rect0 = _QtCore.QRect(self.lpad, _top, box_width, self.title_height)
                qp.drawText(rect0, _QtCore.Qt.TextDontClip | _QtCore.Qt.AlignLeft,
                            main_title)
                bg = 0
                for idx, title in enumerate(titles):
                    qp.setPen(_QtGui.QPen(
                        _QtGui.QColor(self.background_colors[bg]), 1,
                        _QtCore.Qt.SolidLine))
                    rect0 = _QtCore.QRect(self.width() - box_width,
                                          _top + idx * self.title_height,
                                          box_width, self.title_height)
                    qp.drawText(rect0,
                                _QtCore.Qt.TextDontClip | _QtCore.Qt.AlignRight,
                                title)
                    bg = (bg + 1) % len(self.background_colors)
            except Exception:
                _orig(self, qp)

        _cls.drawTitles = drawTitles
        _cls._sharpmod_title_top = True
    except Exception:  # pragma: no cover - vendored module always present
        pass


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
    stretch = STP_FONT_STRETCH
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
    except Exception:  # pragma: no cover - vendored module always present
        pass


def _install_stp_bottom_margin():
    """Give the Effective Layer STP graphic a small bottom margin.

    The vendored widget centres its x-axis labels at ``bry + bpad`` which
    equals ``height - bpad`` -- nearly flush with the bottom edge. Wrapping
    ``backgroundSTP.initUI`` to enlarge ``bpad`` (and recompute ``hgt`` /
    ``bry``) shifts the plot and its labels up, opening a margin below.
    ``plotSTP`` inherits ``initUI``, so both the background and data layers
    use the new geometry. Idempotent + guarded.
    """
    if STP_BOTTOM_MARGIN <= 0:
        return
    try:
        import sharppy.viz.stp as _stp_mod
        _cls = _stp_mod.backgroundSTP
        if getattr(_cls, "_sharpmod_margin", False):
            return
        _orig_initUI = _cls.initUI
        _extra = STP_BOTTOM_MARGIN

        def initUI(self):
            _orig_initUI(self)
            try:
                self.bpad = self.bpad + _extra
                self.hgt = self.size().height() - self.bpad
                self.bry = self.hgt - self.bpad
                # Shrink the inline draw-time fonts (y-ticks + EF x-axis
                # labels) without touching the stored title/box fonts.
                if STP_LABEL_SCALE and STP_LABEL_SCALE != 1.0:
                    self.font_ratio = self.font_ratio * STP_LABEL_SCALE
                # Clear first: the original initUI already drew the frame at
                # the old geometry; redraw on a blank bitmap to avoid a
                # doubled (ghosted) render.
                self.plotBitMap.fill(self.bg_color)
                self.plotBackground()
            except Exception:
                pass

        _cls.initUI = initUI
        _cls._sharpmod_margin = True
    except Exception:  # pragma: no cover - vendored module always present
        pass


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
    except Exception:  # pragma: no cover - vendored module always present
        pass


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
    except Exception:  # pragma: no cover - vendored module always present
        pass


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
                    box_col = _QtGui.QColor(hexc) if hexc else self.box_color
                    lbl_col = _QtGui.QColor(hexc) if hexc else self.fg_color
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
    except Exception:  # pragma: no cover - vendored module always present
        pass


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
                _fm = self.box_metrics
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
                # Anchor the box against the inset's right edge (but never left
                # of the mid-line, so it can't overlap the EF box-and-whisker
                # plot on the left half).
                right_x = self.brx - 5.
                left_x = max(width * 7, right_x - (content_w + 8))

                # One consistent row height for both header and data rows.
                row_h = self.box_height + 1
                if _platform.system() == "Windows":
                    row_h += self.box_metrics.descent()

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

                qp.setFont(self.box_font)
                text_w = right_x - left_x - 3
                x1 = left_x + 3
                x2 = x1 + label_w + col_gap
                y1 = top_y + 2

                ## header/title rows
                pen = _QtGui.QPen(self.fg_color, 1, _QtCore.Qt.SolidLine)
                qp.setPen(pen)
                for text in ['Prob EF2+ torn with supercell',
                             'Sample CLIMO = .15 sigtor']:
                    rect = _QtCore.QRectF(x1, y1, text_w, self.box_height)
                    qp.drawText(
                        rect, _QtCore.Qt.TextDontClip | _QtCore.Qt.AlignLeft, text)
                    y1 += row_h

                ## divider rule, centred in its gap
                div_y = y1 + div_gap / 2.
                qp.drawLine(left_x, div_y, right_x, div_y)
                y1 += div_gap

                ## variable rows
                texts = ['based on CAPE:', 'based on LCL:', 'based on ESRH:',
                         'based on EBWD:', 'based on STPC:', 'based on STP_fixed:']
                probs = [self.cape_p, self.lcl_p, self.esrh_p,
                         self.ebwd_p, self.stpc_p, self.stpf_p]
                colors = [self.cape_c, self.lcl_c, self.esrh_c,
                          self.ebwd_c, self.stpc_c, self.stpf_c]
                for text, p, c in zip(texts, probs, colors):
                    qp.setPen(_QtGui.QPen(c, 1, _QtCore.Qt.SolidLine))
                    rect = _QtCore.QRectF(x1, y1, text_w, self.box_height)
                    rect2 = _QtCore.QRectF(x2, y1, text_w, self.box_height)
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
    except Exception:  # pragma: no cover - vendored module always present
        pass


_title_override_installed = False


def _install_title_override():
    """Format the skew-T title as ``MODEL DATE HHz, Fxxx  VALID: Ddd DATE HHz @lat lon``.

    Overrides the vendored ``plotSkewT.getPlotTitle`` (a read-only presentation
    change) so the heading reads, e.g.::

        HRRR 2026-07-03 10z, F000  VALID: Fri 2026-07-03 10z @41.54N 92.93W

    Lat/lon come from the collection meta (set by the ``.npz`` loader) or the
    profile's ``latitude``/``longitude``; the ``@lat lon`` clause is omitted when
    unavailable. Installed at most once per process; fully guarded.
    """
    global _title_override_installed
    if _title_override_installed:
        return

    def _meta(pc, key):
        try:
            return pc.getMeta(key)
        except Exception:
            return None

    def getPlotTitle(self, prof_coll):
        model = _meta(prof_coll, "model") or ""
        run = _meta(prof_coll, "run")
        base = _meta(prof_coll, "base_time") or run
        try:
            valid = prof_coll.getCurrentDate()
        except Exception:
            valid = run
        fhr = 0
        try:
            fhr = int((valid - base).total_seconds() / 3600)
        except Exception:
            fhr = 0
        run_s = run.strftime("%Y-%m-%d %Hz") if run is not None else ""
        valid_s = ("Valid: " + valid.strftime("%a %Y-%m-%d %Hz")
                   ) if valid is not None else ""
        # Leading spaces indent the left-aligned title off the plot's left
        # frame line so it isn't flush against the border.
        title = "   %s %s F%03d  %s" % (model, run_s, fhr, valid_s)

        lat = _meta(prof_coll, "lat")
        lon = _meta(prof_coll, "lon")
        if lat is None:
            lat = getattr(getattr(self, "prof", None), "latitude", None)
        if lon is None:
            lon = getattr(getattr(self, "prof", None), "longitude", None)
        try:
            if lat is not None and lon is not None:
                latf = float(lat)
                lonf = float(lon)
                ns = "N" if latf >= 0 else "S"
                ew = "E" if lonf >= 0 else "W"
                title += "  @%.2f\u00b0%s %.2f\u00b0%s" % (
                    abs(latf), ns, abs(lonf), ew)
        except (TypeError, ValueError):
            pass
        return title

    try:
        import sharppy.viz.skew as _skew_mod
        _skew_mod.plotSkewT.getPlotTitle = getPlotTitle
        _title_override_installed = True
    except Exception:  # pragma: no cover - vendored module always present
        pass


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


def decode(infile: str):
    """Decode ``infile`` into a profile collection and its station id.

    ``.npz`` point-sounding sidecars go through
    :func:`sharpmod.io.decoder.load_npz` (which preserves the OMEGA column);
    every other input is tried against each registered decoder from
    :func:`sharpmod.io.decoder.getDecoders`. Raises :class:`RenderError` naming
    ``infile`` if no decoder can read it.
    """
    if infile.lower().endswith(".npz"):
        return decoder_mod.load_npz(infile)

    last_err: BaseException | None = None
    for name, cls in decoder_mod.getDecoders().items():
        try:
            dec = cls(infile)
            prof_col = dec.getProfiles()
            stn_id = dec.getStnId()
            return prof_col, stn_id
        except Exception as exc:  # noqa: BLE001 - try every decoder in turn
            last_err = exc
            continue
    raise RenderError(infile, f"no decoder could read it: {last_err}",
                      cause=last_err)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render(infile: str, outfile: str = "sharpmod_sounding.png",
           model: str | None = None, run: datetime | None = None,
           loc: str | None = None) -> str:
    """Render ``infile`` to ``outfile`` and return the output path.

    Composes :class:`~sharppy.viz.SPCWindow.SPCWindow` with a real
    :class:`~sharpmod.viz.SPCWindow.RenderController` (no fake parent window)
    via :func:`sharpmod.viz.SPCWindow.compose_window`, renders headlessly via
    the Qt ``offscreen`` platform, and writes the PNG atomically: the image is
    produced into a temporary file in the destination directory and renamed
    onto ``outfile`` only after a non-empty image exists. On any failure a
    :class:`RenderError` naming ``infile`` is raised and no partial PNG is left
    behind (Requirements 11.4, 11.7, 15.5).
    """
    out_dir = os.path.dirname(os.path.abspath(outfile))
    os.makedirs(out_dir, exist_ok=True)

    try:
        app = QApplication.instance() or QApplication(sys.argv)
        # Install the bundled fonts and force the custom family BEFORE any
        # widget is constructed (Requirement 15.2 / layout parity).
        install_font(app)

        config = build_config(out_dir)
        _apply_sars_match_color()
        _install_title_override()
        _install_skewt_title_shrink()
        _install_title_top()
        _install_custom_barbs()
        _install_hodo_0500()
        _install_hodo_zoom()
        _install_skewt_level_labels_fit()
        # Condense the vendored STP graphic fonts before it is constructed.
        _install_stp_condense()
        _install_stp_label_rename()
        _install_stp_xlabel_colors()
        _install_stp_bottom_margin()
        # Shrink the STP prob box so it is more compact on the enlarged canvas.
        _install_stp_box_shrink()
        _install_stp_prob_box_spacing()
        # Cap the wind-speed strip title so it never overflows on a wide strip.
        _install_speed_title_cap()
        # Cap the temp-advection strip title + axis labels for the same reason.
        _install_advection_font_cap()
        # Size the skew-T mixing-ratio + surface-value label masks to the font
        # so background lines stop bleeding through the (wider-font) digits.
        _install_skewt_mixratio_mask()
        _install_skewt_sfc_label_mask()
        # Redraw the white skew-T outline on top so label masks never gap it.
        _install_skewt_frame_ontop()
        # Keep the bottom isotherm labels inside the widget (no bottom clip).
        _install_skewt_isotherm_label_fit()
        # Keep the Storm Slinky title inside the widget (no descender clip).
        _install_slinky_title_fit()
        # Loosen thermo/kinematics row spacing for the taller custom font,
        # before the vendored panels are constructed/drawn.
        _apply_table_spacing_patch()

        prof_col, stn_id = decode(infile)

        if model is not None:
            prof_col.setMeta("model", model)
        if run is not None:
            prof_col.setMeta("run", run)
        if loc is not None:
            prof_col.setMeta("loc", loc)

        # Fill in the metadata the title/header rendering dereferences, without
        # clobbering what the decoder already worked out.
        has = lambda k: k in prof_col._meta  # noqa: E731
        base = prof_col.getMeta("base_time") if has("base_time") \
            else prof_col.getCurrentDate()
        observed = prof_col.getMeta("observed") if has("observed") else True
        if not has("loc"):
            prof_col.setMeta("loc", stn_id)
        if not has("run"):
            prof_col.setMeta("run", base)
        if not has("model"):
            prof_col.setMeta("model", "Archive" if observed else "Model")

        # Compose SPCWindow with the real minimal controller. The controller is
        # the Qt parent SPCWindow connects its config/preferences hooks to; it
        # must outlive the window, so keep a reference for the render duration.
        # ``mount=True`` appends the SHARPpy Reimagined derived-parameter family rows INTO
        # the vendored index panels (kinematics, thermodynamics, and the STP
        # composite area) and attaches the skew-T HGZ overlay -- no strip is
        # added and the canvas keeps its original size. The mount is fully
        # guarded (see ``mount_products``); the outcome is recorded on
        # ``win.sharpmod_products`` for inspection.
        win, controller = compose_window(config, prof_col, mount=True)

        # Rebrand the vendored version label (top-right "SHARPpy v..." QLabel)
        # and level the top frame so the upper-right panel band lines up with
        # the skew-T top border (see align_top_row).
        rebrand_version_label(win)
        align_top_row(win)

        # Apply the five legacy layout-compensation passes to the composed
        # widget, in the legacy order, after ``compose_window`` has run its
        # ``updateConfig(update_gui=True)`` re-apply. Guarded so a missing
        # widget never crashes the render.
        apply_layout_compensation(win.spc_widget)

        # The SHARPpy Reimagined family panels are placed INSIDE the vendored bottom
        # table band (``grid3``) as a second row beneath their family columns
        # (see ``mount_products``). That new row needs vertical room, so grow
        # the window/canvas height by ``CHART_HEIGHT_GROW`` and give the
        # vendored text frame (which owns ``grid3``) a matching larger minimum
        # height, so the panels render fully below the index tables without
        # overlapping them. Fully guarded so a missing widget never aborts the
        # render.
        _grow_for_family_panels(win)

        # Grow the overall canvas so the outer-grid stretch (which makes the
        # skew-T and hodograph relatively larger) translates into an absolute
        # size gain for the charts, without squeezing their neighbor strips and
        # insets.
        enlarge_canvas(win)

        # Force a few paint passes so the resized layout settles before the
        # pixmap grab.
        for _ in range(6):
            app.processEvents()

        # Atomic write: render to a temp file in the destination directory,
        # verify it is a non-empty image, then rename onto the destination.
        fd, tmp_path = tempfile.mkstemp(suffix=".png", dir=out_dir)
        os.close(fd)
        try:
            win.spc_widget.pixmapToFile(tmp_path)
            if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
                raise RenderError(infile, "renderer produced an empty image")
            os.replace(tmp_path, outfile)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
    except RenderError:
        raise
    except Exception as exc:  # noqa: BLE001 - name the input in every failure
        raise RenderError(infile, str(exc), cause=exc)

    return outfile


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: ``render.py <sounding_file> [output.png]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__)
        return 1
    infile = args[0]
    outfile = args[1] if len(args) > 1 else "sharpmod_sounding.png"
    try:
        out = render(infile, outfile)
    except RenderError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print("wrote", os.path.abspath(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

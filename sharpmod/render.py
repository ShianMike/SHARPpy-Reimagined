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
# and is not clipped at the widget's left edge (vendored default is 30).
SKEWT_LPAD = int(os.environ.get("SKEWT_LPAD", "35"))
# Top y (px) for the skew-T title; the vendored default is 2. A smaller/negative
# value nudges the title up, away from the space below the top border.
TITLE_TOP = int(os.environ.get("TITLE_TOP", "-4"))
TITLE_STRETCH = int(os.environ.get("TITLE_STRETCH", "80"))
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


HODO_0_500_COLOR = os.environ.get("HODO_0_500_COLOR", "#FF69B4")


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
                # pink and the remaining bands keep their configured colors
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
        valid_s = valid.strftime("%a %Y-%m-%d %Hz") if valid is not None else ""
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
                title += "  %.2f\u00b0%s %.2f\u00b0%s" % (
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
        # Condense the vendored STP graphic fonts before it is constructed.
        _install_stp_condense()
        _install_stp_label_rename()
        _install_stp_xlabel_colors()
        _install_stp_bottom_margin()
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

        # Rebrand the vendored version label (top-right "SHARPpy v..." QLabel).
        try:
            from qtpy.QtWidgets import QLabel
            for _lbl in win.findChildren(QLabel):
                if _lbl.text().startswith("SHARPpy"):
                    _lbl.setText("SHARPpy Reimagined v0.1")
        except Exception:
            pass

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

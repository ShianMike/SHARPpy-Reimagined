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

import argparse
import inspect
import logging
import os
import ssl
import sys
import tempfile
import threading
import warnings
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import urlparse

# The vendored SHARPpy index/kinematics widgets format legitimately-missing
# (masked) wind values for display by coercing them to float, e.g.
# ``np.float64(self.srw_ebw[0])``. On recent NumPy that coercion emits a
# spurious "converting a masked element to nan" UserWarning for every missing
# value drawn -- purely cosmetic console noise from unreachable third-party
# code. Silence just that one message (our own code path is fixed at the
# source, see ``sharptab.constants.is_missing``).
warnings.filterwarnings(
    "ignore",
    message="Warning: converting a masked element to nan",
    category=UserWarning,
)
# The vendored SARS database loader (``sharppy/databases/sars.py``) reads its
# supercell/hail text files with ``np.loadtxt``. Those files carry a blank
# second line, which NumPy >=1.23 flags with a one-time "Input line N contained
# no data" UserWarning per ``loadtxt`` call. The blank line is intentional and
# harmless, so silence just that message from the unreachable third-party read.
warnings.filterwarnings(
    "ignore",
    message=r"Input line \d+ contained no data",
    category=UserWarning,
)

# --- Qt platform / binding setup (must precede the first Qt import) --------
# Render without a physical display. ``setdefault`` lets a caller override
# (e.g. to "xcb"/"windows" for an interactive debug run).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Pin the qtpy binding to PySide6 (Qt6); no PySide2/Qt5 fallback.
os.environ.setdefault("QT_API", "pyside6")

import certifi  # noqa: E402
from urllib.error import URLError  # noqa: E402
from urllib.request import urlopen  # noqa: E402

from qtpy import QtCore, QtGui, QtWidgets  # noqa: E402
from qtpy.QtWidgets import QApplication  # noqa: E402

# Restore Qt5-style unscoped enum access for the vendored ``sharppy.viz`` stack
# before importing any vendored widget, so they paint under Qt6/PySide6.
from sharpmod.viz import _qt6_compat  # noqa: E402

_qt6_compat.apply()

from sharppy.viz.preferences import PrefDialog  # noqa: E402
from sutils.config import Config  # noqa: E402


def _safe_config_to_file(self):
    """Replace sutils' leaking one-line config writer with a closed handle."""
    with open(self._file_name, "w") as stream:  # noqa: PTH123
        self._cfg.write(stream)


# sutils 0.3.0 calls ``open`` inside ``RawConfigParser.write`` without ever
# closing the returned stream. Patch that dependency boundary once so GUI,
# headless rendering, and direct Config users all receive deterministic writes.
if not getattr(Config, "_sharpmod_safe_writer", False):
    Config.toFile = _safe_config_to_file
    Config._sharpmod_safe_writer = True

from sharpmod import colors  # noqa: E402
from sharpmod.io import decoder as decoder_mod  # noqa: E402
from sharpmod.resources import font_resolver  # noqa: E402
# Window composition (real minimal controller; no fake parent window). Importing
# it also performs the Qt ``offscreen``/PySide6 + bundled-font environment
# setup before the first Qt widget import.
from sharpmod.viz import unit_text  # noqa: E402
from sharpmod.viz.SPCWindow import RenderController, compose_window  # noqa: E402
from sharpmod.viz.unit_text import apply_render_font_quality  # noqa: E402

PNG_IMAGE_HD = "hd"
PNG_IMAGE_UHD = "uhd"
PNG_IMAGE_LOSSLESS = "lossless"
PNG_IMAGE_MODES = (PNG_IMAGE_HD, PNG_IMAGE_UHD, PNG_IMAGE_LOSSLESS)
PARCEL_TYPES = ("SFC", "ML", "FCST", "MU", "EFF", "USER")
PARCEL_ATTRIBUTES = {
    "SFC": "sfcpcl",
    "ML": "mlpcl",
    "FCST": "fcstpcl",
    "MU": "mupcl",
    "EFF": "effpcl",
    "USER": "usrpcl",
}
DEFAULT_RENDER_PARCEL = "MU"
_LOGGER = logging.getLogger(__name__)
_NATIVE_QPIXMAP = QtGui.QPixmap
_PIXMAP_DENSITY_LOCK = threading.RLock()

__all__ = [
    "PNG_IMAGE_HD", "PNG_IMAGE_UHD", "PNG_IMAGE_LOSSLESS", "RenderError",
    "PARCEL_TYPES", "DEFAULT_RENDER_PARCEL",
    "RenderController", "fetch_url", "build_config", "decode", "render",
    "main", "install_font", "grab_widget_pixmap", "save_widget_png",
]


def _bounded_float_env(name: str, default: float, lower: float,
                       upper: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(lower, min(upper, value))


def _bounded_int_env(name: str, default: int, lower: int,
                     upper: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(lower, min(upper, value))


def _normalise_png_image_mode(mode: str | None) -> str:
    """Return a canonical PNG export mode name."""
    value = (mode or PNG_IMAGE_HD).strip().lower().replace("_", "-")
    aliases = {
        "hires": PNG_IMAGE_HD,
        "hi-res": PNG_IMAGE_HD,
        "high-resolution": PNG_IMAGE_HD,
        "high-definition": PNG_IMAGE_HD,
        "ultra": PNG_IMAGE_UHD,
        "ultrahd": PNG_IMAGE_UHD,
        "ultra-hd": PNG_IMAGE_UHD,
        "ultra-high-definition": PNG_IMAGE_UHD,
        "compact": PNG_IMAGE_LOSSLESS,
        "original": PNG_IMAGE_LOSSLESS,
        "original-size": PNG_IMAGE_LOSSLESS,
    }
    value = aliases.get(value, value)
    if value not in PNG_IMAGE_MODES:
        valid = ", ".join(PNG_IMAGE_MODES)
        raise ValueError(f"unknown PNG image mode {mode!r}; expected {valid}")
    return value


def _normalise_parcel_type(parcel: str | None) -> str:
    """Return a canonical parcel key for GUI-equivalent CLI rendering."""
    try:
        value = str(parcel or DEFAULT_RENDER_PARCEL).strip().upper()
    except Exception as exc:
        raise ValueError(f"invalid parcel value {parcel!r}") from exc
    if value not in PARCEL_TYPES:
        valid = ", ".join(PARCEL_TYPES)
        raise ValueError(f"unknown parcel {parcel!r}; expected {valid}")
    return value


def _apply_render_parcel(win, parcel: str | None) -> str:
    """Select the requested parcel through SHARPpy's normal update path."""
    parcel_type = _normalise_parcel_type(parcel)
    spc_widget = getattr(win, "spc_widget", None)
    if spc_widget is None or not hasattr(spc_widget, "updateParcel"):
        raise ValueError("render window does not expose parcel selection")

    prof = getattr(spc_widget, "default_prof", None)
    if prof is None:
        raise ValueError("rendered sounding has no active profile")

    selected = None
    get_parcel = getattr(spc_widget, "getParcelObj", None)
    if callable(get_parcel):
        selected = get_parcel(prof, parcel_type)
    if selected is None:
        selected = getattr(prof, PARCEL_ATTRIBUTES[parcel_type], None)
    if selected is None:
        raise ValueError(f"{parcel_type} parcel is unavailable for this sounding")

    spc_widget.updateParcel(selected)
    return parcel_type


def _png_lossless_compression_quality() -> int:
    """Return Qt's lossless PNG compression setting for exported images.

    For PNG, Qt's ``quality`` argument controls compression effort, not pixel
    dimensions or visual fidelity. ``0`` gives the smallest lossless PNG while
    preserving the same decoded pixels as the legacy ``quality=100`` save.
    """
    return _bounded_int_env("SHARPMOD_PNG_QUALITY", 0, 0, 100)


def _png_hd_image_scale() -> float:
    """Return the HD export pixel scale.

    A 2x render roughly moves the current 370-400 KB chart exports into the
    requested 1-2 MB range while improving text/line density. Clamp the env
    override so accidental values do not create enormous offscreen pixmaps.
    """
    return _bounded_float_env("SHARPMOD_HD_SCALE", 2.0, 1.0, 4.0)


def _png_hd_compression_quality() -> int:
    """Return Qt's PNG compression setting for HD exports."""
    return _bounded_int_env("SHARPMOD_HD_PNG_QUALITY", 0, 0, 100)


def _png_uhd_image_scale() -> float:
    """Return the UHD export pixel scale."""
    return _bounded_float_env("SHARPMOD_UHD_SCALE", 2.8, 1.0, 4.0)


def _png_uhd_compression_quality() -> int:
    """Return Qt's PNG compression setting for UHD exports."""
    return _bounded_int_env("SHARPMOD_UHD_PNG_QUALITY", 0, 0, 100)


def _png_image_scale(image_mode: str | None) -> float:
    """Return the target pixel-density multiplier for one export mode."""
    mode = _normalise_png_image_mode(image_mode)
    if mode == PNG_IMAGE_UHD:
        return _png_uhd_image_scale()
    if mode == PNG_IMAGE_HD:
        return _png_hd_image_scale()
    return 1.0


def _make_density_pixmap_type(scale: float):
    """Build a QPixmap type with high-density storage and logical geometry.

    SHARPpy's scientific widgets paint into persistent ``plotBitMap`` caches.
    Those caches are created with logical widget dimensions and then blitted by
    each widget's paint event.  During HD/UHD capture, merely scaling the final
    painter enlarges those already-rasterized caches, including their text.

    This type keeps the geometry contract expected by the vendored widgets
    while allocating ``scale`` times as many physical pixels.  Qt's device
    pixel ratio then maps their unchanged logical paint coordinates onto the
    denser storage.  Copy rectangles need the same logical-to-physical mapping
    because the skew-T and hodograph keep secondary bitmap copies.
    """
    density = max(1.0, float(scale))
    native_pixmap = _NATIVE_QPIXMAP

    class DensityPixmap(native_pixmap):
        _sharpmod_density_scale = density

        def __init__(self, *args, **kwargs):
            scaled = False
            if not kwargs and len(args) == 2 and all(
                    isinstance(value, int) for value in args):
                super().__init__(
                    max(0, int(round(args[0] * density))),
                    max(0, int(round(args[1] * density))),
                )
                scaled = True
            elif not kwargs and len(args) == 1 \
                    and isinstance(args[0], QtCore.QSize):
                size = args[0]
                super().__init__(
                    max(0, int(round(size.width() * density))),
                    max(0, int(round(size.height() * density))),
                )
                scaled = True
            else:
                # Empty, filename, and QPixmap-copy constructors retain their
                # normal Qt behavior.  The copy case also preserves its DPR.
                super().__init__(*args, **kwargs)
            if scaled:
                self.setDevicePixelRatio(density)

        def width(self):
            raw = super().width()
            dpr = self.devicePixelRatioF()
            return int(round(raw / dpr)) if dpr > 1.0 else raw

        def height(self):
            raw = super().height()
            dpr = self.devicePixelRatioF()
            return int(round(raw / dpr)) if dpr > 1.0 else raw

        def size(self):
            return QtCore.QSize(self.width(), self.height())

        def rect(self):
            return QtCore.QRect(0, 0, self.width(), self.height())

        @staticmethod
        def _map_copy_axis(start, length, dpr, physical_extent):
            physical_start = int(round(start * dpr))
            if length < 0:
                # Resolve Qt's -1 "through the edge" sentinel explicitly.
                # Passing -1 with physical x/y through PySide can be treated
                # as a null QRect and copy the entire pixmap instead.
                return physical_start, max(
                    0, int(physical_extent) - physical_start)
            physical_end = int(round((start + length) * dpr))
            return physical_start, max(0, physical_end - physical_start)

        def copy(self, *args):
            dpr = self.devicePixelRatioF()
            mapped = args
            if dpr > 1.0 and args:
                if len(args) == 1 and isinstance(args[0], QtCore.QRect):
                    rect = args[0]
                    x, width = self._map_copy_axis(
                        rect.x(), rect.width(), dpr, super().width())
                    y, height = self._map_copy_axis(
                        rect.y(), rect.height(), dpr, super().height())
                    mapped = (QtCore.QRect(x, y, width, height),)
                elif len(args) == 4:
                    x, width = self._map_copy_axis(
                        args[0], args[2], dpr, super().width())
                    y, height = self._map_copy_axis(
                        args[1], args[3], dpr, super().height())
                    mapped = (x, y, width, height)

            # The binding returns the native base type.  Re-wrap it so copied
            # caches keep logical geometry and scaled-copy behavior.
            return DensityPixmap(super().copy(*mapped))

    DensityPixmap.__name__ = (
        f"_DensityPixmap_{str(density).replace('.', '_')}")
    return DensityPixmap


@contextmanager
def _target_density_pixmaps(scale: float):
    """Create new render-window bitmap caches at ``scale`` pixel density.

    ``QtGui.QPixmap`` is process-global, so the temporary substitution is
    serialized, restricted to Qt's GUI thread, and restored even if window
    composition or export fails.  The context must begin before composing the
    offscreen window; changing an already-built tree leaves stale 1x caches.
    """
    density = max(1.0, float(scale))
    if density <= 1.0:
        # Original-size export: leave font hinting at Qt's default, which
        # measured crisper than vertical-only hinting at 1x.
        yield _NATIVE_QPIXMAP
        return

    app = QApplication.instance()
    if app is None:
        raise RuntimeError(
            "target-density pixmaps require an active QApplication")
    if QtCore.QThread.currentThread() is not app.thread():
        raise RuntimeError(
            "target-density pixmaps must be created on the Qt GUI thread")

    with _PIXMAP_DENSITY_LOCK:
        if QtGui.QPixmap is not _NATIVE_QPIXMAP:
            raise RuntimeError(
                "QtGui.QPixmap is already temporarily overridden")
        QtGui.QPixmap = _make_density_pixmap_type(density)
        # Glyphs painted into these density caches are rasterised through a
        # scaled transform, where vertical-only hinting measured crisper.  Every
        # font is created after this point, so the flag is set before the tree.
        previous_scaled = unit_text.set_scaled_export(True)
        try:
            yield _NATIVE_QPIXMAP
        finally:
            unit_text.set_scaled_export(previous_scaled)
            QtGui.QPixmap = _NATIVE_QPIXMAP


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
# Maximum point size for the conditional tornado / VROT probability insets.
# Their vendored draw methods size text from widget height and place labels in
# very narrow rects, so the custom font can clip the title/legend/x labels.
COND_PROB_LABEL_MAX_PT = int(os.environ.get("COND_PROB_LABEL_MAX_PT", "10"))
# Maximum point size for the winter/DGZ panel. The vendored widget scales text
# by panel height and writes long strings into tiny rects with TextDontClip.
WINTER_LABEL_MAX_PT = int(os.environ.get("WINTER_LABEL_MAX_PT", "11"))

# Floor for a winter row, so a very short panel shrinks its text rather than
# collapsing rows onto one another.
WINTER_MIN_ROW_PX = 8

# Rows in the growth-zone block: the vendored depth, mean RH, mean PW, mean
# mixing ratio and mean omega, plus the zone's pressure bounds and the
# snow-to-liquid ratio.
WINTER_DGZ_ROWS = 4

# Rows the warm/cold-layer block reserves.
WINTER_ENERGY_ROWS = 4
# Maximum point size for the fire-weather panel, which has the same fault as the
# winter one: the font is scaled from panel height and the moisture/wind rows are
# written into two-fifths-width rects with TextDontClip, so a row like
# "0-1 km mean = 169/22" is drawn well outside its own column.
FIRE_LABEL_MAX_PT = int(os.environ.get("FIRE_LABEL_MAX_PT", "11"))
# Extra vertical space (px) that preserves the established scientific-panel
# proportions after the combined IndexBoard and Streamwiseness chart are
# mounted.
CHART_HEIGHT_GROW = int(os.environ.get("CHART_HEIGHT_GROW", "120"))
# Extra horizontal space (px) added to the window/canvas so the widened bottom
# index board has room for the storm-motion vectors AND the 1 km / 6 km AGL
# wind barbs beside them without clipping either.
CHART_WIDTH_GROW = int(os.environ.get("CHART_WIDTH_GROW", "170"))

# Guards so per-process monkeypatches are installed at most once even across
# repeated ``render()`` calls (the test suite renders several inputs in-process).
_font_installed = False
_table_spacing_patched = False


def _semantic_qcolor(widget, role, *, override=None):
    """Resolve one draw-time semantic color from a widget's live palette."""
    bg_color = QtGui.QColor(
        getattr(widget, "bg_color", colors.BG_COLOR)).name()
    fg_color = QtGui.QColor(
        getattr(widget, "fg_color", colors.FG_COLOR)).name()
    palette = colors.semantic_palette(bg_color, fg_color)
    resolved = palette[role]

    # Preserve the documented HODO_0_500_COLOR environment override. The
    # default still flows through its named semantic role; a custom value is
    # contrast-adjusted only on a light canvas, like every other role.
    if override is not None:
        dark_default = colors.semantic_palette(
            colors.BG_COLOR, colors.FG_COLOR)[role]
        if QtGui.QColor(override).name() != QtGui.QColor(dark_default).name():
            resolved = colors.resolve_theme_color(
                override, bg_color, fg_color)
    return QtGui.QColor(resolved)


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

    _OrigQFont = QtGui.QFont

    if not loaded:
        # No bundled fonts could be registered, so the family stays on the
        # system font.  Text quality must not depend on that: apply the
        # rasterisation settings anyway, since a substituted face is exactly the
        # case that rendered pixelated.
        class _QualityFont(_OrigQFont):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                apply_render_font_quality(self)

        QtGui.QFont = _QualityFont
        app.setFont(apply_render_font_quality(_QualityFont(app.font())))
        _font_installed = True
        return

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
            # Crisp glyph rasterisation, measured; see the constants above.
            apply_render_font_quality(self)

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
    grow_h = CHART_HEIGHT_GROW
    grow_w = CHART_WIDTH_GROW
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
                _LOGGER.exception("skewt.surface_labels.draw_failed")
            finally:
                if painter.isActive():
                    painter.end()
            return result

        _cls.drawTrace = drawTrace
        _cls.plotData = plotData
        _cls._sharpmod_sfc_mask = True
    except Exception:  # pragma: no cover - vendored module always present
        pass


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

    The span is deliberately wider than the meter's own -49 to -41 bounds. Its
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
    """Return the leftmost x a left-hand annotation may start at.

    Clear of the height-marker column, which is always drawn, and of the omega
    meter when there is one. The meter is the wider obstacle on a forecast
    sounding and so usually decides this, but an observed sounding has no meter
    at all -- and it was that case which put the maximum lapse rate hard against
    the left border and straight through the height labels.
    """
    left = float(getattr(widget, "lpad", 0)) + SKEWT_ANNOTATION_CLEARANCE
    for band in (_skewt_height_label_band(widget, qtgui),
                 _skewt_omega_band(widget, qtgui)):
        if band is not None:
            left = max(left, band[1] + SKEWT_ANNOTATION_CLEARANCE)
    return left


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
    if prof.pres[prof.sfc] == pbot:
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
        # Never start left of the gutter: on a narrow plot the fixed -33 C
        # column can fall inside the omega meter or the height markers.
        left = max(float(left), gutter)
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

    # The layer's top label and its helicity keep their places; the bottom one
    # yields. It is the label with somewhere to go -- it already moves above its
    # own line for a surface-based layer -- and that flip is what creates the
    # collision: a layer that is both surface-based and shallow puts the flipped
    # bottom label on the same line as the top one, in the same column, so a
    # degenerate layer drew "SFC" straight through "0m".
    rect_top = _rect(x2, y2 - rect_h, text_top)
    rect_esrh = _rect(x1 - 15.0, y2 - rect_h, text_esrh)
    rect_bot = _skewt_clear_of(
        _rect(x2, bottom_top, text_bot), (rect_top, rect_esrh), qtcore,
        right_limit=float(getattr(widget, "brx", 0)))

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
                layout = _skewt_effective_layer_labels(
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
    except Exception:  # pragma: no cover - vendored module always present
        pass


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
    """Move the max-lapse-rate annotation into the skew-T's left gutter.

    Upstream anchors it five degrees warm of the temperature trace, which is the
    busiest part of the diagram: the parcel-level labels, the freezing level and
    wet-bulb zero, the significant-level ticks and the wind barbs all sit on that
    side. Measured on a real sounding the value lands at x 595-651 while
    ``draw_temp_levels`` occupies 662-746, so it is crowded by construction
    rather than by accident.

    The cold side is nearly empty, and the effective-inflow layer already
    demonstrates the pattern -- a bracket in a fixed column with its value
    beside it. The whole annotation moves there. The bracket keeps the two
    heights it marks, which is the part that carries meaning; only the column
    changes.

    Placement is measured against its neighbours rather than assumed, because
    the gutter is narrow: between the omega meter's right edge and the
    effective-inflow column there is roughly 56 px on an 814 px wide plot, and
    ``10.5 C/km`` alone needs 63 px. The value therefore has to be able to step
    aside, and the bracket stays put while it does.
    """
    try:
        import sharppy.viz.skew as _skew

        _cls = _skew.plotSkewT
        if getattr(_cls, "_sharpmod_lapse_rate_label_placed", False):
            return
        _tab = _skew.tab
        _QtGui = _skew.QtGui
        _QtCore = _skew.QtCore
        _orig = _cls.draw_max_lapse_rate_layer

        #: Half-length of the tick marking each end of the layer, as upstream.
        _tick = 10.0

        def draw_max_lapse_rate_layer(self, qp, bound=4.5):
            try:
                layer = self.prof.max_lapse_rate_2_6
                rate, pbot, ptop = layer[0], layer[1], layer[2]
                if not (_tab.utils.QC(ptop) and _tab.utils.QC(pbot)):
                    return
                if not rate >= bound:
                    return

                scale = float(getattr(self, "scale", 1.0)) or 1.0
                originy = float(getattr(self, "originy", 0.0))
                y1 = originy + self.pres_to_pix(pbot) / scale
                y2 = originy + self.pres_to_pix(ptop) / scale

                text = _tab.utils.FLOAT2STR(rate, 1) + " C/km"
                metrics = _QtGui.QFontMetrics(self.esrh_font)
                rect_h = max(float(getattr(self, "esrh_height", 0)),
                             float(metrics.height()) + 2.0)
                width = float(metrics.horizontalAdvance(text)) + 4.0

                layout = _skewt_effective_layer_labels(
                    self, _QtCore, _QtGui, _tab)
                blockers = () if layout is None else (
                    layout["rect_bot"], layout["rect_top"],
                    layout["rect_esrh"])

                # Preferred: a column of its own, right of the whole
                # effective-inflow annotation. The gutter has to share a column
                # with the inflow height labels, and those sit at whatever
                # height that layer happens to be, so they can come arbitrarily
                # close to this one -- near enough to read as one smear without
                # ever strictly overlapping. Right of them there is real room:
                # 104 to 310 px across panel sizes against the 67 to 85 px this
                # label needs.
                column = _skewt_left_gutter(self, _QtGui)
                if blockers:
                    candidate = max(
                        float(blocker.right()) for blocker in blockers
                    ) + SKEWT_ANNOTATION_CLEARANCE
                    # That room ends at whichever trace comes first, and a dry
                    # profile puts its dewpoint much further left than a
                    # saturated one, so the limit is measured per sounding.
                    limit = _skewt_trace_left_edge(self, _tab, pbot, ptop)
                    if limit is None:
                        limit = float(getattr(self, "brx", 0))
                    limit -= SKEWT_ANNOTATION_CLEARANCE
                    if candidate + width + SKEWT_LAPSE_COLUMN_SLACK <= limit:
                        column = candidate

                bracket_x = column + _tick
                rect = _QtCore.QRectF(column, y2 - rect_h, width, rect_h)
                # Kept as a net: when the right-hand column is refused for want
                # of room, the label is back in the gutter beside the inflow
                # labels and still has to clear them.
                rect = _skewt_clear_of(
                    rect, blockers, _QtCore,
                    right_limit=float(getattr(self, "brx", 0)))
                rect = _fit_rect_to_skewt_plot(self, _QtCore, rect)

                if rate >= 8:
                    color = self.alert_colors[5]
                elif rate >= 7:
                    color = self.alert_colors[4]
                elif rate >= 6:
                    color = self.alert_colors[1]
                else:
                    color = self.alert_colors[0]

                qp.setClipping(True)
                qp.setPen(_QtGui.QPen(color, 1.5, _QtCore.Qt.SolidLine))
                qp.setFont(self.esrh_font)
                qp.drawLine(bracket_x - _tick, y1, bracket_x + _tick, y1)
                qp.drawLine(bracket_x - _tick, y2, bracket_x + _tick, y2)
                qp.drawLine(bracket_x, y1, bracket_x, y2)
                # No background plate, for the same reason the effective-inflow
                # labels have none: it is filled with the colour already behind
                # it, so it only cuts a hole in the isopleths.
                qp.setClipping(False)
                qp.drawText(
                    rect,
                    _QtCore.Qt.TextDontClip | _QtCore.Qt.AlignLeft
                    | _QtCore.Qt.AlignVCenter,
                    text)
                qp.setClipping(True)
            except Exception:  # pragma: no cover - fall back to upstream
                _orig(self, qp, bound)

        _cls.draw_max_lapse_rate_layer = draw_max_lapse_rate_layer
        _cls._sharpmod_lapse_rate_label_placed = True
    except Exception:  # pragma: no cover - vendored module always present
        pass


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
    except Exception:  # pragma: no cover - vendored module always present
        pass


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
    except Exception:  # pragma: no cover - vendored module always present
        pass


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
    from sharpmod.box_mean import box_mean_badge_lines

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
    budget = plot_w * BOX_MEAN_BADGE_MAX_WIDTH_FRAC
    # Scaled off the plot so the chip keeps its weight on a large export and
    # does not swamp a small GUI pane.
    start_pt = max(BOX_MEAN_BADGE_MIN_PT, min(13, int(round(plot_h * 0.026))))

    def measure(head_pt, texts):
        head = qtgui.QFont(base) if base is not None else qtgui.QFont()
        head.setPointSize(head_pt)
        head.setBold(True)
        sub = qtgui.QFont(head)
        sub.setPointSize(max(BOX_MEAN_BADGE_MIN_PT, head_pt - 2))
        sub.setBold(False)
        fonts = [head] + [sub] * (len(texts) - 1)
        metrics = [qtgui.QFontMetricsF(font) for font in fonts]
        width = max(m.horizontalAdvance(t) for m, t in zip(metrics, texts))
        height = sum(m.height() for m in metrics)
        return fonts, metrics, width, height

    texts = list(lines)
    chosen = None
    while texts:
        for point in range(start_pt, BOX_MEAN_BADGE_MIN_PT - 1, -1):
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

    fill = qtgui.QColor(BOX_MEAN_BADGE_FILL)
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
    :func:`sharpmod.box_mean.mean_model_label` set out to make visible.

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


def _fit_rect_to_skewt_plot(widget, qtcore, rect, pad=2):
    """Return ``rect`` shifted/shrunk so it stays inside the skew-T plot box."""
    left_limit = float(getattr(widget, "lpad", 0)) + pad
    right_limit = float(getattr(
        widget, "brx", getattr(widget, "wid", left_limit))) - pad
    top_limit = float(getattr(widget, "tpad", 0)) + pad
    bottom_limit = float(getattr(
        widget, "bry", getattr(widget, "hgt", top_limit))) - pad

    if right_limit <= left_limit or bottom_limit <= top_limit:
        return rect

    width = min(float(rect.width()), max(1.0, right_limit - left_limit))
    height = min(float(rect.height()), max(1.0, bottom_limit - top_limit))
    max_left = right_limit - width
    max_top = bottom_limit - height
    left = min(max(float(rect.x()), left_limit), max_left)
    top = min(max(float(rect.y()), top_limit), max_top)
    return qtcore.QRectF(left, top, width, height)


def _skewt_surface_label_rect(widget, qtcore, center_x, line_y, width, height,
                              below_offset=4, pad=2):
    """Place a near-surface label below its line, or above it if needed."""
    bottom_limit = float(getattr(
        widget, "bry", getattr(widget, "hgt", line_y))) - pad
    left = float(center_x) - float(width) / 2.0
    top = float(line_y) + below_offset
    if top + float(height) > bottom_limit:
        top = float(line_y) - below_offset - float(height)
    rect = qtcore.QRectF(left, top, float(width), float(height))
    return _fit_rect_to_skewt_plot(widget, qtcore, rect, pad=pad)


def _layout_skewt_surface_labels(widget, qtcore, labels, *, gap=4, pad=2):
    """Pack surface-value labels into bounded, non-overlapping rows.

    ``labels`` contains dictionaries with ``center_x``, ``line_y``, ``width``
    and ``height``.  The returned rectangles follow the input order.  Anchors
    are sorted only for layout, preserving their meteorological left-to-right
    order while a forward/backward pass opens at least ``gap`` logical pixels
    between adjacent masks.  A narrow plot automatically creates extra rows.
    """
    if not labels:
        return []

    gap = max(0.0, float(gap))
    pad = max(0.0, float(pad))
    left_limit = float(getattr(widget, "lpad", 0)) + pad
    right_limit = float(getattr(
        widget, "brx", getattr(widget, "wid", left_limit))) - pad
    top_limit = float(getattr(widget, "tpad", 0)) + pad
    bottom_limit = float(getattr(
        widget, "bry", getattr(widget, "hgt", top_limit))) - pad
    available = max(1.0, right_limit - left_limit)

    prepared = []
    for index, label in enumerate(labels):
        width = min(max(1.0, float(label["width"])), available)
        height = min(
            max(1.0, float(label["height"])),
            max(1.0, bottom_limit - top_limit),
        )
        preferred = _skewt_surface_label_rect(
            widget,
            qtcore,
            float(label["center_x"]),
            float(label["line_y"]),
            width,
            height,
            pad=pad,
        )
        prepared.append({
            "index": index,
            "center_x": float(label["center_x"]),
            "line_y": float(label["line_y"]),
            "width": width,
            "height": height,
            "preferred": preferred,
        })
    prepared.sort(key=lambda item: (item["center_x"], item["index"]))

    # Split only when the complete label cluster cannot fit in one row.
    rows = []
    row = []
    used = 0.0
    for item in prepared:
        extra = item["width"] + (gap if row else 0.0)
        if row and used + extra > available:
            rows.append(row)
            row = []
            used = 0.0
            extra = item["width"]
        row.append(item)
        used += extra
    if row:
        rows.append(row)

    result = [None] * len(labels)
    primary_above = sum(
        item["preferred"].center().y() < item["line_y"]
        for item in prepared
    ) >= (len(prepared) / 2.0)
    direction = -1.0 if primary_above else 1.0
    base_top = min(
        float(item["preferred"].top()) for item in prepared)
    placed_rows = []

    def _row_fits(top, height):
        if top < top_limit or top + height > bottom_limit:
            return False
        return all(
            top + height + gap <= other_top
            or top >= other_top + other_height + gap
            for other_top, other_height in placed_rows
        )

    for row_index, current in enumerate(rows):
        row_height = max(item["height"] for item in current)
        if row_index == 0:
            row_top = min(
                max(base_top, top_limit),
                max(top_limit, bottom_limit - row_height),
            )
        else:
            highest_top = min(top for top, _height in placed_rows)
            lowest_bottom = max(
                top + height for top, height in placed_rows)
            above = highest_top - gap - row_height
            below = lowest_bottom + gap
            candidates = (
                (above, below) if direction < 0 else (below, above)
            )
            row_top = next((
                candidate for candidate in candidates
                if _row_fits(candidate, row_height)
            ), None)

            if row_top is None:
                # Search every remaining bounded vertical slot, nearest the
                # preferred surface-label row first. This handles a first row
                # that fits below its anchor but leaves room only above.
                slot_candidates = [top_limit]
                for other_top, other_height in placed_rows:
                    slot_candidates.extend((
                        other_top - gap - row_height,
                        other_top + other_height + gap,
                    ))
                slot_candidates.sort(
                    key=lambda candidate: abs(candidate - base_top))
                row_top = next((
                    candidate for candidate in slot_candidates
                    if _row_fits(candidate, row_height)
                ), None)

            if row_top is None:
                # The available vertical area is mathematically too small for
                # another row. Keep the rectangle bounded and minimize overlap
                # instead of collapsing every remaining row onto one edge.
                bounded = [
                    min(
                        max(candidate, top_limit),
                        max(top_limit, bottom_limit - row_height),
                    )
                    for candidate in (above, below, base_top)
                ]

                def overlap_cost(candidate):
                    total = 0.0
                    for other_top, other_height in placed_rows:
                        overlap = min(
                            candidate + row_height,
                            other_top + other_height,
                        ) - max(candidate, other_top)
                        total += max(0.0, overlap + gap)
                    return total

                row_top = min(
                    bounded,
                    key=lambda candidate: (
                        overlap_cost(candidate),
                        abs(candidate - base_top),
                    ),
                )
        placed_rows.append((row_top, row_height))

        desired = [
            min(
                max(float(item["preferred"].left()), left_limit),
                right_limit - item["width"],
            )
            for item in current
        ]
        lefts = []
        cursor = left_limit
        for item, wanted in zip(current, desired):
            left = max(wanted, cursor)
            lefts.append(left)
            cursor = left + item["width"] + gap

        # Project back from the right edge, then forward once more. Since rows
        # were split by total width, these two passes always have a feasible
        # non-overlapping solution.
        cursor = right_limit
        for index in range(len(current) - 1, -1, -1):
            item = current[index]
            lefts[index] = min(lefts[index], cursor - item["width"])
            cursor = lefts[index] - gap
        cursor = left_limit
        for index, item in enumerate(current):
            lefts[index] = max(lefts[index], cursor)
            cursor = lefts[index] + item["width"] + gap

        for item, left in zip(current, lefts):
            top = row_top
            if direction < 0:
                # Bottom-align mixed font heights within an upward row.
                top += row_height - item["height"]
            result[item["index"]] = qtcore.QRectF(
                left, top, item["width"], item["height"])

    return result


def _upsert_skewt_surface_label(queue, dedupe_key, entry):
    """Keep the final draw pass for each displayed surface trace."""
    entry["dedupe_key"] = dedupe_key
    for index, queued in enumerate(queue):
        if queued.get("dedupe_key") == dedupe_key:
            queue[index] = entry
            return
    queue.append(entry)


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


def _font_metrics_advance(metrics, text):
    advance = getattr(metrics, "horizontalAdvance", None)
    if advance is None:
        advance = metrics.width
    return advance(str(text))


def _fit_font_to_rect(qtgui, base_font, text, max_width, max_height,
                      *, max_pt=COND_PROB_LABEL_MAX_PT, min_pt=6):
    """Return a copy of ``base_font`` small enough for ``text`` to fit."""
    font = qtgui.QFont(base_font)
    pt = font.pointSizeF()
    if pt <= 0:
        px = font.pixelSize()
        pt = float(px) if px > 0 else float(max_pt)
    pt = min(float(pt), float(max_pt))

    while pt > float(min_pt):
        font.setPointSizeF(pt)
        metrics = qtgui.QFontMetrics(font)
        if (metrics.height() <= max_height and
                _font_metrics_advance(metrics, text) <= max_width):
            return font
        pt -= 0.5

    font.setPointSizeF(max(float(min_pt), 1.0))
    return font


def _centered_rect_in_bounds(qtcore, center_x, top, width, height,
                             left_limit, right_limit):
    width = min(float(width), max(1.0, float(right_limit) - float(left_limit)))
    left = float(center_x) - width / 2.0
    max_left = float(right_limit) - width
    left = min(max(left, float(left_limit)), max_left)
    return qtcore.QRectF(left, float(top), width, float(height))


def _draw_fitted_text(qp, qtcore, qtgui, rect, text, font, color,
                      flags=None, *, max_pt=COND_PROB_LABEL_MAX_PT,
                      min_pt=6, elide=False):
    """Draw text inside rect after fitting the font and optionally eliding."""
    if flags is None:
        flags = qtcore.Qt.AlignCenter
    fitted = _fit_font_to_rect(
        qtgui, font, text, max(1.0, rect.width() - 2),
        max(1.0, rect.height() - 1), max_pt=max_pt, min_pt=min_pt)
    qp.setFont(fitted)
    qp.setPen(qtgui.QPen(color, 1, qtcore.Qt.SolidLine))
    draw_text = str(text)
    if elide:
        metrics = qtgui.QFontMetrics(fitted)
        draw_text = metrics.elidedText(
            draw_text, qtcore.Qt.ElideRight, max(1, int(rect.width()) - 2))
    qp.drawText(rect, flags, draw_text)


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
    except Exception:  # pragma: no cover - vendored module always present
        pass


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
    except Exception:  # pragma: no cover - vendored module always present
        pass


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
    except Exception:  # pragma: no cover - vendored modules always present
        pass


def _install_winter_text_fit():
    """Keep winter/DGZ panel text inside its columns.

    The vendored winter panel uses a height-scaled font and then draws long
    strings into one-tenth-width rectangles with ``TextDontClip``. The result is
    column bleed and right-edge clipping. This caps the panel font and redraws
    dynamic rows into actual full-width/column-width rects with elision only as
    a last resort.
    """
    try:
        import platform as _platform
        import sharppy.sharptab as _tab
        import sharppy.viz.winter as _winter_mod
        _QtGui = _winter_mod.QtGui
        _QtCore = _winter_mod.QtCore
        _bg = _winter_mod.backgroundWinter
        _plot = _winter_mod.plotWinter
        if getattr(_plot, "_sharpmod_text_fit", False):
            return

        _orig_init = _bg.initUI

        def initUI(self):
            _orig_init(self)
            try:
                f = _QtGui.QFont(self.label_font)
                ps = f.pointSizeF()
                if ps <= 0:
                    ps = float(f.pixelSize() if f.pixelSize() > 0
                               else WINTER_LABEL_MAX_PT)
                if ps > WINTER_LABEL_MAX_PT:
                    f.setPointSizeF(float(WINTER_LABEL_MAX_PT))
                    self.label_font = f
                    self.label_metrics = _QtGui.QFontMetrics(f)
                    self.os_mod = (self.label_metrics.descent()
                                   if _platform.system() == "Windows" else 0)
                    self.label_height = self.label_metrics.xHeight() + self.tpad
                    self.ylast = self.label_height
                    self.plotBitMap.fill(self.bg_color)
                    self.plotBackground()
            except Exception:
                pass

        def _columns(self):
            split = float(self.brx) * 0.48
            left_x = int(self.lpad)
            left_w = max(1, int(split - left_x - 6))
            right_x = int(split + 8)
            right_w = max(1, int(float(self.brx) - right_x - self.rpad - 4))
            return left_x, left_w, right_x, right_w

        def _natural_row_height(self):
            metrics = _QtGui.QFontMetrics(self.label_font)
            return max(int(metrics.height()), int(self.label_height) + 4)

        def _frame_bottom(self, row_h):
            """Return where the frame's last row ends for a given row height.

            Mirrors the walk in :func:`draw_frame` and the two row loops, so the
            fit below is measured against the real layout instead of an
            estimate. An earlier estimate that left out the per-row ``os_mod``
            and the taller bold precipitation rows still overran by 13 px.
            """
            gap = _row_gap(self)
            section = _section_gap(self)
            step = row_h + gap
            os_mod = int(getattr(self, "os_mod", 0) or 0)
            precip_h = _precip_row_height_for(self, row_h)
            block = row_h + gap + os_mod

            y = float(self.tpad) + step                     # header -> OPRH
            y += step + section                             # OPRH -> growth zone
            y += WINTER_DGZ_ROWS * block                    # growth-zone rows
            y += _dgz_divider_gap(self) - gap                # divider
            y += section                                    # -> initial phase
            y += step + section - gap                       # phase -> divider
            y += section                                    # -> warm/cold block
            y += WINTER_ENERGY_ROWS * block                 # warm/cold rows
            y += section - gap
            y += section                                    # -> precip header
            y += step + section                             # header -> type
            y += precip_h + section                         # type -> sfc temp
            return y + precip_h                             # bottom of last row

        def _row_height(self):
            """Row height that always fits the panel it is drawn in.

            The vendored panel took this from font metrics alone. Because the
            font is itself scaled from panel height, a taller panel grew its
            rows faster than it gained room and a short one never shrank them at
            all -- measured at 200x260 the last three rows, the precipitation
            type among them, were drawn below the frame.
            """
            natural = _natural_row_height(self)
            limit = float(getattr(self, "bry", 0)) - 2.0
            if limit <= 0:
                return natural
            candidate = natural
            while (candidate > WINTER_MIN_ROW_PX
                    and _frame_bottom(self, candidate) > limit):
                candidate -= 1
            return candidate

        def _row_gap(self):
            return 2

        def _section_gap(self):
            return 4

        def _dgz_divider_gap(self):
            return 8

        def _row_step(self):
            return _row_height(self) + _row_gap(self)

        def _precip_font(self):
            big = _QtGui.QFont(self.label_font)
            big.setBold(True)
            big.setPointSizeF(min(WINTER_LABEL_MAX_PT + 3,
                                  max(big.pointSizeF(), WINTER_LABEL_MAX_PT)))
            return big

        def _precip_row_height_for(self, row_h):
            metrics = _QtGui.QFontMetrics(_precip_font(self))
            return max(int(row_h), int(metrics.height()) + 2)

        def _precip_row_height(self):
            return _precip_row_height_for(self, _row_height(self))

        def _draw_text(self, qp, rect, text, color=None, align=None,
                       base_font=None, max_pt=None, min_pt=4):
            if align is None:
                align = _QtCore.Qt.AlignLeft | _QtCore.Qt.AlignVCenter
            if color is None:
                color = self.fg_color
            if max_pt is None:
                max_pt = WINTER_LABEL_MAX_PT
            fit_height = max(float(rect.height()), float(_row_height(self)))
            font = _fit_font_to_rect(
                _QtGui, base_font or self.label_font, str(text),
                max(1, int(rect.width()) - 2),
                max(1, int(fit_height)),
                max_pt=max_pt, min_pt=min_pt)
            qp.setFont(font)
            qp.setPen(_QtGui.QPen(color, 1, _QtCore.Qt.SolidLine))
            qp.drawText(rect, align | _QtCore.Qt.TextDontClip, str(text))

        def draw_frame(self, qp):
            qp.setPen(_QtGui.QPen(self.dgz_color, 1, _QtCore.Qt.SolidLine))
            qp.setFont(self.label_font)

            row_h = _row_height(self)
            step = _row_step(self)
            section_gap = _section_gap(self)
            # The two row loops advance by ``row_h + gap + os_mod`` while the
            # frame reserved only ``row_h + gap``. Three rows absorbed the
            # difference; a fourth did not, and the growth zone's last row was
            # drawn on top of the initial-phase line below it.
            block = step + int(getattr(self, "os_mod", 0) or 0)

            header_rect = _QtCore.QRectF(0, self.tpad, self.wid, row_h)
            _draw_text(
                self, qp, header_rect,
                '*** DENDRITIC GROWTH ZONE (-12 TO -17 C) ***',
                color=self.dgz_color,
                align=_QtCore.Qt.AlignCenter,
                min_pt=4)

            self.oprh_y1 = self.tpad + step
            self.layers_y1 = self.oprh_y1 + step + section_gap
            begin = self.layers_y1 + step
            # Four rows: the vendored three plus the growth zone's pressure
            # bounds and the snow-to-liquid ratio.
            y1 = (self.layers_y1 + WINTER_DGZ_ROWS * block +
                  _dgz_divider_gap(self) - _row_gap(self))

            qp.setPen(_QtGui.QPen(self.fg_color, 1, _QtCore.Qt.SolidLine))
            qp.drawLine(0, y1, self.brx, y1)
            qp.drawLine(self.brx * .48, y1, self.brx * .48, begin)

            self.init_phase_y1 = y1 + section_gap
            y1 = self.init_phase_y1 + step + section_gap - _row_gap(self)
            qp.drawLine(0, y1, self.brx, y1)

            backup = y1 + section_gap
            y1 = (backup + WINTER_ENERGY_ROWS * block
                  + section_gap - _row_gap(self))

            self.energy_y1 = backup
            qp.drawLine(0, y1, self.brx, y1)
            qp.drawLine(self.brx * .48, y1, self.brx * .48, backup)
            y1 += section_gap

            best_guess_rect = _QtCore.QRectF(0, y1, self.wid, row_h)
            _draw_text(
                self, qp, best_guess_rect,
                '*** BEST GUESS PRECIP TYPE ***',
                align=_QtCore.Qt.AlignCenter,
                min_pt=4)
            self.precip_type_y1 = y1 + step + section_gap
            self.ptype_tmpf_y1 = (
                self.precip_type_y1 + _precip_row_height(self) + section_gap)

        def drawDGZLayer(self, qp):
            pen = _QtGui.QPen(self.fg_color, 1, _QtCore.Qt.SolidLine)
            qp.setPen(pen)
            qp.setFont(self.label_font)
            y1 = self.layers_y1
            sep = _row_gap(self)
            lh = _row_height(self)
            left_x, left_w, right_x, right_w = _columns(self)

            depth = ('Layer Depth: ' + _tab.utils.INT2STR(self.dgz_depth) +
                     " ft (" + _tab.utils.INT2STR(self.dgz_zbot) + '-' +
                     _tab.utils.INT2STR(self.dgz_ztop) + ' ft msl)')
            _draw_text(self, qp, _QtCore.QRectF(
                left_x, y1, self.brx - left_x - self.rpad - 4, lh), depth)
            y1 += lh + sep + self.os_mod

            if self.dgz_meanomeg == 10 * self.prof.missing:
                omeg = 'N/A'
            else:
                omeg = _tab.utils.FLOAT2STR(self.dgz_meanomeg, 1) + ' ub/s'

            # The growth zone is only ever reported in feet MSL, but the
            # Skew-T's own axis is pressure and the band is drawn against it, so
            # the bounds are given in both.
            # ``QC`` is truthy for ``None``, so the presence of the attribute has
            # to be tested separately before either value is converted.
            pbot = getattr(self, "dgz_pbot", None)
            ptop = getattr(self, "dgz_ptop", None)
            bounds = 'DGZ: none'
            if pbot is not None and ptop is not None:
                try:
                    if (_tab.utils.QC(pbot) and _tab.utils.QC(ptop)
                            and float(pbot) != float(ptop)):
                        bounds = ('DGZ: ' + _tab.utils.INT2STR(pbot) + '-' +
                                  _tab.utils.INT2STR(ptop) + ' hPa')
                except (TypeError, ValueError):
                    bounds = 'DGZ: none'

            # Neither upstream nor this fork computed a snow ratio anywhere, so
            # the panel could describe the growth zone in five ways and still
            # not say how much snow an inch of liquid would make.
            try:
                from sharpmod.sharptab import winter as _winter_calc

                ratio = _winter_calc.format_snow_liquid_ratio(
                    _winter_calc.profile_snow_liquid_ratio(self.prof))
            except Exception:  # noqa: BLE001 - a panel row is not worth a crash
                ratio = 'M'

            rows = [
                ('Mean Layer RH: ' +
                 _tab.utils.FLOAT2STR(self.dgz_meanrh, 0) + ' %',
                 'Mean Layer MixRat: ' +
                 _tab.utils.FLOAT2STR(self.dgz_meanq, 1) + ' g/kg'),
                ('Mean Layer PW: ' +
                 _tab.utils.FLOAT2STR(self.dgz_pw, 1) + ' in',
                 'Mean Layer Omega: ' + omeg),
                (bounds, 'Kuchera SLR: ' + ratio),
            ]
            for left, right in rows:
                _draw_text(self, qp, _QtCore.QRectF(left_x, y1, left_w, lh), left)
                _draw_text(self, qp, _QtCore.QRectF(right_x, y1, right_w, lh),
                           right)
                y1 += lh + sep + self.os_mod

        def drawInitial(self, qp):
            qp.setPen(_QtGui.QPen(self.fg_color, 1, _QtCore.Qt.SolidLine))
            qp.setFont(self.label_font)
            rect = _QtCore.QRectF(
                self.lpad, self.init_phase_y1,
                self.brx - self.lpad - self.rpad - 4, _row_height(self))
            if self.plevel > 100:
                hght = _tab.utils.M2FT(_tab.interp.hght(self.prof, self.plevel))
                text = ("Inital Phase: " + self.init_st + ' from: ' +
                        _tab.utils.INT2STR(self.plevel) + ' mb (' +
                        _tab.utils.INT2STR(hght) + ' ft msl; ' +
                        _tab.utils.FLOAT2STR(self.init_tmp, 1) + ' C)')
            else:
                text = "Initial Phase:  No Precipitation layers found."
            _draw_text(self, qp, rect, text)

        def drawWCLayer(self, qp):
            sep = _row_gap(self)
            lh = _row_height(self)
            left_x, left_w, right_x, right_w = _columns(self)

            if self.tpos > 0 and self.tneg < 0:
                string = ('P/N: ' + str(round(self.tpos, 0)) + ' / ' +
                          str(round(self.tneg, 0)) + ' J/kg')
                left_labels = [
                    'TEMPERATURE PROFILE',
                    string,
                    'Melt Lyr: ' + str(int(self.ttop)) + '-' +
                    str(int(self.tbot)) + ' mb',
                    'Frz Lyr: ' + str(int(self.tbot)) + '-' +
                    str(int(self.prof.pres[self.prof.sfc])) + ' mb',
                ]
            else:
                left_labels = [
                    'TEMPERATURE PROFILE', '',
                    'Warm/Cold layers not found.', ''
                ]

            if self.wpos > 0 and self.wneg < 0:
                string = ('P/N: ' + str(round(self.wpos, 0)) + ' / ' +
                          str(round(self.wneg, 0)) + ' J/kg')
                right_labels = [
                    'WETBULB PROFILE',
                    string,
                    'Melt Lyr: ' + str(int(self.wtop)) + '-' +
                    str(int(self.wbot)) + ' mb',
                    'Frz Lyr: ' + str(int(self.wbot)) + '-' +
                    str(int(self.prof.pres[self.prof.sfc])) + ' mb',
                ]
            else:
                right_labels = [
                    'WETBULB PROFILE', '',
                    'Warm/Cold layers not found.', ''
                ]

            for x, width, labels in ((left_x, left_w, left_labels),
                                     (right_x, right_w, right_labels)):
                y1 = self.energy_y1
                for text in labels:
                    _draw_text(self, qp, _QtCore.QRectF(x, y1, width, lh), text)
                    y1 += lh + sep + self.os_mod

        def drawOPRH(self, qp):
            if (self.oprh < -.1 and _tab.utils.QC(self.oprh) and
                    self.dgz_meanomeg != -99990.0):
                color = _QtCore.Qt.red
            else:
                color = self.fg_color

            if self.dgz_meanomeg == -99990.0:
                text = 'OPRH (Omega*PW*RH): N/A'
            else:
                text = ('OPRH (Omega*PW*RH): ' +
                        _tab.utils.FLOAT2STR(self.oprh, 2))
            rect = _QtCore.QRectF(0, self.oprh_y1, self.wid,
                                  _row_height(self))
            _draw_text(self, qp, rect, text, color=color,
                       align=_QtCore.Qt.AlignCenter)

        def drawPrecipType(self, qp):
            big = _precip_font(self)
            metrics = _QtGui.QFontMetrics(big)
            height = max(_precip_row_height(self), metrics.height() + 2)
            rect = _QtCore.QRectF(0, self.precip_type_y1, self.wid, height)
            _draw_text(self, qp, rect, self.precip_type,
                       align=_QtCore.Qt.AlignCenter, base_font=big,
                       max_pt=WINTER_LABEL_MAX_PT + 3, min_pt=5)

        def drawPrecipTypeTemp(self, qp):
            small = _QtGui.QFont(self.label_font)
            small.setPointSizeF(max(6.0, min(
                WINTER_LABEL_MAX_PT, small.pointSizeF())))
            metrics = _QtGui.QFontMetrics(small)
            height = max(_row_height(self), metrics.height() + 2)
            rect = _QtCore.QRectF(0, self.ptype_tmpf_y1, self.wid, height)
            _draw_text(self, qp, rect, self.ptype_tmpf_string,
                       align=_QtCore.Qt.AlignCenter, base_font=small,
                       min_pt=5)

        _bg.initUI = initUI
        _bg.draw_frame = draw_frame
        _plot.drawDGZLayer = drawDGZLayer
        _plot.drawInitial = drawInitial
        _plot.drawWCLayer = drawWCLayer
        _plot.drawOPRH = drawOPRH
        _plot.drawPrecipType = drawPrecipType
        _plot.drawPrecipTypeTemp = drawPrecipTypeTemp
        _plot._sharpmod_text_fit = True
    except Exception:  # pragma: no cover - vendored module always present
        pass


def _install_fire_text_fit():
    """Keep fire-weather panel text inside its columns.

    The same fault the winter panel had, and it went unnoticed for longer because
    this panel is only reachable by right-clicking one specific box. The vendored
    widget scales its font from the panel's height, then writes the moisture and
    low-level-wind rows into rects two fifths of the width with ``TextDontClip``.
    Measured on a real profile at 320x340, six of twenty-one rows were drawn
    wider than the box holding them -- "0-1 km mean = 169/22" wanted 400 px of a
    256 px column -- so the right-aligned wind column ran back across the
    left-aligned moisture column and the two interleaved.

    Two corrections, matching :func:`_install_winter_text_fit`: the label font is
    capped, and every row is drawn into a real column rect with the font fitted
    to it. The columns are also widened to the panel's actual padding; the
    vendored geometry left a tenth of the width empty on each side while
    overflowing the columns between them, which is the worst of both.
    """
    try:
        import platform as _platform

        import sharppy.sharptab as _tab
        import sharppy.viz.fire as _fire_mod
        _QtGui = _fire_mod.QtGui
        _QtCore = _fire_mod.QtCore
        _bg = _fire_mod.backgroundFire
        _plot = _fire_mod.plotFire
        if getattr(_plot, "_sharpmod_text_fit", False):
            return

        _orig_init = _bg.initUI

        def initUI(self):
            _orig_init(self)
            try:
                capped = False
                for name in ("label_font", "fosberg_font"):
                    font = _QtGui.QFont(getattr(self, name))
                    size = font.pointSizeF()
                    if size <= 0:
                        size = float(font.pixelSize() if font.pixelSize() > 0
                                     else FIRE_LABEL_MAX_PT)
                    # The Fosberg/Haines rows are the panel's headline numbers
                    # and are drawn full width, so they keep the two points of
                    # extra size the vendored widget gives them.
                    ceiling = (FIRE_LABEL_MAX_PT if name == "label_font"
                               else FIRE_LABEL_MAX_PT + 2)
                    if size > ceiling:
                        font.setPointSizeF(float(ceiling))
                        setattr(self, name, font)
                        capped = True
                if not capped:
                    return
                self.label_metrics = _QtGui.QFontMetrics(self.label_font)
                self.fosberg_metrics = _QtGui.QFontMetrics(self.fosberg_font)
                self.os_mod = (self.label_metrics.descent()
                               if _platform.system() == "Windows" else 0)
                self.label_height = self.label_metrics.xHeight() + self.tpad
                self.ylast = self.label_height
                self.plotBitMap.fill(self.bg_color)
                self.plotBackground()
            except Exception:
                pass

        def _columns(self):
            """Left and right column rectangles, using the real padding.

            Split at the midpoint with a gutter, so the left column is
            left-aligned moisture and the right column is right-aligned wind and
            the two cannot meet.
            """
            split = float(self.brx) * 0.5
            left_x = float(self.lpad)
            left_w = max(1.0, split - left_x - 4.0)
            right_x = split + 4.0
            right_w = max(1.0, float(self.brx) - self.rpad - right_x)
            return left_x, left_w, right_x, right_w

        #: Rows the panel stacks vertically: the title, the two column captions,
        #: four paired moisture/wind rows, the mixing-height line, the derived
        #: heading, and the three derived indices.
        _FIRE_ROWS = 11
        _FIRE_ROW_GAP = 2.0

        def _row_height(self):
            """Row height that keeps all eleven rows inside the panel.

            The vendored widget sized rows from font metrics alone and let the
            bottom rows fall off the frame -- the Haines row is drawn below the
            panel at 420x400 before this patch, and the whole derived block goes
            under at smaller sizes. So the height available per row is the
            ceiling, and the metrics only make rows *smaller* than that.
            """
            metrics = _QtGui.QFontMetrics(self.label_font)
            wanted = max(int(metrics.height()), int(self.label_height) + 4)
            available = float(self.bry) - self.tpad - self.bpad
            budget = available / _FIRE_ROWS - _FIRE_ROW_GAP
            return max(6.0, min(float(wanted), budget))

        def _row_step(self):
            return _row_height(self) + _FIRE_ROW_GAP

        def _draw_text(self, qp, rect, text, color=None, align=None,
                       base_font=None, max_pt=None, min_pt=5):
            if align is None:
                align = _QtCore.Qt.AlignLeft | _QtCore.Qt.AlignVCenter
            if color is None:
                color = self.fg_color
            if max_pt is None:
                max_pt = FIRE_LABEL_MAX_PT
            fit_height = max(float(rect.height()), float(_row_height(self)))
            font = _fit_font_to_rect(
                _QtGui, base_font or self.label_font, str(text),
                max(1, int(rect.width()) - 2), max(1, int(fit_height)),
                max_pt=max_pt, min_pt=min_pt)
            qp.setFont(font)
            qp.setPen(_QtGui.QPen(color, 1, _QtCore.Qt.SolidLine))
            qp.drawText(rect, align | _QtCore.Qt.TextDontClip, str(text))

        def _publish_layout(self):
            """Compute and store every row position, top to bottom.

            One cursor walked down the panel, rather than the vendored mix of
            fractional offsets and running totals. Both ``draw_frame`` and
            ``drawPBLchar`` read these, so the captions and the rows beneath them
            cannot drift apart.
            """
            row_h = _row_height(self)
            step = _row_step(self)
            left_x, left_w, right_x, right_w = _columns(self)
            self.moist_x, self.moist_width = left_x, left_w
            self.llw_x, self.llw_width = right_x, right_w
            self.moswindsep = _FIRE_ROW_GAP

            y = float(self.tpad)
            self.title_y1 = y
            y += step
            self.caption_y1 = y
            y += step
            self.caption_rule_y = y - _FIRE_ROW_GAP / 2.0
            self.start_data_y1 = y
            y += 4 * step                      # four moisture/wind pairs
            self.pbl_y1 = y
            y += step
            self.derived_y1 = y
            y += step
            self.derived_rule_y = y - _FIRE_ROW_GAP / 2.0
            self.fosberg_y1 = y
            self.fosberg_x = 0
            self.fosberg_width = self.brx
            y += step
            self.haines_y1 = y
            self.haines_x = 0
            self.haines_width = self.brx
            y += step
            # Ventilation rate joins the derived indices rather than the mixed
            # layer rows above, because that is what it is: a composite of the
            # mixing height and the transport wind, both already printed.
            self.vent_y1 = y
            self.vent_width = self.brx
            return row_h

        def draw_frame(self, qp):
            """Title, the two column captions, and the two dividers.

            Restated rather than wrapped because this method is what publishes
            the geometry every row below is drawn into, and both widening the
            columns and fitting the rows to the panel height depend on owning it.
            """
            row_h = _publish_layout(self)
            self.labels = 2 * self.label_height + self.tpad + self.os_mod

            _draw_text(
                self, qp,
                _QtCore.QRectF(0, self.title_y1, self.wid, row_h),
                "Fire Weather Parameters",
                align=_QtCore.Qt.AlignCenter, base_font=self.fosberg_font,
                max_pt=FIRE_LABEL_MAX_PT + 2)

            _draw_text(
                self, qp,
                _QtCore.QRectF(self.moist_x, self.caption_y1,
                               self.moist_width, row_h),
                "Moisture", color=_QtGui.QColor("#00CC33"))
            _draw_text(
                self, qp,
                _QtCore.QRectF(self.llw_x, self.caption_y1,
                               self.llw_width, row_h),
                "Low-Level Wind", color=_QtGui.QColor("#0066CC"),
                align=_QtCore.Qt.AlignRight | _QtCore.Qt.AlignVCenter)

            qp.setPen(_QtGui.QPen(self.fg_color, 1, _QtCore.Qt.SolidLine))
            for rule in (self.caption_rule_y, self.derived_rule_y):
                qp.drawLine(0, int(rule), int(self.brx), int(rule))

            _draw_text(
                self, qp,
                _QtCore.QRectF(0, self.derived_y1, self.brx, row_h),
                "Derived Indices", color=_QtGui.QColor("#FF6633"),
                align=_QtCore.Qt.AlignCenter)

        def drawPBLchar(self, qp):  # noqa: N802 - upstream Qt API
            # ``plotData`` can reach here before ``draw_frame`` has run on a
            # freshly resized widget, so the layout is published either way.
            row_h = _publish_layout(self)
            left_x, left_w = self.moist_x, self.moist_width
            right_x, right_w = self.llw_x, self.llw_width
            step = _row_step(self)
            right_align = _QtCore.Qt.AlignRight | _QtCore.Qt.AlignVCenter

            wind_rows = (
                ("SFC = %s/%s" % (_tab.utils.INT2STR(self.sfc_wind[0]),
                                  _tab.utils.INT2STR(self.sfc_wind[1])), None),
                ("0-1 km mean = %s/%s"
                 % (_tab.utils.INT2STR(self.meanwind01km[0]),
                    _tab.utils.INT2STR(self.meanwind01km[1])), None),
                ("BL mean = %s/%s"
                 % (_tab.utils.INT2STR(self.meanwindpbl[0]),
                    _tab.utils.INT2STR(self.meanwindpbl[1])), None),
                ("BL max = %s/%s"
                 % (_tab.utils.INT2STR(self.maxwindpbl[0]),
                    _tab.utils.INT2STR(self.maxwindpbl[1])),
                 self.getMaxWindFormat()[0]),
            )
            y1 = self.start_data_y1
            for text, color in wind_rows:
                _draw_text(self, qp,
                           _QtCore.QRectF(right_x, y1, right_w, row_h),
                           text, color=color, align=right_align)
                y1 += step

            moisture_rows = (
                ("SFC RH = %s%%" % _tab.utils.INT2STR(self.sfc_rh),
                 self.getSfcRHFormat()[0]),
                ("0-1 km RH = %s%%" % _tab.utils.INT2STR(self.rh01km), None),
                ("BL mean RH = %s%%" % _tab.utils.INT2STR(self.pblrh), None),
                ("PW = %s in" % _tab.utils.FLOAT2STR(self.pwat, 2),
                 self.getPWColor()[0]),
            )
            y1 = self.start_data_y1
            for text, color in moisture_rows:
                _draw_text(self, qp,
                           _QtCore.QRectF(left_x, y1, left_w, row_h),
                           text, color=color)
                y1 += step

            _draw_text(
                self, qp, _QtCore.QRectF(0, self.pbl_y1, self.brx, row_h),
                "PBL Height = %sft / %sm"
                % (_tab.utils.FLOAT2STR(_tab.utils.M2FT(self.pbl_h), 0),
                   _tab.utils.FLOAT2STR(self.pbl_h, 0)),
                align=_QtCore.Qt.AlignCenter)

        def drawFosberg(self, qp):  # noqa: N802 - upstream Qt API
            value = ("M" if self.fosberg == self.prof.missing
                     else _tab.utils.INT2STR(self.fosberg))
            _draw_text(
                self, qp,
                _QtCore.QRectF(0, self.fosberg_y1, self.fosberg_width,
                               _row_height(self)),
                "Fosberg FWI = %s" % value,
                color=self.getFosbergFormat(),
                align=_QtCore.Qt.AlignCenter, base_font=self.fosberg_font,
                max_pt=FIRE_LABEL_MAX_PT + 2)

        def drawHainesIndex(self, qp):  # noqa: N802 - upstream Qt API
            elevation = ("L", "M", "H")[self.haines_hght]
            _draw_text(
                self, qp,
                _QtCore.QRectF(0, self.haines_y1, self.haines_width,
                               _row_height(self)),
                "Haines Index (%s) = %s"
                % (elevation,
                   _tab.utils.INT2STR(self.haines_index[self.haines_hght])),
                color=self.getHainesFormat(),
                align=_QtCore.Qt.AlignCenter, base_font=self.fosberg_font,
                max_pt=FIRE_LABEL_MAX_PT + 2)

        def drawVentilationRate(self, qp):  # noqa: N802 - upstream Qt API
            """Mixing height times transport wind, the smoke-dispersion number.

            Built from ``pbl_h`` and ``meanwindpbl`` -- the same two values this
            panel already prints as "PBL Height" and "BL mean" -- so the three
            rows cannot disagree with each other.

            Left in the foreground colour on purpose. The other rows here are
            graded, but the breakpoints between poor and good ventilation are set
            by whichever agency issues the forecast and differ between them, so
            colouring this one would assert a threshold that is not ours to set.
            """
            from sharpmod.sharptab.constants import is_missing
            from sharpmod.sharptab.fire import ventilation_rate

            # ``meanwindpbl`` was converted to (direction, speed) in setProf.
            speed = self.meanwindpbl[1] if len(self.meanwindpbl) > 1 else None
            rate = ventilation_rate(self.pbl_h, speed)
            text = ("Vent Rate = M" if is_missing(rate)
                    else "Vent Rate = %s m2/s" % _tab.utils.INT2STR(rate))
            _draw_text(
                self, qp,
                _QtCore.QRectF(0, self.vent_y1, self.vent_width,
                               _row_height(self)),
                text, align=_QtCore.Qt.AlignCenter,
                base_font=self.fosberg_font, max_pt=FIRE_LABEL_MAX_PT + 2)

        def plotData(self):  # noqa: N802 - upstream Qt API
            """Redraw the panel body. Mirrors the vendored order, plus the

            ventilation-rate row this project adds.
            """
            if self.prof is None:
                return
            qp = _QtGui.QPainter()
            qp.begin(self.plotBitMap)
            try:
                qp.setRenderHint(qp.RenderHint.Antialiasing)
                qp.setRenderHint(qp.RenderHint.TextAntialiasing)
                self.drawPBLchar(qp)
                self.drawFosberg(qp)
                self.drawHainesIndex(qp)
                self.drawVentilationRate(qp)
            finally:
                qp.end()

        _bg.initUI = initUI
        _bg.draw_frame = draw_frame
        _plot.drawPBLchar = drawPBLchar
        _plot.drawFosberg = drawFosberg
        _plot.drawHainesIndex = drawHainesIndex
        _plot.drawVentilationRate = drawVentilationRate
        _plot.plotData = plotData
        _plot._sharpmod_text_fit = True
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


def application_label() -> str:
    """Return the branded label using the package's canonical version."""
    from sharpmod._version import __version__

    return f"SHARPpy Reimagined v{__version__}"


def _custom_emphasis_font(font):
    """Return ``font`` in the configured chart face with visible emphasis."""
    emphasized = QtGui.QFont(font)
    if USE_CUSTOM_FONT:
        emphasized.setFamily(FONT_FAMILY)
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


def grab_widget_pixmap(widget, scale: float = 1.0):
    """Grab ``widget`` as a pixmap, optionally rendered at higher pixel scale.

    ``scale=1`` intentionally mirrors upstream SHARPpy's ``SPCWidget`` grab so
    compact/lossless exports keep the original widget shape and dimensions.
    Higher scales render through a scaled painter instead of resizing a final
    screenshot.  The headless :func:`render` path additionally composes its
    scientific-panel caches inside :func:`_target_density_pixmaps`, so cached
    text, barbs, and grid lines are rasterized at the final HD/UHD density.
    """
    scale = max(1.0, float(scale))
    if scale <= 1.0:
        return widget.grab()

    size = widget.size()
    width = max(1, int(round(size.width() * scale)))
    height = max(1, int(round(size.height() * scale)))
    # The destination is already specified in physical output pixels.  Bypass
    # the temporary density-aware cache constructor or it would be scaled a
    # second time.
    pixmap = _NATIVE_QPIXMAP(width, height)
    pixmap.fill(QtGui.QColor(0, 0, 0, 0))

    painter = QtGui.QPainter(pixmap)
    try:
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        painter.setRenderHint(QtGui.QPainter.SmoothPixmapTransform, True)
        painter.scale(scale, scale)
        widget.render(painter, QtCore.QPoint(0, 0))
    finally:
        painter.end()
    return pixmap


def _zero_arg_method(widget, name):
    """Return ``widget.name`` only if it is callable with no arguments.

    The vendored panels are not uniform: most paint into their cache from
    ``plotData()``, but a few take the live widget painter instead
    (``plotAdvection.plotData(qp)`` draws its data straight onto the widget and
    keeps only the background in its cache). Those must not be invoked here --
    and they do not need to be, since anything painted live is already
    rasterized at the export density.
    """
    method = getattr(widget, name, None)
    if not callable(method):
        return None
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):  # pragma: no cover - C-implemented slot
        return method
    for parameter in signature.parameters.values():
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            continue
        if parameter.default is parameter.empty:
            return None
    return method


def _panel_background_colour(widget):
    """Return the fill a scientific panel expects behind its background pass."""
    for name in ("bg_color", "bg", "backgroundColor"):
        value = getattr(widget, name, None)
        if isinstance(value, QtGui.QColor) and value.isValid():
            return value
        if isinstance(value, str) and value:
            colour = QtGui.QColor(value)
            if colour.isValid():
                return colour
    return QtGui.QColor(0, 0, 0)


def _cached_panels(root):
    """Yield every widget in ``root``'s tree that keeps a ``plotBitMap`` cache."""
    candidates = [root]
    try:
        candidates.extend(root.findChildren(QtWidgets.QWidget))
    except (AttributeError, RuntimeError):
        pass
    for widget in candidates:
        bitmap = getattr(widget, "plotBitMap", None)
        if bitmap is None:
            continue
        try:
            if bitmap.isNull():
                continue
        except (AttributeError, RuntimeError):
            continue
        yield widget


@contextmanager
def _panels_at_target_density(root, scale: float):
    """Re-rasterize a live widget tree's panel caches at ``scale`` density.

    Every scientific panel paints into a persistent ``plotBitMap`` and blits it
    from ``paintEvent``. The headless renderer gets those caches at the export
    density for free, because it composes the whole window inside
    :func:`_target_density_pixmaps`. An interactive window is composed at 1x, so
    capturing it through a scaled painter enlarges caches that were already
    rasterized -- text included -- which is what makes an on-screen export look
    softer than the command-line one at the same pixel dimensions.

    Rebuilding the caches here closes that gap for the live window. Only the
    cache is replaced: the widgets' own ``initUI`` is deliberately *not* re-run,
    because it recomputes fonts and geometry and would discard the hodograph
    centring and skew-T zoom the user has set. Each panel's existing cache
    supplies the logical size it chose for itself, and the originals are put
    back afterwards so the window on screen is left exactly as it was.
    """
    density = max(1.0, float(scale))
    if density <= 1.0:
        yield 0
        return

    saved: list[tuple] = []
    rebuilt = 0
    try:
        with _target_density_pixmaps(density):
            for panel in _cached_panels(root):
                original = panel.plotBitMap
                original_background = getattr(panel, "backgroundBitMap", None)
                try:
                    replacement = QtGui.QPixmap(original.size())
                    replacement.fill(_panel_background_colour(panel))
                    panel.plotBitMap = replacement
                    saved.append((panel, original, original_background))

                    # Order mirrors the widgets' own initialisation: the
                    # background pass paints into the fresh cache, the background
                    # snapshot is taken from it, and the data pass draws on top.
                    #
                    # ``clearData`` is deliberately not called. It is a reset for
                    # when the profile changes, not a step in the draw sequence,
                    # and most panels here have no ``backgroundBitMap`` for it to
                    # restore from -- it simply allocates a blank cache. Calling
                    # it between the two passes therefore discarded everything
                    # the background pass had just drawn, and the data pass put
                    # back only the data: axes, tick labels, titles, and legends
                    # were silently missing from HD and UHD exports.
                    plot_background = _zero_arg_method(panel, "plotBackground")
                    if plot_background is not None:
                        plot_background()
                    if original_background is not None:
                        panel.backgroundBitMap = panel.plotBitMap.copy()
                    plot_data = _zero_arg_method(panel, "plotData")
                    if plot_data is not None:
                        plot_data()
                    rebuilt += 1
                except Exception:  # noqa: BLE001 - one panel must not fail all
                    _LOGGER.exception(
                        "export.panel_density_failed panel=%s",
                        type(panel).__name__)
            yield rebuilt
    finally:
        # Restore outside the density context so the live window goes back to
        # its 1x caches even if the capture raised.
        for panel, original, original_background in saved:
            try:
                panel.plotBitMap = original
                if original_background is not None:
                    panel.backgroundBitMap = original_background
            except (AttributeError, RuntimeError):
                continue


def save_widget_png(widget, outfile: str,
                    image_mode: str = PNG_IMAGE_HD) -> bool:
    """Save ``widget`` as HD, UHD, or original-size lossless PNG.

    When the caller has not already composed the tree at the export density --
    which is the interactive viewer's case -- the panel caches are re-rasterized
    at that density first, so an on-screen export is as crisp as the equivalent
    ``sharpmod-render`` output rather than a smooth enlargement of 1x text.
    """
    mode = _normalise_png_image_mode(image_mode)
    scale = _png_image_scale(mode)
    if mode == PNG_IMAGE_UHD:
        quality = _png_uhd_compression_quality()
    elif mode == PNG_IMAGE_HD:
        quality = _png_hd_compression_quality()
    else:
        quality = _png_lossless_compression_quality()

    already_dense = QtGui.QPixmap is not _NATIVE_QPIXMAP
    if scale > 1.0 and not already_dense:
        try:
            with _panels_at_target_density(widget, scale):
                pixmap = grab_widget_pixmap(widget, scale=scale)
        except RuntimeError:
            # No QApplication, wrong thread, or a nested override: the export is
            # still worth producing, just without the density rebuild.
            _LOGGER.debug("export.density_rebuild_unavailable", exc_info=True)
            pixmap = grab_widget_pixmap(widget, scale=scale)
    else:
        pixmap = grab_widget_pixmap(widget, scale=scale)
    return bool(pixmap.save(outfile, "PNG", quality))


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
            return decoder_mod._read_bounded(  # noqa: SLF001
                response, decoder_mod._max_remote_bytes())  # noqa: SLF001
    except (URLError, OSError, ValueError) as exc:
        raise RenderError(url, f"remote fetch failed: {exc}", cause=exc)


# ---------------------------------------------------------------------------
# Config construction
# ---------------------------------------------------------------------------


def build_config(out_dir: str) -> Config:
    """Build a render config with the complete selected color style applied.

    This mirrors GUI startup: persisted style selection wins over stale
    per-color keys, dark palettes retain the readable modern amber tiers, and
    the inverted palette receives its light-background contrast adjustments.
    """
    cfg_path = os.path.join(out_dir, "sharpmod_render.ini")
    config = Config(cfg_path)
    PrefDialog.initConfig(config)

    # Use the same complete, contrast-aware style transaction as GUI startup.
    # This replaces stale per-color keys in an existing render config while
    # preserving the selected style and the established dark-theme values.
    from sharpmod.gui_settings import _apply_selected_color_style
    _apply_selected_color_style(config)

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

# Default hodograph viewport, expressed as the knot span across the full widget
# width. A 160-kt span is 20% tighter than the previous 200-kt view, so wind
# vectors and annotations render 25% larger while retaining the same values.
# ``HODO_ZOOM_KTS`` remains an environment override for specialized displays.
HODO_ZOOM_KTS = float(os.environ.get("HODO_ZOOM_KTS", "160"))


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
                c0500 = _semantic_qcolor(
                    self, "hodo_0500", override=HODO_0_500_COLOR)
                hcolors = [c0500] + list(colors)   # 0-500 m + the 4 bands
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
    """Use a 20%-tighter default hodograph viewport.

    The vendored ``backgroundHodo`` scales uniformly from ``hodomag`` -- the
    wind magnitude (in the active units) spanning the full widget width. The
    prior 200-kt view is reduced by 20% to :data:`HODO_ZOOM_KTS` (160 kt),
    increasing visual magnification by 25%. The metric default is scaled
    proportionally and kept within the vendored ``max_zoom``. Applied by
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
        # clamped to the vendored metric max_zoom (100 m/s).
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


def _install_hodo_mean_wind_center():
    """Default the hodograph to its LCL-to-EL mean-wind center.

    Vendored SHARPpy exposes ``Mean Wind`` in the hodograph context menu but
    initializes every widget in ``Normal`` mode, centered on the zero-wind
    origin. The mean vector is unavailable until a profile collection becomes
    active, so this patch selects the mode at construction and applies it after
    profile activation. It also reapplies the selected non-normal center after
    resizes, preference changes, and wheel zooms, all of which rebuild the
    vendored background around the origin.

    A later user choice remains authoritative: selecting ``Normal`` stops the
    automatic mean-wind recentering, while ``Storm Relative`` continues to use
    the active profile's right-moving storm vector.
    """
    try:
        import sharppy.sharptab as _tab
        import sharppy.viz.hodo as _hodo_mod

        _plot = _hodo_mod.plotHodo
        if getattr(_plot, "_sharpmod_mean_wind_default", False):
            return

        def _checked_mode(self):
            modes = {
                "centered": "Normal",
                "stormrelative": "Storm Relative",
                "meanwind": "Mean Wind",
            }
            selected = modes.get(getattr(self, "center_loc", ""))
            try:
                for action in self.popupmenu.actions():
                    if action.text() in modes.values():
                        action.setChecked(action.text() == selected)
            except Exception:
                pass

        def _valid_vector(vector):
            try:
                return (
                    vector is not None
                    and len(vector) >= 2
                    and _tab.utils.QC(vector[0])
                    and _tab.utils.QC(vector[1])
                )
            except Exception:
                return False

        def _selected_vector(self):
            mode = getattr(self, "center_loc", "centered")
            if mode == "meanwind":
                return getattr(self, "mean_lcl_el", None)
            if mode == "stormrelative":
                storm_motion = getattr(self, "srwind", None)
                if storm_motion is not None and len(storm_motion) >= 2:
                    return storm_motion[:2]
            return None

        def _apply_selected_center(self):
            vector = _selected_vector(self)
            if not _valid_vector(vector):
                _checked_mode(self)
                return False

            # Rebase first so center_hodo never applies a new scale/size to an
            # offset retained from the previous profile or widget geometry.
            self.centerx = self.wid / 2.0
            self.centery = self.hgt / 2.0
            self.point = (0, 0)
            self.centered = (vector[0], vector[1])
            self.clearData()
            self.center_hodo(self.centered)
            try:
                self.updateDraggables()
            except Exception:
                pass
            self.plotData()
            self.update()
            _checked_mode(self)
            return True

        _orig_init = _plot.__init__

        def __init__(self, **kwargs):
            _orig_init(self, **kwargs)
            self.centered = (0, 0)
            self.center_loc = "meanwind"
            _checked_mode(self)

        _orig_set_active = _plot.setActiveCollection

        def setActiveCollection(self, pc_idx, **kwargs):
            result = _orig_set_active(self, pc_idx, **kwargs)
            _apply_selected_center(self)
            return result

        _orig_resize = _plot.resizeEvent

        def resizeEvent(self, event):
            result = _orig_resize(self, event)
            _apply_selected_center(self)
            return result

        _orig_prefs = _plot.setPreferences

        def setPreferences(self, update_gui=True, **kwargs):
            result = _orig_prefs(self, update_gui=update_gui, **kwargs)
            _apply_selected_center(self)
            return result

        _orig_wheel = _plot.wheelEvent

        def wheelEvent(self, event):
            result = _orig_wheel(self, event)
            _apply_selected_center(self)
            return result

        _plot.__init__ = __init__
        _plot.setActiveCollection = setActiveCollection
        _plot.resizeEvent = resizeEvent
        _plot.setPreferences = setPreferences
        _plot.wheelEvent = wheelEvent
        _plot._sharpmod_mean_wind_default = True
    except Exception:  # pragma: no cover - vendored module always present
        pass


def _install_hodo_interpolation_menu():
    """Add focused-profile interpolation controls to the hodograph menu.

    Upstream exposes ``Interpolate Focused Profile`` / ``Reset Interpolation``
    only from the main Profiles menu and the ``I`` shortcut. The hodograph
    already has a right-click menu for cursor/centering/reset controls, so add
    the same focused-profile interpolation action there and route it through
    the owning ``SPCWindow``. Calling the window-level methods keeps the main
    Profiles menu visibility in sync with the current interpolation state.
    """
    try:
        import sharppy.viz.hodo as _hodo_mod

        _plot = _hodo_mod.plotHodo
        if getattr(_plot, "_sharpmod_interp_menu", False):
            return

        _QtWidgets = _hodo_mod.QtWidgets

        def _owner(widget):
            candidates = []
            try:
                candidates.append(widget.window())
            except Exception:
                pass

            cur = widget
            for _ in range(12):
                if cur is None:
                    break
                candidates.append(cur)
                try:
                    cur = cur.parentWidget()
                except Exception:
                    break

            for candidate in candidates:
                if candidate is None:
                    continue
                if (hasattr(candidate, "interpProf")
                        and hasattr(candidate, "resetProf")
                        and hasattr(candidate, "spc_widget")):
                    return candidate
            return None

        def _is_interpolated(widget):
            win = _owner(widget)
            try:
                return bool(win.spc_widget.isInterpolated())
            except Exception:
                return False

        def _interpolate(widget):
            win = _owner(widget)
            if win is not None:
                win.interpProf()

        def _reset(widget):
            win = _owner(widget)
            if win is not None:
                win.resetProf()

        def _sync(widget):
            try:
                interp = _is_interpolated(widget)
                widget.sharpmod_hodo_interp_action.setVisible(not interp)
                widget.sharpmod_hodo_reset_interp_action.setVisible(interp)
            except Exception:
                pass

        _orig_init = _plot.__init__

        def __init__(self, **kwargs):
            _orig_init(self, **kwargs)
            if hasattr(self, "sharpmod_hodo_interp_action"):
                return

            try:
                self.popupmenu.addSeparator()

                interp_action = _QtWidgets.QAction(
                    "Interpolate Focused Profile", self)
                interp_action.triggered.connect(lambda: _interpolate(self))
                self.popupmenu.addAction(interp_action)

                reset_action = _QtWidgets.QAction("Reset Interpolation", self)
                reset_action.triggered.connect(lambda: _reset(self))
                reset_action.setVisible(False)
                self.popupmenu.addAction(reset_action)

                self.sharpmod_hodo_interp_action = interp_action
                self.sharpmod_hodo_reset_interp_action = reset_action
            except Exception:
                pass

        _orig_show = _plot.showCursorMenu

        def showCursorMenu(self, pos):
            _sync(self)
            return _orig_show(self, pos)

        _plot.__init__ = __init__
        _plot.showCursorMenu = showCursorMenu
        _plot._sharpmod_interp_menu = True
    except Exception:  # pragma: no cover - vendored module always present
        pass


# Maximum point size for the hodograph label font (RM/LM storm motion labels).
HODO_LABEL_MAX_PT = int(os.environ.get("HODO_LABEL_MAX_PT", "8"))
# Maximum point size for the hodograph readout font (cursor wind readout).
HODO_READOUT_MAX_PT = int(os.environ.get("HODO_READOUT_MAX_PT", "7"))


def _fit_rect_to_hodo(widget, qtcore, rect, pad=2):
    """Return ``rect`` shifted/shrunk so it stays inside the hodograph frame."""
    left_limit = float(getattr(widget, "tlx", 0)) + pad
    right_limit = float(getattr(
        widget, "brx", getattr(widget, "wid", left_limit))) - pad
    top_limit = float(getattr(widget, "tly", 0)) + pad
    bottom_limit = float(getattr(
        widget, "bry", getattr(widget, "hgt", top_limit))) - pad

    if right_limit <= left_limit or bottom_limit <= top_limit:
        return rect

    width = min(float(rect.width()), max(1.0, right_limit - left_limit))
    height = min(float(rect.height()), max(1.0, bottom_limit - top_limit))
    max_left = right_limit - width
    max_top = bottom_limit - height
    left = min(max(float(rect.x()), left_limit), max_left)
    top = min(max(float(rect.y()), top_limit), max_top)
    return qtcore.QRectF(left, top, width, height)


def _place_hodo_annotation_rect(widget, qtcore, marker_rect, width, height,
                                *, occupied=(), gap=5, pad=2):
    """Place a hodo label beside its marker without touching other annotations.

    Right is preferred for the familiar marker-then-value reading order.  Near
    an edge or an occupied annotation, the label automatically tries left,
    below, then above before falling back to the least-overlapping fitted
    candidate.
    """
    marker = qtcore.QRectF(marker_rect)
    width = max(1.0, float(width))
    height = max(1.0, float(height))
    gap = max(0.0, float(gap))
    pad = max(0.0, float(pad))
    center = marker.center()

    candidates = (
        qtcore.QRectF(
            marker.right() + gap, center.y() - height / 2.0,
            width, height),
        qtcore.QRectF(
            marker.left() - gap - width, center.y() - height / 2.0,
            width, height),
        qtcore.QRectF(
            center.x() - width / 2.0, marker.bottom() + gap,
            width, height),
        qtcore.QRectF(
            center.x() - width / 2.0, marker.top() - gap - height,
            width, height),
    )

    left_limit = float(getattr(widget, "tlx", 0)) + pad
    right_limit = float(getattr(
        widget, "brx", getattr(widget, "wid", left_limit))) - pad
    top_limit = float(getattr(widget, "tly", 0)) + pad
    bottom_limit = float(getattr(
        widget, "bry", getattr(widget, "hgt", top_limit))) - pad
    exclusions = [marker, *[qtcore.QRectF(rect) for rect in occupied]]

    def inside(rect):
        return (
            rect.left() >= left_limit
            and rect.right() <= right_limit
            and rect.top() >= top_limit
            and rect.bottom() <= bottom_limit
        )

    def collision_area(rect):
        area = 0.0
        for obstacle in exclusions:
            expanded = qtcore.QRectF(obstacle).adjusted(
                -gap, -gap, gap, gap)
            overlap = rect.intersected(expanded)
            if not overlap.isEmpty():
                area += float(overlap.width()) * float(overlap.height())
        return area

    for candidate in candidates:
        if inside(candidate) and collision_area(candidate) == 0:
            return candidate

    fitted = [
        _fit_rect_to_hodo(widget, qtcore, candidate, pad=pad)
        for candidate in candidates
    ]
    return min(
        fitted,
        key=lambda rect: (
            collision_area(rect),
            abs(rect.center().x() - center.x())
            + abs(rect.center().y() - center.y()),
        ),
    )


def _install_hodo_label_fit():
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
                            self.label_font.family(), label_cap)
                        self.label_font.setBold(True)
                        self.label_metrics = _QtGui.QFontMetrics(self.label_font)
                        self.label_height = (self.label_metrics.xHeight() + 5)
                if readout_cap > 0:
                    pt = self.readout_font.pointSize()
                    if pt < 0:
                        pt = self.readout_font.pixelSize()
                    if pt > readout_cap:
                        self.readout_font = _QtGui.QFont(
                            self.readout_font.family(), readout_cap)
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
                    _QtGui, self.label_font, text, avail_w, 18,
                    max_pt=label_cap, min_pt=5)
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
                    rects.append(_QtCore.QRectF(
                        self.centerx + offset, top_y, width, height))
                bot_y = self.centery + radius - offset
                if bot_y + height <= bottom_limit:  # below origin
                    rects.append(_QtCore.QRectF(
                        self.centerx + offset, bot_y, width, height))
                right_x = self.centerx + radius - offset
                if right_x + width <= right_limit:  # right of origin
                    rects.append(_QtCore.QRectF(
                        right_x, self.centery + offset, width, height))
                left_x = self.centerx - radius - offset
                if left_x >= left_limit:  # left of origin
                    rects.append(_QtCore.QRectF(
                        left_x, self.centery + offset, width, height))

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
                qp.drawLine(center_rm_b - _QPoint(2, 0),
                            center_rm_b + _QPoint(2, 0))
                qp.drawLine(center_rm_b - _QPoint(0, 2),
                            center_rm_b + _QPoint(0, 2))
                qp.drawLine(center_lm_b - _QPoint(2, 0),
                            center_lm_b + _QPoint(2, 0))
                qp.drawLine(center_lm_b - _QPoint(0, 2),
                            center_lm_b + _QPoint(0, 2))

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
                        qp.drawLine(center_lm.x(), center_lm.y(),
                                    uubot, vvbot)
                        qp.drawLine(center_lm.x(), center_lm.y(),
                                    uutop, vvtop)
                    else:
                        qp.drawLine(center_rm.x(), center_rm.y(),
                                    uubot, vvbot)
                        qp.drawLine(center_rm.x(), center_rm.y(),
                                    uutop, vvtop)

                # RM / LM text labels -- sized to font metrics
                qp.setFont(self.label_font)
                fm = _QtGui.QFontMetrics(self.label_font)

                rm_spd = self.bunkers_right_vec[1]
                lm_spd = self.bunkers_left_vec[1]
                if self.wind_units == 'm/s':
                    rm_spd = _tab.utils.KTS2MS(rm_spd)
                    lm_spd = _tab.utils.KTS2MS(lm_spd)

                rm_text = (_tab.utils.INT2STR(
                    _np.float64(self.bunkers_right_vec[0]))
                    + '/' + _tab.utils.INT2STR(rm_spd) + " RM")
                lm_text = (_tab.utils.INT2STR(
                    _np.float64(self.bunkers_left_vec[0]))
                    + '/' + _tab.utils.INT2STR(lm_spd) + " LM")

                h_offset = 2
                v_offset = 5
                pad = 2
                rm_tw = fm.horizontalAdvance(rm_text) + pad * 2
                lm_tw = fm.horizontalAdvance(lm_text) + pad * 2
                th = fm.height() + pad

                rm_rect = _QtCore.QRectF(
                    ruu + h_offset, rvv + v_offset, rm_tw, th)
                lm_rect = _QtCore.QRectF(
                    luu + h_offset, lvv + v_offset, lm_tw, th)

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
                        rect.x() - mask_pad_x, rect.y() - mask_pad_y,
                        rect.width() + mask_pad_x, rect.height() + mask_pad_y)

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
                occupied = getattr(
                    self, "_sharpmod_hodo_annotation_rects", None)
                if isinstance(occupied, list):
                    occupied.extend((
                        _QtCore.QRectF(rm_rect),
                        _QtCore.QRectF(lm_rect),
                        _QtCore.QRectF(ruu - 5, rvv - 5, 10, 10),
                        _QtCore.QRectF(luu - 5, lvv - 5, 10, 10),
                    ))
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
                    vis = getattr(self, 'readout_visible', False)
                    rh = getattr(self, 'readout_hght', -999.)
                    draw_readout = vis and rh >= 0 and rh <= 12000.
                else:
                    draw_readout = False

                if draw_readout:
                    hght_agl = _tab.interp.to_agl(self.prof, self.hght)
                    u_interp = _tab.interp.generic_interp_hght(
                        self.readout_hght, hght_agl, self.u)
                    v_interp = _tab.interp.generic_interp_hght(
                        self.readout_hght, hght_agl, self.v)
                    if _tab.utils.QC(u_interp):
                        wd_interp, ws_interp = _tab.utils.comp2vec(
                            u_interp, v_interp)
                        if self.wind_units == 'm/s':
                            ws_interp = _tab.utils.KTS2MS(ws_interp)
                            units = 'm/s'
                        else:
                            units = 'kts'
                        xx, yy = self.uv_to_pix(u_interp, v_interp)
                        readout = "%03d/%02d %s" % (
                            wd_interp, ws_interp, units)
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
                for overlay in tuple(getattr(
                        type(self), "_sharpmod_live_overlays", ())):
                    try:
                        overlay(self, qp)
                    except Exception:
                        _LOGGER.exception(
                            "hodo_live_overlay.draw_failed",
                            extra={"overlay": getattr(
                                overlay, "__name__", repr(overlay))},
                        )

                if draw_readout and _tab.utils.QC(u_interp):
                    # Use a fixed small font for the readout, bypassing
                    # self.readout_font which may be rebuilt by other code.
                    _readout_f = _QtGui.QFont('Helvetica', readout_cap)
                    _readout_f.setBold(True)
                    qp.setFont(_readout_f)
                    fm = _QtGui.QFontMetrics(_readout_f)
                    pad = 3
                    tw = fm.horizontalAdvance(readout) + pad * 2
                    th = fm.height() + pad
                    text_rect = _fit_rect_to_hodo(
                        self, _QtCore, _QtCore.QRectF(
                            xx + 2, yy + 5, tw, th))
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

                if not _np.isfinite(self.corfidi_up_u) or \
                   not _np.isfinite(self.corfidi_up_v) or \
                   not _np.isfinite(self.corfidi_dn_u) or \
                   not _np.isfinite(self.corfidi_dn_v):
                    return

                up_u, up_v = self.uv_to_pix(
                    self.corfidi_up_u, self.corfidi_up_v)
                dn_u, dn_v = self.uv_to_pix(
                    self.corfidi_dn_u, self.corfidi_dn_v)
                center_up = _QPointF(up_u, up_v)
                center_dn = _QPointF(dn_u, dn_v)
                qp.drawEllipse(center_up, 3, 3)
                qp.drawEllipse(center_dn, 3, 3)

                # Labels sized to font metrics
                qp.setFont(self.label_font)
                fm = _QtGui.QFontMetrics(self.label_font)

                up_spd = self.upshear[1]
                dn_spd = self.downshear[1]
                if self.wind_units == 'm/s':
                    up_spd = _tab.utils.KTS2MS(up_spd)
                    dn_spd = _tab.utils.KTS2MS(dn_spd)

                up_text = ("UP="
                           + _tab.utils.INT2STR(
                               _np.float64(self.upshear[0]))
                           + '/' + _tab.utils.INT2STR(up_spd))
                dn_text = ("DN="
                           + _tab.utils.INT2STR(
                               _np.float64(self.downshear[0]))
                           + '/' + _tab.utils.INT2STR(dn_spd))

                h_offset = 1
                v_offset = 3
                pad = 2
                up_tw = fm.horizontalAdvance(up_text) + pad * 2
                dn_tw = fm.horizontalAdvance(dn_text) + pad * 2
                th = fm.height() + pad

                up_rect = _QtCore.QRectF(
                    up_u + h_offset, up_v + v_offset, up_tw, th)
                dn_rect = _QtCore.QRectF(
                    dn_u + h_offset, dn_v + v_offset, dn_tw, th)

                qp.fillRect(up_rect, self.bg_color)
                qp.fillRect(dn_rect, self.bg_color)

                pen = _QtGui.QPen(corfidi_color)
                qp.setPen(pen)
                qp.drawText(up_rect, _QtCore.Qt.AlignCenter, up_text)
                qp.drawText(dn_rect, _QtCore.Qt.AlignCenter, dn_text)
                occupied = getattr(
                    self, "_sharpmod_hodo_annotation_rects", None)
                if isinstance(occupied, list):
                    occupied.extend((
                        _QtCore.QRectF(up_rect),
                        _QtCore.QRectF(dn_rect),
                        _QtCore.QRectF(up_u - 3, up_v - 3, 6, 6),
                        _QtCore.QRectF(dn_u - 3, dn_v - 3, 6, 6),
                    ))
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
                    self.mean_lcl_el[0], self.mean_lcl_el[1])
                marker_size = 8.0
                marker_rect = _QtCore.QRectF(
                    mean_u - marker_size / 2.0,
                    mean_v - marker_size / 2.0,
                    marker_size,
                    marker_size,
                )

                speed = self.mean_lcl_el_vec[1]
                if self.wind_units == 'm/s':
                    speed = _tab.utils.KTS2MS(speed)
                text = (
                    _tab.utils.INT2STR(
                        _np.float64(self.mean_lcl_el_vec[0]))
                    + '/' + _tab.utils.INT2STR(speed)
                )

                qp.setFont(self.label_font)
                fm = _QtGui.QFontMetrics(self.label_font)
                text_rect = _place_hodo_annotation_rect(
                    self,
                    _QtCore,
                    marker_rect,
                    fm.horizontalAdvance(text) + 6,
                    fm.height() + 2,
                    occupied=getattr(
                        self, "_sharpmod_hodo_annotation_rects", ()),
                    gap=5,
                )
                mean_color = _semantic_qcolor(
                    self, "orange", override="#B8860B")

                qp.fillRect(text_rect, self.bg_color)
                qp.setBrush(_QtGui.QBrush(_QtCore.Qt.NoBrush))
                qp.setPen(_QtGui.QPen(mean_color, 2,
                                      _QtCore.Qt.SolidLine))
                qp.drawRect(marker_rect)
                qp.setPen(_QtGui.QPen(mean_color, 1,
                                      _QtCore.Qt.SolidLine))
                qp.drawText(text_rect, _QtCore.Qt.AlignCenter, text)

                occupied = getattr(
                    self, "_sharpmod_hodo_annotation_rects", None)
                if isinstance(occupied, list):
                    occupied.extend((
                        _QtCore.QRectF(marker_rect),
                        _QtCore.QRectF(text_rect),
                    ))
            except Exception:
                _orig_mean_wind(self, qp)

        _plot.drawLCLtoEL_MW = drawLCLtoEL_MW

        _plot._sharpmod_label_fit = True
    except Exception:  # pragma: no cover - vendored module always present
        pass


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
                _LOGGER.exception("hodo_locator.draw_failed")

        _plot.plotData = plotData
        _plot._sharpmod_locator = True
    except Exception:  # pragma: no cover - vendored module always present
        _LOGGER.exception("hodo_locator.install_failed")
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
                    _LOGGER.exception("hodo_height_levels.draw_failed")
                finally:
                    if painter.isActive():
                        painter.end()

            _plot.paintEvent = paintEvent
        _plot._sharpmod_height_levels = True
    except Exception:  # pragma: no cover - vendored module always present
        _LOGGER.exception("hodo_height_levels.install_failed")
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
                _box_font = _QtGui.QFont(self.box_font)
                if USE_CUSTOM_FONT:
                    _box_font.setFamily(FONT_FAMILY)
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
                _header_font = _custom_emphasis_font(_box_font)
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
    except Exception:  # pragma: no cover - vendored module always present
        pass


_title_override_installed = False


def _install_title_override():
    """Format the skew-T title with full forecast run and valid timestamps.

    Overrides the vendored ``plotSkewT.getPlotTitle`` (a read-only presentation
    change) so the heading reads, e.g.::

        HRRR 2026-07-14 10z, F000  VALID: Tue 2026-07-14 10z @41.54N 92.93W

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
        run_s = run.strftime("%Y-%m-%d %Hz,") if run is not None else ""
        valid_s = ("VALID: " + valid.strftime("%a %Y-%m-%d %Hz")
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


#: One source of truth for the reduced plot-box stroke weight.  The Skew-T and
#: every auxiliary plot use it together.
PANEL_FRAME_WIDTH = colors.PLOT_FRAME_WIDTH

#: The bottom products band is a container as well as a visible plot box.  Its
#: upstream ``QWidget`` selector also matches every child plot, which paints a
#: second one-pixel border immediately inside the container border.  Give the
#: frame a stable object name so its rule can target the container alone.
BOTTOM_BAND_OBJECT_NAME = "sharpmod_bottom_band"

#: Vendored ``(module, class)`` pairs that outline themselves in ``draw_frame``.
_FRAMED_INSETS = (
    ("advection", "backgroundAdvection"),
    ("analogues", "backgroundAnalogues"),
    ("ensemble", "backgroundENS"),
    ("fire", "backgroundFire"),
    ("generic", "backgroundGeneric"),
    ("hodo", "backgroundHodo"),
    ("kinematics", "backgroundKinematics"),
    ("ship", "backgroundSHIP"),
    ("skew", "backgroundSkewT"),
    ("slinky", "backgroundSlinky"),
    ("speed", "backgroundSpeed"),
    ("srwinds", "backgroundWinds"),
    ("stp", "backgroundSTP"),
    ("stpef", "backgroundSTPEF"),
    ("thermo", "backgroundText"),
    ("thetae", "backgroundThetae"),
    ("vrot", "backgroundVROT"),
    ("watch", "backgroundWatch"),
    ("winter", "backgroundWinter"),
)


class _MatchingFramePainter:
    """Forward painter calls while matching the first frame pen to the Skew-T.

    Wrapped around a vendored ``draw_frame`` rather than restating it, so the
    frame geometry and any title text the same method paints stay upstream and
    cannot drift from it.  Only the first wide pen is a panel-frame candidate;
    later wide pens belong to data such as STP curves and pass through exactly.

    Three corrections are limited to that frame:

    *The stroke matches the Skew-T.* The candidate pen receives the widget's
    foreground colour and the shared reduced width. Anything that is not a pen --
    ``Qt.NoPen``, a bare ``QColor`` -- passes straight through.

    *Coordinates are pulled inside the widget* when ``bounds`` is given. Every
    framed inset sets ``rpad`` to zero and so ``brx`` to its own ``width()``,
    which puts the right-hand frame line one column past the last paintable pixel
    where it is clipped away entirely -- these panels have only ever had three
    sides. Clamping is deliberately confined to ``draw_frame``: it is the one
    method whose whole job is an outline on the boundary, so pinning a line to
    the edge is what it meant in the first place.

    *A shared divider is painted once.* Grid neighbours touch with zero spacing,
    so one widget's right/bottom edge and the next widget's left/top edge occupy
    adjacent pixels. The latter owns that divider; suppressing the former keeps
    the visually reduced outline one pixel wide.
    """

    __slots__ = (
        "_frame_color", "_frame_pen_active", "_frame_pen_seen", "_max_x",
        "_max_y", "_pen_type", "_qp", "_sides", "_width",
    )

    def __init__(
        self,
        qp,
        pen_type,
        frame_color,
        width=PANEL_FRAME_WIDTH,
        bounds=None,
        sides=None,
    ):
        self._qp = qp
        self._pen_type = pen_type
        self._frame_color = frame_color
        self._width = float(width)
        self._sides = frozenset(
            sides or ("left", "top", "right", "bottom"))
        self._frame_pen_active = False
        self._frame_pen_seen = False
        if bounds is None:
            self._max_x = None
            self._max_y = None
        else:
            width, height = bounds
            self._max_x = max(0, int(width) - 1)
            self._max_y = max(0, int(height) - 1)

    def setPen(self, pen, *args, **kwargs):  # noqa: N802 - Qt API name
        try:
            width = float(pen.widthF())
        except Exception:  # noqa: BLE001 - not a pen, nothing to match
            self._frame_pen_active = False
            return self._qp.setPen(pen, *args, **kwargs)
        # All geometric vendored frames begin with an upstream 2 px pen (the
        # Skew-T first uses a zero-width background pen, which deliberately does
        # not count).
        # Matching only that first wide pen avoids recolouring/thinning curves
        # drawn later by a few overloaded ``draw_frame`` implementations.
        if not self._frame_pen_seen and width >= 2.0:
            matched = self._pen_type(pen)
            matched.setColor(self._frame_color)
            matched.setWidthF(self._width)
            pen = matched
            self._frame_pen_seen = True
            self._frame_pen_active = True
        else:
            self._frame_pen_active = False
        return self._qp.setPen(pen, *args, **kwargs)

    @staticmethod
    def _clamp(value, limit):
        """Pull ``value`` into ``0..limit``, keeping int coordinates ints."""
        if value < 0:
            result = 0
        elif value > limit:
            result = limit
        else:
            return value
        return float(result) if isinstance(value, float) else int(result)

    @staticmethod
    def _pixel_center(value):
        """Place a one-pixel antialiased stroke on a physical pixel center."""
        import math

        return math.floor(float(value)) + 0.5

    def drawLine(self, *args, **kwargs):  # noqa: N802 - Qt API name
        if (
            self._max_x is None
            or not self._frame_pen_active
            or len(args) != 4
            or kwargs
        ):
            return self._qp.drawLine(*args, **kwargs)
        try:
            x1, y1, x2, y2 = args
            if abs(float(x1) - float(x2)) < 1e-9:
                if float(x1) <= 0.0 and "left" not in self._sides:
                    return None
                if float(x1) >= self._max_x and "right" not in self._sides:
                    return None
            elif abs(float(y1) - float(y2)) < 1e-9:
                if float(y1) <= 0.0 and "top" not in self._sides:
                    return None
                if float(y1) >= self._max_y and "bottom" not in self._sides:
                    return None
            return self._qp.drawLine(QtCore.QLineF(
                self._pixel_center(self._clamp(x1, self._max_x)),
                self._pixel_center(self._clamp(y1, self._max_y)),
                self._pixel_center(self._clamp(x2, self._max_x)),
                self._pixel_center(self._clamp(y2, self._max_y)),
            ))
        except (TypeError, ValueError):
            # Points rather than four scalars; nothing to clamp.
            return self._qp.drawLine(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._qp, name)


def _frame_sides_for_widget(widget):
    """Return the frame sides this widget owns in a zero-spacing layout."""
    explicit = getattr(widget, "_sharpmod_frame_sides", None)
    if explicit:
        if isinstance(explicit, str):
            return frozenset((explicit,))
        return frozenset(explicit)

    sides = {"left", "top", "right", "bottom"}
    try:
        parent = widget.parentWidget()
        own = widget.geometry()
        siblings = parent.children() if parent is not None else ()
    except Exception:  # noqa: BLE001 - stand-ins/standalone panels own all sides
        return frozenset(sides)

    def overlaps(a1, a2, b1, b2):
        return min(a2, b2) >= max(a1, b1)

    # Prefer grid cells over pixel geometry. A resize event can fire while Qt is
    # still assigning sibling geometries, but the layout positions are already
    # final; using them prevents an early full-frame cache from surviving the
    # settled zero-spacing layout.
    try:
        layout = parent.layout()
        own_index = layout.indexOf(widget)
        if own_index >= 0 and hasattr(layout, "getItemPosition"):
            row, column, row_span, column_span = layout.getItemPosition(
                own_index)
            for index in range(layout.count()):
                if index == own_index:
                    continue
                item = layout.itemAt(index)
                sibling = item.widget()
                if sibling is None or sibling.isHidden():
                    continue
                other_row, other_col, other_rows, other_cols = \
                    layout.getItemPosition(index)
                if (
                    other_col == column + column_span
                    and min(row + row_span, other_row + other_rows)
                    > max(row, other_row)
                ):
                    sides.discard("right")
                if (
                    other_row == row + row_span
                    and min(column + column_span, other_col + other_cols)
                    > max(column, other_col)
                ):
                    sides.discard("bottom")
            return frozenset(sides)
    except Exception:
        pass

    for sibling in siblings:
        if sibling is widget:
            continue
        try:
            if not sibling.isWidgetType() or sibling.isHidden():
                continue
            other = sibling.geometry()
        except Exception:
            continue
        if (
            other.left() == own.right() + 1
            and overlaps(own.top(), own.bottom(), other.top(), other.bottom())
        ):
            sides.discard("right")
        if (
            other.top() == own.bottom() + 1
            and overlaps(own.left(), own.right(), other.left(), other.right())
        ):
            sides.discard("bottom")
    return frozenset(sides)


def _matching_panel_stylesheet(widget, sheet, frame_color=None):
    """Return ``sheet`` with its plot border matched to the Skew-T palette."""
    import re

    lowered = sheet.lower() if sheet else ""
    if "border-width" not in lowered or "border-color" not in lowered:
        return sheet

    try:
        source = (
            frame_color
            if frame_color is not None
            else getattr(widget, "fg_color", colors.FG_COLOR)
        )
        matched_color = QtGui.QColor(source)
        if not matched_color.isValid():
            matched_color = QtGui.QColor(colors.FG_COLOR)
        color_name = matched_color.name()
    except Exception:  # noqa: BLE001 - keep a valid default on odd vendored data
        color_name = colors.FG_COLOR

    width_text = f"{PANEL_FRAME_WIDTH:g}px"
    frame_sides = getattr(widget, "_sharpmod_frame_sides", None)
    if frame_sides == "left":
        # A right-hand inset inside the shared bottom frame needs only the
        # separator on its left.  Keeping its top/right/bottom QSS edges would
        # paint directly beside the parent's outer edge and look two pixels
        # thick even though both individual strokes are one pixel.
        # The normalizer is called on every resize and theme apply. Remove its
        # previous side override first so the stylesheet remains idempotent.
        sheet = re.sub(
            r"\s*border-left-width\s*:\s*[^;}]+;?",
            "",
            sheet,
            flags=re.IGNORECASE,
        )
        width_declaration = (
            f"border-width: 0px; border-left-width: {width_text}"
        )
    else:
        width_declaration = f"border-width: {width_text}"
    updated = re.sub(
        r"border-width\s*:\s*[^;}]+",
        width_declaration,
        sheet,
        flags=re.IGNORECASE,
    )
    updated = re.sub(
        r"border-color\s*:\s*[^;}]+",
        f"border-color: {color_name}",
        updated,
        flags=re.IGNORECASE,
    )
    return updated


def _match_bottom_band_stylesheet(widget, frame_color=None):
    """Scope and normalize the shared lower-band frame stylesheet.

    Upstream assigns ``QWidget { ... border ... }`` to this container.  Qt
    applies that selector to every QWidget below it, so the nominal one-pixel
    parent outline becomes two adjacent rows at the top and bottom.  Restricting
    the selector to the named QFrame leaves one true outer outline; each child
    then draws only its intentional internal separator.
    """
    import re

    if widget is None:
        return
    try:
        widget.setObjectName(BOTTOM_BAND_OBJECT_NAME)
        sheet = widget.styleSheet()
    except Exception:  # noqa: BLE001 - not the expected QFrame surface
        return
    scoped = re.sub(
        r"\bQWidget\s*\{",
        f"QFrame#{BOTTOM_BAND_OBJECT_NAME} {{",
        sheet,
        count=1,
        flags=re.IGNORECASE,
    )
    updated = _matching_panel_stylesheet(widget, scoped, frame_color)
    if updated != sheet:
        widget.setStyleSheet(updated)


def _match_panel_stylesheet(widget, frame_color=None):
    """Make an existing QSS plot border match the widget's Skew-T palette."""
    try:
        sheet = widget.styleSheet()
    except Exception:  # noqa: BLE001 - not a stylesheet-backed widget
        return
    updated = _matching_panel_stylesheet(widget, sheet, frame_color)
    if updated != sheet:
        widget.setStyleSheet(updated)


def _install_matching_panel_frames():
    """Give every auxiliary plot the Skew-T's reduced foreground outline.

    Registered last so it wraps whatever ``draw_frame`` each class ends up with,
    including the ones other patches in this registry replace outright.

    Fully guarded and idempotent per class: a module that is absent, or a class
    without ``draw_frame``, is skipped rather than aborting the rest.
    """
    import importlib

    frame_classes = []
    for module_name, class_name in _FRAMED_INSETS:
        try:
            module = importlib.import_module(f"sharppy.viz.{module_name}")
            cls = getattr(module, class_name)
            frame_classes.append(cls)

            if not cls.__dict__.get("_sharpmod_matching_frame", False):
                original = cls.draw_frame
                pen_type = module.QtGui.QPen
                color_type = module.QtGui.QColor

                def draw_frame(
                    self,
                    qp,
                    _orig=original,
                    _pen=pen_type,
                    _color=color_type,
                ):
                    try:
                        bounds = (self.width(), self.height())
                    except Exception:  # noqa: BLE001 - no clamp without bounds
                        bounds = None
                    color = _color(getattr(
                        self, "fg_color", colors.FG_COLOR))
                    try:
                        _orig(
                            self,
                            _MatchingFramePainter(
                                qp,
                                _pen,
                                color,
                                bounds=bounds,
                                sides=_frame_sides_for_widget(self),
                            ),
                        )
                    except Exception:  # noqa: BLE001 - never lose a panel
                        _orig(self, qp)

                cls.draw_frame = draw_frame
                cls._sharpmod_matching_frame = True

            # Several table-like insets use QSS rather than four painter lines.
            # Rewrite the declaration *before* Qt applies it. Applying a second
            # stylesheet after ``initUI`` triggers a resize, whose vendored
            # handler calls ``initUI`` again and alternates forever between the
            # legacy and corrected declarations.
            if (
                hasattr(cls, "setStyleSheet")
                and not cls.__dict__.get("_sharpmod_matching_frame_qss", False)
            ):
                original_set_stylesheet = cls.setStyleSheet

                def setStyleSheet(
                    self,
                    sheet,
                    _orig=original_set_stylesheet,
                ):
                    return _orig(
                        self, _matching_panel_stylesheet(self, sheet))

                cls.setStyleSheet = setStyleSheet
                cls._sharpmod_matching_frame_qss = True

            # A colour-scheme reapply can change ``fg_color`` without calling
            # ``initUI``. Wrap concrete plot subclasses so the QSS follows it.
            for candidate in tuple(vars(module).values()):
                if not isinstance(candidate, type) or not issubclass(candidate, cls):
                    continue
                original_prefs = getattr(candidate, "setPreferences", None)
                if not callable(original_prefs) or candidate.__dict__.get(
                    "_sharpmod_matching_frame_prefs", False
                ):
                    continue

                def setPreferences(
                    self, *args, _orig=original_prefs, **kwargs
                ):
                    result = _orig(self, *args, **kwargs)
                    _match_panel_stylesheet(self)
                    return result

                candidate.setPreferences = setPreferences
                candidate._sharpmod_matching_frame_prefs = True
        except Exception:  # noqa: BLE001 - one inset must not end the pass
            continue

    # The combined bottom plot band is a plain QFrame owned by SPCWidget, not a
    # vendored inset class. Its legacy cyan QSS was the most visible colour
    # mismatch in the screenshot, so keep it synchronized on every theme apply.
    try:
        import sharppy.viz.SPCWindow as _spc_window

        spc_cls = _spc_window.SPCWidget
        if not spc_cls.__dict__.get("_sharpmod_matching_frame_config", False):
            original_update = spc_cls.updateConfig

            def updateConfig(self, *args, **kwargs):
                result = original_update(self, *args, **kwargs)
                _match_bottom_band_stylesheet(
                    getattr(self, "text", None),
                    getattr(self, "fg_color", colors.FG_COLOR),
                )
                for frame_cls in tuple(frame_classes):
                    try:
                        children = self.findChildren(frame_cls)
                    except Exception:
                        continue
                    for child in children:
                        _match_panel_stylesheet(child)
                return result

            spc_cls.updateConfig = updateConfig
            spc_cls._sharpmod_matching_frame_config = True
    except Exception:  # noqa: BLE001 - vendored window always present in app
        pass


def render_patch_specs():
    """Return the sole ordered registry of SHARPpy widget monkeypatches."""
    from sharpmod.render_patch_registry import PatchSpec

    return (
        PatchSpec(
            "skewt.user-parcel-backend",
            _install_user_parcel_acceleration,
        ),
        PatchSpec("title.override", _install_title_override),
        PatchSpec("skewt.title-shrink", _install_skewt_title_shrink),
        PatchSpec("title.top", _install_title_top),
        PatchSpec("barbs.custom", _install_custom_barbs),
        PatchSpec("hodo.0500", _install_hodo_0500),
        PatchSpec("hodo.zoom", _install_hodo_zoom),
        PatchSpec("hodo.mean-wind-default", _install_hodo_mean_wind_center),
        PatchSpec("hodo.interpolation-menu", _install_hodo_interpolation_menu),
        PatchSpec("hodo.label-fit", _install_hodo_label_fit),
        PatchSpec("hodo.locator", _install_hodo_locator),
        PatchSpec("hodo.height-levels", _install_hodo_height_levels),
        PatchSpec("skewt.level-labels", _install_skewt_level_labels_fit),
        PatchSpec("stp.condense", _install_stp_condense),
        PatchSpec("stp.label-rename", _install_stp_label_rename),
        PatchSpec("stp.xlabel-colors", _install_stp_xlabel_colors),
        PatchSpec("stp.bottom-margin", _install_stp_bottom_margin),
        PatchSpec("stp.box-shrink", _install_stp_box_shrink),
        PatchSpec("stp.prob-box-spacing", _install_stp_prob_box_spacing),
        PatchSpec("conditional-prob.fit", _install_conditional_prob_panel_fit),
        PatchSpec("winter-text.fit", _install_winter_text_fit),
        PatchSpec("fire-text.fit", _install_fire_text_fit),
        PatchSpec("speed.0500", _install_speed_0500),
        PatchSpec("speed.title-cap", _install_speed_title_cap),
        PatchSpec("advection.font-cap", _install_advection_font_cap),
        PatchSpec("skewt.mixratio-mask", _install_skewt_mixratio_mask),
        PatchSpec("skewt.surface-label-mask", _install_skewt_sfc_label_mask),
        PatchSpec(
            "skewt.effective-layer-label-fit",
            _install_skewt_effective_layer_label_fit),
        # Before the transparency patch on purpose: that one captures whatever
        # ``draw_max_lapse_rate_layer`` it finds and wraps it, so the placement
        # has to be in place first for the pair to describe the same label.
        PatchSpec(
            "skewt.lapse-rate-label-placement",
            _install_skewt_lapse_rate_label_placement),
        PatchSpec(
            "skewt.lapse-rate-label-transparency",
            _install_skewt_lapse_rate_label_transparency),
        PatchSpec(
            "hodo.storm-motion-label-transparency",
            _install_hodo_storm_motion_label_transparency),
        PatchSpec("skewt.frame-on-top", _install_skewt_frame_ontop),
        # After the frame redraw on purpose: both wrap ``plotData``, and the
        # callout has to sit on top of the outline rather than under it.
        PatchSpec("skewt.box-mean-badge", _install_skewt_box_mean_badge),
        PatchSpec("skewt.isotherm-label-fit", _install_skewt_isotherm_label_fit),
        PatchSpec("slinky.title-fit", _install_slinky_title_fit),
        PatchSpec("tables.spacing", _apply_table_spacing_patch),
        # Last on purpose: it wraps whatever ``draw_frame`` each inset ends up
        # with, including the ones patched above.
        PatchSpec("panels.match-skewt-frames", _install_matching_panel_frames),
    )


def _install_user_parcel_acceleration():
    from sharpmod.sharptab.accelerated_profile import (
        install_user_parcel_acceleration,
    )

    install_user_parcel_acceleration()


def install_render_patches() -> tuple[str, ...]:
    """Validate SHARPpy then install the ordered render patch registry."""
    from sharpmod.render_patch_registry import apply_patch_registry

    return apply_patch_registry(render_patch_specs())


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


def _decode_local_input(infile: str, display_name: str):
    """Decode one already-local input without repeating remote downloads."""
    if infile.lower().endswith(".npz"):
        prof_col, station_id = decoder_mod.load_npz(infile)
        return _accelerate_decoded_collection(prof_col), station_id

    last_err: BaseException | None = None
    for _name, cls in decoder_mod.getDecoders().items():
        try:
            dec = cls(infile)
            prof_col = dec.getProfiles()
            stn_id = dec.getStnId()
            decoder_mod.attach_json_sidecar(prof_col, infile)
            return _accelerate_decoded_collection(prof_col), stn_id
        except Exception as exc:  # noqa: BLE001 - try every decoder in turn
            last_err = exc
            continue
    raise RenderError(
        display_name,
        f"no decoder could read it: {last_err}",
        cause=last_err,
    )


def _accelerate_decoded_collection(prof_collection):
    from sharpmod.sharptab.accelerated_profile import (
        accelerate_profile_collection,
    )

    return accelerate_profile_collection(prof_collection)


def _remote_temp_suffix(url: str) -> str:
    suffix = os.path.splitext(urlparse(url).path)[1].lower()
    if suffix and len(suffix) <= 12 \
            and suffix[1:].replace("-", "").isalnum():
        return suffix
    return ".txt"


def decode(infile: str):
    """Decode ``infile`` into a profile collection and its station id.

    ``.npz`` point-sounding sidecars go through
    :func:`sharpmod.io.decoder.load_npz` (which preserves the OMEGA column);
    every other input is tried against each registered decoder from
    :func:`sharpmod.io.decoder.getDecoders`. Raises :class:`RenderError` naming
    ``infile`` if no decoder can read it.
    """
    source = os.fspath(infile)
    if not decoder_mod._is_http_url(source):  # noqa: SLF001
        return _decode_local_input(
            decoder_mod._local_source_path(source),  # noqa: SLF001
            source,
        )

    # Download a remote source exactly once. The decoder registry may contain
    # several candidates and each legacy candidate otherwise fetches the same
    # URL again before deciding the format is not its own.
    payload = fetch_url(source)
    fd, local_path = tempfile.mkstemp(suffix=_remote_temp_suffix(source))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
        return _decode_local_input(local_path, source)
    finally:
        try:
            os.remove(local_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _resolve_location_title(prof_col, explicit_loc: str | None = None) -> str:
    """Replace a generic model/coordinate label with a cached town title."""

    try:
        current = prof_col.getMeta("loc")
    except Exception:
        current = ""
    current = " ".join(str(current or "").split())

    # A caller-supplied human-readable label always wins.
    if explicit_loc is not None and str(explicit_loc).strip():
        return current

    try:
        model = prof_col.getMeta("model")
    except Exception:
        model = ""

    from sharpmod.place_names import needs_town_name, reverse_town_name

    if not needs_town_name(current, model):
        return current
    try:
        lat = float(prof_col.getMeta("lat"))
        lon = float(prof_col.getMeta("lon"))
    except (KeyError, TypeError, ValueError):
        return current

    place = reverse_town_name(lat, lon)
    if place:
        prof_col.setMeta("loc", place)
        return place
    return current


def render(infile: str, outfile: str = "sharpmod_sounding.png",
           model: str | None = None, run: datetime | None = None,
           loc: str | None = None,
           image_mode: str = PNG_IMAGE_HD,
           parcel: str = DEFAULT_RENDER_PARCEL) -> str:
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
    parcel = _normalise_parcel_type(parcel)
    out_dir = os.path.dirname(os.path.abspath(outfile))
    os.makedirs(out_dir, exist_ok=True)
    density_context = None
    density_context_entered = False

    try:
        app = QApplication.instance() or QApplication(sys.argv)
        # Install the bundled fonts and force the custom family BEFORE any
        # widget is constructed (Requirement 15.2 / layout parity).
        install_font(app)

        config = build_config(out_dir)
        _apply_sars_match_color()
        install_render_patches()

        prof_col, stn_id = decode(infile)

        if model is not None:
            prof_col.setMeta("model", model)
        if run is not None:
            prof_col.setMeta("run", run)
        if loc is not None:
            prof_col.setMeta("loc", loc)
        _resolve_location_title(prof_col, explicit_loc=loc)

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

        # Construct every scientific panel's persistent bitmap cache at the
        # requested output density.  This context must begin before
        # ``compose_window`` and remain active through the final capture;
        # otherwise HD/UHD would smoothly enlarge already-rasterized 1x text.
        density_context = _target_density_pixmaps(
            _png_image_scale(image_mode))
        density_context.__enter__()
        density_context_entered = True

        # Compose SPCWindow with the real minimal controller. The controller is
        # the Qt parent SPCWindow connects its config/preferences hooks to; it
        # must outlive the window, so keep a reference for the render duration.
        # ``mount=True`` installs the combined index board, Streamwiseness,
        # and skew-T overlays. The mount is fully
        # guarded (see ``mount_products``); the outcome is recorded on
        # ``win.sharpmod_products`` for inspection.
        win, controller = compose_window(config, prof_col, mount=True)
        _apply_render_parcel(win, parcel)

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

        # Preserve the lower scientific-band proportions and make horizontal
        # room for the wind barbs.
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
            if not save_widget_png(win.spc_widget, tmp_path,
                                   image_mode=image_mode):
                raise RenderError(infile, "Qt could not save the PNG image")
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
    finally:
        if density_context_entered:
            density_context.__exit__(None, None, None)

    return outfile


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sharpmod-render",
        description="Render a sounding file to a PNG image.")
    parser.add_argument("infile", help="input sounding file")
    parser.add_argument("outfile", nargs="?", default="sharpmod_sounding.png",
                        help="output PNG path")
    parser.add_argument(
        "--image-mode", "--image", choices=PNG_IMAGE_MODES,
        default=PNG_IMAGE_HD, help="PNG image mode (default: hd)")
    parser.add_argument(
        "--hd", action="store_const", const=PNG_IMAGE_HD,
        dest="image_mode",
        help="render a 2x high-density PNG (default)")
    parser.add_argument(
        "--uhd", action="store_const", const=PNG_IMAGE_UHD,
        dest="image_mode",
        help="render a 2.8x ultra-high-density PNG")
    parser.add_argument(
        "--lossless", action="store_const", const=PNG_IMAGE_LOSSLESS,
        dest="image_mode",
        help="render the original-size compact/lossless PNG")
    parser.add_argument(
        "--parcel", type=str.upper, choices=PARCEL_TYPES,
        default=DEFAULT_RENDER_PARCEL,
        help="parcel visualized on the Skew-T (default: MU)")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: ``sharpmod-render <sounding_file> [output.png]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    parser = _build_cli_parser()
    if not args:
        parser.print_help()
        return 1
    ns = parser.parse_args(args)
    try:
        out = render(
            ns.infile,
            ns.outfile,
            image_mode=ns.image_mode,
            parcel=ns.parcel,
        )
    except RenderError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print("wrote", os.path.abspath(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

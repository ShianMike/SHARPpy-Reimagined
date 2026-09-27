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

from sharpmod.viz import colors  # noqa: E402
from sharpmod.export_paths import (  # noqa: E402
    ExportDirectoryError,
    export_file_path,
)
from sharpmod.io import decoder as decoder_mod  # noqa: E402
from sharpmod.resources import font_resolver  # noqa: E402
# Window composition (real minimal controller; no fake parent window). Importing
# it also performs the Qt ``offscreen``/PySide6 + bundled-font environment
# setup before the first Qt widget import.
from sharpmod.viz import unit_text  # noqa: E402
from sharpmod.viz.SPCWindow import RenderController, compose_window  # noqa: E402
from sharpmod.viz.unit_text import apply_render_font_quality  # noqa: E402
from sharpmod.render_patches.palette import _semantic_qcolor  # noqa: E402
from sharpmod.render_patches.geometry import (  # noqa: E402
    COND_PROB_LABEL_MAX_PT, _fit_rect_to_skewt_plot,
    _skewt_surface_label_rect, _layout_skewt_surface_labels,
    _upsert_skewt_surface_label, _font_metrics_advance,
    _fit_font_to_rect, _centered_rect_in_bounds, _draw_fitted_text,
)
from sharpmod.render_patches.frames import (  # noqa: E402
    PANEL_FRAME_WIDTH, BOTTOM_BAND_OBJECT_NAME, _FRAMED_INSETS,
    _MatchingFramePainter, _frame_sides_for_widget,
    _matching_panel_stylesheet, _match_bottom_band_stylesheet,
    _match_panel_stylesheet, _install_matching_panel_frames,
)
from sharpmod.render_patches.hodo_legacy import (  # noqa: E402
    HODO_0_500_COLOR, HODO_ZOOM_KTS, _install_hodo_0500,
    _install_hodo_zoom, _install_hodo_mean_wind_center,
    _install_hodo_interpolation_menu, _fit_rect_to_hodo,
    _place_hodo_annotation_rect,
)
from sharpmod.rendering.export import (  # noqa: E402
    grab_widget_pixmap, _zero_arg_method, _panel_background_colour,
    _cached_panels, _panels_at_target_density, _composition_colours,
    compose_widget_pixmap, save_pixmap_png_atomic, save_widget_png,
)
from sharpmod.rendering.density import (  # noqa: E402
    PNG_IMAGE_HD, PNG_IMAGE_UHD, PNG_IMAGE_LOSSLESS, PNG_IMAGE_MODES,
    _NATIVE_QPIXMAP, _PIXMAP_DENSITY_LOCK, _bounded_float_env,
    _bounded_int_env, _normalise_png_image_mode,
    _png_lossless_compression_quality, _png_hd_image_scale,
    _png_hd_compression_quality, _png_uhd_image_scale,
    _png_uhd_compression_quality, _png_image_scale,
    _make_density_pixmap_type, _target_density_pixmaps,
)

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

__all__ = [
    "PNG_IMAGE_HD", "PNG_IMAGE_UHD", "PNG_IMAGE_LOSSLESS", "RenderError",
    "PARCEL_TYPES", "DEFAULT_RENDER_PARCEL",
    "RenderController", "fetch_url", "build_config", "decode", "render",
    "main", "install_font", "grab_widget_pixmap", "compose_widget_pixmap",
    "save_pixmap_png_atomic", "save_widget_png",
]






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
    except Exception:  # pragma: no cover - registry reports install failure
        raise


# --- Five layout-compensation passes (applied after compose + updateConfig) --
# Each is a faithful port of the legacy helper and is fully guarded so a missing
# or renamed widget never crashes the render.
















# ---------------------------------------------------------------------------
# Secure remote fetch (replaces the legacy ``urlopen(cafile=...)`` wrapper)
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Config construction
# ---------------------------------------------------------------------------








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
    except Exception:  # pragma: no cover - registry reports install failure
        raise


def render_patch_specs():
    """Return the sole ordered registry of panel-owned monkeypatches."""
    from sharpmod.render_patches.render_patch_groups import build_patch_specs

    return build_patch_specs(globals())


def _install_user_parcel_acceleration():
    from sharpmod.sharptab.accelerated_profile import (
        install_user_parcel_acceleration,
    )

    install_user_parcel_acceleration()


def install_render_patches() -> tuple[str, ...]:
    """Validate SHARPpy then install the ordered render patch registry."""
    from sharpmod.render_patches.render_patch_registry import apply_patch_registry

    return apply_patch_registry(render_patch_specs())


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

    from sharpmod.maps.place_names import needs_town_name, reverse_town_name

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


def _attach_render_locator_overlay(prof_col, spec: str | None) -> tuple[str, ...]:
    """Attach the requested locator overlays to ``prof_col`` before composing.

    A bad specification is worth failing on -- the user asked for something the
    renderer cannot provide, and silently producing a bare inset would hide
    that. A provider that is merely unreachable is not: :func:`locator_overlay
    .apply` already treats one failed overlay as nothing to draw, because an
    inset is an aid to reading a sounding rather than a precondition for one.
    """
    if not spec:
        return ()
    from sharpmod.maps import locator_overlay

    selections = locator_overlay.parse(spec)
    if not selections:
        return ()
    latitude = prof_col.getMeta("lat") if "lat" in prof_col._meta else None
    longitude = prof_col.getMeta("lon") if "lon" in prof_col._meta else None
    if latitude is None or longitude is None:
        _LOGGER.info("render.locator_overlay_skipped_no_point")
        return ()
    return locator_overlay.apply(
        prof_col, selections,
        lat=float(latitude), lon=float(longitude),
        valid_time=prof_col.getCurrentDate(),
    )


def render(infile: str, outfile: str | None = None,
           model: str | None = None, run: datetime | None = None,
           loc: str | None = None,
           image_mode: str = PNG_IMAGE_HD,
           parcel: str = DEFAULT_RENDER_PARCEL,
           locator_overlay: str | None = None) -> str:
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
    if outfile is None:
        try:
            outfile = str(export_file_path("sharpmod_sounding.png"))
        except ExportDirectoryError as exc:
            raise RenderError(infile, str(exc)) from exc
    parcel = _normalise_parcel_type(parcel)
    out_dir = os.path.dirname(os.path.abspath(outfile))
    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        raise RenderError(
            infile,
            f"output directory could not be created: {out_dir} ({exc})",
            cause=exc,
        ) from exc
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

        # Fetch any requested locator overlay now, while there is still a plain
        # function call to do it in. The inset's paint path is deliberately
        # offline -- a slow map service must never stall a hodograph repaint --
        # so everything it draws has to be attached to the collection before
        # composing, never fetched from inside the paint.
        _attach_render_locator_overlay(prof_col, locator_overlay)

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
    parser.add_argument(
        "outfile",
        nargs="?",
        default=None,
        help=(
            "output PNG path (default: rendered_soundings/"
            "sharpmod_sounding.png beside the application)"
        ),
    )
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
    parser.add_argument(
        "--locator-overlay", metavar="SPEC", default=None,
        help="overlays drawn on the hodograph's locator inset, as a "
             "comma-separated list of 'risk[:hazard]', 'hrrr[:product]', "
             "'radar-site' or 'radar-mosaic'. Risk and a model field combine; "
             "radar replaces them and is only available for a sounding valid "
             "near the present. Default: none, which reaches for no network.")
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
        outfile = (
            ns.outfile
            if ns.outfile is not None
            else str(export_file_path("sharpmod_sounding.png"))
        )
        out = render(
            ns.infile,
            outfile,
            image_mode=ns.image_mode,
            parcel=ns.parcel,
            locator_overlay=ns.locator_overlay,
        )
    except (ExportDirectoryError, RenderError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print("wrote", os.path.abspath(out))
    return 0


from sharpmod.render_patches.layout import (  # noqa: E402
    tighten_pressure_labels,
    shrink_title,
    fill_table_panels,
    _STP_FONTS,
    _SARS_FONTS,
    enlarge_panel_fonts,
    _grow_for_family_panels,
    enlarge_canvas,
    application_label,
    _custom_emphasis_font,
    rebrand_version_label,
    align_top_row,
    apply_layout_compensation
)


from sharpmod.render_patches.skewt_labels import (  # noqa: E402
    _install_skewt_mixratio_mask,
    _install_skewt_sfc_label_mask,
    SKEWT_ANNOTATION_CLEARANCE,
    SKEWT_OMEGA_BOUNDS_C,
    SKEWT_OMEGA_SCALE_LABEL,
    SKEWT_HEIGHT_LABEL_OFFSET,
    SKEWT_HEIGHT_LABELS,
    SKEWT_HEIGHT_LABEL_FALLBACK_PX,
    _skewt_height_label_band,
    SKEWT_OMEGA_TOP_HPA,
    _skewt_omega_bar_extent,
    _skewt_omega_band,
    _skewt_left_gutter,
    _skewt_clear_of,
    SKEWT_LAPSE_COLUMN_SLACK,
    _skewt_trace_left_edge,
    _skewt_effective_layer_labels
)


from sharpmod.render_patches.skewt_fit import (  # noqa: E402
    _install_skewt_effective_layer_label_fit,
    _RectSuppressingPainter,
    _install_skewt_lapse_rate_label_placement,
    _install_skewt_lapse_rate_label_transparency,
    _install_hodo_storm_motion_label_transparency,
    _install_skewt_frame_ontop,
    BOX_MEAN_BADGE_FILL,
    BOX_MEAN_BADGE_MIN_PT,
    BOX_MEAN_BADGE_MAX_WIDTH_FRAC
)


from sharpmod.render_patches.badge import (  # noqa: E402
    collection_meta,
    box_mean_badge_lines_for,
    draw_box_mean_badge,
    _install_skewt_box_mean_badge,
    _install_skewt_isotherm_label_fit,
    _install_slinky_title_fit,
    SKEWT_COL_STRETCH,
    URPANEL_COL_STRETCH,
    CHART_ROW_STRETCH,
    TEXT_ROW_STRETCH,
    SPEED_STRIP_COL_STRETCH,
    ADV_STRIP_COL_STRETCH,
    HODO_COL_STRETCH,
    CANVAS_GROW_W,
    CANVAS_GROW_H
)


from sharpmod.render_patches.panel_fonts import (  # noqa: E402
    enlarge_charts,
    SPEED_TITLE_MAX_PT,
    SPEED_LABEL_MAX_PT,
    ADV_TITLE_MAX_PT,
    SFC_LABEL_MAX_PT,
    _speed_title_cap_installed,
    _speed_0500_installed,
    _adv_font_cap_installed,
    _speed_axis_label_font,
    _install_advection_font_cap,
    _install_speed_title_cap,
    _install_speed_0500,
    _install_conditional_prob_panel_fit,
    _install_winter_text_fit,
    _install_fire_text_fit
)


from sharpmod.render_patches.later_skewt import (  # noqa: E402
    _apply_sars_match_color,
    _install_skewt_title_shrink,
    HODO_LABEL_MAX_PT,
    HODO_READOUT_MAX_PT,
    _install_hodo_label_fit,
    _install_hodo_locator,
    _install_hodo_height_levels,
    _install_skewt_level_labels_fit,
    _install_custom_barbs
)


from sharpmod.render_patches.stp import (  # noqa: E402
    _stp_condense_installed,
    _install_title_top,
    _install_stp_condense,
    _install_stp_bottom_margin,
    STP_BOX_SCALE,
    _install_stp_box_shrink,
    _install_stp_label_rename,
    STP_XLABEL_COLORS,
    STP_XLABEL_ROLES,
    _install_stp_xlabel_colors,
    _install_stp_prob_box_spacing
)


from sharpmod.rendering.parcel import _normalise_parcel_type, _apply_render_parcel  # noqa: E402


from sharpmod.rendering.input import (  # noqa: E402
    RenderError, fetch_url, build_config, _decode_local_input, _accelerate_decoded_collection, _remote_temp_suffix
)


if __name__ == "__main__":
    raise SystemExit(main())

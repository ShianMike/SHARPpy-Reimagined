"""Window composition for the headless SHARPpy Reimagined renderer.

This module composes the SPC-style sounding window -- the skew-T, hodograph,
storm slinky, wind barbs, index tables, and insets -- with a *real* minimal
controller, replacing the legacy ``StubParent`` fake Picker window carried by
``render_sounding.py`` (Requirement 11.4).

The widget stack itself is the upstream :class:`sharppy.viz.SPCWindow.SPCWindow`
(the exact same rendering engine behind the Pivotal Weather plots), imported
here through :mod:`qtpy` bound to PySide6/Qt6. The only thing this module adds
is the composition glue:

* :class:`RenderController` -- a genuine, minimal controller object that owns
  the render :class:`~sutils.config.Config` and provides exactly the hooks
  ``SPCWindow`` connects to its Qt *parent*: a ``config_changed`` signal (which
  ``SPCWindow`` subscribes to for profile/config refresh) and a
  ``preferencesbox`` slot (wired to the preferences menu action). It is *never*
  shown; under the Qt ``offscreen`` platform no window is realized on screen,
  so composing ``SPCWindow`` with it instantiates **no** fake parent window.

* :func:`compose_window` -- builds the controller, constructs ``SPCWindow`` with
  it as the Qt parent, optionally loads a profile collection, and re-applies the
  configuration so every inset recomputes its palette against the current
  config. It returns *both* the window and the controller: the controller is the
  window's Qt parent and must outlive it, so the caller has to keep a reference.

Qt platform/binding environment defaults are set at import time -- **before the
first Qt import** -- so importing this module is sufficient to render headless
via the Qt ``offscreen`` platform through PySide6 (Qt6), with the bundled fonts
resolved package-relative (never an absolute development path).
"""

from __future__ import annotations

import os

# --- Qt platform / binding setup (must precede the first Qt import) --------
# Render without a physical display. ``setdefault`` lets a caller override
# (e.g. to "xcb"/"windows" for an interactive debug run).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Pin the qtpy binding to PySide6 (Qt6); no PySide2/Qt5 fallback.
os.environ.setdefault("QT_API", "pyside6")

from sharpmod.resources import font_resolver  # noqa: E402

# The Qt "offscreen" platform plugin uses the basic font database, which only
# scans ``QT_QPA_FONTDIR``. Point it at the package-bundled fonts (resolved
# package-relative via importlib.resources, never an absolute dev path --
# Requirement 15.2) so text renders instead of silently drawing blank.
try:
    os.environ.setdefault("QT_QPA_FONTDIR", str(font_resolver.fonts_dir()))
except Exception:  # pragma: no cover - fall back to platform fonts
    _winfonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    if os.path.isdir(_winfonts):
        os.environ.setdefault("QT_QPA_FONTDIR", _winfonts)
# Silence the harmless "Cannot find font directory" warning; Qt falls back to
# the configured font directory above.
os.environ.setdefault("QT_LOGGING_RULES", "qt.text.font.db.warning=false")

from dataclasses import dataclass, field  # noqa: E402
from typing import List, Optional  # noqa: E402

from qtpy import QtGui  # noqa: E402
from qtpy.QtCore import QRect, Qt, Signal  # noqa: E402
from qtpy.QtWidgets import QWidget  # noqa: E402

# Restore Qt5-style unscoped enum access (e.g. ``qp.Antialiasing``) that the
# vendored ``sharppy.viz`` widgets rely on, so they paint under Qt6/PySide6.
# Must run before the first vendored-widget import/paint (Requirement 11.3).
from sharpmod.viz import _qt6_compat  # noqa: E402

_qt6_compat.apply()

# The upstream widget stack, imported through the PySide6/Qt6 binding. Aliased
# so this module can re-export the name ``SPCWindow`` without shadowing the
# import of the class it composes.
from sharppy.viz.SPCWindow import (  # noqa: E402
    SPCWidget as _VendoredSPCWidget,
    SPCWindow as _VendoredSPCWindow,
)

# SHARPpy Reimagined's in-workspace products mounted onto the window (task 17.3). Each is
# a self-contained, headless-importable Qt6 widget/overlay implemented in this
# package (tasks 14-16, plus the hazard label added in 17.3).
from sharpmod import colors  # noqa: E402
from sharpmod.viz.custom_panel import CustomPanel, PanelItem  # noqa: E402
from sharpmod.viz.skew import draw_hgz_overlay, draw_cape_fill  # noqa: E402

__all__ = [
    "RenderController",
    "SPCWindow",
    "compose_window",
    "MountResult",
    "attach_hgz_overlay",
    "attach_cape_fill",
    "attach_family_rows",
    "mount_products",
    "reapply_color_scheme",
    "COMPOSITE_FAMILY_ROWS",
    "THERMO_FAMILY_ROWS",
    "KINEMATIC_FAMILY_ROWS",
    "COMPOSITE_PANEL_ITEMS",
    "THERMO_PANEL_ITEMS",
    "KINEMATIC_PANEL_ITEMS",
    "PANEL_MIN_HEIGHT",
]

#: Re-exported upstream window class so callers can compose or type-check
#: against ``sharpmod.viz.SPCWindow.SPCWindow`` without importing the vendored
#: package directly.
SPCWindow = _VendoredSPCWindow


def _apply_streamwiseness_column_stretches(grid3):
    """Keep the original board share while splitting the former STP column."""
    for column, stretch in enumerate((4, 4, 4, 2, 6)):
        grid3.setColumnStretch(column, stretch)


def _place_right_inset_after_streamwiseness(sw):
    """Move the current swappable right inset back to column 4."""
    grid3 = getattr(sw, "grid3", None)
    right = getattr(sw, "right_inset_ob", None)
    stream = getattr(sw, "streamwiseness", None)
    if grid3 is None or right is None or stream is None:
        return False
    try:
        grid3.removeWidget(right)
        grid3.addWidget(right, 0, 4)
        _apply_streamwiseness_column_stretches(grid3)
        right.show()
        return True
    except Exception:
        return False


def _install_streamwiseness_hooks():
    """Extend SHARPpy profile/deviant/inset swaps for the mounted chart."""
    cls = _VendoredSPCWidget
    if getattr(cls, "_sharpmod_streamwiseness_hooks", False):
        return

    original_toggle = cls.toggleVector
    original_swap = cls.swapInset
    original_update = cls.updateProfs

    def toggleVector(self, deviant):  # noqa: N802 - upstream Qt API
        result = original_toggle(self, deviant)
        chart = getattr(self, "streamwiseness", None)
        if chart is not None:
            try:
                chart.setDeviant(deviant)
            except Exception:
                pass
        return result

    def swapInset(self):  # noqa: N802 - upstream Qt API
        result = original_swap(self)
        if getattr(self, "inset_to_swap", None) == "RIGHT":
            _place_right_inset_after_streamwiseness(self)
        return result

    def updateProfs(self):  # noqa: N802 - upstream Qt API
        result = original_update(self)
        _refresh_mounted_products(self)
        return result

    cls.toggleVector = toggleVector
    cls.swapInset = swapInset
    cls.updateProfs = updateProfs
    cls._sharpmod_streamwiseness_hooks = True


class RenderController(QWidget):
    """Minimal real controller that :class:`SPCWindow` is composed with.

    ``SPCWindow`` treats its Qt parent as the application "picker"/main-window
    controller: it connects the parent's ``config_changed`` signal to its own
    profile/config refresh slots and wires the preferences menu action to the
    parent's ``preferencesbox`` slot. This class provides exactly those hooks
    and owns the render :class:`~sutils.config.Config`, so it is a genuine
    controller rather than a window stand-in.

    It is intentionally never shown. Under the Qt ``offscreen`` platform no
    window is realized on screen, so composing ``SPCWindow`` with this
    controller instantiates **no** fake parent window (Requirement 11.4).
    """

    #: Emitted when the configuration changes; ``SPCWindow`` subscribes to this
    #: to refresh its profiles and re-apply the palette.
    config_changed = Signal(object)

    def __init__(self, config):
        super().__init__()
        self.config = config

    def preferencesbox(self):
        """Preferences hook wired to the ``SPCWindow`` preferences action.

        Headless rendering never opens the interactive preferences dialog, so
        this is a no-op that keeps the signal/slot contract satisfied.
        """
        return None

    def focusPicker(self):
        """No-op focus hook; there is no interactive picker window headless."""
        return None


def compose_window(config, prof_col=None, *, check_integrity=False,
                   mount=False, custom_config=None, custom_sars_lines=None,
                   controller=None):
    """Compose an :class:`SPCWindow` with a real :class:`RenderController`.

    Parameters
    ----------
    config : sutils.config.Config
        The render configuration; owned by the controller and passed to
        ``SPCWindow`` as its ``cfg``.
    prof_col : optional
        A profile collection to load into the window. When provided it is added
        with ``check_integrity`` (default ``False`` to match the headless
        renderer, which fills in metadata itself).
    check_integrity : bool, keyword-only
        Forwarded to ``SPCWindow.addProfileCollection``.
    mount : bool, keyword-only
        When ``True``, mount the SHARPpy Reimagined products onto the composed window via
        :func:`mount_products`: the three grouped derived-parameter
        ``CustomPanel`` widgets are placed *inside* the vendored bottom table
        band (``grid3``) as a second row, each directly beneath its family
        column (thermodynamics, kinematics, and -- spanning the freed SARS
        column -- the composite indices), the vendored SARS analogues inset is
        detached to make room, and the skew-T HGZ overlay is attached. No strip
        is added. The resulting :class:`MountResult` is attached to the window
        as ``win.sharpmod_products``. Defaults to ``False`` so existing callers
        are unaffected.
    custom_config, custom_sars_lines : optional, keyword-only
        Accepted for backward compatibility; currently unused (the vendored
        SARS inset is left pristine).
    controller : QWidget, keyword-only
        An existing controller/parent to compose ``SPCWindow`` onto. It must
        provide the same ``config_changed`` signal + ``preferencesbox`` slot
        contract as :class:`RenderController` (e.g. the interactive picker
        window, which doubles as the controller so the ``W`` key can refocus
        it). When omitted a headless :class:`RenderController` is created, which
        preserves the existing offscreen-render behaviour.

    Returns
    -------
    tuple(SPCWindow, controller)
        The composed window and its controller. The controller is the window's
        Qt parent and **must outlive it**, so the caller has to retain the
        returned reference for the window's duration.
    """
    _install_streamwiseness_hooks()
    # Install before ``SPCWidget`` is constructed so its existing Qt signal
    # connections bind the history-aware mutation methods. Headless renderers
    # never attach an ``AnalysisHistory``, so these wrappers are no-ops there.
    from sharpmod.sessions import install_history_hooks
    install_history_hooks(_VendoredSPCWidget)
    if controller is None:
        controller = RenderController(config)
    win = SPCWindow(parent=controller, cfg=config)
    if prof_col is not None:
        win.addProfileCollection(prof_col, check_integrity=check_integrity)
    # Re-apply the palette with update_gui=True so every inset recomputes
    # per-value tier colors against the current config rather than keeping
    # stale defaults.
    win.spc_widget.updateConfig(config, update_gui=True)

    if mount:
        prof = _highlighted_profile(prof_col)
        win.sharpmod_products = mount_products(win, prof)
        # Drive the SHARPpy Reimagined products through the same Color-Scheme re-apply
        # step so the documented palette is applied consistently to every
        # SHARPpy Reimagined panel/inset (Requirement 22.4) and each recomputes its
        # per-value tier colors from the current value (Requirement 22.5).
        reapply_color_scheme(win, config)
    return win, controller


def reapply_color_scheme(win, config=None):
    """Re-apply the documented Color Scheme to every mounted SHARPpy Reimagined product.

    This is the in-workspace half of the ``updateConfig(config,
    update_gui=True)`` re-apply step: it resolves the documented palette via
    :func:`sharpmod.colors.scheme_preferences` (honoring ``config`` overrides
    where present) and pushes it into each SHARPpy Reimagined panel/inset that exposes the
    ``setPreferences`` contract. Passing ``update_gui=True`` forces every panel
    to redraw, which recomputes each per-value tier color from the *current*
    value rather than reusing a stale default (Requirements 22.4, 22.5).

    Products are discovered from ``win.sharpmod_products`` (the
    :class:`MountResult` from :func:`mount_products`) plus any SHARPpy Reimagined tables
    attached directly to the composed widget (e.g. a ``derived_indices`` panel).
    Every push is individually guarded so a single widget refusing the palette
    can never abort the re-apply. Returns the list of product names the scheme
    was successfully applied to.
    """
    prefs = colors.scheme_preferences(config)
    applied = []

    products = getattr(win, "sharpmod_products", None)
    sw = getattr(win, "spc_widget", None) or win
    candidates = []
    if products is not None:
        candidates.extend(
            (name, getattr(products, name, None))
            for name in (
                "ship", "composite", "thermo", "kinematic", "hail",
                "streamwiseness",
            )
        )
    # Any SHARPpy Reimagined panels/insets attached straight onto the composed widget.
    # ``derived_indices``/``hazard_type``/``custom_panel`` are kept in the scan
    # for backward compatibility with widgets that expose those names.
    for attr in (
            "composite_panel", "thermo_panel", "kinematic_panel",
            "ship_inset", "derived_indices", "hazard_type", "custom_panel",
            "streamwiseness"):
        candidates.append((attr, getattr(sw, attr, None)))

    seen = set()
    for name, widget in candidates:
        if widget is None or id(widget) in seen:
            continue
        seen.add(id(widget))
        set_prefs = getattr(widget, "setPreferences", None)
        if not callable(set_prefs):
            continue
        try:
            set_prefs(update_gui=True, **prefs)
            applied.append(name)
        except Exception:
            # A widget refusing the palette must never abort the re-apply.
            continue
    return applied


# ===========================================================================
# Mounting SHARPpy Reimagined derived-parameter rows INTO the vendored index panels
# ===========================================================================
#
# The SHARPpy Reimagined derived parameters are drawn *inside* the vendored index panel
# that already holds their family, appended just below the related existing
# rows -- no extra row, no strip, no window-height growth (the canvas keeps its
# original ~1180x800 size). Each family maps to one vendored widget:
#
#   * SFC-500 m kinematics (``srh500`` / ``shear_sfc_500m`` /
#     ``mean_wind_sfc_500m``) -> the vendored ``plotKinematics`` panel
#     (``spc_widget.kinematic``), appended beneath the 0-1/0-3 km SRH/shear/
#     mean-wind rows.
#   * Layer thermodynamics (``cape_0_6km``, ``hgz_cape``, ``ncape``, ``wbz_height``,
#     ``ecape``, ``lapserate_sfc_1km``) -> the vendored ``plotText`` panel
#     (``spc_widget.convective``), appended beneath the CAPE/lapse-rate rows.
#   * Composite indices (``dcp``, ``ehi_0_1km``, ``ehi_0_3km``, ``vgp``,
#     ``lrghail``, ``peskov``, ``mcs_index``) -> the vendored ``plotSTP``
#     ("STP STATS") inset, appended beneath the STP/SCP/SHIP composite indices.
#
# Integration seam: a guarded wrapper is installed on each vendored widget's
# ``plotData`` (mirroring :func:`attach_hgz_overlay`) that, AFTER the vendored
# draw, appends the family rows onto the widget's own backing ``plotBitMap``
# using a compact font in a bottom band. Values are read OFF the SHARPpy Reimagined
# derived Profile (:func:`_derived_profile`) and never recomputed (Req 13.3);
# an unavailable value renders the documented missing indicator "--". CAPE and
# lapse-rate rows are recolored on the documented tier scale for legibility on
# black (:func:`sharpmod.colors.tier_color`); the rest use the neutral
# foreground.
#
# Because the vendored panels are fixed-height and already full, the render
# (:func:`sharpmod.render.render`) tightens the vendored row pitch of the
# kinematics/thermo panels (``reserve_panel_band``) so the appended band fits
# without clipping the existing rows. Every wrapper is individually guarded so a
# failure can never break the base render.
#
# SHIP note: the SHIP composite already has a home in the vendored ``plotText``
# panel (its "SHIP =" row in ``drawSevere``), so no separate SHIP panel is
# reintroduced. The vendored SARS + STP insets are left pristine.

#: Value formatters (pure). ``raw`` may be a scalar or a 2-vector (u, v).
def _fmt_int(raw):
    """Rounded integer (J/kg CAPE, SRH, shear readouts)."""
    return str(int(round(_scalar(raw))))


def _fmt_f1(raw):
    """One decimal place (unitless composite indices, lapse rate)."""
    return f"{_scalar(raw):.1f}"


def _fmt_f2(raw):
    """Two decimal places (NCAPE and other fine-grained readouts)."""
    return f"{_scalar(raw):.2f}"


def _fmt_mag_int(raw):
    """Magnitude of a 2-vector as a rounded integer (mean-wind speed)."""
    return str(int(round(_scalar(raw))))


def _scalar(raw):
    """Coerce a scalar or a 2-vector to a float magnitude."""
    if isinstance(raw, (tuple, list)) and len(raw) == 2:
        import math
        return math.hypot(float(raw[0]), float(raw[1]))
    return float(raw)


def _row_missing(raw):
    """Missing-test that understands the (u, v) 2-vector rows (mean wind).

    :func:`sharpmod.colors.is_missing` coerces via ``float(...)`` and therefore
    treats any 2-vector as missing (``float((u, v))`` raises). A mean-wind row
    carries a valid ``(u, v)`` tuple whose *magnitude* is shown, so classify a
    2-vector as missing only when a component itself is missing; scalars defer
    to the documented :func:`~sharpmod.colors.is_missing`.
    """
    if isinstance(raw, (tuple, list)) and len(raw) == 2:
        return colors.is_missing(raw[0]) or colors.is_missing(raw[1])
    return colors.is_missing(raw)


# Each family row: (label, Profile attribute, formatter, tier-scale key | None).
# The tier key (a :data:`sharpmod.colors.TIER_THRESHOLDS` key) recolors the
# value from the *current* reading at draw time; ``None`` keeps the neutral
# foreground.

#: SFC-500 m kinematics rows (appended into ``plotKinematics``).
KINEMATIC_FAMILY_ROWS = [
    ("SFC-500m SRH", "srh500", _fmt_int, None),
    ("SFC-500m Shear", "shear_sfc_500m", _fmt_int, None),
    ("SFC-500m MnWind", "mean_wind_sfc_500m", _fmt_mag_int, None),
    ("SFC-500m SRW", "srw_sfc_500m", _fmt_mag_int, None),
]

#: Layer thermodynamics rows (appended into ``plotText``).
THERMO_FAMILY_ROWS = [
    ("6CAPE", "cape_0_6km", _fmt_int, "cape"),
    ("HGZ CAPE", "hgz_cape", _fmt_int, "cape"),
    ("NCAPE", "ncape", _fmt_f2, None),
    ("WBZ Height", "wbz_height", _fmt_int, None),
    ("ECAPE", "ecape", _fmt_int, "cape"),
    ("SFC-500m LR", "lapserate_sfc_500m", _fmt_f1, "lapse_rate"),
    ("SFC-1km LR", "lapserate_sfc_1km", _fmt_f1, "lapse_rate"),
]

#: Composite indices rows (appended into the STP "STP STATS" inset).
COMPOSITE_FAMILY_ROWS = [
    ("DCP", "dcp", _fmt_f1, None),
    ("EHI 0-1km", "ehi_0_1km", _fmt_f1, None),
    ("EHI 0-3km", "ehi_0_3km", _fmt_f1, None),
    ("VGP", "vgp", _fmt_f2, None),
    ("LRG HAIL", "lrghail", _fmt_f1, None),
    ("Peskov", "peskov", _fmt_f1, None),
    ("MCS", "mcs_index", _fmt_f1, None),
]

#: Missing-value indicator drawn when a derived value is unavailable.
MISSING_STR = colors.MISSING_STR


# ---------------------------------------------------------------------------
# Grouped CustomPanel configurations (in-grid placement)
# ---------------------------------------------------------------------------
#
# The three SHARPpy Reimagined derived-parameter families are rendered as real
# :class:`~sharpmod.viz.custom_panel.CustomPanel` widgets placed *inside* the
# vendored bottom table band (``grid3``), each directly beneath its family
# column (see :func:`mount_products`). Each config is an ordered list of
# ``PanelItem`` naming a Profile attribute; the panel resolves the label/unit
# from :data:`sharpmod.sharptab.constants.PARAM_REGISTRY` and reads the value
# OFF the SHARPpy Reimagined-derived Profile (never recomputing it -- Requirement 13.3).

#: SFC-500 m kinematics panel (placed beneath the vendored kinematics table).
KINEMATIC_PANEL_ITEMS = [
    PanelItem(param="srh500"),
    PanelItem(param="shear_sfc_500m"),
    PanelItem(param="mean_wind_sfc_500m"),
    PanelItem(param="srw_sfc_500m"),
]

#: Hail group (large-hail parameters on their own).
HAIL_PANEL_ITEMS = [
    PanelItem(param="vgp"),
    PanelItem(param="lrghail"),
]

#: Layer thermodynamics panel (placed beneath the vendored convective table).
THERMO_PANEL_ITEMS = [
    PanelItem(param="cape_0_6km"),
    PanelItem(param="hgz_cape"),
    PanelItem(param="ecape"),
    PanelItem(param="ncape"),
    PanelItem(param="wbz_height"),
    PanelItem(param="lapserate_sfc_500m"),
    PanelItem(param="lapserate_sfc_1km"),
]

#: Composite indices panel (placed beneath the Effective Layer STP chart, and
#: spanning the freed SARS column so the wider list stays readable).
COMPOSITE_PANEL_ITEMS = [
    PanelItem(param="dcp"),
    PanelItem(param="ehi_0_1km"),
    PanelItem(param="ehi_0_3km"),
    PanelItem(param="peskov"),
    PanelItem(param="mcs_index"),
]

#: Convective-table appended rows: 6CAPE (next to 3CAPE) and the SFC-1km
#: lapse rate (next to the other lapse rates), placed in the vendored
#: convective panel.
CONVECTIVE_APPEND_ROWS = [
    ("6CAPE", "cape_0_6km", _fmt_int, "cape"),
    ("SFC-1km LR", "lapserate_sfc_1km", _fmt_f1, "lapse_rate"),
]

#: Ex-SARS indices panel (occupies the freed SARS slot): the composite
#: indices plus the remaining layer-thermodynamics with no vendored home.
EXSARS_PANEL_ITEMS = [
    PanelItem(param="hgz_cape"),
    PanelItem(param="ecape"),
    PanelItem(param="ncape"),
    PanelItem(param="wbz_height"),
    PanelItem(param="dcp"),
    PanelItem(param="ehi_0_1km"),
    PanelItem(param="ehi_0_3km"),
    PanelItem(param="vgp"),
    PanelItem(param="lrghail"),
    PanelItem(param="peskov"),
    PanelItem(param="mcs_index"),
]

#: Minimum height (px) given to each mounted family panel so the enlarged rows
#: are comfortably readable in the widened bottom table band.
PANEL_MIN_HEIGHT = 180


@dataclass
class MountResult:
    """Outcome of :func:`mount_products`.

    The SHARPpy Reimagined derived-parameter families are mounted as real
    :class:`~sharpmod.viz.custom_panel.CustomPanel` widgets placed *inside* the
    vendored bottom table band (``grid3``), each directly beneath its family
    column -- there is no separate strip and no extra top-level row.

    ``mounted`` names each panel that was placed into ``grid3`` (with its grid
    cell); ``blocked`` names any step that could not complete (with the reason),
    including the SARS-inset detach. ``hgz_attached`` reports whether the skew-T
    overlay pass was installed. ``sars_detached`` reports whether the vendored
    SARS analogues inset was successfully removed from ``grid3`` to reclaim its
    column. ``kinematic``/``thermo``/``composite`` reference the mounted
    ``CustomPanel`` widgets (or ``None`` when that panel was blocked).
    ``streamwiseness`` references the 0-6 km chart mounted beside the narrowed
    right/STP inset. ``ship`` is ``None`` -- the SHIP inset is not mounted in
    this family layout (the vendored ``plotText`` panel already carries the
    SHIP composite).
    """

    ship: Optional[object] = None
    streamwiseness: Optional[object] = None
    composite: Optional[object] = None
    thermo: Optional[object] = None
    kinematic: Optional[object] = None
    hgz_attached: bool = False
    sars_detached: bool = False
    mounted: List[str] = field(default_factory=list)
    blocked: List[str] = field(default_factory=list)


def _highlighted_profile(prof_col):
    """Best-effort extraction of the analyzed Profile from a collection."""
    if prof_col is None:
        return None
    for getter in ("getHighlightedProf", "getCurrentProfs", "getProfile"):
        fn = getattr(prof_col, getter, None)
        if callable(fn):
            try:
                prof = fn()
            except Exception:
                continue
            if isinstance(prof, dict):
                # getCurrentProfs() -> {parcel: Profile}; take any member.
                prof = next(iter(prof.values()), None)
            if prof is not None:
                return prof
    return None


def _derived_profile(prof):
    """Return a Profile that exposes the SHARPpy Reimagined-derived attributes.

    The analyzed profile handed to the renderer is the vendored
    ``sharppy`` profile: it carries the core reported-level arrays (and the
    vendored ``ship`` attribute) but *not* the SHARPpy Reimagined lazy-derived attributes
    (``dcp``, ``ehi_0_1km``, ``hgz_cape`` ...). The derived-indices panel reads
    those attributes OFF a Profile and never recomputes them (Requirement 13.3);
    the canonical Profile that computes them lazily/cached is
    :class:`sharpmod.sharptab.profile.Profile`.

    This builds that Profile from the analyzed profile's core arrays so the panel
    can read the derived values off it. Values the derived functions cannot
    resolve stay :data:`MISSING` and render as the documented missing indicator.
    Best-effort: on any failure the original ``prof`` is returned unchanged so
    the panel still draws (with unresolved/missing indicators).
    """
    if prof is None:
        return None
    try:
        from sharpmod.sharptab.profile import Profile as _SMProfile
        from sharpmod.sharptab.profile import create_profile as _sm_create
    except Exception:
        return prof
    # Already a SHARPpy Reimagined Profile -> read its derived attributes directly.
    if isinstance(prof, _SMProfile):
        return prof
    try:
        raw_meta = getattr(prof, "meta", None)
        meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}
        for key, attr in (
            ("date", "date"),
            ("valid", "date"),
            ("location", "location"),
            ("loc", "location"),
        ):
            if key in meta:
                continue
            value = getattr(prof, attr, None)
            if value is not None:
                meta[key] = value
        for key, attr in (
            ("lat", "latitude"),
            ("lon", "longitude"),
            ("sfc_relative_vorticity", "sfc_relative_vorticity"),
            ("surface_relative_vorticity", "surface_relative_vorticity"),
            ("sfc_vorticity", "sfc_vorticity"),
            ("surface_vorticity", "surface_vorticity"),
            ("vorticity", "vorticity"),
        ):
            if key in meta:
                continue
            value = getattr(prof, attr, None)
            try:
                fval = float(value)
            except (TypeError, ValueError):
                continue
            if fval == fval:
                meta[key] = fval
        return _sm_create(
            pres=prof.pres, hght=prof.hght, tmpc=prof.tmpc, dwpc=prof.dwpc,
            wdir=prof.wdir, wspd=prof.wspd,
            omeg=getattr(prof, "omeg", None),
            wetbulb=getattr(prof, "wetbulb", None),
            meta=meta,
        )
    except Exception:
        return prof


def _refresh_mounted_products(sw):
    """Refresh custom products from the currently focused/recomputed profile."""
    prof = getattr(sw, "default_prof", None)
    if prof is None:
        return
    derived = _derived_profile(prof)

    board = getattr(sw, "index_board", None)
    if board is not None:
        try:
            board.setData(prof, derived)
        except Exception:
            pass

    stream = getattr(sw, "streamwiseness", None)
    if stream is not None:
        try:
            stream.setProf(prof)
        except Exception:
            pass

    sound = getattr(sw, "sound", None)
    if sound is not None:
        sound._sharpmod_derived_profile = derived
        try:
            sound.clearData()
            sound.plotData()
            sound.update()
        except Exception:
            pass


def attach_hgz_overlay(
    skewt, *, fill_color=None, edge_color=None, draw_fill=True, profile=None
):
    """Install the Hail-Growth-Zone overlay pass on a vendored skew-T widget.

    This is the concrete mount seam for Requirements 19.9-19.11: it wraps the
    skew-T's ``plotData`` so that, *after* the vendored widget renders its
    temperature/dewpoint traces onto its backing pixmap, the HGZ band is drawn
    over the -10 degrees C to -30 degrees C layer via
    :func:`sharpmod.viz.skew.draw_hgz_overlay`, using the widget's own
    pressure->pixel transform (``originy + pres_to_pix(p) / scale`` -- the same
    composition the vendored widget applies to every plotted level) and its plot
    rectangle (``tlx/tly/brx/bry``). The overlay draws nothing when
    ``skewt.prof.hgz_cape`` is missing (Requirement 19.11) and is clipped to the
    plot rectangle (Requirement 19.10). ``profile`` may supply the SHARPpy
    Reimagined derived profile when the vendored skew-T profile lacks those
    lazy-derived attributes.

    The wrapper is defensive: any failure in the overlay pass is swallowed so it
    can never break the base skew-T rendering. Returns ``True`` when the pass was
    installed, ``False`` when the widget does not expose the required hooks
    (``plotData`` / ``plotBitMap`` / the plot geometry).
    """
    if skewt is None:
        return False
    if getattr(skewt, "_sharpmod_hgz_attached", False):
        return True
    original_plot_data = getattr(skewt, "plotData", None)
    if not callable(original_plot_data):
        return False
    if profile is not None:
        skewt._sharpmod_derived_profile = profile

    def _plot_rect():
        tlx = getattr(skewt, "tlx", None)
        tly = getattr(skewt, "tly", None)
        brx = getattr(skewt, "brx", None)
        bry = getattr(skewt, "bry", None)
        if None in (tlx, tly, brx, bry):
            return None
        left = int(tlx)
        # When the omega meter is drawn it sits at the cold (left) end of the
        # skew-T (~-49..-41 C at 1000 mb). Start the HGZ band to the right of it
        # so the translucent fill does not wash out the omega bars.
        if getattr(skewt, "plot_omega", False):
            try:
                omega_right = skewt.tmpc_to_pix(-39, 1000)
                if omega_right == omega_right:  # NaN guard
                    left = max(left, int(omega_right))
            except Exception:
                pass
        return QRect(left, int(tly), int(brx - left), int(bry - tly))

    def _transform(p):
        # Match the vendored widget's own level transform so the band aligns
        # with the plotted isotherms even under pan/zoom.
        originy = getattr(skewt, "originy", 0.0) or 0.0
        scale = getattr(skewt, "scale", 1.0) or 1.0
        return originy + skewt.pres_to_pix(p) / scale

    def _wrapped_plot_data(*args, **kwargs):
        result = original_plot_data(*args, **kwargs)
        try:
            prof = getattr(skewt, "_sharpmod_derived_profile", None)
            if prof is None:
                prof = profile if profile is not None \
                    else getattr(skewt, "prof", None)
            rect = _plot_rect()
            bitmap = getattr(skewt, "plotBitMap", None)
            if prof is not None and rect is not None and bitmap is not None:
                qp = QtGui.QPainter()
                qp.begin(bitmap)
                try:
                    draw_hgz_overlay(
                        qp, prof, rect, _transform,
                        fill_color=fill_color, edge_color=edge_color,
                        draw_fill=draw_fill,
                    )
                finally:
                    qp.end()
        except Exception:
            # The overlay pass must never break the base skew-T rendering.
            pass
        return result

    skewt.plotData = _wrapped_plot_data
    skewt._sharpmod_hgz_attached = True
    return True


def attach_cape_fill(skewt, *, pos_color=None, neg_color=None):
    """Install the CAPE/CIN buoyancy-area fill pass on a vendored skew-T widget.

    Wraps the skew-T's ``plotData`` so that, *after* the vendored widget renders
    its temperature/dewpoint/parcel traces onto its backing pixmap, the area
    between the current parcel's virtual-temperature trace (``skewt.pcl.ttrace``
    / ``ptrace``) and the environment virtual-temperature trace
    (``skewt.prof.vtmp`` / ``pres``) is shaded via
    :func:`sharpmod.viz.skew.draw_cape_fill`: orange where the parcel is warmer
    (CAPE) and blue where it is colder (CIN).

    The pass uses the widget's own composed transforms -- ``originy +
    pres_to_pix(p)/scale`` and ``originx + tmpc_to_pix(t, p)/scale`` -- so the
    shading aligns with the plotted parcel trace under pan/zoom, and is clipped
    to the plot rectangle (``tlx/tly/brx/bry``). It draws nothing when no parcel
    has been set. The wrapper swallows any error so it can never break the base
    skew-T rendering. Returns ``True`` when the pass was installed, ``False``
    when the widget does not expose the required hooks.
    """
    if skewt is None:
        return False
    if getattr(skewt, "_sharpmod_cape_fill_attached", False):
        return True
    original_plot_data = getattr(skewt, "plotData", None)
    if not callable(original_plot_data):
        return False

    def _plot_rect():
        tlx = getattr(skewt, "tlx", None)
        tly = getattr(skewt, "tly", None)
        brx = getattr(skewt, "brx", None)
        bry = getattr(skewt, "bry", None)
        if None in (tlx, tly, brx, bry):
            return None
        return QRect(int(tlx), int(tly), int(brx - tlx), int(bry - tly))

    def _to_y(p):
        originy = getattr(skewt, "originy", 0.0) or 0.0
        scale = getattr(skewt, "scale", 1.0) or 1.0
        return originy + skewt.pres_to_pix(p) / scale

    def _to_x(t, p):
        originx = getattr(skewt, "originx", 0.0) or 0.0
        scale = getattr(skewt, "scale", 1.0) or 1.0
        return originx + skewt.tmpc_to_pix(t, p) / scale

    def _wrapped_plot_data(*args, **kwargs):
        result = original_plot_data(*args, **kwargs)
        try:
            prof = getattr(skewt, "prof", None)
            pcl = getattr(skewt, "pcl", None)
            rect = _plot_rect()
            bitmap = getattr(skewt, "plotBitMap", None)
            if prof is not None and pcl is not None and rect is not None and bitmap is not None:
                qp = QtGui.QPainter()
                qp.begin(bitmap)
                try:
                    qp.setRenderHint(qp.Antialiasing)
                    draw_cape_fill(
                        qp,
                        getattr(pcl, "ttrace", None),
                        getattr(pcl, "ptrace", None),
                        getattr(prof, "pres", None),
                        getattr(prof, "vtmp", None),
                        rect,
                        _to_x,
                        _to_y,
                        pos_color=pos_color,
                        neg_color=neg_color,
                    )
                finally:
                    qp.end()
        except Exception:
            # The fill pass must never break the base skew-T rendering.
            pass
        return result

    skewt.plotData = _wrapped_plot_data
    skewt._sharpmod_cape_fill_attached = True
    return True


def _family_entry(prof, label, attr, formatter, tier_param):
    """Resolve one family row to ``(label, value_text, QColor)``.

    The value is read OFF ``prof`` and never recomputed (Requirement 13.3). A
    missing/masked/non-finite value renders the documented ``--`` indicator in
    the neutral foreground; a present value is formatted and, when the row has a
    documented tier scale, recolored from the *current* reading via
    :func:`sharpmod.colors.tier_color` (Requirement 22.5).
    """
    fg = QtGui.QColor(colors.FG_COLOR)
    raw = getattr(prof, attr, None) if prof is not None else None
    if _row_missing(raw):
        return (label, MISSING_STR, fg)
    try:
        text = formatter(raw)
    except Exception:
        return (label, MISSING_STR, fg)
    color = fg
    if tier_param is not None:
        try:
            color = QtGui.QColor(colors.tier_color(tier_param, _scalar(raw)))
        except Exception:
            color = fg
    return (label, text, color)


def _draw_family_band(widget, entries, *, title=None, ncols=1):
    """Append ``entries`` as a compact bottom band on ``widget.plotBitMap``.

    Draws a dark band (with a top separator matching the vendored inset border)
    across the bottom of the widget's backing pixmap and lays the rows out in
    ``ncols`` columns using a compact copy of the widget's own label font, so
    the appended rows visually match the panel. Everything is clipped to the
    pixmap so nothing spills into adjacent panels. Sets
    ``widget._sharpmod_rows_ran = True`` once drawn.
    """
    bmp = getattr(widget, "plotBitMap", None)
    if bmp is None:
        return
    W = bmp.width()
    H = bmp.height()
    if W <= 2 or H <= 2 or not entries:
        return

    base = getattr(widget, "label_font", None)
    font = QtGui.QFont(base) if base is not None else QtGui.QFont("Helvetica")
    font.setBold(False)
    font.setPixelSize(9)
    fm = QtGui.QFontMetrics(font)
    row_h = max(fm.height(), 10)

    n = len(entries)
    ncols = max(1, min(ncols, n))
    per_col = (n + ncols - 1) // ncols
    title_h = row_h if title else 0
    band_h = title_h + per_col * row_h + 3
    band_top = max(0, H - band_h - 1)

    qp = QtGui.QPainter()
    qp.begin(bmp)
    try:
        qp.setClipRect(QRect(0, 0, W, H))
        # Dark band so the appended rows stay legible over whatever vendored
        # content sat at the bottom, with a separator in the inset border color.
        qp.fillRect(QRect(0, band_top, W, H - band_top),
                    QtGui.QColor(colors.BG_COLOR))
        qp.setPen(QtGui.QPen(QtGui.QColor("#3399CC"), 1, Qt.SolidLine))
        qp.drawLine(0, band_top, W, band_top)

        y0 = band_top + 2
        if title:
            tf = QtGui.QFont(font)
            tf.setBold(True)
            qp.setFont(tf)
            qp.setPen(QtGui.QPen(QtGui.QColor(colors.FG_COLOR), 1, Qt.SolidLine))
            qp.drawText(QRect(3, y0, W - 6, row_h),
                        int(Qt.AlignLeft | Qt.AlignVCenter), title)
            y0 += row_h

        qp.setFont(font)
        col_w = W // ncols
        for i, (label, text, color) in enumerate(entries):
            col = i // per_col
            row = i % per_col
            x = col * col_w
            y = y0 + row * row_h
            lbl_w = int(col_w * 0.62)
            lbl_rect = QRect(x + 3, y, lbl_w - 3, row_h)
            val_rect = QRect(x + lbl_w, y, col_w - lbl_w - 4, row_h)
            qp.setPen(QtGui.QPen(QtGui.QColor(colors.FG_COLOR), 1, Qt.SolidLine))
            qp.drawText(lbl_rect,
                        int(Qt.TextSingleLine | Qt.AlignLeft | Qt.AlignVCenter),
                        fm.elidedText(label, Qt.ElideRight, lbl_rect.width()))
            qp.setPen(QtGui.QPen(color, 1, Qt.SolidLine))
            qp.drawText(val_rect,
                        int(Qt.TextSingleLine | Qt.AlignRight | Qt.AlignVCenter),
                        text)
    finally:
        qp.end()
    widget._sharpmod_rows_ran = True


def attach_family_rows(widget, rows, derived_prof, *, title=None, ncols=1):
    """Install a guarded ``plotData`` wrapper that appends family ``rows``.

    Mirrors :func:`attach_hgz_overlay`: it wraps the vendored widget's
    ``plotData`` so that, AFTER the vendored draw, the SHARPpy Reimagined family rows are
    appended onto the widget's backing ``plotBitMap`` via
    :func:`_draw_family_band`. Values are read OFF ``derived_prof`` (a SHARPpy Reimagined
    Profile exposing the derived attributes) and never recomputed. The wrapper
    swallows any error so it can never break the base render. Returns ``True``
    when installed, ``False`` when the widget exposes no callable ``plotData``.
    """
    if widget is None:
        return False
    if getattr(widget, "_sharpmod_family_attached", False):
        widget._sharpmod_derived_prof = derived_prof
        return True
    original_plot_data = getattr(widget, "plotData", None)
    if not callable(original_plot_data):
        return False

    widget._sharpmod_derived_prof = derived_prof
    widget._sharpmod_rows_ran = False

    def _wrapped_plot_data(*args, **kwargs):
        result = original_plot_data(*args, **kwargs)
        try:
            prof = getattr(widget, "_sharpmod_derived_prof", None)
            entries = [_family_entry(prof, *spec) for spec in rows]
            _draw_family_band(widget, entries, title=title, ncols=ncols)
        except Exception:
            # Appending rows must never break the base panel rendering.
            pass
        return result

    widget.plotData = _wrapped_plot_data
    widget._sharpmod_family_attached = True
    return True


def _find_stp_inset(sw):
    """Locate the vendored STP ("STP STATS") inset on the composed widget."""
    insets = getattr(sw, "insets", None)
    if isinstance(insets, dict):
        stp = insets.get("STP STATS")
        if stp is not None:
            return stp
    try:
        from sharppy.viz.stp import plotSTP
        found = sw.findChildren(plotSTP)
        if found:
            return found[0]
    except Exception:
        pass
    return None


def _detach_sars_inset(sw, result):
    """Detach the vendored SARS analogues inset from ``grid3`` (0,2).

    Frees the SARS column so the composite panel can occupy/span it. The inset
    widget is removed from the layout, reparented away, and hidden -- but never
    deleted, so the vendored swap/menu machinery keeps a valid reference. Fully
    guarded: any failure is recorded in ``result.blocked`` and never raised.
    """
    grid3 = getattr(sw, "grid3", None)
    sars = getattr(sw, "left_inset_ob", None)
    if grid3 is None or sars is None:
        result.blocked.append(
            "SARS detach: grid3/left_inset_ob not found on the vendored widget"
        )
        return
    try:
        grid3.removeWidget(sars)
        sars.setParent(None)
        sars.hide()
        result.sars_detached = True
        result.mounted.append("SARS analogues inset detached from grid3 (0,2)")
    except Exception as exc:  # noqa: BLE001 - record, never abort the mount
        result.blocked.append(f"SARS detach: {exc}")


def _mount_family_panel(sw, grid3, title, items, cell, derived):
    """Build, configure, and place one grouped :class:`CustomPanel`.

    Returns the mounted panel. Raises on failure so the caller can record the
    failure into :attr:`MountResult.blocked` (the caller guards every call).
    """
    parent = getattr(sw, "text", None) or sw
    panel = CustomPanel(parent=parent)
    panel.title = title
    panel.configure(items)
    # Read values OFF the SHARPpy Reimagined-derived Profile (never recomputed).
    panel.setProf(derived)
    # Enlarge the panel so the rows are readable in the widened table band.
    panel.setMinimumHeight(PANEL_MIN_HEIGHT)
    grid3.addWidget(panel, *cell)
    panel.show()
    return panel


def mount_products(win, prof=None, *, custom_config=None, custom_sars_lines=None):
    """Place the SHARPpy Reimagined derived-parameter family panels INSIDE the chart.

    The three grouped :class:`~sharpmod.viz.custom_panel.CustomPanel` widgets are
    added to the vendored bottom table band (``grid3``) as a *second row*, each
    directly beneath its family column -- there is no separate strip and no extra
    top-level row:

    * layer thermodynamics -> ``grid3`` (1, 0)  (below the convective table),
    * SFC-500 m kinematics  -> ``grid3`` (1, 1)  (below the kinematics table),
    * composite indices     -> ``grid3`` (1, 2, 1, 2)  (below the STP chart,
      spanning the freed SARS column).

    Before placing the panels the vendored SARS analogues inset is detached from
    ``grid3`` (0, 2) to reclaim its column (:func:`_detach_sars_inset`); the
    Streamwiseness is placed at (0, 3) and the Effective Layer STP chart moves
    to (0, 4), splitting its former width allocation. The skew-T HGZ overlay is
    also attached (Requirement 19.9).

    Values are read OFF a SHARPpy Reimagined Profile derived from ``prof``
    (:func:`_derived_profile`) and never recomputed (Requirement 13.3);
    unavailable values render the documented ``--`` indicator.

    Every step is individually guarded: a failure is captured in
    :attr:`MountResult.blocked` (naming the step) instead of raising, so a
    partial mount is observable rather than fatal.

    Parameters
    ----------
    win :
        The composed window (a vendored ``SPCWindow``) or any object exposing the
        vendored ``SPCWidget`` surface via a ``spc_widget`` attribute (or being
        one itself).
    prof : optional
        The analyzed Profile the derived Profile is built from.
    custom_config, custom_sars_lines : optional
        Accepted for backward compatibility; currently unused.

    Returns
    -------
    MountResult
        Which panels were placed into ``grid3`` (with their cells), which steps
        were blocked (with reasons), whether the SARS inset was detached, and
        whether the HGZ overlay pass was installed.
    """
    sw = getattr(win, "spc_widget", None) or win
    derived = _derived_profile(prof)
    result = MountResult()

    # Refactor: within the vendored bottom band (grid3), replace the
    # convective / kinematics / SARS panels with the SHARPpy Reimagined IndexBoard (a
    # legacy-styled 3-column reimplementation whose columns are computed so
    # Space Grotesk never overlaps, with the derived params woven in). The
    # vendored Effective Layer STP graphic is kept but later moved beside the
    # streamwiseness chart. The board spans grid3 columns 0-2.
    try:
        from sharpmod.viz.index_board import IndexBoard
        grid3 = getattr(sw, "grid3", None)
        for attr in ("convective", "kinematic", "left_inset_ob"):
            wdg = getattr(sw, attr, None)
            if wdg is not None and grid3 is not None:
                try:
                    grid3.removeWidget(wdg)
                    wdg.hide()
                except Exception:
                    pass
        board = IndexBoard(parent=getattr(sw, "text", None) or sw)
        board.setData(prof, derived)
        if grid3 is not None:
            grid3.addWidget(board, 0, 0, 1, 3)
            # Give Effective Layer STP a larger dedicated column. The IndexBoard
            # compensates internally so only its SHIP/composite section tightens.
            try:
                grid3.setColumnStretch(0, 4)
                grid3.setColumnStretch(1, 4)
                grid3.setColumnStretch(2, 4)
                grid3.setColumnStretch(3, 7)
            except Exception:
                pass
        board.show()
        sw.index_board = board
        result.composite = board
        result.mounted.append("IndexBoard (cols 0-2)")
    except Exception as exc:  # noqa: BLE001 - record, never abort the mount
        result.blocked.append(f"IndexBoard: {exc}")

    # Place the streamwiseness profile directly left of the existing right
    # inset.  The original board-to-right-inset width share remains stable;
    # only the old STP allocation is split between these two charts.
    try:
        from sharpmod.viz.streamwiseness import plotStreamwiseness

        grid3 = getattr(sw, "grid3", None)
        right = getattr(sw, "right_inset_ob", None)
        if grid3 is None or right is None:
            raise RuntimeError("grid3/right_inset_ob not found")
        stream = plotStreamwiseness(parent=getattr(sw, "text", None) or sw)
        stream.setProf(prof)
        latitude = getattr(prof, "latitude", 0.0) if prof is not None else 0.0
        stream.setDeviant("left" if latitude < 0 else "right")
        grid3.addWidget(stream, 0, 3)
        sw.streamwiseness = stream
        if isinstance(getattr(sw, "insets", None), dict):
            sw.insets["SHARPMOD STREAMWISENESS"] = stream
        if not _place_right_inset_after_streamwiseness(sw):
            raise RuntimeError("could not move right inset to column 4")
        stream.show()
        result.streamwiseness = stream
        result.mounted.append(
            "Streamwiseness (col 3); right/STP inset narrowed (col 4)")
    except Exception as exc:  # noqa: BLE001 - record, never abort the mount
        result.blocked.append(f"Streamwiseness: {exc}")

    # 3. CAPE/CIN buoyancy fill + HGZ overlay -> skew-T (Requirement 19.9).
    #    The CAPE/CIN fill is attached first so it renders *behind* the HGZ
    #    boundary lines/label. The HGZ band's own translucent fill is suppressed
    #    (``draw_fill=False``) so it does not muddy the buoyancy shading over the
    #    -10/-30 C layer -- only its dashed boundaries + label remain.
    skewt = getattr(sw, "sound", None)
    try:
        if attach_cape_fill(skewt):
            result.mounted.append("CAPE/CIN buoyancy fill (skew-T)")
        else:
            result.blocked.append(
                "CAPE/CIN fill: skew-T widget did not expose plotData/geometry"
            )
    except Exception as exc:  # noqa: BLE001
        result.blocked.append(f"CAPE/CIN fill: {exc}")

    try:
        if attach_hgz_overlay(skewt, draw_fill=False, profile=derived):
            result.hgz_attached = True
            result.mounted.append("HGZ overlay (skew-T)")
        else:
            result.blocked.append(
                "HGZ overlay: skew-T widget did not expose plotData/geometry"
            )
    except Exception as exc:  # noqa: BLE001
        result.blocked.append(f"HGZ overlay: {exc}")

    return result

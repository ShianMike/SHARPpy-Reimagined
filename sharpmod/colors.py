"""Documented Color Scheme for the SHARPpy Reimagined renderer (Requirement 22).

This module encodes the *foreground* Color Scheme the Renderer applies to
index-table values, tier/alert colors, and SARS match colors so every element
stays legible against the black chart background.

It has three responsibilities:

1. **Palette + legacy->modern substitutions** (Requirements 22.1, 22.2, 22.3).
   The legacy scheme used three low-contrast colors against black; each is
   replaced by a brightened, documented value:

   ===========================  ===========  ===========
   Element                      Legacy       Modernized
   ===========================  ===========  ===========
   SARS non-tornadic match      ``#996600``  ``#c9a24b``
   Alert tier L1                ``#775000``  ``#c8911f``
   Alert tier L2                ``#996600``  ``#e0a800``
   ===========================  ===========  ===========

2. **Per-value tier threshold maps** for CAPE, CINH, LCL, LI, lapse rate,
   STP (fixed), STP (effective/cin), SHIP, and SCP. The thresholds mirror the
   legacy renderer's coloring logic (thresholds provided by Rich Thompson, SPC).

3. **Recompute-at-draw-time functions** (Requirement 22.5). Each tier color is
   recomputed from the *current* value rather than reused from a stale default.
   The ``tier_color`` dispatcher and the per-parameter helpers all return a
   ``#rrggbb`` hex string, so this module carries no Qt dependency and stays
   pure/testable. The renderer wraps the returned hex in a ``QColor``.

Missing values (NumPy masked constant, ``None``, ``"--"``, non-finite floats)
never raise: they map to the neutral foreground color (or, for the alert-scale
parameters, to the documented lowest tier), matching the legacy behavior.
"""

from __future__ import annotations

import math

__all__ = [
    "BLACK",
    "WHITE",
    "FG_COLOR",
    "BG_COLOR",
    "LBROWN_LEGACY",
    "SARS_NONTOR_MATCH",
    "ALERT_L1_LEGACY",
    "ALERT_L1_COLOR",
    "ALERT_L2_LEGACY",
    "ALERT_L2_COLOR",
    "LEGACY_SUBSTITUTIONS",
    "ALERT_TIERS_LEGACY",
    "ALERT_TIERS",
    "TIER_THRESHOLDS",
    "MISSING_STR",
    "is_missing",
    "alert_tier_color",
    "cape_color",
    "cinh_color",
    "lcl_color",
    "li_color",
    "lapse_rate_color",
    "stp_fixed_color",
    "stp_effective_color",
    "scp_color",
    "ship_color",
    "sweat_color",
    "tier_color",
    "scheme_preferences",
]

# ---------------------------------------------------------------------------
# Base foreground palette
# ---------------------------------------------------------------------------

BLACK = "#000000"
WHITE = "#ffffff"

#: Neutral foreground: used for text with no active tier and as the safe
#: fallback for missing/undefined values.
FG_COLOR = WHITE
#: Chart background the palette is designed to remain legible against.
BG_COLOR = BLACK


# ---------------------------------------------------------------------------
# Legacy -> modern substitutions (Requirements 22.2, 22.3)
# ---------------------------------------------------------------------------

# SARS non-tornadic match color (Requirement 22.2): the dim legacy LBROWN is
# replaced by a readable tan.
LBROWN_LEGACY = "#996600"
SARS_NONTOR_MATCH = "#c9a24b"

# Amber alert tiers (Requirement 22.3): the two lowest amber tiers default to
# near-unreadable dark browns; brighten them while keeping the amber hue and
# the tier ordering intact.
ALERT_L1_LEGACY = "#775000"
ALERT_L1_COLOR = "#c8911f"

ALERT_L2_LEGACY = "#996600"
ALERT_L2_COLOR = "#e0a800"

#: The documented set of legacy->modern color substitutions. Keys are the
#: legacy hex values that must never be drawn; values are their replacements.
LEGACY_SUBSTITUTIONS = {
    ("sars_nontornadic", LBROWN_LEGACY): SARS_NONTOR_MATCH,
    ("alert_l1_color", ALERT_L1_LEGACY): ALERT_L1_COLOR,
    ("alert_l2_color", ALERT_L2_LEGACY): ALERT_L2_COLOR,
}


# ---------------------------------------------------------------------------
# Alert tier palette
# ---------------------------------------------------------------------------
#
# The renderer's alert-colored parameters index a 7-entry palette
# ``color_list[0..6]`` (config keys ``alert_lscp_color`` and
# ``alert_l1_color`` .. ``alert_l6_color``). Index 0 is the lowest/neutral
# tier; index 6 is the most extreme. Only L1 and L2 change under the
# modernized scheme (Requirement 22.3); the remaining tiers keep their legacy
# high-contrast values.

# palette index: (config key, legacy hex, modern hex)
_ALERT_PALETTE = (
    ("alert_lscp_color", "#775000", "#775000"),  # 0 - lowest / SCP-left / missing
    ("alert_l1_color", ALERT_L1_LEGACY, ALERT_L1_COLOR),  # 1
    ("alert_l2_color", ALERT_L2_LEGACY, ALERT_L2_COLOR),  # 2
    ("alert_l3_color", "#ffff00", "#ffff00"),  # 3 - yellow
    ("alert_l4_color", "#ff0000", "#ff0000"),  # 4 - red
    ("alert_l5_color", "#e700df", "#e700df"),  # 5 - magenta
    ("alert_l6_color", "#ad00e7", "#ad00e7"),  # 6 - purple
)

#: Legacy alert tier palette, indexed 0..6.
ALERT_TIERS_LEGACY = tuple(legacy for (_key, legacy, _modern) in _ALERT_PALETTE)
#: Modernized alert tier palette, indexed 0..6 (Requirement 22.1, 22.3).
ALERT_TIERS = tuple(modern for (_key, _legacy, modern) in _ALERT_PALETTE)


def alert_tier_color(index: int) -> str:
    """Return the modernized hex color for alert-palette ``index`` (0..6)."""
    return ALERT_TIERS[index]


# ---------------------------------------------------------------------------
# Missing-value handling
# ---------------------------------------------------------------------------

#: The string the renderer draws in place of an unavailable value.
MISSING_STR = "--"


def is_missing(value) -> bool:
    """Return True if ``value`` should be treated as missing/undefined.

    Handles the renderer's ``"--"`` sentinel, ``None``, the NumPy masked
    constant, and non-finite floats, without importing NumPy.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == MISSING_STR or value.strip() == ""
    # NumPy masked constant: numpy.ma.masked is falsy in a bool context and
    # compares equal to itself; detect it structurally without importing numpy.
    if getattr(value, "__class__", None).__name__ == "MaskedConstant":
        return True
    try:
        return not math.isfinite(float(value))
    except (TypeError, ValueError):
        return True


def _as_float(value):
    """Coerce ``value`` to float, or return None when not possible/finite."""
    if is_missing(value):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


# ---------------------------------------------------------------------------
# Per-value tier threshold maps
# ---------------------------------------------------------------------------
#
# Each map is an ordered list of ``(test, result)`` rules evaluated top to
# bottom; the first matching rule wins. ``result`` is either an alert-palette
# index (int) or a literal hex string (lapse rate uses fixed colors, not the
# alert palette). The maps are exposed as data (``TIER_THRESHOLDS``) so tests
# and documentation can introspect the exact cut points, and the helper
# functions below apply them at draw time.

# CAPE (J/kg): colored only for the selected parcel with positive CAPE.
_CAPE_RULES = (
    (4000, 6),
    (3000, 5),
    (2000, 4),
    (1000, 3),
    (0, 2),  # strictly > 0
)

# CINH (J/kg, negative): colored only when the parcel has positive CAPE.
_CINH_RULES = (
    (-25, 6),
    (-50, 5),
    (-75, 4),
    (-125, 3),
    # anything more negative than -125 -> tier 2
)

# LCL height (m AGL): lower LCL -> higher tier. Colored only with positive CAPE.
_LCL_RULES = (
    (750, 6),
    (1000, 5),
    (1250, 4),
    (1500, 3),
    (2000, 3),
    # >= 2000 -> tier 2
)

# Lifted Index (C): more negative -> higher tier. Colored only with positive CAPE.
_LI_RULES = (
    (-13, 6),
    (-10, 5),
    (-7, 4),
    (-4, 3),
    # >= -4 -> tier 2
)

# Lapse rate (C/km): fixed color scale (not the alert palette).
_LAPSE_RATE_RULES = (
    (6.0, "#00FF00"),
    (7.0, "#FFFF00"),
    (8.0, "#FFA500"),
    (9.0, "#FF0000"),
    # > 9.0 -> magenta
)
_LAPSE_RATE_TOP = "#FF00FF"

# STP (fixed-layer) unitless.
_STP_FIXED_RULES = (
    (7, 6),
    (5, 5),
    (2, 4),
    (1, 3),
    (0, 2),  # strictly > 0
    # else tier 1
)

# STP (effective / cin) and SCP share the same symmetric scale.
_STP_EFFECTIVE_RULES = (
    (15, 6),
    (10, 5),
    (5, 4),
    (2, 3),
    (0.5, 2),
    (-0.5, 1),
    # < -0.5 -> tier 0
)

# SHIP unitless.
_SHIP_RULES = (
    (5, 6),
    (3, 5),
    (2, 4),
    (1, 3),
    (0.1, 2),
    # else tier 1
)

# SWEAT (Severe Weather Threat) index -- fixed color scale (not the alert
# palette). Bands per the documented request:
#   < 250      -> blue
#   250 - 350  -> white
#   350 - 500  -> yellow
#   500 - 650  -> red
#   >= 650     -> pink
_SWEAT_RULES = (
    (250, "#3399FF"),   # < 250 : blue
    (350, "#FFFFFF"),   # 250-350 : white
    (500, "#FFFF00"),   # 350-500 : yellow
    (650, "#FF0000"),   # 500-650 : red
    # >= 650 -> pink
)
_SWEAT_TOP = "#FF00FF"

# LRGHAIL (SPC Large Hail Parameter / LHP) unitless, physical range [0, 20].
# Thresholds track the LHP distributions reported by Johnson & Sugden (2014):
# median ~14.5 for the >=3.5 in class, ~10.6 for >=2 in, ~7.9 for 2.0-3.25 in,
# and ~4 for sub-severe sizes.
_LRGHAIL_RULES = (
    (14, 6),   # giant hail (>=3.5 in) territory
    (10, 5),   # significant large hail (>=2 in) median
    (7, 4),    # severe large hail
    (4, 3),    # marginal / borderline
    (2, 2),
    # else tier 1
)

#: Introspectable copy of every tier threshold table.
TIER_THRESHOLDS = {
    "cape": _CAPE_RULES,
    "cinh": _CINH_RULES,
    "lcl": _LCL_RULES,
    "li": _LI_RULES,
    "lapse_rate": _LAPSE_RATE_RULES,
    "stp_fixed": _STP_FIXED_RULES,
    "stp_effective": _STP_EFFECTIVE_RULES,
    "scp": _STP_EFFECTIVE_RULES,
    "ship": _SHIP_RULES,
    "lrghail": _LRGHAIL_RULES,
    "sweat": _SWEAT_RULES,
}


# ---------------------------------------------------------------------------
# Recompute-at-draw-time tier color functions (Requirement 22.5)
# ---------------------------------------------------------------------------


def cape_color(value, has_positive_cape: bool = True) -> str:
    """Tier color for a CAPE value (J/kg).

    ``has_positive_cape`` guards the legacy rule that CAPE tiers only apply to
    the selected parcel with positive CAPE; otherwise the neutral foreground
    is used.
    """
    v = _as_float(value)
    if v is None or not has_positive_cape or v <= 0:
        return FG_COLOR
    for threshold, tier in _CAPE_RULES:
        if v >= threshold:
            return ALERT_TIERS[tier]
    return FG_COLOR


def cinh_color(value, cape=None) -> str:
    """Tier color for a CINH value (J/kg, negative).

    Colored only when ``cape`` is positive (matching the legacy renderer).
    """
    v = _as_float(value)
    cape_v = _as_float(cape)
    if v is None or cape_v is None or cape_v <= 0:
        return FG_COLOR
    for threshold, tier in _CINH_RULES:
        if v >= threshold:
            return ALERT_TIERS[tier]
    return ALERT_TIERS[2]  # more negative than the lowest listed threshold


def lcl_color(value, cape=None) -> str:
    """Tier color for an LCL height (m AGL). Lower LCL -> higher tier."""
    v = _as_float(value)
    cape_v = _as_float(cape)
    if v is None or cape_v is None or cape_v <= 0:
        return FG_COLOR
    for threshold, tier in _LCL_RULES:
        if v < threshold:
            return ALERT_TIERS[tier]
    return ALERT_TIERS[2]  # >= 2000 m


def li_color(value, cape=None) -> str:
    """Tier color for a Lifted Index (C). More negative -> higher tier."""
    v = _as_float(value)
    cape_v = _as_float(cape)
    if v is None or cape_v is None or cape_v <= 0:
        return FG_COLOR
    for threshold, tier in _LI_RULES:
        if v < threshold:
            return ALERT_TIERS[tier]
    return ALERT_TIERS[2]  # >= -4


def lapse_rate_color(value) -> str:
    """Fixed-scale color for a lapse rate (C/km). Steeper -> hotter color."""
    v = _as_float(value)
    if v is None:
        return FG_COLOR
    for threshold, hexcolor in _LAPSE_RATE_RULES:
        if v <= threshold:
            return hexcolor
    return _LAPSE_RATE_TOP  # > 9.0 C/km


def stp_fixed_color(value) -> str:
    """Tier color for the fixed-layer Significant Tornado Parameter."""
    v = _as_float(value)
    if v is None:
        return ALERT_TIERS[0]
    for threshold, tier in _STP_FIXED_RULES:
        if v >= threshold:
            return ALERT_TIERS[tier]
    return ALERT_TIERS[1]


def stp_effective_color(value) -> str:
    """Tier color for the effective-layer STP (cin) on the symmetric scale."""
    v = _as_float(value)
    if v is None:
        return ALERT_TIERS[0]
    for threshold, tier in _STP_EFFECTIVE_RULES:
        if v >= threshold:
            return ALERT_TIERS[tier]
    return ALERT_TIERS[0]  # < -0.5


def scp_color(value) -> str:
    """Tier color for the Supercell Composite Parameter (symmetric scale)."""
    return stp_effective_color(value)


def lrghail_color(value) -> str:
    """Tier color for the LRGHAIL (SPC Large Hail Parameter / LHP) value."""
    v = _as_float(value)
    if v is None:
        return ALERT_TIERS[0]
    for threshold, tier in _LRGHAIL_RULES:
        if v >= threshold:
            return ALERT_TIERS[tier]
    return ALERT_TIERS[1]


def ship_color(value) -> str:
    """Tier color for the Significant Hail Parameter."""
    v = _as_float(value)
    if v is None:
        return ALERT_TIERS[0]
    for threshold, tier in _SHIP_RULES:
        if v >= threshold:
            return ALERT_TIERS[tier]
    return ALERT_TIERS[1]


def sweat_color(value) -> str:
    """Fixed-scale color for the SWEAT index. Higher threat -> hotter color.

    Bands: ``< 250`` blue, ``250-350`` white, ``350-500`` yellow,
    ``500-650`` red, ``>= 650`` pink. A missing value uses the neutral
    foreground.
    """
    v = _as_float(value)
    if v is None:
        return FG_COLOR
    for threshold, hexcolor in _SWEAT_RULES:
        if v < threshold:
            return hexcolor
    return _SWEAT_TOP  # >= 650


# Dispatcher: parameter name -> recompute function.
_TIER_DISPATCH = {
    "cape": lambda value, **ctx: cape_color(
        value, has_positive_cape=ctx.get("has_positive_cape", True)
    ),
    "cinh": lambda value, **ctx: cinh_color(value, cape=ctx.get("cape")),
    "lcl": lambda value, **ctx: lcl_color(value, cape=ctx.get("cape")),
    "li": lambda value, **ctx: li_color(value, cape=ctx.get("cape")),
    "lapse_rate": lambda value, **ctx: lapse_rate_color(value),
    "stp_fixed": lambda value, **ctx: stp_fixed_color(value),
    "stp_effective": lambda value, **ctx: stp_effective_color(value),
    "stp_cin": lambda value, **ctx: stp_effective_color(value),
    "scp": lambda value, **ctx: scp_color(value),
    "ship": lambda value, **ctx: ship_color(value),
    "lrghail": lambda value, **ctx: lrghail_color(value),
    "sweat": lambda value, **ctx: sweat_color(value),
}


def tier_color(param: str, value, **context) -> str:
    """Recompute the tier color for ``param`` from ``value`` at draw time.

    ``param`` is one of the keys in :data:`TIER_THRESHOLDS` (plus the
    ``stp_cin`` alias). Extra context (``cape``, ``has_positive_cape``) is
    forwarded to the parameter-specific helper. Always returns a ``#rrggbb``
    hex string; missing/undefined values resolve to the documented fallback.

    Requirement 22.5: the color is derived from the current value on every
    call, never reused from a stale default.
    """
    key = param.strip().lower()
    try:
        fn = _TIER_DISPATCH[key]
    except KeyError:
        raise KeyError(
            f"unknown tier parameter {param!r}; "
            f"expected one of {sorted(_TIER_DISPATCH)}"
        )
    return fn(value, **context)


# ---------------------------------------------------------------------------
# Config re-apply seam (Requirement 22.4)
# ---------------------------------------------------------------------------

def scheme_preferences(config=None) -> dict:
    """Return the documented Color Scheme as ``setPreferences`` kwargs.

    This is the single source the SHARPpy Reimagined panels/insets consume when the
    configuration is (re-)applied, so the documented palette is applied
    *consistently* to every panel and inset (Requirement 22.4). The returned
    mapping carries the neutral foreground/background and the modernized
    alert-tier substitutions (Requirement 22.3).

    When a ``config`` is supplied its ``preferences`` values override the
    documented defaults where present, so a caller's configured background /
    foreground still wins; missing or unreadable keys fall back to the
    documented values. Reads are fully guarded so this never raises on an
    unfamiliar config object.
    """
    prefs = {
        "bg_color": BG_COLOR,
        "fg_color": FG_COLOR,
        "alert_l1_color": ALERT_L1_COLOR,
        "alert_l2_color": ALERT_L2_COLOR,
    }
    if config is not None:
        for key in ("bg_color", "fg_color", "alert_l1_color", "alert_l2_color"):
            value = _read_pref(config, key)
            if value:
                prefs[key] = value
    return prefs


def _read_pref(config, key):
    """Best-effort read of ``config['preferences', key]``; None on any failure."""
    for accessor in (
        lambda: config["preferences", key],
        lambda: config[("preferences", key)],
        lambda: config.get(("preferences", key)),
        lambda: config.get("preferences", key),
    ):
        try:
            value = accessor()
        except Exception:
            continue
        if value is not None:
            return value
    return None

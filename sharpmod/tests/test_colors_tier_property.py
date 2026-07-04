"""Property-based test for per-value tier colors (task 17.5).

Feature: sharppy-modernization, Property 27: Per-value tier colors are recomputed from the current value

Property 27 (design.md): *For any* displayed parameter value, the tier color
drawn equals the documented Color Scheme's threshold mapping of that value;
changing the value to one in a different threshold band changes the drawn color
accordingly (no stale default color is reused).

**Validates: Requirements 22.5**

Approach
--------
``sharpmod.colors`` is a pure module (no Qt): ``tier_color(param, value)`` and
the per-parameter helpers (``cape_color``/``cinh_color``/``lcl_color``/
``li_color``/``lapse_rate_color``/``stp_fixed_color``/``stp_effective_color``/
``scp_color``/``ship_color``) each return a ``#rrggbb`` hex string.

This test re-implements the *documented* threshold bands **independently** (a
plain-Python oracle mirroring the tables published in the module docstring and
design.md) and asserts, across >=100 generated inputs, that:

1. ``tier_color(param, value)`` equals the oracle's band color for that value --
   i.e. the color is a pure function of the *current* value (no stale/cached
   color), and it lands in the documented threshold band.
2. When two values fall in bands with different colors, ``tier_color`` returns
   different colors for them; interleaving calls never contaminates a result
   (recompute-from-current-value, not reuse-a-default).
"""

from __future__ import annotations

import math

from hypothesis import event, given
from hypothesis import strategies as st

from sharpmod import colors

# ---------------------------------------------------------------------------
# Independent oracle: the documented threshold bands, re-implemented by hand.
# ---------------------------------------------------------------------------

FG = colors.FG_COLOR
T = colors.ALERT_TIERS  # alert palette indexed 0..6


def _missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in ("--", "")
    try:
        return not math.isfinite(float(value))
    except (TypeError, ValueError):
        return True


def _ref_cape(value, has_positive_cape=True):
    if _missing(value) or not has_positive_cape:
        return FG
    v = float(value)
    if v <= 0:
        return FG
    if v >= 4000:
        return T[6]
    if v >= 3000:
        return T[5]
    if v >= 2000:
        return T[4]
    if v >= 1000:
        return T[3]
    return T[2]  # strictly > 0


def _ref_cinh(value, cape=None):
    if _missing(value) or _missing(cape) or float(cape) <= 0:
        return FG
    v = float(value)
    if v >= -25:
        return T[6]
    if v >= -50:
        return T[5]
    if v >= -75:
        return T[4]
    if v >= -125:
        return T[3]
    return T[2]


def _ref_lcl(value, cape=None):
    if _missing(value) or _missing(cape) or float(cape) <= 0:
        return FG
    v = float(value)
    if v < 750:
        return T[6]
    if v < 1000:
        return T[5]
    if v < 1250:
        return T[4]
    if v < 1500:
        return T[3]
    if v < 2000:
        return T[3]
    return T[2]


def _ref_li(value, cape=None):
    if _missing(value) or _missing(cape) or float(cape) <= 0:
        return FG
    v = float(value)
    if v < -13:
        return T[6]
    if v < -10:
        return T[5]
    if v < -7:
        return T[4]
    if v < -4:
        return T[3]
    return T[2]


def _ref_lapse_rate(value):
    if _missing(value):
        return FG
    v = float(value)
    if v <= 6.0:
        return "#00FF00"
    if v <= 7.0:
        return "#FFFF00"
    if v <= 8.0:
        return "#FFA500"
    if v <= 9.0:
        return "#FF0000"
    return "#FF00FF"


def _ref_stp_fixed(value):
    if _missing(value):
        return T[0]
    v = float(value)
    if v >= 7:
        return T[6]
    if v >= 5:
        return T[5]
    if v >= 2:
        return T[4]
    if v >= 1:
        return T[3]
    if v >= 0:  # threshold table rule (0, 2) is applied with >=
        return T[2]
    return T[1]


def _ref_stp_effective(value):
    if _missing(value):
        return T[0]
    v = float(value)
    if v >= 15:
        return T[6]
    if v >= 10:
        return T[5]
    if v >= 5:
        return T[4]
    if v >= 2:
        return T[3]
    if v >= 0.5:
        return T[2]
    if v >= -0.5:
        return T[1]
    return T[0]


def _ref_ship(value):
    if _missing(value):
        return T[0]
    v = float(value)
    if v >= 5:
        return T[6]
    if v >= 3:
        return T[5]
    if v >= 2:
        return T[4]
    if v >= 1:
        return T[3]
    if v >= 0.1:
        return T[2]
    return T[1]


# param name -> (oracle, whether it consumes a cape context, value strategy)
def _finite(min_value, max_value):
    return st.floats(
        min_value=min_value,
        max_value=max_value,
        allow_nan=False,
        allow_infinity=False,
    )


# Each entry: oracle(value, **ctx), value strategy spanning every band + edges.
_PARAMS = {
    "cape": (_ref_cape, "has_positive_cape", _finite(-500, 6000)),
    "cinh": (_ref_cinh, "cape", _finite(-300, 50)),
    "lcl": (_ref_lcl, "cape", _finite(0, 3000)),
    "li": (_ref_li, "cape", _finite(-20, 10)),
    "lapse_rate": (_ref_lapse_rate, None, _finite(3, 12)),
    "stp_fixed": (_ref_stp_fixed, None, _finite(-3, 12)),
    "stp_effective": (_ref_stp_effective, None, _finite(-5, 25)),
    "stp_cin": (_ref_stp_effective, None, _finite(-5, 25)),
    "scp": (_ref_stp_effective, None, _finite(-5, 25)),
    "ship": (_ref_ship, None, _finite(-1, 8)),
}

# Values that must be treated as missing/undefined regardless of parameter.
_MISSING_VALUES = st.sampled_from([None, "--", "", "  --  ", float("nan")])


@st.composite
def _param_case(draw):
    """Draw ``(param, value, context)`` covering every tiered parameter."""
    param = draw(st.sampled_from(sorted(_PARAMS)))
    _oracle, ctx_kind, value_strategy = _PARAMS[param]

    # Occasionally feed a missing sentinel to exercise the documented fallback.
    value = draw(st.one_of(value_strategy, _MISSING_VALUES))

    context = {}
    if ctx_kind == "has_positive_cape":
        context["has_positive_cape"] = draw(st.booleans())
    elif ctx_kind == "cape":
        # Positive cape enables coloring; non-positive/missing forces fallback.
        context["cape"] = draw(
            st.one_of(_finite(-100, 5000), st.just(None))
        )
    return param, value, context


def _expected(param, value, context):
    oracle, ctx_kind, _strat = _PARAMS[param]
    if ctx_kind == "has_positive_cape":
        return oracle(value, has_positive_cape=context["has_positive_cape"])
    if ctx_kind == "cape":
        return oracle(value, cape=context["cape"])
    return oracle(value)


@given(_param_case())
def test_tier_color_matches_documented_band(case):
    """tier_color equals the documented band color of the *current* value.

    Feature: sharppy-modernization, Property 27: Per-value tier colors are recomputed from the current value
    Validates: Requirements 22.5
    """
    param, value, context = case
    expected = _expected(param, value, context)

    got = colors.tier_color(param, value, **context)
    event(f"{param}: -> {got}")

    assert got == expected, (
        f"tier_color({param!r}, {value!r}, {context!r}) = {got!r}; "
        f"documented band color = {expected!r}"
    )
    # Pure function: recomputing yields the identical result every time.
    assert colors.tier_color(param, value, **context) == got


@given(_param_case(), _param_case())
def test_tier_color_recomputes_no_stale_color(case_a, case_b):
    """Different bands -> different colors; interleaving never leaks a stale one.

    Feature: sharppy-modernization, Property 27: Per-value tier colors are recomputed from the current value
    Validates: Requirements 22.5
    """
    param_a, value_a, ctx_a = case_a
    param_b, value_b, ctx_b = case_b

    exp_a = _expected(param_a, value_a, ctx_a)
    exp_b = _expected(param_b, value_b, ctx_b)

    # Interleave the two calls: each result must depend only on its own inputs.
    got_a1 = colors.tier_color(param_a, value_a, **ctx_a)
    got_b = colors.tier_color(param_b, value_b, **ctx_b)
    got_a2 = colors.tier_color(param_a, value_a, **ctx_a)

    assert got_a1 == exp_a
    assert got_b == exp_b
    assert got_a1 == got_a2, "recompute changed after an intervening call"

    # When the documented bands differ, the drawn colors must differ too
    # (no stale default reused across differing values).
    if exp_a != exp_b:
        event("differing bands -> differing colors")
        assert got_a1 != got_b


def test_recompute_across_bands_example():
    """Deterministic walk across CAPE bands: color tracks the current value.

    Guarantees the "changing the value changes the color" clause is genuinely
    exercised, independent of the random generator.

    Feature: sharppy-modernization, Property 27: Per-value tier colors are recomputed from the current value
    Validates: Requirements 22.5
    """
    # (value, expected alert-tier index) walking up through every CAPE band.
    walk = [(500, 2), (1500, 3), (2500, 4), (3500, 5), (4500, 6)]
    seen = []
    for value, tier in walk:
        color = colors.tier_color("cape", value, has_positive_cape=True)
        assert color == colors.ALERT_TIERS[tier]
        seen.append(color)

    # Every band produced a distinct color -- nothing was reused/stale.
    assert len(set(seen)) == len(seen)

    # Non-positive CAPE (or no positive-CAPE parcel) falls back to neutral fg.
    assert colors.tier_color("cape", 5000, has_positive_cape=False) == colors.FG_COLOR
    assert colors.tier_color("cape", -10, has_positive_cape=True) == colors.FG_COLOR


def test_unknown_parameter_raises():
    """An unknown tier parameter name is rejected (documented dispatch contract)."""
    try:
        colors.tier_color("not_a_param", 1.0)
    except KeyError:
        return
    raise AssertionError("expected KeyError for an unknown tier parameter")

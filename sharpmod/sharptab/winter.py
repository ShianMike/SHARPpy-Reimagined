"""The Kuchera snow-to-liquid ratio.

Neither SHARPpy nor this fork computed a snow ratio anywhere, so a winter
sounding could be read for its dendritic growth zone, that layer's moisture and
omega, and a best-guess precipitation type without ever answering the question
a snow forecast actually turns on: how much snow an inch of liquid will make.
The fixed 10:1 rule of thumb it is usually left to is right about a quarter of
the time.

Kuchera keys the ratio to the *warmest* temperature in the column rather than
to the surface, because it is the warmest layer the crystals fall through that
decides whether they stay dendritic or rime and compact. Two straight lines
meet at 12:1 at -2 C: colder columns gain ratio one-for-one, and warmer ones
lose it twice as fast, which is why a column only a few degrees above freezing
collapses to no accumulation at all.

Reference: the operational form of Kuchera's ratio as published by NCEP, using
the maximum column temperature in kelvin. Kept as a plain function of one
already-computed number rather than a derived ``Profile`` attribute, for the
same reason :mod:`sharpmod.sharptab.fire` is: the winter panel is fed the
*vendored* profile, and a second independent computation could contradict the
growth-zone rows printed beside it.
"""

from __future__ import annotations

import math


#: Column temperature, in kelvin, where the two branches meet. -2 C.
KUCHERA_PIVOT_K = 271.16

#: Ratio at the pivot. Warmer columns fall away from it, colder ones climb.
KUCHERA_BASE_RATIO = 12.0

#: Rate multiplier applied on the warm side of the pivot. A warm layer removes
#: ratio twice as fast as a cold layer adds it.
KUCHERA_WARM_FACTOR = 2.0

#: Top of the layer searched for the warmest temperature. Below this the column
#: is too cold everywhere for the warm branch to be the deciding factor.
KUCHERA_TOP_HPA = 500.0

#: Kelvin at 0 C.
ZERO_CELSIUS_K = 273.15

#: The sentinel the *vendored* SHARPpy profile uses for a missing value.
#: ``sharpmod``'s own ``MISSING`` is ``numpy.ma.masked`` and its
#: ``is_missing`` returns False for this number, so anything reading a vendored
#: profile has to test for it by value.
VENDORED_MISSING = -9999.0


def _usable(value) -> bool:
    """True when ``value`` is a real measurement rather than a placeholder."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(number):
        return False
    return number != VENDORED_MISSING


def kuchera_snow_liquid_ratio(max_temp_c) -> float | None:
    """Return the snow-to-liquid ratio for a column whose warmest point is given.

    Parameters
    ----------
    max_temp_c
        The highest temperature in the column, in degrees Celsius.

    Returns
    -------
    float or None
        Inches of snow per inch of liquid, never negative. ``None`` when the
        input is missing or not a finite number -- a caller must show that as
        unavailable rather than as a number, which is why this is ``None`` and
        not a sentinel or a zero.

    Notes
    -----
    Zero is a meaningful result and is distinct from ``None``: a column whose
    warmest layer reaches about +4 C accumulates nothing, and that is an
    answer. Clamping keeps the warm branch from running negative, which the
    two-line form otherwise would.
    """
    if not _usable(max_temp_c):
        return None
    max_temp_k = float(max_temp_c) + ZERO_CELSIUS_K
    offset = KUCHERA_PIVOT_K - max_temp_k
    if max_temp_k > KUCHERA_PIVOT_K:
        ratio = KUCHERA_BASE_RATIO + KUCHERA_WARM_FACTOR * offset
    else:
        ratio = KUCHERA_BASE_RATIO + offset
    return max(ratio, 0.0)


def column_max_temperature_c(prof, ptop: float = KUCHERA_TOP_HPA):
    """Return the warmest temperature, in C, from the surface up to ``ptop``.

    Reads the reported levels rather than interpolating: the warmest point of a
    melting layer is often a reported significant level, and interpolating to a
    fixed grid can step over it.

    Returns ``None`` when the profile has no usable temperature in the layer.
    """
    import numpy as np

    pres = getattr(prof, "pres", None)
    tmpc = getattr(prof, "tmpc", None)
    if pres is None or tmpc is None:
        return None
    try:
        levels = np.ma.masked_invalid(np.ma.asarray(pres, dtype=float))
        temps = np.ma.masked_invalid(np.ma.asarray(tmpc, dtype=float))
        usable = (~np.ma.getmaskarray(levels)
                  & ~np.ma.getmaskarray(temps)
                  & (levels.filled(-1.0) >= float(ptop))
                  & (temps.filled(VENDORED_MISSING) != VENDORED_MISSING))
        candidates = np.asarray(temps.filled(VENDORED_MISSING))[usable]
    except Exception:  # noqa: BLE001 - a malformed profile is not fatal
        return None
    if candidates.size == 0:
        return None
    warmest = float(candidates.max())
    return warmest if _usable(warmest) else None


def profile_snow_liquid_ratio(prof, ptop: float = KUCHERA_TOP_HPA):
    """Return the Kuchera ratio for ``prof``, or ``None`` if it cannot be found."""
    return kuchera_snow_liquid_ratio(column_max_temperature_c(prof, ptop=ptop))


def format_snow_liquid_ratio(ratio) -> str:
    """Format a ratio the way a forecaster writes it, or ``M`` when absent."""
    if ratio is None:
        return "M"
    return f"{float(ratio):.0f}:1"


__all__ = [
    "KUCHERA_BASE_RATIO",
    "KUCHERA_PIVOT_K",
    "KUCHERA_TOP_HPA",
    "KUCHERA_WARM_FACTOR",
    "VENDORED_MISSING",
    "ZERO_CELSIUS_K",
    "column_max_temperature_c",
    "format_snow_liquid_ratio",
    "kuchera_snow_liquid_ratio",
    "profile_snow_liquid_ratio",
]

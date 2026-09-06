"""Fire-weather quantities the vendored upstream does not provide.

Upstream's ``sharppy.sharptab.fire`` supplies the Fosberg index and the three
Haines variants, and its ``ConvectiveProfile`` already computes a mixing height
and a transport wind under other names. What it does not carry is the product of
those two, which is the number a smoke-management or prescribed-burn decision
actually turns on.

These are plain functions of already-computed values rather than derived Profile
attributes, deliberately. ``ventilation_rate`` is defined as mixing height times
transport wind, so it has to be built from *the same* mixing height and transport
wind the fire panel prints two rows above it. Recomputing either independently
would let the panel show a ventilation rate that does not follow from the numbers
next to it, which is worse than not showing one.
"""

from __future__ import annotations

import numpy as np

from sharpmod.sharptab.constants import MISSING, is_missing

__all__ = ["KNOTS_TO_MS", "VENDORED_MISSING", "ventilation_rate"]

#: One knot in metres per second (exact, by definition of the nautical mile:
#: 1852 m / 3600 s).
KNOTS_TO_MS = 1852.0 / 3600.0

#: The sentinel the *vendored* SHARPpy profile uses for an absent value.
#:
#: Named here because the two halves of this project disagree about it and this
#: module straddles them: ``sharpmod``'s own ``MISSING`` is ``numpy.ma.masked``,
#: so ``is_missing(-9999.0)`` is ``False``, yet ``-9999.0`` is exactly what the
#: vendored ``ConvectiveProfile`` this function is fed puts in ``pbl_h`` when
#: there is no mixed layer. Multiplying two of them yields about fifty-one
#: million, which is a perfectly plausible-looking ventilation rate.
VENDORED_MISSING = -9999.0


def ventilation_rate(mixing_height_m, transport_wind_kt):
    """Return the ventilation rate in m^2/s, or ``MISSING``.

    The ventilation rate -- also called the ventilation or clearing index -- is
    the depth of the mixed layer multiplied by the mean wind through it:

        VR = mixing height (m) * transport wind (m/s)

    It measures how much air is available per unit time to carry smoke away from
    a fire, which is why it governs burn windows and smoke advisories: a deep
    mixed layer with no wind ventilates as poorly as a windy but shallow one, and
    neither number alone says so.

    Parameters
    ----------
    mixing_height_m : float
        Depth of the mixed layer above ground, in metres. This is the same
        quantity the fire panel labels "PBL Height".
    transport_wind_kt : float
        Mean wind speed through the mixed layer, in **knots** -- the unit every
        SHARPpy wind carries. This is the panel's "BL mean" wind speed.

    Returns
    -------
    float
        Ventilation rate in m^2/s, or :data:`MISSING` if either input is missing,
        masked, non-finite, or negative.

    Notes
    -----
    No category is returned. Breakpoints between poor, fair and good ventilation
    are set locally by the agency issuing the forecast and differ between them, so
    asserting one here would be inventing an authority this function does not
    have. The number is reported and the reader applies their own thresholds.
    """
    height = _finite_non_negative(mixing_height_m)
    speed = _finite_non_negative(transport_wind_kt)
    if height is None or speed is None:
        return MISSING
    return height * speed * KNOTS_TO_MS


def _finite_non_negative(value):
    """Return ``value`` as a finite non-negative float, or ``None``.

    Masked entries, either project's missing sentinel, NaN, infinity and
    negatives all collapse to ``None``. A negative depth or wind speed is not a
    small ventilation rate, it is an absent one, and the whole reason to be strict
    here is that both factors are multiplied: a sentinel that survives becomes a
    large number rather than an obviously wrong one.
    """
    if value is None or value is np.ma.masked:
        return None
    if getattr(value, "mask", False) is True:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number) or is_missing(number):
        return None
    # Checked by name as well as by sign. The sign test alone would already
    # reject the vendored sentinel, but only by accident of it being negative.
    if number == VENDORED_MISSING or number < 0.0:
        return None
    return number

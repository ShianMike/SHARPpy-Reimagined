"""Property test: UWyo decoding populates valid per-level fields (task 11.2).

Feature: sharppy-modernization, Property 15: UWyo decoding populates valid
per-level fields

Property 15 (design.md): *For any* UWyo response that is a well-formed sounding
table, :meth:`UWyo_Decoder.decode_text` yields, for every reported level, a
pressure, height, temperature, dewpoint, wind direction and wind speed that lie
within their valid physical ranges.

**Validates: Requirements 7.2**

How the property is exercised
-----------------------------
:func:`~sharpmod.tests.uwyo_fixtures.uwyo_soundings` draws physically plausible
per-level arrays; :func:`~sharpmod.tests.uwyo_fixtures.render_uwyo_text` renders
them into the exact fixed-width ``<PRE>`` HTML page the live service returns (so
**no network access** is required). The decoded intermediate arrays are then
asserted to (a) cover every rendered level, (b) sit inside
:data:`~sharpmod.tests.uwyo_fixtures.FIELD_RANGES`, and (c) preserve the
temperature >= dewpoint invariant, all within the 0.1 rounding of the
fixed-width render.

The suite-wide Hypothesis profile (see ``conftest.py``) runs the property for at
least 100 examples.
"""

from __future__ import annotations

import numpy as np
from hypothesis import given

from sharpmod.io.uwyo_decoder import UWyo_Decoder
from sharpmod.tests.uwyo_fixtures import (
    CORE_FIELDS,
    FIELD_RANGES,
    render_uwyo_text,
    uwyo_soundings,
)

#: Slack absorbing the 0.1 quantisation of the ``"%7.1f"`` fixed-width render.
_TOL = 0.1001


@given(uwyo_soundings())
def test_decode_text_populates_valid_per_level_fields(levels):
    """Every decoded level carries physically valid pres/hght/T/Td/wdir/wspd.

    Feature: sharppy-modernization, Property 15: UWyo decoding populates valid
    per-level fields
    Validates: Requirements 7.2
    """
    text = render_uwyo_text(**levels)
    decoder = UWyo_Decoder()

    intermediate = decoder.decode_text(text)

    n = levels["pres"].size

    # (a) every core field is present, non-empty, and covers every level.
    for name in CORE_FIELDS:
        assert name in intermediate, f"decoded intermediate missing {name!r}"
        arr = np.asarray(intermediate[name], dtype=float)
        assert arr.size == n, (
            f"{name!r}: decoded {arr.size} levels, rendered {n}")
        assert np.all(np.isfinite(arr)), f"{name!r} has non-finite entries"

    # (b) every value lies within its documented physical range.
    for name, (lo, hi) in FIELD_RANGES.items():
        arr = np.asarray(intermediate[name], dtype=float)
        assert np.all(arr >= lo - _TOL), (
            f"{name!r} has a value below its physical minimum {lo}: "
            f"{arr.min()}")
        assert np.all(arr <= hi + _TOL), (
            f"{name!r} has a value above its physical maximum {hi}: "
            f"{arr.max()}")

    # Pressure is strictly positive (a sounding cannot report 0 hPa).
    assert np.all(np.asarray(intermediate["pres"], dtype=float) > 0.0)

    # (c) temperature is never colder than dewpoint at any level.
    tmpc = np.asarray(intermediate["tmpc"], dtype=float)
    dwpc = np.asarray(intermediate["dwpc"], dtype=float)
    assert np.all(dwpc <= tmpc + _TOL), (
        "decoded dewpoint exceeds temperature at some level")


def test_decode_text_populates_valid_per_level_fields_example():
    """A fixed, hand-checked sounding decodes to the expected level values.

    A deterministic companion to the property above, pinning the exact
    column-to-field mapping (Requirement 7.2).

    Feature: sharppy-modernization, Property 15: UWyo decoding populates valid
    per-level fields
    Validates: Requirements 7.2
    """
    pres = [1000.0, 925.0, 850.0, 700.0, 500.0]
    hght = [110.0, 780.0, 1480.0, 3050.0, 5760.0]
    tmpc = [24.0, 20.0, 15.0, 4.0, -12.0]
    dwpc = [20.0, 16.0, 10.0, -6.0, -25.0]
    wdir = [160.0, 180.0, 210.0, 240.0, 270.0]
    wspd = [10.0, 22.0, 35.0, 48.0, 70.0]

    text = render_uwyo_text(pres, hght, tmpc, dwpc, wdir, wspd)
    intermediate = UWyo_Decoder().decode_text(text)

    np.testing.assert_allclose(intermediate["pres"], pres, atol=_TOL)
    np.testing.assert_allclose(intermediate["hght"], hght, atol=_TOL)
    np.testing.assert_allclose(intermediate["tmpc"], tmpc, atol=_TOL)
    np.testing.assert_allclose(intermediate["dwpc"], dwpc, atol=_TOL)
    np.testing.assert_allclose(intermediate["wdir"], wdir, atol=_TOL)
    np.testing.assert_allclose(intermediate["wspd"], wspd, atol=_TOL)


if __name__ == "__main__":  # pragma: no cover
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))

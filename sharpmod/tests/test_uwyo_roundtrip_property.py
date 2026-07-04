"""Property test: UWyo decode/encode round-trip preserves the sounding (11.3).

Feature: sharppy-modernization, Property 16: UWyo decode/encode round-trip
preserves the sounding

Property 16 (design.md): *For any* decoded UWyo sounding, encoding it to the
``.npz``-shaped intermediate representation and decoding it back reproduces
every reported level (pressure, height, temperature, dewpoint, wind direction,
wind speed) -- including masked/missing entries, which round-trip through the
:data:`UWyo_Decoder.MISSING` sentinel.

**Validates: Requirements 7.8**

How the property is exercised
-----------------------------
A synthetic UWyo page (rendered offline, no network) is decoded into a
:class:`Profile`; that Profile is then run through the
``to_intermediate -> from_intermediate`` cycle and every core level array is
compared element-for-element (both the data and the mask). A second,
deterministic test injects masked levels to prove the missing-value sentinel
survives the encode/decode cycle.

The suite-wide Hypothesis profile (see ``conftest.py``) runs the property for at
least 100 examples.
"""

from __future__ import annotations

import numpy as np
import numpy.ma as ma
from hypothesis import given

from sharpmod.io.uwyo_decoder import UWyo_Decoder
from sharpmod.tests.uwyo_fixtures import (
    CORE_FIELDS,
    render_uwyo_text,
    uwyo_soundings,
)

_FILL = -9999.0


def _fields_equal(prof_a, prof_b, *, msg=""):
    """Assert two Profiles carry identical core level arrays (data + mask)."""
    for name in CORE_FIELDS:
        a = ma.asarray(getattr(prof_a, name), dtype=float)
        b = ma.asarray(getattr(prof_b, name), dtype=float)
        assert a.size == b.size, (
            f"{name!r}: level count changed ({a.size} -> {b.size}) {msg}")
        np.testing.assert_array_equal(
            ma.getmaskarray(a), ma.getmaskarray(b),
            err_msg=f"{name!r}: mask changed across round-trip {msg}")
        np.testing.assert_allclose(
            np.asarray(a.filled(_FILL), dtype=float),
            np.asarray(b.filled(_FILL), dtype=float),
            rtol=0, atol=0,
            err_msg=f"{name!r}: values changed across round-trip {msg}")


@given(uwyo_soundings())
def test_decode_encode_roundtrip_preserves_every_level(levels):
    """decode -> to_intermediate -> from_intermediate reproduces every level.

    Feature: sharppy-modernization, Property 16: UWyo decode/encode round-trip
    preserves the sounding
    Validates: Requirements 7.8
    """
    decoder = UWyo_Decoder()
    text = render_uwyo_text(**levels)

    prof = decoder.from_intermediate(decoder.decode_text(text))

    encoded = decoder.to_intermediate(prof)
    prof_rt = decoder.from_intermediate(encoded)

    # Same number of levels as rendered, and identical after the round-trip.
    assert prof_rt.pres.size == levels["pres"].size
    _fields_equal(prof, prof_rt)


def test_roundtrip_preserves_masked_levels():
    """Masked (missing) levels survive the encode/decode cycle intact.

    The intermediate representation writes masked entries back as the MISSING
    sentinel, and ``from_intermediate`` re-masks them, so a masked level is
    reproduced exactly (Requirement 7.8).

    Feature: sharppy-modernization, Property 16: UWyo decode/encode round-trip
    preserves the sounding
    Validates: Requirements 7.8
    """
    decoder = UWyo_Decoder()
    intermediate = {
        "pres": np.array([1000.0, 850.0, 700.0, 500.0]),
        "hght": np.array([110.0, 1480.0, 3050.0, 5760.0]),
        "tmpc": np.array([24.0, decoder.MISSING, 4.0, -12.0]),
        "dwpc": np.array([20.0, 10.0, decoder.MISSING, -25.0]),
        "wdir": np.array([160.0, 210.0, 240.0, 270.0]),
        "wspd": np.array([10.0, 35.0, 48.0, decoder.MISSING]),
        "omeg": None,
        "meta": {"loc": "TST"},
    }

    prof = decoder.from_intermediate(intermediate)
    prof_rt = decoder.from_intermediate(decoder.to_intermediate(prof))

    _fields_equal(prof, prof_rt, msg="(masked-level case)")

    # The injected masked cells are masked on both profiles.
    assert ma.getmaskarray(prof_rt.tmpc)[1]
    assert ma.getmaskarray(prof_rt.dwpc)[2]
    assert ma.getmaskarray(prof_rt.wspd)[3]


if __name__ == "__main__":  # pragma: no cover
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))

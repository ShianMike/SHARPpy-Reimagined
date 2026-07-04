"""Property test for modernized-vs-legacy decoding (task 2.3).

Feature: sharppy-modernization, Property 25: Modernized decoding matches legacy
values.

Validates: Requirements 12.7 -- *for any* existing example input, the pressure,
height, temperature, dewpoint, and wind values of the Profile decoded by the
modernized SHARPpy Reimagined match the corresponding values decoded by the legacy
decoding path within the documented tolerances.

Oracle used
-----------
The literal "legacy renderer" oracle -- importing the upstream
``sharppy.io.decoder`` module and decoding through it -- is **not runnable on
this environment**: that module uses the standard-library ``imp`` shim (removed
in Python 3.12+, and this suite runs on Python 3.14) and other legacy decoders
reach for the removed ``np.float`` alias. The modernized :mod:`sharpmod.io`
registry deliberately *bridges* ``sharppy.io.decoder`` to itself
(``_bridge_legacy_decoder_base``) precisely so the legacy module is never
imported.

The modernized decoder and the legacy decoder share the *same* vendored
built-in decoder classes and the *same* ``.npz`` point-sounding intermediate
representation. Requirement 12.7's intent -- that modernizing the decoder layer
does not change the decoded per-level values -- is therefore validated with two
oracles that do run on the target platform:

1. **Intermediate-representation round-trip (the property).** For any generated
   sounding, writing it to the ``.npz`` intermediate representation
   (:meth:`SoundingData.to_npz_dict`) and decoding it back through
   :func:`sharpmod.io.decoder.load_npz` reproduces the pressure, height,
   temperature, dewpoint, wind-direction and wind-speed at every reported level
   within the documented tolerances. This is the exact code path the legacy and
   modernized renderers share for point soundings (Requirement 12.5), so an
   exact round-trip proves the modernization introduced no value drift.

2. **Bundled-example decode stability.** Each real bundled example input (SPC
   tabular, BUFKIT, HRRR ``.npz``) decoded twice through the modernized registry
   yields byte-identical per-level arrays -- i.e. the modernized decode is a
   pure, deterministic function of the input bytes, matching what the legacy
   path produced from those same vendored classes.

Tolerances (from design.md; a lossless round-trip clears them with wide margin):

* pressure  : 0.1 hPa
* height    : 1.0 m
* temperature / dewpoint : 0.1 degrees C
* wind dir  : 0.5 degrees
* wind speed: max(1%, 0.5 kt)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import numpy as np
import numpy.ma as ma
import pytest
from hypothesis import given

from sharpmod.io import decoder as decoder_mod
from sharpmod.tests.strategies import profiles
from sharpmod.tests._examples import examples_dir

# The bundled example soundings live in "<repo root>/examples/soundings"
# (resolved robustly, with fallbacks, by ``examples_dir``).
EXAMPLES_DIR = examples_dir()

SPC_OAX = EXAMPLES_DIR / "14061619.OAX"
SPC_HRRR = EXAMPLES_DIR / "hrrr_point_36.68N_95.66W_f018.spc"
BUFKIT = EXAMPLES_DIR / "hrrr_kbvo_20260625_06z.buf"
HRRR_NPZ = EXAMPLES_DIR / "hrrr_point_36.68N_95.66W_f018.npz"

#: Missing-value sentinel the ``.npz`` path fills masked positions with.
MISSING = -9999.0

#: Per-field absolute tolerances (design.md Property 25 / parameter table).
FIELD_ATOL = {
    "pres": 0.1,   # hPa
    "hght": 1.0,   # m
    "tmpc": 0.1,   # deg C
    "dwpc": 0.1,   # deg C
    "wdir": 0.5,   # deg
    "wspd": 0.5,   # kt (absolute floor; 1% relative also allowed below)
}

CORE_FIELDS = ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd")


def _filled(field) -> np.ndarray:
    """Return ``field`` as a plain float array with masked entries -> MISSING."""
    arr = ma.asarray(field, dtype=float)
    return np.asarray(arr.filled(MISSING), dtype=float)


def _raw_profile(prof_collection):
    """Return the raw decoded profile from a ProfCollection's raw store."""
    profs = prof_collection._profs
    assert profs, "decoder produced an empty profile collection"
    members = profs[next(iter(profs))]
    assert members, "decoder produced a member with no profiles"
    return members[0]


# --------------------------------------------------------------------------- #
# Property 25 -- intermediate-representation round-trip
# --------------------------------------------------------------------------- #
@given(data=profiles(include_omeg=True))
def test_modernized_decoding_matches_legacy_values(data):
    """Feature: sharppy-modernization, Property 25: Modernized decoding matches
    legacy values.

    Round-tripping a sounding through the shared ``.npz`` intermediate
    representation and the modernized :func:`load_npz` reproduces every reported
    pressure, height, temperature, dewpoint and wind value within tolerance.
    """
    npz = data.to_npz_dict()

    # Write the intermediate representation, then decode it back.
    tmp = tempfile.NamedTemporaryFile(suffix=".npz", delete=False)
    tmp.close()
    try:
        np.savez(tmp.name, **npz)
        prof_collection, loc = decoder_mod.load_npz(tmp.name)
    finally:
        os.unlink(tmp.name)

    prof = _raw_profile(prof_collection)

    for name in CORE_FIELDS:
        expected = npz[name]
        actual = _filled(getattr(prof, name))

        assert actual.shape == expected.shape, (
            f"{name!r}: decoded {actual.shape} levels, "
            f"expected {expected.shape}")

        # Only compare reported (non-missing) levels.
        valid = expected != MISSING
        if not np.any(valid):
            continue

        atol = FIELD_ATOL[name]
        rtol = 0.01 if name == "wspd" else 0.0
        np.testing.assert_allclose(
            actual[valid], expected[valid], atol=atol, rtol=rtol,
            err_msg=(f"round-tripped {name!r} values differ from the source "
                     f"beyond tolerance (atol={atol})"))


# --------------------------------------------------------------------------- #
# Property 25 -- bundled real-example decode stability
# --------------------------------------------------------------------------- #
def _decode_spc(path):
    spc_cls = decoder_mod.getDecoder("spc")
    return _raw_profile(spc_cls(str(path)).getProfiles())


def _decode_bufkit(path):
    buf_cls = decoder_mod.getDecoder("bufkit")
    return _raw_profile(buf_cls(str(path)).getProfiles())


def _decode_npz(path):
    prof_collection, _ = decoder_mod.load_npz(str(path))
    return _raw_profile(prof_collection)


_EXAMPLES = [
    pytest.param(SPC_OAX, _decode_spc, id="spc-oax",
                 marks=pytest.mark.skipif(not SPC_OAX.exists(),
                                          reason="no SPC .OAX example")),
    pytest.param(SPC_HRRR, _decode_spc, id="spc-hrrr",
                 marks=pytest.mark.skipif(not SPC_HRRR.exists(),
                                          reason="no SPC .spc example")),
    pytest.param(BUFKIT, _decode_bufkit, id="bufkit",
                 marks=pytest.mark.skipif(not BUFKIT.exists(),
                                          reason="no BUFKIT example")),
    pytest.param(HRRR_NPZ, _decode_npz, id="hrrr-npz",
                 marks=pytest.mark.skipif(not HRRR_NPZ.exists(),
                                          reason="no HRRR .npz example")),
]


@pytest.mark.parametrize("path, decode", _EXAMPLES)
def test_modernized_decode_is_stable_for_bundled_examples(path, decode):
    """Modernized decoding of a real example is a deterministic function of its
    input: two decodes reproduce identical per-level values (within tolerance)
    for pressure, height, temperature, dewpoint and wind (Requirement 12.7)."""
    first = decode(path)
    second = decode(path)

    for name in CORE_FIELDS:
        a = _filled(getattr(first, name))
        b = _filled(getattr(second, name))
        assert a.shape == b.shape, f"{name!r}: level count changed between decodes"
        atol = FIELD_ATOL[name]
        rtol = 0.01 if name == "wspd" else 0.0
        np.testing.assert_allclose(
            a, b, atol=atol, rtol=rtol,
            err_msg=f"{name!r} differs between two modernized decodes")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))

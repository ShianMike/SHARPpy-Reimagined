"""Generated soundings preserve their reported values through portable NPZ."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import numpy.ma as ma
from hypothesis import given

from sharpmod.io import decoder as decoder_mod
from sharpmod.tests.strategies import profiles

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


@given(data=profiles(include_omeg=True))
def test_npz_round_trip_preserves_reported_levels(data):
    npz = data.to_npz_dict()

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "sounding.npz"
        np.savez(path, **npz)
        prof_collection, _ = decoder_mod.load_npz(str(path))

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

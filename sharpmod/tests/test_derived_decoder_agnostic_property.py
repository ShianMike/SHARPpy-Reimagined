"""Property-based test for decoder-agnostic derived parameters (task 8.3).

Feature: sharppy-modernization, Property 14: Derived parameters are
decoder-agnostic

Property 14 (design.md): *For any* two Profiles that expose identical pressure,
height, temperature, dewpoint, and wind arrays but were produced by different
decoders, every derived parameter produces equal results (value or masked),
computed using only the exposed arrays without decoder-specific branching.

**Validates: Requirements 13.5**

What is checked
---------------
Every derived parameter in the SHARPpy Reimagined library is a *pure function of the
Profile-exposed arrays* -- it must read only ``pres`` / ``hght`` / ``tmpc`` /
``dwpc`` / ``wdir`` / ``wspd`` (and the wind components ``u`` / ``v`` and surface
index ``sfc`` derived from them) and must never branch on *which decoder*
produced the Profile (design.md "Decoder-agnostic parameters", Requirement
13.5).

To exercise this the test builds, for each generated sounding, **two profile
objects via two different construction paths**:

1. ``snd`` -- the :class:`~sharpmod.tests.strategies.SoundingData` produced by
   the shared :func:`profiles` strategy (one "decoder"), and

2. an :class:`_AltProfile` built by an *independent* construction path (a
   different class that rebuilds every reported-level array from plain Python
   lists, recomputes the wind components from ``wdir`` / ``wspd``, and -- to
   actively probe for decoder-specific branching -- carries a payload of decoy
   decoder-identifying attributes such as ``source_format``, ``decoder_name``,
   ``model`` and a distinct ``meta`` dict, plus an extra ``omeg`` column).

Both objects expose *identical* reported-level arrays, so every derived
parameter must return an identical result (the same value, or missing in both).
Any parameter that keyed off the object type, the source format, or any of the
decoy metadata would diverge and fail the assertion.

The full public derived-parameter surface is covered:

* kinematics -- SFC->500 m SRH / bulk shear / mean wind
  (:func:`sharpmod.sharptab.winds.sfc_500m_kinematics`) and the Bunkers storm
  motion (:func:`sharpmod.sharptab.winds.storm_motion`);
* thermodynamics -- SFC->1 km lapse rate, hail-growth-zone (-10/-30 degrees C)
  CAPE, 0-6 km-AGL CAPE (:mod:`sharpmod.sharptab.params`);
* ECAPE (:func:`sharpmod.sharptab.ecape.ecape`);
* the composite indices DCP, NCAPE/NCIN, EHI (0-1 km and 0-3 km), LRGHAIL, HPI,
  the Peskov index, and the MCS index (:mod:`sharpmod.sharptab.derived`).

Every derived routine degrades to the ``MISSING`` sentinel rather than raising,
so ``both missing`` counts as equal; otherwise the two paths must agree exactly.
A deterministic supercell sounding guards the property's precondition so the
equality clause is genuinely exercised on real (non-missing) values.
"""

from __future__ import annotations

import numpy as np
import numpy.ma as ma
from hypothesis import event, given

from sharpmod.sharptab import derived as derived_mod
from sharpmod.sharptab import ecape as ecape_mod
from sharpmod.sharptab import params as params_mod
from sharpmod.sharptab import winds as winds_mod
from sharpmod.sharptab.constants import MISSING, is_missing
from sharpmod.tests.strategies import CORE_FIELDS, SoundingData, profiles


# --------------------------------------------------------------------------- #
# An independent "second decoder": identical arrays, different construction
# path, and a payload of decoy decoder-identifying attributes.
# --------------------------------------------------------------------------- #
class _AltProfile:
    """A profile-like object exposing the same arrays as a ``SoundingData``.

    Built by a deliberately *different* construction path than
    :class:`SoundingData`: every reported-level array is rebuilt from plain
    Python lists (mimicking a text-parsing decoder), and the wind components are
    recomputed from ``wdir`` / ``wspd`` using the standard meteorological
    convention. It carries a bundle of *decoy* decoder-identifying attributes
    (``source_format`` / ``decoder_name`` / ``model`` / ``meta`` / an extra
    ``omeg`` column) that a decoder-agnostic parameter must ignore.
    """

    __slots__ = ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd",
                 "u", "v", "logp", "sfc", "omeg",
                 "source_format", "decoder_name", "model", "meta")

    def __init__(self, snd: SoundingData):
        # Rebuild each core array from Python lists (independent of how the
        # SoundingData masked arrays were constructed), preserving both the data
        # and any masked positions so the exposed arrays are identical.
        for name in CORE_FIELDS:
            src = ma.asanyarray(getattr(snd, name), dtype=float)
            data = [float(x) for x in np.asarray(src.filled(np.nan), dtype=float)]
            mask = [bool(m) for m in np.asarray(ma.getmaskarray(src), dtype=bool)]
            setattr(self, name, ma.array(data, mask=mask, dtype=float))

        # Recompute wind components from wdir/wspd with the same meteorological
        # convention SoundingData uses (u = -wspd*sin(dir), v = -wspd*cos(dir)).
        rad = np.deg2rad(self.wdir)
        self.u = -self.wspd * ma.sin(rad)
        self.v = -self.wspd * ma.cos(rad)
        self.logp = ma.log10(self.pres)
        self.sfc = 0

        # --- decoy decoder-identifying attributes (must NOT affect results) --
        self.source_format = "bufkit"
        self.decoder_name = "AltDecoder"
        self.model = "GFS"
        self.meta = {"decoder": "alt", "loc": "ALT", "provenance": "second-path"}
        self.omeg = ma.zeros(self.pres.size)


# --------------------------------------------------------------------------- #
# The full public derived-parameter surface, as pure functions of a Profile.
# Each entry maps a name -> callable(prof) returning a scalar or MISSING (tuple
# returns are split into separately-named scalar entries below).
# --------------------------------------------------------------------------- #
def _kinematics_srh(prof):
    return winds_mod.sfc_500m_kinematics(prof)[0]


def _kinematics_shear(prof):
    return winds_mod.sfc_500m_kinematics(prof)[1]


def _kinematics_meanwind_mag(prof):
    mw = winds_mod.sfc_500m_kinematics(prof)[2]
    if is_missing(mw):
        return MISSING
    return float(winds_mod.mag(mw[0], mw[1]))


def _storm_motion_component(prof, idx):
    return winds_mod.storm_motion(prof)[idx]


def _ncape(prof):
    return derived_mod.normalized_cape_cin(prof)[0]


def _ncin(prof):
    return derived_mod.normalized_cape_cin(prof)[1]


#: name -> callable(prof); every callable is contractually total (never raises)
#: and returns a float or the MISSING sentinel.
DERIVED_PARAMS = {
    "srh_sfc_500m": _kinematics_srh,
    "shear_sfc_500m": _kinematics_shear,
    "mean_wind_sfc_500m": _kinematics_meanwind_mag,
    "storm_motion_rstu": lambda p: _storm_motion_component(p, 0),
    "storm_motion_rstv": lambda p: _storm_motion_component(p, 1),
    "storm_motion_lstu": lambda p: _storm_motion_component(p, 2),
    "storm_motion_lstv": lambda p: _storm_motion_component(p, 3),
    "lapserate_sfc_1km": lambda p: params_mod.lapse_rate(p, 0.0, 1000.0, agl=True),
    "hgz_cape": lambda p: params_mod.layer_cape_isotherm(p, -10, -30),
    "cape_0_6km": lambda p: params_mod.layer_cape_agl(p, 0.0, 6000.0),
    "ecape": ecape_mod.ecape,
    "dcp": derived_mod.dcp,
    "ncape": _ncape,
    "ncin": _ncin,
    "ehi_0_1km": lambda p: derived_mod.ehi(p, 1000),
    "ehi_0_3km": lambda p: derived_mod.ehi(p, 3000),
    "lrghail": derived_mod.large_hail_parameter,
    "hpi": derived_mod.hail_possibility_index,
    "peskov": derived_mod.peskov_index,
    "mcs_index": derived_mod.mcs_index,
}


def _agree(name, a, b):
    """Assert two derived-parameter results agree (value or both missing).

    ``MISSING`` / masked / non-finite results count as "missing"; two missing
    results agree. Otherwise the two construction paths must produce the exact
    same finite value -- the parameter is a deterministic pure function of the
    identical exposed arrays.
    """
    a_missing = is_missing(a) or (isinstance(a, float) and not np.isfinite(a))
    b_missing = is_missing(b) or (isinstance(b, float) and not np.isfinite(b))

    if a_missing or b_missing:
        assert a_missing and b_missing, (
            f"{name!r}: one construction path produced a value and the other "
            f"missing (a={a!r}, b={b!r}) -- the parameter branched on the "
            f"decoder/provenance rather than the exposed arrays")
        return False  # agreed, but no finite value exercised

    fa, fb = float(a), float(b)
    assert fa == fb, (
        f"{name!r}: derived value differs between two construction paths with "
        f"identical arrays (a={fa!r}, b={fb!r}) -- not decoder-agnostic")
    return True  # a finite value was exercised


def _assert_all_agree(snd, alt):
    """Compare every derived parameter across the two profiles; return coverage.

    Returns the set of parameter names that resolved to a real (non-missing)
    value in both paths (so callers can confirm the equality clause was
    genuinely exercised, not vacuously satisfied by everything being missing).
    """
    exercised = set()
    for name, fn in DERIVED_PARAMS.items():
        a = fn(snd)
        b = fn(alt)
        if _agree(name, a, b):
            exercised.add(name)
    return exercised


@given(profiles())
def test_derived_parameters_are_decoder_agnostic(snd):
    """Every derived parameter is identical across two construction paths.

    Feature: sharppy-modernization, Property 14: Derived parameters are
    decoder-agnostic
    Validates: Requirements 13.5
    """
    alt = _AltProfile(snd)

    # Sanity: the two paths really do expose identical reported-level arrays.
    for name in CORE_FIELDS:
        a = ma.asanyarray(getattr(snd, name), dtype=float)
        b = ma.asanyarray(getattr(alt, name), dtype=float)
        np.testing.assert_array_equal(
            ma.getmaskarray(a), ma.getmaskarray(b),
            err_msg=f"{name!r}: mask differs between construction paths")
        np.testing.assert_array_equal(
            np.asarray(a.filled(np.nan)), np.asarray(b.filled(np.nan)),
            err_msg=f"{name!r}: data differs between construction paths")

    exercised = _assert_all_agree(snd, alt)
    if exercised:
        event(f"decoder-agnostic values exercised: {len(exercised)} params")
    else:
        event("all derived parameters missing for this sounding")


def _supercell_sounding() -> SoundingData:
    """A deterministic strongly-buoyant, strongly-sheared supercell sounding.

    Warm/moist near the surface with a steep mid-level lapse rate, a dry mid
    layer, and veering/strengthening winds through the depth so a broad set of
    derived parameters resolve to real (non-missing) values -- guaranteeing the
    equality clause is genuinely exercised on finite values rather than being
    vacuously satisfied by everything degrading to MISSING.
    """
    hght = np.array(
        [0.0, 500.0, 1000.0, 2000.0, 3000.0, 5000.0,
         7000.0, 9000.0, 11000.0, 12000.0, 14000.0, 16000.0], dtype=float)
    pres = 1000.0 * np.exp(-hght / 8000.0)
    tmpc = np.array(
        [30.0, 25.0, 21.0, 13.0, 6.0, -8.0,
         -23.0, -39.0, -55.0, -57.0, -57.0, -57.0], dtype=float)
    dwpc = np.array(
        [23.0, 20.0, 16.0, 6.0, -2.0, -18.0,
         -32.0, -48.0, -62.0, -66.0, -70.0, -72.0], dtype=float)
    wdir = np.array(
        [140.0, 170.0, 195.0, 220.0, 240.0, 255.0,
         260.0, 265.0, 270.0, 275.0, 280.0, 285.0], dtype=float)
    wspd = np.array(
        [10.0, 22.0, 32.0, 42.0, 50.0, 62.0,
         70.0, 78.0, 85.0, 90.0, 96.0, 102.0], dtype=float)
    return SoundingData(pres, hght, tmpc, dwpc, wdir, wspd)


def test_decoder_agnostic_on_supercell_example():
    """A supercell sounding: derived params match across construction paths.

    Guards the property's precondition -- at least one input yields several
    real (non-MISSING) derived values that are identical across the two
    construction paths, so the equality clause is genuinely exercised.

    Feature: sharppy-modernization, Property 14: Derived parameters are
    decoder-agnostic
    Validates: Requirements 13.5
    """
    snd = _supercell_sounding()
    alt = _AltProfile(snd)

    exercised = _assert_all_agree(snd, alt)

    # The purely-kinematic parameters need no parcel-ascent oracle, so they must
    # resolve for this deep, well-sheared profile regardless of the environment.
    for kinematic in ("srh_sfc_500m", "shear_sfc_500m", "mean_wind_sfc_500m",
                      "storm_motion_rstu", "storm_motion_rstv"):
        assert kinematic in exercised, (
            f"expected {kinematic!r} to resolve for the supercell sounding")


if __name__ == "__main__":  # pragma: no cover
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))

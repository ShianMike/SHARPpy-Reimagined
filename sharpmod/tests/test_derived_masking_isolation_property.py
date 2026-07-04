"""Property-based test for input-masking isolation (task 8.4).

Feature: sharppy-modernization, Property 12: Missing or masked inputs are
layer-scoped -- they mask a parameter only when they fall within (or prevent
resolving) the layer that parameter requires

Property 12 (design.md, layer-scoped masking): *For any* derived parameter and
*for any* valid Profile, a masked input degrades the parameter to the
``MISSING`` sentinel (never raising or dividing by zero) only when it prevents
the parameter's required layer from being resolved; a masked input at a level
*outside* every parameter's required layer (e.g. a missing top-of-sounding
wind) leaves the parameters unchanged. Masking one Profile never affects an
unrelated Profile.

**Validates: Requirements 1.6, 2.4, 3.4, 3.5, 4.4, 4.5, 5.6, 6.6, 13.6, 16.3,
17.3, 18.5, 19.4, 19.5, 21.4, 21.5**

How the property is exercised
-----------------------------
Every SHARPpy Reimagined composite derived parameter reads the six core reported-level
columns (``pres``, ``hght``, ``tmpc``, ``dwpc``, ``wdir``, ``wspd``) and
delegates to the vendored ``sharppy`` parcel-ascent / kinematic oracle, which
masks the ``-9999`` missing sentinel and resolves each value from the valid
levels spanning that parameter's required layer. Consequently:

* **Clean-degradation clause** -- for *any* masked input the parameter still
  *returns* (never raises), and any present (non-``MISSING``) result is a finite
  float rather than a divide-by-zero ``inf`` / ``nan`` (Requirements 2.4, 4.4,
  4.5, 5.6, 6.6, 16.3, 17.3, 18.5, 19.4, 19.5, 21.4, 21.5).

* **Above-layer isolation clause** -- masking a level that lies *above* every
  parameter's required layer (the top of a deep sounding) never turns a present
  parameter into ``MISSING``: data irrelevant to a parameter's layer does not
  disqualify it (the core of the layer-scoped fix).

* **Cross-profile isolation clause** -- masking an input on *one* Profile never
  corrupts the derived parameters of an *unrelated* Profile. Each parameter is
  computed by its own function with no shared mutable state, so a baseline
  (unmasked) Profile yields identical results before and after a masked copy is
  evaluated alongside it (Requirements 1.6, 3.4, 3.5, 13.6).

The generators come from the shared :func:`sharpmod.tests.strategies.profiles`
strategy; the suite-wide Hypothesis profile (see ``conftest.py``) runs each
property for at least 100 examples.
"""

from __future__ import annotations

import numpy as np
import numpy.ma as ma
from hypothesis import event, given
from hypothesis import strategies as st

from sharpmod.sharptab import derived as derived_mod
from sharpmod.sharptab.constants import MISSING, is_missing
from sharpmod.tests.strategies import CORE_FIELDS, SoundingData, profiles


# ---------------------------------------------------------------------------
# Derived-parameter probes: (label, callable) -> value or MISSING, never raises
# ---------------------------------------------------------------------------

def _probe_all(prof) -> dict:
    """Return ``{label: value}`` for every SHARPpy Reimagined derived parameter.

    Each entry is either a numeric value or the ``MISSING`` sentinel. By the
    module contract none of these calls raises; the property asserts that
    invariant by invoking them directly (an escaping exception fails the test).
    """
    ncape, ncin = derived_mod.normalized_cape_cin(prof)
    return {
        "dcp": derived_mod.dcp(prof),
        "ncape": ncape,
        "ncin": ncin,
        "ehi_0_1km": derived_mod.ehi(prof, 1000),
        "ehi_0_3km": derived_mod.ehi(prof, 3000),
        "lrghail": derived_mod.large_hail_parameter(prof),
        "hpi": derived_mod.hail_possibility_index(prof),
        "peskov": derived_mod.peskov_index(prof),
        "mcs": derived_mod.mcs_index(prof),
    }


def _finite_when_present(label, value) -> None:
    """A present (non-MISSING) parameter must be a finite float, never inf/nan.

    This is the "never divides by zero" half of the contract: a masked/degenerate
    input must yield the ``MISSING`` sentinel, *not* a non-finite numeric result.
    """
    if not is_missing(value):
        fval = float(value)
        assert np.isfinite(fval), (
            f"{label} present but non-finite ({fval!r}); a masked/degenerate "
            f"input must degrade to MISSING, never a divide-by-zero result"
        )


def _same(a, b) -> bool:
    """True when two probe results are the same value (both MISSING, or equal)."""
    if is_missing(a) and is_missing(b):
        return True
    if is_missing(a) or is_missing(b):
        return False
    return float(a) == float(b)


def _mask_field(base: SoundingData, field: str, idx: int) -> SoundingData:
    """Return a copy of ``base`` with ``field`` masked at level ``idx``.

    Only the requested field/level is altered; every other column is copied
    through unchanged so the masked and baseline soundings share identical data
    apart from the single masked entry.
    """
    cols = {}
    for name in CORE_FIELDS:
        cols[name] = ma.array(getattr(base, name), copy=True)
    target = cols[field]
    mask = ma.getmaskarray(target).copy()
    mask[idx] = True
    target.mask = mask
    return SoundingData(
        pres=cols["pres"], hght=cols["hght"], tmpc=cols["tmpc"],
        dwpc=cols["dwpc"], wdir=cols["wdir"], wspd=cols["wspd"],
        omeg=base.omeg,
    )


# ---------------------------------------------------------------------------
# Property 12 -- masking isolates the affected parameter(s)
# ---------------------------------------------------------------------------

@given(profiles(min_levels=6, max_levels=24), st.data())
def test_masking_input_degrades_cleanly_and_isolates_other_profiles(base, data):
    """Masking a required input degrades cleanly (never raises / never
    inf-nan) and never corrupts an unrelated Profile.

    Under layer-scoped masking a masked level does not unconditionally force
    every parameter to MISSING (a level above a parameter's required layer is
    tolerated); what always holds is that the parameter still *returns* a value
    that is either the MISSING sentinel or a finite float, and that evaluating a
    masked copy leaves the baseline Profile's results unchanged.

    Feature: sharppy-modernization, Property 12: layer-scoped input masking
    Validates: Requirements 1.6, 2.4, 3.4, 3.5, 4.4, 4.5, 5.6, 6.6, 13.6, 16.3,
    17.3, 18.5, 19.4, 19.5, 21.4, 21.5
    """
    field = data.draw(st.sampled_from(CORE_FIELDS), label="masked_field")
    idx = data.draw(
        st.integers(min_value=0, max_value=base.nlevels - 1), label="level")

    # --- baseline (unmasked): must never raise; present values are finite ---
    base_vals = _probe_all(base)
    for label, value in base_vals.items():
        _finite_when_present(label, value)
    if any(not is_missing(v) for v in base_vals.values()):
        event("baseline: at least one parameter present")
    else:
        event("baseline: all parameters MISSING (e.g. degenerate sounding)")

    # --- masked copy: every parameter must still return cleanly -- either ----
    # --- the MISSING sentinel or a finite float, never raising / inf-nan. ----
    masked = _mask_field(base, field, idx)
    masked_vals = _probe_all(masked)
    for label, value in masked_vals.items():
        _finite_when_present(label, value)
    event(f"masked field={field}: all parameters returned cleanly")

    # --- isolation: evaluating the masked copy did not corrupt the baseline --
    base_vals_again = _probe_all(base)
    for label in base_vals:
        assert _same(base_vals[label], base_vals_again[label]), (
            f"{label} on the unmasked baseline changed after evaluating a "
            f"masked copy ({base_vals[label]!r} -> {base_vals_again[label]!r}); "
            f"masking one Profile must not affect an unrelated Profile"
        )


# ---------------------------------------------------------------------------
# Deterministic guards: genuine "one MISSING, an unrelated one present"
# ---------------------------------------------------------------------------

def _supercell_sounding() -> SoundingData:
    """A deep, fully valid supercell-like sounding.

    Strong low-level buoyancy, a steep mid-level lapse rate spanning the
    -10/-30 degrees C hail-growth zone, and veering/strengthening winds through
    the whole column, so (when the parcel-ascent oracle is available) the
    composite parameters resolve to real values -- giving the masking/isolation
    clauses a non-trivial baseline to act on.
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


def _deep_sounding() -> SoundingData:
    """A deep supercell-like sounding with a genuine stratosphere.

    Like :func:`_supercell_sounding` but extended to 20 km with a temperature
    inversion above the ~14 km tropopause, so the most-unstable parcel's
    equilibrium level sits well below the top of the profile. Several reported
    levels (16 / 18 / 20 km) therefore lie *above* every parameter's required
    layer, giving the above-layer isolation clause a real top margin: masking the
    topmost level cannot move an EL/LFC or a kinematic layer top.
    """
    hght = np.array(
        [0.0, 500.0, 1000.0, 2000.0, 3000.0, 5000.0, 7000.0, 9000.0,
         11000.0, 12000.0, 14000.0, 16000.0, 18000.0, 20000.0], dtype=float)
    pres = 1000.0 * np.exp(-hght / 8000.0)
    tmpc = np.array(
        [30.0, 25.0, 21.0, 13.0, 6.0, -8.0, -23.0, -39.0,
         -55.0, -60.0, -62.0, -58.0, -54.0, -50.0], dtype=float)
    dwpc = np.array(
        [23.0, 20.0, 16.0, 6.0, -2.0, -18.0, -32.0, -48.0,
         -62.0, -68.0, -72.0, -75.0, -78.0, -80.0], dtype=float)
    wdir = np.array(
        [140.0, 170.0, 195.0, 220.0, 240.0, 255.0, 260.0, 265.0,
         270.0, 275.0, 280.0, 285.0, 290.0, 295.0], dtype=float)
    wspd = np.array(
        [10.0, 22.0, 32.0, 42.0, 50.0, 62.0, 70.0, 78.0,
         85.0, 90.0, 96.0, 102.0, 108.0, 114.0], dtype=float)
    return SoundingData(pres, hght, tmpc, dwpc, wdir, wspd)


def test_masking_above_required_layers_does_not_disqualify_parameters():
    """On a deep sounding, masking the *topmost* level -- which lies above every
    parameter's required layer -- never turns a present parameter into MISSING.

    This is the deterministic heart of the layer-scoped contract: a missing
    datum irrelevant to a parameter's layer (the classic missing top-of-sounding
    wind of a real radiosonde) must not disqualify the parameter. Each call must
    also return cleanly (never raising or exposing a non-finite result).

    Feature: sharppy-modernization, Property 12: layer-scoped input masking
    Validates: Requirements 2.4, 4.4, 4.5, 5.6, 6.6, 16.3, 17.3, 18.5, 19.5, 21.5
    """
    base = _deep_sounding()

    # Baseline must never raise and never expose a non-finite numeric result.
    base_vals = _probe_all(base)
    for label, value in base_vals.items():
        _finite_when_present(label, value)

    top = base.nlevels - 1  # highest reported level, above every required layer
    for field in CORE_FIELDS:
        masked = _mask_field(base, field, top)
        for label, value in _probe_all(masked).items():
            _finite_when_present(label, value)
            if not is_missing(base_vals[label]):
                assert not is_missing(value), (
                    f"{label} became MISSING when the irrelevant top level's "
                    f"{field!r} was masked; masking data above a parameter's "
                    f"required layer must not disqualify it"
                )

    # Isolation: the baseline is unchanged after all the masked evaluations.
    for label, value in _probe_all(base).items():
        assert _same(base_vals[label], value), (
            f"{label} on the baseline changed after masked evaluations"
        )


def test_missing_structural_input_isolates_to_affected_parameter():
    """A missing structural input masks only the parameter that needs it.

    A warm sounding that never crosses the -10 degrees C isotherm has no
    hail-growth-zone CAPE, so the Hail Possibility Index (which integrates CAPE
    over the -10/-30 degrees C layer) must be MISSING -- yet the Large-Hail
    Parameter, which does not require that layer, is computed independently
    (Requirement 6.6, "without affecting computation of the other index").

    Requires the parcel-ascent oracle; when it is unavailable both indices
    degrade to MISSING and the isolation direction cannot be observed, so the
    check is skipped (matching the suite's other oracle-dependent tests).

    Feature: sharppy-modernization, Property 12: Missing or masked inputs mask
    only the affected parameter
    Validates: Requirements 6.6, 16.3, 17.3
    """
    # Warm, deep-ish sounding whose temperature never reaches -10 degrees C:
    # no hail-growth zone, so HPI's required input term is absent.
    hght = np.array(
        [0.0, 400.0, 800.0, 1500.0, 2200.0, 3000.0, 4000.0, 5000.0],
        dtype=float)
    pres = 1000.0 * np.exp(-hght / 8000.0)
    tmpc = np.array(
        [30.0, 27.0, 24.0, 19.0, 14.0, 9.0, 3.0, -2.0], dtype=float)
    dwpc = np.array(
        [24.0, 22.0, 19.0, 14.0, 9.0, 3.0, -4.0, -12.0], dtype=float)
    wdir = np.array(
        [180.0, 200.0, 215.0, 230.0, 245.0, 255.0, 262.0, 268.0], dtype=float)
    wspd = np.array(
        [15.0, 22.0, 30.0, 38.0, 46.0, 54.0, 60.0, 66.0], dtype=float)
    snd = SoundingData(pres, hght, tmpc, dwpc, wdir, wspd)

    hpi = derived_mod.hail_possibility_index(snd)
    lrghail = derived_mod.large_hail_parameter(snd)

    # Both calls must return cleanly (never raise); present values are finite.
    _finite_when_present("hpi", hpi)
    _finite_when_present("lrghail", lrghail)

    if not is_missing(lrghail):
        # LRGHAIL resolved -> the oracle is available. HPI's missing structural
        # input (no hail-growth zone) must not have prevented LRGHAIL, and HPI
        # itself must be MISSING for this sounding.
        assert is_missing(hpi), (
            "HPI must be MISSING when the sounding has no -10/-30 degrees C "
            f"hail-growth zone, got {hpi!r}"
        )

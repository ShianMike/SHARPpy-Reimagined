"""The reformulated Possible Hazard Type classifier for SharpTab (Requirement 9).

Successor to SHARPpy's legacy "Possible Hazard Type" box. :func:`classify`
assigns a sounding *exactly one* hazard label drawn from :data:`HAZARD_LABELS`::

    ("none", "marginal", "tornado", "supercell", "wind", "hail",
     "insufficient data")

Design principles honoured here (SHARPpy Reimagined design.md, "SharpTab: Hazard_Classifier"):

* **Pure, deterministic function of a fixed input set.** The label is derived
  solely from a fixed set of Profile-computed parameters -- STP (effective,
  CIN-weighted), SCP (right-mover), SHIP, EBWD (effective bulk wind difference),
  MUCAPE, and effective-layer SRH -- evaluated against the fixed threshold
  decision table pinned below. Two Profiles that produce identical values for
  every input parameter therefore receive the identical label (Requirement 9.3),
  and the returned label is always exactly one member of :data:`HAZARD_LABELS`
  (Requirement 9.1).
* **Insufficient data is explicit.** If *any* required input parameter is missing
  or masked, the classifier returns ``"insufficient data"`` rather than any other
  hazard category (Requirement 9.4).
* **Missing data propagates, never crashes.** Any unexpected failure while
  resolving the inputs degrades to ``"insufficient data"``.

Input resolution
----------------
The classifier reads its six inputs off the analyzed ``prof`` by attribute
(Profile attribute access), accepting the SHARPpy convective-profile attribute
names (``stp_cin``, ``right_scp``, ``ship``, ``ebwspd`` / ``ebwd``, ``mupcl``,
``right_esrh``). When the analyzed ``prof`` does not itself expose the convective
parameters (e.g. a bare SHARPpy Reimagined :class:`~sharpmod.sharptab.profile.Profile`),
the classifier -- following the same lazy-``sharppy`` oracle convention used by
:mod:`sharpmod.sharptab.derived` -- builds a ``sharppy`` "convective" profile from
the Profile's reported-level columns and reads the parameters from it. If
``sharppy`` is unavailable or any input still cannot be resolved, the classifier
returns ``"insufficient data"``.

Pinned threshold decision table (design.md defers the numeric table to
implementation-time pinning, the same convention used for the derived-parameter
formulas in :mod:`sharpmod.sharptab.derived`). Thresholds follow established SPC
effective-layer composite-parameter guidance:

* STP >= 1  -- Significant Tornado Parameter favouring significant (EF2+)
  tornadoes (Thompson et al. 2003, *Wea. Forecasting* 18; Thompson et al. 2012).
* SCP >= 1  -- Supercell Composite Parameter favouring supercells
  (Thompson et al. 2003).
* effective SRH >= 100 m^2/s^2 -- low-level rotation supportive of tornadic
  supercells.
* EBWD >= 40 kt -- deep-layer shear supportive of supercell organization;
  EBWD >= 30 kt marks organized/multicell-to-supercell shear.
* SHIP >= 1 -- Significant Hail Parameter favouring hail >= 2 in.
* MUCAPE thresholds: < 25 J/kg == no meaningful convection; >= 1000 J/kg with
  organizing deep shear == damaging-wind potential; >= 500 J/kg == marginal
  severe potential.

The cascade below is evaluated top-to-bottom and returns on the first match, so
each Profile maps to exactly one label:

1. ``insufficient data`` -- any required input missing/masked.
2. ``none``       -- MUCAPE < 25 J/kg (no meaningful convection).
3. ``tornado``    -- STP >= 1 AND SCP >= 1 AND effective SRH >= 100 AND
   EBWD >= 30 kt (significant-tornado environment).
4. ``supercell``  -- SCP >= 1 OR EBWD >= 40 kt (organized/rotating storms not
   meeting the tornado threshold).
5. ``hail``       -- SHIP >= 1 (significant-hail environment).
6. ``wind``       -- MUCAPE >= 1000 J/kg AND EBWD >= 30 kt (strong instability
   with organizing deep shear but weak effective-layer rotation).
7. ``marginal``   -- MUCAPE >= 500 J/kg OR EBWD >= 20 kt (low-end severe
   potential).
8. ``none``       -- otherwise.
"""

from __future__ import annotations

import numpy as np
import numpy.ma as ma

from .constants import is_missing

__all__ = ["HAZARD_LABELS", "classify"]


#: The complete, ordered set of Possible Hazard Type labels (Requirement 9.1).
HAZARD_LABELS = (
    "none",
    "marginal",
    "tornado",
    "supercell",
    "wind",
    "hail",
    "insufficient data",
)

#: The fixed set of input-parameter names the classification consumes.
REQUIRED_INPUTS = ("mucape", "esrh", "ebwd", "stp", "scp", "ship")


# ---------------------------------------------------------------------------
# Pinned threshold decision table (see module docstring)
# ---------------------------------------------------------------------------
_MUCAPE_MIN_CONVECTION = 25.0    # J/kg: below this, no meaningful convection
_STP_TORNADO = 1.0               # Significant Tornado Parameter
_SCP_SUPERCELL = 1.0             # Supercell Composite Parameter
_ESRH_TORNADO = 100.0            # m^2/s^2: effective-layer SRH
_EBWD_SUPERCELL = 40.0           # kt: deep-layer shear supporting supercells
_EBWD_ORGANIZED = 30.0           # kt: organizing deep-layer shear
_SHIP_HAIL = 1.0                 # Significant Hail Parameter
_MUCAPE_WIND = 1000.0            # J/kg: strong instability (damaging wind)
_MUCAPE_MARGINAL = 500.0         # J/kg: marginal severe instability
_EBWD_MARGINAL = 20.0            # kt: marginal organizing shear


# ---------------------------------------------------------------------------
# Numeric-coercion helpers
# ---------------------------------------------------------------------------

def _finite_or_none(value):
    """Return ``float(value)`` when present and finite, else ``None``."""
    if value is None or is_missing(value):
        return None
    try:
        fval = float(value)
    except (TypeError, ValueError):
        return None
    return fval if np.isfinite(fval) else None


def _first_element(value):
    """Coerce a scalar or the first element of a tuple/array to a finite float.

    SHARPpy exposes effective-layer SRH as a ``(total, positive, negative)``
    tuple; the classifier uses the total (element 0).
    """
    if value is None or is_missing(value):
        return None
    if isinstance(value, (tuple, list, np.ndarray)):
        if len(value) == 0:
            return None
        return _finite_or_none(value[0])
    return _finite_or_none(value)


def _magnitude(value):
    """Coerce a wind-difference value to a finite magnitude (kt).

    Accepts a scalar magnitude, or a ``(u, v)`` component pair (SHARPpy's
    ``ebwd``) whose Euclidean magnitude is returned.
    """
    if value is None or is_missing(value):
        return None
    if isinstance(value, (tuple, list, np.ndarray)):
        if len(value) == 2:
            u = _finite_or_none(value[0])
            v = _finite_or_none(value[1])
            if u is None or v is None:
                return None
            return float(np.hypot(u, v))
        if len(value) >= 1:
            return _finite_or_none(value[0])
        return None
    return _finite_or_none(value)


# ---------------------------------------------------------------------------
# Per-input attribute getters (SHARPpy convective-profile attribute names)
# ---------------------------------------------------------------------------

def _get_mucape(src):
    """Most-unstable CAPE (J/kg): ``src.mucape`` or ``src.mupcl.bplus``."""
    value = _finite_or_none(getattr(src, "mucape", None))
    if value is not None:
        return value
    mupcl = getattr(src, "mupcl", None)
    if mupcl is not None:
        return _finite_or_none(getattr(mupcl, "bplus", None))
    return None


def _get_esrh(src):
    """Effective-layer SRH (m^2/s^2): ``src.right_esrh`` (total) or ``src.esrh``."""
    value = getattr(src, "right_esrh", None)
    if value is None:
        value = getattr(src, "esrh", None)
    return _first_element(value)


def _get_ebwd(src):
    """Effective bulk wind difference (kt): ``src.ebwspd`` or ``|src.ebwd|``."""
    value = _finite_or_none(getattr(src, "ebwspd", None))
    if value is not None:
        return value
    return _magnitude(getattr(src, "ebwd", None))


def _get_stp(src):
    """Significant Tornado Parameter: ``stp_cin`` (effective) else ``stp_fixed`` / ``stp``."""
    for name in ("stp_cin", "stp_fixed", "stp"):
        value = _finite_or_none(getattr(src, name, None))
        if value is not None:
            return value
    return None


def _get_scp(src):
    """Supercell Composite Parameter: ``right_scp`` (right-mover) else ``scp``."""
    for name in ("right_scp", "scp"):
        value = _finite_or_none(getattr(src, name, None))
        if value is not None:
            return value
    return None


def _get_ship(src):
    """Significant Hail Parameter: ``src.ship``."""
    return _finite_or_none(getattr(src, "ship", None))


#: ``input name -> getter(source)`` for the fixed classification input set.
_GETTERS = {
    "mucape": _get_mucape,
    "esrh": _get_esrh,
    "ebwd": _get_ebwd,
    "stp": _get_stp,
    "scp": _get_scp,
    "ship": _get_ship,
}


# ---------------------------------------------------------------------------
# sharppy convective-profile oracle (built from the Profile columns)
# ---------------------------------------------------------------------------

def _columns(prof):
    """Return ``(pres, hght, tmpc, dwpc, wdir, wspd)`` as validated float arrays.

    Winds are taken from ``wdir`` / ``wspd`` when present, otherwise derived from
    the ``u`` / ``v`` components. Returns ``None`` when any required field is
    absent, masked, non-finite, mismatched in length, or too short to lift a
    parcel -- mirroring the guard used by :mod:`sharpmod.sharptab.derived`.
    """
    try:
        def col(name):
            arr = getattr(prof, name, None)
            if arr is None:
                return None
            return ma.masked_invalid(ma.asanyarray(arr, dtype=float))

        pres = col("pres")
        hght = col("hght")
        tmpc = col("tmpc")
        dwpc = col("dwpc")
        wdir = col("wdir")
        wspd = col("wspd")
        u_kt = col("u")
        v_kt = col("v")
        if (wdir is None or wspd is None) and (u_kt is not None and v_kt is not None):
            wspd = ma.sqrt(u_kt ** 2 + v_kt ** 2)
            wdir = (270.0 - ma.degrees(ma.arctan2(v_kt, u_kt))) % 360.0

        for arr in (pres, hght, tmpc, dwpc, wdir, wspd):
            if arr is None or ma.getmaskarray(arr).any():
                return None

        n = int(pres.size)
        if n < 3 or any(arr.size != n for arr in (hght, tmpc, dwpc, wdir, wspd)):
            return None

        out = tuple(
            np.asarray(ma.asanyarray(arr).filled(np.nan), dtype=float)
            for arr in (pres, hght, tmpc, dwpc, wdir, wspd)
        )
        if not all(np.all(np.isfinite(arr)) for arr in out):
            return None
        return out
    except Exception:
        return None


def _convective_oracle(prof):
    """Build a ``sharppy`` "convective" profile from ``prof``'s columns.

    Returns the convective profile (which eagerly computes ``stp_cin``,
    ``right_scp``, ``ship``, ``ebwd`` / ``ebwspd``, ``mupcl``, ``right_esrh``,
    ...) or ``None`` when the columns are missing/masked or ``sharppy`` is
    unavailable / the ascent cannot be run. Never raises.
    """
    cols = _columns(prof)
    if cols is None:
        return None
    pres, hght, tmpc, dwpc, wdir, wspd = cols
    try:
        from sharppy.sharptab import profile as sp_profile
    except Exception:
        return None
    try:
        return sp_profile.create_profile(
            profile="convective",
            pres=pres, hght=hght, tmpc=tmpc, dwpc=dwpc, wdir=wdir, wspd=wspd,
            missing=-9999.0, strictQC=False,
        )
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Input extraction
# ---------------------------------------------------------------------------

def _extract_inputs(prof):
    """Resolve the six classification inputs from ``prof``.

    Reads each input off ``prof`` directly first (Profile attribute access);
    for any input still unresolved, builds the ``sharppy`` convective oracle from
    ``prof``'s columns and reads the remaining inputs from it. Returns a dict of
    finite floats keyed by :data:`REQUIRED_INPUTS`, or ``None`` if any input is
    missing/masked (Requirement 9.4).
    """
    values = {name: getter(prof) for name, getter in _GETTERS.items()}
    if all(value is not None for value in values.values()):
        return values

    oracle = _convective_oracle(prof)
    if oracle is not None and oracle is not prof:
        for name, getter in _GETTERS.items():
            if values[name] is None:
                values[name] = getter(oracle)

    if any(value is None for value in values.values()):
        return None
    return values


# ---------------------------------------------------------------------------
# Decision table
# ---------------------------------------------------------------------------

def _classify_from_inputs(mucape, esrh, ebwd, stp, scp, ship):
    """Return the hazard label for the (already-validated) numeric inputs.

    A pure function of the six inputs evaluated against the pinned threshold
    decision table (see module docstring). Deterministic and total: every input
    tuple maps to exactly one non-"insufficient data" label (Requirements 9.1,
    9.2, 9.3).
    """
    # 2. No meaningful convection.
    if mucape < _MUCAPE_MIN_CONVECTION:
        return "none"

    # 3. Significant-tornado environment.
    if (stp >= _STP_TORNADO and scp >= _SCP_SUPERCELL
            and esrh >= _ESRH_TORNADO and ebwd >= _EBWD_ORGANIZED):
        return "tornado"

    # 4. Organized/rotating storms (supercell) not meeting the tornado threshold.
    if scp >= _SCP_SUPERCELL or ebwd >= _EBWD_SUPERCELL:
        return "supercell"

    # 5. Significant-hail environment.
    if ship >= _SHIP_HAIL:
        return "hail"

    # 6. Damaging-wind environment.
    if mucape >= _MUCAPE_WIND and ebwd >= _EBWD_ORGANIZED:
        return "wind"

    # 7. Low-end (marginal) severe potential.
    if mucape >= _MUCAPE_MARGINAL or ebwd >= _EBWD_MARGINAL:
        return "marginal"

    # 8. Otherwise.
    return "none"


def classify(prof) -> str:
    """Assign the Possible Hazard Type label for ``prof``.

    Parameters
    ----------
    prof:
        The analyzed profile. Either a profile that already exposes the
        convective parameters (``stp_cin`` / ``right_scp`` / ``ship`` /
        ``ebwspd`` or ``ebwd`` / ``mupcl`` / ``right_esrh``) or a SHARPpy Reimagined
        :class:`~sharpmod.sharptab.profile.Profile` carrying the reported-level
        columns, from which the parameters are computed via the ``sharppy``
        convective oracle.

    Returns
    -------
    str
        Exactly one label from :data:`HAZARD_LABELS` (Requirement 9.1);
        ``"insufficient data"`` when any required input parameter is
        missing/masked (Requirement 9.4). Deterministic (Requirement 9.3) and
        never raises.
    """
    try:
        inputs = _extract_inputs(prof)
    except Exception:
        # Design principle: missing data propagates, never crashes.
        return "insufficient data"

    if inputs is None:
        return "insufficient data"

    return _classify_from_inputs(
        mucape=inputs["mucape"],
        esrh=inputs["esrh"],
        ebwd=inputs["ebwd"],
        stp=inputs["stp"],
        scp=inputs["scp"],
        ship=inputs["ship"],
    )

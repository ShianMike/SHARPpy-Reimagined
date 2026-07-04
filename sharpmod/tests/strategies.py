"""Shared Hypothesis strategies for the SHARPpy Reimagined test suite.

This module exposes :func:`profiles` -- a Hypothesis strategy that builds
physically plausible soundings usable to construct a ``Profile`` (modelled on
``sharppy.sharptab.profile.create_profile``: the core arrays ``pres``, ``hght``,
``tmpc``, ``dwpc``, ``wdir``, ``wspd``, plus an optional ``omeg`` column).

The strategy yields a lightweight :class:`SoundingData` object that

* holds the six core reported-level arrays (plus optional ``omeg``) as NumPy
  masked arrays, and
* is *directly usable* with the SharpTab interpolation helpers
  (:mod:`sharpmod.sharptab.interp`) today -- it exposes the derived attributes
  those helpers read (``u``, ``v``, ``logp``, ``sfc``) -- so tests can exercise
  the layer/isotherm lookups before the full ``Profile`` lazy-attribute
  mechanism (task 8) lands.

Every generated sounding is *physically plausible* by construction:

* pressure is strictly **monotonically decreasing** with index (surface first),
* height (m MSL) is strictly **monotonically increasing** with index,
* temperature ``T`` is always ``>=`` dewpoint ``Td`` (non-negative dewpoint
  depression),
* wind direction lies in ``[0, 360)`` and wind speed is ``>= 0``.

Controllable edge-case coverage (see :func:`profiles` keyword arguments):

* **shallow tops** -- force the profile top below 500 m / 1 km / 6 km AGL,
* **levels between reported levels** -- the target levels (500 m, 1 km, 6 km
  AGL and the -10/-30 degrees C isotherms) generally fall *between* reported
  levels, so interpolation paths are exercised by default,
* **masked fields** -- inject masked entries into selected fields,
* **zero-CAPE / zero-DCAPE** -- bias the thermodynamic profile toward no
  buoyancy / no downdraft energy (approximate; see notes on the keywords),
* **isotherm spanning** -- force the profile to span (or to *not* span) the
  -10 degrees C to -30 degrees C hail-growth-zone layer.

Other test modules obtain the generator with::

    from sharpmod.tests.strategies import profiles

The shared Hypothesis settings profile (minimum 100 examples) is registered and
loaded as the default in :mod:`sharpmod.tests.conftest`.
"""

from __future__ import annotations

from typing import Iterable, Optional, Sequence, Union

import numpy as np
import numpy.ma as ma
from hypothesis import strategies as st

from sharpmod.sharptab.constants import MISSING  # noqa: F401  (re-exported intent)

__all__ = [
    "SoundingData",
    "profiles",
    "CORE_FIELDS",
    "SHALLOW_CEILINGS",
    "ParamProfile",
    "HAZARD_INPUT_ATTRS",
    "HAZARD_INPUT_RANGES",
    "hazard_inputs",
]

#: The six core reported-level fields every sounding carries, in the order the
#: ``create_profile`` factory expects them.
CORE_FIELDS = ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd")

#: Ceiling (m AGL) associated with each ``shallow_top`` keyword. The generated
#: profile top stays strictly below the selected ceiling so tests can assert
#: that layer parameters requiring that depth return the missing-value sentinel.
SHALLOW_CEILINGS = {"500m": 500.0, "1km": 1000.0, "6km": 6000.0}


class SoundingData:
    """A physically plausible sounding usable to construct a ``Profile``.

    Parameters
    ----------
    pres, hght, tmpc, dwpc, wdir, wspd:
        The six core reported-level arrays. Stored as NumPy masked arrays.
    omeg:
        Optional vertical-velocity column (Pa/s), mirroring the HRRR/ERA5
        ``.npz`` point-sounding sidecar.
    meta:
        Optional metadata dict (location, valid time, ...), used when writing
        the intermediate ``.npz`` representation.

    Notes
    -----
    In addition to the core arrays, the instance exposes the derived attributes
    that :mod:`sharpmod.sharptab.interp` reads directly:

    * ``sfc`` -- surface-level index (always ``0``: the surface is the first,
      highest-pressure level),
    * ``logp`` -- ``log10(pres)``,
    * ``u`` / ``v`` -- wind components (kt) derived from ``wdir`` / ``wspd``.
    """

    __slots__ = ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "omeg",
                 "u", "v", "logp", "sfc", "meta")

    def __init__(self, pres, hght, tmpc, dwpc, wdir, wspd, omeg=None,
                 meta=None):
        self.pres = ma.masked_invalid(ma.asarray(pres, dtype=float))
        self.hght = ma.masked_invalid(ma.asarray(hght, dtype=float))
        self.tmpc = ma.masked_invalid(ma.asarray(tmpc, dtype=float))
        self.dwpc = ma.masked_invalid(ma.asarray(dwpc, dtype=float))
        self.wdir = ma.masked_invalid(ma.asarray(wdir, dtype=float))
        self.wspd = ma.masked_invalid(ma.asarray(wspd, dtype=float))
        self.omeg = None if omeg is None else \
            ma.masked_invalid(ma.asarray(omeg, dtype=float))
        self.meta = dict(meta) if meta else {}

        # Preserve any explicitly requested masks from the source arrays.
        for name, src in (("pres", pres), ("hght", hght), ("tmpc", tmpc),
                          ("dwpc", dwpc), ("wdir", wdir), ("wspd", wspd)):
            src_mask = ma.getmask(src)
            if src_mask is not ma.nomask:
                cur = getattr(self, name)
                cur.mask = ma.mask_or(ma.getmaskarray(cur), src_mask)

        self.sfc = 0
        self.logp = ma.log10(self.pres)
        # Meteorological wind-component convention: a wind *from* ``wdir`` at
        # ``wspd`` kt has components u = -wspd*sin(dir), v = -wspd*cos(dir).
        rad = np.deg2rad(self.wdir)
        self.u = -self.wspd * ma.sin(rad)
        self.v = -self.wspd * ma.cos(rad)

    # -- construction helpers -------------------------------------------------

    @property
    def nlevels(self) -> int:
        """Number of reported levels in the sounding."""
        return int(self.pres.size)

    def to_profile_kwargs(self) -> dict:
        """Return kwargs for ``sharppy.sharptab.profile.create_profile``.

        The arrays are returned filled with ``-9999.0`` at masked positions to
        match the ``missing=-9999.0`` sentinel that factory expects.
        """
        fill = -9999.0
        return {
            "pres": np.asarray(self.pres.filled(fill), dtype=float),
            "hght": np.asarray(self.hght.filled(fill), dtype=float),
            "tmpc": np.asarray(self.tmpc.filled(fill), dtype=float),
            "dwpc": np.asarray(self.dwpc.filled(fill), dtype=float),
            "wdir": np.asarray(self.wdir.filled(fill), dtype=float),
            "wspd": np.asarray(self.wspd.filled(fill), dtype=float),
            "missing": fill,
        }

    def to_npz_dict(self) -> dict:
        """Return the intermediate ``.npz`` point-sounding representation.

        Mirrors the arrays/metadata consumed by
        :func:`sharpmod.io.decoder.load_npz` (``pres, hght, tmpc, dwpc, wdir,
        wspd, omeg`` + ``valid, run, loc, lat, model``).
        """
        fill = -9999.0
        omeg = self.omeg if self.omeg is not None else ma.zeros(self.nlevels)
        out = {
            "pres": np.asarray(self.pres.filled(fill), dtype=float),
            "hght": np.asarray(self.hght.filled(fill), dtype=float),
            "tmpc": np.asarray(self.tmpc.filled(fill), dtype=float),
            "dwpc": np.asarray(self.dwpc.filled(fill), dtype=float),
            "wdir": np.asarray(self.wdir.filled(fill), dtype=float),
            "wspd": np.asarray(self.wspd.filled(fill), dtype=float),
            "omeg": np.asarray(omeg.filled(fill) if ma.isMaskedArray(omeg)
                               else omeg, dtype=float),
        }
        out.update({
            "valid": self.meta.get("valid", "2020-01-01 00:00"),
            "run": self.meta.get("run", "2020-01-01 00:00"),
            "loc": self.meta.get("loc", "TST"),
            "lat": self.meta.get("lat", 35.0),
            "model": self.meta.get("model", "TEST"),
        })
        return out

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"SoundingData(nlevels={self.nlevels}, "
                f"sfc_pres={float(self.pres[0]):.1f} hPa, "
                f"top_pres={float(self.pres[-1]):.1f} hPa)")


def _apply_field_masks(draw, arrays: dict, fields: Sequence[str]) -> None:
    """Mask a random (interior) subset of entries in each named field.

    At least two levels of every field are always left valid so the
    interpolation helpers still have usable data to bracket.
    """
    n = len(next(iter(arrays.values())))
    for name in fields:
        if name not in arrays:
            continue
        # Candidate interior indices (never mask the surface at index 0).
        candidates = list(range(1, n))
        if not candidates:
            continue
        max_mask = max(0, min(len(candidates), n - 2))
        if max_mask == 0:
            continue
        k = draw(st.integers(min_value=1, max_value=max_mask))
        idx = draw(st.lists(st.sampled_from(candidates), min_size=k,
                            max_size=k, unique=True))
        arr = ma.masked_invalid(ma.asarray(arrays[name], dtype=float))
        mask = ma.getmaskarray(arr).copy()
        for i in idx:
            mask[i] = True
        arr.mask = mask
        arrays[name] = arr


@st.composite
def profiles(
    draw,
    *,
    min_levels: int = 5,
    max_levels: int = 40,
    shallow_top: Optional[str] = None,
    surface_pressure: Optional[tuple] = None,
    include_omeg: bool = False,
    masked_fields: Union[bool, Iterable[str]] = False,
    zero_cape: bool = False,
    zero_dcape: bool = False,
    span_hgz: Optional[bool] = None,
) -> SoundingData:
    """Return a Hypothesis strategy producing physically plausible soundings.

    Parameters
    ----------
    min_levels, max_levels:
        Inclusive bounds on the number of reported levels.
    shallow_top:
        One of ``"500m"``, ``"1km"``, ``"6km"`` to force the profile top
        *below* that height AGL (exercising the missing-value path for layer
        parameters that need the depth), or ``None`` for a full-depth profile
        (top 6-16 km AGL).
    surface_pressure:
        ``(low, high)`` bounds (hPa) for the surface pressure; defaults to
        ``(950, 1050)``.
    include_omeg:
        When ``True``, attach a plausible vertical-velocity (OMEGA) column.
    masked_fields:
        ``True`` masks a random subset of entries across all core fields;
        an iterable of field names masks only those fields; ``False`` (default)
        leaves every level valid.
    zero_cape:
        When ``True``, bias toward a non-buoyant sounding (dry column: large
        dewpoint depressions) so surface-based CAPE is approximately zero.
        Approximate: CAPE depends on a parcel ascent not computed here.
    zero_dcape:
        When ``True``, bias toward a saturated column (small dewpoint
        depressions) so downdraft CAPE is approximately zero. Approximate for
        the same reason. If both ``zero_cape`` and ``zero_dcape`` are set,
        ``zero_cape`` (drying) takes precedence.
    span_hgz:
        ``True`` forces the temperature profile to span the -10 to -30 degrees C
        hail-growth-zone layer (surface warmer than -10, top colder than -30);
        ``False`` forces it to *not* span (all temperatures warmer than
        -10 degrees C); ``None`` leaves spanning to chance.

    Returns
    -------
    SoundingData
        A sounding whose invariants (monotonic pressure/height, ``T >= Td``,
        valid winds) always hold.
    """
    n = draw(st.integers(min_value=min_levels, max_value=max_levels))

    # --- vertical grid: heights (m AGL) and surface elevation ---------------
    if shallow_top is not None:
        if shallow_top not in SHALLOW_CEILINGS:
            raise ValueError(
                f"shallow_top must be one of {sorted(SHALLOW_CEILINGS)} or "
                f"None, got {shallow_top!r}")
        ceiling = SHALLOW_CEILINGS[shallow_top]
        # Stay strictly below the ceiling; keep a small floor for >=2 levels.
        floor = min(120.0, ceiling * 0.5)
        top_agl = draw(st.floats(min_value=floor, max_value=ceiling * 0.98))
    else:
        top_agl = draw(st.floats(min_value=6000.0, max_value=16000.0))

    sfc_elev = draw(st.floats(min_value=0.0, max_value=1500.0))
    base_agl = np.linspace(0.0, top_agl, n)  # strictly increasing (top_agl > 0)
    hght = sfc_elev + base_agl

    # --- pressure: hydrostatic, strictly decreasing -------------------------
    lo, hi = surface_pressure if surface_pressure else (950.0, 1050.0)
    p_sfc = draw(st.floats(min_value=lo, max_value=hi))
    scale_h = draw(st.floats(min_value=7000.0, max_value=8500.0))
    pres = p_sfc * np.exp(-base_agl / scale_h)

    # --- temperature: monotone decreasing, isotherm-span controllable -------
    if span_hgz is True:
        t_sfc = draw(st.floats(min_value=0.0, max_value=35.0))
        t_top = draw(st.floats(min_value=-60.0, max_value=-32.0))
    elif span_hgz is False:
        t_sfc = draw(st.floats(min_value=0.0, max_value=35.0))
        # Never reach -10: keep the top warmer than -9 and below the surface.
        t_top = draw(st.floats(min_value=-9.0, max_value=t_sfc - 1.0)) \
            if t_sfc - 1.0 > -9.0 else -9.0
    else:
        t_sfc = draw(st.floats(min_value=-15.0, max_value=40.0))
        lapse = draw(st.floats(min_value=4.0, max_value=9.5))  # deg C / km
        t_top = t_sfc - lapse * (top_agl / 1000.0)
    frac = base_agl / top_agl if top_agl > 0 else np.zeros(n)
    tmpc = t_sfc + (t_top - t_sfc) * frac

    # --- dewpoint: non-negative depression, drier/moister per flags ---------
    if zero_cape:
        dep_sfc = draw(st.floats(min_value=15.0, max_value=30.0))
        dep_slope = draw(st.floats(min_value=0.0, max_value=8.0))
    elif zero_dcape:
        dep_sfc = draw(st.floats(min_value=0.0, max_value=2.0))
        dep_slope = draw(st.floats(min_value=0.0, max_value=1.0))
    else:
        dep_sfc = draw(st.floats(min_value=0.0, max_value=20.0))
        dep_slope = draw(st.floats(min_value=0.0, max_value=6.0))
    depression = dep_sfc + dep_slope * (base_agl / 1000.0)
    depression = np.clip(depression, 0.0, None)
    dwpc = tmpc - depression  # guarantees T >= Td

    # --- winds: direction in [0, 360), speed >= 0 ---------------------------
    wdir = np.array(
        [draw(st.floats(min_value=0.0, max_value=359.999)) for _ in range(n)])
    wspd = np.array(
        [draw(st.floats(min_value=0.0, max_value=120.0)) for _ in range(n)])

    arrays = {
        "pres": pres, "hght": hght, "tmpc": tmpc,
        "dwpc": dwpc, "wdir": wdir, "wspd": wspd,
    }

    # --- optional masked fields ---------------------------------------------
    if masked_fields:
        if masked_fields is True:
            fields = list(CORE_FIELDS)
        else:
            fields = [f for f in masked_fields if f in CORE_FIELDS]
        _apply_field_masks(draw, arrays, fields)

    # --- optional OMEGA column ----------------------------------------------
    omeg = None
    if include_omeg:
        omeg = np.array(
            [draw(st.floats(min_value=-50.0, max_value=50.0))
             for _ in range(n)])

    return SoundingData(
        pres=arrays["pres"], hght=arrays["hght"], tmpc=arrays["tmpc"],
        dwpc=arrays["dwpc"], wdir=arrays["wdir"], wspd=arrays["wspd"],
        omeg=omeg,
        meta={"loc": "TST", "lat": float(draw(st.floats(-89.0, 89.0)))},
    )


# ---------------------------------------------------------------------------
# Hazard-classifier helpers (Requirement 9 / hazard.classify)
# ---------------------------------------------------------------------------
#
# ``hazard.classify`` accepts *either* a full SHARPpy Reimagined/SHARPpy convective
# profile that already exposes the convective parameters, *or* a bare Profile
# whose columns it lifts through the SHARPpy convective oracle. To exercise the
# pinned decision table directly -- independently of whether the heavyweight
# ascent oracle is available in a given environment -- these helpers build a
# minimal object that exposes exactly the six input parameters the classifier
# reads, under the SHARPpy convective-profile attribute names it looks for.

#: ``classification input -> the SHARPpy convective-profile attribute name the
#: classifier reads it from`` (the primary alias for each of the six inputs).
HAZARD_INPUT_ATTRS = {
    "mucape": "mucape",     # most-unstable CAPE (J/kg)
    "esrh": "right_esrh",   # effective-layer SRH (m^2/s^2)
    "ebwd": "ebwspd",       # effective bulk wind difference (kt)
    "stp": "stp_cin",       # Significant Tornado Parameter (effective, CIN-weighted)
    "scp": "right_scp",     # Supercell Composite Parameter (right-mover)
    "ship": "ship",         # Significant Hail Parameter
}

#: Physically plausible draw ranges (per input) wide enough to reach every
#: branch of the classifier's decision cascade.
HAZARD_INPUT_RANGES = {
    "mucape": (0.0, 6000.0),
    "esrh": (-300.0, 900.0),
    "ebwd": (0.0, 130.0),
    "stp": (-3.0, 12.0),
    "scp": (-3.0, 50.0),
    "ship": (0.0, 9.0),
}


class ParamProfile:
    """A minimal profile exposing the six hazard-classifier inputs directly.

    ``hazard.classify`` reads each input off the profile by attribute before it
    ever attempts to build the convective oracle, so an instance carrying the
    SHARPpy convective attribute names (``mucape``, ``right_esrh``, ``ebwspd``,
    ``stp_cin``, ``right_scp``, ``ship``) is classified purely by the pinned
    decision table.

    Only the inputs passed in ``values`` are set. An input mapped to ``None`` is
    left unset entirely (so the classifier's ``getattr`` fallbacks also resolve
    to ``None``), modelling a *missing* input; any other value (including
    ``numpy.ma.masked``) is assigned to the primary attribute, modelling a
    present or *masked* input.
    """

    def __init__(self, values: dict):
        for name, attr in HAZARD_INPUT_ATTRS.items():
            if name in values and values[name] is not None:
                setattr(self, attr, values[name])


@st.composite
def hazard_inputs(draw) -> dict:
    """Return a strategy producing the six hazard-classifier input values.

    Yields a ``{input_name: finite_float}`` mapping (keys are the logical input
    names in :data:`HAZARD_INPUT_ATTRS`) drawn from :data:`HAZARD_INPUT_RANGES`,
    wide enough to reach every branch of the classifier decision cascade.
    """
    return {
        name: draw(st.floats(min_value=lo, max_value=hi,
                             allow_nan=False, allow_infinity=False))
        for name, (lo, hi) in HAZARD_INPUT_RANGES.items()
    }

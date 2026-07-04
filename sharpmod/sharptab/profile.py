"""The SharpTab :class:`Profile` and its lazy, cached derived-attribute mechanism.

Successor to ``sharppy.sharptab.profile.Profile``. A :class:`Profile` holds the
core reported-level arrays of an analyzed sounding (``pres``, ``hght``, ``tmpc``,
``dwpc``, ``wdir``, ``wspd`` and the optional ``omeg`` / ``wetbulb`` columns)
together with the derived wind components (``u`` / ``v``), the log-pressure array
(``logp``) and the surface index (``sfc``) that the SharpTab computation helpers
read directly.

Design principles honoured here (SHARPpy Reimagined design.md, "Design Principles"):

* **Lazy, cached, idempotent attributes.** Every new derived parameter is exposed
  as a ``Profile`` attribute that is *computed on first access* by a
  ``__getattr__`` dispatch table, then *cached on the instance* so a second read
  returns the bitwise-identical cached value -- including when that value is the
  :data:`~sharpmod.sharptab.constants.MISSING` masked sentinel (Requirements
  13.1, 13.2).
* **Decoder-agnostic parameters.** The compute functions read *only* the
  ``Profile``-exposed arrays; they never branch on which decoder produced the
  ``Profile`` (Requirement 13.5). Two ``Profile`` objects carrying identical
  arrays therefore produce identical derived values regardless of provenance.
* **Missing data propagates, never crashes.** A compute function that cannot
  resolve its inputs returns :data:`MISSING`; in addition, any value that falls
  outside its documented physical range in :data:`PARAM_REGISTRY` is clamped to
  :data:`MISSING` before being cached and returned (Requirements 13.6, 14.6).
* **The renderer reads, it does not compute.** Display widgets read these values
  off the ``Profile`` and never recompute them (Requirement 13.3).

The lazily computed attributes registered here (all consuming only the
``Profile``-exposed arrays) are:

======================  ==================================================
Attribute               Computation
======================  ==================================================
``srh500``              :func:`winds.sfc_500m_kinematics` (tuple element 0)
``shear_sfc_500m``      :func:`winds.sfc_500m_kinematics` (tuple element 1)
``mean_wind_sfc_500m``  :func:`winds.sfc_500m_kinematics` (tuple element 2)
``srw_sfc_500m``        :func:`winds.sfc_500m_kinematics` (tuple element 3)
``dcp``                 :func:`derived.dcp`
``lapserate_sfc_1km``   :func:`params.lapse_rate` (0->1000 m AGL)
``ncape``               :func:`derived.normalized_cape_cin` (element 0)
``ncin``                :func:`derived.normalized_cape_cin` (element 1)
``ecape``               :func:`ecape.ecape`
``lrghail``             :func:`derived.large_hail_parameter`
``hpi``                 :func:`derived.hail_possibility_index`
``peskov``              :func:`derived.peskov_index`
``mcs_index``           :func:`derived.mcs_index`
``ehi_0_1km``           :func:`derived.ehi` (0-1 km layer)
``ehi_0_3km``           :func:`derived.ehi` (0-3 km layer)
``hgz_cape``            :func:`params.layer_cape_isotherm` (-10 to -30 C)
``cape_0_6km``          :func:`params.layer_cape_agl` (0->6000 m AGL)
======================  ==================================================
"""

from __future__ import annotations

import numpy as np
import numpy.ma as ma

from . import interp  # noqa: F401  (re-exported convenience for callers)
from . import winds
from . import params
from . import ecape as ecape_mod
from . import derived
from .constants import MISSING, is_missing, PARAM_REGISTRY

__all__ = ["Profile", "create_profile", "DERIVED_ATTRS"]


# ---------------------------------------------------------------------------
# Derived-attribute dispatch tables
# ---------------------------------------------------------------------------
#
# Two shapes of compute function are supported:
#
# * *single* -- the function returns one scalar (or the MISSING sentinel); the
#   attribute is that value.
# * *grouped* -- the function returns a tuple whose elements map one-to-one onto
#   a set of sibling attributes (e.g. ``sfc_500m_kinematics`` -> ``srh500`` /
#   ``shear_sfc_500m`` / ``mean_wind_sfc_500m`` / ``srw_sfc_500m``). Accessing
#   any sibling computes
#   the tuple once and caches *every* sibling, so the shared work happens only
#   once and the siblings stay mutually consistent.
#
# Each ``compute`` is a callable taking the ``Profile`` and reading only its
# exposed arrays (Requirement 13.5).

#: ``attr name -> compute(prof)`` for parameters computed independently.
_SINGLE_COMPUTE = {
    "dcp": lambda prof: derived.dcp(prof),
    "lapserate_sfc_1km": lambda prof: params.lapse_rate(prof, 0, 1000, agl=True),
    "ecape": lambda prof: ecape_mod.ecape(prof),
    "lrghail": lambda prof: derived.large_hail_parameter(prof),
    "hpi": lambda prof: derived.hail_possibility_index(prof),
    "peskov": lambda prof: derived.peskov_index(prof),
    "mcs_index": lambda prof: derived.mcs_index(prof),
    "ehi_0_1km": lambda prof: derived.ehi(prof, 1000),
    "ehi_0_3km": lambda prof: derived.ehi(prof, 3000),
    "hgz_cape": lambda prof: params.layer_cape_isotherm(prof, -10, -30),
    "cape_0_6km": lambda prof: params.layer_cape_agl(prof, 0, 6000),
}

#: ``group name -> (compute(prof), (attr, attr, ...))`` for tuple-valued
#: parameters. The attribute order matches the tuple element order returned by
#: the compute function.
_GROUP_COMPUTE = {
    "sfc_500m_kinematics": (
        lambda prof: winds.sfc_500m_kinematics(prof),
        ("srh500", "shear_sfc_500m", "mean_wind_sfc_500m", "srw_sfc_500m"),
    ),
    "normalized_cape_cin": (
        lambda prof: derived.normalized_cape_cin(prof),
        ("ncape", "ncin"),
    ),
}

#: ``attr name -> group name`` reverse lookup for the grouped parameters.
_ATTR_TO_GROUP = {
    attr: group
    for group, (_fn, attrs) in _GROUP_COMPUTE.items()
    for attr in attrs
}

#: The complete set of lazily computed derived attribute names.
DERIVED_ATTRS = frozenset(_SINGLE_COMPUTE) | frozenset(_ATTR_TO_GROUP)


# ---------------------------------------------------------------------------
# Physical-range clamp (Requirement 14.6)
# ---------------------------------------------------------------------------

def _clamp_to_range(name, value):
    """Return ``value`` unchanged, or :data:`MISSING` if it is out of range.

    Applies the documented ``phys_min`` / ``phys_max`` bounds from
    :data:`PARAM_REGISTRY` (Requirements 14.5, 14.6). Missing/masked inputs stay
    missing. Scalars outside their bounds (or non-finite) become
    :data:`MISSING`. Vector-valued parameters (the SFC-500 m mean wind, returned
    as a ``(u, v)`` knot tuple) are range-checked on their magnitude; an
    in-range vector is returned verbatim so the renderer keeps both components.
    """
    if is_missing(value):
        return MISSING

    spec = PARAM_REGISTRY.get(name)
    if spec is None:
        # No documented range -> pass through unchanged.
        return value

    # Vector-valued result (e.g. mean-wind (u, v)): clamp on the magnitude.
    if isinstance(value, (tuple, list)):
        try:
            magnitude = float(np.hypot(*value))
        except (TypeError, ValueError):
            return MISSING
        if not np.isfinite(magnitude):
            return MISSING
        if magnitude < spec.phys_min or magnitude > spec.phys_max:
            return MISSING
        return value

    try:
        fval = float(value)
    except (TypeError, ValueError):
        return MISSING
    if not np.isfinite(fval):
        return MISSING
    if fval < spec.phys_min or fval > spec.phys_max:
        return MISSING
    return fval


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

class Profile:
    """An in-memory sounding with lazily computed, cached derived attributes.

    Parameters
    ----------
    pres, hght, tmpc, dwpc, wdir, wspd:
        The six core reported-level arrays -- pressure (hPa), height (m MSL),
        temperature (degrees C), dewpoint (degrees C), wind direction (deg) and
        wind speed (kt). Stored as NumPy masked arrays with non-finite entries
        masked; any mask present on the source arrays is preserved.
    omeg:
        Optional vertical-velocity column (Pa/s, matching SHARPpy's convention;
        the skew-T OMEGA meter and omega read-out convert Pa/s internally).
    wetbulb:
        Optional wet-bulb temperature column (degrees C), consumed by the
        isotherm helpers when a wet-bulb isotherm is requested.
    meta:
        Optional metadata dict (location, valid time, ...).

    Notes
    -----
    The core arrays plus the derived ``u`` / ``v`` (kt), ``logp``
    (``log10(pres)``) and ``sfc`` (surface index, ``0``) are stored as ordinary
    instance attributes, so the SharpTab helpers can read them directly without
    triggering the lazy machinery. Every *derived* parameter in
    :data:`DERIVED_ATTRS` is resolved on first access by :meth:`__getattr__`,
    clamped to its documented physical range, and cached *as an instance
    attribute* so subsequent reads short-circuit the lazy machinery and are
    idempotent (Requirements 13.1, 13.2, 14.6).
    """

    def __init__(self, pres, hght, tmpc, dwpc, wdir, wspd, omeg=None,
                 wetbulb=None, meta=None):
        self.pres = ma.masked_invalid(ma.asarray(pres, dtype=float))
        self.hght = ma.masked_invalid(ma.asarray(hght, dtype=float))
        self.tmpc = ma.masked_invalid(ma.asarray(tmpc, dtype=float))
        self.dwpc = ma.masked_invalid(ma.asarray(dwpc, dtype=float))
        self.wdir = ma.masked_invalid(ma.asarray(wdir, dtype=float))
        self.wspd = ma.masked_invalid(ma.asarray(wspd, dtype=float))
        self.omeg = None if omeg is None else \
            ma.masked_invalid(ma.asarray(omeg, dtype=float))
        self.wetbulb = None if wetbulb is None else \
            ma.masked_invalid(ma.asarray(wetbulb, dtype=float))
        self.meta = dict(meta) if meta else {}

        # Preserve any explicitly requested masks from the source arrays.
        for name, src in (("pres", pres), ("hght", hght), ("tmpc", tmpc),
                          ("dwpc", dwpc), ("wdir", wdir), ("wspd", wspd)):
            src_mask = ma.getmask(src)
            if src_mask is not ma.nomask:
                cur = getattr(self, name)
                cur.mask = ma.mask_or(ma.getmaskarray(cur), src_mask)

        # Surface is the first (highest-pressure) level.
        self.sfc = 0
        self.logp = ma.log10(self.pres)
        # Meteorological wind-component convention: a wind *from* ``wdir`` at
        # ``wspd`` kt has components u = -wspd*sin(dir), v = -wspd*cos(dir).
        rad = np.deg2rad(self.wdir)
        self.u = -self.wspd * ma.sin(rad)
        self.v = -self.wspd * ma.cos(rad)

    # -- lazy derived-attribute mechanism ------------------------------------

    def __getattr__(self, name):
        """Resolve a derived attribute on first access, then cache it.

        Invoked by Python only when ``name`` is not already a stored instance
        attribute. For a registered derived parameter the value is computed
        (once), clamped to its documented physical range, cached as an instance
        attribute, and returned; because the value is now stored, a second read
        never re-enters this method and returns the identical cached value
        (Requirements 13.1, 13.2, 14.6). Any other name raises
        :class:`AttributeError` so ``getattr(prof, x, default)`` probes elsewhere
        in the code (e.g. the optional ``srwind`` / ``bunkers`` / ``wetbulb``
        lookups) behave normally.
        """
        # Never intercept private/dunder lookups (this also avoids recursing
        # into ``__getattr__`` before ``__init__`` has populated the instance,
        # e.g. during copy/pickle probes for ``__setstate__``).
        if name.startswith("_"):
            raise AttributeError(name)

        if name in _SINGLE_COMPUTE:
            return self._compute_single(name)

        if name in _ATTR_TO_GROUP:
            self._compute_group(_ATTR_TO_GROUP[name])
            return self.__dict__[name]

        raise AttributeError(
            f"{type(self).__name__!r} object has no attribute {name!r}")

    def _compute_single(self, name):
        """Compute, clamp, and cache a single-valued derived parameter."""
        try:
            raw = _SINGLE_COMPUTE[name](self)
        except Exception:
            # Design principle: missing data propagates, never crashes.
            raw = MISSING
        value = _clamp_to_range(name, raw)
        # Cache as an instance attribute so subsequent reads skip __getattr__.
        self.__dict__[name] = value
        return value

    def _compute_group(self, group):
        """Compute, clamp, and cache every sibling of a tuple-valued group."""
        compute, attrs = _GROUP_COMPUTE[group]
        try:
            result = compute(self)
        except Exception:
            result = None

        if not isinstance(result, (tuple, list)) or len(result) != len(attrs):
            # Degenerate/failed result -> every sibling is MISSING.
            for attr in attrs:
                self.__dict__[attr] = MISSING
            return

        for attr, element in zip(attrs, result):
            self.__dict__[attr] = _clamp_to_range(attr, element)

    # -- convenience ---------------------------------------------------------

    @property
    def nlevels(self) -> int:
        """Number of reported levels in the sounding."""
        return int(self.pres.size)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        try:
            return (f"Profile(nlevels={self.nlevels}, "
                    f"sfc_pres={float(self.pres[0]):.1f} hPa, "
                    f"top_pres={float(self.pres[-1]):.1f} hPa)")
        except Exception:
            return f"Profile(nlevels={self.pres.size})"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_profile(pres, hght, tmpc, dwpc, wdir, wspd, omeg=None,
                   wetbulb=None, meta=None, **kwargs):
    """Construct a :class:`Profile` from the core reported-level arrays.

    Mirrors the ``sharppy.sharptab.profile.create_profile`` calling convention
    for the columns SHARPpy Reimagined needs. Extra keyword arguments (e.g. a ``profile``
    kind or ``missing`` sentinel accepted by the legacy factory) are ignored, so
    the same call site can target either factory.

    Parameters
    ----------
    pres, hght, tmpc, dwpc, wdir, wspd:
        The six core reported-level arrays (see :class:`Profile`).
    omeg, wetbulb, meta:
        Optional OMEGA / wet-bulb columns and metadata dict.

    Returns
    -------
    Profile
        A profile whose derived attributes compute lazily on first access.
    """
    return Profile(
        pres=pres, hght=hght, tmpc=tmpc, dwpc=dwpc, wdir=wdir, wspd=wspd,
        omeg=omeg, wetbulb=wetbulb, meta=meta,
    )

"""Shared primitives for SharpTab: the ``MISSING`` sentinel, physical constants,
and the derived-parameter registry.

Design principle (SHARPpy Reimagined design.md, "Design Principles"):

    *Missing data propagates, never crashes.* Every computation returns a
    masked/missing value (a NumPy masked constant) rather than raising when its
    inputs are absent or out of physical range.

Accordingly, :data:`MISSING` is ``numpy.ma.masked`` -- the canonical NumPy masked
constant -- **not** a legacy numeric flag such as ``-9999``. Use :func:`is_missing`
to test for it.

The :class:`ParamSpec` registry (:data:`PARAM_REGISTRY`) documents, for every
derived parameter, its formula, literature reference, input/output units, physical
range, and validation tolerance. This satisfies Requirement 14 (Scientific
Correctness and Verifiability):

* 14.1 -- each parameter records its formula expression and a literature reference
  (author, title, year) sufficient to locate the source.
* 14.3 -- each parameter records a validation tolerance as a numeric value in the
  parameter's output units (:attr:`ParamSpec.tolerance`). Where the design specifies
  a relative-vs-absolute ``max(rel%, abs)`` tolerance, the relative component is kept
  in :data:`RELATIVE_TOLERANCE` and the absolute component in ``tolerance``.
* 14.4 -- each parameter records both its input units and output units.
* 14.5 -- each parameter that can return a missing value records a physical minimum
  and maximum bound in its output units (:attr:`ParamSpec.phys_min` /
  :attr:`ParamSpec.phys_max`), enabling the 14.6 out-of-range -> MISSING clamp.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict

import numpy as np

__all__ = [
    "MISSING",
    "is_missing",
    "ZEROCNK",
    "G",
    "RD",
    "RV",
    "CP",
    "CV",
    "LV",
    "ROCP",
    "EPS",
    "P0",
    "KTS_PER_MS",
    "TOL",
    "ParamSpec",
    "PARAM_REGISTRY",
    "RELATIVE_TOLERANCE",
    "get_spec",
]


# ---------------------------------------------------------------------------
# Missing-value sentinel
# ---------------------------------------------------------------------------

#: Canonical missing-value indicator for every SharpTab computation.
#:
#: This is the NumPy masked constant (``numpy.ma.masked``). Derived-parameter
#: functions return ``MISSING`` -- never raise -- when required inputs are absent
#: or masked, or when a computed value falls outside its documented physical range
#: (Requirements 13.6, 14.6). Reads are idempotent: ``MISSING`` is a singleton, so a
#: masked result compares identically on every access (Requirement 13.2).
MISSING = np.ma.masked


def is_missing(value) -> bool:
    """Return ``True`` if ``value`` is the :data:`MISSING` sentinel or otherwise
    masked/undefined.

    Recognizes the NumPy masked constant, masked scalars/arrays whose mask is
    fully set, and ``NaN`` floats. This is the single supported way to test a
    derived-parameter result for missingness so callers never branch on a legacy
    numeric flag.
    """
    if value is MISSING or value is np.ma.masked:
        return True
    # Masked arrays / masked scalars: missing when the whole mask is set.
    mask = np.ma.getmask(value)
    if mask is not np.ma.nomask and np.all(mask):
        return True
    # Plain NaN (e.g. a float that fell through a computation).
    try:
        return bool(np.all(np.isnan(value)))
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Physical constants (SI unless noted); values follow standard meteorological usage
# ---------------------------------------------------------------------------

#: Celsius <-> Kelvin offset (K).
ZEROCNK = 273.15
#: Standard gravitational acceleration (m s^-2).
G = 9.80665
#: Specific gas constant for dry air (J kg^-1 K^-1).
RD = 287.04
#: Specific gas constant for water vapor (J kg^-1 K^-1).
RV = 461.5
#: Specific heat of dry air at constant pressure (J kg^-1 K^-1).
CP = 1005.7
#: Specific heat of dry air at constant volume (J kg^-1 K^-1).
CV = 718.0
#: Latent heat of vaporization at 0 degrees C (J kg^-1).
LV = 2.501e6
#: Poisson constant for dry air, RD / CP (dimensionless).
ROCP = RD / CP
#: Ratio of gas constants, RD / RV (dimensionless).
EPS = RD / RV
#: Reference pressure for potential-temperature computations (hPa).
P0 = 1000.0
#: Knots per metre-per-second conversion factor.
KTS_PER_MS = 1.9438444924406046
#: Numerical tolerance used to guard degenerate layers / division by zero.
TOL = 1e-10


# ---------------------------------------------------------------------------
# Derived-parameter registry (Requirement 14)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParamSpec:
    """Documented specification for a single derived parameter.

    Attributes
    ----------
    name:
        The ``Profile`` attribute name the parameter is exposed as.
    label:
        Human-readable display label used by the renderer's index tables.
    input_units:
        Units of the input terms the formula consumes (Requirement 14.4).
    output_units:
        Units of the returned value (Requirement 14.4).
    phys_min:
        Documented physical minimum bound, in ``output_units`` (Requirement 14.5).
        Values below this are returned as :data:`MISSING` (Requirement 14.6).
    phys_max:
        Documented physical maximum bound, in ``output_units`` (Requirement 14.5).
        Values above this are returned as :data:`MISSING` (Requirement 14.6).
    tolerance:
        Validation tolerance as a numeric value in ``output_units``
        (Requirement 14.3). Where the design specifies ``max(rel%, abs)``, this is
        the absolute component; see :data:`RELATIVE_TOLERANCE` for the relative part.
    reference:
        Literature reference (author, title, year) sufficient to locate the source
        (Requirement 14.1).
    formula:
        The formula expression (Requirement 14.1).
    """

    name: str
    label: str
    input_units: str
    output_units: str
    phys_min: float
    phys_max: float
    tolerance: float
    reference: str
    formula: str


#: Relative validation tolerances (as a fraction, e.g. ``0.01`` == 1%) for the
#: parameters whose design tolerance is ``max(rel%, absolute)``. The effective
#: tolerance for such a parameter is ``max(RELATIVE_TOLERANCE[name] * |reference|,
#: PARAM_REGISTRY[name].tolerance)``. Parameters absent from this map use only the
#: absolute :attr:`ParamSpec.tolerance` (e.g. SFC-1km lapse rate: 0.1 degrees C/km).
RELATIVE_TOLERANCE: Dict[str, float] = {
    "srh500": 0.01,
    "shear_sfc_500m": 0.01,
    "mean_wind_sfc_500m": 0.01,
    "srw_sfc_500m": 0.01,
    "dcp": 0.01,
    "ncape": 0.01,
    "ncin": 0.01,
    "ecape": 0.05,
    "lrghail": 0.01,
    "hpi": 0.01,
    "peskov": 0.01,
    "mcs_index": 0.01,
    "ehi_0_1km": 0.01,
    "ehi_0_3km": 0.01,
    "hgz_cape": 0.01,
    "cape_0_6km": 0.01,
}


#: The documented specification for every derived parameter, keyed by ``Profile``
#: attribute name. Populated directly from the design.md Data Models
#: "Parameter Registry" table.
#:
#: NOTE: HPI, the Peskov index, and the MCS index have less-standardized published
#: formulas than the SPC/AMS parameters. Per the design, their reference formula,
#: thresholds, physical range, and absolute tolerance MUST be pinned against the
#: cited source before their implementation task (7.6) is complete. The entries
#: below reserve their structure with provisional physical bounds (flagged in each
#: ``formula`` string) and MUST be revisited when the sources are pinned.
PARAM_REGISTRY: Dict[str, ParamSpec] = {
    "srh500": ParamSpec(
        name="srh500",
        label="SFC-500m SRH",
        input_units="kt, m",
        output_units="m^2/s^2",
        phys_min=-2000.0,
        phys_max=2000.0,
        tolerance=1.0,
        reference="Bunkers et al. (2000), Predicting supercell motion using a new "
        "hodograph technique, Wea. Forecasting 15",
        formula="streamwise-vorticity integral SFC->500 m AGL, Bunkers storm motion",
    ),
    "shear_sfc_500m": ParamSpec(
        name="shear_sfc_500m",
        label="SFC-500m Shear",
        input_units="kt",
        output_units="kt",
        phys_min=0.0,
        phys_max=200.0,
        tolerance=0.5,
        reference="standard bulk-shear definition",
        formula="|V(500 m AGL) - V(sfc)|",
    ),
    "mean_wind_sfc_500m": ParamSpec(
        name="mean_wind_sfc_500m",
        label="SFC-500m Mean Wind",
        input_units="kt, hPa",
        output_units="kt",
        phys_min=0.0,
        phys_max=250.0,
        tolerance=0.5,
        reference="standard pressure-weighted mean-wind definition",
        formula="pressure-weighted mean wind SFC->500 m AGL",
    ),
    "srw_sfc_500m": ParamSpec(
        name="srw_sfc_500m",
        label="SFC-500m SR Wind",
        input_units="kt, hPa",
        output_units="kt",
        phys_min=0.0,
        phys_max=250.0,
        tolerance=0.5,
        reference="standard storm-relative mean-wind definition",
        formula="pressure-weighted mean wind SFC->500 m AGL minus Bunkers "
        "right-mover storm motion",
    ),
    "dcp": ParamSpec(
        name="dcp",
        label="DCP",
        input_units="J/kg, kt",
        output_units="unitless",
        phys_min=0.0,
        phys_max=50.0,
        tolerance=0.05,
        reference="Evans & Doswell (2001), Examining derecho environments using "
        "proximity soundings, Wea. Forecasting 16, 329-342",
        formula="(DCAPE/980)*(MUCAPE/2000)*(shear_0_6km/20)*(mean_wind_0_6km/16)",
    ),
    "lapserate_sfc_1km": ParamSpec(
        name="lapserate_sfc_1km",
        label="SFC-1km Lapse Rate",
        input_units="degrees C, m",
        output_units="degrees C/km",
        phys_min=-5.0,
        phys_max=30.0,
        tolerance=0.1,
        reference="standard lapse-rate definition",
        formula="(T_sfc - T_1km) / 1.0 km",
    ),
    "ncape": ParamSpec(
        name="ncape",
        label="NCAPE",
        input_units="J/kg, m",
        output_units="J/kg/m",
        phys_min=0.0,
        phys_max=1.0,
        tolerance=0.01,
        reference="Blanchard (1998), Assessing the vertical distribution of "
        "convective available potential energy, Wea. Forecasting 13, 870-877",
        formula="MUCAPE / (EL - LFC depth, m)",
    ),
    "ncin": ParamSpec(
        name="ncin",
        label="NCIN",
        input_units="J/kg, m",
        output_units="J/kg/m",
        phys_min=-1.0,
        phys_max=0.0,
        tolerance=0.01,
        reference="Blanchard (1998), Assessing the vertical distribution of "
        "convective available potential energy, Wea. Forecasting 13, 870-877",
        formula="CIN / (LFC - MU-parcel-start depth, m)",
    ),
    "ecape": ParamSpec(
        name="ecape",
        label="ECAPE",
        input_units="hPa, degrees C, kt",
        output_units="J/kg",
        phys_min=0.0,
        phys_max=10000.0,
        tolerance=10.0,
        reference="Peters et al. (2023), An analytic formula for entraining CAPE in "
        "mid-latitude storm environments, arXiv:2301.04712 / J. Atmos. Sci.",
        formula="analytic entraining CAPE (Peters et al. 2023 ECAPE_FUNCTIONS)",
    ),
    "lrghail": ParamSpec(
        name="lrghail",
        label="LRGHAIL",
        input_units="J/kg, kt, degrees C",
        output_units="unitless",
        phys_min=0.0,
        phys_max=20.0,
        tolerance=0.05,
        reference="SPC Mesoanalysis Large Hail Parameter (help_lghl)",
        formula="SPC Large Hail Parameter composite",
    ),
    "hpi": ParamSpec(
        name="hpi",
        label="HPI",
        # PINNED (task 7.6): non-severe hail-sizing index, distinct from LRGHAIL
        # and SHIP (Requirement 6.3). Driven by hail-growth-zone CAPE (J/kg) and
        # the wet-bulb-zero melting height (m AGL).
        input_units="J/kg, m",
        output_units="unitless",
        phys_min=0.0,
        phys_max=25.0,
        tolerance=0.05,
        reference="Fawbush & Miller (1953), A method for forecasting hailstone size "
        "at the earth's surface, Bull. Amer. Meteor. Soc. 34, 235-244; Miller (1972), "
        "AWS TR-200 wet-bulb-zero hail-sizing technique",
        formula="(HGZ_CAPE / 500) * clip(1 - max(0, WBZ_AGL - 3350)/3350, 0, 1); "
        "HGZ_CAPE = CAPE over the -10 to -30 degrees C layer; distinct from LRGHAIL "
        "and SHIP (Req 6.3)",
    ),
    "peskov": ParamSpec(
        name="peskov",
        label="Peskov Index",
        # PINNED (task 7.6): documented thunderstorm-likelihood composite. An
        # authoritative independently reproducible published formula for the
        # historical Peskov index could not be confirmed; per the design this
        # pins a documented instability-energy + mid-level-moisture composite and
        # cites the chosen source.
        input_units="degrees C, J/kg",
        output_units="index",
        phys_min=-60.0,
        phys_max=60.0,
        tolerance=0.1,
        reference="Documented instability-index thunderstorm-forecast methodology "
        "(cf. review of middle-troposphere instability indices vs. thunderstorm "
        "activity, Russian Meteorology and Hydrology 39(5), 2014); George (1960) "
        "K-index. Authoritative historical Peskov coefficients unconfirmed; "
        "documented surrogate per design.",
        formula="K_index + (SBCAPE / 1000) - (DD700 / 5), all terms from the same "
        "Profile",
    ),
    "mcs_index": ParamSpec(
        name="mcs_index",
        label="MCS Index",
        # PINNED (task 7.6): the Coniglio et al. (2006) MMP logistic-regression
        # linear predictor -- a distinct exposed attribute from sharppy's mmp
        # probability, with MMP = 1 / (1 + exp(MCS_index)).
        input_units="m/s, degrees C/km, J/kg",
        output_units="index (logit of MMP)",
        phys_min=-20.0,
        phys_max=20.0,
        tolerance=0.1,
        reference="Coniglio, Stensrud & Wicker (2006), Evaluation of maintenance "
        "probability of mesoscale convective systems, Wea. Forecasting 21, 577-592",
        formula="a0 + a1*max_bulk_shear + a2*lr38 + a3*MUCAPE + a4*mnwind_3_12 "
        "(a0=13.0, a1=-4.59e-2, a2=-1.16, a3=-6.17e-4, a4=-0.17); "
        "MMP = 1/(1+exp(MCS_index))",
    ),
    "ehi_0_1km": ParamSpec(
        name="ehi_0_1km",
        label="EHI 0-1km",
        input_units="J/kg, m^2/s^2",
        output_units="unitless",
        phys_min=-50.0,
        phys_max=50.0,
        tolerance=0.05,
        reference="Hart & Korotky / SPC Energy Helicity Index",
        formula="(CAPE * SRH_0_1km) / 160000",
    ),
    "ehi_0_3km": ParamSpec(
        name="ehi_0_3km",
        label="EHI 0-3km",
        input_units="J/kg, m^2/s^2",
        output_units="unitless",
        phys_min=-50.0,
        phys_max=50.0,
        tolerance=0.05,
        reference="Hart & Korotky / SPC Energy Helicity Index",
        formula="(CAPE * SRH_0_3km) / 160000",
    ),
    "hgz_cape": ParamSpec(
        name="hgz_cape",
        label="HGZ CAPE",
        input_units="hPa, degrees C",
        output_units="J/kg",
        phys_min=0.0,
        phys_max=10000.0,
        tolerance=10.0,
        reference="standard hail-growth-zone CAPE (-10 to -30 degrees C layer)",
        formula="CAPE integrated over the -10 degrees C to -30 degrees C layer",
    ),
    "cape_0_6km": ParamSpec(
        name="cape_0_6km",
        label="6CAPE",
        input_units="hPa, degrees C, m",
        output_units="J/kg",
        phys_min=0.0,
        phys_max=10000.0,
        tolerance=10.0,
        reference="standard 0-6 km AGL CAPE",
        formula="CAPE integrated over the SFC->6 km AGL layer",
    ),
}


def get_spec(name: str) -> ParamSpec:
    """Return the :class:`ParamSpec` for the derived-parameter attribute ``name``.

    Raises
    ------
    KeyError
        If ``name`` is not a registered derived parameter.
    """
    return PARAM_REGISTRY[name]

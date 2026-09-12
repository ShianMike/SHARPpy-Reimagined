"""Derived-parameter fields across a box of point soundings.

This turns the ``.npz`` files produced for a :class:`~sharpmod.box_sounding.\
BoxSamplePlan` into named scalar fields that can be mapped, ranked, and sliced.

Work is split into two tiers because their costs differ by more than two orders
of magnitude. Measured end to end (decode included) on this repository's own
example sounding with the native backend active:

``FAST_TIER``
    Everything the :mod:`sharpmod.backends` contract already returns --
    parcels, effective inflow, kinematics, DCAPE. About 2.3 ms per node, so a
    full 256-node box resolves in well under a second.

``COMPOSITE_TIER``
    The SPC composite indices (STP, SCP, SHIP, ...) and the AMS-literature
    parameters in :mod:`sharpmod.sharptab.derived`. These require the upstream
    SHARPpy ``ConvectiveProfile`` oracle, which eagerly computes its whole
    parameter surface in pure Python: about 424 ms per node, or roughly two
    minutes for a 256-node box.

The tiers are separate so the common case -- draw a box, look at a CAPE or
shear gradient -- is instant, and the expensive composites are only paid for
when asked for. They are *not* recomputed through a second, faster parcel path:
the composites come from the same cached oracle the rest of the application
reads, so a box field and a Skew-T opened from that same cell cannot disagree.

The complete fast tier is submitted through the backend's bounded batch path;
small boxes stay serial and larger boxes use its measured worker cap. The
composite tier remains serial because it is GIL-bound pure Python: measured on
16 nodes it got *slower* with more threads (0.370 s/node at one thread,
0.460 s/node at eight). Composite progress is therefore still streamed per
node.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import os
from types import SimpleNamespace
from typing import Callable, Iterable, Mapping, Sequence

import numpy as np

from sharpmod.box_sounding import BoxSamplePlan, BoxSamplePoint


#: Tier names. ``FAST_TIER`` is always computed; ``COMPOSITE_TIER`` is opt-in.
FAST_TIER = "fast"
COMPOSITE_TIER = "composite"
TIERS = (FAST_TIER, COMPOSITE_TIER)

#: Surface-relative layer tops requested from the kinematics backend, in metres
#: AGL. These are the layers the registry below names.
KINEMATIC_LAYER_TOPS = (1000.0, 3000.0, 6000.0, 8000.0)

#: Values at or below this are the project's missing sentinel, not data.
_MISSING_LIMIT = -9998.0

#: Default pressure ladder for vertical transects, in hPa. Coarse on purpose: a
#: cross-section is read as a shape, and every extra level costs an
#: interpolation per node.
DEFAULT_TRANSECT_LEVELS = tuple(float(value) for value in range(1000, 99, -25))

#: Raw profile columns a vertical transect can slice.
TRANSECT_FIELDS = ("tmpc", "dwpc", "wspd", "wdir", "omeg")

#: :data:`DEFAULT_TRANSECT_LEVELS` in log-pressure, built once. Interpolation is
#: linear in log-pressure -- the way a sounding is read -- and this is also the
#: key that decides whether a caller can use the columns cached during analysis
#: or has to re-read the file for a ladder of its own.
_DEFAULT_LOG_LADDER = np.log10(np.asarray(DEFAULT_TRANSECT_LEVELS, dtype=float))


class BoxAnalysisError(Exception):
    """A box analysis cannot be produced or queried as asked."""


@dataclass(frozen=True)
class BoxParameter:
    """Metadata for one mappable scalar field.

    Extraction lives in the tier extractors rather than on this class so the
    registry stays pure data: it can be serialized into a session, compared,
    and shown in a table without carrying closures.
    """

    key: str
    label: str
    units: str
    tier: str
    group: str
    #: Which end of the range is meteorologically notable. ``"high"`` ranks
    #: descending; ``"low"`` ranks ascending. This is what makes "show me the
    #: worst cell in this box" meaningful for CIN and LCL height, where the
    #: significant extreme is the small one.
    notable: str = "high"
    decimals: int = 0

    def format(self, value) -> str:
        """Render one value for a readout, or an em dash when absent."""
        if value is None:
            return "\u2014"
        return f"{float(value):.{self.decimals}f}"


def _p(key, label, units, tier, group, notable="high", decimals=0):
    return BoxParameter(
        key=key,
        label=label,
        units=units,
        tier=tier,
        group=group,
        notable=notable,
        decimals=decimals,
    )


#: Ordered registry of every field a box can display. Order is presentation
#: order: instability, then kinematics, then composites, then thermodynamics.
PARAMETERS: tuple[BoxParameter, ...] = (
    # -- Instability (fast tier) ----------------------------------------- #
    _p("sbcape", "SBCAPE", "J/kg", FAST_TIER, "Instability"),
    _p("sbcin", "SBCIN", "J/kg", FAST_TIER, "Instability", notable="low"),
    _p("mlcape", "MLCAPE", "J/kg", FAST_TIER, "Instability"),
    _p("mlcin", "MLCIN", "J/kg", FAST_TIER, "Instability", notable="low"),
    _p("mucape", "MUCAPE", "J/kg", FAST_TIER, "Instability"),
    _p("mucin", "MUCIN", "J/kg", FAST_TIER, "Instability", notable="low"),
    _p("mucape_3km", "MUCAPE 0-3 km", "J/kg", FAST_TIER, "Instability"),
    _p("mucape_6km", "MUCAPE 0-6 km", "J/kg", FAST_TIER, "Instability"),
    _p("eff_cape", "Effective CAPE", "J/kg", FAST_TIER, "Instability"),
    _p("eff_cin", "Effective CIN", "J/kg", FAST_TIER, "Instability", notable="low"),
    _p("dcape", "DCAPE", "J/kg", FAST_TIER, "Instability"),
    _p("downrush_t", "Downrush T", "C", FAST_TIER, "Instability", decimals=1),
    # -- Parcel heights (fast tier) -------------------------------------- #
    _p("ml_lcl", "ML LCL", "m AGL", FAST_TIER, "Parcel heights", notable="low"),
    _p("ml_lfc", "ML LFC", "m AGL", FAST_TIER, "Parcel heights", notable="low"),
    _p("mu_el", "MU EL", "m AGL", FAST_TIER, "Parcel heights"),
    _p("sb_lcl", "SB LCL", "m AGL", FAST_TIER, "Parcel heights", notable="low"),
    _p("eff_inflow_base", "Eff inflow base", "hPa", FAST_TIER, "Parcel heights"),
    _p(
        "eff_inflow_top",
        "Eff inflow top",
        "hPa",
        FAST_TIER,
        "Parcel heights",
        notable="low",
    ),
    # -- Kinematics (fast tier) ------------------------------------------ #
    _p("shear_1km", "0-1 km shear", "kt", FAST_TIER, "Kinematics"),
    _p("shear_3km", "0-3 km shear", "kt", FAST_TIER, "Kinematics"),
    _p("shear_6km", "0-6 km shear", "kt", FAST_TIER, "Kinematics"),
    _p("shear_8km", "0-8 km shear", "kt", FAST_TIER, "Kinematics"),
    _p("srh_1km", "0-1 km SRH", "m2/s2", FAST_TIER, "Kinematics"),
    _p("srh_3km", "0-3 km SRH", "m2/s2", FAST_TIER, "Kinematics"),
    _p("mean_wind_6km", "0-6 km mean wind", "kt", FAST_TIER, "Kinematics"),
    _p("storm_motion_r", "Bunkers right", "kt", FAST_TIER, "Kinematics", decimals=1),
    # -- SPC composites (composite tier) --------------------------------- #
    _p("stp_cin", "STP (CIN)", "", COMPOSITE_TIER, "Composites", decimals=2),
    _p("stp_fixed", "STP (fixed)", "", COMPOSITE_TIER, "Composites", decimals=2),
    _p("scp", "Supercell composite", "", COMPOSITE_TIER, "Composites", decimals=2),
    _p("ship", "SHIP", "", COMPOSITE_TIER, "Composites", decimals=2),
    _p("sig_severe", "Sig severe", "m3/s3", COMPOSITE_TIER, "Composites"),
    _p("mmp", "MCS maintenance", "", COMPOSITE_TIER, "Composites", decimals=2),
    _p("wndg", "Wind damage", "", COMPOSITE_TIER, "Composites", decimals=2),
    _p("esp", "Enhanced stretching", "", COMPOSITE_TIER, "Composites", decimals=2),
    _p("sherbe", "SHERBE", "", COMPOSITE_TIER, "Composites", decimals=2),
    _p("esrh", "Effective SRH", "m2/s2", COMPOSITE_TIER, "Composites"),
    _p("ebwd", "Effective shear", "kt", COMPOSITE_TIER, "Composites"),
    _p("critical_angle", "Critical angle", "deg", COMPOSITE_TIER, "Composites"),
    _p("lhp", "Large hail parameter", "", COMPOSITE_TIER, "Composites", decimals=2),
    _p("hpi", "Hail possibility index", "", COMPOSITE_TIER, "Composites", decimals=2),
    _p("peskov", "Peskov index", "", COMPOSITE_TIER, "Composites", decimals=1),
    _p("mcs_index", "MCS index", "", COMPOSITE_TIER, "Composites", decimals=2),
    _p("left_scp", "Left-moving SCP", "", COMPOSITE_TIER, "Composites", decimals=2),
    _p("dcp", "Derecho composite", "", COMPOSITE_TIER, "Composites", decimals=2),
    _p("ehi_1km", "0-1 km EHI", "", COMPOSITE_TIER, "Composites", decimals=2),
    _p("ehi_3km", "0-3 km EHI", "", COMPOSITE_TIER, "Composites", decimals=2),
    _p("vgp", "Vorticity generation", "", COMPOSITE_TIER, "Composites", decimals=2),
    _p("nst", "Non-supercell tornado", "", COMPOSITE_TIER, "Composites", decimals=2),
    # -- Thermodynamics / moisture (composite tier) ---------------------- #
    _p(
        "pwat", "Precipitable water", "in", COMPOSITE_TIER, "Thermodynamics", decimals=2
    ),
    _p(
        "mean_mixr",
        "Mean mixing ratio",
        "g/kg",
        COMPOSITE_TIER,
        "Thermodynamics",
        decimals=1,
    ),
    _p("low_rh", "Low-level RH", "%", COMPOSITE_TIER, "Thermodynamics"),
    _p("mid_rh", "Mid-level RH", "%", COMPOSITE_TIER, "Thermodynamics"),
    _p(
        "lapse_3km",
        "0-3 km lapse rate",
        "C/km",
        COMPOSITE_TIER,
        "Thermodynamics",
        decimals=1,
    ),
    _p(
        "lapse_700_500",
        "700-500 mb lapse rate",
        "C/km",
        COMPOSITE_TIER,
        "Thermodynamics",
        decimals=1,
    ),
    _p("k_index", "K index", "", COMPOSITE_TIER, "Thermodynamics"),
    _p("totals_totals", "Total totals", "", COMPOSITE_TIER, "Thermodynamics"),
    _p("wbz", "Wet-bulb zero", "m", COMPOSITE_TIER, "Thermodynamics"),
    _p("conv_t", "Convective temp", "F", COMPOSITE_TIER, "Thermodynamics"),
    _p("max_t", "Max temp", "F", COMPOSITE_TIER, "Thermodynamics"),
)

PARAMETERS_BY_KEY: Mapping[str, BoxParameter] = {
    parameter.key: parameter for parameter in PARAMETERS
}


def parameter(key) -> BoxParameter:
    """Return the registry entry for ``key``."""
    try:
        return PARAMETERS_BY_KEY[str(key)]
    except KeyError as exc:
        raise BoxAnalysisError(f"unknown box parameter {key!r}") from exc


def parameters_for_tiers(tiers: Iterable[str]) -> tuple[BoxParameter, ...]:
    """Return registry entries belonging to any of ``tiers``."""
    wanted = {str(tier) for tier in tiers}
    unknown = wanted - set(TIERS)
    if unknown:
        raise BoxAnalysisError(
            f"unknown analysis tier(s): {', '.join(sorted(unknown))}"
        )
    return tuple(item for item in PARAMETERS if item.tier in wanted)


def parameter_groups(
    available: Iterable[str] | None = None,
) -> tuple[tuple[str, tuple[BoxParameter, ...]], ...]:
    """Return ``(group, parameters)`` pairs in registry order.

    ``available`` restricts the result to keys that actually carried data, so a
    picker does not offer a field that is blank everywhere.
    """
    allowed = None if available is None else {str(key) for key in available}
    groups: dict[str, list[BoxParameter]] = {}
    for item in PARAMETERS:
        if allowed is not None and item.key not in allowed:
            continue
        groups.setdefault(item.group, []).append(item)
    return tuple((name, tuple(items)) for name, items in groups.items())


@dataclass(frozen=True)
class Criterion:
    """One threshold on one field.

    A criterion returns three answers, not two: satisfied, not satisfied, or
    *unknown* when the node has no value for the field. That third answer is the
    point of the class. Treating a missing value as a failure would quietly shade
    a below-ground or unextracted grid point as "not favourable", which reads on
    the map as information when it is really an absence of it.
    """

    parameter: str
    minimum: float | None = None
    maximum: float | None = None

    def __post_init__(self):
        # Fail here rather than at evaluation time: a typo in a screen should be
        # caught when the screen is defined, not silently mask a whole box.
        parameter(self.parameter)
        if self.minimum is None and self.maximum is None:
            raise BoxAnalysisError(
                f"criterion on {self.parameter!r} needs a minimum or a maximum"
            )
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise BoxAnalysisError(
                f"criterion on {self.parameter!r} has minimum above maximum"
            )

    def matches(self, values: Mapping[str, float]) -> bool | None:
        """Return ``True``, ``False``, or ``None`` when the field is absent."""
        value = values.get(self.parameter)
        if value is None:
            return None
        if self.minimum is not None and value < self.minimum:
            return False
        if self.maximum is not None and value > self.maximum:
            return False
        return True

    def describe(self) -> str:
        """Return a compact reader-facing form, e.g. ``MUCAPE >= 500 J/kg``."""
        item = parameter(self.parameter)
        units = f" {item.units}" if item.units else ""
        if self.minimum is not None and self.maximum is not None:
            return (
                f"{item.label} {item.format(self.minimum)}-"
                f"{item.format(self.maximum)}{units}"
            )
        if self.minimum is not None:
            return f"{item.label} \u2265 {item.format(self.minimum)}{units}"
        return f"{item.label} \u2264 {item.format(self.maximum)}{units}"


#: Named ingredient screens: sets of thresholds that must hold *together*.
#:
#: These are screening heuristics for finding the part of a box worth looking at,
#: not official products, and not a forecast. They are built from the ingredients
#: the corresponding mode is normally discussed in terms of, and every threshold
#: is deliberately on the permissive side so a screen narrows attention without
#: hiding a marginal but real signal. Adjust them freely -- pass your own
#: :class:`Criterion` tuple instead of a name.
#:
#: Only fast-tier fields are used, so a screen resolves as soon as a box is
#: extracted rather than waiting on the composite tier.
INGREDIENT_SCREENS: Mapping[str, tuple[Criterion, ...]] = {
    "surface-based storms": (
        Criterion("sbcape", minimum=250.0),
        Criterion("sbcin", minimum=-100.0),
    ),
    "organized convection": (
        Criterion("mucape", minimum=500.0),
        Criterion("shear_6km", minimum=30.0),
    ),
    "supercell": (
        Criterion("mucape", minimum=500.0),
        Criterion("shear_6km", minimum=35.0),
        Criterion("srh_3km", minimum=100.0),
    ),
    "tornado ingredients": (
        Criterion("mlcape", minimum=500.0),
        Criterion("shear_6km", minimum=35.0),
        Criterion("srh_1km", minimum=100.0),
        # A low cloud base is the ingredient that separates a tornadic
        # environment from a merely sheared and unstable one.
        Criterion("ml_lcl", maximum=1200.0),
        Criterion("mlcin", minimum=-125.0),
    ),
    "large hail ingredients": (
        Criterion("mucape", minimum=1000.0),
        Criterion("shear_6km", minimum=35.0),
        # A deep updraft: the equilibrium level well into the upper troposphere.
        Criterion("mu_el", minimum=9000.0),
    ),
    "damaging wind ingredients": (
        Criterion("mucape", minimum=750.0),
        Criterion("dcape", minimum=750.0),
        Criterion("shear_6km", minimum=25.0),
    ),
    "elevated convection": (
        Criterion("mucape", minimum=500.0),
        # Surface parcels capped, most-unstable parcel not: the signature of
        # storms rooted above the boundary layer.
        Criterion("sbcin", maximum=-100.0),
    ),
}


def screen_names() -> tuple[str, ...]:
    """Return the available ingredient-screen names, in presentation order."""
    return tuple(INGREDIENT_SCREENS)


def screen(name) -> tuple[Criterion, ...]:
    """Return the criteria for a named ingredient screen."""
    try:
        return INGREDIENT_SCREENS[str(name)]
    except KeyError as exc:
        raise BoxAnalysisError(
            f"unknown ingredient screen {name!r}; available: "
            + ", ".join(screen_names())
        ) from exc


def describe_criteria(criteria: Iterable[Criterion]) -> str:
    """Return a single-line description of a criteria set."""
    return " and ".join(item.describe() for item in criteria)


@dataclass(frozen=True)
class CoverageResult:
    """How much of a box satisfies a criteria set, and where."""

    criteria: tuple[Criterion, ...]
    matched: tuple["BoxPointAnalysis", ...]
    #: Nodes that had every field the criteria need, so could be judged at all.
    evaluated: int
    #: Nodes missing at least one needed field. Reported, never counted as a
    #: failure, so a partly-extracted box cannot look like an unfavourable one.
    unknown: int
    total: int
    cell_area_km2: float

    @property
    def count(self) -> int:
        return len(self.matched)

    @property
    def fraction(self) -> float:
        """Matched share of the *judgeable* nodes, in ``[0, 1]``.

        Deliberately not a share of every node: dividing by nodes that could not
        be judged would understate coverage for a box that straddles a domain
        edge.
        """
        if self.evaluated <= 0:
            return 0.0
        return self.count / float(self.evaluated)

    @property
    def area_km2(self) -> float:
        """Approximate matched area: one sample cell per matched node."""
        return self.count * self.cell_area_km2

    @property
    def conclusive(self) -> bool:
        """Whether every node could be judged."""
        return self.unknown == 0

    def describe(self) -> str:
        """Return a reader-facing one-line summary."""
        if self.evaluated <= 0:
            return f"{describe_criteria(self.criteria)}: no point could be judged"
        text = (
            f"{self.count} of {self.evaluated} points "
            f"({self.fraction * 100.0:.0f}%), about "
            f"{self.area_km2:,.0f} km\u00b2"
        )
        if self.unknown:
            text += f"; {self.unknown} point(s) lacked a needed field"
        return text


def _finite(value):
    """Coerce a backend/oracle value to a float, or ``None`` when absent.

    Handles the three ways this codebase spells "no value": ``None``, a NumPy
    masked constant, and the ``-9999`` sentinel. NaN from the backend contract
    is also absent.
    """
    if value is None:
        return None
    try:
        if np.ma.is_masked(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= _MISSING_LIMIT:
        return None
    return number


def _magnitude(u, v):
    """Return the magnitude of a vector when both components are present."""
    u_value = _finite(u)
    v_value = _finite(v)
    if u_value is None or v_value is None:
        return None
    return math.hypot(u_value, v_value)


def _profile_columns(prof):
    """Return filled ``pres/hght/tmpc/dwpc/wdir/wspd`` arrays for a profile."""

    def column(name):
        raw = getattr(prof, name, None)
        if raw is None:
            raise BoxAnalysisError(f"sounding is missing its {name} column")
        return np.ma.filled(np.ma.asarray(raw, dtype=float), -9999.0)

    return tuple(
        column(name) for name in ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd")
    )


def _fast_values(prof, *, include_effective_stp=False) -> dict[str, float]:
    """Return the native-backend tier for one profile.

    Every quantity here comes from the shared :mod:`sharpmod.backends`
    contract, so it agrees with the Skew-T and hodograph drawn for the same
    point and costs well under a millisecond.
    """
    from sharpmod import backends

    pres, hght, tmpc, dwpc, wdir, wspd = _profile_columns(prof)
    u, v = backends.wind_to_components(wdir, wspd)
    values: dict[str, float] = {}

    try:
        thermodynamics = backends._profile_thermodynamics_buffers(
            pres,
            hght,
            tmpc,
            dwpc,
        )
        parcels = thermodynamics.parcels
        convective = thermodynamics.convective
        downdraft = thermodynamics.downdraft
    except Exception:
        # Preserve the prior partial-result behavior for an older or failing
        # backend while keeping the supported path to one profile preparation.
        parcels = backends.profile_parcels(pres, hght, tmpc, dwpc)
        try:
            convective = backends.profile_convective_parcels(
                pres, hght, tmpc, dwpc,
            )
        except Exception:
            convective = None
        try:
            downdraft = backends.profile_dcape(pres, hght, tmpc, dwpc)
        except Exception:
            downdraft = None
    surface = parcels.surface
    mixed = parcels.mixed_layer
    unstable = parcels.most_unstable
    for key, raw in (
        ("sbcape", surface.cape),
        ("sbcin", surface.cin),
        ("sb_lcl", surface.lcl_height),
        ("mlcape", mixed.cape),
        ("mlcin", mixed.cin),
        ("ml_lcl", mixed.lcl_height),
        ("ml_lfc", mixed.lfc_height),
        ("mucape", unstable.cape),
        ("mucin", unstable.cin),
        ("mucape_3km", unstable.cape_3km),
        ("mucape_6km", unstable.cape_6km),
        ("mu_el", unstable.el_height),
    ):
        number = _finite(raw)
        if number is not None:
            values[key] = number

    # The effective inflow layer needs the convective workspace rather than the
    # three-parcel one, and it is what makes an effective-layer field possible
    # without the pure-Python oracle.
    if convective is not None:
        effective = convective.effective.diagnostics
        for key, raw in (
            ("eff_cape", effective.cape),
            ("eff_cin", effective.cin),
            ("eff_inflow_base", convective.effective_bottom_pressure),
            ("eff_inflow_top", convective.effective_top_pressure),
        ):
            number = _finite(raw)
            if number is not None:
                values[key] = number

    kinematics = backends.profile_kinematics(pres, hght, u, v, KINEMATIC_LAYER_TOPS)
    for top, shear_key, srh_key in (
        (1000.0, "shear_1km", "srh_1km"),
        (3000.0, "shear_3km", "srh_3km"),
        (6000.0, "shear_6km", None),
        (8000.0, "shear_8km", None),
    ):
        layer = kinematics.layer(top)
        if layer is None:
            continue
        shear = _magnitude(layer.height_shear_u, layer.height_shear_v)
        if shear is not None:
            values[shear_key] = shear
        if srh_key is not None:
            srh = _finite(layer.srh_total)
            if srh is not None:
                values[srh_key] = srh
        if top == 6000.0:
            mean = _magnitude(layer.mean_u, layer.mean_v)
            if mean is not None:
                values["mean_wind_6km"] = mean
    motion = kinematics.storm_motion
    if motion is not None and len(motion) >= 2:
        right = _magnitude(motion[0], motion[1])
        if right is not None:
            values["storm_motion_r"] = right

    if downdraft is not None:
        for key, raw in (
            ("dcape", downdraft.cape),
            ("downrush_t", downdraft.downrush_temperature),
        ):
            number = _finite(raw)
            if number is not None:
                values[key] = number
    if include_effective_stp and convective is not None:
        stp = _fast_effective_stp_cin(prof, convective)
        if stp is not None:
            values["stp_cin"] = stp
    return values


def _fast_values_from_batch(
    batch,
    index: int,
    prof,
    *,
    include_effective_stp: bool = False,
) -> dict[str, float]:
    """Map one fixed-width backend batch row to the ordinary fast-tier keys."""
    parcels = batch.parcels[index]
    convective = batch.convective_parcels[index]
    bounds = batch.effective_bounds[index]
    downdraft = batch.downdraft[index]
    storm_motion = batch.storm_motion[index]
    layers = batch.kinematic_layers[index]
    values: dict[str, float] = {}

    def put(key, raw):
        number = _finite(raw)
        if number is not None:
            values[key] = number

    surface = parcels[0]
    unstable = parcels[1]
    mixed = parcels[2]
    for key, raw in (
        ("sbcape", surface[10]),
        ("sbcin", surface[11]),
        ("sb_lcl", surface[5]),
        ("mlcape", mixed[10]),
        ("mlcin", mixed[11]),
        ("ml_lcl", mixed[5]),
        ("ml_lfc", mixed[7]),
        ("mucape", unstable[10]),
        ("mucin", unstable[11]),
        ("mucape_3km", unstable[12]),
        ("mucape_6km", unstable[13]),
        ("mu_el", unstable[9]),
        ("eff_cape", convective[4][10]),
        ("eff_cin", convective[4][11]),
        ("eff_inflow_base", bounds[0]),
        ("eff_inflow_top", bounds[1]),
        ("dcape", downdraft[0]),
        ("downrush_t", downdraft[2]),
    ):
        put(key, raw)

    for layer_index, (top, shear_key, srh_key) in enumerate(
        (
            (1000.0, "shear_1km", "srh_1km"),
            (3000.0, "shear_3km", "srh_3km"),
            (6000.0, "shear_6km", None),
            (8000.0, "shear_8km", None),
        )
    ):
        layer = layers[layer_index]
        shear = _magnitude(layer[4], layer[5])
        if shear is not None:
            values[shear_key] = shear
        if srh_key is not None:
            put(srh_key, layer[10])
        if top == 6000.0:
            mean = _magnitude(layer[6], layer[7])
            if mean is not None:
                values["mean_wind_6km"] = mean
    right = _magnitude(storm_motion[0], storm_motion[1])
    if right is not None:
        values["storm_motion_r"] = right

    if include_effective_stp:
        most_unstable = convective[2]
        mixed_layer = convective[3]
        convective_view = SimpleNamespace(
            effective_bottom_pressure=bounds[0],
            effective_top_pressure=bounds[1],
            most_unstable=SimpleNamespace(
                diagnostics=SimpleNamespace(
                    cape=most_unstable[10],
                    cin=most_unstable[11],
                    el_height=most_unstable[9],
                )
            ),
            mixed_layer=SimpleNamespace(
                diagnostics=SimpleNamespace(
                    cape=mixed_layer[10],
                    cin=mixed_layer[11],
                    lcl_height=mixed_layer[5],
                )
            ),
        )
        stp = _fast_effective_stp_cin(prof, convective_view)
        if stp is not None:
            values["stp_cin"] = stp
    return values


def _fast_effective_stp_cin(prof, convective) -> float | None:
    """Compute the table's one composite without building the full oracle.

    The complete ``ConvectiveProfile`` eagerly evaluates every severe, winter,
    fire, and analogue product. STP needs only the parcel workspace already
    produced by the fast tier plus a few effective-layer wind integrations.
    This follows SHARPpy's own ``get_kinematics``/``get_severe`` equations and
    therefore retains numerical parity while avoiding unrelated work.
    """
    existing = _finite(getattr(prof, "stp_cin", None))
    if existing is not None:
        return existing
    try:
        from sharppy.sharptab import interp as sp_interp
        from sharppy.sharptab import params as sp_params
        from sharppy.sharptab import profile as sp_profile
        from sharppy.sharptab import utils as sp_utils
        from sharppy.sharptab import winds as sp_winds

        source = prof
        if not hasattr(source, "sfc"):
            source = sp_profile.BasicProfile.copy(source)
        effective_bottom = float(convective.effective_bottom_pressure)
        effective_top = float(convective.effective_top_pressure)
        if not math.isfinite(effective_bottom) or not math.isfinite(effective_top):
            return 0.0

        mu = convective.most_unstable.diagnostics
        mixed = convective.mixed_layer.diagnostics
        required = (
            mu.cape,
            mu.cin,
            mu.el_height,
            mixed.cape,
            mixed.cin,
            mixed.lcl_height,
        )
        if not all(math.isfinite(float(value)) for value in required):
            return None
        mu_parcel = SimpleNamespace(
            bplus=float(mu.cape),
            bminus=float(mu.cin),
            elhght=float(mu.el_height),
        )
        motion = sp_params.bunkers_storm_motion(
            source, mupcl=mu_parcel, pbot=effective_bottom
        )
        bottom_agl = sp_interp.to_agl(source, sp_interp.hght(source, effective_bottom))
        top_agl = sp_interp.to_agl(source, sp_interp.hght(source, effective_top))
        depth = (float(mu.el_height) - float(bottom_agl)) / 2.0
        shear_top = sp_interp.pres(
            source, sp_interp.to_msl(source, float(bottom_agl) + depth)
        )
        shear = sp_winds.wind_shear(source, pbot=effective_bottom, ptop=shear_top)
        bulk_shear = sp_utils.mag(*shear)

        latitude = _finite(getattr(source, "latitude", None)) or 0.0
        motion_offset = 2 if latitude < 0.0 else 0
        helicity = sp_winds.helicity(
            source,
            float(bottom_agl),
            float(top_agl),
            stu=motion[motion_offset],
            stv=motion[motion_offset + 1],
        )[0]
        if latitude < 0.0:
            helicity = -helicity
        value = sp_params.stp_cin(
            float(mixed.cape),
            helicity,
            sp_utils.KTS2MS(bulk_shear),
            float(mixed.lcl_height),
            float(mixed.cin),
        )
        if latitude < 0.0:
            value = -value
        return _finite(value)
    except Exception:  # noqa: BLE001 - a missing summary value is non-fatal
        return None


def fast_values(prof) -> dict[str, float]:
    """Return the ordinary native-backend tier without optional composites."""
    return _fast_values(prof)


def fast_values_many(
    profiles,
    *,
    include_effective_stp: bool = False,
) -> tuple[dict[str, float], ...]:
    """Return fast-tier values for a complete ordered profile workload.

    The backend owns the measured serial/parallel threshold and worker cap.
    Packing, native analysis, and dense-result mapping are all-or-nothing; an
    unavailable or failing batch path falls back to the established per-profile
    implementation without changing order or public field semantics.
    """
    profiles = tuple(profiles)
    if not profiles:
        return ()

    from sharpmod import backends

    try:
        columns = tuple(
            (*_profile_columns(prof), getattr(prof, "sfc", 0))
            for prof in profiles
        )
        batch = backends.profile_batch_analysis(columns, KINEMATIC_LAYER_TOPS)
        mapped = tuple(
            _fast_values_from_batch(
                batch,
                index,
                prof,
                include_effective_stp=include_effective_stp,
            )
            for index, prof in enumerate(profiles)
        )
        if len(mapped) != len(profiles):
            raise BoxAnalysisError("backend batch returned the wrong profile count")
        return mapped
    except Exception:
        return tuple(
            _fast_values(prof, include_effective_stp=include_effective_stp)
            for prof in profiles
        )


def summary_values(prof) -> dict[str, float]:
    """Return fast comparison metrics plus an optimized effective STP."""
    return _fast_values(prof, include_effective_stp=True)


def composite_values(prof) -> dict[str, float]:
    """Return the SPC/AMS composite tier for one profile.

    Reads the cached upstream ``ConvectiveProfile`` that the rest of the
    application already uses, so a box field and the corresponding Skew-T
    report the same numbers.
    """
    from sharpmod.sharptab import derived

    values: dict[str, float] = {}
    oracle = derived._convective_oracle_profile(prof)
    if oracle is not None:
        for key, attribute in (
            ("stp_cin", "stp_cin"),
            ("stp_fixed", "stp_fixed"),
            ("scp", "scp"),
            ("ship", "ship"),
            ("sig_severe", "sig_severe"),
            ("mmp", "mmp"),
            ("wndg", "wndg"),
            ("esp", "esp"),
            ("sherbe", "sherbe"),
            ("ebwd", "ebwspd"),
            ("critical_angle", "right_critical_angle"),
            ("left_scp", "left_scp"),
            ("pwat", "pwat"),
            ("mean_mixr", "mean_mixr"),
            ("low_rh", "low_rh"),
            ("mid_rh", "mid_rh"),
            ("lapse_3km", "lapserate_3km"),
            ("lapse_700_500", "lapserate_700_500"),
            ("k_index", "k_idx"),
            ("totals_totals", "totals_totals"),
            ("conv_t", "convT"),
            ("max_t", "maxT"),
        ):
            number = _finite(getattr(oracle, attribute, None))
            if number is not None:
                values[key] = number
        # Effective SRH arrives as (total, positive, negative).
        esrh = getattr(oracle, "right_esrh", None)
        if isinstance(esrh, Sequence) and len(esrh) >= 1:
            number = _finite(esrh[0])
            if number is not None:
                values["esrh"] = number

    for key, function in (
        ("lhp", derived.large_hail_parameter),
        ("hpi", derived.hail_possibility_index),
        ("peskov", derived.peskov_index),
        ("mcs_index", derived.mcs_index),
        ("wbz", derived.wet_bulb_zero_height),
        ("dcp", derived.dcp),
        ("vgp", derived.vorticity_generation_parameter),
        ("nst", derived.non_supercell_tornado_parameter),
    ):
        try:
            number = _finite(function(prof))
        except Exception:
            number = None
        if number is not None:
            values[key] = number
    for key, layer in (("ehi_1km", 1000.0), ("ehi_3km", 3000.0)):
        try:
            number = _finite(derived.ehi(prof, layer))
        except Exception:
            number = None
        if number is not None:
            values[key] = number
    return values


def load_profile(npz_path):
    """Load one portable sounding and return its raw ``Profile``."""
    from sharpmod.io import decoder

    collection, _meta = decoder.load_npz(os.fspath(npz_path))
    members = tuple(getattr(collection, "_profs", {}))
    if not members:
        raise BoxAnalysisError(f"{npz_path} contains no profile")
    profiles = collection._profs[members[0]]
    if not profiles:
        raise BoxAnalysisError(f"{npz_path} contains no profile")
    return profiles[0], collection


def analyze_profile(prof, *, tiers=(FAST_TIER,)) -> dict[str, float]:
    """Return every requested tier's values for one profile."""
    wanted = tuple(str(tier) for tier in tiers)
    unknown = set(wanted) - set(TIERS)
    if unknown:
        raise BoxAnalysisError(
            f"unknown analysis tier(s): {', '.join(sorted(unknown))}"
        )
    values: dict[str, float] = {}
    if FAST_TIER in wanted:
        values.update(fast_values(prof))
    if COMPOSITE_TIER in wanted:
        values.update(composite_values(prof))
    return values


@dataclass(frozen=True)
class BoxPointAnalysis:
    """One node's scalar values, or the reason it has none."""

    row: int
    col: int
    lat: float
    lon: float
    request_id: str
    npz_path: str | None = None
    values: Mapping[str, float] = None  # type: ignore[assignment]
    error: str | None = None
    #: Raw profile columns on :data:`DEFAULT_TRANSECT_LEVELS`, captured while
    #: the profile was open on the worker. Empty for a node that was never
    #: read, which the transect and envelope treat as "no data" rather than
    #: going back to the disk for it.
    columns: Mapping[str, tuple[float | None, ...]] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.values is None:
            object.__setattr__(self, "values", {})
        if self.columns is None:
            object.__setattr__(self, "columns", {})

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.values)

    def value(self, key):
        """Return one value, or ``None`` when this node does not have it."""
        return self.values.get(str(key))


@dataclass(frozen=True)
class BoxFieldStats:
    """Area statistics for one field across a box."""

    parameter: BoxParameter
    count: int
    minimum: float
    maximum: float
    mean: float
    median: float
    p10: float
    p90: float
    #: The node at the meteorologically notable end of the range.
    extreme: BoxPointAnalysis

    @property
    def spread(self) -> float:
        """Max minus min: how much the field actually varies across the box."""
        return self.maximum - self.minimum


@dataclass(frozen=True)
class EnvelopeResult:
    """Per-level spread of one profile column across every sounding in a box.

    This answers the question a single sounding cannot: not "what does the
    atmosphere look like here" but "how much does it differ across this area,
    and at which levels". A box whose envelope is a narrow ribbon is one
    airmass; a box whose envelope fans out at low levels has a boundary in it.
    """

    field: str
    levels: tuple[float, ...]
    minimum: tuple[float | None, ...]
    low: tuple[float | None, ...]
    median: tuple[float | None, ...]
    high: tuple[float | None, ...]
    maximum: tuple[float | None, ...]
    #: How many soundings contributed at each level. Falls off near the ground,
    #: where higher terrain has already ended.
    counts: tuple[int, ...]
    #: Every contributing column, for optional spaghetti rendering.
    columns: tuple[tuple[float | None, ...], ...]
    percentiles: tuple[float, float]

    @property
    def soundings(self) -> int:
        return len(self.columns)

    def spread_at(self, pressure) -> float | None:
        """Return max minus min at the ladder level nearest ``pressure``."""
        if not self.levels:
            return None
        target = float(pressure)
        index = min(
            range(len(self.levels)),
            key=lambda position: abs(self.levels[position] - target),
        )
        low = self.minimum[index]
        high = self.maximum[index]
        if low is None or high is None:
            return None
        return high - low

    @property
    def widest_level(self) -> float | None:
        """The pressure at which the box disagrees with itself the most."""
        best = None
        best_span = None
        for index, level in enumerate(self.levels):
            low = self.minimum[index]
            high = self.maximum[index]
            if low is None or high is None:
                continue
            span = high - low
            if best_span is None or span > best_span:
                best, best_span = level, span
        return best


@dataclass(frozen=True)
class BoxAnalysis:
    """Scalar fields for every node of one box sample plan."""

    plan: BoxSamplePlan
    points: tuple[BoxPointAnalysis, ...]
    tiers: tuple[str, ...]
    run_time: object = None
    valid_time: object = None
    fxx: int = 0

    @property
    def rows(self) -> int:
        return self.plan.rows

    @property
    def cols(self) -> int:
        return self.plan.cols

    @property
    def analyzed(self) -> tuple[BoxPointAnalysis, ...]:
        """Nodes that produced at least one value."""
        return tuple(point for point in self.points if point.ok)

    @property
    def failures(self) -> tuple[BoxPointAnalysis, ...]:
        return tuple(point for point in self.points if point.error is not None)

    def point_at(self, row, col) -> BoxPointAnalysis | None:
        """Return the node at ``(row, col)``, or ``None`` when out of range."""
        if not 0 <= row < self.rows or not 0 <= col < self.cols:
            return None
        index = int(row) * self.cols + int(col)
        try:
            return self.points[index]
        except IndexError:
            return None

    def available_parameters(self) -> tuple[BoxParameter, ...]:
        """Return registry entries that at least one node produced.

        This is what a picker should offer: a field that is blank everywhere is
        not a choice, it is a dead end.
        """
        present = set()
        for point in self.points:
            present.update(point.values)
        return tuple(item for item in PARAMETERS if item.key in present)

    def field(self, key) -> tuple[tuple[float | None, ...], ...]:
        """Return the field as ``rows`` tuples of ``cols`` optional values.

        Row 0 is the north edge, matching :class:`~sharpmod.box_sounding.\
        BoxSamplePoint` and raster order.
        """
        item = parameter(key)
        grid = []
        for row in range(self.rows):
            line = []
            for col in range(self.cols):
                point = self.point_at(row, col)
                line.append(None if point is None else point.value(item.key))
            grid.append(tuple(line))
        return tuple(grid)

    def values_of(self, key) -> tuple[float, ...]:
        """Return every present value for one field, unordered."""
        item = parameter(key)
        return tuple(
            point.values[item.key] for point in self.points if item.key in point.values
        )

    def statistics(self, key) -> BoxFieldStats | None:
        """Return area statistics for one field, or ``None`` when it is empty."""
        item = parameter(key)
        present = [point for point in self.points if item.key in point.values]
        if not present:
            return None
        numbers = np.asarray([point.values[item.key] for point in present], dtype=float)
        extreme_index = (
            int(np.argmax(numbers))
            if item.notable == "high"
            else int(np.argmin(numbers))
        )
        return BoxFieldStats(
            parameter=item,
            count=int(numbers.size),
            minimum=float(np.min(numbers)),
            maximum=float(np.max(numbers)),
            mean=float(np.mean(numbers)),
            median=float(np.median(numbers)),
            p10=float(np.percentile(numbers, 10.0)),
            p90=float(np.percentile(numbers, 90.0)),
            extreme=present[extreme_index],
        )

    def ranked(self, key, *, limit=None) -> tuple[BoxPointAnalysis, ...]:
        """Return nodes ordered with the most notable value first."""
        item = parameter(key)
        present = [point for point in self.points if item.key in point.values]
        present.sort(
            key=lambda point: point.values[item.key],
            reverse=item.notable == "high",
        )
        if limit is not None:
            present = present[: max(0, int(limit))]
        return tuple(present)

    def extreme(self, key) -> BoxPointAnalysis | None:
        """Return the single most notable node for one field."""
        stats = self.statistics(key)
        return None if stats is None else stats.extreme

    def field_transect(self, key, *, orientation="ew", index=None):
        """Return one row or column of a field with its along-box distances.

        ``orientation`` is ``"ew"`` for a west-to-east row or ``"ns"`` for a
        north-to-south column. ``index`` defaults to the middle of the box,
        which is the slice a forecaster means by "across the box".

        Returns ``(distances_km, values, points)``.
        """
        item = parameter(key)
        region = self.plan.region
        if orientation not in {"ew", "ns"}:
            raise BoxAnalysisError('orientation must be "ew" or "ns"')
        if orientation == "ew":
            row = self.rows // 2 if index is None else int(index)
            nodes = [self.point_at(row, col) for col in range(self.cols)]
            total = region.width_km
        else:
            col = self.cols // 2 if index is None else int(index)
            nodes = [self.point_at(row, col) for row in range(self.rows)]
            total = region.height_km
        if any(node is None for node in nodes):
            raise BoxAnalysisError("transect index is outside the box")
        count = len(nodes)
        step = 0.0 if count < 2 else total / float(count - 1)
        distances = tuple(step * position for position in range(count))
        values = tuple(node.value(item.key) for node in nodes)
        return distances, values, tuple(nodes)

    def vertical_transect(
        self,
        *,
        field="tmpc",
        orientation="ew",
        index=None,
        levels=DEFAULT_TRANSECT_LEVELS,
    ):
        """Return a pressure-by-distance slice of one raw profile column.

        Reads the columns captured during analysis, so no profile is re-decoded
        to draw a slice. Only a caller supplying its own ``levels`` falls back to
        re-reading the files.

        Returns ``(distances_km, levels, grid)`` where ``grid[level][node]`` is
        the interpolated value or ``None`` below ground / above the profile top.
        """
        from sharpmod import backends

        if field not in TRANSECT_FIELDS:
            raise BoxAnalysisError(
                f"transect field must be one of {', '.join(TRANSECT_FIELDS)}"
            )
        if orientation not in {"ew", "ns"}:
            raise BoxAnalysisError('orientation must be "ew" or "ns"')
        region = self.plan.region
        if orientation == "ew":
            row = self.rows // 2 if index is None else int(index)
            nodes = [self.point_at(row, col) for col in range(self.cols)]
            total = region.width_km
        else:
            col = self.cols // 2 if index is None else int(index)
            nodes = [self.point_at(row, col) for row in range(self.rows)]
            total = region.height_km
        if any(node is None for node in nodes):
            raise BoxAnalysisError("transect index is outside the box")

        ladder = np.asarray([float(value) for value in levels], dtype=float)
        if ladder.size == 0 or not np.all(ladder > 0.0):
            raise BoxAnalysisError("transect levels must all be positive hPa")
        # Interpolate linearly in log-pressure, which is how a sounding is read.
        # Note the backend's own ``log=`` flag does the opposite of what is
        # wanted here: it exponentiates the *result*, for callers recovering a
        # pressure. The coordinate is therefore transformed explicitly and the
        # flag left off.
        log_ladder = np.log10(ladder)
        columns = [_interpolated_column(node, field, log_ladder) for node in nodes]

        count = len(nodes)
        step = 0.0 if count < 2 else total / float(count - 1)
        distances = tuple(step * position for position in range(count))
        grid = tuple(
            tuple(
                column[level_index] if level_index < len(column) else None
                for column in columns
            )
            for level_index in range(ladder.size)
        )
        return distances, tuple(float(v) for v in ladder), grid

    @property
    def cell_area_km2(self) -> float:
        """Approximate ground area one sample node represents, in km2."""
        from sharpmod.box_sounding import KM_PER_DEG_LAT, km_per_deg_lon

        region = self.plan.region
        dlat = (
            region.lat_span / float(self.rows - 1) if self.rows > 1 else region.lat_span
        )
        dlon = (
            region.lon_span / float(self.cols - 1) if self.cols > 1 else region.lon_span
        )
        return dlat * KM_PER_DEG_LAT * dlon * km_per_deg_lon(region.center_lat)

    def _resolve_criteria(self, criteria) -> tuple[Criterion, ...]:
        """Accept a screen name, one Criterion, or an iterable of them."""
        if isinstance(criteria, str):
            return screen(criteria)
        if isinstance(criteria, Criterion):
            return (criteria,)
        resolved = tuple(criteria or ())
        if not resolved:
            raise BoxAnalysisError("at least one criterion is required")
        for item in resolved:
            if not isinstance(item, Criterion):
                raise BoxAnalysisError(
                    "criteria must be Criterion values or a screen name"
                )
        return resolved

    def mask(self, criteria) -> tuple[tuple[bool | None, ...], ...]:
        """Return a rows-by-cols grid of ``True``/``False``/``None``.

        ``None`` marks a node that could not be judged because it lacks one of
        the fields the criteria need. ``criteria`` may be a screen name, one
        :class:`Criterion`, or any iterable of them.
        """
        resolved = self._resolve_criteria(criteria)
        grid = []
        for row in range(self.rows):
            line = []
            for col in range(self.cols):
                point = self.point_at(row, col)
                line.append(
                    None if point is None else _evaluate(resolved, point.values)
                )
            grid.append(tuple(line))
        return tuple(grid)

    def coverage(self, criteria) -> CoverageResult:
        """Return how much of the box satisfies ``criteria``, and which nodes.

        This is the question a single field cannot answer: not "where is CAPE
        largest" but "where are all of these true at once, and over how much
        ground".
        """
        resolved = self._resolve_criteria(criteria)
        matched = []
        evaluated = 0
        unknown = 0
        for point in self.points:
            verdict = _evaluate(resolved, point.values)
            if verdict is None:
                unknown += 1
                continue
            evaluated += 1
            if verdict:
                matched.append(point)
        return CoverageResult(
            criteria=resolved,
            matched=tuple(matched),
            evaluated=evaluated,
            unknown=unknown,
            total=len(self.points),
            cell_area_km2=self.cell_area_km2,
        )

    def screens(self) -> tuple[str, ...]:
        """Return the screen names every field of which this box can evaluate.

        A screen whose fields were never computed is not offered: it would report
        zero coverage and read as "nothing here" rather than "not asked".
        """
        present = set()
        for point in self.points:
            present.update(point.values)
        return tuple(
            name
            for name, criteria in INGREDIENT_SCREENS.items()
            if all(item.parameter in present for item in criteria)
        )

    def envelope(
        self,
        *,
        field="tmpc",
        levels=DEFAULT_TRANSECT_LEVELS,
        percentiles=(10.0, 90.0),
    ) -> EnvelopeResult:
        """Return the per-level spread of one profile column across the box.

        Every extracted sounding is interpolated onto a shared log-pressure
        ladder and reduced to min / low percentile / median / high percentile /
        max at each level. Levels below a node's terrain simply do not
        contribute, which is why :attr:`EnvelopeResult.counts` thins out near the
        ground instead of the envelope quietly widening there.
        """
        if field not in TRANSECT_FIELDS:
            raise BoxAnalysisError(
                f"envelope field must be one of {', '.join(TRANSECT_FIELDS)}"
            )
        try:
            low_pct, high_pct = (float(value) for value in percentiles)
        except (TypeError, ValueError) as exc:
            raise BoxAnalysisError("percentiles must be two numbers") from exc
        if not 0.0 <= low_pct < high_pct <= 100.0:
            raise BoxAnalysisError("percentiles must be ascending and within [0, 100]")
        ladder = np.asarray([float(value) for value in levels], dtype=float)
        if ladder.size == 0 or not np.all(ladder > 0.0):
            raise BoxAnalysisError("envelope levels must all be positive hPa")
        log_ladder = np.log10(ladder)

        columns = []
        for point in self.points:
            column = _interpolated_column(point, field, log_ladder)
            if any(value is not None for value in column):
                columns.append(tuple(column))

        minimum, low, median, high, maximum, counts = [], [], [], [], [], []
        for index in range(ladder.size):
            present = [column[index] for column in columns if column[index] is not None]
            counts.append(len(present))
            if not present:
                for series in (minimum, low, median, high, maximum):
                    series.append(None)
                continue
            values = np.asarray(present, dtype=float)
            if field == "wdir":
                # Keep a cluster straddling north contiguous (350, 10 becomes
                # 350, 370) before reducing it.  Normal scalar percentiles
                # would instead invent a southerly median and a 340-degree
                # spread from a narrow northerly cluster.
                radians = np.deg2rad(np.mod(values, 360.0))
                center = (
                    np.degrees(
                        np.arctan2(
                            np.mean(np.sin(radians)),
                            np.mean(np.cos(radians)),
                        )
                    )
                    % 360.0
                )
                if center < 180.0 and np.any(values > 180.0):
                    center += 360.0
                values = center + ((values - center + 180.0) % 360.0 - 180.0)
            minimum.append(float(np.min(values)))
            low.append(float(np.percentile(values, low_pct)))
            median.append(float(np.median(values)))
            high.append(float(np.percentile(values, high_pct)))
            maximum.append(float(np.max(values)))

        return EnvelopeResult(
            field=str(field),
            levels=tuple(float(value) for value in ladder),
            minimum=tuple(minimum),
            low=tuple(low),
            median=tuple(median),
            high=tuple(high),
            maximum=tuple(maximum),
            counts=tuple(counts),
            columns=tuple(columns),
            percentiles=(low_pct, high_pct),
        )

    def summary(self, keys=None) -> str:
        """Return a reader-facing table of area statistics."""
        items = (
            self.available_parameters()
            if keys is None
            else tuple(parameter(key) for key in keys)
        )
        lines = [
            f"{'Field':24s} {'Min':>10s} {'Mean':>10s} {'Max':>10s}  Extreme",
        ]
        for item in items:
            stats = self.statistics(item.key)
            if stats is None:
                continue
            node = stats.extreme
            lines.append(
                f"{item.label:24s} "
                f"{item.format(stats.minimum):>10s} "
                f"{item.format(stats.mean):>10s} "
                f"{item.format(stats.maximum):>10s}"
                f"  {node.lat:.2f},{node.lon:.2f}"
            )
        return "\n".join(lines)


@dataclass(frozen=True)
class BoxSequence:
    """One box analyzed at several forecast hours.

    A single box says where the atmosphere is favourable. A sequence says *when*,
    which is usually the harder half of the question: a field can be strong at
    F12, gone by F18, and the peak can sit between the hours a forecaster would
    otherwise have checked.
    """

    hours: tuple[int, ...]
    analyses: Mapping[int, BoxAnalysis]
    run_time: object = None

    @property
    def plan(self):
        """The shared sample plan. Every hour samples the same lattice."""
        first = self.at(self.hours[0]) if self.hours else None
        return None if first is None else first.plan

    def at(self, hour) -> BoxAnalysis | None:
        """Return the analysis for one forecast hour, if present."""
        try:
            return self.analyses.get(int(hour))
        except (TypeError, ValueError):
            return None

    def available_parameters(self) -> tuple[BoxParameter, ...]:
        """Fields every hour can show.

        The intersection rather than the union: a field that exists at only some
        hours would make the slider appear to lose and regain data.
        """
        if not self.hours:
            return ()
        shared = None
        for hour in self.hours:
            analysis = self.at(hour)
            keys = (
                set()
                if analysis is None
                else {item.key for item in analysis.available_parameters()}
            )
            shared = keys if shared is None else (shared & keys)
        shared = shared or set()
        return tuple(item for item in PARAMETERS if item.key in shared)

    def statistics_series(self, key):
        """Return ``((hour, BoxFieldStats | None), ...)`` for one field."""
        item = parameter(key)
        series = []
        for hour in self.hours:
            analysis = self.at(hour)
            series.append(
                (
                    hour,
                    None if analysis is None else analysis.statistics(item.key),
                )
            )
        return tuple(series)

    def coverage_series(self, criteria):
        """Return ``((hour, CoverageResult | None), ...)`` for a criteria set."""
        series = []
        for hour in self.hours:
            analysis = self.at(hour)
            if analysis is None:
                series.append((hour, None))
                continue
            try:
                series.append((hour, analysis.coverage(criteria)))
            except BoxAnalysisError:
                series.append((hour, None))
        return tuple(series)

    def peak_hour(self, key) -> int | None:
        """Return the hour whose extreme is the most notable for one field."""
        item = parameter(key)
        best_hour = None
        best_value = None
        for hour, stats in self.statistics_series(item.key):
            if stats is None:
                continue
            value = stats.maximum if item.notable == "high" else stats.minimum
            if best_value is None or (
                value > best_value if item.notable == "high" else value < best_value
            ):
                best_hour, best_value = hour, value
        return best_hour

    def peak_coverage_hour(self, criteria) -> int | None:
        """Return the hour with the largest qualifying area."""
        best_hour = None
        best_count = None
        for hour, coverage in self.coverage_series(criteria):
            if coverage is None:
                continue
            if best_count is None or coverage.count > best_count:
                best_hour, best_count = hour, coverage.count
        return best_hour

    def summary(self, key) -> str:
        """Return a per-hour table of one field's range across the box."""
        item = parameter(key)
        lines = [
            f"{item.label}" + (f" ({item.units})" if item.units else ""),
            f"{'Hour':>6s} {'Min':>10s} {'Mean':>10s} {'Max':>10s}",
        ]
        peak = self.peak_hour(item.key)
        for hour, stats in self.statistics_series(item.key):
            if stats is None:
                lines.append(f"F{hour:03d}".rjust(6) + "         no data")
                continue
            marker = "  <- peak" if hour == peak else ""
            lines.append(
                f"F{hour:03d}".rjust(6)
                + f" {item.format(stats.minimum):>10s}"
                + f" {item.format(stats.mean):>10s}"
                + f" {item.format(stats.maximum):>10s}{marker}"
            )
        return "\n".join(lines)


def analyze_box_sequence(
    plan: BoxSamplePlan,
    outputs_by_hour: Mapping[int, Mapping[str, str]],
    *,
    tiers=(FAST_TIER,),
    run_time=None,
    progress: Callable[[int, int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> BoxSequence:
    """Analyze one box at several forecast hours.

    ``outputs_by_hour`` maps a forecast hour to that hour's
    ``request_id -> .npz path`` mapping. ``progress`` is called as
    ``(hour, done, total)`` so a caller can report which hour is being worked.
    """
    if not isinstance(plan, BoxSamplePlan):
        raise BoxAnalysisError("plan must be a BoxSamplePlan")
    try:
        hours = tuple(sorted(int(hour) for hour in outputs_by_hour))
    except (TypeError, ValueError) as exc:
        raise BoxAnalysisError("forecast hours must be integers") from exc
    if not hours:
        raise BoxAnalysisError("a sequence needs at least one forecast hour")

    analyses: dict[int, BoxAnalysis] = {}
    for hour in hours:
        if cancelled is not None and cancelled():
            break
        outputs = outputs_by_hour[hour] if hour in outputs_by_hour else {}
        analyses[hour] = analyze_box(
            plan,
            outputs,
            tiers=tiers,
            run_time=run_time,
            fxx=hour,
            progress=(
                None
                if progress is None
                else lambda done, total, _point, hour=hour: progress(hour, done, total)
            ),
            cancelled=cancelled,
        )
    return BoxSequence(
        hours=tuple(sorted(analyses)),
        analyses=analyses,
        run_time=run_time,
    )


def _analyze_fast_box_points(
    plan: BoxSamplePlan,
    paths: Mapping[str, str],
    *,
    progress: Callable[[int, int, BoxPointAnalysis], None] | None,
    cancelled: Callable[[], bool] | None,
) -> tuple[BoxPointAnalysis, ...]:
    """Analyze a complete fast-tier box through one ordered backend batch."""
    prepared: list[BoxPointAnalysis | None] = [None] * len(plan.points)
    pending = []

    # Loading and transect interpolation stay per node. They can fail or be
    # cancelled independently and do not belong in the native batch contract.
    for position, node in enumerate(plan.points):
        path = paths.get(node.request_id)
        if path is None:
            prepared[position] = _empty_point(node)
            continue
        if cancelled is not None and cancelled():
            prepared[position] = _empty_point(node, error="cancelled")
            continue
        try:
            prof, _collection = load_profile(path)
            columns = transect_columns(prof)
        except Exception as exc:  # noqa: BLE001 - one bad node must not end
            prepared[position] = BoxPointAnalysis(
                row=node.row,
                col=node.col,
                lat=node.lat,
                lon=node.lon,
                request_id=node.request_id,
                npz_path=path,
                values={},
                error=f"{type(exc).__name__}: {exc}",
            )
            continue
        pending.append((position, node, path, prof, columns))

    if pending:
        try:
            rows = fast_values_many(item[3] for item in pending)
            if len(rows) != len(pending):
                raise BoxAnalysisError(
                    "backend batch returned the wrong profile count"
                )
            outcomes = tuple(rows)
        except Exception:
            # Preserve the old one-bad-node isolation if packing, native work,
            # or dense-result mapping rejects the complete workload.
            isolated = []
            for _position, _node, _path, prof, _columns in pending:
                try:
                    isolated.append(fast_values(prof))
                except Exception as exc:  # noqa: BLE001 - per-node fallback
                    isolated.append(exc)
            outcomes = tuple(isolated)

        for item, outcome in zip(pending, outcomes):
            position, node, path, _prof, columns = item
            if isinstance(outcome, Exception):
                prepared[position] = BoxPointAnalysis(
                    row=node.row,
                    col=node.col,
                    lat=node.lat,
                    lon=node.lon,
                    request_id=node.request_id,
                    npz_path=path,
                    values={},
                    error=f"{type(outcome).__name__}: {outcome}",
                )
            else:
                prepared[position] = BoxPointAnalysis(
                    row=node.row,
                    col=node.col,
                    lat=node.lat,
                    lon=node.lon,
                    request_id=node.request_id,
                    npz_path=path,
                    values=outcome,
                    columns=columns,
                )

    total = len(paths)
    done = 0
    stop_after_progress = False
    results = []
    for position, node in enumerate(plan.points):
        path = paths.get(node.request_id)
        result = prepared[position]
        if result is None:
            # Defensive only: every slot is assigned by loading or batch work.
            result = _empty_point(node, error="analysis produced no result")
        if path is not None:
            if stop_after_progress:
                result = _empty_point(node, error="cancelled")
            done += 1
            if progress is not None:
                progress(done, total, result)
            if cancelled is not None and cancelled():
                stop_after_progress = True
        results.append(result)
    return tuple(results)


def analyze_box(
    plan: BoxSamplePlan,
    outputs: Mapping[str, str],
    *,
    tiers=(FAST_TIER,),
    run_time=None,
    valid_time=None,
    fxx=0,
    progress: Callable[[int, int, BoxPointAnalysis], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> BoxAnalysis:
    """Analyze every extracted node of a plan into scalar fields.

    ``outputs`` maps a node's ``request_id`` to the ``.npz`` path produced for
    it. Nodes with no entry -- outside the domain, failed, or not yet extracted
    -- are carried as empty results so the lattice stays rectangular.

    ``progress`` is called as ``(done, total, point)`` after each analyzed node,
    which is what lets a field map fill in while the composite tier runs.
    """
    if not isinstance(plan, BoxSamplePlan):
        raise BoxAnalysisError("plan must be a BoxSamplePlan")
    wanted = tuple(str(tier) for tier in tiers)
    unknown = set(wanted) - set(TIERS)
    if unknown:
        raise BoxAnalysisError(
            f"unknown analysis tier(s): {', '.join(sorted(unknown))}"
        )
    paths = {str(key): str(value) for key, value in dict(outputs).items()}
    if set(wanted) == {FAST_TIER}:
        results = _analyze_fast_box_points(
            plan,
            paths,
            progress=progress,
            cancelled=cancelled,
        )
    else:
        total = len(paths)
        done = 0
        serial_results: list[BoxPointAnalysis] = []
        for node in plan.points:
            path = paths.get(node.request_id)
            if path is None:
                serial_results.append(_empty_point(node))
                continue
            if cancelled is not None and cancelled():
                serial_results.append(_empty_point(node, error="cancelled"))
                continue
            try:
                prof, _collection = load_profile(path)
                values = analyze_profile(prof, tiers=wanted)
                # The profile is open here, on a worker thread, having already
                # been decoded for the tier values. Taking the transect columns
                # now saves a later GUI-thread re-read for each slice/envelope.
                result = BoxPointAnalysis(
                    row=node.row,
                    col=node.col,
                    lat=node.lat,
                    lon=node.lon,
                    request_id=node.request_id,
                    npz_path=path,
                    values=values,
                    columns=transect_columns(prof),
                )
            except Exception as exc:  # noqa: BLE001 - one bad node must not end
                result = BoxPointAnalysis(
                    row=node.row,
                    col=node.col,
                    lat=node.lat,
                    lon=node.lon,
                    request_id=node.request_id,
                    npz_path=path,
                    values={},
                    error=f"{type(exc).__name__}: {exc}",
                )
            serial_results.append(result)
            done += 1
            if progress is not None:
                progress(done, total, result)
        results = tuple(serial_results)
    return BoxAnalysis(
        plan=plan,
        points=tuple(results),
        tiers=wanted,
        run_time=run_time,
        valid_time=valid_time,
        fxx=int(fxx),
    )


def _column_from_profile(prof, field, log_ladder) -> list[float | None]:
    """Interpolate one already-loaded profile column onto a log-pressure ladder.

    Shared by the precompute below and the on-demand re-read in
    :func:`_interpolated_column`, so a cached column and a freshly read one
    cannot disagree about the same node.

    Returns one optional value per ladder level. A level below the node's
    terrain or above its top becomes ``None`` rather than an extrapolated
    number: the absence is the information.
    """
    from sharpmod import backends

    blank = [None] * int(log_ladder.size)
    try:
        pres = np.ma.filled(np.ma.asarray(prof.pres, dtype=float), -9999.0)
        raw = np.ma.filled(np.ma.asarray(getattr(prof, field), dtype=float), -9999.0)
        # Drop sentinel rows before the logarithm: log10 of a negative sentinel
        # would both warn and poison the interpolation, and this suite runs with
        # warnings as errors.
        usable = (pres > 0.0) & (raw > _MISSING_LIMIT)
        if int(np.count_nonzero(usable)) < 2:
            return blank
        coordinate = np.log10(pres[usable])
        if field == "wdir":
            # Direction is circular: scalar interpolation between 350 and 10
            # degrees points south.  Interpolate the matching wind components
            # and reconstruct the angle so the path crosses north instead.
            speed = np.ma.filled(
                np.ma.asarray(getattr(prof, "wspd"), dtype=float), -9999.0
            )
            usable &= speed > _MISSING_LIMIT
            if int(np.count_nonzero(usable)) < 2:
                return blank
            coordinate = np.log10(pres[usable])
            u, v = backends.wind_to_components(raw[usable], speed[usable])
            interp_u = backends.interpolate_1d(log_ladder, coordinate, u)
            interp_v = backends.interpolate_1d(log_ladder, coordinate, v)
            interpolated, _speed = backends.components_to_wind(interp_u, interp_v)
        else:
            interpolated = backends.interpolate_1d(log_ladder, coordinate, raw[usable])
    except Exception:
        return blank
    return [
        _finite(value) for value in np.atleast_1d(np.asarray(interpolated, dtype=float))
    ]


def transect_columns(prof) -> dict[str, tuple[float | None, ...]]:
    """Interpolate every transect field of one profile onto the shared ladder.

    Called once per node by :func:`analyze_box` while the profile is already
    open on the extraction worker, so drawing a slice or an envelope later never
    has to re-read a file on the GUI thread.

    Five fields at 37 levels measures about 6 kB per node -- 660 kB for a
    108-node box, 6.3 MB for a full twelve-hour 1024-node sequence -- which is
    why the *columns* are kept while the profiles that produced them are still
    thrown away.
    """
    return {
        field: tuple(_column_from_profile(prof, field, _DEFAULT_LOG_LADDER))
        for field in TRANSECT_FIELDS
    }


def _interpolated_column(point, field, log_ladder) -> list[float | None]:
    """Return one node's profile column sampled on ``log_ladder``.

    Prefers the column cached during analysis. Only a caller that asked for a
    ladder other than :data:`DEFAULT_TRANSECT_LEVELS` pays to re-read the file,
    which is why the default path never touches the disk.
    """
    blank = [None] * int(log_ladder.size)
    if point is None:
        return blank
    if log_ladder.shape == _DEFAULT_LOG_LADDER.shape and np.array_equal(
        log_ladder, _DEFAULT_LOG_LADDER
    ):
        cached = point.columns.get(str(field))
        if cached is not None:
            return list(cached)
    if not point.npz_path or not os.path.isfile(point.npz_path):
        return blank
    try:
        prof, _collection = load_profile(point.npz_path)
    except Exception:
        return blank
    return _column_from_profile(prof, field, log_ladder)


def _evaluate(
    criteria: Iterable[Criterion],
    values: Mapping[str, float],
) -> bool | None:
    """Combine criteria with AND, propagating "unknown" rather than failing.

    A single unsatisfied criterion is a definite ``False`` even if another field
    is missing -- the node is out regardless. Only when nothing has failed and
    something is unreadable is the answer ``None``.
    """
    incomplete = False
    for item in criteria:
        verdict = item.matches(values)
        if verdict is False:
            return False
        if verdict is None:
            incomplete = True
    return None if incomplete else True


def _empty_point(node: BoxSamplePoint, *, error=None) -> BoxPointAnalysis:
    return BoxPointAnalysis(
        row=node.row,
        col=node.col,
        lat=node.lat,
        lon=node.lon,
        request_id=node.request_id,
        npz_path=None,
        values={},
        error=error
        if error is not None
        else (None if node.in_domain else "outside model domain"),
    )


__all__ = [
    "COMPOSITE_TIER",
    "INGREDIENT_SCREENS",
    "CoverageResult",
    "Criterion",
    "describe_criteria",
    "screen",
    "screen_names",
    "DEFAULT_TRANSECT_LEVELS",
    "FAST_TIER",
    "KINEMATIC_LAYER_TOPS",
    "PARAMETERS",
    "PARAMETERS_BY_KEY",
    "TIERS",
    "TRANSECT_FIELDS",
    "BoxAnalysis",
    "BoxAnalysisError",
    "BoxFieldStats",
    "EnvelopeResult",
    "BoxParameter",
    "BoxPointAnalysis",
    "BoxSequence",
    "analyze_box",
    "analyze_box_sequence",
    "analyze_profile",
    "composite_values",
    "fast_values",
    "fast_values_many",
    "summary_values",
    "load_profile",
    "parameter",
    "parameter_groups",
    "parameters_for_tiers",
    "transect_columns",
]

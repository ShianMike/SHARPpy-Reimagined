"""Derived-parameter fields across a box of point soundings.

This turns the ``.npz`` files produced for a :class:`~sharpmod.analysis.box_sounding.\
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

from sharpmod.analysis.box_sounding import BoxSamplePlan, BoxSamplePoint


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

    @property
    def display_units(self) -> str:
        """Return reader-facing units without changing serialized raw units.

        The registry's ``units`` field is part of saved threshold data,
        so changing values such as ``m2/s2`` in place would create a needless
        compatibility migration.  Presentation surfaces use this property to
        render the conventional scientific glyphs consistently instead.
        """
        return {
            "C": "°C",
            "F": "°F",
            "C/km": "°C/km",
            "deg": "°",
            "m2/s2": "m²/s²",
            "m3/s3": "m³/s³",
        }.get(self.units, self.units)

    @property
    def display_label(self) -> str:
        """Return the shared picker/table label, including units when present."""
        suffix = f" ({self.display_units})" if self.display_units else ""
        return f"{self.label}{suffix}"

    def format(self, value, *, with_units: bool = False) -> str:
        """Render one value consistently, or an em dash when absent."""
        if value is None:
            return "\u2014"
        rendered = f"{float(value):.{self.decimals}f}"
        if with_units and self.display_units:
            rendered += f" {self.display_units}"
        return rendered


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
        units = f" {item.display_units}" if item.display_units else ""
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


def _profile_surface_index(prof, tmpc) -> int:
    """Return the profile's first usable thermodynamic surface level.

    Portable model profiles carry an explicit ``sfc`` index.  Older observed
    sounding decoders do not, and can prepend mandatory pressure levels whose
    temperature and wind are both masked.  SHARPpy defines the surface as the
    first level with a reported temperature, so mirror that contract here
    instead of silently treating a placeholder row as the surface.
    """
    explicit = getattr(prof, "sfc", None)
    try:
        index = int(explicit)
        exact = not isinstance(explicit, (bool, np.bool_)) and (
            float(explicit) == float(index)
        )
    except (TypeError, ValueError, OverflowError):
        exact = False
        index = -1
    if exact and 0 <= index < len(tmpc) and _finite(tmpc[index]) is not None:
        return index

    candidates = np.flatnonzero(
        np.isfinite(tmpc) & (np.asarray(tmpc, dtype=float) > _MISSING_LIMIT)
    )
    if candidates.size == 0:
        raise BoxAnalysisError("sounding has no usable surface temperature")
    return int(candidates[0])








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
        columns = []
        for prof in profiles:
            profile_columns = _profile_columns(prof)
            columns.append(
                (
                    *profile_columns,
                    _profile_surface_index(prof, profile_columns[2]),
                )
            )
        columns = tuple(columns)
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


from sharpmod.analysis.box_analysis_core import (  # noqa: E402
    EnvelopeResult,
    BoxAnalysis,
    BoxSequence
)


from sharpmod.analysis.box_analysis_fast import (  # noqa: E402
    _fast_values,
    _fast_values_from_batch,
    _fast_effective_stp_cin,
    composite_values,
    _analyze_fast_box_points,
    analyze_box
)

"""Qt-independent geographic-box sounding sampling.

A *box sounding* is a rectangle drawn on the map turned into many point
soundings: the box is sampled on a lattice, every node becomes one request, and
because every node shares a model/run/forecast-hour/member the whole set lands
in a single :mod:`sharpmod.batch_extract` group. That group holds one
``ModelHourCache`` lease and decodes every node out of one downloaded field
subset, so a 64-point box costs one download rather than 64.

Two rules shape the sampling and are the reason this module exists rather than
a bare ``linspace`` at the call site:

*Never sample finer than the model.* The request spacing is snapped up to a
whole multiple of the product's published grid spacing (see
``ModelConfig.grid_spacing_km``). Two nodes inside one grid cell would decode
the same column twice and then invite the reader to see a gradient between two
copies of one number.

*Never silently exceed a budget.* Each node runs a full parcel/kinematics
analysis downstream, so the node count is capped and the spacing is coarsened
until it fits. The coarsening is recorded in :attr:`BoxSamplePlan.notes` so the
caller can say why the box is coarser than asked for instead of the user
guessing.

Nodes that fall outside the model domain are kept in the plan with
``in_domain=False`` rather than dropped. The lattice therefore stays a true
rectangle for rendering, and a partly-covered box shows honest holes instead of
a silently reshaped grid.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

from sharpmod.tools import model_extract


#: Hard ceiling on nodes in one box, whatever the caller asks for. Each node is
#: a complete sounding analysis, so this bounds CPU and memory, not transfer.
MAX_BOX_POINTS = 256

#: Default node count aimed for when the caller gives neither a spacing nor a
#: target. Roughly a 8x8 lattice: dense enough to show a gradient across a
#: mesoscale box, cheap enough to finish while the forecaster is still looking.
DEFAULT_TARGET_POINTS = 64

#: Point-only providers answer one HTTP request per node instead of sharing one
#: downloaded grid, so a box against them is linear in network cost. They get a
#: much smaller budget and a note explaining it.
MAX_POINT_PROVIDER_POINTS = 24
DEFAULT_POINT_PROVIDER_TARGET = 12

#: Mean km per degree of latitude. The sampler only needs enough precision to
#: choose a node count, so one spherical constant beats carrying a geodesy
#: dependency into a module that exists to pick a lattice.
KM_PER_DEG_LAT = 111.32

#: Smallest span that counts as a deliberate drag rather than a stray click,
#: in degrees (~11 m). Below this the box has no area to sample.
MIN_SPAN_DEG = 1.0e-4

#: Longitude convergence is floored so a box drawn near a pole cannot divide by
#: a vanishing cosine and demand an unbounded number of columns.
_MIN_COS_LAT = 0.05


class BoxSoundingError(Exception):
    """Base class for invalid box regions and unsatisfiable sample plans."""


class BoxRegionError(BoxSoundingError):
    """The requested rectangle is degenerate or out of range."""


class BoxSampleError(BoxSoundingError):
    """The rectangle cannot be sampled for the requested model."""


def _normalize_lon180(lon) -> float:
    """Wrap a longitude into ``[-180, 180)``."""
    return ((float(lon) + 180.0) % 360.0) - 180.0


def km_per_deg_lon(lat) -> float:
    """Return km per degree of longitude at ``lat``, floored near the poles."""
    cos_lat = math.cos(math.radians(float(lat)))
    return KM_PER_DEG_LAT * max(_MIN_COS_LAT, abs(cos_lat))


def _even_nodes(start: float, span: float, count: int) -> list[float]:
    """Return ``count`` nodes spanning ``start`` to ``start + span``.

    A single node is placed at the span's midpoint rather than at ``start``: one
    node means the box is thinner than the grid can resolve, and its centre is
    the representative sample, not its edge.
    """
    if count <= 1:
        return [start + span / 2.0]
    step = span / float(count - 1)
    return [start + step * index for index in range(count)]


@dataclass(frozen=True)
class BoxRegion:
    """A lat/lon rectangle, stored so the antimeridian cannot be ambiguous.

    The east edge is held as a *span* east of :attr:`lon0` rather than as a
    second longitude. Two normalized corners cannot distinguish a 20-degree box
    straddling the antimeridian from the 340-degree box that is its complement;
    a west edge plus an eastward span always can.
    """

    lat0: float
    lat1: float
    lon0: float
    lon_span: float

    def __post_init__(self):
        for name in ("lat0", "lat1", "lon0", "lon_span"):
            value = getattr(self, name)
            if not math.isfinite(value):
                raise BoxRegionError(f"box {name} must be a finite number")
        # Interface-neutral wording: this core is shared by the map gesture and
        # the command line, and "drag a rectangle" would be wrong in one of them.
        if self.lat1 - self.lat0 < MIN_SPAN_DEG:
            raise BoxRegionError(
                "box latitude span is too small to sample; the two corners "
                "must differ in latitude"
            )
        if self.lon_span < MIN_SPAN_DEG:
            raise BoxRegionError(
                "box longitude span is too small to sample; the two corners "
                "must differ in longitude"
            )
        if self.lon_span > 360.0:
            raise BoxRegionError("box longitude span cannot exceed 360 degrees")
        if not -90.0 <= self.lat0 <= 90.0 or not -90.0 <= self.lat1 <= 90.0:
            raise BoxRegionError("box latitudes must lie within [-90, 90]")

    @classmethod
    def from_corners(cls, lat_a, lon_a, lat_b, lon_b) -> "BoxRegion":
        """Build a region from two dragged corners, in any order.

        Longitudes may arrive unwrapped (``170`` to ``190``). That is
        deliberate: the raw difference is the span the user actually dragged,
        including across the antimeridian, so only the west edge is wrapped.
        """
        try:
            lat_a = float(lat_a)
            lat_b = float(lat_b)
            lon_a = float(lon_a)
            lon_b = float(lon_b)
        except (TypeError, ValueError) as exc:
            raise BoxRegionError(f"box corners must be numeric: {exc}") from exc
        lat0, lat1 = sorted((lat_a, lat_b))
        # Clamp before measuring: a drag that runs off the top of the map should
        # become a box that stops at the pole, not an invalid region.
        lat0 = max(-90.0, min(90.0, lat0))
        lat1 = max(-90.0, min(90.0, lat1))
        west, east = sorted((lon_a, lon_b))
        span = min(360.0, east - west)
        return cls(
            lat0=lat0,
            lat1=lat1,
            lon0=_normalize_lon180(west),
            lon_span=span,
        )

    @property
    def lat_span(self) -> float:
        return self.lat1 - self.lat0

    @property
    def lon1(self) -> float:
        """The east edge, wrapped into ``[-180, 180)``."""
        return _normalize_lon180(self.lon0 + self.lon_span)

    @property
    def crosses_antimeridian(self) -> bool:
        return self.lon0 + self.lon_span > 180.0

    @property
    def center_lat(self) -> float:
        return (self.lat0 + self.lat1) / 2.0

    @property
    def center_lon(self) -> float:
        return _normalize_lon180(self.lon0 + self.lon_span / 2.0)

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        """Return ``(lon0, lon1, lat0, lat1)``, matching ``model_extract``.

        A wrapped box yields ``lon0 > lon1``, which is exactly how
        ``model_extract._longitude_segments`` already encodes an
        antimeridian-crossing extent.
        """
        return (self.lon0, self.lon1, self.lat0, self.lat1)

    @property
    def height_km(self) -> float:
        return self.lat_span * KM_PER_DEG_LAT

    @property
    def width_km(self) -> float:
        return self.lon_span * km_per_deg_lon(self.center_lat)

    @property
    def area_km2(self) -> float:
        return self.width_km * self.height_km

    def contains(self, lat, lon) -> bool:
        """Whether a point lies inside the rectangle, wrapping included."""
        lat = float(lat)
        if not self.lat0 <= lat <= self.lat1:
            return False
        # Measure eastward from the west edge so the test never has to branch
        # on whether the box wraps.
        delta = (_normalize_lon180(lon) - self.lon0) % 360.0
        return delta <= self.lon_span or math.isclose(
            delta, self.lon_span, abs_tol=1.0e-9
        )

    def longitude_segments(self) -> tuple[tuple[float, float], ...]:
        """Split into wrapped ``(west, east)`` spans for planar drawing."""
        if not self.crosses_antimeridian:
            return ((self.lon0, self.lon0 + self.lon_span),)
        return ((self.lon0, 180.0), (-180.0, self.lon1))

    def label(self) -> str:
        """Return a compact reader-facing description of the rectangle."""
        return (
            f"{abs(self.lat0):.2f}{'N' if self.lat0 >= 0 else 'S'}-"
            f"{abs(self.lat1):.2f}{'N' if self.lat1 >= 0 else 'S'}, "
            f"{abs(self.lon0):.2f}{'E' if self.lon0 >= 0 else 'W'}-"
            f"{abs(self.lon1):.2f}{'E' if self.lon1 >= 0 else 'W'}"
        )


@dataclass(frozen=True)
class BoxSamplePoint:
    """One lattice node in a box sample plan.

    ``row`` counts from the north edge and ``col`` from the west edge, so the
    indices match the order a field renderer walks pixels.
    """

    row: int
    col: int
    lat: float
    lon: float
    in_domain: bool

    @property
    def request_id(self) -> str:
        """Stable, sortable, filesystem-safe identity for this node."""
        return f"r{self.row:03d}c{self.col:03d}"


@dataclass(frozen=True)
class BoxSamplePlan:
    """A resolved lattice for one box, model, and budget."""

    region: BoxRegion
    model_key: str
    model_label: str
    spacing_km: float
    native_spacing_km: float
    rows: int
    cols: int
    points: tuple[BoxSamplePoint, ...]
    notes: tuple[str, ...] = ()
    point_only_provider: bool = False

    @property
    def count(self) -> int:
        """Total lattice nodes, including any outside the model domain."""
        return len(self.points)

    @property
    def shape(self) -> tuple[int, int]:
        return (self.rows, self.cols)

    @property
    def requestable_points(self) -> tuple[BoxSamplePoint, ...]:
        """Nodes inside the model domain, i.e. the ones worth extracting."""
        return tuple(point for point in self.points if point.in_domain)

    @property
    def skipped_points(self) -> tuple[BoxSamplePoint, ...]:
        return tuple(point for point in self.points if not point.in_domain)

    @property
    def oversampled(self) -> bool:
        """Whether the spacing is finer than the model's own grid.

        Always ``False`` for a plan built by :func:`plan_box_samples`; kept as
        an explicit property so a hand-built plan cannot quietly claim
        resolution the model does not have.
        """
        return self.spacing_km < self.native_spacing_km

    @property
    def estimated_downloads(self) -> int:
        """Model-hour transfers this plan needs.

        One for a gridded product, because every node shares a single field
        subset. One per node for a point-only provider, which is why those get
        a smaller budget.
        """
        if self.point_only_provider:
            return len(self.requestable_points)
        return 1

    def point_at(self, row: int, col: int) -> BoxSamplePoint | None:
        """Return the node at ``(row, col)``, or ``None`` when out of range."""
        if not 0 <= row < self.rows or not 0 <= col < self.cols:
            return None
        index = row * self.cols + col
        try:
            return self.points[index]
        except IndexError:
            return None


def _lattice_shape(region: BoxRegion, spacing_km: float) -> tuple[int, int]:
    """Return ``(rows, cols)`` for a spacing, at least 1x1."""
    lat_step = spacing_km / KM_PER_DEG_LAT
    lon_step = spacing_km / km_per_deg_lon(region.center_lat)
    rows = max(1, int(round(region.lat_span / lat_step)) + 1)
    cols = max(1, int(round(region.lon_span / lon_step)) + 1)
    return rows, cols


def _snap_to_native(spacing_km: float, native_km: float) -> float:
    """Round a spacing up to a whole multiple of the model's grid spacing."""
    multiple = max(1, int(round(spacing_km / native_km)))
    return multiple * native_km


def plan_box_samples(
    model,
    region: BoxRegion,
    *,
    spacing_km=None,
    target_points=None,
    max_points=None,
) -> BoxSamplePlan:
    """Resolve a box into a concrete, budgeted lattice of sounding nodes.

    ``spacing_km`` requests an explicit node spacing; ``target_points`` instead
    asks for roughly that many nodes and lets the spacing follow. Both are
    snapped up to a multiple of the model's grid spacing, and both are coarsened
    further if the result would exceed the node budget.
    """
    config = (
        model
        if isinstance(model, model_extract.ModelConfig)
        else model_extract.get_config(model)
    )
    if config.unavailable_reason:
        raise BoxSampleError(
            f"{config.label} cannot produce a sounding: "
            f"{config.unavailable_reason}"
        )
    if not isinstance(region, BoxRegion):
        raise BoxSampleError("region must be a BoxRegion")
    if not model_extract.domain_intersects_bounds(config, region.bounds):
        raise BoxSampleError(
            f"{config.label} covers {config.domain}; the requested box "
            f"({region.label()}) lies entirely outside that domain"
        )

    native = model_extract.grid_spacing_km(config)
    point_only = model_extract.point_only_provider(config)
    notes: list[str] = []

    ceiling = MAX_POINT_PROVIDER_POINTS if point_only else MAX_BOX_POINTS
    if max_points is None:
        budget = ceiling
    else:
        try:
            budget = int(max_points)
        except (TypeError, ValueError) as exc:
            raise BoxSampleError("max_points must be an integer") from exc
        if budget < 1:
            raise BoxSampleError("max_points must be at least one")
        budget = min(budget, ceiling)
    if point_only:
        notes.append(
            f"{config.label} answers one request per point rather than sharing "
            f"one downloaded grid, so this box is limited to "
            f"{budget} soundings."
        )

    if spacing_km is not None and target_points is not None:
        raise BoxSampleError(
            "give either spacing_km or target_points, not both"
        )

    if spacing_km is not None:
        try:
            requested = float(spacing_km)
        except (TypeError, ValueError) as exc:
            raise BoxSampleError("spacing_km must be a number") from exc
        if not math.isfinite(requested) or requested <= 0.0:
            raise BoxSampleError("spacing_km must be positive")
        if requested < native:
            # State only the clamp, not a final figure: the budget pass below
            # may coarsen further and will add its own note if it does.
            notes.append(
                f"Requested {requested:.1f} km spacing is finer than the "
                f"{native:.1f} km {config.label} grid and was raised to "
                f"{native:.1f} km so no two soundings come from one grid cell."
            )
    else:
        if target_points is None:
            target = (
                DEFAULT_POINT_PROVIDER_TARGET if point_only
                else DEFAULT_TARGET_POINTS
            )
        else:
            try:
                target = int(target_points)
            except (TypeError, ValueError) as exc:
                raise BoxSampleError("target_points must be an integer") from exc
            if target < 1:
                raise BoxSampleError("target_points must be at least one")
        target = min(target, budget)
        # Roughly square cells: a lattice of `target` nodes over this area has
        # cells of side sqrt(area/target).
        area = max(region.area_km2, 1.0e-6)
        requested = math.sqrt(area / float(target))

    spacing = _snap_to_native(max(requested, native), native)
    rows, cols = _lattice_shape(region, spacing)

    # Coarsen until the lattice fits the budget, staying on whole multiples of
    # the native spacing. Node count falls as the square of spacing, so jumping
    # straight to sqrt(count/budget) converges in one or two passes instead of
    # stepping one multiple at a time -- which for a continental box against a
    # 3 km grid would be hundreds of passes. The `+ 1` floor guarantees every
    # pass advances, so this cannot stall.
    coarsened_from = None
    guard = 0
    while rows * cols > budget and guard < 64:
        guard += 1
        if coarsened_from is None:
            coarsened_from = spacing
        scale = math.sqrt((rows * cols) / float(budget))
        multiple = max(
            int(round(spacing / native)) + 1,
            int(math.ceil(spacing * scale / native)),
        )
        spacing = multiple * native
        rows, cols = _lattice_shape(region, spacing)
    if coarsened_from is not None:
        notes.append(
            f"Coarsened from {coarsened_from:.1f} km to {spacing:.1f} km to "
            f"stay within {budget} soundings ({rows}x{cols})."
        )

    lats = _even_nodes(region.lat0, region.lat_span, rows)
    lons = _even_nodes(region.lon0, region.lon_span, cols)

    points: list[BoxSamplePoint] = []
    # North-to-south row order matches raster/screen order, so a field renderer
    # can walk the tuple directly.
    for row, lat in enumerate(reversed(lats)):
        for col, lon_raw in enumerate(lons):
            lon = _normalize_lon180(lon_raw)
            points.append(BoxSamplePoint(
                row=row,
                col=col,
                lat=round(float(lat), 6),
                lon=round(float(lon), 6),
                in_domain=bool(
                    model_extract.point_in_domain(config, lat, lon)
                ),
            ))

    inside = sum(1 for point in points if point.in_domain)
    if not inside:
        raise BoxSampleError(
            f"no sampled point falls inside the {config.label} domain "
            f"({config.domain}); move or enlarge the box"
        )
    if inside < len(points):
        notes.append(
            f"{len(points) - inside} of {len(points)} sampled points fall "
            f"outside the {config.label} domain and will be left blank."
        )

    return BoxSamplePlan(
        region=region,
        model_key=config.key,
        model_label=config.label,
        spacing_km=float(spacing),
        native_spacing_km=float(native),
        rows=rows,
        cols=cols,
        points=tuple(points),
        notes=tuple(notes),
        point_only_provider=bool(point_only),
    )


def box_requests(
    plan: BoxSamplePlan,
    *,
    run_time,
    fxx: int = 0,
    member=None,
    loc=None,
) -> tuple:
    """Convert a plan's in-domain nodes into ``batch_extract`` requests.

    Every request carries the same model, run, forecast hour, and member, so
    :class:`~sharpmod.batch_extract.BatchExtractor` groups them into one
    model-hour lease and decodes them all from a single download.
    """
    # Imported here rather than at module scope so this module stays importable
    # without the native GRIB stack; only an actual extraction needs it.
    from sharpmod.batch_extract import BatchRequest

    if not isinstance(plan, BoxSamplePlan):
        raise BoxSampleError("plan must be a BoxSamplePlan")
    nodes = plan.requestable_points
    if not nodes:
        raise BoxSampleError("this plan has no in-domain points to request")
    prefix = str(loc).strip() if loc else plan.model_label
    return tuple(
        BatchRequest(
            id=node.request_id,
            model=plan.model_key,
            lat=node.lat,
            lon=node.lon,
            run_time=run_time,
            fxx=int(fxx),
            output=f"{node.request_id}.npz",
            loc=f"{prefix} {node.request_id}",
            member=str(member) if member else None,
        )
        for node in nodes
    )


#: Most forecast hours one box may be stepped through. Each hour is a separate
#: model-hour download -- the whole saving of a box applies *within* an hour, not
#: across them -- so this bounds transfer, which is the real cost of a sequence.
MAX_BOX_HOURS = 12

#: Ceiling on hours x points for one sequence. A sequence multiplies both the
#: transfer and the analysis, so the two budgets have to be spent together
#: rather than each being checked alone.
MAX_SEQUENCE_NODES = 1024


def normalize_hours(hours) -> tuple[int, ...]:
    """Return sorted, unique, validated forecast hours for a sequence."""
    try:
        values = tuple(sorted({int(hour) for hour in hours}))
    except (TypeError, ValueError) as exc:
        raise BoxSampleError("forecast hours must be integers") from exc
    if not values:
        raise BoxSampleError("a sequence needs at least one forecast hour")
    if any(hour < 0 for hour in values):
        raise BoxSampleError("forecast hours cannot be negative")
    if len(values) > MAX_BOX_HOURS:
        raise BoxSampleError(
            f"a box sequence is limited to {MAX_BOX_HOURS} forecast hours "
            f"({len(values)} requested); each hour is its own download"
        )
    return values


def sequence_request_id(hour, node: BoxSamplePoint) -> str:
    """Return a request id unique across both hour and lattice position."""
    return f"f{int(hour):03d}{node.request_id}"


def box_sequence_requests(
    plan: BoxSamplePlan,
    *,
    run_time,
    hours,
    member=None,
    loc=None,
) -> tuple:
    """Convert a plan into requests covering several forecast hours.

    Each hour becomes its own model-hour group, so an hour is still one download
    shared by every point in it; the sequence simply pays that once per hour.
    Outputs are written under a ``fNNN/`` directory per hour so one hour's
    results can be analyzed on their own.
    """
    from sharpmod.batch_extract import BatchRequest

    if not isinstance(plan, BoxSamplePlan):
        raise BoxSampleError("plan must be a BoxSamplePlan")
    nodes = plan.requestable_points
    if not nodes:
        raise BoxSampleError("this plan has no in-domain points to request")
    values = normalize_hours(hours)
    total = len(nodes) * len(values)
    if total > MAX_SEQUENCE_NODES:
        raise BoxSampleError(
            f"{len(values)} hours x {len(nodes)} points is {total} soundings, "
            f"above the {MAX_SEQUENCE_NODES} allowed for one sequence; "
            "use fewer hours or a coarser box"
        )
    prefix = str(loc).strip() if loc else plan.model_label
    requests = []
    for hour in values:
        for node in nodes:
            requests.append(BatchRequest(
                id=sequence_request_id(hour, node),
                model=plan.model_key,
                lat=node.lat,
                lon=node.lon,
                run_time=run_time,
                fxx=int(hour),
                output=f"f{int(hour):03d}/{node.request_id}.npz",
                loc=f"{prefix} {node.request_id}",
                member=str(member) if member else None,
            ))
    return tuple(requests)


def describe_plan(plan: BoxSamplePlan) -> str:
    """Return a multi-line, reader-facing summary of a sample plan."""
    if not isinstance(plan, BoxSamplePlan):
        raise BoxSampleError("plan must be a BoxSamplePlan")
    region = plan.region
    lines = [
        f"Model      {plan.model_label} ({plan.model_key})",
        f"Box        {region.label()}",
        f"Size       {region.width_km:.0f} x {region.height_km:.0f} km",
        f"Lattice    {plan.rows} x {plan.cols} = {plan.count} points "
        f"({len(plan.requestable_points)} in domain)",
        f"Spacing    {plan.spacing_km:.1f} km "
        f"(native {plan.native_spacing_km:.1f} km)",
        f"Downloads  {plan.estimated_downloads}",
    ]
    lines.extend(f"Note       {note}" for note in plan.notes)
    return "\n".join(lines)


def region_from_bounds(bounds: Sequence[float]) -> BoxRegion:
    """Build a region from ``(lon0, lon1, lat0, lat1)``.

    ``lon0 > lon1`` is read as an antimeridian-crossing extent, matching
    ``model_extract``'s bounds convention.
    """
    try:
        lon0, lon1, lat0, lat1 = (float(value) for value in bounds)
    except (TypeError, ValueError) as exc:
        raise BoxRegionError(
            "bounds must be four numbers (lon0, lon1, lat0, lat1)"
        ) from exc
    span = (lon1 - lon0) % 360.0
    # A full-width extent (-180 to 180) leaves a zero remainder. Read that as
    # the whole world rather than a degenerate box; a genuinely zero-width
    # extent has identical edges and is rejected by BoxRegion.
    if span < MIN_SPAN_DEG and abs(lon1 - lon0) >= 180.0:
        span = 360.0
    return BoxRegion(
        lat0=min(lat0, lat1),
        lat1=max(lat0, lat1),
        lon0=_normalize_lon180(lon0),
        lon_span=span,
    )


__all__ = [
    "DEFAULT_POINT_PROVIDER_TARGET",
    "DEFAULT_TARGET_POINTS",
    "KM_PER_DEG_LAT",
    "MAX_BOX_HOURS",
    "MAX_BOX_POINTS",
    "MAX_POINT_PROVIDER_POINTS",
    "MAX_SEQUENCE_NODES",
    "MIN_SPAN_DEG",
    "box_sequence_requests",
    "normalize_hours",
    "sequence_request_id",
    "BoxRegion",
    "BoxRegionError",
    "BoxSampleError",
    "BoxSamplePlan",
    "BoxSamplePoint",
    "BoxSoundingError",
    "box_requests",
    "describe_plan",
    "km_per_deg_lon",
    "plan_box_samples",
    "region_from_bounds",
]

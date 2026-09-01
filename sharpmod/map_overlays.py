"""Frozen geometry model for toggleable, time-aware map overlays.

An *overlay* is a set of geographic polygons the picker maps draw on top of
the cached basemap raster: SPC convective outlooks today, other products
later.  Three constraints shaped this module.

**It holds no Qt import.**  Overlay payloads are decoded on a worker thread
and the decoded result is handed to the GUI thread, so the model, its
validation, and the time-window logic are all exercised in tests without a
``QApplication``.  Qt colours are constructed by the paint path from the
plain ``#rrggbb`` strings kept here.

**Geometry is prepared once and frozen**, mirroring
:func:`sharpmod.gui_maps._prepare_basemap_layers`.  Every shape carries a
precomputed ``(min_lon, max_lon, min_lat, max_lat)`` box -- the same tuple
order :meth:`sharpmod.gui_maps.StationMapWidget._draw_layer` already unpacks
-- so the paint path rejects off-screen shapes without transforming a single
point.  Immutability lets one decoded layer be shared by several map widgets.

**Decoding is bounded and total.**  The payload is remote, so ring counts and
point counts are capped and every coordinate is range-checked.  Malformed
features are skipped rather than raising, because a partially drawable
outlook is more useful to a forecaster than a blank map, and an overlay must
never be able to prevent the picker from rendering.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

#: Hard caps bounding the cost of decoding one remote overlay payload. The SPC
#: categorical product carries at most six features, so these are generous
#: enough to never reject real data while still refusing a hostile response.
MAX_SHAPES_PER_LAYER = 256
MAX_RINGS_PER_SHAPE = 512
MAX_POINTS_PER_RING = 100_000
MAX_POINTS_PER_LAYER = 500_000

#: A ring needs three distinct vertices to enclose any area.
MIN_RING_POINTS = 3


def _finite(value: Any) -> float | None:
    """Return ``value`` as a finite float, or ``None`` if it is not one."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _ring_from_coordinates(
        raw_ring: Any,
) -> tuple[tuple[float, float], ...] | None:
    """Decode one GeoJSON linear ring into validated ``(lon, lat)`` points.

    Returns ``None`` when the ring is not usable.  Consecutive duplicate
    vertices are dropped: SPC rings repeat the first point to close the ring,
    and a polygon path closes itself, so keeping them would add a zero-length
    segment that shows up as a stroking artefact at every joint.
    """
    if not isinstance(raw_ring, (list, tuple)):
        return None
    if len(raw_ring) > MAX_POINTS_PER_RING:
        return None

    points: list[tuple[float, float]] = []
    for raw_point in raw_ring:
        if not isinstance(raw_point, (list, tuple)) or len(raw_point) < 2:
            continue
        lon = _finite(raw_point[0])
        lat = _finite(raw_point[1])
        if lon is None or lat is None:
            continue
        if not -180.0 <= lon <= 180.0 or not -90.0 <= lat <= 90.0:
            continue
        if points and points[-1] == (lon, lat):
            continue
        points.append((lon, lat))

    # A closed ring repeats its first vertex last; drop it for the same reason.
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    if len(points) < MIN_RING_POINTS:
        return None
    return tuple(points)


def rings_from_geometry(
        geometry: Any,
) -> tuple[tuple[tuple[tuple[float, float], ...], ...], ...]:
    """Split a GeoJSON geometry into per-polygon ring groups.

    Handles both ``Polygon`` and ``MultiPolygon`` because SPC mixes the two in
    a single collection: in one payload the general-thunder area arrived as a
    ``Polygon`` while the marginal area arrived as a ``MultiPolygon``.

    Each returned group is ``(exterior_ring, *hole_rings)``.  Holes are real
    and load-bearing here: the SPC ``nolyr`` products punch out the area where
    a higher category applies, which is what lets the paint path fill each
    category exactly once instead of blending stacked translucent layers.
    """
    if not isinstance(geometry, dict):
        return ()
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, (list, tuple)):
        return ()

    if kind == "Polygon":
        raw_polygons: tuple[Any, ...] = (coordinates,)
    elif kind == "MultiPolygon":
        raw_polygons = tuple(coordinates)
    else:
        return ()

    groups: list[tuple[tuple[tuple[float, float], ...], ...]] = []
    for raw_polygon in raw_polygons:
        if not isinstance(raw_polygon, (list, tuple)):
            continue
        rings: list[tuple[tuple[float, float], ...]] = []
        for raw_ring in raw_polygon[:MAX_RINGS_PER_SHAPE]:
            ring = _ring_from_coordinates(raw_ring)
            if ring is not None:
                rings.append(ring)
        if rings:
            groups.append(tuple(rings))
    return tuple(groups)


def bounds_of(
        rings: tuple[tuple[tuple[float, float], ...], ...],
) -> tuple[float, float, float, float] | None:
    """Return ``(min_lon, max_lon, min_lat, max_lat)`` over every ring.

    The tuple order matches the basemap's prepared geometry so overlay shapes
    can be culled by the same comparison the basemap layers already use.
    """
    min_lon = min_lat = float("inf")
    max_lon = max_lat = float("-inf")
    for ring in rings:
        for lon, lat in ring:
            min_lon = min(min_lon, lon)
            max_lon = max(max_lon, lon)
            min_lat = min(min_lat, lat)
            max_lat = max(max_lat, lat)
    if min_lon > max_lon or min_lat > max_lat:
        return None
    return (min_lon, max_lon, min_lat, max_lat)


@dataclass(frozen=True)
class OverlayShape:
    """One filled, outlined region of an overlay.

    ``rings[0]`` is the exterior boundary and any further rings are holes.
    ``rank`` orders drawing within a layer so that, for a product whose
    categories are ordered by severity, the more severe outline is painted
    last and therefore reads on top where boundaries coincide.
    """

    rings: tuple[tuple[tuple[float, float], ...], ...]
    bounds: tuple[float, float, float, float]
    stroke: str
    fill: str | None = None
    label: str = ""
    description: str = ""
    rank: int = 0
    #: Fill with a diagonal hatch instead of a flat wash. Used for areas that
    #: qualify an area already shaded by another shape -- SPC's "significant
    #: severe" region overlaps the probability band beneath it, and a solid
    #: wash would hide it rather than annotate it.
    hatch: bool = False
    #: Which graded hatch to draw, when the qualifier comes in levels. SPC's
    #: Conditional Intensity Groups are 1 to 3 and are distinguished only by
    #: pattern, since every level publishes the same grey; ``0`` means an
    #: ungraded qualifier and keeps the single pattern used before they existed.
    hatch_level: int = 0

    @property
    def point_count(self) -> int:
        return sum(len(ring) for ring in self.rings)


@dataclass(frozen=True)
class OverlayLayer:
    """A named, drawable, optionally time-bounded group of shapes.

    ``valid_from``/``valid_to`` describe the window the product applies to.
    They are what makes an overlay *time aware*: the map compares its selected
    valid time against this window and can tell the user that the outlook on
    screen does not actually cover the sounding they are looking at, instead of
    silently drawing a mismatched product.
    """

    key: str
    title: str
    shapes: tuple[OverlayShape, ...] = ()
    subtitle: str = ""
    #: Compact product name for places that can only show one label, such as
    #: the sounding locator's badge. A shape label is not always self-describing
    #: -- "MDT" names its own product but "5%" does not say which hazard it
    #: measures -- so a product whose labels need that context supplies it here.
    short_name: str = ""
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    issued: datetime | None = None
    source_url: str = ""
    attribution: str = ""
    bounds: tuple[float, float, float, float] | None = field(default=None)

    def __post_init__(self) -> None:
        if self.bounds is None and self.shapes:
            boxes = [shape.bounds for shape in self.shapes]
            object.__setattr__(self, "bounds", (
                min(box[0] for box in boxes),
                max(box[1] for box in boxes),
                min(box[2] for box in boxes),
                max(box[3] for box in boxes),
            ))

    def __bool__(self) -> bool:
        return bool(self.shapes)

    @property
    def point_count(self) -> int:
        return sum(shape.point_count for shape in self.shapes)

    def covers(self, when: datetime | None) -> bool:
        """Report whether ``when`` falls inside this layer's validity window.

        An unbounded layer covers everything.  A naive ``when`` is rejected
        rather than guessed at, because every time in this application is
        tz-aware UTC and silently assuming a timezone here would produce an
        overlay that looks authoritative while being up to a day wrong.
        """
        if when is None:
            return True
        if self.valid_from is None and self.valid_to is None:
            return True
        if when.tzinfo is None:
            return False
        if self.valid_from is not None and when < self.valid_from:
            return False
        if self.valid_to is not None and when >= self.valid_to:
            return False
        return True


def build_layer(
        key: str,
        title: str,
        shapes: list[OverlayShape] | tuple[OverlayShape, ...],
        **kwargs: Any,
) -> OverlayLayer:
    """Return an :class:`OverlayLayer` with its shapes ordered by ``rank``.

    Sorting once at build time keeps the paint path a straight iteration; a
    stable sort preserves the payload's own order inside one rank.
    """
    ordered = tuple(sorted(shapes, key=lambda shape: shape.rank))
    return OverlayLayer(key=key, title=title, shapes=ordered, **kwargs)


# --------------------------------------------------------------------------- #
# raster overlays
# --------------------------------------------------------------------------- #
#: Upper bound on one raster payload. A 2048x1024 indexed PNG of radar
#: reflectivity is a few hundred kilobytes; this only refuses a hostile body.
MAX_RASTER_BYTES = 16 * 1024 * 1024

#: Leading bytes of the encoded formats a raster overlay may carry. WMS servers
#: report failure as an XML ``ServiceException`` **with HTTP 200**, so the status
#: code cannot be trusted and the payload must identify itself. Checking this
#: here means a service error can never reach the paint path as a broken image.
_IMAGE_MAGIC: tuple[bytes, ...] = (
    b"\x89PNG\r\n\x1a\n",   # PNG
    b"GIF87a",
    b"GIF89a",
    b"\xff\xd8\xff",        # JPEG
)


def looks_like_image(payload: bytes) -> bool:
    """Report whether ``payload`` begins with a supported image signature."""
    if not isinstance(payload, (bytes, bytearray)) or not payload:
        return False
    head = bytes(payload[:16])
    return any(head.startswith(magic) for magic in _IMAGE_MAGIC)


def format_age(seconds: float | None) -> str:
    """Render a data age for display, or ``""`` when it is unknown.

    Coarse on purpose. A live frame's usefulness turns on whether it is minutes
    or tens of minutes old, and a seconds-precise counter that ticks under the
    cursor reads as a stopwatch rather than as the age of the data.

    Lives here rather than on a widget so the map legend and the overlay control
    panel word it identically; the two sit on screen at the same time.
    """
    if seconds is None:
        return ""
    # A clock that has stepped backwards is not worth explaining on a map.
    if seconds < 0.0:
        return "just now"
    if seconds < 90.0:
        return "%d s old" % int(seconds)
    minutes = seconds / 60.0
    if minutes < 90.0:
        return "%d min old" % int(round(minutes))
    return "%.1f h old" % (minutes / 60.0)


@dataclass(frozen=True)
class OverlayRaster:
    """A georeferenced image drawn beneath the vector overlays.

    Holds the image *encoded*, not decoded. Radar frames are fetched on a worker
    thread and this module may not import Qt, so decoding is the paint path's
    job: ``QImage.loadFromData`` is safe off the GUI thread, while ``QPixmap``
    construction is not.

    ``bounds`` is ``(min_lon, max_lon, min_lat, max_lat)`` -- the same tuple
    order :class:`OverlayShape` uses and :meth:`gui_maps.StationMapWidget.
    _draw_layer` already unpacks -- and describes the image's full geographic
    extent, with pixel (0, 0) at the north-west corner. The image is assumed to
    be an equirectangular (plate carree) grid, which is what makes it blittable
    into the map's own linear lon/lat transform without resampling.

    ``valid_time`` is the observation time when the source states one. It is
    deliberately optional: a service that only offers "latest" is recorded with
    ``retrieved_at`` alone rather than having a valid time inferred for it, since
    a radar frame labelled with a time it did not come from is worse than one
    labelled honestly as simply the most recent available.
    """

    key: str
    title: str
    image_bytes: bytes
    bounds: tuple[float, float, float, float]
    subtitle: str = ""
    short_name: str = ""
    valid_time: datetime | None = None
    #: When this payload was received, used to age the frame on screen.
    retrieved_at: datetime | None = None
    #: Nominal seconds between source updates, for staleness judgements.
    update_interval_s: float = 0.0
    opacity: float = 1.0
    source_url: str = ""
    attribution: str = ""

    def __post_init__(self) -> None:
        if not looks_like_image(self.image_bytes):
            raise ValueError("raster payload is not a supported image")
        if len(self.image_bytes) > MAX_RASTER_BYTES:
            raise ValueError("raster payload exceeds the safety limit")

        box = tuple(_finite(value) for value in self.bounds)
        if len(box) != 4 or any(value is None for value in box):
            raise ValueError("raster bounds must be four finite numbers")
        min_lon, max_lon, min_lat, max_lat = box
        if not (-180.0 <= min_lon < max_lon <= 180.0):
            raise ValueError("raster longitude bounds are not ordered")
        if not (-90.0 <= min_lat < max_lat <= 90.0):
            raise ValueError("raster latitude bounds are not ordered")
        object.__setattr__(self, "bounds", (min_lon, max_lon, min_lat, max_lat))

        # Clamp rather than reject: opacity arrives from a user control, and a
        # slider that has drifted a hair outside the range should not blank the
        # overlay.
        opacity = _finite(self.opacity)
        opacity = 1.0 if opacity is None else min(1.0, max(0.0, opacity))
        object.__setattr__(self, "opacity", opacity)

    def __bool__(self) -> bool:
        return bool(self.image_bytes)

    @property
    def byte_count(self) -> int:
        return len(self.image_bytes)

    def at_opacity(self, opacity: float) -> "OverlayRaster":
        """Return this raster at a different opacity.

        The payload object is shared, not copied, which is what lets the paint
        path key its decode cache on the bytes and reuse the pixmap: dragging an
        opacity slider then costs neither a request nor a decode.
        """
        return replace(self, opacity=opacity)

    def intersects(self, view: tuple[float, float, float, float]) -> bool:
        """Report whether this image overlaps a ``(lon0, lon1, lat0, lat1)`` view.

        Lets the paint path skip decoding entirely when the map is showing
        somewhere the product does not cover, which for a CONUS radar mosaic is
        most of the world.
        """
        try:
            lon0, lon1, lat0, lat1 = (float(value) for value in view)
        except (TypeError, ValueError):
            return False
        min_lon, max_lon, min_lat, max_lat = self.bounds
        if max(lon0, lon1) < min_lon or min(lon0, lon1) > max_lon:
            return False
        if max(lat0, lat1) < min_lat or min(lat0, lat1) > max_lat:
            return False
        return True

    def age_seconds(self, now: datetime | None = None) -> float | None:
        """Return how old this frame is, or ``None`` if that is unknown.

        Prefers the source's own observation time and falls back to when the
        payload was received. A naive datetime yields ``None`` rather than a
        guess, matching :meth:`OverlayLayer.covers`.
        """
        stamp = self.valid_time or self.retrieved_at
        if stamp is None or stamp.tzinfo is None:
            return None
        if now is None:
            now = datetime.now(timezone.utc)
        if now.tzinfo is None:
            return None
        return (now - stamp).total_seconds()

    def is_stale(
            self,
            now: datetime | None = None,
            *,
            tolerance: float = 2.5,
    ) -> bool:
        """Report whether the frame is older than its own update cadence allows.

        ``tolerance`` multiplies :attr:`update_interval_s`, so a product that
        refreshes every two minutes is called stale after five rather than at
        two minutes and one second -- one skipped cycle is normal operation, and
        a warning that cries wolf every few minutes teaches users to ignore it.
        Unknown age is never reported stale.
        """
        if self.update_interval_s <= 0.0:
            return False
        age = self.age_seconds(now)
        if age is None:
            return False
        return age > self.update_interval_s * float(tolerance)


# --------------------------------------------------------------------------- #
# carrying overlays to the sounding window's locator inset
# --------------------------------------------------------------------------- #
#: Profile-collection metadata key holding overlays for the locator inset.
#:
#: The inset is painted from inside a vendored widget's render pass, which
#: receives nothing but the widget, so the layers have to travel with the
#: sounding rather than be passed in. Collection metadata is the established
#: route for that (:meth:`setMeta` / :meth:`getMeta`), and it survives
#: ``ProfCollection.subset``.
#:
#: The key is deliberately generic rather than SPC-specific: the inset draws
#: whatever is attached here, so a forecast-model product added later attaches a
#: layer under its own :attr:`OverlayLayer.key` and needs no change to the paint
#: path.
LOCATOR_OVERLAY_META_KEY = "sharpmod_locator_overlays"

#: Bound on how many layers one sounding may carry into the inset.
MAX_LOCATOR_OVERLAYS = 8


def locator_overlays(collection: Any) -> tuple[OverlayLayer, ...]:
    """Return the overlay layers attached to ``collection``.

    Tolerant by design: this is read from a paint path, so a collection with no
    metadata, an unreadable store, or a partially-populated value yields an
    empty result rather than raising and taking the sounding's render with it.
    """
    if collection is None:
        return ()
    value = None
    try:
        value = collection.getMeta(LOCATOR_OVERLAY_META_KEY)
    except (AttributeError, KeyError, TypeError, IndexError):
        try:
            value = getattr(collection, "_meta", {}).get(
                LOCATOR_OVERLAY_META_KEY)
        except (AttributeError, TypeError):
            return ()
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        layer for layer in value if isinstance(layer, OverlayLayer) and layer)


def attach_locator_overlay(
        collection: Any,
        layer: OverlayLayer | None,
        *,
        key: str | None = None,
) -> tuple[OverlayLayer, ...]:
    """Attach ``layer`` to ``collection``, replacing any layer of the same key.

    Replacing by key rather than appending is what lets several products
    coexist: refetching the convective outlook must not discard a model product
    attached alongside it, and must not accumulate stale copies of itself.

    Passing ``layer=None`` removes the entry for ``key``, which is how a product
    reports that it has nothing for this sounding.
    """
    if collection is None:
        return ()
    target_key = key or (layer.key if layer is not None else None)
    if target_key is None:
        return locator_overlays(collection)

    kept = [
        existing for existing in locator_overlays(collection)
        if existing.key != target_key
    ]
    if layer is not None and layer:
        kept.append(layer)
    layers = tuple(kept[-MAX_LOCATOR_OVERLAYS:])
    try:
        collection.setMeta(LOCATOR_OVERLAY_META_KEY, layers)
    except (AttributeError, TypeError):
        return ()
    return layers


def overlays_covering(
        layers: tuple[OverlayLayer, ...] | list[OverlayLayer],
        when: datetime | None,
) -> tuple[OverlayLayer, ...]:
    """Return only those ``layers`` whose validity window contains ``when``.

    The sounding window can hold several profiles at different valid times and
    switches focus between them, so a layer fetched for one of them must not be
    drawn over another. Filtering here means a mismatch shows nothing rather
    than something wrong.
    """
    return tuple(layer for layer in layers if layer and layer.covers(when))


# --------------------------------------------------------------------------- #
# which shape covers a point
# --------------------------------------------------------------------------- #
def shape_contains(shape: OverlayShape, lon: float, lat: float) -> bool:
    """Report whether ``(lon, lat)`` falls inside ``shape``.

    Counts ray crossings across *every* ring and tests the parity, which is the
    odd-even rule the paint path fills with. That makes holes work without
    special-casing them: a point inside the area a higher category occupies has
    an even crossing count for the lower category's shape, so it correctly
    belongs to the higher one alone.
    """
    min_lon, max_lon, min_lat, max_lat = shape.bounds
    if not (min_lon <= lon <= max_lon and min_lat <= lat <= max_lat):
        return False

    crossings = 0
    for ring in shape.rings:
        count = len(ring)
        for index in range(count):
            lon1, lat1 = ring[index]
            lon2, lat2 = ring[(index + 1) % count]
            # Excludes edges that do not straddle the ray, and with it the
            # horizontal edges that would divide by zero below.
            if (lat1 > lat) == (lat2 > lat):
                continue
            lon_at = lon1 + (lat - lat1) * (lon2 - lon1) / (lat2 - lat1)
            if lon < lon_at:
                crossings += 1
    return crossings % 2 == 1


def shape_at(
        layers: tuple[OverlayLayer, ...] | list[OverlayLayer],
        lon: float,
        lat: float,
        *,
        hatch: bool | None = None,
) -> OverlayShape | None:
    """Return the highest-ranked shape covering ``(lon, lat)``, if any.

    Highest-ranked because rank orders a product by severity, and the answer a
    forecaster wants for a point is the most severe category that applies.

    ``hatch`` restricts the search by the shapes' :attr:`OverlayShape.hatch`
    flag, which matters because a hatched shape is a *qualifier* on the band
    beneath it rather than a band of its own. It deliberately outranks every
    band so it paints on top, so an unrestricted search inside one answers with
    the qualifier and loses the graded value the point actually sits in. Pass
    ``False`` for the band and ``True`` to ask whether the qualifier applies.
    """
    best: OverlayShape | None = None
    for layer in layers:
        for shape in layer.shapes:
            if hatch is not None and bool(shape.hatch) is not hatch:
                continue
            if best is not None and shape.rank <= best.rank:
                continue
            if shape_contains(shape, lon, lat):
                best = shape
    return best

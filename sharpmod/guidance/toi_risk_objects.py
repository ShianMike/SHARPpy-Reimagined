"""Issuance-time risk-object detection and anchor selection for archived TOI.

The first archive pilot resolved case anchors by taking the single largest
forecast proxy-STP grid value across CONUS.  That is a noise detector: it put
the 2018-11-05 anchor at 30.30N 76.69W in the Atlantic and the 2023-03-31
anchor at 27.79N 94.06W in the Gulf, the latter with a peak proxy STP of 0.31
during one of the decade's larger outbreaks.  A single grid maximum has no area,
no support, and no land constraint.

This module replaces it with object selection:

1. Threshold the forecast proxy-STP field and label connected components.
2. Reject objects that fail minimum area, intensity, or land-fraction support.
3. Score survivors by an *integrated* measure (summed intensity over area,
   weighted by land fraction) rather than a point maximum.
4. Select the highest-scoring object and anchor it on one of its own land
   grid points.

Step 4 originally used the intensity-weighted centroid of *every* member point.
That let the anchor escape the land domain even though the object passed the
land-fraction test: a large land-and-ocean crescent has most of its mass on
land, but the centroid of the crescent sits in the water.  The measured example
was ``null-2018-04-16``, a zero-tornado day whose 403,143 km^2 / 400-point
object had land fraction 0.698 - comfortably over the 0.5 minimum - yet
anchored at 31.75N 79.74W, roughly 200 km off the Georgia coast in the
Atlantic.  An earlier fix rejected offshore *peaks*; it did not constrain
offshore *centroids*, and 109 of the 335 resolvable archived cases (32.5%)
selected a mixed land-and-ocean object, which is the precondition for the same
escape.

The anchor is therefore built in two steps that cannot leave land: the
intensity-weighted centroid is computed over the object's *land* members only,
then snapped to the land member nearest that centroid.  The result is always an
actual land grid point belonging to the object.

How far the snap moves the anchor depends on how concave the object is and on the
grid stride in use - archive anchor resolution decodes at stride 12, roughly 36 km
on the 3 km HRRR grid - so it is not bounded by a single grid step: a land
centroid that falls in a wide water gap snaps to the nearest land member, which
was measured up to about 90 km away during collection.  The displacement is
recorded per case as ``anchor_snap_km`` rather than assumed, so it can be audited
instead of trusted.  It is a strict improvement regardless: every alternative
leaves the anchor in water.

Every input is available at forecast issuance.  The land domain comes from the
repository's bundled U.S. Census county cartographic boundary tiles, which are
already versioned and hash-recorded, so the mask is reproducible and is not a
hand-drawn polygon.  Observed tornado locations are never consulted; see
:func:`assert_no_observation_leakage`.
"""

from __future__ import annotations

import json
import math
import zipfile
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

import numpy as np

#: v2 constrains the selected anchor to a land grid point of the chosen object.
#: v1 used the intensity-weighted centroid of every member point, which could
#: fall offshore for a large land-and-ocean object; artifacts produced under v1
#: carry the v1 string and are not comparable to v2 anchors.
TOI_RISK_OBJECT_METHOD_VERSION = "sharpmod_toi_risk_object_selection_v2"

#: Bundled Census county cartographic boundary tiles, keyed by integer
#: (floor(latitude), floor(longitude)).  A tile exists only where a county
#: boundary passes through it, so tile presence is a documented CONUS land /
#: immediate-coastal domain test for the 48 contiguous states and DC.
_COUNTY_ARCHIVE = "conus-counties.zip"
_COUNTY_METADATA = "conus-counties.metadata.json"

#: Selection rules.  These are deliberately conservative: a risk area with no
#: area, no intensity, or no land is not a tornado risk area.
DEFAULT_OBJECT_STP_THRESHOLD = 0.5
DEFAULT_MINIMUM_GRID_POINTS = 4
DEFAULT_MINIMUM_PEAK_STP = 1.0
DEFAULT_MINIMUM_LAND_FRACTION = 0.5

EARTH_RADIUS_KM = 6371.0088


class TOIRiskObjectError(ValueError):
    """Raised when no defensible issuance-time risk object exists."""


#: One-degree tiles are the resolution of the bundled county index.
LAND_MASK_TILE_DEGREES = 1


@lru_cache(maxsize=1)
def conus_land_tiles() -> frozenset[tuple[int, int]]:
    """Return the set of 1-degree tiles containing CONUS county boundaries."""

    path = resources.files("sharpmod.resources").joinpath(_COUNTY_ARCHIVE)
    tiles: set[tuple[int, int]] = set()
    with zipfile.ZipFile(str(path)) as archive:
        for name in archive.namelist():
            if not name.startswith("tiles/") or not name.endswith(".json"):
                continue
            parts = name.split("/")
            if len(parts) != 3:
                continue
            try:
                latitude = int(parts[1])
                longitude = int(parts[2].removesuffix(".json"))
            except ValueError:  # pragma: no cover - defensive
                continue
            tiles.add((latitude, longitude))
    if not tiles:  # pragma: no cover - packaging regression
        raise TOIRiskObjectError(
            "bundled CONUS county tiles are missing; the land domain mask "
            "cannot be built"
        )
    return frozenset(tiles)


@lru_cache(maxsize=1)
def conus_land_source() -> dict[str, Any]:
    """Return the versioned identity of the bundled land-domain source."""

    with resources.files("sharpmod.resources").joinpath(_COUNTY_METADATA).open(
        encoding="utf-8"
    ) as handle:
        metadata = json.load(handle)
    return {
        "land_mask_method": TOI_RISK_OBJECT_METHOD_VERSION,
        "land_mask_source": metadata.get(
            "source", "U.S. Census Bureau County Cartographic Boundary File"
        ),
        "land_mask_source_url": metadata.get("source_url", ""),
        "land_mask_source_sha256": metadata.get("archive_sha256", ""),
        "land_mask_coverage": metadata.get("coverage", ""),
        "land_mask_tile_degrees": LAND_MASK_TILE_DEGREES,
        "land_mask_tile_count": len(conus_land_tiles()),
    }


def conus_land_mask(latitude: np.ndarray, longitude: np.ndarray) -> np.ndarray:
    """Return a boolean CONUS land/coastal mask for a coordinate grid.

    A point is inside the domain when its containing one-degree tile holds a
    county boundary.  This admits immediate coastal waters within a shared tile
    and excludes open ocean, which is exactly the behaviour a risk-area domain
    test needs.
    """

    latitudes = np.asarray(latitude, dtype=float)
    longitudes = np.asarray(longitude, dtype=float)
    if latitudes.shape != longitudes.shape:
        raise TOIRiskObjectError("latitude and longitude grids must match")
    tiles = conus_land_tiles()
    finite = np.isfinite(latitudes) & np.isfinite(longitudes)
    floor_lat = np.where(finite, np.floor(latitudes), 0).astype(int)
    floor_lon = np.where(finite, np.floor(longitudes), 0).astype(int)
    mask = np.zeros(latitudes.shape, dtype=bool)
    rows, cols = np.nonzero(finite)
    for row, col in zip(rows.tolist(), cols.tolist(), strict=True):
        mask[row, col] = (
            int(floor_lat[row, col]),
            int(floor_lon[row, col]),
        ) in tiles
    return mask


def _connected_components(mask: np.ndarray) -> list[np.ndarray]:
    """Label eight-connected components, returning flat index arrays."""

    mask = np.asarray(mask, dtype=bool)
    visited = np.zeros(mask.shape, dtype=bool)
    rows, cols = mask.shape
    components: list[np.ndarray] = []
    for row in range(rows):
        for col in range(cols):
            if not mask[row, col] or visited[row, col]:
                continue
            stack = [(row, col)]
            visited[row, col] = True
            member: list[tuple[int, int]] = []
            while stack:
                current_row, current_col = stack.pop()
                member.append((current_row, current_col))
                for row_delta in (-1, 0, 1):
                    for col_delta in (-1, 0, 1):
                        if row_delta == 0 and col_delta == 0:
                            continue
                        next_row = current_row + row_delta
                        next_col = current_col + col_delta
                        if not (0 <= next_row < rows and 0 <= next_col < cols):
                            continue
                        if mask[next_row, next_col] and not visited[
                            next_row, next_col
                        ]:
                            visited[next_row, next_col] = True
                            stack.append((next_row, next_col))
            components.append(np.asarray(member, dtype=int))
    return components


def _cell_area_km2(latitude: np.ndarray, longitude: np.ndarray) -> float:
    """Estimate one grid cell's area from the grid's own spacing."""

    latitudes = np.asarray(latitude, dtype=float)
    longitudes = np.asarray(longitude, dtype=float)
    if latitudes.shape[0] < 2 or latitudes.shape[1] < 2:
        return 0.0
    mean_latitude = float(np.nanmean(latitudes))
    delta_lat = abs(float(np.nanmean(np.diff(latitudes[:, 0]))))
    delta_lon = abs(float(np.nanmean(np.diff(longitudes[0, :]))))
    km_per_degree = math.pi * EARTH_RADIUS_KM / 180.0
    height = delta_lat * km_per_degree
    width = delta_lon * km_per_degree * math.cos(math.radians(mean_latitude))
    return max(0.0, height * width)


def _great_circle_km(
    latitude1: float,
    longitude1: float,
    latitude2: np.ndarray,
    longitude2: np.ndarray,
) -> np.ndarray:
    """Great-circle distance from one point to an array of points, in km."""

    phi1 = math.radians(latitude1)
    phi2 = np.radians(np.asarray(latitude2, dtype=float))
    delta_phi = phi2 - phi1
    delta_lambda = np.radians(np.asarray(longitude2, dtype=float) - longitude1)
    value = (
        np.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * np.cos(phi2) * np.sin(delta_lambda / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_KM * np.arcsin(np.minimum(1.0, np.sqrt(value)))


def _land_anchor(
    latitudes: np.ndarray,
    longitudes: np.ndarray,
    rows: np.ndarray,
    cols: np.ndarray,
    values: np.ndarray,
    on_land: np.ndarray,
) -> tuple[float, float, float, float, bool, float]:
    """Anchor one risk object on a land grid point it actually contains.

    Returns ``(anchor_lat, anchor_lon, land_centroid_lat, land_centroid_lon,
    on_land, snap_km)``.  The intensity-weighted centroid is taken over land
    members only, then snapped to the nearest land member so the anchor can
    never sit in water.  Ties snap to the lowest ``(row, col)`` so the result is
    deterministic.

    When the object has no land member at all there is nothing to anchor to; the
    all-member centroid is returned with ``on_land=False``.  Such an object has
    land fraction 0.0 and is always rejected by the land-fraction rule.
    """

    weights = np.maximum(values, 0.0)
    if not np.any(on_land):
        fallback_weights = weights if float(np.sum(weights)) > 0 else None
        return (
            float(np.average(latitudes[rows, cols], weights=fallback_weights)),
            float(np.average(longitudes[rows, cols], weights=fallback_weights)),
            math.nan,
            math.nan,
            False,
            0.0,
        )

    land_rows = rows[on_land]
    land_cols = cols[on_land]
    land_latitudes = latitudes[land_rows, land_cols]
    land_longitudes = longitudes[land_rows, land_cols]
    land_weights = weights[on_land]
    if float(np.sum(land_weights)) <= 0:  # pragma: no cover - threshold > 0
        land_weights = None
    centroid_latitude = float(np.average(land_latitudes, weights=land_weights))
    centroid_longitude = float(np.average(land_longitudes, weights=land_weights))

    distances = _great_circle_km(
        centroid_latitude, centroid_longitude, land_latitudes, land_longitudes
    )
    # Deterministic tie-break: nearest, then lowest row, then lowest column.
    order = np.lexsort((land_cols, land_rows, np.round(distances, 6)))
    best = int(order[0])
    return (
        float(land_latitudes[best]),
        float(land_longitudes[best]),
        centroid_latitude,
        centroid_longitude,
        True,
        float(distances[best]),
    )


@dataclass(frozen=True)
class RiskObject:
    """One connected issuance-time forecast risk object."""

    grid_points: int
    area_km2: float
    peak_stp: float
    mean_stp: float
    integrated_stp: float
    land_fraction: float
    #: Anchor point.  Always a land grid point of this object when the object
    #: has any land support; see ``anchor_on_land``.
    centroid_latitude: float
    centroid_longitude: float
    score: float
    accepted: bool
    rejection_reason: str = ""
    #: Intensity-weighted centroid of the object's land members, before the
    #: snap to an actual land grid point.  Retained so the snap is auditable.
    land_centroid_latitude: float = math.nan
    land_centroid_longitude: float = math.nan
    #: False only when the object contains no land member at all, in which case
    #: it also fails the land-fraction rule and is never selected.
    anchor_on_land: bool = True
    #: Distance the weighted land centroid moved when snapped onto the grid.
    anchor_snap_km: float = 0.0

    def to_mapping(self) -> dict[str, Any]:
        return {
            "grid_points": self.grid_points,
            "area_km2": round(self.area_km2, 1),
            "peak_stp": round(self.peak_stp, 3),
            "mean_stp": round(self.mean_stp, 3),
            "integrated_stp": round(self.integrated_stp, 3),
            "land_fraction": round(self.land_fraction, 3),
            "centroid_latitude": round(self.centroid_latitude, 4),
            "centroid_longitude": round(self.centroid_longitude, 4),
            "land_centroid_latitude": (
                round(self.land_centroid_latitude, 4)
                if math.isfinite(self.land_centroid_latitude)
                else None
            ),
            "land_centroid_longitude": (
                round(self.land_centroid_longitude, 4)
                if math.isfinite(self.land_centroid_longitude)
                else None
            ),
            "anchor_on_land": self.anchor_on_land,
            "anchor_snap_km": round(self.anchor_snap_km, 2),
            "score": round(self.score, 3),
            "accepted": self.accepted,
            "rejection_reason": self.rejection_reason,
        }


def detect_risk_objects(
    stp: np.ndarray,
    latitude: np.ndarray,
    longitude: np.ndarray,
    *,
    threshold: float = DEFAULT_OBJECT_STP_THRESHOLD,
    minimum_grid_points: int = DEFAULT_MINIMUM_GRID_POINTS,
    minimum_peak_stp: float = DEFAULT_MINIMUM_PEAK_STP,
    minimum_land_fraction: float = DEFAULT_MINIMUM_LAND_FRACTION,
    land_mask: np.ndarray | None = None,
) -> tuple[RiskObject, ...]:
    """Detect and score every connected forecast risk object.

    Rejected candidates are returned too, each with its exact reason, so the
    selection can be audited instead of only reporting the winner.
    """

    field = np.asarray(stp, dtype=float)
    if field.ndim == 3:
        finite_any = np.any(np.isfinite(field), axis=0)
        peak = np.max(np.where(np.isfinite(field), field, -np.inf), axis=0)
        peak = np.where(finite_any, peak, np.nan)
    elif field.ndim == 2:
        peak = field
    else:
        raise TOIRiskObjectError("proxy STP must be a 2-D or 3-D field")
    latitudes = np.asarray(latitude, dtype=float)
    longitudes = np.asarray(longitude, dtype=float)
    if peak.shape != latitudes.shape or peak.shape != longitudes.shape:
        raise TOIRiskObjectError("proxy STP and coordinates must share a shape")

    domain = conus_land_mask(latitudes, longitudes) if land_mask is None else (
        np.asarray(land_mask, dtype=bool)
    )
    cell_area = _cell_area_km2(latitudes, longitudes)
    candidate = np.isfinite(peak) & (peak >= float(threshold))
    objects: list[RiskObject] = []
    for member in _connected_components(candidate):
        rows, cols = member[:, 0], member[:, 1]
        values = peak[rows, cols]
        weights = np.maximum(values, 0.0)
        total_weight = float(np.sum(weights))
        if total_weight <= 0:  # pragma: no cover - threshold keeps values > 0
            continue
        on_land = np.asarray(domain[rows, cols], dtype=bool)
        land_points = int(np.count_nonzero(on_land))
        land_fraction = land_points / len(member)
        integrated = float(np.sum(values)) * cell_area
        peak_value = float(np.max(values))
        (
            anchor_latitude,
            anchor_longitude,
            land_centroid_latitude,
            land_centroid_longitude,
            anchor_on_land,
            anchor_snap_km,
        ) = _land_anchor(latitudes, longitudes, rows, cols, values, on_land)
        reasons: list[str] = []
        if len(member) < int(minimum_grid_points):
            reasons.append(
                f"area {len(member)} grid point(s) below minimum "
                f"{int(minimum_grid_points)}"
            )
        if peak_value < float(minimum_peak_stp):
            reasons.append(
                f"peak proxy STP {peak_value:.2f} below minimum "
                f"{float(minimum_peak_stp):.2f}"
            )
        if land_fraction < float(minimum_land_fraction):
            reasons.append(
                f"land fraction {land_fraction:.2f} below minimum "
                f"{float(minimum_land_fraction):.2f}"
            )
        objects.append(
            RiskObject(
                grid_points=len(member),
                area_km2=len(member) * cell_area,
                peak_stp=peak_value,
                mean_stp=float(np.mean(values)),
                integrated_stp=integrated,
                land_fraction=land_fraction,
                centroid_latitude=anchor_latitude,
                centroid_longitude=anchor_longitude,
                land_centroid_latitude=land_centroid_latitude,
                land_centroid_longitude=land_centroid_longitude,
                anchor_on_land=anchor_on_land,
                anchor_snap_km=anchor_snap_km,
                # Documented integrated score: intensity summed over area and
                # discounted by the fraction of the object that is not on land.
                score=integrated * land_fraction,
                accepted=not reasons,
                rejection_reason="; ".join(reasons),
            )
        )
    return tuple(
        sorted(objects, key=lambda item: (not item.accepted, -item.score))
    )


def select_risk_object(
    stp: np.ndarray,
    latitude: np.ndarray,
    longitude: np.ndarray,
    **rules: Any,
) -> tuple[RiskObject, dict[str, Any]]:
    """Select the best-supported issuance-time risk object, or fail loudly."""

    objects = detect_risk_objects(stp, latitude, longitude, **rules)
    accepted = [item for item in objects if item.accepted]
    provenance: dict[str, Any] = {
        "anchor_source": "model_forecast_maximum_stp",
        "anchor_selection_method": TOI_RISK_OBJECT_METHOD_VERSION,
        "anchor_candidate_objects": len(objects),
        "anchor_accepted_objects": len(accepted),
        "anchor_rules": {
            "threshold": rules.get("threshold", DEFAULT_OBJECT_STP_THRESHOLD),
            "minimum_grid_points": rules.get(
                "minimum_grid_points", DEFAULT_MINIMUM_GRID_POINTS
            ),
            "minimum_peak_stp": rules.get(
                "minimum_peak_stp", DEFAULT_MINIMUM_PEAK_STP
            ),
            "minimum_land_fraction": rules.get(
                "minimum_land_fraction", DEFAULT_MINIMUM_LAND_FRACTION
            ),
        },
    }
    provenance.update(conus_land_source())
    if not accepted:
        rejected = "; ".join(
            f"[{item.grid_points}pt peak {item.peak_stp:.2f} land "
            f"{item.land_fraction:.2f}] {item.rejection_reason}"
            for item in objects[:3]
        )
        raise TOIRiskObjectError(
            "no issuance-time risk object met the documented minimum area, "
            f"intensity, and land support ({len(objects)} candidate(s))"
            + (f": {rejected}" if rejected else "")
        )
    selected = accepted[0]
    if not selected.anchor_on_land:  # pragma: no cover - land rule precedes this
        raise TOIRiskObjectError(
            "selected risk object has no land grid point to anchor on; the "
            "land-fraction rule should already have rejected it"
        )
    provenance.update(
        {
            "anchor_selected_area_km2": round(selected.area_km2, 1),
            "anchor_selected_score": round(selected.score, 3),
            "anchor_selected_land_fraction": round(selected.land_fraction, 3),
            "anchor_selected_peak_stp": round(selected.peak_stp, 3),
            "anchor_selected_grid_points": selected.grid_points,
            "anchor_resolved_latitude": round(selected.centroid_latitude, 4),
            "anchor_resolved_longitude": round(selected.centroid_longitude, 4),
            # The anchor is a land grid point of the object, not the centroid of
            # all its members, so a large land-and-ocean object cannot anchor
            # offshore.  Both the pre-snap land centroid and the snap distance
            # are recorded so the constraint is auditable.
            "anchor_on_land": True,
            "anchor_land_centroid_latitude": (
                round(selected.land_centroid_latitude, 4)
                if math.isfinite(selected.land_centroid_latitude)
                else None
            ),
            "anchor_land_centroid_longitude": (
                round(selected.land_centroid_longitude, 4)
                if math.isfinite(selected.land_centroid_longitude)
                else None
            ),
            "anchor_snap_km": round(selected.anchor_snap_km, 2),
            "anchor_runner_up_score": (
                round(accepted[1].score, 3) if len(accepted) > 1 else None
            ),
        }
    )
    return selected, provenance


def assert_no_observation_leakage(payload: Any) -> None:
    """Fail if anything observation-derived reached the anchor inputs.

    Selection consumes only forecast fields and a static land domain.  This
    guard makes that explicit and testable rather than a comment: any key that
    names observed reports, tornado locations, or damage surveys is rejected.
    """

    forbidden = (
        "observed",
        "tornado_lat",
        "tornado_lon",
        "storm_report",
        "damage_survey",
        "verified",
        "centroid_of_reports",
    )
    if isinstance(payload, dict):
        for key, value in payload.items():
            lowered = str(key).casefold()
            if any(token in lowered for token in forbidden):
                raise TOIRiskObjectError(
                    f"anchor selection input contains observation-derived key "
                    f"{key!r}; every predictor and anchor must be restricted to "
                    "information available at forecast issuance"
                )
            assert_no_observation_leakage(value)
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            assert_no_observation_leakage(item)


__all__ = [
    "LAND_MASK_TILE_DEGREES",
    "DEFAULT_MINIMUM_GRID_POINTS",
    "DEFAULT_MINIMUM_LAND_FRACTION",
    "DEFAULT_MINIMUM_PEAK_STP",
    "DEFAULT_OBJECT_STP_THRESHOLD",
    "TOI_RISK_OBJECT_METHOD_VERSION",
    "RiskObject",
    "TOIRiskObjectError",
    "assert_no_observation_leakage",
    "conus_land_mask",
    "conus_land_source",
    "conus_land_tiles",
    "detect_risk_objects",
    "select_risk_object",
]

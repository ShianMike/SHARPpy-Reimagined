"""Experimental regional feature extraction for the Tornado Outbreak Index.

This module implements the reproducible part of the public-method description:
detecting and tracking midlevel jet objects, relating the selected track to a
risk mask, and finding peak STP.  It intentionally does *not* invent the missing
official scorecard or probability calibration.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from .schemas import GuidanceGrid, TOIFeatures

EARTH_RADIUS_KM = 6371.0088
KM_PER_NAUTICAL_MILE = 1.852
MPS_TO_KT = 1.9438444924406

#: Kinematic ceiling on how fast one jet object may be considered to have
#: translated between two frames.
#:
#: Association originally used a fixed distance radius (1200 km here, 1800 km in
#: the live HRRR producer) with no reference to the gap between frames.  At the
#: 3-hourly TOI sampling interval a 1800 km jump implies 324 kt, so the matcher
#: could link two unrelated jet streaks and the endpoint-to-endpoint translation
#: speed inherited that jump.  The measured archive showed 54 of 337 cases above
#: 80 kt and a worst case of 135.7 kt, which no midlevel jet feature attains.
#:
#: Because the endpoint great-circle displacement can never exceed the sum of
#: the per-step displacements, bounding each step by this ceiling also bounds
#: ``JetTrack.translation_speed_kt`` by it.
DEFAULT_MAX_JET_TRANSLATION_KT = 90.0


@dataclass(frozen=True)
class JetObject:
    """One connected midlevel jet region at one valid time."""

    time_index: int
    valid_time: datetime
    centroid_latitude: float
    centroid_longitude: float
    maximum_wind_kt: float
    mean_wind_kt: float
    grid_point_count: int


@dataclass(frozen=True)
class JetTrack:
    """A deterministic nearest-neighbour track of jet objects."""

    objects: tuple[JetObject, ...]

    def __post_init__(self) -> None:
        if not self.objects:
            raise ValueError("a jet track must contain at least one object")
        indexes = tuple(obj.time_index for obj in self.objects)
        if tuple(sorted(indexes)) != indexes or len(set(indexes)) != len(indexes):
            raise ValueError("jet-track objects must have unique increasing times")

    @property
    def duration_hours(self) -> float:
        if len(self.objects) < 2:
            return 0.0
        seconds = (
            self.objects[-1].valid_time - self.objects[0].valid_time
        ).total_seconds()
        return max(0.0, seconds / 3600.0)

    @property
    def translation_speed_kt(self) -> float:
        hours = self.duration_hours
        if hours <= 0:
            return 0.0
        first, last = self.objects[0], self.objects[-1]
        distance_km = _great_circle_km(
            first.centroid_latitude,
            first.centroid_longitude,
            last.centroid_latitude,
            last.centroid_longitude,
        )
        return distance_km / KM_PER_NAUTICAL_MILE / hours


def _great_circle_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlambda = math.radians(lon2 - lon1)
    value = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    return 2.0 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(value)))


def _bearing_degrees(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, clockwise from north."""

    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(
        dlambda
    )
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def _circular_longitude(longitudes: np.ndarray, weights: np.ndarray) -> float:
    radians = np.radians(longitudes)
    x = np.sum(weights * np.cos(radians))
    y = np.sum(weights * np.sin(radians))
    if x == 0 and y == 0:
        return float(np.average(longitudes, weights=weights))
    value = math.degrees(math.atan2(y, x))
    return ((value + 180.0) % 360.0) - 180.0


def _connected_components(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    visited = np.zeros(mask.shape, dtype=bool)
    components: list[list[tuple[int, int]]] = []
    rows, cols = mask.shape
    for row in range(rows):
        for col in range(cols):
            if not mask[row, col] or visited[row, col]:
                continue
            stack = [(row, col)]
            visited[row, col] = True
            component: list[tuple[int, int]] = []
            while stack:
                cur_row, cur_col = stack.pop()
                component.append((cur_row, cur_col))
                for drow in (-1, 0, 1):
                    for dcol in (-1, 0, 1):
                        if drow == 0 and dcol == 0:
                            continue
                        next_row, next_col = cur_row + drow, cur_col + dcol
                        if not (0 <= next_row < rows and 0 <= next_col < cols):
                            continue
                        if mask[next_row, next_col] and not visited[next_row, next_col]:
                            visited[next_row, next_col] = True
                            stack.append((next_row, next_col))
            components.append(component)
    return components


def detect_jet_objects(
    wind_speed_kt: np.ndarray,
    latitude: np.ndarray,
    longitude: np.ndarray,
    *,
    time_index: int,
    valid_time: datetime,
    threshold_kt: float = 50.0,
    min_grid_points: int = 4,
) -> tuple[JetObject, ...]:
    """Label eight-connected wind regions at or above ``threshold_kt``."""

    speed = np.asarray(wind_speed_kt, dtype=float)
    latitude = np.asarray(latitude, dtype=float)
    longitude = np.asarray(longitude, dtype=float)
    if (
        speed.ndim != 2
        or latitude.shape != speed.shape
        or longitude.shape != speed.shape
    ):
        raise ValueError("wind speed and coordinates must be matching 2-D grids")
    threshold = float(threshold_kt)
    if not math.isfinite(threshold) or threshold <= 0:
        raise ValueError("threshold_kt must be a positive finite value")
    if int(min_grid_points) < 1:
        raise ValueError("min_grid_points must be at least one")

    finite = np.isfinite(speed) & np.isfinite(latitude) & np.isfinite(longitude)
    mask = finite & (speed >= threshold)
    result: list[JetObject] = []
    for component in _connected_components(mask):
        if len(component) < int(min_grid_points):
            continue
        rows = np.fromiter((point[0] for point in component), dtype=int)
        cols = np.fromiter((point[1] for point in component), dtype=int)
        values = speed[rows, cols]
        weights = np.maximum(values - threshold, 0.0) + 1.0
        result.append(
            JetObject(
                time_index=int(time_index),
                valid_time=valid_time,
                centroid_latitude=float(
                    np.average(latitude[rows, cols], weights=weights)
                ),
                centroid_longitude=_circular_longitude(longitude[rows, cols], weights),
                maximum_wind_kt=float(np.max(values)),
                mean_wind_kt=float(np.mean(values)),
                grid_point_count=len(component),
            )
        )
    return tuple(sorted(result, key=lambda obj: obj.maximum_wind_kt, reverse=True))


def track_jet_objects(
    objects_by_time: tuple[tuple[JetObject, ...], ...],
    *,
    maximum_match_distance_km: float = 1200.0,
    maximum_translation_kt: float = DEFAULT_MAX_JET_TRANSLATION_KT,
) -> tuple[JetTrack, ...]:
    """Track objects with deterministic one-to-one nearest-neighbour matches.

    A match must satisfy both the absolute ``maximum_match_distance_km`` radius
    and the ``maximum_translation_kt`` kinematic ceiling applied over the actual
    gap between the two frames.  The ceiling is additionally clamped to the
    stronger of the two objects' peak winds, since a coherent jet maximum cannot
    propagate faster than the flow that forms it.  Bounding every step this way
    bounds each track's endpoint translation speed by the same ceiling.
    """

    maximum_distance = float(maximum_match_distance_km)
    if not math.isfinite(maximum_distance) or maximum_distance <= 0:
        raise ValueError("maximum_match_distance_km must be positive and finite")
    translation_ceiling = float(maximum_translation_kt)
    if not math.isfinite(translation_ceiling) or translation_ceiling <= 0:
        raise ValueError("maximum_translation_kt must be positive and finite")
    tracks: list[list[JetObject]] = []
    for time_index, objects in enumerate(objects_by_time):
        active_indexes = [
            index
            for index, track in enumerate(tracks)
            if track[-1].time_index == time_index - 1
        ]
        candidates: list[tuple[float, float, int, int]] = []
        for track_index in active_indexes:
            previous = tracks[track_index][-1]
            for object_index, obj in enumerate(objects):
                distance = _great_circle_km(
                    previous.centroid_latitude,
                    previous.centroid_longitude,
                    obj.centroid_latitude,
                    obj.centroid_longitude,
                )
                gap_hours = max(
                    0.0,
                    (obj.valid_time - previous.valid_time).total_seconds() / 3600.0,
                )
                speed_ceiling = min(
                    translation_ceiling,
                    max(previous.maximum_wind_kt, obj.maximum_wind_kt),
                )
                allowed = min(
                    maximum_distance,
                    speed_ceiling * KM_PER_NAUTICAL_MILE * gap_hours,
                )
                if distance <= allowed:
                    intensity_change = abs(
                        previous.maximum_wind_kt - obj.maximum_wind_kt
                    )
                    candidates.append(
                        (distance, intensity_change, track_index, object_index)
                    )
        matched_tracks: set[int] = set()
        matched_objects: set[int] = set()
        for _distance, _change, track_index, object_index in sorted(candidates):
            if track_index in matched_tracks or object_index in matched_objects:
                continue
            tracks[track_index].append(objects[object_index])
            matched_tracks.add(track_index)
            matched_objects.add(object_index)
        for object_index, obj in enumerate(objects):
            if object_index not in matched_objects:
                tracks.append([obj])
    return tuple(JetTrack(tuple(track)) for track in tracks)


def _wind_field_to_knots(values: np.ndarray, units: str, field_name: str) -> np.ndarray:
    normalized = "".join(str(units).strip().casefold().split())
    if normalized in {"kt", "kts", "knot", "knots"}:
        return np.asarray(values, dtype=float)
    if normalized in {"m/s", "ms-1", "m/s-1", "meter/second", "meters/second"}:
        return np.asarray(values, dtype=float) * MPS_TO_KT
    raise ValueError(
        f"{field_name!r} units must explicitly be knots or metres per second"
    )


def _risk_centroid(
    latitude: np.ndarray, longitude: np.ndarray, risk_mask: np.ndarray
) -> tuple[float, float]:
    raw_mask = np.asarray(risk_mask, dtype=bool)
    if raw_mask.shape != latitude.shape:
        raise ValueError("risk_mask shape must match the guidance grid")
    mask = raw_mask & np.isfinite(latitude) & np.isfinite(longitude)
    if not np.any(mask):
        raise ValueError("risk_mask must select at least one finite grid point")
    weights = np.ones(int(np.count_nonzero(mask)), dtype=float)
    return (
        float(np.average(latitude[mask], weights=weights)),
        _circular_longitude(longitude[mask], weights),
    )


def extract_toi_features(
    grid: GuidanceGrid,
    risk_mask: np.ndarray,
    *,
    pressure_level_hpa: int | None = None,
    jet_threshold_kt: float = 50.0,
    min_grid_points: int = 4,
    maximum_match_distance_km: float = 1200.0,
    maximum_translation_kt: float = DEFAULT_MAX_JET_TRANSLATION_KT,
) -> TOIFeatures:
    """Extract experimental TOI features from a regional time series.

    June through August default to 300 hPa; other months default to 500 hPa.
    The returned bearing points from the risk centroid toward the selected jet
    object's centroid.  Track selection is transparent and deterministic: the
    track that passes closest to the risk centroid wins, followed by longer
    duration and then stronger maximum wind.
    """

    month = grid.valid_times[0].month
    level = int(pressure_level_hpa or (300 if month in {6, 7, 8} else 500))
    if level not in {300, 500}:
        raise ValueError("experimental TOI supports only 300 or 500 hPa jets")
    u_name = f"u_wind_{level}_hpa"
    v_name = f"v_wind_{level}_hpa"
    missing = [name for name in (u_name, v_name, "stp") if name not in grid.fields]
    if missing:
        raise ValueError(
            "guidance grid is missing required field(s): " + ", ".join(missing)
        )
    u = np.asarray(grid.fields[u_name], dtype=float)
    v = np.asarray(grid.fields[v_name], dtype=float)
    if u.ndim != 3 or v.ndim != 3:
        raise ValueError("TOI jet translation requires time-varying 3-D wind fields")
    if u.shape != v.shape or u.shape[0] != len(grid.valid_times):
        raise ValueError("TOI wind fields must share the guidance time/space shape")
    if u_name not in grid.units or v_name not in grid.units:
        raise ValueError("TOI wind fields require explicit units")
    u_kt = _wind_field_to_knots(u, grid.units[u_name], u_name)
    v_kt = _wind_field_to_knots(v, grid.units[v_name], v_name)
    speed_kt = np.hypot(u_kt, v_kt)

    objects_by_time = tuple(
        detect_jet_objects(
            speed_kt[index],
            grid.latitude,
            grid.longitude,
            time_index=index,
            valid_time=valid_time,
            threshold_kt=jet_threshold_kt,
            min_grid_points=min_grid_points,
        )
        for index, valid_time in enumerate(grid.valid_times)
    )
    tracks = tuple(
        track
        for track in track_jet_objects(
            objects_by_time,
            maximum_match_distance_km=maximum_match_distance_km,
            maximum_translation_kt=maximum_translation_kt,
        )
        if len(track.objects) >= 2 and track.duration_hours > 0
    )
    if not tracks:
        raise ValueError("no trackable jet object met the configured threshold")

    risk_latitude, risk_longitude = _risk_centroid(
        grid.latitude, grid.longitude, np.asarray(risk_mask)
    )

    def track_rank(track: JetTrack) -> tuple[float, float, float]:
        closest = min(
            _great_circle_km(
                risk_latitude,
                risk_longitude,
                obj.centroid_latitude,
                obj.centroid_longitude,
            )
            for obj in track.objects
        )
        maximum_wind = max(obj.maximum_wind_kt for obj in track.objects)
        return closest, -track.duration_hours, -maximum_wind

    selected_track = min(tracks, key=track_rank)
    reference_object = min(
        selected_track.objects,
        key=lambda obj: _great_circle_km(
            risk_latitude,
            risk_longitude,
            obj.centroid_latitude,
            obj.centroid_longitude,
        ),
    )
    distance_km = _great_circle_km(
        risk_latitude,
        risk_longitude,
        reference_object.centroid_latitude,
        reference_object.centroid_longitude,
    )
    bearing = _bearing_degrees(
        risk_latitude,
        risk_longitude,
        reference_object.centroid_latitude,
        reference_object.centroid_longitude,
    )

    stp = np.asarray(grid.fields["stp"], dtype=float)
    mask = np.asarray(risk_mask, dtype=bool)
    if mask.shape != grid.latitude.shape:
        raise ValueError("risk_mask shape must match the guidance grid")
    stp_values = stp[:, mask] if stp.ndim == 3 else stp[mask]
    finite_stp = stp_values[np.isfinite(stp_values)]
    if finite_stp.size == 0:
        raise ValueError("STP is unavailable inside the selected risk mask")

    return TOIFeatures(
        pressure_level_hpa=level,
        translation_speed_kt=selected_track.translation_speed_kt,
        maximum_jet_speed_kt=max(obj.maximum_wind_kt for obj in selected_track.objects),
        jet_to_risk_distance_km=distance_km,
        jet_to_risk_bearing_deg=bearing,
        maximum_stp=max(0.0, float(np.max(finite_stp))),
        month=month,
    )


__all__ = [
    "DEFAULT_MAX_JET_TRANSLATION_KT",
    "JetObject",
    "JetTrack",
    "detect_jet_objects",
    "extract_toi_features",
    "track_jet_objects",
]

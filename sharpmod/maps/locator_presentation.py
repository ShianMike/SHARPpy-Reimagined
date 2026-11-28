"""Portable presentation state for the sounding locator map.

The locator appears in three places: the small hodograph inset, an expanded
inspection window, and a standalone PNG export.  This module is the one
Qt-free contract shared by all three.  It deliberately stores choices and
geographic bounds, never pixels or fetched overlay payloads.

Bounds in this module are always ``(west, south, east, north)``.  Picker map
widgets expose ``(west, east, south, north)`` instead, so the two explicit
conversion helpers below prevent a four-float tuple from being accepted in the
wrong order without anyone noticing.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import math
from typing import Any, Mapping


PRESENTATION_VERSION = 1
PRESENTATION_META_KEY = "sharpmod_locator_presentation"
OVERLAY_STATUS_META_KEY = "sharpmod_locator_overlay_statuses"

LINK_FOLLOW = "follow"
LINK_PINNED = "pinned"
LINK_MODES = (LINK_FOLLOW, LINK_PINNED)
LINK_LABELS = {
    LINK_FOLLOW: "Follow main map",
    LINK_PINNED: "Pinned locator",
}

EXTENT_LOCAL = "local"
EXTENT_REGIONAL = "regional"
EXTENT_SELECTED = "selected-area"
EXTENT_CUSTOM = "custom"
EXTENT_MODES = (
    EXTENT_LOCAL,
    EXTENT_REGIONAL,
    EXTENT_SELECTED,
    EXTENT_CUSTOM,
)
EXTENT_LABELS = {
    EXTENT_LOCAL: "Local",
    EXTENT_REGIONAL: "Regional",
    EXTENT_SELECTED: "Fit selected area",
    EXTENT_CUSTOM: "Custom extent",
}

# Kept aligned with the established inset geometry.  They live here now so
# the expanded view/export cannot quietly choose different meanings for Local
# and Regional than the hodograph inset does.
LOCAL_HALF_LAT_DEGREES = 0.98
REGIONAL_HALF_LAT_DEGREES = 5.5
LON_ASPECT = 1.35
AREA_MARGIN = 1.18
MAX_HALF_LAT_DEGREES = 40.0
MIN_SPAN_DEGREES = 0.02
MAX_LONGITUDE_MAGNITUDE = 1_000_000.0


Bounds = tuple[float, float, float, float]


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def normalise_bounds(value: Any) -> Bounds | None:
    """Return valid ``(west, south, east, north)`` bounds or ``None``.

    An east edge at or west of the west edge is treated as an antimeridian
    crossing and unwrapped eastward.  Longitudes remain unwrapped afterwards;
    that is the same representation used by the shared picker map projection.
    """
    import sharpmod_rs

    return sharpmod_rs.locator_normalise_bounds(value)


def bounds_from_map_view(value: Any) -> Bounds | None:
    """Convert a map widget's ``(west, east, south, north)`` view tuple."""

    if isinstance(value, (str, bytes)):
        return None
    try:
        west, east, south, north = value
    except (TypeError, ValueError):
        return None
    return normalise_bounds((west, south, east, north))


def bounds_to_map_view(value: Any) -> tuple[float, float, float, float] | None:
    """Convert canonical bounds to a map widget view tuple."""

    bounds = normalise_bounds(value)
    if bounds is None:
        return None
    west, south, east, north = bounds
    return (west, east, south, north)


def area_from_map_box(value: Any) -> Bounds | None:
    """Convert a map box's ``(south, west, north, east)`` tuple."""

    if isinstance(value, (str, bytes)):
        return None
    try:
        south, west, north, east = value
    except (TypeError, ValueError):
        return None
    return normalise_bounds((west, south, east, north))


def area_to_map_box(value: Any) -> tuple[float, float, float, float] | None:
    """Convert canonical bounds to a picker map's box tuple."""
    import sharpmod_rs

    return sharpmod_rs.locator_area_to_map_box(value)


def _normalise_point(value: Any) -> tuple[float, float] | None:
    try:
        lat, lon = (_finite(item) for item in value)
    except (TypeError, ValueError):
        return None
    if lat is None or lon is None or not -90.0 <= lat <= 90.0:
        return None
    if not -540.0 <= lon <= 540.0:
        return None
    return (lat, lon)


def centred_bounds(
    point: tuple[float, float],
    *,
    half_lat: float,
    include: Bounds | None = None,
) -> Bounds:
    """Frame a point at the established ground aspect, optionally fitting area."""
    import sharpmod_rs

    lat, lon = point
    return sharpmod_rs.locator_centred_bounds(
        float(lat), float(lon), float(half_lat), include,
    )


def fit_bounds(
    point: tuple[float, float],
    area: Bounds | None,
    *,
    minimum_half_lat: float = LOCAL_HALF_LAT_DEGREES,
) -> Bounds:
    """Fit ``area`` and ``point`` with margin, centring their geographic union."""
    import sharpmod_rs

    lat, lon = point
    return sharpmod_rs.locator_fit_bounds(
        float(lat), float(lon), area, float(minimum_half_lat),
    )


@dataclass(frozen=True)
class LocatorPresentation:
    """Choices sufficient to reproduce one locator view."""

    link_mode: str = LINK_PINNED
    extent_mode: str = EXTENT_LOCAL
    custom_bounds: Bounds | None = None
    main_bounds: Bounds | None = None
    selected_area: Bounds | None = None
    scale_units: str = "metric"
    version: int = PRESENTATION_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "link_mode",
            self.link_mode if self.link_mode in LINK_MODES else LINK_PINNED,
        )
        object.__setattr__(
            self,
            "extent_mode",
            self.extent_mode if self.extent_mode in EXTENT_MODES else EXTENT_LOCAL,
        )
        object.__setattr__(self, "custom_bounds", normalise_bounds(self.custom_bounds))
        object.__setattr__(self, "main_bounds", normalise_bounds(self.main_bounds))
        object.__setattr__(self, "selected_area", normalise_bounds(self.selected_area))
        object.__setattr__(
            self, "scale_units", "imperial" if self.scale_units == "imperial" else "metric"
        )
        object.__setattr__(self, "version", PRESENTATION_VERSION)

    def to_mapping(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "version": PRESENTATION_VERSION,
            "link_mode": self.link_mode,
            "extent_mode": self.extent_mode,
            "scale_units": self.scale_units,
        }
        for key in ("custom_bounds", "main_bounds", "selected_area"):
            value = getattr(self, key)
            if value is not None:
                result[key] = list(value)
        return result

    @classmethod
    def from_mapping(cls, value: Any) -> "LocatorPresentation":
        if not isinstance(value, Mapping):
            return cls()
        return cls(
            link_mode=str(value.get("link_mode") or LINK_PINNED),
            extent_mode=str(value.get("extent_mode") or EXTENT_LOCAL),
            custom_bounds=value.get("custom_bounds"),
            main_bounds=value.get("main_bounds"),
            selected_area=value.get("selected_area"),
            scale_units=str(value.get("scale_units") or "metric"),
        )

    def updated(self, **changes: Any) -> "LocatorPresentation":
        return replace(self, **changes)

    def effective_bounds(
        self,
        point: tuple[float, float],
        *,
        represented_area: Bounds | None = None,
    ) -> Bounds:
        """Resolve the exact extent every locator renderer must use."""

        normal_point = _normalise_point(point)
        if normal_point is None:
            raise ValueError("locator point must contain finite latitude/longitude")
        if self.link_mode == LINK_FOLLOW and self.main_bounds is not None:
            return self.main_bounds
        if self.extent_mode == EXTENT_CUSTOM and self.custom_bounds is not None:
            return self.custom_bounds
        if self.extent_mode == EXTENT_REGIONAL:
            return centred_bounds(
                normal_point,
                half_lat=REGIONAL_HALF_LAT_DEGREES,
                include=represented_area,
            )
        if self.extent_mode == EXTENT_SELECTED:
            return fit_bounds(
                normal_point,
                self.selected_area or represented_area,
            )
        # Local still fits an averaged sounding's own represented area.  Cropping
        # it would misstate what the mean describes; Fit selected area is for a
        # separate active picker box.
        return centred_bounds(
            normal_point,
            half_lat=LOCAL_HALF_LAT_DEGREES,
            include=represented_area,
        )

    def summary(self) -> str:
        if self.link_mode == LINK_FOLLOW:
            if self.main_bounds is not None:
                return LINK_LABELS[LINK_FOLLOW]
            return (
                f"{LINK_LABELS[LINK_FOLLOW]} (view unavailable; "
                f"using {EXTENT_LABELS[self.extent_mode]})"
            )
        return f"{LINK_LABELS[LINK_PINNED]} · {EXTENT_LABELS[self.extent_mode]}"


def collection_presentation(collection: Any) -> LocatorPresentation:
    try:
        value = collection.getMeta(PRESENTATION_META_KEY)
    except (AttributeError, KeyError, TypeError, RuntimeError):
        value = getattr(collection, "_meta", {}).get(PRESENTATION_META_KEY)
    return LocatorPresentation.from_mapping(value)


def set_collection_presentation(
    collection: Any, presentation: LocatorPresentation
) -> LocatorPresentation:
    state = presentation.to_mapping()
    try:
        collection.setMeta(PRESENTATION_META_KEY, state)
    except (AttributeError, TypeError, RuntimeError):
        try:
            collection._meta[PRESENTATION_META_KEY] = state
        except (AttributeError, TypeError):
            pass
    return presentation


def update_collection_presentation(collection: Any, **changes: Any) -> LocatorPresentation:
    return set_collection_presentation(
        collection, collection_presentation(collection).updated(**changes)
    )


def overlay_statuses(collection: Any) -> dict[str, dict[str, Any]]:
    try:
        raw = collection.getMeta(OVERLAY_STATUS_META_KEY)
    except (AttributeError, KeyError, TypeError, RuntimeError):
        raw = getattr(collection, "_meta", {}).get(OVERLAY_STATUS_META_KEY)
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(key): dict(value)
        for key, value in raw.items()
        if isinstance(value, Mapping)
    }


def set_overlay_status(
    collection: Any,
    key: str,
    state: str,
    *,
    family: str = "",
    product: str = "",
    detail: str = "",
) -> dict[str, dict[str, Any]]:
    """Record lightweight overlay availability/provenance for UI and sessions."""

    allowed = {"loading", "available", "unavailable", "not-selected"}
    state = state if state in allowed else "unavailable"
    statuses = overlay_statuses(collection)
    statuses[str(key)] = {
        "state": state,
        "family": str(family or ""),
        "product": str(product or ""),
        "detail": " ".join(str(detail or "").split()),
        "updated_at": datetime.now(timezone.utc),
    }
    try:
        collection.setMeta(OVERLAY_STATUS_META_KEY, statuses)
    except (AttributeError, TypeError, RuntimeError):
        try:
            collection._meta[OVERLAY_STATUS_META_KEY] = statuses
        except (AttributeError, TypeError):
            pass
    return statuses


__all__ = [
    "AREA_MARGIN",
    "Bounds",
    "EXTENT_CUSTOM",
    "EXTENT_LABELS",
    "EXTENT_LOCAL",
    "EXTENT_MODES",
    "EXTENT_REGIONAL",
    "EXTENT_SELECTED",
    "LINK_FOLLOW",
    "LINK_LABELS",
    "LINK_MODES",
    "LINK_PINNED",
    "LocatorPresentation",
    "OVERLAY_STATUS_META_KEY",
    "PRESENTATION_META_KEY",
    "area_from_map_box",
    "area_to_map_box",
    "bounds_from_map_view",
    "bounds_to_map_view",
    "centred_bounds",
    "collection_presentation",
    "fit_bounds",
    "normalise_bounds",
    "overlay_statuses",
    "set_collection_presentation",
    "set_overlay_status",
    "update_collection_presentation",
]

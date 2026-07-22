"""Utilities for composing and navigating multi-hour sounding timelines."""

from __future__ import annotations

from datetime import datetime

from sharppy.sharptab.prof_collection import ProfCollection


def forecast_hour_range(available, start, end, step=1) -> tuple[int, ...]:
    """Return an inclusive, validated subset of selectable forecast hours."""
    values = tuple(sorted({int(value) for value in available}))
    if not values:
        raise ValueError("no forecast hours are available")
    start = int(start)
    end = int(end)
    step = int(step)
    if step < 1:
        raise ValueError("timeline step must be at least one hour")
    if start > end:
        raise ValueError("timeline start must not be after its end")
    selected = tuple(
        value for value in values
        if start <= value <= end and (value - start) % step == 0
    )
    if not selected:
        raise ValueError("the selected range contains no available forecast hour")
    if selected[0] != start or selected[-1] != end:
        raise ValueError("timeline start and end must be available forecast hours")
    return selected


def combine_collections(collections) -> ProfCollection:
    """Combine ordered single-member collections into one time collection.

    The input collections are left untouched. Duplicate valid times are
    rejected because SHARPpy selects a time by its datetime value.
    """
    collections = tuple(collections)
    if not collections:
        raise ValueError("at least one profile collection is required")

    profiles = []
    dates: list[datetime] = []
    hours = []
    source_paths = []
    source_metadata = []
    for collection in collections:
        member_names = tuple(getattr(collection, "_profs", {}))
        if len(member_names) != 1:
            raise ValueError(
                "timeline inputs must contain one deterministic member"
            )
        member_profiles = collection._profs[member_names[0]]
        member_dates = list(getattr(collection, "_dates", ()))
        if len(member_profiles) != len(member_dates) or not member_profiles:
            raise ValueError("timeline input has inconsistent profile dates")
        profiles.extend(member_profiles)
        dates.extend(member_dates)
        try:
            hours.extend([int(collection.getMeta("fxx"))] * len(member_dates))
        except (KeyError, TypeError, ValueError):
            pass
        try:
            source_paths.append(str(collection.getMeta("npz_path")))
        except (KeyError, TypeError, ValueError):
            pass
        source_metadata.extend(
            [dict(getattr(collection, "_meta", {}))] * len(member_dates)
        )

    if len(set(dates)) != len(dates):
        raise ValueError("timeline inputs contain duplicate valid times")
    order = sorted(range(len(dates)), key=dates.__getitem__)
    sorted_dates = [dates[index] for index in order]
    sorted_profiles = [profiles[index] for index in order]

    first = collections[0]
    metadata = dict(getattr(first, "_meta", {}))
    metadata["timeline"] = True
    metadata["timeline_count"] = len(sorted_dates)
    if len(hours) == len(dates):
        metadata["timeline_hours"] = [hours[index] for index in order]
    if source_paths:
        metadata["timeline_sources"] = source_paths
    if len(source_metadata) == len(dates):
        metadata["timeline_provenance"] = [
            source_metadata[index] for index in order
        ]
    result = ProfCollection(
        {"": sorted_profiles},
        sorted_dates,
        target_type=getattr(first, "_target_type", None),
        **metadata,
    )
    # Older SHARPpy versions require a real type even if a custom collection
    # omitted the private target metadata.
    if result._target_type is None:
        result._target_type = first._target_type
    return result


def append_collection(timeline, incoming) -> int:
    """Append one deterministic profile in-place and return its sorted index.

    This is the GUI streaming seam: completed hours can become usable while
    the remaining queue continues. The currently displayed valid time is
    preserved when a newly completed hour sorts before it.
    """
    target_members = tuple(getattr(timeline, "_profs", {}))
    incoming_members = tuple(getattr(incoming, "_profs", {}))
    if len(target_members) != 1 or len(incoming_members) != 1:
        raise ValueError("timeline streaming requires deterministic profiles")
    source_profiles = incoming._profs[incoming_members[0]]
    source_dates = list(getattr(incoming, "_dates", ()))
    if len(source_profiles) != 1 or len(source_dates) != 1:
        raise ValueError("one completed timeline hour is required")
    valid = source_dates[0]
    if valid in timeline._dates:
        raise ValueError(f"timeline already contains {valid!s}")
    current = timeline.getCurrentDate()
    target_profiles = timeline._profs[target_members[0]]
    target_profiles.append(source_profiles[0])
    timeline._dates.append(valid)

    existing_hours = list(timeline._meta.get("timeline_hours", ()))
    if len(existing_hours) != len(timeline._dates) - 1:
        existing_hours = [None] * (len(timeline._dates) - 1)
    try:
        hour = int(incoming.getMeta("fxx"))
    except (KeyError, TypeError, ValueError):
        hour = None
    existing_hours.append(hour)

    order = sorted(range(len(timeline._dates)), key=timeline._dates.__getitem__)
    timeline._dates[:] = [timeline._dates[index] for index in order]
    target_profiles[:] = [target_profiles[index] for index in order]
    timeline._meta["timeline"] = True
    timeline._meta["timeline_count"] = len(timeline._dates)
    timeline._meta["timeline_hours"] = [existing_hours[index] for index in order]
    sources = list(timeline._meta.get("timeline_sources", ()))
    try:
        sources.append(str(incoming.getMeta("npz_path")))
    except (KeyError, TypeError, ValueError):
        pass
    if len(sources) == len(timeline._dates):
        timeline._meta["timeline_sources"] = [
            sources[index] for index in order
        ]
    elif sources:
        timeline._meta["timeline_sources"] = sources
    source_metadata = list(timeline._meta.get("timeline_provenance", ()))
    if len(source_metadata) != len(timeline._dates) - 1:
        source_metadata = [dict(timeline._meta)] * (len(timeline._dates) - 1)
    source_metadata.append(dict(getattr(incoming, "_meta", {})))
    timeline._meta["timeline_provenance"] = [
        source_metadata[index] for index in order
    ]

    # These private lists are the state tracked by the vendored collection for
    # edits/interpolation. Streaming adds untouched raw profiles only.
    count = len(timeline._dates)
    timeline._mod_therm = [False] * count
    timeline._mod_wind = [False] * count
    timeline._interp = [False] * count
    timeline._orig_profs.clear()
    timeline._interp_profs.clear()
    if current in timeline._dates:
        timeline.setCurrentDate(current)
    return timeline._dates.index(valid)


def timeline_dates(collection) -> tuple[datetime, ...]:
    """Return the collection's ordered valid times without mutating it."""
    return tuple(getattr(collection, "_dates", ()))


__all__ = [
    "append_collection", "combine_collections", "forecast_hour_range",
    "timeline_dates",
]

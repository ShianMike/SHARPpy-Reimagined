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
        value
        for value in values
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
            raise ValueError("timeline inputs must contain one deterministic member")
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
        metadata["timeline_provenance"] = [source_metadata[index] for index in order]
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


def combine_ensemble_collections(
    collections,
    member_names=None,
) -> ProfCollection:
    """Combine single-time collections into one multi-member collection.

    The source collections and their profile lists are not changed.  Member
    names default to each collection's ``member`` metadata when present and
    otherwise to its sole profile-dictionary key.
    """
    collections = tuple(collections)
    if not collections:
        raise ValueError("at least one profile collection is required")

    source_members = []
    source_profiles = []
    source_dates = []
    for collection in collections:
        members = tuple(getattr(collection, "_profs", {}))
        if len(members) != 1:
            raise ValueError("ensemble inputs must each contain exactly one member")
        profiles = tuple(collection._profs[members[0]])
        dates = tuple(getattr(collection, "_dates", ()))
        if len(profiles) != 1 or len(dates) != 1:
            raise ValueError("ensemble inputs must each contain exactly one valid time")
        if not isinstance(dates[0], datetime):
            raise ValueError("ensemble input valid time must be a datetime")
        source_members.append(members[0])
        source_profiles.append(profiles[0])
        source_dates.append(dates[0])

    valid = source_dates[0]
    if any(date != valid for date in source_dates[1:]):
        raise ValueError("ensemble inputs must share the same valid time")

    if member_names is None:
        labels = []
        for collection, source_member in zip(collections, source_members):
            try:
                label = collection.getMeta("member")
            except (KeyError, TypeError, ValueError):
                label = source_member
            labels.append(label)
        labels = tuple(labels)
    else:
        labels = tuple(member_names)
        if len(labels) != len(collections):
            raise ValueError("member_names must contain one label for each collection")

    if any(not isinstance(label, str) or not label for label in labels):
        raise ValueError("ensemble member labels must be non-empty strings")
    if len(set(labels)) != len(labels):
        raise ValueError("ensemble member labels must be unique")

    first = collections[0]
    profiles_by_member = {
        label: [profile] for label, profile in zip(labels, source_profiles)
    }
    provenance = []
    for label, collection in zip(labels, collections):
        source = dict(getattr(collection, "_meta", {}))
        source["member_label"] = label
        provenance.append(source)

    metadata = dict(getattr(first, "_meta", {}))
    metadata.update(
        {
            "ensemble": True,
            "ensemble_count": len(labels),
            "ensemble_member_count": len(labels),
            "ensemble_members": list(labels),
            "ensemble_provenance": provenance,
        }
    )
    if metadata.get("highlight") not in profiles_by_member:
        metadata["highlight"] = labels[0]

    target_type = getattr(first, "_target_type", None)
    if target_type is None:
        result = ProfCollection(profiles_by_member, [valid], **metadata)
    else:
        result = ProfCollection(
            profiles_by_member,
            [valid],
            target_type=target_type,
            **metadata,
        )
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

    existing_dates = tuple(timeline._dates)
    edit_state_by_valid = {
        date: (
            timeline._mod_therm[index],
            timeline._mod_wind[index],
            timeline._interp[index],
        )
        for index, date in enumerate(existing_dates)
    }
    originals_by_valid = {
        existing_dates[index]: profile
        for index, profile in timeline._orig_profs.items()
        if 0 <= index < len(existing_dates)
    }
    interpolated_by_valid = {
        existing_dates[index]: profile
        for index, profile in timeline._interp_profs.items()
        if 0 <= index < len(existing_dates)
    }

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
        timeline._meta["timeline_sources"] = [sources[index] for index in order]
    elif sources:
        timeline._meta["timeline_sources"] = sources
    source_metadata = list(timeline._meta.get("timeline_provenance", ()))
    if len(source_metadata) != len(timeline._dates) - 1:
        source_metadata = [dict(timeline._meta)] * (len(timeline._dates) - 1)
    source_metadata.append(dict(getattr(incoming, "_meta", {})))
    timeline._meta["timeline_provenance"] = [source_metadata[index] for index in order]

    # The vendored collection keys edit/interpolation state by time index.
    # Re-key existing state after sorting; the streamed profile starts clean.
    sorted_edit_state = [
        edit_state_by_valid.get(date, (False, False, False)) for date in timeline._dates
    ]
    timeline._mod_therm[:] = [state[0] for state in sorted_edit_state]
    timeline._mod_wind[:] = [state[1] for state in sorted_edit_state]
    timeline._interp[:] = [state[2] for state in sorted_edit_state]
    timeline._orig_profs.clear()
    timeline._orig_profs.update(
        {
            index: originals_by_valid[date]
            for index, date in enumerate(timeline._dates)
            if date in originals_by_valid
        }
    )
    timeline._interp_profs.clear()
    timeline._interp_profs.update(
        {
            index: interpolated_by_valid[date]
            for index, date in enumerate(timeline._dates)
            if date in interpolated_by_valid
        }
    )
    if current in timeline._dates:
        timeline.setCurrentDate(current)
    return timeline._dates.index(valid)


def timeline_dates(collection) -> tuple[datetime, ...]:
    """Return the collection's ordered valid times without mutating it."""
    return tuple(getattr(collection, "_dates", ()))


__all__ = [
    "append_collection",
    "combine_collections",
    "combine_ensemble_collections",
    "forecast_hour_range",
    "timeline_dates",
]

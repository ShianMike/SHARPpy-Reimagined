"""Merge logic for restoring undo history while forecast hours stream into a sounding
collection.

Additive timeline growth preserves the stored edit state and carries forward newly
arrived profiles; non-additive shape changes retain exact-snapshot behavior. Session
serialization and history ownership remain in ``sessions``."""

from __future__ import annotations

from copy import deepcopy
from sharpmod.state import sessions as _api


def _merge_streamed_snapshot_state(stored: dict, live: dict) -> dict:
    """Keep dates added after a history entry without changing its edit state.

    Undo records intentionally snapshot a whole SHARPpy collection because one
    vendored edit can update several derived arrays at once. Forecast timelines
    are different: their remaining hours can arrive *after* an edit was
    recorded. Restoring that older snapshot verbatim would therefore remove the
    newly streamed hours. When the live dates are a strict superset, align the
    stored profiles and index-keyed edit state onto the live timeline and copy
    only the newly added dates from the live snapshot.

    Any non-additive shape change retains the old exact-snapshot behavior. That
    keeps this narrowly scoped to streaming and avoids guessing when a future
    operation deliberately removes or replaces profiles.
    """
    try:
        stored_dates = list(stored["dates"])
        live_dates = list(live["dates"])
        stored_profiles = stored["profiles"]
        live_profiles = live["profiles"]
        stored_keys = [_api._snapshot_date_key(item) for item in stored_dates]
        live_keys = [_api._snapshot_date_key(item) for item in live_dates]
    except (KeyError, TypeError, ValueError):
        return stored

    if (
        len(live_keys) <= len(stored_keys)
        or len(set(stored_keys)) != len(stored_keys)
        or len(set(live_keys)) != len(live_keys)
        or not set(stored_keys).issubset(live_keys)
        or set(stored_profiles) != set(live_profiles)
    ):
        return stored

    stored_index = {key: index for index, key in enumerate(stored_keys)}
    live_index = {key: index for index, key in enumerate(live_keys)}
    if any(
        len(stored_profiles[member]) != len(stored_dates)
        or len(live_profiles[member]) != len(live_dates)
        for member in stored_profiles
    ):
        return stored

    merged = deepcopy(stored)
    merged["dates"] = deepcopy(live_dates)
    merged["profiles"] = {
        member: [
            deepcopy(
                stored_profiles[member][stored_index[key]]
                if key in stored_index
                else live_profiles[member][live_index[key]]
            )
            for key in live_keys
        ]
        for member in stored_profiles
    }

    for field in ("modified_thermo", "modified_wind", "interpolated"):
        stored_values = _api._flags(stored.get(field), len(stored_dates))
        live_values = _api._flags(live.get(field), len(live_dates))
        merged[field] = [
            stored_values[stored_index[key]]
            if key in stored_index
            else live_values[live_index[key]]
            for key in live_keys
        ]

    for field in ("original_profiles", "interpolated_profiles"):
        stored_values = stored.get(field) or {}
        live_values = live.get(field) or {}
        values = {}
        for index, key in enumerate(live_keys):
            source = stored_values if key in stored_index else live_values
            source_index = stored_index.get(key, live_index[key])
            item = source.get(str(source_index))
            if item is not None:
                values[str(index)] = deepcopy(item)
        merged[field] = values

    # Timeline provenance, source paths, count, and late locator context are
    # additive external state, not part of a profile edit. Keep their live form.
    for field in ("meta", "locator_overlays"):
        if field in live:
            merged[field] = deepcopy(live[field])

    try:
        selected_key = stored_keys[int(stored.get("profile_index", 0))]
        merged["profile_index"] = live_index[selected_key]
    except (IndexError, TypeError, ValueError):
        merged["profile_index"] = int(live.get("profile_index", 0) or 0)
    return merged

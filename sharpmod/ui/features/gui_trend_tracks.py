"""Which parameters the trend chart stacks, in which order, and how it persists.

The trend chart plotted exactly one parameter. Reading how CAPE and shear evolve
together meant switching the selector back and forth and remembering the previous
shape, which is not a comparison. Stacking them is, provided each keeps its own
value axis: CAPE in J/kg and shear in knots share no scale, and putting them on
one axis flattens the smaller quantity into a line along the bottom.

Selection, ordering, validation, and reset are the same problem the comparison
columns already solved, so :class:`sharpmod.ui.features.gui_metric_columns.MetricColumnsDialog`
is reused with its own wording and its own bound. The bound differs because the
constraint differs: a table can carry twelve columns, but a stack of twelve tracks
gives each one a few pixels of height.
"""

from __future__ import annotations

import json

from sharpmod.ui.features.gui_common import _LOGGER
from sharpmod.ui.features.gui_metric_columns import MetricColumnsDialog, available_keys
from sharpmod.analysis.profile_metrics import DEFAULT_METRIC_KEYS

#: Durable preference, stored beside the other additive settings documents.
SETTINGS_KEY = "analysis/trend_tracks"

SETTINGS_VERSION = 1

#: Beyond four bands each track is too short to read a shape in, which is the
#: only reason to stack them at all.
MAX_TRACKS = 4

#: One track by default, so the chart opens exactly as it did before stacking
#: existed and the extra tracks are something the analyst asks for.
DEFAULT_TRACK_COUNT = 1


def default_tracks():
    """Return the single default track."""
    keys = tuple(DEFAULT_METRIC_KEYS) or ("mlcape",)
    return keys[:DEFAULT_TRACK_COUNT]


def normalize_keys(keys, *, default=None):
    """Return a valid, de-duplicated, bounded track order."""
    default = tuple(default) if default else default_tracks()
    known = set(available_keys())
    ordered = []
    for key in tuple(keys or ()):
        name = str(key)
        if name in known and name not in ordered:
            ordered.append(name)
        if len(ordered) >= MAX_TRACKS:
            break
    return tuple(ordered) if ordered else tuple(default)


def read_preference(settings, *, default=None):
    """Read the durable track preference, tolerating anything unusable."""
    default = tuple(default) if default else default_tracks()
    if settings is None:
        return tuple(default)
    try:
        raw = settings.value(SETTINGS_KEY, "", str)
    except TypeError:
        raw = settings.value(SETTINGS_KEY, "")
    if not raw:
        return tuple(default)
    try:
        document = json.loads(str(raw))
    except (TypeError, ValueError):
        _LOGGER.debug("trend_tracks.preference_unreadable")
        return tuple(default)
    if not isinstance(document, dict):
        return tuple(default)
    try:
        version = int(document.get("version", 0))
    except (TypeError, ValueError):
        return tuple(default)
    if version > SETTINGS_VERSION:
        # Written by a newer build: leave it alone rather than downgrading it.
        return tuple(default)
    keys = document.get("tracks")
    if not isinstance(keys, (list, tuple)):
        return tuple(default)
    return normalize_keys(keys, default=default)


def write_preference(settings, keys) -> bool:
    """Persist the track preference; returns whether it was stored."""
    if settings is None:
        return False
    payload = json.dumps(
        {"version": SETTINGS_VERSION, "tracks": list(normalize_keys(keys))}
    )
    try:
        settings.setValue(SETTINGS_KEY, payload)
        settings.sync()
    except (AttributeError, RuntimeError, TypeError):
        _LOGGER.debug("trend_tracks.preference_not_written")
        return False
    return True


def track_dialog(current, parent=None):
    """Return a picker for the stacked trend tracks."""
    return MetricColumnsDialog(
        normalize_keys(current),
        parent,
        default=default_tracks(),
        title="Trend tracks",
        hint_text=(
            "Checked diagnostics become stacked tracks, in the order listed. They "
            f"share the time axis only; each keeps its own units and value scale. "
            f"Up to {MAX_TRACKS} may be chosen, because a shorter band is harder "
            "to read a shape in."
        ),
        list_name="Stacked trend tracks, in order",
        limit=MAX_TRACKS,
    )


__all__ = [
    "DEFAULT_TRACK_COUNT",
    "MAX_TRACKS",
    "SETTINGS_KEY",
    "SETTINGS_VERSION",
    "default_tracks",
    "normalize_keys",
    "read_preference",
    "track_dialog",
    "write_preference",
]

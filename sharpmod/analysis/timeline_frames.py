"""An immutable per-frame acquisition ledger for forecast-hour timelines.

A timeline used to be nothing but the hours that happened to arrive. The vendored
``ProfCollection`` stores parallel lists keyed by position in its sorted ``_dates``,
so an hour that was requested and then failed, was cancelled, or was never
attempted simply is not there. Its error string lived on the Qt worker object and
died with it, and the requested range -- validated by
:func:`sharpmod.analysis.profile_timeline.forecast_hour_range` and then thrown away -- was
unrecoverable. The result was a playback slider that presented four loaded hours
as a continuous four-hour forecast when twelve had been asked for.

This module records what was asked for and what became of each hour, so a gap can
be drawn as a gap and explained. It deliberately mirrors
:class:`sharpmod.analysis.ensemble_members.EnsembleAcquisition`: same immutable-ledger
shape, same requested-versus-loaded split, same portable ``_meta`` round trip. The
status words are the ones the goal names.

**Frames are keyed by forecast hour, never by index.**
:func:`sharpmod.analysis.profile_timeline.append_collection` re-sorts every index-keyed
parallel list each time a streamed hour lands, so an index-keyed ledger would
silently re-associate reasons with the wrong hours. The forecast hour is the only
identity that survives a late-arriving earlier hour.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from types import MappingProxyType
from typing import Iterable, Mapping

#: Every state a requested frame can be in, in lifecycle order.
#:
#: ``requested`` is the honest starting point: asked for, nothing attempted yet.
#: ``loading`` is in flight. ``loaded`` has a usable profile. ``failed`` was
#: attempted and errored. ``canceled`` was abandoned because the reader stopped
#: the queue or closed the viewer. ``unavailable`` means the source does not
#: publish it at all, which is not a failure of this application and is worth
#: distinguishing so a reader does not retry it forever. ``unknown`` is for a
#: frame whose outcome genuinely was not reported -- it is never inferred.
FRAME_STATES = (
    "requested",
    "loading",
    "loaded",
    "failed",
    "canceled",
    "unavailable",
    "unknown",
)

#: States that will not change without a new request.
TERMINAL_STATES = ("loaded", "failed", "canceled", "unavailable")

#: States that are still expected to resolve.
PENDING_STATES = ("requested", "loading")

#: A frame in any of these states has no plottable value, and the difference
#: between them is exactly what the reader needs in order to know what to do.
MISSING_STATES = ("requested", "loading", "failed", "canceled", "unavailable", "unknown")

#: Human wording, so the chart, the toolbar, and a report describe a frame the
#: same way rather than each inventing a phrase.
STATE_LABELS = MappingProxyType(
    {
        "requested": "requested, not started",
        "loading": "loading",
        "loaded": "loaded",
        "failed": "failed",
        "canceled": "canceled",
        "unavailable": "not published by the source",
        "unknown": "outcome unknown",
    }
)

#: Batch/worker vocabulary that means the same thing. ``cancelled`` is the
#: spelling ``ensemble_members`` and the batch runner use; the goal specifies
#: ``canceled``, so one spelling is stored and both are accepted.
_STATE_ALIASES = MappingProxyType(
    {
        "cancelled": "canceled",
        "complete": "loaded",
        "completed": "loaded",
        "success": "loaded",
        "succeeded": "loaded",
        "error": "failed",
        "working": "loading",
        "pending": "requested",
        "queued": "requested",
        "missing": "unavailable",
    }
)

_META_FRAMES = "timeline_frames"
_META_RUN = "timeline_run"


def normalize_state(value) -> str:
    """Return a canonical frame state, translating known synonyms.

    Anything unrecognised becomes ``unknown`` rather than raising: this is fed
    from a batch runner's event stream, and a new status word there must degrade
    into "we do not know" instead of taking the timeline down.
    """
    text = str(value or "").strip().lower()
    text = _STATE_ALIASES.get(text, text)
    return text if text in FRAME_STATES else "unknown"


@dataclass(frozen=True)
class TimelineFrame:
    """One requested forecast hour and what became of it."""

    forecast_hour: int
    state: str = "requested"
    valid_time: datetime | None = None
    reason: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "forecast_hour", int(self.forecast_hour))
        object.__setattr__(self, "state", normalize_state(self.state))
        if self.valid_time is not None and not isinstance(self.valid_time, datetime):
            raise ValueError("frame valid time must be a datetime or None")
        if self.state == "loaded" and self.valid_time is None:
            raise ValueError("a loaded frame must carry the valid time it loaded")
        if self.reason is not None:
            object.__setattr__(self, "reason", str(self.reason))

    @property
    def available(self) -> bool:
        """Whether this frame has a usable profile."""
        return self.state == "loaded"

    @property
    def pending(self) -> bool:
        """Whether this frame is still expected to resolve."""
        return self.state in PENDING_STATES

    @property
    def label(self) -> str:
        """``F018`` -- the form every other forecast-hour control already uses."""
        return f"F{self.forecast_hour:03d}"

    def describe(self) -> str:
        """One line naming the hour, its state, and any reason given."""
        text = f"{self.label}: {STATE_LABELS[self.state]}"
        if self.reason:
            text += f" \u2014 {self.reason}"
        return text


@dataclass(frozen=True)
class TimelineCoverage:
    """What a timeline request asked for and what each hour turned into."""

    frames: tuple[TimelineFrame, ...] = ()
    run_time: datetime | None = None

    def __post_init__(self):
        frames = tuple(self.frames)
        hours = [frame.forecast_hour for frame in frames]
        if len(set(hours)) != len(hours):
            raise ValueError("each forecast hour needs exactly one frame")
        if self.run_time is not None and not isinstance(self.run_time, datetime):
            raise ValueError("timeline run time must be a datetime or None")
        object.__setattr__(
            self,
            "frames",
            tuple(sorted(frames, key=lambda frame: frame.forecast_hour)),
        )

    # -- construction -------------------------------------------------------- #

    @classmethod
    def requested_range(cls, hours: Iterable[int], *, run_time=None) -> "TimelineCoverage":
        """Open a ledger for a validated forecast-hour range."""
        return cls(
            tuple(TimelineFrame(int(hour)) for hour in sorted({int(h) for h in hours})),
            run_time,
        )

    @classmethod
    def from_collection(cls, collection) -> "TimelineCoverage":
        """Rebuild the ledger a collection carries, or derive a loaded-only one.

        A collection that predates this ledger, or one assembled by a path that
        does not record coverage, still has its loaded hours and valid times. It
        is reported as fully loaded, which is truthful: nothing is known to have
        been requested beyond what is present.
        """
        stored = _meta(collection, _META_FRAMES)
        run_time = _meta(collection, _META_RUN)
        if not isinstance(run_time, datetime):
            run_time = _meta(collection, "run") if isinstance(
                _meta(collection, "run"), datetime
            ) else None
        if isinstance(stored, (list, tuple)) and stored:
            frames = []
            for item in stored:
                if not isinstance(item, Mapping):
                    continue
                try:
                    hour = int(item.get("forecast_hour"))
                except (TypeError, ValueError):
                    continue
                valid = item.get("valid_time")
                if not isinstance(valid, datetime):
                    valid = None
                state = normalize_state(item.get("state"))
                if state == "loaded" and valid is None:
                    # A loaded frame with no time is not loaded as far as anyone
                    # can prove; say so rather than raising on a saved file.
                    state = "unknown"
                frames.append(
                    TimelineFrame(hour, state, valid, item.get("reason"))
                )
            if frames:
                return cls(tuple(frames), run_time)
        return cls._from_loaded(collection, run_time)

    @classmethod
    def _from_loaded(cls, collection, run_time) -> "TimelineCoverage":
        dates = tuple(getattr(collection, "_dates", ()) or ())
        hours = _meta(collection, "timeline_hours")
        frames = []
        for index, date in enumerate(dates):
            hour = None
            if isinstance(hours, (list, tuple)) and index < len(hours):
                try:
                    hour = int(hours[index])
                except (TypeError, ValueError):
                    hour = None
            if hour is None:
                hour = index
            frames.append(TimelineFrame(hour, "loaded", date))
        return cls(tuple(frames), run_time)

    # -- queries ------------------------------------------------------------- #

    def __len__(self) -> int:
        return len(self.frames)

    def __iter__(self):
        return iter(self.frames)

    @property
    def requested_hours(self) -> tuple[int, ...]:
        """Every hour the request covered, in order."""
        return tuple(frame.forecast_hour for frame in self.frames)

    def hours_in(self, *states: str) -> tuple[int, ...]:
        """Return the hours currently in any of ``states``."""
        wanted = {normalize_state(state) for state in states}
        return tuple(
            frame.forecast_hour for frame in self.frames if frame.state in wanted
        )

    @property
    def loaded_hours(self) -> tuple[int, ...]:
        return self.hours_in("loaded")

    @property
    def loading_hours(self) -> tuple[int, ...]:
        return self.hours_in("loading")

    @property
    def failed_hours(self) -> tuple[int, ...]:
        return self.hours_in("failed")

    @property
    def canceled_hours(self) -> tuple[int, ...]:
        return self.hours_in("canceled")

    @property
    def unavailable_hours(self) -> tuple[int, ...]:
        return self.hours_in("unavailable")

    @property
    def unknown_hours(self) -> tuple[int, ...]:
        return self.hours_in("unknown")

    @property
    def missing_hours(self) -> tuple[int, ...]:
        """Every requested hour without a usable profile, whatever the reason."""
        return self.hours_in(*MISSING_STATES)

    @property
    def pending_hours(self) -> tuple[int, ...]:
        return self.hours_in(*PENDING_STATES)

    def is_complete(self) -> bool:
        """Whether every requested hour has reached a terminal state."""
        return not self.pending_hours

    def frame_for(self, hour) -> TimelineFrame | None:
        try:
            wanted = int(hour)
        except (TypeError, ValueError):
            return None
        return next(
            (frame for frame in self.frames if frame.forecast_hour == wanted), None
        )

    def frame_at(self, valid_time) -> TimelineFrame | None:
        """Return the frame that loaded ``valid_time``, if any."""
        if not isinstance(valid_time, datetime):
            return None
        return next(
            (frame for frame in self.frames if frame.valid_time == valid_time), None
        )

    def state_of(self, hour) -> str:
        """Return one hour's state; an hour never requested is ``unknown``."""
        frame = self.frame_for(hour)
        return frame.state if frame is not None else "unknown"

    # -- transitions --------------------------------------------------------- #

    def with_state(self, hour, state, *, valid_time=None, reason=None) -> "TimelineCoverage":
        """Return a new ledger with one hour moved to ``state``.

        An hour that was not part of the original request is added, because a
        source can legitimately return an hour that was not asked for and
        pretending otherwise would lose it.
        """
        wanted = int(hour)
        state = normalize_state(state)
        frames = []
        found = False
        for frame in self.frames:
            if frame.forecast_hour != wanted:
                frames.append(frame)
                continue
            found = True
            frames.append(
                replace(
                    frame,
                    state=state,
                    # A resolved time is never discarded by a later transition:
                    # a frame that loaded and was then superseded still tells the
                    # reader which valid time it had.
                    valid_time=valid_time if valid_time is not None else frame.valid_time,
                    reason=reason,
                )
            )
        if not found:
            frames.append(TimelineFrame(wanted, state, valid_time, reason))
        return TimelineCoverage(tuple(frames), self.run_time)

    def with_pending_resolved(self, state="canceled", *, reason=None) -> "TimelineCoverage":
        """Close every still-pending hour, for when a queue stops early.

        Used when the reader cancels or closes the viewer: the hours that never
        started are ``canceled``, not ``unknown``, because the cause is known.
        """
        state = normalize_state(state)
        return TimelineCoverage(
            tuple(
                replace(frame, state=state, reason=reason) if frame.pending else frame
                for frame in self.frames
            ),
            self.run_time,
        )

    # -- presentation and portability ---------------------------------------- #

    def summary(self) -> str:
        """State the coverage in one sentence, counting every category present."""
        if not self.frames:
            return "No forecast hours were requested."
        parts = [f"{len(self.loaded_hours)} of {len(self.frames)} requested hour(s) loaded"]
        for state in ("loading", "requested", "failed", "canceled", "unavailable", "unknown"):
            hours = self.hours_in(state)
            if hours:
                parts.append(f"{len(hours)} {STATE_LABELS[state]}")
        return "; ".join(parts) + "."

    def explain(self) -> tuple[str, ...]:
        """One line per hour that is not loaded, for a status list or a report."""
        return tuple(
            frame.describe() for frame in self.frames if frame.state != "loaded"
        )

    def to_metadata(self) -> dict[str, object]:
        return {
            _META_FRAMES: [
                {
                    "forecast_hour": frame.forecast_hour,
                    "state": frame.state,
                    "valid_time": frame.valid_time,
                    "reason": frame.reason,
                }
                for frame in self.frames
            ],
            _META_RUN: self.run_time,
        }

    def attach(self, collection) -> None:
        """Attach the ledger to a profile collection's metadata.

        Stored by hour, so :func:`profile_timeline.append_collection` re-sorting
        the index-keyed lists cannot scramble it.
        """
        for key, value in self.to_metadata().items():
            _set_meta(collection, key, value)


def _meta(collection, key, default=None):
    try:
        value = collection.getMeta(key)
    except (AttributeError, KeyError, TypeError, ValueError):
        value = getattr(collection, "_meta", {}).get(key, default)
    return default if value is None else value


def _set_meta(collection, key, value) -> None:
    try:
        collection.setMeta(key, value)
        return
    except (AttributeError, KeyError, TypeError, ValueError):
        pass
    meta = getattr(collection, "_meta", None)
    if isinstance(meta, dict):
        meta[key] = value


__all__ = [
    "FRAME_STATES",
    "MISSING_STATES",
    "PENDING_STATES",
    "STATE_LABELS",
    "TERMINAL_STATES",
    "TimelineCoverage",
    "TimelineFrame",
    "normalize_state",
]

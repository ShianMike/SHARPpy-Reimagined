"""Notice that a newer model run exists, without ever adopting it.

A sounding loaded from the 06Z run stays the 06Z run. When 12Z publishes, the
analysis on screen does not become wrong -- it becomes *a different question* -- and
swapping the data underneath a reader who has been building a case on it is the one
behaviour this must not have. Silently refreshing would also invalidate every
number already written into notes or a comparison.

So this module only ever *reports*. It answers "is there something newer, and how
much newer", and the caller offers an explicit action. Nothing here mutates a
collection, and nothing here fetches.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

#: Models whose publishing cadence is known well enough to say "newer exists"
#: without a network call. Anything absent reports "unknown", not "up to date":
#: claiming currency for a model whose schedule is not known would be a guess.
_CYCLE_HOURS = {
    "hrrr": 1,
    "rap": 1,
    "nam": 6,
    "gfs": 6,
    "namnest": 6,
    "rrfs": 1,
}

#: How long after a cycle time that run is normally complete enough to load. A
#: newer cycle whose data has not published yet is not an available update.
_PUBLISH_DELAY = {
    "hrrr": timedelta(minutes=55),
    "rap": timedelta(minutes=55),
    "nam": timedelta(hours=3),
    "gfs": timedelta(hours=4),
    "namnest": timedelta(hours=3),
    "rrfs": timedelta(hours=1),
}

_DEFAULT_DELAY = timedelta(hours=1)


@dataclass(frozen=True)
class RunUpdate:
    """Whether a newer run is believed to exist, and what it is."""

    model: str
    current_run: datetime | None
    latest_run: datetime | None
    available: bool
    reason: str = ""

    @property
    def newer_by(self) -> timedelta | None:
        if self.current_run is None or self.latest_run is None:
            return None
        return self.latest_run - self.current_run

    def summary(self) -> str:
        """One sentence a reader can act on, or an explanation of why not."""
        if not self.available:
            return self.reason or "No newer run is known."
        gap = self.newer_by
        hours = "" if gap is None else f", {int(gap.total_seconds() // 3600)} h newer"
        return (
            f"{self.model.upper()} {self.latest_run:%Y-%m-%d %HZ} has published"
            f"{hours}. The analysis on screen is still "
            f"{self.current_run:%Y-%m-%d %HZ} and will not change until you load it."
        )


def _normalize(value):
    if not isinstance(value, datetime):
        return None
    return (
        value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    ).astimezone(timezone.utc)


def latest_cycle(model, now=None):
    """Return the newest cycle of ``model`` believed to have published."""
    key = str(model or "").strip().lower()
    step = _CYCLE_HOURS.get(key)
    if step is None:
        return None
    moment = _normalize(now) or datetime.now(timezone.utc)
    delay = _PUBLISH_DELAY.get(key, _DEFAULT_DELAY)
    candidate = (moment - delay).replace(minute=0, second=0, microsecond=0)
    candidate -= timedelta(hours=candidate.hour % step)
    return candidate


def check(model, current_run, now=None) -> RunUpdate:
    """Report whether a newer run of ``model`` exists. Never fetches or mutates."""
    key = str(model or "").strip().lower()
    current = _normalize(current_run)
    if not key:
        return RunUpdate("", current, None, False, "The model is unknown.")
    if current is None:
        return RunUpdate(
            key, None, None, False, "This sounding has no run time to compare."
        )
    if key not in _CYCLE_HOURS:
        # Honest about the limit rather than implying the analysis is current.
        return RunUpdate(
            key,
            current,
            None,
            False,
            f"{key.upper()} publishing cadence is not known here, so newer runs "
            "are not detected.",
        )
    latest = latest_cycle(key, now)
    if latest is None or latest <= current:
        return RunUpdate(
            key, current, latest, False, "This is the newest published run."
        )
    return RunUpdate(key, current, latest, True)


def check_collection(collection, now=None) -> RunUpdate:
    """Report on a profile collection using its own model and run metadata."""

    def meta(name):
        try:
            return collection.getMeta(name)
        except (AttributeError, KeyError, TypeError, ValueError):
            return getattr(collection, "_meta", {}).get(name)

    model = meta("model_key") or meta("model") or ""
    return check(model, meta("run"), now)


__all__ = ["RunUpdate", "check", "check_collection", "latest_cycle"]

"""Qt-free map time, frame coverage, and live/history model (T21).

The maps paint time state; this module decides *what the time state is* so the
paint path, the picker rails, the frame scrubber, the export path, and the
tests all agree without importing Qt:

* Time header (T21.1): one compact line naming the requested valid time, the
  actual displayed time, and -- for forecast tabs -- the model run and lead.
  Observation time for live layers, retrieval age kept secondary and labelled.
* Time match (T21.2): per-layer ``exact`` / ``nearest ±offset`` /
  ``outside coverage`` / ``unavailable``, with stale/offset thresholds taken
  from the products' own behaviour (GOES ±20 min, surface ±60 min with
  stale past 30 min, radar/HRRR staleness from each frame's own
  ``update_interval_s``, SPC from its validity window).
* Frame ledger (T21.3): one ``TimelineCoverage``-shaped vocabulary over the
  forecast hours a tab offers, reusing the T09 state words
  (``ready``/``loading``/``missing``/``failed``) so the scrubber strip and the
  sounding timeline never speak two clocks.
* Live/history (T21.4): a per-map mode naming whether the view follows the
  newest frame or a pinned historical one; a refresh keeps the labelled
  historical frame and never swaps in the current overlay unasked.

Session carries *choices* (run, forecast hour, mode, observed time) never
pixels: a restore re-derives coverage from the live catalogue.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

#: Time-header version for session payloads. A future vocabulary migrates
#: through :func:`restore_time_state`; unknown versions degrade to defaults.
TIME_STATE_VERSION = 1

#: Map live/history modes (T21.4). ``live`` follows the newest published
#: frame; ``history`` pins the view to the chosen run/hour even as newer runs
#: publish. The default is ``live``: a historical view is something the user
#: enters explicitly, never something a refresh implies.
MAP_MODES = ("live", "history")

#: Frame states for the forecast-hour scrubber (T21.3). The words match the
#: T09 :mod:`sharpmod.analysis.timeline_frames` vocabulary so one clock is spoken in
#: both places: ``ready`` is a selectable hour (the T09 ``loaded``), ``loading``
#: is in flight, ``missing`` is not published for this run/hour, ``failed``
#: was attempted and errored.
FRAME_STATES = ("ready", "loading", "missing", "failed")

#: GOES nearest-scan tolerance, from the product's own selection rule
#: (:data:`sharpmod.providers.satellite_context.DEFAULT_TIME_TOLERANCE`).
GOES_TIME_TOLERANCE = timedelta(minutes=20)

#: Surface observation match window and stale line, from the product's own
#: rules (:data:`sharpmod.providers.surface_observations.DEFAULT_TOLERANCE` and
#: :data:`sharpmod.providers.surface_observations.STALE_OFFSET`).
SURFACE_TIME_TOLERANCE = timedelta(minutes=60)
SURFACE_STALE_OFFSET = timedelta(minutes=30)

#: Storm-report window half-width, from the provider's own query rule
#: (:data:`sharpmod.providers.storm_reports.DEFAULT_WINDOW`, centred on the selection).
REPORTS_WINDOW = timedelta(hours=6)


def _utc(value) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _finite_number(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def format_offset(seconds: float | None) -> str:
    """Render a requested-vs-actual offset for display, or ``""``."""
    if seconds is None:
        return ""
    try:
        total = float(seconds)
    except (TypeError, ValueError, OverflowError):
        return ""
    if not math.isfinite(total):
        return ""
    minutes = total / 60.0
    if abs(minutes) < 1.0:
        return "exact" if abs(total) < 1.0 else f"{total:+.0f} s"
    if abs(minutes) < 90.0:
        return f"{minutes:+.0f} min"
    return f"{minutes / 60.0:+.1f} h"


def _header_time(moment: datetime) -> str:
    """Compact UTC time that retains minutes when they carry information."""
    return moment.strftime(
        "%d %b %H:%MZ" if moment.minute or moment.second else "%d %b %HZ"
    )


def _header_time_after(moment: datetime, anchor: datetime) -> str:
    """Omit a calendar date already established by an earlier time."""
    if moment.date() != anchor.date():
        return _header_time(moment)
    return moment.strftime(
        "%H:%MZ" if moment.minute or moment.second else "%HZ"
    )


def time_header(*, requested=None, displayed=None, run=None,
                forecast_hour=None, observation=None,
                retrieved_at=None, retrieved_age: str = "",
                mixed: bool = False) -> str:
    """Return the compact persistent time header line (T21.1).

    Names the requested valid time and -- only when it differs -- the actual
    displayed time, so a screenshot never leaves the reader guessing which
    time they are looking at. Forecast tabs add ``run · Fxxx``; live layers
    add the observation time; retrieval time/age is secondary and labelled.
    ``mixed`` is an explicit warning for screenshots containing products with
    different actual timestamps rather than leaving the reader to compare each
    minute by eye.
    """
    run_dt = _utc(run)
    forecast_lead = None
    if forecast_hour is not None:
        try:
            forecast_lead = int(forecast_hour)
        except (TypeError, ValueError, OverflowError):
            pass
    requested_dt = _utc(requested)
    displayed_dt = _utc(displayed)
    parts: list[str] = []
    target_dt = displayed_dt if displayed_dt is not None else requested_dt
    requested_mismatch = (
        requested_dt is not None
        and displayed_dt is not None
        and displayed_dt != requested_dt
    )
    forecast_valid = (
        run_dt + timedelta(hours=forecast_lead)
        if run_dt is not None and forecast_lead is not None else None
    )
    compact_forecast = (
        forecast_valid is not None
        and target_dt == forecast_valid
        and not requested_mismatch
    )
    if compact_forecast:
        if forecast_lead == 0:
            parts.append(
                f"Run/valid {_header_time(run_dt)} · F{forecast_lead:03d}"
            )
        else:
            parts.append(
                f"Run {_header_time(run_dt)} · F{forecast_lead:03d} · "
                f"valid {_header_time_after(forecast_valid, run_dt)}"
            )
        anchor_dt = forecast_valid
    else:
        if requested_mismatch:
            shown = _header_time_after(displayed_dt, requested_dt)
            parts.append(
                f"Valid {_header_time(requested_dt)} requested · "
                f"showing {shown}"
            )
        elif requested_dt is not None:
            parts.append(f"Valid {_header_time(requested_dt)}")
        elif displayed_dt is not None:
            parts.append(f"Showing {_header_time(displayed_dt)}")
        if run_dt is not None:
            anchor = displayed_dt or requested_dt
            shown = (
                _header_time_after(run_dt, anchor)
                if anchor is not None else _header_time(run_dt)
            )
            run_part = f"Run {shown}"
            if forecast_lead is not None:
                run_part += f" · F{forecast_lead:03d}"
            parts.append(run_part)
        anchor_dt = run_dt or target_dt
    # The first time-bearing clause establishes the calendar date.  Later
    # observation/retrieval instants on that same day only need the clock;
    # repeating ``20 Sep`` two more times made the small persistent chip read
    # like a date ledger instead of a quick map status.  A day boundary still
    # restores the full date, so no scientific distinction is lost.
    observation_dt = _utc(observation)
    if observation_dt is not None:
        shown = (
            _header_time_after(observation_dt, anchor_dt)
            if anchor_dt is not None else _header_time(observation_dt)
        )
        parts.append(f"Observed {shown}")
        # This clause is now the nearest date-bearing anchor.  If it crossed a
        # day boundary it printed that new date, so retrieval on the same new
        # day should not repeat it again.
        anchor_dt = observation_dt
    retrieved_dt = _utc(retrieved_at)
    if retrieved_dt is not None:
        shown = (
            _header_time_after(retrieved_dt, anchor_dt)
            if anchor_dt is not None else _header_time(retrieved_dt)
        )
        parts.append(f"retrieved {shown}")
    elif retrieved_age:
        parts.append(f"retrieved {retrieved_age}")
    if mixed:
        parts.append("Mixed layer times")
    return " · ".join(part for part in parts if part)


def layer_time_match(*, layer_key: str, requested=None,
                     valid_from=None, valid_to=None,
                     actual=None, available: bool = True,
                     loading: bool = False,
                     update_interval_s=None) -> tuple[str, str]:
    """Return ``(state, detail)`` for one layer against ``requested`` (T21.2).

    States are ``exact`` (the depicted frame is the requested hour),
    ``nearest`` (a published frame within the product's own tolerance, with
    the offset named), ``outside`` (the request falls past what the product
    publishes), and ``unavailable`` (nothing to depict: failed, unproduced,
    or switched off). ``loading`` outranks every state but an exact frame
    already on screen -- a spinner replaces "nothing here", never a picture.
    Thresholds come from the products' own behaviour, documented in the
    module docstring; a tolerance can be overridden per call but the product
    default is the honest one.
    """
    moment = _utc(requested)
    if not available:
        return "unavailable", "Unavailable"
    if moment is None:
        return "exact", "exact"
    start = _utc(valid_from)
    end = _utc(valid_to)
    stamp = _utc(actual)
    if start is not None and moment < start:
        return "outside", f"outside · before coverage from {start:%d %b %H}Z"
    if end is not None and moment >= end:
        return "outside", f"outside · past coverage to {end:%d %b %H}Z"
    if start is not None or end is not None:
        # A windowed layer (outlook, reports, surface set) covering the
        # request depicts the requested hour: the window *is* the product's
        # answer to "at what time", unlike a stamped frame that may lag it.
        return "exact", "exact"
    if stamp is None:
        if loading:
            return "loading", "Loading…"
        return "unavailable", "Unavailable · actual time unknown"
    offset_s = (stamp - moment).total_seconds()
    tolerance = _match_tolerance(layer_key, update_interval_s)
    if abs(offset_s) < 60.0:
        return "exact", "exact"
    if abs(offset_s) <= tolerance.total_seconds():
        return "nearest", f"nearest {format_offset(offset_s)}".strip()
    if loading:
        return "loading", "Loading…"
    return "outside", f"outside · {format_offset(offset_s)} past tolerance".strip()


def _match_tolerance(layer_key: str, update_interval_s=None) -> timedelta:
    key = str(layer_key or "")
    if key in ("goes_context", "goes_satellite"):
        return GOES_TIME_TOLERANCE
    if key in ("surface_observations", "surface"):
        return SURFACE_TIME_TOLERANCE
    if key == "storm_reports":
        return REPORTS_WINDOW
    if key in ("hrrr_field", "radar_mosaic", "radar_site"):
        cadence = _finite_number(update_interval_s, 0.0)
        if cadence > 0.0:
            return timedelta(seconds=cadence)
    return timedelta(hours=1)


@dataclass(frozen=True)
class TimeFrame:
    """One forecast hour and what the map can do with it (T21.3)."""

    forecast_hour: int
    state: str = "ready"
    valid_time: datetime | None = None
    reason: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "forecast_hour", int(self.forecast_hour))
        state = str(self.state or "ready").strip().lower()
        object.__setattr__(self, "state",
                           state if state in FRAME_STATES else "missing")
        if self.valid_time is not None and not isinstance(
                self.valid_time, datetime):
            raise ValueError("frame valid time must be a datetime or None")
        if self.reason is not None:
            object.__setattr__(self, "reason", str(self.reason))

    @property
    def selectable(self) -> bool:
        """Whether stepping to this hour depicts something."""
        return self.state == "ready"

    @property
    def label(self) -> str:
        """``F018`` -- the form every other forecast-hour control uses."""
        return f"F{self.forecast_hour:03d}"

    def describe(self) -> str:
        """One line naming the hour, its state, and any reason given."""
        text = f"{self.label}: {self.state}"
        if self.reason:
            text += f" — {self.reason}"
        return text


@dataclass(frozen=True)
class FrameCoverage:
    """What a tab offers and what each hour turned into (T21.3)."""

    frames: tuple[TimeFrame, ...] = ()
    run_time: datetime | None = None

    def __post_init__(self):
        frames = tuple(self.frames)
        hours = [frame.forecast_hour for frame in frames]
        if len(set(hours)) != len(hours):
            raise ValueError("each forecast hour needs exactly one frame")
        object.__setattr__(
            self, "frames",
            tuple(sorted(frames, key=lambda frame: frame.forecast_hour)))

    @classmethod
    def offered(cls, hours, *, run_time=None) -> "FrameCoverage":
        """Open a ledger for the catalogue's selectable hours."""
        return cls(
            tuple(TimeFrame(int(hour)) for hour in sorted({int(h) for h in hours})),
            run_time,
        )

    def __len__(self) -> int:
        return len(self.frames)

    def __iter__(self):
        return iter(self.frames)

    @property
    def offered_hours(self) -> tuple[int, ...]:
        """Every hour the catalogue offers, in order."""
        return tuple(frame.forecast_hour for frame in self.frames)

    def hours_in(self, *states: str) -> tuple[int, ...]:
        """Return the hours currently in any of ``states``."""
        wanted = {str(state).strip().lower() for state in states}
        return tuple(
            frame.forecast_hour for frame in self.frames if frame.state in wanted
        )

    @property
    def ready_hours(self) -> tuple[int, ...]:
        return self.hours_in("ready")

    def state_of(self, hour) -> str:
        """Return one hour's state, or ``"missing"`` when unoffered."""
        try:
            wanted = int(hour)
        except (TypeError, ValueError, OverflowError):
            return "missing"
        for frame in self.frames:
            if frame.forecast_hour == wanted:
                return frame.state
        return "missing"

    def with_state(self, hour, state, *, valid_time=None,
                   reason=None) -> "FrameCoverage":
        """Return a copy with one hour moved to ``state``."""
        wanted = int(hour)
        frames = []
        for frame in self.frames:
            if frame.forecast_hour != wanted:
                frames.append(frame)
                continue
            frames.append(TimeFrame(wanted, state, valid_time,
                                    reason if reason is not None
                                    else frame.reason))
        return FrameCoverage(tuple(frames), self.run_time)

    def summary(self) -> str:
        """State the coverage in one sentence."""
        if not self.frames:
            return "No forecast hours are offered."
        parts = [f"{len(self.ready_hours)} of {len(self.frames)} hour(s) ready"]
        for state in ("loading", "missing", "failed"):
            hours = self.hours_in(state)
            if hours:
                parts.append(f"{len(hours)} {state}")
        return "; ".join(parts) + "."


def normalise_mode(value) -> str:
    """Return ``"live"`` or ``"history"``, degrading unknown to live (T21.4)."""
    text = str(value or "").strip().lower()
    return text if text in MAP_MODES else "live"


def mode_label(mode: str) -> str:
    """Return the rail-facing live/history wording (T21.4)."""
    if normalise_mode(mode) == "history":
        return "Historical view — pinned, not following new runs"
    return "Live — following the newest published run"


def resolve_displayed(*, requested=None, actual=None,
                      loading: bool = False) -> datetime | None:
    """Return the time the map should claim to depict (T21.4).

    The requested time while loading or failing -- never a newer frame that
    happened to arrive first. A late response does not overwrite a newer
    selection: the caller passes the *current* request, and anything attached
    for an older one keeps its own label until the new frame lands.
    """
    moment = _utc(requested)
    if moment is not None:
        return moment
    return _utc(actual)


def time_state(*, run=None, forecast_hour=None,
               requested=None, mode: str = "live") -> dict:
    """Return the portable time-choice snapshot (T21/session).

    Choices only: the selected run, forecast hour, requested valid time, and
    live/history mode. Frames, ages, and error text are deliberately absent --
    a restore re-requests, it never replays pixels.
    """
    run_dt = _utc(run)
    requested_dt = _utc(requested)
    try:
        hour = None if forecast_hour is None else int(forecast_hour)
    except (TypeError, ValueError, OverflowError):
        hour = None
    return {
        "version": TIME_STATE_VERSION,
        "run": run_dt.isoformat() if run_dt is not None else "",
        "forecast_hour": hour,
        "requested": requested_dt.isoformat() if requested_dt is not None else "",
        "mode": normalise_mode(mode),
    }


def restore_time_state(payload) -> dict:
    """Return a validated time-choice snapshot, tolerating old payloads."""
    if not isinstance(payload, dict):
        return time_state()
    try:
        version = int(payload.get("version", TIME_STATE_VERSION))
    except (TypeError, ValueError, OverflowError):
        version = TIME_STATE_VERSION
    if version != TIME_STATE_VERSION:
        return time_state()
    run = payload.get("run") or ""
    requested = payload.get("requested") or ""
    try:
        run_dt = datetime.fromisoformat(str(run)) if run else None
    except ValueError:
        run_dt = None
    try:
        requested_dt = datetime.fromisoformat(str(requested)) if requested else None
    except ValueError:
        requested_dt = None
    return time_state(run=run_dt, forecast_hour=payload.get("forecast_hour"),
                      requested=requested_dt, mode=payload.get("mode", "live"))


__all__ = [
    "FRAME_STATES",
    "GOES_TIME_TOLERANCE",
    "MAP_MODES",
    "REPORTS_WINDOW",
    "SURFACE_STALE_OFFSET",
    "SURFACE_TIME_TOLERANCE",
    "TIME_STATE_VERSION",
    "FrameCoverage",
    "TimeFrame",
    "format_offset",
    "layer_time_match",
    "mode_label",
    "normalise_mode",
    "resolve_displayed",
    "restore_time_state",
    "time_header",
    "time_state",
]

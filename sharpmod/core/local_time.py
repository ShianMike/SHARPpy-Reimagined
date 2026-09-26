"""Optional local-time display, with UTC always kept primary.

Every run, cycle, and valid time in this application is UTC, and everything shown
carries a literal ``Z``. That is correct and must not change: a forecast discussed
in local time across a time-zone boundary, or across a daylight-saving transition,
is a forecast two people can read differently.

What was missing is the other half. A reader planning against a local afternoon
was converting in their head on every valid time, and mental conversion is exactly
where the DST hour goes missing. So local time is offered *alongside* UTC, never
instead of it, and it is never shown bare: it carries the zone abbreviation, the
UTC offset, and whether daylight saving is in force at that instant, because
"3 PM" without those three is not a time anyone can act on.

Built on :mod:`dateutil.tz`, which is already a declared dependency, rather than
:mod:`zoneinfo`. ``zoneinfo`` is in the standard library, but it reads the system
tz database, and Windows does not have one; without the ``tzdata`` package -- which
this project does not declare -- ``ZoneInfo("America/Chicago")`` raises
``ZoneInfoNotFoundError``. ``dateutil`` carries its own Windows fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

#: The UTC form. One definition, so the primary reading cannot drift between the
#: playback bar, the trend axis, and a report.
UTC_FORMAT = "%Y-%m-%d %H:%MZ"


class _SystemZone:
    """Sentinel for "no zone given", distinct from "a zone that did not resolve".

    Defaulting a missing zone to the system zone is convenient, but it made those
    two cases identical: ``zone_for("Mars/Olympus")`` returns ``None``, and a
    caller passing that straight through got the *machine's* zone instead of no
    local time. Showing an instant in the wrong zone is the one outcome this
    module exists to prevent.
    """

    def __repr__(self):  # pragma: no cover - debugging aid
        return "<system zone>"


SYSTEM = _SystemZone()


def _tz_module():
    try:
        from dateutil import tz
    except Exception:  # noqa: BLE001 - local time degrades, UTC never does
        return None
    return tz


def local_zone():
    """Return the system's zone, or ``None`` if it cannot be determined."""
    tz = _tz_module()
    if tz is None:
        return None
    try:
        return tz.tzlocal()
    except Exception:  # noqa: BLE001 - reported by returning None
        return None


def zone_for(name):
    """Return a named zone, or ``None`` when the name is unknown.

    ``None`` rather than a guess: showing a time in the wrong zone is worse than
    showing no local time at all.
    """
    if not name:
        return local_zone()
    tz = _tz_module()
    if tz is None:
        return None
    try:
        return tz.gettz(str(name))
    except Exception:  # noqa: BLE001 - unknown zone reported as None
        return None


def format_utc(value):
    """Return the primary UTC reading, or an empty string."""
    if not isinstance(value, datetime):
        return ""
    moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime(UTC_FORMAT)


@dataclass(frozen=True)
class LocalReading:
    """One instant expressed locally, with the context that makes it usable."""

    text: str
    abbreviation: str
    offset_minutes: int
    daylight: bool
    zone_name: str = ""

    @property
    def offset_text(self) -> str:
        """``UTC-05:00``, with a real minus sign rather than a hyphen."""
        total = int(self.offset_minutes)
        sign = "\u2212" if total < 0 else "+"
        total = abs(total)
        return f"UTC{sign}{total // 60:02d}:{total % 60:02d}"

    def describe(self) -> str:
        """The local reading with its zone, offset, and daylight-saving state."""
        parts = [self.text]
        if self.abbreviation:
            parts.append(self.abbreviation)
        detail = self.offset_text
        # Stated either way. "CST" and "CDT" differ by one letter, and a reader
        # skimming will not catch it; "standard time" and "daylight saving time"
        # cannot be misread.
        detail += ", daylight saving time" if self.daylight else ", standard time"
        return f"{' '.join(parts)} ({detail})"


def local_reading(value, zone=SYSTEM):
    """Return ``value`` in ``zone`` with its context, or ``None``.

    ``None`` whenever the conversion cannot be trusted -- no dateutil, no
    resolvable zone, or a non-datetime -- so a caller shows UTC alone instead of a
    wrong local time. Omitting ``zone`` uses the system zone; passing ``None``
    explicitly means "this zone did not resolve" and yields ``None``.
    """
    if not isinstance(value, datetime):
        return None
    resolved = local_zone() if isinstance(zone, _SystemZone) else zone
    if resolved is None:
        return None
    moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    try:
        converted = moment.astimezone(resolved)
    except Exception:  # noqa: BLE001 - a failed conversion shows no local time
        return None
    offset = converted.utcoffset()
    if offset is None:
        return None
    try:
        daylight = bool(converted.dst())
    except Exception:  # noqa: BLE001 - absence of DST data is not daylight
        daylight = False
    abbreviation = converted.strftime("%Z") or ""
    # Some platforms return the full zone name here; a long string in a compact
    # readout is worse than none, and the offset already carries the meaning.
    if len(abbreviation) > 6:
        abbreviation = ""
    return LocalReading(
        converted.strftime("%Y-%m-%d %H:%M"),
        abbreviation,
        int(offset.total_seconds() // 60),
        daylight,
        str(getattr(resolved, "tzname", lambda _dt: "")(converted) or ""),
    )


def dual_reading(value, zone=SYSTEM, *, show_local=True):
    """Return the UTC reading, optionally followed by the local one.

    UTC comes first and is never omitted, whatever the local conversion does.
    """
    utc = format_utc(value)
    if not utc or not show_local:
        return utc
    reading = local_reading(value, zone)
    return utc if reading is None else f"{utc}  \u00b7  {reading.describe()}"


def crosses_dst(start, end, zone=SYSTEM):
    """Whether a span changes daylight-saving state in ``zone``.

    A timeline that crosses a transition contains a local hour that repeats or
    does not exist, and a reader converting in their head will not notice.
    """
    first = local_reading(start, zone)
    last = local_reading(end, zone)
    if first is None or last is None:
        return False
    return first.daylight != last.daylight


__all__ = [
    "LocalReading",
    "SYSTEM",
    "UTC_FORMAT",
    "crosses_dst",
    "dual_reading",
    "format_utc",
    "local_reading",
    "local_zone",
    "zone_for",
]

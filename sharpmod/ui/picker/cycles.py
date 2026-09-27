"""UTC run and forecast-hour labels shared by picker source tabs."""

from __future__ import annotations

from datetime import datetime, timezone
from qtpy.QtCore import Qt

def _select_cycle(combo, hour) -> None:
    """Select cycle ``hour`` in ``combo``, leaving it alone if not offered."""
    index = combo.findData(int(hour))
    if index >= 0:
        combo.setCurrentIndex(index)
#: Marks the newest cycle on the selected date whose run hour has passed.
#:
#: One positive marker rather than a warning on every future cycle. Marking the
#: future was the first attempt and it was measurably worse: an hourly model
#: mid-morning put a note on most of the list, so the annotation became the
#: background rather than the signal, and it widened the closed field to fit a
#: label the selected value usually did not carry.
#:
#: This says the one thing worth saying -- which run is the freshest that can
#: exist -- on at most one row, and it is the row the user most often wants. On a
#: past date it lands on that day's last cycle; on a future date, on nothing.
#:
#: It marks rather than disables anything. Publication lags the hour, so "the hour
#: has passed" is not proof a run is there and the converse is not proof it is
#: absent; this panel deliberately keeps Fetch reachable when availability is
#: uncertain -- see the availability chip's own tooltip. Greying rows out would
#: overrule a judgement the user is entitled to make, and the chip already
#: reports what the catalogue actually holds.
_CYCLE_LATEST = " \u00b7 latest"


def _run_datetime(run_date, hour: int) -> datetime | None:
    """Compose a UTC run time from a ``QDate`` and a cycle hour."""
    if run_date is None:
        return None
    try:
        return datetime(
            run_date.year(),
            run_date.month(),
            run_date.day(),
            int(hour),
            tzinfo=timezone.utc,
        )
    except (AttributeError, ValueError):
        return None


def _latest_elapsed_cycle(combo, run_date, now: datetime) -> int | None:
    """Return the newest cycle hour in ``combo`` whose run time has passed."""
    if run_date is None:
        return None
    elapsed = []
    for index in range(combo.count()):
        hour = combo.itemData(index)
        if hour is None:
            continue
        run = _run_datetime(run_date, int(hour))
        if run is not None and run <= now:
            elapsed.append(int(hour))
    return max(elapsed) if elapsed else None


def _annotate_cycle_combo(combo, run_date, now: datetime | None = None) -> None:
    """Refresh every cycle entry against ``run_date``, in place.

    Rewrites item *text* rather than rebuilding the list, because the date and the
    cycle are separate controls: a rebuild would fire ``currentIndexChanged`` and
    so re-enter the forecast-hour refresh on every date edit, and it would drop
    the selection whenever the new date made the chosen hour momentarily absent.
    """
    if combo is None:
        return
    resolved = now or datetime.now(timezone.utc)
    latest = _latest_elapsed_cycle(combo, run_date, resolved)
    for index in range(combo.count()):
        hour = combo.itemData(index)
        if hour is None:
            continue
        hour = int(hour)
        suffix = _CYCLE_LATEST if latest is not None and hour == latest else ""
        combo.setItemText(index, f"{hour:02d}Z{suffix}")
        run = _run_datetime(run_date, hour)
        combo.setItemData(
            index,
            f"Run {run:%a %d %b %Y %H}Z" if run is not None else f"{hour:02d}Z run",
            Qt.ToolTipRole,
        )


def _fxx_item_text(hour: int, valid: datetime | None) -> str:
    """Return one forecast-hour entry's label.

    The valid time travels *in the entry*, which is the point. A bare ``F012``
    names the offset and not the thing being chosen by -- the hour the sounding
    depicts -- so picking one meant adding the offset to the run in your head,
    for every candidate, while the only place the sum appeared was a label two
    rows below that updated after the choice was committed.
    """
    base = f"F{int(hour):03d}"
    if valid is None:
        return base
    return f"{base}  \u00b7  {valid:%b %d %H}Z"


def _fill_cycle_combo(combo, hours, selected=None, *, run_date=None) -> None:
    """Populate ``combo`` with UTC cycle hours, newest first.

    Newest first because the freshest run is the one usually wanted. Ascending
    order buried it: an hourly model publishes 24 cycles, so the newest sat off
    the bottom of a scrolling list while 00Z -- by then most of a day stale --
    sat under the cursor as the first entry.

    Hours travel as item data, so callers select by hour rather than by position
    and the display order is free to change without breaking them.

    ``run_date`` is the date the hours will be composed with, used only to mark
    cycles that have not run yet. Omit it and the entries are bare hours.
    """
    combo.clear()
    for hour in sorted({int(value) for value in hours}, reverse=True):
        combo.addItem(f"{hour:02d}Z", hour)
    _annotate_cycle_combo(combo, run_date)
    if selected is not None:
        _select_cycle(combo, selected)


def _newest_cycle_not_after(hours, hour: int) -> int:
    """Return the newest cycle at or before ``hour``.

    Falls back to the earliest cycle when none qualifies, which is the case for
    a time earlier than the day's first run.
    """
    ordered = sorted({int(value) for value in hours})
    if not ordered:
        return 0
    eligible = [value for value in ordered if value <= int(hour)]
    return eligible[-1] if eligible else ordered[0]

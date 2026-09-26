"""Qt-free legend placement, compacting, scale-lock, and hover-link model (T20).

The map paints legends; this module decides *what the legend says* so the
paint path, the picker card, the export path, and the tests all agree without
importing Qt:

* Placement (T20.1): choose a corner that avoids the sounding point, the
  inspection card, and the scale bar; collapse to a one-line summary when no
  corner fits or the user asks.
* Compact identity (T20.2): one ``name · units`` line plus the scale, wrapped
  categorical rows, and an explicit continuous-vs-categorical encoding tag.
* Fixed vs auto scales (T20.3): a versioned lock naming the product, the range,
  and the comparator panels/times; any change while locked is labelled, never
  silent. The lock is advisory on the render -- the PNG is unchanged -- so a
  comparison across panels/times reads against one stated scale.
* Hover link (T20.4): map a hovered numeric value to a fractional position on
  the continuous colour bar, with native grid resolution context beside the
  rendered smoothness.

Session carries *choices* (placement, collapsed, locks), never pixels: a
restore re-derives the layout from the live widget size.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

#: Where a legend may sit, in preference order. Bottom-right leads. The
#: coordinate readout permanently owns top-left, so exposing that corner made
#: it possible to persist an overlapping layout and made the legend jump away
#: as soon as the pointer entered it. The pinned inspection card owns top-right;
#: the scale bar yields to a bottom-right legend.
LEGEND_CORNERS = ("bottom-right", "bottom-left", "top-right")

#: Legend version for session payloads. A future vocabulary migrates through
#: :func:`restore_legend_state`; unknown versions degrade to defaults.
LEGEND_STATE_VERSION = 1

#: Native grid spacing per family, for the resolution context (T20.4). HRRR is
#: ~3 km; MRMS mosaics are ~1 km resampled to the ~1.9 km display grid; a
#: single site resolves roughly half a kilometre; GOES channels state their
#: own native resolution in the context row.
NATIVE_RESOLUTION_KM = {
    "hrrr_field": 3.0,
    "radar_mosaic": 1.9,
    "radar_site": 0.5,
    "goes_context": 2.0,
    "spc_outlook": 0.0,
    "storm_reports": 0.0,
    "surface_observations": 0.0,
}

#: Maximum categorical rows before the compact legend wraps the remainder into
#: a "+N more" line (T20.2). Eight swatches fit a 240 px row at caption size;
#: past that the row is a list of the data rather than a key to it.
COMPACT_CATEGORY_LIMIT = 8


@dataclass
class LegendBox:
    """A placed legend rectangle in widget pixels (T20.1)."""

    corner: str = "bottom-right"
    x: float = 0.0
    y: float = 0.0
    width: float = 0.0
    height: float = 0.0
    collapsed: bool = False

    def rect(self) -> tuple[float, float, float, float]:
        """Return ``(x, y, width, height)``."""
        return (float(self.x), float(self.y), float(self.width),
                float(self.height))

    def covers(self, px: float, py: float, *, pad: float = 6.0) -> bool:
        """Report whether a widget point falls inside (with margin)."""
        try:
            x, y = float(px), float(py)
        except (TypeError, ValueError, OverflowError):
            return False
        return (self.x - pad <= x <= self.x + self.width + pad
                and self.y - pad <= y <= self.y + self.height + pad)


def _finite_number(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def choose_legend_corner(*, width: float, height: float, box_width: float,
                         box_height: float, avoid: tuple = (),
                         preferred: str = "") -> LegendBox:
    """Choose a legend corner avoiding widget points (T20.1).

    ``avoid`` carries ``(x, y)`` widget points that matter -- the sounding
    point, permanent readout chrome, and inspection-card corners. The
    first automatic corner whose rectangle covers none of them wins. A named
    ``preferred`` corner is an explicit pin and therefore stays put even when
    content moves underneath it; this is what makes the corner control and a
    restored user preference deterministic. When every automatic corner
    collides, the least-colliding corner wins and the caller collapses (see
    :func:`should_collapse`).
    """
    width = max(1.0, _finite_number(width, 1.0))
    height = max(1.0, _finite_number(height, 1.0))
    box_width = max(0.0, _finite_number(box_width, 0.0))
    box_height = max(0.0, _finite_number(box_height, 0.0))
    margin = 10.0
    points = []
    for point in avoid or ():
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError, OverflowError, IndexError):
            continue
        if math.isfinite(x) and math.isfinite(y):
            points.append((x, y))
    order = [preferred] if preferred in LEGEND_CORNERS else []
    order.extend(corner for corner in LEGEND_CORNERS if corner not in order)
    boxes = {}
    for corner in order:
        if corner == "top-right":
            x, y = width - margin - box_width, 8.0
        elif corner == "bottom-right":
            x, y = width - margin - box_width, height - 8.0 - box_height
        else:
            x, y = margin, height - 8.0 - box_height
        boxes[corner] = LegendBox(corner=corner, x=x, y=y, width=box_width,
                                  height=box_height)
    if preferred in LEGEND_CORNERS:
        return boxes[preferred]
    if not points or box_width <= 0.0 or box_height <= 0.0:
        return boxes[order[0]]
    scored = []
    for corner in order:
        box = boxes[corner]
        hits = sum(1 for (x, y) in points if box.covers(x, y))
        scored.append((hits, order.index(corner), box))
    scored.sort(key=lambda entry: (entry[0], entry[1]))
    return scored[0][2]


def should_collapse(*, width: float, height: float, box_width: float,
                    box_height: float, avoid: tuple = (),
                    user_collapsed: bool = False) -> bool:
    """Report whether the legend must collapse to one line (T20.1).

    True when the user asked, when the full box would cover more than a third
    of a small widget, or when every corner collides with something that
    matters. Collapsing keeps identity (name · units · scale) and drops the
    key, never the other way round.
    """
    if user_collapsed:
        return True
    width = max(1.0, _finite_number(width, 1.0))
    height = max(1.0, _finite_number(height, 1.0))
    box_width = max(0.0, _finite_number(box_width, 0.0))
    box_height = max(0.0, _finite_number(box_height, 0.0))
    if box_width <= 0.0 or box_height <= 0.0:
        return False
    # A narrow widget has no spare corner: the bar alone is ~120 px wide, so
    # below ~480 px the full key (bar + swatches + prose) crowds the point and
    # the controls whatever the area arithmetic says. Collapse keeps identity
    # (name · units · scale); the key returns on expansion or a wider window.
    if width < 480.0:
        return True
    # A tall key is just as obstructive as a wide one. This matters at 150–200%
    # interface text, where a dozen honest time/source rows can consume most of
    # a map even though their rectangle is still below the area threshold.
    if box_height > height * 0.55:
        return True
    if box_width * box_height > width * height / 3.0:
        return True
    for corner in LEGEND_CORNERS:
        box = choose_legend_corner(width=width, height=height,
                                   box_width=box_width, box_height=box_height,
                                   avoid=avoid, preferred=corner)
        if box.corner != corner:
            continue
        hits = False
        for point in avoid or ():
            try:
                if box.covers(float(point[0]), float(point[1])):
                    hits = True
                    break
            except (TypeError, ValueError, OverflowError, IndexError):
                continue
        if not hits:
            return False
    return True


def compact_identity(*, product: str, units: str = "",
                     scale_text: str = "") -> str:
    """Return the one-line compact legend identity (T20.2).

    ``"<product> · <units> · <scale>"``, dropping blanks but never the product
    name: a collapsed legend that says only "dBZ" has kept the units and lost
    the meaning.
    """
    parts = [str(product or "").strip()]
    for extra in (str(units or "").strip(), str(scale_text or "").strip()):
        if extra:
            parts.append(extra)
    return " · ".join(part for part in parts if part)


def wrap_category_rows(rows: list, *, limit: int = COMPACT_CATEGORY_LIMIT
                       ) -> tuple[list, int]:
    """Split categorical rows into shown + hidden counts (T20.2).

    Returns ``(shown, hidden_count)``. The caller renders shown plus a
    ``"+N more"`` line naming the count rather than eliding silently.
    """
    rows = list(rows or ())
    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError, OverflowError):
        limit = COMPACT_CATEGORY_LIMIT
    return rows[:limit], max(0, len(rows) - limit)


def encoding_tag(*, continuous: bool, stepped: bool = False) -> str:
    """Name the colour encoding so the two cannot be confused (T20.2)."""
    if continuous and stepped:
        return "banded scale"
    if continuous:
        return "continuous ramp"
    return "categories"


def scale_lock_label(*, product: str, minimum: float, maximum: float,
                     units: str = "", locked: bool = True) -> str:
    """Return the fixed/auto scale line for a colour bar (T20.3)."""
    low = _finite_number(minimum, 0.0)
    high = _finite_number(maximum, 0.0)
    if high < low:
        low, high = high, low
    span = f"{low:g}–{high:g}"
    if units:
        span += f" {units}"
    mode = "Fixed scale" if locked else "Auto scale"
    return f"{mode}: {product} {span}"


def scale_change_note(*, product: str, old: tuple, new: tuple,
                      units: str = "") -> str:
    """Return the explicit range-change line while locked (T20.3).

    Empty when the range did not move: a lock that narrates every repaint
    would train the reader to ignore the one line that matters.
    """
    try:
        old_low, old_high = float(old[0]), float(old[1])
        new_low, new_high = float(new[0]), float(new[1])
    except (TypeError, ValueError, OverflowError, IndexError):
        return ""
    if not all(math.isfinite(value)
               for value in (old_low, old_high, new_low, new_high)):
        return ""
    if old_low == new_low and old_high == new_high:
        return ""
    suffix = f" {units}" if units else ""
    return (f"⚠ {product} range changed {old_low:g}–{old_high:g}{suffix} → "
            f"{new_low:g}–{new_high:g}{suffix} (locked scale kept)")


def colorbar_fraction(value: float, minimum: float, maximum: float) -> float | None:
    """Return the 0–1 position of ``value`` on a continuous bar (T20.4).

    ``None`` when the value or range is not finite, or the span is zero: a
    marker cannot honestly point at a bar with no extent. Clamped to the bar
    rather than extrapolated past it -- the marker says where the value falls
    *on this scale*, and out-of-range values are labelled, not projected.
    """
    try:
        number = float(value)
        low = float(minimum)
        high = float(maximum)
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(item) for item in (number, low, high)):
        return None
    span = high - low
    if span <= 0.0:
        return None
    return min(1.0, max(0.0, (number - low) / span))


def resolution_context(*, layer_key: str, render_smooth: bool = True) -> str:
    """Return the native-resolution caveat for a layer (T20.4).

    Rendering interpolates, so a zoomed mosaic looks smoother than its grid;
    the line states the grid beside the smoothness rather than letting the
    pixels imply precision they do not have. Point/area families carry no grid
    and say so plainly instead of borrowing one.
    """
    spacing = NATIVE_RESOLUTION_KM.get(str(layer_key), 0.0)
    if not spacing or spacing <= 0.0:
        return "No gridded resolution — areas and points, not cells."
    smooth = "rendering smooths between cells" if render_smooth else \
        "rendering shows native cells"
    return f"≈{spacing:g} km native grid; {smooth}."


def legend_state(*, corner: str = "", collapsed: bool = False,
                 locks: dict | None = None) -> dict:
    """Return the portable legend-choice snapshot (T20/session).

    Choices only: preferred corner, collapsed flag, and fixed-scale locks keyed
    by product. Geometry re-derives from the live widget size at paint time.
    """
    clean_locks = {}
    for key, lock in (locks or {}).items():
        if not isinstance(lock, dict):
            continue
        try:
            low = float(lock.get("minimum"))
            high = float(lock.get("maximum"))
        except (TypeError, ValueError, OverflowError):
            continue
        if not (math.isfinite(low) and math.isfinite(high)) or high <= low:
            continue
        clean_locks[str(key)] = {
            "minimum": float(low), "maximum": float(high),
            "units": str(lock.get("units") or ""),
            "label": str(lock.get("label") or str(key)),
        }
    wanted = str(corner or "").strip().lower().replace("_", "-")
    return {
        "version": LEGEND_STATE_VERSION,
        "corner": wanted if wanted in LEGEND_CORNERS else "",
        "collapsed": bool(collapsed),
        "locks": clean_locks,
    }


def restore_legend_state(payload) -> dict:
    """Return a validated legend-choice snapshot, tolerating old payloads."""
    if not isinstance(payload, dict):
        return legend_state()
    try:
        version = int(payload.get("version", LEGEND_STATE_VERSION))
    except (TypeError, ValueError, OverflowError):
        version = LEGEND_STATE_VERSION
    if version != LEGEND_STATE_VERSION:
        return legend_state()
    locks = payload.get("locks")
    return legend_state(corner=payload.get("corner", ""),
                        collapsed=payload.get("collapsed", False),
                        locks=locks if isinstance(locks, dict) else {})


__all__ = [
    "COMPACT_CATEGORY_LIMIT",
    "LEGEND_CORNERS",
    "LEGEND_STATE_VERSION",
    "NATIVE_RESOLUTION_KM",
    "LegendBox",
    "choose_legend_corner",
    "colorbar_fraction",
    "compact_identity",
    "encoding_tag",
    "legend_state",
    "resolution_context",
    "restore_legend_state",
    "scale_change_note",
    "scale_lock_label",
    "should_collapse",
    "wrap_category_rows",
]

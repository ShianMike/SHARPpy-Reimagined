"""Map geography presentation: boundaries, labels, scale, and theme tuning.

T16 gives the picker maps a legible geographic foundation that stays above
filled weather layers: an explicit boundary hierarchy, adaptive place labels
with a density control, a projection-aware distance scale with orientation
context, and persisted presentation preferences. Scientific values, marker
semantics, and overlay behaviour are untouched; this module only decides how
geography is drawn.

All helpers are UI-free so focused tests can verify hierarchy ordering,
declutter rules, scale arithmetic, and contrast without a QApplication.
"""

from __future__ import annotations

import math


#: Paint order for boundary families, dimmest first. The basemap raster draws
#: them in this order so the most prominent outline (coastline) finishes on
#: top; nothing here changes which families exist, only the contract that a
#: new family must declare where it sits rather than landing wherever a dict
#: happened to iterate.
BOUNDARY_ZORDER = ("states", "countries", "lakes", "coastline")

#: Relative stroke widths at a reference 1x density, in the same units the map
#: passes to ``QPen``. Coastline stays the heaviest outline, then national
#: borders, then lakes and internal borders together.
BOUNDARY_WIDTHS = {
    "states": 1.0,
    "countries": 1.2,
    "lakes": 1.1,
    "coastline": 1.4,
}

#: Label density choices offered by the presentation control. ``off`` hides
#: place labels while keeping the selected/hovered/pinned location readable;
#: ``sparse`` names only major cities; ``standard`` adds regional towns;
#: ``dense`` shows the closer-zoom town set as well.
LABEL_DENSITY_LEVELS = ("off", "sparse", "standard", "dense")
LABEL_DENSITY_LABELS = {
    "off": "Off",
    "sparse": "Major cities",
    "standard": "Cities and towns",
    "dense": "Dense",
}

#: Longitude span (degrees) at or below which the ``standard`` tier appears,
#: and at or below which the ``dense`` tier appears. Continental views keep
#: only major cities; the town sets fade in as the reader zooms toward them.
LABEL_SPAN_STANDARD = 30.0
LABEL_SPAN_DENSE = 12.0

#: Minimum on-screen separation between two place labels, in pixels. A
#: candidate that would land closer to an already-placed label (or to a
#: protected marker readout) is withheld rather than overprinted. The widget
#: scales this with the interface text size: enlarged text needs more room or
#: the labels pile up exactly as the first 200% capture showed.
LABEL_MIN_SEPARATION_PX = 48.0

#: Separation applied at 200% interface text, in pixels. Passed explicitly by
#: the widget so headless unit tests keep the deterministic 48 px default.
LABEL_MIN_SEPARATION_PX_LARGE_TEXT = 96.0

#: Maximum place labels drawn in one frame. Bounds the paint cost on dense
#: views and forces the importance ordering below to mean something. Sized so
#: a continent-wide view at a narrow 700 px map pane still names a readable
#: spread of metros: 40 candidates at 96 px separation overflow a small pane
#: and collapse onto the coasts.
LABEL_MAX_COUNT = 40
#: Cap applied at 200% interface text, where each label is roughly twice as
#: wide. Passed explicitly by the widget so headless tests keep the default.
LABEL_MAX_COUNT_LARGE_TEXT = 16

#: Major metros named at continental extents, as ``(label, lat, lon)`` in
#: rough population order.
#: Labels match the bundled Census ``"City, State"`` form so the continental
#: view and a zoomed view name a city the same way. A curated list rather than
#: an area cutoff: in the rural west a small town can cover more land than a
#: major eastern city, so land area cannot tell them apart and the full index
#: would put dozens of townships on a continent-wide map. Kept in this order
#: deliberately: ties in the declutter keep input order, so a western metro
#: that would otherwise lose to an alphabetically earlier eastern one survives.
MAJOR_CITIES = (
    ("New York, New York", 40.71, -74.00),
    ("Los Angeles, California", 34.05, -118.24),
    ("Chicago, Illinois", 41.88, -87.63),
    ("Houston, Texas", 29.76, -95.37),
    ("Phoenix, Arizona", 33.45, -112.07),
    ("Philadelphia, Pennsylvania", 39.95, -75.17),
    ("San Antonio, Texas", 29.42, -98.49),
    ("San Diego, California", 32.72, -117.16),
    ("Dallas, Texas", 32.78, -96.80),
    ("Denver, Colorado", 39.74, -104.99),
    ("Seattle, Washington", 47.61, -122.33),
    ("Minneapolis, Minnesota", 44.98, -93.27),
    ("New Orleans, Louisiana", 29.95, -90.07),
    ("Atlanta, Georgia", 33.75, -84.39),
    ("Miami, Florida", 25.76, -80.19),
    ("Nashville, Tennessee", 36.16, -86.78),
    ("St. Louis, Missouri", 38.63, -90.20),
    ("Kansas City, Missouri", 39.10, -94.58),
    ("Indianapolis, Indiana", 39.77, -86.16),
    ("Columbus, Ohio", 39.96, -83.00),
    ("Pittsburgh, Pennsylvania", 40.44, -79.99),
    ("Baltimore, Maryland", 39.29, -76.61),
    ("Charlotte, North Carolina", 35.23, -80.84),
    ("Memphis, Tennessee", 35.15, -90.05),
    ("Milwaukee, Wisconsin", 43.04, -87.91),
    ("Detroit, Michigan", 42.33, -83.05),
    ("Cleveland, Ohio", 41.50, -81.69),
    ("Oklahoma City, Oklahoma", 35.47, -97.52),
    ("Albuquerque, New Mexico", 35.08, -106.65),
    ("Salt Lake City, Utah", 40.76, -111.89),
    ("Las Vegas, Nevada", 36.17, -115.14),
    ("Portland, Oregon", 45.52, -122.68),
    ("San Francisco, California", 37.77, -122.42),
    ("Sacramento, California", 38.58, -121.49),
    ("Tucson, Arizona", 32.22, -110.97),
    ("El Paso, Texas", 31.76, -106.49),
    ("Omaha, Nebraska", 41.26, -95.93),
    ("Little Rock, Arkansas", 34.75, -92.29),
    ("Birmingham, Alabama", 33.52, -86.80),
    ("Jacksonville, Florida", 30.33, -81.66),
    ("Tampa, Florida", 27.95, -82.46),
    ("Orlando, Florida", 28.54, -81.38),
    ("Richmond, Virginia", 37.54, -77.44),
    ("Washington, District of Columbia", 38.90, -77.04),
    ("Boston, Massachusetts", 42.36, -71.06),
    ("Buffalo, New York", 42.89, -78.88),
    ("Boise, Idaho", 43.62, -116.20),
    ("Billings, Montana", 45.78, -108.50),
    ("Fargo, North Dakota", 46.88, -96.79),
    ("Wichita, Kansas", 37.69, -97.34),
    ("Cheyenne, Wyoming", 41.14, -104.82),
    ("Spokane, Washington", 47.66, -117.43),
)

#: Nice scale-bar target widths, in kilometres, before snapping. The bar picks
#: the entry whose on-screen width lands closest to the target pixel width.
SCALE_BAR_STEPS_KM = (
    10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000,
)
#: Desired on-screen bar length, in pixels. A fixed pixel target keeps the bar
#: readable at every zoom instead of shrinking with the view or spanning it.
SCALE_BAR_TARGET_PX = 120.0

EARTH_RADIUS_KM = 6371.0088
KM_PER_MILE = 1.609344


def boundary_draw_order(names) -> tuple[str, ...]:
    """Return known boundary families dimmest-first, unknowns last in place."""
    known = [name for name in BOUNDARY_ZORDER if name in set(names)]
    extra = [name for name in names if name not in BOUNDARY_ZORDER]
    return tuple(known + extra)


def boundary_width(family: str, *, density_scale: float = 1.0) -> float:
    """Return the stroke width for one boundary family."""
    base = BOUNDARY_WIDTHS.get(str(family), 1.0)
    try:
        scale = float(density_scale)
    except (TypeError, ValueError, OverflowError):
        scale = 1.0
    if not math.isfinite(scale) or scale <= 0.0:
        scale = 1.0
    return round(base * min(2.0, max(0.5, scale)), 3)


def normalise_label_density(value) -> str:
    """Return a valid label-density key, defaulting to ``standard``."""
    text = str(value or "standard").strip().lower()
    if text in LABEL_DENSITY_LEVELS:
        return text
    return "standard"


def labels_visible_for_span(density: str, lon_span: float) -> tuple[bool, bool, bool]:
    """Return ``(major, towns, smalls)`` visibility for a view span.

    ``major`` cities (large incorporated places) show at every span; ``towns``
    appear once the view closes to :data:`LABEL_SPAN_STANDARD`; the ``smalls``
    tier -- small places and subdivisions -- appears at or below
    :data:`LABEL_SPAN_DENSE` and only when the reader asked for ``dense``.
    ``off`` hides every tier; ``sparse`` keeps major cities at any zoom.
    """
    density = normalise_label_density(density)
    if density == "off":
        return (False, False, False)
    try:
        span = abs(float(lon_span))
    except (TypeError, ValueError, OverflowError):
        span = float("inf")
    if not math.isfinite(span):
        span = float("inf")
    if density == "sparse":
        return (True, False, False)
    if density == "dense":
        return (True, span <= LABEL_SPAN_STANDARD, span <= LABEL_SPAN_DENSE)
    return (True, span <= LABEL_SPAN_STANDARD, False)


def declutter_labels(
    candidates,
    *,
    protected=(),
    protected_rects=(),
    min_separation_px=LABEL_MIN_SEPARATION_PX,
    limit=LABEL_MAX_COUNT,
    label_widths=None,
    always_keep_importance=None,
    viewport_size=None,
):
    """Select non-overlapping labels, most important first.

    ``candidates`` are ``(importance, x, y, label)`` with lower ``importance``
    meaning more important. Lower tiers win over later candidate labels but
    still avoid ``protected`` marker/readout positions by default.
    Within one tier the sort is spatial, not arrival order. When
    ``viewport_size`` is supplied, candidates are visited in a deterministic
    viewport grid round-robin: one candidate from each occupied screen cell
    before a second candidate from any cell. A label cap therefore samples the
    *whole* viewport instead of exhausting the top and bottom edges first.
    Without a viewport, a stable centre-out y/x order keeps this Qt-free helper
    useful to headless callers. ``major`` metros keep their curated population
    order -- that order is the continental tie-break the continental view
    depends on. ``protected`` are ``(x, y)`` marker/readout positions labels
    must also avoid. ``protected_rects`` are existing text rectangles in
    ``(x, y, width, height)`` form. ``label_widths`` optionally maps each label
    to its drawn pixel width; a kept label then reserves its full text extent
    to the east, so a long eastern name cannot start inside a western one that
    point separation alone allowed. ``always_keep_importance`` optionally
    exempts one tier from both collision checks; the station-map caller uses
    tier 0 for selected/hovered/pinned labels whose own marker is necessarily
    protected. Returns the kept candidates in placement order.
    """
    try:
        minimum = float(min_separation_px)
    except (TypeError, ValueError, OverflowError):
        minimum = float(LABEL_MIN_SEPARATION_PX)
    if not math.isfinite(minimum) or minimum < 0.0:
        minimum = float(LABEL_MIN_SEPARATION_PX)
    try:
        cap = max(0, int(limit))
    except (TypeError, ValueError, OverflowError):
        cap = LABEL_MAX_COUNT
    if cap == 0:
        return []
    widths = dict(label_widths or {})
    valid = [
        item for item in candidates or ()
        if len(tuple(item)) == 4 and str(item[3]).strip()
    ]
    try:
        viewport_w, viewport_h = (
            max(1.0, float(viewport_size[0])),
            max(1.0, float(viewport_size[1])),
        )
        viewport_ok = math.isfinite(viewport_w) and math.isfinite(viewport_h)
    except (TypeError, ValueError, OverflowError, IndexError):
        viewport_w = viewport_h = 1.0
        viewport_ok = False

    def _spread(items):
        """Yield a deterministic viewport-wide order for one priority tier."""
        if not viewport_ok:
            # Centre-out avoids the historical edge bands even for callers that
            # cannot describe a viewport.
            centre_y = sum(float(item[2]) for item in items) / max(1, len(items))
            return sorted(
                items,
                key=lambda item: (
                    abs(float(item[2]) - centre_y), float(item[1]), float(item[2]),
                    str(item[3]),
                ),
            )
        # The screen grid is tied to the same minimum spacing the collision pass
        # uses. This keeps the bucket count bounded and makes a cap distribute
        # labels over both axes, rather than merely alternating two y extremes.
        aspect = viewport_w / viewport_h
        columns = max(1, int(round(math.sqrt(max(1, cap) * aspect))))
        rows = max(1, int(max(1, cap) // columns))
        columns = min(columns, max(1, int(viewport_w / max(minimum * 2.5, 1.0))))
        rows = min(rows, max(1, int(viewport_h / max(minimum * 1.6, 1.0))))
        while columns * rows > max(1, cap):
            if columns >= rows and columns > 1:
                columns -= 1
            elif rows > 1:
                rows -= 1
            else:
                break
        cell_w = viewport_w / columns
        cell_h = viewport_h / rows
        buckets: dict[tuple[int, int], list] = {}
        for item in items:
            col = max(0, min(columns - 1, int(float(item[1]) / cell_w)))
            row = max(0, min(rows - 1, int(float(item[2]) / cell_h)))
            buckets.setdefault((row, col), []).append(item)
        for bucket in buckets.values():
            bucket.sort(key=lambda item: (
                str(item[3]), float(item[2]), float(item[1]),
            ))
        # Checkerboard phase first, then the remaining cells. This prevents a
        # low cap from painting one contiguous band while remaining stable.
        cells = sorted(
            buckets,
            key=lambda cell: (
                (cell[0] + cell[1]) % 2,
                abs(cell[0] - (rows - 1) / 2.0),
                abs(cell[1] - (columns - 1) / 2.0),
                cell[0], cell[1],
            ),
        )
        spread = []
        depth = 0
        while True:
            added = False
            for cell in cells:
                bucket = buckets[cell]
                if depth < len(bucket):
                    spread.append(bucket[depth])
                    added = True
            if not added:
                break
            depth += 1
        return spread

    ordered: list = []
    for tier in sorted({item[0] for item in valid}):
        tier_items = [item for item in valid if item[0] == tier]
        if tier == 1:
            # Curated population order is the continental tie-break.
            ordered.extend(tier_items)
            continue
        ordered.extend(_spread(tier_items))
    kept = []
    placed = [(float(x), float(y)) for x, y in (protected or ()) if _finite(x, y)]
    reserved = []
    for rect in protected_rects or ():
        try:
            rx, ry, rw, rh = (float(value) for value in rect[:4])
        except (TypeError, ValueError, OverflowError, IndexError):
            continue
        if _finite(rx, ry, rw, rh) and rw >= 0.0 and rh >= 0.0:
            reserved.append((rx, ry, rw, rh))
    for importance, x, y, label in ordered:
        if not _finite(x, y):
            continue
        point = (float(x), float(y))
        force_keep = (
            always_keep_importance is not None
            and importance == always_keep_importance
        )
        if not force_keep and any(
            math.hypot(point[0] - px, point[1] - py) < minimum
            for px, py in placed
        ):
            continue
        try:
            extent = float(widths.get(str(label), 0.0))
        except (TypeError, ValueError, OverflowError):
            extent = 0.0
        if not math.isfinite(extent) or extent < 0.0:
            extent = 0.0
        draw_x = point[0] + 5.0
        if viewport_ok:
            draw_x = min(draw_x, max(0.0, viewport_w - extent - 4.0))
        candidate_rect = (draw_x, point[1] - 13.0, extent + 8.0, 14.0)
        blocked = any(
            candidate_rect[0] < rx + rw + 4.0
            and candidate_rect[0] + candidate_rect[2] + 4.0 > rx
            and candidate_rect[1] < ry + rh + 2.0
            and candidate_rect[1] + candidate_rect[3] + 2.0 > ry
            for rx, ry, rw, rh in reserved
        )
        if blocked and not force_keep:
            continue
        kept.append((importance, float(x), float(y), str(label)))
        placed.append(point)
        reserved.append(candidate_rect)
        if len(kept) >= cap:
            break
    return kept


def _finite(*values) -> bool:
    try:
        return all(math.isfinite(float(value)) for value in values)
    except (TypeError, ValueError, OverflowError):
        return False


def km_per_pixel_at_latitude(lon_span: float, widget_width_px: float,
                             latitude: float, *, cos_adjusted: bool = True) -> float:
    """Return kilometres per screen pixel at one latitude on a flat view.

    The flat projection squeezes longitudes by ``cos(latitude)``, so one pixel
    covers fewer kilometres at high latitudes than at the equator. Curved
    (conic) views vary continuously across the frame; callers pass the cone's
    local scale there instead -- this helper documents the flat-view contract
    the scale bar is built on.
    """
    try:
        span = abs(float(lon_span))
        width = float(widget_width_px)
        latitude = float(latitude)
    except (TypeError, ValueError, OverflowError):
        return float("nan")
    if not math.isfinite(span) or not math.isfinite(width) or width <= 0.0:
        return float("nan")
    if not math.isfinite(latitude):
        return float("nan")
    clamped = max(-89.0, min(89.0, latitude))
    squeeze = math.cos(math.radians(clamped)) if cos_adjusted else 1.0
    squeeze = max(0.05, squeeze)
    degrees_per_pixel = span / width
    return degrees_per_pixel * squeeze * 111.195


def pick_scale_length(km_per_pixel: float, *, target_px=SCALE_BAR_TARGET_PX,
                      imperial: bool = False) -> tuple[float, float, str]:
    """Return ``(length_value, length_px, unit_label)`` for the scale bar.

    Picks the step whose on-screen width lands closest to the target, then
    reports the matching imperial length when asked. A non-finite or
    non-positive scale returns a zero-length bar so the caller draws the
    "scale unavailable" state instead of a misleading bar.
    """
    try:
        scale = float(km_per_pixel)
        target = float(target_px)
    except (TypeError, ValueError, OverflowError):
        scale, target = float("nan"), float(SCALE_BAR_TARGET_PX)
    if not math.isfinite(scale) or scale <= 0.0 or not math.isfinite(target) \
            or target <= 0.0:
        return (0.0, 0.0, "km" if not imperial else "mi")
    if imperial:
        steps = tuple(value / KM_PER_MILE for value in SCALE_BAR_STEPS_KM)
        unit = "mi"
    else:
        steps = tuple(float(value) for value in SCALE_BAR_STEPS_KM)
        unit = "km"
    best = min(steps, key=lambda step: abs(step / scale - target))
    return (best, best / scale, unit)


def format_scale_length(length: float, unit: str) -> str:
    """Format a scale-bar length without trailing noise."""
    try:
        value = float(length)
    except (TypeError, ValueError, OverflowError):
        return f"Unknown {unit}"
    if not math.isfinite(value) or value <= 0.0:
        return f"Unknown {unit}"
    text = f"{value:.0f}" if value >= 100.0 else (
        f"{value:.1f}".rstrip("0").rstrip("."))
    return f"{text} {unit}"


def luminance(hex_colour: str) -> float:
    """Return the relative luminance of a ``#rrggbb`` colour, 0..1."""
    text = str(hex_colour or "").strip().lstrip("#")
    if len(text) != 6:
        return float("nan")
    try:
        channels = tuple(int(text[offset:offset + 2], 16) / 255.0
                         for offset in (0, 2, 4))
    except ValueError:
        return float("nan")

    def linear(channel: float) -> float:
        if channel <= 0.03928:
            return channel / 12.92
        return ((channel + 0.055) / 1.055) ** 2.4

    red, green, blue = (linear(channel) for channel in channels)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def contrast_ratio(first: str, second: str) -> float:
    """Return the WCAG contrast ratio between two ``#rrggbb`` colours."""
    first_lum = luminance(first)
    second_lum = luminance(second)
    if not math.isfinite(first_lum) or not math.isfinite(second_lum):
        return float("nan")
    lighter = max(first_lum, second_lum)
    darker = min(first_lum, second_lum)
    return (lighter + 0.05) / (darker + 0.05)


__all__ = [
    "BOUNDARY_WIDTHS",
    "BOUNDARY_ZORDER",
    "EARTH_RADIUS_KM",
    "KM_PER_MILE",
    "LABEL_DENSITY_LABELS",
    "LABEL_DENSITY_LEVELS",
    "LABEL_MAX_COUNT",
    "LABEL_MAX_COUNT_LARGE_TEXT",
    "LABEL_MIN_SEPARATION_PX",
    "LABEL_MIN_SEPARATION_PX_LARGE_TEXT",
    "LABEL_SPAN_DENSE",
    "LABEL_SPAN_STANDARD",
    "MAJOR_CITIES",
    "SCALE_BAR_STEPS_KM",
    "SCALE_BAR_TARGET_PX",
    "boundary_draw_order",
    "boundary_width",
    "contrast_ratio",
    "declutter_labels",
    "format_scale_length",
    "km_per_pixel_at_latitude",
    "labels_visible_for_span",
    "luminance",
    "normalise_label_density",
    "pick_scale_length",
]

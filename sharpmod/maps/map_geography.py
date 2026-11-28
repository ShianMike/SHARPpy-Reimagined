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
    import sharpmod_rs

    return sharpmod_rs.geo_boundary_draw_order(names)


def boundary_width(family: str, *, density_scale: float = 1.0) -> float:
    """Return the stroke width for one boundary family."""
    import sharpmod_rs

    return sharpmod_rs.geo_boundary_width(str(family), density_scale)


def normalise_label_density(value) -> str:
    """Return a valid label-density key, defaulting to ``standard``."""
    import sharpmod_rs

    return sharpmod_rs.geo_normalise_label_density(value)


def labels_visible_for_span(density: str, lon_span: float) -> tuple[bool, bool, bool]:
    """Return ``(major, towns, smalls)`` visibility for a view span.

    ``major`` cities (large incorporated places) show at every span; ``towns``
    appear once the view closes to :data:`LABEL_SPAN_STANDARD`; the ``smalls``
    tier -- small places and subdivisions -- appears at or below
    :data:`LABEL_SPAN_DENSE` and only when the reader asked for ``dense``.
    ``off`` hides every tier; ``sparse`` keeps major cities at any zoom.
    """
    import sharpmod_rs

    return sharpmod_rs.geo_labels_visible_for_span(density, lon_span)


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
    import sharpmod_rs

    return sharpmod_rs.geo_declutter_labels(
        candidates,
        protected,
        protected_rects,
        min_separation_px,
        limit,
        label_widths,
        always_keep_importance,
        viewport_size,
    )


def km_per_pixel_at_latitude(lon_span: float, widget_width_px: float,
                             latitude: float, *, cos_adjusted: bool = True) -> float:
    """Return kilometres per screen pixel at one latitude on a flat view.

    The flat projection squeezes longitudes by ``cos(latitude)``, so one pixel
    covers fewer kilometres at high latitudes than at the equator. Curved
    (conic) views vary continuously across the frame; callers pass the cone's
    local scale there instead -- this helper documents the flat-view contract
    the scale bar is built on.
    """
    import sharpmod_rs

    return sharpmod_rs.geo_km_per_pixel_at_latitude(
        lon_span, widget_width_px, latitude, bool(cos_adjusted),
    )


def pick_scale_length(km_per_pixel: float, *, target_px=SCALE_BAR_TARGET_PX,
                      imperial: bool = False) -> tuple[float, float, str]:
    """Return ``(length_value, length_px, unit_label)`` for the scale bar.

    Picks the step whose on-screen width lands closest to the target, then
    reports the matching imperial length when asked. A non-finite or
    non-positive scale returns a zero-length bar so the caller draws the
    "scale unavailable" state instead of a misleading bar.
    """
    import sharpmod_rs

    return sharpmod_rs.geo_pick_scale_length(km_per_pixel, target_px, bool(imperial))


def format_scale_length(length: float, unit: str) -> str:
    """Format a scale-bar length without trailing noise."""
    import sharpmod_rs

    return sharpmod_rs.geo_format_scale_length(length, str(unit))


def luminance(hex_colour: str) -> float:
    """Return the relative luminance of a ``#rrggbb`` colour, 0..1."""
    import sharpmod_rs

    return sharpmod_rs.geo_luminance(hex_colour)


def contrast_ratio(first: str, second: str) -> float:
    """Return the WCAG contrast ratio between two ``#rrggbb`` colours."""
    import sharpmod_rs

    return sharpmod_rs.geo_contrast_ratio(first, second)


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

"""NWS Local Storm Reports as a map overlay.

Reports are read against the outlook that anticipated them: the question they
answer is whether what was forecast is what happened. :mod:`sharpmod
.locator_overlay` enforces that pairing, and this module only supplies the
points.

**Source.** The Iowa Environmental Mesonet's Local Storm Report query service
rather than SPC's ``climo/reports`` files. That is a deliberate exception to this
project's habit of reading primary NOAA sources, and it is worth stating why,
because the SPC file is the more obvious choice and the wrong one:

* SPC serves three CSV tables concatenated into one file, with the hazard implied
  by which header block a row sits under and no column naming it.
* Its times are bare ``HHMM`` over a 12Z convective day, so they wrap past
  midnight and have to be reassembled against a date the file does not carry.
* Its hail sizes are hundredths of an inch (``325`` meaning 3.25"), a convention
  nothing else in this codebase uses.
* It offers no filtering, so reading one county means transferring the continent.

The IEM service answers with a single header, full ``YYYYMMDDHHMM`` timestamps,
magnitudes in natural units, and server-side filtering by time, area, hazard and
magnitude. What it returns are *Local Storm Reports* -- the NWS products SPC
filters and de-duplicates to build its own list -- so this is the fuller,
noisier upstream rather than SPC's edit of it. It includes marine and non-severe
types, which is why :data:`HAZARDS` narrows to the three convective ones.

**Marker size is in screen pixels.** The geographic rings remain for spatial
queries and export, while both picker and sounding maps draw the supplied
point symbol at a fixed size as the view zooms.
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import math
import os
import tempfile
import threading
import time
import urllib.parse
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sharpmod.maps.map_overlays import OverlayShape, bounds_of, build_layer

_LOGGER = logging.getLogger(__name__)

#: Registry key. Matches :data:`sharpmod.maps.locator_overlay.OVERLAY_KEYS`.
OVERLAY_KEY = "storm_reports"
OVERLAY_TITLE = "Storm reports"
ATTRIBUTION = "NWS Local Storm Reports via Iowa Environmental Mesonet"

_BASE = "https://mesonet.agron.iastate.edu/cgi-bin/request/gis/lsr.py"


@dataclass(frozen=True)
class Hazard:
    """One report class this overlay draws."""

    code: str
    label: str
    stroke: str
    fill: str
    units: str = ""
    #: Magnitude at or above which SPC plots the report as *significant severe*,
    #: in :attr:`units`. ``None`` for a class SPC does not grade on this map:
    #: a significant tornado is EF2+, which is a damage survey's verdict days
    #: later and not something a same-day report carries.
    significant_at: float | None = None


#: Knots to mph, for stating SPC's 65 kt significant-wind threshold in the units
#: this feed publishes.
#:
#: The feed's unit is not documented in the response -- there is no unit column
#: -- so it was read off the data: wind rows carrying ``MAG`` of 53, 68 and 98
#: sit beside remarks reading "gusted to 53 mph", "gusted to 68 mph" and "98 mph
#: measured by Nebraska Mesonet", and hail rows run 0.25 to 4.0. Magnitudes are
#: therefore mph and inches, not knots and hundredths.
_MPH_PER_KNOT = 1.15078

#: SPC's significant-severe thresholds: 2" hail and a 65 kt gust.
SIGNIFICANT_HAIL_IN = 2.00
SIGNIFICANT_WIND_MPH = 65.0 * _MPH_PER_KNOT

#: The colour SPC gives a significant report, whatever its hazard.
#:
#: One neutral for all of them, so the few 2"+ hail and 65 kt+ gusts inside a
#: scatter of ordinary reports can be picked out at a glance. On SPC's white
#: graphic that neutral is black. Here the basemap is near-black, so the role is
#: filled by white instead: what the design needs is one high-contrast colour
#: that is neither red, blue nor green, and black on this map is not a colour but
#: an absence.
SIGNIFICANT_FILL = "#FFFFFF"
SIGNIFICANT_STROKE = "#101010"

#: The convective hazards, keyed by the service's ``TYPECODE``.
#:
#: Narrowed on purpose. The feed also carries marine wind, snow, freezing rain
#: and flooding, none of which a convective outlook forecasts, so drawing them
#: beside one would answer a question nobody asked.
#:
#: Colours are SPC's plotting convention -- red tornado, blue wind, green hail --
#: lifted in lightness for a dark basemap. A forecaster reads those three
#: without a legend, which is the point: this overlay draws one shape per
#: observation, so it has no legend to read.
HAZARDS = {
    "T": Hazard("T", "Tornado", "#7F0000", "#E5342F"),
    "G": Hazard("G", "Wind", "#00337F", "#3C7CFF", units="mph",
                significant_at=SIGNIFICANT_WIND_MPH),
    "D": Hazard("D", "Wind damage", "#00337F", "#3C7CFF", units="mph",
                significant_at=SIGNIFICANT_WIND_MPH),
    "H": Hazard("H", "Hail", "#004D1A", "#26B14E", units="in",
                significant_at=SIGNIFICANT_HAIL_IN),
}

#: Geographic radius retained for shape bounds and non-screen spatial queries.
#: Its value does not affect the marker painted on either map.
MARKER_SPAN_FRACTION = 0.004

#: Keep the fallback geographic geometry bounded at extreme view spans.
MIN_MARKER_DEG = 0.02
MAX_MARKER_DEG = 0.30

#: Preserve the significant-report ring's relative extent for spatial clients.
SIGNIFICANT_MARKER_SCALE = 1.45

# Map and sounding locator symbols are painted in screen pixels. Their
# geographic rings remain for spatial queries, but no longer set visible size.
REPORT_MARKER_RADIUS_PX = 2.75
TORNADO_MARKER_RADIUS_PX = 3.25
SIGNIFICANT_MARKER_RADIUS_PX = 3.5
REPORT_MARKER_SYMBOLS = {
    "T": "triangle",
    "G": "circle",
    "D": "square",
    "H": "diamond",
}

#: How far back to ask for reports when a caller does not say.
DEFAULT_WINDOW = timedelta(hours=6)

#: User-facing report filters.  The provider's ``type`` query is intentionally
#: not used (see :func:`build_url`); these are applied to the parsed response so
#: selecting a hazard cannot silently turn a valid feed into an empty one.
REPORT_FILTER_LABELS = {
    "all": "All convective hazards",
    "tornado": "Tornado",
    "hail": "Hail",
    "wind": "Wind + wind damage",
    "significant": "Significant hail/wind",
}

#: Total query spans, presented as symmetric windows around the selected time.
REPORT_WINDOWS = (
    ("±1 hour", timedelta(hours=2)),
    ("±3 hours", timedelta(hours=6)),
    ("±6 hours", timedelta(hours=12)),
    ("±12 hours", timedelta(hours=24)),
)

#: A live window keeps changing, so it is held only long enough to spare the
#: service a burst of identical requests while a user scrubs a time field.
LIVE_CACHE_TTL_S = 180.0

#: A window that has already closed does not gain reports at any rate worth
#: re-asking about, so it is held for a session and written to disk.
ARCHIVE_CACHE_TTL_S = 3600.0

#: Remember a failure briefly, so a broken feed is not retried on every repaint.
FAILURE_CACHE_TTL_S = 60.0

_CACHE_MAX_ENTRIES = 32

#: How far past a window's end it must be before that window counts as settled.
#:
#: Reports arrive late -- a delayed public report can be appended hours after the
#: event -- so a window is not treated as final the moment it ends. Beyond this
#: it is stable enough to keep on disk, which is the same judgement
#: :mod:`sharpmod.providers.hrrr_field` makes about a finished model run.
ARCHIVE_SETTLED_AFTER = timedelta(hours=6)

#: Vertices in the retained geographic ring. The GUI paints from the report's
#: centre and fixed pixel radius rather than drawing these vertices.
_MARKER_VERTICES = 16


class StormReportsError(RuntimeError):
    """Reports could not be fetched. Cancellation is not one of these."""


@dataclass(frozen=True)
class StormReport:
    """One report, in the units the service published it in."""

    hazard: Hazard
    valid: datetime
    lat: float
    lon: float
    magnitude: float | None
    city: str = ""
    county: str = ""
    state: str = ""
    wfo: str = ""
    source: str = ""
    remark: str = ""

    def magnitude_text(self) -> str:
        """Return the magnitude the way a forecaster would say it."""
        if self.magnitude is None:
            return ""
        if self.hazard.units == "in":
            # Bare units: :meth:`label` already names the hazard, and "Hail
            # 1.75 in hail" says it twice.
            return f"{self.magnitude:.2f} in"
        if self.hazard.units == "mph":
            return f"{self.magnitude:.0f} mph"
        return f"{self.magnitude:g}"

    def is_significant(self) -> bool:
        """Whether SPC would plot this as significant severe.

        A report with no magnitude is not significant: most wind *damage* rows
        carry none, and treating an unknown as significant would promote the
        vaguest reports in the feed to the most prominent marks on the map.
        """
        threshold = self.hazard.significant_at
        return (threshold is not None and self.magnitude is not None
                and self.magnitude >= threshold)

    def label(self) -> str:
        """Give the marker a compact SPC-style label for click details."""
        magnitude = self.magnitude_text()
        name = (
            f"Sig {self.hazard.label}"
            if self.is_significant() else self.hazard.label
        )
        return f"{name} {magnitude}" if magnitude else name

    def map_caption(self) -> str:
        """Keep any on-map text brief; the click retains the full report."""
        if self.hazard.code == "T":
            return "TOR"
        if self.hazard.code == "D":
            return "DMG"
        if self.hazard.code == "H" and self.magnitude is not None:
            return f"{self.magnitude:g}\u2033"
        return self.magnitude_text() or self.hazard.label

    def description(self) -> str:
        """The detail behind the label, assembled for a click to display."""
        lines = []
        where = ", ".join(part for part in (self.city, self.county, self.state)
                          if part)
        if where:
            lines.append(where)
        lines.append(f"Event time: {self.valid:%d %b %H%MZ}")
        magnitude = self.magnitude_text()
        if magnitude:
            lines.append(f"Magnitude: {magnitude}")
        if self.source:
            lines.append(f"Source: {self.source} (report source)")
        if self.remark:
            lines.append(self.remark)
        if self.wfo:
            lines.append(f"Issuing office: NWS WFO {self.wfo}")
        lines.append("Provider: NWS Local Storm Reports via Iowa Environmental Mesonet")
        return "\n".join(lines)


def _float(value: str) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _valid_time(value: str) -> datetime | None:
    """Parse the service's ``YYYYMMDDHHMM`` stamp, which is UTC."""
    text = str(value).strip()
    try:
        return datetime.strptime(text, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_reports(text: str) -> tuple[StormReport, ...]:
    """Parse the service's CSV into reports, discarding what cannot be drawn.

    Tolerant by row rather than by file: a single malformed record, an unknown
    hazard code, or a report with no usable position is skipped, because one bad
    row in a live feed should cost that row and not the whole overlay.
    """
    if not text or not text.strip():
        return ()
    reader = csv.DictReader(io.StringIO(text))
    reports: list[StormReport] = []
    for row in reader:
        hazard = HAZARDS.get(str(row.get("TYPECODE", "")).strip().upper())
        if hazard is None:
            continue
        latitude = _float(row.get("LAT", ""))
        longitude = _float(row.get("LON", ""))
        when = _valid_time(row.get("VALID", ""))
        if (latitude is None or longitude is None or when is None
                or not -90.0 <= latitude <= 90.0
                or not -180.0 <= longitude <= 180.0):
            continue
        reports.append(StormReport(
            hazard=hazard,
            valid=when,
            lat=latitude,
            lon=longitude,
            magnitude=_float(row.get("MAG", "")),
            city=str(row.get("CITY", "") or "").strip(),
            county=str(row.get("UGCNAME", "") or row.get("COUNTY", "")).strip(),
            state=str(row.get("STATE", "") or "").strip(),
            wfo=str(row.get("WFO", "") or "").strip(),
            source=str(row.get("SOURCE", "") or "").strip(),
            remark=str(row.get("REMARK", "") or "").strip(),
        ))
    return tuple(reports)


def normalise_report_filter(value: str | None) -> str:
    """Return a supported hazard-filter key, defaulting safely to ``all``."""

    key = str(value or "all").strip().lower().replace("_", "-")
    aliases = {
        "t": "tornado",
        "h": "hail",
        "g": "wind",
        "d": "wind",
        "sig": "significant",
        "significant-severe": "significant",
    }
    key = aliases.get(key, key)
    return key if key in REPORT_FILTER_LABELS else "all"


def filter_reports(reports, hazard_filter: str | None = "all") -> tuple[StormReport, ...]:
    """Filter parsed reports without asking the provider's broken type filter."""

    key = normalise_report_filter(hazard_filter)
    reports = tuple(reports)
    if key == "all":
        return reports
    if key == "tornado":
        return tuple(report for report in reports if report.hazard.code == "T")
    if key == "hail":
        return tuple(report for report in reports if report.hazard.code == "H")
    if key == "wind":
        return tuple(report for report in reports if report.hazard.code in {"G", "D"})
    return tuple(report for report in reports if report.is_significant())


def report_window_label(window: timedelta | None) -> str:
    """Describe a symmetric total query span as a compact half-window."""

    span = max(timedelta(minutes=15), window or DEFAULT_WINDOW)
    half_minutes = int(round(span.total_seconds() / 120.0))
    if half_minutes % 60 == 0:
        hours = half_minutes // 60
        return f"±{hours} hour{'s' if hours != 1 else ''}"
    return f"±{half_minutes} min"


def _report_count_label(filter_key: str, count: int) -> str:
    """Name a count and its optional filter in one compact caption unit."""
    noun = f"storm report{'s' if count != 1 else ''}"
    if filter_key == "all":
        return noun
    return f"{noun} ({REPORT_FILTER_LABELS[filter_key]})"


def marker_radius(span_deg: float | None) -> float:
    """Return a geographic query radius; visible symbols use screen pixels."""
    if span_deg is None or not math.isfinite(span_deg) or span_deg <= 0.0:
        span_deg = 60.0  # a continental view, the common default
    scaled = float(span_deg) * MARKER_SPAN_FRACTION
    return max(MIN_MARKER_DEG, min(MAX_MARKER_DEG, scaled))


def _marker_ring(report: StormReport, radius: float):
    """Return a small closed ring standing in for a point.

    Longitude is stretched by ``1/cos(latitude)`` so the marker stays visibly
    round on a plate-carree map instead of flattening towards the poles.
    """
    stretch = 1.0 / max(0.2, math.cos(math.radians(report.lat)))
    points = []
    for index in range(_MARKER_VERTICES):
        angle = 2.0 * math.pi * index / _MARKER_VERTICES
        points.append((
            report.lon + radius * stretch * math.cos(angle),
            report.lat + radius * math.sin(angle),
        ))
    points.append(points[0])
    return tuple(points)


#: Draw order within the layer, by hazard code.
#:
#: Tornado on top, so where markers overlap the tornado is the one left legible
#: and, where they coincide exactly, the one a click reports. Significant wind
#: and hail are lifted above ordinary reports but stay below it: they are the
#: exceptional members of their own class, not a rival to a tornado.
_BASE_RANK = {"H": 1, "G": 2, "D": 2, "T": 5}
_SIGNIFICANT_RANK_BONUS = 2


def layer_from_reports(reports, *, span_deg=None, valid_from=None,
                       valid_to=None, source_url: str = "", around=None,
                       window: timedelta | None = None,
                       hazard_filter: str | None = "all"):
    """Build the drawable overlay for ``reports``, or ``None`` if there are none.

    Shapes are marked as point symbols, so the paint path draws them opaque at
    a fixed screen-pixel size rather than washing them translucent the way it
    treats a risk polygon. Individual symbol shapes separate hazards even when
    several reports share a location.

    The layer opts out of the legend. Its shapes are individual observations, so
    a swatch row would list the data -- "Hail 1.00 in  Wind 61 mph  Wind 57 mph"
    across the bottom of the map -- rather than provide a key to it. The three
    hazard colours are SPC's own and need none.
    """
    filter_key = normalise_report_filter(hazard_filter)
    reports = filter_reports(reports, filter_key)
    if not reports:
        return None
    radius = marker_radius(span_deg)
    shapes = []
    for report in reports:
        significant = report.is_significant()
        rings = (_marker_ring(
            report,
            radius * SIGNIFICANT_MARKER_SCALE if significant else radius),)
        shapes.append(OverlayShape(
            rings=rings,
            bounds=bounds_of(rings),
            stroke=SIGNIFICANT_STROKE if significant else report.hazard.stroke,
            fill=SIGNIFICANT_FILL if significant else report.hazard.fill,
            label=report.label(),
            description=report.description(),
            rank=_BASE_RANK.get(report.hazard.code, 1)
            + (_SIGNIFICANT_RANK_BONUS if significant else 0),
            marker=True,
            marker_id=(
                f"report:{report.valid.isoformat()}:{report.hazard.code}:"
                f"{report.lat:.5f}:{report.lon:.5f}"
            ),
            marker_kind="report",
            marker_category=(
                "significant" if significant else report.hazard.code.lower()
            ),
            marker_time=report.valid,
            marker_significant=significant,
            marker_lon=report.lon,
            marker_lat=report.lat,
            marker_radius_px=(
                SIGNIFICANT_MARKER_RADIUS_PX if significant else
                TORNADO_MARKER_RADIUS_PX if report.hazard.code == "T" else
                REPORT_MARKER_RADIUS_PX
            ),
            marker_symbol=REPORT_MARKER_SYMBOLS[report.hazard.code],
            marker_caption=report.map_caption(),
        ))
    moments = [report.valid for report in reports]
    centre = around
    if centre is None:
        centre = min(moments) + (max(moments) - min(moments)) / 2
    try:
        centre_text = centre.strftime("%d %b %H%MZ")
    except AttributeError:
        centre_text = "selected time"
    subtitle = (
        f"{len(shapes)} {_report_count_label(filter_key, len(shapes))} · "
        f"{report_window_label(window)} · {centre_text}"
    )
    return build_layer(
        OVERLAY_KEY,
        OVERLAY_TITLE,
        shapes,
        subtitle=subtitle,
        valid_from=valid_from or min(moments),
        valid_to=valid_to or max(moments),
        short_name="LSR",
        source_url=source_url,
        attribution=ATTRIBUTION,
        legend=False,
    )


def filter_report_layer(layer, hazard_filter: str | None = "all"):
    """Return ``layer`` narrowed to one UI hazard filter, or ``None`` if empty."""

    if layer is None:
        return None
    key = normalise_report_filter(hazard_filter)

    def keep(shape):
        category = str(getattr(shape, "marker_category", "") or "").lower()
        if key == "all":
            return True
        if key == "significant":
            return bool(getattr(shape, "marker_significant", False))
        if key == "tornado":
            return category == "t"
        if key == "hail":
            return category in {"h", "significant"} and "hail" in str(
                getattr(shape, "label", "")
            ).lower()
        return category in {"g", "d", "significant"} and any(
            word in str(getattr(shape, "label", "")).lower()
            for word in ("wind", "gust")
        )

    shapes = tuple(shape for shape in layer.shapes if keep(shape))
    if not shapes:
        return None
    subtitle = str(getattr(layer, "subtitle", "") or "")
    parts = subtitle.split(" · ")
    if parts:
        parts[0] = f"{len(shapes)} {_report_count_label(key, len(shapes))}"
    return replace(layer, shapes=shapes, subtitle=" · ".join(parts))


def _normalise_view(view) -> tuple[str, str, str, str] | None:
    """Return a service-safe bounding box, or omit an unusable view.

    Map widgets can briefly report a zero-sized extent during layout, and a
    highly zoomed extent can collapse when rounded to the four decimals the
    IEM service accepts. Sending either produces HTTP 422 and marks the layer as
    failed. The box is only an optimization, so an unusable one falls back to
    the provider's normal time-window query.
    """
    if view is None:
        return None
    try:
        if len(view) != 4:
            return None
        lon0, lon1, lat0, lat1 = (float(value) for value in view)
    except (TypeError, ValueError, OverflowError):
        return None

    if not all(math.isfinite(value) for value in (lon0, lon1, lat0, lat1)):
        return None
    west, east = sorted((lon0, lon1))
    south, north = sorted((lat0, lat1))
    if not (-180.0 <= west < east <= 180.0
            and -90.0 <= south < north <= 90.0):
        return None

    formatted = tuple(f"{value:.4f}" for value in (west, east, south, north))
    if not (float(formatted[0]) < float(formatted[1])
            and float(formatted[2]) < float(formatted[3])):
        return None
    return formatted


def build_url(*, window: timedelta = DEFAULT_WINDOW, view=None,
              around: datetime | None = None) -> str:
    """Return the query for a window of reports, optionally within ``view``.

    Pass ``around`` to ask about a past event; leave it unset for "the last few
    hours". Unlike radar, there is a deep archive behind these reports -- a 2015
    query answers -- so a sounding from years ago can still be shown what
    actually happened around it, which is the whole point of reading reports
    beside an outlook.

    A past window is expressed with the service's ``year1``/``month1``/... fields
    rather than ``sts``/``ets``. Measured against the live service, ``sts`` is
    accepted only with a trailing ``Z`` -- the same value without a zone, with
    seconds, or space-separated all answer 422, and a compact ``YYYYMMDDHHMM``
    is accepted but matches nothing. The numeric fields have no such ambiguity.

    **The service's own hazard filter is deliberately not used.** Measured
    against the live feed, ``type=H`` -- and every other code, including the ones
    the response's own ``TYPECODE`` column contains -- answers 200 OK with a
    header and no rows, while the same request without it returns 190 reports of
    which 8 are hail. A filter that silently discards everything would leave a
    permanently empty overlay with nothing to explain it, so hazards are selected
    from the response instead, in :func:`parse_reports`.

    A valid bounding box narrows the response to reports that can be drawn.
    Invalid or zero-area bounds are omitted: a map can briefly report one while
    laying out, and the service rejects it with HTTP 422.
    """
    span = max(timedelta(minutes=15), window)
    params: list[tuple[str, str]] = [("justcsv", "1")]
    if around is None:
        params.append(("recent", str(min(int(span.total_seconds()), 999_999))))
    else:
        # Centred on the moment asked about, so reports both leading up to and
        # following the sounding are included.
        moment = around if around.tzinfo else around.replace(tzinfo=timezone.utc)
        start = (moment - span / 2).astimezone(timezone.utc)
        end = (moment + span / 2).astimezone(timezone.utc)
        for index, edge in ((1, start), (2, end)):
            params.extend((
                (f"year{index}", str(edge.year)),
                (f"month{index}", str(edge.month)),
                (f"day{index}", str(edge.day)),
                (f"hour{index}", str(edge.hour)),
                (f"minute{index}", str(edge.minute)),
            ))
    bbox = _normalise_view(view)
    if bbox is not None:
        west, east, south, north = bbox
        params.extend((
            ("west", west),
            ("east", east),
            ("south", south),
            ("north", north),
        ))
    return f"{_BASE}?{urllib.parse.urlencode(params)}"


# --------------------------------------------------------------------------- #
# Download, cached in memory and -- once a window has settled -- on disk
# --------------------------------------------------------------------------- #

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, str | None]] = {}


def disk_cache_root() -> Path:
    """Where settled report windows are kept between sessions."""
    override = os.environ.get("SHARPMOD_STORM_REPORTS_CACHE", "").strip()
    if override:
        return Path(override).expanduser()
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "SHARPpy Reimagined" / "storm-reports"


def clear_cache() -> None:
    """Forget every in-memory response. The disk copies are left alone."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _cache_key(url: str) -> str:
    """A filesystem-safe identity for a request.

    The query carries a bounding box with four decimal places and a date, so it
    is hashed rather than transcribed: it would otherwise make a long and
    punctuation-heavy filename.
    """
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]


def window_is_settled(*, window: timedelta, around: datetime | None,
                      now: datetime | None = None) -> bool:
    """Whether this window has closed long enough ago to be worth keeping.

    A live request never is, by definition. A past one is once late-arriving
    reports have had :data:`ARCHIVE_SETTLED_AFTER` to turn up.
    """
    if around is None:
        return False
    moment = around if around.tzinfo else around.replace(tzinfo=timezone.utc)
    ends = moment + max(timedelta(minutes=15), window) / 2
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (reference - ends) >= ARCHIVE_SETTLED_AFTER


def _disk_read(path: Path) -> str | None:
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8")
    except OSError:
        return None
    return None


def _disk_write(path: Path, payload: str) -> None:
    """Write a response atomically, and never fail the overlay if we cannot."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".part")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
            os.replace(temporary, path)
        except BaseException:
            with suppress(OSError):
                os.unlink(temporary)
            raise
    except OSError:
        _LOGGER.debug("storm_reports.disk_write_failed path=%s", path,
                      exc_info=True)


def _remember(key: str, payload: str | None, ttl_marker: float) -> None:
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX_ENTRIES:
            # Drop the oldest rather than clearing: a user stepping through hours
            # would otherwise lose the window they just came from.
            oldest = min(_CACHE, key=lambda item: _CACHE[item][0])
            _CACHE.pop(oldest, None)
        _CACHE[key] = (ttl_marker, payload)


def download(*, window: timedelta = DEFAULT_WINDOW, view=None,
             around: datetime | None = None, opener=None,
             should_cancel=None) -> tuple[str, str] | None:
    """Request one window of reports, returning ``(csv_text, url)``.

    Caching follows :mod:`sharpmod.providers.hrrr_field`: answered from memory while still
    fresh, and a window that has settled is also kept on disk so revisiting a
    past event costs nothing. A failure is remembered briefly too, so a broken
    feed is not retried on every repaint.

    Returns ``None`` only when ``should_cancel`` asked us to stop; a real failure
    raises :class:`StormReportsError`.
    """
    if should_cancel and should_cancel():
        return None
    url = build_url(window=window, view=view, around=around)
    key = _cache_key(url)
    settled = window_is_settled(window=window, around=around)
    moment = time.monotonic()

    with _CACHE_LOCK:
        cached = _CACHE.get(key)
    if cached is not None:
        age = moment - cached[0]
        payload = cached[1]
        if payload is None:
            if age < FAILURE_CACHE_TTL_S:
                raise StormReportsError(
                    "storm reports were unavailable for this window")
        elif age < (ARCHIVE_CACHE_TTL_S if settled else LIVE_CACHE_TTL_S):
            return payload, url

    path = disk_cache_root() / f"{key}.csv"
    if settled:
        stored = _disk_read(path)
        if stored is not None:
            _remember(key, stored, moment)
            return stored, url

    call = opener or _default_opener
    try:
        payload = call(url, 20.0, 8 * 1024 * 1024)
    except Exception as error:  # noqa: BLE001 - one failure mode for callers
        _remember(key, None, moment)
        raise StormReportsError(f"storm reports were unavailable: {error}") \
            from error
    if should_cancel and should_cancel():
        return None
    text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) \
        else str(payload)
    _remember(key, text, moment)
    if settled:
        _disk_write(path, text)
    return text, url


def fetch_layer(*, window: timedelta = DEFAULT_WINDOW, view=None,
                around: datetime | None = None, span_deg=None, opener=None,
                should_cancel=None):
    """Fetch reports as an overlay layer, downloading only when needed.

    Returns ``None`` for cancellation and for a window that simply contained no
    reports -- a quiet day is not a failure. Anything else raises
    :class:`StormReportsError`.
    """
    answer = download(window=window, view=view, around=around, opener=opener,
                      should_cancel=should_cancel)
    if answer is None:
        return None
    text, url = answer
    if should_cancel and should_cancel():
        return None
    valid_from = valid_to = None
    if around is not None:
        moment = around if around.tzinfo else around.replace(
            tzinfo=timezone.utc)
        half = max(timedelta(minutes=15), window) / 2
        valid_from = moment - half
        valid_to = moment + half
    return layer_from_reports(
        parse_reports(text), span_deg=span_deg, source_url=url,
        valid_from=valid_from, valid_to=valid_to, around=around,
        window=window)


def _default_opener(url: str, timeout: float, limit: int) -> bytes:
    """Fetch ``url`` over verified HTTPS with a bounded response body.

    Mirrors :mod:`sharpmod.providers.spc_outlook`: the body is capped because this is an
    external service and a redirect to something enormous must not be able to
    exhaust memory.
    """
    import ssl
    import urllib.request

    import certifi

    context = ssl.create_default_context(cafile=certifi.where())
    request = urllib.request.Request(
        url, headers={"User-Agent": "sharpmod storm reports"})
    with urllib.request.urlopen(request, timeout=timeout,
                                context=context) as response:
        return response.read(limit)

"""Time-aware SPC convective outlook overlay source.

Resolves a sounding's valid time to the Storm Prediction Center categorical
convective outlook that actually covers it, fetches the GeoJSON, and decodes
it into a :class:`sharpmod.map_overlays.OverlayLayer` the picker maps draw.

Everything here is Qt-free and runs on a worker thread.

Facts this module encodes, each verified against the live service rather than
recalled, because every one of them changes behaviour:

* **The convective day runs 12Z to 12Z.**  A sounding valid 06Z Tuesday
  belongs to the outlook issued Monday, not Tuesday.  Getting this wrong
  shifts the overlay by a full day for every pre-12Z sounding.
* **Issuance times are fixed per day.**  Day 1 at 0100/1200/1300/1630/2000Z,
  Day 2 at 0600Z (0700Z before ~2021) and 1730Z, Day 3 at 0730Z (0830Z
  earlier) and 1930Z.  Day 1's 0100Z issuance is filed under the *next*
  calendar date while still describing the convective day that began at 12Z.
* **The GeoJSON archive begins in 2020.**  Earlier dates return 404 for every
  issuance.  ERA5 soundings reach back decades, so without this cutoff a
  1998 case study would fire nine futile requests before giving up.
* **Categorical ``DN`` values skip 7**: 2=TSTM, 3=MRGL, 4=SLGT, 5=ENH,
  6=MDT, 8=HIGH.
* **The ``nolyr`` products carry interior rings** that punch out the area
  covered by a higher category.  Filling those instead of the nested ``lyr``
  polygons means each point on the map receives exactly one translucent fill,
  so an Enhanced area is not darkened by the Slight and Marginal areas
  stacked beneath it.

Because the payload itself carries ``stroke``, ``fill``, ``VALID``,
``EXPIRE``, and ``ISSUE``, colours and the validity window are read from the
response.  :data:`CATEGORIES` exists only to order the areas by severity and
to supply a fallback when a field is missing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from sharpmod.map_overlays import (
    MAX_POINTS_PER_LAYER,
    MAX_SHAPES_PER_LAYER,
    OverlayLayer,
    OverlayShape,
    bounds_of,
    build_layer,
    rings_from_geometry,
)

logger = logging.getLogger(__name__)

#: Stable overlay identifier, used as the map's registry key.
OVERLAY_KEY = "spc_outlook"

OVERLAY_TITLE = "SPC convective outlook"
ATTRIBUTION = "NOAA/NWS Storm Prediction Center"

_BASE = "https://www.spc.noaa.gov/products/outlook"

#: The categorical GeoJSON archive starts here; earlier dates 404 everywhere.
ARCHIVE_FIRST_YEAR = 2020

#: SPC convective days are bounded by 12Z, not local midnight.
CONVECTIVE_DAY_START_HOUR = 12

#: Outlook days this module can resolve. Days 4-8 publish no ``_cat`` GeoJSON.
SUPPORTED_DAYS = (1, 2, 3)

#: Approximate envelope the convective outlooks cover, as
#: ``(lon0, lon1, lat0, lat1)``. Generous on purpose -- it only exists so a
#: sounding somewhere the SPC does not forecast skips the request entirely
#: instead of fetching a CONUS outlook whose polygons could never be nearby.
COVERAGE_BOUNDS = (-130.0, -60.0, 20.0, 55.0)

#: Upper bound on candidate URLs tried for one valid time.
MAX_CANDIDATES = 12

#: These payloads are a few kilobytes; the cap only guards a hostile response.
MAX_OUTLOOK_BYTES = 8 * 1024 * 1024

#: Live endpoints are re-read after this long; archived issuances never change.
LIVE_CACHE_TTL_S = 300.0
ARCHIVE_CACHE_TTL_S = 24 * 3600.0
#: A "no outlook exists" answer is remembered too, so scrubbing the date field
#: across a quiet stretch of days does not re-probe SPC on every keystroke.
MISS_CACHE_TTL_S = 900.0
#: A whole-day "nothing here" verdict is kept much longer than an individual
#: 404: it is only recorded once the candidate set is complete, and a date
#: absent from the archive stays absent.
DAY_MISS_CACHE_TTL_S = 6 * 3600.0

#: Entries are a decoded layer or a tombstone, both small. The cap exists to
#: bound growth, not to be reached: scrubbing a date field across a season must
#: not evict the days already resolved, because a re-visit would then re-request
#: every one of them.
_CACHE_MAX_ENTRIES = 512
_DAY_MISS_MAX_ENTRIES = 512


@dataclass(frozen=True)
class OutlookCategory:
    """Severity metadata for one categorical risk level."""

    code: str
    name: str
    rank: int
    stroke: str
    fill: str


#: ``DN`` -> category. Values and colours harvested from the live service.
CATEGORIES: dict[int, OutlookCategory] = {
    2: OutlookCategory("TSTM", "General Thunderstorms", 0, "#55BB55", "#C1E9C1"),
    3: OutlookCategory("MRGL", "Marginal Risk", 1, "#005500", "#66A366"),
    4: OutlookCategory("SLGT", "Slight Risk", 2, "#DDAA00", "#FFE066"),
    5: OutlookCategory("ENH", "Enhanced Risk", 3, "#FF6600", "#FFA366"),
    6: OutlookCategory("MDT", "Moderate Risk", 4, "#CC0000", "#E06666"),
    8: OutlookCategory("HIGH", "High Risk", 5, "#CC00CC", "#EE99EE"),
}

#: The label marking SPC's "significant severe" area, drawn hatched over the
#: probability band it qualifies rather than as another band of its own. SPC
#: retired it operationally on 2026-03-03 in favour of the Conditional Intensity
#: Groups below, but the archive read here reaches back to 2020, so both forms
#: are decoded and an outlook carries one or the other, never both.
SIGNIFICANT_LABEL = "SIGN"

#: Conditional Intensity Groups, which replaced ``SIGN`` from the 1630Z Day 1
#: outlook on 2026-03-03. Where ``SIGN`` was binary, these grade how intense a
#: hazard could become if it occurs: tornado and wind publish three levels,
#: hail and the Day 3 total-severe product two.
#:
#: The label is the only usable key. Every level ships the same grey fill, so
#: colour cannot separate them, and ``DN`` collides with the probability scale --
#: CIG1 is ``DN=2``, which is also the tornado 2% band.
CIG_LABEL_PREFIX = "CIG"
CIG_MAX_LEVEL = 3


def cig_level(label: Any) -> int:
    """Return the Conditional Intensity Group level ``label`` names, else ``0``.

    ``SIGN`` reports ``0`` deliberately. It is a hatch qualifier too, but an
    ungraded one, and reporting it as level 1 would assert an intensity SPC
    never published for it.
    """
    text = str(label or "").strip().upper()
    if not text.startswith(CIG_LABEL_PREFIX):
        return 0
    try:
        level = int(text[len(CIG_LABEL_PREFIX):])
    except ValueError:
        return 0
    return level if 1 <= level <= CIG_MAX_LEVEL else 0


def is_qualifier_label(label: Any) -> bool:
    """Report whether ``label`` names an area qualifying a band beneath it."""
    return (str(label or "").strip().upper() == SIGNIFICANT_LABEL
            or cig_level(label) > 0)


@dataclass(frozen=True)
class OutlookProduct:
    """One selectable outlook product and the outlook days that publish it."""

    key: str
    label: str
    days: tuple[int, ...]
    probabilistic: bool
    #: Compact hazard name for space-constrained labels. Empty for the
    #: categorical product, whose own labels ("SLGT", "MDT") already say what
    #: they are; a bare "5%" does not.
    short: str = ""


#: Selectable products. ``days`` is measured, not assumed: the hazard-specific
#: probabilities are only issued for Days 1 and 2, and requesting them for a
#: Day 3 time returns 404 for every issuance. Day 3 instead publishes one
#: combined total-severe probability, which is offered as its own entry rather
#: than substituted for a hazard the day does not carry -- it is a different
#: quantity, and SPC gives it its own probability-to-category conversion.
PRODUCTS: dict[str, OutlookProduct] = {
    "cat": OutlookProduct("cat", "Categorical risk", (1, 2, 3), False),
    "torn": OutlookProduct("torn", "Tornado probability", (1, 2), True, "TOR"),
    "wind": OutlookProduct("wind", "Wind probability", (1, 2), True, "WIND"),
    "hail": OutlookProduct("hail", "Hail probability", (1, 2), True, "HAIL"),
    "prob": OutlookProduct(
        "prob", "Total severe probability", (3,), True, "SVR"),
}

DEFAULT_PRODUCT = "cat"

#: Fallback colours per product, keyed by ``LABEL`` rather than ``DN``.
#:
#: ``DN`` cannot be the key: in the tornado product ``DN=10`` is used for both
#: the 10% probability band and the significant-severe area. ``LABEL`` is also
#: what distinguishes the scales -- tornado runs 2/5/10/15/30/45/60 while wind
#: and hail run 5/15/30/45/60, and the colours follow position in the scale
#: rather than the numeric value, so 0.15 is amber for wind but red for tornado.
#:
#: The payload carries ``stroke`` and ``fill`` on every feature observed, so
#: these only apply if a field is missing or malformed.
_PRODUCT_PALETTES: dict[str, dict[str, tuple[str, str]]] = {
    "torn": {
        "0.02": ("#005500", "#66A366"),
        "0.05": ("#70380f", "#9d4e15"),
        "0.10": ("#DDAA00", "#FFE066"),
        "0.15": ("#CC0000", "#E06666"),
        "0.30": ("#CC00CC", "#EE99EE"),
        "0.45": ("#a300cc", "#d633ff"),
        "0.60": ("#a300cc", "#d633ff"),
        SIGNIFICANT_LABEL: ("#000000", "#888888"),
    },
    "wind": {
        "0.05": ("#70380f", "#9d4e15"),
        "0.15": ("#DDAA00", "#FFE066"),
        "0.30": ("#CC0000", "#E06666"),
        "0.45": ("#CC00CC", "#EE99EE"),
        "0.60": ("#a300cc", "#d633ff"),
        # Added by SPC on 2026-03-03 alongside the Conditional Intensity
        # Groups. Only reached if a feature omits its own colours; the ranking
        # and the "75%" label derive from the probability itself.
        "0.75": ("#7d00b3", "#c04dff"),
        "0.90": ("#5c0080", "#a366ff"),
        SIGNIFICANT_LABEL: ("#000000", "#888888"),
    },
}
#: Hail shares the wind probability scale and its colours, and the Day 3
#: total-severe probability shares the hail scale: both run 5/15/30/45/60 and
#: convert to categories identically.
_PRODUCT_PALETTES["hail"] = _PRODUCT_PALETTES["wind"]
_PRODUCT_PALETTES["prob"] = _PRODUCT_PALETTES["hail"]

#: Issuance clock times per outlook day. Order here is irrelevant: candidates
#: are sorted chronologically at resolution time, because Day 1's 01Z update is
#: filed under the following date and is therefore the *latest* issuance for its
#: convective day despite having the smallest clock value.
#: Superseded early-era times are included so archived cases still resolve.
_ISSUANCES: dict[int, tuple[tuple[int, int], ...]] = {
    1: ((20, 0), (16, 30), (13, 0), (12, 0), (1, 0)),
    2: ((17, 30), (6, 0), (7, 0)),
    3: ((19, 30), (7, 30), (8, 30)),
}

#: Day 1's 0100Z update is filed under the following calendar date.
_NEXT_DAY_ISSUANCE_HOURS = {1: {1}}


class OutlookError(RuntimeError):
    """Raised when an outlook could not be retrieved or decoded."""


# --------------------------------------------------------------------------- #
# time resolution
# --------------------------------------------------------------------------- #
def convective_day_start(when: datetime) -> datetime:
    """Return the 12Z boundary at or before ``when``.

    ``when`` must be tz-aware; a naive datetime cannot be placed on the 12Z
    grid without inventing a timezone, and guessing would silently return the
    wrong convective day for half of all inputs.
    """
    if when.tzinfo is None:
        raise ValueError("convective_day_start requires a tz-aware datetime")
    base = when.astimezone(timezone.utc)
    start = base.replace(
        hour=CONVECTIVE_DAY_START_HOUR, minute=0, second=0, microsecond=0)
    if base < start:
        start -= timedelta(days=1)
    return start


def outlook_day_offset(valid_time: datetime, now: datetime) -> int:
    """Return how many convective days separate ``valid_time`` from ``now``.

    ``0`` is the convective day in progress, ``1`` tomorrow's, and so on.
    Negative values are past days, for which the Day 1 archive is the best
    available record.
    """
    return (convective_day_start(valid_time)
            - convective_day_start(now)).days


@dataclass(frozen=True)
class OutlookRequest:
    """One resolved candidate URL for a categorical outlook."""

    url: str
    day: int
    issued: datetime | None
    label: str
    live: bool = False


def _archive_url(day: int, issued: datetime, product: str) -> str:
    return (
        f"{_BASE}/archive/{issued:%Y}/day{day}otlk_"
        f"{issued:%Y%m%d}_{issued:%H%M}_{product}.nolyr.geojson"
    )


def _live_url(day: int, product: str) -> str:
    return f"{_BASE}/day{day}otlk_{product}.nolyr.geojson"


def resolve_product(product: str | None) -> OutlookProduct:
    """Return the requested product, falling back to the categorical one."""
    return PRODUCTS.get(product or DEFAULT_PRODUCT, PRODUCTS[DEFAULT_PRODUCT])


def format_product_days(spec: OutlookProduct) -> str:
    """Return a phrase naming the outlook days ``spec`` publishes.

    Single-day products are common enough to matter: the Day 3 total-severe
    probability would otherwise be described as spanning "Days 3-3".
    """
    days = spec.days
    if not days:
        return ""
    if len(days) == 1:
        return f"Day {days[0]}"
    return f"Days {days[0]}\u2013{days[-1]}"


def covers_location(lat: Any, lon: Any) -> bool:
    """Report whether an outlook could plausibly apply near ``lat``/``lon``.

    Used to avoid requesting a CONUS product for a sounding on another
    continent. An unusable coordinate answers ``True`` so a missing or odd
    latitude degrades to attempting the fetch rather than silently suppressing
    the overlay where it would have been wanted.
    """
    try:
        latitude = float(lat)
        longitude = float(lon)
    except (TypeError, ValueError):
        return True
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        return True
    lon0, lon1, lat0, lat1 = COVERAGE_BOUNDS
    longitude = ((longitude + 180.0) % 360.0) - 180.0
    return lon0 <= longitude <= lon1 and lat0 <= latitude <= lat1


def _issuances_for(day: int, day_start: datetime) -> list[datetime]:
    """Return every issuance datetime for ``day`` covering ``day_start``.

    ``day_start`` is the 12Z start of the convective day being described. A
    Day *N* outlook for that window is issued ``N-1`` days earlier.
    """
    base = day_start - timedelta(days=day - 1)
    next_day_hours = _NEXT_DAY_ISSUANCE_HOURS.get(day, set())
    issued: list[datetime] = []
    for hour, minute in _ISSUANCES[day]:
        stamp = base.replace(hour=hour, minute=minute)
        if hour in next_day_hours:
            stamp += timedelta(days=1)
        issued.append(stamp)
    return issued


def candidates_for(
        valid_time: datetime,
        now: datetime | None = None,
        product: str = DEFAULT_PRODUCT,
) -> tuple[OutlookRequest, ...]:
    """Return candidate outlook URLs for ``valid_time``, best first.

    Ordering encodes the forecast-quality preference twice over. Across days,
    the lowest outlook day that has actually been issued wins, since a Day 1 is
    more skilful than the Day 2 it supersedes. Within a day, the winner is the
    newest issuance made *before* ``valid_time`` -- the product a forecaster
    would have had in hand at that hour -- rather than simply the newest one.

    Because an unissued product just 404s, this also handles the future without
    a special case: tomorrow's Day 1 does not exist yet, so the Day 2 issued
    this morning leads. Issuances later than ``now`` are dropped rather than
    requested, which keeps a three-days-out selection to a single round trip.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if valid_time.tzinfo is None:
        raise ValueError("candidates_for requires a tz-aware valid_time")

    day_start = convective_day_start(valid_time)
    if day_start.year < ARCHIVE_FIRST_YEAR:
        return ()

    offset = outlook_day_offset(valid_time, now)
    spec = resolve_product(product)
    requests: list[OutlookRequest] = []

    # The live Day 1 endpoint is the freshest view of the convective day in
    # progress, and using it avoids having to guess which issuance is out yet.
    #
    # Only Day 1 qualifies. Between 00Z and 12Z the live Day 2 and Day 3
    # endpoints have already rolled forward to the next issuance cycle while
    # Day 1 still describes the convective day that began at the previous 12Z,
    # so they sit two convective days ahead of Day 1 rather than one. Offering
    # them here produced a request whose window never matched the target.
    if offset == 0 and 1 in spec.days:
        requests.append(OutlookRequest(
            url=_live_url(1, spec.key),
            day=1,
            issued=None,
            label="Day 1 (current)",
            live=True,
        ))

    for day in SUPPORTED_DAYS:
        if day not in spec.days:
            continue
        issuances = [
            issued for issued in _issuances_for(day, day_start)
            if issued <= now and issued.year >= ARCHIVE_FIRST_YEAR
        ]
        # The outlook in force at a given hour is the newest one issued *before*
        # that hour, so try those first, newest first. Issuances made after the
        # target still follow as a fallback, since their validity window can
        # extend over it -- but preferring them would answer a 06Z sounding with
        # the previous evening's 20Z product instead of the 01Z update.
        issuances.sort(reverse=True)
        ordered = ([issued for issued in issuances if issued <= valid_time]
                   + [issued for issued in issuances if issued > valid_time])
        for issued in ordered:
            requests.append(OutlookRequest(
                url=_archive_url(day, issued, spec.key),
                day=day,
                issued=issued,
                # "issuance" rather than a bare time: every issuance of a
                # convective day shares one expiry, so a bare "2000Z" next to a
                # validity window reads as a valid time and makes a correct
                # answer look like the wrong day.
                label=f"Day {day} \u00b7 {issued:%H%M}Z issuance",
            ))

    return tuple(requests[:MAX_CANDIDATES])


# --------------------------------------------------------------------------- #
# decoding
# --------------------------------------------------------------------------- #
def resolution_signature(
        valid_time: datetime,
        now: datetime | None = None,
        product: str = DEFAULT_PRODUCT,
) -> tuple[str, ...]:
    """Return a token identifying what resolving this request would produce.

    Comparing tokens is how a caller learns that an overlay already on screen is
    no longer the right one, without performing any I/O. Two distinct things can
    make it wrong, and the token has to capture both:

    * **Something better now exists.** As SPC issues each successive outlook the
      candidate list grows, as a target day advances from Day 3 to Day 2 to Day 1
      it changes shape, and a hazard that publishes nothing for Day 3 goes from
      empty to populated the moment the target becomes Day 2. Checking whether
      the displayed outlook still covers the selected time cannot detect any of
      this, because a Day 3 outlook's window still contains the target long after
      the Day 1 for the same day has been issued.

    * **A different issuance now applies.** Moving a forecast hour from 18Z to
      00Z stays inside one convective day and one candidate list, so the same
      products remain available -- but the outlook in force has changed from the
      1630Z issuance to the 2000Z one. This is why the token is ordered rather
      than a set: the ordering is derived from ``valid_time`` and is exactly what
      selects between issuances that are all equally available.
    """
    return tuple(
        request.url for request in candidates_for(valid_time, now, product))


def _parse_stamp(value: Any) -> datetime | None:
    """Decode an SPC ``YYYYMMDDHHMM`` stamp into tz-aware UTC."""
    if not isinstance(value, str) or len(value) != 12 or not value.isdigit():
        return None
    try:
        return datetime.strptime(value, "%Y%m%d%H%M").replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


#: Ranks the hatched significant-severe area above every probability band.
_SIGNIFICANT_RANK = 10_000


def _feature_style(
        product: str,
        dn: int | None,
        label: str,
) -> tuple[str, int, str, str, bool, int]:
    """Return ``(name, rank, stroke, fill, hatch, hatch_level)`` for a feature.

    Ranking is derived from ``LABEL`` rather than ``DN`` so it stays correct for
    both product families: the categorical product orders by severity index,
    while the probabilistic ones order by the probability itself. Ordering by
    ``DN`` would collapse distinct areas onto one level, because SPC reuses the
    field -- the tornado 2% band and CIG1 are both ``DN=2``.
    """
    palette = _PRODUCT_PALETTES.get(product, {})
    level = cig_level(label)
    if level or str(label or "").strip().upper() == SIGNIFICANT_LABEL:
        stroke, fill = palette.get(
            SIGNIFICANT_LABEL, ("#000000", "#888888"))
        name = (f"Conditional Intensity Group {level}" if level
                else "Significant severe")
        # Above every probability band so the qualifier paints on top, and
        # ordered among themselves so the higher group wins at a point both
        # cover and reads on top where their boundaries touch.
        return name, _SIGNIFICANT_RANK + level, stroke, fill, True, level

    if product == "cat":
        category = CATEGORIES.get(dn) if dn is not None else None
        if category is not None:
            return (category.name, category.rank,
                    category.stroke, category.fill, False, 0)
        # An unrecognised category sorts above every known one, so a new
        # severity level is never hidden beneath the areas it supersedes.
        return label or "Unknown", len(CATEGORIES) + (dn or 0), \
            "#FFFFFF", "#FFFFFF", False, 0

    stroke, fill = palette.get(label, ("#FFFFFF", "#FFFFFF"))
    try:
        rank = int(round(float(label) * 1000))
    except (TypeError, ValueError):
        rank = dn or 0
    try:
        name = f"{float(label) * 100:g}% probability"
    except (TypeError, ValueError):
        name = label
    return name, rank, stroke, fill, False, 0


def _display_label(product: str, raw: str) -> str:
    """Return the label shown on a legend swatch or badge.

    SPC publishes a probability as a bare decimal, so ``0.05`` becomes ``5%``:
    the raw form reads as an identifier rather than a quantity, and the swatch
    has no room for the longer description.
    """
    if product == "cat" or is_qualifier_label(raw):
        return raw
    try:
        return f"{float(raw) * 100:g}%"
    except (TypeError, ValueError):
        return raw


def _colour(value: Any, fallback: str) -> str:
    """Return a ``#rrggbb`` colour from the payload, or ``fallback``."""
    if isinstance(value, str):
        text = value.strip()
        if len(text) == 7 and text[0] == "#":
            try:
                int(text[1:], 16)
            except ValueError:
                return fallback
            return text
    return fallback


def parse_outlook(
        payload: bytes | str,
        *,
        source_url: str = "",
        day: int = 1,
        label: str = "",
        product: str = DEFAULT_PRODUCT,
) -> OverlayLayer:
    """Decode one outlook GeoJSON into an overlay layer.

    Unknown ``DN`` values are kept rather than dropped: SPC could add a
    category, and an unrecognised area drawn in its payload colours at the top
    of the severity order is far better than an area silently missing from a
    severe-weather overlay.
    """
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OutlookError(f"outlook payload is not UTF-8: {exc}") from exc
    try:
        document = json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise OutlookError(f"outlook payload is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise OutlookError("outlook payload is not a GeoJSON object")

    features = document.get("features")
    if not isinstance(features, list):
        raise OutlookError("outlook payload has no feature list")

    shapes: list[OverlayShape] = []
    total_points = 0
    truncated = False
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    issued: datetime | None = None

    for feature in features[:MAX_SHAPES_PER_LAYER]:
        if truncated:
            break
        if not isinstance(feature, dict):
            continue
        properties = feature.get("properties")
        properties = properties if isinstance(properties, dict) else {}

        raw_dn = properties.get("DN")
        dn = raw_dn if isinstance(raw_dn, int) else None

        code = str(properties.get("LABEL") or "").strip()
        # A product with no areas is published as a single placeholder feature
        # carrying DN 0, an empty label, and no colours -- not as a 404. Drawing
        # it would put a white shape of nothing on the map.
        if not code and not dn:
            continue

        (default_name, rank, default_stroke, default_fill,
         hatch, hatch_level) = _feature_style(product, dn, code)
        name = str(properties.get("LABEL2") or default_name).strip()
        stroke = _colour(properties.get("stroke"), default_stroke)
        fill = _colour(properties.get("fill"), default_fill)

        valid_from = valid_from or _parse_stamp(properties.get("VALID"))
        valid_to = valid_to or _parse_stamp(properties.get("EXPIRE"))
        issued = issued or _parse_stamp(properties.get("ISSUE"))

        for rings in rings_from_geometry(feature.get("geometry")):
            bounds = bounds_of(rings)
            if bounds is None:
                continue
            points = sum(len(ring) for ring in rings)
            if total_points + points > MAX_POINTS_PER_LAYER:
                logger.warning(
                    "spc_outlook.truncated url=%s points=%d",
                    source_url, total_points)
                truncated = True
                break
            total_points += points
            shapes.append(OverlayShape(
                rings=rings,
                bounds=bounds,
                stroke=stroke,
                fill=fill,
                label=_display_label(product, code),
                description=name,
                rank=rank,
                hatch=hatch,
                hatch_level=hatch_level,
            ))

    subtitle = label
    if valid_from is not None and valid_to is not None:
        prefix = f"{label} \u00b7 " if label else ""
        subtitle = (
            f"{prefix}valid {valid_from:%d %b %H%M}Z"
            f" \u2192 {valid_to:%d %b %H%M}Z"
        )

    spec = resolve_product(product)
    return build_layer(
        OVERLAY_KEY,
        f"{OVERLAY_TITLE} \u2014 Day {day} {spec.label.lower()}",
        shapes,
        short_name=spec.short,
        subtitle=subtitle,
        valid_from=valid_from,
        valid_to=valid_to,
        issued=issued,
        source_url=source_url,
        attribution=ATTRIBUTION,
    )


# --------------------------------------------------------------------------- #
# fetching
# --------------------------------------------------------------------------- #
_CACHE_LOCK = threading.RLock()
#: url -> (expires_at_monotonic, layer or None). ``None`` is a remembered miss.
_CACHE: dict[str, tuple[float, OverlayLayer | None]] = {}
#: frozenset of candidate urls -> expires_at. Records that an entire candidate
#: walk produced nothing, so revisiting that day costs no requests at all.
#:
#: Keyed by the candidate set rather than the date because the set is what
#: determines the answer, and it grows as SPC issues more products. A new
#: issuance therefore produces a different key and re-opens the question by
#: itself, with no staleness window to reason about.
_DAY_MISS: dict[frozenset, float] = {}


def clear_cache() -> None:
    """Drop every cached outlook. Exposed for tests and manual refresh."""
    with _CACHE_LOCK:
        _CACHE.clear()
        _DAY_MISS.clear()


def _prune_locked(store: dict) -> None:
    """Drop expired entries. Caller must hold :data:`_CACHE_LOCK`."""
    now = time.monotonic()
    for key in [k for k, v in store.items()
                if (v[0] if isinstance(v, tuple) else v) < now]:
        store.pop(key, None)


def _day_miss_cached(candidates: tuple[OutlookRequest, ...]) -> bool:
    key = frozenset(request.url for request in candidates)
    with _CACHE_LOCK:
        expires_at = _DAY_MISS.get(key)
        if expires_at is None:
            return False
        if expires_at < time.monotonic():
            _DAY_MISS.pop(key, None)
            return False
        return True


def _remember_day_miss(candidates: tuple[OutlookRequest, ...]) -> None:
    key = frozenset(request.url for request in candidates)
    with _CACHE_LOCK:
        if len(_DAY_MISS) >= _DAY_MISS_MAX_ENTRIES:
            _prune_locked(_DAY_MISS)
        if len(_DAY_MISS) >= _DAY_MISS_MAX_ENTRIES:
            _DAY_MISS.pop(min(_DAY_MISS, key=_DAY_MISS.get), None)
        _DAY_MISS[key] = time.monotonic() + DAY_MISS_CACHE_TTL_S


def _cache_get(url: str) -> tuple[bool, OverlayLayer | None]:
    with _CACHE_LOCK:
        entry = _CACHE.get(url)
        if entry is None:
            return False, None
        expires_at, layer = entry
        if expires_at < time.monotonic():
            _CACHE.pop(url, None)
            return False, None
        return True, layer


def _cache_put(url: str, layer: OverlayLayer | None, ttl: float) -> None:
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX_ENTRIES:
            # Reclaim genuinely expired entries before evicting a live one.
            # Evicting by soonest-expiry alone preferentially discarded the
            # short-TTL 404 tombstones, which are exactly what stops a repeated
            # scrub over a barren stretch of dates from re-probing SPC.
            _prune_locked(_CACHE)
        if len(_CACHE) >= _CACHE_MAX_ENTRIES:
            oldest = min(_CACHE, key=lambda key: _CACHE[key][0])
            _CACHE.pop(oldest, None)
        _CACHE[url] = (time.monotonic() + ttl, layer)


# --------------------------------------------------------------------------- #
# on-disk cache for archived issuances
# --------------------------------------------------------------------------- #
# Only archived issuances are written here, and that is the whole reason this is
# safe: once SPC has published the outlook for a past issuance time, that file
# never changes, so there is no staleness to reason about and no TTL to tune.
# The live endpoint is deliberately excluded because it advances through the
# day, and 404s are excluded because a product missing now may be issued later.
#
# The payloads are small -- a few kilobytes each, so a year of heavy browsing
# across every product is single-digit megabytes -- but the directory is still
# capped by size and age so it cannot grow without bound.
_DISK_DIR_NAME = "outlook-cache"
_DISK_DEFAULT_MB = 32.0
_DISK_DEFAULT_DAYS = 90.0
#: Sweeping on every write would walk the directory constantly for no benefit.
_DISK_SWEEP_INTERVAL = 64

_disk_writes_since_sweep = _DISK_SWEEP_INTERVAL  # sweep on first write


def _env_number(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def disk_cache_root() -> Path | None:
    """Return the on-disk cache directory, or ``None`` when disabled.

    Mirrors :func:`sharpmod.model_disk_cache.default_model_cache_root` so the
    application keeps one cache location per platform, and honours
    ``SHARPMOD_OUTLOOK_CACHE=off`` for environments that must stay stateless.
    """
    explicit = os.environ.get("SHARPMOD_OUTLOOK_CACHE")
    if explicit:
        if explicit.strip().casefold() in {"off", "0", "none", "false"}:
            return None
        return Path(explicit).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "sharpmod" / _DISK_DIR_NAME
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base).expanduser() / "sharpmod" / _DISK_DIR_NAME
    return Path.home() / ".cache" / "sharpmod" / _DISK_DIR_NAME


def _disk_path(root: Path, url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    # Shard by the first byte so one flat directory never holds every entry.
    return root / digest[:2] / f"{digest}.json"


def _disk_read(url: str) -> bytes | None:
    root = disk_cache_root()
    if root is None:
        return None
    try:
        path = _disk_path(root, url)
        if not path.is_file():
            return None
        payload = path.read_bytes()
    except OSError:
        return None
    if not payload or len(payload) > MAX_OUTLOOK_BYTES:
        return None
    try:
        # Touch so the size sweep evicts genuinely unused entries rather than
        # ones that are simply old but still being read.
        os.utime(path, None)
    except OSError:
        pass
    return payload


def _disk_write(url: str, payload: bytes) -> None:
    global _disk_writes_since_sweep
    root = disk_cache_root()
    if root is None or not payload or len(payload) > MAX_OUTLOOK_BYTES:
        return
    path = _disk_path(root, url)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write via a sibling temporary file so a crash or a concurrent reader
        # never observes a half-written outlook.
        temporary = path.with_suffix(f".{os.getpid()}.part")
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    except OSError as exc:
        logger.debug("spc_outlook.disk_write_failed url=%s err=%s", url, exc)
        return

    _disk_writes_since_sweep += 1
    if _disk_writes_since_sweep >= _DISK_SWEEP_INTERVAL:
        _disk_writes_since_sweep = 0
        _disk_sweep()


def _disk_sweep() -> None:
    """Drop entries past the age limit, then oldest-first past the size limit."""
    root = disk_cache_root()
    if root is None or not root.is_dir():
        return
    max_bytes = int(_env_number("SHARPMOD_OUTLOOK_CACHE_MB",
                                _DISK_DEFAULT_MB) * 1024 * 1024)
    max_age_s = _env_number("SHARPMOD_OUTLOOK_CACHE_DAYS",
                            _DISK_DEFAULT_DAYS) * 86400.0
    cutoff = time.time() - max_age_s

    entries: list[tuple[float, int, Path]] = []
    try:
        for path in root.rglob("*.json"):
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime < cutoff:
                path.unlink(missing_ok=True)
                continue
            entries.append((stat.st_mtime, stat.st_size, path))
    except OSError:
        return

    total = sum(size for _mtime, size, _path in entries)
    if total <= max_bytes:
        return
    for _mtime, size, path in sorted(entries):
        if total <= max_bytes:
            break
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue
        total -= size


def clear_disk_cache() -> None:
    """Remove every persisted outlook. Exposed for tests and manual refresh."""
    root = disk_cache_root()
    if root is None or not root.is_dir():
        return
    try:
        for path in root.rglob("*.json"):
            path.unlink(missing_ok=True)
    except OSError:
        pass


def _ssl_context() -> ssl.SSLContext:
    """Return a certificate-verifying context, preferring the bundled roots.

    ``certifi`` is a hard dependency of the remote decoders, but the overlay is
    an optional embellishment: if it is somehow unavailable the system trust
    store still gives a verified connection, whereas raising here would take
    the map down with it.
    """
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())


def _default_opener(url: str, timeout: float, limit: int) -> bytes:
    """Fetch ``url`` over verified HTTPS with a bounded response body."""
    with urllib.request.urlopen(
            url, timeout=timeout, context=_ssl_context()) as resp:
        body = resp.read(limit + 1)
    if len(body) > limit:
        raise OutlookError(
            f"outlook response exceeds the {limit:,}-byte safety limit")
    return body


def _fetch_bytes(url: str, opener: Callable[..., bytes] | None) -> bytes:
    """Fetch one URL, honouring the project-wide remote-IO limits."""
    from sharpmod.io import decoder as _decoder  # local: keeps this module light

    timeout = _decoder._remote_timeout()  # noqa: SLF001
    limit = min(_decoder._max_remote_bytes(), MAX_OUTLOOK_BYTES)  # noqa: SLF001
    return (opener or _default_opener)(url, timeout, limit)


def fetch_layer(
        valid_time: datetime,
        *,
        now: datetime | None = None,
        product: str = DEFAULT_PRODUCT,
        opener: Callable[..., bytes] | None = None,
        should_cancel: Callable[[], bool] | None = None,
) -> OverlayLayer | None:
    """Return the outlook layer covering ``valid_time``, or ``None``.

    ``None`` means "SPC published nothing that covers this time" -- before the
    2020 archive, beyond Day 3, or a quiet day with no outlook on file. That is
    an ordinary answer, not an error, and it is cached so repeated scrubbing
    over such a period stays local.

    A candidate is accepted only once its own ``VALID``/``EXPIRE`` window is
    confirmed to contain ``valid_time``.  The live endpoints in particular
    advance without warning, so trusting the URL alone would eventually draw
    yesterday's outlook over today's sounding.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    candidates = candidates_for(valid_time, now, product)
    if not candidates:
        return None
    if _day_miss_cached(candidates):
        # This exact candidate set has already been walked to exhaustion.
        return None

    fallback: OverlayLayer | None = None
    exhausted = True
    for request in candidates:
        if should_cancel is not None and should_cancel():
            return None

        hit, cached = _cache_get(request.url)
        if hit:
            if cached is None:
                continue
            if cached.covers(valid_time):
                return cached
            fallback = fallback or cached
            continue

        # An archived issuance is immutable, so a previous session's copy is
        # as good as a fresh request and costs no network at all.
        from_disk = None if request.live else _disk_read(request.url)
        if from_disk is not None:
            try:
                layer = parse_outlook(
                    from_disk, source_url=request.url, day=request.day,
                    label=request.label, product=product)
            except OutlookError:
                layer = None
            if layer:
                _cache_put(request.url, layer, ARCHIVE_CACHE_TTL_S)
                if layer.covers(valid_time):
                    return layer
                fallback = fallback or layer
                continue

        try:
            payload = _fetch_bytes(request.url, opener)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # Not yet issued, or never existed. Remember it and move on.
                _cache_put(request.url, None, MISS_CACHE_TTL_S)
                continue
            # A server-side error says nothing about whether the product
            # exists, so this walk must not be recorded as a settled "nothing
            # here" verdict that suppresses retries for hours.
            exhausted = False
            logger.debug(
                "spc_outlook.http_error url=%s code=%s", request.url, exc.code)
            continue
        except (urllib.error.URLError, OSError, ValueError, OutlookError) as exc:
            exhausted = False
            logger.debug("spc_outlook.fetch_failed url=%s err=%s",
                         request.url, exc)
            continue

        try:
            layer = parse_outlook(
                payload,
                source_url=request.url,
                day=request.day,
                label=request.label,
                product=product,
            )
        except OutlookError as exc:
            logger.debug("spc_outlook.decode_failed url=%s err=%s",
                         request.url, exc)
            _cache_put(request.url, None, MISS_CACHE_TTL_S)
            continue

        ttl = LIVE_CACHE_TTL_S if request.live else ARCHIVE_CACHE_TTL_S
        _cache_put(request.url, layer if layer else None, ttl)
        if layer and not request.live:
            _disk_write(request.url, payload)

        if not layer:
            continue
        if layer.covers(valid_time):
            return layer
        # A real product that does not cover the target: keep it only as a last
        # resort and keep looking for one that does.
        fallback = fallback or layer

    if fallback is not None:
        logger.debug(
            "spc_outlook.window_mismatch valid=%s using=%s",
            valid_time, fallback.source_url)
        return fallback
    if exhausted:
        # Every candidate was reachable and none of them held an outlook, so
        # record the verdict once for the whole set instead of relying on the
        # individual 404 tombstones surviving in the per-URL cache.
        _remember_day_miss(candidates)
    return None

"""Time-aware SPC convective outlook overlay source.

Resolves a sounding's valid time to the Storm Prediction Center categorical
convective outlook that actually covers it, fetches the GeoJSON, and decodes
it into a :class:`sharpmod.maps.map_overlays.OverlayLayer` the picker maps draw.

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sharpmod.maps.map_overlays import (
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
    import sharpmod_rs

    return sharpmod_rs.spc_cig_level(str(label or ""))


def is_qualifier_label(label: Any) -> bool:
    """Report whether ``label`` names an area qualifying a band beneath it."""
    import sharpmod_rs

    return sharpmod_rs.spc_is_qualifier_label(str(label or ""))


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


class OutlookError(RuntimeError):
    """Raised when an outlook could not be retrieved or decoded."""


# --------------------------------------------------------------------------- #
# time resolution
# --------------------------------------------------------------------------- #
def _utc_epoch(when: datetime) -> int:
    """Whole UTC epoch seconds for a tz-aware ``when``.

    ``floor`` keeps a sub-second instant in the second it occupies, so the
    native 12Z grid arithmetic sees exactly the calendar Python's own
    datetime comparisons would.
    """
    return math.floor(when.astimezone(timezone.utc).timestamp())


def convective_day_start(when: datetime) -> datetime:
    """Return the 12Z boundary at or before ``when``.

    ``when`` must be tz-aware; a naive datetime cannot be placed on the 12Z
    grid without inventing a timezone, and guessing would silently return the
    wrong convective day for half of all inputs.
    """
    if when.tzinfo is None:
        raise ValueError("convective_day_start requires a tz-aware datetime")
    import sharpmod_rs

    return datetime.fromtimestamp(
        sharpmod_rs.spc_convective_day_start_epoch(_utc_epoch(when)),
        tz=timezone.utc)


def outlook_day_offset(valid_time: datetime, now: datetime) -> int:
    """Return how many convective days separate ``valid_time`` from ``now``.

    ``0`` is the convective day in progress, ``1`` tomorrow's, and so on.
    Negative values are past days, for which the Day 1 archive is the best
    available record.
    """
    if valid_time.tzinfo is None or now.tzinfo is None:
        raise ValueError("convective_day_start requires a tz-aware datetime")
    import sharpmod_rs

    return sharpmod_rs.spc_outlook_day_offset(
        _utc_epoch(valid_time), _utc_epoch(now))


@dataclass(frozen=True)
class OutlookRequest:
    """One resolved candidate URL for a categorical outlook."""

    url: str
    day: int
    issued: datetime | None
    label: str
    live: bool = False


def resolve_product(product: str | None) -> OutlookProduct:
    """Return the requested product, falling back to the categorical one."""
    return PRODUCTS.get(product or DEFAULT_PRODUCT, PRODUCTS[DEFAULT_PRODUCT])


def product_publishes(
        valid_time: datetime | None,
        product: str | None,
        *,
        now: datetime | None = None,
) -> bool:
    """Report whether ``product`` publishes any outlook covering ``valid_time``.

    Pure and network-free: it only asks whether :func:`candidates_for` can name a
    URL. Lets a caller choose a different product *before* fetching, rather than
    discovering the gap as an empty result -- the hazard probabilities cover only
    Days 1-2 and the total-severe probability only Day 3, so a caller carrying a
    user's hazard preference onto an arbitrary valid time needs to know when that
    preference cannot be honoured.
    """
    if not isinstance(valid_time, datetime):
        return False
    try:
        return bool(candidates_for(valid_time, now, product or DEFAULT_PRODUCT))
    except (TypeError, ValueError):
        return False


def format_product_days(spec: OutlookProduct) -> str:
    """Return a phrase naming the outlook days ``spec`` publishes.

    Single-day products are common enough to matter: the Day 3 total-severe
    probability would otherwise be described as spanning "Days 3-3".
    """
    import sharpmod_rs

    return sharpmod_rs.spc_format_product_days(list(spec.days))


def covers_location(lat: Any, lon: Any) -> bool:
    """Report whether an outlook could plausibly apply near ``lat``/``lon``.

    Used to avoid requesting a CONUS product for a sounding on another
    continent. An unusable coordinate answers ``True`` so a missing or odd
    latitude degrades to attempting the fetch rather than silently suppressing
    the overlay where it would have been wanted.
    """
    try:
        latitude = float(lat)
    except (TypeError, ValueError):
        latitude = None
    try:
        longitude = float(lon)
    except (TypeError, ValueError):
        longitude = None
    import sharpmod_rs

    return sharpmod_rs.spc_covers_location(latitude, longitude)


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
    if now.tzinfo is None:
        raise ValueError("convective_day_start requires a tz-aware datetime")
    import sharpmod_rs

    rows = sharpmod_rs.spc_candidates_for(
        _utc_epoch(valid_time), _utc_epoch(now), product)
    return tuple(
        OutlookRequest(
            url=url,
            day=day,
            issued=(datetime.fromtimestamp(issued, tz=timezone.utc)
                    if issued is not None else None),
            label=label,
            live=live,
        )
        for url, day, issued, label, live in rows
    )


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
    if not isinstance(value, str):
        return None
    import sharpmod_rs

    epoch = sharpmod_rs.spc_parse_stamp_epoch(value)
    return (datetime.fromtimestamp(epoch, tz=timezone.utc)
            if epoch is not None else None)


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
    import sharpmod_rs

    return sharpmod_rs.spc_feature_style(product, dn, label)


def _display_label(product: str, raw: str) -> str:
    """Return the label shown on a legend swatch or badge.

    SPC publishes a probability as a bare decimal, so ``0.05`` becomes ``5%``:
    the raw form reads as an identifier rather than a quantity, and the swatch
    has no room for the longer description.
    """
    import sharpmod_rs

    return sharpmod_rs.spc_display_label(product, raw)


def _colour(value: Any, fallback: str) -> str:
    """Return a ``#rrggbb`` colour from the payload, or ``fallback``."""
    import sharpmod_rs

    return sharpmod_rs.spc_validate_colour(
        value if isinstance(value, str) else None, fallback)


def _layer_from_native(payload: dict[str, Any]) -> OverlayLayer:
    def parse_iso(value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    shapes = tuple(
        OverlayShape(
            rings=tuple(
                tuple((float(lon), float(lat)) for lon, lat in ring)
                for ring in area["rings"]
            ),
            bounds=tuple(float(v) for v in area["bounds"]),
            stroke=area["stroke"],
            fill=area["fill"],
            label=area["label"],
            description=area["description"],
            rank=int(area["rank"]),
            hatch=bool(area["hatch"]),
            hatch_level=int(area["hatch_level"]),
        )
        for area in payload.get("areas", ())
    )
    raw_bounds = payload.get("bounds")
    return OverlayLayer(
        key=payload["key"],
        title=payload["title"],
        shapes=shapes,
        short_name=payload["short_name"],
        subtitle=payload["subtitle"],
        valid_from=parse_iso(payload.get("valid_from")),
        valid_to=parse_iso(payload.get("valid_to")),
        issued=parse_iso(payload.get("issued")),
        source_url=payload["source_url"],
        attribution=payload["attribution"],
        bounds=tuple(float(v) for v in raw_bounds) if raw_bounds else None,
        affected_states=(
            tuple(payload["affected_states"]) if "affected_states" in payload else None
        ),
    )


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
    import sharpmod_rs

    try:
        raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
        parsed = json.loads(
            sharpmod_rs.spc_outlook_parse(
                raw,
                source_url=source_url,
                day=int(day),
                label=label,
                product=product,
            )
        )
    except (ValueError, TypeError) as exc:
        raise OutlookError(str(exc)) from exc
    return _layer_from_native(parsed)


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
    import sharpmod_rs

    with _CACHE_LOCK:
        _CACHE.clear()
        _DAY_MISS.clear()
    sharpmod_rs.spc_outlook_clear_cache()


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


def _remember_day_miss(
        candidates: tuple[OutlookRequest, ...],
        ttl: float = DAY_MISS_CACHE_TTL_S,
) -> None:
    key = frozenset(request.url for request in candidates)
    with _CACHE_LOCK:
        if len(_DAY_MISS) >= _DAY_MISS_MAX_ENTRIES:
            _prune_locked(_DAY_MISS)
        if len(_DAY_MISS) >= _DAY_MISS_MAX_ENTRIES:
            _DAY_MISS.pop(min(_DAY_MISS, key=_DAY_MISS.get), None)
        _DAY_MISS[key] = time.monotonic() + ttl


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

    Mirrors :func:`sharpmod.models.model_disk_cache.default_model_cache_root` so the
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


from sharpmod.providers.spc_outlook_fetch import (  # noqa: E402
    fetch_layer
)

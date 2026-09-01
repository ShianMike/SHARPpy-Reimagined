"""Live radar mosaic overlay, from NOAA's MRMS products via NCEP GeoServer.

Fetches a georeferenced radar image and wraps it in a
:class:`sharpmod.map_overlays.OverlayRaster` the picker maps draw beneath the
vector overlays.

Everything here is Qt-free and runs on a worker thread. The payload stays
encoded; decoding it is the paint path's job.

Facts this module encodes, each read from the live ``GetCapabilities`` document
rather than recalled, because every one of them changes behaviour:

* **The service advertises both ``CRS:84`` and ``EPSG:4326``, and they disagree
  about axis order.**  The capabilities document lists, for the same layer,
  ``CRS:84`` as ``minx=-130 miny=20`` but ``EPSG:4326`` as ``minx=20
  miny=-130``: WMS 1.3.0 restored the authority-defined latitude-first order for
  EPSG:4326.  This module therefore asks for ``CRS:84``, which is unambiguously
  longitude-first.  :mod:`sharpmod.eccc_geomet` documents the same trap for its
  ``GetFeatureInfo`` route, where it handles it by swapping the values instead.
* **A WMS failure arrives as XML with HTTP 200.**  GeoServer reports errors as a
  ``ServiceException`` body, so the status code cannot be trusted and the
  payload has to identify itself.  :func:`map_overlays.looks_like_image` gates
  every frame, and the exception text is lifted into the raised error.
* **The mosaics update about every two minutes**, not on a tidy schedule: the
  advertised time dimension steps 03:02:40, 03:04:43, 03:06:43, 03:08:39.
  Nothing here may assume a fixed offset from the wall clock.
* **Coverage is CONUS only**, ``-130`` to ``-60`` longitude and ``20`` to ``55``
  latitude.  This application places soundings worldwide, so a viewport outside
  that envelope must skip the request rather than fetch a frame that could not
  intersect it.

The frame is requested at a **fixed geographic extent**, never at the map's
current viewport. That is the central design decision here and it buys three
things. One request serves all four picker maps whichever way each is panned;
panning and zooming cost nothing because the image's corners are simply
re-projected; and because the extent is constant, a frame stays glued to the
basemap during the wheel-zoom preview that :meth:`gui_maps.StationMapWidget.
_draw_basemap` performs, with no equivalent of that method's affine fix-up. The
cost is fixed resolution -- see :data:`DEFAULT_FRAME_SIZE`.
"""

from __future__ import annotations

import logging
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from sharpmod.map_overlays import (
    MAX_RASTER_BYTES,
    OverlayRaster,
    looks_like_image,
)

logger = logging.getLogger(__name__)

#: Stable overlay identifier, used as the map's registry key. One key for every
#: product: the radar overlay is a single slot the user retargets, not three
#: independently toggleable layers, so selecting echo tops replaces
#: reflectivity rather than stacking a second translucent image on top of it.
OVERLAY_KEY = "radar_mosaic"

OVERLAY_TITLE = "Live radar"

#: MRMS is a NOAA/NSSL product served through NCEP's public GeoServer. US
#: government work, so this is a credit rather than a licence condition, but the
#: map legend shows it for the same reason every other source is named.
ATTRIBUTION = "NOAA/NWS MRMS via NCEP GeoServer"

WMS_URL = "https://opengeo.ncep.noaa.gov/geoserver/conus/wms"
WMS_VERSION = "1.3.0"

#: Longitude-first CRS. See the module docstring: asking for ``EPSG:4326`` here
#: would silently transpose the bounding box.
WMS_CRS = "CRS:84"

WMS_FORMAT = "image/png"

#: Geographic extent every frame is requested at, ``(lon0, lon1, lat0, lat1)``,
#: matching the tuple order :data:`sharpmod.spc_outlook.COVERAGE_BOUNDS` uses.
#:
#: Rounded outward from the advertised envelope, which ends at
#: ``-60.0000015646219`` east and ``20.000000782311`` south. The excess is under
#: a ten-thousandth of one pixel and comes back transparent; in exchange the
#: extent is exactly 70 by 35 degrees, so the frame is exactly 2:1 and needs no
#: awkward pixel dimensions.
COVERAGE_BOUNDS = (-130.0, -60.0, 20.0, 55.0)

#: Frame size in pixels, matching the 2:1 extent.
#:
#: MRMS is a 1 km grid and this is the resolution the frame is resampled to, so
#: it sets how sharp the overlay can ever be. 35 degrees of latitude across 2048
#: pixels works out at 3.8 km per pixel -- nearly four times coarser than the
#: source -- which reads as visibly soft as soon as the map is zoomed past a
#: continental view. 4096 halves that to 1.9 km per pixel for about 1.1 MB per
#: frame, which is affordable at one request every two minutes shared across
#: every map.
#:
#: Going further is the wrong lever: reaching the native 1 km would need roughly
#: 7800 pixels of width. Sharpness beyond this comes from
#: :func:`viewport_frame_size`, which spends the pixel budget on the area being
#: looked at instead of on the whole continent.
DEFAULT_FRAME_SIZE = (4096, 2048)

#: Bounds on a caller-supplied frame size. The upper bound is what keeps a
#: request from asking the shared public service for something enormous.
MIN_FRAME_PIXELS = 256
MAX_FRAME_PIXELS = 4096

#: These frames are a few hundred kilobytes; the cap only guards a hostile body.
MAX_FRAME_BYTES = min(8 * 1024 * 1024, MAX_RASTER_BYTES)

#: How long a fetched frame is reused. Deliberately just under the roughly
#: two-minute publication cadence, so a frame is never held across a full
#: update, while four maps enabling the overlay together still cost one request.
FRAME_CACHE_TTL_S = 100.0

#: A failure is remembered briefly so a service outage does not turn a repeating
#: refresh timer into a tight retry loop against a struggling server.
FAILURE_CACHE_TTL_S = 60.0

#: Bounded because entries are keyed by product and frame size, of which there
#: are only a handful; this exists to make growth impossible, not to be reached.
_CACHE_MAX_ENTRIES = 16


class RadarError(Exception):
    """Raised when a radar frame cannot be fetched or is not an image."""


@dataclass(frozen=True)
class RadarProduct:
    """One selectable MRMS raster product.

    ``update_interval_s`` is nominal. It drives staleness reporting and the
    refresh cadence, not any assumption about when a frame becomes available.
    """

    key: str
    #: GeoServer layer name, exactly as advertised.
    layer: str
    label: str
    description: str
    #: Named GeoServer style. Empty asks for the layer's default.
    style: str = ""
    update_interval_s: float = 120.0
    #: Units the pixel values represent, for the legend.
    units: str = ""

    @property
    def legend_url(self) -> str:
        """URL of the service's own colour ramp for this product.

        The scale is the service's to define, so rendering our own would risk
        showing a key that does not match the pixels.
        """
        query = urllib.parse.urlencode({
            "service": "WMS",
            "version": WMS_VERSION,
            "request": "GetLegendGraphic",
            "format": WMS_FORMAT,
            "width": "500",
            "height": "30",
            "layer": self.layer,
        })
        return "%s?%s" % (WMS_URL, query)


#: The products this module offers, all verified present in the live
#: capabilities document for the ``conus`` workspace.
PRODUCTS: dict[str, RadarProduct] = {
    "composite-reflectivity": RadarProduct(
        key="composite-reflectivity",
        layer="conus_cref_qcd",
        label="Composite reflectivity",
        description=(
            "Quality-controlled 1 km CONUS composite reflectivity, the maximum "
            "returned power in any tilt above each point"
        ),
        style="radar_reflectivity",
        units="dBZ",
    ),
    "base-reflectivity": RadarProduct(
        key="base-reflectivity",
        layer="conus_bref_qcd",
        label="Base reflectivity",
        description=(
            "Quality-controlled 1 km CONUS base reflectivity, the lowest tilt "
            "only, which is what most public radar displays show"
        ),
        style="radar_reflectivity",
        units="dBZ",
    ),
    "echo-tops": RadarProduct(
        key="echo-tops",
        layer="conus_neet_v18",
        label="Enhanced echo tops",
        description="1 km CONUS enhanced echo tops, the height of the echo top",
        style="radar_echo_tops",
        units="kft",
    ),
}

#: Composite reflectivity is the default because it is the product that answers
#: "how deep is this storm", which is the question a sounding is being drawn to
#: investigate. Base reflectivity would under-represent elevated cores.
DEFAULT_PRODUCT = "composite-reflectivity"


def available_products() -> tuple[RadarProduct, ...]:
    """Return every selectable product, in presentation order."""
    return tuple(PRODUCTS.values())


def get_product(product: str | RadarProduct | None = None) -> RadarProduct:
    """Resolve a product key to a :class:`RadarProduct`.

    Accepts an already-resolved product so callers can pass either without
    branching. ``None`` yields the default.
    """
    if isinstance(product, RadarProduct):
        return product
    key = str(product or DEFAULT_PRODUCT).strip().lower()
    try:
        return PRODUCTS[key]
    except KeyError:
        raise KeyError("unknown radar product %r" % (product,)) from None


def covers(view: tuple[float, float, float, float] | None) -> bool:
    """Report whether a ``(lon0, lon1, lat0, lat1)`` view meets CONUS coverage.

    The caller uses this to skip the request entirely. A map showing Europe can
    never display a CONUS mosaic, and spending a request to discover that would
    be indefensible on a shared public service.
    """
    if view is None:
        return False
    try:
        lon0, lon1, lat0, lat1 = (float(value) for value in view)
    except (TypeError, ValueError):
        return False
    min_lon, max_lon, min_lat, max_lat = COVERAGE_BOUNDS
    if max(lon0, lon1) < min_lon or min(lon0, lon1) > max_lon:
        return False
    if max(lat0, lat1) < min_lat or min(lat0, lat1) > max_lat:
        return False
    return True


def _frame_size(size: tuple[int, int] | None) -> tuple[int, int]:
    """Validate and clamp a requested frame size."""
    if size is None:
        return DEFAULT_FRAME_SIZE
    try:
        width, height = (int(value) for value in size)
    except (TypeError, ValueError):
        raise ValueError("frame size must be two integers") from None
    width = min(MAX_FRAME_PIXELS, max(MIN_FRAME_PIXELS, width))
    height = min(MAX_FRAME_PIXELS, max(MIN_FRAME_PIXELS, height))
    return width, height


def build_query(
        product: str | RadarProduct | None = None,
        *,
        size: tuple[int, int] | None = None,
        transparent: bool = True,
) -> dict[str, str]:
    """Build one WMS 1.3.0 ``GetMap`` query for a full-extent radar frame.

    No ``TIME`` parameter is sent. Omitting it selects the layer's advertised
    default, which is the newest frame -- the thing a live overlay wants. Naming
    an explicit time would require first fetching the capabilities document to
    learn which times exist, doubling the request count per refresh to gain a
    label the overlay can convey honestly from its retrieval time instead.
    """
    resolved = get_product(product)
    width, height = _frame_size(size)
    lon0, lon1, lat0, lat1 = COVERAGE_BOUNDS
    return {
        "SERVICE": "WMS",
        "VERSION": WMS_VERSION,
        "REQUEST": "GetMap",
        "LAYERS": resolved.layer,
        "STYLES": resolved.style,
        "CRS": WMS_CRS,
        # CRS:84 is longitude-first, so this is min_lon,min_lat,max_lon,max_lat.
        "BBOX": "%.6f,%.6f,%.6f,%.6f" % (lon0, lat0, lon1, lat1),
        "WIDTH": str(width),
        "HEIGHT": str(height),
        "FORMAT": WMS_FORMAT,
        "TRANSPARENT": "TRUE" if transparent else "FALSE",
    }


def frame_url(
        product: str | RadarProduct | None = None,
        *,
        size: tuple[int, int] | None = None,
        transparent: bool = True,
) -> str:
    """Return the full ``GetMap`` URL for a radar frame."""
    query = build_query(product, size=size, transparent=transparent)
    return "%s?%s" % (WMS_URL, urllib.parse.urlencode(query))


# --------------------------------------------------------------------------- #
# transport
# --------------------------------------------------------------------------- #
#: Matches the exception text GeoServer returns in place of an image.
#:
#: The element name must be followed by whitespace or the closing angle bracket.
#: Without that guard this also matches the enclosing
#: ``<ServiceExceptionReport>`` wrapper, and the captured "message" then begins
#: with the real element's own opening tag.
_SERVICE_EXCEPTION = re.compile(
    rb"<ServiceException(?:\s[^>]*)?>(.*?)</ServiceException\s*>",
    re.DOTALL | re.IGNORECASE,
)

#: How much of a non-image body is inspected for an explanation. The body could
#: be anything, so this is read as a bounded prefix rather than in full.
_EXCEPTION_SCAN_BYTES = 4096


def _service_exception_text(payload: bytes) -> str:
    """Return the ``ServiceException`` message in ``payload``, if any."""
    match = _SERVICE_EXCEPTION.search(payload[:_EXCEPTION_SCAN_BYTES])
    if match is None:
        return ""
    try:
        text = match.group(1).decode("utf-8", "replace")
    except Exception:  # pragma: no cover - decode with replace cannot raise
        return ""
    # Collapse the whitespace GeoServer pretty-prints into the element.
    return " ".join(text.split())[:300]


def _ssl_context() -> ssl.SSLContext:
    """Return a certificate-verifying context, preferring the bundled roots.

    Mirrors :func:`sharpmod.spc_outlook._ssl_context`: the overlay is optional,
    so a missing ``certifi`` degrades to the system trust store rather than
    taking the map down.
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
        raise RadarError(
            "radar frame exceeds the %s-byte safety limit" % format(limit, ","))
    return body


def _fetch_bytes(url: str, opener: Callable[..., bytes] | None) -> bytes:
    """Fetch one URL, honouring the project-wide remote-IO limits.

    The ``opener`` signature matches :mod:`sharpmod.spc_outlook` exactly so the
    same kind of test double works for both, and so image-ness is decided by the
    payload's own magic bytes rather than a header a caller would have to relay.
    """
    from sharpmod.io import decoder as _decoder  # local: keeps this module light

    timeout = _decoder._remote_timeout()  # noqa: SLF001
    limit = min(_decoder._max_remote_bytes(), MAX_FRAME_BYTES)  # noqa: SLF001
    return (opener or _default_opener)(url, timeout, limit)


# --------------------------------------------------------------------------- #
# cache
# --------------------------------------------------------------------------- #
#: ``{cache_key: (expires_at, raster_or_None)}``. A ``None`` payload is a
#: short-lived failure tombstone.
#:
#: There is no disk cache, and that is deliberate rather than unfinished.
#: :mod:`sharpmod.spc_outlook` persists only immutable archived products and
#: explicitly excludes live endpoints; a radar frame is the purest example of
#: that exclusion, being superseded within two minutes and worthless on the next
#: launch.
_CACHE: dict[tuple, tuple[float, OverlayRaster | None]] = {}
_CACHE_LOCK = threading.Lock()


def _cache_key(product: RadarProduct, size: tuple[int, int]) -> tuple:
    """Return the cache key for a frame request.

    Note what is absent: the viewport. Frames are requested at a fixed extent,
    so every map shares one entry however each is panned or zoomed.
    """
    return (product.layer, product.style, size)


def _cache_get(key: tuple) -> tuple[bool, OverlayRaster | None]:
    """Return ``(hit, raster)``; ``hit`` distinguishes a miss from a tombstone."""
    now = time.monotonic()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return False, None
        expires_at, raster = entry
        if expires_at <= now:
            _CACHE.pop(key, None)
            return False, None
        return True, raster


def _cache_put(key: tuple, raster: OverlayRaster | None, ttl: float) -> None:
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX_ENTRIES:
            # Evict whatever expires soonest; with a handful of possible keys
            # this is bookkeeping rather than a policy that will ever matter.
            oldest = min(_CACHE, key=lambda item: _CACHE[item][0], default=None)
            if oldest is not None:
                _CACHE.pop(oldest, None)
        _CACHE[key] = (time.monotonic() + float(ttl), raster)


def clear_cache() -> None:
    """Drop every cached frame. Exposed for tests and manual refresh."""
    with _CACHE_LOCK:
        _CACHE.clear()


def cached_frame(
        product: str | RadarProduct | None = None,
        *,
        size: tuple[int, int] | None = None,
) -> OverlayRaster | None:
    """Return a still-fresh cached frame without touching the network."""
    hit, raster = _cache_get(_cache_key(get_product(product), _frame_size(size)))
    return raster if hit else None


# --------------------------------------------------------------------------- #
# fetch
# --------------------------------------------------------------------------- #
def fetch_frame(
        product: str | RadarProduct | None = None,
        *,
        size: tuple[int, int] | None = None,
        opacity: float = 1.0,
        now: datetime | None = None,
        opener: Callable[..., bytes] | None = None,
        should_cancel: Callable[[], bool] | None = None,
) -> OverlayRaster | None:
    """Return the newest radar frame as an :class:`OverlayRaster`.

    ``None`` means the fetch was cancelled, which is an ordinary answer when the
    user turns the overlay off mid-flight. Every other failure raises
    :class:`RadarError`, so a caller can tell "you stopped me" from "the service
    is broken" and report only the second.

    A cached frame is returned without a request. A recent failure is also
    remembered, so a repeating refresh timer cannot become a retry loop against
    a service that is already struggling.
    """
    resolved = get_product(product)
    frame_size = _frame_size(size)
    key = _cache_key(resolved, frame_size)

    hit, cached = _cache_get(key)
    if hit:
        if cached is None:
            raise RadarError(
                "%s was unavailable moments ago; not retrying yet"
                % resolved.label)
        # Opacity is a display property, so a differing request re-wraps the
        # cached bytes rather than re-fetching them.
        if abs(cached.opacity - float(opacity)) < 1e-6:
            return cached
        return _with_opacity(cached, opacity)

    if should_cancel is not None and should_cancel():
        return None

    url = frame_url(resolved, size=frame_size)
    try:
        payload = _fetch_bytes(url, opener)
    except RadarError:
        _cache_put(key, None, FAILURE_CACHE_TTL_S)
        raise
    except urllib.error.HTTPError as exc:
        _cache_put(key, None, FAILURE_CACHE_TTL_S)
        raise RadarError(
            "%s request failed: HTTP %s" % (resolved.label, exc.code)) from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        _cache_put(key, None, FAILURE_CACHE_TTL_S)
        raise RadarError(
            "%s request failed: %s" % (resolved.label, exc)) from exc

    # No cancellation check here on purpose. The request has already been paid
    # for, so a frame that arrived after the user toggled the overlay off is
    # still cached: re-enabling it a moment later should then be free.
    if not looks_like_image(payload):
        _cache_put(key, None, FAILURE_CACHE_TTL_S)
        detail = _service_exception_text(payload)
        raise RadarError(
            "%s returned no image%s"
            % (resolved.label, ": %s" % detail if detail else ""))

    if now is None:
        now = datetime.now(timezone.utc)

    try:
        raster = OverlayRaster(
            key=OVERLAY_KEY,
            title="%s (%s)" % (resolved.label, resolved.units)
            if resolved.units else resolved.label,
            image_bytes=payload,
            bounds=(
                COVERAGE_BOUNDS[0], COVERAGE_BOUNDS[1],
                COVERAGE_BOUNDS[2], COVERAGE_BOUNDS[3],
            ),
            subtitle=resolved.description,
            short_name=resolved.label,
            retrieved_at=now,
            update_interval_s=resolved.update_interval_s,
            opacity=opacity,
            source_url=url,
            attribution=ATTRIBUTION,
        )
    except ValueError as exc:
        _cache_put(key, None, FAILURE_CACHE_TTL_S)
        raise RadarError("%s frame was unusable: %s" % (resolved.label, exc)) \
            from exc

    _cache_put(key, raster, FRAME_CACHE_TTL_S)
    return raster


def _with_opacity(raster: OverlayRaster, opacity: float) -> OverlayRaster:
    """Return ``raster`` at a different opacity, reusing its bytes."""
    return raster.at_opacity(opacity)


__all__ = [
    "ATTRIBUTION",
    "COVERAGE_BOUNDS",
    "DEFAULT_FRAME_SIZE",
    "DEFAULT_PRODUCT",
    "FRAME_CACHE_TTL_S",
    "MAX_FRAME_BYTES",
    "OVERLAY_KEY",
    "OVERLAY_TITLE",
    "PRODUCTS",
    "WMS_CRS",
    "WMS_URL",
    "WMS_VERSION",
    "RadarError",
    "RadarProduct",
    "available_products",
    "build_query",
    "cached_frame",
    "clear_cache",
    "covers",
    "fetch_frame",
    "frame_url",
    "get_product",
]

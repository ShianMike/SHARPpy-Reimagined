"""Single-site NEXRAD imagery, chosen by where the user is looking.

No Qt. A worker thread calls :func:`fetch_frame` and gets back a frozen
:class:`~sharpmod.map_overlays.OverlayRaster`, the same contract the CONUS mosaic
and the HRRR fields use.

Why a site rather than the mosaic
--------------------------------
:mod:`sharpmod.radar_mosaic` fetches one 70x35 degree CONUS frame. At its 4096 px
width that is about 1.9 km per pixel, which is coarser than the 1 km mosaic it is
rendered from and far coarser than a single radar's own resolution. Zoomed out
that is the right trade -- one request serves the whole country and every map.
Zoomed in to a storm it is the wrong one: the echo the user is looking at is a
handful of pixels.

A single site covers 10 degrees instead of 70. Requesting the same pixel budget
over that extent lands near half a kilometre per pixel, so the structure inside a
storm survives. The cost is that the frame only covers one radar, which is why
the site is chosen from the map rather than configured: :func:`nearest_site` picks
the antenna closest to wherever the user has centred, and the overlay follows
them as they pan.

Composition
-----------
The key is distinct from the mosaic's, so this is a separate slot: a user can run
a model field, an SPC outlook and a site frame together, and
:data:`sharpmod.gui_maps.RASTER_DRAW_ORDER` puts this one on top because it is the
smallest and brightest of the three. Site and mosaic are separate slots too, so
both *can* be shown -- but the controller offers one or the other, because two
reflectivity ramps over the same storm is not twice the information.
"""

from __future__ import annotations

import json
import logging
import math
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from types import MappingProxyType

from sharpmod.map_overlays import MAX_RASTER_BYTES, OverlayRaster

_LOGGER = logging.getLogger("sharpmod.radar_site")

#: One slot for every single-site product, so switching product replaces the
#: frame rather than stacking a second one.
OVERLAY_KEY = "radar_site"

ATTRIBUTION = "NOAA/NWS NEXRAD via NCEP GeoServer"

BASE = "https://opengeo.ncep.noaa.gov/geoserver"
WMS_VERSION = "1.3.0"
#: ``CRS:84`` for the reason :mod:`sharpmod.radar_mosaic` documents at length:
#: WMS 1.3.0 restored latitude-first axis order for ``EPSG:4326``, so asking for
#: that would silently transpose the bounding box. ``CRS:84`` is longitude-first
#: by definition, and it makes the server return plate carree, which is what the
#: overlay contract requires.
WMS_CRS = "CRS:84"
WMS_FORMAT = "image/png"

#: Pixels along the longer edge of a site frame.
#:
#: A site's advertised extent is 10 degrees, so 2048 px is about 0.5 km per pixel
#: at mid-latitudes -- fine enough that a hook echo or a bounded weak echo region
#: is more than a few pixels, which is the whole point of preferring a site over
#: the mosaic.
DEFAULT_FRAME_PIXELS = 2048
MIN_FRAME_PIXELS = 256
MAX_FRAME_PIXELS = 4096

MAX_FRAME_BYTES = min(8 * 1024 * 1024, MAX_RASTER_BYTES)

#: Just under the ~2 minute volume scan cadence, so a refresh usually finds a
#: new frame without polling for one that does not exist yet.
FRAME_CACHE_TTL_S = 100.0
FAILURE_CACHE_TTL_S = 60.0
_CACHE_MAX_ENTRIES = 12

#: Beyond this the nearest antenna is too far for its frame to be useful, and a
#: 10 degree box centred hundreds of kilometres away is misleading rather than
#: helpful. About the useful range of a WSR-88D for precipitation.
MAX_SITE_DISTANCE_KM = 460.0


class RadarSiteError(RuntimeError):
    """A site frame could not be fetched. Cancellation is not one of these."""


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RadarSiteProduct:
    """One single-site product, addressed by the suffix its layer name uses."""

    key: str
    suffix: str
    label: str
    description: str = ""
    update_interval_s: float = 150.0
    units: str = ""

    def layer(self, site_id: str) -> str:
        """Return the WMS layer name for this product at ``site_id``."""
        return f"{site_id.lower()}{self.suffix}"

    def legend_url(self, site_id: str) -> str:
        """Return the server's own legend graphic for this layer."""
        query = urllib.parse.urlencode({
            "service": "WMS",
            "version": WMS_VERSION,
            "request": "GetLegendGraphic",
            "format": WMS_FORMAT,
            "layer": self.layer(site_id),
        })
        return f"{BASE}/{site_id.lower()}/ows?{query}"


_PRODUCTS = (
    RadarSiteProduct(
        "reflectivity", "_sr_bref", "Base reflectivity",
        "Lowest-tilt reflectivity from the single radar, at its own resolution.",
        units="dBZ"),
    RadarSiteProduct(
        "velocity", "_sr_bvel", "Base velocity",
        "Lowest-tilt radial velocity: toward the radar is negative, away is "
        "positive.",
        units="kt"),
    RadarSiteProduct(
        "hydrometeor", "_bdhc", "Hydrometeor classification",
        "The radar's own classification of what it is detecting."),
    RadarSiteProduct(
        "storm-total", "_bdsa", "Storm total accumulation",
        "Rainfall accumulated since the radar began the current precipitation "
        "episode.", update_interval_s=300.0, units="in"),
    RadarSiteProduct(
        "one-hour", "_boha", "One-hour accumulation",
        "Rainfall accumulated over the past hour.",
        update_interval_s=300.0, units="in"),
)

PRODUCTS: MappingProxyType = MappingProxyType(
    {product.key: product for product in _PRODUCTS})

DEFAULT_PRODUCT = "reflectivity"


def available_products() -> tuple[RadarSiteProduct, ...]:
    return _PRODUCTS


def get_product(key: str | None) -> RadarSiteProduct:
    """Return a product by key, resolving anything unknown to the default."""
    if key:
        product = PRODUCTS.get(str(key).strip())
        if product is not None:
            return product
    return PRODUCTS[DEFAULT_PRODUCT]


# --------------------------------------------------------------------------- #
# Site catalogue
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RadarSite:
    """One WSR-88D: where it is, and how far its frame reaches."""

    id: str
    lat: float
    lon: float
    half_span_deg: float
    products: tuple[str, ...] = ()

    def bounds(self) -> tuple[float, float, float, float]:
        """Return the frame extent as ``(lon0, lon1, lat0, lat1)``.

        Clamped into the valid lon/lat range because ``OverlayRaster`` refuses
        anything outside it, and a site near the antimeridian or a pole would
        otherwise produce a box the overlay contract rejects outright.
        """
        span = self.half_span_deg
        return (max(-180.0, self.lon - span), min(180.0, self.lon + span),
                max(-90.0, self.lat - span), min(90.0, self.lat + span))

    def supports(self, product: RadarSiteProduct) -> bool:
        if not self.products:
            return True
        return product.suffix.lstrip("_") in self.products


@lru_cache(maxsize=1)
def sites() -> tuple[RadarSite, ...]:
    """Return the bundled site catalogue, or empty if it cannot be read.

    Empty rather than raising: a missing resource should disable this one
    overlay, not prevent the map from drawing.
    """
    try:
        from importlib.resources import files
        payload = files("sharpmod.resources").joinpath(
            "radar_sites.json").read_text(encoding="utf-8")
        data = json.loads(payload)
    except Exception:  # noqa: BLE001
        _LOGGER.debug("radar_site.catalogue_unreadable", exc_info=True)
        return ()

    resolved: list[RadarSite] = []
    for entry in data.get("sites", ()):
        try:
            resolved.append(RadarSite(
                id=str(entry["id"]).upper(),
                lat=float(entry["lat"]),
                lon=float(entry["lon"]),
                half_span_deg=float(entry.get("half_span_deg", 5.0)),
                products=tuple(entry.get("products", ())),
            ))
        except (KeyError, TypeError, ValueError):
            continue
    return tuple(resolved)


def site_by_id(site_id: str | None) -> RadarSite | None:
    if not site_id:
        return None
    wanted = str(site_id).strip().upper()
    for site in sites():
        if site.id == wanted:
            return site
    return None


def _distance_km(lat0: float, lon0: float, lat1: float, lon1: float) -> float:
    """Great-circle distance, so site choice stays right at high latitude.

    A degree of longitude is 111 km at the equator and 57 km at 60 N, so
    comparing raw coordinate differences would pick the wrong Alaskan radar.
    """
    radius = 6371.0
    phi0, phi1 = math.radians(lat0), math.radians(lat1)
    dphi = phi1 - phi0
    dlambda = math.radians(lon1 - lon0)
    a = (math.sin(dphi / 2.0) ** 2
         + math.cos(phi0) * math.cos(phi1) * math.sin(dlambda / 2.0) ** 2)
    return 2.0 * radius * math.asin(min(1.0, math.sqrt(a)))


def nearest_site(lat: float, lon: float, *,
                 product: RadarSiteProduct | None = None,
                 max_km: float = MAX_SITE_DISTANCE_KM
                 ) -> tuple[RadarSite, float] | None:
    """Return the closest site publishing ``product``, and its distance in km.

    ``None`` when nothing is within ``max_km``, which is how a map over the
    ocean or outside the network reports "no radar here" without a request.
    """
    best: tuple[RadarSite, float] | None = None
    for site in sites():
        if product is not None and not site.supports(product):
            continue
        distance = _distance_km(lat, lon, site.lat, site.lon)
        if best is None or distance < best[1]:
            best = (site, distance)
    if best is None or best[1] > max_km:
        return None
    return best


def site_for_view(view: tuple[float, float, float, float], *,
                  product: RadarSiteProduct | None = None
                  ) -> tuple[RadarSite, float] | None:
    """Return the site nearest the centre of a lon/lat view."""
    lon0, lon1, lat0, lat1 = view
    return nearest_site((lat0 + lat1) / 2.0, (lon0 + lon1) / 2.0,
                        product=product)


def covers(view: tuple[float, float, float, float], *,
           product: RadarSiteProduct | None = None) -> bool:
    """Whether any radar is close enough to the view to be worth fetching."""
    return site_for_view(view, product=product) is not None


# --------------------------------------------------------------------------- #
# Fetch
# --------------------------------------------------------------------------- #


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


def _remote_limits() -> tuple[float, int]:
    try:
        from sharpmod.io.decoder import _max_remote_bytes, _remote_timeout
        return float(_remote_timeout()), int(_max_remote_bytes())
    except Exception:  # noqa: BLE001
        return 30.0, 32 * 1024 * 1024


def _default_opener(url: str, timeout: float, limit: int) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": "sharpmod"})
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=_ssl_context()) as response:
            payload = response.read(limit + 1)
    except urllib.error.HTTPError as error:
        raise RadarSiteError(
            f"radar request failed: HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise RadarSiteError(f"radar request failed: {error}") from error
    if len(payload) > limit:
        raise RadarSiteError("radar response exceeded the byte budget")
    return payload


_SERVICE_EXCEPTION = re.compile(
    r"<ServiceException[^>]*>(.*?)</ServiceException>", re.S | re.I)


def _frame_size(site: RadarSite, pixels: int) -> tuple[int, int]:
    """Return ``(width, height)`` matching the site's own aspect.

    The extent is square in degrees, but a degree of longitude is shorter than a
    degree of latitude away from the equator. Sizing the image to the *degree*
    box keeps it plate carree, which is what the blit assumes; letting the pixel
    aspect follow the degree aspect would stretch it.
    """
    lon0, lon1, lat0, lat1 = site.bounds()
    lon_span = max(1e-6, lon1 - lon0)
    lat_span = max(1e-6, lat1 - lat0)
    if lon_span >= lat_span:
        width = pixels
        height = max(MIN_FRAME_PIXELS,
                     int(round(pixels * lat_span / lon_span)))
    else:
        height = pixels
        width = max(MIN_FRAME_PIXELS,
                    int(round(pixels * lon_span / lat_span)))
    return width, height


def build_url(product: RadarSiteProduct, site: RadarSite,
              size: tuple[int, int]) -> str:
    lon0, lon1, lat0, lat1 = site.bounds()
    query = urllib.parse.urlencode({
        "service": "WMS",
        "version": WMS_VERSION,
        "request": "GetMap",
        "layers": product.layer(site.id),
        "styles": "",
        "crs": WMS_CRS,
        "bbox": f"{lon0},{lat0},{lon1},{lat1}",
        "width": int(size[0]),
        "height": int(size[1]),
        "format": WMS_FORMAT,
        "transparent": "TRUE",
    })
    return f"{BASE}/{site.id.lower()}/ows?{query}"


_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple, tuple[float, OverlayRaster | None]] = {}


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def fetch_frame(product_key: str | None, *, site_id: str | None = None,
                view: tuple[float, float, float, float] | None = None,
                pixels: int = DEFAULT_FRAME_PIXELS, opacity: float = 0.85,
                opener=None, should_cancel=None) -> OverlayRaster | None:
    """Fetch one single-site frame.

    Give either ``site_id`` to pin a radar, or ``view`` to have the one nearest
    its centre chosen. Returns ``None`` only for cancellation; every real failure
    raises :class:`RadarSiteError`, so the caller can tell the two apart.
    """
    product = get_product(product_key)
    site = site_by_id(site_id)
    if site is None:
        if view is None:
            raise RadarSiteError("no radar site was given and no map view "
                                 "was supplied to choose one from")
        chosen = site_for_view(view, product=product)
        if chosen is None:
            raise RadarSiteError(
                "no NEXRAD site is within "
                f"{MAX_SITE_DISTANCE_KM:.0f} km of this view")
        site = chosen[0]
    if not site.supports(product):
        raise RadarSiteError(f"{site.id} does not publish {product.label}")

    frame = _frame_size(site, max(MIN_FRAME_PIXELS,
                                  min(MAX_FRAME_PIXELS, int(pixels))))
    cache_key = (product.key, site.id, frame)
    moment = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
    if cached is not None:
        age = moment - cached[0]
        raster = cached[1]
        if raster is None:
            if age < FAILURE_CACHE_TTL_S:
                raise RadarSiteError(
                    f"{site.id} {product.label} was unavailable")
        elif age < FRAME_CACHE_TTL_S:
            return raster if raster.opacity == opacity \
                else raster.at_opacity(opacity)

    if should_cancel and should_cancel():
        return None

    timeout, _limit = _remote_limits()
    call = opener or _default_opener
    url = build_url(product, site, frame)
    try:
        payload = call(url, timeout, MAX_FRAME_BYTES)
    except RadarSiteError:
        with _CACHE_LOCK:
            _CACHE[cache_key] = (moment, None)
        raise

    if should_cancel and should_cancel():
        return None

    # A WMS reports failure as an XML body with HTTP 200, so the payload has to
    # be inspected rather than trusted.
    if not payload:
        raise RadarSiteError(f"{site.id} returned an empty frame")
    if payload.lstrip()[:1] == b"<":
        text = payload.decode("utf-8", errors="replace")
        match = _SERVICE_EXCEPTION.search(text)
        detail = match.group(1).strip() if match else "unrecognised response"
        raise RadarSiteError(f"{site.id}: {detail[:200]}")

    lon0, lon1, lat0, lat1 = site.bounds()
    try:
        raster = OverlayRaster(
            key=OVERLAY_KEY,
            title=f"{site.id} {product.label}",
            image_bytes=payload,
            bounds=(lon0, lon1, lat0, lat1),
            subtitle=f"Single site \u00b7 {site.lat:.2f}, {site.lon:.2f}",
            short_name=site.id,
            retrieved_at=datetime.now(timezone.utc),
            update_interval_s=product.update_interval_s,
            opacity=opacity,
            source_url=url,
            attribution=ATTRIBUTION,
        )
    except ValueError as error:
        raise RadarSiteError(f"radar frame rejected: {error}") from error

    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX_ENTRIES:
            oldest = min(_CACHE, key=lambda key: _CACHE[key][0])
            _CACHE.pop(oldest, None)
        _CACHE[cache_key] = (time.monotonic(), raster)
    return raster

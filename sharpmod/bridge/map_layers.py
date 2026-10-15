"""Map overlays: SPC outlooks, the radar mosaic, place labels, and the UWyo
station list for an observation time.

Each handler calls the sharpmod provider the legacy map uses and passes its
data on as geography and numbers; the frontend draws it. Outlook areas keep
SPC's own colours, labels, and holes; the radar frame is the service's PNG.
"""

from __future__ import annotations

import base64
from datetime import timedelta
from typing import Any

from sharpmod.bridge import params as p
from sharpmod.bridge.errors import BridgeError
from sharpmod.bridge.fields import OUTLOOK_PRODUCTS

#: As for the sounding image: base64 keeps the reply under the 8 MiB frame.
MAX_IMAGE_BYTES = 6 * 1024 * 1024 - 4096
#: Decimal places kept for outlook vertices: about 10 m, which SPC's own
#: polygons do not exceed.
VERTEX_DECIMALS = 4


def _short(exc: Exception) -> str:
    text = str(exc).strip()
    return text.splitlines()[0][:200] if text else type(exc).__name__


def spc_outlook(params: dict[str, Any]) -> dict[str, Any]:
    """The SPC outlook of one product covering ``valid_time``, or null."""
    p.keys(params, ("product", "valid_time"))
    product = p.choice(params, "product", OUTLOOK_PRODUCTS, "cat")
    when = p.time(params, "valid_time")
    from sharpmod.providers import spc_outlook as spc
    from sharpmod.providers import spc_outlook_fetch

    try:
        layer = spc_outlook_fetch.fetch_layer(when, product=product)
    except spc.OutlookError as exc:
        raise BridgeError(
            "OVERLAY_FETCH_FAILED",
            f"The SPC outlook could not be read: {_short(exc)}.",
            True,
        ) from None
    result: dict[str, Any] = {
        "product": product,
        "valid_time": p.iso(when),
        "outlook": None,
    }
    if not layer:
        return result
    round_to = VERTEX_DECIMALS
    result["outlook"] = {
        "title": layer.title,
        "subtitle": layer.subtitle,
        "valid_from": p.iso(layer.valid_from),
        "valid_to": p.iso(layer.valid_to),
        "issued": p.iso(layer.issued),
        "covers": bool(layer.covers(when)),
        "source_url": layer.source_url,
        "attribution": layer.attribution,
        "areas": [
            {
                "label": shape.label,
                "description": shape.description,
                "fill": shape.fill,
                "stroke": shape.stroke,
                "rank": int(shape.rank),
                "hatch": bool(shape.hatch),
                "hatch_level": int(shape.hatch_level),
                "rings": [
                    [[round(lon, round_to), round(lat, round_to)] for lon, lat in ring]
                    for ring in shape.rings
                ],
            }
            for shape in layer.shapes
            if not shape.marker
        ],
    }
    return result


def radar_mosaic(params: dict[str, Any]) -> dict[str, Any]:
    """The newest MRMS mosaic frame of one product, as the service's PNG."""
    p.keys(params, (), ("product",))
    from sharpmod.providers import radar_mosaic as radar

    keys = tuple(product.key for product in radar.available_products())
    key = p.choice(params, "product", keys, radar.DEFAULT_PRODUCT)
    product = radar.get_product(key)
    try:
        raster = radar.fetch_frame(key)
    except radar.RadarError as exc:
        raise BridgeError(
            "OVERLAY_FETCH_FAILED",
            f"The radar mosaic could not be loaded: {_short(exc)}.",
            True,
        ) from None
    if raster is None:
        raise BridgeError(
            "OVERLAY_FETCH_FAILED", "The radar request was cancelled.", True
        )
    data = bytes(raster.image_bytes)
    if len(data) > MAX_IMAGE_BYTES or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise BridgeError(
            "OVERLAY_FETCH_FAILED",
            "The radar service did not send a usable PNG frame.",
            True,
        )
    west, east, south, north = (float(value) for value in raster.bounds)
    return {
        "product": key,
        "label": product.label,
        "units": product.units,
        "bounds": {"west": west, "east": east, "south": south, "north": north},
        "retrieved_at": p.iso(raster.retrieved_at),
        "valid_time": p.iso(raster.valid_time),
        "update_interval_s": float(raster.update_interval_s),
        "attribution": raster.attribution,
        "source_url": raster.source_url,
        "byte_length": len(data),
        "png_base64": base64.b64encode(data).decode("ascii"),
    }


def radar_site(params: dict[str, Any]) -> dict[str, Any]:
    """One NEXRAD antenna's own frame, at its own resolution.

    Pass ``site_id`` to pin an antenna by identifier, or ``latitude`` and
    ``longitude`` to serve the nearest antenna publishing the product. The
    reply has the mosaic's shape, plus ``site_id`` naming the serving antenna.
    """
    p.keys(params, (), ("product", "site_id", "latitude", "longitude"))
    from sharpmod.providers import radar_site as site

    keys = tuple(product.key for product in site.available_products())
    key = p.choice(params, "product", keys, site.DEFAULT_PRODUCT)
    product = site.get_product(key)
    raw_site = params.get("site_id")
    if raw_site is not None:
        if not isinstance(raw_site, str):
            raise p.invalid("site_id must be text")
        resolved = site.site_by_id(raw_site)
        if resolved is None:
            raise p.invalid(f"unknown radar site {raw_site.strip()!r}")
        if not resolved.supports(product):
            raise p.invalid(f"{resolved.id} does not publish {product.label}")
        fetch = {"site_id": resolved.id}
    else:
        if params.get("latitude") is None or params.get("longitude") is None:
            raise p.invalid("radar.site needs a site_id or a latitude/longitude")
        lat = p.number(params, "latitude", -90.0, 90.0)
        lon = p.number(params, "longitude", -180.0, 180.0)
        nearest = site.nearest_site(lat, lon, product=product)
        if nearest is None:
            raise BridgeError(
                "OVERLAY_FETCH_FAILED",
                "No NEXRAD site is within range of this view.",
                False,
            )
        fetch = {"site_id": nearest[0].id}
    try:
        raster = site.fetch_frame(key, **fetch)
    except site.RadarSiteError as exc:
        raise BridgeError(
            "OVERLAY_FETCH_FAILED",
            f"The single-site radar could not be loaded: {_short(exc)}.",
            True,
        ) from None
    if raster is None:
        raise BridgeError(
            "OVERLAY_FETCH_FAILED", "The radar request was cancelled.", True
        )
    data = bytes(raster.image_bytes)
    if len(data) > MAX_IMAGE_BYTES or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise BridgeError(
            "OVERLAY_FETCH_FAILED",
            "The radar service did not send a usable PNG frame.",
            True,
        )
    west, east, south, north = (float(value) for value in raster.bounds)
    return {
        "product": key,
        "site_id": raster.short_name,
        "label": product.label,
        "units": product.units,
        "bounds": {"west": west, "east": east, "south": south, "north": north},
        "retrieved_at": p.iso(raster.retrieved_at),
        "valid_time": p.iso(raster.valid_time),
        "update_interval_s": float(raster.update_interval_s),
        "attribution": raster.attribution,
        "source_url": raster.source_url,
        "byte_length": len(data),
        "png_base64": base64.b64encode(data).decode("ascii"),
    }


def radar_sites(params: dict[str, Any]) -> dict[str, Any]:
    """The bundled WSR-88D catalogue backing the single-site picker."""
    p.keys(params, ())
    from sharpmod.providers import radar_site as site

    return {
        "sites": [
            {"id": entry.id, "lat": float(entry.lat), "lon": float(entry.lon)}
            for entry in site.sites()
        ]
    }


def place_labels(params: dict[str, Any]) -> dict[str, Any]:
    """Map place names: the curated major cities, then towns, one per quarter degree.

    The selection is the legacy map's (``ui/maps/presentation.py``): major
    cities always, and from the bundled Census index the largest incorporated
    place in each quarter-degree cell. Towns come largest first.
    """
    p.keys(params, ())
    from sharpmod.maps.map_geography import MAJOR_CITIES
    from sharpmod.maps.place_names import _conus_place_cells

    places = [
        {"name": str(name), "lat": float(lat), "lon": float(lon), "tier": 1}
        for name, lat, lon in MAJOR_CITIES
    ]
    best: dict[tuple[int, int, int, int], tuple[float, str, float, float]] = {}
    for (lat_cell, lon_cell), records in _conus_place_cells().items():
        for label, lat, lon, kind, area in records:
            if kind != "place":
                continue
            cell = (
                lat_cell,
                lon_cell,
                max(0, min(3, int((lat - lat_cell) * 4.0))),
                max(0, min(3, int((lon - lon_cell) * 4.0))),
            )
            held = best.get(cell)
            if held is None or (area, label) > (held[0], held[1]):
                best[cell] = (float(area), str(label), float(lat), float(lon))
    towns = sorted(best.values(), key=lambda town: (-town[0], town[1]))
    places.extend(
        {"name": label, "lat": lat, "lon": lon, "tier": 2}
        for _area, label, lat, lon in towns
    )
    return {"places": places}


def station_availability(params: dict[str, Any]) -> dict[str, Any]:
    """The stations UWyo lists for one observation time."""
    p.keys(params, ("observation_time",))
    when = p.time(params, "observation_time")
    if when.minute or when.second:
        raise p.invalid("observation_time must be on a whole hour")
    if when > p.now() + timedelta(hours=1):
        raise p.invalid("observation_time is in the future")
    from sharpmod.bridge.handlers import filter_records
    from sharpmod.io import uwyo_catalog

    try:
        records = uwyo_catalog.fetch_stations_for_datetime(when)
    except uwyo_catalog.StationListError as exc:
        raise BridgeError(
            "STATION_LIST_UNAVAILABLE",
            f"The UWyo station list for {when:%Y-%m-%d %H}Z could not be loaded "
            f"({_short(exc)}). The bundled list is still shown.",
            True,
        ) from None
    stations, dropped = filter_records(records)
    bundled = {record["id"] for record in uwyo_catalog.all_stations()}
    return {
        "observation_time": p.iso(when),
        "listed_ids": [
            station["id"] for station in stations if station["id"] in bundled
        ],
        "extra_stations": [
            station for station in stations if station["id"] not in bundled
        ],
        "dropped_count": dropped,
    }


HANDLERS = {
    "outlooks.spc": spc_outlook,
    "radar.mosaic": radar_mosaic,
    "radar.site": radar_site,
    "radar.sites": radar_sites,
    "places.labels": place_labels,
    "stations.availability": station_availability,
}

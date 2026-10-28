"""Regenerate the map contract fixtures from the real bridge handlers, offline.

Writes under ``src-tauri/contracts/``:

* ``bridge/map/*.json``: the bridge results (snake_case), as the handlers in
  ``sharpmod/bridge/fields.py`` and ``sharpmod/bridge/map_layers.py`` return them;
* ``public/map/*.json``: the same payloads as the webview receives them (camelCase);
* ``public/map/field.shrf`` and ``public/map/radar.shrf``: the binary frames the
  webview receives for a field grid and a radar frame.

The network is replaced by deterministic stand-ins: one synthetic HRRR record
(derived, reprojected, and quantized by the real code onto a 256 by 128 frame),
SPC payloads read by the real SPC parser, a fixed synthetic radar PNG, and a
UWyo station list. The clock is pinned. Run from the repository
root: ``python scripts/export_map_fixtures.py``.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from sharpmod.bridge.public import camel as _camel, shrf

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "src-tauri" / "contracts"
FIXED_NOW = datetime(2026, 10, 4, 18, 30, tzinfo=UTC)
RUN_TIME = "2026-10-04T18:00:00Z"
OUTLOOK_TIME = "2026-10-04T21:00:00Z"
FIXTURE_FRAME = (256, 128)
#: Major cities and the largest towns; enough for the browser fixture build.
PLACES_KEPT = 400
#: SPC stamps: issued 1250Z for the 1300Z Day 1 outlook, valid to 12Z.
SPC_STAMPS = {
    "VALID": "202610041300",
    "EXPIRE": "202610051200",
    "ISSUE": "202610041250",
}
#: Local Storm Reports in the provider's own CSV shape: one tornado, one
#: significant hail, one wind gust, and one wind damage report near Norman.
_REPORTS_CSV = (
    "VALID,TYPECODE,LAT,LON,MAG,CITY,UGCNAME,STATE,WFO,SOURCE,REMARK\n"
    "202610041915,T,35.20,-97.10,,Norman,Cleveland,OK,OUN,EMERGENCY MGR,Tornado reported\n"
    "202610041940,H,35.30,-97.30,2.75,Norman,Cleveland,OK,OUN,PUBLIC,Baseball size hail\n"
    "202610042005,G,35.40,-97.50,68,Norman,Cleveland,OK,OUN,MESONET,Measured wind gust\n"
    "202610042030,D,35.50,-97.60,,Norman,Cleveland,OK,OUN,BROADCAST MEDIA,Damage reported\n"
)


def _surface_set(surface_module, when):
    """One real SurfaceObservationSet near Norman, built from the provider's
    own record types so its wind-state and staleness rules run for real.

    Six stations cover every plot state the map distinguishes (T22): a staff
    with barbs and a gust, a calm circle, a speed without a direction, a stale
    plot, one station with no observation in the window, and one whose query
    failed. Times are fixed offsets from the requested hour.
    """
    from datetime import timedelta

    station = surface_module.SurfaceStation
    observation = surface_module.SurfaceObservation

    koun = station("KOUN", "Norman", 35.2, -97.4)
    kokc = station("KOKC", "Oklahoma City", 35.4, -97.6)
    ksps = station("KSPS", "Wichita Falls", 33.9, -98.5)
    kswa = station("KSWA", "Wichita", 37.6, -97.4)
    khbr = station("KHBR", "Hobart", 35.0, -99.0)
    kfoo = station("KFOO", "Failed", 34.0, -97.0)
    observations = (
        observation(koun, when, 21.5, 12.2, 270.0, 20.0, 28.0),
        observation(kokc, when, 18.0, 10.0, None, 0.0),
        observation(ksps, when, 33.0, 20.0, None, 12.0),
        observation(kswa, when - timedelta(minutes=45), 26.0, 9.0, 180.0, 8.0),
    )
    return surface_module.SurfaceObservationSet(
        when,
        35.2,
        -97.4,
        observations,
        6,
        ("KHBR", "KFOO"),
        FIXED_NOW,
        "https://api.weather.gov/stations",
        ("KOUN", "KOKC", "KSPS", "KSWA", "KHBR", "KFOO"),
        (koun, kokc, ksps, kswa, khbr, kfoo),
        ("KFOO",),
    )


def _json(value: Any) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode("ascii")


def _reflectivity(shape: tuple[int, int]) -> Any:
    """Three echoes on the native HRRR grid, over the southern Plains."""
    import numpy as np

    rows, cols = np.mgrid[0 : shape[0], 0 : shape[1]].astype(np.float32)
    field = np.full(shape, -10.0, dtype=np.float32)
    for row, col, peak, radius in (
        (520, 980, 62.0, 18.0),
        (560, 1040, 48.0, 30.0),
        (470, 900, 35.0, 45.0),
    ):
        cell = (
            peak * np.exp(-((rows - row) ** 2 + (cols - col) ** 2) / (2.0 * radius**2))
            - 5.0
        )
        field = np.maximum(field, cell.astype(np.float32))
    return field


def _height_plane(shape: tuple[int, int]) -> Any:
    """A gentle geopotential ramp (metres) spanning about two 6 dam levels."""
    import numpy as np

    rows, cols = np.mgrid[0 : shape[0], 0 : shape[1]].astype(np.float32)
    return (5550.0 + rows * 0.05 + cols * 0.03).astype(np.float32)


def _feature(rings: list, props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": rings},
        "properties": {**props, **SPC_STAMPS},
    }


def _box(west: float, south: float, east: float, north: float) -> list[list[float]]:
    return [[west, south], [east, south], [east, north], [west, north], [west, south]]


def _outlook_payloads() -> dict[str, bytes]:
    tstm, mrgl, slgt = (
        _box(-104, 30, -88, 42),
        _box(-101, 32, -92, 39),
        _box(-99, 34, -95, 37),
    )
    cat = [
        _feature(
            [tstm, mrgl],
            {
                "DN": 2,
                "LABEL": "TSTM",
                "LABEL2": "General Thunderstorms Risk",
                "stroke": "#55BB55",
                "fill": "#C1E9C1",
            },
        ),
        _feature(
            [mrgl, slgt],
            {
                "DN": 3,
                "LABEL": "MRGL",
                "LABEL2": "Marginal Risk",
                "stroke": "#005500",
                "fill": "#66A366",
            },
        ),
        _feature(
            [slgt],
            {
                "DN": 4,
                "LABEL": "SLGT",
                "LABEL2": "Slight Risk",
                "stroke": "#DDAA00",
                "fill": "#FFE066",
            },
        ),
    ]
    two, five, sign = (
        _box(-101, 32, -93, 39),
        _box(-99, 34, -95, 37),
        _box(-98, 34.5, -96, 36.5),
    )
    torn = [
        _feature(
            [two, five],
            {
                "DN": 2,
                "LABEL": "0.02",
                "LABEL2": "2% Tornado Risk",
                "stroke": "#005500",
                "fill": "#66A366",
            },
        ),
        _feature(
            [five],
            {
                "DN": 5,
                "LABEL": "0.05",
                "LABEL2": "5% Tornado Risk",
                "stroke": "#70380F",
                "fill": "#9D4E15",
            },
        ),
        _feature(
            [sign],
            {
                "DN": 10,
                "LABEL": "SIGN",
                "LABEL2": "10% or Greater Significant Tornado Risk",
                "stroke": "#000000",
                "fill": "#888888",
            },
        ),
    ]
    return {
        product: json.dumps({"type": "FeatureCollection", "features": features}).encode(
            "utf-8"
        )
        for product, features in (("cat", cat), ("torn", torn))
    }


def _radar_png() -> bytes:
    # Provider input stays fixed across Pillow/Qt and zlib versions.
    return (CONTRACTS / "inputs" / "radar.png").read_bytes()


@contextmanager
def stand_ins() -> Iterator[None]:
    """The offline providers, installed for the duration of the block only."""
    import numpy as np

    from sharpmod.bridge import fields
    from sharpmod.bridge import params as p
    from sharpmod.io import uwyo_catalog
    from sharpmod.maps.map_overlays import OverlayRaster
    from sharpmod.providers import (
        hrrr_field,
        radar_mosaic,
        radar_site,
        satellite_context as sat_module,
        storm_reports as reports_module,
        spc_outlook,
        spc_outlook_fetch,
        surface_observations as surface_module,
    )

    hrrr_field.clear_derived_cache()
    stack = ExitStack()
    cache_root = stack.enter_context(TemporaryDirectory(prefix="sharpmod-map-fixtures-"))
    stack.enter_context(patch.object(hrrr_field, "disk_cache_root", lambda: Path(cache_root)))
    stack.callback(hrrr_field.clear_derived_cache)
    stack.enter_context(patch.object(p, "now", lambda: FIXED_NOW))
    stack.enter_context(patch.object(fields, "FRAME", FIXTURE_FRAME))
    stack.enter_context(
        patch.object(
            hrrr_field,
            "_download_fields",
            lambda product, run, hour, **_: {
                "refc": _reflectivity(hrrr_field.HRRR_SHAPE),
                "hgt500": _height_plane(hrrr_field.HRRR_SHAPE),
                **{
                    field.name: np.full(
                        hrrr_field.HRRR_SHAPE,
                        30 if field.variable == "UGRD" else 10,
                        dtype=np.float32,
                    )
                    for field in product.fields
                    if field.variable in {"UGRD", "VGRD"}
                },
            },
        )
    )

    layers = {
        product: spc_outlook.parse_outlook(
            payload,
            source_url=f"https://www.spc.noaa.gov/products/outlook/archive/2026/day1otlk_20261004_1300_{product}.nolyr.geojson",
            day=1,
            label="Day 1",
            product=product,
        )
        for product, payload in _outlook_payloads().items()
    }
    stack.enter_context(
        patch.object(
            spc_outlook_fetch,
            "fetch_layer",
            lambda when, product="cat", **_: layers.get(product),
        )
    )

    def radar_frame(key, size=None, **_):
        product = radar_mosaic.get_product(key)
        return OverlayRaster(
            key=radar_mosaic.OVERLAY_KEY,
            title=f"{product.label} ({product.units})",
            image_bytes=_radar_png(),
            bounds=radar_mosaic.COVERAGE_BOUNDS,
            subtitle=product.description,
            short_name=product.label,
            retrieved_at=FIXED_NOW,
            update_interval_s=product.update_interval_s,
            opacity=1.0,
            source_url=radar_mosaic.frame_url(product, size=size),
            attribution=radar_mosaic.ATTRIBUTION,
        )

    stack.enter_context(patch.object(radar_mosaic, "fetch_frame", radar_frame))

    def radar_site_frame(product_key=None, site_id=None, view=None, **_):
        from sharpmod.providers import radar_level2
        product = radar_site.get_product(product_key)
        site = radar_site.site_by_id(site_id) or radar_site.site_by_id("KTLX")
        assert site is not None
        native = product.key in radar_site.LEVEL2_PRODUCTS
        return OverlayRaster(
            key=radar_site.OVERLAY_KEY,
            title=f"{site.id} {product.label}",
            image_bytes=_radar_png(),
            bounds=site.bounds(),
            subtitle=f"Single site \u00b7 {site.lat:.2f}, {site.lon:.2f}",
            short_name=site.id,
            retrieved_at=FIXED_NOW,
            valid_time=FIXED_NOW if native else None,
            elevation_angle=0.5 if native else None,
            update_interval_s=product.update_interval_s,
            opacity=1.0,
            source_url=(f"{radar_level2.BASE}/{FIXED_NOW:%Y/%m/%d}/{site.id}/{site.id}{FIXED_NOW:%Y%m%d_%H%M%S}_V06"
                        if native else radar_site.build_url(product, site, (2048, 1024))),
            attribution="NOAA/NEXRAD Level II · NSF Unidata" if native else radar_site.ATTRIBUTION,
        )

    stack.enter_context(patch.object(radar_site, "fetch_frame", radar_site_frame))

    def satellite_overlay(when, *, bounds, channel="infrared", **_):
        west, east, south, north = (float(value) for value in bounds)
        raster = OverlayRaster(
            key="goes_context",
            title="GOES satellite",
            image_bytes=_radar_png(),
            bounds=(west, east, south, north),
            subtitle="",
            short_name=channel,
            valid_time=when,
            retrieved_at=FIXED_NOW,
            update_interval_s=300.0,
            opacity=0.72,
            source_url="https://noaa-goes19.s3.amazonaws.com/example.nc",
            attribution=sat_module.ATTRIBUTION,
        )
        return sat_module.SatelliteOverlay(raster, when, when, "G19", channel, 0.87, 2.0)

    stack.enter_context(
        patch.object(sat_module, "fetch_satellite_overlay", satellite_overlay)
    )

    def report_download(**_):
        return _REPORTS_CSV, "https://mesonet.agron.iastate.edu/example"

    stack.enter_context(patch.object(reports_module, "download", report_download))

    def surface_fetch(latitude, longitude, when, **_):
        return _surface_set(surface_module, when)

    stack.enter_context(
        patch.object(surface_module, "fetch_surface_observations", surface_fetch)
    )

    def station_list(when, timeout=30):
        return [
            {
                "id": "72357",
                "name": "Norman",
                "lat": 35.18,
                "lon": -97.44,
                "src": "FM35",
            },
            {
                "id": "72451",
                "name": "Dodge City",
                "lat": 37.76,
                "lon": -99.97,
                "src": "FM35",
            },
            {
                "id": "99901",
                "name": "Example Field Site",
                "lat": 36.5,
                "lon": -98.5,
                "src": "BUFR",
            },
        ]

    stack.enter_context(
        patch.object(uwyo_catalog, "fetch_stations_for_datetime", station_list)
    )
    with stack:
        yield


def build() -> dict[str, bytes]:
    """Every map fixture, keyed by its path under ``src-tauri/contracts``."""
    with stand_ins():
        return _build()


def _build() -> dict[str, bytes]:
    from sharpmod.bridge import fields, map_layers
    from sharpmod.providers import radar_level3

    out: dict[str, bytes] = {}
    out["public/map/radar-velocity.palette.json"] = _json(radar_level3.palette())
    out["public/map/radar-reflectivity.palette.json"] = _json(radar_level3.palette("mosaic"))
    out["public/map/radar-base-reflectivity.palette.json"] = _json(radar_level3.palette("reflectivity"))
    out["public/map/radar-correlation.palette.json"] = _json(radar_level3.palette("correlation-coefficient"))
    out["public/map/radar-echo-tops.palette.json"] = _json(radar_level3.palette("echo-tops"))

    catalog = fields.catalog({})
    out["bridge/map/catalog.result.json"] = _json(catalog)
    out["public/map/catalog.json"] = _json(_camel(catalog))
    from sharpmod.providers.hrrr_products import FIELD_MODELS

    catalogs = {model: fields.catalog({"model": model}) for model in FIELD_MODELS if model != "hrrr"}
    out["bridge/map/catalog.models.json"] = _json(catalogs)
    out["public/map/catalog.models.json"] = _json(_camel(catalogs))

    grid = fields.grid({"product": "refc", "run_time": RUN_TIME, "forecast_hour": 1})
    out["bridge/map/field.result.json"] = _json(grid)
    header = {
        "kind": "field",
        **_camel({k: v for k, v in grid.items() if k != "data_base64"}),
    }
    out["public/map/field.shrf"] = shrf(header, base64.b64decode(grid["data_base64"]))

    radar = map_layers.radar_mosaic({})
    out["bridge/map/radar.result.json"] = _json(radar)
    keep = {k: v for k, v in radar.items() if k not in ("byte_length", "png_base64")}
    out["public/map/radar.shrf"] = shrf(
        {"kind": "radar", **_camel(keep)}, base64.b64decode(radar["png_base64"])
    )

    site = map_layers.radar_site(
        {"product": "reflectivity", "site_id": "KTLX"}
    )
    out["bridge/map/radar-site.result.json"] = _json(site)
    keep = {k: v for k, v in site.items() if k not in ("byte_length", "png_base64")}
    out["public/map/radar-site.shrf"] = shrf(
        {"kind": "radar-site", **_camel(keep)},
        base64.b64decode(site["png_base64"]),
    )

    sites = map_layers.radar_sites({})
    cuts = {"site_id": "KTLX", "product": "velocity", "checked_at": FIXED_NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tilts": [{"tilt": cut, "status": "available" if cut in "0123" else "unavailable"}
                      for cut in ("z", "0", "a", "1", "b", "2", "3")]}
    out["bridge/map/radar-tilts.result.json"] = _json(cuts)
    out["public/map/radar-tilts.json"] = _json(_camel(cuts))
    out["bridge/map/radar-sites.result.json"] = _json(sites)
    out["public/map/radar-sites.json"] = _json(
        {
            "sites": [
                {
                    "id": site["id"],
                    "latitude": site["lat"],
                    "longitude": site["lon"],
                }
                for site in sites["sites"]
            ]
        }
    )

    from sharpmod.bridge import satellite as satellite_bridge

    for channel in ("infrared", "visible"):
        goes = satellite_bridge.goes_frame(
            {"valid_time": OUTLOOK_TIME, "channel": channel}
        )
        suffix = "" if channel == "infrared" else ".visible"
        out[f"bridge/map/satellite.result{suffix}.json"] = _json(goes)
        keep = {k: v for k, v in goes.items() if k not in ("byte_length", "png_base64")}
        out[f"public/map/satellite{suffix}.shrf"] = shrf(
            {"kind": "satellite", **_camel(keep)},
            base64.b64decode(goes["png_base64"]),
        )

    from sharpmod.bridge import reports as reports_bridge

    for hazard in ("all", "tornado", "hail", "wind", "significant"):
        storms = reports_bridge.storm_reports(
            {"valid_time": OUTLOOK_TIME, "window_s": 21600, "hazard_filter": hazard}
        )
        suffix = "" if hazard == "all" else f".{hazard}"
        out[f"bridge/map/reports.result{suffix}.json"] = _json(storms)
        out[f"public/map/reports{suffix}.json"] = _json(
            {
                **{k: v for k, v in _camel(storms).items() if k != "reports"},
                "reports": [
                    {
                        **{k: v for k, v in _camel(report).items() if k not in ("lat", "lon")},
                        "latitude": report["lat"],
                        "longitude": report["lon"],
                    }
                    for report in storms["reports"]
                ],
            }
        )

    options = reports_bridge.report_options({})
    out["bridge/map/reports-options.result.json"] = _json(options)
    out["public/map/reports-options.json"] = _json(_camel(options))

    from sharpmod.bridge import surface_plots as surface_bridge

    plots = surface_bridge.plots(
        {"latitude": 35.2, "longitude": -97.4, "valid_time": OUTLOOK_TIME}
    )
    out["bridge/map/surface.result.json"] = _json(plots)
    out["public/map/surface.json"] = _json(_camel(plots))

    from sharpmod.bridge import contours as contours_bridge

    lines = contours_bridge.contours(
        {"product": "hgt-wind-500", "run_time": RUN_TIME, "forecast_hour": 1}
    )
    out["bridge/map/contours.result.json"] = _json(lines)
    out["public/map/contours.json"] = _json(_camel(lines))

    for product in ("cat", "torn", "wind"):
        outlook = map_layers.spc_outlook(
            {"product": product, "valid_time": OUTLOOK_TIME}
        )
        out[f"bridge/map/outlook.result.{product}.json"] = _json(outlook)
        out[f"public/map/outlook.{product}.json"] = _json(_camel(outlook))

    alerts = {"valid_time": OUTLOOK_TIME, "fetched_at": OUTLOOK_TIME, "alerts": [
        {"id": f"synthetic-{kind}", "event": event, "kind": kind, "office": "KOUN",
         "issued": "2026-10-04T20:45:00Z", "expires": "2026-10-04T22:00:00Z", "colour": colour,
         "source_url": f"https://api.weather.gov/alerts/synthetic-{kind}",
         "polygons": [[[[west, 34.0], [-96.0, 34.0], [-96.0, 36.0], [west, 36.0], [west, 34.0]]]]}
        for kind, event, colour, west in [("warning", "Tornado Warning", "#ff0000", -98.0), ("watch", "Tornado Watch", "#ffff00", -99.0)]
    ]}
    out["bridge/map/alerts.result.json"] = _json(alerts)
    out["public/map/alerts.json"] = _json(_camel(alerts))

    places = map_layers.place_labels({})
    places = {"places": places["places"][:PLACES_KEPT]}
    out["bridge/map/places.result.json"] = _json(places)
    out["public/map/places.json"] = _json(
        {
            "places": [
                {
                    "name": place["name"],
                    "latitude": place["lat"],
                    "longitude": place["lon"],
                    "tier": place["tier"],
                }
                for place in places["places"]
            ]
        }
    )

    listed = map_layers.station_availability(
        {"observation_time": "2026-10-04T12:00:00Z"}
    )
    out["bridge/map/availability.result.json"] = _json(listed)
    out["public/map/availability.json"] = _json(
        {
            "observationTime": listed["observation_time"],
            "listedIds": listed["listed_ids"],
            "extraStations": [
                {
                    "id": station["id"],
                    "name": station["name"],
                    "latitude": station["lat"],
                    "longitude": station["lon"],
                    "source": station["src"],
                    "availability": "listed",
                }
                for station in listed["extra_stations"]
            ],
            "droppedCount": listed["dropped_count"],
        }
    )
    return out


def main() -> int:
    for relative, data in build().items():
        path = CONTRACTS / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"wrote {relative} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

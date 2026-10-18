"""The browser's public DTOs, pinned to the desktop's contract fixtures."""

from __future__ import annotations

import base64
import json
import re
import struct
from typing import Any

from sharpmod.bridge import params as p

COMMANDS = {
    "get_sounding_progress": "soundings.progress",
    "get_storage_usage": "storage.usage",
    "clear_cache": "storage.clear",
    "get_station_catalog": "stations.catalog",
    "get_model_catalog": "models.catalog",
    "get_model_status": "models.status",
    "inspect_wrf": "soundings.inspect_wrf",
    "get_map_catalog": "map.catalog",
    "get_weather_field": "fields.grid",
    "get_spc_outlook": "outlooks.spc",
    "get_weather_alerts": "alerts.weather",
    "get_radar_frame": "radar.mosaic",
    "get_radar_site_frame": "radar.site",
    "get_radar_sites": "radar.sites",
    "get_radar_tilts": "radar.tilts",
    "get_satellite_frame": "satellite.goes",
    "get_storm_reports": "reports.storms",
    "get_report_options": "reports.options",
    "get_field_contours": "fields.contours",
    "get_surface_plots": "surface.plots",
    "get_place_labels": "places.labels",
    "get_station_availability": "stations.availability",
    "get_sounding_analysis": "soundings.analysis",
    "get_sounding_trends": "soundings.trends",
    "render_sounding": "soundings.render",
    "render_sounding_view": "soundings.view",
    "get_threshold_metrics": "thresholds.metrics",
    "evaluate_threshold": "thresholds.evaluate",
    "export_threshold_csv": "thresholds.export_csv",
}
FRAME_KINDS = {
    "get_weather_field": "field",
    "get_radar_frame": "radar",
    "get_radar_site_frame": "radar-site",
    "get_satellite_frame": "satellite",
}


def camel(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            re.sub(r"_([a-z])", lambda match: match.group(1).upper(), key): camel(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [camel(item) for item in value]
    return value


def shrf(header: dict[str, Any], body: bytes) -> bytes:
    """SHRF v1, including the desktop's eight-byte body alignment."""
    text = json.dumps(
        header, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    text += b" " * ((-(12 + len(text))) % 8)
    return b"SHRF" + struct.pack("<HHI", 1, 0, len(text)) + text + body


def public_station(
    record: dict[str, Any], availability: str = "unknown"
) -> dict[str, Any]:
    return {
        "id": record["id"],
        "name": record["name"],
        "latitude": record["lat"],
        "longitude": record["lon"],
        "source": record["src"],
        "availability": availability,
    }


def bridge_request(command: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if command == "open_sounding":
        p.keys(args, ("request",), ("progressId",))
        request = args["request"]
        if not isinstance(request, dict):
            raise p.invalid("request must be an object")
        kind = p.choice(request, "kind", ("observed", "model", "era5", "file", "wrf"), "")
        method = f"soundings.open_{kind}"
        progress_id = args.get("progressId")
        args = {key: value for key, value in request.items() if key != "kind"}
        if progress_id is not None:
            args["progressId"] = progress_id
    else:
        method = COMMANDS[command]
    renamed = {"site": "site_id", "ascent": "parcel"}
    return method, {
        renamed.get(
            key, re.sub(r"([A-Z])", lambda m: "_" + m.group(1).lower(), key)
        ): value
        for key, value in args.items()
    }


def public_result(command: str, result: dict[str, Any]) -> dict[str, Any] | bytes:
    if command in FRAME_KINDS or command == "render_sounding":
        encoded_key = "data_base64" if command == "get_weather_field" else "png_base64"
        body = base64.b64decode(result[encoded_key], validate=True)
        if encoded_key == "png_base64":
            if len(body) != result["byte_length"] or not body.startswith(
                b"\x89PNG\r\n\x1a\n"
            ):
                raise ValueError("invalid provider PNG")
        elif len(body) != result["width"] * result["height"] * 2:
            raise ValueError("invalid provider grid size")
        if command == "render_sounding":
            return body
        header = {
            key: value
            for key, value in result.items()
            if key not in (encoded_key, "byte_length")
        }
        return shrf({"kind": FRAME_KINDS[command], **camel(header)}, body)
    public = camel(result)
    if command == "get_station_catalog":
        public["stations"] = [public_station(station) for station in result["stations"]]
        public["origin"] = (
            "seedFallback" if result["origin"] == "seed_fallback" else result["origin"]
        )
        public["availabilityBasis"] = {"kind": "catalogOnly"}
    elif command == "get_station_availability":
        public["extraStations"] = [
            public_station(station, "listed") for station in result["extra_stations"]
        ]
    for collection in ("places", "sites", "reports"):
        for record in public.get(collection, []):
            record["latitude"] = record.pop("lat")
            record["longitude"] = record.pop("lon")
    return public

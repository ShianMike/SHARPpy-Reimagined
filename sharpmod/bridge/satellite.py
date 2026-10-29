"""GOES satellite imagery as a map frame.

One handler calls ``providers.satellite_context`` and passes its rendered
plate-carree PNG on as geography and provenance; the frontend draws it. The
frame is the provider's own geolocation of the GOES fixed grid: pixels outside
the scan are transparent and cannot masquerade as a valid frame over an
uncovered region.
"""

from __future__ import annotations

import base64
from typing import Any

from sharpmod.bridge import cancellation, params as p
from sharpmod.bridge.errors import BridgeError

#: The render window: HRRR's coverage bounds, the map's own fixed extent. The
#: radar mosaic documents the same decision -- one request serves every panel,
#: and panning or zooming costs nothing because only the corners move.
FRAME_BOUNDS = (-134.5, -60.5, 20.5, 53.0)
#: As for the sounding image: base64 keeps the reply under the 8 MiB frame.
MAX_IMAGE_BYTES = 6 * 1024 * 1024 - 4096
#: The imagery channels the provider publishes, as request keys.
CHANNELS = ("visible", "infrared")


def _short(exc: Exception) -> str:
    text = str(exc).strip()
    return text.splitlines()[0][:200] if text else type(exc).__name__


def goes_frame(params: dict[str, Any]) -> dict[str, Any]:
    """The GOES frame nearest ``valid_time``, rendered over the map's extent."""
    p.keys(params, ("valid_time",), ("channel",))
    when = p.time(params, "valid_time")
    channel = p.choice(params, "channel", CHANNELS, "infrared")
    from sharpmod.providers import satellite_context as sat

    try:
        overlay = sat.fetch_satellite_overlay(
            when,
            bounds=FRAME_BOUNDS,
            channel=channel,
            cancel=cancellation.event(),
        )
    except sat.SatelliteContextError as exc:
        cancellation.check()
        raise BridgeError(
            "OVERLAY_FETCH_FAILED",
            f"The GOES satellite frame could not be loaded: {_short(exc)}.",
            True,
        ) from None
    raster = overlay.raster
    data = bytes(raster.image_bytes)
    if len(data) > MAX_IMAGE_BYTES or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise BridgeError(
            "OVERLAY_FETCH_FAILED",
            "The GOES service did not send a usable PNG frame.",
            True,
        )
    west, east, south, north = (float(value) for value in raster.bounds)
    channel_title = sat.CHANNELS[channel][1]
    return {
        "product": channel,
        "satellite": overlay.satellite,
        "label": f"{overlay.satellite} {channel_title}",
        "units": "",
        "bounds": {"west": west, "east": east, "south": south, "north": north},
        "requested_time": p.iso(overlay.requested_time),
        "retrieved_at": p.iso(raster.retrieved_at),
        "valid_time": p.iso(overlay.selected_time),
        "update_interval_s": float(raster.update_interval_s),
        "coverage_fraction": float(overlay.coverage_fraction),
        "native_resolution_km": float(overlay.native_resolution_km),
        "attribution": raster.attribution,
        "source_url": raster.source_url,
        "byte_length": len(data),
        "png_base64": base64.b64encode(data).decode("ascii"),
    }


HANDLERS = {
    "satellite.goes": goes_frame,
}

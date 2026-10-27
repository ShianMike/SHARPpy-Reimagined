"""Sounding operations: open, parameters, image, and the forecast-model list.

Every value comes from sharpmod: observed soundings from
``providers.observations``, model soundings from ``tools.model_extract``,
parameters from ``analysis.profile_metrics`` and ``sharptab.hazard``, and the
image from ``rendering.cli.render`` in the Qt render worker. These modules only
validate requests, move data between those functions, and shape the results.
They import sharpmod inside each handler, so importing them stays cheap and
never loads Qt.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta
from typing import Any

from sharpmod.bridge import params as p
from sharpmod.bridge.errors import BridgeError
from sharpmod.bridge.sounding_analysis import analysis, trends
from sharpmod.bridge.sounding_cache import cache
from sharpmod.bridge.sounding_open import (
    open_era5, open_file, open_model, open_observed,
)
from sharpmod.bridge.sounding_view import render_view
from sharpmod.bridge.sounding_progress import get_progress
from sharpmod.bridge.sounding_wrf import inspect_wrf, open_wrf

#: Base64 makes the PNG a third larger; this keeps the whole reply frame, its
#: other fields included, under the protocol's 8 MiB cap.
MAX_IMAGE_BYTES = 6 * 1024 * 1024 - 4096
RENDER_PARCELS = ("SFC", "ML", "FCST", "MU", "EFF")
IMAGE_MODES = ("hd", "uhd")
RECENT_RUN_COUNT = 16


def _png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise BridgeError(
            "RENDER_FAILED", "The sounding renderer did not produce a PNG image.", False
        )
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def render(params: dict[str, Any]) -> dict[str, Any]:
    """The SPC-style sounding image, drawn by ``sharpmod.rendering.cli.render``."""
    p.keys(params, ("sounding_id",), ("parcel", "image_mode"))
    entry = cache().get(params["sounding_id"])
    parcel = p.choice(params, "parcel", RENDER_PARCELS, "MU")
    mode = p.choice(params, "image_mode", IMAGE_MODES, "hd")
    out = cache().render_path(entry.sounding_id, parcel, mode)
    with entry.render_lock:
        if not out.exists():
            from sharpmod.bridge.render_client import client

            client().render(entry.path, out, parcel, mode)
        data = out.read_bytes()
    if len(data) > MAX_IMAGE_BYTES:
        raise BridgeError(
            "RENDER_FAILED", "The sounding image is too large to send.", False
        )
    width, height = _png_size(data)
    return {
        "sounding_id": entry.sounding_id,
        "parcel": parcel,
        "image_mode": mode,
        "width": width,
        "height": height,
        "byte_length": len(data),
        "png_base64": base64.b64encode(data).decode("ascii"),
    }


def _recent_runs(cycles: tuple[int, ...], now: datetime, count: int) -> list[datetime]:
    hours = sorted({int(h) % 24 for h in cycles}) or [0]
    moment = now.replace(minute=0, second=0, microsecond=0)
    runs: list[datetime] = []
    while len(runs) < count:
        if moment.hour in hours:
            runs.append(moment)
        moment -= timedelta(hours=1)
    return runs


def model_status(params: dict[str, Any]) -> dict[str, Any]:
    """Check provider inventories without decoding map or sounding data."""
    p.keys(params, ("model", "run_time", "forecast_hour"))
    from sharpmod.tools import model_extract as mx

    key = p.text(params, "model", 1, 40)
    config = next((cfg for cfg in mx.available_models() if cfg.key == key), None)
    if key == "aigfs":
        config = mx.get_config(key)
    if config is None:
        raise p.invalid("model is not in the forecast catalogue")
    run = p.time(params, "run_time")
    hour = p.integer(params, "forecast_hour", 0, 1000)
    if run.minute or run.second or run.microsecond or run.hour not in config.cycles:
        raise p.invalid("run_time must be a model cycle")
    if hour not in mx.forecast_hours(config, cycle_hour=run.hour):
        raise p.invalid("forecast_hour is not published by this cycle")
    provider = mx.provider_capability(config, cycle_hour=run.hour).provider
    if key == "aigfs":
        from sharpmod.providers import hrrr_field as hf
        provider = "NOAA/NCEP NOMADS"
        try:
            rows = hf.fetch_inventory(hf.grib_url(run, hour, "sfc", key), timeout=2.0)
            state = "available" if rows else "unavailable"
            message = "Surface map inventory found. Maps only; soundings are unavailable." if rows else "No surface map inventory found for this run and hour."
        except hf.HrrrFieldUnavailable:
            state, message = "unavailable", "Map fields are not published for this run and hour."
        except hf.HrrrFieldError:
            state, message = "unknown", "Map availability could not be confirmed."
    elif key in mx.OPENMETEO_POINT_KEYS:
        state = "configured"
        message = "Provider configured. Live data is confirmed when you fetch a sounding."
    else:
        try:
            result = mx.probe(key, run_time=run, fxx=hour, open_subset=False,
                              request_timeout=2.0, deadline_seconds=8.0)
        except Exception:
            logging.getLogger(__name__).exception("models.status failed for %s", key)
            result = {"error": "probe failed"}
        if result.get("available"):
            state = "partial" if result.get("surface_contract_complete") is False else "available"
            message = ("Inventory found; required surface fields are incomplete." if state == "partial"
                       else "Provider inventory found for this cycle and forecast hour.")
        elif result.get("error"):
            state = "unknown"
            message = "Provider check failed; availability could not be confirmed."
            logging.getLogger(__name__).warning("models.status %s: %s", key, result["error"])
        else:
            state = "unavailable"
            message = "No provider inventory found for this cycle and forecast hour."
    return {"model": key, "provider": provider, "run_time": p.iso(run),
            "forecast_hour": hour, "state": state, "message": message,
            "checked_at": p.iso(p.now())}


def models_catalog(params: dict[str, Any]) -> dict[str, Any]:
    """Forecast models and their sounding capability, with recent runs."""
    p.keys(params, ())
    from sharpmod.tools import model_extract as mx

    now = p.now()
    from sharpmod.analysis.ensemble_members import member_labels

    models: list[dict[str, Any]] = []
    for config in (*mx.available_models(), mx.get_config("aigfs")):
        runs = _recent_runs(config.cycles, now, RECENT_RUN_COUNT)
        # Publication lags the cycle: hourly models by about two hours, the others
        # by about five. The suggestion is the newest run older than that.
        lag = timedelta(hours=2 if len(set(config.cycles)) >= 8 else 5)
        if config.key == "ecmwf-ifs-9km":
            # shortcut: conservative 10 h estimate; probe publication if the schedule changes.
            lag = timedelta(hours=10)
        suggested = next((run for run in runs if run <= now - lag), runs[-1])
        west, east, south, north = (float(v) for v in config.domain_bounds)
        models.append(
            {
                "key": config.key,
                "label": config.label,
                "soundings_available": not bool(config.unavailable_reason),
                "domain": config.domain,
                "bounds": {"west": west, "east": east, "south": south, "north": north},
                "outline": [
                    [float(lon), float(lat)] for lon, lat in config.domain_outline
                ],
                "cycles": sorted({int(h) for h in config.cycles}),
                "forecast_hours": [int(h) for h in config.fxx_values]
                or [int(config.default_fxx)],
                "max_forecast_hours": [
                    max(mx.forecast_hours(config, cycle_hour=hour))
                    if hour in config.cycles else 0 for hour in range(24)
                ],
                "default_forecast_hour": int(config.default_fxx),
                "grid_spacing_km": float(config.grid_spacing_km) or None,
                "notes": "Maps only. Point soundings are unavailable." if config.key == "aigfs" else str(config.notes or ""),
                "members": list(member_labels(config.key) or ()),
                "recent_runs": [p.iso(run) for run in runs],
                "suggested_run": p.iso(suggested),
            }
        )
    return {"models": models, "generated_at": p.iso(now)}


HANDLERS = {
    "soundings.progress": get_progress,
    "models.catalog": models_catalog,
    "models.status": model_status,
    "soundings.open_observed": open_observed,
    "soundings.open_model": open_model,
    "soundings.open_era5": open_era5,
    "soundings.open_file": open_file,
    "soundings.inspect_wrf": inspect_wrf,
    "soundings.open_wrf": open_wrf,
    "soundings.analysis": analysis,
    "soundings.trends": trends,
    "soundings.render": render,
    "soundings.view": render_view,
}

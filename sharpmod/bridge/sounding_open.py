"""Open soundings through the common decoder and process-owned cache."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sharpmod.bridge import cancellation, params as p
from sharpmod.bridge.errors import BridgeError
from sharpmod.bridge.sounding_cache import cache
from sharpmod.bridge.sounding_progress import track
from sharpmod.bridge.sounding_summary import decode, meta, register, summary
from sharpmod.models.model_transport import DownloadCancelled

log = logging.getLogger("sharpmod.bridge")

MAX_UPLOAD_BYTES = 5 * 1024 * 1024


def open_observed(params: dict[str, Any]) -> dict[str, Any]:
    """Open one observed profile through the native provider transaction."""
    from sharpmod_rs import open_observed_sounding

    return open_observed_sounding(params)


def open_model(params: dict[str, Any]) -> dict[str, Any]:
    """Open a forecast profile through the native cache transaction."""
    from sharpmod_rs import open_model_sounding

    return open_model_sounding(params)


def open_era5(params: dict[str, Any]) -> dict[str, Any]:
    """Open an ERA5 profile through the native cache transaction."""
    from sharpmod_rs import open_era5_sounding

    return open_era5_sounding(params)


def open_file(params: dict[str, Any]) -> dict[str, Any]:
    """Stage a file through Rust while retaining the existing decoder contracts."""
    from sharpmod_rs import open_sounding_file

    return open_sounding_file(params, MAX_UPLOAD_BYTES)

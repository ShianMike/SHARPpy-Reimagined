"""Immutable map grids and winds, retained beside the existing map-image cache."""

from __future__ import annotations

import gzip
import json
import zlib
from datetime import datetime
from pathlib import Path
from typing import Any

from sharpmod.providers import hrrr_field


def path(kind: str, model: str, product: str, run: datetime, hour: int,
         size: tuple[int, int]) -> Path:
    # Version the derived representation separately from the provider's raw data.
    return hrrr_field.disk_cache_root() / (
        f"{kind}-v1_{model}_{product}_{run:%Y%m%d%H}_f{hour:03d}_{size[0]}x{size[1]}.json.gz"
    )


def read(target: Path) -> dict[str, Any] | None:
    data = hrrr_field._disk_read(target)
    if data is not None:
        try:
            value = json.loads(gzip.decompress(data))
            if isinstance(value, dict):
                return value
        except (OSError, ValueError, EOFError, zlib.error):
            pass  # An interrupted or corrupt cache must be replaceable by a fresh download.
    return None


def write(target: Path, value: dict[str, Any]) -> dict[str, Any]:
    hrrr_field._disk_write(target, gzip.compress(
        json.dumps(value, separators=(",", ":"), allow_nan=False).encode(),
        compresslevel=1, mtime=0,
    ))
    return value

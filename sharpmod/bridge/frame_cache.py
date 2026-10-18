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


def cached_hours(params):
    """Hours with every selected compact field and required auxiliary file on disk."""
    from sharpmod.bridge import fields, params as p, storage

    p.keys(params, ("model", "products", "run_time", "hours"))
    products, hours = params["products"], params["hours"]
    if not isinstance(products, list) or not 1 <= len(products) <= 4:
        raise p.invalid("products must contain 1 to 4 product keys")
    if not isinstance(hours, list) or not 1 <= len(hours) <= 85:
        raise p.invalid("hours must contain 1 to 85 forecast hours")
    selections = [fields._request({"model": params["model"], "product": product,
                                   "run_time": params["run_time"], "forecast_hour": 0})
                  for product in products]
    hours = [p.integer({"hour": hour}, "hour", 0, 84) for hour in hours]
    hrrr_field._prune_disk_cache()
    present = {target for target, size, ready in storage._files(hrrr_field.disk_cache_root(), "*.json.gz") if size > 0 and ready}
    result = []
    for hour in sorted(set(hours)):
        needed = [path(kind, model, product.key, run, hour, fields.FRAME)
                  for model, product, run, _ in selections
                  for kind in (["grid", "contours"] if product.contour is not None or product.wind else ["grid"])]
        if all(target in present for target in needed):
            result.append(hour)
    return result

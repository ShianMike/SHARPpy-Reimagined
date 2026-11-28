"""Immutable map grids and winds, retained beside the existing map-image cache."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any


def path(kind: str, model: str, product: str, run: datetime, hour: int,
         size: tuple[int, int], *, legacy: int = 0) -> Path:
    import sharpmod_rs
    from sharpmod.providers import hrrr_field

    version = sharpmod_rs.frame_cache_version(kind, model, product, legacy)
    return hrrr_field.disk_cache_root() / (
        f"{kind}-v{version}_{model}_{product}_{run:%Y%m%d%H}_f{hour:03d}_{size[0]}x{size[1]}.json.gz"
    )


def read(target: Path) -> dict[str, Any] | None:
    import sharpmod_rs
    from sharpmod.providers import hrrr_field

    data = hrrr_field._disk_read(target)
    if data is not None:
        return sharpmod_rs.frame_cache_read_gzip(data)
    return None


def write(target: Path, value: dict[str, Any]) -> dict[str, Any]:
    import sharpmod_rs
    from sharpmod.providers import hrrr_field

    hrrr_field._disk_write(target, sharpmod_rs.frame_cache_compress_gzip(value))
    return value


def cached_hours(params):
    """Hours with every selected compact field and required auxiliary file on disk."""
    from sharpmod.bridge import fields, params as p, storage
    from sharpmod.providers import hrrr_field

    p.keys(params, ("model", "products", "run_time", "hours"))
    products, hours = params["products"], params["hours"]
    if not isinstance(products, list) or not 1 <= len(products) <= 4:
        raise p.invalid("products must contain 1 to 4 product keys")
    if not isinstance(hours, list) or not 1 <= len(hours) <= 385:
        raise p.invalid("hours must contain 1 to 385 forecast hours")
    selections = [fields._resolve_request({"model": params["model"], "product": product,
                                           "run_time": params["run_time"], "forecast_hour": 0}, require_interval=False)
                  for product in products]
    hours = [fields._resolve_request({"model": params["model"], "product": products[0],
                                      "run_time": params["run_time"], "forecast_hour": hour}, require_interval=False)[-1]
             for hour in hours]
    first_hour = max(product.max_hours for _, product, _, _, _ in selections)
    hrrr_field._prune_disk_cache()
    present = {target for target, size, ready in storage._files(hrrr_field.disk_cache_root(), "*.json.gz") if size > 0 and ready}
    result = []
    for hour in sorted(set(hours)):
        if hour < first_hour:
            continue
        needed = [
            tuple(
                path(kind, model, requested_key if kind == "grid" else product.key, run, hour, fields.FRAME, legacy=revision)
                for revision in range(3 if kind == "contours" else 2)
            )
            for model, product, requested_key, run, _ in selections
            for kind in (["grid", "contours"] if product.contour is not None or product.wind else ["grid"])
        ]
        if all(any(target in present for target in candidates) for candidates in needed):
            result.append(hour)
    return result

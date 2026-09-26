"""Acquisition, rendering, and cache orchestration for HRRR map-field overlays.

Field definitions and the provider-facing API stay in ``hrrr_field``; image transforms
live in ``hrrr_field_images`` and downloads use shared model transport helpers."""

from __future__ import annotations

from datetime import datetime
from datetime import timedelta
from datetime import timezone
from sharpmod.providers.hrrr_products import HrrrProduct
from sharpmod.providers.hrrr_products import SOURCE_PRESSURE
from sharpmod.providers.hrrr_products import SOURCE_SURFACE
from sharpmod.providers.hrrr_products import get_product
from sharpmod.maps.map_overlays import OverlayRaster
import numpy as np
import time
from sharpmod.providers import hrrr_field as _api


def _download_fields(product: HrrrProduct, run: datetime, fxx: int, *,
                     opener, timeout: float, should_cancel) -> dict[str, np.ndarray]:
    """Fetch and decode every record the product needs, grouped per source file.

    Records are located in the inventory, coalesced into as few HTTP ranges as
    possible, then sliced back apart by offset so each message is decoded as
    itself. A product spanning both output files makes one inventory request and
    one range request per file in the common case.
    """
    eccodes = _api._load_eccodes()
    decoded: dict[str, np.ndarray] = {}

    # Plan every range across both files first, so they can all go out together
    # rather than one file's requests waiting on the other's.
    jobs: list[tuple[str, int, int, tuple[_api.InventoryRow, ...], dict[int, str]]] = []
    for source in (SOURCE_SURFACE, SOURCE_PRESSURE):
        specs = [spec for spec in product.all_fields if spec.source == source]
        if not specs:
            continue
        if should_cancel and should_cancel():
            return {}
        url = _api.grib_url(run, fxx, source)
        rows = _api.fetch_inventory(url, opener=opener, timeout=timeout)
        located = [(_api.locate(rows, spec), spec.name) for spec in specs]
        by_offset = {row.offset: name for row, name in located}
        for start, end, members in _api._merge_ranges([row for row, _ in located]):
            stop = (start + _api._TAIL_BUDGET) if not end else end
            jobs.append((url, start, stop, tuple(members), by_offset))

    if should_cancel and should_cancel():
        return {}

    def fetch(job):
        url, start, stop, _members, _names = job
        return opener(url, timeout, stop - start + 4096, (start, stop))

    payloads: list[bytes | None] = [None] * len(jobs)
    if len(jobs) == 1:
        payloads[0] = fetch(jobs[0])
    else:
        from concurrent.futures import ThreadPoolExecutor
        workers = min(_api._RANGE_WORKERS, len(jobs))
        with ThreadPoolExecutor(max_workers=workers,
                               thread_name_prefix="hrrr-range") as pool:
            futures = {pool.submit(fetch, job): position
                       for position, job in enumerate(jobs)}
            for future in futures:
                position = futures[future]
                payloads[position] = future.result()

    if should_cancel and should_cancel():
        return {}

    # Decoding stays serial: it is CPU-bound rather than latency-bound, and one
    # eccodes handle at a time keeps this independent of the C library's
    # threading guarantees.
    for (url, start, _stop, members, by_offset), payload in zip(jobs, payloads):
        if payload is None:
            continue
        for row in members:
            name = by_offset.get(row.offset)
            if name is None:
                continue
            begin = row.offset - start
            finish = (begin + row.length) if row.length else len(payload)
            if begin < 0 or begin >= len(payload):
                continue
            decoded[name] = _api.decode_message(payload[begin:finish], eccodes)

    missing = [spec.name for spec in product.all_fields
               if spec.name not in decoded]
    if missing:
        raise _api.HrrrFieldUnavailable(
            "HRRR did not return " + ", ".join(sorted(missing)))
    return decoded


def render_field(product: HrrrProduct, decoded: dict[str, np.ndarray], *,
                 size: tuple[int, int], bounds=_api.COVERAGE_BOUNDS,
                 remember_numeric: bool = True) -> bytes:
    """Derive, reproject, colour and encode a product from decoded records.

    With ``remember_numeric`` (the default) the derived display-units grid is
    also remembered in the bounded derived cache under the resolved run, hour,
    and frame size, so synchronous map inspection reads the same numbers the
    render coloured -- never a colour lookup. Disk frames carry only the PNG;
    numeric probes of a disk-loaded frame find no derived grid and say so.
    """
    field = np.asarray(product.derive(decoded), dtype=np.float32)
    if field.shape != _api.HRRR_SHAPE:
        raise _api.HrrrFieldError(
            f"{product.key} derived a {field.shape} field, wanted {_api.HRRR_SHAPE}")
    mapping = _api.index_map(size, bounds)
    projected = _api.reproject(field, mapping)
    rgba = _api.colourize(projected, product.palette)

    if product.contour is not None:
        spec = product.contour
        raw = decoded.get(spec.name)
        if raw is not None:
            values = raw if spec.convert is None else spec.convert(raw)
            _api.draw_contours(rgba, _api.reproject(np.asarray(values, dtype=np.float32),
                                          mapping),
                          spec.interval, spec.colour, spec.width)
    payload = _api.encode_png(rgba)
    if remember_numeric:
        try:
            run_iso = decoded.get("__run_iso")
            run_fxx = decoded.get("__run_fxx")
            if run_iso is not None and run_fxx is not None:
                try:
                    run = datetime.fromisoformat(str(run_iso))
                except ValueError:
                    run = str(run_iso)
                _api.remember_derived(product.key, run, int(run_fxx), tuple(size),
                                 np.asarray(projected, dtype=np.float32))
        except Exception:  # noqa: BLE001 - a PNG never depends on the cache
            pass
    return payload


def fetch_field(product_key: str | None, *, valid_time: datetime | None = None,
                run: datetime | None = None, fxx: int | None = None,
                size=_api.DEFAULT_FRAME_SIZE, opacity: float = 0.75,
                now: datetime | None = None, opener=None,
                should_cancel=None) -> OverlayRaster | None:
    """Produce one product as a map overlay.

    The forecast is chosen one of two ways. Passing ``run`` pins the request to
    that exact cycle and ``fxx``, which is what the Forecast Model tab does so
    the field matches the run the sounding will come from. Passing only
    ``valid_time`` asks for the freshest run that reaches that hour, which is
    what the observed tab wants: there is no cycle selection there, only a
    moment. Either way the returned raster's subtitle states the run, the
    forecast hour, and the valid time, so the map cannot imply a currency it
    does not have.

    Returns ``None`` only when ``should_cancel`` asked us to stop; every real
    failure raises :class:`HrrrFieldError`. That split is what lets the calling
    worker tell "the user moved on" apart from "this product is broken".
    """
    product = get_product(product_key)
    frame = _api._clamp_size(size)
    if run is not None:
        run, fxx = _api.pin_request(run, fxx)
    else:
        run, fxx = _api.resolve_request(valid_time, now=now)
    timeout, _limit = _api._remote_limits()
    call = opener or _api._default_opener

    cache_key = (product.key, run.isoformat(), fxx, frame)
    return _api._singleflight_field(
        cache_key,
        lambda: _api._fetch_resolved_field(
            product, run, fxx, frame, opacity, now, call, timeout,
            should_cancel, cache_key),
        should_cancel)


def _fetch_resolved_field(product, run, fxx, frame, opacity, now, call,
                          timeout, should_cancel, cache_key):
    """Look up or build an exact frame while its per-key flight is held."""
    moment = time.monotonic()
    with _api._CACHE_LOCK:
        cached = _api._CACHE.get(cache_key)
    if cached is not None:
        age = moment - cached[0]
        raster = cached[1]
        if isinstance(raster, _api._UnpublishedFrame):
            if age < _api.FAILURE_CACHE_TTL_S:
                raise _api.HrrrFieldUnavailable(raster.message)
        elif age < _api.FRAME_CACHE_TTL_S:
            return raster if raster.opacity == opacity \
                else raster.at_opacity(opacity)

    disk = _api._disk_path(product.key, run, fxx, frame)
    payload = _api._disk_read(disk)
    from_disk = payload is not None

    if payload is None:
        if should_cancel and should_cancel():
            return None
        try:
            decoded = _api._download_fields(product, run, fxx, opener=call,
                                       timeout=timeout,
                                       should_cancel=should_cancel)
        except _api.HrrrFieldUnavailable as error:
            with _api._CACHE_LOCK:
                # Keep a previously delivered image for offline fallback even
                # if a later refresh finds its remote object temporarily absent.
                old = _api._CACHE.get(cache_key)
                if old is None or isinstance(old[1], _api._UnpublishedFrame):
                    if old is None and len(_api._CACHE) >= _api._CACHE_MAX_ENTRIES:
                        oldest = min(_api._CACHE, key=lambda key: _api._CACHE[key][0])
                        _api._CACHE.pop(oldest, None)
                    _api._CACHE[cache_key] = (
                        time.monotonic(), _api._UnpublishedFrame(str(error)))
            raise
        if not decoded:
            return None
        if should_cancel and should_cancel():
            return None
        try:
            decoded["__run_iso"] = run.isoformat()
            decoded["__run_fxx"] = int(fxx)
        except Exception:  # noqa: BLE001 - numeric cache tags are advisory
            pass
        payload = _api.render_field(product, decoded, size=frame)

    valid = run + timedelta(hours=int(fxx))
    subtitle = (f"{run:%d %b %HZ} run \u00b7 F{int(fxx):02d} \u00b7 "
                f"valid {valid:%d %b %HZ}")
    try:
        raster = OverlayRaster(
            key=_api.OVERLAY_KEY,
            title=f"HRRR {product.label}",
            image_bytes=payload,
            bounds=_api.COVERAGE_BOUNDS,
            subtitle=subtitle,
            short_name=product.key,
            valid_time=valid,
            retrieved_at=datetime.now(timezone.utc),
            update_interval_s=3600.0,
            opacity=opacity,
            source_url=_api.grib_url(run, fxx, product.sources[0]),
            attribution=_api.ATTRIBUTION,
        )
    except ValueError as error:
        raise _api.HrrrFieldError(f"field image rejected: {error}") from error

    if not from_disk:
        # Only a finished run is immutable enough to keep.
        if run < _api.latest_run(now):
            _api._disk_write(disk, payload)
    with _api._CACHE_LOCK:
        if cache_key not in _api._CACHE and len(_api._CACHE) >= _api._CACHE_MAX_ENTRIES:
            oldest = min(_api._CACHE, key=lambda key: _api._CACHE[key][0])
            _api._CACHE.pop(oldest, None)
        _api._CACHE[cache_key] = (time.monotonic(), raster)
    return raster

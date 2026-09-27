"""Time-valid SPC outlook selection and overlay retrieval.

A layer is returned only when the product's own valid/expiry window covers the requested
time; shared product definitions and the provider-facing API remain in ``spc_outlook``."""

from __future__ import annotations

from datetime import datetime
from datetime import timezone
from sharpmod.maps.map_overlays import OverlayLayer
from typing import Callable
import urllib.request
from sharpmod.providers import spc_outlook as _api


def fetch_layer(
        valid_time: datetime,
        *,
        now: datetime | None = None,
        product: str = _api.DEFAULT_PRODUCT,
        opener: Callable[..., bytes] | None = None,
        should_cancel: Callable[[], bool] | None = None,
) -> OverlayLayer | None:
    """Return the outlook layer covering ``valid_time``, or ``None``.

    ``None`` means "SPC published nothing that covers this time" -- before the
    2020 archive, beyond Day 3, or a quiet day with no outlook on file. That is
    an ordinary answer, not an error, and it is cached so repeated scrubbing
    over such a period stays local.

    A candidate is accepted only once its own ``VALID``/``EXPIRE`` window is
    confirmed to contain ``valid_time``.  The live endpoints in particular
    advance without warning, so trusting the URL alone would eventually draw
    yesterday's outlook over today's sounding.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    candidates = _api.candidates_for(valid_time, now, product)
    if not candidates:
        return None
    if _api._day_miss_cached(candidates):
        # This exact candidate set has already been walked to exhaustion.
        return None

    fallback: OverlayLayer | None = None
    exhausted = True
    for request in candidates:
        if should_cancel is not None and should_cancel():
            return None

        hit, cached = _api._cache_get(request.url)
        if hit:
            if cached is None:
                continue
            if cached.covers(valid_time):
                return cached
            fallback = fallback or cached
            continue

        # An archived issuance is immutable, so a previous session's copy is
        # as good as a fresh request and costs no network at all.
        from_disk = None if request.live else _api._disk_read(request.url)
        if from_disk is not None:
            try:
                layer = _api.parse_outlook(
                    from_disk, source_url=request.url, day=request.day,
                    label=request.label, product=product)
            except _api.OutlookError:
                layer = None
            if layer:
                _api._cache_put(request.url, layer, _api.ARCHIVE_CACHE_TTL_S)
                if layer.covers(valid_time):
                    return layer
                fallback = fallback or layer
                continue

        try:
            payload = _api._fetch_bytes(request.url, opener)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # Not yet issued, or never existed. Remember it and move on.
                _api._cache_put(request.url, None, _api.MISS_CACHE_TTL_S)
                continue
            # A server-side error says nothing about whether the product
            # exists, so this walk must not be recorded as a settled "nothing
            # here" verdict that suppresses retries for hours.
            exhausted = False
            _api.logger.debug(
                "spc_outlook.http_error url=%s code=%s", request.url, exc.code)
            continue
        except (urllib.error.URLError, OSError, ValueError, _api.OutlookError) as exc:
            exhausted = False
            _api.logger.debug("spc_outlook.fetch_failed url=%s err=%s",
                         request.url, exc)
            continue

        try:
            layer = _api.parse_outlook(
                payload,
                source_url=request.url,
                day=request.day,
                label=request.label,
                product=product,
            )
        except _api.OutlookError as exc:
            _api.logger.debug("spc_outlook.decode_failed url=%s err=%s",
                         request.url, exc)
            _api._cache_put(request.url, None, _api.MISS_CACHE_TTL_S)
            continue

        ttl = _api.LIVE_CACHE_TTL_S if request.live else _api.ARCHIVE_CACHE_TTL_S
        _api._cache_put(request.url, layer if layer else None, ttl)
        if layer and not request.live:
            _api._disk_write(request.url, payload)

        if not layer:
            continue
        if layer.covers(valid_time):
            return layer
        # A real product that does not cover the target: keep it only as a last
        # resort and keep looking for one that does.
        fallback = fallback or layer

    if fallback is not None:
        _api.logger.debug(
            "spc_outlook.window_mismatch valid=%s using=%s",
            valid_time, fallback.source_url)
        return fallback
    if exhausted:
        # Every candidate was reachable and none of them held an outlook, so
        # record the verdict once for the whole set instead of relying on the
        # individual 404 tombstones surviving in the per-URL cache.
        _api._remember_day_miss(candidates)
    return None

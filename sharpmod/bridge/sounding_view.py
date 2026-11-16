"""Validated, non-destructive sounding edits and diagnostic selections."""
from __future__ import annotations

import hashlib
import json

MAX_CACHED_VIEWS = 4


def _render_key(parcel, mode, view, color_style):
    return hashlib.sha256(json.dumps(
        [parcel, mode, color_style, {**view, "timeIndex": view.get("timeIndex", 0)}], sort_keys=True,
    ).encode()).hexdigest()


def validate_view(value):
    from sharpmod_rs import validate_sounding_view

    return validate_sounding_view(value)


def render_view(params):
    from sharpmod_rs import render_sounding_view
    from sharpmod.bridge.soundings import MAX_IMAGE_BYTES

    return render_sounding_view(params, MAX_IMAGE_BYTES, MAX_CACHED_VIEWS)

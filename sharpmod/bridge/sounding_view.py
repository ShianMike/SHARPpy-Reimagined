"""Validated, non-destructive sounding edits and diagnostic selections."""
from __future__ import annotations

import base64
import hashlib
import json
import tempfile
from pathlib import Path

from sharpmod.bridge import params as p
from sharpmod.bridge.sounding_cache import cache, log

MAX_CACHED_VIEWS = 4


def _render_key(parcel, mode, view, color_style):
    return hashlib.sha256(json.dumps(
        [parcel, mode, color_style, {**view, "timeIndex": view.get("timeIndex", 0)}], sort_keys=True,
    ).encode()).hexdigest()


def validate_view(value):
    from sharpmod_rs import validate_sounding_view

    return validate_sounding_view(value)


def render_view(params):
    from sharpmod.bridge.render_client import client
    from sharpmod.bridge.soundings import COLOR_STYLES, IMAGE_MODES, MAX_IMAGE_BYTES, RENDER_PARCELS, _png_size
    from sharpmod.bridge.errors import BridgeError

    p.keys(params, ("sounding_id", "view"), ("parcel", "image_mode", "color_style"))
    entry = cache().get(params["sounding_id"])
    view = validate_view(params["view"])
    if view.get("timeIndex", 0) >= max(1, entry.summary["time_count"]):
        raise p.invalid("timeIndex is outside this sounding's times")
    parcel = p.choice(params, "parcel", RENDER_PARCELS, "MU")
    mode = p.choice(params, "image_mode", IMAGE_MODES, "uhd")
    style = p.choice(params, "color_style", COLOR_STYLES, "standard")
    key = _render_key(parcel, mode, view, style)
    with entry.render_lock:
        cached = entry.view_renders.get(key)
        if cached is not None and cached[0].is_file():
            out, metadata = cached
            data = out.read_bytes()
            entry.view_renders.move_to_end(key)
        else:
            # Each new view replays a fresh collection; the source file stays intact.
            with tempfile.TemporaryDirectory(prefix="sounding-view-", dir=cache().renders) as directory:
                temporary = Path(directory) / "view.png"
                metadata = client().render(entry.path, temporary, parcel, mode, view=view, color_style=style)
                data = temporary.read_bytes()
                if len(data) > MAX_IMAGE_BYTES:
                    raise BridgeError("RENDER_FAILED", "The sounding image is too large to send.", False)
                _png_size(data)
                resolved = {**view, "edits": metadata["edits"]}
                resolved.pop("drag", None)
                resolved_key = _render_key(parcel, mode, resolved, style)
                out = cache().render_path(entry.sounding_id, f"{parcel}-{resolved_key}", mode)
                temporary.replace(out)
            entry.view_renders[key] = (out, metadata)
            entry.view_renders.move_to_end(key)
            entry.view_renders[resolved_key] = (out, metadata)
            entry.view_renders.move_to_end(resolved_key)
            # shortcut: keep four recent request keys per sounding; expand only with a disk budget.
            while len(entry.view_renders) > MAX_CACHED_VIEWS:
                _, (old, _) = entry.view_renders.popitem(last=False)
                if not any(path == old for path, _ in entry.view_renders.values()):
                    try:
                        old.unlink(missing_ok=True)
                    except OSError:
                        log.warning("could not remove cached sounding image %s", old)
        width, height = _png_size(data)
    return {"image": base64.b64encode(data).decode("ascii"), "width": width,
            "height": height, **metadata}

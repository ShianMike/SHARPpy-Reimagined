"""Validated, non-destructive sounding edits and diagnostic selections."""
from __future__ import annotations

import base64
import hashlib
import json
import tempfile
from pathlib import Path

from sharpmod.bridge import params as p
from sharpmod.bridge.sounding_cache import cache, log

FIELDS = {"tmpc": (-150, 65), "dwpc": (-150, 65), "u": (-350, 350), "v": (-350, 350)}
CHARTS = ("streamwiseness", "srw", "theta", "cape")
PANELS = ("STP STATS", "COND STP", "WINTER", "FIRE", "SHIP", "VROT", "HEIGHT CHARTS")
MAX_CACHED_VIEWS = 4


def _render_key(parcel, mode, view):
    return hashlib.sha256(json.dumps(
        [parcel, mode, {**view, "timeIndex": view.get("timeIndex", 0)}], sort_keys=True,
    ).encode()).hexdigest()


def validate_view(value):
    if not isinstance(value, dict):
        raise p.invalid("view must be an object")
    p.keys(value, ("edits",), ("chart", "panel", "drag", "timeIndex"))
    value = {"chart": "streamwiseness", "panel": "STP STATS", **value}
    p.choice(value, "chart", CHARTS, "streamwiseness")
    p.choice(value, "panel", PANELS, "STP STATS")
    if "timeIndex" in value:
        p.integer(value, "timeIndex", 0, 1_000_000)
    edits = value["edits"]
    if not isinstance(edits, list) or len(edits) > 2000:
        raise p.invalid("edits must contain at most 2000 levels")
    seen = set()
    for edit in edits:
        if not isinstance(edit, dict):
            raise p.invalid("each edit must be an object")
        p.keys(edit, ("index", "values"))
        index = p.integer(edit, "index", -2, 10000)
        if index in seen:
            raise p.invalid("each edited level must occur once")
        seen.add(index)
        values = edit["values"]
        if not isinstance(values, dict) or not values:
            raise p.invalid("edit values must be a nonempty object")
        p.keys(values, (), tuple(FIELDS) if index >= 0 else ("u", "v"))
        for field, number in values.items():
            p.number({field: number}, field, *FIELDS[field])
    drag = value.get("drag")
    if drag is not None:
        if not isinstance(drag, dict):
            raise p.invalid("drag must be an object")
        p.keys(drag, ("target", "x", "y"))
        target = p.text(drag, "target", 1, 30).split(":")
        if len(target) != 2 or target[0] not in ("tmpc", "dwpc", "wind", "storm"):
            raise p.invalid("unknown edit target")
        try:
            index = int(target[1])
        except ValueError:
            raise p.invalid("edit target needs a level") from None
        if not (-2 <= index <= -1 if target[0] == "storm" else 0 <= index <= 10000):
            raise p.invalid("edit target level is invalid")
        p.number(drag, "x", 0, 1)
        p.number(drag, "y", 0, 1)
    return value


def render_view(params):
    from sharpmod.bridge.render_client import client
    from sharpmod.bridge.soundings import IMAGE_MODES, MAX_IMAGE_BYTES, RENDER_PARCELS, _png_size
    from sharpmod.bridge.errors import BridgeError

    p.keys(params, ("sounding_id", "view"), ("parcel", "image_mode"))
    entry = cache().get(params["sounding_id"])
    view = validate_view(params["view"])
    if view.get("timeIndex", 0) >= max(1, entry.summary["time_count"]):
        raise p.invalid("timeIndex is outside this sounding's times")
    parcel = p.choice(params, "parcel", RENDER_PARCELS, "MU")
    mode = p.choice(params, "image_mode", IMAGE_MODES, "uhd")
    key = _render_key(parcel, mode, view)
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
                metadata = client().render(entry.path, temporary, parcel, mode, view=view)
                data = temporary.read_bytes()
                if len(data) > MAX_IMAGE_BYTES:
                    raise BridgeError("RENDER_FAILED", "The sounding image is too large to send.", False)
                _png_size(data)
                resolved = {**view, "edits": metadata["edits"]}
                resolved.pop("drag", None)
                resolved_key = _render_key(parcel, mode, resolved)
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

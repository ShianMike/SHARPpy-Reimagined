"""Qt-free layer identity, status, and ordering for T19 map layers.

This module is the single vocabulary every map tab shares, so Field Panels
cannot fork the state the map tabs describe (T24/T25 must reuse it, not make a
parallel one):

* :class:`LayerEntry` -- one row of the active-layer list: display name,
  group, visibility, opacity, actual time, availability/error state, and the
  occlusion verdict that tells T19.4 apart from a quiet lie.
* Named compatible layer presets (T19.2) with versioned migration, resolving
  through :func:`migrate_preset_choice` so a stored key from another build
  degrades to a working choice.
* The occlusion predicate :func:`occlusion_guard` -- one clear rule for when
  two opaque colour-filled products drawing together misleads, with the
  mitigation stated in words.
* The dependency model (T19.3): storm reports are read against the outlook
  that anticipated them.

No Qt import. The list widget, sliders, and retry wiring stay on the
controllers and the picker; the map keeps the physical paint order. Session
state carries the *choices* (enabled, products, opacities, preset name), never
the fetched frames -- a launch must not replay the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Group order for the active-layer list (T19.1): weather first, then the
#: things read against weather, then the geography that locates them, then what
#: travels onto the sounding's locator inset. Rows are presented in paint order
#: (see :func:`layer_list_rows`); the group names the section each row reads
#: under. Locator rows are never painted on the main map, so they sort last.
LAYER_GROUPS = ("weather fields", "observations/reports/outlooks", "geography",
                "sounding locator")

#: Layers whose products are opaque colour washes over the same pixels. Two of
#: them drawing at cover strength is not twice the information: the top one wins
#: and the list must say so (T19.4).
OPAQUE_FILL_KEYS = ("hrrr_field", "radar_site", "radar_mosaic", "goes_context")

#: Opacity at or above this means a colour-filled layer covers what is beneath
#: it. Sliders already bottom out at 0.20, so "covering" is a real choice, not
#: something a dragged value can stumble into by accident.
OCCLUSION_OPACITY = 0.90

#: Version of the preset vocabulary. Stored presets migrate through
#: :func:`migrate_preset_choice`; a version the code does not know degrades to
#: the default rather than failing the launch.
PRESET_VERSION = 1

#: Named compatible layer presets (T19.2). Each entry names the layers that
#: draw together, in bottom-to-top order, with opacities chosen so the stack
#: stays readable under :func:`occlusion_guard`. Keys are overlay keys; the
#: outlook/reports pair travels together because reports are read against the
#: outlook that anticipated them.
LAYER_PRESETS: dict[str, dict] = {
    "storm environment": {
        "label": "Storm environment",
        "layers": (
            ("hrrr_field", 0.60),
            ("spc_outlook", 1.0),
        ),
        "why": "Model field underneath at reduced opacity; outlook boundaries stay readable above it.",
    },
    " convection watch": {
        "label": "Convection watch",
        "layers": (
            ("radar_site", 0.85),
            ("spc_outlook", 1.0),
        ),
        "why": "Live radar on top of outlook boundaries; storm reports ride along once the outlook draws.",
    },
    "surface context": {
        "label": "Surface context",
        "layers": (
            ("hrrr_field", 0.50),
            ("surface_observations", 1.0),
        ),
        "why": "Station plots are symbols, not a wash, so they read over a dimmed field.",
    },
}

#: The default preset key, normalised without a leading space (see below).
DEFAULT_PRESET_KEY = "storm environment"


def _normalise_preset_entries() -> None:
    """Repair hand-written preset keys in place (tolerate stray spaces)."""
    for key in list(LAYER_PRESETS):
        fixed = " ".join(str(key).split())
        if fixed != key:
            LAYER_PRESETS[fixed] = LAYER_PRESETS.pop(key)
            entry = LAYER_PRESETS[fixed]
            entry["label"] = " ".join(str(entry.get("label", fixed)).split())


_normalise_preset_entries()


@dataclass
class LayerEntry:
    """One row of the active-layer list (T19.1)."""

    key: str
    name: str
    group: str = "weather fields"
    visible: bool = False
    opacity: float = 1.0
    #: Actual time actually drawn: a valid time, an age string, or "".
    time_text: str = ""
    #: Availability/error state: one of ``ok``, ``loading``, ``off``,
    #: ``unavailable``, ``failed``, ``offline``, ``no-data``, ``canceled``,
    #: ``outside-domain``, ``stale``, ``empty``, ``occluded``.
    state: str = "off"
    #: Human detail behind :attr:`state` (the fetch error, the coverage note).
    detail: str = ""
    #: Time-match state for the row (T21.2): ``exact`` / ``nearest ±offset`` /
    #: ``outside coverage`` / ``unavailable``, from the product's own rule.
    match_text: str = ""
    #: What unblocks this layer, when the dependency model names one (T19.3).
    requires: str = ""
    #: Follow/pinned qualifier for the sounding's locator inset (T19.3).
    scope: str = "main map"
    #: Occlusion note from :func:`occlusion_guard`, or "" (T19.4).
    occlusion: str = ""

    def status_text(self) -> str:
        """Return the one-line status the row and the card share.

        Failed rows keep the failure text even when the layer is hidden: the
        failure is what the row exists to report, and hiding must not clear it
        the way it clears a time or an opacity.
        """
        if self.state in ("failed", "unavailable", "offline", "no-data"):
            return self.detail or "Unavailable"
        if self.state == "canceled":
            return self.detail or "Canceled"
        if self.state == "outside-domain":
            return self.detail or "Outside HRRR coverage"
        if self.state == "off":
            return "Off"
        if self.state == "loading":
            return "Loading…"
        if self.state == "stale":
            return self.detail or "Stale"
        if self.state == "empty":
            return self.detail or "Nothing here"
        if self.detail:
            return self.detail
        return "On"


def layer_list_rows(entries: list[LayerEntry]) -> list[str]:
    """Return the active-layer list row texts in paint order (T19.1/T19.4).

    Bottom-to-top: the first row is what the map draws first, per
    :data:`LAYER_PAINT_ORDER` (raster order, then vector order, then
    geography). A ``"— top —"`` marker follows the topmost visible fill so the
    order reads without guessing.
    """
    rows = sorted(list(entries or ()),
                  key=lambda entry: paint_order_index(entry.key))
    lines = []
    top_fill = -1
    for index, entry in enumerate(rows):
        if entry.visible and entry.key in OPAQUE_FILL_KEYS:
            top_fill = index
    for index, entry in enumerate(rows):
        eye = "●" if entry.visible else "○"
        opacity = f"{int(round(entry.opacity * 100))}%" if entry.visible else "—"
        time = entry.time_text or "—"
        # The geography row is always drawn and never carries data of its
        # own: name it without the per-layer value/time/status columns so one
        # housekeeping row cannot set the width of every control rail.
        if entry.key == "geography":
            line = f"{eye} {entry.name}"
        else:
            line = (f"{eye} {entry.name} · {opacity} · {time} · "
                    f"{entry.status_text()}")
        if index == top_fill and sum(1 for item in rows
                                     if item.visible and item.key in OPAQUE_FILL_KEYS) > 1:
            line += " — top"
        if entry.occlusion and entry.visible:
            line += f" — {entry.occlusion}"
        lines.append(line)
    return lines


def occlusion_guard(visible: list[LayerEntry]) -> dict[str, str]:
    """Return ``{key: note}`` for visibly occluded fill layers (T19.4).

    Only layers that are *on*, *covering*, and *not on top* are named: a hidden
    layer is not occluded, a translucent one still reads, and the topmost fill
    is doing the covering. The note names the covering layer and the mitigation
    (lower the top opacity, or switch one of them off).
    """
    fills = [entry for entry in visible
             if entry.key in OPAQUE_FILL_KEYS and entry.visible
             and entry.opacity >= OCCLUSION_OPACITY - 1e-9]
    if len(fills) < 2:
        return {}
    top = fills[-1]
    notes = {}
    for entry in fills[:-1]:
        notes[entry.key] = (
            f"hidden under {top.name} — lower {top.name}'s opacity "
            f"below {int(OCCLUSION_OPACITY * 100)}% or switch one off")
    return notes


def migrate_preset_choice(stored, *, version: int = PRESET_VERSION) -> str:
    """Resolve a stored preset name to a known key (T19.2).

    Unknown, blank, or version-mismatched values degrade to the default preset
    rather than failing the launch; a stored *layer list* is not silently
    promoted to a named preset.
    """
    if version != PRESET_VERSION:
        return DEFAULT_PRESET_KEY
    wanted = " ".join(str(stored or "").split()).casefold()
    for key, entry in LAYER_PRESETS.items():
        if key.casefold() == wanted or str(entry.get("label", "")).casefold() == wanted:
            return key
    return DEFAULT_PRESET_KEY


def preset_layers(key: str) -> tuple[tuple[str, float], ...]:
    """Return the ``(layer_key, opacity)`` pairs for a preset (T19.2)."""
    entry = LAYER_PRESETS.get(migrate_preset_choice(key) if key not in LAYER_PRESETS else key)
    if entry is None:
        entry = LAYER_PRESETS[DEFAULT_PRESET_KEY]
    return tuple((str(layer), float(opacity))
                 for layer, opacity in entry["layers"])


def reports_dependency(outlook_on: bool) -> tuple[bool, str, str]:
    """Return ``(available, reason, action)`` for storm reports (T19.3).

    Reports are read against the outlook that anticipated them, so the switch
    stays disabled until the outlook is showing. The action names the one thing
    that unblocks the layer.
    """
    if outlook_on:
        return True, "", ""
    return (False, "Needs the outlook switched on",
            "Switch on the SPC convective outlook first")


#: Bottom-to-top order for the active-layer list, mirroring the paint path:
#: raster keys in ``RASTER_DRAW_ORDER`` order, then vector keys in
#: ``VECTOR_DRAW_ORDER`` order, then geography (basemap/labels/scale, always
#: drawn), then the sounding-locator rows (never painted on the main map, so
#: they sort last). The list presents this sequence so "what covers what" is a
#: stated order, not insertion luck.
LAYER_PAINT_ORDER: tuple[str, ...] = (
    "goes_context",
    "hrrr_field",
    "radar_mosaic",
    "radar_site",
    "surface_observations",
    "storm_reports",
    "spc_outlook",
    "geography",
)


def paint_order_index(key: str) -> int:
    """Return the list position for an overlay key, tolerating newcomers."""
    try:
        return LAYER_PAINT_ORDER.index(str(key))
    except ValueError:
        if str(key).startswith("locator:"):
            return len(LAYER_PAINT_ORDER)
        return LAYER_PAINT_ORDER.index("storm_reports")


def locator_entry(key: str, label: str, *, detail: str = "") -> LayerEntry:
    """Return the active-layer row for one locator/inset family (T19.3).

    The sounding's locator inset is not the main map, and its families reuse
    the main-map keys -- so the row key is namespaced (``locator:risk``) and
    the scope says where it draws. ``detail`` carries the pinned hazard or the
    follow state.
    """
    return LayerEntry(
        key=f"locator:{key}", name=label, group="sounding locator",
        visible=True, opacity=1.0, time_text="", state="ok",
        detail=detail or "Follows the map", scope="sounding locator inset")


def normalise_opacity(value, *, minimum: float = 0.20,
                      maximum: float = 0.95) -> float:
    """Clamp an opacity to the slider band, tolerating bad input (T19.2)."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return round((minimum + maximum) / 2.0, 2)
    import math

    if not math.isfinite(number):
        return round((minimum + maximum) / 2.0, 2)
    return round(min(maximum, max(minimum, number)), 2)


def opacity_reset_value() -> float:
    """Return the reset opacity for a field/radar slider (T19.2)."""
    return 0.75


def layer_session_state(*, enabled: dict, products: dict, opacities: dict,
                        preset: str | None = None,
                        times: dict | None = None) -> dict:
    """Return the portable layer-choice snapshot (T19.5/session, T21 times).

    Choices only: which layers draw, what each one shows, how strongly,
    which named preset (if any) was last applied, and -- since T21 -- the
    per-tab time choices (run/hour/mode/requested) validated by
    :mod:`sharpmod.maps.map_time`. Fetched frames, ages, and error text are
    deliberately absent -- a restore re-requests, it never replays pixels.
    Versioned so a future vocabulary can migrate.
    """
    from sharpmod.maps.map_time import restore_time_state

    clean_times = {}
    for tab, slot in (times or {}).items():
        if not isinstance(slot, dict):
            continue
        try:
            clean_times[str(tab)] = restore_time_state(slot)
        except (AttributeError, TypeError, ValueError):
            continue
    return {
        "version": 1,
        "enabled": {str(key): bool(value) for key, value in (enabled or {}).items()},
        "products": {str(key): str(value) for key, value in (products or {}).items()},
        "opacities": {str(key): float(value) for key, value in (opacities or {}).items()},
        "preset": migrate_preset_choice(preset) if preset else "",
        "times": clean_times,
    }


def restore_layer_session_state(payload) -> dict:
    """Return a validated layer-choice snapshot, tolerating old payloads."""
    if not isinstance(payload, dict):
        return layer_session_state(enabled={}, products={}, opacities={})
    version = payload.get("version", 1)
    try:
        version = int(version)
    except (TypeError, ValueError, OverflowError):
        version = 1
    if version != 1:
        return layer_session_state(enabled={}, products={}, opacities={})
    enabled = payload.get("enabled") if isinstance(payload.get("enabled"), dict) else {}
    products = payload.get("products") if isinstance(payload.get("products"), dict) else {}
    opacities = payload.get("opacities") if isinstance(payload.get("opacities"), dict) else {}
    preset = payload.get("preset") if isinstance(payload.get("preset"), str) else ""
    times = payload.get("times") if isinstance(payload.get("times"), dict) else {}
    clean_opacities = {}
    for key, value in opacities.items():
        try:
            clean_opacities[str(key)] = max(0.05, min(1.0, float(value)))
        except (TypeError, ValueError, OverflowError):
            continue
    return layer_session_state(enabled=enabled, products=products,
                               opacities=clean_opacities, preset=preset or None,
                               times=times)


__all__ = [
    "LAYER_GROUPS",
    "LAYER_PAINT_ORDER",
    "LAYER_PRESETS",
    "DEFAULT_PRESET_KEY",
    "OPAQUE_FILL_KEYS",
    "OCCLUSION_OPACITY",
    "PRESET_VERSION",
    "LayerEntry",
    "layer_list_rows",
    "locator_entry",
    "migrate_preset_choice",
    "normalise_opacity",
    "occlusion_guard",
    "opacity_reset_value",
    "paint_order_index",
    "preset_layers",
    "reports_dependency",
    "layer_session_state",
    "restore_layer_session_state",
]

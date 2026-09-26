"""Parcel selection for a composed sounding window."""

from __future__ import annotations

from sharpmod.rendering.cli import PARCEL_TYPES, PARCEL_ATTRIBUTES, DEFAULT_RENDER_PARCEL


def _normalise_parcel_type(parcel: str | None) -> str:
    """Return a canonical parcel key for GUI-equivalent CLI rendering."""
    try:
        value = str(parcel or DEFAULT_RENDER_PARCEL).strip().upper()
    except Exception as exc:
        raise ValueError(f"invalid parcel value {parcel!r}") from exc
    if value not in PARCEL_TYPES:
        valid = ", ".join(PARCEL_TYPES)
        raise ValueError(f"unknown parcel {parcel!r}; expected {valid}")
    return value

def _apply_render_parcel(win, parcel: str | None) -> str:
    """Select the requested parcel through SHARPpy's normal update path."""
    parcel_type = _normalise_parcel_type(parcel)
    spc_widget = getattr(win, "spc_widget", None)
    if spc_widget is None or not hasattr(spc_widget, "updateParcel"):
        raise ValueError("render window does not expose parcel selection")

    prof = getattr(spc_widget, "default_prof", None)
    if prof is None:
        raise ValueError("rendered sounding has no active profile")

    selected = None
    get_parcel = getattr(spc_widget, "getParcelObj", None)
    if callable(get_parcel):
        selected = get_parcel(prof, parcel_type)
    if selected is None:
        selected = getattr(prof, PARCEL_ATTRIBUTES[parcel_type], None)
    if selected is None:
        raise ValueError(f"{parcel_type} parcel is unavailable for this sounding")

    spc_widget.updateParcel(selected)
    return parcel_type

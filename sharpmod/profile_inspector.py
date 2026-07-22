"""Sounding source provenance and conservative data-quality diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math

import numpy as np


@dataclass(frozen=True)
class QualityFinding:
    """One non-mutating profile quality observation."""

    severity: str
    code: str
    message: str


_PROVENANCE_KEYS = (
    "model",
    "model_key",
    "loc",
    "run",
    "valid",
    "fxx",
    "member",
    "requested_lat",
    "requested_lon",
    "selected_lat",
    "selected_lon",
    "requested_valid",
    "selected_valid",
    "levels",
    "herbie_model",
    "product",
    "transport",
    "decoder",
    "source_provider",
    "source_provider_name",
    "provider",
    "provider_name",
    "source_grib",
    "source_file",
    "source_url",
    "fields",
    "backend",
    "cache_hit",
    "model_hour_reused",
    "surface_relative_vorticity",
    "surface_vorticity_source",
    "fallback_attempts",
    "fallback_from",
    "npz_path",
    "metadata_sidecar",
)


def _meta(collection, key, default=None):
    try:
        value = collection.getMeta(key)
    except Exception:
        return default
    return default if value is None else value


def provenance(collection) -> dict:
    """Return stable, user-facing source metadata from a profile collection."""
    def present(value):
        return value is not None and value != "" and value != () and value != []

    metadata = {
        key: value
        for key in _PROVENANCE_KEYS
        if present(value := _meta(collection, key))
    }
    try:
        per_hour = collection.getMeta("timeline_provenance")
        index = int(getattr(collection, "_prof_idx", 0))
        focused = per_hour[index]
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        focused = None
    if isinstance(focused, dict):
        for key in _PROVENANCE_KEYS:
            value = focused.get(key)
            if present(value):
                metadata[key] = value
    return metadata


def _current_profile(collection):
    try:
        profiles = collection.getCurrentProfs() or {}
        for profile in profiles.values():
            if profile is not None:
                return profile
    except Exception:
        pass
    for profiles in getattr(collection, "_profs", {}).values():
        for profile in profiles:
            if profile is not None:
                return profile
    return None


def _values(profile, field):
    try:
        values = np.ma.asarray(getattr(profile, field), dtype=float).filled(np.nan)
    except Exception:
        return np.asarray([], dtype=float)
    missing = float(getattr(profile, "missing", -9999.0))
    values = np.asarray(values, dtype=float).reshape(-1)
    values[~np.isfinite(values)] = np.nan
    values[np.isclose(values, missing)] = np.nan
    values[values <= -9000.0] = np.nan
    return values


def inspect_profile(collection) -> list[QualityFinding]:
    """Return conservative warnings without modifying or interpolating data."""
    profile = _current_profile(collection)
    if profile is None:
        return [QualityFinding("error", "no-profile", "No profile is available.")]

    findings = []
    pres = _values(profile, "pres")
    finite_pres = pres[np.isfinite(pres) & (pres > 0)]
    if finite_pres.size < 8:
        findings.append(QualityFinding(
            "error", "too-few-levels",
            f"Only {finite_pres.size} usable pressure levels are present.",
        ))
    elif np.any(np.diff(finite_pres) >= 0):
        findings.append(QualityFinding(
            "warning", "pressure-order",
            "Pressure levels are not strictly descending or contain duplicates.",
        ))
    if finite_pres.size and float(np.nanmin(finite_pres)) > 300.0:
        findings.append(QualityFinding(
            "warning", "shallow-profile",
            f"Profile stops near {float(np.nanmin(finite_pres)):.0f} hPa.",
        ))

    hght = _values(profile, "hght")
    paired = min(pres.size, hght.size)
    height_mask = np.isfinite(pres[:paired]) & np.isfinite(hght[:paired])
    heights = hght[:paired][height_mask]
    if heights.size > 1 and np.any(np.diff(heights) < -10.0):
        findings.append(QualityFinding(
            "warning", "height-order",
            "Height decreases while pressure ascends through the profile.",
        ))

    tmpc = _values(profile, "tmpc")
    dwpc = _values(profile, "dwpc")
    paired = min(tmpc.size, dwpc.size)
    thermo_mask = np.isfinite(tmpc[:paired]) & np.isfinite(dwpc[:paired])
    supersaturated = int(np.sum(
        dwpc[:paired][thermo_mask] > tmpc[:paired][thermo_mask] + 0.5
    ))
    if supersaturated:
        findings.append(QualityFinding(
            "warning", "dewpoint-above-temperature",
            f"Dewpoint exceeds temperature by more than 0.5 C at "
            f"{supersaturated} level(s).",
        ))

    wspd = _values(profile, "wspd")
    negative_wind = int(np.sum(wspd[np.isfinite(wspd)] < 0.0))
    if negative_wind:
        findings.append(QualityFinding(
            "error", "negative-wind-speed",
            f"Negative wind speed occurs at {negative_wind} level(s).",
        ))

    expected = max(1, finite_pres.size)
    for field, label in ((tmpc, "temperature"), (dwpc, "dewpoint"),
                         (wspd, "wind speed")):
        missing = max(0, expected - int(np.sum(np.isfinite(field))))
        if missing / expected > 0.25:
            findings.append(QualityFinding(
                "warning", f"missing-{label.replace(' ', '-')}",
                f"More than 25% of {label} values are missing.",
            ))

    requested = (_meta(collection, "requested_lat"),
                 _meta(collection, "requested_lon"))
    selected = (_meta(collection, "selected_lat"),
                _meta(collection, "selected_lon"))
    try:
        distance = _great_circle_km(*requested, *selected)
    except (TypeError, ValueError, OverflowError):
        distance = None
    if distance is not None and distance > 75.0:
        findings.append(QualityFinding(
            "warning", "distant-grid-point",
            f"Selected source point is {distance:.1f} km from the request.",
        ))
    return findings


def _great_circle_km(lat1, lon1, lat2, lon2):
    values = tuple(float(value) for value in (lat1, lon1, lat2, lon2))
    if not all(math.isfinite(value) for value in values):
        raise ValueError("coordinates must be finite")
    phi1, lam1, phi2, lam2 = map(math.radians, values)
    term = math.sin((phi2 - phi1) / 2) ** 2 + (
        math.cos(phi1) * math.cos(phi2)
        * math.sin((lam2 - lam1) / 2) ** 2
    )
    return 6371.0088 * 2 * math.asin(min(1.0, math.sqrt(term)))


def _display(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)


def profile_summary(collection) -> list[str]:
    """Return explicit level, missing-field, and vorticity summary lines."""
    profile = _current_profile(collection)
    if profile is None:
        return ["Pressure levels: 0", "Missing fields: profile unavailable"]
    pres = _values(profile, "pres")
    total = int(pres.size)
    usable = int(np.sum(np.isfinite(pres) & (pres > 0)))
    lines = [f"Pressure levels: {usable} usable / {total} rows"]
    missing_fields = []
    partial = []
    for name, label in (
        ("hght", "height"), ("tmpc", "temperature"),
        ("dwpc", "dewpoint"), ("wdir", "wind direction"),
        ("wspd", "wind speed"), ("omeg", "omega"),
    ):
        values = _values(profile, name)
        finite = int(np.sum(np.isfinite(values)))
        expected = max(total, int(values.size))
        if finite == 0:
            missing_fields.append(label)
        elif finite < expected:
            partial.append(f"{label} {expected - finite}/{expected}")
    lines.append(
        "Missing fields: " + (", ".join(missing_fields) if missing_fields else "none")
    )
    lines.append(
        "Partial missing values: " + (", ".join(partial) if partial else "none")
    )
    vorticity = _meta(collection, "surface_relative_vorticity")
    source = _meta(collection, "surface_vorticity_source")
    if vorticity is None:
        lines.append(
            "Surface relative vorticity: unavailable"
            + (f" ({source})" if source else "")
        )
    else:
        try:
            value = f"{float(vorticity):.3e} s^-1"
        except (TypeError, ValueError):
            value = str(vorticity)
        lines.append(
            f"Surface relative vorticity: {value}"
            + (f" ({source})" if source else "")
        )
    return lines


def format_report(collection) -> str:
    """Build a copyable plain-text provenance and QC report."""
    lines = ["SOURCE AND PROVENANCE"]
    metadata = provenance(collection)
    if metadata:
        width = max(len(key) for key in metadata)
        lines.extend(
            f"{key.replace('_', ' ').title():<{width + 2}} {_display(value)}"
            for key, value in metadata.items()
        )
    else:
        lines.append("No extractor provenance metadata is available.")
    lines.extend(("", "PROFILE SUMMARY"))
    lines.extend(profile_summary(collection))
    lines.extend(("", "DATA QUALITY"))
    findings = inspect_profile(collection)
    if not findings:
        lines.append("PASS  No basic structural problems detected.")
    else:
        lines.extend(
            f"{item.severity.upper():7} {item.message} [{item.code}]"
            for item in findings
        )
    lines.extend((
        "",
        "These checks flag structural limitations; they do not replace "
        "meteorological quality control.",
    ))
    return "\n".join(lines)


__all__ = [
    "QualityFinding", "format_report", "inspect_profile", "profile_summary",
    "provenance",
]

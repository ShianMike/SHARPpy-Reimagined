"""Forecast-model field plans that avoid equivalent GRIB messages."""

from __future__ import annotations

import re


NOAA_REQUIRED_FIELDS = ("HGT", "TMP", "UGRD", "VGRD")
IFS_REQUIRED_FIELDS = ("gh", "t", "u", "v")


def _available_variables(inventory, *, upper: bool) -> set[str]:
    try:
        values = inventory["variable"]
    except (KeyError, TypeError) as exc:
        raise ValueError("model inventory has no variable column") from exc
    if upper:
        return {str(value).upper() for value in values}
    return {str(value).lower() for value in values}


def choose_noaa_fields(inventory) -> tuple[str, ...]:
    """Return one complete, non-duplicated NOAA sounding field plan."""
    available = _available_variables(inventory, upper=True)
    missing = [name for name in NOAA_REQUIRED_FIELDS if name not in available]
    if missing:
        raise ValueError(
            "missing required pressure fields: %s" % ", ".join(missing)
        )

    fields = list(NOAA_REQUIRED_FIELDS)
    if "RH" in available:
        fields.append("RH")
    elif "SPFH" in available:
        fields.append("SPFH")
    else:
        raise ValueError("missing required pressure fields: RH or SPFH")

    # VVEL is pressure vertical velocity and is preferred over the geometric
    # DZDT field. Retaining one avoids downloading two interchangeable columns
    # for the current point-sounding output.
    if "VVEL" in available:
        fields.append("VVEL")
    elif "DZDT" in available:
        fields.append("DZDT")

    if "ABSV" in available:
        fields.append("ABSV")
    return tuple(fields)


def choose_ifs_fields(inventory) -> tuple[str, ...]:
    """Return one complete, non-duplicated ECMWF pressure-level field plan."""
    available = _available_variables(inventory, upper=False)
    missing = [name for name in IFS_REQUIRED_FIELDS if name not in available]
    if missing:
        raise ValueError(
            "missing required pressure fields: %s" % ", ".join(missing)
        )
    fields = list(IFS_REQUIRED_FIELDS)
    if "r" in available:
        fields.append("r")
    elif "q" in available:
        fields.append("q")
    else:
        raise ValueError("missing required pressure fields: r or q")
    for optional in ("w", "vo"):
        if optional in available:
            fields.append(optional)
    return tuple(fields)


def build_noaa_search(fields) -> str:
    """Build an all-published-pressure-level NOAA inventory regex."""
    names = "|".join(re.escape(str(field).upper()) for field in fields)
    return rf":({names}):\d+(?:\.\d+)? mb:"


def build_ifs_search(fields) -> str:
    """Build an all-published-pressure-level ECMWF inventory regex."""
    names = "|".join(re.escape(str(field).lower()) for field in fields)
    return rf":({names}):\d+:pl:"


def choose_search(config, inventory) -> tuple[str, tuple[str, ...]]:
    """Return ``(search, fields)`` for a configured Herbie model."""
    if str(getattr(config, "herbie_model", "")).lower() in {"ifs", "aifs"}:
        fields = choose_ifs_fields(inventory)
        return build_ifs_search(fields), fields
    fields = choose_noaa_fields(inventory)
    return build_noaa_search(fields), fields

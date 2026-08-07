"""Forecast-model field plans that avoid equivalent GRIB messages."""

from __future__ import annotations

import re


NOAA_REQUIRED_FIELDS = ("HGT", "TMP", "UGRD", "VGRD")
IFS_REQUIRED_FIELDS = ("gh", "t", "u", "v")
NOAA_SURFACE_FIELDS = ("PRES", "DPT", "RH", "SPFH")
NOAA_SURFACE_SEARCH = (
    r":(?:PRES|HGT):surface:"
    r"|:(?:TMP|DPT|RH|SPFH):2 m above ground:"
    r"|:(?:UGRD|VGRD):10 m above ground:"
)
# CFS flux fields are split across regular latitude/longitude and Gaussian
# grids. Specific humidity shares the pressure/terrain/temperature Gaussian
# grid, so deriving dewpoint from it keeps the inserted ground row colocated.
CFS_SURFACE_SEARCH = (
    r":(?:PRES|HGT):surface:"
    r"|:(?:TMP|SPFH):2 m above ground:"
    r"|:(?:UGRD|VGRD):10 m above ground:"
)
IFS_SURFACE_FIELDS = ("z", "sp", "2t", "2d", "10u", "10v")
IFS_SURFACE_SEARCH = r":(?:z|sp|2t|2d|10u|10v):sfc:"
# Terrain height is time-invariant, and several products publish it only in
# the step-0 file of a run. ECMWF open data carries it as surface geopotential
# (``z:sfc``) and GEFS carries it as surface geopotential height
# (``HGT:surface``); both omit it from every forecast hour after F000 while
# still publishing the rest of the ground contract. The verified surface row
# therefore sources height from the same run's analysis file.
IFS_INVARIANT_FIELDS = ("z",)
IFS_INVARIANT_SEARCH = r":z:sfc:"
NOAA_INVARIANT_FIELDS = ("HGT",)
NOAA_INVARIANT_SEARCH = r":HGT:surface:"


# Herbie names the variable column differently per index style: ``variable``
# for the wgrib2 ``.idx`` files NOAA publishes, and ``param`` for the eccodes
# ``.index`` files ECMWF open data publishes. Field planning has to read both.
_VARIABLE_COLUMNS = ("variable", "param")


def variable_column(inventory) -> str | None:
    """Return the column naming each record's variable, or ``None``."""
    for name in _VARIABLE_COLUMNS:
        try:
            if name in inventory:
                return name
        except TypeError:
            return None
    return None


def _available_variables(inventory, *, upper: bool) -> set[str]:
    column = variable_column(inventory)
    if column is None:
        raise ValueError(
            "model inventory has no variable or param column"
        )
    values = inventory[column]
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
    """Build an all-level NOAA search plus the verified surface inputs."""
    names = "|".join(re.escape(str(field).upper()) for field in fields)
    pressure = rf":(?:{names}):\d+(?:\.\d+)? mb:"
    return rf"(?:{pressure})|(?:{NOAA_SURFACE_SEARCH})"


def supports_noaa_surface_merge(fields) -> bool:
    """Return whether provenance proves all ground-level inputs were fetched."""
    available = {str(field).upper() for field in (fields or ())}
    required = set(NOAA_REQUIRED_FIELDS) | {"PRES"}
    moisture = {"DPT", "RH", "SPFH"}
    return required.issubset(available) and not moisture.isdisjoint(available)


def supports_ifs_surface_merge(fields) -> bool:
    """Return whether provenance includes the complete IFS ground contract."""
    available = {str(field).lower() for field in (fields or ())}
    return set(IFS_REQUIRED_FIELDS + IFS_SURFACE_FIELDS).issubset(available)


def build_ifs_search(fields) -> str:
    """Build an all-published-pressure-level ECMWF inventory regex."""
    names = "|".join(re.escape(str(field).lower()) for field in fields)
    pressure = rf":(?:{names}):\d+:pl:"
    return rf"(?:{pressure})|(?:{IFS_SURFACE_SEARCH})"


def choose_search(config, inventory) -> tuple[str, tuple[str, ...]]:
    """Return ``(search, fields)`` for a configured Herbie model."""
    if str(getattr(config, "herbie_model", "")).lower() in {"ifs", "aifs"}:
        fields = choose_ifs_fields(inventory)
        return build_ifs_search(fields), fields
    fields = choose_noaa_fields(inventory)
    return build_noaa_search(fields), fields

"""Forecast-model field plans that avoid equivalent GRIB messages."""

from __future__ import annotations


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


def choose_noaa_fields(inventory) -> tuple[str, ...]:
    """Return one complete, non-duplicated NOAA sounding field plan."""
    import sharpmod_rs

    column = variable_column(inventory)
    if column is None:
        raise ValueError(
            "model inventory has no variable or param column"
        )
    return sharpmod_rs.choose_noaa_fields(inventory[column])


def choose_ifs_fields(inventory) -> tuple[str, ...]:
    """Return one complete, non-duplicated ECMWF pressure-level field plan."""
    import sharpmod_rs

    column = variable_column(inventory)
    if column is None:
        raise ValueError(
            "model inventory has no variable or param column"
        )
    return sharpmod_rs.choose_ifs_fields(inventory[column])


def build_noaa_search(fields) -> str:
    """Build an all-level NOAA search plus the verified surface inputs."""
    import sharpmod_rs

    return sharpmod_rs.build_noaa_search(fields)


def supports_noaa_surface_merge(fields) -> bool:
    """Return whether provenance proves all ground-level inputs were fetched."""
    import sharpmod_rs

    return sharpmod_rs.supports_noaa_surface_merge(fields)


def supports_ifs_surface_merge(fields) -> bool:
    """Return whether provenance includes the complete IFS ground contract."""
    import sharpmod_rs

    return sharpmod_rs.supports_ifs_surface_merge(fields)


def build_ifs_search(fields) -> str:
    """Build an all-published-pressure-level ECMWF inventory regex."""
    import sharpmod_rs

    return sharpmod_rs.build_ifs_search(fields)


def choose_search(config, inventory) -> tuple[str, tuple[str, ...]]:
    """Return ``(search, fields)`` for a configured Herbie model."""
    if str(getattr(config, "herbie_model", "")).lower() in {"ifs", "aifs"}:
        fields = choose_ifs_fields(inventory)
        return build_ifs_search(fields), fields
    fields = choose_noaa_fields(inventory)
    return build_noaa_search(fields), fields

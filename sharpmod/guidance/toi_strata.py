"""Verification strata for TOI calibration: region, season, lead, and HRRR era.

A single pooled skill number hides the failures that matter operationally. A
calibrator can look good overall while being badly miscalibrated in the cool
season, at long lead, in one region, or under the HRRR configuration that
happens to dominate the training years. This module defines the deterministic,
documented partitions used to report those breakdowns, and always reports the
sample size behind each one so a stratum with three events is not mistaken for
evidence.

Every classifier here is a pure function of fields already stored in the
dataset, so strata are reproducible from a saved dataset without re-running
extraction.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

#: Coarse CONUS boxes used only for verification stratification.  The
#: boundaries are explicit and deliberately simple; they are not a
#: meteorological regionalisation and are not tuned to any result.
REGION_BOUNDARIES = (
    "longitude < -104 splits west; -104 to -87 is central; >= -87 is east. "
    "latitude >= 39 splits the northern half of each band."
)

#: Documented NCEP HRRR operational implementation dates.  Statistical
#: relationships between UH/STP-style predictors and observed tornado days are
#: not stationary across these upgrades, so skill is reported per era.
HRRR_ERAS = (
    ("pre-HRRRv1", None, datetime(2014, 9, 30)),
    ("HRRRv1", datetime(2014, 9, 30), datetime(2016, 8, 23)),
    ("HRRRv2", datetime(2016, 8, 23), datetime(2018, 7, 12)),
    ("HRRRv3", datetime(2018, 7, 12), datetime(2020, 12, 2)),
    ("HRRRv4", datetime(2020, 12, 2), None),
)

SEASONS = {
    12: "winter",
    1: "winter",
    2: "winter",
    3: "spring",
    4: "spring",
    5: "spring",
    6: "summer",
    7: "summer",
    8: "summer",
    9: "autumn",
    10: "autumn",
    11: "autumn",
}

#: Forecast-lead bins, in hours, as ``(label, inclusive_upper_bound)``.
LEAD_BINS = (
    ("f00-f06", 6),
    ("f07-f12", 12),
    ("f13-f18", 18),
    ("f19-plus", None),
)

STRATUM_DIMENSIONS = ("region", "season", "forecast_lead", "hrrr_era")


class TOIStratumError(ValueError):
    """Raised when a row cannot be assigned to a documented stratum."""


def conus_region(latitude: float, longitude: float) -> str:
    """Classify a point into one coarse, documented CONUS verification box."""

    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError) as exc:
        raise TOIStratumError("latitude and longitude must be numeric") from exc
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        raise TOIStratumError("latitude/longitude are out of range")
    if not (24.0 <= lat <= 50.0 and -125.0 <= lon <= -66.0):
        return "outside_conus"
    northern = lat >= 39.0
    if lon < -104.0:
        return "northern_rockies_west" if northern else "southwest"
    if lon < -87.0:
        return (
            "northern_plains_midwest" if northern else "southern_plains_lower_ms"
        )
    return "northeast_ohio_valley" if northern else "southeast"


def season_name(month: int) -> str:
    """Return the meteorological season for a month number."""

    try:
        key = int(month)
    except (TypeError, ValueError) as exc:
        raise TOIStratumError("month must be an integer") from exc
    if key not in SEASONS:
        raise TOIStratumError(f"month must be 1-12; got {month!r}")
    return SEASONS[key]


def forecast_lead_bin(forecast_hour: int) -> str:
    """Return the documented forecast-lead bin for one forecast hour."""

    try:
        hour = int(forecast_hour)
    except (TypeError, ValueError) as exc:
        raise TOIStratumError("forecast_hour must be an integer") from exc
    if hour < 0:
        raise TOIStratumError("forecast_hour must be non-negative")
    for label, upper in LEAD_BINS:
        if upper is None or hour <= upper:
            return label
    raise TOIStratumError("forecast lead bins must end with an open bin")


def hrrr_era(issuance_time: str | datetime) -> str:
    """Return the HRRR operational era covering one model cycle."""

    if isinstance(issuance_time, datetime):
        moment = issuance_time
    else:
        text = str(issuance_time).strip().replace("Z", "+00:00")
        try:
            moment = datetime.fromisoformat(text)
        except ValueError as exc:
            raise TOIStratumError(
                f"issuance_time must be an ISO-8601 datetime; got {issuance_time!r}"
            ) from exc
    naive = moment.replace(tzinfo=None)
    for label, start, end in HRRR_ERAS:
        if (start is None or naive >= start) and (end is None or naive < end):
            return label
    raise TOIStratumError(  # pragma: no cover - eras cover the whole timeline
        f"no documented HRRR era covers {moment.isoformat()}"
    )


def row_strata(row: Any) -> dict[str, str]:
    """Return every stratum label for one dataset row."""

    return {
        "region": conus_region(row["latitude"], row["longitude"]),
        "season": season_name(row["month"]),
        "forecast_lead": forecast_lead_bin(row["forecast_hour"]),
        "hrrr_era": hrrr_era(row["issuance_time"]),
    }


def group_rows_by_stratum(
    rows: Sequence[Any],
) -> dict[str, dict[str, tuple[int, ...]]]:
    """Return ``{dimension: {stratum: row indexes}}`` for a row sequence."""

    grouped: dict[str, dict[str, list[int]]] = {
        dimension: {} for dimension in STRATUM_DIMENSIONS
    }
    for index, row in enumerate(rows):
        for dimension, label in row_strata(row).items():
            grouped[dimension].setdefault(label, []).append(index)
    return {
        dimension: {
            label: tuple(indexes)
            for label, indexes in sorted(labels.items())
        }
        for dimension, labels in grouped.items()
    }


__all__ = [
    "HRRR_ERAS",
    "LEAD_BINS",
    "REGION_BOUNDARIES",
    "SEASONS",
    "STRATUM_DIMENSIONS",
    "TOIStratumError",
    "conus_region",
    "forecast_lead_bin",
    "group_rows_by_stratum",
    "hrrr_era",
    "row_strata",
    "season_name",
]

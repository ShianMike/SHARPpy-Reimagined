"""Deterministic synthetic fixtures for the offline TOI calibration pipeline.

These fixtures exercise the *pipeline*: manifest validation, operational feature
reuse, year-blocked splitting, fitting, metrics, and artifact export. They are
explicitly declared ``synthetic-fixture`` so no artifact fitted from them can
ever claim historical validation.

The module is importable by path (``sharpmod.tests._toi_fixtures:synthetic_
fetcher``) so the ``sharpmod-guidance`` CLI can be driven end to end without a
network archive.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from sharpmod.guidance.hrrr import HrrrRegionalFrame

#: ``(event_id, case_class, year, month, day, strength)``.  ``strength`` in
#: ``[0, 1]`` scales both the synthetic jet evolution and the proxy-STP
#: magnitude, so features vary smoothly across cases.
_CASE_SPECS: tuple[tuple[str, str, int, int, int, float], ...] = (
    ("outbreak-a", "outbreak", 2019, 4, 14, 0.95),
    ("outbreak-b", "outbreak", 2019, 5, 20, 0.80),
    ("severe-a", "severe", 2019, 4, 3, 0.50),
    ("severe-b", "severe", 2019, 6, 9, 0.45),
    ("null-a", "null", 2019, 3, 12, 0.10),
    ("null-b", "null", 2019, 10, 21, 0.05),
    ("outbreak-c", "outbreak", 2020, 4, 12, 0.90),
    ("outbreak-d", "outbreak", 2020, 3, 28, 0.85),
    ("severe-c", "severe", 2020, 5, 4, 0.55),
    ("severe-d", "severe", 2020, 6, 22, 0.40),
    ("null-c", "null", 2020, 2, 18, 0.05),
    ("null-d", "null", 2020, 11, 7, 0.15),
    ("outbreak-e", "outbreak", 2021, 12, 10, 0.95),
    ("outbreak-f", "outbreak", 2021, 3, 25, 0.80),
    ("severe-e", "severe", 2021, 4, 27, 0.50),
    ("severe-f", "severe", 2021, 5, 16, 0.45),
    ("null-e", "null", 2021, 1, 30, 0.10),
    ("null-f", "null", 2021, 9, 14, 0.05),
    ("outbreak-g", "outbreak", 2022, 3, 31, 0.90),
    ("outbreak-h", "outbreak", 2022, 4, 5, 0.85),
    ("severe-g", "severe", 2022, 5, 12, 0.55),
    ("severe-h", "severe", 2022, 6, 1, 0.40),
    ("null-g", "null", 2022, 2, 22, 0.05),
    ("null-h", "null", 2022, 12, 15, 0.10),
)

DEFAULT_FORECAST_HOUR = 6
DEFAULT_LATITUDE = 35.0
DEFAULT_LONGITUDE = -97.0

def _run_time(year: int, month: int, day: int) -> datetime:
    # 06Z the day before the event, matching the published TOI window start.
    return datetime(year, month, day, 6, tzinfo=timezone.utc)


#: Run-time keyed strength map, built at import time so a freshly imported
#: module (for example from the CLI's ``--fetcher`` reference) behaves
#: identically to one used directly by a test.
SYNTHETIC_STRENGTH: dict[str, float] = {
    _run_time(year, month, day).isoformat(): strength
    for _event_id, _case_class, year, month, day, strength in _CASE_SPECS
}


def _observed(case_class: str, strength: float) -> dict[str, Any]:
    """Synthetic NCEI-style observed tornado counts for the named proxy."""

    if case_class == "outbreak":
        return {
            "tornado_count": 45,
            "ef2_plus_count": 12,
            "ef3_plus_count": 5,
            "ef4_plus_count": 2,
            "longest_ef2_plus_path_miles": 62.0,
        }
    if case_class == "severe":
        return {
            "tornado_count": 8,
            "ef2_plus_count": 1,
            "ef3_plus_count": 0,
            "ef4_plus_count": 0,
            "longest_ef2_plus_path_miles": 9.0,
        }
    return {
        "tornado_count": 0,
        "ef2_plus_count": 0,
        "ef3_plus_count": 0,
        "ef4_plus_count": 0,
        "longest_ef2_plus_path_miles": 0.0,
    }


def synthetic_manifest_payload(
    *,
    target_definition: str = "high_risk_worthy_proxy_v1",
    population_base_rate: float | None = 0.02,
    years: tuple[int, ...] | None = None,
) -> dict[str, Any]:
    """Return a documented synthetic label manifest covering four years."""

    cases: list[dict[str, Any]] = []
    for event_id, case_class, year, month, day, strength in _CASE_SPECS:
        if years is not None and year not in years:
            continue
        run_time = _run_time(year, month, day)
        observed = _observed(case_class, strength)
        case: dict[str, Any] = {
            "event_id": event_id,
            "case_class": case_class,
            "run_time": run_time.isoformat(),
            "forecast_hour": DEFAULT_FORECAST_HOUR,
            "latitude": DEFAULT_LATITUDE,
            "longitude": DEFAULT_LONGITUDE,
            "anchor_source": "fixed_domain_grid",
            "observed": observed,
        }
        if target_definition == "manifest_label_v1":
            case["label"] = 1 if case_class == "outbreak" else 0
        cases.append(case)
    payload: dict[str, Any] = {
        "target_definition": target_definition,
        "label_source": (
            "synthetic fixture; stands in for an NCEI Storm Events export"
        ),
        "dataset_kind": "synthetic-fixture",
        "notes": "Pipeline fixture only. Not a historical archive.",
        "cases": cases,
    }
    if population_base_rate is not None:
        payload["population_base_rate"] = population_base_rate
    return payload


#: ``(event_id, case_class, year, month, day, strength)`` for a manifest whose
#: outbreak event straddles a New Year boundary.  Both of its cycles must land
#: in one blocking year, or a single event would cross a train/test split.
_YEAR_SPANNING_SPECS: tuple[tuple[str, str, int, int, int, float], ...] = (
    ("newyear-outbreak", "outbreak", 2020, 12, 31, 0.90),
    ("newyear-outbreak", "outbreak", 2021, 1, 1, 0.85),
    ("severe-span", "severe", 2021, 5, 4, 0.50),
    ("null-span", "null", 2021, 9, 9, 0.05),
    ("outbreak-span-2022", "outbreak", 2022, 4, 5, 0.90),
    ("severe-span-2022", "severe", 2022, 5, 12, 0.55),
    ("null-span-2022", "null", 2022, 2, 22, 0.05),
)

SYNTHETIC_STRENGTH.update(
    {
        _run_time(year, month, day).isoformat(): strength
        for _event_id, _case_class, year, month, day, strength
        in _YEAR_SPANNING_SPECS
    }
)


def year_spanning_manifest_payload() -> dict[str, Any]:
    """Return a manifest whose outbreak event spans 31 December to 1 January."""

    cases = [
        {
            "event_id": event_id,
            "case_class": case_class,
            "run_time": _run_time(year, month, day).isoformat(),
            "forecast_hour": DEFAULT_FORECAST_HOUR,
            "latitude": DEFAULT_LATITUDE,
            "longitude": DEFAULT_LONGITUDE,
            "anchor_source": "fixed_domain_grid",
            "observed": _observed(case_class, strength),
        }
        for event_id, case_class, year, month, day, strength in _YEAR_SPANNING_SPECS
    ]
    return {
        "target_definition": "high_risk_worthy_proxy_v1",
        "label_source": "synthetic year-spanning fixture",
        "dataset_kind": "synthetic-fixture",
        "notes": "Pipeline fixture only. Not a historical archive.",
        "cases": cases,
    }


def synthetic_frame(
    run_time: datetime,
    forecast_hour: int,
    point_latitude: float = DEFAULT_LATITUDE,
    point_longitude: float = DEFAULT_LONGITUDE,
    pressure_level_hpa: int = 500,
    *,
    strength: float | None = None,
) -> HrrrRegionalFrame:
    """Build one deterministic regional frame around the requested point."""

    if strength is None:
        strength = SYNTHETIC_STRENGTH.get(
            run_time.astimezone(timezone.utc).isoformat(), 0.5
        )
    strength = float(min(1.0, max(0.0, strength)))
    rows, cols = 7, 9
    latitude = np.repeat(
        np.linspace(point_latitude - 3.0, point_latitude + 3.0, rows)[:, None],
        cols,
        axis=1,
    )
    longitude = np.repeat(
        np.linspace(point_longitude - 8.0, point_longitude + 8.0, cols)[None, :],
        rows,
        axis=0,
    )

    # A westerly jet streak translating east across the 18-hour window; faster
    # and stronger for higher-strength cases.
    total_steps = 1 + int(round(strength * 5))
    jet_left = 1 + int(round(total_steps * min(18, max(0, int(forecast_hour))) / 18))
    jet_left = min(jet_left, cols - 2)
    u_wind = np.zeros((rows, cols), dtype=float)
    v_wind = np.zeros_like(u_wind)
    u_wind[1:3, jet_left : jet_left + 2] = 30.0 + 25.0 * strength

    # Fixed-layer STP proxy terms: (CAPE/1500) * 1 * (SRH/150) * (shear/20).
    scale = 1.0 + 2.0 * strength
    cape = np.zeros_like(u_wind)
    srh = np.zeros_like(u_wind)
    cape[3:5, 4:6] = 1500.0 * scale
    srh[3:5, 4:6] = 150.0 * scale
    return HrrrRegionalFrame(
        run_time=run_time,
        valid_time=run_time + timedelta(hours=int(forecast_hour)),
        forecast_hour=int(forecast_hour),
        pressure_level_hpa=int(pressure_level_hpa),
        latitude=latitude,
        longitude=longitude,
        u_wind_mps=u_wind,
        v_wind_mps=v_wind,
        surface_cape_jkg=cape,
        temperature_2m_k=np.full_like(u_wind, 300.0),
        dewpoint_2m_k=np.full_like(u_wind, 300.0),
        srh_1km_m2s2=srh,
        shear_u_6km_mps=np.full_like(u_wind, 20.0),
        shear_v_6km_mps=np.zeros_like(u_wind),
        source_url=f"memory://synthetic/{run_time:%Y%m%d%H}/f{int(forecast_hour):03d}",
        shear_interpretation="synthetic m/s component delta",
    )


def synthetic_fetcher(
    run_time: datetime,
    forecast_hour: int,
    point_latitude: float,
    point_longitude: float,
    pressure_level_hpa: int,
    **_kwargs: Any,
) -> HrrrRegionalFrame:
    """Drop-in replacement for ``fetch_hrrr_regional_frame`` in tests/CLI runs."""

    return synthetic_frame(
        run_time,
        forecast_hour,
        point_latitude,
        point_longitude,
        pressure_level_hpa,
    )


__all__ = [
    "DEFAULT_FORECAST_HOUR",
    "DEFAULT_LATITUDE",
    "DEFAULT_LONGITUDE",
    "SYNTHETIC_STRENGTH",
    "synthetic_fetcher",
    "synthetic_frame",
    "synthetic_manifest_payload",
    "year_spanning_manifest_payload",
]

"""The parameters of an open sounding, computed by sharpmod.

Values come from ``analysis.profile_metrics`` (the box-analysis registry, the
same keys, labels, units, and decimals the legacy tables use), the profile's
own parcels and Bunkers vectors, ``ConvectiveProfile.get_watch`` (the SPC
possible hazard type the image shows), and ``sharptab.hazard.classify``. The
results are labelled tables, so the frontend renders them without naming any
quantity itself.
"""

from __future__ import annotations

import logging
from typing import Any

from sharpmod.bridge import params as p
from sharpmod.bridge.errors import BridgeError
from sharpmod.bridge.sounding_summary import entry_collection

log = logging.getLogger("sharpmod.bridge")

#: ``(key, label, short label, profile attribute)`` of each parcel row.
PARCELS = (
    ("sb", "Surface-based", "SFC", "sfcpcl"),
    ("ml", "Mixed-layer", "ML", "mlpcl"),
    ("mu", "Most-unstable", "MU", "mupcl"),
    ("fcst", "Forecast surface", "FCST", "fcstpcl"),
    ("eff", "Effective inflow", "EFF", "effpcl"),
)
#: ``(key, label, units, decimals, Parcel attribute)`` of each parcel column.
PARCEL_COLUMNS = (
    ("cape", "CAPE", "J/kg", 0, "bplus"),
    ("cin", "CIN", "J/kg", 0, "bminus"),
    ("lcl", "LCL", "m AGL", 0, "lclhght"),
    ("lfc", "LFC", "m AGL", 0, "lfchght"),
    ("el", "EL", "m AGL", 0, "elhght"),
    ("li", "LI", "\u00b0C", 0, "li5"),
)


#: ``(key, label, units, decimals)`` of the Bunkers table's columns.
MOTION_COLUMNS = (
    ("direction", "Direction", "\u00b0", 0),
    ("speed", "Speed", "kt", 0),
)


def _columns(specs: tuple[tuple[Any, ...], ...]) -> list[dict[str, Any]]:
    return [
        {"key": key, "label": label, "units": units, "decimals": decimals}
        for key, label, units, decimals, *_ in specs
    ]


def _parcel_table(prof: Any) -> dict[str, Any]:
    rows = []
    for key, label, short, attribute in PARCELS:
        pcl = getattr(prof, attribute, None)
        rows.append(
            {
                "key": key,
                "label": label,
                "short": short,
                "values": [
                    p.num(getattr(pcl, name, None)) if pcl is not None else None
                    for *_, name in PARCEL_COLUMNS
                ],
            }
        )
    return {"title": "Parcels", "columns": _columns(PARCEL_COLUMNS), "rows": rows}


def _vector(u: Any, v: Any) -> list[float | None]:
    """``[direction, speed]`` of a ``(u, v)`` wind, or two ``None`` when missing."""
    from sharppy.sharptab import utils

    if p.num(u) is None or p.num(v) is None:
        return [None, None]
    direction, speed = utils.comp2vec(float(u), float(v))
    return [p.num(direction), p.num(speed)]


def _motion_table(prof: Any) -> dict[str, Any]:
    """The profile's Bunkers right and left movers as direction and speed."""
    bunkers = getattr(prof, "bunkers", None)
    try:
        rm_u, rm_v, lm_u, lm_v = (bunkers[i] for i in range(4))
    except (TypeError, IndexError):
        rm_u = rm_v = lm_u = lm_v = None
    rows = [
        {
            "key": "bunkers_right",
            "label": "Bunkers right mover",
            "short": "RM",
            "values": _vector(rm_u, rm_v),
        },
        {
            "key": "bunkers_left",
            "label": "Bunkers left mover",
            "short": "LM",
            "values": _vector(lm_u, lm_v),
        },
    ]
    return {"title": "Storm motion", "columns": _columns(MOTION_COLUMNS), "rows": rows}


def _groups(values: Any) -> list[dict[str, Any]]:
    from sharpmod.analysis import box_analysis

    groups: list[dict[str, Any]] = []
    for parameter in box_analysis.PARAMETERS:
        if not groups or groups[-1]["name"] != parameter.group:
            groups.append({"name": parameter.group, "parameters": []})
        groups[-1]["parameters"].append(
            {
                "key": parameter.key,
                "label": parameter.label,
                "units": parameter.display_units,
                "decimals": int(parameter.decimals),
                "notable": parameter.notable,
                "value": p.num(values.get(parameter.key)),
            }
        )
    return groups


def analysis(params: dict[str, Any]) -> dict[str, Any]:
    """Parcel and Bunkers tables, hazard types, and every registry parameter."""
    p.keys(params, ("sounding_id",))
    entry, collection = entry_collection(params["sounding_id"])
    with entry.lock:
        if entry.analysis is not None:
            return entry.analysis
        from sharpmod.analysis import box_analysis, profile_metrics
        from sharpmod.sharptab import hazard

        prof = collection.getHighlightedProf()
        keys = [parameter.key for parameter in box_analysis.PARAMETERS]
        try:
            values = profile_metrics.ProfileMetricsEngine(max_entries=4).values(
                prof, keys
            )
        except profile_metrics.ProfileMetricsError as exc:
            raise BridgeError(
                "SOUNDING_INVALID", f"The sounding could not be analysed: {exc}", False
            ) from None
        try:
            label = str(hazard.classify(prof))
        except Exception:  # noqa: BLE001 - one missing input never loses the tables
            log.exception("hazard classification failed")
            label = "insufficient data"
        watch = str(getattr(prof, "right_watch_type", "") or "").strip() or None
        entry.analysis = {
            "sounding_id": entry.sounding_id,
            "valid_time": entry.summary.get("valid_time"),
            "watch_type": watch,
            "hazard": label,
            "parcel_table": _parcel_table(prof),
            "motion_table": _motion_table(prof),
            "groups": _groups(values),
        }
        return entry.analysis


#: A forecast file can hold hundreds of times; the trend stops here.
MAX_TREND_POINTS = 400


def trends(params: dict[str, Any]) -> dict[str, Any]:
    """The legacy Trends metrics at every valid time of the sounding's collection.

    ``ProfileMetricsEngine.timeline`` with ``DEFAULT_METRIC_KEYS``, exactly as
    the legacy Analysis workspace's Trends tab computes them.
    """
    p.keys(params, ("sounding_id",))
    entry, collection = entry_collection(params["sounding_id"])
    with entry.lock:
        if entry.trends is not None:
            return entry.trends
        from sharpmod.analysis import box_analysis, profile_metrics

        keys = tuple(profile_metrics.DEFAULT_METRIC_KEYS)
        samples = profile_metrics.ProfileMetricsEngine(max_entries=8).timeline(
            collection, keys
        )
        columns = []
        for key in keys:
            parameter = box_analysis.parameter(key)
            columns.append(
                {
                    "key": key,
                    "label": parameter.label,
                    "units": parameter.display_units,
                    "decimals": int(parameter.decimals),
                }
            )
        points = [
            {
                "time": p.iso(sample.valid_time),
                "values": [p.num(sample.values.get(key)) for key in keys],
                "note": None
                if sample.available
                else (str(sample.reason or "unavailable")[:200]),
            }
            for sample in samples[:MAX_TREND_POINTS]
        ]
        entry.trends = {
            "sounding_id": entry.sounding_id,
            "title": "Trends",
            "columns": columns,
            "points": points,
            "truncated": len(samples) > MAX_TREND_POINTS,
        }
        return entry.trends

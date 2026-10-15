"""Ensemble acquisition and summary: one forecast-model run's members at a point.

Every value comes from sharpmod: members are fetched by
``tools.model_extract.extract`` (one portable ``.npz`` per member), combined by
``analysis.profile_timeline.combine_ensemble_collections``, accounted by
``analysis.ensemble_members.EnsembleAcquisition``, and summarized by
``analysis.profile_metrics.ProfileMetricsEngine.ensemble`` with the same keys,
labels, units, and decimals the legacy tables use. The results are labelled
rows, so the frontend renders them without naming any quantity. These modules
only validate requests, move data between those functions, and shape the
results. They import sharpmod inside each handler, so importing them stays
cheap and never loads Qt.

``acquire`` is the shared fetch-and-merge seam: ``bridge.thresholds`` evaluates
rules against the same merged collection this module summarizes.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from sharpmod.bridge import params as p
from sharpmod.bridge.errors import BridgeError

MAX_MEMBERS = 32
MIN_MEMBERS = 2
MAX_MEMBER_LABEL = 16
MAX_REASON = 300


def _short(exc: Exception) -> str:
    text = str(exc).strip().splitlines()
    return (text[0] if text else type(exc).__name__)[:MAX_REASON]


def _native_members(model_key: str) -> dict[str, object]:
    """The member arguments ``extract`` takes, keyed by their wire labels."""
    from sharpmod.analysis.ensemble_members import ENSEMBLE_MEMBERS

    return {str(member): member for member in ENSEMBLE_MEMBERS.get(model_key, ())}


def _requested_members(value: Any, allowed: dict[str, object]) -> tuple[str, ...]:
    if not isinstance(value, list) or not MIN_MEMBERS <= len(value) <= MAX_MEMBERS:
        raise p.invalid(
            f"members must be a list of {MIN_MEMBERS} to {MAX_MEMBERS} member labels"
        )
    labels: list[str] = []
    for item in value:
        if not isinstance(item, str) or not 1 <= len(item.strip()) <= MAX_MEMBER_LABEL:
            raise p.invalid(
                f"each member must be a label of 1 to {MAX_MEMBER_LABEL} characters"
            )
        label = item.strip()
        if label not in allowed:
            raise p.invalid(f"member {label!r} is not a member of this model")
        if label in labels:
            raise p.invalid(f"member {label!r} was requested twice")
        labels.append(label)
    return tuple(labels)


def acquire(params: dict[str, Any]):
    """Fetch the requested members and merge them into one collection.

    Returns ``(collection, ledger)`` with the acquisition ledger attached to the
    merged profiles. One member's failed download is that member's ledger row,
    never a failed request.
    """
    key = p.text(params, "model", 1, 40)
    lat = p.number(params, "latitude", -90.0, 90.0)
    lon = p.number(params, "longitude", -180.0, 180.0)
    fxx = p.integer(params, "forecast_hour", 0, 1000)
    run = p.time(params, "run_time") if params.get("run_time") is not None else None
    location = p.optional_text(params, "location", 64)

    from sharpmod.analysis.ensemble_members import EnsembleAcquisition, MemberFailure
    from sharpmod.analysis.profile_timeline import combine_ensemble_collections
    from sharpmod.bridge.sounding_cache import SoundingDecodeError, decode_collection
    from sharpmod.tools import era5_extract
    from sharpmod.tools import model_extract as mx

    try:
        config = mx.get_config(key)
    except (KeyError, era5_extract.ERA5ExtractionError):
        raise BridgeError(
            "INVALID_ARGUMENT",
            f"The forecast model {key!r} is not available",
            False,
        ) from None
    allowed = _native_members(config.key)
    if not allowed:
        raise BridgeError(
            "INVALID_ARGUMENT",
            f"{config.label} is deterministic; choose GEFS or CFS for an ensemble",
            False,
        ) from None
    members = _requested_members(params.get("members"), allowed)

    collections = []
    labels: list[str] = []
    failures: list[MemberFailure] = []
    with tempfile.TemporaryDirectory(prefix="sharpmod-ensemble") as tmp:
        for member in members:
            path = Path(tmp) / f"{member}.npz"
            try:
                written = mx.extract(
                    config.key,
                    lat,
                    lon,
                    run_time=run,
                    fxx=fxx,
                    out_path=str(path),
                    loc=location,
                    member=allowed[member],
                )
                collection, _ = decode_collection(Path(written) if written else path)
            except era5_extract.ParameterRangeError as exc:
                raise BridgeError("INVALID_ARGUMENT", str(exc), False) from None
            except (
                era5_extract.ERA5ExtractionError,
                SoundingDecodeError,
                OSError,
                ValueError,
            ) as exc:
                # Any reason one member did not load is that member's ledger
                # row; the other members still evaluate (failure isolation).
                failures.append(MemberFailure(member, "failed", _short(exc)))
                continue
            collections.append(collection)
            labels.append(member)

    if not collections:
        reason = failures[0].reason if failures else "no member was requested"
        raise BridgeError(
            "SOUNDING_FETCH_FAILED",
            f"The {config.label} ensemble could not be retrieved: {reason}",
            True,
        ) from None

    ledger = EnsembleAcquisition(members, tuple(labels), tuple(failures))
    # The combiner attaches the ledger it is given and refuses a mismatch
    # between it and the profiles it merged.
    merged = combine_ensemble_collections(collections, tuple(labels), acquisition=ledger)
    return merged, ledger


def summary(params: dict[str, Any]) -> dict[str, Any]:
    """The metric spreads of one ensemble run at one valid time."""
    p.keys(
        params,
        ("model", "latitude", "longitude", "forecast_hour", "members"),
        ("run_time", "location"),
    )
    from sharpmod.analysis import box_analysis
    from sharpmod.analysis.profile_metrics import DEFAULT_METRIC_KEYS
    from sharpmod.analysis.profile_metrics_engine import ProfileMetricsEngine

    merged, ledger = acquire(params)
    result = ProfileMetricsEngine().ensemble(merged, DEFAULT_METRIC_KEYS)

    metrics = []
    for wanted in DEFAULT_METRIC_KEYS:
        spread = result.scalar[wanted]
        item = box_analysis.parameter(wanted)
        metrics.append(
            {
                "key": wanted,
                "label": item.label,
                "units": item.display_units,
                "decimals": int(item.decimals),
                "count": int(spread.count),
                "p10": p.num(spread.p10),
                "median": p.num(spread.median),
                "p90": p.num(spread.p90),
                "minimum": p.num(spread.minimum),
                "maximum": p.num(spread.maximum),
            }
        )
    samples = [
        {
            "member": row.member,
            "outcome": row.outcome,
            "reason": row.reason[:MAX_REASON] if row.reason else None,
            "values": [p.num(row.values.get(wanted)) for wanted in DEFAULT_METRIC_KEYS],
        }
        for row in result.member_samples
    ]
    return {
        "valid_time": p.iso(result.valid_time),
        "reason": result.reason[:MAX_REASON] if result.reason else None,
        "requested_members": list(ledger.requested_members),
        "loaded_members": list(result.loaded_members),
        "failures": [
            {
                "member": item.member,
                "status": item.status,
                "reason": item.reason[:MAX_REASON] if item.reason else None,
            }
            for item in ledger.failures
        ],
        "metrics": metrics,
        "samples": samples,
    }


HANDLERS = {"ensemble.summary": summary}

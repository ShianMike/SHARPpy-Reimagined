"""Ensemble threshold evaluation: one user-defined rule and its member verdicts.

Every value comes from sharpmod: the rule is built and validated by
``analysis.ensemble_thresholds`` (``ThresholdDefinition``), the verdicts come
from its ``evaluate_threshold`` over the same merged members
``bridge.ensemble.acquire`` builds, and the CSV is its ``export_threshold_csv``
rows unchanged. The form's diagnostic catalog is
``analysis.box_analysis.PARAMETERS`` with the numeric-control bounds from
``analysis.metric_controls.control_for``, so the frontend's controls carry the
registry's own labels, units, and precision. These modules only validate
requests, move data between those functions, and shape the results. They import
sharpmod inside each handler, so importing them stays cheap and never loads Qt.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from sharpmod.bridge import params as p
from sharpmod.bridge.ensemble import MAX_REASON
from sharpmod.bridge.errors import BridgeError

MAX_CONDITIONS = 3
MAX_NAME = 120
MAX_METRIC = 32
#: Generously beyond every control bound in ``metric_controls``; the engine
#: itself only requires a finite value.
VALUE_BOUND = 1e9
CSV_NAME = "ensemble-threshold-results.csv"
MAX_CSV_CHARS = 300_000


def _definition(payload: Any):
    """Build the engine's own ``ThresholdDefinition`` from the wire shape.

    The condition's units are filled from the registry here, exactly as the
    desktop form does, so the frontend never carries a unit string of its own.
    """
    from sharpmod.analysis import box_analysis
    from sharpmod.analysis.ensemble_thresholds import (
        ThresholdCondition,
        ThresholdDefinition,
        ThresholdError,
    )

    if not isinstance(payload, dict):
        raise p.invalid("definition must be an object")
    p.keys(payload, ("name", "conditions"))
    name = p.text(payload, "name", 1, MAX_NAME)
    rows = payload.get("conditions")
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_CONDITIONS:
        raise p.invalid(
            f"conditions must be a list of 1 to {MAX_CONDITIONS} conditions"
        )
    conditions = []
    for row in rows:
        if not isinstance(row, dict):
            raise p.invalid("each condition must be an object")
        p.keys(row, ("metric", "operator", "value"), ("upper",))
        metric = p.text(row, "metric", 1, MAX_METRIC)
        try:
            units = box_analysis.parameter(metric).units
        except Exception:  # noqa: BLE001 - one sentence naming the field
            raise p.invalid(
                f"metric {metric!r} is not one of the registry diagnostics"
            ) from None
        try:
            conditions.append(
                ThresholdCondition(
                    metric,
                    p.text(row, "operator", 1, 10),
                    p.number(row, "value", -VALUE_BOUND, VALUE_BOUND),
                    (
                        None
                        if row.get("upper") is None
                        else p.number(row, "upper", -VALUE_BOUND, VALUE_BOUND)
                    ),
                    units,
                )
            )
        except ThresholdError as exc:
            raise p.invalid(str(exc)) from None
    try:
        return ThresholdDefinition(name, tuple(conditions))
    except ThresholdError as exc:
        raise p.invalid(str(exc)) from None


def _result(result) -> dict[str, Any]:
    """``ThresholdResult.as_dict`` with every number normalized for the wire."""
    payload = result.as_dict()
    payload["agreement_fraction"] = p.num(payload["agreement_fraction"])
    payload["requested_coverage"] = p.num(payload["requested_coverage"])
    for row in payload["members"]:
        row["values"] = {key: p.num(value) for key, value in row["values"].items()}
        if row["reason"]:
            row["reason"] = row["reason"][:MAX_REASON]
    return payload


def metrics(params: dict[str, Any]) -> dict[str, Any]:
    """The registry's diagnostics with their control bounds, for the form."""
    p.keys(params, ())
    from sharpmod.analysis import box_analysis
    from sharpmod.analysis.ensemble_thresholds import OPERATOR_ORDER
    from sharpmod.analysis.metric_controls import control_for

    rows = []
    for item in box_analysis.PARAMETERS:
        control = control_for(item.key)
        rows.append(
            {
                "key": control.key,
                "label": control.label,
                "units": control.units,
                "display_label": item.display_label,
                "decimals": int(control.decimals),
                "minimum": float(control.minimum),
                "maximum": float(control.maximum),
                "step": float(control.step),
                "suggested": float(control.suggested),
                "guidance": control.describe(),
            }
        )
    from sharpmod.analysis.metric_controls import SHIPPED_SUGGESTIONS

    return {
        "metrics": rows,
        "operators": list(OPERATOR_ORDER),
        # The diagnostics the form starts with: the three this application has
        # always shipped starting values for.
        "default_metrics": list(SHIPPED_SUGGESTIONS),
    }


def _evaluate(params: dict[str, Any]):
    p.keys(
        params,
        ("model", "latitude", "longitude", "forecast_hour", "members", "definition"),
        ("run_time", "location"),
    )
    definition = _definition(params.get("definition"))
    from sharpmod.bridge.ensemble import acquire

    collection, _ledger = acquire(params)
    from sharpmod.analysis.ensemble_thresholds import evaluate_threshold
    from sharpmod.analysis.profile_metrics import ProfileMetricsEngine

    return evaluate_threshold(collection, definition, ProfileMetricsEngine())


def evaluate(params: dict[str, Any]) -> dict[str, Any]:
    """Where each requested member stands on one rule, with its denominators."""
    return _result(_evaluate(params))


def export_csv(params: dict[str, Any]) -> dict[str, Any]:
    """The same verdicts as ``export_threshold_csv`` writes, as download text."""
    from sharpmod.analysis.ensemble_thresholds import export_threshold_csv

    result = _evaluate(params)
    with tempfile.TemporaryDirectory(prefix="sharpmod-threshold") as tmp:
        written = export_threshold_csv(Path(tmp) / CSV_NAME, (result,))
        text = written.read_text(encoding="utf-8")
    if len(text) > MAX_CSV_CHARS:
        raise BridgeError(
            "INTERNAL", "The threshold export was too large to return.", False
        ) from None
    return {"file_name": CSV_NAME, "csv": text}


HANDLERS = {
    "thresholds.metrics": metrics,
    "thresholds.evaluate": evaluate,
    "thresholds.export_csv": export_csv,
}

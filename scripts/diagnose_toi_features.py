#!/usr/bin/env python
"""Compare candidate TOI feature sets on DEVELOPMENT YEARS ONLY.

Why development years only
--------------------------
The frozen validation plan fixed the feature schema at
``(toi_score, peak_stp_bin)``.  Fitting a *different* feature set and scoring it
on the reserved 2023-2025 test period would mean the test set had been used to
choose a model, which is precisely the researcher-degrees-of-freedom
contamination the pre-registration exists to prevent.  Once a test period has
been used for selection, its numbers stop being an honest estimate of future
skill and no amount of later care restores them.

So this script is a *diagnostic*, not a validation.  It uses leave-one-year-out
cross-validation inside the development years and never reads 2023-2025.  Its
output cannot promote anything; its purpose is to say which direction is worth a
new pre-registered attempt.

It reuses the production estimator (``fit_logistic_calibrator``) so a difference
between feature sets cannot be an artefact of a different fitting routine.

Usage::

    python scripts/diagnose_toi_features.py --dataset data/toi_dataset.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sharpmod.guidance.toi_calibration import (  # noqa: E402
    TOICalibrationError,
    fit_logistic_calibrator,
)

#: Candidate feature sets. The first is the frozen plan's schema, so every other
#: row is read as a difference from what is currently shipped.
CANDIDATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("frozen plan: score + STP bin", ("experimental_score", "peak_stp_bin")),
    ("STP bin only", ("peak_stp_bin",)),
    ("raw peak STP only", ("maximum_stp",)),
    ("experimental score only", ("experimental_score",)),
    ("STP bin + raw STP", ("peak_stp_bin", "maximum_stp")),
    (
        "STP bin + jet distance",
        ("peak_stp_bin", "jet_to_risk_distance_km"),
    ),
    (
        "all features",
        (
            "experimental_score",
            "peak_stp_bin",
            "maximum_stp",
            "maximum_jet_speed_kt",
            "jet_to_risk_distance_km",
            "translation_speed_kt",
        ),
    ),
)


def auc(scores: list[float], labels: list[int]) -> float | None:
    """Mann-Whitney U with explicit half credit for ties."""
    pos = [s for s, y in zip(scores, labels, strict=True) if y == 1]
    neg = [s for s, y in zip(scores, labels, strict=True) if y == 0]
    if not pos or not neg:
        return None
    wins = ties = 0
    for p in pos:
        for n in neg:
            if p > n:
                wins += 1
            elif p == n:
                ties += 1
    return (wins + 0.5 * ties) / (len(pos) * len(neg))


def brier(pred: list[float], labels: list[int], weights: list[float]) -> float:
    total = sum(weights)
    return sum(
        w * (p - y) ** 2 for p, y, w in zip(pred, labels, weights, strict=True)
    ) / total


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="data/toi_dataset.json")
    parser.add_argument("--last-development-year", type=int, default=2022)
    parser.add_argument("--l2", type=float, default=1.0)
    parser.add_argument("--report", default=None)
    args = parser.parse_args(argv)

    payload = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    rows = payload["rows"] if isinstance(payload, dict) else payload

    dev = [r for r in rows if int(r["event_year"]) <= args.last_development_year]
    held_back = len(rows) - len(dev)
    years = sorted({int(r["event_year"]) for r in dev})
    positives = sum(int(r["label"]) for r in dev)
    print(
        f"development rows {len(dev)} across {len(years)} year(s) "
        f"{years[0]}-{years[-1]}, positives {positives}"
    )
    print(f"{held_back} test-period row(s) deliberately NOT read\n")

    if positives < 2:
        print("too few positives to cross-validate")
        return 1

    results = []
    for label, feature_names in CANDIDATES:
        # Out-of-fold predictions, pooled across leave-one-year-out folds.
        pooled_pred: list[float] = []
        pooled_label: list[int] = []
        pooled_weight: list[float] = []
        unevaluated: list[int] = []
        for year in years:
            train = [r for r in dev if int(r["event_year"]) != year]
            test = [r for r in dev if int(r["event_year"]) == year]
            y_train = [int(r["label"]) for r in train]
            if not test or min(y_train) == max(y_train):
                unevaluated.append(year)
                continue
            x_train = np.asarray(
                [[float(r[n]) for n in feature_names] for r in train], dtype=float
            )
            w_train = np.asarray(
                [float(r["sample_weight"]) for r in train], dtype=float
            )
            try:
                intercept, coefs, means, scales = fit_logistic_calibrator(
                    x_train, y_train, sample_weights=w_train, l2_penalty=args.l2
                )
            except TOICalibrationError:
                unevaluated.append(year)
                continue
            beta = np.asarray(coefs, dtype=float)
            mu = np.asarray(means, dtype=float)
            sd = np.asarray(scales, dtype=float)
            for r in test:
                x = np.asarray([float(r[n]) for n in feature_names], dtype=float)
                logit = intercept + float(beta @ ((x - mu) / sd))
                clipped = max(-35.0, min(35.0, logit))
                pooled_pred.append(1.0 / (1.0 + math.exp(-clipped)))
                pooled_label.append(int(r["label"]))
                pooled_weight.append(float(r["sample_weight"]))

        if not pooled_pred:
            print(f"{label:32s} -- no evaluable fold")
            continue

        base = sum(
            w * y for w, y in zip(pooled_weight, pooled_label, strict=True)
        ) / sum(pooled_weight)
        model_brier = brier(pooled_pred, pooled_label, pooled_weight)
        clim_brier = brier(
            [base] * len(pooled_pred), pooled_label, pooled_weight
        )
        bss = 1.0 - model_brier / clim_brier if clim_brier > 0 else float("nan")
        area = auc(pooled_pred, pooled_label)
        results.append(
            {
                "features": label,
                "columns": list(feature_names),
                "out_of_fold_cases": len(pooled_pred),
                "evaluated_folds": len(years) - len(unevaluated),
                "unevaluated_years": unevaluated,
                "brier": round(model_brier, 6),
                "climatology_brier": round(clim_brier, 6),
                "brier_skill_score": round(bss, 4),
                "auc": None if area is None else round(area, 4),
            }
        )

    print(f"{'feature set':32s} {'BSS':>8s} {'AUC':>7s} {'folds':>6s}")
    print("-" * 58)
    for r in sorted(results, key=lambda x: -(x["brier_skill_score"])):
        print(
            f"{r['features']:32s} {r['brier_skill_score']:+8.4f} "
            f"{r['auc']:7.3f} {r['evaluated_folds']:6d}"
        )

    print(
        "\nBSS > 0 beats climatology on pooled out-of-fold development cases."
        "\nAUC 0.5 is a coin flip."
    )
    print(
        f"\nCAVEAT: {positives} positive case(s) over {len(dev)} development "
        "rows. Differences of a few hundredths are inside the noise; treat this "
        "as a direction to pre-register, never as evidence of skill."
    )
    print("The 2023-2025 test period was not read and remains usable once.")

    if args.report:
        out = Path(args.report)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(
                {
                    "purpose": (
                        "exploratory feature diagnostic on development years "
                        "only; cannot promote an artifact"
                    ),
                    "development_years": years,
                    "development_rows": len(dev),
                    "development_positives": positives,
                    "test_rows_not_read": held_back,
                    "l2_penalty": args.l2,
                    "scheme": "leave-one-year-out, pooled out-of-fold",
                    "results": results,
                },
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

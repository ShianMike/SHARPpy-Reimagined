"""Prepared conservative interpolation for repeated vertical profile reads.

Comparison can sample several fields on the same height
column.  Preparing the finite, sorted coordinate once per field avoids sorting
that column again for every displayed level while retaining the existing
no-extrapolation and maximum-gap rules.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

VerticalInterpolator = Callable[[float], float | None]


def prepare_vertical_interpolator(
    heights,
    values,
    *,
    max_gap_m: float,
) -> VerticalInterpolator:
    """Return a reusable scalar interpolator over cleaned vertical columns."""

    try:
        height_column = np.asarray(
            np.ma.asarray(heights, dtype=float).filled(np.nan),
            dtype=float,
        ).reshape(-1)
        value_column = np.asarray(
            np.ma.asarray(values, dtype=float).filled(np.nan),
            dtype=float,
        ).reshape(-1)
    except (TypeError, ValueError):
        return lambda _target: None
    size = min(height_column.size, value_column.size)
    finite = np.isfinite(height_column[:size]) & np.isfinite(value_column[:size])
    x = height_column[:size][finite]
    y = value_column[:size][finite]
    if not x.size:
        return lambda _target: None

    order = np.argsort(x)
    x, y = x[order], y[order]
    x, indices = np.unique(x, return_index=True)
    y = y[indices]
    gap_limit = float(max_gap_m)

    def interpolate(target: float) -> float | None:
        target = float(target)
        if not np.isfinite(target):
            return None
        upper = int(np.searchsorted(x, target, side="right"))

        # The previous implementation used ``np.isclose`` across the complete
        # column.  Only the neighbours around the insertion point can match;
        # checking them in ascending order retains the same first-match rule.
        tolerance = 0.01 + 1e-05 * abs(target)
        first_close = int(np.searchsorted(x, target - tolerance, side="left"))
        if first_close < x.size and np.isclose(x[first_close], target, atol=0.01):
            return float(y[first_close])

        lower = upper - 1
        if lower < 0 or upper >= x.size or x[upper] - x[lower] > gap_limit:
            return None
        weight = (target - x[lower]) / (x[upper] - x[lower])
        return float(y[lower] + weight * (y[upper] - y[lower]))

    return interpolate


__all__ = ["VerticalInterpolator", "prepare_vertical_interpolator"]

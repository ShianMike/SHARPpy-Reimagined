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
    import sharpmod_rs

    try:
        height_column = np.ascontiguousarray(
            np.ma.asarray(heights, dtype=float).filled(np.nan).reshape(-1),
            dtype=float,
        )
        value_column = np.ascontiguousarray(
            np.ma.asarray(values, dtype=float).filled(np.nan).reshape(-1),
            dtype=float,
        )
    except (TypeError, ValueError):
        return lambda _target: None
    return sharpmod_rs.prepare_vertical_interpolator(
        height_column, value_column, float(max_gap_m),
    )


__all__ = ["VerticalInterpolator", "prepare_vertical_interpolator"]

"""SHARPpy Reimagined Qt6/PySide6 rendering widgets.

Skew-T, hodograph, storm slinky, wind barbs, index tables, the SHIP chart
inset, the customizable SARS-slot panel, and the window composition used by the
headless renderer.
"""

from __future__ import annotations

from .skew import (
    HGZOverlay,
    SkewTHGZOverlayMixin,
    draw_hgz_overlay,
    HGZ_TOP_ISOTHERM,
    HGZ_BOTTOM_ISOTHERM,
)
from .ship import plotSHIP, SHIP_SCALE_MIN, SHIP_SCALE_MAX
from .thermo import (
    plotDerivedIndices,
    derived_rows,
    format_derived_value,
    DERIVED_INDEX_ROWS,
    MISSING_STR,
)

__all__ = [
    # Skew-T HGZ overlay (task 15.1)
    "HGZOverlay",
    "SkewTHGZOverlayMixin",
    "draw_hgz_overlay",
    "HGZ_TOP_ISOTHERM",
    "HGZ_BOTTOM_ISOTHERM",
    # SHIP chart inset (task 14.1)
    "plotSHIP",
    "SHIP_SCALE_MIN",
    "SHIP_SCALE_MAX",
    # Derived-parameter index table (task 17.2)
    "plotDerivedIndices",
    "derived_rows",
    "format_derived_value",
    "DERIVED_INDEX_ROWS",
    "MISSING_STR",
]

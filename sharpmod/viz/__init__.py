"""SHARPpy Reimagined Qt6/PySide6 rendering widgets.

Skew-T, hodograph, storm slinky, wind barbs, index tables, SHIP and
streamwiseness chart insets, the customizable SARS-slot panel, and the window
composition used by the headless renderer.
"""

from __future__ import annotations

from .skew import (
    HGZOverlay,
    SkewTHGZOverlayMixin,
    draw_hgz_overlay,
    HGZ_TOP_ISOTHERM,
    HGZ_BOTTOM_ISOTHERM,
    CAPE_FILL_COLOR,
    CIN_FILL_COLOR,
    draw_cape_fill,
)
from .ship import plotSHIP, SHIP_SCALE_MIN, SHIP_SCALE_MAX
from .streamwiseness import (
    StreamwisenessData,
    plotStreamwiseness,
    streamwiseness_profile,
)
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
    # Skew-T CAPE/CIN buoyancy-area fill
    "CAPE_FILL_COLOR",
    "CIN_FILL_COLOR",
    "draw_cape_fill",
    # SHIP chart inset (task 14.1)
    "plotSHIP",
    "SHIP_SCALE_MIN",
    "SHIP_SCALE_MAX",
    # 0-6 km streamwiseness chart inset
    "StreamwisenessData",
    "plotStreamwiseness",
    "streamwiseness_profile",
    # Derived-parameter index table (task 17.2)
    "plotDerivedIndices",
    "derived_rows",
    "format_derived_value",
    "DERIVED_INDEX_ROWS",
    "MISSING_STR",
]

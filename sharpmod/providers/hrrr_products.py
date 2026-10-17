"""HRRR gridded product catalogue: what to fetch, how to derive it, how to colour it.

No Qt, no network, no file IO. This module is the declarative half of the model
overlay feature: it names the GRIB records each product needs, the arithmetic
that turns those records into the displayed field, and the colour scale the
result is painted with. :mod:`sharpmod.providers.hrrr_field` does the fetching and
rendering against this catalogue.

Keeping it separate is what makes the products testable without a network or a
display: every entry here can be checked for a coherent ingredient list, a
finite colour range, and a derivation that runs on synthetic arrays.

Design notes
------------
*Native fields only.* Every product resolves to records HRRR actually
publishes, verified against a live ``.idx`` inventory. Nothing here needs a
parcel ascent, so a whole-CONUS field costs one byte-range download and a
vectorised NumPy expression rather than a lifted sounding per grid point.

*Fixed-layer composites.* SCP and STP are published by SPC from *effective
inflow layer* ingredients, which cannot be recovered from 2-D output. The
entries below use the fixed-layer formulations that HRRR's own records support,
which is what every convection-allowing model page does, and each one says so in
its ``caveat`` so the difference is never silent.
"""

from __future__ import annotations

from dataclasses import dataclass, field as _dataclass_field, replace
from fnmatch import fnmatchcase
from functools import lru_cache
from types import MappingProxyType
from typing import Callable, Literal

import numpy as np

#: The two HRRR CONUS output files a product can draw records from. ``sfc`` is
#: the 2-D/derived-parameter file; ``prs`` carries the pressure-level grids.
SOURCE_SURFACE = "sfc"
SOURCE_PRESSURE = "prs"

#: Sentinel used for "no data here" throughout the pipeline. NaN rather than a
#: magic number so arithmetic propagates it instead of quietly computing on it.
MISSING = np.nan

# --------------------------------------------------------------------------- #
# Unit conversions. Kept as named functions so a product's arithmetic reads in
# the units the forecaster expects rather than the units GRIB happens to store.
# --------------------------------------------------------------------------- #

#: Metres per second to knots.
MS_TO_KT = 1.9438444924406046
#: Geopotential metres to decametres, the unit height contours are labelled in.
M_TO_DAM = 0.1


def kelvin_to_fahrenheit(values: np.ndarray) -> np.ndarray:
    return (values - 273.15) * 9.0 / 5.0 + 32.0


def kelvin_to_celsius(values: np.ndarray) -> np.ndarray:
    return values - 273.15


def ms_to_kt(values: np.ndarray) -> np.ndarray:
    return values * MS_TO_KT


def mm_to_in(values: np.ndarray) -> np.ndarray:
    return values / 25.4


def m_to_in(values: np.ndarray) -> np.ndarray:
    return values * 1000.0 / 25.4


def precipitation_rate_to_mm_per_hour(values: np.ndarray) -> np.ndarray:
    return values * 3600.0


def precipitation_rate_to_in_per_hour(values: np.ndarray) -> np.ndarray:
    return mm_to_in(precipitation_rate_to_mm_per_hour(values))


# --------------------------------------------------------------------------- #
# GRIB record addressing
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class FieldSpec:
    """One GRIB message to pull, addressed the way a ``.idx`` line spells it.

    ``variable`` and ``level`` are matched verbatim against the inventory's
    third and fourth colon-separated columns, because those strings are exactly
    what NCEP publishes and paraphrasing them is how a product silently starts
    matching the wrong record.

    ``forecast`` is a substring, a whole-column glob when it contains ``*``, or
    ``""`` for the instantaneous field. ``max fcst`` selects hourly maxima;
    ``0-* acc fcst`` selects run totals in either hours or days, excluding
    preceding-hour accumulations such as ``10-11 hour acc fcst``.
    """

    name: str
    source: str
    variable: str
    level: str
    forecast: str = ""

    def matches(self, variable: str, level: str, forecast: str) -> bool:
        """Return whether an inventory row addresses this record."""
        if variable != self.variable or level != self.level:
            return False
        if self.forecast:
            return (fnmatchcase(forecast, self.forecast) if "*" in self.forecast
                    else self.forecast in forecast)
        # An instantaneous request must not match a max/min/average record.
        return not any(token in forecast
                       for token in (" max ", " min ", " ave ", " acc "))


def _sfc(name: str, variable: str, level: str, forecast: str = "") -> FieldSpec:
    return FieldSpec(name, SOURCE_SURFACE, variable, level, forecast)


def _prs(name: str, variable: str, level: str) -> FieldSpec:
    return FieldSpec(name, SOURCE_PRESSURE, variable, level)


# Ingredient records, named once and reused across products so two products can
# never disagree about which record a name refers to.
F_REFC = _sfc("refc", "REFC", "entire atmosphere")
F_TMP2M = _sfc("tmp2m", "TMP", "2 m above ground")
F_DPT2M = _sfc("dpt2m", "DPT", "2 m above ground")
F_PRES = _sfc("pres", "PRES", "surface")
F_RH2M = _sfc("rh2m", "RH", "2 m above ground")
# NOAA documents LTNG in flashes/km²/5 min despite its dimensionless GRIB label.
F_LTNG = _sfc("ltng", "LTNG", "entire atmosphere")
F_SBCAPE = _sfc("sbcape", "CAPE", "surface")
F_SBCIN = _sfc("sbcin", "CIN", "surface")
F_MLCAPE = _sfc("mlcape", "CAPE", "180-0 mb above ground")
F_MLCIN = _sfc("mlcin", "CIN", "180-0 mb above ground")
F_MUCAPE = _sfc("mucape", "CAPE", "255-0 mb above ground")
F_CAPE03 = _sfc("cape03", "CAPE", "0-3000 m above ground")
F_SRH01 = _sfc("srh01", "HLCY", "1000-0 m above ground")
F_SRH03 = _sfc("srh03", "HLCY", "3000-0 m above ground")
F_USHR01 = _sfc("ushr01", "VUCSH", "0-1000 m above ground")
F_VSHR01 = _sfc("vshr01", "VVCSH", "0-1000 m above ground")
F_USHR06 = _sfc("ushr06", "VUCSH", "0-6000 m above ground")
F_VSHR06 = _sfc("vshr06", "VVCSH", "0-6000 m above ground")
F_LCL = _sfc("lcl", "HGT", "level of adiabatic condensation from sfc")
F_UH03 = _sfc("uh03", "MXUPHL", "3000-0 m above ground", forecast="max fcst")
F_SFC_HGT = _sfc("sfchgt", "HGT", "surface")
F_APCP = _sfc("apcp", "APCP", "surface", forecast="0-* acc fcst")
F_CFRZR = _sfc("cfrzr", "CFRZR", "surface")
F_CSNOW = _sfc("csnow", "CSNOW", "surface")
F_CICEP = _sfc("cicep", "CICEP", "surface")
F_EFHL = _sfc("efhl", "EFHL", "surface")
F_GUST = _sfc("gust", "GUST", "surface")
F_LFTX = _sfc("lftx", "LFTX", "500-1000 mb")
F_MAXUVV = _sfc("maxuvv", "MAXUVV", "100-1000 mb above ground", forecast="max fcst")
F_PRATE = _sfc("prate", "PRATE", "surface")
F_PWAT = _sfc("pwat", "PWAT", "entire atmosphere (considered as a single layer)")
F_SNOD = _sfc("snod", "SNOD", "surface")
F_U10 = _sfc("u10", "UGRD", "10 m above ground")
F_V10 = _sfc("v10", "VGRD", "10 m above ground")


# --------------------------------------------------------------------------- #
# Colour scales
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Palette:
    """A colour scale for one product.

    ``stops`` are ``(value, "#rrggbb")`` in the product's display units and in
    ascending order. ``stepped`` selects banded rather than blended colour:
    reflectivity and the SPC parameter fields are published in discrete classes,
    and a smooth ramp misreports them as a continuum.

    ``floor`` is the value below which nothing is drawn at all, which is what
    keeps a CAPE field from painting a flat wash over the entire country. It is
    distinct from ``stops[0][0]`` so a scale can start colouring at its first
    stop while still hiding values beneath it.

    ``ceiling`` is the mirror of ``floor``, for a field whose significant end is
    its *low* end. Convective inhibition is the case: it is conventionally
    plotted negative, so the values worth seeing are the large negative ones and
    the ones to hide are those near zero. Without this the weak end cannot be
    suppressed, and a CIN map spends most of its area on ground that is not
    capped at all.
    """

    stops: tuple[tuple[float, str], ...]
    units: str
    stepped: bool = False
    floor: float | None = None
    ceiling: float | None = None
    #: Labelled ticks for the colour bar. Empty means "use the stop values".
    ticks: tuple[float, ...] = ()
    display: Literal["auto", "fixed", "temperature-difference"] = "auto"

    def __post_init__(self) -> None:
        if self.display not in {"auto", "fixed", "temperature-difference"}:
            raise ValueError("unknown palette display quantity")
        if len(self.stops) < 2:
            raise ValueError("a palette needs at least two stops")
        if (self.floor is not None and self.ceiling is not None
                and self.ceiling <= self.floor):
            raise ValueError("palette ceiling must exceed its floor")
        values = [value for value, _colour in self.stops]
        if any(later <= earlier
               for earlier, later in zip(values, values[1:])):
            raise ValueError("palette stops must ascend")
        for _value, colour in self.stops:
            if not (isinstance(colour, str) and len(colour) == 7
                    and colour.startswith("#")):
                raise ValueError(f"expected #rrggbb, got {colour!r}")

    @property
    def minimum(self) -> float:
        return self.stops[0][0]

    @property
    def maximum(self) -> float:
        return self.stops[-1][0]

    def tick_values(self) -> tuple[float, ...]:
        if self.ticks:
            return self.ticks
        return tuple(value for value, _colour in self.stops)


# Model reflectivity: the supplied 2.5 dBZ colour bands, without interpolation.
PALETTE_REFLECTIVITY = Palette(
    stops=(
        (7.5, "#ffffff"), (10.0, "#d8e2f3"), (12.5, "#b1c5e6"),
        (15.0, "#8aa8da"), (17.5, "#628acd"), (20.0, "#3b6dc1"),
        (22.5, "#1450b4"), (25.0, "#0f505f"), (27.5, "#42756d"),
        (30.0, "#769b7c"), (32.5, "#a9c08a"), (35.0, "#fff371"),
        (37.5, "#f6d35a"), (40.0, "#edb344"), (42.5, "#e4942d"),
        (45.0, "#db7417"), (47.5, "#d25400"), (50.0, "#cc0000"),
        (52.5, "#a10205"), (55.0, "#76030a"), (57.5, "#a037af"),
        (60.0, "#8125a1"), (62.5, "#611293"), (65.0, "#828282"),
        (67.5, "#b4b4b4"), (70.0, "#e6e6e6"),
        (72.5, "#e6e6e6"),  # Close the final band at the legend's upper edge.
    ),
    units="dBZ", stepped=True, floor=7.5,
    ticks=(7.5, 10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0,
           45.0, 50.0, 55.0, 60.0, 65.0, 70.0, 72.5),
)

# Surface temperature: colour bands sampled from the supplied Fahrenheit table.
PALETTE_TEMPERATURE_F = Palette(
    stops=(
        (-61.0, "#1f497e"), (-60.0, "#326681"), (-57.0, "#49798a"),
        (-55.0, "#558499"), (-53.0, "#6397a3"), (-51.0, "#79a6ac"),
        (-48.0, "#8ebfbf"), (-45.0, "#a3d0d1"), (-43.0, "#acdfda"),
        (-41.0, "#b3ebe0"), (-38.0, "#acdfda"), (-35.0, "#a3d0d1"),
        (-33.0, "#a4bdd7"), (-27.0, "#9aa1c7"), (-22.0, "#9380c2"),
        (-16.0, "#8b64ba"), (-12.0, "#854bb5"), (-7.0, "#7f39b0"),
        (-3.0, "#7a25ab"), (0.0, "#9f41ad"), (2.0, "#a85fb7"),
        (5.0, "#b583c6"), (8.0, "#c0a6d3"), (11.0, "#cac7d4"),
        (13.0, "#c5d5e6"), (14.0, "#d7e6ee"), (17.0, "#c5d5e6"),
        (19.0, "#a4bdd7"), (22.0, "#799dd0"), (25.0, "#5481c6"),
        (28.0, "#3567b7"), (30.0, "#1953b4"), (32.0, "#0f4354"),
        (33.0, "#1c4e5a"), (34.0, "#2a585f"), (35.0, "#376363"),
        (36.0, "#456d68"), (37.0, "#52776d"), (38.0, "#5f8172"),
        (39.0, "#6d8c77"), (40.0, "#7a967c"), (41.0, "#88a080"),
        (42.0, "#95aa85"), (43.0, "#a3b58a"), (44.0, "#b0bf8f"),
        (45.0, "#bdc994"), (46.0, "#cbd399"), (47.0, "#d8de9d"),
        (48.0, "#e6e8a2"), (49.0, "#f3f2a7"), (50.0, "#f8eea2"),
        (51.0, "#f0e199"), (52.0, "#e8d591"), (53.0, "#e1c888"),
        (54.0, "#d9bc80"), (55.0, "#d1af77"), (56.0, "#c9a36f"),
        (57.0, "#c19666"), (58.0, "#ba8a5e"), (59.0, "#b27d55"),
        (60.0, "#aa714d"), (61.0, "#a26444"), (62.0, "#9b583c"),
        (63.0, "#934b33"), (64.0, "#8b3f2b"), (65.0, "#833222"),
        (66.0, "#7b261a"), (67.0, "#741911"), (68.0, "#6c0d09"),
        (69.0, "#640000"), (70.0, "#5f0000"), (71.0, "#630507"),
        (72.0, "#670a0e"), (73.0, "#6c0f15"), (74.0, "#70141c"),
        (75.0, "#741824"), (76.0, "#781d2b"), (77.0, "#7d2232"),
        (78.0, "#812739"), (79.0, "#852c40"), (80.0, "#73372d"),
        (81.0, "#7a4036"), (82.0, "#80493f"), (83.0, "#875349"),
        (84.0, "#8e5c52"), (85.0, "#94655b"), (86.0, "#9b6e64"),
        (87.0, "#a2776d"), (88.0, "#a88177"), (89.0, "#af8a80"),
        (90.0, "#b8968c"), (92.0, "#c1a69d"), (93.0, "#ccb3aa"),
        (96.0, "#dbd3cd"), (98.0, "#dfdddb"), (99.0, "#e6e5e5"),
        (100.0, "#dfdddb"), (101.0, "#dbd3cd"), (103.0, "#c4c0be"),
        (105.0, "#b8b5b3"), (107.0, "#aaa9a9"), (108.0, "#a4a19f"),
        (109.0, "#999999"), (110.0, "#959391"), (111.0, "#8d8b8a"),
        (112.0, "#838281"), (113.0, "#7c7b7a"), (114.0, "#747372"),
        (115.0, "#686867"), (118.0, "#545554"), (120.0, "#474747"),
        (121.0, "#474747"),  # Close the final band at the legend's upper edge.
    ),
    units="\u00b0F", stepped=True,
    ticks=(-61.0, -60.0, -50.0, -40.0, -30.0, -20.0, -10.0, 0.0,
           10.0, 20.0, 30.0, 32.0, 40.0, 50.0, 60.0, 70.0, 80.0,
           90.0, 100.0, 110.0, 120.0, 121.0),
)

# Dewpoint: colour bands sampled from the supplied Fahrenheit table.
PALETTE_DEWPOINT_F = Palette(
    stops=(
        (-41.0, "#986d4d"), (-40.0, "#966c4c"), (-39.0, "#946b4c"),
        (-38.0, "#926a4b"), (-37.0, "#90694b"), (-36.0, "#8e684a"),
        (-35.0, "#8c664a"), (-34.0, "#8a6549"), (-33.0, "#886448"),
        (-32.0, "#866348"), (-31.0, "#846247"), (-30.0, "#826147"),
        (-29.0, "#806046"), (-28.0, "#7e5f46"), (-27.0, "#7c5e45"),
        (-26.0, "#7a5d44"), (-25.0, "#785b44"), (-24.0, "#765a43"),
        (-23.0, "#745943"), (-22.0, "#725842"), (-21.0, "#715742"),
        (-20.0, "#6f5641"), (-19.0, "#6d5540"), (-18.0, "#6b5440"),
        (-17.0, "#69533f"), (-16.0, "#67523f"), (-15.0, "#65503e"),
        (-14.0, "#634f3d"), (-13.0, "#614e3d"), (-12.0, "#5f4d3c"),
        (-11.0, "#5d4c3c"), (-10.0, "#5b4b3b"), (-9.0, "#594a3b"),
        (-8.0, "#57493a"), (-7.0, "#554839"), (-6.0, "#534739"),
        (-5.0, "#514538"), (-4.0, "#4f4438"), (-3.0, "#4d4337"),
        (-2.0, "#4b4237"), (-1.0, "#494136"), (0.0, "#4d4334"),
        (1.0, "#514738"), (2.0, "#564c3c"), (3.0, "#5a5041"),
        (4.0, "#5e5545"), (5.0, "#625949"), (6.0, "#675e4d"),
        (7.0, "#6b6251"), (8.0, "#6f6755"), (9.0, "#746b5a"),
        (10.0, "#78705e"), (11.0, "#7c7462"), (12.0, "#807966"),
        (13.0, "#857d6a"), (14.0, "#89826f"), (15.0, "#8d8673"),
        (16.0, "#928b77"), (17.0, "#968f7b"), (18.0, "#9a947f"),
        (19.0, "#9e9883"), (20.0, "#a39d88"), (21.0, "#a7a18c"),
        (22.0, "#aba690"), (23.0, "#afaa94"), (24.0, "#b4af98"),
        (25.0, "#b8b39c"), (26.0, "#bcb8a1"), (27.0, "#c1bca5"),
        (28.0, "#c5c1a9"), (29.0, "#c9c5ad"), (30.0, "#cdcab1"),
        (31.0, "#d2ceb6"), (32.0, "#d6d3ba"), (33.0, "#dad7be"),
        (34.0, "#dfdcc2"), (35.0, "#e3e0c6"), (36.0, "#e7e5ca"),
        (37.0, "#ebe9cf"), (38.0, "#f0eed3"), (39.0, "#f4f2d7"),
        (40.0, "#e6f5e6"), (41.0, "#d7f0d7"), (42.0, "#c8eac8"),
        (43.0, "#b9e5b9"), (44.0, "#aadfaa"), (45.0, "#9bda9b"),
        (46.0, "#8cd48c"), (47.0, "#7dcf7d"), (48.0, "#6ec96e"),
        (49.0, "#5fc45f"), (50.0, "#30ae30"), (51.0, "#2ca32c"),
        (52.0, "#279927"), (53.0, "#238e23"), (54.0, "#1e831e"),
        (55.0, "#1a791a"), (56.0, "#156e15"), (57.0, "#116311"),
        (58.0, "#0c590c"), (59.0, "#084e08"), (60.0, "#61a3af"),
        (61.0, "#5896a0"), (62.0, "#508992"), (63.0, "#477b83"),
        (64.0, "#3e6e74"), (65.0, "#366166"), (66.0, "#2d5457"),
        (67.0, "#244648"), (68.0, "#1c393a"), (69.0, "#132c2b"),
        (70.0, "#66669a"), (71.0, "#605e94"), (72.0, "#59568e"),
        (73.0, "#534e88"), (74.0, "#4d4682"), (75.0, "#463e7c"),
        (76.0, "#403676"), (77.0, "#3a2e70"), (78.0, "#33266a"),
        (79.0, "#2d1e64"), (80.0, "#724071"), (81.0, "#784573"),
        (82.0, "#7d4b75"), (83.0, "#835076"), (84.0, "#885678"),
        (85.0, "#8e5b7a"), (86.0, "#93617c"), (87.0, "#99667d"),
        (88.0, "#9e6c7f"), (89.0, "#a47181"),
        (90.0, "#a47181"),  # Close the final band at the legend's upper edge.
    ),
    units="\u00b0F", stepped=True,
    ticks=(-41.0, -40.0, -30.0, -20.0, -10.0, 0.0, 10.0, 20.0,
           30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0),
)

# Relative humidity: supplied 1% colour bands within the physical 0-100% range.
PALETTE_RELATIVE_HUMIDITY = Palette(
    stops=(
        (0.0, "#9a6c49"), (1.0, "#8f6546"), (2.0, "#845f42"),
        (3.0, "#79583e"), (4.0, "#6f513b"), (5.0, "#644a37"),
        (6.0, "#594333"), (7.0, "#4e3d2f"), (8.0, "#43362c"),
        (9.0, "#382f28"), (10.0, "#3c352a"), (11.0, "#423a2f"),
        (12.0, "#474035"), (13.0, "#4d453a"), (14.0, "#524b3f"),
        (15.0, "#585044"), (16.0, "#5d554a"), (17.0, "#635b4f"),
        (18.0, "#686054"), (19.0, "#6e6559"), (20.0, "#746b5f"),
        (21.0, "#797064"), (22.0, "#7f7669"), (23.0, "#847b6f"),
        (24.0, "#8a8074"), (25.0, "#8f8679"), (26.0, "#958b7e"),
        (27.0, "#9a9084"), (28.0, "#a09689"), (29.0, "#a59b8e"),
        (30.0, "#aba194"), (31.0, "#b1a699"), (32.0, "#b6ab9e"),
        (33.0, "#bcb1a3"), (34.0, "#c1b6a9"), (35.0, "#c7bbae"),
        (36.0, "#ccc1b3"), (37.0, "#d2c6b8"), (38.0, "#d7ccbe"),
        (39.0, "#ddd1c3"), (40.0, "#c8d7c0"), (41.0, "#c4d4bd"),
        (42.0, "#c0d1ba"), (43.0, "#bcceb7"), (44.0, "#b8cbb4"),
        (45.0, "#b4c9b1"), (46.0, "#b0c6ae"), (47.0, "#abc3ab"),
        (48.0, "#a7c0a8"), (49.0, "#a3bda5"), (50.0, "#9fbaa2"),
        (51.0, "#9bb79f"), (52.0, "#97b49c"), (53.0, "#93b29a"),
        (54.0, "#8faf97"), (55.0, "#8bac94"), (56.0, "#87a991"),
        (57.0, "#83a68e"), (58.0, "#7fa38b"), (59.0, "#7aa088"),
        (60.0, "#769d85"), (61.0, "#729b82"), (62.0, "#6e987f"),
        (63.0, "#6a957c"), (64.0, "#669279"), (65.0, "#628f76"),
        (66.0, "#5e8c73"), (67.0, "#5a8970"), (68.0, "#56866d"),
        (69.0, "#52846a"), (70.0, "#4e8167"), (71.0, "#497e64"),
        (72.0, "#457b61"), (73.0, "#41785e"), (74.0, "#3d755b"),
        (75.0, "#397258"), (76.0, "#356f55"), (77.0, "#316d53"),
        (78.0, "#2d6a50"), (79.0, "#29674d"), (80.0, "#25644a"),
        (81.0, "#216147"), (82.0, "#1d5e44"), (83.0, "#185b41"),
        (84.0, "#14583e"), (85.0, "#10563b"), (86.0, "#0c5338"),
        (87.0, "#085035"), (88.0, "#044d32"), (89.0, "#004a2f"),
        (90.0, "#004123"), (91.0, "#04432e"), (92.0, "#084638"),
        (93.0, "#0c4843"), (94.0, "#104a4d"), (95.0, "#144d58"),
        (96.0, "#184f62"), (97.0, "#1c516d"), (98.0, "#205377"),
        (99.0, "#245682"), (100.0, "#28588c"),
    ),
    units="%", stepped=True,
    ticks=(0.0, 10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0),
)

# CAPE: supplied colour bands; the printed 6000-to-8500 segment uses 500 J/kg bands.
PALETTE_CAPE = Palette(
    stops=(
        (0.0, "#ffffff"), (100.0, "#f0f0f0"), (200.0, "#e1e1e1"),
        (300.0, "#d2d2d2"), (400.0, "#c3c3c3"), (500.0, "#a5a5a5"),
        (600.0, "#969696"), (700.0, "#878787"), (800.0, "#787878"),
        (900.0, "#696969"), (1000.0, "#37536a"), (1100.0, "#436075"),
        (1200.0, "#506d80"), (1300.0, "#5c7a8b"), (1400.0, "#698796"),
        (1500.0, "#7594a2"), (1600.0, "#82a1ad"), (1700.0, "#8eaeb8"),
        (1800.0, "#9bbbc3"), (1900.0, "#a7c8ce"), (2000.0, "#e9dd96"),
        (2100.0, "#e8d186"), (2200.0, "#e7c575"), (2300.0, "#e6b865"),
        (2400.0, "#e5ac54"), (2500.0, "#e5a044"), (2600.0, "#e49433"),
        (2700.0, "#e38723"), (2800.0, "#e27b12"), (2900.0, "#e16f02"),
        (3000.0, "#dc4110"), (3100.0, "#d33b17"), (3200.0, "#ca351e"),
        (3300.0, "#c12e25"), (3400.0, "#b8282c"), (3500.0, "#af2234"),
        (3600.0, "#a61c3b"), (3700.0, "#9d1542"), (3800.0, "#940f49"),
        (3900.0, "#8b0950"), (4000.0, "#73088a"), (4100.0, "#7e1894"),
        (4200.0, "#8a289f"), (4300.0, "#9538a9"), (4400.0, "#a148b3"),
        (4500.0, "#ac59be"), (4600.0, "#b869c8"), (4700.0, "#c379d2"),
        (4800.0, "#cf89dd"), (4900.0, "#da99e7"), (5000.0, "#e9bec3"),
        (5100.0, "#e3b0b7"), (5200.0, "#dda3ac"), (5300.0, "#d795a0"),
        (5400.0, "#d18894"), (5500.0, "#ca7a89"), (5600.0, "#c46d7d"),
        (5700.0, "#be5f71"), (5800.0, "#b85266"), (5900.0, "#b2445a"),
        (6000.0, "#893d48"), (6500.0, "#8f4752"), (7000.0, "#96525b"),
        (7500.0, "#9c5c65"), (8000.0, "#a2676f"), (8500.0, "#a97178"),
        (9000.0, "#af7c82"), (9500.0, "#b6868b"), (10000.0, "#bc9195"),
        (10500.0, "#bc9195"),  # Close the final band at the legend's upper edge.
    ),
    units="J/kg", stepped=True, floor=100.0,
    ticks=(0.0, 500.0, 1000.0, 1500.0, 2000.0, 2500.0, 3000.0, 3500.0,
           4000.0, 4500.0, 5000.0, 5500.0, 6000.0, 8500.0, 10500.0),
)

# The supplied lightning table reuses the first 61 CAPE colours at these edges.
_LIGHTNING_EDGES = (
    *(value / 100.0 for value in range(10)),
    *(value / 100.0 for value in range(10, 30, 2)),
    *(value / 100.0 for value in range(30, 100, 7)),
    *(value / 10.0 for value in range(10, 30, 2)),
    *(value / 10.0 for value in range(30, 60, 3)),
    *(value / 10.0 for value in range(60, 150, 9)),
    15.0,
)
PALETTE_LIGHTNING = Palette(
    stops=tuple((value, colour) for value, (_, colour) in
                zip(_LIGHTNING_EDGES, PALETTE_CAPE.stops[:61], strict=True)),
    units="flashes", stepped=True, floor=0.01,
    ticks=(0.0, 0.1, 0.3, 1.0, 3.0, 6.0, 15.0),
)

# CIN: supplied colour bands and negative thresholds; zero inhibition stays hidden.
PALETTE_CIN = Palette(
    stops=(
        (-1050.0, "#bc9195"), (-1000.0, "#b6868b"), (-950.0, "#af7c82"),
        (-900.0, "#a97178"), (-850.0, "#a2676f"), (-800.0, "#9c5c65"),
        (-750.0, "#96525b"), (-700.0, "#8f4752"), (-650.0, "#893d48"),
        (-600.0, "#b2445a"), (-580.0, "#b85266"), (-560.0, "#be5f71"),
        (-540.0, "#c46d7d"), (-520.0, "#ca7a89"), (-500.0, "#d18894"),
        (-480.0, "#d795a0"), (-460.0, "#dda3ac"), (-440.0, "#e3b0b7"),
        (-420.0, "#e9bec3"), (-400.0, "#da99e7"), (-380.0, "#cf89dd"),
        (-360.0, "#c379d2"), (-340.0, "#b869c8"), (-320.0, "#ac59be"),
        (-300.0, "#a148b3"), (-280.0, "#9538a9"), (-260.0, "#8a289f"),
        (-240.0, "#7e1894"), (-220.0, "#73088a"), (-200.0, "#8b0950"),
        (-190.0, "#940f49"), (-180.0, "#9d1542"), (-170.0, "#a61c3b"),
        (-160.0, "#af2234"), (-150.0, "#b8282c"), (-140.0, "#c12e25"),
        (-130.0, "#ca351e"), (-120.0, "#d33b17"), (-110.0, "#dc4110"),
        (-100.0, "#e16f02"), (-95.0, "#e27b12"), (-90.0, "#e38723"),
        (-85.0, "#e49433"), (-80.0, "#e5a044"), (-75.0, "#e5ac54"),
        (-70.0, "#e6b865"), (-65.0, "#e7c575"), (-60.0, "#e8d186"),
        (-55.0, "#e9dd96"), (-50.0, "#a7c8ce"), (-47.5, "#9bbbc3"),
        (-45.0, "#8eaeb8"), (-42.5, "#82a1ad"), (-40.0, "#7594a2"),
        (-37.5, "#698796"), (-35.0, "#5c7a8b"), (-32.5, "#506d80"),
        (-30.0, "#436075"), (-27.5, "#37536a"), (-25.0, "#696969"),
        (-22.5, "#787878"), (-20.0, "#878787"), (-17.5, "#969696"),
        (-15.0, "#a5a5a5"), (-12.5, "#c3c3c3"), (-10.0, "#d2d2d2"),
        (-7.5, "#e1e1e1"), (-5.0, "#f0f0f0"), (-2.5, "#ffffff"),
        (0.0, "#ffffff"),  # Close the final band at the legend's upper edge.
    ),
    units="J/kg", stepped=True, ceiling=-2.5,
    ticks=(-1050.0, -850.0, -600.0, -500.0, -400.0, -300.0, -200.0,
           -150.0, -100.0, -75.0, -50.0, -37.5, -25.0, -12.5, 0.0),
)

# Lapse rate: supplied colour bands, keeping the observed 10.5 C/km extreme on scale.
PALETTE_LAPSE_RATE = Palette(
    stops=(
        (0.0, "#ffffff"), (0.6, "#f0f0f0"), (1.2, "#e1e1e1"),
        (1.8, "#d2d2d2"), (2.4, "#c3c3c3"), (3.0, "#a5a5a5"),
        (3.6, "#969696"), (4.2, "#878787"), (4.8, "#787878"),
        (5.4, "#696969"), (6.0, "#37536a"), (6.1, "#436075"),
        (6.2, "#506d80"), (6.3, "#5c7a8b"), (6.4, "#698796"),
        (6.5, "#7594a2"), (6.6, "#82a1ad"), (6.7, "#8eaeb8"),
        (6.8, "#9bbbc3"), (6.9, "#a7c8ce"), (7.0, "#e9dd96"),
        (7.1, "#e8d186"), (7.2, "#e7c575"), (7.3, "#e6b865"),
        (7.4, "#e5ac54"), (7.5, "#e5a044"), (7.6, "#e49433"),
        (7.7, "#e38723"), (7.8, "#e27b12"), (7.9, "#e16f02"),
        (8.0, "#dc4110"), (8.1, "#d33b17"), (8.2, "#ca351e"),
        (8.3, "#c12e25"), (8.4, "#b8282c"), (8.5, "#af2234"),
        (8.6, "#a61c3b"), (8.7, "#9d1542"), (8.8, "#940f49"),
        (8.9, "#8b0950"), (9.0, "#73088a"), (9.1, "#7e1894"),
        (9.2, "#8a289f"), (9.3, "#9538a9"), (9.4, "#a148b3"),
        (9.5, "#ac59be"), (9.6, "#b869c8"), (9.7, "#c379d2"),
        (9.8, "#cf89dd"), (9.9, "#da99e7"),
        (11.0, "#da99e7"),  # Close the final band at the legend's upper edge.
    ),
    units="\u00b0C/km", stepped=True,
    ticks=(0.0, 3.0, 6.0, 6.5, 7.0, 7.5, 8.0, 8.5, 9.0, 9.5, 11.0),
)


#: Supplied bulk-shear table, sampled at each 1 kt band.
_SHEAR_KT_STOPS = (
    (9.0, "#ffffff"), (10.0, "#f0f8ff"), (11.0, "#dbf0fe"),
    (12.0, "#c6e7fd"), (13.0, "#b1dffc"), (14.0, "#9cd6fb"),
    (15.0, "#87cefa"), (16.0, "#81b7f1"), (17.0, "#7ba0e8"),
    (18.0, "#7688df"), (19.0, "#7071d6"), (20.0, "#6a5acd"),
    (21.0, "#8366d0"), (22.0, "#9c72d3"), (23.0, "#b47ed6"),
    (24.0, "#cd8ad9"), (25.0, "#e696dc"), (26.0, "#e08ad6"),
    (27.0, "#da7ed0"), (28.0, "#d472ca"), (29.0, "#ce66c4"),
    (30.0, "#c85abe"), (31.0, "#c04cb6"), (32.0, "#b83eae"),
    (33.0, "#b030a6"), (34.0, "#a8229e"), (35.0, "#a01496"),
    (36.0, "#a81080"), (37.0, "#b00c6a"), (38.0, "#b80854"),
    (39.0, "#c0043e"), (40.0, "#c80028"), (41.0, "#cc082c"),
    (42.0, "#d01030"), (43.0, "#d41834"), (44.0, "#d82038"),
    (45.0, "#dc283c"), (46.0, "#e03040"), (47.0, "#e43844"),
    (48.0, "#e84048"), (49.0, "#ec484c"), (50.0, "#f05050"),
    (51.0, "#f27054"), (52.0, "#f49058"), (53.0, "#f6b05c"),
    (54.0, "#f8d060"), (55.0, "#faf064"), (56.0, "#f4e65e"),
    (57.0, "#eedc58"), (58.0, "#e8d252"), (59.0, "#e2c84c"),
    (60.0, "#dcbe46"), (61.0, "#d6b440"), (62.0, "#d0aa3a"),
    (63.0, "#caa034"), (64.0, "#c4962e"), (65.0, "#be8c28"),
    (66.0, "#b88222"), (67.0, "#b2781c"), (68.0, "#ac6e16"),
    (69.0, "#a66410"), (70.0, "#a05a0a"),
)


def _shear_palette(stops, floor) -> Palette:
    """Keep 5 kt shallow shear visible and deep shear on scale through 100 kt."""
    return Palette(
        stops=stops, units="kt", stepped=True, floor=floor,
        ticks=(stops[0][0], *range(10, 71, 5), stops[-1][0]),
    )


PALETTE_SHEAR_LOW_KT = _shear_palette(
    ((5.0, "#ffffff"), *_SHEAR_KT_STOPS[1:], (71.0, "#a05a0a")),
    5.0,
)

#: Values above the supplied table retain its final brown shade.
PALETTE_SHEAR_DEEP_KT = _shear_palette(
    (*_SHEAR_KT_STOPS, (100.0, "#a05a0a")),
    10.0,
)

#: The supplied SRH table uses the CAPE bands at one tenth their values.
PALETTE_SRH_LOW = Palette(
    stops=tuple((value / 10.0, colour) for value, colour in PALETTE_CAPE.stops),
    units="m^2/s^2", stepped=True, floor=25.0,
    ticks=tuple(value / 10.0 for value in PALETTE_CAPE.ticks),
)

#: Keep the deeper layer's existing floor while sharing the reference colors.
PALETTE_SRH = Palette(
    stops=PALETTE_SRH_LOW.stops,
    units="m^2/s^2", stepped=True, floor=50.0,
    ticks=PALETTE_SRH_LOW.ticks,
)

PALETTE_EHI = Palette(
    stops=(
        (0.5, "#e6eef6"), (1.0, "#bcd8ec"), (1.5, "#84b6da"),
        (2.0, "#f0e36a"), (2.5, "#e9b944"),
        (3.0, "#dd7d31"), (4.0, "#c32f27"), (6.0, "#8f1340"),
        (8.0, "#5c0b45"),
    ),
    units="", stepped=True, floor=0.5,
    ticks=(0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0),
)

#: Supplied SCP intervals reuse the first 50 CAPE colors.
_SCP_EDGES = (*(value / 2.0 for value in range(20)), *range(10, 30), *range(30, 50, 2))
PALETTE_SCP = Palette(
    stops=tuple((value, colour) for value, (_, colour) in
                zip(_SCP_EDGES, PALETTE_CAPE.stops[:50], strict=True))
    + ((52.0, "#da99e7"),),  # Keep the observed 50.4 extreme on scale.
    units="", stepped=True, floor=1.0,
    ticks=(0.0, *range(1, 11), *range(12, 31, 2), *range(34, 51, 4), 52.0),
)

#: The supplied STP table uses the CAPE bands at one thousandth their values.
PALETTE_STP = Palette(
    stops=tuple((value / 1000.0, colour) for value, colour in PALETTE_CAPE.stops),
    units="", stepped=True, floor=0.5,
    ticks=tuple(value / 1000.0 for value in PALETTE_CAPE.ticks),
)

#: Supplied UH intervals reuse the first 60 CAPE colors.
_UH_EDGES = (*(value * 2.5 for value in range(40)), *range(100, 200, 5))
PALETTE_UPDRAFT_HELICITY = Palette(
    stops=tuple((value, colour) for value, (_, colour) in
                zip(_UH_EDGES, PALETTE_CAPE.stops[:60], strict=True))
    + ((200.0, "#b2445a"),),
    units="m^2/s^2", stepped=True, floor=5.0,
    ticks=(0.0, *range(5, 101, 5), *range(110, 201, 10)),
)


#: Supplied height/wind table, sampled at each 1 kt band.
_ISOTACH_COLOURS = (
    "#ffffff", "#f0f8ff", "#e6f4ff", "#dbf0fe", "#d1ebfe", "#c6e7fd",
    "#bce3fd", "#b1dffc", "#a7dbfc", "#9cd6fb", "#92d2fb", "#87cefa",
    "#84c2f6", "#81b7f1", "#7eabed", "#7ba0e8", "#7994e4", "#7688df",
    "#737ddb", "#7071d6", "#6d66d2", "#6a5acd", "#7660cf", "#8366d0",
    "#8f6cd2", "#9c72d3", "#a878d5", "#b47ed6", "#c184d8", "#cd8ad9",
    "#da90db", "#e696dc", "#e390d9", "#e08ad6", "#dd84d3", "#da7ed0",
    "#d778cd", "#d472ca", "#d16cc7", "#ce66c4", "#cb60c1", "#c85abe",
    "#c453ba", "#c04cb6", "#bc45b2", "#b83eae", "#b437aa", "#b030a6",
    "#ac29a2", "#a8229e", "#a41b9a", "#a01496", "#a4128b", "#a81080",
    "#ac0e75", "#b00c6a", "#b40a5f", "#b80854", "#bc0649", "#c0043e",
    "#c40233", "#c80028", "#ca042a", "#cc082c", "#ce0c2e", "#d01030",
    "#d21432", "#d41834", "#d61c36", "#d82038", "#da243a", "#dc283c",
    "#de2c3e", "#e03040", "#e23442", "#e43844", "#e63c46", "#e84048",
    "#ea444a", "#ec484c", "#ee4c4e", "#f05050", "#f16052", "#f27054",
    "#f38056", "#f49058", "#f5a05a", "#f6b05c", "#f7c05e", "#f8d060",
    "#f9e062", "#faf064", "#f7eb61", "#f4e65e", "#f1e15b", "#eedc58",
    "#ebd755", "#e8d252", "#e5cd4f", "#e2c84c", "#dfc349", "#dcbe46",
    "#d9b943", "#d6b440", "#d3af3d", "#d0aa3a", "#cda537", "#caa034",
    "#c79b31", "#c4962e", "#c1912b", "#be8c28", "#bb8725", "#b88222",
    "#b57d1f", "#b2781c", "#af7319", "#ac6e16", "#a96913", "#a66410",
    "#a35f0d", "#a05a0a",
)


PALETTE_ISOTACH_KT = Palette(
    stops=tuple(zip(range(49, 171), _ISOTACH_COLOURS, strict=True))
    + ((171.0, "#a05a0a"),),
    units="kt", stepped=True, floor=50.0,
    ticks=(49.0, *range(50, 171, 5), 171.0),
)


# Precipitation totals and rates: colour bands sampled from the supplied inch table.
PALETTE_PRECIPITATION = Palette(
    stops=tuple(zip(
        (0.01, 0.03, 0.05, 0.075, 0.1, 0.15, 0.2, 0.25, 0.3, 0.35, 0.4, 0.45,
         0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95,
         1, 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9,
         2, 2.25, 2.5, 2.75, 3, 3.25, 3.5, 3.75, 4, 4.5, 5, 5.5, 6, 7,
         8, 9, 10, 12.5, 15, 17.5),
        ("#dcdcdc", "#bebebe", "#a0a0a0", "#828282", "#b7f0be", "#9fdbb3",
         "#87c7a7", "#6fb29c", "#579d90", "#3f8885", "#277479", "#146473",
         "#1450b4", "#2a61bb", "#3f73c2", "#5584c9", "#6b96d0", "#80a7d6",
         "#96b9dd", "#accae4", "#c1dceb", "#d7edf2", "#cebce0", "#c9addb",
         "#c49ed5", "#bf90d0", "#ba81ca", "#b472c5", "#af63bf", "#aa55ba",
         "#a546b4", "#a037af", "#a53a34", "#ad4842", "#b5554f", "#bd635d",
         "#c6716b", "#ce7f79", "#d68c86", "#de9a94", "#f8eea2", "#eed68c",
         "#e5bd76", "#dba560", "#d28c4a", "#c87434", "#ac632d", "#965727",
         "#814a21", "#6b3e1b", "#553115", "#553115"), strict=True)),
    # The repeated final colour closes the 15+ band at the reference's right edge.
    units="in", stepped=True, floor=0.01,
    ticks=(0.01, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9,
           1, 1.2, 1.4, 1.6, 1.8, 2, 2.5, 3, 3.5, 4, 5, 6, 8, 10, 15, 17.5),
)
PALETTE_PRECIPITATION_RATE = replace(PALETTE_PRECIPITATION, units="in/h")
# Winter precipitation rates: supplied colours, with identical adjacent bands combined.
PALETTE_FREEZING_RAIN_RATE = Palette(
    stops=(
        (0.1, "#ffa6af"), (0.25, "#ef82ab"), (0.75, "#df5893"),
        (1.5, "#cc3e80"), (2, "#c5266c"), (3, "#af0848"),
        (4, "#be112e"), (6, "#cc2d0d"), (10, "#ec3801"),
        (14, "#ff4504"), (16, "#ff4504"),
    ),
    units="mm/h", stepped=True, floor=0.1,
    ticks=(0.1, 0.5, 1, 2, 3, 4, 6, 10, 14, 16),
)
PALETTE_SLEET_RATE = replace(
    PALETTE_FREEZING_RAIN_RATE,
    stops=(
        (0.1, "#ada8ff"), (0.25, "#c376ff"), (0.75, "#c24df7"),
        (2, "#ad22d9"), (3, "#9700b9"), (4, "#a60983"),
        (5, "#a5119f"), (6, "#c02782"), (10, "#c5266c"),
        (12, "#dc445b"), (16, "#dc445b"),
    ),
)
PALETTE_SNOW_RATE = replace(
    PALETTE_FREEZING_RAIN_RATE,
    stops=(
        (0.1, "#00ffff"), (0.25, "#00eaff"), (0.75, "#00bfff"),
        (1, "#009ffd"), (2, "#006aee"), (2.5, "#0449d1"),
        (3.5, "#0326cf"), (4, "#0e0ba3"), (8, "#5b1291"),
        (10, "#a60983"), (12, "#ce0094"), (14, "#ff0080"),
        (16, "#ff0080"),
    ),
)
# Snow depth: colour bands sampled from the supplied inch table.
PALETTE_SNOW_DEPTH = Palette(
    stops=tuple(zip(
        (0, 0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 5.5,
         6, 7, 8, 9, 10, 11, 12, 14, 16, 18, 20, 22, 24, 26, 28, 30, 32,
         34, 36, 40, 44, 48, 52, 56, 60, 64),
        ("#ffffff", "#e1e1e1", "#c8c8c8", "#afafaf", "#969696", "#a9e7f2",
         "#72bfd7", "#3b98bb", "#0470a0", "#0046b0", "#2764bc", "#4d82c8",
         "#74a0d3", "#9abedf", "#c1dceb", "#c9addb", "#c195d2", "#b97ec9",
         "#b066c1", "#a84fb8", "#a037af", "#870a47", "#971c5a", "#a72d6e",
         "#b63f81", "#c65095", "#d662a8", "#eba7b7", "#e89ba1", "#e68f8a",
         "#e38274", "#e1765d", "#de6a47", "#dc8150", "#e19568", "#e6a97f",
         "#ecbd97", "#f1d1af", "#f6e5c6", "#fbf9de", "#fbf9de"),
        strict=True)),
    # The repeated final colour closes the 60+ band at the reference's right edge.
    units="in", stepped=True, floor=0.1,
    ticks=(0, 0.1, 0.5, 1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 28,
           32, 36, 44, 52, 60, 64),
)
# PWAT: 0.05 inch colour bands sampled from the supplied reference.
PALETTE_PRECIPITABLE_WATER = Palette(
    stops=tuple(zip(
        (step / 20 for step in range(1, 61)),
        ("#433920", "#534a31", "#635a42", "#736b52", "#837c63", "#928c74",
         "#a29d85", "#b2ad95", "#c2bea6", "#b9e5b9", "#a5d4a5", "#92c392",
         "#7eb37e", "#6aa26a", "#579157", "#438043", "#2f702f", "#1c5f1c",
         "#084e08", "#61a3af", "#5896a0", "#508992", "#477b83", "#3e6e74",
         "#366166", "#2d5457", "#244648", "#1c393a", "#132c2b", "#66669a",
         "#605e94", "#59568e", "#534e88", "#4d4682", "#463e7c", "#403676",
         "#3a2e70", "#33266a", "#2d1e64", "#724172", "#784674", "#7d4c75",
         "#835177", "#885779", "#8e5c7a", "#93627c", "#99677e", "#9e6d7f",
         "#a47281", "#c9978c", "#cea093", "#d2a99b", "#d7b2a2", "#dbbbaa",
         "#e0c4b1", "#e4ccb9", "#e9d5c0", "#eddec8", "#f2e7cf", "#f6f0d7"),
        strict=True)) + ((3.05, "#f6f0d7"),),
    units="in", stepped=True, floor=0.05,
    ticks=(0.05, *(step / 4 for step in range(1, 13)), 3.05),
)
# Surface lifted index: supplied Celsius bands, with end bands at -20 and 0.
PALETTE_LIFTED_INDEX = Palette(
    stops=tuple(zip(
        (-20, *(step / 5 for step in range(-90, -50, 2)),
         *(step / 5 for step in range(-50, 0))),
        ("#c9a6a8", "#c29b9f", "#bc9195", "#b6868b", "#af7c82", "#a97178",
         "#a2676f", "#9c5c65", "#96525b", "#8f4752", "#893d48", "#b2445a",
         "#b85266", "#be5f71", "#c46d7d", "#ca7a89", "#d18894", "#d795a0",
         "#dda3ac", "#e3b0b7", "#e9bec3", "#da99e7", "#cf89dd", "#c379d2",
         "#b869c8", "#ac59be", "#a148b3", "#9538a9", "#8a289f", "#7e1894",
         "#73088a", "#8b0950", "#940f49", "#9d1542", "#a61c3b", "#af2234",
         "#b8282c", "#c12e25", "#ca351e", "#d33b17", "#dc4110", "#e16f02",
         "#e27b12", "#e38723", "#e49433", "#e5a044", "#e5ac54", "#e6b865",
         "#e7c575", "#e8d186", "#e9dd96", "#a7c8ce", "#9bbbc3", "#8eaeb8",
         "#82a1ad", "#7594a2", "#698796", "#5c7a8b", "#506d80", "#436075",
         "#37536a", "#696969", "#787878", "#878787", "#969696", "#a5a5a5",
         "#c3c3c3", "#d2d2d2", "#e1e1e1", "#f0f0f0", "#ffffff"), strict=True))
    + ((0, "#ffffff"),),
    units="\u00b0C", display="fixed", stepped=True, ceiling=0,
    ticks=(-20, -18, -14, -10, -8, -6, -4, -2, 0),
)
# Updraft RGB table adapted from NOAA-GSL pygraf
# specs.py/default_specs.yml (MIT; see NOTICE). No Matplotlib runtime dependency.
PALETTE_MAX_UPDRAFT = Palette(
    stops=tuple(zip(
        (0.5, 1, 1.5, 2, 2.5, 5, 7.5, 10, 12.5, 15, 17.5, 20, 22.5, 25, 30, 35, 40),
        ("#0000c4", "#0000ff", "#0034ff", "#0070ff", "#00acff", "#02e8f4",
         "#33ffc4", "#63ff94", "#94ff63", "#c4ff33", "#f4f802", "#ffc100",
         "#ff8900", "#ff5200", "#ff1a00", "#c40000", "#800000"), strict=True)),
    units="m/s", display="fixed", stepped=True, floor=0.5,
    ticks=(0.5, 5, 10, 20, 30, 40),
)
# Surface wind and gusts: 1 kt colour bands sampled from the supplied knot table.
PALETTE_SURFACE_WIND = Palette(
    stops=tuple(zip(
        range(9, 61),
        ("#fefefe", "#f0f8ff", "#d7eefe", "#bee4fd", "#a4dafb", "#8bd0fa",
         "#81b7f1", "#7a9be6", "#737fdb", "#6c63d1", "#7e64cf", "#9c72d3",
         "#b980d7", "#d78fda", "#e28fd8", "#db80d1", "#d472ca", "#cd64c3",
         "#c554bb", "#bb44b1", "#b233a8", "#a8229e", "#a21392", "#ab0e77",
         "#b50a5d", "#be0542", "#c80028", "#cd0a2d", "#d21332", "#d61d36",
         "#db263b", "#e03040", "#e53a45", "#ea434a", "#ee4d4e", "#f26a53",
         "#f49058", "#f6b65d", "#f9dd62", "#f6ea60", "#efde59", "#e8d252",
         "#e1c64b", "#daba44", "#d2ae3c", "#cba235", "#c4962e", "#bd8a27",
         "#b67e20", "#ae7218", "#a76611", "#a05a0a"), strict=True))
    + ((61.0, "#a05a0a"),),
    units="kt", stepped=True, floor=10, ticks=(9, *range(10, 61, 5), 61),
)


# --------------------------------------------------------------------------- #
# Derivations
#
# Each takes the decoded ingredient arrays keyed by ``FieldSpec.name``, in GRIB
# units, and returns the displayed field in the palette's units. They are plain
# functions of dicts of arrays so they can be tested on synthetic input.
# --------------------------------------------------------------------------- #


# 1 K bands sampled from the supplied theta-e table; its labelled ticks run 230-360 K.
PALETTE_THETAE = Palette(
    stops=tuple(zip(range(229, 370), (
        "#986d4d", "#966c4c", "#946b4c", "#926a4b", "#90694b", "#8e684a",
        "#8c664a", "#8a6549", "#886448", "#866348", "#846247", "#826147",
        "#806046", "#7e5f46", "#7c5e45", "#7a5d44", "#785b44", "#765a43",
        "#745943", "#725842", "#715742", "#6f5641", "#6d5540", "#6b5440",
        "#69533f", "#67523f", "#65503e", "#634f3d", "#614e3d", "#5f4d3c",
        "#5d4c3c", "#5b4b3b", "#594a3b", "#57493a", "#554839", "#534739",
        "#514538", "#4f4438", "#4d4337", "#4b4237", "#494136", "#4d4334",
        "#504737", "#544a3b", "#574e3e", "#5b5141", "#5e5545", "#615848",
        "#655c4b", "#68604f", "#6c6352", "#6f6755", "#726a59", "#766e5c",
        "#79715f", "#7d7563", "#807966", "#847c69", "#87806d", "#8a8370",
        "#8e8773", "#918a77", "#958e7a", "#98927d", "#9b9581", "#9f9984",
        "#a29c87", "#a6a08a", "#a9a38e", "#aca791", "#b0ab94", "#b3ae98",
        "#b7b29b", "#bab59e", "#bdb9a2", "#c1bca5", "#c4c0a8", "#c8c4ac",
        "#cbc7af", "#cfcbb2", "#d2ceb6", "#d5d2b9", "#d9d5bc", "#dcd9c0",
        "#e0ddc3", "#e3e0c6", "#e6e4ca", "#eae7cd", "#edebd0", "#f1eed4",
        "#f4f2d7", "#e6f5e6", "#d7f0d7", "#c8eac8", "#b9e5b9", "#aadfaa",
        "#9bda9b", "#8cd48c", "#7dcf7d", "#6ec96e", "#5fc45f", "#30ae30",
        "#2ca32c", "#279927", "#238e23", "#1e831e", "#1a791a", "#156e15",
        "#116311", "#0c590c", "#084e08", "#61a3af", "#5896a0", "#508992",
        "#477b83", "#3e6e74", "#366166", "#2d5457", "#244648", "#1c393a",
        "#132c2b", "#66669a", "#605e94", "#59568e", "#534e88", "#4d4682",
        "#463e7c", "#403676", "#3a2e70", "#33266a", "#2d1e64", "#724071",
        "#784573", "#7d4b75", "#835076", "#885678", "#8e5b7a", "#93617c",
        "#99667d", "#9e6c7f", "#a47181",
    ), strict=True)) + ((370.0, "#a47181"),),
    units="K", display="fixed", stepped=True, ticks=(229, *range(230, 370, 10), 370),
)


def _thetae_2m(fields: dict[str, np.ndarray]) -> np.ndarray:
    """Bolton equivalent potential temperature using 2 m T/Td and surface pressure."""
    from metpy.calc import equivalent_potential_temperature
    from metpy.units import units

    pressure, temperature, dewpoint = (np.asarray(fields[key]) for key in ("pres", "tmp2m", "dpt2m"))
    valid = ((pressure > 0) & (temperature > 0) & (dewpoint > 0)
             & np.isfinite(pressure) & np.isfinite(temperature) & np.isfinite(dewpoint))
    with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
        result = equivalent_potential_temperature(
            units.Quantity(np.where(valid, pressure, MISSING), "Pa"),
            units.Quantity(np.where(valid, temperature, MISSING), "K"),
            units.Quantity(np.where(valid, dewpoint, MISSING), "K"),
        ).m_as("K")
    return np.where(np.isfinite(result), result, MISSING)


def _single(name: str, convert: Callable[[np.ndarray], np.ndarray] | None = None):
    """Return a derivation that passes one record through, optionally converted."""

    def derive(fields: dict[str, np.ndarray]) -> np.ndarray:
        values = fields[name]
        return values if convert is None else convert(values)

    return derive


def _precipitation_type_rate(name: str):
    """Total liquid-equivalent rate in cells with the published type flag."""

    def derive(fields: dict[str, np.ndarray]) -> np.ndarray:
        rate = precipitation_rate_to_mm_per_hour(fields["prate"])
        flag = fields[name]
        valid = np.isfinite(rate) & (rate >= 0) & ((flag == 0) | (flag == 1))
        # Type flags select cells, not mass fractions; mixed types can overlap.
        return np.where(valid, np.where(flag == 1, rate, 0.0), MISSING)

    return derive


def _magnitude(u_name: str, v_name: str,
               convert: Callable[[np.ndarray], np.ndarray] | None = None):
    """Return a derivation for the magnitude of a vector pair."""

    def derive(fields: dict[str, np.ndarray]) -> np.ndarray:
        magnitude = np.hypot(fields[u_name], fields[v_name])
        return magnitude if convert is None else convert(magnitude)

    return derive


def _ehi(srh_name: str):
    """Energy-Helicity Index: ``CAPE * SRH / 160000`` (Hart and Korotky 1991)."""

    def derive(fields: dict[str, np.ndarray]) -> np.ndarray:
        return fields["mlcape"] * fields[srh_name] / 160000.0

    return derive


def _lapse_700_500(fields: dict[str, np.ndarray]) -> np.ndarray:
    """700-500 mb lapse rate in degrees C per kilometre.

    Computed from the two temperatures and the geopotential thickness between
    them rather than a standard-atmosphere depth, so it stays correct where the
    layer is unusually thick or thin.
    """
    depth_km = (fields["hgt500"] - fields["hgt700"]) / 1000.0
    with np.errstate(invalid="ignore", divide="ignore"):
        rate = (fields["tmp700"] - fields["tmp500"]) / depth_km
    return np.where(depth_km > 0.1, rate, MISSING)


def _lapse_0_3km(fields: dict[str, np.ndarray]) -> np.ndarray:
    """0-3 km AGL lapse rate, shown only where there is CAPE to lift.

    The layer top is found by interpolating the pressure-level temperatures to
    3 km above the model's own surface height, so terrain is respected instead
    of assuming a sea-level column.

    Masked to MLCAPE > 500 J/kg: a steep lapse rate over a capped or bone-dry
    column is not a severe-weather signal, and SPC's mesoanalysis page applies
    the same screen.
    """
    surface = fields["sfchgt"]
    target = surface + 3000.0
    # Pressure-level stack, ordered from the ground upward.
    heights = np.stack([fields[f"hgt{level}"] for level in _LAPSE_LEVELS])
    temps = np.stack([fields[f"tmp{level}"] for level in _LAPSE_LEVELS])
    top = _interpolate_to_height(heights, temps, target)
    with np.errstate(invalid="ignore"):
        rate = (fields["tmp2m"] - top) / 3.0
    return np.where(fields["mlcape"] > 500.0, rate, MISSING)


#: Pressure levels used to find the 3 km AGL temperature.
#:
#: Chosen to bracket "3 km above ground" everywhere in the CONUS domain, from
#: the Gulf coast (target near 700 mb) to the high Rockies (target near 450 mb),
#: while costing as few downloads as possible: each level adds a temperature and
#: a height record, so the list is the budget for this product.
#:
#: The gaps above 650 mb are wider than the levels below it. Temperature is very
#: nearly linear in height through the free troposphere, so interpolating across
#: a 1.4 km gap costs well under half a degree -- under 0.2 C/km once divided by
#: the 3 km depth, which is finer than the 0.5 C/km bands the scale draws.
_LAPSE_LEVELS = (750, 700, 650, 550, 450)


def _interpolate_to_height(heights: np.ndarray, values: np.ndarray,
                           target: np.ndarray) -> np.ndarray:
    """Linearly interpolate ``values`` to ``target`` height, per grid point.

    ``heights`` and ``values`` are ``(level, row, col)`` with level ascending in
    height. Points where the target falls outside the stack come back MISSING
    rather than being clamped, because a clamped value would look like a real
    lapse rate computed over the wrong depth.
    """
    result = np.full(target.shape, MISSING, dtype=np.float32)
    # Track assignment independently of the missing-value representation.  At
    # present MISSING is NaN, but interpolation must remain correct if a GRIB
    # boundary supplies (or a future writer adopts) a finite sentinel.
    filled = np.zeros(target.shape, dtype=bool)
    for index in range(heights.shape[0] - 1):
        lower_h = heights[index]
        upper_h = heights[index + 1]
        inside = (lower_h <= target) & (target <= upper_h) & (upper_h > lower_h)
        if not inside.any():
            continue
        weight = np.zeros_like(target, dtype=np.float64)
        np.divide(target - lower_h, upper_h - lower_h,
                  out=weight, where=inside)
        candidate = values[index] + (values[index + 1] - values[index]) * weight
        assign = inside & ~filled & np.isfinite(candidate)
        result = np.where(assign, candidate, result)
        filled |= assign
    return result


def _supercell_composite(fields: dict[str, np.ndarray]) -> np.ndarray:
    """Supercell Composite Parameter, fixed-layer form.

    SPC computes SCP from effective-inflow-layer SRH and effective bulk shear.
    Those need a parcel ascent per column, which 2-D output cannot supply, so
    this uses the 0-3 km SRH and 0-6 km shear that HRRR publishes directly. The
    shear term follows SPC in saturating at 20 m/s and vanishing below 10.
    """
    mucape = np.maximum(fields["mucape"], 0.0)
    srh = fields["srh03"]
    shear = np.hypot(fields["ushr06"], fields["vshr06"])
    shear_term = np.clip(shear / 20.0, 0.0, 1.0)
    shear_term = np.where(shear < 10.0, 0.0, shear_term)
    return (mucape / 1000.0) * (srh / 50.0) * shear_term


def _significant_tornado(fields: dict[str, np.ndarray]) -> np.ndarray:
    """Significant Tornado Parameter, fixed-layer form (Thompson et al. 2003).

    Uses surface-based CAPE and CIN, the model's own LCL height, 0-1 km SRH and
    0-6 km shear -- all published records. SPC's operational STP uses effective
    inflow values instead, so the two will differ where the inflow layer is not
    surface based.

    The term clamps are SPC's: LCL saturates at 1000 m and vanishes above 2000,
    shear saturates at 30 m/s and vanishes below 12.5, and CIN saturates above
    -50 J/kg and vanishes below -200.
    """
    sbcape = np.maximum(fields["sbcape"], 0.0)
    lcl = fields["lcl"]
    srh = fields["srh01"]
    shear = np.hypot(fields["ushr06"], fields["vshr06"])
    cin = fields["sbcin"]

    cape_term = sbcape / 1500.0
    lcl_term = np.clip((2000.0 - lcl) / 1000.0, 0.0, 1.0)
    srh_term = srh / 150.0
    shear_term = np.clip(shear / 20.0, 0.0, 1.5)
    shear_term = np.where(shear < 12.5, 0.0, shear_term)
    cin_term = np.clip((200.0 + cin) / 150.0, 0.0, 1.0)
    return cape_term * lcl_term * srh_term * shear_term * cin_term


# --------------------------------------------------------------------------- #
# Contour overlays (height lines drawn into the field image)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ContourSpec:
    """Isopleths drawn over a filled field.

    ``interval`` and ``field`` are in the contour's own display units. Height
    contours are conventionally drawn every 6 dam at 500 mb and above and every
    3 dam below, which is what the per-product intervals below encode.
    """

    name: str
    interval: float
    colour: str = "#101418"
    width: float = 1.2
    convert: Callable[[np.ndarray], np.ndarray] | None = None
    label: str = ""


# --------------------------------------------------------------------------- #
# The catalogue
# --------------------------------------------------------------------------- #

#: Display order of the product groups, matching how a forecaster works down
#: from the synoptic pattern to explicit convective output.
CATEGORIES = (
    "Height and Wind",
    "Surface",
    "Precipitation",
    "Winter Weather",
    "Radar",
    "Instability",
    "Wind Shear",
    "Composite Parameters",
    "Explicit Convective",
)


@dataclass(frozen=True)
class HrrrProduct:
    """One selectable model overlay."""

    key: str
    label: str
    category: str
    fields: tuple[FieldSpec, ...]
    derive: Callable[[dict[str, np.ndarray]], np.ndarray]
    palette: Palette
    description: str = ""
    caveat: str = ""
    contour: ContourSpec | None = None
    #: Records needed only by the contour, kept separate so a product's own
    #: colour field cannot accidentally depend on them.
    contour_fields: tuple[FieldSpec, ...] = ()
    #: Display the product's UGRD/VGRD components as wind barbs in knots.
    wind: bool = False
    #: The shortest name a forecaster would still recognise, for places with no
    #: room for :attr:`label` -- the locator inset beside a sounding is about a
    #: hundred pixels wide. Left empty where the key already *is* the standard
    #: abbreviation (STP, SCP, REFC, the CAPE and CIN variants), and spelled out
    #: where the key is a slug rather than a name: ``srh-0-3km`` is not how
    #: anybody writes 0-3 km SRH.
    chip: str = ""

    @property
    def short_label(self) -> str:
        """The compact name, falling back to the key upper-cased.

        A fallback rather than a required field so a new product is never
        unlabelled: an ugly chip is a smaller failure than a field drawn with
        nothing saying what it is.
        """
        return self.chip or self.key.upper()

    @property
    def all_fields(self) -> tuple[FieldSpec, ...]:
        seen: dict[str, FieldSpec] = {}
        for spec in self.fields + self.contour_fields:
            seen.setdefault(spec.name, spec)
        return tuple(seen.values())

    @property
    def sources(self) -> tuple[str, ...]:
        return tuple(sorted({spec.source for spec in self.all_fields}))


def _height_wind_product(level: int) -> HrrrProduct:
    """Build the height/wind product for one mandatory pressure level.

    Filled with isotachs and overlaid with height contours: the wind speed is
    what varies smoothly enough to read as colour, and the height field is what
    reads as a pattern of lines. Contour interval follows convention -- 6 dam
    aloft, 3 dam in the lower troposphere.
    """
    hgt = _prs(f"hgt{level}", "HGT", f"{level} mb")
    ugrd = _prs(f"ugrd{level}", "UGRD", f"{level} mb")
    vgrd = _prs(f"vgrd{level}", "VGRD", f"{level} mb")
    interval = 6.0 if level <= 500 else 3.0
    return HrrrProduct(
        key=f"hgt-wind-{level}",
        label=f"{level} mb Height, Wind",
        # Named for the wind, because that is what the colours are: the height
        # field is contour lines, and a chip that said "500 mb Hgt" would point
        # at the wrong half of the product.
        chip=f"{level} mb Wind",
        category="Height and Wind",
        fields=(ugrd, vgrd),
        derive=_magnitude(f"ugrd{level}", f"vgrd{level}", ms_to_kt),
        palette=PALETTE_ISOTACH_KT,
        description=(f"{level} mb wind speed, with geopotential height "
                     f"contoured every {interval:.0f} dam."),
        contour=ContourSpec(name=f"hgt{level}", interval=interval,
                            convert=lambda values: values * M_TO_DAM,
                            label="dam"),
        contour_fields=(hgt,),
        wind=True,
    )


def _build_catalogue() -> tuple[HrrrProduct, ...]:
    products: list[HrrrProduct] = []

    # -- Height and Wind ---------------------------------------------------- #
    for level in (200, 300, 500, 700, 850):
        products.append(_height_wind_product(level))

    # -- Surface ------------------------------------------------------------ #
    products.append(HrrrProduct(
        key="tmp-2m", label="2 m AGL Temperature", chip="2 m Temp",
        category="Surface",
        fields=(F_TMP2M,), derive=_single("tmp2m", kelvin_to_fahrenheit),
        palette=PALETTE_TEMPERATURE_F,
        description="Temperature at 2 m above ground."))
    products.append(HrrrProduct(
        key="dpt-2m", label="2 m AGL Dewpoint", chip="2 m Dewpt",
        category="Surface",
        fields=(F_DPT2M,), derive=_single("dpt2m", kelvin_to_fahrenheit),
        palette=PALETTE_DEWPOINT_F,
        description="Dewpoint at 2 m above ground."))
    products.append(HrrrProduct(
        key="thetae-2m", label="2 m AGL Theta-e, 10 m AGL Wind", chip="2 m Theta-e",
        category="Surface", fields=(F_TMP2M, F_DPT2M, F_PRES, F_U10, F_V10),
        derive=_thetae_2m, palette=PALETTE_THETAE, wind=True,
        description="2 m equivalent potential temperature in kelvin, with 10 m wind barbs in knots.",
        caveat="Bolton formulation using 2 m temperature and dewpoint with model surface pressure."))
    products.append(HrrrProduct(
        key="rh-2m", label="2 m AGL Relative Humidity", chip="2 m RH",
        category="Surface",
        fields=(F_RH2M,), derive=_single("rh2m"),
        palette=PALETTE_RELATIVE_HUMIDITY,
        description="Relative humidity at 2 m above ground, in percent."))
    products.append(HrrrProduct(
        key="ltng", label="Lightning Flash Density", chip="Lightning",
        category="Surface",
        fields=(F_LTNG,), derive=_single("ltng"),
        palette=PALETTE_LIGHTNING,
        description=("Hourly maximum predicted flash density per square kilometre "
                     "in a five-minute period. Values of 15 or more share the final colour.")))
    products.append(HrrrProduct(
        key="gust", label="Wind Gust", category="Surface",
        fields=(F_GUST,), derive=_single("gust", ms_to_kt),
        palette=PALETTE_SURFACE_WIND,
        description="Model wind gust potential at this forecast time, from GUST."))
    products.append(HrrrProduct(
        key="wind-10m", label="10 m Wind Speed", chip="10 m Wind", category="Surface",
        fields=(F_U10, F_V10), derive=_magnitude("u10", "v10", ms_to_kt),
        palette=PALETTE_SURFACE_WIND,
        description="Instantaneous wind speed at 10 m above ground, from UGRD and VGRD.",
        caveat="Sustained model wind, distinct from gusts and the previous-hour WIND maximum."))

    # -- Precipitation ------------------------------------------------------ #
    products.append(HrrrProduct(
        key="apcp", label="Total Precipitation", category="Precipitation",
        fields=(F_APCP,), derive=_single("apcp", mm_to_in),
        palette=PALETTE_PRECIPITATION,
        description="Accumulated precipitation since the selected model run, in liquid-equivalent inches.",
        caveat="Includes all precipitation types; this is a run total, not an hourly amount."))
    products.append(HrrrProduct(
        key="prate", label="Precipitation Rate", category="Precipitation",
        fields=(F_PRATE,), derive=_single("prate", precipitation_rate_to_in_per_hour),
        palette=PALETTE_PRECIPITATION_RATE,
        description="Instantaneous liquid-equivalent precipitation rate, in inches per hour.",
        caveat="A rate at the selected time, not the amount accumulated over an hour."))
    products.append(HrrrProduct(
        key="pwat", label="Precipitable Water", category="Precipitation",
        fields=(F_PWAT,), derive=_single("pwat", mm_to_in), palette=PALETTE_PRECIPITABLE_WATER,
        description="Total water vapour in the atmospheric column, expressed as inches of liquid water."))

    # -- Winter Weather ----------------------------------------------------- #
    for key, label, chip, flag, palette in (
        ("prate-frzr", "Freezing Rain", "FrzR rate", F_CFRZR, PALETTE_FREEZING_RAIN_RATE),
        ("prate-sleet", "Sleet", "Sleet rate", F_CICEP, PALETTE_SLEET_RATE),
        ("prate-snow", "Snow", "Snow rate", F_CSNOW, PALETTE_SNOW_RATE),
    ):
        products.append(HrrrProduct(
            key=key, label=f"{label} Precipitation Rate", chip=chip,
            category="Winter Weather", fields=(F_PRATE, flag),
            derive=_precipitation_type_rate(flag.name), palette=palette,
            description=f"Instantaneous liquid-equivalent rate where the model identifies {label.lower()}, in mm/h.",
            caveat="Uses total rate in flagged cells; mixed types can overlap. Not snow depth or ice accretion per hour."))
    products.append(HrrrProduct(
        key="snod", label="Snow Depth", category="Winter Weather",
        fields=(F_SNOD,), derive=_single("snod", m_to_in), palette=PALETTE_SNOW_DEPTH,
        description="Total snow depth on the ground in inches at the selected forecast time, including existing snow."))

    # -- Radar -------------------------------------------------------------- #
    products.append(HrrrProduct(
        key="refc", label="Composite Reflectivity", category="Radar",
        fields=(F_REFC,), derive=_single("refc"),
        palette=PALETTE_REFLECTIVITY,
        description="Simulated maximum reflectivity in the column, in 2.5 dBZ colour bands.",
        caveat=("Model output, not an observation -- compare it with the radar "
                "overlay rather than reading it as one.")))

    # -- Instability -------------------------------------------------------- #
    products.append(HrrrProduct(
        key="mlcape", label="Mixed Layer CAPE", category="Instability",
        fields=(F_MLCAPE,), derive=_single("mlcape"), palette=PALETTE_CAPE,
        description="CAPE for a parcel mixed through the lowest 180 mb."))
    products.append(HrrrProduct(
        key="mlcin", label="Mixed Layer CIN", category="Instability",
        fields=(F_MLCIN,), derive=_single("mlcin"), palette=PALETTE_CIN,
        description="Convective inhibition for the lowest-180 mb mixed parcel."))
    products.append(HrrrProduct(
        key="mucape", label="Most Unstable CAPE", category="Instability",
        fields=(F_MUCAPE,), derive=_single("mucape"), palette=PALETTE_CAPE,
        description=("CAPE for the most unstable parcel in the lowest 255 mb.")))
    products.append(HrrrProduct(
        key="sbcape", label="Surface-Based CAPE", category="Instability",
        fields=(F_SBCAPE,), derive=_single("sbcape"), palette=PALETTE_CAPE,
        description="CAPE for a parcel lifted from the surface."))
    products.append(HrrrProduct(
        key="lftx", label="Surface-Based Lifted Index", chip="Sfc LI", category="Instability",
        # LFTX is a temperature difference: its native kelvins equal Celsius degrees.
        fields=(F_LFTX,), derive=_single("lftx"), palette=PALETTE_LIFTED_INDEX,
        description=("Temperature at 500 mb minus the temperature of a parcel lifted from the surface. "
                     "Negative values indicate instability. Shown in \u00b0C.")))
    products.append(HrrrProduct(
        key="lapse-0-3km", label="Lapse Rate: 0-3 km AGL, CAPE>500",
        chip="0-3 km LR", category="Instability",
        fields=(F_TMP2M, F_SFC_HGT, F_MLCAPE) + tuple(
            _prs(f"tmp{level}", "TMP", f"{level} mb")
            for level in _LAPSE_LEVELS) + tuple(
            _prs(f"hgt{level}", "HGT", f"{level} mb")
            for level in _LAPSE_LEVELS),
        derive=_lapse_0_3km, palette=PALETTE_LAPSE_RATE,
        description=("Temperature fall from 2 m to 3 km above ground, shown "
                     "only where mixed-layer CAPE exceeds 500 J/kg."),
        caveat=("The 3 km temperature is interpolated from the pressure-level "
                "grids above the model's own terrain height.")))
    products.append(HrrrProduct(
        key="lapse-700-500", label="Lapse Rate: 700-500 mb",
        chip="700-500 LR", category="Instability",
        fields=(_prs("tmp700", "TMP", "700 mb"),
                _prs("tmp500", "TMP", "500 mb"),
                _prs("hgt700", "HGT", "700 mb"),
                _prs("hgt500", "HGT", "500 mb")),
        derive=_lapse_700_500, palette=PALETTE_LAPSE_RATE,
        description="Mid-level lapse rate through the 700-500 mb layer."))

    # -- Wind Shear --------------------------------------------------------- #
    products.append(HrrrProduct(
        key="shear-0-1km", label="Bulk Shear: 0-1 km AGL", chip="0-1 km Shr",
        category="Wind Shear", fields=(F_USHR01, F_VSHR01),
        derive=_magnitude("ushr01", "vshr01", ms_to_kt),
        palette=PALETTE_SHEAR_LOW_KT,
        description="Vector wind difference between the surface and 1 km AGL."))
    products.append(HrrrProduct(
        key="shear-0-6km", label="Bulk Shear: 0-6 km AGL", chip="0-6 km Shr",
        category="Wind Shear", fields=(F_USHR06, F_VSHR06),
        derive=_magnitude("ushr06", "vshr06", ms_to_kt),
        palette=PALETTE_SHEAR_DEEP_KT,
        description="Vector wind difference between the surface and 6 km AGL."))
    products.append(HrrrProduct(
        key="srh-0-1km", label="Storm Relative Helicity: 0-1 km AGL",
        chip="0-1 km SRH",
        category="Wind Shear", fields=(F_SRH01,), derive=_single("srh01"),
        palette=PALETTE_SRH_LOW,
        description="Storm-relative helicity in the lowest kilometre."))
    products.append(HrrrProduct(
        key="srh-0-3km", label="Storm Relative Helicity: 0-3 km AGL",
        chip="0-3 km SRH",
        category="Wind Shear", fields=(F_SRH03,), derive=_single("srh03"),
        palette=PALETTE_SRH,
        description="Storm-relative helicity in the lowest three kilometres."))

    products.append(HrrrProduct(
        key="efhl", label="Effective Storm Relative Helicity", category="Wind Shear",
        fields=(F_EFHL,), derive=_single("efhl"), palette=PALETTE_SRH,
        description="Effective-layer storm-relative helicity published directly by the model as EFHL.",
        caveat="The inflow layer varies with the environment; this is distinct from fixed 0-1 km or 0-3 km SRH."))

    # -- Composite Parameters ----------------------------------------------- #
    products.append(HrrrProduct(
        key="ehi-0-1km", label="Energy Helicity Index: 0-1 km AGL",
        chip="0-1 km EHI",
        category="Composite Parameters", fields=(F_MLCAPE, F_SRH01),
        derive=_ehi("srh01"), palette=PALETTE_EHI,
        description="Mixed-layer CAPE times 0-1 km SRH, scaled by 160000."))
    products.append(HrrrProduct(
        key="ehi-0-3km", label="Energy Helicity Index: 0-3 km AGL",
        chip="0-3 km EHI",
        category="Composite Parameters", fields=(F_MLCAPE, F_SRH03),
        derive=_ehi("srh03"), palette=PALETTE_EHI,
        description="Mixed-layer CAPE times 0-3 km SRH, scaled by 160000."))
    products.append(HrrrProduct(
        key="scp", label="Supercell Composite",
        category="Composite Parameters",
        fields=(F_MUCAPE, F_SRH03, F_USHR06, F_VSHR06),
        derive=_supercell_composite, palette=PALETTE_SCP,
        description=("Most-unstable CAPE, 0-3 km SRH and 0-6 km shear combined "
                     "into SPC's supercell composite."),
        caveat=("Fixed-layer form: SPC's operational SCP uses effective inflow "
                "layer SRH and shear, which 2-D model output cannot supply.")))
    products.append(HrrrProduct(
        key="stp", label="Significant Tornado Parameter",
        category="Composite Parameters",
        fields=(F_SBCAPE, F_SBCIN, F_LCL, F_SRH01, F_USHR06, F_VSHR06),
        derive=_significant_tornado, palette=PALETTE_STP,
        description=("Surface-based CAPE and CIN, LCL height, 0-1 km SRH and "
                     "0-6 km shear combined into SPC's significant tornado "
                     "parameter."),
        caveat=("Fixed-layer form: SPC's operational STP uses effective inflow "
                "layer values, so the two differ where the inflow layer is not "
                "surface based.")))

    # -- Explicit Convective ------------------------------------------------ #
    products.append(HrrrProduct(
        key="uh-0-3km", label="Updraft Helicity: 0-3 km AGL (run max)",
        chip="0-3 km UH",
        category="Explicit Convective", fields=(F_UH03,),
        derive=_single("uh03"), palette=PALETTE_UPDRAFT_HELICITY,
        description=("Largest 0-3 km updraft helicity the model produced during "
                     "the hour ending at this forecast time.")))
    products.append(HrrrProduct(
        key="maxuvv", label="Maximum Updraft Velocity (1 h)", category="Explicit Convective",
        fields=(F_MAXUVV,), derive=_single("maxuvv"), palette=PALETTE_MAX_UPDRAFT,
        description="Maximum upward vertical velocity during the hour ending at the selected forecast time.",
        caveat="Native MAXUVV layer: 100-1000 mb above ground. F000 is the zero-length initial interval."))

    return tuple(products)


PRODUCTS: MappingProxyType = MappingProxyType(
    {product.key: product for product in _build_catalogue()})

#: Composite reflectivity: the field that shows at a glance whether the run has
#: convection where the user is looking, which is the usual first question.
DEFAULT_PRODUCT = "refc"


FIELD_MODELS = ("hrrr", "rrfs-a", "nam-3km-conus", *(f"refs-m{i}" for i in range(1, 6)))
# The reduced REFS member stream omits these products' ingredients.
_REFS_UNSUPPORTED = frozenset({
    "hgt-wind-200", "mlcape", "mlcin", "mucape", "lapse-0-3km",
    "ehi-0-1km", "ehi-0-3km", "scp", "stp",
    "efhl", "lftx", "prate", "prate-frzr", "prate-sleet", "prate-snow", "snod",
})
_SHEAR_LEVELS = (925, 850, 750, 650, 550, 450)


def _shear_0_1km(fields: dict[str, np.ndarray]) -> np.ndarray:
    """NAM has no 0-1 km shear record; interpolate winds above its own terrain."""
    surface = fields["sfchgt"]
    heights = np.stack([surface + 10, *(fields[f"hgt{p}"] for p in _SHEAR_LEVELS)])
    top = [
        _interpolate_to_height(
            heights,
            np.stack([fields[f"{component}10"], *(fields[f"{component}{p}"] for p in _SHEAR_LEVELS)]),
            surface + 1000,
        ) - fields[f"{component}10"]
        for component in ("ugrd", "vgrd")
    ]
    return ms_to_kt(np.hypot(*top))


@lru_cache(maxsize=len(FIELD_MODELS))
def available_products(model: str = "hrrr") -> tuple[HrrrProduct, ...]:
    """Reuse the HRRR products with each model's published record addresses."""
    if model not in FIELD_MODELS:
        raise ValueError(f"{model!r} has no map field catalogue")
    if model == "hrrr":
        return tuple(PRODUCTS.values())

    def mapped(spec: FieldSpec) -> FieldSpec:
        return replace(
            spec,
            source=SOURCE_SURFACE if model == "nam-3km-conus" else spec.source,
            level=("entire atmosphere (considered as a single layer)" if spec.variable == "REFC"
                   else "100-1000 mb" if spec.variable == "MAXUVV" else spec.level),
            forecast=("max fcst" if spec.variable == "LTNG" else "* acc fcst"
                      if model == "nam-3km-conus" and spec.variable == "APCP" else spec.forecast),
        )

    products = []
    for product in PRODUCTS.values():
        if model == "nam-3km-conus" and product.key in {"ltng", "efhl"}:
            continue
        if model.startswith("refs-") and product.key in _REFS_UNSUPPORTED:
            continue
        if product.key == "lftx":
            # NAM/RRFS encode this index with a Kelvin offset; HRRR does not.
            product = replace(product, derive=_single("lftx", kelvin_to_celsius))
        if product.key == "maxuvv":
            product = replace(product, caveat="Native MAXUVV layer: 100-1000 mb. Hourly maxima are published from F001.")
        if model == "nam-3km-conus" and product.key == "shear-0-1km":
            product = replace(
                product,
                fields=(F_SFC_HGT, _sfc("ugrd10", "UGRD", "10 m above ground"),
                        _sfc("vgrd10", "VGRD", "10 m above ground")) + tuple(
                    _prs(f"{variable.lower()}{level}", variable, f"{level} mb")
                    for level in _SHEAR_LEVELS for variable in ("HGT", "UGRD", "VGRD")
                ),
                derive=_shear_0_1km,
                caveat="1 km AGL winds are interpolated from pressure levels above the model terrain; the lower wind is at 10 m.",
            )
        products.append(replace(product, fields=tuple(map(mapped, product.fields)),
                                contour_fields=tuple(map(mapped, product.contour_fields))))
    return tuple(products)


def products_by_category(model: str = "hrrr") -> tuple[tuple[str, tuple[HrrrProduct, ...]], ...]:
    """Return ``(category, products)`` in display order, skipping empty groups."""
    grouped = []
    for category in CATEGORIES:
        members = tuple(product for product in available_products(model)
                        if product.category == category)
        if members:
            grouped.append((category, members))
    return tuple(grouped)


def get_product(key: str | None) -> HrrrProduct:
    """Return a product by key, falling back to the default for anything unknown.

    Unknown keys resolve rather than raise because the key can arrive from
    persisted settings written by a different version of the catalogue.
    """
    if key:
        product = PRODUCTS.get(str(key).strip())
        if product is not None:
            return product
    return PRODUCTS[DEFAULT_PRODUCT]

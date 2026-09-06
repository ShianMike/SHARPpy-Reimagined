"""HRRR gridded product catalogue: what to fetch, how to derive it, how to colour it.

No Qt, no network, no file IO. This module is the declarative half of the model
overlay feature: it names the GRIB records each product needs, the arithmetic
that turns those records into the displayed field, and the colour scale the
result is painted with. :mod:`sharpmod.hrrr_field` does the fetching and
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

from dataclasses import dataclass, field as _dataclass_field
from types import MappingProxyType
from typing import Callable

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

    ``forecast`` is a substring the forecast column must contain, or ``""`` for
    the plain instantaneous field. It exists for the run-maximum records, whose
    column reads ``0-1 hour max fcst`` at F01 and ``1-2 hour max fcst`` at F02 --
    a literal would only ever match one hour, so the shared token is matched
    instead.
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
            return self.forecast in forecast
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

    def __post_init__(self) -> None:
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


# NWS reflectivity classes. Stepped, because this is the scale every radar
# product in the country is read against and a blended version of it reads as a
# different intensity.
PALETTE_REFLECTIVITY = Palette(
    stops=(
        (5.0, "#04e9e7"), (10.0, "#019ff4"), (15.0, "#0300f4"),
        (20.0, "#02fd02"), (25.0, "#01c501"), (30.0, "#008e00"),
        (35.0, "#fdf802"), (40.0, "#e5bc00"), (45.0, "#fd9500"),
        (50.0, "#fd0000"), (55.0, "#d40000"), (60.0, "#bc0000"),
        (65.0, "#f800fd"), (70.0, "#9854c6"), (75.0, "#ffffff"),
    ),
    units="dBZ", stepped=True, floor=5.0,
    ticks=(5.0, 15.0, 25.0, 35.0, 45.0, 55.0, 65.0, 75.0),
)

# Surface temperature in 5 F classes. Banded rather than blended: a forecaster
# reads a surface map for where a threshold lies -- freezing, 100 F -- and a
# continuous wash makes every isotherm a judgement about shade. 32 F gets its own
# class boundary because it is the one that changes precipitation type.
PALETTE_TEMPERATURE_F = Palette(
    stops=(
        (-40.0, "#e2d3ee"), (-35.0, "#cbb0e0"), (-30.0, "#b48ed2"),
        (-25.0, "#9d6cc4"), (-20.0, "#864ab6"), (-15.0, "#6e3aa4"),
        (-10.0, "#5a2f92"), (-5.0, "#452580"), (0.0, "#2f1b6e"),
        (5.0, "#26307f"), (10.0, "#1f4fa8"), (15.0, "#2470bd"),
        (20.0, "#2f8fd0"), (25.0, "#4eaddd"), (32.0, "#7fd4e8"),
        (35.0, "#1f8f74"), (40.0, "#2f9e6b"), (45.0, "#41af58"),
        (50.0, "#57c04a"), (55.0, "#8fd04c"), (60.0, "#c8de52"),
        (65.0, "#e6e94e"), (70.0, "#f5d94b"), (75.0, "#f5ba41"),
        (80.0, "#f09a37"), (85.0, "#ea742f"), (90.0, "#e05127"),
        (95.0, "#cc3520"), (100.0, "#b01a1a"), (105.0, "#941420"),
        (110.0, "#7a0f26"), (115.0, "#620c21"), (120.0, "#4a0a1c"),
    ),
    units="\u00b0F", stepped=True,
    ticks=(-40.0, -20.0, 0.0, 20.0, 32.0, 50.0, 70.0, 90.0, 100.0, 110.0,
           120.0),
)

# Dewpoint in 5 F classes on the conventional brown-to-green-to-teal ramp. The
# top reaches 85 F: the Gulf coast and the Corn Belt in July both exceed 80, and
# a scale ending there paints the moistest air the same colour as merely humid
# air.
PALETTE_DEWPOINT_F = Palette(
    stops=(
        (-20.0, "#6b4a2f"), (-10.0, "#84603c"), (0.0, "#9c7a4f"),
        (10.0, "#b59767"), (20.0, "#c9b184"), (25.0, "#d8c6a1"),
        (30.0, "#e4dcc0"), (35.0, "#dbe0b4"), (40.0, "#cbdca8"),
        (45.0, "#bcd9a0"), (50.0, "#94cc84"), (55.0, "#6fbf6a"),
        (60.0, "#3aa05a"), (65.0, "#1f7f52"), (70.0, "#12684f"),
        (75.0, "#0d5252"), (80.0, "#123f5e"), (85.0, "#1b2c6e"),
    ),
    units="\u00b0F", stepped=True,
    ticks=(-20.0, 0.0, 20.0, 30.0, 40.0, 50.0, 60.0, 65.0, 70.0, 75.0, 80.0,
           85.0),
)

# CAPE in SPC's increments. Stepped so the 500/1000/2000/3000 thresholds a
# forecaster actually reasons about are readable off the map.
# Starts at 100 J/kg, not 250. Elevated convection works on a few hundred joules,
# and a 250 floor hid roughly 45% of a summer CONUS domain -- including all of the
# weakly unstable ground where elevated storms actually form.
PALETTE_CAPE = Palette(
    stops=(
        (100.0, "#e8f6e2"), (250.0, "#d8f0d0"), (500.0, "#a8de90"),
        (750.0, "#74c760"), (1000.0, "#f6ef7a"), (1500.0, "#f4d152"),
        (2000.0, "#ef9f3c"), (2500.0, "#e5642c"), (3000.0, "#d02b23"),
        (3500.0, "#b8172a"), (4000.0, "#a3122b"), (5000.0, "#c43b8f"),
        (6000.0, "#e79ad4"),
    ),
    units="J kg\u207b\u00b9", stepped=True, floor=100.0,
    ticks=(100.0, 500.0, 1000.0, 2000.0, 3000.0, 4000.0, 5000.0, 6000.0),
)

# CIN is plotted negative, so its significant end is the *bottom* of the scale
# and the values to suppress are those near zero. The old scale ran -400..-25
# with nothing hidden, which put every point weaker than -25 -- 83% of a summer
# domain, most of it not capped at all -- into the last colour, and clipped the
# strong end where a real cap reaches -600. Now it reaches -800 and hides
# anything weaker than -25 outright.
PALETTE_CIN = Palette(
    stops=(
        (-800.0, "#1a0329"), (-700.0, "#2a0640"), (-600.0, "#3b0a52"),
        (-500.0, "#4b1069"), (-400.0, "#5a1a86"), (-300.0, "#3f47a8"),
        (-250.0, "#3f63b4"), (-200.0, "#3f7fc0"), (-150.0, "#5199ca"),
        (-100.0, "#63aed4"), (-75.0, "#84c2de"), (-50.0, "#a5d6e8"),
        (-25.0, "#d8ecf4"),
    ),
    units="J kg\u207b\u00b9", stepped=True, ceiling=-25.0,
    ticks=(-800.0, -600.0, -400.0, -300.0, -200.0, -100.0, -50.0, -25.0),
)

# Tops at 11 C/km rather than 10: dry-adiabatic is 9.8, and superadiabatic
# near-surface layers over the high desert exceed 10 in the afternoon.
PALETTE_LAPSE_RATE = Palette(
    stops=(
        (5.0, "#e8f0d8"), (5.5, "#d6e7b9"), (6.0, "#c3dd9a"),
        (6.5, "#8dc46a"), (7.0, "#f2e879"), (7.5, "#f0c352"),
        (8.0, "#e8913c"), (8.5, "#d9532a"), (9.0, "#b31f27"),
        (9.5, "#96182b"), (10.0, "#7d1030"), (11.0, "#5c0b30"),
    ),
    units="\u00b0C km\u207b\u00b9", stepped=True, floor=5.0,
    ticks=(5.0, 6.0, 7.0, 7.5, 8.0, 8.5, 9.0, 9.8, 11.0),
)


def _shear_palette(stops, floor) -> Palette:
    """Build a bulk-shear scale. Depth decides the range, so each layer gets one.

    0-1 km and 0-6 km shear differ by roughly a factor of three, and sharing one
    scale left the shallow-layer map 98% blank -- its median was 5 kt against a
    20 kt floor.
    """
    return Palette(stops=stops, units="kt", stepped=True, floor=floor)


#: 0-1 km bulk shear. 15 kt is the conventional "worth noticing" mark and 20 kt
#: the significant-tornado one, so the classes are 5 kt wide through that range.
PALETTE_SHEAR_LOW_KT = _shear_palette(
    (
        (5.0, "#eef3f8"), (10.0, "#cfe0ef"), (15.0, "#a5c6e2"),
        (20.0, "#79a9d4"), (25.0, "#5bbf8a"), (30.0, "#8ed060"),
        (35.0, "#e0da55"), (40.0, "#e8ab3d"), (45.0, "#dd6c2c"),
        (50.0, "#b52026"), (60.0, "#8d1150"),
    ),
    5.0,
)

#: 0-6 km bulk shear. 35-40 kt is the supercell threshold and 50 kt the strongly
#: organised one; the top reaches 100 kt because a strong jet-coupled event does.
PALETTE_SHEAR_DEEP_KT = _shear_palette(
    (
        (10.0, "#eef3f8"), (15.0, "#dfe8f2"), (20.0, "#b6cde6"),
        (25.0, "#84b0d8"), (30.0, "#5b93c8"), (35.0, "#4f9e6a"),
        (40.0, "#78bd53"), (45.0, "#b6d150"), (50.0, "#e3d64f"),
        (55.0, "#e8a23a"), (60.0, "#dc5f2a"), (70.0, "#b52026"),
        (85.0, "#8d1150"), (100.0, "#5c0b45"),
    ),
    10.0,
)

#: 0-1 km storm-relative helicity. Runs several times smaller than the 0-3 km
#: layer -- a summer CONUS median of 12 against 45 -- so a shared 50 floor hid
#: 90% of it. 100 m2/s2 is the conventional significant mark for this layer.
PALETTE_SRH_LOW = Palette(
    stops=(
        (25.0, "#e4ecf4"), (50.0, "#bcd6ea"), (75.0, "#8ab6dc"),
        (100.0, "#5f97cc"), (150.0, "#f0e36a"), (200.0, "#e9b944"),
        (250.0, "#dd7d31"), (300.0, "#c32f27"), (400.0, "#8f1340"),
        (500.0, "#5c0b45"),
    ),
    units="m\u00b2 s\u207b\u00b2", stepped=True, floor=25.0,
    ticks=(25.0, 50.0, 100.0, 150.0, 200.0, 250.0, 300.0, 400.0, 500.0),
)

#: 0-3 km storm-relative helicity. 150 is marginal, 250 supercell, 400+ strong.
PALETTE_SRH = Palette(
    stops=(
        (50.0, "#e4ecf4"), (100.0, "#bcd6ea"), (150.0, "#8ab6dc"),
        (200.0, "#5f97cc"), (250.0, "#f0e36a"), (300.0, "#e9b944"),
        (400.0, "#dd7d31"), (500.0, "#c32f27"), (700.0, "#8f1340"),
        (900.0, "#5c0b45"),
    ),
    units="m\u00b2 s\u207b\u00b2", stepped=True, floor=50.0,
    ticks=(50.0, 100.0, 200.0, 300.0, 400.0, 500.0, 700.0, 900.0),
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

PALETTE_SCP = Palette(
    stops=(
        (1.0, "#e6eef6"), (2.0, "#bcd8ec"), (4.0, "#84b6da"),
        (6.0, "#f0e36a"), (8.0, "#e9b944"), (12.0, "#dd7d31"),
        (16.0, "#c32f27"), (24.0, "#8f1340"), (32.0, "#5c0b45"),
        (48.0, "#33062b"), (64.0, "#1a0316"),
    ),
    units="", stepped=True, floor=1.0,
    ticks=(1.0, 2.0, 4.0, 8.0, 12.0, 16.0, 24.0, 32.0, 48.0, 64.0),
)

PALETTE_STP = Palette(
    stops=(
        (0.5, "#e6eef6"), (1.0, "#bcd8ec"), (1.5, "#84b6da"),
        (2.0, "#f0e36a"), (3.0, "#e9b944"), (4.0, "#dd7d31"),
        (6.0, "#c32f27"), (8.0, "#8f1340"), (10.0, "#5c0b45"),
    ),
    units="", stepped=True, floor=0.5,
    ticks=(0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0),
)

#: Scaled for the 0-3 km layer, not the classic 2-5 km one. Shallow-layer
#: updraft helicity runs several times smaller -- an observed run maximum of
#: 80 m2/s2 in the 0-3 km layer accompanied 130 in the 2-5 km layer over the same
#: hour -- so a scale topping out in the hundreds would leave every real
#: signature in the first colour.
#:
#: The floor is 5, not 15. A rotating updraft is a handful of grid points, and at
#: 15 the first classes that separate "a shower turning" from "a mesocyclone"
#: were never drawn: on a quiet summer hour the entire field fell beneath the
#: floor and the map was blank rather than merely empty.
PALETTE_UPDRAFT_HELICITY = Palette(
    stops=(
        (5.0, "#eef3f8"), (10.0, "#e8eef4"), (15.0, "#c2d8ea"),
        (25.0, "#8fbadc"), (40.0, "#5f97cc"), (60.0, "#f2e36a"),
        (80.0, "#e9b944"), (110.0, "#dd7d31"), (150.0, "#c32f27"),
        (200.0, "#8f1340"),
    ),
    units="m\u00b2 s\u207b\u00b2", stepped=True, floor=5.0,
    ticks=(5.0, 15.0, 25.0, 40.0, 60.0, 80.0, 110.0, 150.0, 200.0),
)


#: Isotach class edges per pressure level, in knots.
#:
#: One scale for every level was the original choice, so a colour meant one wind
#: speed everywhere. Measured against a real run that cost more than it bought:
#: on a 20-180 kt scale the 850 mb map left 90% of the domain beneath the floor
#: and used 4 of 11 classes, because low-level winds simply do not reach jet
#: speeds. Each level now gets the range its own winds occupy, and the level is
#: on the colour bar, which is where the reader looks for it.
_ISOTACH_EDGES = {
    850: (10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 50.0, 60.0, 70.0, 80.0),
    700: (10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0, 60.0, 70.0, 85.0, 100.0),
    500: (20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0, 120.0, 140.0),
    300: (30.0, 45.0, 60.0, 75.0, 90.0, 105.0, 120.0, 135.0, 150.0, 170.0,
          190.0),
    200: (30.0, 45.0, 60.0, 75.0, 90.0, 105.0, 120.0, 135.0, 150.0, 170.0,
          200.0),
}

#: Eleven-class isotach ramp, pale through green and yellow to deep red. Shared
#: across levels so a stronger colour still means a stronger wind *for that
#: level*, which is the comparison a reader makes within one map.
_ISOTACH_COLOURS = (
    "#eef3f8", "#cfe0ef", "#a5c6e2", "#79a9d4", "#5bbf8a", "#8ed060",
    "#e0da55", "#e8ab3d", "#dd6c2c", "#bc2426", "#8d1150",
)


def _isotach_palette(level: int) -> Palette:
    """Return the isotach scale for one mandatory pressure level."""
    edges = _ISOTACH_EDGES[level]
    return Palette(
        stops=tuple(zip(edges, _ISOTACH_COLOURS)),
        units="kt", stepped=True, floor=edges[0],
        ticks=edges,
    )


# --------------------------------------------------------------------------- #
# Derivations
#
# Each takes the decoded ingredient arrays keyed by ``FieldSpec.name``, in GRIB
# units, and returns the displayed field in the palette's units. They are plain
# functions of dicts of arrays so they can be tested on synthetic input.
# --------------------------------------------------------------------------- #


def _single(name: str, convert: Callable[[np.ndarray], np.ndarray] | None = None):
    """Return a derivation that passes one record through, optionally converted."""

    def derive(fields: dict[str, np.ndarray]) -> np.ndarray:
        values = fields[name]
        return values if convert is None else convert(values)

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
        result = np.where(inside & ~np.isfinite(result), candidate, result)
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
    shear_term = np.clip(shear / 20.0, 0.0, 1.5)
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
        palette=_isotach_palette(level),
        description=(f"{level} mb wind speed, with geopotential height "
                     f"contoured every {interval:.0f} dam."),
        contour=ContourSpec(name=f"hgt{level}", interval=interval,
                            convert=lambda values: values * M_TO_DAM,
                            label="dam"),
        contour_fields=(hgt,),
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

    # -- Radar -------------------------------------------------------------- #
    products.append(HrrrProduct(
        key="refc", label="Composite Reflectivity", category="Radar",
        fields=(F_REFC,), derive=_single("refc"),
        palette=PALETTE_REFLECTIVITY,
        description=("Simulated maximum reflectivity in the column, on the NWS "
                     "reflectivity scale."),
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

    return tuple(products)


PRODUCTS: MappingProxyType = MappingProxyType(
    {product.key: product for product in _build_catalogue()})

#: Composite reflectivity: the field that shows at a glance whether the run has
#: convection where the user is looking, which is the usual first question.
DEFAULT_PRODUCT = "refc"


def available_products() -> tuple[HrrrProduct, ...]:
    """Return every product in catalogue order."""
    return tuple(PRODUCTS.values())


def products_by_category() -> tuple[tuple[str, tuple[HrrrProduct, ...]], ...]:
    """Return ``(category, products)`` in display order, skipping empty groups."""
    grouped = []
    for category in CATEGORIES:
        members = tuple(product for product in PRODUCTS.values()
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

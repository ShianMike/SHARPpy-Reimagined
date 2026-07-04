"""Shared helpers for the UWyo_Decoder test suite (tasks 11.2-11.5).

The University of Wyoming (UWyo) upper-air service returns the classic
fixed-width ``<PRE>`` HTML table. :func:`render_uwyo_text` synthesises a
byte-for-byte compatible page from arbitrary per-level arrays so the decoder can
be exercised **without any network access**, and :func:`uwyo_soundings` is a
Hypothesis strategy producing physically plausible level arrays to feed it.

The column layout mirrors the real service (and the decoder's fixed 7-char
column parser, columns ``0,1,2,3,6,7`` -> ``pres/hght/tmpc/dwpc/wdir/wspd``)::

       PRES   HGHT   TEMP   DWPT   RELH   MIXR   DRCT   SKNT   THTA ...
        hPa     m      C      C      %    g/kg    deg   knot     K  ...

Every synthesised level is rendered with ``"%7.1f"`` so each value occupies
exactly one 7-character column, matching what :meth:`UWyo_Decoder.decode_text`
slices out.
"""

from __future__ import annotations

import numpy as np
from hypothesis import strategies as st

__all__ = [
    "render_uwyo_text",
    "uwyo_soundings",
    "UNAVAILABLE_PAGE",
    "FIELD_RANGES",
    "CORE_FIELDS",
]

CORE_FIELDS = ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd")

#: Valid physical ranges every decoded per-level field must fall inside
#: (Requirement 7.2). A small rounding slack is applied by callers where the
#: ``"%7.1f"`` render quantises values to 0.1.
FIELD_RANGES = {
    "pres": (0.0, 1100.0),      # hPa, strictly positive
    "hght": (-500.0, 45000.0),  # m MSL
    "tmpc": (-150.0, 60.0),     # deg C
    "dwpc": (-160.0, 60.0),     # deg C
    "wdir": (0.0, 360.0),       # deg
    "wspd": (0.0, 1000.0),      # kt
}

#: A representative UWyo "no data for this station/time" response body. The
#: decoder's :meth:`_looks_unavailable` heuristic must classify this as a
#: :class:`StationTimeUnavailableError` (Requirement 7.5).
UNAVAILABLE_PAGE = (
    "<HTML>\n<TITLE>University of Wyoming - Radiosonde Data</TITLE>\n"
    "<BODY>\n<H2>Sorry, the server can't get the data you requested.</H2>\n"
    "No data available for this station and time.\n"
    "</BODY></HTML>\n"
)


def _fmt(value: float) -> str:
    """Format one value into a fixed 7-character UWyo column."""
    text = "%7.1f" % float(value)
    # Guard against any value that would overflow the fixed-width column and
    # silently shift every downstream column.
    if len(text) != 7:
        raise ValueError(
            f"value {value!r} does not fit a 7-char UWyo column (got {text!r})")
    return text


def render_uwyo_text(
    pres, hght, tmpc, dwpc, wdir, wspd,
    *,
    loc: str = "OAX",
    valid: str = "00Z 16 Jun 2014",
    lat: float = 41.32,
) -> str:
    """Render per-level arrays into a synthetic UWyo ``<PRE>`` HTML page.

    The output is shaped exactly like a live UWyo ``TYPE=TEXT:LIST`` response:
    an ``<H2>`` title line, a data ``<PRE>`` block (dashes / column names /
    units / dashes header, then one fixed-width row per level), a
    ``</PRE><H3>`` terminator, and a trailing station-info ``<PRE>`` block
    carrying the ``Station latitude`` line.

    Parameters
    ----------
    pres, hght, tmpc, dwpc, wdir, wspd:
        Equal-length per-level arrays.
    loc, valid, lat:
        Metadata woven into the ``<H2>`` title and the station-info block.

    Returns
    -------
    str
        The full HTML page as text.
    """
    arrs = [np.asarray(a, dtype=float) for a in
            (pres, hght, tmpc, dwpc, wdir, wspd)]
    n = arrs[0].size
    if any(a.size != n for a in arrs):
        raise ValueError("all level arrays must be the same length")

    header = (
        "<HTML>\n"
        "<TITLE>University of Wyoming - Radiosonde Data</TITLE>\n"
        "<BODY BGCOLOR=\"white\">\n"
        f"<H2>{loc:<6.6s} Observations at {valid}</H2>\n"
        "<PRE>\n"
        "-----------------------------------------------------------------------------\n"
        "   PRES   HGHT   TEMP   DWPT   RELH   MIXR   DRCT   SKNT   THTA   THTE   THTV\n"
        "    hPa     m      C      C      %    g/kg    deg   knot     K      K      K \n"
        "-----------------------------------------------------------------------------"
    )

    rows = []
    for i in range(n):
        # relh / mixr (columns 4, 5) are placeholders: the decoder never reads
        # them, but they must occupy their 7-char slots to keep wdir/wspd
        # (columns 6, 7) aligned at character offsets 42 and 49.
        row = "".join(_fmt(v) for v in (
            arrs[0][i], arrs[1][i], arrs[2][i], arrs[3][i],
            50.0, 5.0, arrs[4][i], arrs[5][i]))
        rows.append(row)

    footer = (
        "</PRE><H3>Station information and sounding indices</H3>\n"
        "<PRE>\n"
        f"                         Station identifier: {loc}\n"
        f"                         Station latitude: {lat}\n"
        "                         Station elevation: 350.0\n"
        "</PRE>\n"
        "</BODY></HTML>\n"
    )

    return header + "\n" + "\n".join(rows) + "\n" + footer


@st.composite
def uwyo_soundings(draw, *, min_levels: int = 5, max_levels: int = 30) -> dict:
    """Hypothesis strategy: physically plausible UWyo per-level arrays.

    Produces a ``{field: ndarray}`` mapping whose values sit comfortably inside
    :data:`FIELD_RANGES`: pressure strictly decreasing from a realistic surface
    value, height strictly increasing, temperature decreasing with height,
    dewpoint never warmer than temperature, wind direction in ``[0, 360)`` and
    wind speed non-negative.
    """
    n = draw(st.integers(min_value=min_levels, max_value=max_levels))

    top_agl = draw(st.floats(min_value=6000.0, max_value=14000.0))
    sfc_elev = draw(st.floats(min_value=0.0, max_value=1500.0))
    base = np.linspace(0.0, top_agl, n)
    hght = sfc_elev + base

    p_sfc = draw(st.floats(min_value=950.0, max_value=1050.0))
    scale_h = draw(st.floats(min_value=7000.0, max_value=8500.0))
    pres = p_sfc * np.exp(-base / scale_h)

    t_sfc = draw(st.floats(min_value=-5.0, max_value=35.0))
    lapse = draw(st.floats(min_value=4.0, max_value=8.5))  # deg C / km
    tmpc = t_sfc - lapse * (base / 1000.0)
    tmpc = np.clip(tmpc, -120.0, 55.0)

    dep_sfc = draw(st.floats(min_value=0.0, max_value=20.0))
    dep_slope = draw(st.floats(min_value=0.0, max_value=3.0))
    depression = np.clip(dep_sfc + dep_slope * (base / 1000.0), 0.0, None)
    dwpc = np.clip(np.minimum(tmpc - depression, tmpc), -140.0, 55.0)

    wdir = np.array(
        [draw(st.floats(min_value=0.0, max_value=359.9)) for _ in range(n)])
    wspd = np.array(
        [draw(st.floats(min_value=0.0, max_value=150.0)) for _ in range(n)])

    return {
        "pres": pres, "hght": hght, "tmpc": tmpc,
        "dwpc": dwpc, "wdir": wdir, "wspd": wspd,
    }

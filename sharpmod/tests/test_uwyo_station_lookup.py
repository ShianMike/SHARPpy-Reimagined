"""Unit tests for UWyo station lookup and typed errors (task 11.4).

Covers Requirement 7 station-resolution and error behaviour of
:class:`sharpmod.io.uwyo_decoder.UWyo_Decoder`:

* **7.3** ``resolve_station`` resolves a query to exactly one station's
  metadata (id, name, lat, lon, elevation);
* **7.4** a query matching zero *or* more than one station raises
  :class:`StationLookupError` and returns no station id;
* **7.7** an unparseable response raises :class:`SoundingParseError`.

All tests run fully offline -- ``resolve_station`` and ``decode_text`` never
touch the network.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from sharpmod.io.uwyo_decoder import (
    SoundingParseError,
    StationLookupError,
    StationMeta,
    UWYO_STATIONS,
    UWyo_Decoder,
)
from sharpmod.tests.uwyo_fixtures import render_uwyo_text


# --------------------------------------------------------------------------- #
# 7.3 single-station metadata resolution
# --------------------------------------------------------------------------- #
def test_resolve_station_by_exact_id():
    """Req 7.3: an exact station-id query resolves to that station's metadata."""
    decoder = UWyo_Decoder()
    meta = decoder.resolve_station("72558")

    assert isinstance(meta, StationMeta)
    assert meta.id == "72558"
    name, lat, lon, elev = UWYO_STATIONS["72558"]
    assert meta.name == name
    assert meta.lat == pytest.approx(lat)
    assert meta.lon == pytest.approx(lon)
    assert meta.elev_m == pytest.approx(elev)


def test_resolve_station_by_unique_name_substring():
    """Req 7.3: a unique name substring resolves to a single station."""
    decoder = UWyo_Decoder()
    # "Norman" appears in exactly one catalogue entry (OUN Norman, OK).
    meta = decoder.resolve_station("norman")
    assert meta.id == "72357"
    assert "Norman" in meta.name


def test_resolve_station_by_unique_call_sign():
    """Req 7.3: a unique call-sign substring resolves to a single station."""
    decoder = UWyo_Decoder()
    meta = decoder.resolve_station("OUN")
    assert meta.id == "72357"


def test_resolve_station_all_catalog_ids_resolve_uniquely():
    """Req 7.3: every embedded station id resolves to itself with valid coords."""
    decoder = UWyo_Decoder()
    for sid in UWYO_STATIONS:
        meta = decoder.resolve_station(sid)
        assert meta.id == sid
        assert -90.0 <= meta.lat <= 90.0
        assert -180.0 <= meta.lon <= 180.0
        assert math.isfinite(meta.elev_m)


# --------------------------------------------------------------------------- #
# 7.4 ambiguous / no-match -> StationLookupError (no id returned)
# --------------------------------------------------------------------------- #
def test_resolve_station_no_match_raises():
    """Req 7.4: a query matching no station raises StationLookupError."""
    decoder = UWyo_Decoder()
    with pytest.raises(StationLookupError):
        decoder.resolve_station("NoSuchPlace12345")


def test_resolve_station_ambiguous_raises():
    """Req 7.4: a query matching multiple stations raises StationLookupError."""
    decoder = UWyo_Decoder()
    # "NE" is a substring of both "OAX Omaha/Valley, NE" and
    # "LBF North Platte, NE", so the query is ambiguous.
    with pytest.raises(StationLookupError):
        decoder.resolve_station("NE")


def test_resolve_station_ambiguous_error_names_candidates():
    """Req 7.4: the ambiguous-lookup error identifies the competing stations."""
    decoder = UWyo_Decoder()
    with pytest.raises(StationLookupError) as excinfo:
        decoder.resolve_station("NE")
    # No single station id is "returned"; the message enumerates the matches.
    message = str(excinfo.value)
    assert "72558" in message and "72562" in message


def test_resolve_station_empty_or_none_raises():
    """Req 7.4: an empty or ``None`` query raises StationLookupError."""
    decoder = UWyo_Decoder()
    for bad in (None, "", "   "):
        with pytest.raises(StationLookupError):
            decoder.resolve_station(bad)


def test_resolve_station_custom_catalog_ambiguity():
    """Req 7.4: ambiguity is detected against a custom station catalogue too."""
    catalog = {
        "00001": ("ALPHA Springfield", 40.0, -90.0, 200.0),
        "00002": ("BETA Springfield", 41.0, -91.0, 250.0),
    }
    decoder = UWyo_Decoder(station_catalog=catalog)
    with pytest.raises(StationLookupError):
        decoder.resolve_station("Springfield")
    # But an unambiguous token still resolves.
    assert decoder.resolve_station("ALPHA").id == "00001"


# --------------------------------------------------------------------------- #
# 7.7 unparseable response -> SoundingParseError
# --------------------------------------------------------------------------- #
def test_decode_empty_response_raises_parse_error():
    """Req 7.7: an empty response is a parse failure."""
    decoder = UWyo_Decoder()
    for empty in ("", "   \n  \n"):
        with pytest.raises(SoundingParseError):
            decoder.decode_text(empty)


def test_decode_html_without_data_block_raises_parse_error():
    """Req 7.7: HTML with no sounding data block is a parse failure."""
    decoder = UWyo_Decoder()
    html = (
        "<HTML>\n<TITLE>UWyo</TITLE>\n<BODY>\n"
        "<H2>Some unrelated page</H2>\n"
        "<p>There is no PRE data block here.</p>\n"
        "</BODY></HTML>\n"
    )
    with pytest.raises(SoundingParseError):
        decoder.decode_text(html)


def test_decode_garbage_rows_raise_parse_error():
    """Req 7.7: a data block whose rows are non-numeric is a parse failure."""
    decoder = UWyo_Decoder()
    # A well-formed <PRE> frame whose "data" rows cannot be parsed as floats.
    text = (
        "<H2>OAX Observations at 00Z 16 Jun 2014</H2>\n"
        "<PRE>\n"
        "----\n"
        "PRES HGHT\n"
        "hPa m\n"
        "----\n"
        "this-is-not-a-number   and-neither-is-this\n"
        "</PRE><H3>indices</H3>\n"
    )
    with pytest.raises(SoundingParseError):
        decoder.decode_text(text)


def test_from_intermediate_missing_fields_raises_parse_error():
    """Req 7.7: an intermediate missing core fields is a parse failure."""
    decoder = UWyo_Decoder()
    incomplete = {
        "pres": np.array([1000.0, 850.0]),
        "hght": np.array([100.0, 1500.0]),
        # tmpc/dwpc/wdir/wspd deliberately absent
    }
    with pytest.raises(SoundingParseError):
        decoder.from_intermediate(incomplete)


def test_from_intermediate_mismatched_lengths_raises_parse_error():
    """Req 7.7: mismatched intermediate array lengths are a parse failure."""
    decoder = UWyo_Decoder()
    bad = {
        "pres": np.array([1000.0, 850.0, 700.0]),
        "hght": np.array([100.0, 1500.0]),
        "tmpc": np.array([20.0, 15.0]),
        "dwpc": np.array([10.0, 5.0]),
        "wdir": np.array([180.0, 200.0]),
        "wspd": np.array([10.0, 20.0]),
    }
    with pytest.raises(SoundingParseError):
        decoder.from_intermediate(bad)


def test_wellformed_page_decodes_without_error():
    """Sanity: a well-formed synthetic page is *not* treated as a parse error."""
    decoder = UWyo_Decoder()
    text = render_uwyo_text(
        pres=[1000.0, 850.0, 700.0, 500.0, 300.0],
        hght=[110.0, 1480.0, 3050.0, 5760.0, 9500.0],
        tmpc=[24.0, 15.0, 4.0, -12.0, -40.0],
        dwpc=[20.0, 10.0, -6.0, -25.0, -55.0],
        wdir=[160.0, 210.0, 240.0, 270.0, 290.0],
        wspd=[10.0, 35.0, 48.0, 70.0, 95.0],
    )
    intermediate = decoder.decode_text(text)
    assert intermediate["pres"].size == 5


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))

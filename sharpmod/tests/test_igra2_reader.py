"""The IGRA v2 fixed-width reader, its cache, and its archive selection.

Every fixture here is built by a formatter that is first proven to reproduce
records captured verbatim from the live archive, so a column drifting by one
character fails immediately rather than silently shifting a field.

No test in this module touches the network: HTTP is supplied as a callable.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json
import math
import zipfile

import pytest

from sharpmod.io import igra2


# Captured verbatim from data-y2d/USM00072357-data-beg2026.txt.zip.
REAL_HEADER = (
    "#USM00072357 2026 01 01 00 2302  217 ncdc-nws ncdc-gts  351808  -974378"
)
REAL_LEVEL = "21     0  97788B  345   176B  260   197   235    15"

# Captured verbatim from doc/igra2-station-list.txt.
REAL_STATION_LINES = "\n".join([
    "ACM00078861  17.1170  -61.7830   10.0    "
    "COOLIDGE FIELD (UA)            1947 1993  13896",
    "AEM00041217  24.4333   54.6500   16.0    "
    "ABU DHABI INTERNATIONAL AIRPOR 1983 2026  41271",
    "AEXUAE05467  25.2500   55.3700    4.0    "
    "SHARJAH                        1935 1942   2477",
    "AFM00040911  36.7000   67.2000 -999.9    "
    "MAZAR-I-SHARIF                 2010 2014   2179",
])


# --------------------------------------------------------------------------- #
# Record formatters, proven against the real bytes
# --------------------------------------------------------------------------- #
def build_header(station="USM00072357", year=2026, month=1, day=1, hour=0,
                 reltime=2302, numlev=217, p_src="ncdc-nws",
                 np_src="ncdc-gts", raw_lat=351808, raw_lon=-974378):
    """Format one header record per ``igra2-data-format.txt``."""
    return (
        "#" + station.ljust(11)
        + " " + f"{year:04d}"
        + " " + f"{month:02d}"
        + " " + f"{day:02d}"
        + " " + f"{hour:02d}"
        + " " + f"{reltime:04d}"
        + " " + f"{numlev:>4d}"
        + " " + p_src.ljust(8)
        + " " + np_src.ljust(8)
        + " " + f"{raw_lat:>7d}"
        + " " + f"{raw_lon:>8d}"
    )


def build_level(lvltyp1=2, lvltyp2=1, etime=0, press=97788, pflag="B",
                gph=345, zflag=" ", temp=176, tflag="B", rh=260, dpdp=197,
                wdir=235, wspd=15):
    """Format one data record per ``igra2-data-format.txt``."""
    return (
        f"{lvltyp1:d}{lvltyp2:d}"
        + " " + f"{etime:>5d}"
        + " " + f"{press:>6d}"
        + pflag
        + f"{gph:>5d}"
        + zflag
        + f"{temp:>5d}"
        + tflag
        + f"{rh:>5d}"
        + " " + f"{dpdp:>5d}"
        + " " + f"{wdir:>5d}"
        + " " + f"{wspd:>5d}"
    )


def test_the_header_formatter_reproduces_a_captured_record():
    """Without this, every header fixture below could be silently misaligned."""
    assert build_header() == REAL_HEADER


def test_the_level_formatter_reproduces_a_captured_record():
    assert build_level() == REAL_LEVEL


def build_station_line(station_id="USM00072357", lat=35.1808, lon=-97.4378,
                       elev=344.9, state="OK",
                       name="NORMAN/MAX WESTHEIMER A; OK.",
                       first_year=1974, last_year=2026, n_obs=28213):
    """Format one station-list row per ``igra2-list-format.txt``."""
    return (
        station_id.ljust(11)
        + " " + f"{lat:>8.4f}"
        + " " + f"{lon:>9.4f}"
        + " " + f"{elev:>6.1f}"
        + " " + state.ljust(2)
        + " " + name.ljust(30)[:30]
        + " " + f"{first_year:>4d}"
        + " " + f"{last_year:>4d}"
        + " " + f"{n_obs:>6d}"
    )


def test_the_station_line_formatter_reproduces_a_captured_record():
    """Hand-aligning this row put FSTYEAR/LSTYEAR two columns off."""
    assert build_station_line(
        station_id="ACM00078861", lat=17.1170, lon=-61.7830, elev=10.0,
        state="", name="COOLIDGE FIELD (UA)", first_year=1947,
        last_year=1993, n_obs=13896,
    ) == REAL_STATION_LINES.splitlines()[0]


# --------------------------------------------------------------------------- #
# Station list
# --------------------------------------------------------------------------- #
def test_the_station_list_columns_are_read_from_real_lines():
    stations = igra2.parse_station_list(REAL_STATION_LINES)

    assert len(stations) == 4
    first = stations[0]
    assert first.id == "ACM00078861"
    assert first.name == "COOLIDGE FIELD (UA)"
    assert first.lat == pytest.approx(17.1170)
    assert first.lon == pytest.approx(-61.7830)
    assert first.elev_m == pytest.approx(10.0)
    assert first.first_year == 1947
    assert first.last_year == 1993
    assert first.n_obs == 13896


def test_a_truncated_name_column_does_not_bleed_into_the_years():
    """The NAME field is 30 characters and upstream truncates into it."""
    abu_dhabi = igra2.parse_station_list(REAL_STATION_LINES)[1]

    assert abu_dhabi.name == "ABU DHABI INTERNATIONAL AIRPOR"
    assert (abu_dhabi.first_year, abu_dhabi.last_year) == (1983, 2026)


def test_a_missing_elevation_keeps_its_published_sentinel():
    mazar = igra2.parse_station_list(REAL_STATION_LINES)[3]

    assert mazar.elev_m == pytest.approx(igra2.MISSING_ELEVATION)


def test_unparseable_and_short_lines_are_skipped_not_fatal():
    text = REAL_STATION_LINES + "\n\nshort\n" + " " * 50
    stations = igra2.parse_station_list(text)

    assert len(stations) == 4


def test_an_empty_station_list_is_an_error():
    with pytest.raises(igra2.IGRASoundingParseError):
        igra2.parse_station_list("")


def test_a_station_list_with_no_records_is_an_error():
    with pytest.raises(igra2.IGRASoundingParseError):
        igra2.parse_station_list("short\nlines\nonly\n")


# --------------------------------------------------------------------------- #
# Station identity, including the WMO bridge
# --------------------------------------------------------------------------- #
def _station(station_id, **kwargs):
    defaults = dict(
        name="TEST", lat=35.0, lon=-97.0, elev_m=300.0, state="OK",
        first_year=1974, last_year=2026, n_obs=100,
    )
    defaults.update(kwargs)
    return igra2.IGRAStation(id=station_id, **defaults)


def test_a_wmo_network_id_exposes_the_bare_wmo_number():
    """This is what lets the existing station map address IGRA."""
    station = _station("USM00072357")

    assert station.network == "M"
    assert station.country == "US"
    assert station.wmo_id == "72357"
    assert station.icao_id is None


def test_an_icao_network_id_exposes_its_callsign():
    station = _station("CAI00KOUN")

    assert station.network == "I"
    assert station.icao_id == "KOUN"
    assert station.wmo_id is None


def test_a_specially_constructed_id_encodes_neither_identifier():
    station = _station("AEXUAE05467")

    assert station.network == "X"
    assert station.wmo_id is None
    assert station.icao_id is None


def test_a_mobile_station_is_recognised_from_its_sentinels():
    assert _station("XXV00000001", lat=igra2.MOBILE_LAT,
                    lon=igra2.MOBILE_LON).is_mobile is True
    assert _station("USM00072357").is_mobile is False


@pytest.mark.parametrize("year,covered", [
    (1973, False), (1974, True), (2000, True), (2026, True), (2027, False),
])
def test_the_published_record_span_is_checked_before_any_download(year,
                                                                 covered):
    assert _station("USM00072357").covers_year(year) is covered


# --------------------------------------------------------------------------- #
# Header decoding
# --------------------------------------------------------------------------- #
def test_a_real_header_decodes_every_field():
    header = igra2._decode_header(REAL_HEADER, 7)

    assert header.station_id == "USM00072357"
    assert (header.year, header.month, header.day) == (2026, 1, 1)
    assert header.hour == 0
    assert header.reltime == 2302
    assert header.n_levels == 217
    assert header.pressure_source == "ncdc-nws"
    assert header.nonpressure_source == "ncdc-gts"
    assert header.lat == pytest.approx(35.1808)
    assert header.lon == pytest.approx(-97.4378)
    assert header.line_index == 7


def test_the_nominal_valid_time_is_utc():
    header = igra2._decode_header(REAL_HEADER, 0)

    assert header.nominal_valid == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_a_release_before_its_nominal_hour_rolls_back_a_day():
    """A 00Z sounding is launched around 23Z the previous day."""
    header = igra2._decode_header(REAL_HEADER, 0)

    assert header.release_time == datetime(
        2025, 12, 31, 23, 2, tzinfo=timezone.utc)


def test_a_release_inside_its_own_day_keeps_that_date():
    header = igra2._decode_header(
        build_header(hour=12, reltime=1119), 0)

    assert header.release_time == datetime(
        2026, 1, 1, 11, 19, tzinfo=timezone.utc)


def test_a_missing_nominal_hour_falls_back_to_the_release_hour():
    header = igra2._decode_header(
        build_header(hour=igra2.UNKNOWN_HOUR, reltime=1130), 0)

    assert header.nominal_valid == datetime(
        2026, 1, 1, 11, tzinfo=timezone.utc)


def test_a_sounding_with_no_time_at_all_has_no_valid_time():
    header = igra2._decode_header(
        build_header(hour=igra2.UNKNOWN_HOUR,
                     reltime=igra2.UNKNOWN_RELTIME), 0)

    assert header.nominal_valid is None
    assert header.release_time is None


def test_a_release_reported_to_the_hour_only_is_accepted():
    """RELTIME 2399 means the hour is known and the minute is not."""
    header = igra2._decode_header(build_header(hour=0, reltime=2399), 0)

    assert header.release_time == datetime(
        2025, 12, 31, 23, 0, tzinfo=timezone.utc)


def test_an_impossible_calendar_date_yields_no_valid_time():
    header = igra2._decode_header(build_header(month=2, day=31), 0)

    assert header.nominal_valid is None


@pytest.mark.parametrize("sentinel", [igra2.RAW_MISSING, igra2.RAW_REMOVED])
def test_a_missing_position_is_not_scaled_into_a_real_coordinate(sentinel):
    """``-9999 / 10000`` is ``-0.9999``: a plausible latitude near the equator.

    Scaling before the sentinel check would make a missing position
    indistinguishable from a real one just off the Gulf of Guinea.
    """
    header = igra2._decode_header(
        build_header(raw_lat=sentinel, raw_lon=sentinel), 0)

    assert math.isnan(header.lat)
    assert math.isnan(header.lon)


def test_a_mobile_platform_position_sentinel_is_rejected():
    header = igra2._decode_header(
        build_header(raw_lat=-988888, raw_lon=-9988888), 0)

    assert math.isnan(header.lat)
    assert math.isnan(header.lon)


# --------------------------------------------------------------------------- #
# Level decoding
# --------------------------------------------------------------------------- #
def test_a_real_level_decodes_into_project_units():
    level = igra2._decode_level(REAL_LEVEL)

    assert level.pres == pytest.approx(977.88)
    assert level.hght == pytest.approx(345.0)
    assert level.tmpc == pytest.approx(17.6)
    assert level.wdir == pytest.approx(235.0)
    assert level.wspd == pytest.approx(1.5 * igra2.MS_TO_KNOTS)
    assert level.is_surface is True


def test_dewpoint_comes_from_the_depression_not_the_raw_field():
    """DPDP is a dewpoint *depression*: 17.6 C minus 19.7 C is -2.1 C."""
    level = igra2._decode_level(REAL_LEVEL)

    assert level.dwpc == pytest.approx(-2.1)
    assert level.dewpoint_from_rh is False


def test_wind_speed_is_converted_from_metres_per_second_to_knots():
    level = igra2._decode_level(build_level(wspd=100))

    assert level.wspd == pytest.approx(10.0 * igra2.MS_TO_KNOTS)
    assert level.wspd == pytest.approx(19.438, abs=1e-3)


@pytest.mark.parametrize("sentinel", [igra2.RAW_MISSING, igra2.RAW_REMOVED])
def test_a_level_with_no_pressure_is_dropped(sentinel):
    assert igra2._decode_level(build_level(press=sentinel)) is None


def test_a_declared_non_pressure_level_is_dropped():
    """LVLTYP1 3 is a wind-only level with no pressure coordinate."""
    assert igra2._decode_level(
        build_level(lvltyp1=3, lvltyp2=0, press=igra2.RAW_MISSING)
    ) is None


@pytest.mark.parametrize("sentinel", [igra2.RAW_MISSING, igra2.RAW_REMOVED])
def test_missing_per_level_fields_become_the_shared_sentinel(sentinel):
    level = igra2._decode_level(
        build_level(gph=sentinel, temp=sentinel, rh=sentinel,
                    dpdp=sentinel, wdir=sentinel, wspd=sentinel))

    assert level.hght == igra2.MISSING
    assert level.tmpc == igra2.MISSING
    assert level.dwpc == igra2.MISSING
    assert level.wdir == igra2.MISSING
    assert level.wspd == igra2.MISSING


def test_dewpoint_falls_back_to_relative_humidity_when_needed():
    """The older record often carries RH but no dewpoint depression."""
    level = igra2._decode_level(
        build_level(temp=200, rh=500, dpdp=igra2.RAW_MISSING))

    assert level.dewpoint_from_rh is True
    assert level.dwpc < 20.0
    assert level.dwpc == pytest.approx(9.26, abs=0.1)


def test_the_depression_is_preferred_over_relative_humidity():
    level = igra2._decode_level(build_level(temp=200, rh=500, dpdp=50))

    assert level.dwpc == pytest.approx(15.0)
    assert level.dewpoint_from_rh is False


def test_no_dewpoint_is_invented_without_a_temperature():
    level = igra2._decode_level(
        build_level(temp=igra2.RAW_MISSING, rh=500,
                    dpdp=igra2.RAW_MISSING))

    assert level.dwpc == igra2.MISSING
    assert level.dewpoint_from_rh is False


@pytest.mark.parametrize("wdir", [-1, 361, 720])
def test_an_out_of_range_wind_direction_is_discarded(wdir):
    assert igra2._decode_level(build_level(wdir=wdir)).wdir == igra2.MISSING


def test_a_calm_wind_is_kept_rather_than_treated_as_missing():
    level = igra2._decode_level(build_level(wdir=0, wspd=0))

    assert level.wdir == 0.0
    assert level.wspd == 0.0


def test_a_short_line_does_not_raise():
    assert igra2._decode_level("21     0  97788B  345") is not None


# --------------------------------------------------------------------------- #
# Dewpoint from relative humidity
# --------------------------------------------------------------------------- #
def test_saturated_air_has_a_dewpoint_equal_to_its_temperature():
    assert igra2.dewpoint_from_relative_humidity(20.0, 100.0) == \
        pytest.approx(20.0, abs=0.05)


def test_drier_air_has_a_lower_dewpoint():
    humid = igra2.dewpoint_from_relative_humidity(20.0, 80.0)
    dry = igra2.dewpoint_from_relative_humidity(20.0, 30.0)

    assert dry < humid < 20.0


@pytest.mark.parametrize("rh", [0.0, -5.0])
def test_a_non_positive_humidity_yields_no_dewpoint(rh):
    assert igra2.dewpoint_from_relative_humidity(20.0, rh) == igra2.MISSING


def test_a_non_finite_input_yields_no_dewpoint():
    assert igra2.dewpoint_from_relative_humidity(
        float("nan"), 50.0) == igra2.MISSING
    assert igra2.dewpoint_from_relative_humidity(
        20.0, float("inf")) == igra2.MISSING


def test_an_over_unity_humidity_is_clamped_not_extrapolated():
    assert igra2.dewpoint_from_relative_humidity(20.0, 140.0) == \
        pytest.approx(20.0, abs=0.05)


# --------------------------------------------------------------------------- #
# Sounding assembly
# --------------------------------------------------------------------------- #
def _archive(*soundings):
    """Join ``(header, levels)`` pairs into one station archive text."""
    lines = []
    for header, levels in soundings:
        lines.append(header)
        lines.extend(levels)
    return "\n".join(lines) + "\n"


def test_a_sounding_is_assembled_from_its_declared_level_count():
    levels = [
        build_level(press=97788, gph=345, temp=176),
        build_level(press=95000, gph=560, temp=150),
        build_level(press=92500, gph=790, temp=130),
    ]
    text = _archive((build_header(numlev=3), levels))
    header = next(iter(igra2.iter_headers(text)))

    sounding = igra2.parse_sounding(text, header)

    assert sounding.n_levels == 3
    assert sounding.pres == pytest.approx((977.88, 950.0, 925.0))
    assert sounding.tmpc == pytest.approx((17.6, 15.0, 13.0))


def test_pressure_is_forced_strictly_decreasing():
    """Downstream interpolation assumes a monotonic vertical coordinate."""
    levels = [
        build_level(press=97788),
        build_level(press=95000),
        build_level(press=95000),   # repeat
        build_level(press=96000),   # inversion
        build_level(press=92500),
    ]
    text = _archive((build_header(numlev=5), levels))
    header = next(iter(igra2.iter_headers(text)))

    sounding = igra2.parse_sounding(text, header)

    assert sounding.pres == pytest.approx((977.88, 950.0, 925.0))
    assert sounding.dropped_levels == 2
    assert list(sounding.pres) == sorted(sounding.pres, reverse=True)


def test_dropped_non_pressure_levels_are_counted():
    levels = [
        build_level(press=97788),
        build_level(lvltyp1=3, lvltyp2=0, press=igra2.RAW_MISSING),
        build_level(lvltyp1=3, lvltyp2=0, press=igra2.RAW_MISSING),
        build_level(press=92500),
    ]
    text = _archive((build_header(numlev=4), levels))
    header = next(iter(igra2.iter_headers(text)))

    sounding = igra2.parse_sounding(text, header)

    assert sounding.n_levels == 2
    assert sounding.dropped_levels == 2


def test_levels_reconstructed_from_humidity_are_counted():
    levels = [
        build_level(press=97788, temp=200, rh=500, dpdp=igra2.RAW_MISSING),
        build_level(press=95000, temp=180, rh=400, dpdp=igra2.RAW_MISSING),
        build_level(press=92500, temp=160, rh=300, dpdp=40),
    ]
    text = _archive((build_header(numlev=3), levels))
    header = next(iter(igra2.iter_headers(text)))

    sounding = igra2.parse_sounding(text, header)

    assert sounding.dewpoint_from_rh_levels == 2


def test_every_column_has_the_same_length():
    levels = [build_level(press=97788 - 1000 * index) for index in range(6)]
    text = _archive((build_header(numlev=6), levels))
    header = next(iter(igra2.iter_headers(text)))

    sounding = igra2.parse_sounding(text, header)

    lengths = {
        len(sounding.pres), len(sounding.hght), len(sounding.tmpc),
        len(sounding.dwpc), len(sounding.wdir), len(sounding.wspd),
    }
    assert lengths == {6}


def test_a_sounding_with_no_pressure_levels_is_an_error():
    levels = [build_level(lvltyp1=3, lvltyp2=0, press=igra2.RAW_MISSING)]
    text = _archive((build_header(numlev=1), levels))
    header = next(iter(igra2.iter_headers(text)))

    with pytest.raises(igra2.IGRASoundingParseError):
        igra2.parse_sounding(text, header)


def test_assembly_stops_at_the_next_header_even_if_numlev_overruns():
    first = (build_header(numlev=9), [build_level(press=97788)])
    second = (build_header(day=2, numlev=1), [build_level(press=90000)])
    text = _archive(first, second)
    header = next(iter(igra2.iter_headers(text)))

    sounding = igra2.parse_sounding(text, header)

    assert sounding.n_levels == 1
    assert sounding.pres == pytest.approx((977.88,))


def test_every_header_in_an_archive_is_found_in_order():
    text = _archive(
        (build_header(day=1, numlev=1), [build_level()]),
        (build_header(day=2, numlev=1), [build_level()]),
        (build_header(day=3, numlev=1), [build_level()]),
    )

    headers = list(igra2.iter_headers(text))

    assert [item.day for item in headers] == [1, 2, 3]
    assert [item.line_index for item in headers] == [0, 2, 4]


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
def test_the_cache_root_honours_an_explicit_override(monkeypatch, tmp_path):
    monkeypatch.setenv("SHARPMOD_IGRA_CACHE", str(tmp_path / "elsewhere"))

    assert igra2.default_igra_cache_root() == tmp_path / "elsewhere"


def test_the_cache_root_is_separate_from_the_model_cache(monkeypatch):
    monkeypatch.delenv("SHARPMOD_IGRA_CACHE", raising=False)
    from sharpmod import model_disk_cache

    assert igra2.default_igra_cache_root() != \
        model_disk_cache.default_model_cache_root()


def test_a_written_entry_reads_back(tmp_path):
    cache = igra2.IGRACache(root=tmp_path)
    cache.write("thing.txt", b"payload")

    assert cache.read("thing.txt") == b"payload"


def test_a_missing_entry_reads_as_none_and_is_infinitely_old(tmp_path):
    cache = igra2.IGRACache(root=tmp_path)

    assert cache.read("absent") is None
    assert cache.age_seconds("absent") == math.inf


def test_a_fresh_entry_is_served_without_calling_the_loader(tmp_path):
    cache = igra2.IGRACache(root=tmp_path)
    cache.write("thing", b"cached")
    calls = []

    payload = cache.load(
        "thing", lambda: calls.append(1) or b"fetched",
        max_age_seconds=3600)

    assert payload == b"cached"
    assert calls == []


def test_a_stale_entry_is_refreshed(tmp_path):
    cache = igra2.IGRACache(root=tmp_path)
    cache.write("thing", b"old")

    payload = cache.load("thing", lambda: b"new", max_age_seconds=-1)

    assert payload == b"new"
    assert cache.read("thing") == b"new"


def test_a_successful_load_enforces_the_cache_budget(tmp_path):
    import os

    cache = igra2.IGRACache(root=tmp_path, max_bytes=150)
    cache.load("old", lambda: b"a" * 100, max_age_seconds=3600)
    os.utime(cache.path_for("old"), (1, 1))

    payload = cache.load("new", lambda: b"b" * 100, max_age_seconds=3600)

    assert payload == b"b" * 100
    assert cache.read("old") is None
    assert cache.read("new") == b"b" * 100


def test_a_stale_entry_is_served_when_the_refresh_fails(tmp_path):
    """A daily-updated public mirror should degrade to yesterday's copy."""
    cache = igra2.IGRACache(root=tmp_path)
    cache.write("thing", b"yesterday")

    def _fail():
        raise igra2.IGRARetrievalError("offline")

    payload = cache.load("thing", _fail, max_age_seconds=-1)

    assert payload == b"yesterday"


def test_a_failed_refresh_with_no_cached_copy_raises(tmp_path):
    cache = igra2.IGRACache(root=tmp_path)

    def _fail():
        raise igra2.IGRARetrievalError("offline")

    with pytest.raises(igra2.IGRARetrievalError):
        cache.load("thing", _fail, max_age_seconds=3600)


def test_a_stale_copy_can_be_refused(tmp_path):
    cache = igra2.IGRACache(root=tmp_path)
    cache.write("thing", b"yesterday")

    def _fail():
        raise igra2.IGRARetrievalError("offline")

    with pytest.raises(igra2.IGRARetrievalError):
        cache.load("thing", _fail, max_age_seconds=-1, allow_stale=False)


def test_a_write_leaves_no_partial_files_behind(tmp_path):
    cache = igra2.IGRACache(root=tmp_path)
    cache.write("thing", b"payload")

    leftovers = [
        path.name for path in cache.directory.iterdir()
        if path.name.endswith(".part")
    ]
    assert leftovers == []


def test_pruning_evicts_oldest_first_down_to_the_budget(tmp_path):
    import os
    import time

    cache = igra2.IGRACache(root=tmp_path, max_bytes=200)
    for index, name in enumerate(("oldest", "middle", "newest")):
        cache.write(name, b"x" * 100)
        os.utime(cache.path_for(name), (time.time() + index,
                                        time.time() + index))

    removed = cache.prune()

    assert [path.name for path in removed] == ["oldest"]
    assert cache.read("oldest") is None
    assert cache.read("newest") == b"x" * 100


def test_pruning_within_budget_removes_nothing(tmp_path):
    cache = igra2.IGRACache(root=tmp_path, max_bytes=10_000)
    cache.write("thing", b"x" * 100)

    assert cache.prune() == []


def test_clearing_removes_every_entry(tmp_path):
    cache = igra2.IGRACache(root=tmp_path)
    cache.write("one", b"a")
    cache.write("two", b"b")

    removed = cache.clear()

    assert len(removed) == 2
    assert cache.entries() == []


def test_a_station_id_cannot_escape_the_cache_directory(tmp_path):
    cache = igra2.IGRACache(root=tmp_path)

    path = cache.path_for("../../escape.zip")

    assert path.parent == cache.directory
    assert cache.directory.resolve() in path.resolve().parents


# --------------------------------------------------------------------------- #
# Decoder: station resolution
# --------------------------------------------------------------------------- #
def _decoder(tmp_path, *, archives=None, station_list=None, exists=None,
             **kwargs):
    """Build a decoder whose HTTP is supplied rather than performed."""
    archives = archives or {}
    listing = (station_list or REAL_STATION_LINES).encode("utf-8")

    def http_get(url):
        if url == igra2.STATION_LIST_URL:
            return listing
        if url in archives:
            return archives[url]
        raise igra2.IGRAStationTimeUnavailableError(f"no archive at {url}")

    def http_head(url):
        if exists is not None:
            return 200 if exists(url) else 404
        return 200 if url in archives else 404

    return igra2.IGRA_Decoder(
        cache=igra2.IGRACache(root=tmp_path),
        http_get=http_get,
        http_head=http_head,
        **kwargs,
    )


def test_stations_are_parsed_and_reused(tmp_path):
    decoder = _decoder(tmp_path)

    first = decoder.stations()
    second = decoder.stations()

    assert len(first) == 4
    assert first is second


def test_a_full_igra_id_resolves(tmp_path):
    decoder = _decoder(tmp_path)

    assert decoder.resolve_station("acm00078861").id == "ACM00078861"


def test_a_bare_wmo_number_resolves_through_the_network_code(tmp_path):
    decoder = _decoder(tmp_path)

    assert decoder.resolve_station("41217").id == "AEM00041217"


def test_a_name_substring_resolves(tmp_path):
    decoder = _decoder(tmp_path)

    assert decoder.resolve_station("sharjah").id == "AEXUAE05467"


def test_an_unknown_query_is_reported(tmp_path):
    decoder = _decoder(tmp_path)

    with pytest.raises(igra2.IGRAStationLookupError, match="no IGRA station"):
        decoder.resolve_station("nowhere at all")


def test_an_empty_query_is_reported(tmp_path):
    decoder = _decoder(tmp_path)

    with pytest.raises(igra2.IGRAStationLookupError):
        decoder.resolve_station("   ")


def test_an_ambiguous_name_lists_the_candidates(tmp_path):
    listing = "\n".join([
        build_station_line(station_id="USM00072357", name="NORMAN NORTH"),
        build_station_line(station_id="USM00072358", name="NORMAN SOUTH"),
    ])
    decoder = _decoder(tmp_path, station_list=listing)

    with pytest.raises(igra2.IGRAStationLookupError, match="matched 2"):
        decoder.resolve_station("norman")


# --------------------------------------------------------------------------- #
# Decoder: archive selection and fetch
# --------------------------------------------------------------------------- #
def _zip_bytes(text, member="USM00072357-data.txt"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(member, text)
    return buffer.getvalue()


OKC_LIST = build_station_line()


def _y2d_url(year=2026, station="USM00072357"):
    return f"{igra2.DATA_Y2D_URL}/{station}-data-beg{year}.txt.zip"


def _por_url(station="USM00072357"):
    return f"{igra2.DATA_POR_URL}/{station}-data.txt.zip"


def _one_sounding_archive(hour=12, day=1, month=9, year=2026):
    levels = [
        build_level(press=97620, gph=345, temp=222, dpdp=53),
        build_level(press=95000, gph=560, temp=205, dpdp=60),
        build_level(press=92500, gph=790, temp=190, dpdp=70),
        build_level(press=85000, gph=1500, temp=150, dpdp=90),
    ]
    text = _archive(
        (build_header(year=year, month=month, day=day, hour=hour, numlev=4),
         levels)
    )
    return _zip_bytes(text)


def test_a_recent_date_uses_the_small_year_to_date_archive(tmp_path):
    decoder = _decoder(
        tmp_path,
        station_list=OKC_LIST,
        archives={_y2d_url(): _one_sounding_archive()},
        now=lambda: datetime(2026, 9, 5, tzinfo=timezone.utc),
    )

    sounding = decoder.fetch(
        "72357", datetime(2026, 9, 1, 12, tzinfo=timezone.utc))

    assert sounding.archive_kind == "y2d"
    assert "data-beg2026" in sounding.archive_url
    assert sounding.n_levels == 4


def test_the_moving_year_to_date_filename_is_discovered_not_constructed(
        tmp_path):
    """``beg2025`` is already gone upstream, so the year has to be probed."""
    decoder = _decoder(
        tmp_path,
        station_list=OKC_LIST,
        archives={_y2d_url(2025): _one_sounding_archive(year=2025)},
        now=lambda: datetime(2026, 9, 5, tzinfo=timezone.utc),
    )

    sounding = decoder.fetch(
        "72357", datetime(2025, 9, 1, 12, tzinfo=timezone.utc))

    assert "data-beg2025" in sounding.archive_url


def test_a_date_before_the_year_to_date_window_falls_back_to_the_full_record(
        tmp_path):
    archive = _one_sounding_archive(year=1995)
    decoder = _decoder(
        tmp_path,
        station_list=OKC_LIST,
        archives={_y2d_url(): _one_sounding_archive(), _por_url(): archive},
        now=lambda: datetime(2026, 9, 5, tzinfo=timezone.utc),
    )

    sounding = decoder.fetch(
        "72357", datetime(1995, 9, 1, 12, tzinfo=timezone.utc))

    assert sounding.archive_kind == "por"
    assert sounding.archive_url == _por_url()


def test_a_cheap_probe_refuses_the_full_record_archive(tmp_path):
    """An 80 MB download must not happen behind an availability check."""
    decoder = _decoder(
        tmp_path,
        station_list=OKC_LIST,
        archives={_y2d_url(): _one_sounding_archive()},
        now=lambda: datetime(2026, 9, 5, tzinfo=timezone.utc),
        allow_full_record=False,
    )

    with pytest.raises(igra2.IGRAFullRecordRequiredError):
        decoder.fetch("72357", datetime(1995, 9, 1, 12, tzinfo=timezone.utc))


def test_the_refusal_is_still_an_unavailable_error(tmp_path):
    """Callers that do not care about the distinction keep working."""
    assert issubclass(igra2.IGRAFullRecordRequiredError,
                      igra2.IGRAStationTimeUnavailableError)


def test_a_year_outside_the_published_record_is_refused_without_a_download(
        tmp_path):
    requested = []

    def http_get(url):
        requested.append(url)
        if url == igra2.STATION_LIST_URL:
            return OKC_LIST.encode("utf-8")
        raise AssertionError("no archive should be requested")

    decoder = igra2.IGRA_Decoder(
        cache=igra2.IGRACache(root=tmp_path),
        http_get=http_get,
        http_head=lambda _url: 404,
    )

    with pytest.raises(igra2.IGRAStationTimeUnavailableError,
                       match="1974-2026"):
        decoder.fetch("72357", datetime(1850, 1, 1, tzinfo=timezone.utc))

    assert requested == [igra2.STATION_LIST_URL]


def test_an_exact_hour_match_is_preferred(tmp_path):
    text = _archive(
        (build_header(year=2026, month=9, day=1, hour=6, numlev=1),
         [build_level(press=97000)]),
        (build_header(year=2026, month=9, day=1, hour=12, numlev=1),
         [build_level(press=96000)]),
    )
    decoder = _decoder(
        tmp_path, station_list=OKC_LIST,
        archives={_y2d_url(): _zip_bytes(text)},
        now=lambda: datetime(2026, 9, 5, tzinfo=timezone.utc),
    )

    sounding = decoder.fetch(
        "72357", datetime(2026, 9, 1, 12, tzinfo=timezone.utc))

    assert sounding.valid.hour == 12
    assert sounding.pres == pytest.approx((960.0,))


def test_a_special_release_answers_a_nearby_synoptic_request(tmp_path):
    """IGRA carries ascents at 16-21Z, so 18Z should find a 19Z release."""
    text = _archive(
        (build_header(year=2026, month=9, day=1, hour=19, numlev=1),
         [build_level(press=96000)]),
    )
    decoder = _decoder(
        tmp_path, station_list=OKC_LIST,
        archives={_y2d_url(): _zip_bytes(text)},
        now=lambda: datetime(2026, 9, 5, tzinfo=timezone.utc),
    )

    sounding = decoder.fetch(
        "72357", datetime(2026, 9, 1, 18, tzinfo=timezone.utc))

    assert sounding.valid.hour == 19


def test_a_sounding_beyond_the_tolerance_is_not_used(tmp_path):
    text = _archive(
        (build_header(year=2026, month=9, day=1, hour=0, numlev=1),
         [build_level(press=96000)]),
    )
    decoder = _decoder(
        tmp_path, station_list=OKC_LIST,
        archives={_y2d_url(): _zip_bytes(text)},
        now=lambda: datetime(2026, 9, 5, tzinfo=timezone.utc),
    )

    with pytest.raises(igra2.IGRAStationTimeUnavailableError, match="within"):
        decoder.fetch("72357", datetime(2026, 9, 1, 12,
                                        tzinfo=timezone.utc))


def test_the_match_tolerance_can_be_widened_per_request(tmp_path):
    text = _archive(
        (build_header(year=2026, month=9, day=1, hour=0, numlev=1),
         [build_level(press=96000)]),
    )
    decoder = _decoder(
        tmp_path, station_list=OKC_LIST,
        archives={_y2d_url(): _zip_bytes(text)},
        now=lambda: datetime(2026, 9, 5, tzinfo=timezone.utc),
    )

    sounding = decoder.fetch(
        "72357", datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
        tolerance=timedelta(hours=13))

    assert sounding.valid.hour == 0


def test_a_naive_request_time_is_treated_as_utc(tmp_path):
    decoder = _decoder(
        tmp_path, station_list=OKC_LIST,
        archives={_y2d_url(): _one_sounding_archive()},
        now=lambda: datetime(2026, 9, 5, tzinfo=timezone.utc),
    )

    sounding = decoder.fetch("72357", datetime(2026, 9, 1, 12))

    assert sounding.valid == datetime(2026, 9, 1, 12, tzinfo=timezone.utc)


def test_a_non_datetime_request_time_is_rejected(tmp_path):
    decoder = _decoder(tmp_path, station_list=OKC_LIST)

    with pytest.raises(TypeError):
        decoder.fetch("72357", "2026-09-01 12:00")


def test_a_missing_header_position_falls_back_to_the_station_list(tmp_path):
    text = _archive(
        (build_header(year=2026, month=9, day=1, hour=12, numlev=1,
                      raw_lat=igra2.RAW_MISSING, raw_lon=igra2.RAW_MISSING),
         [build_level(press=96000)]),
    )
    decoder = _decoder(
        tmp_path, station_list=OKC_LIST,
        archives={_y2d_url(): _zip_bytes(text)},
        now=lambda: datetime(2026, 9, 5, tzinfo=timezone.utc),
    )

    sounding = decoder.fetch(
        "72357", datetime(2026, 9, 1, 12, tzinfo=timezone.utc))

    assert sounding.lat == pytest.approx(35.1808)
    assert sounding.lon == pytest.approx(-97.4378)


def test_a_corrupt_archive_is_evicted_so_it_cannot_keep_failing(tmp_path):
    cache = igra2.IGRACache(root=tmp_path)
    decoder = igra2.IGRA_Decoder(
        cache=cache,
        http_get=lambda url: (
            OKC_LIST.encode("utf-8")
            if url == igra2.STATION_LIST_URL else b"not a zip"
        ),
        http_head=lambda _url: 200,
        now=lambda: datetime(2026, 9, 5, tzinfo=timezone.utc),
    )

    with pytest.raises(igra2.IGRASoundingParseError):
        decoder.fetch("72357", datetime(2026, 9, 1, 12, tzinfo=timezone.utc))

    assert cache.read("USM00072357-y2d.zip") is None


def test_an_empty_archive_is_reported(tmp_path):
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w"):
        pass
    decoder = _decoder(
        tmp_path, station_list=OKC_LIST,
        archives={_y2d_url(): empty.getvalue()},
        now=lambda: datetime(2026, 9, 5, tzinfo=timezone.utc),
    )

    with pytest.raises(igra2.IGRASoundingParseError, match="no files"):
        decoder.fetch("72357", datetime(2026, 9, 1, 12, tzinfo=timezone.utc))


def test_the_discovered_year_to_date_year_is_remembered(tmp_path):
    probes = []
    archives = {_y2d_url(): _one_sounding_archive()}

    def http_head(url):
        probes.append(url)
        return 200 if url in archives else 404

    cache = igra2.IGRACache(root=tmp_path)

    def build():
        return igra2.IGRA_Decoder(
            cache=cache,
            http_get=lambda url: (
                OKC_LIST.encode("utf-8")
                if url == igra2.STATION_LIST_URL else archives[url]
            ),
            http_head=http_head,
            now=lambda: datetime(2026, 9, 5, tzinfo=timezone.utc),
        )

    when = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    build().fetch("72357", when)
    first_round = len(probes)
    build().fetch("72357", when)

    assert json.loads(cache.read("y2d-begin-year.json"))["year"] == 2026
    assert len(probes) == first_round + 1, \
        "the remembered year should be tried first, not rediscovered"


def test_an_oversized_archive_is_refused(tmp_path):
    decoder = igra2.IGRA_Decoder(
        cache=igra2.IGRACache(root=tmp_path),
        http_get=lambda url: (
            OKC_LIST.encode("utf-8")
            if url == igra2.STATION_LIST_URL else b"x" * 5000
        ),
        http_head=lambda _url: 200,
        max_archive_bytes=100,
        now=lambda: datetime(2026, 9, 5, tzinfo=timezone.utc),
    )
    # The station list itself is within the ceiling; the archive is not.
    decoder.stations()

    with pytest.raises(igra2.IGRASoundingParseError):
        decoder.fetch("72357", datetime(2026, 9, 1, 12, tzinfo=timezone.utc))


def test_the_download_ceiling_clears_a_real_full_record_archive():
    """A real period-of-record ZIP is around 80 MB; the default must clear it."""
    assert igra2.IGRA_Decoder.DEFAULT_MAX_ARCHIVE_BYTES > 100 * 1024 * 1024


def test_the_second_fetch_of_a_station_does_not_download_again(tmp_path):
    downloads = []
    archive = _one_sounding_archive()

    def http_get(url):
        downloads.append(url)
        if url == igra2.STATION_LIST_URL:
            return OKC_LIST.encode("utf-8")
        return archive

    decoder = igra2.IGRA_Decoder(
        cache=igra2.IGRACache(root=tmp_path),
        http_get=http_get,
        http_head=lambda _url: 200,
        now=lambda: datetime(2026, 9, 5, tzinfo=timezone.utc),
    )
    when = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)

    decoder.fetch("72357", when)
    after_first = list(downloads)
    decoder.fetch("72357", when)

    assert downloads == after_first

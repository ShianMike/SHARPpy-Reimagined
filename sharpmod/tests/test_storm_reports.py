"""Parsing and drawing NWS Local Storm Reports.

The fixture below is the real response shape, taken from a live call to the
service, so a change in its columns shows up here rather than as an empty
overlay.
"""

from __future__ import annotations

import urllib.parse
from datetime import datetime, timedelta, timezone

import pytest

from sharpmod import storm_reports as sr

UTC = timezone.utc

HEADER = ("VALID,VALID2,LAT,LON,MAG,WFO,TYPECODE,TYPETEXT,CITY,COUNTY,STATE,"
          "SOURCE,REMARK,UGC,UGCNAME,QUALIFIER")

FEED = "\n".join([
    HEADER,
    "202609070450,2026/09/07 04:50,47.71,-108.72,3.25,GGW,H,HAIL,"
    "14 SW Landusky,Phillips,MT,Public,"
    "Report from mPING: Baseball+ (3.25 in.).,MTC071,Phillips,M",
    "202609070519,2026/09/07 05:19,44.05,-104.54,54.0,UNR,G,TSTM WND GST,"
    "6 SE Upton,Weston,WY,Public,,WYC045,Weston,M",
    "202609070530,2026/09/07 05:30,35.22,-97.44,,OUN,T,TORNADO,"
    "3 N Norman,Cleveland,OK,Storm Chaser,Brief touchdown.,OKC027,Cleveland,M",
    # Marine wind: real in the feed, and not something an outlook forecasts.
    "202609070540,2026/09/07 05:40,29.91,-84.34,42.0,TAE,M,MARINE TSTM WIND,"
    "4 ENE Alligator Point,Franklin,FL,Mesonet,,FLC037,Franklin,M",
])


def test_the_three_convective_hazards_are_kept():
    reports = sr.parse_reports(FEED)

    assert [report.hazard.code for report in reports] == ["H", "G", "T"]


def test_marine_and_other_non_convective_types_are_dropped():
    """The feed is the fuller upstream of SPC's list, not SPC's edit of it."""
    assert all(report.hazard.code != "M" for report in sr.parse_reports(FEED))


def test_the_timestamp_is_read_as_utc():
    """No convective-day reassembly needed: the service gives a full stamp."""
    first = sr.parse_reports(FEED)[0]

    assert first.valid == datetime(2026, 9, 7, 4, 50, tzinfo=UTC)


def test_magnitudes_keep_the_units_the_service_published():
    hail, wind, tornado = sr.parse_reports(FEED)

    assert hail.magnitude == pytest.approx(3.25)
    assert hail.magnitude_text() == "3.25 in"
    assert hail.label() == "Sig Hail 3.25 in", (
        "3.25 in is past SPC's 2 in significant threshold, and the hazard must "
        "not be said twice")
    assert wind.magnitude_text() == "54 mph"
    assert tornado.magnitude is None, "a fresh tornado report carries no scale"
    assert tornado.magnitude_text() == ""


def test_a_report_without_a_magnitude_is_still_labelled():
    tornado = sr.parse_reports(FEED)[2]

    assert tornado.label() == "Tornado"


def test_the_description_carries_what_a_click_should_show():
    hail = sr.parse_reports(FEED)[0]
    text = hail.description()

    assert "Phillips" in text and "MT" in text
    assert "07 Sep 0450Z" in text
    assert "Source: Public" in text
    assert "mPING" in text
    assert "GGW" in text


@pytest.mark.parametrize("payload", ["", "   ", HEADER, HEADER + "\n"])
def test_a_quiet_period_parses_to_nothing(payload):
    assert sr.parse_reports(payload) == ()


@pytest.mark.parametrize("row", [
    "202609070450,x,999.0,-108.72,3.25,GGW,H,HAIL,a,b,MT,Public,,,,M",
    "202609070450,x,47.71,-999.0,3.25,GGW,H,HAIL,a,b,MT,Public,,,,M",
    "notatime,x,47.71,-108.72,3.25,GGW,H,HAIL,a,b,MT,Public,,,,M",
    "202609070450,x,,-108.72,3.25,GGW,H,HAIL,a,b,MT,Public,,,,M",
])
def test_one_undrawable_row_costs_only_that_row(row):
    """A live feed will contain oddities; the overlay must survive them."""
    reports = sr.parse_reports("\n".join([HEADER, row]))

    assert reports == ()


def test_a_bad_row_does_not_discard_the_good_ones():
    feed = "\n".join([
        HEADER,
        "notatime,x,47.71,-108.72,3.25,GGW,H,HAIL,a,b,MT,Public,,,,M",
        "202609070519,2026/09/07 05:19,44.05,-104.54,54.0,UNR,G,TSTM WND GST,"
        "6 SE Upton,Weston,WY,Public,,WYC045,Weston,M",
    ])

    assert len(sr.parse_reports(feed)) == 1


# --------------------------------------------------------------------------- #
# markers are sized for the view that asked for them
# --------------------------------------------------------------------------- #
def test_a_narrow_view_gets_a_smaller_marker_than_a_continental_one():
    """One fixed size cannot serve a 2 degree inset and a 60 degree map."""
    assert sr.marker_radius(2.0) < sr.marker_radius(60.0)


@pytest.mark.parametrize("span", [None, 0.0, -5.0, float("nan")])
def test_an_unusable_span_falls_back_to_a_drawable_marker(span):
    radius = sr.marker_radius(span)

    assert sr.MIN_MARKER_DEG <= radius <= sr.MAX_MARKER_DEG


@pytest.mark.parametrize("span", [0.001, 10_000.0])
def test_the_marker_stays_within_its_bounds(span):
    assert sr.MIN_MARKER_DEG <= sr.marker_radius(span) <= sr.MAX_MARKER_DEG


def test_markers_stay_round_away_from_the_equator():
    """A degree of longitude is shorter than a degree of latitude up north."""
    high = sr.StormReport(sr.HAZARDS["H"], datetime(2026, 9, 7, tzinfo=UTC),
                          60.0, -100.0, 1.0)
    ring = sr._marker_ring(high, 0.1)
    lons = [point[0] for point in ring]
    lats = [point[1] for point in ring]

    assert (max(lons) - min(lons)) > (max(lats) - min(lats))


# --------------------------------------------------------------------------- #
# layer
# --------------------------------------------------------------------------- #
def test_no_reports_means_no_layer():
    """A quiet day is not a failure, and an empty layer would draw a title."""
    assert sr.layer_from_reports(()) is None


def test_the_layer_carries_one_shape_per_report_with_click_text():
    layer = sr.layer_from_reports(sr.parse_reports(FEED), span_deg=6.0)

    assert layer.key == sr.OVERLAY_KEY
    assert len(layer.shapes) == 3
    assert all(shape.label for shape in layer.shapes)
    assert all(shape.description for shape in layer.shapes)
    assert layer.attribution == sr.ATTRIBUTION


def _ranks(layer):
    """Rank per hazard word, with SPC's "Sig" qualifier set aside."""
    return {shape.label.removeprefix("Sig ").split()[0]: shape.rank
            for shape in layer.shapes}


def test_a_tornado_outranks_wind_and_hail_where_markers_overlap():
    """Rank decides what stays legible, and what a click answers with."""
    ranks = _ranks(sr.layer_from_reports(sr.parse_reports(FEED), span_deg=6.0))

    assert ranks["Tornado"] > ranks["Wind"]
    assert ranks["Tornado"] > ranks["Hail"]


# --------------------------------------------------------------------------- #
# SPC's plotting convention
# --------------------------------------------------------------------------- #
#: An ordinary hail report and a significant gust, to sit either side of both
#: thresholds. Kept apart from ``FEED`` so the tests that index that fixture
#: positionally are not disturbed.
SIGNIFICANT_FEED = "\n".join([
    HEADER,
    # 1.00 in: severe, but under SPC's 2 in significant threshold.
    "202609070455,2026/09/07 04:55,35.22,-97.44,1.00,OUN,H,HAIL,"
    "Norman,Cleveland,OK,Public,Quarter size.,OKC027,Cleveland,M",
    # 80 mph: past 65 kt, so significant.
    "202609070500,2026/09/07 05:00,35.30,-97.50,80.0,OUN,G,TSTM WND GST,"
    "Norman,Cleveland,OK,Mesonet,Measured 80 mph.,OKC027,Cleveland,M",
    # Wind damage with no measurement, which is most of them.
    "202609070505,2026/09/07 05:05,35.40,-97.60,,OUN,D,TSTM WND DMG,"
    "Norman,Cleveland,OK,Public,Tree down.,OKC027,Cleveland,",
])


def test_spcs_thresholds_are_the_ones_used():
    """2 in hail and a 65 kt gust, in the units the feed publishes.

    The feed carries no unit column, so the units were read off the data: wind
    magnitudes sit beside remarks quoting the same number in mph, and hail runs
    0.25 to 4.0. A knots threshold would make every gust over 65 mph
    significant, roughly trebling the count.
    """
    assert sr.SIGNIFICANT_HAIL_IN == pytest.approx(2.00)
    assert sr.SIGNIFICANT_WIND_MPH == pytest.approx(74.8, abs=0.1)


def test_a_report_is_significant_only_past_its_own_threshold():
    ordinary_hail, big_gust, damage = sr.parse_reports(SIGNIFICANT_FEED)

    assert not ordinary_hail.is_significant(), "1 in hail is severe, not sig"
    assert big_gust.is_significant()
    assert not damage.is_significant(), (
        "an unmeasured damage report must not be promoted to the most "
        "prominent mark on the map")


def test_a_tornado_is_never_marked_significant_from_a_same_day_report():
    """EF2+ is a damage survey's verdict days later, not a report's."""
    tornado = sr.parse_reports(FEED)[2]

    assert tornado.hazard.significant_at is None
    assert not tornado.is_significant()


def test_significant_reports_take_the_neutral_colour_and_a_larger_mark():
    """SPC plots them in one colour whatever the hazard, so they stand out."""
    layer = sr.layer_from_reports(
        sr.parse_reports(SIGNIFICANT_FEED), span_deg=60.0)
    by_label = {shape.label: shape for shape in layer.shapes}

    ordinary = by_label["Hail 1.00 in"]
    significant = by_label["Sig Wind 80 mph"]

    assert ordinary.fill == sr.HAZARDS["H"].fill
    assert significant.fill == sr.SIGNIFICANT_FILL
    assert significant.stroke == sr.SIGNIFICANT_STROKE
    assert significant.fill != sr.HAZARDS["G"].fill, (
        "a significant gust drawn in the ordinary wind blue is invisible "
        "amongst the ordinary gusts")

    def width(shape):
        return shape.bounds[1] - shape.bounds[0]

    assert width(significant) > width(ordinary)


def test_an_ordinary_report_keeps_its_hazard_colour():
    """Red tornado, blue wind, green hail is what makes the map readable."""
    layer = sr.layer_from_reports(sr.parse_reports(FEED), span_deg=60.0)
    fills = {shape.label.removeprefix("Sig ").split()[0]: shape.fill
             for shape in layer.shapes}

    assert fills["Tornado"] == sr.HAZARDS["T"].fill
    assert fills["Wind"] == sr.HAZARDS["G"].fill
    assert len({sr.HAZARDS[code].fill for code in ("T", "G", "H")}) == 3, \
        "the three hazards must not share a colour"


def test_a_significant_report_outranks_its_own_class_but_not_a_tornado():
    reports = sr.parse_reports(SIGNIFICANT_FEED) + sr.parse_reports(FEED)
    ranks = _ranks(sr.layer_from_reports(reports, span_deg=60.0))

    assert ranks["Wind"] > ranks["Hail"]
    assert ranks["Tornado"] > ranks["Wind"], (
        "a significant gust is the exception within its class, not a rival "
        "to a tornado")


def test_every_report_is_drawn_as_a_point_symbol_not_an_area():
    """The paint path washes areas translucent; a five-pixel dot must not be.

    A washed marker reads as a smudge, and where an outbreak clusters dozens of
    reports the smudges merge into one bruise with nothing countable in it.
    """
    layer = sr.layer_from_reports(sr.parse_reports(FEED), span_deg=60.0)

    assert all(shape.marker for shape in layer.shapes)


def test_the_layer_asks_to_stay_out_of_the_map_legend():
    """One label per report is a listing of the data, not a key to it."""
    layer = sr.layer_from_reports(sr.parse_reports(FEED), span_deg=60.0)

    assert layer.legend is False
    assert layer.attribution, "the credit still has to be shown somewhere"


def test_the_layers_window_spans_the_reports_it_holds():
    layer = sr.layer_from_reports(sr.parse_reports(FEED))

    assert layer.valid_from == datetime(2026, 9, 7, 4, 50, tzinfo=UTC)
    assert layer.valid_to == datetime(2026, 9, 7, 5, 30, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# request
# --------------------------------------------------------------------------- #
def _query(url):
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


def test_the_services_hazard_filter_is_never_used():
    """It answers 200 OK with no rows for every code, including its own.

    Measured live: unfiltered returns 190 reports of which 8 are hail, while
    ``type=H`` returns a bare header. Sending it would leave a permanently empty
    overlay with nothing to explain it, so hazards are chosen from the response.
    """
    query = _query(sr.build_url())

    assert "type" not in query
    assert query["justcsv"] == ["1"]


def test_a_view_is_sent_as_a_bounding_box():
    """Reading one county must not transfer the continent."""
    query = _query(sr.build_url(view=(-103.0, -94.0, 33.5, 37.5)))

    assert query["west"] == ["-103.0000"] and query["east"] == ["-94.0000"]
    assert query["south"] == ["33.5000"] and query["north"] == ["37.5000"]


def test_a_reversed_view_is_normalised():
    query = _query(sr.build_url(view=(-94.0, -103.0, 37.5, 33.5)))

    assert query["west"] == ["-103.0000"] and query["north"] == ["37.5000"]


def test_the_window_is_expressed_in_seconds_back():
    query = _query(sr.build_url(window=timedelta(hours=3)))

    assert query["recent"] == ["10800"]


@pytest.mark.parametrize("window,expected", [
    (timedelta(seconds=1), "900"),        # floored to a useful minimum
    (timedelta(days=90), "999999"),       # capped as the service requires
])
def test_the_window_is_bounded(window, expected):
    assert _query(sr.build_url(window=window))["recent"] == [expected]


# --------------------------------------------------------------------------- #
# fetch
# --------------------------------------------------------------------------- #
def test_fetching_decodes_and_builds_the_layer():
    calls = []

    def opener(url, timeout, limit):
        calls.append(url)
        return FEED.encode("utf-8")

    layer = sr.fetch_layer(opener=opener, span_deg=6.0)

    assert len(layer.shapes) == 3
    assert layer.source_url == calls[0]


def test_a_quiet_window_returns_no_layer():
    layer = sr.fetch_layer(opener=lambda *args: HEADER.encode("utf-8"))

    assert layer is None


def test_cancellation_is_not_a_failure():
    def forbidden(*args, **kwargs):
        raise AssertionError("a cancelled fetch must not reach the network")

    assert sr.fetch_layer(opener=forbidden, should_cancel=lambda: True) is None


def test_a_provider_failure_is_reported_as_one():
    def broken(url, timeout, limit):
        raise OSError("connection reset")

    with pytest.raises(sr.StormReportsError, match="unavailable"):
        sr.fetch_layer(opener=broken)


# --------------------------------------------------------------------------- #
# the archive
#
# Unlike radar there is a deep archive behind these reports -- a 2015 query
# answers -- so a sounding from years ago can still be shown what happened.
# --------------------------------------------------------------------------- #
def test_a_past_event_is_asked_for_by_date_not_by_recency():
    """``sts`` is accepted only with a trailing ``Z``; the numeric fields aren't
    ambiguous, so those are used instead."""
    query = _query(sr.build_url(
        around=datetime(2021, 5, 1, 18, 0, tzinfo=UTC),
        window=timedelta(hours=6)))

    assert "recent" not in query
    assert query["year1"] == ["2021"] and query["year2"] == ["2021"]
    assert query["month1"] == ["5"] and query["day1"] == ["1"]


def test_the_past_window_is_centred_on_the_moment_asked_about():
    """Reports both leading up to and following the sounding are wanted."""
    query = _query(sr.build_url(
        around=datetime(2021, 5, 1, 18, 0, tzinfo=UTC),
        window=timedelta(hours=4)))

    assert query["hour1"] == ["16"] and query["hour2"] == ["20"]


def test_a_naive_moment_is_read_as_utc():
    query = _query(sr.build_url(around=datetime(2021, 5, 1, 18, 0),
                                window=timedelta(hours=2)))

    assert query["hour1"] == ["17"] and query["hour2"] == ["19"]


def test_a_window_crossing_midnight_rolls_the_date():
    query = _query(sr.build_url(
        around=datetime(2021, 5, 1, 23, 0, tzinfo=UTC),
        window=timedelta(hours=4)))

    assert query["day1"] == ["1"] and query["hour1"] == ["21"]
    assert query["day2"] == ["2"] and query["hour2"] == ["1"]


def test_omitting_the_moment_still_asks_for_the_latest():
    query = _query(sr.build_url())

    assert "recent" in query
    assert "year1" not in query


def test_a_past_request_can_still_be_narrowed_to_a_box():
    query = _query(sr.build_url(
        around=datetime(2021, 5, 1, 18, 0, tzinfo=UTC),
        view=(-103.0, -94.0, 33.5, 37.5)))

    assert query["year1"] == ["2021"]
    assert query["west"] == ["-103.0000"]


# --------------------------------------------------------------------------- #
# download and caching, following the model-field convention
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Never read or write the developer's real report cache from a test."""
    monkeypatch.setenv("SHARPMOD_STORM_REPORTS_CACHE", str(tmp_path / "cache"))
    sr.clear_cache()
    yield
    sr.clear_cache()


def _counting_opener(payload=FEED):
    calls: list[str] = []

    def opener(url, timeout, limit):
        calls.append(url)
        return payload.encode("utf-8")

    return opener, calls


PAST = datetime(2021, 5, 1, 18, 0, tzinfo=UTC)


def test_a_repeated_request_is_served_from_memory():
    """Scrubbing a time field must not become a burst of identical requests."""
    opener, calls = _counting_opener()

    first, url = sr.download(opener=opener)
    second, _ = sr.download(opener=opener)

    assert first == second
    assert len(calls) == 1
    assert url.startswith("https://")


def test_a_settled_window_is_kept_on_disk_between_sessions():
    opener, calls = _counting_opener()
    sr.download(around=PAST, opener=opener)
    assert len(calls) == 1

    # A new session: memory is empty but the disk copy remains.
    sr.clear_cache()
    sr.download(around=PAST, opener=opener)

    assert len(calls) == 1, "a settled window must not be downloaded twice"
    assert list(sr.disk_cache_root().glob("*.csv")), "nothing was written"


def test_a_live_window_is_never_written_to_disk():
    """It changes constantly, so a stored copy would only go stale."""
    opener, _calls = _counting_opener()

    sr.download(opener=opener)

    assert not list(sr.disk_cache_root().glob("*.csv"))


def test_a_recent_past_window_is_not_treated_as_settled():
    """Late reports arrive hours afterwards, so a window is not final at once."""
    just_ended = datetime.now(UTC) - timedelta(minutes=30)

    assert not sr.window_is_settled(window=timedelta(hours=1),
                                    around=just_ended)
    assert sr.window_is_settled(window=timedelta(hours=1), around=PAST)
    assert not sr.window_is_settled(window=timedelta(hours=1), around=None)


def test_different_windows_do_not_share_a_cache_entry():
    opener, calls = _counting_opener()

    sr.download(around=PAST, opener=opener)
    sr.download(around=PAST + timedelta(days=1), opener=opener)

    assert len(calls) == 2


def test_a_bounding_box_is_part_of_the_cache_identity():
    opener, calls = _counting_opener()

    sr.download(around=PAST, opener=opener)
    sr.download(around=PAST, view=(-103.0, -94.0, 33.5, 37.5), opener=opener)

    assert len(calls) == 2, "a narrowed request is a different question"


def test_a_failure_is_remembered_briefly_rather_than_retried_each_repaint():
    calls: list[str] = []

    def broken(url, timeout, limit):
        calls.append(url)
        raise OSError("connection reset")

    with pytest.raises(sr.StormReportsError):
        sr.download(opener=broken)
    with pytest.raises(sr.StormReportsError):
        sr.download(opener=broken)

    assert len(calls) == 1, "the second call must come from the failure cache"


def test_cancellation_downloads_nothing():
    def forbidden(*args, **kwargs):
        raise AssertionError("a cancelled download must not reach the network")

    assert sr.download(opener=forbidden, should_cancel=lambda: True) is None


def test_the_layer_reuses_a_cached_download():
    opener, calls = _counting_opener()

    sr.fetch_layer(opener=opener, span_deg=6.0)
    layer = sr.fetch_layer(opener=opener, span_deg=6.0)

    assert len(calls) == 1
    assert layer is not None and len(layer.shapes) == 3


def test_an_unwritable_cache_directory_does_not_break_the_overlay(tmp_path,
                                                                 monkeypatch):
    """Losing the disk copy costs a re-download, never the reports.

    The cache is an optimisation. A read-only profile, a full disk or a path the
    user cannot create must degrade to fetching every time, not to an overlay
    that refuses to draw.
    """
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    # Rooting the cache inside a regular file makes mkdir fail for real.
    monkeypatch.setenv("SHARPMOD_STORM_REPORTS_CACHE", str(blocker / "cache"))
    opener, calls = _counting_opener()

    first = sr.download(around=PAST, opener=opener)
    sr.clear_cache()
    second = sr.download(around=PAST, opener=opener)

    assert first is not None and second is not None
    assert first[0] == second[0]
    assert len(calls) == 2, "with no disk copy it simply asks again"

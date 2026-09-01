"""Coverage for time-aware SPC convective outlook resolution and decoding.

No test here touches the network: every fetch goes through an injected opener,
and the pre-archive path is asserted to need no opener at all.
"""

from __future__ import annotations

import json
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from sharpmod import spc_outlook as spc

UTC = timezone.utc

# Nested categories with a hole punched where the higher one sits, mirroring
# the real ``nolyr`` payload shape.
OUTER = [[-100.0, 35.0], [-90.0, 35.0], [-90.0, 45.0], [-100.0, 45.0],
         [-100.0, 35.0]]
INNER = [[-97.0, 38.0], [-93.0, 38.0], [-93.0, 42.0], [-97.0, 42.0],
         [-97.0, 38.0]]


def _payload(valid="202503311630", expire="202504011200",
             issue="202503311619"):
    return json.dumps({
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [OUTER, INNER]},
                "properties": {
                    "DN": 3, "LABEL": "MRGL", "LABEL2": "Marginal Risk",
                    "stroke": "#005500", "fill": "#66A366",
                    "VALID": valid, "EXPIRE": expire, "ISSUE": issue,
                },
            },
            {
                "type": "Feature",
                "geometry": {"type": "MultiPolygon", "coordinates": [[INNER]]},
                "properties": {
                    "DN": 8, "LABEL": "HIGH", "LABEL2": "High Risk",
                    "stroke": "#CC00CC", "fill": "#EE99EE",
                    "VALID": valid, "EXPIRE": expire, "ISSUE": issue,
                },
            },
        ],
    }).encode("utf-8")


@pytest.fixture(autouse=True)
def _clean_cache():
    spc.clear_cache()
    yield
    spc.clear_cache()


# --------------------------------------------------------------------------- #
# convective day
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("when,expected", [
    # Inside the day that began at 12Z.
    (datetime(2025, 5, 14, 18), datetime(2025, 5, 14, 12)),
    # Exactly on the boundary belongs to the day it starts.
    (datetime(2025, 5, 14, 12), datetime(2025, 5, 14, 12)),
    # One minute before belongs to the previous day.
    (datetime(2025, 5, 14, 11, 59), datetime(2025, 5, 13, 12)),
    # An overnight 06Z sounding belongs to the *previous* calendar day's
    # outlook; getting this wrong shifts the overlay by 24 hours.
    (datetime(2025, 5, 15, 6), datetime(2025, 5, 14, 12)),
    (datetime(2025, 5, 14, 0), datetime(2025, 5, 13, 12)),
])
def test_convective_day_start(when, expected):
    got = spc.convective_day_start(when.replace(tzinfo=UTC))
    assert got == expected.replace(tzinfo=UTC)


def test_convective_day_start_requires_tz_aware():
    with pytest.raises(ValueError, match="tz-aware"):
        spc.convective_day_start(datetime(2025, 5, 14, 18))


def test_convective_day_start_normalises_other_zones():
    eastern = timezone(timedelta(hours=-5))
    # 09:00 -05:00 is 14:00Z, inside the day that began at 12Z.
    got = spc.convective_day_start(
        datetime(2025, 5, 14, 9, tzinfo=eastern))
    assert got == datetime(2025, 5, 14, 12, tzinfo=UTC)


@pytest.mark.parametrize("hours,expected", [
    (0, 0), (12, 0), (24, 1), (48, 2), (-24, -1), (72, 3),
])
def test_outlook_day_offset(hours, expected):
    now = datetime(2025, 5, 14, 15, tzinfo=UTC)
    assert spc.outlook_day_offset(now + timedelta(hours=hours), now) == expected


# --------------------------------------------------------------------------- #
# candidate resolution
# --------------------------------------------------------------------------- #
NOW = datetime(2025, 5, 14, 15, tzinfo=UTC)


def test_pre_archive_dates_short_circuit():
    """ERA5 reaches back decades; probing 1998 would be nine dead requests."""
    assert spc.candidates_for(datetime(1998, 5, 20, 18, tzinfo=UTC), NOW) == ()


def test_beyond_day_three_has_no_candidates():
    assert spc.candidates_for(NOW + timedelta(days=6), NOW) == ()


def test_current_day_prefers_the_live_day1_endpoint():
    got = spc.candidates_for(datetime(2025, 5, 14, 18, tzinfo=UTC), NOW)
    assert got[0].live is True
    assert got[0].day == 1
    assert got[0].url.endswith("day1otlk_cat.nolyr.geojson")


def test_live_endpoint_only_offered_for_the_current_day():
    """Before 12Z the live Day 2/3 endpoints sit two convective days ahead."""
    for days in (1, 2):
        got = spc.candidates_for(NOW + timedelta(days=days), NOW)
        assert not any(c.live for c in got), f"day+{days} must not use live"


def test_unissued_products_are_not_requested():
    """Tomorrow's Day 1 does not exist yet, so Day 2 must lead."""
    got = spc.candidates_for(NOW + timedelta(days=1), NOW)
    assert got, "expected at least one candidate"
    assert 1 not in {c.day for c in got}
    assert got[0].day == 2
    assert all(c.issued <= NOW for c in got)


def test_the_outlook_in_force_at_the_target_hour_is_tried_first():
    """An 18Z sounding wants the 1630Z issuance, not the later 2000Z one."""
    got = [c for c in spc.candidates_for(
        datetime(2023, 3, 31, 18, tzinfo=UTC), NOW) if c.day == 1]
    assert got[0].issued == datetime(2023, 3, 31, 16, 30, tzinfo=UTC)

    before = [c.issued for c in got
              if c.issued <= datetime(2023, 3, 31, 18, tzinfo=UTC)]
    assert before == sorted(before, reverse=True), "newest applicable first"


def test_day1_0100z_update_wins_for_an_overnight_target():
    """The 01Z update is the freshest product for a 06Z sounding.

    It is filed under the next calendar date, so ordering by clock time would
    bury it behind the previous evening's 20Z outlook.
    """
    got = [c for c in spc.candidates_for(
        datetime(2023, 4, 1, 6, tzinfo=UTC), NOW) if c.day == 1]
    assert got[0].issued == datetime(2023, 4, 1, 1, 0, tzinfo=UTC)
    assert "day1otlk_20230401_0100_cat" in got[0].url


def test_lower_outlook_days_are_preferred():
    got = spc.candidates_for(datetime(2023, 3, 31, 18, tzinfo=UTC), NOW)
    days = [c.day for c in got]
    assert days == sorted(days), "day 1 must be exhausted before day 2"


def test_day1_0100z_issuance_is_filed_under_the_next_date():
    """The 01Z update still describes the day that began at the previous 12Z."""
    got = spc.candidates_for(datetime(2023, 3, 31, 18, tzinfo=UTC), NOW)
    urls = [c.url for c in got if c.issued
            and c.issued.hour == 1 and c.day == 1]
    assert len(urls) == 1
    assert "day1otlk_20230401_0100_cat" in urls[0]


def test_archive_url_shape():
    got = spc.candidates_for(datetime(2023, 3, 31, 18, tzinfo=UTC), NOW)
    archived = next(c for c in got if not c.live)
    assert archived.url == (
        "https://www.spc.noaa.gov/products/outlook/archive/2023/"
        "day1otlk_20230331_1630_cat.nolyr.geojson")


def test_candidate_count_is_bounded():
    got = spc.candidates_for(datetime(2023, 3, 31, 18, tzinfo=UTC), NOW)
    assert len(got) <= spc.MAX_CANDIDATES


def test_candidates_require_tz_aware():
    with pytest.raises(ValueError, match="tz-aware"):
        spc.candidates_for(datetime(2025, 5, 14, 18), NOW)


# --------------------------------------------------------------------------- #
# decoding
# --------------------------------------------------------------------------- #
def test_parse_reads_categories_colours_and_window():
    layer = spc.parse_outlook(_payload(), source_url="u", day=1,
                              label="Day 1 1630Z")
    assert layer.valid_from == datetime(2025, 3, 31, 16, 30, tzinfo=UTC)
    assert layer.valid_to == datetime(2025, 4, 1, 12, 0, tzinfo=UTC)
    assert layer.issued == datetime(2025, 3, 31, 16, 19, tzinfo=UTC)
    assert layer.attribution == spc.ATTRIBUTION
    assert layer.source_url == "u"
    assert [s.label for s in layer.shapes] == ["MRGL", "HIGH"]
    assert layer.shapes[0].stroke == "#005500"
    assert layer.shapes[1].fill == "#EE99EE"
    assert "valid" in layer.subtitle and "Day 1 1630Z" in layer.subtitle


def test_parse_orders_by_severity_not_payload_order():
    document = json.loads(_payload())
    document["features"].reverse()  # HIGH first
    layer = spc.parse_outlook(json.dumps(document))
    assert [s.label for s in layer.shapes] == ["MRGL", "HIGH"], \
        "a more severe area must paint last so its outline reads on top"


def test_parse_preserves_the_hole():
    layer = spc.parse_outlook(_payload())
    marginal = layer.shapes[0]
    assert len(marginal.rings) == 2, "the hole under HIGH must survive"


def test_parse_keeps_unknown_categories_above_known_ones():
    document = json.loads(_payload())
    document["features"][0]["properties"]["DN"] = 99
    document["features"][0]["properties"]["LABEL"] = "NEW"
    layer = spc.parse_outlook(json.dumps(document))
    labels = [s.label for s in layer.shapes]
    assert "NEW" in labels, "an unrecognised risk area must not be dropped"
    assert labels[-1] == "NEW"


def test_parse_falls_back_to_the_builtin_palette():
    document = json.loads(_payload())
    for feature in document["features"]:
        feature["properties"].pop("stroke")
        feature["properties"]["fill"] = "not-a-colour"
    layer = spc.parse_outlook(json.dumps(document))
    assert layer.shapes[0].stroke == spc.CATEGORIES[3].stroke
    assert layer.shapes[0].fill == spc.CATEGORIES[3].fill


@pytest.mark.parametrize("bad", [
    b"", b"not json", b"[]", b'{"type":"FeatureCollection"}', b"\xff\xfe",
])
def test_parse_rejects_malformed_payloads(bad):
    with pytest.raises(spc.OutlookError):
        spc.parse_outlook(bad)


def test_parse_survives_junk_features():
    document = json.loads(_payload())
    document["features"] = [
        None, 42, {}, {"geometry": None}, {"properties": {}},
    ] + document["features"]
    layer = spc.parse_outlook(json.dumps(document))
    assert [s.label for s in layer.shapes] == ["MRGL", "HIGH"]


@pytest.mark.parametrize("stamp", [
    "nope", "20250331", "2025033116300", "202513011200", 20250331, None,
])
def test_unparseable_timestamps_degrade_to_unbounded(stamp):
    """A layer with no usable window is drawn rather than discarded."""
    document = json.loads(_payload())
    for feature in document["features"]:
        feature["properties"]["VALID"] = stamp
        feature["properties"]["EXPIRE"] = stamp
    layer = spc.parse_outlook(json.dumps(document))
    assert layer.valid_from is None
    assert layer.valid_to is None
    assert layer.covers(datetime(2030, 1, 1, tzinfo=UTC))
    assert layer.shapes, "geometry must survive a bad timestamp"


def test_a_half_bounded_window_still_constrains():
    """Losing only VALID must not turn EXPIRE into open-ended coverage."""
    document = json.loads(_payload())
    for feature in document["features"]:
        feature["properties"]["VALID"] = "garbage"
    layer = spc.parse_outlook(json.dumps(document))
    assert layer.valid_from is None
    assert layer.valid_to == datetime(2025, 4, 1, 12, tzinfo=UTC)
    assert layer.covers(datetime(2025, 3, 31, 20, tzinfo=UTC))
    assert not layer.covers(datetime(2030, 1, 1, tzinfo=UTC))


# --------------------------------------------------------------------------- #
# fetching
# --------------------------------------------------------------------------- #
def _opener(responses, calls):
    """Return an opener serving ``responses`` by URL and recording calls."""
    def opener(url, timeout, limit):
        calls.append(url)
        result = responses.get(url)
        if result is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        if isinstance(result, Exception):
            raise result
        return result
    return opener


def test_fetch_returns_none_before_the_archive_without_any_request():
    calls: list[str] = []

    def explode(url, timeout, limit):
        raise AssertionError(f"unexpected request: {url}")

    assert spc.fetch_layer(datetime(1998, 6, 1, 18, tzinfo=UTC),
                           now=NOW, opener=explode) is None
    assert calls == []


def test_fetch_skips_a_product_whose_window_misses_the_target():
    """An 18Z sounding must get the 1630Z outlook, not the later 2000Z one."""
    target = datetime(2023, 3, 31, 18, tzinfo=UTC)
    base = ("https://www.spc.noaa.gov/products/outlook/archive/2023/"
            "day1otlk_20230331_%s_cat.nolyr.geojson")
    responses = {
        base % "2000": _payload(valid="202303312000", expire="202304011200"),
        base % "1630": _payload(valid="202303311630", expire="202304011200"),
    }
    calls: list[str] = []
    layer = spc.fetch_layer(target, now=NOW,
                            opener=_opener(responses, calls))
    assert layer is not None
    assert layer.covers(target)
    assert layer.valid_from == datetime(2023, 3, 31, 16, 30, tzinfo=UTC)
    # Resolved in a single request: the 1630Z issuance is the newest one made
    # before 18Z, so the 2000Z product is never requested at all.
    assert calls == [base % "1630"]


def test_fetch_caches_and_reuses_the_same_layer():
    target = datetime(2023, 3, 31, 18, tzinfo=UTC)
    url = ("https://www.spc.noaa.gov/products/outlook/archive/2023/"
           "day1otlk_20230331_1630_cat.nolyr.geojson")
    calls: list[str] = []
    responses = {url: _payload(valid="202303311630", expire="202304011200")}
    first = spc.fetch_layer(target, now=NOW, opener=_opener(responses, calls))
    assert first is not None
    assert len(calls) == 1

    def explode(u, timeout, limit):
        raise AssertionError(f"cache miss: {u}")

    second = spc.fetch_layer(target, now=NOW, opener=explode)
    assert second is first, "the decoded layer should be reused as-is"


def test_404_is_remembered_so_a_quiet_period_is_not_re_probed():
    target = datetime(2023, 3, 31, 18, tzinfo=UTC)
    calls: list[str] = []
    assert spc.fetch_layer(target, now=NOW,
                           opener=_opener({}, calls)) is None
    first_round = len(calls)
    assert first_round > 0

    calls.clear()
    assert spc.fetch_layer(target, now=NOW,
                           opener=_opener({}, calls)) is None
    assert calls == [], "every miss was already cached"
    assert first_round <= spc.MAX_CANDIDATES


def test_a_barren_day_is_only_walked_once():
    """Revisiting a date with no outlook on file must cost nothing.

    Guards a regression that made scrubbing expensive: the verdict used to live
    only in per-URL tombstones, so a range of barren dates overflowed the cache
    and every pass over it re-probed SPC for all eleven candidates.
    """
    target = datetime(2023, 3, 31, 18, tzinfo=UTC)
    calls: list[str] = []
    assert spc.fetch_layer(target, now=NOW,
                           opener=_opener({}, calls)) is None
    assert len(calls) > 1, "sanity: the first walk tries several candidates"

    def explode(url, timeout, limit):
        raise AssertionError(f"re-probed a known-barren day: {url}")

    assert spc.fetch_layer(target, now=NOW, opener=explode) is None
    # Any other hour of the same convective day shares the candidate set.
    assert spc.fetch_layer(datetime(2023, 3, 31, 21, tzinfo=UTC),
                           now=NOW, opener=explode) is None


def test_a_long_barren_scrub_does_not_evict_itself():
    """A month of barren dates must still be remembered on the way back."""
    days = [datetime(2023, 3, 1, 18, tzinfo=UTC) + timedelta(days=d)
            for d in range(30)]
    calls: list[str] = []
    for target in days:
        assert spc.fetch_layer(target, now=NOW,
                              opener=_opener({}, calls)) is None
    assert calls, "sanity: the first pass does request"

    def explode(url, timeout, limit):
        raise AssertionError(f"cache was evicted: {url}")

    for target in days:
        assert spc.fetch_layer(target, now=NOW, opener=explode) is None


def test_a_transient_failure_is_not_recorded_as_a_settled_verdict():
    """A network outage must not suppress retries for hours."""
    target = datetime(2023, 3, 31, 18, tzinfo=UTC)
    urls = [c.url for c in spc.candidates_for(target, NOW)]
    offline = dict.fromkeys(urls, urllib.error.URLError("offline"))
    calls: list[str] = []
    assert spc.fetch_layer(target, now=NOW,
                           opener=_opener(offline, calls)) is None

    # Now the network is back and the outlook resolves on a later attempt.
    recovered = {urls[0]: _payload(valid="202303311630",
                                   expire="202304011200")}
    retry: list[str] = []
    layer = spc.fetch_layer(target, now=NOW,
                            opener=_opener(recovered, retry))
    assert layer is not None, "a transient error must not become permanent"
    assert retry, "the retry must actually reach the network"


def test_server_errors_are_also_treated_as_transient():
    target = datetime(2023, 3, 31, 18, tzinfo=UTC)
    urls = [c.url for c in spc.candidates_for(target, NOW)]
    broken = dict.fromkeys(
        urls, urllib.error.HTTPError(urls[0], 503, "Unavailable", None, None))
    calls: list[str] = []
    assert spc.fetch_layer(target, now=NOW,
                           opener=_opener(broken, calls)) is None

    recovered = {urls[0]: _payload(valid="202303311630",
                                   expire="202304011200")}
    retry: list[str] = []
    assert spc.fetch_layer(target, now=NOW,
                           opener=_opener(recovered, retry)) is not None


def test_clear_cache_resets_both_stores():
    target = datetime(2023, 3, 31, 18, tzinfo=UTC)
    calls: list[str] = []
    spc.fetch_layer(target, now=NOW, opener=_opener({}, calls))
    assert spc._CACHE or spc._DAY_MISS
    spc.clear_cache()
    assert not spc._CACHE
    assert not spc._DAY_MISS


def test_a_new_issuance_reopens_a_barren_verdict():
    """The verdict is keyed by the candidate set, so it self-invalidates."""
    target = datetime(2025, 5, 14, 18, tzinfo=UTC)
    early = datetime(2025, 5, 14, 13, 5, tzinfo=UTC)
    calls: list[str] = []
    assert spc.fetch_layer(target, now=early,
                           opener=_opener({}, calls)) is None

    # Later in the day SPC has issued the 1630Z update, so the candidate set
    # differs and the question must be asked again without any TTL expiring.
    later = datetime(2025, 5, 14, 17, tzinfo=UTC)
    url = ("https://www.spc.noaa.gov/products/outlook/archive/2025/"
           "day1otlk_20250514_1630_cat.nolyr.geojson")
    retry: list[str] = []
    layer = spc.fetch_layer(
        target, now=later,
        opener=_opener({url: _payload(valid="202505141630",
                                      expire="202505151200")}, retry))
    assert layer is not None
    assert url in retry


def test_transport_errors_do_not_propagate():
    target = datetime(2023, 3, 31, 18, tzinfo=UTC)
    calls: list[str] = []
    responses = dict.fromkeys(
        [c.url for c in spc.candidates_for(target, NOW)],
        urllib.error.URLError("offline"),
    )
    assert spc.fetch_layer(target, now=NOW,
                           opener=_opener(responses, calls)) is None


def test_oversized_response_is_refused():
    target = datetime(2023, 3, 31, 18, tzinfo=UTC)

    def flood(url, timeout, limit):
        return b"x" * (limit + 1)

    # Every candidate over-runs the cap, so the result is simply "no outlook"
    # rather than a decoded giant payload or an escaping exception.
    assert spc.fetch_layer(target, now=NOW, opener=flood) is None


def test_cancellation_stops_the_walk():
    target = datetime(2023, 3, 31, 18, tzinfo=UTC)
    calls: list[str] = []
    assert spc.fetch_layer(
        target, now=NOW, opener=_opener({}, calls),
        should_cancel=lambda: True) is None
    assert calls == []


def test_every_known_category_is_ordered_by_severity():
    ranks = [spc.CATEGORIES[dn].rank for dn in sorted(spc.CATEGORIES)]
    assert ranks == sorted(ranks)
    assert spc.CATEGORIES[8].code == "HIGH", "DN 8 is HIGH; DN 7 is unused"
    assert 7 not in spc.CATEGORIES


# --------------------------------------------------------------------------- #
# hazard products
# --------------------------------------------------------------------------- #
def _prob_payload(bands, valid="202303311630", expire="202304011200"):
    """Build a probabilistic payload from ``(DN, LABEL, stroke, fill)`` rows."""
    features = []
    for dn, label, stroke, fill in bands:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [OUTER]},
            "properties": {
                "DN": dn, "LABEL": label, "LABEL2": label,
                "stroke": stroke, "fill": fill,
                "VALID": valid, "EXPIRE": expire, "ISSUE": "202303311619",
            },
        })
    return json.dumps({"type": "FeatureCollection",
                       "features": features}).encode()


#: The real tornado bands, including the DN=10 collision between the 10%
#: probability and the significant-severe area.
TORNADO_BANDS = [
    (2, "0.02", "#005500", "#66A366"),
    (5, "0.05", "#70380f", "#9d4e15"),
    (10, "0.10", "#DDAA00", "#FFE066"),
    (15, "0.15", "#CC0000", "#E06666"),
    (10, "SIGN", "#000000", "#888888"),
]


def test_products_declare_the_days_that_publish_them():
    assert spc.PRODUCTS["cat"].days == (1, 2, 3)
    for key in ("torn", "wind", "hail"):
        assert spc.PRODUCTS[key].days == (1, 2), \
            f"{key} probabilities are not issued for Day 3"
        assert spc.PRODUCTS[key].probabilistic


def test_hazard_products_are_never_requested_for_day_three():
    """Requesting them returns 404 for every Day 3 issuance."""
    day3 = NOW + timedelta(days=2)
    assert 3 in {c.day for c in spc.candidates_for(day3, NOW, "cat")}
    for key in ("torn", "wind", "hail"):
        days = {c.day for c in spc.candidates_for(day3, NOW, key)}
        assert 3 not in days, f"{key} must not reach Day 3"


def test_product_appears_in_the_url():
    for key in spc.PRODUCTS:
        got = spc.candidates_for(datetime(2023, 3, 31, 18, tzinfo=UTC),
                                 NOW, key)
        assert got, f"no candidates for {key}"
        assert all(f"_{key}.nolyr.geojson" in c.url for c in got)


def test_unknown_product_falls_back_to_categorical():
    assert spc.resolve_product("nope").key == "cat"
    assert spc.resolve_product(None).key == "cat"


def test_tornado_dn_collision_is_split_by_label():
    """``DN=10`` is both the 10% band and the significant-severe area."""
    layer = spc.parse_outlook(_prob_payload(TORNADO_BANDS), product="torn")
    by_label = {s.label: s for s in layer.shapes}
    assert {"10%", "SIGN"} <= set(by_label), \
        "both DN=10 features must survive as distinct bands"
    assert by_label["10%"].rank != by_label["SIGN"].rank
    assert by_label["10%"].hatch is False
    assert by_label["SIGN"].hatch is True


def test_probability_bands_order_by_probability():
    layer = spc.parse_outlook(_prob_payload(TORNADO_BANDS), product="torn")
    labels = [s.label for s in layer.shapes]
    assert labels == ["2%", "5%", "10%", "15%", "SIGN"]


def test_probability_labels_read_as_quantities():
    """A bare ``0.05`` reads as an identifier rather than a probability."""
    layer = spc.parse_outlook(_prob_payload(TORNADO_BANDS), product="torn")
    assert not any("0." in shape.label for shape in layer.shapes)


def test_categorical_labels_are_left_alone():
    layer = spc.parse_outlook(_payload(), product="cat")
    assert [shape.label for shape in layer.shapes] == ["MRGL", "HIGH"]


def test_the_layer_carries_a_compact_hazard_name():
    """A probability label alone cannot say which hazard it measures."""
    for key, expected in (
        ("cat", ""), ("torn", "TOR"), ("wind", "WIND"), ("hail", "HAIL"),
    ):
        payload = _payload() if key == "cat" else _prob_payload(TORNADO_BANDS)
        layer = spc.parse_outlook(payload, product=key)
        assert layer.short_name == expected


def test_significant_severe_sorts_above_every_band():
    """It annotates the band beneath it, so it must be painted last."""
    layer = spc.parse_outlook(_prob_payload(TORNADO_BANDS), product="torn")
    assert layer.shapes[-1].label == "SIGN"
    assert layer.shapes[-1].rank > max(
        s.rank for s in layer.shapes if s.label != "SIGN")


def test_only_significant_severe_is_hatched():
    layer = spc.parse_outlook(_prob_payload(TORNADO_BANDS), product="torn")
    assert [s.label for s in layer.shapes if s.hatch] == ["SIGN"]


def test_categorical_areas_are_never_hatched():
    layer = spc.parse_outlook(_payload(), product="cat")
    assert not any(s.hatch for s in layer.shapes)


def test_probability_descriptions_are_human_readable():
    layer = spc.parse_outlook(
        _prob_payload([(5, "0.05", "#70380f", "#9d4e15")]), product="wind")
    # LABEL2 is echoed by the fixture, so check the derived fallback instead.
    document = json.loads(_prob_payload([(5, "0.05", "#70380f", "#9d4e15")]))
    document["features"][0]["properties"].pop("LABEL2")
    derived = spc.parse_outlook(json.dumps(document), product="wind")
    assert derived.shapes[0].description == "5% probability"
    assert layer.shapes[0].label == "5%"


def test_the_product_name_reaches_the_layer_title():
    layer = spc.parse_outlook(_prob_payload(TORNADO_BANDS), day=1,
                              product="torn")
    assert "tornado probability" in layer.title.lower()


def test_wind_and_hail_share_the_probability_scale():
    assert spc._PRODUCT_PALETTES["hail"] is spc._PRODUCT_PALETTES["wind"]


def test_the_same_probability_differs_by_product():
    """0.15 is amber for wind but red for tornado, so DN cannot drive colour."""
    assert spc._PRODUCT_PALETTES["torn"]["0.15"] != \
        spc._PRODUCT_PALETTES["wind"]["0.15"]


@pytest.mark.parametrize("label", [None, "", 0])
def test_the_empty_product_sentinel_is_skipped(label):
    """An unpopulated product is a 280-byte DN=0 placeholder, not a 404."""
    document = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": None,
            "properties": {"DN": 0, "LABEL": label},
        }],
    }
    layer = spc.parse_outlook(json.dumps(document), product="torn")
    assert not layer, "a placeholder must not become a drawable shape"
    assert layer.shapes == ()


# --------------------------------------------------------------------------- #
# on-disk cache
# --------------------------------------------------------------------------- #
@pytest.fixture
def disk_cache(tmp_path, monkeypatch):
    """Point the persistent cache at a temporary directory."""
    monkeypatch.setenv("SHARPMOD_OUTLOOK_CACHE", str(tmp_path))
    spc.clear_cache()
    yield tmp_path


def test_disk_cache_can_be_disabled(monkeypatch):
    for value in ("off", "0", "none", "false", "OFF"):
        monkeypatch.setenv("SHARPMOD_OUTLOOK_CACHE", value)
        assert spc.disk_cache_root() is None


def test_disk_cache_honours_an_explicit_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARPMOD_OUTLOOK_CACHE", str(tmp_path))
    assert spc.disk_cache_root() == tmp_path


def test_an_archived_issuance_survives_a_cleared_memory_cache(disk_cache):
    target = datetime(2023, 3, 31, 18, tzinfo=UTC)
    url = ("https://www.spc.noaa.gov/products/outlook/archive/2023/"
           "day1otlk_20230331_1630_cat.nolyr.geojson")
    responses = {url: _payload(valid="202303311630", expire="202304011200")}
    calls: list[str] = []
    assert spc.fetch_layer(target, now=NOW,
                           opener=_opener(responses, calls)) is not None
    assert list(disk_cache.rglob("*.json")), "nothing was persisted"

    # Simulate a restart: memory is empty but the file is still on disk.
    spc.clear_cache()

    def explode(u, timeout, limit):
        raise AssertionError(f"disk cache missed: {u}")

    layer = spc.fetch_layer(target, now=NOW, opener=explode)
    assert layer is not None
    assert layer.covers(target)


def test_the_live_endpoint_is_never_persisted(disk_cache):
    """It advances through the day, so a stored copy would go stale."""
    target = NOW
    live = [c for c in spc.candidates_for(target, NOW) if c.live]
    assert live, "sanity: the current day offers a live candidate"
    responses = {live[0].url: _payload(
        valid=f"{target:%Y%m%d}0000", expire=f"{target + timedelta(days=1):%Y%m%d}0000")}
    calls: list[str] = []
    assert spc.fetch_layer(target, now=NOW,
                           opener=_opener(responses, calls)) is not None
    assert not list(disk_cache.rglob("*.json")), \
        "the live endpoint must not be written to disk"


def test_a_404_is_never_persisted(disk_cache):
    """A product missing now may be issued later, so it must stay requestable."""
    target = datetime(2023, 3, 31, 18, tzinfo=UTC)
    calls: list[str] = []
    assert spc.fetch_layer(target, now=NOW,
                           opener=_opener({}, calls)) is None
    assert not list(disk_cache.rglob("*.json"))


def test_a_corrupt_disk_entry_falls_back_to_the_network(disk_cache):
    target = datetime(2023, 3, 31, 18, tzinfo=UTC)
    url = ("https://www.spc.noaa.gov/products/outlook/archive/2023/"
           "day1otlk_20230331_1630_cat.nolyr.geojson")
    payload = _payload(valid="202303311630", expire="202304011200")
    assert spc.fetch_layer(target, now=NOW,
                           opener=_opener({url: payload}, [])) is not None

    for path in disk_cache.rglob("*.json"):
        path.write_bytes(b"{ this is not json")
    spc.clear_cache()

    calls: list[str] = []
    layer = spc.fetch_layer(target, now=NOW,
                            opener=_opener({url: payload}, calls))
    assert layer is not None, "a damaged cache file must not break the overlay"
    assert calls, "it should have gone back to the network"


def test_an_unwritable_cache_directory_is_survivable(monkeypatch, tmp_path):
    """Disk problems must degrade to in-memory behaviour, not raise."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    monkeypatch.setenv("SHARPMOD_OUTLOOK_CACHE", str(blocker / "nested"))
    spc.clear_cache()

    target = datetime(2023, 3, 31, 18, tzinfo=UTC)
    url = ("https://www.spc.noaa.gov/products/outlook/archive/2023/"
           "day1otlk_20230331_1630_cat.nolyr.geojson")
    responses = {url: _payload(valid="202303311630", expire="202304011200")}
    assert spc.fetch_layer(target, now=NOW,
                           opener=_opener(responses, [])) is not None


def test_products_do_not_collide_on_disk(disk_cache):
    """Each product has its own URL, so each gets its own cache entry."""
    target = datetime(2023, 3, 31, 18, tzinfo=UTC)
    written = set()
    for key in ("cat", "torn"):
        url = next(c.url for c in spc.candidates_for(target, NOW, key)
                   if not c.live)
        body = (_payload(valid="202303311630", expire="202304011200")
                if key == "cat"
                else _prob_payload(TORNADO_BANDS))
        assert spc.fetch_layer(target, now=NOW, product=key,
                               opener=_opener({url: body}, [])) is not None
        written = {p.name for p in disk_cache.rglob("*.json")}
    assert len(written) == 2, f"expected one file per product, got {written}"


def test_clear_disk_cache_empties_the_directory(disk_cache):
    target = datetime(2023, 3, 31, 18, tzinfo=UTC)
    url = ("https://www.spc.noaa.gov/products/outlook/archive/2023/"
           "day1otlk_20230331_1630_cat.nolyr.geojson")
    spc.fetch_layer(target, now=NOW, opener=_opener(
        {url: _payload(valid="202303311630", expire="202304011200")}, []))
    assert list(disk_cache.rglob("*.json"))
    spc.clear_disk_cache()
    assert not list(disk_cache.rglob("*.json"))


def test_the_size_sweep_evicts_oldest_first(disk_cache, monkeypatch):
    monkeypatch.setenv("SHARPMOD_OUTLOOK_CACHE_MB", "0.001")  # ~1 KB
    root = spc.disk_cache_root()
    for index in range(6):
        spc._disk_write(f"https://example.invalid/{index}", b"x" * 400)
    spc._disk_sweep()
    remaining = list(root.rglob("*.json"))
    total = sum(p.stat().st_size for p in remaining)
    assert total <= 1024 + 400, f"sweep left {total} bytes"
    assert remaining, "the sweep should not empty the cache entirely"


# --------------------------------------------------------------------------- #
# supersession
# --------------------------------------------------------------------------- #
#: A 21Z sounding on 20 May 2026; its convective day starts 20 May 12Z.
SUPERSEDE_TARGET = datetime(2026, 5, 20, 21, tzinfo=UTC)


def test_the_best_outlook_day_improves_as_time_passes():
    """Day 3, then Day 2, then Day 1, for one unchanged target time."""
    progression = []
    for now in (
        datetime(2026, 5, 18, 15, tzinfo=UTC),
        datetime(2026, 5, 19, 9, tzinfo=UTC),
        datetime(2026, 5, 20, 13, tzinfo=UTC),
    ):
        got = spc.candidates_for(SUPERSEDE_TARGET, now)
        progression.append(got[0].day if got else None)
    assert progression == [3, 2, 1]


def test_a_hazard_absent_on_day_three_becomes_available_on_day_two():
    early = datetime(2026, 5, 18, 15, tzinfo=UTC)
    later = datetime(2026, 5, 19, 9, tzinfo=UTC)
    assert spc.candidates_for(SUPERSEDE_TARGET, early, "torn") == ()
    assert spc.candidates_for(SUPERSEDE_TARGET, later, "torn")


def test_the_signature_changes_when_a_better_outlook_exists():
    """This is what tells a caller an overlay on screen is now stale."""
    signatures = [
        spc.resolution_signature(SUPERSEDE_TARGET, now)
        for now in (
            datetime(2026, 5, 18, 15, tzinfo=UTC),
            datetime(2026, 5, 19, 9, tzinfo=UTC),
            datetime(2026, 5, 20, 13, tzinfo=UTC),
        )
    ]
    assert len(set(signatures)) == 3, "each step must be distinguishable"
    # Products only accumulate, so each basis contains the one before it.
    assert set(signatures[0]) < set(signatures[1]) < set(signatures[2])


def test_the_signature_changes_on_a_new_issuance_within_a_day():
    before = spc.resolution_signature(
        SUPERSEDE_TARGET, datetime(2026, 5, 20, 16, tzinfo=UTC))
    after = spc.resolution_signature(
        SUPERSEDE_TARGET, datetime(2026, 5, 20, 17, tzinfo=UTC))
    assert after != before, "the 1630Z update must be noticed"


def test_the_signature_is_stable_between_issuances():
    """Otherwise the periodic recheck would refetch for no reason."""
    first = spc.resolution_signature(
        SUPERSEDE_TARGET, datetime(2026, 5, 20, 14, tzinfo=UTC))
    second = spc.resolution_signature(
        SUPERSEDE_TARGET, datetime(2026, 5, 20, 15, 59, tzinfo=UTC))
    assert first == second


def test_the_signature_distinguishes_products():
    now = datetime(2026, 5, 20, 13, tzinfo=UTC)
    signatures = {
        key: spc.resolution_signature(SUPERSEDE_TARGET, now, key)
        for key in spc.PRODUCTS
    }
    assert len(set(signatures.values())) == len(spc.PRODUCTS)


def test_the_signature_is_empty_when_nothing_can_answer():
    assert spc.resolution_signature(
        datetime(1998, 6, 1, 18, tzinfo=UTC), NOW) == ()
    assert spc.resolution_signature(
        SUPERSEDE_TARGET, datetime(2026, 5, 18, 15, tzinfo=UTC), "torn") == ()


def test_the_signature_performs_no_io():
    """It is called on every interaction, so it must stay pure."""
    def explode(*args, **kwargs):
        raise AssertionError("resolution_signature attempted network access")

    original = spc._default_opener
    spc._default_opener = explode
    try:
        assert spc.resolution_signature(SUPERSEDE_TARGET, NOW) is not None
    finally:
        spc._default_opener = original


def test_a_stale_day_three_still_covers_the_target():
    """Why coverage alone cannot decide whether to refetch.

    The Day 3 outlook's validity window still contains the target time long
    after the Day 1 for the same convective day has been issued, so a check
    based only on ``covers`` would pin the stale product on screen.
    """
    day3 = spc.parse_outlook(
        _payload(valid="202605201200", expire="202605211200"), day=3)
    assert day3.covers(SUPERSEDE_TARGET)
    later = datetime(2026, 5, 20, 13, tzinfo=UTC)
    assert spc.candidates_for(SUPERSEDE_TARGET, later)[0].day == 1


# --------------------------------------------------------------------------- #
# the issuance in force at a given hour
# --------------------------------------------------------------------------- #
#: A HRRR run whose forecast hours span a whole convective day and cross the
#: 12Z boundary into the next one.
RUN_15_APR = datetime(2026, 4, 15, 0, tzinfo=UTC)
LATE_2026 = datetime(2026, 8, 29, 12, tzinfo=UTC)


def _best_issuance(valid_time):
    got = spc.candidates_for(valid_time, LATE_2026)
    return None if not got else got[0].issued


@pytest.mark.parametrize("fxx,expected", [
    # Within the 15 Apr 12Z - 16 Apr 12Z convective day, the issuance in force
    # is the newest one at or before the valid hour.
    (12, datetime(2026, 4, 15, 12, tzinfo=UTC)),
    (14, datetime(2026, 4, 15, 13, tzinfo=UTC)),
    (17, datetime(2026, 4, 15, 16, 30, tzinfo=UTC)),
    (18, datetime(2026, 4, 15, 16, 30, tzinfo=UTC)),
    (21, datetime(2026, 4, 15, 20, tzinfo=UTC)),
    # 00Z the next morning still belongs to the 15 Apr convective day, and the
    # 2000Z issuance is the one in force -- not the 1630Z one.
    (24, datetime(2026, 4, 15, 20, tzinfo=UTC)),
    # The 01Z update, filed under the following calendar date.
    (26, datetime(2026, 4, 16, 1, tzinfo=UTC)),
    (35, datetime(2026, 4, 16, 1, tzinfo=UTC)),
    # 12Z starts a new convective day, so the new day's first issuance applies.
    (36, datetime(2026, 4, 16, 12, tzinfo=UTC)),
])
def test_the_issuance_in_force_is_chosen_for_each_forecast_hour(fxx, expected):
    valid = RUN_15_APR + timedelta(hours=fxx)
    assert _best_issuance(valid) == expected


def test_the_twelve_z_boundary_starts_the_next_convective_day():
    """F036 of an 00Z run lands exactly on the boundary."""
    valid = RUN_15_APR + timedelta(hours=36)
    assert valid == datetime(2026, 4, 16, 12, tzinfo=UTC)
    assert spc.convective_day_start(valid) == \
        datetime(2026, 4, 16, 12, tzinfo=UTC)
    first = spc.candidates_for(valid, LATE_2026)[0]
    assert "day1otlk_20260416_1200" in first.url


def test_hours_under_one_issuance_share_a_signature():
    """Otherwise stepping forecast hours would refetch for no reason."""
    signatures = {
        spc.resolution_signature(RUN_15_APR + timedelta(hours=h), LATE_2026)
        for h in (20, 21, 22, 24)
    }
    assert len(signatures) == 1


@pytest.mark.parametrize("earlier,later", [
    (16, 18),   # crosses the 1630Z issuance
    (18, 24),   # crosses the 2000Z issuance -- the reported regression
    (24, 26),   # crosses the 0100Z update
    (35, 36),   # crosses into the next convective day
])
def test_crossing_an_issuance_changes_the_signature(earlier, later):
    before = spc.resolution_signature(
        RUN_15_APR + timedelta(hours=earlier), LATE_2026)
    after = spc.resolution_signature(
        RUN_15_APR + timedelta(hours=later), LATE_2026)
    assert before != after


def test_a_superseded_issuance_still_covers_the_target():
    """Why coverage cannot decide which issuance to use.

    Every issuance of a convective day expires at the same 12Z, so an earlier
    one still contains a later hour and would look perfectly valid.
    """
    early = spc.parse_outlook(
        _payload(valid="202604151630", expire="202604161200"))
    target = datetime(2026, 4, 16, 0, tzinfo=UTC)
    assert early.covers(target), "the 1630Z outlook does still cover 00Z"
    assert _best_issuance(target) == datetime(2026, 4, 15, 20, tzinfo=UTC), \
        "but the 2000Z issuance is the one in force"


def test_the_issuance_label_cannot_be_read_as_a_valid_time():
    """``Day 1 1630Z`` beside a validity window read as a valid time."""
    got = spc.candidates_for(
        datetime(2026, 4, 16, 0, tzinfo=UTC), LATE_2026)
    archived = next(c for c in got if not c.live)
    assert "issuance" in archived.label
    layer = spc.parse_outlook(
        _payload(valid="202604152000", expire="202604161200"),
        label=archived.label)
    assert "issuance" in layer.subtitle
    assert "valid" in layer.subtitle


# --------------------------------------------------------------------------- #
# coverage area
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("lat,lon,covered", [
    (35.2, -97.4, True),      # Norman, OK
    (25.8, -80.2, True),      # Miami
    (47.6, -122.3, True),     # Seattle
    (13.5, 144.8, False),     # Guam
    (52.0, 13.4, False),      # Berlin
    (64.8, -147.7, False),    # Fairbanks
    (21.3, -157.9, False),    # Honolulu
])
def test_covers_location(lat, lon, covered):
    """Avoids requesting a CONUS product for a sounding on another continent."""
    assert spc.covers_location(lat, lon) is covered


@pytest.mark.parametrize("lat,lon", [
    (None, None), ("x", "y"), (float("nan"), 0.0), (0.0, float("inf")),
])
def test_an_unusable_coordinate_still_attempts_the_fetch(lat, lon):
    """Better to try and find nothing than to silently suppress the overlay."""
    assert spc.covers_location(lat, lon) is True


def test_longitudes_are_normalised_before_the_coverage_test():
    # 262.6 degrees east is the same meridian as -97.4.
    assert spc.covers_location(35.2, 262.6) is True

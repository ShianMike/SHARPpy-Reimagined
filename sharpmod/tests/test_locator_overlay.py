"""Which overlays the sounding locator may carry, and which displace others."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sharpmod import locator_overlay as lo

UTC = timezone.utc
NOW = datetime(2026, 5, 1, 18, 0, tzinfo=UTC)
NORMAN = (35.18, -97.44)


class _Collection:
    """The two metadata operations the transport seam needs."""

    def __init__(self):
        self._meta: dict = {}

    def getMeta(self, key):  # noqa: N802 - SHARPpy's spelling
        return self._meta.get(key)

    def setMeta(self, key, value):  # noqa: N802 - SHARPpy's spelling
        self._meta[key] = value


@pytest.fixture(autouse=True)
def _isolated_report_cache(tmp_path, monkeypatch):
    """Keep the report download cache from leaking between these tests.

    It is module-level and deliberately survives a repaint, so without this a
    test asserting that a request happens can be answered by the request an
    earlier test made.
    """
    from sharpmod import storm_reports

    monkeypatch.setenv("SHARPMOD_STORM_REPORTS_CACHE", str(tmp_path / "reports"))
    storm_reports.clear_cache()
    yield
    storm_reports.clear_cache()


def _raster(key):
    return SimpleNamespace(key=key, opacity=0.8)


def _attached_keys(collection) -> set[str]:
    """Read the transport list itself.

    The typed accessors filter by ``isinstance``, which a stand-in overlay would
    fail; what these tests check is the attach and detach bookkeeping, so they
    look at what was actually stored.
    """
    from sharpmod import map_overlays

    stored = collection.getMeta(map_overlays.LOCATOR_OVERLAY_META_KEY) or ()
    return {entry.key for entry in stored}


# --------------------------------------------------------------------------- #
# keys
# --------------------------------------------------------------------------- #
def test_every_family_key_matches_its_provider_module():
    """The table is written out to keep start-up light; this stops it rotting.

    ``reports`` is absent on purpose: its key is a forward declaration, made now
    so the dependency on the outlook is defined in one place, and there is no
    ``sharpmod.storm_reports`` to compare against until the provider lands.
    """
    from sharpmod import hrrr_field, radar_mosaic, radar_site, spc_outlook

    provided = {
        lo.FAMILY_RISK: spc_outlook.OVERLAY_KEY,
        lo.FAMILY_HRRR: hrrr_field.OVERLAY_KEY,
        lo.FAMILY_RADAR_SITE: radar_site.OVERLAY_KEY,
        lo.FAMILY_RADAR_MOSAIC: radar_mosaic.OVERLAY_KEY,
    }
    assert {family: lo.OVERLAY_KEYS[family] for family in provided} == provided
    assert set(lo.OVERLAY_KEYS) - set(provided) == {lo.FAMILY_REPORTS}


def test_the_pending_family_has_a_distinct_key():
    """A key collision would make reports replace the overlay it depends on."""
    assert lo.OVERLAY_KEYS[lo.FAMILY_REPORTS] not in {
        key for family, key in lo.OVERLAY_KEYS.items()
        if family != lo.FAMILY_REPORTS
    }


def test_selecting_an_unknown_family_is_refused():
    with pytest.raises(lo.LocatorOverlayError, match="unknown locator overlay"):
        lo.Selection("satellite")


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("text", [None, "", "   ", "none", "NONE"])
def test_nothing_selected_means_a_bare_locator(text):
    """The default must not reach for the network before being asked."""
    assert lo.parse(text) == ()


def test_a_family_and_its_product_round_trip():
    (selection,) = lo.parse("hrrr:refc")

    assert (selection.family, selection.product) == (lo.FAMILY_HRRR, "refc")
    assert selection.spec() == "hrrr:refc"
    assert lo.parse(selection.spec()) == (selection,)


def test_a_family_without_a_product_defers_to_the_provider():
    (selection,) = lo.parse("risk")

    assert selection.product is None
    assert selection.spec() == "risk"


# --------------------------------------------------------------------------- #
# exclusivity
# --------------------------------------------------------------------------- #
def test_risk_and_a_model_field_are_shown_together():
    selections = lo.parse("risk:torn,hrrr:stp")

    assert [item.family for item in selections] == [
        lo.FAMILY_RISK, lo.FAMILY_HRRR]


def test_radar_displaces_the_composable_overlays():
    """Radar over a risk polygon buries the polygon."""
    selections = lo.parse("risk,hrrr,radar-site")

    assert selections == (lo.Selection(lo.FAMILY_RADAR_SITE),)


def test_only_one_radar_scope_survives():
    """Two reflectivity ramps over one storm is not twice the information."""
    selections = lo.parse("radar-mosaic,radar-site")

    assert selections == (lo.Selection(lo.FAMILY_RADAR_MOSAIC),)


def test_one_slot_per_family():
    selections = lo.parse("hrrr:refc,hrrr:stp")

    assert selections == (lo.Selection(lo.FAMILY_HRRR, "refc"),)


# --------------------------------------------------------------------------- #
# radar is a nowcast
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("offset", [
    timedelta(0), timedelta(minutes=59), timedelta(minutes=-59),
])
def test_radar_is_offered_for_a_current_sounding(offset):
    resolved = lo.resolve(lo.parse("radar-mosaic"), lat=NORMAN[0],
                          lon=NORMAN[1], valid_time=NOW + offset, now=NOW)

    assert resolved == (lo.Selection(lo.FAMILY_RADAR_MOSAIC),)


@pytest.mark.parametrize("offset", [
    timedelta(hours=3), timedelta(days=-2), timedelta(hours=24),
])
def test_radar_is_withheld_from_a_sounding_it_cannot_describe(offset):
    """There is no archive behind these frames, so silence beats false currency.

    An hour is also the window the inset's own raster filter enforces, so a
    frame accepted here is one that will actually be drawn.
    """
    resolved = lo.resolve(lo.parse("radar-site"), lat=NORMAN[0], lon=NORMAN[1],
                          valid_time=NOW + offset, now=NOW)

    assert resolved == ()


def test_a_naive_valid_time_is_read_as_utc():
    resolved = lo.resolve(lo.parse("radar-mosaic"), lat=NORMAN[0],
                          lon=NORMAN[1],
                          valid_time=NOW.replace(tzinfo=None), now=NOW)

    assert resolved == (lo.Selection(lo.FAMILY_RADAR_MOSAIC),)


def test_the_nowcast_rule_does_not_touch_the_composable_overlays():
    """A forecast field and a risk area are addressed by time; radar is not."""
    resolved = lo.resolve(lo.parse("risk,hrrr"), lat=NORMAN[0], lon=NORMAN[1],
                          valid_time=NOW + timedelta(days=2), now=NOW)

    assert [item.family for item in resolved] == [
        lo.FAMILY_RISK, lo.FAMILY_HRRR]


# --------------------------------------------------------------------------- #
# site versus mosaic
# --------------------------------------------------------------------------- #
def test_a_point_sounding_gets_the_nearest_single_site(monkeypatch):
    from sharpmod import radar_site

    site = SimpleNamespace(id="KTLX", half_span_deg=5.0)
    monkeypatch.setattr(radar_site, "nearest_site",
                        lambda lat, lon, **kwargs: (site, 12.0))

    resolved = lo.resolve(lo.parse("radar-site"), lat=NORMAN[0], lon=NORMAN[1],
                          valid_time=NOW, now=NOW)

    assert resolved == (lo.Selection(lo.FAMILY_RADAR_SITE),)


def test_a_box_wider_than_the_antenna_falls_back_to_the_mosaic(monkeypatch):
    """A detailed frame over one corner of a summarised area is misleading."""
    from sharpmod import radar_site

    site = SimpleNamespace(id="KTLX", half_span_deg=5.0)
    monkeypatch.setattr(radar_site, "nearest_site",
                        lambda lat, lon, **kwargs: (site, 12.0))

    resolved = lo.resolve(lo.parse("radar-site"), lat=NORMAN[0], lon=NORMAN[1],
                          valid_time=NOW, now=NOW, half_span_deg=18.0)

    assert resolved == (lo.Selection(lo.FAMILY_RADAR_MOSAIC),)


def test_outside_the_network_the_mosaic_answers_instead(monkeypatch):
    from sharpmod import radar_site

    monkeypatch.setattr(radar_site, "nearest_site",
                        lambda lat, lon, **kwargs: None)

    resolved = lo.resolve(lo.parse("radar-site"), lat=51.5, lon=-0.12,
                          valid_time=NOW, now=NOW)

    assert resolved == (lo.Selection(lo.FAMILY_RADAR_MOSAIC),)


# --------------------------------------------------------------------------- #
# attaching
# --------------------------------------------------------------------------- #
def test_attaching_reports_the_keys_it_placed(monkeypatch):
    from sharpmod import map_overlays

    monkeypatch.setattr(lo, "fetch",
                        lambda selection, **kwargs: _raster(selection.key))
    collection = _Collection()

    attached = lo.apply(collection, lo.parse("risk,hrrr"), lat=NORMAN[0],
                        lon=NORMAN[1], valid_time=NOW, now=NOW)

    assert set(attached) == {"spc_outlook", "hrrr_field"}
    assert _attached_keys(collection) == {"spc_outlook", "hrrr_field"}


def test_switching_to_radar_detaches_what_it_displaces(monkeypatch):
    """Without this the displaced field stays underneath the new frame."""
    from sharpmod import map_overlays

    monkeypatch.setattr(lo, "fetch",
                        lambda selection, **kwargs: _raster(selection.key))
    collection = _Collection()
    lo.apply(collection, lo.parse("risk,hrrr"), lat=NORMAN[0], lon=NORMAN[1],
             valid_time=NOW, now=NOW)

    attached = lo.apply(collection, lo.parse("radar-mosaic"), lat=NORMAN[0],
                        lon=NORMAN[1], valid_time=NOW, now=NOW)

    assert attached == ("radar_mosaic",)
    assert _attached_keys(collection) == {"radar_mosaic"}, \
        "the displaced field would otherwise stay under the new frame"


def test_selecting_nothing_clears_the_inset(monkeypatch):
    monkeypatch.setattr(lo, "fetch",
                        lambda selection, **kwargs: _raster(selection.key))
    collection = _Collection()
    lo.apply(collection, lo.parse("hrrr"), lat=NORMAN[0], lon=NORMAN[1],
             valid_time=NOW, now=NOW)

    assert lo.apply(collection, lo.parse("none"), lat=NORMAN[0],
                    lon=NORMAN[1], valid_time=NOW, now=NOW) == ()
    assert _attached_keys(collection) == set()


def test_a_failing_provider_never_stops_the_render(monkeypatch):
    """The inset is an aid to reading a sounding, not a precondition for one."""
    def explode(selection, **kwargs):
        if selection.family == lo.FAMILY_HRRR:
            raise RuntimeError("bucket unreachable")
        return _raster(selection.key)

    monkeypatch.setattr(lo, "fetch", explode)

    attached = lo.apply(_Collection(), lo.parse("risk,hrrr"), lat=NORMAN[0],
                        lon=NORMAN[1], valid_time=NOW, now=NOW)

    assert attached == ("spc_outlook",)


def test_a_provider_with_nothing_to_offer_attaches_nothing(monkeypatch):
    monkeypatch.setattr(lo, "fetch", lambda selection, **kwargs: None)

    assert lo.apply(_Collection(), lo.parse("risk"), lat=NORMAN[0],
                    lon=NORMAN[1], valid_time=NOW, now=NOW) == ()


# --------------------------------------------------------------------------- #
# storm reports depend on the outlook they are read against
# --------------------------------------------------------------------------- #
def test_reports_are_shown_alongside_the_outlook():
    selections = lo.parse("risk:torn,reports")

    assert [item.family for item in selections] == [
        lo.FAMILY_RISK, lo.FAMILY_REPORTS]


def test_reports_alone_are_dropped():
    """Report markers with no risk areas behind them are just dots.

    The question storm reports answer is whether what was forecast is what
    happened, which needs the forecast on screen to be answerable at all.
    """
    assert lo.parse("reports") == ()


def test_naming_reports_first_still_requires_the_outlook():
    """Order of mention must not decide whether the rule applies."""
    selections = lo.parse("reports,risk")

    assert [item.family for item in selections] == [
        lo.FAMILY_RISK, lo.FAMILY_REPORTS]


def test_reports_do_not_survive_the_outlook_being_dropped():
    """Radar displaces risk, so it cannot leave the reports stranded either."""
    assert lo.parse("risk,reports,radar-site") == (
        lo.Selection(lo.FAMILY_RADAR_SITE),)


def test_reports_ride_alongside_a_model_field_too():
    selections = lo.parse("risk,hrrr:stp,reports")

    assert {item.family for item in selections} == {
        lo.FAMILY_RISK, lo.FAMILY_HRRR, lo.FAMILY_REPORTS}


def test_the_unmet_prerequisite_can_be_named():
    """A control should be able to say why the option is unavailable."""
    assert lo.missing_prerequisite(
        lo.FAMILY_REPORTS, lo.parse("hrrr")) == lo.FAMILY_RISK
    assert lo.missing_prerequisite(
        lo.FAMILY_REPORTS, lo.parse("risk")) is None
    assert lo.missing_prerequisite(lo.FAMILY_HRRR, ()) is None


def test_selecting_reports_reaches_the_storm_report_provider():
    """The marker size has to follow the view that asked for the reports."""
    seen: list[str] = []

    def opener(url, timeout, limit):
        seen.append(url)
        return (
            "VALID,VALID2,LAT,LON,MAG,WFO,TYPECODE,TYPETEXT,CITY,COUNTY,STATE,"
            "SOURCE,REMARK,UGC,UGCNAME,QUALIFIER\n"
            "202605011800,x,35.22,-97.44,1.75,OUN,H,HAIL,Norman,Cleveland,OK,"
            "Public,,OKC027,Cleveland,M\n"
        ).encode("utf-8")

    layer = lo.fetch(lo.Selection(lo.FAMILY_REPORTS), lat=NORMAN[0],
                     lon=NORMAN[1], valid_time=NOW, now=NOW, span_deg=3.0,
                     opener=opener)

    assert layer is not None and len(layer.shapes) == 1
    assert seen, "the provider has to be asked"


def test_reports_reach_back_for_an_archived_sounding():
    """Unlike radar, reports have an archive, so an old sounding still gets them.

    Asking whether the forecast verified is a question about the past, and the
    service answers for years gone by. The outlook beside them is the shorter
    lived of the two: its own archive begins in 2020.
    """
    seen: list[str] = []

    def opener(url, timeout, limit):
        seen.append(url)
        return b"VALID,VALID2,LAT,LON,MAG,WFO,TYPECODE,TYPETEXT,CITY,COUNTY,"\
               b"STATE,SOURCE,REMARK,UGC,UGCNAME,QUALIFIER\n"

    lo.fetch(lo.Selection(lo.FAMILY_REPORTS), lat=NORMAN[0], lon=NORMAN[1],
             valid_time=datetime(2021, 5, 1, 18, tzinfo=UTC), now=NOW,
             opener=opener)

    assert seen, "an archived sounding must still ask for reports"
    assert "year1=2021" in seen[0], "and ask about its own date, not today"
    assert "recent=" not in seen[0]


def test_a_current_sounding_asks_for_the_latest_reports():
    seen: list[str] = []

    def opener(url, timeout, limit):
        seen.append(url)
        return b"VALID,VALID2,LAT,LON,MAG,WFO,TYPECODE,TYPETEXT,CITY,COUNTY,"\
               b"STATE,SOURCE,REMARK,UGC,UGCNAME,QUALIFIER\n"

    lo.fetch(lo.Selection(lo.FAMILY_REPORTS), lat=NORMAN[0], lon=NORMAN[1],
             valid_time=NOW, now=NOW, opener=opener)

    assert "recent=" in seen[0]
    assert "year1=" not in seen[0]


def test_a_broken_report_feed_does_not_stop_the_outlook_drawing(monkeypatch):
    """``apply`` still treats one failing overlay as nothing to draw."""
    def explode(selection, **kwargs):
        if selection.family == lo.FAMILY_REPORTS:
            raise RuntimeError("feed unreachable")
        return _raster(selection.key)

    monkeypatch.setattr(lo, "fetch", explode)

    attached = lo.apply(_Collection(), lo.parse("risk,reports"), lat=NORMAN[0],
                        lon=NORMAN[1], valid_time=NOW, now=NOW)

    assert attached == ("spc_outlook",)

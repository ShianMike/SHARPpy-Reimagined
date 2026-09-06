"""The IGRA provider adapter: registry placement, error mapping, provenance.

The reader is covered in ``test_igra2_reader.py``. What matters here is the
seam: IGRA has to be reachable by name, unreachable by accident, and it has to
translate its own errors into the taxonomy every other provider speaks.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math

import numpy as np
import pytest

from sharpmod import observations
from sharpmod.io import igra2


WHEN = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)

STATION = igra2.IGRAStation(
    id="USM00072357",
    name="NORMAN/MAX WESTHEIMER A; OK.",
    lat=35.1808,
    lon=-97.4378,
    elev_m=344.9,
    state="OK",
    first_year=1974,
    last_year=2026,
    n_obs=28213,
)


def _sounding(**kwargs):
    defaults = dict(
        station_id="USM00072357",
        valid=WHEN,
        release_time=datetime(2026, 9, 1, 11, 19, tzinfo=timezone.utc),
        lat=35.1808,
        lon=-97.4378,
        pres=(976.2, 950.0, 925.0, 850.0, 700.0, 500.0, 300.0, 250.0),
        hght=(345.0, 560.0, 790.0, 1500.0, 3100.0, 5800.0, 9400.0, 10600.0),
        tmpc=(22.2, 20.5, 19.0, 15.0, 8.0, -4.0, -32.0, -42.0),
        dwpc=(16.9, 14.5, 12.0, 6.0, -2.0, -18.0, -46.0, -55.0),
        wdir=(184.0, 185.0, 194.0, 200.0, 220.0, 250.0, 270.0, 275.0),
        wspd=(10.3, 10.7, 28.8, 25.0, 30.0, 40.0, 60.0, 70.0),
        pressure_source="ncdc-nws",
        nonpressure_source="ncdc-gts",
        dewpoint_from_rh_levels=0,
        dropped_levels=76,
        archive_url=(
            f"{igra2.DATA_Y2D_URL}/USM00072357-data-beg2026.txt.zip"
        ),
        archive_kind="y2d",
    )
    defaults.update(kwargs)
    return igra2.IGRASounding(**defaults)


class _StubDecoder:
    """Stand in for :class:`igra2.IGRA_Decoder` without any network."""

    def __init__(self, *, station=STATION, sounding=None,
                 resolve_error=None, fetch_error=None):
        self._station = station
        self._sounding = sounding if sounding is not None else _sounding()
        self._resolve_error = resolve_error
        self._fetch_error = fetch_error
        self.resolved = []
        self.fetched = []

    def resolve_station(self, query):
        self.resolved.append(query)
        if self._resolve_error is not None:
            raise self._resolve_error
        return self._station

    def fetch(self, station_id, when_utc, **_kwargs):
        self.fetched.append((station_id, when_utc))
        if self._fetch_error is not None:
            raise self._fetch_error
        return self._sounding


def _provider(**kwargs):
    return observations.IGRAObservedProvider(decoder=_StubDecoder(**kwargs))


# --------------------------------------------------------------------------- #
# Registry placement
# --------------------------------------------------------------------------- #
def test_igra_is_registered_and_selectable_by_name():
    provider = observations.get_observed_provider("igra2")

    assert isinstance(provider, observations.IGRAObservedProvider)
    assert provider.info.key == "igra2"


def test_igra_is_absent_from_the_automatic_fallback_chain():
    """One station archive is megabytes at best; auto must never reach it."""
    assert "igra2" not in observations.DEFAULT_PROVIDER_ORDER


def test_the_registry_view_leads_with_the_fallback_chain():
    keys = observations.registered_provider_keys()

    assert keys[:len(observations.DEFAULT_PROVIDER_ORDER)] == \
        observations.DEFAULT_PROVIDER_ORDER
    assert "igra2" in keys
    assert len(keys) == len(set(keys))


def test_every_registered_provider_is_described():
    infos = observations.available_observed_providers()
    keys = [info.key for info in infos]

    assert keys == list(observations.registered_provider_keys())
    for info in infos:
        assert info.name.strip()
        assert info.homepage.startswith("https://")


def test_the_provider_satisfies_the_shared_protocol():
    assert isinstance(
        observations.get_observed_provider("igra2"),
        observations.ObservedSoundingProvider,
    )


def test_an_unknown_provider_key_is_rejected():
    with pytest.raises(KeyError):
        observations.get_observed_provider("not-a-provider")


def test_the_automatic_chain_never_constructs_the_igra_provider(monkeypatch):
    built = []

    class _Tripwire(observations.IGRAObservedProvider):
        def __init__(self, *args, **kwargs):
            built.append(1)
            super().__init__(*args, **kwargs)

    monkeypatch.setitem(observations._PROVIDER_FACTORIES, "igra2", _Tripwire)
    attempted = []

    class _Declining:
        def __init__(self, key):
            self.info = observations.ObservedProviderInfo(
                key=key, name=key, homepage="")

        def fetch(self, _station, _when):
            attempted.append(self.info.key)
            raise observations.ObservedUnavailableError("nothing")

    with pytest.raises(observations.ObservedFallbackError):
        observations.fetch_observed(
            "72357", WHEN,
            providers=[_Declining(key)
                       for key in observations.DEFAULT_PROVIDER_ORDER],
        )

    assert attempted == list(observations.DEFAULT_PROVIDER_ORDER)
    assert built == []


# --------------------------------------------------------------------------- #
# Construction is cheap
# --------------------------------------------------------------------------- #
def test_constructing_the_provider_performs_no_io(monkeypatch):
    def _explode(*_args, **_kwargs):
        raise AssertionError("no reader should be built at construction")

    monkeypatch.setattr(igra2, "IGRA_Decoder", _explode)

    observations.IGRAObservedProvider()


def test_the_reader_is_built_once_on_first_use(monkeypatch):
    built = []

    class _Fake:
        def __init__(self, **kwargs):
            built.append(kwargs)

    monkeypatch.setattr(igra2, "IGRA_Decoder", _Fake)
    provider = observations.IGRAObservedProvider()

    first = provider.decoder
    second = provider.decoder

    assert first is second
    assert len(built) == 1


def test_the_full_record_setting_reaches_the_reader(monkeypatch):
    built = []

    class _Fake:
        def __init__(self, **kwargs):
            built.append(kwargs)

    monkeypatch.setattr(igra2, "IGRA_Decoder", _Fake)
    observations.IGRAObservedProvider(allow_full_record=False).decoder

    assert built == [{"allow_full_record": False}]


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #
def test_an_unresolvable_station_is_a_station_error():
    provider = _provider(
        resolve_error=igra2.IGRAStationLookupError("no match"))

    with pytest.raises(observations.ObservedStationError, match="no match"):
        provider.fetch("nowhere", WHEN)


def test_a_reader_failure_during_resolution_is_a_retrieval_error():
    provider = _provider(
        resolve_error=igra2.IGRARetrievalError("list unreachable"))

    with pytest.raises(observations.ObservedRetrievalError):
        provider.fetch("72357", WHEN)


@pytest.mark.parametrize("raised,expected", [
    (igra2.IGRAStationTimeUnavailableError("nothing archived"),
     observations.ObservedUnavailableError),
    (igra2.IGRAFullRecordRequiredError("needs the full record"),
     observations.ObservedUnavailableError),
    (igra2.IGRASoundingParseError("garbled"),
     observations.ObservedParseError),
    (igra2.IGRARetrievalError("offline"),
     observations.ObservedRetrievalError),
    (igra2.IGRAArchiveTooLargeError("too big"),
     observations.ObservedRetrievalError),
    (igra2.IGRAStationLookupError("ambiguous"),
     observations.ObservedStationError),
    (igra2.IGRAError("something else"),
     observations.ObservedRetrievalError),
])
def test_reader_errors_map_onto_the_shared_taxonomy(raised, expected):
    provider = _provider(fetch_error=raised)

    with pytest.raises(expected):
        provider.fetch("72357", WHEN)


def test_the_original_error_is_preserved_as_the_cause():
    """The GUI reads ``__cause__`` to tell a refused download from no data."""
    original = igra2.IGRAFullRecordRequiredError("needs the full record")
    provider = _provider(fetch_error=original)

    with pytest.raises(observations.ObservedUnavailableError) as caught:
        provider.fetch("72357", WHEN)

    assert caught.value.__cause__ is original


def test_a_refused_download_is_distinguishable_from_a_missing_sounding():
    refused = _provider(
        fetch_error=igra2.IGRAFullRecordRequiredError("needs full record"))
    absent = _provider(
        fetch_error=igra2.IGRAStationTimeUnavailableError("nothing"))

    with pytest.raises(observations.ObservedUnavailableError) as first:
        refused.fetch("72357", WHEN)
    with pytest.raises(observations.ObservedUnavailableError) as second:
        absent.fetch("72357", WHEN)

    assert isinstance(first.value.__cause__, igra2.IGRAFullRecordRequiredError)
    assert not isinstance(
        second.value.__cause__, igra2.IGRAFullRecordRequiredError)


# --------------------------------------------------------------------------- #
# Successful fetch
# --------------------------------------------------------------------------- #
def test_a_fetch_resolves_the_station_before_retrieving_it():
    decoder = _StubDecoder()
    provider = observations.IGRAObservedProvider(decoder=decoder)

    provider.fetch("72357", WHEN)

    assert decoder.resolved == ["72357"]
    assert decoder.fetched == [("USM00072357", WHEN)]


def test_the_result_reports_the_igra_station_id_and_the_request():
    result = _provider().fetch("72357", WHEN)

    assert result.provider == "igra2"
    assert result.station_id == "USM00072357"
    assert result.requested_station == "72357"
    assert result.valid == WHEN
    assert "data-beg2026" in result.source_url


def test_a_naive_request_time_is_normalised_to_utc():
    decoder = _StubDecoder()
    observations.IGRAObservedProvider(decoder=decoder).fetch(
        "72357", datetime(2026, 9, 1, 12))

    _station, when = decoder.fetched[0]
    assert when == WHEN


def test_the_profile_carries_the_reported_levels():
    result = _provider().fetch("72357", WHEN)
    profile = result.profile

    assert np.ma.asarray(profile.pres).size == 8
    assert float(np.ma.asarray(profile.pres)[0]) == pytest.approx(976.2)
    assert float(np.ma.asarray(profile.tmpc)[0]) == pytest.approx(22.2)
    assert float(np.ma.asarray(profile.dwpc)[0]) == pytest.approx(16.9)
    assert float(np.ma.asarray(profile.wspd)[2]) == pytest.approx(28.8)


def test_missing_levels_are_masked_rather_than_left_as_a_sentinel():
    sounding = _sounding(
        tmpc=(22.2, igra2.MISSING, 19.0, 15.0, 8.0, -4.0, -32.0, -42.0),
    )
    result = observations.IGRAObservedProvider(
        decoder=_StubDecoder(sounding=sounding)).fetch("72357", WHEN)

    tmpc = np.ma.asarray(result.profile.tmpc)
    assert np.ma.getmaskarray(tmpc)[1]
    assert not np.ma.getmaskarray(tmpc)[0]
    assert igra2.MISSING not in np.asarray(tmpc.filled(np.nan)).tolist()


def test_the_station_position_reaches_the_metadata():
    result = _provider().fetch("72357", WHEN)

    assert result.metadata["lat"] == pytest.approx(35.1808)
    assert result.metadata["lon"] == pytest.approx(-97.4378)


def test_a_positionless_sounding_falls_back_to_the_station_record():
    sounding = _sounding(lat=float("nan"), lon=float("nan"))
    result = observations.IGRAObservedProvider(
        decoder=_StubDecoder(sounding=sounding)).fetch("72357", WHEN)

    assert result.metadata["lat"] == pytest.approx(STATION.lat)
    assert result.metadata["lon"] == pytest.approx(STATION.lon)
    assert math.isfinite(result.metadata["lat"])


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
def test_the_metadata_marks_the_sounding_as_observed():
    metadata = _provider().fetch("72357", WHEN).metadata

    assert metadata["observed"] is True
    assert metadata["model"] == "Observed"
    assert metadata["fxx"] == 0
    assert metadata["source"] == "igra2"
    assert metadata["source_provider_name"].startswith("NOAA")


def test_the_metadata_records_which_archive_was_read():
    metadata = _provider().fetch("72357", WHEN).metadata

    assert metadata["igra_archive"] == "y2d"
    assert metadata["igra_station_id"] == "USM00072357"
    assert metadata["igra_wmo_id"] == "72357"
    assert metadata["igra_state"] == "OK"
    assert metadata["igra_record_years"] == "1974-2026"
    assert metadata["igra_pressure_source"] == "ncdc-nws"


def test_the_requested_time_is_kept_beside_the_delivered_one():
    """The match tolerance means the two can legitimately differ."""
    delivered = datetime(2026, 9, 1, 19, tzinfo=timezone.utc)
    requested = datetime(2026, 9, 1, 18, tzinfo=timezone.utc)
    provider = observations.IGRAObservedProvider(
        decoder=_StubDecoder(sounding=_sounding(valid=delivered)))

    result = provider.fetch("72357", requested)

    assert result.metadata["valid"] == delivered
    assert result.metadata["igra_requested_valid"] == requested


def test_reconstructed_dewpoint_levels_are_disclosed():
    provider = observations.IGRAObservedProvider(
        decoder=_StubDecoder(sounding=_sounding(dewpoint_from_rh_levels=12)))

    metadata = provider.fetch("72357", WHEN).metadata

    assert metadata["igra_dewpoint_from_rh_levels"] == 12


def test_dropped_non_pressure_levels_are_disclosed():
    metadata = _provider().fetch("72357", WHEN).metadata

    assert metadata["igra_dropped_nonpressure_levels"] == 76


def test_a_sounding_without_a_release_time_omits_the_field():
    provider = observations.IGRAObservedProvider(
        decoder=_StubDecoder(sounding=_sounding(release_time=None)))

    metadata = provider.fetch("72357", WHEN).metadata

    assert "igra_release_time" not in metadata


def test_the_metadata_is_attached_to_the_profile_itself():
    result = _provider().fetch("72357", WHEN)

    assert result.profile.meta["source_provider"] == "igra2"
    assert result.profile.meta["igra_archive"] == "y2d"


# --------------------------------------------------------------------------- #
# Portable output
# --------------------------------------------------------------------------- #
def test_the_portable_npz_and_sidecar_carry_the_igra_provenance(tmp_path):
    result = _provider().fetch("72357", WHEN)
    target = tmp_path / "igra.npz"

    observations.write_observed_npz(result, target)

    with np.load(target, allow_pickle=False) as payload:
        assert str(payload["source"]) == "igra2"
        assert str(payload["source_station"]) == "USM00072357"
        assert payload["pres"].size == 8
        assert float(payload["lat"]) == pytest.approx(35.1808)

    sidecar = json.loads((tmp_path / "igra.json").read_text(encoding="utf-8"))
    assert sidecar["provider"] == "igra2"
    assert sidecar["levels"] == 8
    assert sidecar["igra_archive"] == "y2d"
    assert sidecar["igra_wmo_id"] == "72357"
    assert sidecar["valid"] == "2026-09-01T12:00:00Z"


def test_an_explicitly_named_provider_records_no_fallback(tmp_path):
    result = observations.fetch_observed(
        "72357", WHEN, providers=(_provider(),))

    assert "fallback_attempts" not in result.metadata


def test_a_named_provider_failure_is_reported_not_silently_substituted():
    provider = _provider(
        fetch_error=igra2.IGRAStationTimeUnavailableError("nothing"))

    with pytest.raises(observations.ObservedFallbackError) as caught:
        observations.fetch_observed("72357", WHEN, providers=(provider,))

    assert [item["provider"] for item in caught.value.attempts] == ["igra2"]

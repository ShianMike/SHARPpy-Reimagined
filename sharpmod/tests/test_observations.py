"""Deterministic tests for redundant observed-sounding providers."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest

from sharpmod.io.decoder import load_npz
from sharpmod.observations import (
    IEMObservedProvider,
    ObservedProviderInfo,
    ObservedSounding,
    ObservedUnavailableError,
    fetch_observed,
    write_observed_npz,
)


WHEN = datetime(2024, 5, 20, 0, tzinfo=timezone.utc)


def _iem_payloads():
    catalog = {
        "data": [{
            "id": "KOUN",
            "synop": 72357,
            "name": "Norman OK/US",
            "plot_name": "Norman, OK/US",
            "latitude": 35.22,
            "longitude": -97.4,
        }]
    }
    sounding = {
        "profiles": [{
            "station": "KOUN",
            "valid": "2024-05-20T00:00:00Z",
            "profile": [
                {
                    "pres": 967.0, "hght": 345.0, "tmpc": 30.0,
                    "dwpc": 21.0, "drct": 155.0, "sknt": 14.0,
                },
                {
                    "pres": 925.0, "hght": 739.0, "tmpc": 25.6,
                    "dwpc": 18.6, "drct": 160.0, "sknt": 32.0,
                },
                {
                    "pres": 850.0, "hght": 1477.0, "tmpc": 19.0,
                    "dwpc": 17.1, "drct": None, "sknt": None,
                },
            ],
        }]
    }
    return catalog, sounding


def _provider_and_urls():
    catalog, sounding = _iem_payloads()
    urls = []

    def http_json(url):
        urls.append(url)
        return catalog if "/network/RAOB.json" in url else sounding

    return IEMObservedProvider(http_json=http_json), urls


def test_iem_provider_resolves_wmo_and_preserves_exact_source_metadata():
    provider, urls = _provider_and_urls()

    result = provider.fetch("72357", WHEN)

    assert result.provider == "iem"
    assert result.station_id == "KOUN"
    assert result.valid == WHEN
    assert result.metadata["source_provider"] == "iem"
    assert result.metadata["source_station"] == "KOUN"
    assert result.metadata["requested_station"] == "72357"
    assert result.profile.meta["source_url"] == result.source_url
    assert np.asarray(result.profile.pres).size == 3
    assert np.ma.is_masked(result.profile.wdir[-1])

    query = parse_qs(urlparse(result.source_url).query)
    assert query == {"ts": ["202405200000"], "station": ["KOUN"]}
    assert urls == [provider.NETWORK_URL, result.source_url]


def test_iem_provider_rejects_non_exact_profile_instead_of_substituting():
    catalog, sounding = _iem_payloads()
    sounding["profiles"][0]["valid"] = "2024-05-20T12:00:00Z"
    provider = IEMObservedProvider(
        http_json=lambda url: catalog if "/network/" in url else sounding
    )

    with pytest.raises(ObservedUnavailableError, match="exact requested"):
        provider.fetch("72357", WHEN)


class _UnavailableProvider:
    info = ObservedProviderInfo("uwyo", "UWyo", "https://example.invalid/uwyo")

    def fetch(self, station, when):
        raise ObservedUnavailableError("UWyo has no report")


class _SuccessfulProvider:
    info = ObservedProviderInfo("iem", "IEM", "https://example.invalid/iem")

    def __init__(self, result):
        self.result = result

    def fetch(self, station, when):
        return self.result


def test_fallback_returns_one_provider_without_merging_and_records_failure():
    provider, _urls = _provider_and_urls()
    iem_result = provider.fetch("72357", WHEN)

    result = fetch_observed(
        "72357",
        WHEN,
        providers=(_UnavailableProvider(), _SuccessfulProvider(iem_result)),
    )

    assert result.provider == "iem"
    assert np.array_equal(result.profile.pres, iem_result.profile.pres)
    attempts = result.metadata["fallback_attempts"]
    assert len(attempts) == 1
    assert attempts[0]["provider"] == "uwyo"
    assert attempts[0]["error_type"] == "ObservedUnavailableError"


def test_observed_writer_and_npz_decoder_retain_provider_provenance(tmp_path):
    provider, _urls = _provider_and_urls()
    base = provider.fetch("72357", WHEN)
    result = fetch_observed(
        "72357",
        WHEN,
        providers=(_UnavailableProvider(), _SuccessfulProvider(base)),
    )
    output = tmp_path / "oun.npz"

    assert write_observed_npz(result, output) == str(output)

    with np.load(output, allow_pickle=False) as payload:
        assert str(payload["source_provider"]) == "iem"
        assert str(payload["source_station"]) == "KOUN"
        assert payload["fallback_from"].tolist() == ["uwyo"]
        assert bool(payload["observed"])
    sidecar = json.loads(output.with_suffix(".json").read_text("utf-8"))
    assert sidecar["provider"] == "iem"
    assert sidecar["source_url"] == result.source_url
    assert sidecar["fallback_attempts"][0]["provider"] == "uwyo"

    collection, _loc = load_npz(str(output))
    assert collection.getMeta("observed") is True
    assert collection.getMeta("source_provider") == "iem"
    assert collection.getMeta("source_station") == "KOUN"
    assert collection.getMeta("fallback_from") == ("uwyo",)


def test_gui_fetch_worker_uses_provider_fallback_and_emits_actual_source(
    monkeypatch,
):
    from qtpy.QtCore import QCoreApplication

    from sharpmod import gui_workers, observations

    QCoreApplication.instance() or QCoreApplication([])
    captured = {}
    result = SimpleNamespace(
        provider="iem",
        provider_name="IEM RAOB Archive",
        station_id="KOUN",
        metadata={
            "station_name": "OUN Norman, OK",
            "lat": 35.22,
            "lon": -97.4,
        },
    )

    monkeypatch.setattr(
        gui_workers, "_decoder_for_station", lambda _station: (object(), "72357")
    )

    def fake_fetch(station, when, *, providers):
        captured["station"] = station
        captured["providers"] = [provider.info.key for provider in providers]
        return result

    def fake_write(selected, path, *, loc):
        captured["loc"] = loc
        open(path, "wb").close()
        return path

    monkeypatch.setattr(observations, "fetch_observed", fake_fetch)
    monkeypatch.setattr(observations, "write_observed_npz", fake_write)
    successes = []
    failures = []
    worker = gui_workers._FetchWorker("72357", WHEN)
    worker.finished_ok.connect(
        lambda path, meta, when: successes.append((path, meta, when))
    )
    worker.failed.connect(failures.append)

    worker.run()

    assert failures == []
    assert captured["station"] == "72357"
    assert captured["providers"] == ["uwyo", "iem"]
    assert successes[0][1].provider == "iem"
    assert successes[0][1].id == "KOUN"
    os.remove(successes[0][0])

"""Cached town-name lookup regressions."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from importlib.resources import files
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from sharpmod import gui_workers, place_names, render
from sharpmod.tools import model_extract


class _Response:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self._payload


def test_reverse_town_is_identified_rate_safe_and_persistently_cached(
        tmp_path, monkeypatch):
    cache_path = tmp_path / "places.json"
    monkeypatch.setenv(place_names.PLACE_CACHE_ENV, str(cache_path))
    monkeypatch.setenv(
        place_names.GEOCODER_URL_ENV, "https://example.test/reverse")
    monkeypatch.setattr(place_names, "_MIN_REQUEST_INTERVAL_S", 0.0)
    monkeypatch.setattr(place_names, "_LAST_REQUEST_AT", 0.0)
    monkeypatch.setattr(
        place_names, "offline_conus_town_name", lambda *_args: "")
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        return _Response({
            "address": {
                "village": "Bruce",
                "state": "Wisconsin",
                "country": "United States",
            },
        })

    assert place_names.reverse_town_name(
        45.76, -91.60, opener=opener) == "Bruce, Wisconsin"
    assert len(requests) == 1
    request, timeout = requests[0]
    query = parse_qs(urlparse(request.full_url).query)
    assert query["lat"] == ["45.760000"]
    assert query["lon"] == ["-91.600000"]
    assert query["zoom"] == ["13"]
    assert "SHARPpy-Reimagined/" in request.get_header("User-agent")
    assert timeout == 4.0

    def unexpected_network(*_args, **_kwargs):
        raise AssertionError("cached coordinate repeated a network request")

    assert place_names.reverse_town_name(
        45.7602, -91.6002, opener=unexpected_network
    ) == "Bruce, Wisconsin"
    stored = json.loads(cache_path.read_text(encoding="utf-8"))
    assert stored["attribution"] == place_names.OSM_ATTRIBUTION


def test_town_lookup_can_be_disabled_without_network(tmp_path, monkeypatch):
    monkeypatch.setenv(
        place_names.PLACE_CACHE_ENV, str(tmp_path / "places.json"))
    monkeypatch.setenv(place_names.GEOCODER_URL_ENV, "off")

    assert place_names.reverse_town_name(
        14.5995,
        120.9842,
        opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("disabled lookup attempted network access")
        ),
    ) == ""


def test_reverse_lookup_rejects_nearby_non_conus_points_before_network(
        tmp_path, monkeypatch):
    monkeypatch.setenv(
        place_names.PLACE_CACHE_ENV, str(tmp_path / "places.json"))
    monkeypatch.setenv(
        place_names.GEOCODER_URL_ENV, "https://example.test/reverse")
    network_calls = []

    def unexpected_network(*args, **kwargs):
        network_calls.append((args, kwargs))
        raise AssertionError("a non-CONUS point attempted reverse geocoding")

    outside_points = {
        "Vancouver": (49.2827, -123.1207),
        "Toronto": (43.6532, -79.3832),
        "Montreal": (45.5017, -73.5673),
        "Tijuana": (32.5149, -117.0382),
        "Gulf of Mexico": (27.0, -90.0),
        "Atlantic Ocean": (35.0, -70.0),
    }
    for lat, lon in outside_points.values():
        assert not place_names.is_conus_point(lat, lon)
        assert place_names.offline_conus_town_name(lat, lon) == ""
        assert place_names.reverse_town_name(
            lat, lon, opener=unexpected_network) == ""
    assert network_calls == []


def test_conus_boundary_mask_retains_interior_and_coastal_us_points():
    inside_points = {
        "Seattle": (47.6062, -122.3321),
        "Miami": (25.7617, -80.1918),
        "Brownsville": (25.9017, -97.4975),
        "Detroit": (42.3314, -83.0458),
        "Birchwood": (45.76, -91.60),
    }
    for lat, lon in inside_points.values():
        assert place_names.is_conus_point(lat, lon)
        assert place_names.offline_conus_town_name(lat, lon)


def test_disabled_online_lookup_uses_bundled_conus_coverage(
        tmp_path, monkeypatch):
    monkeypatch.setenv(
        place_names.PLACE_CACHE_ENV, str(tmp_path / "places.json"))
    monkeypatch.setenv(place_names.GEOCODER_URL_ENV, "off")

    def unexpected_network(*_args, **_kwargs):
        raise AssertionError("offline CONUS lookup attempted network access")

    assert place_names.reverse_town_name(
        41.53, -88.39, opener=unexpected_network
    ) == "Plattville, Illinois"
    assert place_names.reverse_town_name(
        45.76, -91.60, opener=unexpected_network
    ) == "Town of Birchwood, Wisconsin"


def test_area_proxy_prefers_denver_over_nearby_tiny_enclave():
    # Denver's Census representative point lies east of downtown, making the
    # tiny Glendale enclave's representative point closer under a raw
    # nearest-neighbor search. Land-area footprints restore the plausible
    # containing city without hiding Glendale at its own center.
    assert place_names.offline_conus_town_name(
        39.7392, -104.9903) == "Denver, Colorado"
    assert place_names.offline_conus_town_name(
        39.703052, -104.936157) == "Glendale, Colorado"


def test_area_proxy_preserves_rural_township_and_boundary_behavior():
    assert place_names.offline_conus_town_name(
        45.76, -91.60) == "Town of Birchwood, Wisconsin"
    assert place_names.offline_conus_town_name(
        38.5, -100.5) == "Dighton Township, Kansas"
    assert place_names.offline_conus_town_name(
        43.6532, -79.3832) == ""


def test_place_record_parser_accepts_legacy_and_area_aware_rows():
    assert place_names._place_record_from_fields([
        "Legacy Town, State",
        "40.0",
        "-100.0",
        "place",
        "0000000",
    ]) == ("Legacy Town, State", 40.0, -100.0, "place", 0.0)
    assert place_names._place_record_from_fields([
        "Current Town, State",
        "40.0",
        "-100.0",
        "subdivision",
        "0000000",
        "25.125",
    ]) == ("Current Town, State", 40.0, -100.0, "subdivision", 25.125)


def test_bundled_conus_index_is_complete_and_matches_metadata():
    resource_root = files("sharpmod.resources")
    payload = resource_root.joinpath(
        place_names.CONUS_PLACE_RESOURCE
    ).read_bytes()
    metadata = json.loads(
        resource_root.joinpath(
            "conus-places.metadata.json"
        ).read_text(encoding="utf-8")
    )

    assert metadata["record_count"] == 52_818
    assert metadata["place_count"] == 31_540
    assert metadata["town_or_township_count"] == 21_278
    assert metadata["state_or_district_count"] == 49
    assert metadata["schema_version"] == 2
    assert metadata["place_record_fields"][-1] == "land_area_sqmi"
    assert metadata["boundary_polygon_count"] == 831
    assert metadata["boundary_point_count"] == 165_187
    assert hashlib.sha256(payload).hexdigest() == metadata["output_sha256"]
    assert sum(
        len(values)
        for values in place_names._conus_place_cells().values()
    ) == metadata["record_count"]
    denver = next(
        record
        for values in place_names._conus_place_cells().values()
        for record in values
        if record[0] == "Denver, Colorado"
    )
    assert denver[4] == 153.074
    assert len(place_names._conus_boundary_polygons()) == \
        metadata["boundary_polygon_count"]


def test_conus_index_has_an_annual_freshness_workflow():
    root = Path(__file__).resolve().parents[2]
    workflow = (
        root / ".github" / "workflows" / "conus-place-index.yml"
    ).read_text(encoding="utf-8")

    assert "schedule:" in workflow
    assert "scripts/build_conus_place_index.py --year $year" in workflow
    assert "conus-places.tsv.gz" in workflow
    assert "conus-places.metadata.json" in workflow


def test_town_parser_uses_country_when_region_is_unavailable():
    assert place_names._place_from_payload({
        "address": {"city": "Singapore", "country": "Singapore"},
    }) == "Singapore"


def test_town_parser_does_not_promote_counties_or_neighborhoods():
    assert place_names._place_from_payload({
        "address": {
            "county": "Cook County",
            "state": "Illinois",
            "country_code": "us",
        },
    }) == ""
    assert place_names._place_from_payload({
        "address": {
            "suburb": "Lakeview",
            "city_district": "North Side",
            "county": "Cook County",
            "state": "Illinois",
            "country_code": "us",
        },
    }) == ""


def test_offline_fallback_is_cached_without_network(tmp_path, monkeypatch):
    cache_path = tmp_path / "places.json"
    monkeypatch.setenv(place_names.PLACE_CACHE_ENV, str(cache_path))
    monkeypatch.setenv(
        place_names.GEOCODER_URL_ENV, "https://example.test/reverse")
    monkeypatch.setattr(place_names, "_MIN_REQUEST_INTERVAL_S", 0.0)
    monkeypatch.setattr(place_names, "_LAST_REQUEST_AT", 0.0)
    calls = []

    def unexpected_opener(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("bundled CONUS coverage attempted network access")

    expected = "Plattville, Illinois"
    assert place_names.reverse_town_name(
        41.53, -88.39, opener=unexpected_opener) == expected
    assert place_names.reverse_town_name(
        41.5302, -88.3902, opener=unexpected_opener) == expected
    assert calls == []

    stored = json.loads(cache_path.read_text(encoding="utf-8"))
    entry = stored["places"]["41.530,-88.390"]
    assert stored["version"] == place_names._CACHE_VERSION
    assert entry["label"] == expected
    assert entry["source"] == "offline"


def test_negative_network_result_is_cached(tmp_path, monkeypatch):
    cache_path = tmp_path / "places.json"
    monkeypatch.setenv(place_names.PLACE_CACHE_ENV, str(cache_path))
    monkeypatch.setenv(
        place_names.GEOCODER_URL_ENV, "https://example.test/reverse")
    monkeypatch.setattr(place_names, "_MIN_REQUEST_INTERVAL_S", 0.0)
    monkeypatch.setattr(place_names, "_LAST_REQUEST_AT", 0.0)
    monkeypatch.setattr(
        place_names, "offline_conus_town_name", lambda *_args: "")
    calls = []

    def failing_opener(*args, **kwargs):
        calls.append((args, kwargs))
        raise OSError("simulated geocoder outage")

    assert place_names.reverse_town_name(
        41.53, -88.39, opener=failing_opener) == ""
    assert place_names.reverse_town_name(
        41.5302, -88.3902, opener=failing_opener) == ""
    assert len(calls) == 1
    stored = json.loads(cache_path.read_text(encoding="utf-8"))
    assert stored["places"]["41.530,-88.390"]["source"] == "negative"


def test_cache_entries_expire_by_source_ttl(tmp_path):
    cache_path = tmp_path / "places.json"
    cached_at = 1_000_000.0
    entries = {
        "online": {
            "label": "Online, State",
            "source": "online",
            "cached_at": cached_at,
        },
        "offline": {
            "label": "Offline, State",
            "source": "offline",
            "cached_at": cached_at,
        },
        "negative": {
            "label": "",
            "source": "negative",
            "cached_at": cached_at,
        },
    }
    cache_path.write_text(json.dumps({
        "version": place_names._CACHE_VERSION,
        "places": entries,
    }), encoding="utf-8")

    assert set(place_names._read_places(
        cache_path, now=cached_at + 1.0)) == set(entries)
    assert "negative" not in place_names._read_places(
        cache_path,
        now=cached_at + place_names._NEGATIVE_CACHE_TTL_S + 1.0,
    )
    assert "offline" not in place_names._read_places(
        cache_path,
        now=cached_at + place_names._FALLBACK_CACHE_TTL_S + 1.0,
    )
    assert "online" not in place_names._read_places(
        cache_path,
        now=cached_at + place_names._POSITIVE_CACHE_TTL_S + 1.0,
    )


def test_non_us_online_result_falls_back_to_bundled_conus_name(
        tmp_path, monkeypatch):
    monkeypatch.setenv(
        place_names.PLACE_CACHE_ENV, str(tmp_path / "places.json"))
    monkeypatch.setenv(
        place_names.GEOCODER_URL_ENV, "https://example.test/reverse")
    monkeypatch.setattr(place_names, "_MIN_REQUEST_INTERVAL_S", 0.0)
    monkeypatch.setattr(place_names, "_LAST_REQUEST_AT", 0.0)
    offline_results = iter(("", "Plattville, Illinois"))
    monkeypatch.setattr(
        place_names,
        "offline_conus_town_name",
        lambda *_args: next(offline_results),
    )

    def opener(_request, timeout):
        assert timeout == 4.0
        return _Response({
            "address": {
                "city": "Toronto",
                "state": "Ontario",
                "country": "Canada",
                "country_code": "ca",
            },
        })

    assert place_names.reverse_town_name(
        41.53, -88.39, opener=opener) == "Plattville, Illinois"


def test_render_resolves_coordinate_only_location_for_locator_title(monkeypatch):
    metadata = {
        "loc": "HRRR 41.53N 88.39W",
        "model": "HRRR",
        "lat": 41.53,
        "lon": -88.39,
    }
    collection = type("Collection", (), {
        "getMeta": lambda self, key: metadata[key],
        "setMeta": lambda self, key, value: metadata.__setitem__(key, value),
    })()
    monkeypatch.setattr(
        place_names,
        "reverse_town_name",
        lambda lat, lon: "Plattville, Illinois",
    )

    assert render._resolve_location_title(collection) == \
        "Plattville, Illinois"
    assert metadata["loc"] == "Plattville, Illinois"


def test_render_preserves_an_explicit_location_title(monkeypatch):
    metadata = {
        "loc": "My forecast point",
        "model": "HRRR",
        "lat": 41.53,
        "lon": -88.39,
    }
    collection = type("Collection", (), {
        "getMeta": lambda self, key: metadata[key],
        "setMeta": lambda self, key, value: metadata.__setitem__(key, value),
    })()
    monkeypatch.setattr(
        place_names,
        "reverse_town_name",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("explicit title attempted reverse lookup")
        ),
    )

    assert render._resolve_location_title(
        collection, explicit_loc="My forecast point"
    ) == "My forecast point"


def test_gui_model_worker_writes_resolved_town_into_sounding(
        tmp_path, monkeypatch):
    monkeypatch.setattr(
        place_names,
        "reverse_town_name",
        lambda lat, lon: "Town of Birchwood, Wisconsin",
    )
    received_locations = []

    def fake_extract(*_args, **kwargs):
        received_locations.append(kwargs["loc"])
        target = Path(kwargs["out_path"])
        target.write_bytes(b"portable")
        target.with_suffix(".json").write_text(
            json.dumps({"backend": "test"}), encoding="utf-8")
        return str(target)

    monkeypatch.setattr(model_extract, "extract", fake_extract)
    out_path = tmp_path / "sounding.npz"
    worker = gui_workers._ModelFetchWorker(
        "hrrr",
        45.76,
        -91.60,
        datetime(2026, 7, 27, 3, tzinfo=timezone.utc),
        8,
        str(out_path),
        resolve_place=True,
        download_dir=str(tmp_path),
        model_hour_cache=None,
    )
    completed = []
    worker.finished_ok.connect(lambda path, *_args: completed.append(path))

    worker.run()

    assert completed == [str(out_path)]
    assert received_locations == ["Town of Birchwood, Wisconsin"]

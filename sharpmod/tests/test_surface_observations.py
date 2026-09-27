"""Regression coverage for surface station observations and map overlays.

The tests protect units and missing-value semantics, actual observation times, checked
station availability, screen-space decluttering, layer styling, and portable CSV round
trips."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from sharpmod.surface_observations import (
    SURFACE_OVERLAY_KEY,
    SurfaceObservation,
    SurfaceObservationSet,
    SurfaceStation,
    declutter_observations,
    export_surface_csv,
    fetch_surface_observations,
    parse_observations,
    parse_stations,
    read_surface_observations,
    surface_overlay_layer,
    write_surface_observations,
)


UTC = timezone.utc
REQUESTED = datetime(2026, 9, 13, 1, 45, tzinfo=UTC)


def _quantity(value, unit):
    return {"value": value, "unitCode": f"wmoUnit:{unit}"}


def _station_feature(station_id, lon, lat):
    return {
        "id": f"https://api.weather.gov/stations/{station_id}",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "stationIdentifier": station_id,
            "name": f"Station {station_id}",
            "elevation": _quantity(300.0, "m"),
        },
    }


def _observation_feature(
    station_id,
    timestamp,
    *,
    dewpoint=15.0,
    wind_direction=225.0,
    wind_speed=10.0,
):
    return {
        "id": f"https://api.weather.gov/stations/{station_id}/observations/{timestamp}",
        "properties": {
            "timestamp": timestamp,
            "temperature": _quantity(22.0, "degC"),
            "dewpoint": _quantity(dewpoint, "degC"),
            "windDirection": _quantity(wind_direction, "degree_(angle)"),
            "windSpeed": _quantity(wind_speed, "m_s-1"),
            "windGust": {"value": None, "unitCode": "wmoUnit:m_s-1"},
            "textDescription": "Mostly Cloudy",
        },
    }


def test_station_and_observation_parsers_preserve_units_and_missing_values():
    station = parse_stations({"features": [_station_feature("KAAA", -97, 35)]})[0]
    observation = parse_observations(
        {"features": [_observation_feature("KAAA", "2026-09-13T01:35:00Z")]},
        station,
    )[0]
    assert observation.dewpoint_c == 15.0
    assert observation.wind_speed_kt == pytest.approx(19.4384, rel=1e-4)
    assert observation.wind_gust_kt is None
    u, v = observation.wind_uv_kt
    assert u == pytest.approx(13.745, rel=1e-3)
    assert v == pytest.approx(13.745, rel=1e-3)


def test_calm_and_missing_winds_remain_distinct():
    station = SurfaceStation("KAAA", "Station KAAA", 35.0, -97.0)
    calm = SurfaceObservation(station, REQUESTED, 22.0, 15.0, None, 0.0)
    missing = SurfaceObservation(station, REQUESTED, 22.0, 15.0, None, None)
    partial = SurfaceObservation(station, REQUESTED, 22.0, 15.0, None, 12.0)

    assert calm.wind_state == "calm" and calm.wind_uv_kt == (0.0, 0.0)
    assert missing.wind_state == "missing" and missing.wind_uv_kt == (None, None)
    assert partial.wind_state == "direction-missing"
    assert partial.wind_uv_kt == (None, None)


def test_fetch_matches_actual_times_and_discloses_missing_stations():
    station_payload = {
        "features": [
            _station_feature("KAAA", -97.0, 35.0),
            _station_feature("KBBB", -97.4, 35.2),
            _station_feature("KCCC", -98.0, 35.4),
        ]
    }
    payloads = {
        "KAAA": {
            "features": [_observation_feature("KAAA", "2026-09-13T01:35:00Z")]
        },
        "KBBB": {
            "features": [_observation_feature("KBBB", "2026-09-13T01:00:00Z")]
        },
        "KCCC": {"features": []},
    }

    def opener(url, **_kwargs):
        if "/points/" in url:
            return {
                "properties": {
                    "observationStations": (
                        "https://api.weather.gov/gridpoints/OUN/1,1/stations"
                    )
                }
            }
        if "/gridpoints/" in url:
            return station_payload
        station_id = url.split("/stations/")[1].split("/", 1)[0]
        return payloads[station_id]

    result = fetch_surface_observations(
        35.0,
        -97.0,
        REQUESTED,
        opener=opener,
        workers=2,
    )
    assert [item.station.station_id for item in result.observations] == ["KAAA", "KBBB"]
    assert result.unmatched_station_ids == ("KCCC",)
    assert result.stale_count == 1
    assert result.observations[0].offset_seconds(REQUESTED) == -600
    assert result.queried_station_count == 3
    assert result.unqueried_station_count == 0


def test_fetch_does_not_claim_unqueried_stations_are_unavailable():
    station_payload = {
        "features": [
            _station_feature("KAAA", -97.0, 35.0),
            _station_feature("KBBB", -97.4, 35.2),
            _station_feature("KCCC", -98.0, 35.4),
        ]
    }

    def opener(url, **_kwargs):
        if "/points/" in url:
            return {
                "properties": {
                    "observationStations": (
                        "https://api.weather.gov/gridpoints/OUN/1,1/stations"
                    )
                }
            }
        if "/gridpoints/" in url:
            return station_payload
        station_id = url.split("/stations/")[1].split("/", 1)[0]
        return {
            "features": [
                _observation_feature(station_id, "2026-09-13T01:35:00Z")
            ]
        }

    result = fetch_surface_observations(
        35.0, -97.0, REQUESTED, opener=opener, workers=1, max_stations=2
    )

    assert result.queried_station_ids == ("KAAA", "KBBB")
    assert result.unmatched_station_ids == ()
    assert result.unqueried_station_count == 1


def test_decluttering_and_layer_expose_screen_station_payload():
    stations = parse_stations(
        {
            "features": [
                _station_feature("KAAA", -97.00, 35.00),
                _station_feature("KBBB", -97.01, 35.01),
            ]
        }
    )
    observations = tuple(
        parse_observations(
            {"features": [_observation_feature(station.station_id, "2026-09-13T01:35:00Z")]},
            station,
        )[0]
        for station in stations
    )
    dataset = SurfaceObservationSet(REQUESTED, 35, -97, observations, 2)
    shown = declutter_observations(
        observations,
        (-98, -96, 34, 36),
        pixel_size=(400, 300),
        minimum_spacing_px=50,
    )
    assert len(shown) == 1
    layer = surface_overlay_layer(
        dataset,
        (-98, -96, 34, 36),
        pixel_size=(400, 300),
        minimum_spacing_px=50,
    )
    assert layer.key == SURFACE_OVERLAY_KEY
    assert len(layer.shapes) == 1
    assert layer.shapes[0].station_id == "KAAA"
    assert layer.shapes[0].dewpoint_c == 15.0
    assert "1 displayed / 2 time-matched / 2 nearby" in layer.subtitle


def test_layer_styles_calm_missing_stale_and_checked_unavailable_separately():
    available = SurfaceStation("KCALM", "Calm", 35.0, -97.0)
    missing = SurfaceStation("KMISS", "Missing wind", 35.8, -97.0)
    unavailable = SurfaceStation("KNONE", "No observation", 34.2, -97.0)
    calm = SurfaceObservation(available, REQUESTED, 20.0, 15.0, None, 0.0)
    stale_missing = SurfaceObservation(
        missing,
        REQUESTED - timedelta(minutes=45),
        19.0,
        14.0,
        None,
        None,
    )
    dataset = SurfaceObservationSet(
        REQUESTED,
        35.0,
        -97.0,
        (calm, stale_missing),
        4,
        ("KNONE",),
        queried_station_ids=("KCALM", "KMISS", "KNONE"),
        station_catalog=(available, missing, unavailable),
    )

    layer = surface_overlay_layer(
        dataset,
        (-99.0, -95.0, 33.0, 37.0),
        pixel_size=(800, 600),
        minimum_spacing_px=20,
        max_stations=6,
    )
    by_id = {shape.station_id: shape for shape in layer.shapes}

    assert by_id["KCALM"].wind_state == "calm"
    assert "calm (0 kt)" in by_id["KCALM"].description
    assert by_id["KMISS"].wind_state == "missing"
    assert by_id["KMISS"].stale is True
    assert "missing (not calm)" in by_id["KMISS"].description
    assert by_id["KNONE"].availability_state == "unavailable"
    assert "No observation was available" in by_id["KNONE"].description
    assert "1 not queried" in layer.subtitle


def test_surface_set_round_trips_and_exports_explicit_units(tmp_path):
    station = parse_stations({"features": [_station_feature("KAAA", -97, 35)]})[0]
    observation = parse_observations(
        {"features": [_observation_feature("KAAA", "2026-09-13T01:35:00Z")]},
        station,
    )[0]
    dataset = SurfaceObservationSet(REQUESTED, 35, -97, (observation,), 1)
    json_path = write_surface_observations(tmp_path / "surface.json", dataset)
    restored = read_surface_observations(json_path)
    assert restored.as_dict() == dataset.as_dict()
    assert json.loads(json_path.read_text())["attribution"].startswith("NOAA/NWS")
    csv_path = export_surface_csv(tmp_path / "surface.csv", restored)
    text = csv_path.read_text(encoding="utf-8")
    assert "dewpoint_c" in text
    assert "wind_speed_kt" in text
    assert "2026-09-13T01:35:00Z" in text

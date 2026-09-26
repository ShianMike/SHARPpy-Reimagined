"""Common pasted forms, strict axes, and range/ambiguity boundaries."""

import pytest

from sharpmod.coordinate_input import CoordinateInputError, interpret_coordinates


@pytest.mark.parametrize("text,lat,lon", [
    ("35.63, -97.44", 35.63, -97.44),
    ("-97.44 35.63", 35.63, -97.44),
    ("(35.63; -97.44)", 35.63, -97.44),
    ("35.63N 80.44W", 35.63, -80.44),
    ("W 80.44, N 35.63", 35.63, -80.44),
    ("35.63 North, 80.44 West", 35.63, -80.44),
    ("lat=35.63, lon=-80.44", 35.63, -80.44),
    ("Longitude: 80.44W; Latitude: 35.63N", 35.63, -80.44),
    ("lat 35.63 lon −80.44", 35.63, -80.44),
    ("35°30′00″N, 80°15′30″W", 35.5, -80.2583333333),
    ("S 35°30.5', E 120°15'", -35.5083333333, 120.25),
    ("lat: -35°30'20\"; lon: 120°00'00\"", -35.5055555556, 120),
    ("-35S, -80W", -35, -80),
    ("90N, 180E", 90, 180),
    ("90S, 180W", -90, -180),
    (".5N / .25E", .5, .25),
])
def test_unambiguous_forms(text, lat, lon):
    result = interpret_coordinates(text)
    assert result.lat == pytest.approx(lat)
    assert result.lon == pytest.approx(lon)
    assert result.explanation


@pytest.mark.parametrize("text", ["10,20", "35,-80", "0 0", "45 90"])
def test_ambiguous_pairs_require_explicit_axis_correction(text):
    with pytest.raises(CoordinateInputError, match="Both values"):
        interpret_coordinates(text)
    first, second = map(float, text.replace(",", " ").split())
    assert interpret_coordinates(text, order="lat_lon").lat == first
    assert interpret_coordinates(text, order="lon_lat").lat == second


@pytest.mark.parametrize("text", [
    "", "35", "NaN, 80", "inf, 80", "35, 80, 90", "35,5,-80,1",
    "91N 80W", "35N 181E", "90°00′01″N, 80W", "35°60′N, 80W",
    "35°30′60″N, 80W", "35.5°10′N, 80W", "35°30.5′10″N, 80W",
    "-35N, 80W", "+35S, 80W", "35N 80S", "lat: 35E lon: 80N",
    "lat:35 lat:80", "Longitude:80", "lat:91 lon:120", "120, 150",
    "35N 80W trailing", "https://example.com/35N/80W", "35 10 20N, 80W",
])
def test_invalid_or_conflicting_forms_are_not_guessed(text):
    with pytest.raises(CoordinateInputError):
        interpret_coordinates(text)


def test_explicit_order_still_validates_ranges_and_does_not_override_labels():
    with pytest.raises(CoordinateInputError, match="Latitude"):
        interpret_coordinates("120, 35", order="lat_lon")
    with pytest.raises(CoordinateInputError, match="Longitude"):
        interpret_coordinates("35, 350", order="lat_lon")
    assert interpret_coordinates("lat:35 lon:80", order="lon_lat").lat == 35
    with pytest.raises(CoordinateInputError, match="Choose"):
        interpret_coordinates("35, 80", order="guess")

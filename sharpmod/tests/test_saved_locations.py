"""Saved/recent point location persistence regressions."""

from qtpy.QtCore import QSettings
import pytest

from sharpmod.saved_locations import (
    LocationFormatError,
    RECENT_SETTINGS_KEY,
    SavedLocationStore,
)


def _settings(tmp_path):
    return QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)


def test_saved_locations_round_trip_and_case_insensitive_upsert(tmp_path):
    store = SavedLocationStore(_settings(tmp_path))

    store.upsert("Norman", 35.18, -97.44)
    store.upsert("NORMAN", 35.22, -97.50)

    assert [(item.name, item.lat, item.lon) for item in store.load()] == [
        ("NORMAN", 35.22, -97.50)
    ]


def test_recent_points_are_bounded_and_deduplicate_coordinates(tmp_path):
    store = SavedLocationStore(
        _settings(tmp_path), key=RECENT_SETTINGS_KEY, max_entries=2
    )

    store.remember_recent(35, -97, "first")
    store.remember_recent(36, -98, "second")
    store.remember_recent(35, -97, "updated")

    assert [item.name for item in store.load()] == ["updated", "second"]


def test_import_export_is_versioned_and_atomic(tmp_path):
    first = SavedLocationStore(_settings(tmp_path / "one"))
    first.upsert("Guam", 13.45, 144.8)
    exported = first.export_file(tmp_path / "points.json")
    second = SavedLocationStore(_settings(tmp_path / "two"))

    second.import_file(exported)

    assert second.load() == first.load()


@pytest.mark.parametrize(
    ("name", "lat", "lon"),
    [("", 0, 0), ("bad", 91, 0), ("bad", 0, 181), ("bad", "x", 0)],
)
def test_invalid_locations_are_rejected(name, lat, lon, tmp_path):
    store = SavedLocationStore(_settings(tmp_path))

    with pytest.raises(LocationFormatError):
        store.upsert(name, lat, lon)

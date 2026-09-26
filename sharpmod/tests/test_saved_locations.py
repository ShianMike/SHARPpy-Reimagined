"""Saved/recent point location persistence regressions."""

import json

from qtpy.QtCore import QSettings
import pytest

from sharpmod.saved_locations import (
    LOCATION_FORMAT,
    LOCATION_VERSION,
    LocationFormatError,
    RECENT_SETTINGS_KEY,
    SAVED_SETTINGS_KEY,
    SavedLocationStore,
    generated_recent_label,
    is_generated_recent_label,
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


def test_unnamed_recent_label_is_stable_and_distinguishable(tmp_path):
    store = SavedLocationStore(
        _settings(tmp_path), key=RECENT_SETTINGS_KEY, max_entries=2
    )

    recent = store.remember_recent(35.12344, -97.56784)

    assert recent.name == generated_recent_label(recent.lat, recent.lon)
    assert is_generated_recent_label(recent.name, recent.lat, recent.lon)
    assert not is_generated_recent_label("Norman", recent.lat, recent.lon)


def test_import_export_is_versioned_and_atomic(tmp_path):
    first = SavedLocationStore(_settings(tmp_path / "one"))
    first.upsert("Guam", 13.45, 144.8, group="Pacific sites")
    exported = first.export_file(tmp_path / "points.json")
    second = SavedLocationStore(_settings(tmp_path / "two"))

    second.import_file(exported)

    assert second.load() == first.load()


def test_version_one_locations_migrate_to_ungrouped(tmp_path):
    settings = _settings(tmp_path)
    settings.setValue(
        SAVED_SETTINGS_KEY,
        json.dumps({
            "format": LOCATION_FORMAT,
            "version": 1,
            "locations": [{"name": "Legacy", "lat": 35, "lon": -97}],
        }),
    )
    store = SavedLocationStore(settings)

    assert store.load()[0].group == ""

    store.save(store.load())
    migrated = json.loads(settings.value(SAVED_SETTINGS_KEY, "", str))
    assert migrated["version"] == LOCATION_VERSION
    assert migrated["locations"][0]["group"] == ""


def test_coordinate_update_preserves_existing_group_by_default(tmp_path):
    store = SavedLocationStore(_settings(tmp_path))
    store.upsert("Norman", 35.18, -97.44, group="Home")

    store.upsert("NORMAN", 35.22, -97.50)

    assert store.load()[0].group == "Home"


@pytest.mark.parametrize(
    ("name", "lat", "lon"),
    [("", 0, 0), ("bad", 91, 0), ("bad", 0, 181), ("bad", "x", 0)],
)
def test_invalid_locations_are_rejected(name, lat, lon, tmp_path):
    store = SavedLocationStore(_settings(tmp_path))

    with pytest.raises(LocationFormatError):
        store.upsert(name, lat, lon)

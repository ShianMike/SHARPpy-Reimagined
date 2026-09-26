"""Source-neutral persistence for reusable geographic boxes (T23.5)."""

import json

import pytest

from sharpmod.box_sounding import BoxRegion
from sharpmod.saved_box_regions import (
    BOX_REGION_SETTINGS_KEY,
    SavedBoxRegionFormatError,
    SavedBoxRegionStore,
)


class _Settings:
    def __init__(self):
        self.values = {}
        self.synced = 0

    def value(self, key, default=None, _type=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value

    def sync(self):
        self.synced += 1


def test_round_trip_keeps_antimeridian_span_without_source_state():
    settings = _Settings()
    store = SavedBoxRegionStore(settings)
    original = BoxRegion.from_corners(45.0, 170.0, 55.0, 190.0)

    saved = store.upsert("Aleutians", original)
    restored = SavedBoxRegionStore(settings).load()[0]

    assert restored == saved
    assert restored.region.crosses_antimeridian
    assert restored.map_corners == (45.0, 170.0, 55.0, 190.0)
    payload = json.loads(settings.values[BOX_REGION_SETTINGS_KEY])
    assert set(payload["regions"][0]) == {
        "name", "lat0", "lat1", "lon0", "lon_span"
    }
    assert settings.synced == 1


def test_upsert_is_case_insensitive_and_last_bounds_win():
    settings = _Settings()
    store = SavedBoxRegionStore(settings)
    store.upsert("Plains", BoxRegion.from_corners(30, -105, 40, -95))
    replacement = store.upsert(
        "PLAINS", BoxRegion.from_corners(32, -102, 38, -96))

    assert store.load() == [replacement]


def test_duplicate_uses_stable_human_names_and_same_bounds():
    store = SavedBoxRegionStore(_Settings())
    region = BoxRegion.from_corners(30, -105, 40, -95)
    store.upsert("Plains", region)

    first = store.duplicate("Plains")
    second = store.duplicate("Plains")

    assert first.name == "Plains copy"
    assert second.name == "Plains copy 2"
    assert first.region == region == second.region


def test_invalid_saved_document_is_reported_not_partially_loaded():
    settings = _Settings()
    settings.values[BOX_REGION_SETTINGS_KEY] = "{not json"

    with pytest.raises(SavedBoxRegionFormatError, match="JSON"):
        SavedBoxRegionStore(settings).load()

"""Recent history compatibility, identity, timestamps, and bounded storage."""

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest
from qtpy.QtCore import QSettings

from sharpmod.recent_destinations import (
    FORMAT, LIMITS, SETTINGS_KEY, VERSION, RecentDestinationStore,
    RecentFormatError, describe_collection, destination,
)
from sharpmod.saved_locations import SavedLocation


def _settings(tmp_path):
    return QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)


def test_legacy_missing_paths_and_locations_are_preserved_without_minted_times(tmp_path):
    settings = _settings(tmp_path)
    missing = tmp_path / "moved.spc"
    settings.setValue("recent_files", [str(missing)])
    location = SavedLocation.create("Manila", 14.6, 120.98)
    entries = RecentDestinationStore(settings).load(locations=[location])
    assert len(entries) == 2
    assert entries[0].target == str(missing)
    assert not entries[0].available()
    assert entries[1].available()
    assert all(entry.last_used is None and "not recorded" in entry.last_used_label for entry in entries)
    assert not settings.contains(SETTINGS_KEY), "reading legacy shortcuts must not mutate them"


def test_path_identity_same_names_types_timestamps_and_reopen(tmp_path):
    settings = _settings(tmp_path)
    store = RecentDestinationStore(settings)
    start = datetime(2026, 9, 16, 8, tzinfo=timezone.utc)
    first = tmp_path / "first" / "same.spc"
    second = tmp_path / "second" / "same.spc"
    store.remember("file", first, details="Location: OAX", now=start)
    store.remember("file", second, details="Location: OUN", now=start + timedelta(seconds=1))
    store.remember("session", first, details="2 soundings", now=start + timedelta(seconds=2))
    entry = store.remember("file", first, details="Location: OAX · Valid: 2014-06-16 19:00 UTC",
                           now=(start + timedelta(seconds=3)).astimezone(timezone(timedelta(hours=8))))
    reread = RecentDestinationStore(QSettings(settings.fileName(), QSettings.IniFormat)).load()
    assert len(reread) == 3
    assert reread[0] == entry
    assert reread[0].last_used == "2026-09-16T08:00:03+00:00"
    assert "08:00:03 UTC" in reread[0].last_used_label
    assert reread[1].kind == "session"
    assert {item.target for item in reread if item.kind == "file"} == {str(first), str(second)}
    assert "OAX" in reread[0].details


def test_each_kind_is_bounded_and_untimed_legacy_entries_follow_timed_entries(tmp_path):
    settings = _settings(tmp_path)
    store = RecentDestinationStore(settings)
    start = datetime(2026, 9, 16, tzinfo=timezone.utc)
    settings.setValue("recent_files", [str(tmp_path / "legacy.spc")])
    for number in range(10):
        store.remember("file", tmp_path / f"{number}.spc", now=start + timedelta(seconds=number))
        store.remember("session", tmp_path / f"{number}.sharpmod-session", now=start + timedelta(seconds=number))
    entries = store.load()
    assert sum(item.kind == "file" for item in entries) == LIMITS["file"]
    assert sum(item.kind == "session" for item in entries) == LIMITS["session"]
    assert not any(item.target.endswith("legacy.spc") for item in entries)
    assert any(item.target.endswith("9.spc") for item in entries)
    assert not any(item.target.endswith("0.spc") for item in entries)


@pytest.mark.parametrize("version", [2, True, "1", None])
def test_unknown_or_invalid_versions_are_not_overwritten(tmp_path, version):
    settings = _settings(tmp_path)
    raw = json.dumps({"format": FORMAT, "version": version, "destinations": []})
    settings.setValue(SETTINGS_KEY, raw)
    with pytest.raises(RecentFormatError, match="version"):
        RecentDestinationStore(settings).remember("file", tmp_path / "new.spc")
    assert settings.value(SETTINGS_KEY, "", str) == raw


@pytest.mark.parametrize("record", [
    {"kind": [], "target": "x.spc", "label": "x"},
    {"kind": "file", "target": 123, "label": "x"},
    {"kind": "file", "target": "x\0.spc", "label": "x"},
    {"kind": "file", "target": "x.spc", "label": "x", "last_used": "2026-09-16T08:00:00"},
    {"kind": "location", "target": "bad", "label": "x", "lat": 91, "lon": 80},
    {"kind": "location", "target": "bad", "label": "x", "lat": False, "lon": 80},
])
def test_invalid_metadata_is_not_guessed_or_written(tmp_path, record):
    settings = _settings(tmp_path)
    raw = json.dumps({"format": FORMAT, "version": VERSION, "destinations": [record]})
    settings.setValue(SETTINGS_KEY, raw)
    with pytest.raises(RecentFormatError):
        RecentDestinationStore(settings).load()
    assert settings.value(SETTINGS_KEY, "", str) == raw


def test_location_dateline_identity_and_reported_collection_details(tmp_path):
    store = RecentDestinationStore(_settings(tmp_path))
    store.remember("location", label="Dateline", lat=35, lon=180)
    store.remember("location", label="Same point", lat=35, lon=-180)
    assert len(store.load()) == 1
    assert store.load()[0].label == "Same point"
    assert destination("location", label="Point", lat=35, lon=180).lon == -180
    metadata = {"loc": "OAX", "model": "Archive", "run": None}
    collection = SimpleNamespace(getMeta=lambda key: metadata.get(key),
                                 getCurrentDate=lambda: datetime(2014, 6, 16, 19))
    details = describe_collection(collection)
    assert "Location: OAX" in details and "Model: Archive" in details
    assert "Valid: 2014-06-16 19:00 UTC" in details
    assert "Initialization" not in details
    assert describe_collection(object()) == "Sounding"

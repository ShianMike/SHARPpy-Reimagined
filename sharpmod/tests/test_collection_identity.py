"""Organization is a metadata-only presentation of authoritative profile IDs."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sharpmod.collection_identity import collection_identities, describe_identity, organize_identities


class Collection:
    def __init__(self, **metadata):
        self.metadata = metadata

    def getMeta(self, key):  # noqa: N802
        return self.metadata[key]

    def getCurrentDate(self):  # noqa: N802
        return self.metadata.get("valid")

    def isModified(self):  # noqa: N802
        return self.metadata.get("modified", False)


def test_search_full_identity_source_and_times_without_modifying_collections():
    collection = Collection(loc="São Paulo", model="HRRR", observed=False,
                            source_provider_name="NOAA", run=datetime(2026, 9, 15, 12),
                            valid=datetime(2026, 9, 15, 21, tzinfo=timezone(timedelta(hours=8))))
    widget = SimpleNamespace(prof_ids=["authoritative-ID"], prof_collections=[collection])
    identity, = collection_identities(widget)
    assert identity.matches("sao NOAA 13:00")
    assert identity.matches("authoritative-ID forecast")
    assert not identity.matches("14:00")
    assert "Valid: 2026-09-15 13:00 UTC" in identity.label
    assert identity.initialization.tzinfo is timezone.utc
    assert collection.metadata["run"].tzinfo is None
    assert widget.prof_ids == ["authoritative-ID"]
    assert widget.prof_collections == [collection]


@pytest.mark.parametrize("sort", ["location", "source", "initialization", "valid_time"])
def test_sort_unknowns_last_and_group_without_reordering_scientific_state(sort):
    first = Collection(loc="Zulu", model="NAM", run=datetime(2026, 9, 15, 12), valid=datetime(2026, 9, 15, 15))
    second = Collection(loc="Alpha", model="GFS", run=datetime(2026, 9, 15, 0), valid=datetime(2026, 9, 15, 3))
    widget = SimpleNamespace(prof_ids=["Z", "A", "unknown"], prof_collections=[first, second, Collection()])
    identities = collection_identities(widget)
    rows = organize_identities(identities, sort=sort)
    assert [entry.profile_id for _group, entry in rows] == ["A", "Z", "unknown"]
    rows = organize_identities(identities, sort=sort, reverse=True)
    assert [entry.profile_id for _group, entry in rows] == ["Z", "A", "unknown"]
    grouped = organize_identities(identities, sort=sort, group=sort)
    assert sum(entry is None for _group, entry in grouped) == 3
    assert {entry.profile_id for _group, entry in grouped if entry is not None} == set(widget.prof_ids)
    assert widget.prof_ids == ["Z", "A", "unknown"]
    assert widget.prof_collections[0] is first


@pytest.mark.parametrize("metadata, expected", [
    ({"observed": True}, "Observed"), ({"observed": False}, "Forecast"),
    ({"observed": False, "model": "ERA5"}, "Reanalysis"),
    ({"profile_kind": "reanalysis", "observed": False}, "Reanalysis"),
    ({"model": "HRRR"}, "Type not reported"),
])
def test_type_uses_reported_provenance_not_model_color(metadata, expected):
    assert describe_identity("ID", Collection(**metadata)).kind == expected


def test_edited_parent_relationship_remains_visible():
    edited = describe_identity("ID", Collection(loc="KOUN", observed=True, modified=True))
    assert edited.type_label == "Edited observed"
    assert "Loaded source baseline" in edited.parent
    unknown = describe_identity("RAW ID", Collection())
    assert unknown.label == "RAW ID"
    assert "Type not reported" in unknown.details


def test_invalid_organization_keys_fall_back_to_loaded_order():
    widget = SimpleNamespace(prof_ids=["B", "A"], prof_collections=[Collection(), Collection()])
    assert [entry.profile_id for _group, entry in organize_identities(
        collection_identities(widget), sort="invalid", group="invalid")] == ["B", "A"]


@pytest.mark.parametrize("kind", ["observed", "reanalysis"])
def test_nonforecast_run_is_not_presented_as_a_forecast_initialization(kind):
    identity = describe_identity("ID", Collection(profile_kind=kind, run=datetime(2026, 9, 15, 12),
                                                valid=datetime(2026, 9, 15, 12)))
    assert identity.initialization is None
    assert "Init:" not in identity.label
    assert "Valid: 2026-09-15 12:00 UTC" in identity.label
    heading, = [label for label, entry in organize_identities([identity], group="initialization") if entry is None]
    assert heading == f"Not applicable ({kind})"


def test_discovery_never_calculates_a_profile_or_calls_highlighted_prof():
    collection = Collection(observed=False)
    collection.getHighlightedProf = lambda: pytest.fail("Organization must not calculate science")
    profile = SimpleNamespace(user_srwind=(1.0, 2.0, 3.0, 4.0), bunkers=(1.0, 2.0, 3.0, 4.0))
    collection._profs, collection._highlight, collection._prof_idx = {"": [profile]}, "", 0
    assert not describe_identity("ID", collection).edited
    profile.user_srwind = (10.0, 2.0, 3.0, 4.0)
    assert describe_identity("ID", collection).edited
    profile.user_srwind = profile.bunkers
    assert not describe_identity("ID", collection).edited

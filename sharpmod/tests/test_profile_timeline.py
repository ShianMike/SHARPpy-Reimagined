from datetime import datetime, timedelta

import pytest

from sharppy.sharptab import prof_collection, profile

from sharpmod.profile_timeline import (
    append_collection,
    combine_collections,
    forecast_hour_range,
)


RUN = datetime(2026, 7, 22, 0)


def _collection(fxx):
    valid = RUN + timedelta(hours=fxx)
    raw = profile.create_profile(
        profile="raw",
        pres=[1000, 900, 800],
        hght=[100, 1000, 2000],
        tmpc=[25, 18, 10],
        dwpc=[20, 12, 4],
        wdir=[180, 200, 220],
        wspd=[10, 20, 30],
        location="TEST",
        date=valid,
        latitude=35.0,
        missing=-9999.0,
    )
    collection = prof_collection.ProfCollection({"": [raw]}, [valid])
    collection.setMeta("loc", "TEST")
    collection.setMeta("model", "RRFS")
    collection.setMeta("observed", False)
    collection.setMeta("run", RUN)
    collection.setMeta("fxx", fxx)
    collection.setMeta("npz_path", f"f{fxx:03d}.npz")
    return collection


def test_forecast_hour_range_uses_only_available_hours():
    assert forecast_hour_range(range(0, 19), 3, 12, 3) == (3, 6, 9, 12)
    assert forecast_hour_range((0, 3, 6, 9), 0, 9, 3) == (0, 3, 6, 9)


@pytest.mark.parametrize(
    "start,end,step",
    ((9, 3, 1), (0, 9, 0), (1, 9, 2), (0, 8, 3)),
)
def test_forecast_hour_range_rejects_ambiguous_ranges(start, end, step):
    with pytest.raises(ValueError):
        forecast_hour_range((0, 3, 6, 9), start, end, step)


def test_combine_collections_sorts_and_preserves_timeline_metadata():
    combined = combine_collections((_collection(6), _collection(0), _collection(3)))
    assert combined._dates == [RUN, RUN + timedelta(hours=3), RUN + timedelta(hours=6)]
    assert combined.getMeta("timeline") is True
    assert combined.getMeta("timeline_hours") == [0, 3, 6]
    assert combined.getMeta("timeline_count") == 3
    assert len(combined._profs[""]) == 3


def test_combine_collections_rejects_duplicate_valid_time():
    with pytest.raises(ValueError, match="duplicate"):
        combine_collections((_collection(0), _collection(0)))


def test_append_collection_streams_sorted_hour_and_preserves_focus():
    timeline = combine_collections((_collection(3), _collection(6)))
    timeline.setCurrentDate(RUN + timedelta(hours=6))

    index = append_collection(timeline, _collection(0))

    assert index == 0
    assert timeline._dates == [
        RUN, RUN + timedelta(hours=3), RUN + timedelta(hours=6)
    ]
    assert timeline.getCurrentDate() == RUN + timedelta(hours=6)
    assert timeline.getMeta("timeline_hours") == [0, 3, 6]
    assert timeline.getMeta("timeline_sources") == [
        "f000.npz", "f003.npz", "f006.npz"
    ]
    assert [
        metadata["fxx"]
        for metadata in timeline.getMeta("timeline_provenance")
    ] == [0, 3, 6]


def test_append_collection_rejects_duplicate_time():
    timeline = combine_collections((_collection(0), _collection(3)))
    with pytest.raises(ValueError, match="already contains"):
        append_collection(timeline, _collection(3))

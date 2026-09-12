from datetime import datetime, timedelta

import pytest

from sharppy.sharptab import prof_collection, profile

from sharpmod.profile_timeline import (
    append_collection,
    combine_collections,
    combine_ensemble_collections,
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


def test_combine_ensemble_collections_builds_true_member_collection():
    control = _collection(0)
    perturbed = _collection(0)
    control.setMeta("member", "c00")
    perturbed.setMeta("member", "p01")
    control_profiles = control._profs[""]
    perturbed_profiles = perturbed._profs[""]
    control_metadata = dict(control._meta)
    target_type = control._target_type

    combined = combine_ensemble_collections((control, perturbed))

    assert combined._dates == [RUN]
    assert tuple(combined._profs) == ("c00", "p01")
    assert combined._profs["c00"] == control_profiles
    assert combined._profs["p01"] == perturbed_profiles
    assert combined._profs["c00"] is not control_profiles
    assert combined._profs["p01"] is not perturbed_profiles
    assert combined._target_type is target_type
    assert combined.isEnsemble()
    assert combined.getMeta("loc") == "TEST"
    assert combined.getMeta("ensemble") is True
    assert combined.getMeta("ensemble_count") == 2
    assert combined.getMeta("ensemble_member_count") == 2
    assert combined.getMeta("ensemble_members") == ["c00", "p01"]
    assert [
        item["member_label"] for item in combined.getMeta("ensemble_provenance")
    ] == ["c00", "p01"]
    assert control._profs[""] is control_profiles
    assert perturbed._profs[""] is perturbed_profiles
    assert control._meta == control_metadata


def test_combine_ensemble_collections_honors_requested_member_names():
    combined = combine_ensemble_collections(
        (_collection(0), _collection(0)),
        member_names=("control", "perturbed"),
    )

    assert tuple(combined._profs) == ("control", "perturbed")
    assert combined._highlight == "control"


@pytest.mark.parametrize(
    "member_names,match",
    (
        (("p01", "p01"), "unique"),
        (("p01",), "one label"),
        (("p01", ""), "non-empty"),
    ),
)
def test_combine_ensemble_collections_rejects_bad_member_names(
    member_names,
    match,
):
    with pytest.raises(ValueError, match=match):
        combine_ensemble_collections(
            (_collection(0), _collection(0)),
            member_names=member_names,
        )


def test_combine_ensemble_collections_rejects_mismatched_times():
    with pytest.raises(ValueError, match="same valid time"):
        combine_ensemble_collections(
            (_collection(0), _collection(3)),
            member_names=("c00", "p01"),
        )


def test_combine_ensemble_collections_rejects_empty_or_multitime_inputs():
    with pytest.raises(ValueError, match="at least one"):
        combine_ensemble_collections(())

    multi_time = combine_collections((_collection(0), _collection(3)))
    with pytest.raises(ValueError, match="exactly one valid time"):
        combine_ensemble_collections((multi_time,), member_names=("c00",))


def test_append_collection_streams_sorted_hour_and_preserves_focus():
    timeline = combine_collections((_collection(3), _collection(6)))
    timeline.setCurrentDate(RUN + timedelta(hours=6))

    index = append_collection(timeline, _collection(0))

    assert index == 0
    assert timeline._dates == [RUN, RUN + timedelta(hours=3), RUN + timedelta(hours=6)]
    assert timeline.getCurrentDate() == RUN + timedelta(hours=6)
    assert timeline.getMeta("timeline_hours") == [0, 3, 6]
    assert timeline.getMeta("timeline_sources") == ["f000.npz", "f003.npz", "f006.npz"]
    assert [
        metadata["fxx"] for metadata in timeline.getMeta("timeline_provenance")
    ] == [0, 3, 6]


def test_append_collection_preserves_modification_state_for_reset():
    timeline = combine_collections((_collection(3), _collection(6)))
    selected_valid = RUN + timedelta(hours=6)
    timeline.setCurrentDate(selected_valid)
    original_temperature = float(timeline._profs[""][1].tmpc[0])
    original_wind_speed = float(timeline._profs[""][1].wspd[0])

    timeline.modify(
        0,
        tmpc=original_temperature + 5.0,
        wspd=original_wind_speed + 5.0,
    )
    saved_original = timeline._orig_profs[1]

    append_collection(timeline, _collection(0))

    assert timeline.getCurrentDate() == selected_valid
    assert timeline._mod_therm == [False, False, True]
    assert timeline._mod_wind == [False, False, True]
    assert set(timeline._orig_profs) == {2}
    assert timeline._orig_profs[2] is saved_original
    assert timeline.isModified()

    timeline.resetModification("tmpc", "wspd")

    assert float(timeline._profs[""][2].tmpc[0]) == pytest.approx(original_temperature)
    assert float(timeline._profs[""][2].wspd[0]) == pytest.approx(original_wind_speed)
    assert not timeline.isModified()
    assert timeline._orig_profs == {}


def test_append_collection_preserves_interpolation_state_for_undo():
    timeline = combine_collections((_collection(3), _collection(6)))
    selected_valid = RUN + timedelta(hours=3)
    timeline.setCurrentDate(selected_valid)
    original_profile = timeline._profs[""][0]
    interpolated_profile = _collection(3)._profs[""][0]
    timeline._profs[""][0] = interpolated_profile
    timeline._orig_profs[0] = original_profile
    timeline._interp_profs[0] = interpolated_profile
    timeline._interp[0] = True

    append_collection(timeline, _collection(0))

    assert timeline.getCurrentDate() == selected_valid
    assert timeline._interp == [False, True, False]
    assert set(timeline._orig_profs) == {1}
    assert set(timeline._interp_profs) == {1}

    timeline.resetInterpolation()

    assert timeline._profs[""][1] is original_profile
    assert timeline._interp == [False, False, False]
    assert timeline._orig_profs == {}
    assert timeline._interp_profs == {}


def test_append_collection_rejects_duplicate_time():
    timeline = combine_collections((_collection(0), _collection(3)))
    with pytest.raises(ValueError, match="already contains"):
        append_collection(timeline, _collection(3))

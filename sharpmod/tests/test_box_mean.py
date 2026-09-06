"""Tests for averaging a box of soundings into one composite sounding."""

from datetime import datetime, timezone

import numpy as np
import pytest

from sharpmod import box_mean as bm
from sharpmod.box_sounding import BoxRegion, plan_box_samples
from sharpmod.tests._examples import examples_dir


HRRR_NPZ = examples_dir() / "hrrr_point_36.68N_95.66W_f018.npz"

pytestmark = pytest.mark.skipif(
    not HRRR_NPZ.is_file(), reason="no HRRR .npz example sounding")

RUN = datetime(2026, 9, 3, 0, tzinfo=timezone.utc)
VALID = datetime(2026, 9, 3, 18, tzinfo=timezone.utc)


def _region() -> BoxRegion:
    return BoxRegion.from_corners(36.0, -96.5, 37.4, -94.8)


def _source() -> dict:
    return dict(np.load(HRRR_NPZ, allow_pickle=False))


def _write(directory, name, arrays) -> str:
    path = directory / f"{name}.npz"
    np.savez(path, **arrays)
    return str(path)


def _members(directory, count=6, *, warm_step=0.0, moist_step=0.0, cut=0):
    """Write ``count`` perturbed copies of the example sounding."""
    source = _source()
    paths = []
    for index in range(count):
        arrays = {
            key: (value.copy() if hasattr(value, "copy") else value)
            for key, value in source.items()
        }
        for field in bm.MEAN_PROFILE_FIELDS:
            arrays[field] = np.asarray(arrays[field], dtype=float)
        if warm_step:
            real = arrays["tmpc"] > -9000.0
            arrays["tmpc"] = np.where(
                real, arrays["tmpc"] + warm_step * index, arrays["tmpc"])
        if moist_step:
            real = arrays["dwpc"] > -9000.0
            arrays["dwpc"] = np.where(
                real, arrays["dwpc"] + moist_step * index, arrays["dwpc"])
        # Keep the dewpoint physical after any perturbation.
        arrays["dwpc"] = np.minimum(arrays["dwpc"], arrays["tmpc"])
        if cut and index == count - 1:
            for field in bm.MEAN_PROFILE_FIELDS:
                arrays[field] = arrays[field][cut:]
        arrays["lat"] = 36.0 + 0.1 * index
        arrays["lon"] = -96.0
        paths.append(_write(directory, f"m{index:02d}", arrays))
    return paths


# -- the averaged column -------------------------------------------------- #


def test_mean_is_physically_ordered(tmp_path):
    """The result has to satisfy the same contract as any other sounding."""
    profile = bm.box_mean_profile(
        _members(tmp_path), lat=36.7, lon=-96.0)

    pres = np.asarray(profile.pres)
    hght = np.asarray(profile.hght)
    assert np.all(np.diff(pres) < 0.0), "pressure must strictly decrease"
    assert np.all(np.diff(hght) > 0.0), "height must strictly increase"

    tmpc = np.asarray(profile.tmpc)
    dwpc = np.asarray(profile.dwpc)
    real = (tmpc > -9998.0) & (dwpc > -9998.0)
    assert np.all(dwpc[real] <= tmpc[real])

    wspd = np.asarray(profile.wspd)
    wdir = np.asarray(profile.wdir)
    blowing = wspd > -9998.0
    assert np.all(wspd[blowing] >= 0.0)
    assert np.all((wdir[blowing] >= 0.0) & (wdir[blowing] <= 360.0))


def test_mean_temperature_is_the_average_of_the_members(tmp_path):
    """A 1 C per member ramp must land on the arithmetic mean."""
    paths = _members(tmp_path, count=5, warm_step=1.0)
    profile = bm.box_mean_profile(paths, lat=36.7, lon=-96.0)

    base = np.asarray(_source()["tmpc"], dtype=float)
    # Members are base + 0..4 C, so the mean is base + 2 C.
    expected = base[0] + 2.0
    assert profile.tmpc[0] == pytest.approx(expected, abs=0.05)


def test_member_count_and_level_counts_are_reported(tmp_path):
    profile = bm.box_mean_profile(
        _members(tmp_path, count=4), lat=36.7, lon=-96.0)
    assert profile.members == 4
    assert profile.requested == 4
    assert profile.levels == len(profile.pres)
    assert len(profile.counts) == profile.levels
    assert set(profile.counts) == {4}


# -- winds must be averaged as components -------------------------------- #


def test_opposing_directions_average_across_north_not_through_south():
    """350 and 10 degrees average to 0, never to 180."""
    speed = 20.0
    us, vs = [], []
    for direction in (350.0, 10.0):
        radians = np.radians(direction)
        us.append(-speed * np.sin(radians))
        vs.append(-speed * np.cos(radians))
    resolved, bearing = bm._speed_direction(
        np.asarray([float(np.mean(us))]), np.asarray([float(np.mean(vs))]))
    assert bearing[0] == pytest.approx(0.0, abs=0.5)
    assert resolved[0] == pytest.approx(speed * np.cos(np.radians(10.0)), abs=0.5)


def test_a_cancelling_mean_reports_calm_rather_than_due_north():
    """Opposing members cancel; 0 degrees would read as a real northerly."""
    resolved, bearing = bm._speed_direction(
        np.asarray([0.0]), np.asarray([0.0]))
    assert resolved[0] == pytest.approx(0.0)
    assert bearing[0] == pytest.approx(0.0)


def test_components_are_preferred_over_speed_and_direction(tmp_path):
    """An archive carrying u/v must not be re-derived from speed/direction."""
    arrays = _source()
    for field in bm.MEAN_PROFILE_FIELDS:
        arrays[field] = np.asarray(arrays[field], dtype=float)
    levels = arrays["pres"].size
    arrays["uwnd"] = np.full(levels, 7.0)
    arrays["vwnd"] = np.full(levels, -3.0)
    # Deliberately inconsistent, so a fallback would be visible.
    arrays["wspd"] = np.full(levels, 99.0)
    arrays["wdir"] = np.full(levels, 123.0)
    arrays["lat"] = 36.5
    arrays["lon"] = -96.0
    member = bm._read_member(_write(tmp_path, "components", arrays))

    u, v = bm._components(member)
    assert np.allclose(u, 7.0)
    assert np.allclose(v, -3.0)


def test_wind_falls_back_to_speed_and_direction_without_components(tmp_path):
    arrays = _source()
    for field in bm.MEAN_PROFILE_FIELDS:
        arrays[field] = np.asarray(arrays[field], dtype=float)
    levels = arrays["pres"].size
    arrays["wspd"] = np.full(levels, 10.0)
    arrays["wdir"] = np.full(levels, 270.0)   # from the west
    arrays["lat"] = 36.5
    arrays["lon"] = -96.0
    arrays.pop("uwnd", None)
    arrays.pop("vwnd", None)
    member = bm._read_member(_write(tmp_path, "polar", arrays))

    u, v = bm._components(member)
    # A westerly blows toward the east: positive u, no v.
    assert np.allclose(u, 10.0, atol=1e-6)
    assert np.allclose(v, 0.0, atol=1e-6)


# -- moisture ------------------------------------------------------------- #


def test_moisture_is_averaged_as_mixing_ratio_not_dewpoint(tmp_path):
    """Averaging dewpoint directly biases the column; mixing ratio does not."""
    from sharppy.sharptab import thermo

    paths = _members(tmp_path, count=2, moist_step=-25.0)
    profile = bm.box_mean_profile(paths, lat=36.7, lon=-96.0)

    source = _source()
    pressure = float(np.asarray(source["pres"], dtype=float)[0])
    dewpoints = [
        float(np.asarray(source["dwpc"], dtype=float)[0]),
        float(np.asarray(source["dwpc"], dtype=float)[0]) - 25.0,
    ]
    ratios = [float(thermo.mixratio(pressure, value)) for value in dewpoints]
    expected = float(thermo.temp_at_mixrat(float(np.mean(ratios)), pressure))
    naive = float(np.mean(dewpoints))

    assert profile.dwpc[0] == pytest.approx(expected, abs=0.3)
    # The two answers must actually differ, or this test proves nothing.
    assert abs(expected - naive) > 1.0
    assert profile.dwpc[0] != pytest.approx(naive, abs=0.5)


def test_supersaturation_from_the_convex_curve_is_clamped(tmp_path):
    """Mean mixing ratio can exceed saturation at the mean temperature."""
    source = _source()
    paths = []
    for index, offset in enumerate((-20.0, 20.0)):
        arrays = {
            key: (value.copy() if hasattr(value, "copy") else value)
            for key, value in source.items()
        }
        temperature = np.asarray(arrays["tmpc"], dtype=float) + offset
        arrays["tmpc"] = temperature
        arrays["dwpc"] = temperature.copy()      # saturated everywhere
        arrays["lat"] = 36.0 + 0.1 * index
        arrays["lon"] = -96.0
        paths.append(_write(tmp_path, f"sat{index}", arrays))

    profile = bm.box_mean_profile(paths, lat=36.5, lon=-96.0)
    assert profile.clamped_dewpoints > 0

    tmpc = np.asarray(profile.tmpc)
    dwpc = np.asarray(profile.dwpc)
    real = (tmpc > -9998.0) & (dwpc > -9998.0)
    assert np.all(dwpc[real] <= tmpc[real])


# -- terrain -------------------------------------------------------------- #


def test_only_the_layer_every_member_shares_is_averaged(tmp_path):
    """The mean starts at the highest ground, not at the deepest member."""
    paths = _members(tmp_path, count=4, cut=6)
    profile = bm.box_mean_profile(paths, lat=36.7, lon=-96.0)

    ladder = np.asarray(_source()["pres"], dtype=float)
    assert profile.trimmed_below == 6
    assert profile.surface_pressure_hpa == pytest.approx(ladder[6])
    # Nothing thinned out: every surviving level has every member.
    assert set(profile.counts) == {4}


def test_the_trim_count_does_not_depend_on_the_requested_centre(tmp_path):
    """The ladder comes from the deepest member, not the nearest one.

    Keying it off proximity made the reported trim depend on which member
    happened to sit closest to the centre, so the same box could report 0 or 6
    trimmed levels for the same terrain.
    """
    paths = _members(tmp_path, count=4, cut=6)
    trims = {
        bm.box_mean_profile(paths, lat=lat, lon=-96.0).trimmed_below
        for lat in (36.0, 36.15, 36.3, 40.0)
    }
    assert trims == {6}


def test_a_box_spanning_too_much_terrain_is_refused(tmp_path):
    """Fewer than two shared levels is not a column and must be said so."""
    source = _source()
    pressure = np.asarray(source["pres"], dtype=float)
    # One member only low down, the other only high up, so the two do not
    # overlap at all -- the extreme of a box straddling a mountain range.
    masks = (pressure >= 250.0, pressure <= 200.0)
    paths = []
    for index, mask in enumerate(masks):
        arrays = {
            key: (value.copy() if hasattr(value, "copy") else value)
            for key, value in source.items()
        }
        for field in bm.MEAN_PROFILE_FIELDS:
            arrays[field] = np.asarray(arrays[field], dtype=float)[mask]
        arrays["lat"] = 36.0 + 0.1 * index
        arrays["lon"] = -96.0
        paths.append(_write(tmp_path, f"steep{index}", arrays))

    with pytest.raises(bm.BoxMeanError, match="terrain"):
        bm.box_mean_profile(paths, lat=36.5, lon=-96.0)


# -- inputs and refusals -------------------------------------------------- #


def test_a_mapping_of_outputs_is_accepted(tmp_path):
    """A box extraction hands over ``request_id -> path``."""
    paths = _members(tmp_path, count=3)
    outputs = {f"r000c{index:03d}": path for index, path in enumerate(paths)}
    profile = bm.box_mean_profile(outputs, lat=36.7, lon=-96.0)
    assert profile.members == 3


def test_an_unreadable_member_is_counted_not_fatal(tmp_path):
    paths = _members(tmp_path, count=3)
    profile = bm.box_mean_profile(
        list(paths) + [str(tmp_path / "absent.npz")], lat=36.7, lon=-96.0)
    assert profile.members == 3
    assert profile.requested == 4
    assert "unreadable" in profile.describe()


@pytest.mark.parametrize(
    "outputs, match",
    [
        ([], "no soundings"),
        ("one.npz", "collection"),
    ],
)
def test_bad_input_is_refused(outputs, match):
    with pytest.raises(bm.BoxMeanError, match=match):
        bm.box_mean_profile(outputs)


def test_one_member_is_not_an_area_average(tmp_path):
    paths = _members(tmp_path, count=1)
    with pytest.raises(bm.BoxMeanError, match="at least 2"):
        bm.box_mean_profile(paths, lat=36.7, lon=-96.0)


def test_the_centre_falls_back_to_the_members(tmp_path):
    """Without an explicit centre, the members' mean position is used."""
    profile = bm.box_mean_profile(_members(tmp_path, count=3))
    assert profile.lat == pytest.approx(36.1)
    assert profile.lon == pytest.approx(-96.0)


def test_a_member_without_coordinates_cannot_supply_a_centre(tmp_path):
    source = _source()
    paths = []
    for index in range(2):
        arrays = {
            key: (value.copy() if hasattr(value, "copy") else value)
            for key, value in source.items()
        }
        arrays.pop("lat", None)
        arrays.pop("lon", None)
        paths.append(_write(tmp_path, f"nowhere{index}", arrays))
    with pytest.raises(bm.BoxMeanError, match="centre"):
        bm.box_mean_profile(paths)


# -- the portable output -------------------------------------------------- #


def test_written_mean_passes_the_portable_sounding_contract(tmp_path):
    from sharpmod.portable_sounding import validate_portable_sounding_pair

    profile = bm.box_mean_profile(
        _members(tmp_path), lat=36.7, lon=-96.0)
    target = tmp_path / "out" / "box-mean.npz"
    written = bm.write_box_mean_sounding(
        profile, target, model="HRRR", run_time=RUN, valid_time=VALID, fxx=18)

    assert validate_portable_sounding_pair(written)
    assert (tmp_path / "out" / "box-mean.json").is_file()


def test_the_written_label_lets_the_town_lookup_run(tmp_path):
    """A grid-point id defeats the lookup; a coordinate label invites it."""
    from sharpmod.place_names import needs_town_name

    profile = bm.box_mean_profile(
        _members(tmp_path), lat=36.7, lon=-96.0)
    written = bm.write_box_mean_sounding(
        profile, tmp_path / "labelled.npz", model="HRRR",
        run_time=RUN, valid_time=VALID)

    with np.load(written, allow_pickle=False) as data:
        label = str(data["loc"])
    assert label == "HRRR 36.70, -96.00"
    assert needs_town_name(label, "HRRR") is True


def test_an_explicit_label_is_kept(tmp_path):
    profile = bm.box_mean_profile(
        _members(tmp_path), lat=36.7, lon=-96.0)
    written = bm.write_box_mean_sounding(
        profile, tmp_path / "named.npz", model="HRRR",
        run_time=RUN, valid_time=VALID, loc="Tulsa area")
    with np.load(written, allow_pickle=False) as data:
        assert str(data["loc"]) == "Tulsa area"


def test_the_sidecar_records_how_the_mean_was_made(tmp_path):
    import json

    profile = bm.box_mean_profile(
        _members(tmp_path, count=5, cut=4), lat=36.7, lon=-96.0)
    written = bm.write_box_mean_sounding(
        profile, tmp_path / "provenance.npz", model="HRRR", model_key="hrrr",
        run_time=RUN, valid_time=VALID, fxx=18, spacing_km=15.0,
        box=(36.0, -96.5, 37.4, -94.8))

    meta = json.loads(
        (tmp_path / "provenance.json").read_text(encoding="utf-8"))
    assert meta["box_mean"] is True
    assert meta["box_mean_members"] == 5
    assert meta["box_mean_trimmed_below"] == 4
    assert meta["model_key"] == "hrrr"
    assert meta["box_mean_spacing_km"] == pytest.approx(15.0)
    assert len(meta["box_mean_bounds"]) == 4
    assert "not the mean of the members" in meta["box_mean_note"]
    # Every member arrived through the verified surface contract and the mean
    # starts at the highest ground, so this claim is a real one.
    assert meta["below_ground_levels_removed"] is True


def test_the_written_mean_decodes_like_any_other_sounding(tmp_path):
    from sharpmod import render

    profile = bm.box_mean_profile(
        _members(tmp_path), lat=36.7, lon=-96.0)
    written = bm.write_box_mean_sounding(
        profile, tmp_path / "decodable.npz", model="HRRR",
        run_time=RUN, valid_time=VALID, fxx=18)

    prof_col, stn_id = render.decode(written)
    assert stn_id == "HRRR 36.70, -96.00"
    # A real datetime is the first gate the outlook overlay checks.
    assert isinstance(prof_col.getCurrentDate(), datetime)
    assert prof_col.getMeta("lat") == pytest.approx(36.7)
    assert prof_col.getMeta("lon") == pytest.approx(-96.0)


def test_derived_parameters_resolve_on_the_mean(tmp_path):
    from sharpmod import box_analysis as ba
    from sharpmod import render

    profile = bm.box_mean_profile(
        _members(tmp_path), lat=36.7, lon=-96.0)
    written = bm.write_box_mean_sounding(
        profile, tmp_path / "derived.npz", model="HRRR",
        run_time=RUN, valid_time=VALID, fxx=18)

    prof_col, _stn = render.decode(written)
    values = ba.fast_values(prof_col.getHighlightedProf())
    assert values["mucape"] >= values["mlcape"]
    assert np.isfinite(values["mucape"])


def test_write_refuses_a_non_profile(tmp_path):
    with pytest.raises(bm.BoxMeanError, match="BoxMeanProfile"):
        bm.write_box_mean_sounding(
            object(), tmp_path / "nope.npz", model="HRRR",
            run_time=RUN, valid_time=VALID)


def test_write_refuses_a_non_datetime(tmp_path):
    profile = bm.box_mean_profile(
        _members(tmp_path), lat=36.7, lon=-96.0)
    with pytest.raises(bm.BoxMeanError, match="datetimes"):
        bm.write_box_mean_sounding(
            profile, tmp_path / "nope.npz", model="HRRR",
            run_time="2026-09-03", valid_time=VALID)


def test_describe_reports_the_caveats(tmp_path):
    profile = bm.box_mean_profile(
        _members(tmp_path, count=4, cut=3), lat=36.7, lon=-96.0)
    text = profile.describe()
    assert "mean of 4 soundings" in text
    assert "hPa" in text
    assert "shares" in text


def test_a_real_plan_of_points_averages(tmp_path):
    """The shape a box extraction actually produces."""
    plan = plan_box_samples("hrrr", _region(), target_points=16)
    source = _source()
    outputs = {}
    for node in plan.requestable_points:
        arrays = {
            key: (value.copy() if hasattr(value, "copy") else value)
            for key, value in source.items()
        }
        arrays["lat"] = float(node.lat)
        arrays["lon"] = float(node.lon)
        outputs[node.request_id] = _write(tmp_path, node.request_id, arrays)

    profile = bm.box_mean_profile(
        outputs,
        lat=plan.region.center_lat,
        lon=plan.region.center_lon,
    )
    assert profile.members == len(plan.requestable_points)
    assert profile.lat == pytest.approx(plan.region.center_lat)


# -- saying that it is a mean --------------------------------------------- #


@pytest.mark.parametrize(
    "members, expected",
    [
        (132, "HRRR box mean of 132"),
        (2, "HRRR box mean of 2"),
        (0, "HRRR box mean"),
        (None, "HRRR box mean"),
    ],
)
def test_the_display_model_names_the_average(members, expected):
    assert bm.mean_model_label("HRRR", members) == expected


def test_the_display_model_survives_a_missing_product_name():
    assert bm.mean_model_label("", 5) == "MODEL box mean of 5"
    assert bm.mean_model_label(None, 5) == "MODEL box mean of 5"


def test_the_written_model_field_says_it_is_a_mean(tmp_path):
    """The Skew-T draws its title from the model name, so it lives there."""
    profile = bm.box_mean_profile(
        _members(tmp_path, count=4), lat=36.7, lon=-96.0)
    written = bm.write_box_mean_sounding(
        profile, tmp_path / "marked.npz", model="HRRR",
        run_time=RUN, valid_time=VALID, fxx=18)

    with np.load(written, allow_pickle=False) as data:
        assert str(data["model"]) == "HRRR box mean of 4"


def test_the_sidecar_keeps_the_plain_product_name(tmp_path):
    """The decorated name is for reading; provenance stays machine-readable."""
    import json

    profile = bm.box_mean_profile(
        _members(tmp_path, count=4), lat=36.7, lon=-96.0)
    bm.write_box_mean_sounding(
        profile, tmp_path / "plain.npz", model="HRRR", model_key="hrrr",
        run_time=RUN, valid_time=VALID, fxx=18)

    meta = json.loads((tmp_path / "plain.json").read_text(encoding="utf-8"))
    assert meta["model"] == "HRRR box mean of 4"
    assert meta["model_label"] == "HRRR"
    assert meta["model_key"] == "hrrr"


def test_the_skewt_title_states_the_average(tmp_path):
    """End to end: what the plot actually prints above the sounding."""
    from types import SimpleNamespace

    from sharpmod import render

    render.install_render_patches()
    from sharppy.viz.skew import plotSkewT

    profile = bm.box_mean_profile(
        _members(tmp_path, count=4), lat=36.7, lon=-96.0)
    written = bm.write_box_mean_sounding(
        profile, tmp_path / "titled.npz", model="HRRR",
        run_time=RUN, valid_time=VALID, fxx=18)

    prof_col, _stn = render.decode(written)
    title = plotSkewT.getPlotTitle(SimpleNamespace(prof=None), prof_col)

    assert "box mean of 4" in title


def test_the_decorated_model_name_still_invites_the_town_lookup(tmp_path):
    """Decorating the model must not accidentally satisfy the label check."""
    from sharpmod.place_names import needs_town_name

    profile = bm.box_mean_profile(
        _members(tmp_path, count=4), lat=36.7, lon=-96.0)
    written = bm.write_box_mean_sounding(
        profile, tmp_path / "lookup.npz", model="HRRR",
        run_time=RUN, valid_time=VALID, fxx=18)

    with np.load(written, allow_pickle=False) as data:
        label = str(data["loc"])
        model = str(data["model"])
    assert needs_town_name(label, model) is True

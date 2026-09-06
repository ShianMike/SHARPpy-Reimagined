"""Derived-parameter fields, area statistics, and transects over a box."""

from dataclasses import replace
import math
from types import SimpleNamespace

import numpy as np
import pytest

from sharpmod import box_analysis as ba
from sharpmod.box_sounding import BoxRegion, plan_box_samples
from sharpmod.tests._examples import examples_dir


HRRR_NPZ = examples_dir() / "hrrr_point_36.68N_95.66W_f018.npz"

pytestmark = pytest.mark.skipif(
    not HRRR_NPZ.is_file(), reason="no HRRR .npz example sounding")


def _region() -> BoxRegion:
    """A small box around the example sounding's location."""
    return BoxRegion.from_corners(36.0, -96.5, 37.4, -94.8)


def _plan(target=16):
    return plan_box_samples("hrrr", _region(), target_points=target)


def _write_variants(plan, directory):
    """Write one perturbed copy of the example sounding per in-domain node.

    Low-level moisture increases west to east and wind speed scales with the
    column index, so every field has a real gradient to measure rather than one
    value repeated across the lattice.
    """
    source = dict(np.load(HRRR_NPZ, allow_pickle=False))
    outputs = {}
    for node in plan.requestable_points:
        arrays = {
            key: (value.copy() if hasattr(value, "copy") else value)
            for key, value in source.items()
        }
        bump = 0.45 * node.col - 0.25 * node.row
        low = arrays["pres"] > 800.0
        arrays["dwpc"] = np.where(
            low & (arrays["dwpc"] > -9000.0),
            arrays["dwpc"] + bump,
            arrays["dwpc"],
        )
        arrays["wspd"] = np.where(
            arrays["wspd"] > -9000.0,
            arrays["wspd"] * (1.0 + 0.02 * node.col),
            arrays["wspd"],
        )
        path = directory / f"{node.request_id}.npz"
        np.savez(path, **arrays)
        outputs[node.request_id] = str(path)
    return outputs


@pytest.fixture
def analysis(tmp_path):
    plan = _plan()
    outputs = _write_variants(plan, tmp_path)
    return ba.analyze_box(
        plan, outputs, tiers=(ba.FAST_TIER,), fxx=18)


# -- registry -------------------------------------------------------------- #


def test_registry_keys_are_unique():
    keys = [item.key for item in ba.PARAMETERS]
    assert len(set(keys)) == len(keys)


def test_every_parameter_declares_a_known_tier_and_notable_end():
    for item in ba.PARAMETERS:
        assert item.tier in ba.TIERS
        assert item.notable in {"high", "low"}
        assert item.label


def test_parameter_lookup_and_unknown_key():
    assert ba.parameter("mucape").label == "MUCAPE"
    with pytest.raises(ba.BoxAnalysisError, match="unknown box parameter"):
        ba.parameter("not-a-field")


def test_tiers_partition_the_registry():
    fast = set(ba.parameters_for_tiers([ba.FAST_TIER]))
    composite = set(ba.parameters_for_tiers([ba.COMPOSITE_TIER]))
    assert fast and composite
    assert not (fast & composite)
    assert fast | composite == set(ba.PARAMETERS)


def test_parameters_for_unknown_tier_is_refused():
    with pytest.raises(ba.BoxAnalysisError, match="unknown analysis tier"):
        ba.parameters_for_tiers(["nope"])


def test_parameter_groups_can_be_restricted_to_available_keys():
    groups = dict(ba.parameter_groups(["mucape", "shear_6km"]))
    flattened = [item.key for items in groups.values() for item in items]
    assert sorted(flattened) == ["mucape", "shear_6km"]


def test_format_renders_absent_values_as_an_em_dash():
    assert ba.parameter("mucape").format(None) == "\u2014"
    assert ba.parameter("stp_cin").format(1.2345) == "1.23"


# -- missing-value coercion ------------------------------------------------ #


@pytest.mark.parametrize(
    "value",
    (None, np.ma.masked, -9999.0, -9999, float("nan"), float("inf"), "abc"),
)
def test_absent_values_coerce_to_none(value):
    assert ba._finite(value) is None


@pytest.mark.parametrize("value", (0.0, -47.5, 2989.4, np.float64(3.5)))
def test_present_values_coerce_to_float(value):
    assert ba._finite(value) == pytest.approx(float(value))


# -- fast tier ------------------------------------------------------------- #


def test_fast_tier_produces_the_expected_families():
    prof, _collection = ba.load_profile(HRRR_NPZ)
    values = ba.fast_values(prof)
    for key in (
        "sbcape", "mlcape", "mucape", "mlcin", "ml_lcl", "mu_el",
        "shear_1km", "shear_3km", "shear_6km", "srh_1km", "srh_3km",
        "dcape",
    ):
        assert key in values, key
    # Every fast-tier key must be registered, or a field map cannot label it.
    assert set(values) <= {
        item.key for item in ba.parameters_for_tiers([ba.FAST_TIER])
    }


def test_fast_tier_values_are_physically_ordered():
    prof, _collection = ba.load_profile(HRRR_NPZ)
    values = ba.fast_values(prof)
    # MUCAPE is by definition the largest of the three parcel CAPEs.
    assert values["mucape"] >= values["sbcape"] - 1e-6
    assert values["mucape"] >= values["mlcape"] - 1e-6
    # CIN is an inhibition, so it is not positive.
    assert values["mlcin"] <= 0.0
    # Deeper shear layers cannot be shorter vectors than the 0-1 km layer here.
    assert values["shear_6km"] >= values["shear_1km"]
    # Heights are above ground and ordered.
    assert 0.0 < values["ml_lcl"] < values["mu_el"]


def test_analyze_profile_rejects_an_unknown_tier():
    prof, _collection = ba.load_profile(HRRR_NPZ)
    with pytest.raises(ba.BoxAnalysisError, match="unknown analysis tier"):
        ba.analyze_profile(prof, tiers=("nope",))


def test_load_profile_rejects_a_missing_file(tmp_path):
    with pytest.raises(Exception):
        ba.load_profile(tmp_path / "absent.npz")


# -- analyze_box ----------------------------------------------------------- #


def test_analysis_covers_the_whole_lattice(analysis):
    plan = analysis.plan
    assert len(analysis.points) == plan.rows * plan.cols
    assert analysis.rows == plan.rows
    assert analysis.cols == plan.cols
    assert len(analysis.analyzed) == len(plan.requestable_points)
    assert analysis.failures == ()


def test_analysis_nodes_keep_their_lattice_identity(analysis):
    for row in range(analysis.rows):
        for col in range(analysis.cols):
            point = analysis.point_at(row, col)
            assert point.row == row
            assert point.col == col
            assert point.request_id == f"r{row:03d}c{col:03d}"
    assert analysis.point_at(-1, 0) is None
    assert analysis.point_at(0, analysis.cols) is None


def test_unextracted_nodes_are_carried_as_empty_results(tmp_path):
    plan = _plan()
    outputs = _write_variants(plan, tmp_path)
    partial = dict(list(outputs.items())[:3])
    result = ba.analyze_box(plan, partial, tiers=(ba.FAST_TIER,))
    # The lattice stays rectangular even though most nodes have no data.
    assert len(result.points) == plan.rows * plan.cols
    assert len(result.analyzed) == 3
    assert any(not point.values for point in result.points)


def test_a_corrupt_node_is_recorded_without_ending_the_run(tmp_path):
    plan = _plan()
    outputs = _write_variants(plan, tmp_path)
    victim = sorted(outputs)[0]
    (tmp_path / f"{victim}.npz").write_bytes(b"not an npz archive")
    result = ba.analyze_box(plan, outputs, tiers=(ba.FAST_TIER,))
    failures = {point.request_id for point in result.failures}
    assert victim in failures
    # The remaining nodes still analyzed.
    assert len(result.analyzed) == len(outputs) - 1


def test_progress_is_reported_once_per_extracted_node(tmp_path):
    plan = _plan()
    outputs = _write_variants(plan, tmp_path)
    seen = []
    ba.analyze_box(
        plan, outputs, tiers=(ba.FAST_TIER,),
        progress=lambda done, total, point: seen.append((done, total)),
    )
    assert len(seen) == len(outputs)
    assert seen[-1] == (len(outputs), len(outputs))
    assert [done for done, _total in seen] == list(
        range(1, len(outputs) + 1))


def test_cancellation_stops_analyzing_further_nodes(tmp_path):
    plan = _plan()
    outputs = _write_variants(plan, tmp_path)
    result = ba.analyze_box(
        plan, outputs, tiers=(ba.FAST_TIER,), cancelled=lambda: True)
    assert result.analyzed == ()
    assert any(point.error == "cancelled" for point in result.points)


def test_analyze_box_requires_a_plan():
    with pytest.raises(ba.BoxAnalysisError, match="BoxSamplePlan"):
        ba.analyze_box(object(), {})


def test_out_of_domain_nodes_explain_themselves():
    region = BoxRegion.from_corners(18.0, -105.0, 24.0, -99.0)
    plan = plan_box_samples("hrrr", region)
    result = ba.analyze_box(plan, {}, tiers=(ba.FAST_TIER,))
    reasons = {point.error for point in result.points if point.error}
    assert "outside model domain" in reasons


# -- fields ---------------------------------------------------------------- #


def test_field_is_a_row_major_grid_with_north_first(analysis):
    grid = analysis.field("mucape")
    assert len(grid) == analysis.rows
    assert all(len(row) == analysis.cols for row in grid)
    north = analysis.point_at(0, 0)
    south = analysis.point_at(analysis.rows - 1, 0)
    assert north.lat > south.lat
    assert grid[0][0] == north.value("mucape")


def test_field_gradient_follows_the_applied_perturbation(analysis):
    grid = analysis.field("mucape")
    # Moisture was increased eastward, so CAPE must increase along each row.
    for row in grid:
        present = [value for value in row if value is not None]
        assert present == sorted(present)
    # And decreased southward, so the north edge is the moistest.
    assert grid[0][0] > grid[-1][0]


def test_field_of_an_unknown_key_is_refused(analysis):
    with pytest.raises(ba.BoxAnalysisError):
        analysis.field("not-a-field")


def test_available_parameters_excludes_untouched_tiers(analysis):
    available = {item.key for item in analysis.available_parameters()}
    composite = {
        item.key for item in ba.parameters_for_tiers([ba.COMPOSITE_TIER])
    }
    assert available
    assert not (available & composite)


def test_values_of_returns_only_present_values(analysis):
    values = analysis.values_of("mucape")
    assert len(values) == len(analysis.analyzed)
    assert all(isinstance(value, float) for value in values)


# -- statistics ------------------------------------------------------------ #


def test_statistics_describe_the_area(analysis):
    stats = analysis.statistics("mucape")
    values = analysis.values_of("mucape")
    assert stats.count == len(values)
    assert stats.minimum == pytest.approx(min(values))
    assert stats.maximum == pytest.approx(max(values))
    assert stats.mean == pytest.approx(sum(values) / len(values))
    assert stats.spread == pytest.approx(stats.maximum - stats.minimum)
    assert stats.minimum <= stats.p10 <= stats.median <= stats.p90
    assert stats.p90 <= stats.maximum


def test_extreme_of_a_high_notable_field_is_the_maximum(analysis):
    stats = analysis.statistics("mucape")
    assert stats.extreme.value("mucape") == pytest.approx(stats.maximum)
    assert analysis.extreme("mucape") is stats.extreme


def test_extreme_of_a_low_notable_field_is_the_minimum(analysis):
    # A lower LCL is the significant end, so the extreme must be the minimum.
    assert ba.parameter("ml_lcl").notable == "low"
    stats = analysis.statistics("ml_lcl")
    assert stats.extreme.value("ml_lcl") == pytest.approx(stats.minimum)


def test_cin_extreme_is_the_most_negative_value(analysis):
    assert ba.parameter("mlcin").notable == "low"
    stats = analysis.statistics("mlcin")
    assert stats.extreme.value("mlcin") == pytest.approx(stats.minimum)


def test_statistics_of_an_empty_field_is_none(analysis):
    assert analysis.statistics("stp_cin") is None


# -- ranking --------------------------------------------------------------- #


def test_ranking_puts_the_notable_end_first(analysis):
    ranked = analysis.ranked("mucape")
    values = [point.value("mucape") for point in ranked]
    assert values == sorted(values, reverse=True)
    ascending = [point.value("ml_lcl") for point in analysis.ranked("ml_lcl")]
    assert ascending == sorted(ascending)


def test_ranking_honours_its_limit(analysis):
    assert len(analysis.ranked("mucape", limit=3)) == 3
    assert analysis.ranked("mucape", limit=0) == ()


def test_ranking_of_an_empty_field_is_empty(analysis):
    assert analysis.ranked("ship") == ()


# -- transects ------------------------------------------------------------- #


def test_field_transect_runs_the_width_of_the_box(analysis):
    distances, values, nodes = analysis.field_transect(
        "mucape", orientation="ew")
    assert len(distances) == analysis.cols == len(values) == len(nodes)
    assert distances[0] == 0.0
    assert distances[-1] == pytest.approx(analysis.plan.region.width_km)
    assert all(a < b for a, b in zip(distances, distances[1:]))


def test_field_transect_runs_the_height_of_the_box(analysis):
    distances, values, _nodes = analysis.field_transect(
        "mucape", orientation="ns")
    assert len(distances) == analysis.rows == len(values)
    assert distances[-1] == pytest.approx(analysis.plan.region.height_km)


def test_field_transect_defaults_to_the_middle_slice(analysis):
    _d, _v, nodes = analysis.field_transect("mucape", orientation="ew")
    assert {node.row for node in nodes} == {analysis.rows // 2}


@pytest.mark.parametrize(
    "kwargs",
    ({"orientation": "up"}, {"index": 999}, {"index": -1}),
)
def test_field_transect_rejects_bad_slices(analysis, kwargs):
    with pytest.raises(ba.BoxAnalysisError):
        analysis.field_transect("mucape", **kwargs)


def test_vertical_transect_is_physically_sensible(analysis):
    distances, levels, grid = analysis.vertical_transect(field="tmpc")
    assert len(levels) == len(grid)
    assert all(len(row) == analysis.cols for row in grid)
    assert len(distances) == analysis.cols
    # Levels descend in pressure, so temperature must fall with height through
    # the troposphere.
    by_level = dict(zip(levels, grid))
    warm = by_level[850.0][0]
    cold = by_level[300.0][0]
    assert warm is not None and cold is not None
    assert warm > cold
    assert -90.0 < cold < 0.0
    assert 0.0 < warm < 50.0


def test_vertical_transect_leaves_below_ground_levels_blank(analysis):
    _distances, levels, grid = analysis.vertical_transect(field="tmpc")
    by_level = dict(zip(levels, grid))
    # The example sounding's surface is near 986 hPa, so 1000 hPa is below
    # ground and must not be extrapolated into existence.
    assert all(value is None for value in by_level[1000.0])


def test_vertical_transect_reflects_the_wind_perturbation(analysis):
    _distances, levels, grid = analysis.vertical_transect(field="wspd")
    by_level = dict(zip(levels, grid))
    row = [value for value in by_level[500.0] if value is not None]
    assert len(row) == analysis.cols
    assert row == sorted(row)


def test_wind_direction_column_interpolates_across_north():
    midpoint = math.sqrt(1000.0 * 900.0)
    prof = SimpleNamespace(
        pres=np.asarray([1000.0, 900.0]),
        wdir=np.asarray([350.0, 10.0]),
        wspd=np.asarray([20.0, 20.0]),
    )

    value = ba._column_from_profile(
        prof, "wdir", np.log10(np.asarray([midpoint]))
    )[0]

    assert value is not None
    assert min(abs(value), abs(value - 360.0)) < 1.0


def test_vertical_transect_handles_nodes_without_data(tmp_path):
    plan = _plan()
    outputs = _write_variants(plan, tmp_path)
    result = ba.analyze_box(
        plan, dict(list(outputs.items())[:2]), tiers=(ba.FAST_TIER,))
    _distances, _levels, grid = result.vertical_transect(field="tmpc")
    # Missing nodes become blank columns rather than raising.
    assert any(value is None for row in grid for value in row)


@pytest.mark.parametrize(
    "kwargs",
    (
        {"field": "nope"},
        {"orientation": "up"},
        {"levels": ()},
        {"levels": (0.0, 500.0)},
    ),
)
def test_vertical_transect_rejects_bad_arguments(analysis, kwargs):
    with pytest.raises(ba.BoxAnalysisError):
        analysis.vertical_transect(**kwargs)


# -- summary --------------------------------------------------------------- #


def test_summary_lists_available_fields_with_their_extremes(analysis):
    text = analysis.summary()
    assert "MUCAPE" in text
    assert "Field" in text
    # One header line plus one line per available field.
    assert len(text.splitlines()) == len(
        analysis.available_parameters()) + 1


def test_summary_can_be_restricted_to_chosen_fields(analysis):
    text = analysis.summary(keys=("mucape", "shear_6km"))
    assert len(text.splitlines()) == 3
    assert "MUCAPE" in text and "0-6 km shear" in text


# -- composite tier -------------------------------------------------------- #


def test_composite_tier_adds_spc_composites(tmp_path):
    # Deliberately tiny: the composite tier costs roughly 0.4 s per node.
    plan = plan_box_samples("hrrr", _region(), target_points=4)
    outputs = _write_variants(plan, tmp_path)
    result = ba.analyze_box(
        plan, outputs, tiers=(ba.FAST_TIER, ba.COMPOSITE_TIER))
    available = {item.key for item in result.available_parameters()}
    for key in ("stp_cin", "scp", "ship", "pwat", "lapse_700_500"):
        assert key in available, key
    # The fast tier is still present alongside it.
    assert "mucape" in available
    assert result.tiers == (ba.FAST_TIER, ba.COMPOSITE_TIER)


def test_composite_values_agree_with_the_shared_oracle():
    from sharpmod.sharptab import derived

    prof, _collection = ba.load_profile(HRRR_NPZ)
    values = ba.composite_values(prof)
    oracle = derived._convective_oracle_profile(prof)
    # The box must read the same cached oracle the Skew-T does, not a second
    # parcel path that could disagree.
    assert values["stp_cin"] == pytest.approx(float(oracle.stp_cin))
    assert values["scp"] == pytest.approx(float(oracle.scp))
    assert values["pwat"] == pytest.approx(float(oracle.pwat))


# -- ingredient criteria --------------------------------------------------- #


def test_criterion_returns_three_answers_not_two():
    item = ba.Criterion("mucape", minimum=500.0)
    assert item.matches({"mucape": 900.0}) is True
    assert item.matches({"mucape": 100.0}) is False
    # Absent is neither pass nor fail; treating it as a failure would shade an
    # unextracted point as "unfavourable".
    assert item.matches({}) is None


def test_criterion_supports_a_maximum_and_a_band():
    assert ba.Criterion("ml_lcl", maximum=1200.0).matches(
        {"ml_lcl": 900.0}) is True
    assert ba.Criterion("ml_lcl", maximum=1200.0).matches(
        {"ml_lcl": 1500.0}) is False
    band = ba.Criterion("shear_6km", minimum=30.0, maximum=60.0)
    assert band.matches({"shear_6km": 45.0}) is True
    assert band.matches({"shear_6km": 70.0}) is False
    assert band.matches({"shear_6km": 20.0}) is False


@pytest.mark.parametrize(
    "kwargs",
    (
        {"parameter": "not-a-field", "minimum": 1.0},
        {"parameter": "mucape"},
        {"parameter": "mucape", "minimum": 100.0, "maximum": 10.0},
    ),
)
def test_malformed_criteria_are_refused_at_definition(kwargs):
    with pytest.raises(ba.BoxAnalysisError):
        ba.Criterion(**kwargs)


def test_criterion_describes_itself_with_units():
    assert ba.Criterion("mucape", minimum=500.0).describe() == \
        "MUCAPE \u2265 500 J/kg"
    assert "\u2264" in ba.Criterion("ml_lcl", maximum=1200.0).describe()
    assert "-" in ba.Criterion(
        "shear_6km", minimum=30.0, maximum=60.0).describe()


def test_every_named_screen_is_well_formed():
    assert ba.screen_names()
    for name in ba.screen_names():
        criteria = ba.screen(name)
        assert criteria
        for item in criteria:
            assert isinstance(item, ba.Criterion)
            # Screens use fast-tier fields only, so they resolve as soon as a
            # box is extracted.
            assert ba.parameter(item.parameter).tier == ba.FAST_TIER


def test_unknown_screen_lists_the_available_ones():
    with pytest.raises(ba.BoxAnalysisError, match="unknown ingredient screen"):
        ba.screen("nope")


def test_describe_criteria_joins_with_and():
    text = ba.describe_criteria(ba.screen("supercell"))
    assert text.count(" and ") == 2
    assert "MUCAPE" in text and "SRH" in text


# -- masks and coverage ---------------------------------------------------- #


def test_evaluate_combines_with_and_but_propagates_unknown():
    criteria = (
        ba.Criterion("mucape", minimum=500.0),
        ba.Criterion("shear_6km", minimum=30.0),
    )
    assert ba._evaluate(criteria, {"mucape": 900.0, "shear_6km": 40.0}) is True
    assert ba._evaluate(criteria, {"mucape": 900.0, "shear_6km": 10.0}) is False
    # A definite failure wins over a missing field: the node is out regardless.
    assert ba._evaluate(criteria, {"shear_6km": 10.0}) is False
    # Nothing failed, but something is unreadable.
    assert ba._evaluate(criteria, {"mucape": 900.0}) is None


def test_mask_is_a_rectangular_tri_state_grid(analysis):
    grid = analysis.mask("organized convection")
    assert len(grid) == analysis.rows
    assert all(len(row) == analysis.cols for row in grid)
    assert all(value in (True, False, None) for row in grid for value in row)


def test_mask_accepts_a_name_a_single_criterion_and_an_iterable(analysis):
    one = analysis.mask(ba.Criterion("mucape", minimum=1.0))
    many = analysis.mask([ba.Criterion("mucape", minimum=1.0)])
    named = analysis.mask("organized convection")
    assert one == many
    assert len(named) == len(one)


def test_mask_rejects_empty_or_malformed_criteria(analysis):
    with pytest.raises(ba.BoxAnalysisError, match="at least one criterion"):
        analysis.mask([])
    with pytest.raises(ba.BoxAnalysisError, match="Criterion values"):
        analysis.mask(["mucape"])


def test_coverage_counts_only_judgeable_nodes(analysis):
    # Every node in this box was extracted, so everything is judgeable.
    result = analysis.coverage(ba.Criterion("mucape", minimum=1.0))
    assert result.total == len(analysis.points)
    assert result.evaluated == len(analysis.analyzed)
    assert result.unknown == result.total - result.evaluated
    assert result.count == result.evaluated
    assert result.fraction == pytest.approx(1.0)
    assert result.conclusive is (result.unknown == 0)


def test_coverage_of_an_impossible_threshold_is_empty_but_conclusive(analysis):
    result = analysis.coverage(ba.Criterion("mucape", minimum=1.0e9))
    assert result.count == 0
    assert result.fraction == 0.0
    assert result.evaluated > 0
    assert result.matched == ()


def test_coverage_reports_unknown_rather_than_failing_absent_fields(analysis):
    # The composite tier was never computed for this box.
    result = analysis.coverage(ba.Criterion("stp_cin", minimum=1.0))
    assert result.evaluated == 0
    assert result.unknown == result.total
    assert result.count == 0
    assert result.conclusive is False
    assert "no point could be judged" in result.describe()


def test_coverage_area_uses_the_sample_cell_size(analysis):
    result = analysis.coverage(ba.Criterion("mucape", minimum=1.0))
    assert analysis.cell_area_km2 > 0.0
    assert result.cell_area_km2 == pytest.approx(analysis.cell_area_km2)
    assert result.area_km2 == pytest.approx(
        result.count * analysis.cell_area_km2)
    # The matched area cannot exceed the box it came from, allowing for the
    # half-cell overhang of an edge-inclusive lattice.
    assert result.area_km2 <= analysis.plan.region.area_km2 * 2.0


def test_cell_area_scales_with_the_box(analysis):
    from sharpmod.box_sounding import BoxRegion, plan_box_samples

    big = plan_box_samples(
        "hrrr", BoxRegion.from_corners(34.0, -99.0, 38.0, -94.0),
        target_points=16)
    bigger = ba.analyze_box(big, {}, tiers=(ba.FAST_TIER,))
    assert bigger.cell_area_km2 > analysis.cell_area_km2


def test_coverage_describe_is_readable(analysis):
    text = analysis.coverage(ba.Criterion("mucape", minimum=1.0)).describe()
    assert "points" in text
    assert "km" in text
    assert "%" in text


def test_a_tighter_screen_never_matches_more_than_a_looser_one(analysis):
    loose = analysis.coverage(ba.Criterion("mucape", minimum=1000.0))
    tight = analysis.coverage((
        ba.Criterion("mucape", minimum=1000.0),
        ba.Criterion("shear_6km", minimum=1000.0),
    ))
    assert tight.count <= loose.count


def test_screens_offers_only_evaluable_sets(analysis):
    offered = analysis.screens()
    assert offered
    for name in offered:
        for item in ba.screen(name):
            # Every field the screen needs is present somewhere in the box.
            assert any(
                item.parameter in point.values for point in analysis.points)


def test_screens_is_empty_when_nothing_was_analyzed():
    from sharpmod.box_sounding import BoxRegion, plan_box_samples

    plan = plan_box_samples(
        "hrrr", BoxRegion.from_corners(36.0, -96.5, 37.4, -94.8),
        target_points=16)
    empty = ba.analyze_box(plan, {}, tiers=(ba.FAST_TIER,))
    assert empty.screens() == ()


def test_matched_nodes_actually_satisfy_the_criteria(analysis):
    criteria = (
        ba.Criterion("mucape", minimum=2500.0),
        ba.Criterion("shear_6km", minimum=1.0),
    )
    result = analysis.coverage(criteria)
    assert result.count > 0
    for point in result.matched:
        assert point.value("mucape") >= 2500.0
        assert point.value("shear_6km") >= 1.0
    # And the mask agrees with the point list.
    grid = analysis.mask(criteria)
    assert sum(
        1 for row in grid for value in row if value is True) == result.count


# -- cross-box envelope ---------------------------------------------------- #


@pytest.fixture
def fanned(tmp_path):
    """A box whose low levels fan out west-to-east, so spread is non-zero."""
    plan = _plan()
    source = dict(np.load(HRRR_NPZ, allow_pickle=False))
    outputs = {}
    for node in plan.requestable_points:
        arrays = {
            key: (value.copy() if hasattr(value, "copy") else value)
            for key, value in source.items()
        }
        # Only below 850 hPa, so the upper levels stay identical and the test
        # can prove the spread is where it was put.
        low = arrays["pres"] > 850.0
        arrays["tmpc"] = np.where(
            low & (arrays["tmpc"] > -9000.0),
            arrays["tmpc"] + 0.4 * node.col, arrays["tmpc"])
        arrays["dwpc"] = np.where(
            low & (arrays["dwpc"] > -9000.0),
            arrays["dwpc"] - 6.0 + 0.9 * node.col, arrays["dwpc"])
        path = tmp_path / f"{node.request_id}.npz"
        np.savez(path, **arrays)
        outputs[node.request_id] = str(path)
    return ba.analyze_box(plan, outputs, tiers=(ba.FAST_TIER,))


def test_envelope_summarizes_every_extracted_sounding(fanned):
    envelope = fanned.envelope(field="tmpc")
    assert envelope.field == "tmpc"
    assert envelope.soundings == len(fanned.analyzed)
    assert len(envelope.levels) == len(ba.DEFAULT_TRANSECT_LEVELS)
    for series in (envelope.minimum, envelope.low, envelope.median,
                   envelope.high, envelope.maximum, envelope.counts):
        assert len(series) == len(envelope.levels)


def test_envelope_percentiles_are_ordered_at_every_level(fanned):
    envelope = fanned.envelope(field="tmpc")
    for index in range(len(envelope.levels)):
        if envelope.minimum[index] is None:
            continue
        assert (
            envelope.minimum[index] <= envelope.low[index]
            <= envelope.median[index] <= envelope.high[index]
            <= envelope.maximum[index]
        )


def test_envelope_spread_appears_only_where_it_was_introduced(fanned):
    envelope = fanned.envelope(field="tmpc")
    # The perturbation was applied below 850 hPa only.
    assert envelope.spread_at(900.0) > 0.5
    assert envelope.spread_at(500.0) == pytest.approx(0.0, abs=1e-6)
    assert envelope.widest_level >= 850.0


def test_envelope_counts_thin_out_below_ground(fanned):
    envelope = fanned.envelope(field="tmpc")
    by_level = dict(zip(envelope.levels, envelope.counts))
    # The example sounding's surface is near 986 hPa, so nothing reaches 1000.
    assert by_level[1000.0] == 0
    assert by_level[950.0] == len(fanned.analyzed)
    # And a level with no contributors carries no statistics.
    index = envelope.levels.index(1000.0)
    assert envelope.median[index] is None
    assert envelope.minimum[index] is None


def test_envelope_columns_are_available_for_spaghetti(fanned):
    envelope = fanned.envelope(field="tmpc")
    assert len(envelope.columns) == envelope.soundings
    assert all(len(column) == len(envelope.levels)
               for column in envelope.columns)
    # Every column contributed something, or it would not be retained.
    assert all(
        any(value is not None for value in column)
        for column in envelope.columns
    )


def test_envelope_median_lies_between_the_extreme_columns(fanned):
    envelope = fanned.envelope(field="tmpc")
    index = envelope.levels.index(900.0)
    present = [
        column[index] for column in envelope.columns
        if column[index] is not None
    ]
    assert min(present) == pytest.approx(envelope.minimum[index])
    assert max(present) == pytest.approx(envelope.maximum[index])


def test_envelope_percentile_band_can_be_widened(fanned):
    narrow = fanned.envelope(field="tmpc", percentiles=(40.0, 60.0))
    wide = fanned.envelope(field="tmpc", percentiles=(0.0, 100.0))
    index = narrow.levels.index(900.0)
    assert wide.high[index] - wide.low[index] >= \
        narrow.high[index] - narrow.low[index]
    # The widest band is the full range.
    assert wide.low[index] == pytest.approx(wide.minimum[index])
    assert wide.high[index] == pytest.approx(wide.maximum[index])


def test_envelope_of_an_unextracted_box_is_empty():
    from sharpmod.box_sounding import BoxRegion, plan_box_samples

    plan = plan_box_samples(
        "hrrr", BoxRegion.from_corners(36.0, -96.5, 37.4, -94.8),
        target_points=16)
    empty = ba.analyze_box(plan, {}, tiers=(ba.FAST_TIER,))
    envelope = empty.envelope(field="tmpc")
    assert envelope.soundings == 0
    assert all(count == 0 for count in envelope.counts)
    assert envelope.widest_level is None
    assert envelope.spread_at(500.0) is None


def test_envelope_supports_every_transect_field(fanned):
    for field in ba.TRANSECT_FIELDS:
        envelope = fanned.envelope(field=field)
        assert envelope.field == field


def test_wind_direction_envelope_stays_narrow_across_north(analysis):
    points = []
    for index, point in enumerate(analysis.points):
        columns = dict(point.columns)
        columns["wdir"] = tuple(
            350.0 if index % 2 else 10.0
            for _level in ba.DEFAULT_TRANSECT_LEVELS
        )
        points.append(replace(point, columns=columns))
    wrapped = replace(analysis, points=tuple(points))

    envelope = wrapped.envelope(field="wdir")
    level = envelope.levels.index(500.0)

    assert envelope.maximum[level] - envelope.minimum[level] == \
        pytest.approx(20.0)
    assert envelope.median[level] == pytest.approx(360.0)


@pytest.mark.parametrize(
    "kwargs",
    (
        {"field": "not-a-column"},
        {"levels": ()},
        {"levels": (0.0, 500.0)},
        {"percentiles": (90.0, 10.0)},
        {"percentiles": (-5.0, 90.0)},
        {"percentiles": (10.0, 120.0)},
        {"percentiles": "nope"},
    ),
)
def test_envelope_rejects_bad_arguments(fanned, kwargs):
    with pytest.raises(ba.BoxAnalysisError):
        fanned.envelope(**kwargs)


def test_envelope_and_vertical_transect_agree_at_a_shared_node(fanned):
    # Both go through the same interpolation helper, so a node's value must be
    # inside the envelope that summarizes it.
    envelope = fanned.envelope(field="tmpc")
    _distances, levels, grid = fanned.vertical_transect(field="tmpc")
    index = levels.index(900.0)
    envelope_index = envelope.levels.index(900.0)
    for value in grid[index]:
        if value is None:
            continue
        assert envelope.minimum[envelope_index] - 1e-9 <= value
        assert value <= envelope.maximum[envelope_index] + 1e-9


# -- forecast-hour sequences ----------------------------------------------- #


@pytest.fixture
def sequence(tmp_path):
    """A three-hour sequence whose MUCAPE peaks at the middle hour."""
    plan = _plan()
    source = dict(np.load(HRRR_NPZ, allow_pickle=False))
    outputs_by_hour = {}
    # Moisture builds then decays, so the peak is provably F06 rather than an
    # end of the range.
    for hour, bump in ((0, 0.0), (6, 2.5), (12, 1.0)):
        hour_outputs = {}
        for node in plan.requestable_points:
            arrays = {
                key: (value.copy() if hasattr(value, "copy") else value)
                for key, value in source.items()
            }
            low = arrays["pres"] > 800.0
            arrays["dwpc"] = np.where(
                low & (arrays["dwpc"] > -9000.0),
                arrays["dwpc"] + bump + 0.2 * node.col, arrays["dwpc"])
            directory = tmp_path / f"f{hour:03d}"
            directory.mkdir(exist_ok=True)
            path = directory / f"{node.request_id}.npz"
            np.savez(path, **arrays)
            hour_outputs[node.request_id] = str(path)
        outputs_by_hour[hour] = hour_outputs
    return ba.analyze_box_sequence(
        plan, outputs_by_hour, tiers=(ba.FAST_TIER,))


def test_sequence_holds_one_analysis_per_hour(sequence):
    assert sequence.hours == (0, 6, 12)
    for hour in sequence.hours:
        analysis = sequence.at(hour)
        assert analysis is not None
        assert analysis.fxx == hour
        assert len(analysis.analyzed) > 0
    assert sequence.at(99) is None
    assert sequence.at("nope") is None


def test_sequence_shares_one_plan(sequence):
    assert sequence.plan is not None
    for hour in sequence.hours:
        assert sequence.at(hour).plan is sequence.plan


def test_sequence_fields_are_the_intersection_of_its_hours(sequence):
    shared = {item.key for item in sequence.available_parameters()}
    assert "mucape" in shared
    for hour in sequence.hours:
        hour_keys = {
            item.key for item in sequence.at(hour).available_parameters()
        }
        # Intersection: never a key some hour cannot show.
        assert shared <= hour_keys


def test_sequence_statistics_series_covers_every_hour(sequence):
    series = sequence.statistics_series("mucape")
    assert [hour for hour, _stats in series] == list(sequence.hours)
    assert all(stats is not None for _hour, stats in series)


def test_sequence_peak_hour_finds_the_middle_maximum(sequence):
    # Moisture was made to build then decay, so the peak is an interior hour.
    assert sequence.peak_hour("mucape") == 6


def test_sequence_peak_hour_respects_a_low_notable_field(sequence):
    # For MLCIN the significant extreme is the most negative value.
    assert ba.parameter("mlcin").notable == "low"
    peak = sequence.peak_hour("mlcin")
    values = [
        sequence.at(hour).statistics("mlcin").minimum
        for hour in sequence.hours
    ]
    assert sequence.at(peak).statistics("mlcin").minimum == min(values)


def test_sequence_peak_hour_is_none_for_an_absent_field(sequence):
    assert sequence.peak_hour("stp_cin") is None


def test_sequence_coverage_series_tracks_qualifying_area(sequence):
    series = sequence.coverage_series(ba.Criterion("mucape", minimum=3000.0))
    assert [hour for hour, _c in series] == list(sequence.hours)
    counts = {hour: coverage.count for hour, coverage in series}
    # The moistest hour must qualify at least as much as the driest.
    assert counts[6] >= counts[0]
    assert sequence.peak_coverage_hour(
        ba.Criterion("mucape", minimum=3000.0)) == 6


def test_sequence_coverage_series_tolerates_a_bad_screen(sequence):
    series = sequence.coverage_series("not-a-screen")
    assert all(coverage is None for _hour, coverage in series)


def test_sequence_summary_marks_the_peak_hour(sequence):
    text = sequence.summary("mucape")
    assert "MUCAPE" in text
    assert "F006" in text
    assert "<- peak" in text
    # Header lines plus one row per hour.
    assert len(text.splitlines()) == len(sequence.hours) + 2


def test_sequence_progress_reports_the_hour(tmp_path):
    plan = _plan()
    outputs = _write_variants(plan, tmp_path)
    seen = []
    ba.analyze_box_sequence(
        plan, {0: outputs, 6: outputs}, tiers=(ba.FAST_TIER,),
        progress=lambda hour, done, total: seen.append((hour, done, total)),
    )
    assert {hour for hour, _d, _t in seen} == {0, 6}
    assert seen[-1][1] == len(outputs)


def test_sequence_cancellation_stops_between_hours(tmp_path):
    plan = _plan()
    outputs = _write_variants(plan, tmp_path)
    result = ba.analyze_box_sequence(
        plan, {0: outputs, 6: outputs}, tiers=(ba.FAST_TIER,),
        cancelled=lambda: True)
    assert result.hours == ()
    assert result.analyses == {}


def test_sequence_requires_a_plan_and_at_least_one_hour(tmp_path):
    plan = _plan()
    with pytest.raises(ba.BoxAnalysisError, match="BoxSamplePlan"):
        ba.analyze_box_sequence(object(), {0: {}})
    with pytest.raises(ba.BoxAnalysisError, match="at least one forecast hour"):
        ba.analyze_box_sequence(plan, {})
    with pytest.raises(ba.BoxAnalysisError, match="must be integers"):
        ba.analyze_box_sequence(plan, {"noon": {}})


def test_a_single_hour_sequence_still_works(tmp_path):
    plan = _plan()
    outputs = _write_variants(plan, tmp_path)
    one = ba.analyze_box_sequence(plan, {3: outputs}, tiers=(ba.FAST_TIER,))
    assert one.hours == (3,)
    assert one.peak_hour("mucape") == 3
    assert one.at(3).fxx == 3

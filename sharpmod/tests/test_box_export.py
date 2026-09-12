"""CSV and GeoJSON export of box soundings."""

import json

import numpy as np
import pytest

from sharpmod import box_analysis as ba
from sharpmod import box_export as be
from sharpmod.box_sounding import BoxRegion, plan_box_samples
from sharpmod.tests._examples import examples_dir
from sharpmod.tests.test_box_analysis import _write_variants


HRRR_NPZ = examples_dir() / "hrrr_point_36.68N_95.66W_f018.npz"

pytestmark = pytest.mark.skipif(
    not HRRR_NPZ.is_file(), reason="no HRRR .npz example sounding")


def _plan(target=9):
    return plan_box_samples(
        "hrrr", BoxRegion.from_corners(36.0, -96.5, 37.4, -94.8),
        target_points=target)


@pytest.fixture(scope="module")
def analysis(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("box-export")
    plan = _plan()
    outputs = _write_variants(plan, tmp_path)
    return ba.analyze_box(plan, outputs, tiers=(ba.FAST_TIER,), fxx=18)


@pytest.fixture(scope="module")
def sequence(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("box-export-sequence")
    plan = _plan()
    source = dict(np.load(HRRR_NPZ, allow_pickle=False))
    outputs_by_hour = {}
    for hour in (0, 6):
        hour_outputs = {}
        directory = tmp_path / f"f{hour:03d}"
        directory.mkdir(exist_ok=True)
        for node in plan.requestable_points:
            arrays = {
                key: (value.copy() if hasattr(value, "copy") else value)
                for key, value in source.items()
            }
            low = arrays["pres"] > 800.0
            arrays["dwpc"] = np.where(
                low & (arrays["dwpc"] > -9000.0),
                arrays["dwpc"] + 0.1 * hour + 0.2 * node.col, arrays["dwpc"])
            path = directory / f"{node.request_id}.npz"
            np.savez(path, **arrays)
            hour_outputs[node.request_id] = str(path)
        outputs_by_hour[hour] = hour_outputs
    return ba.analyze_box_sequence(
        plan, outputs_by_hour, tiers=(ba.FAST_TIER,))


# -- CSV ------------------------------------------------------------------- #


def test_csv_header_leads_with_identity_then_fields(analysis):
    rows = list(be.box_csv_rows(analysis))
    header = rows[0]
    assert header[:6] == [
        "request_id", "row", "col", "lat", "lon", "status"]
    assert "mucape" in header
    # Fields follow registry order, so two exports are comparable.
    keys = [item.key for item in analysis.available_parameters()]
    assert header[6:] == keys


def test_csv_has_one_row_per_lattice_node(analysis):
    rows = list(be.box_csv_rows(analysis))
    assert len(rows) - 1 == len(analysis.points)


def test_csv_writes_absent_values_as_empty_not_zero(analysis, tmp_path):
    # Ask for a field the composite tier never computed.
    rows = list(be.box_csv_rows(analysis, keys=("mucape", "stp_cin")))
    header, first = rows[0], rows[1]
    assert header[-2:] == ["mucape", "stp_cin"]
    assert first[-1] == ""
    assert first[-2] != ""


def test_csv_round_trips_through_a_file(analysis, tmp_path):
    destination = tmp_path / "out" / "box.csv"
    written = be.write_box_csv(analysis, destination)
    assert written == len(analysis.points)
    lines = destination.read_text(encoding="utf-8").splitlines()
    assert len(lines) - 1 == written
    assert lines[0].startswith("request_id,row,col,lat,lon,status")


def test_csv_can_be_restricted_to_chosen_fields(analysis):
    rows = list(be.box_csv_rows(analysis, keys=("mucape", "shear_6km")))
    assert rows[0][6:] == ["mucape", "shear_6km"]


def test_csv_rejects_an_unknown_field(analysis):
    with pytest.raises(ba.BoxAnalysisError):
        list(be.box_csv_rows(analysis, keys=("not-a-field",)))


def test_csv_records_a_screen_verdict_per_row(analysis):
    rows = list(be.box_csv_rows(
        analysis, keys=("mucape",), criteria="organized convection"))
    assert rows[0][-1] == "matches"
    verdicts = {row[-1] for row in rows[1:]}
    assert verdicts <= {"yes", "no", ""}
    assert "yes" in verdicts


def test_csv_status_column_explains_empty_nodes(analysis):
    rows = list(be.box_csv_rows(analysis))
    statuses = {row[5] for row in rows[1:]}
    assert "ok" in statuses


def test_csv_longitudes_are_wrapped_into_range():
    region = BoxRegion.from_corners(50.0, 170.0, 56.0, 190.0)
    plan = plan_box_samples("gfs", region, target_points=9)
    empty = ba.analyze_box(plan, {}, tiers=(ba.FAST_TIER,))
    rows = list(be.box_csv_rows(empty))
    for row in rows[1:]:
        assert -180.0 <= float(row[4]) <= 180.0


# -- CSV for a sequence ---------------------------------------------------- #


def test_sequence_csv_adds_an_hour_column_first(sequence):
    rows = list(be.box_csv_rows(sequence))
    assert rows[0][0] == "fxx"
    assert rows[0][1:7] == [
        "request_id", "row", "col", "lat", "lon", "status"]
    hours = {int(row[0]) for row in rows[1:]}
    assert hours == set(sequence.hours)


def test_sequence_csv_covers_every_hour_and_node(sequence):
    rows = list(be.box_csv_rows(sequence))
    per_hour = len(sequence.at(sequence.hours[0]).points)
    assert len(rows) - 1 == per_hour * len(sequence.hours)


def test_sequence_csv_writes_one_file_for_all_hours(sequence, tmp_path):
    destination = tmp_path / "seq.csv"
    written = be.write_box_csv(sequence, destination)
    assert written == len(sequence.hours) * len(
        sequence.at(sequence.hours[0]).points)
    assert destination.read_text(encoding="utf-8").startswith("fxx,")


# -- GeoJSON --------------------------------------------------------------- #


def test_geojson_is_a_feature_collection_with_a_box_outline(analysis):
    document = be.box_geojson(analysis)
    assert document["type"] == "FeatureCollection"
    # One feature per node plus the requested box itself.
    assert len(document["features"]) == len(analysis.points) + 1
    assert document["features"][-1]["properties"]["kind"] == "box"


def test_geojson_cells_are_closed_rings_in_lon_lat_order(analysis):
    document = be.box_geojson(analysis)
    feature = document["features"][0]
    assert feature["geometry"]["type"] == "Polygon"
    ring = feature["geometry"]["coordinates"][0]
    assert len(ring) == 5
    assert ring[0] == ring[-1]
    for lon, lat in ring:
        assert -180.0 <= lon <= 180.0
        assert -90.0 <= lat <= 90.0
    # The cell surrounds its node rather than being a bare marker.
    lons = [lon for lon, _lat in ring]
    lats = [lat for _lon, lat in ring]
    assert min(lons) < feature["properties"]["lon"] < max(lons)
    assert min(lats) < feature["properties"]["lat"] < max(lats)


def test_geojson_properties_carry_identity_and_values(analysis):
    document = be.box_geojson(analysis)
    properties = document["features"][0]["properties"]
    for key in ("request_id", "row", "col", "lat", "lon", "status"):
        assert key in properties
    assert isinstance(properties["mucape"], float)


def test_geojson_absent_values_are_null_not_zero(analysis):
    document = be.box_geojson(analysis, keys=("mucape", "stp_cin"))
    properties = document["features"][0]["properties"]
    assert properties["stp_cin"] is None
    assert properties["mucape"] is not None


def test_geojson_metadata_describes_the_sampling(analysis):
    metadata = be.box_geojson(analysis)["metadata"]
    assert metadata["generator"] == be.GEOJSON_GENERATOR
    assert metadata["model_key"] == "hrrr"
    assert metadata["rows"] == analysis.rows
    assert metadata["cols"] == analysis.cols
    assert metadata["spacing_km"] == pytest.approx(
        analysis.plan.spacing_km, abs=1e-3)
    assert metadata["native_spacing_km"] == pytest.approx(3.0, abs=1e-3)
    assert metadata["fxx"] == 18
    assert metadata["fields"]


def test_geojson_records_a_screen_and_tags_features(analysis):
    document = be.box_geojson(analysis, criteria="organized convection")
    assert "MUCAPE" in document["metadata"]["criteria"]
    verdicts = {
        feature["properties"].get("matches")
        for feature in document["features"][:-1]
    }
    assert verdicts <= {True, False, None}
    assert True in verdicts


def test_geojson_writes_valid_parseable_json(analysis, tmp_path):
    destination = tmp_path / "gis" / "box.geojson"
    count = be.write_box_geojson(analysis, destination)
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert count == len(document["features"])
    assert document["type"] == "FeatureCollection"


def test_geojson_refuses_to_emit_nan(analysis, tmp_path):
    # allow_nan=False means a stray NaN would raise rather than produce a file
    # no strict JSON reader can load.
    destination = tmp_path / "box.geojson"
    be.write_box_geojson(analysis, destination)
    text = destination.read_text(encoding="utf-8")
    assert "NaN" not in text
    assert "Infinity" not in text


def test_geojson_splits_a_cell_that_crosses_the_antimeridian():
    region = BoxRegion.from_corners(50.0, 170.0, 56.0, 190.0)
    plan = plan_box_samples("gfs", region, target_points=9)
    empty = ba.analyze_box(plan, {}, tiers=(ba.FAST_TIER,))
    document = be.box_geojson(empty)
    kinds = {feature["geometry"]["type"] for feature in document["features"]}
    # At least one geometry straddles 180 and must be split, per RFC 7946.
    assert "MultiPolygon" in kinds
    for feature in document["features"]:
        rings = feature["geometry"]["coordinates"]
        flat = rings[0] if feature["geometry"]["type"] == "Polygon" else [
            point for polygon in rings for ring in polygon for point in ring
        ]
        for lon, _lat in flat:
            assert -180.0 <= lon <= 180.0


def test_geojson_clamps_latitudes_at_the_poles():
    region = BoxRegion.from_corners(84.0, 10.0, 90.0, 40.0)
    plan = plan_box_samples("gfs", region, target_points=9)
    empty = ba.analyze_box(plan, {}, tiers=(ba.FAST_TIER,))
    document = be.box_geojson(empty)
    for feature in document["features"]:
        if feature["geometry"]["type"] != "Polygon":
            continue
        for _lon, lat in feature["geometry"]["coordinates"][0]:
            assert -90.0 <= lat <= 90.0


# -- GeoJSON for a sequence ------------------------------------------------ #


def test_sequence_geojson_tags_every_feature_with_its_hour(sequence):
    document = be.box_geojson(sequence)
    hours = {
        feature["properties"]["fxx"]
        for feature in document["features"]
        if "fxx" in feature["properties"]
    }
    assert hours == set(sequence.hours)
    assert document["metadata"]["hours"] == list(sequence.hours)
    assert "fxx" not in document["metadata"]


def test_sequence_geojson_holds_every_hour_plus_one_outline(sequence):
    document = be.box_geojson(sequence)
    per_hour = len(sequence.at(sequence.hours[0]).points)
    assert len(document["features"]) == \
        per_hour * len(sequence.hours) + 1


# -- input validation ------------------------------------------------------ #


@pytest.mark.parametrize("writer", (be.box_csv_rows, be.box_geojson))
def test_exporters_reject_an_unsupported_source(writer):
    with pytest.raises(TypeError):
        list(writer(object()))


def test_exporters_reject_an_empty_sequence():
    empty = ba.BoxSequence(hours=(), analyses={})
    with pytest.raises(ValueError):
        be.box_geojson(empty)


def test_summarize_export_describes_both_shapes(analysis, sequence):
    single = be.summarize_export(analysis)
    assert "points" in single and "fields" in single
    many = be.summarize_export(sequence)
    assert "forecast hours" in many

"""The box-extract command line: planning, extraction, and reporting."""

from types import SimpleNamespace

import numpy as np
import pytest

from sharpmod import batch_extract
from sharpmod.tests._examples import examples_dir
from sharpmod.tools import box_extract


HRRR_NPZ = examples_dir() / "hrrr_point_36.68N_95.66W_f018.npz"

PLAINS = ["hrrr", "34.0", "-99.0", "37.0", "-95.0"]


def _run(argv):
    return box_extract.main(argv)


# -- field listing --------------------------------------------------------- #


def test_list_fields_reports_every_key_and_marks_the_expensive_tier(capsys):
    assert _run(["--list-fields"]) == 0
    out = capsys.readouterr().out
    assert "mucape" in out and "stp_cin" in out
    assert "Instability" in out and "Composites" in out
    assert "* needs --composites" in out


# -- planning -------------------------------------------------------------- #


def test_dry_run_describes_the_plan_without_downloading(capsys):
    assert _run([*PLAINS, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "HRRR" in out
    assert "Lattice" in out
    assert "Downloads  1" in out


def test_dry_run_honours_a_target_point_count(capsys):
    assert _run([*PLAINS, "--dry-run", "--target-points", "16"]) == 0
    first = capsys.readouterr().out
    assert _run([*PLAINS, "--dry-run", "--target-points", "100"]) == 0
    second = capsys.readouterr().out

    def count(text):
        for line in text.splitlines():
            if line.startswith("Lattice"):
                return int(line.split("=")[1].split()[0])
        raise AssertionError("no lattice line")

    assert count(second) > count(first)


def test_explicit_spacing_is_snapped_to_the_model_grid(capsys):
    assert _run([*PLAINS, "--dry-run", "--spacing-km", "1"]) == 0
    out = capsys.readouterr().out
    assert "native 3.0 km" in out
    assert "raised to 3.0 km" in out


def test_an_antimeridian_box_is_planned_as_the_short_way_round(capsys):
    assert _run(
        ["gfs", "50", "170", "56", "190", "--dry-run",
         "--target-points", "24"]) == 0
    out = capsys.readouterr().out
    assert "170.00E-170.00W" in out
    # 20 degrees of longitude at 53N, not the 340-degree complement.
    width = int(
        [line for line in out.splitlines() if line.startswith("Size")][0]
        .split()[1])
    assert width < 2000


# -- argument errors ------------------------------------------------------- #


def test_missing_corners_is_an_error(capsys):
    assert _run(["--dry-run"]) == 2
    assert "four box corners are required" in capsys.readouterr().err


def test_unknown_model_is_reported_once(capsys):
    assert _run(["nope", "34", "-99", "37", "-95", "--dry-run"]) == 2
    err = capsys.readouterr().err
    assert "unknown forecast model" in err
    # The message must not be double-quoted by KeyError's repr.
    assert err.count("unknown forecast model") == 1


def test_a_box_outside_the_domain_is_reported(capsys):
    assert _run(["hrrr", "-40", "20", "-30", "30", "--dry-run"]) == 2
    assert "outside that domain" in capsys.readouterr().err


def test_a_degenerate_box_is_reported_without_gui_wording(capsys):
    assert _run(["hrrr", "35", "-97", "35", "-97", "--dry-run"]) == 2
    err = capsys.readouterr().err
    assert "too small to sample" in err
    # This core is shared with the map, but the message must not tell a CLI
    # user to drag anything.
    assert "drag" not in err.lower()


def test_output_dir_is_required_for_a_real_run(capsys):
    assert _run(PLAINS) == 2
    assert "--output-dir is required" in capsys.readouterr().err


def test_withheld_model_is_refused(capsys):
    assert _run(["aigfs", "34", "-99", "37", "-95", "--dry-run"]) == 2
    assert "cannot produce a sounding" in capsys.readouterr().err


@pytest.mark.parametrize(
    "value", ("not-a-time", "2026-13-01T00:00"),
)
def test_an_invalid_run_time_is_rejected(value):
    with pytest.raises(SystemExit):
        _run([*PLAINS, "--dry-run", "--run", value])


def test_target_and_spacing_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        _run([*PLAINS, "--dry-run", "--target-points", "16",
              "--spacing-km", "20"])


# -- full run through a faked extractor ------------------------------------ #


@pytest.fixture
def fake_extractor(monkeypatch):
    """Stand in for BatchExtractor, writing real soundings for each request."""
    if not HRRR_NPZ.is_file():
        pytest.skip("no HRRR .npz example sounding")
    source = dict(np.load(HRRR_NPZ, allow_pickle=False))
    state = {}

    class FakeExtractor:
        def __init__(self, progress_callback=None):
            self.progress_callback = progress_callback

        def run(self, requests, **kwargs):
            from pathlib import Path

            state["ids"] = [item.id for item in requests]
            state["workers"] = kwargs["max_workers"]
            state["resume"] = kwargs["resume"]
            root = Path(kwargs["output_dir"])
            items = []
            for index, item in enumerate(requests):
                target = root / item.output
                target.parent.mkdir(parents=True, exist_ok=True)
                arrays = dict(source)
                # Vary the moisture so the reported fields are not one value
                # repeated across the whole box.
                low = arrays["pres"] > 800.0
                arrays["dwpc"] = np.where(
                    low & (arrays["dwpc"] > -9000.0),
                    arrays["dwpc"] + 0.3 * index, arrays["dwpc"])
                np.savez(target, **arrays)
                if self.progress_callback is not None:
                    self.progress_callback({
                        "event": "completed", "request_id": item.id})
                items.append(SimpleNamespace(
                    id=item.id, status="completed", output_path=target))
            return SimpleNamespace(
                items=tuple(items), completed=len(items), failed=0,
                cancelled=0, skipped=0, ok=True,
                manifest_path=root / "batch-manifest.json",
            )

    monkeypatch.setattr(batch_extract, "BatchExtractor", FakeExtractor)
    return state


def test_a_full_run_extracts_and_summarizes(capsys, tmp_path, fake_extractor):
    code = _run([
        "hrrr", "36.0", "-96.5", "37.4", "-94.8",
        "--output-dir", str(tmp_path), "--target-points", "16", "--quiet",
    ])
    out = capsys.readouterr().out
    assert code == 0
    # One model hour, so one worker and resume enabled.
    assert fake_extractor["workers"] == 1
    assert fake_extractor["resume"] is True
    assert len(fake_extractor["ids"]) > 4
    assert "completed=" in out
    assert "MUCAPE" in out
    assert "Extreme" in out


def test_a_full_run_can_print_a_field_grid(capsys, tmp_path, fake_extractor):
    code = _run([
        "hrrr", "36.0", "-96.5", "37.4", "-94.8",
        "--output-dir", str(tmp_path), "--target-points", "16",
        "--field", "mucape", "--quiet",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "row 0 = north edge" in out
    assert "MUCAPE [J/kg]" in out


def test_an_unknown_field_key_is_reported(capsys, tmp_path, fake_extractor):
    code = _run([
        "hrrr", "36.0", "-96.5", "37.4", "-94.8",
        "--output-dir", str(tmp_path), "--target-points", "16",
        "--field", "not-a-field", "--quiet",
    ])
    assert code == 2
    assert "unknown box parameter" in capsys.readouterr().err


def test_a_full_run_can_export_csv(capsys, tmp_path, fake_extractor):
    destination = tmp_path / "out" / "box.csv"
    code = _run([
        "hrrr", "36.0", "-96.5", "37.4", "-94.8",
        "--output-dir", str(tmp_path / "npz"), "--target-points", "16",
        "--csv", str(destination), "--quiet",
    ])
    assert code == 0
    assert destination.is_file()
    lines = destination.read_text(encoding="utf-8").splitlines()
    header = lines[0].split(",")
    assert header[:6] == [
        "request_id", "row", "col", "lat", "lon", "status"]
    assert "mucape" in header
    # One row per lattice node, header excluded.
    assert len(lines) - 1 > 4
    assert "ok" in lines[1]
    assert f"rows={len(lines) - 1}" in capsys.readouterr().out


def test_progress_is_printed_unless_quiet(capsys, tmp_path, fake_extractor):
    _run([
        "hrrr", "36.0", "-96.5", "37.4", "-94.8",
        "--output-dir", str(tmp_path), "--target-points", "16",
    ])
    assert "completed r000c000" in capsys.readouterr().out


def test_composites_flag_adds_the_expensive_tier(
    capsys, tmp_path, fake_extractor
):
    # Deliberately tiny: this tier costs about 0.4 s per point.
    code = _run([
        "hrrr", "36.6", "-95.8", "36.9", "-95.4",
        "--output-dir", str(tmp_path), "--target-points", "4",
        "--composites", "--quiet",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "STP (CIN)" in out


def test_an_empty_extraction_exits_nonzero(capsys, tmp_path, monkeypatch):
    class Empty:
        def __init__(self, progress_callback=None):
            pass

        def run(self, requests, **kwargs):
            from pathlib import Path

            return SimpleNamespace(
                items=(), completed=0, failed=len(requests), cancelled=0,
                skipped=0, ok=False,
                manifest_path=Path(kwargs["output_dir"]) / "m.json",
            )

    monkeypatch.setattr(batch_extract, "BatchExtractor", Empty)
    code = _run([*PLAINS, "--output-dir", str(tmp_path), "--quiet"])
    assert code == 1
    assert "no point in the box could be extracted" in capsys.readouterr().err


def test_a_batch_error_is_reported(capsys, tmp_path, monkeypatch):
    class Boom:
        def __init__(self, progress_callback=None):
            pass

        def run(self, requests, **kwargs):
            raise batch_extract.BatchSpecError("bad job")

    monkeypatch.setattr(batch_extract, "BatchExtractor", Boom)
    code = _run([*PLAINS, "--output-dir", str(tmp_path), "--quiet"])
    assert code == 2
    assert "bad job" in capsys.readouterr().err


def test_cancellation_returns_the_conventional_code(
    capsys, tmp_path, monkeypatch
):
    class Interrupted:
        def __init__(self, progress_callback=None):
            pass

        def run(self, requests, **kwargs):
            raise KeyboardInterrupt

    monkeypatch.setattr(batch_extract, "BatchExtractor", Interrupted)
    code = _run([*PLAINS, "--output-dir", str(tmp_path), "--quiet"])
    assert code == 130
    assert "cancelled" in capsys.readouterr().err


# -- parser ---------------------------------------------------------------- #


def test_parser_exposes_the_documented_options():
    parser = box_extract.build_parser()
    args = parser.parse_args([
        "hrrr", "34", "-99", "37", "-95",
        "--output-dir", "out", "--fxx", "6", "--member", "c00",
        "--loc", "Box", "--max-points", "36", "--composites",
        "--no-resume", "--quiet",
    ])
    assert args.model == "hrrr"
    assert args.fxx == 6
    assert args.member == "c00"
    assert args.max_points == 36
    assert args.composites is True
    assert args.no_resume is True


def test_run_time_parsing_assumes_utc():
    parser = box_extract.build_parser()
    args = parser.parse_args([*PLAINS, "--run", "2026-09-03T12:00"])
    assert args.run.tzinfo is not None
    assert args.run.hour == 12


# -- forecast-hour sequences ----------------------------------------------- #


def test_hours_option_samples_several_forecast_hours(
    capsys, tmp_path, fake_extractor
):
    code = _run([
        "hrrr", "36.0", "-96.5", "37.4", "-94.8",
        "--output-dir", str(tmp_path), "--target-points", "9",
        "--hours", "3", "--quiet",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "Hours      3 (F000 to F002)" in out
    # One model-hour group per hour, so three downloads.
    assert sorted({item[1] for item in _hours_of(fake_extractor)}) == [0, 1, 2]
    # A sequence holds at most two model hours at once.
    assert fake_extractor["workers"] == 2
    # The per-hour table is printed, with the peak marked.
    assert "MUCAPE" in out
    assert "F000" in out and "F002" in out
    assert "<- peak" in out
    assert "peak hour" in out


def test_sequence_reports_an_hour_when_all_of_its_nodes_fail(
        capsys, tmp_path, monkeypatch):
    if not HRRR_NPZ.is_file():
        pytest.skip("no HRRR .npz example sounding")
    source = dict(np.load(HRRR_NPZ, allow_pickle=False))

    class PartialHourExtractor:
        def __init__(self, progress_callback=None):
            self.progress_callback = progress_callback

        def run(self, requests, **kwargs):
            from pathlib import Path

            root = Path(kwargs["output_dir"])
            items = []
            completed = failed = 0
            for item in requests:
                target = root / item.output
                if item.fxx == 0:
                    failed += 1
                    items.append(SimpleNamespace(
                        id=item.id, status="failed", output_path=target))
                    continue
                completed += 1
                target.parent.mkdir(parents=True, exist_ok=True)
                np.savez(target, **source)
                items.append(SimpleNamespace(
                    id=item.id, status="completed", output_path=target))
            return SimpleNamespace(
                items=tuple(items), completed=completed, failed=failed,
                cancelled=0, skipped=0, ok=True,
                manifest_path=root / "batch-manifest.json",
            )

    monkeypatch.setattr(
        batch_extract, "BatchExtractor", PartialHourExtractor)

    code = _run([
        "hrrr", "36.0", "-96.5", "37.4", "-94.8",
        "--output-dir", str(tmp_path), "--target-points", "4",
        "--hours", "2", "--quiet",
    ])
    out = capsys.readouterr().out

    assert code == 0
    assert "F000" in out and "no data" in out
    assert "F001" in out


def _hours_of(state):
    """Return ``(request_id, hour)`` pairs from the recorded request ids."""
    return [(item, int(item[1:4])) for item in state["ids"]]


def test_hour_step_skips_intermediate_hours(
    capsys, tmp_path, fake_extractor
):
    code = _run([
        "hrrr", "36.0", "-96.5", "37.4", "-94.8",
        "--output-dir", str(tmp_path), "--target-points", "9",
        "--hours", "3", "--hour-step", "6", "--quiet",
    ])
    assert code == 0
    assert "F000 to F012" in capsys.readouterr().out
    assert sorted({hour for _id, hour in _hours_of(fake_extractor)}) == \
        [0, 6, 12]


def test_a_sequence_starts_from_fxx(capsys, tmp_path, fake_extractor):
    code = _run([
        "hrrr", "36.0", "-96.5", "37.4", "-94.8",
        "--output-dir", str(tmp_path), "--target-points", "9",
        "--fxx", "6", "--hours", "2", "--quiet",
    ])
    assert code == 0
    assert "F006 to F007" in capsys.readouterr().out


def test_hours_of_one_stays_a_single_hour_run(
    capsys, tmp_path, fake_extractor
):
    code = _run([
        "hrrr", "36.0", "-96.5", "37.4", "-94.8",
        "--output-dir", str(tmp_path), "--target-points", "9",
        "--hours", "1", "--quiet",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "Hours " not in out
    assert fake_extractor["workers"] == 1


def test_a_sequence_past_the_forecast_horizon_is_refused(capsys, tmp_path):
    # HRRR does not publish beyond F048, so nothing follows F048.
    code = _run([
        "hrrr", "36.0", "-96.5", "37.4", "-94.8",
        "--output-dir", str(tmp_path), "--fxx", "48", "--hours", "4",
        "--hour-step", "6", "--quiet",
    ])
    assert code == 2
    assert "no further forecast hour" in capsys.readouterr().err


def test_a_sequence_can_export_csv_for_its_peak_hour(
    capsys, tmp_path, fake_extractor
):
    destination = tmp_path / "seq.csv"
    code = _run([
        "hrrr", "36.0", "-96.5", "37.4", "-94.8",
        "--output-dir", str(tmp_path / "npz"), "--target-points", "9",
        "--hours", "2", "--csv", str(destination), "--quiet",
    ])
    assert code == 0
    assert destination.is_file()
    assert len(destination.read_text(encoding="utf-8").splitlines()) > 1


def test_sequence_hours_appear_in_the_parser():
    parser = box_extract.build_parser()
    args = parser.parse_args([
        *PLAINS, "--output-dir", "out", "--hours", "6", "--hour-step", "3",
    ])
    assert args.hours == 6
    assert args.hour_step == 3


# -- screens and GeoJSON export -------------------------------------------- #


def test_list_screens_describes_each_set(capsys):
    assert _run(["--list-screens"]) == 0
    out = capsys.readouterr().out
    assert "supercell" in out
    assert "MUCAPE" in out
    # The heuristic nature must be stated, not implied.
    assert "not official products" in out


def test_screen_reports_coverage_for_a_single_hour(
    capsys, tmp_path, fake_extractor
):
    code = _run([
        "hrrr", "36.0", "-96.5", "37.4", "-94.8",
        "--output-dir", str(tmp_path), "--target-points", "9",
        "--screen", "organized convection", "--quiet",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "Ingredient overlap: organized convection" in out
    assert "MUCAPE" in out
    assert "km" in out and "%" in out


def test_screen_reports_coverage_per_hour_for_a_sequence(
    capsys, tmp_path, fake_extractor
):
    code = _run([
        "hrrr", "36.0", "-96.5", "37.4", "-94.8",
        "--output-dir", str(tmp_path), "--target-points", "9",
        "--hours", "2", "--screen", "organized convection", "--quiet",
    ])
    out = capsys.readouterr().out
    assert code == 0
    assert "Ingredient overlap" in out
    assert "F000" in out and "F001" in out


def test_an_unknown_screen_is_reported(capsys, tmp_path, fake_extractor):
    code = _run([
        "hrrr", "36.0", "-96.5", "37.4", "-94.8",
        "--output-dir", str(tmp_path), "--target-points", "9",
        "--screen", "not-a-screen", "--quiet",
    ])
    assert code == 2
    assert "unknown ingredient screen" in capsys.readouterr().err


def test_geojson_export_writes_a_feature_collection(
    capsys, tmp_path, fake_extractor
):
    import json

    destination = tmp_path / "gis" / "box.geojson"
    code = _run([
        "hrrr", "36.0", "-96.5", "37.4", "-94.8",
        "--output-dir", str(tmp_path / "npz"), "--target-points", "9",
        "--geojson", str(destination), "--quiet",
    ])
    assert code == 0
    assert destination.is_file()
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert document["type"] == "FeatureCollection"
    assert document["metadata"]["model_key"] == "hrrr"
    assert f"features={len(document['features'])}" in capsys.readouterr().out


def test_geojson_export_tags_features_with_the_screen(
    tmp_path, fake_extractor
):
    import json

    destination = tmp_path / "box.geojson"
    assert _run([
        "hrrr", "36.0", "-96.5", "37.4", "-94.8",
        "--output-dir", str(tmp_path / "npz"), "--target-points", "9",
        "--geojson", str(destination), "--screen", "organized convection",
        "--quiet",
    ]) == 0
    document = json.loads(destination.read_text(encoding="utf-8"))
    assert "criteria" in document["metadata"]
    assert any(
        "matches" in feature["properties"]
        for feature in document["features"]
    )


def test_csv_and_geojson_can_be_written_together(
    tmp_path, fake_extractor
):
    csv_path = tmp_path / "box.csv"
    geo_path = tmp_path / "box.geojson"
    assert _run([
        "hrrr", "36.0", "-96.5", "37.4", "-94.8",
        "--output-dir", str(tmp_path / "npz"), "--target-points", "9",
        "--csv", str(csv_path), "--geojson", str(geo_path), "--quiet",
    ]) == 0
    assert csv_path.is_file() and geo_path.is_file()


def test_export_options_appear_in_the_parser():
    parser = box_extract.build_parser()
    args = parser.parse_args([
        *PLAINS, "--output-dir", "out", "--geojson", "g.geojson",
        "--screen", "supercell",
    ])
    assert str(args.geojson) == "g.geojson"
    assert args.screen == "supercell"

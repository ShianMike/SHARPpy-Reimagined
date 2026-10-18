"""Regression coverage for portable sounding overwrite and cancellation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from sharpmod.io.portable_sounding import (
    portable_sounding_pair_valid,
    validate_portable_sounding_pair,
)
from sharpmod.models.model_transport import DownloadCancelled
from sharpmod.tests.era5_synth import make_era5_dataset
from sharpmod.tools import era5_extract, model_extract


_WHEN = datetime(2024, 5, 20, tzinfo=timezone.utc)
_WRITERS = ("model", "era5", "openmeteo", "eccc", "wrf", "ifs",
            "observed", "box_mean")
_CANCELLABLE = ("model", "era5", "openmeteo", "eccc", "wrf")


def _extract(kind, output, *, loc, cancelled=None):
    if kind == "wrf":
        from sharpmod.tests.test_wrf_workflow import _wrf_dataset
        from sharpmod.tools import wrf_extract

        with _wrf_dataset() as dataset:
            return wrf_extract.extract(
                "memory://wrfout_d01", 35.1, -98.1, str(output),
                dataset=dataset, loc=loc, cancelled=cancelled,
            )
    if kind == "openmeteo":
        from sharpmod.providers import openmeteo
        from sharpmod.tests.test_openmeteo import MODEL, RUN, FXX, _fetch

        dataset, _ = _fetch()
        return openmeteo.extract(
            MODEL, 38.77, -90.87, run_time=RUN, fxx=FXX,
            dataset=dataset, out_path=str(output), loc=loc, cancelled=cancelled,
        )
    if kind == "eccc":
        from dataclasses import replace
        from sharpmod.providers import eccc_geomet
        from sharpmod.tests.test_eccc_geomet import RUN, _fake_get_factory

        capability = replace(
            eccc_geomet.get_capability("gdps"),
            pressure_levels=(1000, 850, 500), omega_levels=(850, 500),
        )
        request_get, _ = _fake_get_factory()
        dataset = eccc_geomet.fetch_point(
            capability, 45.5, -73.6, run_time=RUN, fxx=3,
            request_get=request_get, max_workers=2,
        )
        return eccc_geomet.extract(
            "gdps", 45.5, -73.6, run_time=RUN, fxx=3,
            dataset=dataset, out_path=str(output), loc=loc, cancelled=cancelled,
        )
    if kind == "observed":
        from sharpmod.providers import observations
        from sharpmod.tests.test_observations import _provider_and_urls

        provider, _ = _provider_and_urls()
        result = provider.fetch("72357", _WHEN)
        return observations.write_observed_npz(result, output, loc=loc)
    if kind == "box_mean":
        from sharpmod.analysis import box_mean
        from sharpmod.tests.test_box_mean import HRRR_NPZ, RUN, VALID

        profile = box_mean.box_mean_profile(
            [HRRR_NPZ, HRRR_NPZ], lat=36.7, lon=-96.0)
        return box_mean.write_box_mean_sounding(
            profile, output, model="HRRR", run_time=RUN, valid_time=VALID,
            fxx=18, loc=loc,
        )
    with make_era5_dataset(
        [34.0, 35.0], [260.0, 261.0], [1000.0, 850.0, 700.0], [_WHEN],
    ) as dataset:
        kwargs = dict(
            out_path=str(output), dataset=dataset, loc=loc, cancelled=cancelled,
        )
        if kind == "model":
            return model_extract.extract(
                "gfs", 35.0, -99.0, run_time=_WHEN, **kwargs,
            )
        if kind == "ifs":
            from sharpmod.tools import ifs_extract

            kwargs.pop("cancelled")
            return ifs_extract.extract(
                35.0, -99.0, _WHEN, run_time=_WHEN, **kwargs,
            )
        return era5_extract.extract(35.0, -99.0, _WHEN, **kwargs)


def _assert_pair(output, loc):
    assert validate_portable_sounding_pair(output) == output
    metadata = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
    with np.load(output, allow_pickle=False) as arrays:
        generation = arrays["pair_generation"].item()
        assert isinstance(generation, str) and generation
        assert metadata["pair_generation"] == generation
        assert str(arrays["loc"]) == loc
        if bool(arrays["observed"]):
            assert str(arrays["source_provider"]) == metadata["provider"]
            assert str(arrays["source_station"]) == metadata["source_station"]
            valid = datetime.strptime(
                str(arrays["valid"]), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            assert datetime.fromisoformat(
                metadata["valid"].replace("Z", "+00:00")) == valid
        else:
            assert metadata["loc"] == loc
            assert str(arrays["run"]) == metadata["run"]
            assert str(arrays["valid"]) == metadata["valid"]
    assert metadata["npz"] == str(output)


@pytest.mark.parametrize("kind", _WRITERS)
def test_every_writer_validates_a_marked_staged_pair(tmp_path, monkeypatch, kind):
    output = tmp_path / "point.npz"
    validate = era5_extract.validate_portable_sounding_pair
    staged = []

    def validate_staged(path):
        path = Path(path)
        assert path.name == "new.npz"
        with np.load(path, allow_pickle=False) as arrays:
            generation = arrays["pair_generation"].item()
        metadata = json.loads(path.with_suffix(".json").read_text("utf-8"))
        assert isinstance(generation, str) and generation
        assert metadata["pair_generation"] == generation
        staged.append(generation)
        return validate(path)

    monkeypatch.setattr(era5_extract, "validate_portable_sounding_pair", validate_staged)
    _extract(kind, output, loc="test")

    assert len(staged) == 1
    assert json.loads(output.with_suffix(".json").read_text("utf-8"))[
        "pair_generation"] == staged[0]
    _assert_pair(output, "test")


@pytest.mark.parametrize("kind", _WRITERS)
@pytest.mark.parametrize("previous_marked", (False, True), ids=("legacy", "marked"))
def test_reader_rejects_mixed_pair_between_replacements(
    tmp_path, monkeypatch, kind, previous_marked,
):
    output = tmp_path / "point.npz"
    _extract(kind, output, loc="previous")
    if not previous_marked:
        with np.load(output, allow_pickle=False) as data:
            arrays = {name: data[name] for name in data.files
                      if name != "pair_generation"}
        np.savez(output, **arrays)
        sidecar = output.with_suffix(".json")
        metadata = json.loads(sidecar.read_text("utf-8"))
        metadata.pop("pair_generation")
        sidecar.write_text(json.dumps(metadata), encoding="utf-8")
    assert portable_sounding_pair_valid(output)
    replace = era5_extract.os.replace
    checked = []

    def publish(source, target):
        replace(source, target)
        if Path(source).name == "new.npz" and Path(target) == output:
            with pytest.raises(ValueError, match="generation"):
                validate_portable_sounding_pair(output)
            assert not portable_sounding_pair_valid(output)
            checked.append(True)

    monkeypatch.setattr(era5_extract.os, "replace", publish)
    _extract(kind, output, loc="replacement")

    assert checked == [True]
    _assert_pair(output, "replacement")


@pytest.mark.parametrize("overwrite", (False, True), ids=("fresh", "overwrite"))
@pytest.mark.parametrize(
    ("kind", "fault"),
    [
        (kind, fault)
        for kind in _WRITERS
        for fault in (
            "npz_write", "json_write", "invalid_pair", "npz_replace",
            "json_replace", "cancel_staged", "cancel_npz", "cancel_json",
        )
        if kind in _CANCELLABLE or not fault.startswith("cancel_")
    ],
)
def test_failed_extraction_preserves_previous_output_and_cleans_own_partials(
    tmp_path, monkeypatch, kind, overwrite, fault,
):
    output = tmp_path / "point.npz"
    (tmp_path / "unrelated.txt").write_text("keep", encoding="utf-8")
    if overwrite:
        _extract(kind, output, loc="previous")
        _assert_pair(output, "previous")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    cancelled = False
    replace = era5_extract.os.replace
    dump = era5_extract.json.dump

    def fail_npz(file, **arrays):
        file.write(b"partial NPZ")
        raise OSError("injected NPZ write failure")

    def write_json(payload, file, **kwargs):
        if fault == "json_write":
            file.write('{"partial":')
            raise OSError("injected JSON write failure")
        dump([] if fault == "invalid_pair" else payload, file, **kwargs)

    def publish(source, target):
        nonlocal cancelled
        source, target = Path(source), Path(target)
        if source.name == "new.npz" and target == output:
            if fault == "npz_replace":
                raise PermissionError("injected NPZ replacement denial")
        if source.name == "new.json" and target == output.with_suffix(".json"):
            if fault == "json_replace":
                raise PermissionError("injected JSON replacement denial")
        replace(source, target)
        if (
            (fault == "cancel_staged" and target.name == "new.json")
            or (fault == "cancel_npz" and target == output)
            or (fault == "cancel_json" and target == output.with_suffix(".json"))
        ):
            cancelled = True

    if fault == "npz_write":
        monkeypatch.setattr(era5_extract.np, "savez", fail_npz)
    monkeypatch.setattr(era5_extract.json, "dump", write_json)
    monkeypatch.setattr(era5_extract.os, "replace", publish)
    error = (
        DownloadCancelled if kind in ("model", "openmeteo", "eccc")
        else era5_extract.ExtractionCancelled
    ) if fault.startswith("cancel_") else (
        ValueError if fault == "invalid_pair" else OSError
    )

    with pytest.raises(error):
        _extract(kind, output, loc="replacement", cancelled=lambda: cancelled)

    assert {path.name for path in tmp_path.iterdir()} == before.keys()
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    if overwrite:
        _assert_pair(output, "previous")


@pytest.mark.parametrize("kind", _WRITERS)
@pytest.mark.parametrize("suffix", (".npz", ".json"))
def test_backup_failure_does_not_touch_previous_pair(
    tmp_path, monkeypatch, kind, suffix,
):
    output = tmp_path / "point.npz"
    _extract(kind, output, loc="previous")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    copy = era5_extract.shutil.copy2

    def copy_previous(source, target):
        if Path(source).suffix == suffix:
            raise PermissionError("injected backup denial")
        return copy(source, target)

    monkeypatch.setattr(era5_extract.shutil, "copy2", copy_previous)
    with pytest.raises(PermissionError, match="backup denial"):
        _extract(kind, output, loc="replacement")

    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    _assert_pair(output, "previous")


@pytest.mark.parametrize("kind", _WRITERS)
def test_successful_overwrite_publishes_one_valid_pair_without_staging_files(
    tmp_path, kind,
):
    output = tmp_path / "point.npz"
    _extract(kind, output, loc="previous")
    old_bytes = output.read_bytes()
    old_metadata = json.loads(output.with_suffix(".json").read_text("utf-8"))

    assert _extract(kind, output, loc="replacement") == str(output)

    assert output.read_bytes() != old_bytes
    assert {path.name for path in tmp_path.iterdir()} == {"point.npz", "point.json"}
    _assert_pair(output, "replacement")
    metadata = json.loads(output.with_suffix(".json").read_text("utf-8"))
    assert metadata["pair_generation"] != old_metadata["pair_generation"]
    assert {key: value for key, value in metadata.items()
            if key not in {"loc", "pair_generation"}} == {
        key: value for key, value in old_metadata.items()
        if key not in {"loc", "pair_generation"}
    }


@pytest.mark.parametrize("kind", _WRITERS)
def test_interruption_after_primary_replacement_restores_the_previous_pair(
    tmp_path, monkeypatch, kind,
):
    output = tmp_path / "point.npz"
    _extract(kind, output, loc="previous")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    replace = era5_extract.os.replace

    def interrupt_sidecar(source, target):
        if Path(source).name == "new.json":
            raise KeyboardInterrupt("injected interruption")
        replace(source, target)

    monkeypatch.setattr(era5_extract.os, "replace", interrupt_sidecar)
    with pytest.raises(KeyboardInterrupt, match="injected interruption"):
        _extract(kind, output, loc="replacement")

    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    _assert_pair(output, "previous")


def test_failed_rollback_retains_previous_pair_for_recovery(tmp_path, monkeypatch):
    output = tmp_path / "point.npz"
    _extract("era5", output, loc="previous")
    previous_npz = output.read_bytes()
    previous_json = output.with_suffix(".json").read_bytes()
    replace = era5_extract.os.replace

    def deny_sidecar_and_restore(source, target):
        if Path(source).name in {"new.json", "previous.npz"}:
            raise PermissionError("injected persistent replacement denial")
        replace(source, target)

    monkeypatch.setattr(era5_extract.os, "replace", deny_sidecar_and_restore)
    with pytest.raises(OSError, match="recovery files retained in") as failure:
        _extract("era5", output, loc="replacement")

    recovery, = tmp_path.glob(".sharpmod-output-*")
    assert str(recovery) in str(failure.value)
    assert (recovery / "previous.npz").read_bytes() == previous_npz
    assert (recovery / "previous.json").read_bytes() == previous_json
    assert validate_portable_sounding_pair(recovery / "previous.npz")


def test_late_cancel_tries_both_restores_and_keeps_failed_restore_backup(
    tmp_path, monkeypatch,
):
    output = tmp_path / "point.npz"
    _extract("era5", output, loc="previous")
    previous_npz = output.read_bytes()
    previous_json = output.with_suffix(".json").read_bytes()
    replace = era5_extract.os.replace
    cancelled = False

    def deny_json_restore(source, target):
        nonlocal cancelled
        if Path(source).name == "previous.json":
            raise PermissionError("injected rollback denial")
        replace(source, target)
        if Path(target) == output.with_suffix(".json"):
            cancelled = True

    monkeypatch.setattr(era5_extract.os, "replace", deny_json_restore)
    with pytest.raises(OSError, match="recovery files retained in"):
        _extract("era5", output, loc="replacement", cancelled=lambda: cancelled)

    recovery, = tmp_path.glob(".sharpmod-output-*")
    assert output.read_bytes() == previous_npz
    assert (recovery / "previous.json").read_bytes() == previous_json
    replace(recovery / "previous.json", output.with_suffix(".json"))
    _assert_pair(output, "previous")


def test_cli_render_extraction_failure_preserves_previous_pair(
    tmp_path, monkeypatch, capsys,
):
    from sharpmod.tools import model_extract_cli

    output = tmp_path / "point.npz"
    _extract("model", output, loc="previous")
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    directories = []
    make_directory = model_extract_cli.tempfile.mkdtemp

    def make_download_dir(*args, **kwargs):
        path = make_directory(*args, **kwargs)
        directories.append(Path(path))
        return path

    def fail_extract(*args, **kwargs):
        (Path(kwargs["download_dir"]) / "partial.grib2").write_bytes(b"GRIB")
        raise OSError("injected retrieval failure")

    monkeypatch.setattr(model_extract_cli.tempfile, "mkdtemp", make_download_dir)
    monkeypatch.setattr(model_extract, "extract", fail_extract)

    assert model_extract.main([
        "gfs", "35", "-99", str(output), "--render", str(tmp_path / "point.png"),
    ]) == 1

    assert "injected retrieval failure" in capsys.readouterr().out
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    assert directories and all(not path.exists() for path in directories)
    _assert_pair(output, "previous")

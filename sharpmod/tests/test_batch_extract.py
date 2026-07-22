"""Deterministic batch extraction/cache/manifest tests."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from sharpmod.batch_extract import (
    MANIFEST_SCHEMA,
    MANIFEST_VERSION,
    BatchExtractor,
    BatchRequest,
    BatchSpecError,
    load_batch_spec,
    run_batch,
)
from sharpmod.backends.grib import DecodedPoint
from sharpmod.model_hour_cache import ModelHourCache
from sharpmod.tests.era5_synth import make_era5_dataset
from sharpmod.tools import model_extract


RUN = datetime(2026, 7, 8, 0, tzinfo=timezone.utc)


def _dataset():
    dataset = make_era5_dataset(
        lats=[34.0, 35.0],
        lons=[260.0, 261.0],
        levels=[1000.0, 850.0, 700.0],
        times=[RUN],
        seed=44,
    )
    return dataset.assign(
        vo=(dataset["t"].dims, np.full(dataset["t"].shape, 7.0e-5))
    )


def _requests():
    return [
        BatchRequest(
            "west", "gfs", 34.0, -100.0, RUN,
            fxx=0, output="points/west.npz", loc="west",
        ),
        BatchRequest(
            "east", "gfs", 35.0, -99.0, RUN,
            fxx=0, output="points/east.npz", loc="east",
        ),
    ]


def test_batch_reuses_one_model_hour_and_writes_atomic_manifest(
    tmp_path, monkeypatch
):
    calls = []

    def retrieve(config, run_dt, fxx, **kwargs):
        calls.append((
            config.key, run_dt, fxx, kwargs.get("download_dir"), dict(kwargs)
        ))
        return _dataset(), SimpleNamespace(
            grib="memory://gfs",
            _sharpmod_source_url="memory://gfs",
            _sharpmod_fields=("TMP", "HGT", "UGRD", "VGRD", "RH", "ABSV"),
            _sharpmod_transport="test",
        )

    monkeypatch.setattr(model_extract, "_retrieve_dataset", retrieve)

    result = run_batch(_requests(), output_dir=tmp_path, max_workers=2)

    assert result.ok
    assert result.completed == 2
    assert len(calls) == 1
    assert "lat" not in calls[0][4]
    assert "lon" not in calls[0][4]
    assert result.output_paths == (
        tmp_path / "points" / "west.npz",
        tmp_path / "points" / "east.npz",
    )
    assert [item.id for item in result.items] == ["west", "east"]
    manifest = json.loads(result.manifest_path.read_text("utf-8"))
    assert manifest["schema"] == MANIFEST_SCHEMA
    assert manifest["version"] == MANIFEST_VERSION
    assert manifest["summary"] == {
        "total": 2, "completed": 2, "failed": 0,
        "cancelled": 0, "skipped": 0,
    }
    assert manifest["requests"][0]["model_hour_reused"] is False
    assert manifest["requests"][1]["model_hour_reused"] is True
    for entry in manifest["requests"]:
        output = tmp_path / entry["output"]
        assert output.is_file()
        assert output.with_suffix(".json").is_file()
        assert entry["artifacts"]["npz_bytes"] == output.stat().st_size
    assert not list(tmp_path.rglob("*.tmp"))


def test_multi_point_local_grib_uses_one_vectorized_decode(
        tmp_path, monkeypatch):
    import sharpmod.batch_extract as batch_mod

    path = tmp_path / "batch.grib2"
    path.write_bytes(b"GRIB-batch-vectorized-7777")
    source = model_extract._LocalGribDataset(path)
    calls = []

    def retrieve(*_args, **_kwargs):
        return source, SimpleNamespace(
            grib="https://example.invalid/batch.grib2",
            _sharpmod_source_url="https://example.invalid/batch.grib2",
            _sharpmod_fields=("HGT", "TMP", "RH", "UGRD", "VGRD", "ABSV"),
            _sharpmod_transport="optimized-ranges",
        )

    def bulk_decode(received_path, points):
        calls.append((Path(received_path), tuple(points)))
        results = []
        for index, (lat, lon) in enumerate(points):
            matrix = np.vstack([
                [1000.0, 850.0],
                [100.0, 1500.0],
                [20.0, 10.0],
                [10.0, 0.0],
                [180.0, 200.0],
                [10.0, 20.0],
                [-0.2, -0.1],
                [0.0, 3.0],
                [5.0, 4.0],
            ])
            results.append(DecodedPoint(matrix, lat, lon, 8.0e-5 + index))
        return tuple(results)

    monkeypatch.setattr(model_extract, "_retrieve_dataset", retrieve)
    monkeypatch.setattr(batch_mod, "decode_grib_points", bulk_decode)

    result = run_batch(_requests(), output_dir=tmp_path / "out")

    assert result.ok
    assert len(calls) == 1
    assert calls[0][0] == path.resolve()
    assert calls[0][1] == ((34.0, -100.0), (35.0, -99.0))
    for output in result.output_paths:
        metadata = json.loads(output.with_suffix(".json").read_text("utf-8"))
        assert metadata["backend"] == (
            "vectorized multi-point direct GRIB decoder"
        )


def test_multi_point_missing_vorticity_uses_one_vectorized_wind_stencil(
        tmp_path, monkeypatch):
    import sharpmod.batch_extract as batch_mod

    path = tmp_path / "batch-wind.grib2"
    path.write_bytes(b"GRIB-batch-wind-vectorized-7777")
    source = model_extract._LocalGribDataset(path)
    wind_calls = []

    monkeypatch.setattr(
        model_extract,
        "_retrieve_dataset",
        lambda *_args, **_kwargs: (
            source,
            SimpleNamespace(grib="https://example.invalid/batch.grib2"),
        ),
    )

    def bulk_decode(_path, points):
        matrix = np.vstack([
            [1000.0, 850.0], [100.0, 1500.0], [20.0, 10.0],
            [10.0, 0.0], [180.0, 200.0], [10.0, 20.0],
            [-0.2, -0.1], [0.0, 3.0], [5.0, 4.0],
        ])
        return tuple(
            DecodedPoint(matrix, lat, lon, None) for lat, lon in points
        )

    def bulk_wind(_path, points):
        wind_calls.append(tuple(points))
        return (1.0e-5, 2.0e-5)

    monkeypatch.setattr(batch_mod, "decode_grib_points", bulk_decode)
    monkeypatch.setattr(
        batch_mod, "decode_grib_wind_vorticities", bulk_wind
    )

    result = run_batch(_requests(), output_dir=tmp_path / "out")

    assert result.ok
    assert wind_calls == [((34.0, -100.0), (35.0, -99.0))]
    for expected, output in zip((1.0e-5, 2.0e-5), result.output_paths):
        with np.load(output, allow_pickle=False) as payload:
            assert float(payload["surface_relative_vorticity"]) == expected
        metadata = json.loads(output.with_suffix(".json").read_text("utf-8"))
        assert metadata["surface_vorticity_source"] == (
            "targeted horizontal wind-gradient fallback"
        )


def test_resume_validates_checksums_and_is_deterministic(tmp_path, monkeypatch):
    calls = 0

    def retrieve(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _dataset(), SimpleNamespace(grib="memory://gfs")

    monkeypatch.setattr(model_extract, "_retrieve_dataset", retrieve)
    first = run_batch(_requests(), output_dir=tmp_path)
    assert calls == 1

    def unexpected(*_args, **_kwargs):
        raise AssertionError("a valid resumed job downloaded its model hour")

    monkeypatch.setattr(model_extract, "_retrieve_dataset", unexpected)
    second = run_batch(_requests(), output_dir=tmp_path)
    first_resume_text = second.manifest_path.read_text("utf-8")
    third = run_batch(_requests(), output_dir=tmp_path)

    assert second.skipped == third.skipped == 2
    assert second.output_paths == first.output_paths
    assert third.manifest_path.read_text("utf-8") == first_resume_text


def test_corrupt_completed_output_is_retried_not_silently_resumed(
    tmp_path, monkeypatch
):
    calls = 0

    def retrieve(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _dataset(), SimpleNamespace(grib="memory://gfs")

    monkeypatch.setattr(model_extract, "_retrieve_dataset", retrieve)
    first = run_batch(_requests(), output_dir=tmp_path)
    first.output_paths[0].write_bytes(b"not-an-npz")

    second = run_batch(_requests(), output_dir=tmp_path)

    assert calls == 2
    assert second.skipped == 1
    assert second.completed == 2
    with np.load(second.output_paths[0], allow_pickle=False) as payload:
        assert payload["pres"].size == 3


def test_external_cancellation_marks_every_request_without_loading(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        model_extract,
        "_retrieve_dataset",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cancelled job retrieved a model hour")
        ),
    )

    result = BatchExtractor().run(
        _requests(), output_dir=tmp_path, cancelled=lambda: True
    )

    assert result.completed == 0
    assert result.cancelled == 2
    assert [item.status for item in result.items] == ["cancelled", "cancelled"]
    assert not list(tmp_path.rglob("*.npz"))


def test_heterogeneous_hours_return_outputs_in_input_order(tmp_path, monkeypatch):
    calls = []

    def retrieve(config, run_dt, fxx, **_kwargs):
        calls.append((config.key, fxx))
        ds = _dataset()
        # The fixture has one time; model_extract derives valid time from it,
        # which is sufficient for this cache/grouping contract test.
        return ds, SimpleNamespace(grib=f"memory://gfs/f{fxx:03d}")

    monkeypatch.setattr(model_extract, "_retrieve_dataset", retrieve)
    requests = [
        BatchRequest("f002", "gfs", 35, -99, RUN, 2, "f002.npz"),
        BatchRequest("f000", "gfs", 35, -99, RUN, 0, "f000.npz"),
        BatchRequest("f001", "gfs", 35, -99, RUN, 1, "f001.npz"),
    ]

    result = run_batch(requests, output_dir=tmp_path, max_workers=1)

    assert calls == [("gfs", 2), ("gfs", 0), ("gfs", 1)]
    assert [path.name for path in result.output_paths] == [
        "f002.npz", "f000.npz", "f001.npz"
    ]


def test_caller_owned_model_hour_cache_remains_available(tmp_path, monkeypatch):
    calls = 0

    def retrieve(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return _dataset(), SimpleNamespace(grib="memory://gfs")

    monkeypatch.setattr(model_extract, "_retrieve_dataset", retrieve)
    cache = ModelHourCache(max_entries=1)
    try:
        first = run_batch(
            _requests(), output_dir=tmp_path / "first",
            model_hour_cache=cache,
        )
        second = run_batch(
            _requests(), output_dir=tmp_path / "second",
            model_hour_cache=cache,
        )

        assert first.ok and second.ok
        assert calls == 1
        assert len(cache) == 1
        second_manifest = json.loads(second.manifest_path.read_text("utf-8"))
        assert all(
            entry["model_hour_cache_hit"] is True
            for entry in second_manifest["requests"]
        )
    finally:
        cache.clear()


def test_single_point_hour_keeps_spatial_subset_route(tmp_path, monkeypatch):
    seen = []

    def retrieve(*_args, **kwargs):
        seen.append(kwargs)
        return _dataset(), SimpleNamespace(grib="memory://gfs")

    monkeypatch.setattr(model_extract, "_retrieve_dataset", retrieve)
    request = BatchRequest(
        "oun", "gfs", 35.0, -99.0, RUN, output="oun.npz"
    )

    result = run_batch([request], output_dir=tmp_path)

    assert result.ok
    assert seen[0]["lat"] == 35.0
    assert seen[0]["lon"] == -99.0


def test_point_only_provider_groups_cache_by_coordinate(tmp_path):
    requests = [
        BatchRequest("yul", "gdps", 45.5, -73.6, RUN, output="yul.npz"),
        BatchRequest("yyz", "gdps", 43.7, -79.4, RUN, output="yyz.npz"),
        BatchRequest("yul-copy", "gdps", 45.5, -73.6, RUN,
                     output="yul-copy.npz"),
    ]

    prepared = BatchExtractor._prepare_requests(requests, tmp_path.resolve())
    keys = [item.hour_key for item in prepared]

    assert keys[0] != keys[1]
    assert keys[0] == keys[2]
    assert keys[0].spatial == "45.5000,-73.6000"


def test_batch_rejects_output_path_traversal(tmp_path):
    request = BatchRequest(
        "escape", "gfs", 35.0, -99.0, RUN, output="../escape.npz"
    )

    with pytest.raises(BatchSpecError, match="escapes output_dir"):
        run_batch([request], output_dir=tmp_path)


def test_load_batch_spec_requires_versioned_nonempty_requests(tmp_path):
    spec = tmp_path / "job.json"
    spec.write_text(json.dumps({
        "version": 1,
        "requests": [{
            "id": "oun", "model": "gfs", "lat": 35.18, "lon": -97.44,
            "run": "2026-07-08T00:00:00Z", "fxx": 6,
        }],
    }), encoding="utf-8")

    requests = load_batch_spec(spec)

    assert len(requests) == 1
    assert requests[0].run_time == RUN
    assert requests[0].fxx == 6

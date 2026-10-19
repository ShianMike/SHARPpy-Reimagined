"""Source-hour reuse exercises the real extractor's ground validation and writes."""

import json
from datetime import UTC, datetime
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from sharpmod.bridge import sounding_cache, sounding_open
from sharpmod.bridge import cancellation
from sharpmod.bridge.errors import BridgeError
from sharpmod.models.model_hour_cache import ModelHourCache
from sharpmod.tests.test_model_extract import _dataset, _dataset_with_surface
from sharpmod.tools import model_extract as mx


RUN = datetime(2026, 7, 8, tzinfo=UTC)


@pytest.fixture
def sources(tmp_path, monkeypatch):
    loads, closed = [], []
    cache = ModelHourCache(directory_factory=lambda _key: str(tmp_path / f"source-{len(loads)}"))

    def retrieve(config, run, fxx, *, member, download_dir, lat, lon,
                 cancelled, progress_callback, on_bytes):
        assert not cancelled()
        loads.append((config.key, run, fxx, member, lat, lon, download_dir))
        Path(download_dir, "source.grib2").write_bytes(b"local source marker")
        progress_callback("downloading", 19)
        on_bytes(19)
        dataset = _dataset_with_surface()
        dataset.set_close(lambda: closed.append(download_dir))
        return dataset, SimpleNamespace(
            _sharpmod_source_url=f"memory://{config.key}/{run.isoformat()}/{fxx}",
            _sharpmod_fields=("HGT", "TMP", "RH", *mx.NOAA_SURFACE_FIELDS),
            _sharpmod_transport="test-source",
        )

    monkeypatch.setattr(mx, "_retrieve_dataset", retrieve)
    yield cache, loads, closed
    cache.clear()


def test_grid_source_is_reused_for_same_and_nearby_rrfs_points_without_changing_science(
        tmp_path, sources):
    cache, loads, closed = sources
    outputs = []
    for index, (lat, lon) in enumerate(((35.0, -99.0), (35.0, -99.0), (34.0, -100.0))):
        output = tmp_path / f"{index}.npz"
        mx.extract("rrfs", lat, lon, run_time=RUN, out_path=output,
                   loc=f"point {index}", source_cache=cache)
        reference = tmp_path / f"reference-{index}.npz"
        with _dataset_with_surface() as dataset:
            mx.extract("rrfs-a", lat, lon, run_time=RUN, out_path=reference,
                       loc=f"point {index}", dataset=dataset)
        with np.load(output, allow_pickle=False) as actual, np.load(reference, allow_pickle=False) as expected:
            for name in ("pres", "hght", "tmpc", "dwpc", "uwnd", "vwnd", "omeg", "lat", "lon", "surface_relative_vorticity"):
                np.testing.assert_array_equal(actual[name], expected[name])
        metadata = json.loads(output.with_suffix(".json").read_text())
        assert metadata["surface_merged"] and metadata["qc_valid"]
        assert metadata["loc"] == f"point {index}"
        assert metadata["run"] == "2026-07-08 00:00" and metadata["fxx"] == 0
        assert metadata["transport"] == "test-source"
        assert metadata["source_grib"] == "memory://rrfs-a/2026-07-08T00:00:00+00:00/0"
        outputs.append(output)
    assert len(loads) == 1 and not closed
    assert len(set(outputs)) == 3
    cache.clear()
    assert closed == [loads[0][-1]] and not Path(loads[0][-1]).exists()


@pytest.mark.parametrize("changed", [
    {"model": "refs-m1"}, {"run_time": datetime(2026, 7, 8, 6, tzinfo=UTC)},
    {"fxx": 1}, {"member": 1},
])
def test_source_identity_does_not_cross_model_cycle_hour_or_member(tmp_path, sources, changed):
    cache, loads, closed = sources
    params = {"model": "rrfs-a", "lat": 35.0, "lon": -99.0, "run_time": RUN}
    mx.extract(**params, out_path=tmp_path / "first.npz", source_cache=cache)
    mx.extract(**(params | changed), out_path=tmp_path / "second.npz", source_cache=cache)
    assert len(loads) == 2 and len(closed) == 1
    assert len(cache) == 1


def test_fixed_refs_member_keys_do_not_share_sources(tmp_path, sources):
    cache, loads, _closed = sources
    for index, model in enumerate(("refs-m1", "refs-m2")):
        mx.extract(model, 35.0, -99.0, run_time=RUN,
                   out_path=tmp_path / f"{index}.npz", source_cache=cache)
    assert [call[0] for call in loads] == ["refs-m1", "refs-m2"]


def test_point_subsets_use_exact_spatial_keys(tmp_path, sources, monkeypatch):
    cache, loads, _closed = sources
    monkeypatch.setenv("SHARPMOD_POINT_BACKENDS", "auto")
    for index, lat in enumerate((35.000001, 35.000002)):
        mx.extract("hrrr", lat, -99.0, run_time=RUN, fxx=1,
                   out_path=tmp_path / f"{index}.npz", source_cache=cache)
    assert len(loads) == 2


@pytest.mark.parametrize("model", ["gdps", "openmeteo-icon-global", "hrrr"])
def test_point_only_and_hrrr_analysis_routes_do_not_enter_source_cache(tmp_path, sources, monkeypatch, model):
    cache, loads, _closed = sources
    monkeypatch.setenv("SHARPMOD_POINT_BACKENDS", "auto")
    monkeypatch.setenv("SHARPMOD_HRRR_BACKEND", "auto")
    monkeypatch.setattr(cache, "lease", lambda *_args, **_kwargs: pytest.fail("point route entered source cache"))
    if model == "gdps":
        monkeypatch.setattr(mx.eccc_geomet, "extract", lambda *_args, **_kwargs: "point-output")
    elif model.startswith("openmeteo"):
        monkeypatch.setattr(mx.openmeteo, "extract", lambda *_args, **_kwargs: "point-output")
    result = mx.extract(model, 35.0, -99.0, run_time=RUN,
                        out_path=tmp_path / "point.npz", source_cache=cache,
                        download_dir=tmp_path,
                        progress_callback=lambda *_args: None, on_bytes=lambda _size: None,
                        cancelled=lambda: False)
    assert len(cache) == 0
    if model != "hrrr":
        assert result == "point-output"
    else:
        assert len(loads) == 1


def test_reused_source_cannot_bypass_verified_ground_validation(tmp_path, sources, monkeypatch):
    cache, _loads, _closed = sources
    monkeypatch.setattr(mx, "_retrieve_dataset", lambda *_args, **_kwargs: (_dataset(), None))
    output = tmp_path / "no-ground.npz"
    with pytest.raises(mx.RetrievalError, match="verified surface merge"):
        mx.extract("rrfs-a", 35.0, -99.0, run_time=RUN,
                   out_path=output, source_cache=cache)
    assert not output.exists() and not output.with_suffix(".json").exists()
    assert len(cache) == 0 and not cache.protected_directories()


def test_bridge_reuses_source_but_registers_distinct_sounding_files(tmp_path, sources, monkeypatch):
    source_cache, loads, _closed = sources
    session = sounding_cache.SoundingCache(tmp_path / "bridge")
    session.model_hours = source_cache
    monkeypatch.setattr(sounding_cache, "_CACHE", session)
    # The extraction and its NPZ/JSON validation stay real; summary decoding is
    # already covered by test_bridge_soundings and adds no ownership coverage.
    monkeypatch.setattr(sounding_open, "decode", lambda path: (SimpleNamespace(
        _meta=json.loads(Path(path).with_suffix(".json").read_text()),
    ), ""))
    results = [sounding_open.open_model({
        "model": "rrfs-a", "forecast_hour": 0, "latitude": lat,
        "longitude": lon, "run_time": "2026-07-08T00:00:00Z", "location": name,
    }) for lat, lon, name in ((35.0, -99.0, "first"), (34.0, -100.0, "second"))]
    assert len(loads) == 1
    assert results[0]["sounding_id"] != results[1]["sounding_id"]
    assert [result["location"] for result in results] == ["first", "second"]
    for result in results:
        assert session.get(result["sounding_id"]).path.is_file()
    session._remove_files(results[0]["sounding_id"])
    assert Path(loads[0][-1]).exists()


def test_bridge_captures_stop_event_before_background_loader_or_waiter_poll(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    session = sounding_cache.SoundingCache(tmp_path / "bridge")
    monkeypatch.setattr(sounding_cache, "_CACHE", session)
    stop = Event()
    original = session.model_hours.lease

    @contextmanager
    def checked(*args, cancelled, **kwargs):
        stop.set()
        with ThreadPoolExecutor(1) as pool:
            # This thread has no cancellation.scope. A thread-local callback
            # returns False here even before a waiter has time to unregister.
            assert pool.submit(cancelled).result(timeout=5) is True
        with original(*args, cancelled=cancelled, **kwargs) as result:
            yield result

    monkeypatch.setattr(session.model_hours, "lease", checked)
    with cancellation.scope(stop), pytest.raises(BridgeError) as error:
        sounding_open.open_model({"model": "rrfs-a", "forecast_hour": 0,
                                 "latitude": 35.0, "longitude": -99.0,
                                 "run_time": "2026-07-08T00:00:00Z"})
    assert error.value.details == {"cancelled": True}
    assert not session.model_hours._inflight and not list(session.model_root.iterdir())


def test_session_reset_defers_active_source_disposal(tmp_path, monkeypatch):
    from sharpmod.models.model_hour_cache import ModelHourKey

    session = sounding_cache.SoundingCache(tmp_path / "old")
    monkeypatch.setattr(sounding_cache, "_CACHE", session)
    closed = []
    with session.model_hours.lease(
        ModelHourKey.create("rrfs-a", RUN, 0),
        lambda directory: (SimpleNamespace(close=lambda: closed.append(directory)), None),
    ) as (entry, _hit):
        current = sounding_cache.reset_cache(tmp_path / "new")
        assert current is not session and not closed
        assert Path(entry.download_dir).exists() and len(session.model_hours) == 0
    assert closed == [entry.download_dir] and not Path(entry.download_dir).exists()

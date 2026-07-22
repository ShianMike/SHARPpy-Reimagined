"""Focused ERA5 and raw-WRF desktop workflow regressions."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from qtpy.QtWidgets import QApplication

from sharpmod import gui_picker, gui_workers
from sharpmod.model_disk_cache import ModelDiskCache
from sharpmod.tests.era5_synth import make_era5_dataset
from sharpmod.tools import era5_extract, wrf_extract


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


def _wrf_dataset():
    xr = pytest.importorskip("xarray")
    times = np.asarray([list("2024-05-20_00:00:00")], dtype="S1")
    lat2d = np.asarray([
        [34.0, 34.1, 34.2],
        [35.0, 35.1, 35.2],
        [36.0, 36.1, 36.2],
    ])
    lon2d = np.asarray([
        [-99.0, -98.0, -97.0],
        [-99.1, -98.1, -97.1],
        [-99.2, -98.2, -97.2],
    ])
    shape = (1, 3, 3, 3)
    pressure = np.asarray([100000.0, 85000.0, 70000.0])[None, :, None, None]
    pressure = np.broadcast_to(pressure, shape)
    geopotential = (
        np.asarray([0.0, 1000.0, 3000.0, 6000.0])
        * wrf_extract.G0
    )[None, :, None, None]
    geopotential = np.broadcast_to(geopotential, (1, 4, 3, 3))
    return xr.Dataset({
        "Times": (("Time", "DateStrLen"), times),
        "XLAT": (("Time", "south_north", "west_east"), lat2d[None]),
        "XLONG": (("Time", "south_north", "west_east"), lon2d[None]),
        "P": (("Time", "bottom_top", "south_north", "west_east"),
              np.zeros(shape)),
        "PB": (("Time", "bottom_top", "south_north", "west_east"),
               pressure),
        "PH": (("Time", "bottom_top_stag", "south_north", "west_east"),
               np.zeros((1, 4, 3, 3))),
        "PHB": (("Time", "bottom_top_stag", "south_north", "west_east"),
                geopotential),
        "T": (("Time", "bottom_top", "south_north", "west_east"),
              np.zeros(shape)),
        "QVAPOR": (("Time", "bottom_top", "south_north", "west_east"),
                   np.full(shape, 0.008)),
        "U": (("Time", "bottom_top", "south_north", "west_east_stag"),
              np.full((1, 3, 3, 4), 8.0)),
        "V": (("Time", "bottom_top", "south_north_stag", "west_east"),
              np.full((1, 3, 4, 3), 4.0)),
        "COSALPHA": (("Time", "south_north", "west_east"),
                     np.ones((1, 3, 3))),
        "SINALPHA": (("Time", "south_north", "west_east"),
                     np.zeros((1, 3, 3))),
    })


def test_picker_exposes_era5_and_guided_raw_wrf_tabs(
        qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("SHARPMOD_SETTINGS_PATH", str(tmp_path / "settings.ini"))
    monkeypatch.setenv("SHARPMOD_MODEL_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(
        gui_picker.PickerWindow, "_refresh_station_catalog",
        lambda *_args, **_kwargs: None,
    )

    picker = gui_picker.PickerWindow()
    labels = [picker._tabs.tabText(i) for i in range(picker._tabs.count())]

    assert "Reanalysis (ERA5)" in labels
    assert "Open File" in labels
    assert picker._file_modes.tabText(1) == "Raw WRF wrfout"
    assert picker._era5_map is not picker._model_map
    assert picker._wrf_map is not picker._model_map
    assert picker._model_timeline_btn.text() == "Timeline…"
    assert picker._cache_library_action.text() == "Downloaded Data &Library…"
    assert picker._manage_locations_action.text() == "Manage Saved Locations…"
    picker._saved_location_store.upsert("OUN", 35.22, -97.44)
    picker._refresh_location_markers()
    marker = picker._model_map._saved_points[0]
    assert marker[0] == "OUN"
    assert marker[1:] == pytest.approx((-97.44, 35.22))
    picker.close()


def test_era5_worker_reuses_snapped_point_hour_cache(
        qt_app, tmp_path, monkeypatch):
    calls = []
    valid = datetime(2024, 5, 20, 0, tzinfo=timezone.utc)

    def fake_extract(lat, lon, valid_time, out_path, loc="ERA5pt",
                     progress_callback=None, cancelled=None, **_kwargs):
        calls.append((lat, lon, valid_time, loc))
        if progress_callback:
            progress_callback("retrieving")
        arrays = {
            "pres": [1000.0, 850.0], "hght": [100.0, 1500.0],
            "tmpc": [20.0, 10.0], "dwpc": [15.0, 5.0],
            "wdir": [180.0, 200.0], "wspd": [10.0, 20.0],
            "loc": loc, "model": "ERA5", "lat": lat, "lon": lon,
        }
        era5_extract._atomic_write_npz(out_path, arrays)
        era5_extract._atomic_write_json(
            str(Path(out_path).with_suffix(".json")),
            {"loc": loc, "requested_lat": lat, "requested_lon": lon,
             "npz": str(out_path)},
        )
        return str(out_path)

    monkeypatch.setattr(era5_extract, "extract", fake_extract)
    cache = ModelDiskCache(tmp_path / "cache")
    outputs = []
    progress = []
    for index, (lat, lon) in enumerate(((35.18, -97.44), (35.20, -97.40))):
        output_dir = tmp_path / f"viewer-{index}"
        output_dir.mkdir()
        worker = gui_workers._ERA5FetchWorker(
            lat, lon, valid, output_dir / "sounding.npz",
            loc=f"request-{index}", disk_cache=cache,
        )
        worker.progress.connect(progress.append)
        worker.finished_ok.connect(lambda path, *_args: outputs.append(path))
        worker.run()

    assert len(calls) == 1
    assert calls[0][:2] == (35.25, -97.5)
    assert "cached" in progress
    assert len(outputs) == 2
    with np.load(outputs[1], allow_pickle=True) as data:
        assert str(data["loc"]) == "request-1"
    metadata = json.loads(Path(outputs[1]).with_suffix(".json").read_text())
    assert metadata["requested_lat"] == 35.20
    assert metadata["requested_lon"] == -97.40
    assert metadata["cache_hit"] is True

    gui_workers._cleanup_point_data(outputs[0], str(tmp_path / "viewer-0"))
    assert not (tmp_path / "viewer-0").exists()
    assert any((entry["path"] / "era5-point.npz").exists()
               for entry in cache._entries())


def test_era5_cancel_after_synchronous_retrieval_cleans_outputs(tmp_path):
    valid = datetime(2024, 5, 20, 0, tzinfo=timezone.utc)
    dataset = make_era5_dataset(
        [35.0, 35.25], [262.5, 262.75], [1000.0, 850.0, 700.0],
        [valid], seed=9,
    )
    state = {"cancelled": False}

    def progress(stage):
        if stage == "writing":
            state["cancelled"] = True

    out = tmp_path / "cancelled.npz"
    with pytest.raises(era5_extract.ExtractionCancelled):
        era5_extract.extract(
            35.1, -97.4, valid, out, dataset=dataset,
            progress_callback=progress,
            cancelled=lambda: state["cancelled"],
        )

    assert not out.exists()
    assert not out.with_suffix(".json").exists()


def test_wrf_inspection_and_extraction_enforce_real_grid_perimeter(tmp_path):
    dataset = _wrf_dataset()
    domain = wrf_extract.inspect_file("memory://wrfout_d01", dataset=dataset)

    assert domain["shape"] == (3, 3)
    assert domain["times"] == (
        datetime(2024, 5, 20, 0, tzinfo=timezone.utc),)
    assert wrf_extract.point_in_domain(domain, 35.1, -98.1)
    assert not wrf_extract.point_in_domain(domain, 36.15, -99.5)

    out = tmp_path / "wrf.npz"
    stages = []
    wrf_extract.extract(
        "memory://wrfout_d01", 35.1, -98.1, out,
        dataset=dataset, progress_callback=stages.append,
    )
    assert out.exists()
    assert out.with_suffix(".json").exists()
    assert stages == ["validating", "extracting", "writing", "complete"]
    metadata = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert metadata["backend"] == "xarray/NetCDF WRF"
    assert metadata["decoder"] == "raw wrfout pressure-column extractor"
    assert metadata["cache_hit"] is False

    outside = tmp_path / "outside.npz"
    with pytest.raises(wrf_extract.ParameterRangeError, match="outside"):
        wrf_extract.extract(
            "memory://wrfout_d01", 36.15, -99.5, outside,
            dataset=dataset,
        )
    assert not outside.exists()


def test_wrf_workers_surface_inspection_and_cleanup_on_cancel(
        qt_app, tmp_path, monkeypatch):
    domain = {
        "source_file": str(tmp_path / "wrfout_d01"),
        "shape": (3, 4),
        "bounds": (-99.0, -97.0, 34.0, 36.0),
        "center": (35.0, -98.0),
        "boundary": ((34.0, -99.0), (34.0, -97.0),
                     (36.0, -97.0), (36.0, -99.0)),
        "times": (datetime(2024, 5, 20, tzinfo=timezone.utc),),
    }
    monkeypatch.setattr(wrf_extract, "inspect_file", lambda *_a, **_k: domain)
    inspected = []
    inspector = gui_workers._WRFInspectWorker(domain["source_file"])
    inspector.inspected.connect(inspected.append)
    inspector.run()
    assert inspected == [domain]

    output_dir = tmp_path / "wrf-output"
    output_dir.mkdir()
    out = output_dir / "sounding.npz"

    def cancelled_extract(*_args, **_kwargs):
        out.write_bytes(b"partial")
        out.with_suffix(".json").write_text("{}", encoding="utf-8")
        raise wrf_extract.ExtractionCancelled("cancelled")

    monkeypatch.setattr(wrf_extract, "extract", cancelled_extract)
    cancelled = []
    worker = gui_workers._WRFExtractWorker(
        domain["source_file"], 35.0, -98.0, out)
    worker.cancelled.connect(lambda: cancelled.append(True))
    worker.run()

    assert cancelled == [True]
    assert not output_dir.exists()

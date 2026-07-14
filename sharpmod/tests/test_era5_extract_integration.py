"""Integration test for the ERA5_Extractor (task 12.5).

Exercises :func:`sharpmod.tools.era5_extract.extract` end-to-end against a small
synthetic ``xarray.Dataset`` (no network / Herbie), covering:

* **nearest-point / nearest-time wiring** -- the column written is the one at
  the true great-circle nearest grid point and the true nearest analysis time
  (Requirement 8.1);
* **atomic write** -- a successful extraction leaves a complete, loadable
  ``.npz`` plus its ``.json`` sidecar and no leftover temp files;
* **retrieval-failure cleanup** -- when retrieval raises (simulated via a mock),
  the extractor surfaces a :class:`RetrievalError` and leaves **no** output
  file behind (Requirement 8.6).

_Requirements: 8.1, 8.6_
"""

from __future__ import annotations

import glob
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from sharpmod.io import decoder as decoder_mod
from sharpmod.tools import era5_extract as era5
from sharpmod.tests.era5_synth import make_era5_dataset

_LEVELS = [1000.0, 925.0, 850.0, 700.0, 500.0, 300.0, 200.0]
_TIMES = [
    datetime(2018, 9, 1, 0, tzinfo=timezone.utc),
    datetime(2018, 9, 1, 6, tzinfo=timezone.utc),
    datetime(2018, 9, 1, 12, tzinfo=timezone.utc),
]


def _dataset():
    lats = np.array([25.0, 30.0, 35.0, 40.0, 45.0], dtype=float)
    lons = np.array([250.0, 255.0, 260.0, 265.0, 270.0], dtype=float)
    return lats, lons, make_era5_dataset(lats, lons, _LEVELS, _TIMES, seed=42)


def test_retrieve_dataset_uses_cds_pressure_level_request(monkeypatch):
    """Live ERA5 retrieval uses CDS, not Herbie's removed ERA5 model."""
    calls = {}

    class FakeClient:
        def retrieve(self, dataset, request, target):
            calls["dataset"] = dataset
            calls["request"] = request
            calls["target"] = target
            Path(target).write_bytes(b"fake-grib")

    class FakeDataset:
        loaded = False
        closed = False

        def load(self):
            self.loaded = True
            return self

        def close(self):
            self.closed = True

    decoded = FakeDataset()

    def open_datasets(path, backend_kwargs=None):
        calls["opened"] = path
        calls["backend_kwargs"] = backend_kwargs
        return [decoded]

    monkeypatch.setitem(
        sys.modules, "cdsapi", SimpleNamespace(Client=FakeClient))
    monkeypatch.setitem(
        sys.modules, "cfgrib", SimpleNamespace(open_datasets=open_datasets))
    monkeypatch.setattr(era5, "_merge_datasets", lambda values: values[0])

    result = era5._retrieve_dataset(
        58.26, 59.73,
        datetime(2026, 6, 22, 12, tzinfo=timezone.utc))

    assert result is decoded
    assert decoded.loaded
    assert decoded.closed
    assert calls["dataset"] == "reanalysis-era5-pressure-levels"
    request = calls["request"]
    assert request["year"] == "2026"
    assert request["month"] == "06"
    assert request["day"] == "22"
    assert request["time"] == "12:00"
    assert request["area"] == [58.25, 59.75, 58.25, 59.75]
    assert len(request["pressure_level"]) == 37
    assert set(request["variable"]) == {
        "geopotential",
        "relative_humidity",
        "temperature",
        "u_component_of_wind",
        "v_component_of_wind",
        "vertical_velocity",
    }
    assert request["data_format"] == "grib"
    assert request["download_format"] == "unarchived"
    assert calls["backend_kwargs"] == {"indexpath": ""}
    assert not Path(calls["target"]).exists()


def test_retrieve_dataset_explains_missing_cds_credentials(monkeypatch):
    """A missing CDS profile produces setup guidance instead of a model error."""
    class MissingCredentialsClient:
        def __init__(self):
            raise Exception(
                "Missing/incomplete configuration file: C:/Users/test/.cdsapirc")

    monkeypatch.setitem(
        sys.modules, "cdsapi",
        SimpleNamespace(Client=MissingCredentialsClient))
    monkeypatch.setitem(
        sys.modules, "cfgrib",
        SimpleNamespace(open_datasets=lambda *_args, **_kwargs: []))

    with pytest.raises(
            era5.RetrievalError,
            match=r"CDS API credentials.*\.cdsapirc"):
        era5._retrieve_dataset(
            58.26, 59.73,
            datetime(2026, 6, 22, 12, tzinfo=timezone.utc))


# --------------------------------------------------------------------------- #
# Nearest-point / nearest-time wiring + atomic write
# --------------------------------------------------------------------------- #
def test_extract_selects_nearest_point_and_time_and_writes_atomically(tmp_path):
    """A successful extraction writes the nearest column and loads cleanly.

    _Requirements: 8.1, 8.6
    """
    lats, lons, ds = _dataset()
    req_lat, req_lon, req_time = 34.4, 261.2, datetime(
        2018, 9, 1, 5, tzinfo=timezone.utc)  # closest to 35N/260E and 06Z

    out_path = str(tmp_path / "era5_point.npz")
    result = era5.extract(req_lat, req_lon, req_time, out_path, dataset=ds)
    assert result == out_path

    # Primary output + sidecar exist; no leftover temp files in the directory.
    assert os.path.exists(out_path)
    json_path = os.path.splitext(out_path)[0] + ".json"
    assert os.path.exists(json_path)
    leftover = [p for p in glob.glob(str(tmp_path / "*"))
                if p not in (out_path, json_path)]
    assert leftover == [], f"unexpected leftover files: {leftover}"

    # Nearest-point / nearest-time wiring: the .npz records the true nearest.
    (_, _), true_lat, true_lon = era5.select_nearest_grid_point(
        lats, lons, req_lat, req_lon)
    true_lon_norm = ((true_lon + 180.0) % 360.0) - 180.0
    _, true_time = era5.select_nearest_time(_TIMES, req_time)

    with np.load(out_path, allow_pickle=True) as npz:
        assert float(npz["lat"]) == true_lat
        assert float(npz["lon"]) == true_lon_norm
        assert str(npz["valid"]) == true_time.strftime("%Y-%m-%d %H:%M")

    with open(json_path, encoding="utf-8") as fh:
        meta = json.load(fh)
    assert meta["selected_lat"] == true_lat
    assert meta["selected_lon"] == true_lon_norm
    assert meta["selected_valid"] == true_time.strftime("%Y-%m-%d %H:%M")

    # The output loads through the shared point-sounding path.
    prof_collection, loc = decoder_mod.load_npz(out_path)
    assert loc
    prof = next(iter(prof_collection._profs.values()))[0]
    assert np.asarray(prof.pres).size == len(_LEVELS)


# --------------------------------------------------------------------------- #
# Retrieval-failure cleanup (Requirement 8.6)
# --------------------------------------------------------------------------- #
def test_retrieval_failure_leaves_no_output_file(tmp_path, monkeypatch):
    """A retrieval failure raises RetrievalError and writes no output file.

    _Requirements: 8.6
    """
    def _boom(lat, lon, valid_time):
        raise era5.RetrievalError("simulated ERA5 retrieval failure")

    # No dataset= supplied -> extract() calls _retrieve_dataset, which we mock.
    monkeypatch.setattr(era5, "_retrieve_dataset", _boom)

    out_path = str(tmp_path / "era5_point.npz")
    with pytest.raises(era5.RetrievalError, match="retrieval failure"):
        era5.extract(35.0, 260.0, _TIMES[0], out_path)

    # No primary output, no sidecar, no leftover temp files.
    assert not os.path.exists(out_path)
    assert not os.path.exists(os.path.splitext(out_path)[0] + ".json")
    assert glob.glob(str(tmp_path / "*")) == []


def test_out_of_range_request_writes_nothing(tmp_path):
    """An out-of-range latitude raises ParameterRangeError and writes nothing.

    _Requirements: 8.6
    """
    _, _, ds = _dataset()
    out_path = str(tmp_path / "era5_point.npz")

    with pytest.raises(era5.ParameterRangeError, match="latitude"):
        era5.extract(999.0, 260.0, _TIMES[0], out_path, dataset=ds)

    assert not os.path.exists(out_path)
    assert glob.glob(str(tmp_path / "*")) == []


def test_json_sidecar_failure_rolls_back_primary_output(tmp_path, monkeypatch):
    """If the sidecar write fails, the primary .npz is rolled back.

    Confirms the atomic-pair guarantee: no orphaned .npz is left when the
    metadata sidecar cannot be written (Requirement 8.6).
    """
    _, _, ds = _dataset()

    def _boom_json(path, payload):
        raise OSError("simulated sidecar write failure")

    monkeypatch.setattr(era5, "_atomic_write_json", _boom_json)

    out_path = str(tmp_path / "era5_point.npz")
    with pytest.raises(OSError, match="sidecar write failure"):
        era5.extract(35.0, 260.0, _TIMES[0], out_path, dataset=ds)

    assert not os.path.exists(out_path), "primary .npz was not rolled back"
    assert glob.glob(str(tmp_path / "*")) == []

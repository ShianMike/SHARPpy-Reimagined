"""Raw WRF NetCDF extraction, domain validation, runtime, and cancellation regressions."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from types import SimpleNamespace

import numpy as np
import pytest

from sharpmod.tools import wrf_extract
from sharpmod.upstream_warnings import (
    known_netcdf4_numpy_shape_deprecation,
)


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
    assert metadata["observed"] is False
    with np.load(out, allow_pickle=False) as payload:
        assert bool(payload["observed"]) is False

    outside = tmp_path / "outside.npz"
    with pytest.raises(wrf_extract.ParameterRangeError, match="outside"):
        wrf_extract.extract(
            "memory://wrfout_d01", 36.15, -99.5, outside,
            dataset=dataset,
        )
    assert not outside.exists()


def test_raw_wrf_netcdf4_file_uses_capable_xarray_engine(tmp_path):
    """A real NetCDF4/HDF5 file opens through the same path used by the GUI."""
    pytest.importorskip("netCDF4")
    source = tmp_path / "wrfout_d01_2024-05-20_00_00_00"
    dataset = _wrf_dataset()
    with known_netcdf4_numpy_shape_deprecation():
        dataset.to_netcdf(source, engine="netcdf4")

    domain = wrf_extract.inspect_file(source)
    assert domain["shape"] == (3, 3)

    out = tmp_path / "raw-wrf.npz"
    wrf_extract.extract(source, 35.1, -98.1, out)
    assert out.exists()
    assert out.with_suffix(".json").exists()


def test_wrf_runtime_rejects_scipy_only_netcdf_backend(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "netCDF4", None)
    monkeypatch.setitem(__import__("sys").modules, "h5netcdf", None)
    with pytest.raises(wrf_extract.RetrievalError, match="NetCDF3-only"):
        wrf_extract._preferred_netcdf_engine(SimpleNamespace())


def test_wrf_extraction_preserves_cancellation_after_opening_dataset(tmp_path):
    dataset = _wrf_dataset()
    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        # The first check happens before opening the input.  Cancel at the
        # next check, after the supplied WRF dataset is already available.
        return checks >= 2

    out = tmp_path / "cancelled-wrf.npz"
    with pytest.raises(wrf_extract.ExtractionCancelled):
        wrf_extract.extract(
            "memory://wrfout_d01", 35.1, -98.1, out,
            dataset=dataset, cancelled=cancelled,
        )

    assert not out.exists()
    assert not out.with_suffix(".json").exists()

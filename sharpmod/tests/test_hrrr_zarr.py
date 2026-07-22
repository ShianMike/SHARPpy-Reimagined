"""HRRR Zarr point-backend regressions."""

from __future__ import annotations

import numpy as np
from numcodecs import get_codec
import pytest

from sharpmod.hrrr_zarr import (
    _point_dataset_from_columns,
    decode_zarr_point,
    discover_pressure_plan,
    hrrr_grid_index,
)


def _array_metadata(shape=(1059, 1799), chunks=(150, 150)):
    return {
        "shape": list(shape),
        "chunks": list(chunks),
        "dtype": "<f4",
        "compressor": {
            "id": "blosc", "cname": "lz4", "clevel": 5,
            "shuffle": 1, "blocksize": 0,
        },
        "fill_value": -9999.0,
        "filters": None,
        "order": "C",
        "zarr_format": 2,
    }


def test_hrrr_projection_maps_known_conus_point():
    iy, ix, selected_lat, selected_lon = hrrr_grid_index(35.0, -97.0)

    assert (iy, ix) == (399, 914)
    assert selected_lat == pytest.approx(35.009, abs=0.03)
    assert selected_lon == pytest.approx(-97.013, abs=0.03)


def test_metadata_plan_keeps_all_levels_and_prunes_equivalent_fields():
    metadata = {}
    for level in ("1000mb", "975mb"):
        for field in (
            "HGT", "TMP", "RH", "SPFH", "UGRD", "VGRD",
            "VVEL", "ABSV",
        ):
            key = f"{level}/{field}/{level}/{field}/.zarray"
            metadata[key] = _array_metadata()

    plan = discover_pressure_plan(metadata)

    assert plan.levels == (1000.0, 975.0)
    assert plan.fields == (
        "HGT", "TMP", "UGRD", "VGRD", "RH", "VVEL", "ABSV"
    )
    assert len(plan.arrays) == len(plan.levels) * len(plan.fields)


def test_decode_zarr_point_handles_compressed_chunk():
    metadata = _array_metadata(shape=(2, 2), chunks=(2, 2))
    values = np.asarray([[1.0, 2.0], [3.0, 4.0]], dtype="<f4")
    payload = get_codec(metadata["compressor"]).encode(values.tobytes())

    selected = decode_zarr_point(payload, metadata, iy=1, ix=0)

    assert selected == 3.0


def test_point_columns_normalize_without_constructing_xarray():
    from datetime import datetime, timezone

    levels = (1000.0, 850.0)
    columns = {
        "HGT": np.asarray([100.0, 1500.0]),
        "TMP": np.asarray([293.15, 283.15]),
        "RH": np.asarray([50.0, 60.0]),
        "UGRD": np.asarray([3.0, -5.0]),
        "VGRD": np.asarray([4.0, 0.0]),
        "VVEL": np.asarray([-0.2, -0.1]),
        "ABSV": np.asarray([1.0e-4, 8.0e-5]),
    }

    dataset = _point_dataset_from_columns(
        levels, columns, 35.0, -97.0,
        datetime(2026, 7, 22, tzinfo=timezone.utc), 35.01, -97.01,
    )

    np.testing.assert_array_equal(dataset.decoded.pres, levels)
    np.testing.assert_allclose(dataset.decoded.tmpc, [20.0, 10.0])
    np.testing.assert_allclose(dataset.decoded.wspd, [9.71922245, 9.71922245])
    assert dataset.decoded.surface_relative_vorticity is not None
    assert dataset.requested_lat == pytest.approx(35.01)
    assert dataset.requested_lon == pytest.approx(-97.01)

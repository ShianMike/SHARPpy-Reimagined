"""Regression coverage for satellite frame discovery and rendering.

The tests verify scan-time/channel parsing, bounded nearest-frame selection,
deterministic spacecraft choice, fixed-grid geolocation, and disclosure of the selected
frame time."""

from __future__ import annotations

import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from sharpmod.satellite_context import (
    GOES_OVERLAY_KEY,
    SatelliteContextError,
    choose_satellite,
    fetch_satellite_overlay,
    parse_listing,
    render_goes_frame,
    select_candidate,
)


UTC = timezone.utc


def _listing(*keys):
    rows = "".join(
        f"<Contents><Key>{key}</Key><Size>1234</Size></Contents>" for key in keys
    )
    return f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">{rows}</ListBucketResult>'.encode()


def _goes_netcdf(tmp_path: Path) -> bytes:
    netcdf4 = pytest.importorskip("netCDF4")
    path = tmp_path / "frame.nc"
    with netcdf4.Dataset(path, "w") as dataset:
        dataset.createDimension("x", 81)
        dataset.createDimension("y", 71)
        x = dataset.createVariable("x", "f4", ("x",))
        y = dataset.createVariable("y", "f4", ("y",))
        x[:] = np.linspace(-0.11, 0.11, 81)
        y[:] = np.linspace(0.10, -0.10, 71)
        projection = dataset.createVariable("goes_imager_projection", "i1")
        projection.perspective_point_height = 35_786_023.0
        projection.longitude_of_projection_origin = -75.0
        projection.sweep_angle_axis = "x"
        projection.semi_major_axis = 6_378_137.0
        projection.semi_minor_axis = 6_356_752.31414
        cmi = dataset.createVariable("CMI", "f4", ("y", "x"), fill_value=-999.0)
        rows, columns = np.meshgrid(
            np.linspace(0.0, 1.0, 71),
            np.linspace(0.0, 1.0, 81),
            indexing="ij",
        )
        values = np.asarray(0.15 + 0.75 * (rows + columns) / 2.0, dtype=np.float32)
        # netCDF4 1.7.4 assigns ndarray.shape internally for multidimensional
        # writes. NumPy 2.5 deprecates that implementation detail; the fixture
        # itself remains valid and production only reads these files.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Setting the shape on a NumPy array has been deprecated.*",
                category=DeprecationWarning,
            )
            cmi[:] = values
            dqf = dataset.createVariable("DQF", "u1", ("y", "x"))
            dqf[:] = 0
    return path.read_bytes()


def test_listing_parses_actual_scan_time_and_channel():
    key = (
        "ABI-L2-CMIPC/2026/256/01/"
        "OR_ABI-L2-CMIPC-M6C13_G19_s20262560146120_"
        "e20262560148505_c20262560149001.nc"
    )
    ignored = key.replace("C13", "C02")
    candidates = parse_listing(
        _listing(key, ignored), satellite="G19", channel="infrared"
    )
    assert len(candidates) == 1
    assert candidates[0].scan_start == datetime(2026, 9, 13, 1, 46, 12, tzinfo=UTC)
    assert candidates[0].url.endswith(".nc")


def test_nearest_frame_is_bounded_by_explicit_tolerance():
    key = (
        "ABI-L2-CMIPC/2026/256/01/"
        "OR_ABI-L2-CMIPC-M6C13_G19_s20262560100120_"
        "e20262560105120_c20262560106000.nc"
    )
    candidate = parse_listing(
        _listing(key), satellite="G19", channel="infrared"
    )[0]
    with pytest.raises(SatelliteContextError, match="allowed mismatch"):
        select_candidate(
            (candidate,),
            datetime(2026, 9, 13, 1, 45, tzinfo=UTC),
            tolerance=timedelta(minutes=10),
        )


def test_spacecraft_preference_is_deterministic_but_not_a_coverage_claim():
    assert choose_satellite((-125, -115, 30, 40)) == "G18"
    assert choose_satellite((-105, -90, 30, 40)) == "G19"


@pytest.mark.parametrize("channel", ("visible", "infrared"))
def test_fixed_grid_frame_is_geolocated_to_a_real_png(tmp_path, channel):
    payload = _goes_netcdf(tmp_path)
    png, coverage, resolution = render_goes_frame(
        payload,
        bounds=(-100.0, -70.0, 25.0, 45.0),
        channel=channel,
        max_output_pixels=20_000,
    )
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert 0.5 < coverage <= 1.0
    assert resolution == (0.5 if channel == "visible" else 2.0)


def test_end_to_end_fetch_discloses_selected_timestamp(tmp_path):
    payload = _goes_netcdf(tmp_path)
    key = (
        "ABI-L2-CMIPC/2026/256/01/"
        "OR_ABI-L2-CMIPC-M6C13_G19_s20262560146120_"
        "e20262560148505_c20262560149001.nc"
    )
    listing = _listing(key)

    def opener(url, **_kwargs):
        return payload if url.endswith(".nc") else listing

    result = fetch_satellite_overlay(
        datetime(2026, 9, 13, 1, 45, tzinfo=UTC),
        bounds=(-100.0, -70.0, 25.0, 45.0),
        channel="infrared",
        satellite="G19",
        opener=opener,
    )
    assert result.raster.key == GOES_OVERLAY_KEY
    assert result.selected_time == datetime(2026, 9, 13, 1, 46, 12, tzinfo=UTC)
    assert "actual 2026-09-13 01:46:12Z" in result.raster.subtitle
    assert "NOAA" in result.raster.attribution

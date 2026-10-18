"""Independent NOAA-formula checks at western, central and eastern grid points."""

import math

import numpy as np
import pytest

from sharpmod.backends.grib import clear_grib_caches, load_eccodes
from sharpmod.backends.python_backend import PythonBackend
from sharpmod.backends.rust_backend import RustBackend
from sharpmod.models.wind_reference import rotate_winds, wind_rotation


def _expected(u, v, longitude, cone=math.sin(math.radians(38.5)), origin=-97.5):
    angle = math.radians(cone * ((longitude - origin + 180) % 360 - 180))
    return (
        math.cos(angle) * u + math.sin(angle) * v,
        -math.sin(angle) * u + math.cos(angle) * v,
    )


def _write_profile(path, longitude, relative, *, projection="lambert", parallels=(38.5, 38.5),
                   missing_v=False):
    codes = load_eccodes()
    with path.open("wb") as output:
        fields = [
            (name, "isobaricInhPa", level, value)
            for level, height in [(1000, 100), (850, 1500)]
            for name, value in [("gh", height), ("t", 290), ("r", 50), ("u", 3), ("v", 4)]
        ]
        fields += [
            ("sp", "surface", 0, 95000), ("orog", "surface", 0, 500),
            ("2t", "heightAboveGround", 2, 290), ("2d", "heightAboveGround", 2, 280),
            ("10u", "heightAboveGround", 10, 5), ("10v", "heightAboveGround", 10, 12),
        ]
        for name, level_type, level, value in fields:
            message = codes.codes_grib_new_from_samples("regular_ll_pl_grib2")
            try:
                settings = {
                    "gridType": projection, "Nx": 3, "Ny": 3,
                    "latitudeOfFirstGridPointInDegrees": 38.5,
                    "longitudeOfFirstGridPointInDegrees": longitude % 360,
                    "DxInMetres": 3000, "DyInMetres": 3000,
                    "resolutionAndComponentFlags": 48 | (relative * 8),
                    "typeOfLevel": level_type, "level": level, "shortName": name,
                }
                if projection == "lambert":
                    settings.update(LoVInDegrees=262.5,
                                    Latin1InDegrees=parallels[0], Latin2InDegrees=parallels[1])
                else:
                    settings.update(orientationOfTheGridInDegrees=262.5,
                                    projectionCentreFlag=0, LaDInDegrees=60)
                for key, setting in settings.items():
                    codes.codes_set(message, key, setting)
                if missing_v and name == "v" and level == 850:
                    codes.codes_set(message, "bitmapPresent", 1)
                    value = codes.codes_get(message, "missingValue")
                codes.codes_set_values(message, np.full(9, float(value)))
                codes.codes_write(message, output)
            finally:
                codes.codes_release(message)


@pytest.mark.parametrize("backend_name", ["python", "rust"])
@pytest.mark.parametrize("longitude", [-120.0, -97.5, -75.0])
@pytest.mark.parametrize("relative", [0, 1])
def test_projected_profile_and_surface_winds_use_true_north(
        tmp_path, backend_name, longitude, relative):
    path = tmp_path / "winds.grib2"
    _write_profile(path, longitude, relative)
    backend = PythonBackend() if backend_name == "python" else RustBackend(
        pytest.importorskip("sharpmod_rs"))
    clear_grib_caches()
    result = backend.decode_grib_point(path, 38.5, longitude)
    assert result.surface_merged
    np.testing.assert_allclose(result.pres, [950, 850])
    expected = [_expected(u, v, longitude) if relative else (u, v)
                for u, v in [(5, 12), (3, 4)]]
    np.testing.assert_allclose(result.u, [value[0] for value in expected], atol=1e-10)
    np.testing.assert_allclose(result.v, [value[1] for value in expected], atol=1e-10)
    np.testing.assert_allclose(result.wspd, np.array([13, 5]) * 1.94384449)
    direction = [(270 - math.degrees(math.atan2(v, u))) % 360 for u, v in expected]
    np.testing.assert_allclose(result.wdir, direction, atol=1e-10)
    # Batch decode, duplicate points, warm native inventories and longitude wrapping.
    batch = backend.decode_grib_points(path, [(38.5, longitude), (38.5, longitude + 360)])
    for actual in batch:
        np.testing.assert_allclose(actual.matrix, result.matrix, equal_nan=True)


@pytest.mark.parametrize("backend_name", ["python", "rust"])
@pytest.mark.parametrize("projection", ["lambert", "polar_stereographic"])
def test_secant_and_polar_projection_metadata_are_used(tmp_path, backend_name, projection):
    path = tmp_path / "projection.grib2"
    _write_profile(path, -120, 1, projection=projection, parallels=(30, 60))
    cone = 1.0 if projection == "polar_stereographic" else (
        math.log(math.cos(math.radians(30)) / math.cos(math.radians(60))) /
        math.log(math.tan(math.radians(75)) / math.tan(math.radians(60)))
    )
    backend = PythonBackend() if backend_name == "python" else RustBackend(
        pytest.importorskip("sharpmod_rs"))
    result = backend.decode_grib_point(path, 38.5, -120)
    east, north = _expected(3, 4, -120, cone=cone)
    assert result.u[-1] == pytest.approx(east)
    assert result.v[-1] == pytest.approx(north)


@pytest.mark.parametrize("backend_name", ["python", "rust"])
@pytest.mark.parametrize("relative", [0, 1])
@pytest.mark.parametrize("missing", [-9999.0, -1234.0])
def test_missing_wind_component_does_not_become_valid_after_rotation(
        tmp_path, backend_name, relative, missing):
    path = tmp_path / "missing.grib2"
    _write_profile(path, -120, relative, missing_v=True)
    backend = PythonBackend() if backend_name == "python" else RustBackend(
        pytest.importorskip("sharpmod_rs"))
    result = backend.decode_grib_point(path, 38.5, -120, missing=missing)
    assert result.v[-1] == result.wdir[-1] == result.wspd[-1] == missing
    assert result.u[-1] == (missing if relative else 3.0)
    east, north = _expected(5, 12, -120) if relative else (5, 12)
    assert result.u[0] == pytest.approx(east)
    assert result.v[0] == pytest.approx(north)


def test_unsupported_or_invalid_grid_reference_is_rejected():
    for attributes in [
        {"uvRelativeToGrid": 1, "gridType": "rotated_ll"},
        {"uvRelativeToGrid": None, "gridType": "lambert"},
        {"uvRelativeToGrid": 2, "gridType": "lambert"},
        {"uvRelativeToGrid": 1, "gridType": "lambert",
         "Latin1InDegrees": 0, "Latin2InDegrees": 0, "LoVInDegrees": -97.5},
    ]:
        with pytest.raises(ValueError):
            wind_rotation(attributes.get)
    u, v = rotate_winds(3, 4, -120, None)
    assert (u, v) == (3, 4)


@pytest.mark.parametrize("relative", [0, 1])
@pytest.mark.parametrize("v_field", ["complete", "missing", "absent"])
def test_cfgrib_fallback_keeps_wind_flags_for_upper_and_surface_rows(
        tmp_path, relative, v_field):
    from datetime import datetime, timezone
    from sharpmod.tools import model_extract
    path = tmp_path / "fallback.grib2"
    _write_profile(path, -120, relative, missing_v=v_field == "missing")
    source = model_extract._LocalGribDataset(path)
    try:
        dataset = source.fallback_point_dataset(
            38.5, -120, datetime(2007, 3, 23, 12, tzinfo=timezone.utc))
        if v_field == "absent":
            dataset = dataset.drop_vars("v")
        try:
            columns, _count, _lat, longitude, _valid, _selected = (
                model_extract._xarray_point_columns(
                    dataset, 38.5, -120,
                    datetime(2007, 3, 23, 12, tzinfo=timezone.utc), 0, "test")
            )
        finally:
            dataset.close()
    finally:
        source.close()
    expected = [_expected(u, v, longitude) if relative else (u, v)
                for u, v in [(5, 12), (3, 4)]]
    if v_field != "complete":
        expected[-1] = (-9999.0 if relative else 3.0, -9999.0)
    assert columns["_surface_merged"]
    np.testing.assert_allclose(columns["u"], [x[0] for x in expected], atol=1e-10)
    np.testing.assert_allclose(columns["v"], [x[1] for x in expected], atol=1e-10)


@pytest.mark.parametrize("longitude", [-120, -97.5, -75])
@pytest.mark.parametrize("surface_u", [5.0, -9999.0, np.nan, None])
def test_zarr_native_winds_are_rotated_once_without_mutating_inputs(longitude, surface_u):
    from datetime import datetime, timezone
    from sharpmod.providers.hrrr_zarr import _point_dataset_from_columns
    columns = {
        "HGT": np.array([1500.0, 3000.0]), "TMP": np.array([290.0, 280.0]),
        "RH": np.array([50.0, 50.0]), "UGRD": np.array([3.0, -9999.0]),
        "VGRD": np.array([4.0, 4.0]),
    }
    surface = {"PRES": 95000, "HGT": 500, "TMP": 290, "DPT": 280, "UGRD": surface_u, "VGRD": 12}
    result = _point_dataset_from_columns(
        [850, 700], columns, 38.5, longitude,
        datetime(2026, 10, 17, tzinfo=timezone.utc), 38.5, longitude, surface).decoded
    expected = [_expected(3, 4, longitude)]
    assert result.surface_merged == (surface_u == 5.0)
    if result.surface_merged:
        expected.insert(0, _expected(5, 12, longitude))
    np.testing.assert_allclose(result.u[:-1], [x[0] for x in expected])
    np.testing.assert_allclose(result.v[:-1], [x[1] for x in expected])
    assert result.u[-1] == result.v[-1] == result.wdir[-1] == -9999
    np.testing.assert_array_equal(columns["UGRD"], [3, -9999])
    assert surface["UGRD"] is surface_u


@pytest.mark.parametrize("invalid", [-9999.0, np.nan, np.inf])
def test_rotation_does_not_export_half_of_a_grid_relative_vector(invalid):
    east, north = rotate_winds([3.0, invalid], [invalid, 4.0], -120, (0.6225, -97.5))
    np.testing.assert_array_equal(east, [-9999.0, -9999.0])
    np.testing.assert_array_equal(north, [-9999.0, -9999.0])

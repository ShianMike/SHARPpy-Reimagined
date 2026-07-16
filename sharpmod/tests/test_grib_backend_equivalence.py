"""Direct ecCodes point-decoder regressions for the Python backend."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from sharpmod.backends.grib import (
    DecodedPoint,
    GRIB_COLUMN_NAMES,
    clear_grib_caches,
    decode_grib_point,
    grib_cache_info,
    load_eccodes,
)
from sharpmod.backends.python_backend import PythonBackend
from sharpmod.backends.rust_backend import RustBackend


_LEVEL_VALUES = {
    1000.0: {
        "gh": 100.0,
        "t": 293.15,
        "r": 50.0,
        "q": 0.005,
        "u": 3.0,
        "v": 4.0,
        "w": -0.2,
        "absv": 1.0e-4,
    },
    850.0: {
        "gh": 1500.0,
        "t": 283.15,
        "r": 60.0,
        "q": 0.004,
        "u": -5.0,
        "v": 0.0,
        "w": -0.1,
        "absv": 8.0e-5,
    },
}


def _message_handle(eccodes, short_name, level, point_value):
    message = eccodes.codes_grib_new_from_samples("regular_ll_pl_grib2")
    settings = {
        "Ni": 3,
        "Nj": 2,
        "latitudeOfFirstGridPointInDegrees": 10.0,
        "longitudeOfFirstGridPointInDegrees": 100.0,
        "latitudeOfLastGridPointInDegrees": 11.0,
        "longitudeOfLastGridPointInDegrees": 102.0,
        "iDirectionIncrementInDegrees": 1.0,
        "jDirectionIncrementInDegrees": 1.0,
        "jScansPositively": 1,
        "level": int(level),
        "shortName": short_name,
    }
    for key, value in settings.items():
        eccodes.codes_set(message, key, value)
    # Index four is 11 N, 101 E. Keep every surrounding value distinct so
    # the nearest-point assertion catches a wrong flat index.
    values = float(point_value) + np.arange(-4.0, 2.0)
    eccodes.codes_set_values(message, values)
    return message


def _write_message(eccodes, output, short_name, level, point_value):
    message = _message_handle(eccodes, short_name, level, point_value)
    try:
        eccodes.codes_write(message, output)
    finally:
        eccodes.codes_release(message)


def _write_multifield_wind_message(eccodes, output, level, u_value, v_value):
    u_message = _message_handle(eccodes, "u", level, u_value)
    v_message = _message_handle(eccodes, "v", level, v_value)
    multi_message = eccodes.codes_grib_multi_new()
    try:
        eccodes.codes_grib_multi_append(u_message, 0, multi_message)
        eccodes.codes_grib_multi_append(v_message, 4, multi_message)
        eccodes.codes_grib_multi_write(multi_message, output)
    finally:
        eccodes.codes_grib_multi_release(multi_message)
        eccodes.codes_release(v_message)
        eccodes.codes_release(u_message)


@pytest.fixture
def compact_grib(tmp_path):
    try:
        eccodes = load_eccodes()
    except RuntimeError as exc:
        pytest.skip(str(exc))

    path = tmp_path / "compact-pressure-levels.grib2"
    with path.open("wb") as output:
        # Deliberately write levels top-down; the decoder must produce the
        # SHARPpy bottom-up (descending-pressure) order.
        for level in (850.0, 1000.0):
            for short_name, value in _LEVEL_VALUES[level].items():
                _write_message(eccodes, output, short_name, level, value)
        # This pressure-level field is not used by a point sounding and must
        # not trigger a value read.
        _write_message(eccodes, output, "clwmr", 1000.0, 999.0)
    return path, eccodes


@pytest.fixture
def multifield_grib(tmp_path):
    try:
        eccodes = load_eccodes()
    except RuntimeError as exc:
        pytest.skip(str(exc))

    path = tmp_path / "multifield-pressure-levels.grib2"
    with path.open("wb") as output:
        for level in (850.0, 1000.0):
            values = _LEVEL_VALUES[level]
            for short_name in ("gh", "t", "r", "w", "absv"):
                _write_message(
                    eccodes, output, short_name, level, values[short_name]
                )
            _write_multifield_wind_message(
                eccodes, output, level, values["u"], values["v"]
            )
    # A decoder must enable multi-field support itself rather than inheriting
    # process-global ecCodes state from another caller.
    eccodes.codes_grib_multi_support_off()
    return path, eccodes


@pytest.fixture(autouse=True)
def isolated_grib_caches():
    clear_grib_caches()
    yield
    clear_grib_caches()


def test_direct_decoder_returns_compact_named_numpy_columns(compact_grib):
    path, _eccodes = compact_grib

    result = PythonBackend().decode_grib_point(path, 10.9, 101.1)

    assert GRIB_COLUMN_NAMES == (
        "pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "omeg", "u", "v",
    )
    assert result.matrix.shape == (len(GRIB_COLUMN_NAMES), 2)
    assert result.matrix.dtype == np.float64
    assert result.matrix.flags.c_contiguous
    assert not result.matrix.flags.writeable
    np.testing.assert_array_equal(result.pres, [1000.0, 850.0])
    np.testing.assert_allclose(result.hght, [100.0, 1500.0])
    np.testing.assert_allclose(result.tmpc, [20.0, 10.0], atol=1e-10)
    np.testing.assert_allclose(result.u, [3.0, -5.0])
    np.testing.assert_allclose(result.v, [4.0, 0.0])
    np.testing.assert_allclose(result.omeg, [-0.2, -0.1], atol=1e-6)
    assert result.selected_lat == pytest.approx(11.0)
    assert result.selected_lon == pytest.approx(101.0)
    assert np.shares_memory(result.pres, result.matrix)


def test_python_decoder_reads_both_fields_from_multifield_messages(
        multifield_grib):
    path, _eccodes = multifield_grib

    result = PythonBackend().decode_grib_point(path, 10.9, 101.1)

    np.testing.assert_array_equal(result.pres, [1000.0, 850.0])
    np.testing.assert_allclose(result.u, [3.0, -5.0])
    np.testing.assert_allclose(result.v, [4.0, 0.0])
    np.testing.assert_allclose(result.wdir, [216.86989765, 90.0])
    np.testing.assert_allclose(
        result.wspd, [9.71922245, 9.71922245], rtol=1e-8
    )


def test_rust_decoder_reads_both_fields_from_multifield_messages(
        multifield_grib):
    native = pytest.importorskip("sharpmod_rs")
    path, _eccodes = multifield_grib

    expected = PythonBackend().decode_grib_point(path, 10.9, 101.1)
    actual = RustBackend(native).decode_grib_point(path, 10.9, 101.1)

    np.testing.assert_allclose(actual.matrix, expected.matrix, rtol=1e-12)
    np.testing.assert_allclose(actual.v, [4.0, 0.0])


def test_direct_decoder_rejects_missing_required_core_column(tmp_path):
    try:
        eccodes = load_eccodes()
    except RuntimeError as exc:
        pytest.skip(str(exc))

    path = tmp_path / "missing-v-wind.grib2"
    with path.open("wb") as output:
        for short_name, value in (
            ("gh", 100.0),
            ("t", 293.15),
            ("r", 50.0),
            ("u", 3.0),
        ):
            _write_message(eccodes, output, short_name, 1000.0, value)

    with pytest.raises(
        RuntimeError, match=r"missing required pressure-level fields: v wind"
    ):
        decode_grib_point(path, 10.9, 101.1)


def test_decoded_point_keeps_a_contiguous_native_owned_view_zero_copy():
    owner = np.arange(27.0, dtype=np.float64).reshape(9, 3)
    native_view = owner.view()
    assert not native_view.flags.owndata

    result = DecodedPoint(native_view, 35.0, -97.0)

    assert np.shares_memory(result.matrix, owner)
    assert not result.matrix.flags.writeable


def test_q_and_geopotential_fallbacks_preserve_missing_fields(tmp_path):
    try:
        eccodes = load_eccodes()
    except RuntimeError as exc:
        pytest.skip(str(exc))
    path = tmp_path / "fallback-fields.grib2"
    with path.open("wb") as output:
        for level, height, temperature, humidity in (
            (850.0, 1500.0, 283.15, 0.004),
            (1000.0, 100.0, 293.15, 0.005),
        ):
            _write_message(eccodes, output, "z", level, height * 9.80665)
            _write_message(eccodes, output, "t", level, temperature)
            _write_message(eccodes, output, "q", level, humidity)
            _write_message(eccodes, output, "u", level, 3.0)
            if level == 1000.0:
                _write_message(eccodes, output, "v", level, 4.0)

    result = decode_grib_point(path, 10.9, 101.1)

    np.testing.assert_allclose(result.hght, [100.0, 1500.0], atol=1e-4)
    assert np.isfinite(result.dwpc).all()
    np.testing.assert_array_equal(result.omeg, [-9999.0, -9999.0])
    assert result.wdir[1] == -9999.0
    assert result.wspd[1] == -9999.0
    assert result.u[1] != -9999.0
    assert result.v[1] == -9999.0
    assert result.surface_relative_vorticity is None


def test_decoder_reads_one_element_per_selected_message_and_caches_points(
        compact_grib, monkeypatch):
    path, eccodes = compact_grib
    calls = {"nearest": 0, "element": 0}
    real_nearest = eccodes.codes_grib_find_nearest
    real_element = eccodes.codes_get_double_element

    def counted_nearest(*args, **kwargs):
        calls["nearest"] += 1
        return real_nearest(*args, **kwargs)

    def counted_element(*args, **kwargs):
        calls["element"] += 1
        return real_element(*args, **kwargs)

    monkeypatch.setattr(eccodes, "codes_grib_find_nearest", counted_nearest)
    monkeypatch.setattr(eccodes, "codes_get_double_element", counted_element)

    first = decode_grib_point(path, 10.9, 101.1)
    same_request = decode_grib_point(path, 10.9, 101.1)
    same_grid_cell = decode_grib_point(path, 10.95, 101.05)

    # Six profile fields plus one vorticity field at each of two levels. The
    # lower-priority q messages and unrelated cloud-water message are skipped.
    assert calls == {"nearest": 2, "element": 14}
    assert same_request is first
    assert same_grid_cell is first
    info = grib_cache_info()
    assert info["inventory"] == {
        "size": 1, "max_size": 8, "hits": 2, "misses": 1,
    }
    assert info["nearest"]["hits"] == 1
    assert info["nearest"]["misses"] == 2
    assert info["points"]["hits"] == 2
    assert info["points"]["misses"] == 1


def test_file_identity_invalidates_all_cached_decode_state(compact_grib):
    path, _eccodes = compact_grib
    first = decode_grib_point(path, 10.9, 101.1)
    stat = path.stat()

    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    second = decode_grib_point(path, 10.9, 101.1)

    assert second is not first
    np.testing.assert_array_equal(second.matrix, first.matrix)
    info = grib_cache_info()
    assert info["inventory"]["misses"] == 2
    assert info["nearest"]["misses"] == 2
    assert info["points"]["misses"] == 2


def test_direct_decoder_matches_existing_xarray_column_science(compact_grib):
    path, _eccodes = compact_grib
    cfgrib = pytest.importorskip("cfgrib")
    from sharpmod.tools import era5_extract

    direct = decode_grib_point(path, 10.9, 101.1)
    source_datasets = list(cfgrib.open_datasets(
        path, backend_kwargs={"indexpath": ""}))
    dataset = era5_extract._merge_datasets(source_datasets)
    try:
        _, lats = era5_extract._coord_values(dataset, era5_extract._LAT_COORDS)
        _, lons = era5_extract._coord_values(dataset, era5_extract._LON_COORDS)
        index, selected_lat, selected_lon = \
            era5_extract.select_nearest_grid_point(lats, lons, 10.9, 101.1)
        columns, _count = era5_extract._build_columns(
            dataset, index, latitude=selected_lat)
    finally:
        dataset.close()
        for source in source_datasets:
            source.close()

    expected = np.vstack([
        columns["pres"], columns["hght"], columns["tmpc"], columns["dwpc"],
        columns["wdir"], columns["wspd"], columns["omeg"], columns["u"],
        columns["v"],
    ])
    # cfgrib exposes float32 data arrays while the direct ecCodes scalar API
    # preserves the decoded double. Their difference is bounded by float32
    # representation, not a change in GRIB packing or field semantics.
    np.testing.assert_allclose(
        direct.matrix, expected, rtol=1e-6, atol=1e-5)
    assert direct.selected_lat == pytest.approx(selected_lat)
    assert direct.selected_lon == pytest.approx(selected_lon)
    assert direct.surface_relative_vorticity == pytest.approx(
        columns["surface_relative_vorticity"], rel=1e-6, abs=1e-10)


def test_rust_direct_decoder_matches_python_matrix(compact_grib):
    native = pytest.importorskip("sharpmod_rs")
    path, _eccodes = compact_grib

    expected = PythonBackend().decode_grib_point(path, 10.9, 101.1)
    actual = RustBackend(native).decode_grib_point(path, 10.9, 101.1)

    np.testing.assert_allclose(
        actual.matrix, expected.matrix, rtol=1e-12, atol=1e-12
    )
    assert actual.matrix.shape == expected.matrix.shape == (9, 2)
    assert actual.matrix.flags.c_contiguous
    assert not actual.matrix.flags.writeable
    assert actual.selected_lat == pytest.approx(expected.selected_lat)
    assert actual.selected_lon == pytest.approx(expected.selected_lon)
    assert actual.surface_relative_vorticity == pytest.approx(
        expected.surface_relative_vorticity, rel=1e-12, abs=1e-12
    )


def test_xarray_fallback_reuses_cfgrib_persistent_index(compact_grib):
    from sharpmod.tools import model_extract

    path, _eccodes = compact_grib
    source = model_extract._LocalGribDataset(path)
    first = second = None
    try:
        first = source.fallback_point_dataset(
            10.9, 101.1, datetime(2026, 7, 16, tzinfo=timezone.utc)
        )
        second = source.fallback_point_dataset(
            10.9, 101.1, datetime(2026, 7, 16, tzinfo=timezone.utc)
        )
        assert first.sizes == second.sizes
        assert all(size <= 3 for name, size in first.sizes.items()
                   if name not in {"isobaricInhPa", "isobaricInPa"})
        assert list(path.parent.glob(path.name + ".*.idx"))
    finally:
        if first is not None:
            first.close()
        if second is not None:
            second.close()
        source.close()


def test_rust_adapter_caches_one_native_matrix_by_file_identity(
        tmp_path, monkeypatch):
    from sharpmod.backends import rust_backend

    path = tmp_path / "native-cache.grib2"
    path.write_bytes(b"GRIB-native-cache-7777")
    calls = []

    class FakeNative:
        @staticmethod
        def decode_grib_point(*args):
            calls.append(args)
            matrix = np.arange(18.0, dtype=np.float64).reshape(9, 2)
            return matrix, 11.0, 101.0, None

    monkeypatch.setattr(
        rust_backend, "_eccodes_library_path", lambda: "eccodes-library"
    )
    backend = RustBackend(FakeNative())

    first = backend.decode_grib_point(path, 10.9, 101.1)
    wrapped_request = backend.decode_grib_point(path, 10.9, 461.1)

    assert wrapped_request is first
    assert len(calls) == 1
    assert backend.grib_cache_info() == {
        "size": 1, "max_size": 128, "hits": 1, "misses": 1,
    }

    backend.clear_grib_cache(points=False, reset_stats=False)
    assert backend.grib_cache_info() == {
        "size": 1, "max_size": 128, "hits": 1, "misses": 1,
    }

    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    changed = backend.decode_grib_point(path, 10.9, 101.1)
    assert changed is not first
    assert len(calls) == 2
    assert backend.grib_cache_info()["misses"] == 2


def test_retrieval_returns_local_grib_without_constructing_xarray(
        compact_grib, monkeypatch):
    from sharpmod.tools import model_extract

    path, _eccodes = compact_grib

    class FakeHerbie:
        grib = "https://example.invalid/gfs.grib2"

        def __init__(self, *_args, **_kwargs):
            pass

        def xarray(self, *_args, **_kwargs):
            raise AssertionError("direct local decoding must bypass H.xarray")

    fields = ("HGT", "TMP", "RH", "UGRD", "VGRD", "VVEL", "ABSV")
    monkeypatch.setenv("SHARPMOD_GRIB_DECODER", "auto")
    monkeypatch.setattr(model_extract, "require_runtime_dependencies", lambda: None)
    monkeypatch.setattr(
        model_extract,
        "_planned_model_search",
        lambda *_args: ("pressure-search", fields, []),
    )
    monkeypatch.setattr(model_extract, "select_herbie_provider", lambda _h: None)
    monkeypatch.setattr(
        model_extract,
        "download_herbie_subset",
        lambda *_args, **_kwargs: (path, path.stat().st_size),
    )
    monkeypatch.setitem(
        sys.modules, "herbie", SimpleNamespace(Herbie=FakeHerbie)
    )

    dataset, source = model_extract._retrieve_dataset(
        model_extract.get_config("gfs"),
        datetime(2026, 7, 16, 0, tzinfo=timezone.utc),
        0,
    )

    assert isinstance(dataset, model_extract._LocalGribDataset)
    assert dataset.path == path.resolve()
    assert source._sharpmod_fields == fields


def test_model_extract_direct_source_preserves_npz_and_metadata_contract(
        compact_grib, tmp_path, monkeypatch):
    from sharpmod import backends
    from sharpmod.tools import model_extract

    path, _eccodes = compact_grib
    output = tmp_path / "direct-point.npz"
    source = model_extract._LocalGribDataset(path)
    progress = []
    monkeypatch.setenv("SHARPMOD_BACKEND", "python")
    backends.reset_backend_cache()
    try:
        result = model_extract.extract(
            "gfs",
            10.9,
            101.1,
            run_time=datetime(2026, 7, 16, 0, tzinfo=timezone.utc),
            fxx=6,
            out_path=output,
            dataset=source,
            source_grib="https://example.invalid/gfs.grib2",
            source_fields=("HGT", "TMP", "RH", "UGRD", "VGRD", "ABSV"),
            source_transport="optimized-ranges",
            progress_callback=lambda stage, total: progress.append(
                (stage, total)
            ),
        )
    finally:
        backends.reset_backend_cache()
        source.close()

    direct = PythonBackend().decode_grib_point(path, 10.9, 101.1)
    assert Path(result) == output
    assert [stage for stage, _total in progress] == [
        "decoding", "extracting", "writing", "complete",
    ]
    with np.load(output, allow_pickle=False) as payload:
        for output_name, direct_name in (
            ("pres", "pres"),
            ("hght", "hght"),
            ("tmpc", "tmpc"),
            ("dwpc", "dwpc"),
            ("wdir", "wdir"),
            ("wspd", "wspd"),
            ("omeg", "omeg"),
            ("uwnd", "u"),
            ("vwnd", "v"),
        ):
            np.testing.assert_allclose(
                payload[output_name], getattr(direct, direct_name)
            )
        assert float(payload["lat"]) == pytest.approx(direct.selected_lat)
        assert float(payload["lon"]) == pytest.approx(direct.selected_lon)
        assert str(payload["valid"]) == "2026-07-16 06:00"

    import json
    metadata = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
    assert metadata["selected_lat"] == pytest.approx(direct.selected_lat)
    assert metadata["selected_lon"] == pytest.approx(direct.selected_lon)
    assert metadata["source_grib"] == "https://example.invalid/gfs.grib2"
    assert metadata["transport"] == "optimized-ranges"

    from sharpmod.io import decoder as decoder_mod
    profiles, loaded_location = decoder_mod.load_npz(str(output))
    assert loaded_location.startswith("GFS ")
    assert profiles.getMeta("model") == "GFS"
    assert profiles.getMeta("surface_relative_vorticity") == pytest.approx(
        direct.surface_relative_vorticity
    )

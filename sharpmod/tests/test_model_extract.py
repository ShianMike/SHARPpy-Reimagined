"""Tests for the generic forecast-model point extractor."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import builtins
import os
import re
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from sharpmod.io import decoder as decoder_mod
from sharpmod.tools import model_extract
from sharpmod.tests.era5_synth import make_era5_dataset


def _dataset():
    ds = make_era5_dataset(
        lats=[34.0, 35.0],
        lons=[260.0, 261.0],
        levels=[1000.0, 850.0, 700.0],
        times=[datetime(2026, 7, 8, 0, tzinfo=timezone.utc)],
        seed=20,
    )
    vo = np.full(ds["t"].shape, 8.0e-5, dtype=float)
    return ds.assign(vo=(ds["t"].dims, vo))


def test_model_aliases_resolve_to_supported_configs():
    assert model_extract.get_config("ecmwf").key == "ecmwf-ifs"
    assert model_extract.get_config("ifs").key == "ecmwf-ifs"
    assert model_extract.get_config("aifs").key == "ecmwf-aifs"
    assert model_extract.get_config("ecmwf-aifs").herbie_model == "aifs"
    assert model_extract.get_config("nam3").key == "nam-3km-conus"
    assert model_extract.get_config("rrfs").key == "rrfs-a"


def test_every_model_search_accepts_non_mandatory_pressure_levels():
    """Every supported model requests all published pressure levels."""
    for cfg in model_extract.available_models():
        sample = ":t:975:pl:" if cfg.key.startswith("ecmwf-") \
            else ":TMP:975 mb:"
        assert re.search(cfg.search, sample), cfg.key


def test_every_selectable_forecast_model_exists_in_herbie_registry():
    """No picker model may fail with a missing ``herbie.models`` attribute."""
    herbie_models = pytest.importorskip("herbie.models")
    missing = sorted({
        cfg.herbie_model
        for cfg in model_extract.available_models()
        if not hasattr(herbie_models, cfg.herbie_model)
    })

    assert missing == []


def test_forecast_hours_are_model_specific():
    hrrr_off_hour = model_extract.forecast_hours("hrrr", cycle_hour=5)
    assert max(hrrr_off_hour) == 18

    gfs_hours = model_extract.forecast_hours("gfs")
    assert 120 in gfs_hours
    assert 123 in gfs_hours
    assert 121 not in gfs_hours
    assert max(gfs_hours) == 384

    cfs_hours = model_extract.forecast_hours("cfs")
    assert cfs_hours[:3] == (0, 6, 12)
    assert max(cfs_hours) == 384


def test_domain_helpers_reject_out_of_region_points():
    assert model_extract.point_in_domain("hrrr", 35.0, -97.0)
    assert not model_extract.point_in_domain("hrrr", 52.0, 10.0)
    assert model_extract.point_in_domain("gfs", 52.0, 10.0)

    with pytest.raises(model_extract.ParameterRangeError, match="outside"):
        model_extract.extract(
            "hrrr", 52.0, 10.0,
            run_time=datetime(2026, 7, 8, 0, tzinfo=timezone.utc),
            out_path="unused.npz",
            dataset=_dataset(),
        )


def test_model_extract_writes_loadable_npz(tmp_path, monkeypatch):
    """A supported model writes the shared point-sounding format."""
    ds = _dataset()

    def _fake_retrieve(
            config, run_dt, fxx, member=None, download_dir=None, **_kwargs):
        return ds, SimpleNamespace(grib="memory://gfs")

    monkeypatch.setattr(model_extract, "_retrieve_dataset", _fake_retrieve)

    out_path = tmp_path / "gfs_point.npz"
    result = model_extract.extract(
        "gfs", 35.0, -99.0,
        run_time=datetime(2026, 7, 8, 2, tzinfo=timezone.utc),
        fxx=6,
        out_path=str(out_path),
        loc="test-point",
    )

    assert result == str(out_path)
    with np.load(out_path, allow_pickle=True) as npz:
        assert str(npz["model"]) == "GFS"
        assert str(npz["loc"]) == "test-point"
        assert int(npz["fxx"]) == 6
        value = float(np.asarray(
            npz["surface_relative_vorticity"]).reshape(-1)[0])
        assert value == pytest.approx(8.0e-5)

    prof_collection, loc = decoder_mod.load_npz(str(out_path))
    assert loc == "test-point"
    assert prof_collection.getMeta("model") == "GFS"
    assert prof_collection.getMeta("surface_relative_vorticity") == pytest.approx(
        8.0e-5)


def test_extract_forwards_isolated_download_directory(tmp_path, monkeypatch):
    seen = {}

    def _fake_retrieve(
            config, run_dt, fxx, member=None, download_dir=None, **_kwargs):
        seen["download_dir"] = download_dir
        return _dataset(), SimpleNamespace(grib="memory://gfs")

    monkeypatch.setattr(model_extract, "_retrieve_dataset", _fake_retrieve)
    download_dir = tmp_path / "downloads"
    model_extract.extract(
        "gfs", 35.0, -99.0,
        run_time=datetime(2026, 7, 8, 0, tzinfo=timezone.utc),
        out_path=str(tmp_path / "point.npz"),
        download_dir=str(download_dir),
    )

    assert seen["download_dir"] == str(download_dir)


def test_retrieve_dataset_suppresses_herbie_download_output(tmp_path, monkeypatch):
    """Herbie must not print Unicode status glyphs in Windows worker consoles."""
    seen = {}
    dataset = _dataset()

    class FakeHerbie:
        grib = "memory://hrrr"

        def __init__(self, *args, **kwargs):
            seen["constructor"] = kwargs

        def download(self, search, **kwargs):
            seen["download"] = kwargs
            print("👨🏻‍🏭 Created directory")
            return tmp_path / "subset.grib2"

        def xarray(self, search, **kwargs):
            seen["search"] = search
            seen["xarray"] = kwargs
            print("👨🏻‍🏭 Created directory")
            return dataset

    class Cp1252Stream:
        def write(self, text):
            text.encode("cp1252")
            return len(text)

        def flush(self):
            pass

    monkeypatch.setitem(sys.modules, "herbie", SimpleNamespace(Herbie=FakeHerbie))
    monkeypatch.setattr(sys, "stdout", Cp1252Stream())
    config = model_extract.get_config("hrrr")
    returned, _herbie = model_extract._retrieve_dataset(
        config,
        datetime(2026, 7, 11, 12, tzinfo=timezone.utc),
        0,
        download_dir=tmp_path,
    )

    assert returned is dataset
    assert seen["download"]["verbose"] is False
    assert seen["download"]["save_dir"] == str(tmp_path)
    assert seen["xarray"]["verbose"] is False
    assert seen["xarray"]["remove_grib"] is False
    assert seen["xarray"]["save_dir"] == str(tmp_path)


def test_retrieve_dataset_reports_real_download_and_decode_stages(
        tmp_path, monkeypatch):
    """Progress totals come from the same GRIB byte groups Herbie downloads."""
    seen = {}
    progress = []
    dataset = _dataset()

    class FakeHerbie:
        grib = "memory://hrrr"

        def __init__(self, *args, **kwargs):
            pass

        def inventory(self, search):
            seen["inventory_search"] = search
            return pd.DataFrame({
                "grib_message": [1, 2, 5],
                "start_byte": [0, 100, 400],
                "end_byte": [99, 199, 499],
            })

        def download(self, search, **kwargs):
            seen["download"] = (search, kwargs)
            return tmp_path / "subset.grib2"

        def xarray(self, search, **kwargs):
            seen["xarray"] = (search, kwargs)
            return dataset

    monkeypatch.setattr(model_extract, "require_runtime_dependencies", lambda: None)
    monkeypatch.setitem(sys.modules, "herbie", SimpleNamespace(Herbie=FakeHerbie))
    config = model_extract.get_config("hrrr")

    returned, _herbie = model_extract._retrieve_dataset(
        config,
        datetime(2026, 7, 14, 0, tzinfo=timezone.utc),
        0,
        download_dir=tmp_path,
        progress_callback=lambda stage, total: progress.append((stage, total)),
    )

    assert returned is dataset
    assert progress == [
        ("locating", 0),
        ("downloading", 300),
        ("decoding", 300),
    ]
    assert seen["download"][1]["save_dir"] == str(tmp_path)
    assert seen["download"][1]["verbose"] is False
    assert seen["xarray"][1]["remove_grib"] is False
    assert seen["xarray"][1]["save_dir"] == str(tmp_path)


def test_retrieve_dataset_uses_pruned_search_and_optimized_transport(
        tmp_path, monkeypatch):
    seen = {}
    dataset = _dataset()

    class FakeHerbie:
        grib = "https://example.invalid/hrrr.grib2"

        def __init__(self, *args, **kwargs):
            pass

        def inventory(self, search):
            seen.setdefault("inventory", []).append(search)
            variables = [
                "HGT", "TMP", "RH", "SPFH", "UGRD", "VGRD",
                "VVEL", "DZDT", "ABSV",
            ]
            return pd.DataFrame({
                "variable": variables,
                "grib_message": range(1, len(variables) + 1),
                "start_byte": [value * 100 for value in range(len(variables))],
                "end_byte": [value * 100 + 99 for value in range(len(variables))],
            })

        def download(self, *_args, **_kwargs):
            raise AssertionError("optimized transport should avoid Herbie.download")

        def xarray(self, search, **kwargs):
            seen["xarray"] = (search, kwargs)
            return dataset

    def fake_optimized(herbie, search, **kwargs):
        seen["optimized"] = (herbie, search, kwargs)
        return tmp_path / "subset.grib2", 700

    monkeypatch.setattr(model_extract, "require_runtime_dependencies", lambda: None)
    monkeypatch.setattr(
        model_extract, "download_herbie_subset", fake_optimized, raising=False
    )
    monkeypatch.setitem(sys.modules, "herbie", SimpleNamespace(Herbie=FakeHerbie))

    returned, herbie = model_extract._retrieve_dataset(
        model_extract.get_config("hrrr"),
        datetime(2026, 7, 14, 0, tzinfo=timezone.utc),
        0,
        download_dir=tmp_path,
    )

    assert returned is dataset
    search = seen["optimized"][1]
    assert "SPFH" not in search
    assert "DZDT" not in search
    assert seen["xarray"][0] == search
    assert herbie._sharpmod_fields == (
        "HGT", "TMP", "UGRD", "VGRD", "RH", "VVEL", "ABSV"
    )


def test_retrieve_dataset_prefers_nomads_point_subset_when_coordinates_exist(
        tmp_path, monkeypatch):
    seen = {}
    dataset = _dataset()
    monkeypatch.setenv("SHARPMOD_HRRR_BACKEND", "grib")

    class FakeHerbie:
        grib = "https://example.invalid/hrrr.grib2"
        SOURCES = {
            "nomads": (
                "https://nomads.ncep.noaa.gov/pub/data/nccf/com/hrrr/prod/"
                "hrrr.20260714/conus/hrrr.t00z.wrfprsf00.grib2"
            )
        }

        def __init__(self, *args, **kwargs):
            pass

        def inventory(self, search):
            variables = ["HGT", "TMP", "RH", "UGRD", "VGRD", "VVEL"]
            return pd.DataFrame({
                "variable": variables,
                "grib_message": range(1, 7),
                "start_byte": range(0, 600, 100),
                "end_byte": range(99, 699, 100),
            })

        def xarray(self, search, **kwargs):
            seen["xarray"] = search
            return dataset

    def fake_nomads(herbie, config, search, fields, lat, lon, **kwargs):
        seen["nomads"] = (config.key, search, fields, lat, lon, kwargs)
        return tmp_path / "point.grib2", 2048, "https://nomads/query"

    monkeypatch.setattr(model_extract, "require_runtime_dependencies", lambda: None)
    monkeypatch.setattr(
        model_extract,
        "_subset_download_bytes",
        lambda *_args: model_extract._NOMADS_MIN_RANGE_BYTES + 1,
    )
    monkeypatch.setattr(
        model_extract, "download_nomads_subset", fake_nomads, raising=False
    )
    monkeypatch.setattr(
        model_extract, "download_herbie_subset",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("range transport must not run after NOMADS succeeds")
        ),
    )
    monkeypatch.setitem(sys.modules, "herbie", SimpleNamespace(Herbie=FakeHerbie))

    returned, herbie = model_extract._retrieve_dataset(
        model_extract.get_config("hrrr"),
        datetime(2026, 7, 14, 0, tzinfo=timezone.utc),
        0,
        download_dir=tmp_path,
        lat=35.0,
        lon=-97.0,
    )

    assert returned is dataset
    assert seen["nomads"][3:5] == (35.0, -97.0)
    assert herbie._sharpmod_transport == "nomads-subregion"
    assert herbie._sharpmod_source_url == "https://nomads/query"


def test_small_indexed_subset_prefers_ranges_over_nomads(tmp_path, monkeypatch):
    dataset = _dataset()
    seen = {"ranges": 0, "nomads": 0}

    class FakeHerbie:
        grib = "https://example.invalid/rap.grib2"
        SOURCES = {}

        def __init__(self, *args, **kwargs):
            pass

        def inventory(self, search):
            variables = ["HGT", "TMP", "RH", "UGRD", "VGRD", "VVEL"]
            return pd.DataFrame({
                "variable": variables,
                "grib_message": range(1, 7),
                "start_byte": range(0, 600, 100),
                "end_byte": range(99, 699, 100),
            })

        def xarray(self, search, **kwargs):
            return dataset

    monkeypatch.setattr(model_extract, "require_runtime_dependencies", lambda: None)
    monkeypatch.setattr(model_extract, "nomads_supported", lambda _cfg: True)
    monkeypatch.setattr(
        model_extract,
        "download_nomads_subset",
        lambda *_args, **_kwargs: seen.__setitem__(
            "nomads", seen["nomads"] + 1
        ),
    )
    monkeypatch.setattr(model_extract, "select_herbie_provider", lambda _h: None)

    def fake_ranges(*args, **kwargs):
        seen["ranges"] += 1
        return tmp_path / "subset.grib2", 600

    monkeypatch.setattr(model_extract, "download_herbie_subset", fake_ranges)
    monkeypatch.setitem(sys.modules, "herbie", SimpleNamespace(Herbie=FakeHerbie))

    _returned, herbie = model_extract._retrieve_dataset(
        model_extract.get_config("rap"),
        datetime(2026, 7, 14, 0, tzinfo=timezone.utc),
        0,
        download_dir=tmp_path,
        lat=35.0,
        lon=-97.0,
    )

    assert seen == {"ranges": 1, "nomads": 0}
    assert herbie._sharpmod_transport == "optimized-ranges"


def test_point_backend_grib_mode_bypasses_nomads(tmp_path, monkeypatch):
    dataset = _dataset()
    seen = {"ranges": 0, "nomads": 0}

    class FakeHerbie:
        grib = "https://example.invalid/rap.grib2"
        SOURCES = {}

        def __init__(self, *args, **kwargs):
            pass

        def inventory(self, search):
            variables = ["HGT", "TMP", "RH", "UGRD", "VGRD", "VVEL"]
            return pd.DataFrame({
                "variable": variables,
                "grib_message": range(1, 7),
                "start_byte": range(0, 600, 100),
                "end_byte": range(99, 699, 100),
            })

        def xarray(self, search, **kwargs):
            return dataset

    def fake_nomads(*args, **kwargs):
        seen["nomads"] += 1
        raise AssertionError("NOMADS must be disabled in grib mode")

    def fake_ranges(*args, **kwargs):
        seen["ranges"] += 1
        return tmp_path / "subset.grib2", 600

    monkeypatch.setenv("SHARPMOD_POINT_BACKENDS", "grib")
    monkeypatch.setattr(model_extract, "require_runtime_dependencies", lambda: None)
    monkeypatch.setattr(model_extract, "nomads_supported", lambda _cfg: True)
    monkeypatch.setattr(model_extract, "download_nomads_subset", fake_nomads)
    monkeypatch.setattr(model_extract, "select_herbie_provider", lambda _h: None)
    monkeypatch.setattr(model_extract, "download_herbie_subset", fake_ranges)
    monkeypatch.setitem(sys.modules, "herbie", SimpleNamespace(Herbie=FakeHerbie))

    returned, herbie = model_extract._retrieve_dataset(
        model_extract.get_config("rap"),
        datetime(2026, 7, 14, 0, tzinfo=timezone.utc),
        0,
        download_dir=tmp_path,
        lat=35.0,
        lon=-97.0,
    )

    assert returned is dataset
    assert seen == {"ranges": 1, "nomads": 0}
    assert herbie._sharpmod_transport == "optimized-ranges"


def test_hrrr_f000_uses_zarr_before_loading_grib_runtime(tmp_path, monkeypatch):
    dataset = _dataset()
    source = SimpleNamespace(
        grib="https://hrrrzarr/store",
        _sharpmod_source_url="https://hrrrzarr/store",
        _sharpmod_fields=("HGT", "TMP", "RH", "UGRD", "VGRD"),
        _sharpmod_transport="hrrr-zarr-point",
    )
    seen = {}

    def fake_zarr(run_dt, fxx, lat, lon, **kwargs):
        seen["request"] = (run_dt, fxx, lat, lon, kwargs)
        return dataset, source

    monkeypatch.setattr(
        model_extract, "fetch_hrrr_zarr_point", fake_zarr, raising=False
    )
    monkeypatch.setattr(
        model_extract,
        "require_runtime_dependencies",
        lambda: (_ for _ in ()).throw(
            AssertionError("Zarr must run before the ecCodes boundary")
        ),
    )
    monkeypatch.setenv("SHARPMOD_HRRR_BACKEND", "auto")

    returned, returned_source = model_extract._retrieve_dataset(
        model_extract.get_config("hrrr"),
        datetime(2026, 7, 14, 0, tzinfo=timezone.utc),
        0,
        download_dir=tmp_path,
        lat=35.0,
        lon=-97.0,
    )

    assert returned is dataset
    assert returned_source is source
    assert seen["request"][1:4] == (0, 35.0, -97.0)


def test_runtime_preflight_loads_only_the_native_eccodes_boundary(monkeypatch):
    """Slow Herbie/xarray imports must remain on the background worker."""
    real_import = builtins.__import__
    forbidden = {"cfgrib", "herbie", "xarray"}

    def _guarded_import(name, *args, **kwargs):
        if name.split(".", 1)[0] in forbidden:
            raise AssertionError(f"preflight imported {name}")
        return real_import(name, *args, **kwargs)

    fake_eccodes = SimpleNamespace(codes_get_api_version=lambda: "2.47.0")
    monkeypatch.setitem(sys.modules, "eccodes", fake_eccodes)
    monkeypatch.setattr(builtins, "__import__", _guarded_import)

    model_extract.require_runtime_dependencies()


def test_runtime_preflight_uses_bundled_windows_dll_without_helper_wheel(
        tmp_path, monkeypatch):
    """The pure Python 3.14 wheel can use its bundled DLL via findlibs."""
    package_dir = tmp_path / "eccodes"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "eccodes.dll").write_bytes(b"dll")
    fake_spec = SimpleNamespace(origin=str(package_dir / "__init__.py"))
    fake_eccodes = SimpleNamespace(codes_get_api_version=lambda: "2.46.3")
    real_import = builtins.__import__

    monkeypatch.setattr(model_extract.sys, "platform", "win32")
    monkeypatch.setattr(
        model_extract, "importlib",
        SimpleNamespace(util=SimpleNamespace(find_spec=lambda _name: fake_spec)),
        raising=False,
    )
    monkeypatch.delenv("ECCODES_PYTHON_USE_FINDLIBS", raising=False)
    monkeypatch.setenv("PATH", "C:\\Windows")

    def _guarded_import(name, *args, **kwargs):
        if name == "eccodes":
            assert os.environ["ECCODES_PYTHON_USE_FINDLIBS"] == "1"
            assert os.environ["PATH"].split(os.pathsep)[0] == str(package_dir)
            return fake_eccodes
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _guarded_import)

    model_extract.require_runtime_dependencies()


def test_probe_preflights_runtime_before_importing_herbie(monkeypatch):
    events = []

    class FakeHerbie:
        grib = "https://example.invalid/model.grib2"

        def __init__(self, *_args, **_kwargs):
            assert events == ["runtime"]

        @staticmethod
        def inventory():
            return ["row"]

    monkeypatch.setattr(
        model_extract, "require_runtime_dependencies",
        lambda: events.append("runtime"),
    )
    monkeypatch.setitem(sys.modules, "herbie", SimpleNamespace(Herbie=FakeHerbie))

    result = model_extract.probe(
        "hrrr", datetime(2026, 7, 14, 0, tzinfo=timezone.utc), fxx=0,
    )

    assert result["available"] is True
    assert events == ["runtime"]


def test_render_mode_removes_fetched_data_but_keeps_png(tmp_path, monkeypatch):
    npz_path = tmp_path / "point.npz"
    json_path = tmp_path / "point.json"
    png_path = tmp_path / "point.png"
    seen = {}

    def _fake_extract(*args, out_path=None, download_dir=None, **kwargs):
        seen["download_dir"] = Path(download_dir)
        Path(download_dir, "gfs", "raw.grib2").parent.mkdir(parents=True)
        Path(download_dir, "gfs", "raw.grib2").write_bytes(b"grib")
        Path(out_path).write_bytes(b"npz")
        Path(out_path).with_suffix(".json").write_text("{}", encoding="utf-8")
        return out_path

    def _fake_render(npz, png):
        assert Path(npz).exists()
        Path(png).write_bytes(b"png")
        return png

    monkeypatch.setattr(model_extract, "extract", _fake_extract)
    import sharpmod.tools as tools_mod
    monkeypatch.setattr(tools_mod, "render_npz", _fake_render)

    result = model_extract.main([
        "gfs", "35", "-99", str(npz_path), "--render", str(png_path),
    ])

    assert result == 0
    assert png_path.read_bytes() == b"png"
    assert not npz_path.exists()
    assert not json_path.exists()
    assert not seen["download_dir"].exists()


def test_render_failure_still_removes_all_fetched_data(tmp_path, monkeypatch):
    npz_path = tmp_path / "failed.npz"
    download_dirs = []

    def _fake_extract(*args, out_path=None, download_dir=None, **kwargs):
        download_dir = Path(download_dir)
        download_dirs.append(download_dir)
        Path(out_path).write_bytes(b"npz")
        Path(out_path).with_suffix(".json").write_text("{}", encoding="utf-8")
        (download_dir / "raw.grib2").write_bytes(b"grib")
        return out_path

    def _failed_render(npz, png):
        raise RuntimeError("render failed")

    monkeypatch.setattr(model_extract, "extract", _fake_extract)
    import sharpmod.tools as tools_mod
    monkeypatch.setattr(tools_mod, "render_npz", _failed_render)

    with pytest.raises(RuntimeError, match="render failed"):
        model_extract.main([
            "gfs", "35", "-99", str(npz_path), "--render", "failed.png",
        ])

    assert not npz_path.exists()
    assert not npz_path.with_suffix(".json").exists()
    assert len(download_dirs) == 1
    assert not download_dirs[0].exists()


def test_unsupported_model_reports_reason():
    with pytest.raises(model_extract.RetrievalError, match="not enabled"):
        model_extract.get_config("ukmet")

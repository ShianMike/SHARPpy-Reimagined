"""The 0.1-degree IFS feed must keep pressure, surface and terrain together."""

from datetime import datetime, timezone

import pandas as pd
import pytest

from sharpmod.tools import model_extract as mx
from sharpmod.tools import model_extract_retrieval as retrieval

MODEL = "ecmwf-ifs-9km"
RUN = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)


def test_9km_uses_its_own_grid_and_the_published_ifs_steps():
    config = mx.get_config("ecmwf-9km")
    assert config.key == MODEL
    assert config.kwargs == {"resolution": "0p1"}
    assert config.grid_spacing_km == 11.1
    assert [max(mx.forecast_hours(MODEL, cycle_hour=h)) for h in config.cycles] \
        == [360, 144, 360, 144]
    assert 147 not in mx.forecast_hours(MODEL)
    assert mx.get_config("ecmwf").key == "ecmwf-ifs"


@pytest.mark.parametrize("resolution,levtype,suffix", [
    ("0p25", "pl", "-fc.grib2"),
    ("0p1", "pl", "-fc-pl.grib2"),
    ("0p1", "sfc", "-fc-sfc.grib2"),
])
def test_split_urls_and_local_names_do_not_reuse_the_025_product(
        resolution, levtype, suffix):
    class Herbie:
        def __init__(self, **kwargs):
            self.levtype = kwargs["levtype"]
            self.SOURCES = {"aws": f"https://example.invalid/{resolution}/run-fc.grib2"}
            self.LOCALFILE = "run-fc.grib2"
            self.grib, self.grib_source = self.find_grib()

        @property
        def get_remoteFileName(self):
            return next(iter(self.SOURCES.values())).rsplit("/", 1)[1]

        def find_grib(self):
            return self.SOURCES["aws"], "aws"

    source = mx._create_herbie(
        Herbie, model="ifs", resolution=resolution, levtype=levtype,
    )
    assert source.grib == f"https://example.invalid/{resolution}/run{suffix}"
    assert source.LOCALFILE == f"run{suffix}"


@pytest.fixture
def split_feed(tmp_path, monkeypatch):
    feed = {"downloads": [], "omit": set(), "fallback": False}

    class Herbie:
        SOURCES = {}

        def __init__(self, _date, **kwargs):
            assert kwargs["model"] == "ifs" and kwargs["resolution"] == "0p1"
            self.levtype = kwargs.get("levtype", "pl")
            self.fxx = kwargs["fxx"]
            self.grib = f"https://example.invalid/{self.fxx}-fc-{self.levtype}.grib2"

        def inventory(self, search=None):
            if self.levtype == "pl":
                rows = [(field, f":{field}:{level}:pl:")
                        for field in ("gh", "t", "r", "u", "v", "w", "vo")
                        for level in (1000, 975, 10)]
            else:
                fields = ("z", "sp", "2t", "2d", "10u", "10v")
                rows = [(field, f":{field}:sfc:") for field in fields
                        if field not in feed["omit"]
                        and (field != "z" or self.fxx == 0)]
            inventory = pd.DataFrame(rows, columns=("param", "search_this"))
            if search is not None:
                inventory = inventory[inventory.search_this.str.contains(search)]
            return inventory.copy()

        def xarray(self, *_args, **_kwargs):
            raise AssertionError("split products must decode the combined local file")

    def download(source, _search, *, inventory, **_kwargs):
        feed["downloads"].append((source.levtype, source.fxx))
        path = tmp_path / f"{source.fxx}-{source.levtype}.grib2"
        path.write_bytes(b"GRIB" + "|".join(inventory.search_this).encode() + b"7777")
        return path, path.stat().st_size

    def optimized(source, *args, **kwargs):
        if feed["fallback"] and source.levtype == "sfc":
            raise mx.OptimizedTransportUnavailable("test fallback")
        return download(source, *args, **kwargs)

    monkeypatch.setattr(mx, "require_runtime_dependencies", lambda: None)
    monkeypatch.setattr(mx, "_load_herbie_class", lambda: Herbie)
    monkeypatch.setattr(mx, "select_herbie_provider", lambda _source: None)
    monkeypatch.setattr(mx, "download_herbie_subset", optimized)
    monkeypatch.setattr(retrieval, "download_herbie_subset_fallback", download)
    return feed


@pytest.mark.parametrize("fxx,fallback", [(0, False), (12, False), (12, True)])
def test_retrieval_joins_all_levels_surface_and_same_run_analysis(
        split_feed, tmp_path, fxx, fallback):
    split_feed["fallback"] = fallback
    dataset, source = mx._retrieve_dataset(
        mx.get_config(MODEL), RUN, fxx, download_dir=tmp_path,
    )
    try:
        assert split_feed["downloads"] == [
            ("pl", fxx), ("sfc", fxx), *([("sfc", 0)] if fxx else []),
        ]
        payload = dataset.path.read_bytes()
        assert b":gh:975:pl:" in payload and b":gh:10:pl:" in payload
        assert b":2d:sfc:" in payload and b":z:sfc:" in payload
        assert mx.cached_source_fields_compatible(
            mx.get_config(MODEL), source._sharpmod_fields,
        )
        if fxx:
            assert "0-fc-sfc.grib2" in source._sharpmod_source_url
        assert source._sharpmod_transport.endswith("+surface-companion")
    finally:
        dataset.close()


@pytest.mark.parametrize("missing", ["2d", "z"])
def test_incomplete_ground_data_is_refused(split_feed, tmp_path, missing):
    split_feed["omit"] = {missing}
    with pytest.raises(mx.RetrievalError, match="verified ground|invariant surface height"):
        mx._retrieve_dataset(mx.get_config(MODEL), RUN, 12, download_dir=tmp_path)


def test_availability_checks_both_products_and_the_analysis_terrain(split_feed):
    result = mx.probe(MODEL, run_time=RUN, fxx=12)
    assert result["available"] and result["surface_contract_complete"]
    assert result["surface_contract_invariant_companion"]
    assert result["surface_grib"].endswith("12-fc-sfc.grib2")
    assert split_feed["downloads"] == []


def test_cancellation_after_pressure_download_skips_surface(split_feed, tmp_path):
    with pytest.raises(mx.DownloadCancelled):
        mx._retrieve_dataset(
            mx.get_config(MODEL), RUN, 12, download_dir=tmp_path,
            cancelled=lambda: bool(split_feed["downloads"]),
        )
    assert split_feed["downloads"] == [("pl", 12)]

"""Forecast-model provider and server-side subset routing regressions."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from sharpmod.model_sources import (
    build_nomads_subset_url,
    choose_provider,
    nomads_supported,
)


def test_nomads_query_contains_fields_all_levels_and_small_region():
    config = SimpleNamespace(key="hrrr")
    source = (
        "https://nomads.ncep.noaa.gov/pub/data/nccf/com/hrrr/prod/"
        "hrrr.20260714/conus/hrrr.t08z.wrfprsf00.grib2"
    )

    url = build_nomads_subset_url(
        config, source, 35.0, -97.0,
        ("HGT", "TMP", "RH", "UGRD", "VGRD", "VVEL"),
    )
    parsed = urlparse(url)
    query = parse_qs(parsed.query, keep_blank_values=True)

    assert parsed.path.endswith("/filter_hrrr_2d.pl")
    assert query["file"] == ["hrrr.t08z.wrfprsf00.grib2"]
    assert query["dir"] == ["/hrrr.20260714/conus"]
    assert query["all_lev"] == ["on"]
    assert query["var_HGT"] == ["on"]
    assert query["var_RH"] == ["on"]
    assert float(query["toplat"][0]) == 35.15
    assert float(query["bottomlat"][0]) == 34.85
    assert float(query["leftlon"][0]) == -97.15
    assert float(query["rightlon"][0]) == -96.85


def test_nomads_directory_derivation_supports_gfs_nested_cycle():
    config = SimpleNamespace(key="gfs")
    source = (
        "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod/"
        "gfs.20260714/06/atmos/gfs.t06z.pgrb2.0p25.f000"
    )

    query = parse_qs(urlparse(build_nomads_subset_url(
        config, source, 0.0, 120.0,
        ("HGT", "TMP", "RH", "UGRD", "VGRD"),
    )).query)

    assert query["dir"] == ["/gfs.20260714/06/atmos"]
    assert query["file"] == ["gfs.t06z.pgrb2.0p25.f000"]


def test_nomads_support_is_capability_checked():
    assert nomads_supported(SimpleNamespace(key="hrrr"))
    assert nomads_supported(SimpleNamespace(key="nam-3km-conus"))
    assert not nomads_supported(SimpleNamespace(key="ecmwf-ifs"))


def test_fastest_compatible_provider_wins():
    candidates = {
        "aws": "https://aws/file",
        "google": "https://google/file",
        "azure": "https://azure/file",
    }
    results = {
        "https://aws/file": (True, 4000, 0.4),
        "https://google/file": (True, 4000, 0.2),
        "https://azure/file": (True, 5000, 0.1),
    }

    selected = choose_provider(
        candidates,
        lambda url: results[url],
        reference_url="https://aws/file",
    )

    assert selected == ("google", "https://google/file")


def test_provider_selection_retains_reference_when_probes_fail():
    candidates = {"aws": "https://aws/file", "google": "https://google/file"}

    selected = choose_provider(
        candidates,
        lambda _url: (False, 0, 99.0),
        reference_url="https://aws/file",
    )

    assert selected == ("aws", "https://aws/file")

"""Provenance sidecar and profile quality inspector regressions."""

from datetime import datetime
import json
from types import SimpleNamespace

import numpy as np

from sharpmod.io.decoder import load_npz
from sharpmod.profile_inspector import format_report, inspect_profile, provenance


class _Collection:
    def __init__(self, profile, metadata=None):
        self.profile = profile
        self.metadata = dict(metadata or {})

    def getCurrentProfs(self):
        return {"": self.profile}

    def getMeta(self, key):
        return self.metadata.get(key)


def _profile(**overrides):
    values = {
        "pres": np.array([1000, 900, 800, 700, 600, 500, 400, 300]),
        "hght": np.arange(8) * 1000.0,
        "tmpc": np.linspace(25, -45, 8),
        "dwpc": np.linspace(20, -50, 8),
        "wspd": np.linspace(5, 60, 8),
        "missing": -9999.0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_clean_profile_report_includes_provenance():
    collection = _Collection(
        _profile(),
        {"model": "RRFS A", "transport": "optimized-ranges", "fxx": 3},
    )

    assert inspect_profile(collection) == []
    assert provenance(collection)["transport"] == "optimized-ranges"
    report = format_report(collection)
    assert "RRFS A" in report
    assert "Pressure levels: 8 usable / 8 rows" in report
    assert "Missing fields:" in report
    assert "Surface relative vorticity:" in report
    assert "No basic structural problems" in report


def test_provenance_uses_focused_timeline_hour_metadata():
    collection = _Collection(
        _profile(),
        {
            "fxx": 0,
            "source_url": "https://example.test/f000",
            "timeline_provenance": [
                {"fxx": 6, "source_url": "https://example.test/f006"}
            ],
        },
    )
    collection._prof_idx = 0

    metadata = provenance(collection)

    assert metadata["fxx"] == 6
    assert metadata["source_url"].endswith("f006")


def test_inspector_flags_order_supersaturation_and_negative_wind():
    profile = _profile(
        pres=np.array([1000, 900, 900, 700, 600, 500, 400, 350]),
        dwpc=np.linspace(30, -40, 8),
        wspd=np.array([-1, 5, 10, 15, 20, 25, 30, 35]),
    )

    codes = {item.code for item in inspect_profile(_Collection(profile))}

    assert {"pressure-order", "dewpoint-above-temperature",
            "negative-wind-speed", "shallow-profile"} <= codes


def test_npz_decoder_attaches_json_sidecar_provenance(tmp_path):
    path = tmp_path / "point.npz"
    valid = "2026-07-22 03:00"
    np.savez(
        path,
        pres=np.array([1000, 900, 800, 700, 600, 500, 400, 300]),
        hght=np.arange(8) * 1000.0,
        tmpc=np.linspace(25, -45, 8),
        dwpc=np.linspace(20, -50, 8),
        wdir=np.linspace(180, 250, 8),
        wspd=np.linspace(5, 60, 8),
        omeg=np.zeros(8),
        loc="TEST", lat=35.0, lon=-97.0, model="GFS",
        run="2026-07-22 00:00", valid=valid,
    )
    sidecar = {
        "model": "GFS",
        "requested_lat": 35.1,
        "requested_lon": -97.1,
        "selected_lat": 35.0,
        "selected_lon": -97.0,
        "transport": "nomads-subregion",
        "fields": ["HGT", "TMP"],
    }
    path.with_suffix(".json").write_text(json.dumps(sidecar), encoding="utf-8")

    collection, _loc = load_npz(path)

    assert collection.getMeta("transport") == "nomads-subregion"
    assert collection.getMeta("requested_lat") == 35.1
    assert collection.getMeta("fields") == ["HGT", "TMP"]
    assert collection.getMeta("run") == datetime(2026, 7, 22, 0, 0)
    assert collection.getMeta("decoder") == "portable NPZ decoder"

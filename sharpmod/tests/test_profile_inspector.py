"""Provenance sidecar and profile quality inspector regressions."""

from datetime import datetime
import json

import numpy as np

from sharpmod.io.decoder import load_npz


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
        loc="TEST",
        lat=35.0,
        lon=-97.0,
        model="GFS",
        run="2026-07-22 00:00",
        valid=valid,
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

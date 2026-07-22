"""Tests for the ECCC GeoMet GDPS/RDPS point adapter."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import re
import threading
import time

import numpy as np
import pytest

from sharpmod import eccc_geomet
from sharpmod.tools import model_extract


RUN = datetime(2026, 7, 22, 0, tzinfo=timezone.utc)


class _Response:
    def __init__(self, *, payload=None, text="", status=200):
        self._payload = payload
        self.text = text
        self.status_code = status

    def json(self):
        if self._payload is None:
            raise ValueError("not JSON")
        return self._payload


def _capabilities_xml(run=RUN):
    value = run.strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        '<WMS_Capabilities xmlns="http://www.opengis.net/wms">'
        "<Capability><Layer>"
        '<Dimension name="time" units="ISO8601" '
        'default="2026-07-22T06:00:00Z">unused</Dimension>'
        '<Dimension name="reference_time" units="ISO8601" '
        'default="%s">unused</Dimension>'
        "</Layer></Capability></WMS_Capabilities>" % value
    )


@pytest.fixture
def small_gdps(monkeypatch):
    capability = replace(
        eccc_geomet.get_capability("gdps"),
        pressure_levels=(1000, 850, 500),
        omega_levels=(850, 500),
        forecast_hours=(0, 3, 6),
    )
    monkeypatch.setitem(eccc_geomet._CAPABILITIES, "gdps", capability)
    return capability


def _value(variable, level):
    return {
        "AirTemp": 14.0 - (1000 - level) * 0.006,
        "GeopotentialHeight": (1000 - level) * 16.0,
        "SpecificHumidity": 0.007 * (level / 1000.0),
        "WindDir": 270.0,
        "WindSpeed": 10.0,
        "VerticalVelocity": -0.2,
    }[variable]


def _fake_get_factory(*, fail_layer=None, delay=0.0):
    state = {"active": 0, "max_active": 0, "calls": []}
    lock = threading.Lock()

    def get(_url, *, params, headers, timeout):
        assert "SHARPpy-Reimagined" in headers["User-Agent"]
        assert timeout == (10, 30)
        request = params["REQUEST"]
        if request == "GetCapabilities":
            return _Response(text=_capabilities_xml())
        layer = params["LAYERS"]
        with lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
            state["calls"].append(layer)
        try:
            if delay:
                time.sleep(delay)
            match = re.search(
                r"_(AirTemp|GeopotentialHeight|SpecificHumidity|WindDir|"
                r"WindSpeed|VerticalVelocity)_(\d+)mb$",
                layer,
            )
            assert match, layer
            variable, level_text = match.groups()
            level = int(level_text)
            features = [] if layer == fail_layer else [{
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [-73.65, 45.45],
                },
                "properties": {
                    "value": _value(variable, level),
                    "time": params["TIME"],
                    "dim_reference_time": params["DIM_REFERENCE_TIME"],
                },
            }]
            return _Response(payload={
                "type": "FeatureCollection",
                "layer": layer,
                "features": features,
            })
        finally:
            with lock:
                state["active"] -= 1

    return get, state


def test_provider_capabilities_are_real_point_routes():
    gdps = eccc_geomet.get_capability("gem-global")
    rdps = eccc_geomet.get_capability("rdps")

    assert gdps.model_key == "gdps"
    assert gdps.domain == "Global"
    assert gdps.cycles == (0, 12)
    assert max(gdps.forecast_hours) == 240
    assert gdps.transports == ("wms-getfeatureinfo-point",)
    assert rdps.cycles == (0, 6, 12, 18)
    assert max(rdps.forecast_hours) == 84


def test_feature_info_uses_wms_13_axis_order_and_exact_cycle():
    params = eccc_geomet.build_feature_info_params(
        "gdps",
        "AirTemp",
        850,
        45.5,
        -73.6,
        RUN.replace(hour=6),
        RUN,
    )

    assert params["LAYERS"] == "GDPS_15km_AirTemp_850mb"
    assert params["BBOX"] == "45.300000,-73.800000,45.700000,-73.400000"
    assert params["I"] == params["J"] == "1"
    assert params["TIME"] == "2026-07-22T06:00:00Z"
    assert params["DIM_REFERENCE_TIME"] == "2026-07-22T00:00:00Z"


def test_latest_reference_time_uses_small_layer_capabilities(small_gdps):
    get, state = _fake_get_factory()

    result = eccc_geomet.latest_reference_time("gdps", request_get=get)

    assert result == RUN
    assert state["calls"] == []


def test_bounded_fanout_normalizes_profile_columns(small_gdps):
    get, state = _fake_get_factory(delay=0.01)

    dataset = eccc_geomet.fetch_point(
        "gdps",
        45.5,
        -73.6,
        run_time=RUN,
        fxx=6,
        max_workers=2,
        request_get=get,
    )

    assert state["max_active"] == 2
    assert len(state["calls"]) == 17
    assert dataset.request_count == 17
    assert dataset.max_workers == 2
    np.testing.assert_array_equal(dataset.columns["pres"], [1000, 850, 500])
    np.testing.assert_allclose(dataset.columns["wspd"], 19.4384449)
    np.testing.assert_allclose(dataset.columns["u"], 10.0, atol=1e-10)
    np.testing.assert_allclose(dataset.columns["v"], 0.0, atol=1e-10)
    assert np.all(np.isfinite(dataset.columns["dwpc"]))
    assert dataset.selected_lat == pytest.approx(45.45)
    assert dataset.selected_lon == pytest.approx(-73.65)


def test_extract_writes_portable_npz_and_provenance(tmp_path, small_gdps):
    get, _state = _fake_get_factory()
    stages = []
    out = tmp_path / "gdps.npz"

    result = eccc_geomet.extract(
        "gdps",
        45.5,
        -73.6,
        run_time=RUN,
        fxx=3,
        out_path=out,
        loc="Montreal",
        max_workers=3,
        request_get=get,
        progress_callback=lambda stage, total=0: stages.append((stage, total)),
    )

    assert result == str(out)
    with np.load(out, allow_pickle=True) as payload:
        assert str(payload["model"]) == small_gdps.label
        assert str(payload["loc"]) == "Montreal"
        assert int(payload["fxx"]) == 3
        np.testing.assert_array_equal(payload["pres"], [1000, 850, 500])
    metadata = json.loads(out.with_suffix(".json").read_text(encoding="utf-8"))
    assert metadata["model_key"] == "gdps"
    assert metadata["provider"] == "ECCC MSC GeoMet"
    assert metadata["transport"] == "wms-getfeatureinfo-point"
    assert metadata["request_count"] == 17
    assert metadata["max_workers"] == 3
    assert [stage for stage, _total in stages] == [
        "locating", "downloading", "extracting", "writing", "complete"
    ]


def test_required_layer_failure_is_not_silently_dropped(small_gdps):
    get, _state = _fake_get_factory(
        fail_layer="GDPS_15km_AirTemp_850mb"
    )

    with pytest.raises(eccc_geomet.RetrievalError, match="incomplete at 850 mb"):
        eccc_geomet.fetch_point(
            "gdps",
            45.5,
            -73.6,
            run_time=RUN,
            max_workers=2,
            request_get=get,
        )


def test_cached_point_dataset_identity_is_strict(tmp_path, small_gdps):
    get, _state = _fake_get_factory()
    dataset = eccc_geomet.fetch_point(
        "gdps", 45.5, -73.6, run_time=RUN, fxx=3, request_get=get
    )

    with pytest.raises(eccc_geomet.RetrievalError, match="forecast hour"):
        eccc_geomet.extract(
            "gdps",
            45.5,
            -73.6,
            run_time=RUN,
            fxx=6,
            out_path=tmp_path / "wrong.npz",
            dataset=dataset,
        )


def test_cancellation_stops_before_network(small_gdps):
    get, state = _fake_get_factory()

    with pytest.raises(eccc_geomet.DownloadCancelled):
        eccc_geomet.fetch_point(
            "gdps",
            45.5,
            -73.6,
            run_time=RUN,
            request_get=get,
            cancelled=lambda: True,
        )

    assert state["calls"] == []


def test_default_request_cancellation_closes_blocked_sessions(
    small_gdps, monkeypatch
):
    import requests

    cancel = threading.Event()
    started = threading.Event()

    class BlockingSession:
        def __init__(self):
            self.closed = threading.Event()

        def get(self, *_args, **_kwargs):
            assert _kwargs["stream"] is True
            started.set()
            if not self.closed.wait(5.0):
                raise TimeoutError("test session was not closed")
            raise OSError("socket closed by cancellation")

        def close(self):
            self.closed.set()

    monkeypatch.setattr(requests, "Session", BlockingSession)
    timer = threading.Timer(0.10, cancel.set)
    timer.start()
    began = time.monotonic()
    try:
        with pytest.raises(eccc_geomet.DownloadCancelled):
            eccc_geomet.fetch_point(
                "gdps",
                45.5,
                -73.6,
                run_time=RUN,
                max_workers=2,
                cancelled=cancel.is_set,
            )
    finally:
        timer.cancel()

    assert started.is_set()
    assert time.monotonic() - began < 1.0


def test_exact_reference_time_mismatch_is_rejected(small_gdps):
    def wrong_run_get(_url, *, params, headers, timeout):
        if params["REQUEST"] == "GetCapabilities":
            return _Response(text=_capabilities_xml())
        variable, level = re.search(
            r"_(AirTemp|GeopotentialHeight|SpecificHumidity|WindDir|"
            r"WindSpeed|VerticalVelocity)_(\d+)mb$",
            params["LAYERS"],
        ).groups()
        return _Response(payload={"features": [{
            "geometry": {"coordinates": [-73.65, 45.45]},
            "properties": {
                "value": _value(variable, int(level)),
                "time": params["TIME"],
                "dim_reference_time": "2026-07-21T12:00:00Z",
            },
        }]})

    with pytest.raises(eccc_geomet.RetrievalError, match="incomplete"):
        eccc_geomet.fetch_point(
            "gdps",
            45.5,
            -73.6,
            run_time=RUN,
            max_workers=1,
            request_get=wrong_run_get,
        )


def test_worker_count_is_environment_bounded(monkeypatch):
    monkeypatch.setenv("SHARPMOD_GEOMET_WORKERS", "99")
    assert eccc_geomet.worker_count() == 8
    monkeypatch.setenv("SHARPMOD_GEOMET_WORKERS", "bad")
    assert eccc_geomet.worker_count() == 4
    assert eccc_geomet.worker_count(0) == 1


def test_generic_model_catalog_exposes_eccc_point_adapters():
    gdps = model_extract.get_config("gem-global")
    rdps = model_extract.get_config("cmc-regional")

    assert gdps.key == "gdps"
    assert gdps.product == "geomet-point"
    assert rdps.key == "rdps"
    assert "gdps" not in model_extract.unsupported_models()
    assert "rdps" not in model_extract.unsupported_models()
    capability = model_extract.provider_capability("gdps")
    assert capability.provider == "ECCC MSC GeoMet"
    assert capability.transports == ("wms-getfeatureinfo-point",)
    assert capability.levels == "33 published pressure levels"
    assert model_extract.spatial_cache_key(gdps, 45.5, -73.6) \
        == "45.5000,-73.6000"
    assert model_extract.requires_grib_runtime(gdps) is False
    assert model_extract.requires_grib_runtime(rdps) is False
    assert model_extract.requires_grib_runtime("gfs") is True
    assert model_extract.point_only_provider(gdps) is True
    assert model_extract.point_only_provider("gfs") is False


def test_generic_extract_dispatches_to_eccc_adapter(tmp_path, monkeypatch):
    seen = {}

    def fake_extract(model, lat, lon, **kwargs):
        seen.update(model=model, lat=lat, lon=lon, kwargs=kwargs)
        return "delegated.npz"

    monkeypatch.setattr(eccc_geomet, "extract", fake_extract)
    out = tmp_path / "point.npz"

    result = model_extract.extract(
        "gdps",
        45.5,
        -73.6,
        run_time=RUN,
        fxx=6,
        out_path=out,
        loc="Montreal",
    )

    assert result == "delegated.npz"
    assert seen["model"] == "gdps"
    assert seen["lat"] == pytest.approx(45.5)
    assert seen["kwargs"]["run_time"] == RUN
    assert seen["kwargs"]["out_path"] == out


def test_generic_cache_loader_returns_spatial_dataset_and_provenance(
    small_gdps, monkeypatch
):
    dataset = object()
    monkeypatch.setattr(eccc_geomet, "fetch_point", lambda *a, **k: dataset)
    cfg = model_extract.get_config("gdps")

    result, source = model_extract._retrieve_dataset(
        cfg, RUN, 3, lat=45.5, lon=-73.6
    )

    assert result is dataset
    assert source._sharpmod_source_url == eccc_geomet.GEOMET_URL
    assert source._sharpmod_transport == "wms-getfeatureinfo-point"
    assert source._sharpmod_fields == small_gdps.fields

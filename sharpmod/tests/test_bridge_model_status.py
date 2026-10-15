"""Provider-status checks stay advisory, bounded and independent of catalogue loading."""
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from sharpmod.bridge import params, soundings
from sharpmod.bridge.errors import BridgeError
from sharpmod.bridge.handlers import HANDLERS
from sharpmod.bridge.public import bridge_request, public_result
from sharpmod.bridge.protocol import Request, check_params
from sharpmod.tools import model_extract as mx
from sharpmod.tools import model_extract_probe
from sharpmod.providers import rrfs_nomads

REQUEST = {"model": "hrrr", "run_time": "2026-10-13T00:00:00Z", "forecast_hour": 0}


@pytest.mark.parametrize(("result", "state"), [
    ({"available": True, "surface_contract_complete": True}, "available"),
    ({"available": True, "surface_contract_complete": False}, "partial"),
    ({"available": False}, "unavailable"),
    ({"error": "secret-provider-debug-text"}, "unknown"),
])
def test_public_status_uses_the_existing_bounded_probe(monkeypatch, result, state):
    calls = []
    monkeypatch.setattr(params, "now", lambda: datetime(2026, 10, 13, 2, tzinfo=UTC))
    monkeypatch.setattr(mx, "probe", lambda *args, **kwargs: calls.append((args, kwargs)) or result)
    method, args = bridge_request("get_model_status", {"model": "hrrr", "runTime": REQUEST["run_time"], "forecastHour": 0})
    check_params(Request(1, method, args))
    reply = public_result("get_model_status", HANDLERS[method](args))
    assert reply["state"] == state and reply["model"] == "hrrr"
    assert reply["runTime"] == REQUEST["run_time"] and reply["checkedAt"] == "2026-10-13T02:00:00Z"
    assert "secret-provider-debug-text" not in str(reply)
    assert calls[0][0] == ("hrrr",)
    assert calls[0][1] == {"run_time": datetime(2026, 10, 13, tzinfo=UTC), "fxx": 0,
                           "open_subset": False, "request_timeout": 2.0, "deadline_seconds": 8.0}


def test_openmeteo_status_does_not_spend_the_users_quota(monkeypatch):
    def unexpected_probe(*args, **kwargs):
        pytest.fail("Open-Meteo background status must not make a metered request")
    monkeypatch.setattr(mx, "probe", unexpected_probe)
    result = soundings.model_status({**REQUEST, "model": "openmeteo-ecmwf-ifs025"})
    assert result["state"] == "configured" and result["provider"] == "Open-Meteo"


def test_provider_exception_is_unconfirmed_and_never_claims_missing_data(monkeypatch):
    def failed_probe(*args, **kwargs):
        raise TimeoutError("private detail")
    monkeypatch.setattr(mx, "probe", failed_probe)
    result = soundings.model_status(REQUEST)
    assert result["state"] == "unknown" and "private detail" not in str(result)


@pytest.mark.parametrize("change", [{"model": "missing"}, {"forecast_hour": 1001},
                                    {"run_time": "invalid"}, {"run_time": "2026-10-13T00:30:00Z"},
                                    {"forecast_hour": 48, "run_time": "2026-10-13T01:00:00Z"}])
def test_invalid_model_cycle_and_hour_never_reach_a_probe(change):
    with pytest.raises(BridgeError) as failure:
        soundings.model_status({**REQUEST, **change})
    assert failure.value.code == "INVALID_ARGUMENT"


@pytest.mark.parametrize("model", sorted(mx.DIRECT_GRIB_KEYS))
def test_direct_model_status_checks_both_nomads_products_without_herbie(monkeypatch, model):
    calls = []
    pressure = ["HGT:1000 mb", "TMP:1000 mb", "RH:1000 mb",
                "UGRD:1000 mb", "VGRD:1000 mb"]
    surface = ["PRES:surface", "HGT:surface", "TMP:2 m above ground",
               "SPFH:2 m above ground", "UGRD:10 m above ground", "VGRD:10 m above ground"]

    def get(url, **kwargs):
        calls.append((url, kwargs))
        fields = pressure if ".prslev" in url else surface
        text = "\n".join(f"{i + 1}:{i * 100}:d=2026101300:{field}:anl:"
                         for i, field in enumerate(fields))
        return nullcontext(SimpleNamespace(
            status_code=403 if "/prod/" in url else 200 if url.endswith(".idx") else 206,
            text=text, headers={"Content-Range": "bytes 0-0/1000"},
            iter_content=lambda **_kwargs: iter([b"G"]),
        ))

    monkeypatch.setattr(rrfs_nomads, "_RELEASE_CACHE", None)
    monkeypatch.setattr(rrfs_nomads, "_new_session", lambda: nullcontext(SimpleNamespace(get=get)))
    monkeypatch.setattr(mx, "require_runtime_dependencies", lambda: pytest.fail("inventory check loaded GRIB runtime"))
    result = soundings.model_status({**REQUEST, "model": model})
    assert result["state"] == "available" and result["provider"] == "NOAA NOMADS"
    config = mx.get_config(model)
    for product in (rrfs_nomads.PRESSURE_PRODUCT, rrfs_nomads.SURFACE_PRODUCT):
        url = rrfs_nomads.build_url("para", datetime(2026, 10, 13, tzinfo=UTC), 0,
                                    product, config.kwargs["domain"], member=config.kwargs.get("member"))
        assert any(request == url + ".idx" for request, _ in calls)
        assert any(request == url and args["headers"] == {"Range": "bytes=0-0"}
                   for request, args in calls)
    assert all(0 < args["timeout"] <= 2 for _, args in calls)
    assert len(calls) == 5  # One unavailable release, then two index/range pairs.


@pytest.mark.parametrize(("failure", "state"), [("partial", "partial"), ("missing", "unknown"), ("timeout", "unknown")])
def test_direct_model_status_does_not_hide_incomplete_or_failed_surface_checks(monkeypatch, failure, state):
    pressure = rrfs_nomads.parse_idx("\n".join(
        f"{i + 1}:{i * 100}:d=2026101300:{field}:1000 mb:anl:"
        for i, field in enumerate(("HGT", "TMP", "RH", "UGRD", "VGRD"))
    ), 1000)

    def inventory(_session, url, **_kwargs):
        if failure == "missing":
            raise rrfs_nomads.RrfsUnavailable("RRFS inventory is unavailable (HTTP 404)")
        if failure == "timeout":
            raise TimeoutError("provider did not respond")
        # A pressure-level TMP record cannot satisfy the required 2-m field.
        return pressure, 1000

    monkeypatch.setattr(rrfs_nomads, "_pressure_inventory", lambda *_a, **_k: ("para", "pressure.grib2", pressure))
    monkeypatch.setattr(rrfs_nomads, "_inventory", inventory)
    result = soundings.model_status({**REQUEST, "model": "refs-m1"})
    assert result["state"] == state


def test_direct_probe_obeys_the_total_deadline_and_cancellation(monkeypatch):
    moment = [0.0]
    calls = []
    closed = []

    def get(url, **kwargs):
        calls.append(url)
        assert kwargs["timeout"] == 0.5
        moment[0] = 1.0
        return SimpleNamespace(close=lambda: closed.append(True))

    monkeypatch.setattr(model_extract_probe.time, "monotonic", lambda: moment[0])
    monkeypatch.setattr(rrfs_nomads, "_RELEASE_CACHE", None)
    monkeypatch.setattr(rrfs_nomads, "_new_session", lambda: nullcontext(SimpleNamespace(get=get)))
    result = mx.probe("refs-m1", run_time=REQUEST["run_time"], request_timeout=2, deadline_seconds=0.5)
    assert not result["available"] and "TimeoutError" in result["error"]
    assert len(calls) == 1 and closed == [True]

    def cancelled_inventory(*_args, **_kwargs):
        raise mx.DownloadCancelled("cancelled during inventory check")

    monkeypatch.setattr(rrfs_nomads, "_inventory", cancelled_inventory)
    with pytest.raises(mx.DownloadCancelled):
        mx.probe("refs-m1", run_time=REQUEST["run_time"])


def test_direct_probe_explicit_subset_uses_the_same_member_and_cleans_up(monkeypatch):
    pressure = rrfs_nomads.parse_idx("\n".join(
        f"{i + 1}:{i * 100}:d=2026101300:{field}:1000 mb:anl:"
        for i, field in enumerate(("HGT", "TMP", "RH", "UGRD", "VGRD"))
    ), 1000)
    seen = {}
    closed = []

    def fetch_pair(model, domain, run, hour, **kwargs):
        seen.update(model=model, domain=domain, member=kwargs["member"], directory=kwargs["download_dir"])
        assert kwargs["workers"] == 1
        return [SimpleNamespace(path=Path(kwargs["download_dir"]) / name)
                for name in ("pressure.grib2", "surface.grib2")]

    def open_datasets(path, **_kwargs):
        return [SimpleNamespace(data_vars=["t"] if path.name == "pressure.grib2" else ["t2m"],
                                close=lambda: closed.append(path.name))]

    monkeypatch.setattr(rrfs_nomads, "_pressure_inventory", lambda *_a, **_k: ("para", "pressure.grib2", pressure))
    monkeypatch.setattr(rrfs_nomads, "_inventory", lambda *_a, **_k: (pressure, 1000))
    monkeypatch.setattr(rrfs_nomads, "fetch_pair", fetch_pair)
    monkeypatch.setattr(mx, "require_runtime_dependencies", lambda: None)
    monkeypatch.setitem(sys.modules, "cfgrib", SimpleNamespace(open_datasets=open_datasets))
    result = mx.probe("refs-m5", run_time=REQUEST["run_time"], open_subset=True)
    assert result["subset_opened"] and result["data_vars"] == ["t", "t2m"]
    assert seen["model"] == "refs-m5" and seen["member"] == 5 and seen["domain"] == "conus"
    assert closed == ["pressure.grib2", "surface.grib2"]
    assert not Path(seen["directory"]).exists()

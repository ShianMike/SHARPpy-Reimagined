"""Open-Meteo point soundings: catalog gating, normalization, and writing.

The adapter's job is to turn one provider response into a sounding that is
either correct or refused. These tests hold that line in both directions: the
units and the ground row must be right, and every incomplete or mislabelled
response must raise rather than render.

No test here touches the network. The transport is injected.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from sharpmod import openmeteo as om
from sharpmod.backends import basic_sounding_qc
from sharpmod.tools import model_extract

UTC = timezone.utc
MODEL = "openmeteo-icon-global"
RUN = datetime(2026, 8, 29, 0, tzinfo=UTC)
FXX = 12
VALID = RUN + timedelta(hours=FXX)
SURFACE_P = 1005.0

#: The ladder the enabled model actually requests, taken from the manifest
#: rather than restated. The provider advertises 26 levels for every model and
#: fills at most twelve, so a hardcoded count here would drift from the
#: capability the moment a measured ladder is corrected.
LEVELS = om.CAPABILITIES[MODEL].pressure_levels


def _std_height(pressure: float) -> float:
    """Standard-atmosphere geopotential height, for a self-consistent profile."""
    return 44330.0 * (1.0 - (pressure / 1013.25) ** 0.1903)


class _Response:
    status_code = 200
    reason = "OK"
    headers = {"Content-Type": "application/json"}

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _payload(*, elevation=142.0, surface_pressure=SURFACE_P,
             drop_level=None, blank_family=None, times=None):
    """Build a response whose profile is hydrostatically self-consistent."""
    midnight = VALID.replace(hour=0)
    stamps = times if times is not None else [
        int((midnight + timedelta(hours=hour)).timestamp())
        for hour in range(24)
    ]
    hourly: dict = {"time": stamps}
    slot = VALID.hour

    def series(value):
        column = [None] * len(stamps)
        if slot < len(column):
            column[slot] = value
        return column

    for level in LEVELS:
        height = _std_height(float(level))
        temperature = max(-65.0, 15.0 - 6.5 * height / 1000.0)
        values = {
            "temperature": temperature,
            "dew_point": temperature - 5.0,
            "wind_speed": 5.0 + height / 1000.0 * 2.0,
            "wind_direction": 240.0,
            "geopotential_height": height,
        }
        for family in om.PRESSURE_FAMILIES:
            key = "%s_%dhPa" % (family, level)
            if blank_family == family:
                hourly[key] = series(None)
            elif drop_level == level and family == "temperature":
                hourly[key] = series(None)
            else:
                hourly[key] = series(values[family])

    hourly["surface_pressure"] = series(surface_pressure)
    hourly["temperature_2m"] = series(16.0)
    hourly["dew_point_2m"] = series(11.0)
    hourly["wind_speed_10m"] = series(4.0)
    hourly["wind_direction_10m"] = series(230.0)

    return {
        "latitude": 38.75,
        "longitude": -90.875,
        "elevation": elevation,
        "generationtime_ms": 12.5,
        "utc_offset_seconds": 0,
        "hourly": hourly,
    }


def _getter(payload):
    """Return an injectable transport plus the query it captured."""
    seen: dict = {}

    def request_get(url, params=None, timeout=None):
        seen["url"] = url
        seen["params"] = dict(params or {})
        return _Response(payload)

    return request_get, seen


def _fetch(**kwargs):
    payload = kwargs.pop("payload", None)
    request_get, seen = _getter(payload if payload is not None else _payload())
    call = {"run_time": RUN, "fxx": FXX, "request_get": request_get}
    call.update(kwargs)
    dataset = om.fetch_point(MODEL, 38.77, -90.87, **call)
    return dataset, seen


def _refuse(*_args, **_kwargs):
    raise AssertionError("this path must not reach the network")


# --------------------------------------------------------------------------- #
# Catalog gating
# --------------------------------------------------------------------------- #
def test_exactly_one_model_is_enabled():
    """Scope is deliberate: one audited model beats a list of maybes."""
    assert [c.model_key for c in om.available_capabilities()] == [MODEL]


@pytest.mark.parametrize("api_model", [
    "best_match", "icon_seamless", "jma_seamless", "ukmo_seamless",
    "ncep_hgefs025_ensemble_mean", "ecmwf_ifs", "ecmwf_ifs025",
    "ecmwf_aifs025_single",
    "ncep_gfs_global", "ncep_hrrr_conus", "cmc_gem_gdps", "cmc_gem_rdps",
])
def test_combined_and_duplicate_routes_are_not_selectable(api_model):
    assert not om.is_selectable_api_model(api_model)


def test_the_enabled_model_is_selectable():
    assert om.is_selectable_api_model("icon_global")


def test_a_withheld_model_explains_itself():
    """A considered exclusion must not surface as a typo."""
    with pytest.raises(om.RetrievalError) as excinfo:
        om.get_capability("openmeteo-best-match")
    assert "combines several models" in str(excinfo.value).lower()


def test_ecmwf_is_withheld_for_publishing_no_pressure_levels():
    """The audit found a healthy response carrying zero pressure levels.

    Recorded as a test because the identifier is the obvious thing to reach for
    and the reason is not guessable: the run resolves and every surface field
    arrives, so nothing about the response looks like a failure.
    """
    with pytest.raises(om.RetrievalError) as excinfo:
        om.get_capability("openmeteo-ecmwf-ifs")
    assert "without pressure-level fields" in str(excinfo.value)
    assert not om.is_selectable_api_model("ecmwf_ifs")


def test_an_unknown_model_is_a_key_error():
    with pytest.raises(KeyError):
        om.get_capability("openmeteo-invented")


def test_unsupported_models_use_namespaced_keys():
    keys = om.unsupported_models()
    assert "openmeteo-best-match" in keys
    assert "openmeteo-ecmwf-ifs" in keys
    assert all(key.startswith("openmeteo-") for key in keys)


def test_the_enabled_model_is_not_also_reported_as_unsupported():
    """A key cannot be both selectable and explained as absent."""
    assert MODEL not in om.unsupported_models()


def test_the_request_asks_for_one_variable_per_level_and_field():
    capability = om.get_capability(MODEL)
    names = om.hourly_variables(capability)
    assert len(names) == capability.variable_count()
    assert len(names) == len(om.PRESSURE_FAMILIES) * len(LEVELS) \
        + len(om.SURFACE_VARIABLES)
    assert len(set(names)) == len(names)
    # Vertical velocity is m/s here, not the pressure velocity omeg carries.
    assert not any(name.startswith("vertical_velocity") for name in names)


def test_only_measured_levels_are_requested():
    """The provider advertises 26 levels per model and fills at most twelve.

    Asking for a level the model never populates cannot produce data and does
    cost request width, which is billable.
    """
    assert LEVELS == om.MEASURED_LADDERS["icon_global"]
    assert len(LEVELS) == 12
    unpopulated = {950, 900, 750, 650, 550, 450, 350, 275, 225, 175, 125}
    assert not unpopulated & set(LEVELS)


def test_every_capability_carries_a_measured_ladder():
    """No capability may inherit an unmeasured ladder from a default."""
    for capability in om.CAPABILITIES.values():
        assert capability.pressure_levels
        assert capability.pressure_levels \
            == om.MEASURED_LADDERS[capability.api_model]


def test_stratospheric_levels_are_not_requested():
    """Nothing computed here reaches them, and each level costs request width."""
    assert max(LEVELS) == 1000
    assert min(LEVELS) == 100
    assert 50 not in LEVELS
    assert 10 not in LEVELS
    # The candidate set an audit probes is bounded the same way.
    assert min(om.PRESSURE_LEVELS) == 100
    assert 50 not in om.PRESSURE_LEVELS


@pytest.mark.parametrize("cycle,expected_max", [
    (0, 180), (6, 120), (12, 180), (18, 120),
])
def test_the_short_cutoff_cycles_stop_early(cycle, expected_max):
    hours = om.get_capability(MODEL).hours_for_cycle(cycle)
    assert max(hours) == expected_max


def test_weighted_units_do_not_undercount_a_wide_request():
    """One sounding is far more than one unit against the user's allowance."""
    assert om.weighted_units(125) == pytest.approx(12.5)
    assert om.weighted_units(4) == 1.0
    # The enabled model's real width, which measured ladders roughly halved.
    assert om.get_capability(MODEL).variable_count() == 65
    assert om.weighted_units(65) == pytest.approx(6.5)


# --------------------------------------------------------------------------- #
# Request contract
# --------------------------------------------------------------------------- #
def test_one_sounding_is_one_request_bounded_by_forecast_hours():
    dataset, seen = _fetch()
    params = seen["params"]
    assert dataset.request_count == 1
    assert params["models"] == "icon_global"
    assert params["run"] == "2026-08-29T00:00"
    # The endpoint rejects start_date/end_date and start_hour/end_hour, so the
    # span is bounded by forecast_hours: fxx + 1 hours starting at the run.
    assert params["forecast_hours"] == str(FXX + 1)
    assert "start_date" not in params
    assert "end_date" not in params
    assert "start_hour" not in params
    assert params["timeformat"] == "unixtime"
    assert params["timezone"] == "GMT"
    # Keep the provider on its own grid instead of downscaling to terrain.
    assert params["cell_selection"] == "nearest"
    assert params["elevation"] == "nan"
    assert params["wind_speed_unit"] == "ms"
    assert params["temperature_unit"] == "celsius"
    assert "apikey" not in params


def test_the_selected_grid_point_is_recorded_not_the_requested_one():
    dataset, _ = _fetch()
    assert (dataset.requested_lat, dataset.requested_lon) == (38.77, -90.87)
    assert (dataset.selected_lat, dataset.selected_lon) == (38.75, -90.875)


# --------------------------------------------------------------------------- #
# Units and the ground row
# --------------------------------------------------------------------------- #
def test_wind_speed_is_knots_while_components_stay_metres_per_second():
    """The easiest thing in this contract to get wrong."""
    dataset, _ = _fetch()
    columns = dataset.columns
    for index in range(1, 6):
        magnitude = float(np.hypot(columns["u"][index], columns["v"][index]))
        assert columns["wspd"][index] / magnitude == pytest.approx(
            om.KNOTS_PER_MS, rel=1e-9)


def test_wind_components_use_the_meteorological_convention():
    """A 270 degree wind blows from the west, so u is positive eastward."""
    payload = _payload()
    slot = VALID.hour
    for level in LEVELS:
        payload["hourly"]["wind_direction_%dhPa" % level][slot] = 270.0
        payload["hourly"]["wind_speed_%dhPa" % level][slot] = 10.0
    dataset, _ = _fetch(payload=payload)
    assert dataset.columns["u"][1] == pytest.approx(10.0, abs=1e-9)
    assert dataset.columns["v"][1] == pytest.approx(0.0, abs=1e-9)


def test_omega_is_missing_rather_than_a_wrong_conversion():
    dataset, _ = _fetch()
    assert np.all(dataset.columns["omeg"] == om.MISSING)


def test_the_ground_row_comes_first_and_carries_the_surface_fields():
    dataset, _ = _fetch()
    columns = dataset.columns
    assert columns["pres"][0] == pytest.approx(SURFACE_P)
    assert columns["tmpc"][0] == pytest.approx(16.0)
    assert columns["dwpc"][0] == pytest.approx(11.0)


def test_the_profile_satisfies_physical_quality_control():
    dataset, _ = _fetch()
    columns = dataset.columns
    assert np.all(np.diff(columns["pres"]) < 0)
    assert np.all(np.diff(columns["hght"]) > 0)
    qc = basic_sounding_qc(
        columns["pres"], columns["hght"], columns["tmpc"], columns["dwpc"],
        columns["wdir"], columns["wspd"], missing=om.MISSING)
    assert qc.valid, qc.issues


@pytest.mark.parametrize("elevation", [float("nan"), 142.0, 3000.0, -500.0])
def test_the_ground_height_ignores_the_provider_elevation(elevation):
    """The regression this design exists to prevent.

    ``elevation`` is a terrain value from a different dataset than the mass
    field. Trusting it put the ground above the lowest isobar, height stopped
    increasing, and the whole sounding failed quality control. Deriving the
    height from the model's own geopotential profile is self-consistent, so the
    provider value cannot move the ground row at all.
    """
    baseline, _ = _fetch()
    dataset, _ = _fetch(payload=_payload(elevation=elevation))
    assert dataset.surface_height_m == pytest.approx(baseline.surface_height_m)
    qc = basic_sounding_qc(
        dataset.columns["pres"], dataset.columns["hght"],
        dataset.columns["tmpc"], dataset.columns["dwpc"],
        dataset.columns["wdir"], dataset.columns["wspd"], missing=om.MISSING)
    assert qc.valid, qc.issues


def test_the_provider_elevation_is_still_recorded_as_a_diagnostic():
    dataset, _ = _fetch()
    assert dataset.provider_elevation_m == pytest.approx(142.0)
    missing, _ = _fetch(payload=_payload(elevation=float("nan")))
    assert missing.provider_elevation_m == om.MISSING


def test_high_terrain_interpolates_and_drops_below_ground_levels():
    dataset, _ = _fetch(payload=_payload(surface_pressure=700.0))
    assert dataset.surface_height_m == pytest.approx(
        _std_height(700.0), abs=25.0)
    # Derived from the ladder, not hardcoded: how many isobars a 700 hPa surface
    # buries depends entirely on which levels the model publishes.
    buried = [level for level in LEVELS if level >= 700.0]
    assert dataset.below_ground_levels_removed == len(buried) == 4
    assert not np.any(dataset.columns["pres"][1:] >= 700.0)
    # Eight levels survive, which is exactly the accepted minimum, so this case
    # also pins the floor.
    assert dataset.levels_retained == om.MIN_USABLE_LEVELS == 8
    qc = basic_sounding_qc(
        dataset.columns["pres"], dataset.columns["hght"],
        dataset.columns["tmpc"], dataset.columns["dwpc"],
        dataset.columns["wdir"], dataset.columns["wspd"], missing=om.MISSING)
    assert qc.valid, qc.issues


# --------------------------------------------------------------------------- #
# Refusals
# --------------------------------------------------------------------------- #
def test_an_incomplete_level_is_dropped_not_filled():
    baseline, _ = _fetch()
    dataset, _ = _fetch(payload=_payload(drop_level=850))
    assert dataset.levels_retained == baseline.levels_retained - 1
    assert 850.0 not in list(dataset.columns["pres"])


def test_a_missing_variable_family_is_refused():
    with pytest.raises(om.RetrievalError) as excinfo:
        _fetch(payload=_payload(blank_family="geopotential_height"))
    message = str(excinfo.value)
    assert "no usable pressure level" in message
    # Naming the families is what makes the failure actionable.
    assert "geopotential_height" in message


def test_a_missing_surface_field_is_refused():
    payload = _payload()
    payload["hourly"]["dew_point_2m"] = [None] * 24
    with pytest.raises(om.RetrievalError) as excinfo:
        _fetch(payload=payload)
    assert "surface is incomplete" in str(excinfo.value)


def test_an_absent_valid_hour_is_refused_rather_than_guessed():
    """Never take an array position on faith."""
    payload = _payload(times=[
        int((VALID.replace(hour=0) + timedelta(hours=hour)).timestamp())
        for hour in range(6)
    ])
    with pytest.raises(om.RetrievalError) as excinfo:
        _fetch(payload=payload)
    assert "does not contain" in str(excinfo.value)


def test_a_duplicated_timestamp_is_refused():
    payload = _payload()
    payload["hourly"]["time"][0] = payload["hourly"]["time"][VALID.hour]
    with pytest.raises(om.RetrievalError) as excinfo:
        _fetch(payload=payload)
    assert "ambiguous" in str(excinfo.value)


def test_a_run_before_the_archive_is_refused_without_a_request():
    with pytest.raises(om.RetrievalError) as excinfo:
        om.fetch_point(MODEL, 38.77, -90.87,
                       run_time=datetime(2023, 1, 1, tzinfo=UTC), fxx=0,
                       request_get=_refuse)
    assert "archived from" in str(excinfo.value)


def test_a_forecast_hour_past_the_horizon_is_refused_without_a_request():
    with pytest.raises(om.ParameterRangeError):
        om.fetch_point(MODEL, 38.77, -90.87, run_time=RUN, fxx=250,
                       request_get=_refuse)


def test_an_hour_beyond_a_short_cutoff_cycle_is_refused():
    with pytest.raises(om.ParameterRangeError) as excinfo:
        om.fetch_point(MODEL, 38.77, -90.87,
                       run_time=datetime(2026, 8, 29, 6, tzinfo=UTC),
                       fxx=168, request_get=_refuse)
    assert "F120" in str(excinfo.value)


@pytest.mark.parametrize("lat,lon", [(95.0, 0.0), (-91.0, 0.0)])
def test_an_impossible_latitude_is_refused_without_a_request(lat, lon):
    with pytest.raises(om.ParameterRangeError):
        om.fetch_point(MODEL, lat, lon, run_time=RUN, fxx=FXX,
                       request_get=_refuse)


def test_a_dateline_longitude_is_normalised_and_accepted():
    dataset, seen = _fetch(payload=_payload())
    assert -180.0 <= dataset.requested_lon <= 180.0
    wrapped, _ = _fetch()
    assert wrapped is not None
    assert float(seen["params"]["longitude"]) == pytest.approx(-90.87)


def test_cancellation_is_honoured():
    from sharpmod.model_transport import DownloadCancelled

    with pytest.raises(DownloadCancelled):
        om.fetch_point(MODEL, 38.77, -90.87, run_time=RUN, fxx=FXX,
                       request_get=_refuse, cancelled=lambda: True)


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #
def test_extract_writes_a_valid_portable_sounding_pair(tmp_path):
    from sharpmod.portable_sounding import portable_sounding_pair_valid

    request_get, _ = _getter(_payload())
    out = tmp_path / "sounding.npz"
    path = om.extract(MODEL, 38.77, -90.87, run_time=RUN, fxx=FXX,
                      out_path=str(out), loc="Test Point",
                      request_get=request_get)
    assert portable_sounding_pair_valid(out)

    with np.load(path, allow_pickle=False) as data:
        # ``u``/``v`` are renamed on the way to disk, as every writer does.
        for name in ("pres", "hght", "tmpc", "dwpc", "wdir", "wspd", "omeg",
                     "uwnd", "vwnd", "lat", "lon", "loc", "model", "run",
                     "valid", "fxx", "observed"):
            assert name in data.files, name
        assert str(data["run"]) == "2026-08-29 00:00"
        assert str(data["valid"]) == "2026-08-29 12:00"
        assert not bool(data["observed"])


def test_the_sidecar_records_provenance_and_no_credential(tmp_path):
    request_get, _ = _getter(_payload())
    out = tmp_path / "sounding.npz"
    om.extract(MODEL, 38.77, -90.87, run_time=RUN, fxx=FXX,
               out_path=str(out), request_get=request_get)
    meta = json.loads(
        out.with_suffix(".json").read_text(encoding="utf-8"))

    assert meta["provider"] == om.PROVIDER
    assert meta["provider_model"] == "icon_global"
    assert meta["transport"] == om.TRANSPORT
    assert meta["access_mode"] == "free-direct"
    assert meta["surface_height_source"] == "derived-from-geopotential-profile"
    assert meta["request_count"] == 1
    assert meta["estimated_weighted_units"] == pytest.approx(6.5)
    assert meta["omega_available"] is False
    assert meta["qc_valid"] is True
    # Attribution is a licence obligation, not a nicety.
    assert "Open-Meteo" in meta["attribution"]
    # The originating centre, not the redistributor, since the model licence is
    # theirs.
    assert "Deutscher Wetterdienst" in meta["origin"]

    blob = json.dumps(meta).lower()
    for leaked in ("apikey", "api_key", "secret"):
        assert leaked not in blob


def test_a_profile_failing_quality_control_is_not_written(tmp_path):
    """A stored sounding is indistinguishable from a trustworthy one."""
    request_get, _ = _getter(_payload())
    dataset = om.fetch_point(MODEL, 38.77, -90.87, run_time=RUN, fxx=FXX,
                             request_get=request_get)
    # Break monotonicity the way a bad provider response would.
    dataset.columns["hght"][3] = dataset.columns["hght"][1]
    out = tmp_path / "broken.npz"
    with pytest.raises(om.RetrievalError) as excinfo:
        om.write_point_dataset(dataset, str(out))
    assert "quality control" in str(excinfo.value)
    assert not out.exists()
    assert not out.with_suffix(".json").exists()


def test_reusing_a_dataset_makes_no_request(tmp_path):
    request_get, _ = _getter(_payload())
    dataset = om.fetch_point(MODEL, 38.77, -90.87, run_time=RUN, fxx=FXX,
                             request_get=request_get)
    path = om.extract(MODEL, 38.77, -90.87, run_time=RUN, fxx=FXX,
                      dataset=dataset, out_path=str(tmp_path / "reused.npz"),
                      request_get=_refuse)
    assert path


@pytest.mark.parametrize("override", [
    {"fxx": 24},
    {"lat": 40.0},
    {"run_time": datetime(2026, 8, 29, 12, tzinfo=UTC)},
])
def test_reusing_a_dataset_for_the_wrong_request_is_refused(tmp_path, override):
    """Mislabelling a sounding is worse than refetching it."""
    request_get, _ = _getter(_payload())
    dataset = om.fetch_point(MODEL, 38.77, -90.87, run_time=RUN, fxx=FXX,
                             request_get=request_get)
    call = {"lat": 38.77, "lon": -90.87, "run_time": RUN, "fxx": FXX}
    call.update(override)
    with pytest.raises(om.RetrievalError):
        om.extract(MODEL, call.pop("lat"), call.pop("lon"), dataset=dataset,
                   out_path=str(tmp_path / "bad.npz"),
                   request_get=_refuse, **call)


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #
def test_probe_answers_from_the_manifest_without_a_request():
    """The interface re-probes on every selection change; that must be free."""
    verdict = om.probe(MODEL, run_time=RUN, fxx=FXX, request_get=_refuse)
    assert verdict["available"] is True
    assert verdict["live"] is False
    assert verdict["surface_contract_complete"] is True
    assert verdict["run"] == "2026-08-29 00:00"
    assert "manifest check only" in verdict["note"]


def test_probe_reports_rather_than_raises():
    for kwargs in (
            {"run_time": datetime(2023, 5, 1, tzinfo=UTC), "fxx": 0},
            {"run_time": datetime(2026, 8, 29, 6, tzinfo=UTC), "fxx": 168},
    ):
        verdict = om.probe(MODEL, request_get=_refuse, **kwargs)
        assert verdict["available"] is False
        assert verdict["error"]


def test_probe_reports_a_withheld_model():
    verdict = om.probe("openmeteo-ecmwf-ifs", request_get=_refuse)
    assert verdict["available"] is False
    assert "not available" in verdict["error"]


def test_a_live_probe_opens_the_profile_when_asked():
    request_get, _ = _getter(_payload())
    verdict = om.probe(MODEL, run_time=RUN, fxx=FXX, live=True,
                       lat=38.77, lon=-90.87, request_get=request_get)
    assert verdict["available"] is True
    assert verdict["subset_opened"] is True
    # Every isobar is above the 1005 hPa surface here, so none is dropped.
    assert verdict["levels_retained"] == len(LEVELS) == 12


# --------------------------------------------------------------------------- #
# Facade integration
# --------------------------------------------------------------------------- #
def test_the_facade_routes_the_model_without_a_grib_runtime():
    config = model_extract.get_config("om-icon")
    assert config.key == MODEL
    assert not model_extract.requires_grib_runtime(config)
    assert model_extract.point_only_provider(config)
    assert model_extract.spatial_cache_key(config, 38.77, -90.87)


def test_the_facade_keeps_the_built_in_ecmwf_route_separate():
    """Re-pointing ``ecmwf`` would change what saved sessions mean."""
    assert model_extract.get_config("ecmwf").key == "ecmwf-ifs"
    assert model_extract.get_config("ifs").key == "ecmwf-ifs"
    assert model_extract.requires_grib_runtime("ecmwf")
    assert not model_extract.point_only_provider("ecmwf")


@pytest.mark.parametrize("cycle,expected_max", [(0, 180), (6, 120)])
def test_the_facade_applies_the_provider_cutoff(cycle, expected_max):
    hours = model_extract.forecast_hours("om-icon", cycle_hour=cycle)
    assert max(hours) == expected_max


def test_the_facade_reports_the_provider_capability():
    capability = model_extract.provider_capability("om-icon")
    assert capability.provider == om.PROVIDER
    assert capability.transports == (om.TRANSPORT,)
    assert "1000 hPa to 100 hPa" in capability.levels
    assert "2026-04-02" in capability.archive_window


def test_the_facade_refuses_a_member_for_a_deterministic_model():
    with pytest.raises(model_extract.RetrievalError):
        model_extract.extract("om-icon", 38.77, -90.87, run_time=RUN,
                              fxx=FXX, member="c00")


def test_the_facade_explains_withheld_open_meteo_models():
    unsupported = model_extract.unsupported_models()
    assert "openmeteo-ecmwf-ifs" in unsupported
    assert "openmeteo-best-match" in unsupported


def test_bare_icon_resolves_now_that_a_route_exists():
    """``icon`` used to raise "the installed Herbie has no ICON loader".

    Claiming the name is safe in a way that re-pointing ``ecmwf`` would not be,
    because nothing resolved it before, so no saved session changes meaning.
    """
    assert model_extract.get_config("icon").key == MODEL
    assert "icon" not in model_extract.unsupported_models()


def test_a_withheld_open_meteo_key_explains_itself_through_the_facade():
    """``--list`` prints these keys, so typing one back must not be a KeyError."""
    with pytest.raises(model_extract.RetrievalError) as excinfo:
        model_extract.get_config("openmeteo-ecmwf-ifs")
    assert "without pressure-level fields" in str(excinfo.value)
    with pytest.raises(KeyError):
        model_extract.get_config("openmeteo-not-a-model")

"""Scientific and failure-state tests for live experimental HRRR TOI inputs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from sharpmod.guidance import GuidanceState
from sharpmod.guidance.hrrr import (
    TOI_MAXIMUM_FRAMES,
    HrrrRegionalFrame,
    build_hrrr_guidance_from_frames,
    build_live_hrrr_guidance,
    fixed_layer_stp_proxy,
    normalize_hrrr_bulk_shear_components,
    select_objective_risk_region,
    toi_sampling_hours,
)
from sharpmod.model_transport import DownloadCancelled


def _frame(
    forecast_hour: int,
    *,
    jet_left: int = 1,
    level: int = 500,
    jet_speed_mps: float = 40.0,
    valid_hour: int | None = None,
) -> HrrrRegionalFrame:
    rows, cols = 7, 9
    latitude = np.repeat(np.linspace(31.0, 37.0, rows)[:, None], cols, axis=1)
    longitude = np.repeat(
        np.linspace(-102.0, -94.0, cols)[None, :], rows, axis=0
    )
    u_wind = np.zeros((rows, cols), dtype=float)
    v_wind = np.zeros_like(u_wind)
    u_wind[1:3, jet_left : jet_left + 2] = float(jet_speed_mps)

    cape = np.zeros_like(u_wind)
    srh = np.zeros_like(u_wind)
    risk_slice = (slice(3, 5), slice(5, 7))
    cape[risk_slice] = 3000.0
    srh[risk_slice] = 300.0
    run_time = datetime(2026, 4, 10, 0, tzinfo=timezone.utc)
    return HrrrRegionalFrame(
        run_time=run_time,
        valid_time=run_time
        + timedelta(
            hours=forecast_hour if valid_hour is None else valid_hour
        ),
        forecast_hour=forecast_hour,
        pressure_level_hpa=level,
        latitude=latitude,
        longitude=longitude,
        u_wind_mps=u_wind,
        v_wind_mps=v_wind,
        surface_cape_jkg=cape,
        temperature_2m_k=np.full_like(u_wind, 300.0),
        dewpoint_2m_k=np.full_like(u_wind, 300.0),
        srh_1km_m2s2=srh,
        shear_u_6km_mps=np.full_like(u_wind, 20.0),
        shear_v_6km_mps=np.zeros_like(u_wind),
        source_url=f"memory://hrrr/f{forecast_hour:03d}",
        shear_interpretation="synthetic m/s component delta",
    )


def test_fixed_layer_stp_proxy_matches_sharppy_term_normalizations():
    frame = _frame(0)

    result = fixed_layer_stp_proxy(frame)

    # CAPE/1500=2, LCL=1, SRH/150=2, BWD/20=1.
    assert result[3, 5] == pytest.approx(4.0)
    assert result[0, 0] == pytest.approx(0.0)


def test_hrrr_shear_normalization_handles_rate_and_operational_delta_forms():
    rate_u = np.full((2, 2), 0.003)
    rate_v = np.full((2, 2), 0.004)

    normalized_u, normalized_v, rate_method = (
        normalize_hrrr_bulk_shear_components(rate_u, rate_v)
    )
    assert normalized_u[0, 0] == pytest.approx(18.0)
    assert normalized_v[0, 0] == pytest.approx(24.0)
    assert "integrated" in rate_method

    delta_u, delta_v, delta_method = normalize_hrrr_bulk_shear_components(
        np.full((2, 2), 15.0), np.full((2, 2), 20.0)
    )
    assert delta_u[0, 0] == pytest.approx(15.0)
    assert delta_v[0, 0] == pytest.approx(20.0)
    assert "component deltas" in delta_method


def test_objective_risk_region_selects_nearest_component_not_strongest_far_one():
    latitude = np.repeat(np.arange(6.0)[:, None], 8, axis=1)
    longitude = np.repeat(np.arange(8.0)[None, :], 6, axis=0)
    stp = np.zeros((2, 6, 8), dtype=float)
    stp[:, 1:3, 1:3] = 1.0
    stp[:, 3:5, 5:7] = 6.0

    region = select_objective_risk_region(
        stp,
        latitude,
        longitude,
        point_latitude=1.0,
        point_longitude=1.0,
        maximum_point_distance_km=1000.0,
    )

    assert np.all(region.mask[1:3, 1:3])
    assert not np.any(region.mask[3:5, 5:7])
    assert region.peak_stp == pytest.approx(1.0)
    assert region.grid_point_count == 4


def test_hrrr_frames_produce_experimental_toi():
    result = build_hrrr_guidance_from_frames(
        (_frame(0, jet_left=1), _frame(18, jet_left=3)),
        point_latitude=34.0,
        point_longitude=-97.0,
    )

    assert result.toi.state is GuidanceState.EXPERIMENTAL
    assert result.toi.features.translation_speed_kt > 0
    assert result.toi.features.maximum_jet_speed_kt == pytest.approx(
        40.0 * 1.9438444924406
    )
    assert result.toi.features.maximum_stp == pytest.approx(4.0)
    assert 0.0 <= result.toi.score <= 5.0
    assert 0.0 < result.toi.high_risk_probability < 1.0
    assert "public_anchor_probability" in result.toi.calibration_version
    assert "not official SPC calibration" in result.provenance["toi_probability_status"]
    assert result.provenance["toi_public_method"].startswith("https://www.spc.noaa.gov/")
    assert "official" in result.toi.reason


RUN_TIME = datetime(2026, 4, 10, 0, tzinfo=timezone.utc)


def _tracking_frame(forecast_hour: int, **overrides) -> HrrrRegionalFrame:
    """A westerly jet stepping one grid column east every sampling interval."""

    overrides.setdefault("jet_left", 1 + int(forecast_hour) // 3)
    return _frame(forecast_hour, **overrides)


def _recording_fetcher(
    *,
    failing_hours: frozenset[int] = frozenset(),
    frame_factory=_tracking_frame,
):
    calls: list[tuple[int, str]] = []

    def fetcher(run_time, forecast_hour, *_args, **kwargs):
        calls.append((int(forecast_hour), str(kwargs["download_dir"])))
        if forecast_hour in failing_hours:
            raise RuntimeError(f"F{int(forecast_hour):03d} not archived")
        return frame_factory(forecast_hour)

    return fetcher, calls


def test_toi_sampling_plan_is_three_hourly_bounded_sorted_and_deduplicated():
    # The whole applicable window at the published three-hour interval.
    assert toi_sampling_hours(6) == (0, 3, 6, 9, 12, 15, 18)
    # A duplicate requested hour never repeats an already planned frame.
    assert toi_sampling_hours(18) == (0, 3, 6, 9, 12, 15, 18)
    # An off-interval requested hour is added and the plan stays sorted.
    plan = toi_sampling_hours(7)
    assert plan == (0, 3, 6, 7, 9, 12, 15, 18)
    assert list(plan) == sorted(set(plan))
    # Beyond 18 h the window slides so it always ends on the requested hour.
    assert toi_sampling_hours(24) == (6, 9, 12, 15, 18, 21, 24)
    # Downloads stay bounded even if a caller asks for a denser interval.
    assert len(toi_sampling_hours(6)) <= TOI_MAXIMUM_FRAMES
    with pytest.raises(ValueError, match="bounded maximum"):
        toi_sampling_hours(6, interval_hours=1)


def test_live_builder_requests_every_three_hours_and_uses_all_frames(tmp_path):
    fetcher, calls = _recording_fetcher()

    result = build_live_hrrr_guidance(
        RUN_TIME, 6, 34.0, -97.0, download_dir=tmp_path, fetcher=fetcher
    )

    assert [hour for hour, _dir in calls] == [0, 3, 6, 9, 12, 15, 18]
    assert all(directory == str(tmp_path) for _hour, directory in calls)
    assert result.toi.state is GuidanceState.EXPERIMENTAL
    assert result.toi.high_risk_probability is not None
    provenance = result.provenance
    assert provenance["toi_requested_forecast_hours"] == "0,3,6,9,12,15,18"
    assert provenance["toi_successful_forecast_hours"] == "0,3,6,9,12,15,18"
    assert provenance["toi_failed_forecast_hours"] == "none"
    assert provenance["toi_frame_count"] == "7"
    assert provenance["toi_time_coverage_hours"] == "18"
    assert provenance["toi_sampling_interval_hours"] == "3"
    assert provenance["toi_maximum_sampling_gap_hours"] == "3"
    assert provenance["toi_sampling_status"] == "complete"
    assert "toi_sampling_degraded_reason" not in provenance
    assert result.valid_start == RUN_TIME
    assert result.valid_end == RUN_TIME + timedelta(hours=18)


def test_live_builder_uses_intermediate_frames_not_only_the_endpoints(tmp_path):
    def peaked(forecast_hour: int) -> HrrrRegionalFrame:
        # The strongest jet exists only at an intermediate forecast hour.
        speed = 60.0 if forecast_hour == 9 else 40.0
        return _tracking_frame(forecast_hour, jet_speed_mps=speed)

    full_fetcher, _calls = _recording_fetcher(frame_factory=peaked)
    endpoints_fetcher, _endpoint_calls = _recording_fetcher(
        failing_hours=frozenset({3, 6, 9, 12, 15}), frame_factory=peaked
    )

    full = build_live_hrrr_guidance(
        RUN_TIME, 6, 34.0, -97.0, download_dir=tmp_path, fetcher=full_fetcher
    )
    endpoints = build_live_hrrr_guidance(
        RUN_TIME, 6, 34.0, -97.0, download_dir=tmp_path, fetcher=endpoints_fetcher
    )

    assert full.toi.features.maximum_jet_speed_kt == pytest.approx(
        60.0 * 1.9438444924406
    )
    assert endpoints.toi.features.maximum_jet_speed_kt == pytest.approx(
        40.0 * 1.9438444924406
    )
    assert (
        full.toi.features.maximum_jet_speed_kt
        > endpoints.toi.features.maximum_jet_speed_kt
    )


def test_live_builder_orders_frames_by_valid_time_not_arrival_order(tmp_path):
    # F015 is served out of order; the builder must still sort by valid time.
    order = (0, 3, 6, 9, 15, 12, 18)

    def fetcher(_run_time, forecast_hour, *_args, **_kwargs):
        return _tracking_frame(forecast_hour)

    result = build_live_hrrr_guidance(
        RUN_TIME,
        6,
        34.0,
        -97.0,
        download_dir=tmp_path,
        fetcher=fetcher,
    )
    shuffled = build_live_hrrr_guidance(
        RUN_TIME,
        6,
        34.0,
        -97.0,
        download_dir=tmp_path,
        fetcher=lambda run_time, hour, *args, **kwargs: fetcher(
            run_time, order[toi_sampling_hours(6).index(hour)], *args, **kwargs
        ),
    )

    assert result.provenance["toi_successful_forecast_hours"] == "0,3,6,9,12,15,18"
    assert shuffled.provenance["toi_successful_forecast_hours"] == (
        "0,3,6,9,12,15,18"
    )
    assert shuffled.toi.features == result.toi.features


def test_live_builder_degrades_on_partial_sampling_without_breaking_sounding(
    tmp_path,
):
    fetcher, calls = _recording_fetcher(failing_hours=frozenset({3, 12}))

    result = build_live_hrrr_guidance(
        RUN_TIME, 6, 34.0, -97.0, download_dir=tmp_path, fetcher=fetcher
    )

    # Every planned hour is still attempted; failures do not stop the sample.
    assert [hour for hour, _dir in calls] == [0, 3, 6, 9, 12, 15, 18]
    assert result.toi.state is GuidanceState.EXPERIMENTAL
    provenance = result.provenance
    assert provenance["toi_successful_forecast_hours"] == "0,6,9,15,18"
    assert provenance["toi_failed_forecast_hours"] == "3,12"
    assert provenance["toi_frame_count"] == "5"
    assert provenance["toi_time_coverage_hours"] == "18"
    assert provenance["toi_sampling_status"] == "degraded"
    assert "missing forecast hours 3,12" in provenance[
        "toi_sampling_degraded_reason"
    ]
    assert "not archived" in provenance["partial_fetch_failures"]


def test_live_builder_marks_wide_sampling_gaps_as_degraded(tmp_path):
    fetcher, _calls = _recording_fetcher(
        failing_hours=frozenset({3, 6, 9, 12, 15})
    )

    result = build_live_hrrr_guidance(
        RUN_TIME, 6, 34.0, -97.0, download_dir=tmp_path, fetcher=fetcher
    )

    assert result.toi.state is GuidanceState.EXPERIMENTAL
    assert result.provenance["toi_maximum_sampling_gap_hours"] == "18"
    assert result.provenance["toi_sampling_status"] == "degraded"
    assert "largest used gap" in result.provenance["toi_sampling_degraded_reason"]


def test_live_builder_reports_exact_reason_when_coverage_is_insufficient(tmp_path):
    fetcher, _calls = _recording_fetcher(
        failing_hours=frozenset({9, 12, 15, 18})
    )

    result = build_live_hrrr_guidance(
        RUN_TIME, 6, 34.0, -97.0, download_dir=tmp_path, fetcher=fetcher
    )

    assert result.toi.state is GuidanceState.UNAVAILABLE
    assert "6 h of the 18 h window" in result.toi.reason
    assert "at least 9 h" in result.toi.reason
    assert result.provenance["toi_successful_forecast_hours"] == "0,3,6"
    assert result.provenance["toi_failed_forecast_hours"] == "9,12,15,18"


def test_live_builder_reports_exact_reason_when_every_frame_fails(tmp_path):
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("provider offline")

    result = build_live_hrrr_guidance(
        RUN_TIME, 6, 34.0, -97.0, download_dir=tmp_path, fetcher=unavailable
    )

    assert result.toi.state is GuidanceState.UNAVAILABLE
    assert "only 0 of 7 requested HRRR frames decoded" in result.toi.reason
    assert "provider offline" in result.toi.reason


def test_live_builder_discards_duplicate_valid_times(tmp_path):
    def duplicating(_run_time, forecast_hour, *_args, **_kwargs):
        # F009 mistakenly decodes to the F006 valid time.
        valid_hour = 6 if forecast_hour == 9 else None
        return _tracking_frame(forecast_hour, valid_hour=valid_hour)

    result = build_live_hrrr_guidance(
        RUN_TIME, 6, 34.0, -97.0, download_dir=tmp_path, fetcher=duplicating
    )

    assert result.toi.state is GuidanceState.EXPERIMENTAL
    assert result.provenance["toi_successful_forecast_hours"] == "0,3,6,12,15,18"
    assert result.provenance["toi_frame_count"] == "6"
    assert "duplicate valid time" in result.provenance["partial_fetch_failures"]


def test_live_builder_propagates_cancellation_without_partial_guidance(tmp_path):
    calls: list[int] = []

    def fetcher(_run_time, forecast_hour, *_args, **_kwargs):
        calls.append(int(forecast_hour))
        return _tracking_frame(forecast_hour)

    with pytest.raises(DownloadCancelled):
        build_live_hrrr_guidance(
            RUN_TIME,
            6,
            34.0,
            -97.0,
            download_dir=tmp_path,
            fetcher=fetcher,
            cancelled=lambda: len(calls) >= 2,
        )

    assert calls == [0, 3]


def test_frame_builder_records_sampling_provenance_for_direct_frames():
    result = build_hrrr_guidance_from_frames(
        tuple(_tracking_frame(hour) for hour in (0, 6, 12, 18)),
        point_latitude=34.0,
        point_longitude=-97.0,
    )

    assert result.provenance["toi_requested_forecast_hours"] == "0,6,12,18"
    assert result.provenance["toi_successful_forecast_hours"] == "0,6,12,18"
    assert result.provenance["toi_frame_count"] == "4"
    assert result.provenance["toi_time_coverage_hours"] == "18"
